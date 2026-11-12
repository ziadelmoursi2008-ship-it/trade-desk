"""Provider failures, recovery and future-entry regressions without external requests."""

import json
import os
import tempfile
import time
import unittest
from email.utils import formatdate
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

import database
import news_engine
import news_feeds
from decision_engine import validate_ai_trade_decision
from signal_manager import manage_signal, monitor_signal_market
from test_platform import ai_proposal, technical


def rss(entries):
    body = ''.join(f'<item><title>{title}</title><link>https://example.test/{i}</link><pubDate>{formatdate(time.time()-age, usegmt=True)}</pubDate></item>'
                   for i, (title, age) in enumerate(entries))
    return ('<rss version="2.0"><channel><title>Test</title>' + body + '</channel></rss>').encode()


def response(status=200, content=None):
    result = requests.Response()
    result.status_code = status
    result._content = content if content is not None else rss([("Gold news", 60)])
    result.url = "https://example.test/feed"
    return result


class NewsReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="news-reliability-tests-")
        self.environment = patch.dict(os.environ, {"TRADING_DB_PATH": str(Path(self.directory.name) / "test.db"),
                                                   "NEWS_FEED_ITEMS": "30", "DECISION_WEIGHTS_JSON": "", "MACRO_WEIGHTS_JSON": ""})
        self.environment.start()
        database.init_db()
        news_engine._feed_cache.clear()
        news_engine._analysis_cache.clear()
        news_engine._ai_context_retry_after.clear()
        news_engine._ai_retry_after = 0

    def tearDown(self):
        news_engine._ai_context_retry_after.clear()
        news_engine._ai_retry_after = 0
        self.environment.stop()
        self.directory.cleanup()

    def test_both_failed_feeds_are_unavailable_not_empty(self):
        with patch("news_feeds.requests.get", side_effect=requests.ConnectionError("offline")):
            result = news_feeds.fetch_sources("XAUUSD", "Gold", "GC=F")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["items"], [])
        self.assertTrue(all(row["status"] == "error" for row in result["source_statuses"]))

    def test_last_success_survives_failure_and_process_cache_loss(self):
        with patch("news_feeds.requests.get", return_value=response()):
            first = news_feeds.fetch_feed("https://example.test/feed", "Google News", "XAUUSD")
        news_engine._feed_cache.clear()
        with patch("news_feeds.requests.get", side_effect=requests.Timeout("timeout")):
            fallback = news_feeds.fetch_feed("https://example.test/feed", "Google News", "XAUUSD")
        self.assertEqual(fallback["status"], "cached")
        self.assertEqual(fallback["items"][0]["headline"], "Gold news")
        self.assertEqual(fallback["last_success_at"], first["last_success_at"])
        self.assertEqual(fallback["error_code"], "timeout")

    def test_recent_items_are_selected_before_limit_even_if_feed_is_unsorted(self):
        entries = [(f"Old news {i}", 100 * 3600) for i in range(40)] + [("Recent gold news", 60)]
        with patch("news_feeds.requests.get", return_value=response(content=rss(entries))):
            result = news_feeds.fetch_feed("https://example.test/feed", "Google News", "XAUUSD")
        self.assertEqual(result["received_count"], 41)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["headline"], "Recent gold news")

    def test_expired_snapshot_does_not_reintroduce_old_articles(self):
        with patch("news_feeds.requests.get", return_value=response(content=rss([("Expired", 73 * 3600)]))):
            news_feeds.fetch_feed("https://example.test/feed", "Google News", "XAUUSD")
        with patch("news_feeds.requests.get", side_effect=requests.Timeout("timeout")):
            result = news_feeds.fetch_feed("https://example.test/feed", "Google News", "XAUUSD")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["items"], [])

    def test_html_error_page_is_not_a_successfully_empty_feed(self):
        with patch("news_feeds.requests.get", return_value=response(content=b'<html>Try again later</html>')):
            result = news_feeds.fetch_feed("https://example.test/feed", "Google News", "XAUUSD")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "invalid_feed")

    def test_transient_http_error_retries_once(self):
        with patch("news_feeds.requests.get", side_effect=[response(503), response()]) as get:
            result = news_feeds.fetch_feed("https://example.test/feed", "Google News", "XAUUSD")
        self.assertEqual(get.call_count, 2)
        self.assertEqual(result["status"], "live")
        self.assertIsNone(result["error_code"])

    def test_rate_limited_feed_is_not_retried_in_a_loop(self):
        with patch("news_feeds.requests.get", return_value=response(429)) as get:
            result = news_feeds.fetch_feed("https://example.test/feed", "Google News", "XAUUSD")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(result["error_code"], "rate_limit")

    def test_valid_empty_rss_is_distinguished_from_failure(self):
        with patch("news_feeds.requests.get", return_value=response(content=rss([]))):
            result = news_feeds.fetch_sources("XAUUSD", "Gold", "GC=F")
        self.assertEqual(result["status"], "empty")
        self.assertTrue(all(row["status"] == "empty" for row in result["source_statuses"]))

    def test_failed_bundle_is_rechecked_after_short_interval(self):
        acquisition = {"items": [], "feed_items_received": 0, "status": "unavailable", "checked_at": time.time(), "source_statuses": []}
        with patch("news_engine.fetch_sources", return_value=acquisition) as fetch:
            news_engine.fetch_news_bundle("XAUUSD")
            saved = news_engine._feed_cache["XAUUSD"]
            news_engine._feed_cache["XAUUSD"] = (time.monotonic() - 16, saved[1])
            news_engine.fetch_news_bundle("XAUUSD")
        self.assertEqual(fetch.call_count, 2)

    def test_recovery_invalidates_empty_same_candle_analysis(self):
        hard = {"items": [], "snapshot_id": "hard", "sources": []}
        feed = {"items": [], "sources": [], "feed_items_received": 0, "locally_matched": 0, "selection_limit": 30,
                "status": "unavailable", "source_statuses": [{"source": "Google News", "recent_count": 0, "status": "error"}]}
        macro = {"ticker": "XAUUSD", "snapshot_id": "hard", "sentiment": 0, "relevance": 1, "credibility": 1,
                 "novelty": 0, "impact_score": 0, "summary": "Test", "analysis_method": "gemini", "analyzed_at": 1}
        with patch("news_engine.get_hard_economic_data", return_value=hard), patch("news_engine.fetch_news_bundle", return_value=feed), \
             patch("news_engine._gemini_context_analysis", return_value=(macro, [], ai_proposal(), "gemini")) as analyze:
            first = news_engine.get_news_context_and_store("XAUUSD", "key", technical=technical(), timeframe="5m", analysis_bar_at=900000)
            feed.update(status="live", items=[{"url": "https://example.test/new", "headline": "Gold news", "published_ts": 2}])
            feed["source_statuses"][0].update(status="live", recent_count=1)
            second = news_engine.get_news_context_and_store("XAUUSD", "key", technical=technical(), timeframe="5m", analysis_bar_at=900000)
        self.assertEqual(analyze.call_count, 2)
        self.assertNotEqual(first["pipeline"]["context_id"], second["pipeline"]["context_id"])
        self.assertEqual(second["pipeline"]["feed_status"], "live")

    def test_single_instrument_timeout_does_not_disable_other_instruments(self):
        failure = type("Unavailable", (Exception,), {"code": 503})
        hard = {"items": [], "snapshot_id": "test"}
        with patch("google.genai.Client") as client:
            generate = client.return_value.__enter__.return_value.models.generate_content
            generate.side_effect = failure("provider offline")
            first = news_engine._gemini_context_analysis("XAUUSD", "5m", technical(), [], hard, "key", "a")
            news_engine._gemini_context_analysis("BTC-USD", "5m", technical(), [], hard, "key", "b")
            news_engine._gemini_context_analysis("XAUUSD", "5m", technical(), [], hard, "key", "a")
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(first[2]["analysis_error"]["kind"], "service_unavailable")
        self.assertEqual(news_engine._ai_retry_after, 0)

    def test_future_plan_survives_arithmetic_error_and_waits_for_entry(self):
        proposal = {**ai_proposal(-1), "direction_score": 0, "entry_price": 101.5, "entry_zone_min": 101.3,
                    "entry_zone_max": 101.7, "stop_loss": 103.5, "take_profit_1": 97.5, "take_profit_2": 95.5,
                    "entry_condition": "Pullback an den Widerstand", "decision_breakdown": {"weighted_direction_score": 30}}
        decision = validate_ai_trade_decision("XAUUSD", "5m", technical(), proposal, analysis_method="gemini")
        quote = {"timestamp": 900000, "open": 100, "high": 100, "low": 100, "close": 100}
        result = manage_signal("XAUUSD", "5m", decision, proposal, analysis_method="gemini", context_id="planned", bar_at=900000,
                               completed_bar_times=[900000], market_bars=[quote], now=1000000)
        self.assertEqual(result["signal_type"], "SELL")
        self.assertEqual(result["lifecycle_status"], "VORBEREITET")
        self.assertEqual(result["entry_price"], 101.5)
        quote.update(close=101.5, high=101.5)
        active = monitor_signal_market("XAUUSD", "5m", [quote], now=1100000)
        self.assertEqual(active["lifecycle_status"], "AKTIV")

    def test_explicit_confirmation_requirement_prevents_price_touch_activation(self):
        proposal = {**ai_proposal(), "requires_entry_confirmation": True, "entry_mode": "LIMIT"}
        decision = validate_ai_trade_decision("TEST", "5m", technical(), proposal, analysis_method="gemini")
        self.assertEqual(decision["entry_mode"], "CONFIRMATION")

    def test_local_schema_requires_one_result_for_every_article_and_macro_series(self):
        schema = news_engine.context_response_schema(30, 12).model_json_schema()
        self.assertEqual(schema["properties"]["articles"]["minItems"], 30)
        self.assertEqual(schema["properties"]["articles"]["maxItems"], 30)
        self.assertEqual(schema["$defs"]["CompleteMacroEvaluation"]["properties"]["assessments"]["minItems"], 12)

    def test_schema_rejects_omitted_article_results_instead_of_silently_using_keywords(self):
        from pydantic import ValidationError
        schema = news_engine.context_response_schema(2)
        with self.assertRaises(ValidationError) as error:
            schema.model_validate({"articles": []})
        self.assertTrue(any(row["loc"] == ("articles",) and row["type"] == "too_short" for row in error.exception.errors()))

    def test_quote_captured_before_ai_response_cannot_activate_new_plan(self):
        proposal = ai_proposal()
        decision = validate_ai_trade_decision("TEST", "5m", technical(), proposal, analysis_method="gemini")
        quote = {"timestamp": 900000, "open": 100, "high": 100, "low": 100, "close": 100, "observed_at": 990000}
        result = manage_signal("TEST", "5m", decision, proposal, analysis_method="gemini", context_id="delayed", bar_at=900000,
                               completed_bar_times=[900000], market_bars=[quote], now=1000000)
        self.assertEqual(result["lifecycle_status"], "VORBEREITET")
        quote["observed_at"] = 1000100
        active = monitor_signal_market("TEST", "5m", [quote], now=1000100)
        self.assertEqual(active["lifecycle_status"], "AKTIV")

    def test_invalid_plan_is_repaired_once_without_reanalyzing_news(self):
        review = {"previous_setup_id": None, "assessment": "NONE", "confidence_in_previous": 0, "technical_break": False,
                  "macro_opposition": False, "macro_opposition_strength": 0, "macro_event_id": None,
                  "entry_confirmed": False, "entry_evidence_bar_at": None, "reason": "No previous plan"}
        plan = {**ai_proposal(), "entry_mode": "LIMIT", "entry_condition": "At support", "invalidation_price": 98,
                "invalidation_condition": "Support broken", "valid_for_bars": 12, "take_profit_1": 101}
        initial = {"macro": {"sentiment": 0, "relevance": 1, "novelty": 0, "impact_score": 0, "brief_summary": "Macro", "assessments": []},
                   "articles": [], "trade": plan, "previous_setup_review": review, "technical_direction_score": 80}
        repaired = {**plan, "entry_price": 98.5, "entry_zone_min": 98.3, "entry_zone_max": 98.7,
                    "stop_loss": 96.5, "invalidation_price": 96.5, "take_profit_1": 101.5, "take_profit_2": 103.5}
        with patch("google.genai.Client") as client:
            generate = client.return_value.__enter__.return_value.models.generate_content
            generate.side_effect = [SimpleNamespace(text=json.dumps(initial)), SimpleNamespace(text=json.dumps({"trade": repaired}))]
            macro, articles, trade, method = news_engine._gemini_context_analysis("TEST", "5m", technical(), [],
                                            {"items": [], "snapshot_id": "x"}, "key", "test")
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(method, "gemini")
        self.assertEqual(trade["plan_repair"], "completed")
        self.assertEqual(trade["entry_price"], 98.5)
        self.assertEqual(validate_ai_trade_decision("TEST", "5m", technical(), trade, analysis_method=method)["signal_type"], "BUY")

    def test_missing_article_is_completed_once_and_never_claimed_as_a_keyword_ai_evaluation(self):
        review = {"previous_setup_id": None, "assessment": "NONE", "confidence_in_previous": 0, "technical_break": False,
                  "macro_opposition": False, "macro_opposition_strength": 0, "macro_event_id": None,
                  "entry_confirmed": False, "entry_evidence_bar_at": None, "reason": "No previous plan"}
        plan = {**ai_proposal(), "entry_mode": "LIMIT", "entry_condition": "At support", "invalidation_price": 98,
                "invalidation_condition": "Support broken", "valid_for_bars": 12}
        initial = {"macro": {"sentiment": 0, "relevance": 1, "novelty": 0, "impact_score": 0, "brief_summary": "Macro", "assessments": []},
                   "articles": [], "trade": plan, "previous_setup_review": review, "technical_direction_score": 80}
        article = {"item_id": 0, "relevant_to_instrument": True, "sentiment": 0.5, "relevance": 1, "credibility": None,
                   "novelty": 1, "impact_score": 80, "event_type": "earnings", "brief_summary": "Known facts",
                   "importance_score": 80, "importance_reason": "Relevant for this timeframe", "event_key": "earnings", "effect_horizon": "intraday"}
        raw_article = {"ticker": "TEST", "headline": "TEST earnings", "url": "https://example.test/news", "summary": "Facts"}
        with patch("google.genai.Client") as client:
            generate = client.return_value.__enter__.return_value.models.generate_content
            generate.side_effect = [SimpleNamespace(text=json.dumps(initial)),
                                    SimpleNamespace(text=json.dumps({"articles": [article], "macro_assessments": []}))]
            macro, articles, trade, method = news_engine._gemini_context_analysis("TEST", "5m", technical(), [raw_article],
                                            {"items": [], "snapshot_id": "x"}, "key", "test")
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(method, "gemini")
        self.assertEqual(articles[0]["analysis_method"], "gemini")
        self.assertEqual(articles[0]["importance_score"], 80)
        self.assertTrue(trade["assessments_completed"])
        self.assertIs(generate.call_args_list[0].kwargs["config"]["response_schema"], news_engine.ContextEvaluation)


if __name__ == "__main__":
    unittest.main()
