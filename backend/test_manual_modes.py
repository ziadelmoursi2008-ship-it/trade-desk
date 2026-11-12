"""Manual mode selection, independent state and technical-only execution."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import database
import main
import news_engine
from analysis_modes import get_analysis_mode, set_analysis_mode, ModeChangedError
from decision_engine import validate_ai_trade_decision
from signal_manager import get_signal_state, manage_signal, signal_history
from technical_strategy import build_technical_plan
from test_platform import ai_proposal, candles, empty_news_context


def technical(direction=1):
    return {"latest_close": 100, "atr": 1, "warmup_complete": True,
            "support": 97 if direction == 1 else 96, "local_support": 98 if direction == 1 else 98,
            "resistance": 104 if direction == 1 else 103, "local_resistance": 102,
            "ema": {}, "vwap": None, "volume_profile": {}, "market_structure": {},
            "indicator_groups": {"comparison": "CONFIRMED_BULLISH" if direction == 1 else "CONFIRMED_BEARISH",
                                 "trend_structure": {"score": 80 * direction, "direction": "BULLISH" if direction == 1 else "BEARISH", "available_components": 6},
                                 "momentum_participation": {"score": 80 * direction, "direction": "BULLISH" if direction == 1 else "BEARISH", "available_components": 8}}}


class TechnicalStrategyTests(unittest.TestCase):
    def test_old_swing_with_large_reward_risk_is_not_a_nearby_entry(self):
        for direction in (1, -1):
            with self.subTest(direction=direction):
                entry = 100 - direction * 9
                targets = [entry + direction * distance for distance in (5, 6)]
                data = {**technical(direction), "support": None, "local_support": None,
                        "resistance": None, "local_resistance": None,
                        "market_structure": {"swings": {
                            "lows" if direction == 1 else "highs": [{"price": entry}],
                            "highs" if direction == 1 else "lows": [{"price": target} for target in targets]}}}
                _, decision = build_technical_plan("TEST", "15m", data)
                self.assertEqual(decision["signal_type"], "NEUTRAL")
                self.assertIsNone(decision["entry_price"])

    def test_large_reward_risk_does_not_outweigh_a_nearby_valid_entry(self):
        for direction in (1, -1):
            with self.subTest(direction=direction):
                near = 100 - direction * 0.2
                data = {**technical(direction),
                        "support": near if direction == 1 else 99.5,
                        "local_support": None if direction == 1 else 99,
                        "resistance": 100.5 if direction == 1 else near,
                        "local_resistance": 101 if direction == 1 else None,
                        "market_structure": {"swings": {
                            "lows" if direction == 1 else "highs": [{"price": 100 - direction * 1.8}]}}}
                _, decision = build_technical_plan("TEST", "15m", data)
                self.assertEqual(decision["signal_type"], "BUY" if direction == 1 else "SELL")
                self.assertAlmostEqual(decision["entry_price"], near)

    def test_future_long_and_short_have_structure_based_levels(self):
        for direction in (1, -1):
            proposal, decision = build_technical_plan("TEST", "15m", technical(direction))
            self.assertEqual(decision["signal_type"], "BUY" if direction == 1 else "SELL")
            self.assertEqual(decision["decision_source"], "python")
            self.assertGreater(direction * (100 - decision["entry_price"]), 0)
            self.assertGreater(direction * (decision["entry_price"] - decision["stop_loss"]), 0)
            self.assertGreater(direction * (decision["take_profit_2"] - decision["take_profit_1"]), 0)
            self.assertGreaterEqual(decision["risk_reward_ratio"], 1.5)
            self.assertEqual(len(proposal["level_basis"]), 4)

    def test_conflicting_groups_remain_neutral(self):
        data = technical()
        data["indicator_groups"]["momentum_participation"].update(direction="BEARISH", score=-80)
        proposal, decision = build_technical_plan("TEST", "15m", data)
        self.assertEqual(decision["signal_type"], "NEUTRAL")
        self.assertIsNone(decision["entry_price"])

    def test_nearby_obstacle_is_not_skipped_to_force_risk_reward(self):
        data = {**technical(), "current_price": 100.5, "support": 99.5, "local_support": None, "resistance": 101, "local_resistance": 99.9}
        proposal, decision = build_technical_plan("TEST", "15m", data)
        self.assertEqual(decision["signal_type"], "BUY")
        self.assertAlmostEqual(decision["entry_price"], 99.5)
        self.assertAlmostEqual(decision["take_profit_1"], 99.9)
        self.assertGreaterEqual(decision["risk_reward_ratio"], 1)
        self.assertLess(decision["risk_reward_ratio"], 1.5)

    def test_one_real_target_is_sufficient_without_inventing_a_second(self):
        data = {**technical(), "resistance": 102, "local_resistance": 102}
        _, decision = build_technical_plan("TEST", "15m", data)
        self.assertEqual(decision["signal_type"], "BUY")
        self.assertEqual(decision["take_profit_1"], 102)
        self.assertIsNone(decision["take_profit_2"])

    def test_missing_atr_is_not_replaced_by_invented_levels(self):
        _, decision = build_technical_plan("TEST", "15m", {**technical(), "atr": None})
        self.assertEqual(decision["signal_type"], "NEUTRAL")
        self.assertIsNone(decision["stop_loss"])

    def test_distant_entries_beyond_plausibility_are_not_selected(self):
        data = {**technical(), "support": 70, "local_support": None}
        _, decision = build_technical_plan("TEST", "15m", data)
        self.assertEqual(decision["signal_type"], "NEUTRAL")

    def test_gemini_validator_does_not_automatically_allow_python_plans(self):
        result = validate_ai_trade_decision("TEST", "15m", technical(), ai_proposal(), analysis_method="python")
        self.assertEqual(result["signal_type"], "NEUTRAL")


class ManualModeApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="manual-mode-tests-")
        self.environment = patch.dict(os.environ, {"TRADING_DB_PATH": str(Path(self.directory.name) / "test.db"), "GEMINI_API_KEY": ""})
        self.environment.start()
        self.client = TestClient(main.app)
        self.client.__enter__()
        main._scan_cache.clear()
        main._analysis_cache.clear()
        news_engine._analysis_cache.clear()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.environment.stop()
        self.directory.cleanup()

    def choose(self, mode):
        response = self.client.post("/api/analysis-mode", json={"mode": mode})
        self.assertEqual(response.status_code, 200)

    def test_only_manual_endpoint_changes_selection_and_it_survives_initialization(self):
        self.assertEqual(self.client.get("/api/analysis-mode").json()["mode"], "gemini")
        self.choose("python")
        database.init_db()
        self.assertEqual(get_analysis_mode(), "python")
        self.assertTrue(self.client.get("/api/analysis-mode").json()["manual_only"])
        self.assertEqual(self.client.post("/api/analysis-mode", json={"mode": "auto"}).status_code, 422)
        self.assertEqual(get_analysis_mode(), "python")
        self.choose("gemini")
        self.assertEqual(get_analysis_mode(), "gemini")

    def test_python_analysis_never_calls_ai_news_or_macro(self):
        self.choose("python")
        frame = candles()
        frame.loc[:, ["Open", "Close"]] = 100.0
        frame.loc[:, "High"], frame.loc[:, "Low"] = 100.5, 99.5
        with patch("main.market_frame", return_value=frame), patch("main.run_technical_analysis", return_value=technical()), \
             patch("main.get_news_context_and_store", side_effect=AssertionError("News path used")) as news, \
             patch("main.historical_news", side_effect=AssertionError("Historical news read")), \
             patch("requests.get", side_effect=AssertionError("Unexpected HTTP request")), \
             patch("google.genai.Client", side_effect=AssertionError("Gemini used")) as ai:
            response = self.client.get("/api/analysis?ticker=TEST&timeframe=15m&mode=python")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["analysis_mode"], "python")
        self.assertEqual(result["decision"]["decision_source"], "python")
        self.assertEqual(result["forecast"]["direction"], "UP")
        self.assertEqual(result["forecast"]["reference_price"], 100)
        self.assertEqual(result["forecast"]["target_price"], 102)
        self.assertFalse(result["forecast"]["trade_eligible"])
        self.assertEqual(result["decision"]["signal_type"], "NEUTRAL")
        self.assertEqual(result["news"], [])
        self.assertEqual(result["hard_data"]["status"], "disabled")
        self.assertEqual(result["news_pipeline"]["ai_status"], "disabled")
        self.assertIsNone(result["macro_analysis"])
        ai.assert_not_called()
        news.assert_not_called()

    def test_python_api_updates_outlook_without_creating_pending_entries(self):
        self.choose("python")
        frames = []
        for price in (100, 104):
            frame = candles()
            frame.loc[:, ["Open", "Close"]] = float(price)
            frame.loc[:, "High"] = price + 0.5
            frame.loc[:, "Low"] = price - 0.5
            frames.append(frame)
        with patch("main.market_frame", side_effect=frames), \
             patch("main.load_lower_timeframe_contexts", return_value={}), \
             patch("main.run_technical_analysis", side_effect=[technical(), {**technical(), "latest_close": 104}]):
            first = self.client.get("/api/analysis?ticker=TEST&timeframe=15m&mode=python")
            second = self.client.get("/api/analysis?ticker=TEST&timeframe=15m&mode=python")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        initial, result = first.json()["forecast"], second.json()["forecast"]
        self.assertEqual(initial["reference_price"],100)
        self.assertEqual(result["reference_price"],104)
        self.assertEqual(result["status"],"NO_TARGET")
        self.assertIsNone(get_signal_state("TEST","15m","python"))

    def test_stale_gemini_requests_are_blocked_after_manual_switch(self):
        self.choose("python")
        with patch("main.market_frame") as market, patch("main.get_news_context_and_store") as news:
            response = self.client.get("/api/analysis?mode=gemini")
            scan = self.client.get("/api/scan?mode=gemini")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(scan.status_code, 409)
        market.assert_not_called()
        news.assert_not_called()

    def test_ai_failure_does_not_switch_to_python(self):
        with patch("main.market_frame", return_value=candles()), patch("main.get_news_context_and_store", return_value=empty_news_context()):
            result = self.client.get("/api/analysis?mode=gemini")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["analysis_mode"], "gemini")
        self.assertEqual(get_analysis_mode(), "gemini")

    def test_mode_change_during_ai_request_does_not_commit_a_stale_result(self):
        def interrupted(*args, **kwargs):
            set_analysis_mode("python")
            return empty_news_context()
        with patch("main.market_frame", return_value=candles()), patch("main.get_news_context_and_store", side_effect=interrupted):
            result = self.client.get("/api/analysis?mode=gemini")
        self.assertEqual(result.status_code, 409)
        self.assertIsNone(get_signal_state("NVDA", "15m", "gemini"))

    def test_python_scanner_uses_only_technical_strategy(self):
        self.choose("python")
        with patch("main.DEFAULT_UNIVERSE", ["TEST"]), patch("main.market_frame", return_value=candles()), \
             patch("main.run_technical_analysis", return_value=technical()), \
             patch("main.get_news_context_and_store", side_effect=AssertionError("News path used")) as news:
            response = self.client.get("/api/scan?timeframe=15m&min_strength=65&mode=python")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["analysis_mode"], "python")
        self.assertEqual(response.json()["analyzed"], 1)
        self.assertEqual(response.json()["errors"], [])
        news.assert_not_called()

    def test_states_and_histories_are_separate_and_switch_back_preserves_ai_plan(self):
        ai = ai_proposal()
        decision = validate_ai_trade_decision("TEST", "15m", technical(), ai, analysis_method="gemini")
        first = manage_signal("TEST", "15m", decision, ai, analysis_method="gemini", context_id="ai", bar_at=1,
                              completed_bar_times=[1], market_bars=[], now=1000)
        self.choose("python")
        proposal, decision = build_technical_plan("TEST", "15m", technical())
        second = manage_signal("TEST", "15m", decision, proposal, analysis_method="python", context_id="technical", bar_at=1,
                               completed_bar_times=[1], market_bars=[], now=1000, mode="python")
        self.assertNotEqual(first["setup_id"], second["setup_id"])
        self.assertEqual(len(signal_history("TEST", "15m", mode="gemini")), 1)
        self.assertEqual(len(signal_history("TEST", "15m", mode="python")), 1)
        self.choose("gemini")
        self.assertEqual(get_signal_state("TEST", "15m")["setup_id"], first["setup_id"])
        self.assertEqual(get_signal_state("TEST", "15m", "python")["setup_id"], second["setup_id"])

    def test_python_websocket_reads_only_python_state(self):
        self.choose("python")
        with patch("main.fetch_stock_df", return_value=candles()):
            with self.client.websocket_connect("/ws/quotes/TEST?timeframe=15m&mode=python") as socket:
                response = socket.receive_json()
        self.assertEqual(response["analysis_mode"], "python")

    def test_news_engine_rejects_ai_calls_while_python_is_selected(self):
        self.choose("python")
        with patch("google.genai.Client") as client:
            with self.assertRaises(ModeChangedError):
                news_engine._gemini_context_analysis("TEST", "15m", technical(), [], {"items": []}, "key", "x")
        client.assert_not_called()

    def test_running_ai_response_stops_before_any_followup_after_manual_switch(self):
        def answer_after_switch(**kwargs):
            set_analysis_mode("python")
            return SimpleNamespace(text='{}')
        with patch("google.genai.Client") as client, patch.object(news_engine, "_ai_retry_after", 0), \
             patch.dict(news_engine._ai_context_retry_after, {}, clear=True):
            generate = client.return_value.__enter__.return_value.models.generate_content
            generate.side_effect = answer_after_switch
            with self.assertRaises(ModeChangedError):
                news_engine._gemini_context_analysis("TEST", "15m", technical(), [], {"items": []}, "key", "x")
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(get_analysis_mode(), "python")


if __name__ == "__main__":
    unittest.main()
