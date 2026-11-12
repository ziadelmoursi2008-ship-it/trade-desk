"""Lifecycle regressions use isolated SQLite and deterministic observations, never live orders."""

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import database
import news_engine
from decision_engine import validate_ai_trade_decision
from signal_manager import get_signal_state, manage_signal, monitor_signal_market, previous_signal_context, signal_history
from signal_policy import decision_policy, summarize_macro, summarize_news, weighted_hard_bundle
from test_platform import ai_proposal, technical


def quote(price, timestamp=900000, low=None, high=None):
    return {"timestamp": timestamp, "open": price, "close": price,
            "low": price if low is None else low, "high": price if high is None else high, "volume": 1000}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="signal-manager-tests-")
        self.environment = patch.dict(os.environ, {"TRADING_DB_PATH": str(Path(self.directory.name) / "test.db"),
                                                   "DECISION_WEIGHTS_JSON": "", "MACRO_WEIGHTS_JSON": ""})
        self.environment.start()
        database.init_db()
        news_engine._analysis_cache.clear()
        news_engine._feed_cache.clear()

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def observe(self, step=0, proposal=None, review=None, *, price=100, method="gemini", context=None,
                ticker="TEST", timeframe="5m", hard=None, stale=False):
        proposal = ai_proposal() if proposal is None else proposal
        bar_at = 900000 + step * 300000
        current_technical = {**technical(), "latest_close": price}
        decision = validate_ai_trade_decision(ticker, timeframe, current_technical, proposal,
                                            as_of=bar_at / 1000, analysis_method=method)
        return manage_signal(ticker, timeframe, decision, proposal, analysis_method=method,
                             context_id=context or f"{ticker}:{timeframe}:{step}:{method}", bar_at=bar_at,
                             completed_bar_times=[900000 + i * 300000 for i in range(step + 1)],
                             market_bars=[quote(price, bar_at - 300000)], review=review, hard_bundle=hard,
                             now=bar_at + 100000, market_stale=stale, technical=current_technical)

    def review(self, assessment="WEAKENING", confidence=50, **extra):
        return {"previous_setup_id": get_signal_state("TEST", "5m")["setup_id"],
                "assessment": assessment, "confidence_in_previous": confidence, "reason": "Gemini-Strukturbegründung.", **extra}

    def neutral(self):
        return {"signal_type": "NEUTRAL", "direction_score": 50, "confidence": 50, "reasons": ["Kein neuer Entry."]}

    def test_buy_neutral_buy_keeps_setup_and_original_levels(self):
        initial = self.observe()
        weakened = self.observe(1, self.neutral(), self.review())
        recovered = self.observe(2, review=self.review("CONFIRMED", 85))
        self.assertEqual(initial["lifecycle_status"], "AKTIV")
        self.assertEqual(weakened["signal_label"], "WEAK_BUY")
        self.assertTrue(weakened["hold_previous"])
        self.assertEqual(recovered["signal_label"], "STRONG_BUY")
        self.assertEqual(initial["setup_id"], recovered["setup_id"])
        for name in ("entry_price", "stop_loss", "take_profit_1", "take_profit_2"):
            self.assertEqual(initial[name], weakened[name])

    def test_sell_weakens_symmetrically(self):
        initial = self.observe(proposal=ai_proposal(-1))
        weakened = self.observe(1, self.neutral(), self.review(confidence=60))
        self.assertEqual(initial["signal_label"], "STRONG_SELL")
        self.assertEqual(weakened["signal_label"], "WEAK_SELL")
        self.assertEqual(weakened["signal_type"], "SELL")

    def test_new_same_direction_levels_never_silently_move_old_stop(self):
        initial = self.observe()
        proposal = {**ai_proposal(), "entry_price": 101, "entry_zone_min": 100.8, "entry_zone_max": 101.2,
                    "stop_loss": 99, "take_profit_1": 105, "take_profit_2": 107}
        result = self.observe(1, proposal, self.review("CONFIRMED", 75))
        self.assertEqual(result["stop_loss"], initial["stop_loss"])
        self.assertEqual(result["entry_price"], initial["entry_price"])

    def test_weakening_keeps_gemini_confidence_separate_from_managed_strength(self):
        self.observe()
        result = self.observe(1, self.neutral(), self.review("WEAKENING", 85))
        self.assertEqual(result["confidence"], 85)
        self.assertEqual(result["signal_label"], "WEAK_BUY")
        self.assertEqual(result["strength"], 64)

    def test_two_distinct_consecutive_closed_candles_end_setup(self):
        self.observe()
        first = self.observe(1, self.neutral(), self.review("AGAINST"))
        repeated = self.observe(1, self.neutral(), self.review("AGAINST"), context="changed-news-same-bar")
        second = self.observe(2, self.neutral(), self.review("AGAINST"))
        self.assertEqual(first["opposing_bars"], 1)
        self.assertEqual(repeated["opposing_bars"], 1)
        self.assertEqual(second["lifecycle_status"], "BEENDET")
        self.assertEqual(second["signal_type"], "NEUTRAL")

    def test_moderate_weakening_does_not_count_as_opposition(self):
        self.observe()
        self.observe(1, self.neutral(), self.review(confidence=60))
        result = self.observe(2, self.neutral(), self.review(confidence=60))
        self.assertEqual(result["signal_type"], "BUY")
        self.assertEqual(result["opposing_bars"], 0)

    def test_two_massive_confidence_drops_end_setup(self):
        self.observe()
        self.observe(1, self.neutral(), self.review(confidence=40))
        result = self.observe(2, self.neutral(), self.review(confidence=40))
        self.assertEqual(result["lifecycle_status"], "BEENDET")
        self.assertEqual(result["low_confidence_bars"], 2)

    def test_api_failure_preserves_plan_and_breaks_counter_sequence(self):
        self.observe()
        self.observe(1, self.neutral(), self.review("AGAINST"))
        offline = self.observe(2, method="keywords")
        recovered = self.observe(3, self.neutral(), self.review("AGAINST"))
        self.assertEqual(offline["signal_type"], "BUY")
        self.assertEqual(offline["verification_status"], "unavailable")
        self.assertEqual(offline["opposing_bars"], 1)
        self.assertEqual(recovered["opposing_bars"], 1)

    def test_rejected_new_plan_does_not_cancel_existing_plan(self):
        self.observe()
        invalid = self.observe(1, {**ai_proposal(), "stop_loss": 101})
        self.assertEqual(invalid["signal_type"], "BUY")
        self.assertEqual(invalid["verification_status"], "rejected_proposal")
        self.assertEqual(invalid["opposing_bars"], 0)

    def test_strong_opposite_ends_without_same_bar_resurrection(self):
        first = self.observe()
        opposite = {**ai_proposal(-1), "confidence": 85}
        ended = self.observe(1, opposite, self.review("AGAINST", 20))
        repeated = self.observe(1, opposite, context="different-context-same-candle")
        next_setup = self.observe(2, opposite)
        self.assertEqual(ended["lifecycle_status"], "BEENDET")
        self.assertEqual(repeated["lifecycle_status"], "BEENDET")
        self.assertEqual(next_setup["signal_type"], "SELL")
        self.assertNotEqual(first["setup_id"], next_setup["setup_id"])

    def test_technical_break_requires_review_of_exact_setup(self):
        self.observe()
        mismatch = self.review("INVALIDATED", 40, technical_break=True)
        mismatch["previous_setup_id"] = "other-setup"
        kept = self.observe(1, self.neutral(), mismatch)
        ended = self.observe(2, self.neutral(), self.review("INVALIDATED", 40, technical_break=True))
        self.assertEqual(kept["signal_type"], "BUY")
        self.assertEqual(ended["lifecycle_status"], "BEENDET")

    def test_macro_opposition_requires_actually_changed_official_observation(self):
        hard = {"items": [{"series_id": "DFEDTARL", "period": "2026-10-26", "value": 4, "stale": False}]}
        self.observe(hard=hard)
        arguments = {"macro_opposition": True, "macro_opposition_strength": 95, "macro_event_id": "DFEDTARL:2026-10-26"}
        kept = self.observe(1, self.neutral(), self.review(**arguments), hard=hard)
        changed = {"items": [{**hard["items"][0], "value": 4.5}]}
        ended = self.observe(1, self.neutral(), self.review(**arguments), hard=changed, context="new-official-release")
        self.assertEqual(kept["signal_type"], "BUY")
        self.assertEqual(ended["lifecycle_status"], "BEENDET")

    def test_prepared_entry_ignores_historical_touch_then_activates_on_new_quote(self):
        initial = self.observe(price=101)
        old_touch = monitor_signal_market("TEST", "5m", [quote(101, 900000, low=99, high=106)], now=1100000)
        touched = monitor_signal_market("TEST", "5m", [quote(100, 900000, low=99, high=106)], now=1100010)
        self.assertEqual(initial["lifecycle_status"], "VORBEREITET")
        self.assertEqual(old_touch["lifecycle_status"], "VORBEREITET")
        self.assertEqual(touched["lifecycle_status"], "AKTIV")

    def test_confirmation_entry_requires_gemini_confirmation_for_current_closed_bar(self):
        proposal = {**ai_proposal(), "entry_mode": "CONFIRMATION", "entry_condition": "Abgeschlossener Rejection-Close."}
        first = self.observe(proposal=proposal)
        pending = self.observe(1, self.neutral(), self.review("CONFIRMED", 75, entry_confirmed=True, entry_evidence_bar_at=900000))
        active = self.observe(2, self.neutral(), self.review("CONFIRMED", 75, entry_confirmed=True, entry_evidence_bar_at=1500000))
        self.assertEqual(first["lifecycle_status"], "VORBEREITET")
        self.assertEqual(pending["lifecycle_status"], "VORBEREITET")
        self.assertEqual(active["lifecycle_status"], "AKTIV")

    def test_far_future_breakout_is_rejected(self):
        proposal = {**ai_proposal(), "entry_price": 106, "entry_zone_min": 105.8, "entry_zone_max": 106.2,
                    "stop_loss": 104, "take_profit_1": 110, "take_profit_2": 112,
                    "entry_mode": "STOP", "entry_condition": "Ausbruch in die geplante Zone.", "invalidation_price": 97}
        result = self.observe(proposal=proposal)
        self.assertEqual(result["signal_type"], "NEUTRAL")
        self.assertIsNone(result["entry_price"])

    def test_nearby_future_breakout_can_activate_with_its_original_stop(self):
        proposal = {**ai_proposal(), "entry_price": 101.5, "entry_zone_min": 101.4, "entry_zone_max": 101.6,
                    "stop_loss": 100.5, "take_profit_1": 104, "take_profit_2": 106,
                    "entry_mode": "STOP", "entry_condition": "Ausbruch in die nahe Zone.", "invalidation_price": 97}
        result = self.observe(proposal=proposal)
        self.assertEqual(result["lifecycle_status"], "VORBEREITET")
        self.assertEqual(result["entry_price"], 101.5)
        active = monitor_signal_market("TEST", "5m", [quote(101.5)], now=1100000)
        stopped = monitor_signal_market("TEST", "5m", [quote(100.5)], now=1100010)
        self.assertEqual(active["lifecycle_status"], "AKTIV")
        self.assertEqual(stopped["lifecycle_status"], "BEENDET")

    def test_pending_plan_expires_when_price_moves_too_far_and_levels_remain_in_history(self):
        initial = self.observe(price=101)
        expired = self.observe(1, self.neutral(), self.review("CONFIRMED", 85), price=103)
        self.assertEqual(expired["lifecycle_status"], "ABGELAUFEN")
        self.assertEqual(expired["signal_type"], "NEUTRAL")
        self.assertEqual(expired["setup_id"], initial["setup_id"])
        self.assertEqual(expired["entry_price"], initial["entry_price"])
        self.assertIn("3.00 ATR", expired["state_reason"])
        events = signal_history("TEST", "5m")
        self.assertEqual(sum(event["event_type"] == "ENTRY_TOO_DISTANT" for event in events), 1)

    def test_pending_distance_is_reviewed_before_cached_context_returns(self):
        self.observe(price=101, context="same-context")
        expired = self.observe(price=103, context="same-context")
        repeated = self.observe(price=103, context="same-context")
        self.assertEqual(expired["lifecycle_status"], "ABGELAUFEN")
        self.assertEqual(repeated["lifecycle_status"], "ABGELAUFEN")
        self.assertEqual(sum(event["event_type"] == "ENTRY_TOO_DISTANT" for event in signal_history("TEST", "5m")), 1)

    def test_active_plan_keeps_levels_when_price_moves_far_from_entry(self):
        initial = self.observe()
        result = self.observe(1, self.neutral(), self.review("CONFIRMED", 85), price=103)
        self.assertEqual(result["lifecycle_status"], "AKTIV")
        self.assertEqual(result["entry_price"], initial["entry_price"])
        self.assertEqual(result["stop_loss"], initial["stop_loss"])

    def test_stale_market_does_not_expire_distant_pending_entry(self):
        self.observe(price=101)
        result = self.observe(1, self.neutral(), self.review("CONFIRMED", 85), price=110, stale=True)
        self.assertEqual(result["lifecycle_status"], "VORBEREITET")
        self.assertEqual(result["verification_status"], "stale_market")

    def test_quote_stop_works_without_new_gemini_call(self):
        self.observe()
        result = monitor_signal_market("TEST", "5m", [quote(97)], now=1100000)
        self.assertEqual(result["lifecycle_status"], "BEENDET")
        self.assertIn("Stop-Loss", result["state_reason"])

    def test_short_stop_and_targets_are_symmetric(self):
        self.observe(proposal=ai_proposal(-1))
        first = monitor_signal_market("TEST", "5m", [quote(96)], now=1100000)
        second = monitor_signal_market("TEST", "5m", [quote(94)], now=1100010)
        self.assertTrue(first["tp1_reached"])
        self.assertTrue(second["tp2_reached"])
        self.assertEqual(second["lifecycle_status"], "BEENDET")

    def test_ambiguous_closed_candle_prioritizes_stop_over_target(self):
        self.observe()
        result = monitor_signal_market("TEST", "5m", [quote(100, 1200000, low=97, high=107)], now=1500000)
        self.assertEqual(result["lifecycle_status"], "BEENDET")
        self.assertFalse(result["tp2_reached"])

    def test_expiry_does_not_reopen_same_bar(self):
        self.observe(proposal={**ai_proposal(), "valid_for_bars": 2})
        self.observe(1)
        result = self.observe(2)
        self.assertEqual(result["lifecycle_status"], "ABGELAUFEN")
        self.assertEqual(result["elapsed_bars"], 2)

    def test_state_is_persistent_and_scoped_per_instrument_and_timeframe(self):
        initial = self.observe()
        self.observe(ticker="OTHER", price=101)
        self.observe(timeframe="15m", proposal=ai_proposal(-1))
        self.assertEqual(previous_signal_context("TEST", "5m")["setup_id"], initial["setup_id"])
        self.assertEqual(get_signal_state("OTHER", "5m")["status"], "VORBEREITET")
        self.assertEqual(get_signal_state("TEST", "15m")["direction"], "SELL")

    def test_history_includes_neutral_observation_and_frozen_levels_without_duplicates(self):
        self.observe()
        self.observe(1, self.neutral(), self.review())
        self.observe(1, self.neutral(), self.review())
        events = signal_history("TEST", "5m")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["decision"]["latest_ai_proposal"]["signal_type"], "NEUTRAL")
        self.assertEqual(events[0]["decision"]["stop_loss"], 98)

    def test_parallel_refreshes_count_a_candle_only_once(self):
        self.observe()
        review = self.review("AGAINST")
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda i: self.observe(1, self.neutral(), review, context=f"parallel:{i}"), range(8)))
        self.assertEqual(get_signal_state("TEST", "5m")["opposing_bars"], 1)

    def test_stale_market_cannot_activate_or_end_from_old_price(self):
        self.observe()
        result = self.observe(1, self.neutral(), self.review("AGAINST", 20), stale=True, price=97)
        self.assertEqual(result["signal_type"], "BUY")
        self.assertEqual(result["opposing_bars"], 0)
        self.assertEqual(result["verification_status"], "stale_market")


class WeightingAndContextTests(LifecycleTests):
    # Reuse only the fixture and helpers, not inherited test methods (see load_tests below).
    def test_fixed_weights_total_100_and_missing_data_is_not_renormalized(self):
        policy = decision_policy()
        self.assertEqual(sum(policy["decision_weights_pct"].values()), 100)
        self.assertEqual(sum(policy["macro_weights_pct"].values()), 100)
        bundle = weighted_hard_bundle({"items": [{"series_id": "GDPC1", "stale": False}]}, policy)
        self.assertEqual(bundle["items"][0]["importance_pct"], 10)
        self.assertEqual(bundle["items"][0]["decision_weight_pct"], 2.5)
        result = summarize_macro([{"series_id": "GDPC1", "directional_effect": 1, "reason": "Gemini judgment"}], bundle)
        self.assertEqual(result["weighted_direction_score"], 55)
        self.assertEqual(result["assessed_weight_pct"], 10)

    def test_invalid_configuration_is_rejected(self):
        with patch.dict(os.environ, {"DECISION_WEIGHTS_JSON": '{"technical":80,"hard_data":25,"normal_news":25}'}):
            with self.assertRaises(ValueError):
                decision_policy()

    def test_repeated_articles_about_same_event_do_not_increase_news_weight(self):
        article = {"event_key": "earnings", "analysis_method": "gemini", "importance_score": 80,
                   "relevance": 1, "novelty": 1, "sentiment": 1}
        opposite = {**article, "event_key": "lawsuit", "sentiment": -1}
        result = summarize_news([article] * 20 + [opposite])
        self.assertEqual(result["distinct_events"], 2)
        self.assertEqual(result["weighted_direction_score"], 50)

    def test_gemini_direction_cannot_ignore_configured_block_weights(self):
        proposal = {**ai_proposal(), "decision_breakdown": {"weighted_direction_score": 30}}
        result = validate_ai_trade_decision("TEST", "5m", technical(), proposal, analysis_method="gemini")
        self.assertEqual(result["signal_type"], "NEUTRAL")
        self.assertIn("Richtungsscore", result["reasons"][0])

    def test_arithmetic_error_does_not_discard_an_otherwise_valid_directional_plan(self):
        proposal = {**ai_proposal(-1), "direction_score": 0, "decision_breakdown": {"weighted_direction_score": 28.5}}
        result = validate_ai_trade_decision("TEST", "5m", technical(), proposal, analysis_method="gemini")
        self.assertEqual(result["signal_type"], "SELL")
        self.assertEqual(result["score"], 28.5)
        self.assertEqual(result["reported_direction_score"], 0)
        self.assertTrue(result["score_weight_adjusted"])

    def test_directional_response_schema_requires_a_concrete_entry(self):
        from pydantic import ValidationError
        proposal = {**ai_proposal(), "entry_mode": "LIMIT", "entry_condition": "At support", "invalidation_price": 98,
                    "invalidation_condition": "Support broken", "valid_for_bars": 12, "entry_price": None}
        with self.assertRaises(ValidationError):
            news_engine.DirectionalTradeProposal.model_validate(proposal)

    def test_response_schema_is_accepted_by_installed_gemini_sdk(self):
        from google.genai import _transformers
        schema = _transformers.t_schema(None, news_engine.ContextEvaluation)
        self.assertIsNotNone(schema)
        self.assertEqual(len(schema.properties["trade"].any_of), 2)

    def test_combined_gemini_contract_contains_previous_plan_and_individual_news_importance(self):
        policy = decision_policy()
        hard = weighted_hard_bundle({"items": [{"series_id": "GDPC1", "period": "2026-Q3", "value": 100}], "snapshot_id": "x"}, policy)
        previous = {"setup_id": "prior-plan", "direction": "BUY", "original_plan": ai_proposal()}
        article = {"ticker": "TEST", "url": "https://example.test/earnings", "headline": "Earnings", "summary": "Published facts"}
        response = {
            "macro": {"sentiment": 0.1, "relevance": 1, "novelty": 0.5, "impact_score": 50, "brief_summary": "Macro",
                      "assessments": [{"series_id": "GDPC1", "directional_effect": 1, "reason": "Gemini judgment"}]},
            "articles": [{"item_id": 0, "relevant_to_instrument": True, "sentiment": 1, "relevance": 1,
                          "credibility": None, "novelty": 1, "impact_score": 90, "event_type": "earnings",
                          "brief_summary": "Earnings", "importance_score": 90, "importance_reason": "Relevant for 5m",
                          "event_key": "earnings-q3", "effect_horizon": "intraday"}],
            "trade": {**ai_proposal(), "direction_score": 78.75, "entry_mode": "LIMIT", "entry_condition": "At support",
                      "invalidation_price": 98, "invalidation_condition": "Support broken", "valid_for_bars": 12},
            "previous_setup_review": {"previous_setup_id": "prior-plan", "assessment": "CONFIRMED",
                                      "confidence_in_previous": 80, "technical_break": False, "macro_opposition": False,
                                      "macro_opposition_strength": 0, "macro_event_id": None,
                                      "entry_confirmed": False, "entry_evidence_bar_at": None, "reason": "Still valid"},
            "technical_direction_score": 80,
        }
        with patch("google.genai.Client") as client, patch.object(news_engine, "_ai_retry_after", 0):
            generate = client.return_value.__enter__.return_value.models.generate_content
            generate.return_value = SimpleNamespace(text=json.dumps(response))
            macro, articles, trade, method = news_engine._gemini_context_analysis(
                "TEST", "5m", technical(), [article], hard, "test-key", "test-context",
                previous_signal=previous, policy=policy, analysis_bar_at=900000)
        self.assertEqual(method, "gemini")
        self.assertEqual(macro["weighted_direction_score"], 55)
        self.assertEqual(articles[0]["importance_score"], 90)
        # Explicit 50/25/25 policy: 80*.5 + macro 55*.25 + news (50+50*.9)*.25.
        self.assertEqual(trade["decision_breakdown"]["weighted_direction_score"], 77.5)
        self.assertEqual(trade["previous_setup_review"]["previous_setup_id"], "prior-plan")
        arguments = generate.call_args.kwargs
        payload = json.loads(arguments["contents"].rsplit("\n", 1)[1])
        self.assertEqual(payload["previous_signal"], previous)
        self.assertEqual(payload["hard_economic_data"][0]["importance_pct"], 10)
        self.assertNotIn("tools", arguments["config"])

    def test_normal_news_change_does_not_requery_same_candle_but_macro_change_does(self):
        hard = {"items": [], "snapshot_id": "a", "sources": []}
        feed = {"items": [{"url": "https://example.test/original", "headline": "Original ordinary headline", "published_ts": 1}],
                "sources": [], "feed_items_received": 1, "locally_matched": 0, "selection_limit": 30}
        macro = {"ticker": "TEST", "snapshot_id": "a", "sentiment": 0, "relevance": 1, "credibility": 1,
                 "novelty": 0, "impact_score": 0, "summary": "Test", "analysis_method": "gemini", "analyzed_at": 1}
        with patch("news_engine.get_hard_economic_data", return_value=hard), \
             patch("news_engine.fetch_news_bundle", return_value=feed), \
             patch("news_engine._gemini_context_analysis", return_value=(macro, [], ai_proposal(), "gemini")) as analyze:
            first = news_engine.get_news_context_and_store("TEST", "key", technical=technical(), timeframe="5m", analysis_bar_at=900000)
            feed["items"].append({"url": "https://example.test/new", "headline": "New ordinary headline", "published_ts": 2})
            news_engine._analysis_cache.clear()  # also verify persistence across process cache loss
            repeated = news_engine.get_news_context_and_store("TEST", "key", technical=technical(), timeframe="5m", analysis_bar_at=900000)
            self.assertEqual(analyze.call_count, 1)
            self.assertEqual(first["pipeline"]["context_id"], repeated["pipeline"]["context_id"])
            self.assertTrue(repeated["pipeline"]["cache_hit"])
            hard["snapshot_id"] = "new-official-observation"
            news_engine.get_news_context_and_store("TEST", "key", technical=technical(), timeframe="5m", analysis_bar_at=900000)
            self.assertEqual(analyze.call_count, 2)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(LifecycleTests))
    for name in WeightingAndContextTests.__dict__:
        if name.startswith("test_"):
            suite.addTest(WeightingAndContextTests(name))
    return suite


if __name__ == "__main__":
    unittest.main()
