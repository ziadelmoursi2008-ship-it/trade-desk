"""Offline regression tests. Synthetic prices exist only in these isolated fixtures."""
import copy
import os
import sqlite3
import tempfile
import shutil
import uuid
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

import backtesting
import database
import economic_data
import main
import market_data
import news_engine
from decision_engine import validate_ai_trade_decision
from instruments import normalize_ticker, source_ticker
from technical_analysis import build_indicator_frame, calculate_atr, calculate_rsi, calculate_vwap, run_technical_analysis


def candles(count=260):
    index = pd.date_range(end=pd.Timestamp.now(tz="UTC").floor("15min") - pd.Timedelta(minutes=15), periods=count, freq="15min")
    close = 100 + np.arange(count) * 0.02 + np.sin(np.arange(count) / 4) * 0.4
    frame = pd.DataFrame({"Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close, "Volume": 1000.0}, index=index)
    frame.attrs = {"currency": "USD", "instrument_type": "CRYPTOCURRENCY", "exchange_timezone": "UTC", "fetched_at": time.time(), "timeframe": "15m"}
    return frame


def technical(direction=1):
    return {"latest_close": 100.0, "atr": 1.0, "support": 95.0, "resistance": 105.0,
            "local_support": 99.0, "local_resistance": 101.0,
            "ema": {"20": 99.5 if direction == 1 else 100.5, "50": 98 if direction == 1 else 102, "200": 97 if direction == 1 else 103},
            "vwap": 99 if direction == 1 else 101, "rsi": 60 if direction == 1 else 40,
            "trend": "BULLISH" if direction == 1 else "BEARISH",
            "macd": {"hist": direction * 0.1}, "momentum": "STRONG_UP" if direction == 1 else "STRONG_DOWN",
            "breakout": "NONE", "warmup_complete": True}


def neutral():
    return {"signal_type": "NEUTRAL"}


def empty_news_context():
    return {"news": [], "hard_data": {"items": [], "snapshot_id": "empty", "fetched_at": None, "status": "stale", "sources": []},
            "macro_analysis": None, "ai_trade_proposal": None, "analysis_method": "keywords",
            "pipeline": {"normal_sources": [], "hard_sources": [], "normal_candidates": 0,
            "normal_relevant": 0, "ai_batching": "one combined context request", "ai_search_enabled": False}}


def execution_candles():
    frame = candles(60)
    frame.loc[:, ["Open", "Close"]] = 101.0
    frame.loc[:, "High"] = 102.0
    frame.loc[:, "Low"] = 100.5
    return frame


def limit_setup():
    return {"signal_type": "BUY", "entry_price": 100.0, "stop_loss": 98.0, "take_profit_1": 104.0, "take_profit_2": 106.0}


def ai_proposal(direction=1):
    return {
        "signal_type": "BUY" if direction == 1 else "SELL", "direction_score": 80 if direction == 1 else 20,
        "confidence": 80, "setup_quality": "A", "entry_price": 100.0,
        "entry_zone_min": 99.8, "entry_zone_max": 100.2, "stop_loss": 98.0 if direction == 1 else 102.0,
        "take_profit_1": 104.0 if direction == 1 else 96.0, "take_profit_2": 106.0 if direction == 1 else 94.0,
        "group_comparison": "CONFIRMED_BULLISH" if direction == 1 else "CONFIRMED_BEARISH",
        "reasons": ["Beide Gruppen bestätigen die Richtung."], "contradictions": [],
        "level_basis": ["Entry an Strukturzone", "Stop hinter Invalidation", "Ziele an Liquidität"],
    }


class EconomicDataTests(unittest.TestCase):
    def test_rate_series_does_not_mislabel_yearly_ratio_as_percentage_change(self):
        observations = [(f"2026-{month:02d}", 4.0 + month / 100, False) for month in range(1, 13)] + [("2027-01", 4.2, False)]
        config = {"name": "Rate", "category": "labor", "unit": "%", "mode": "difference", "change_unit": "Prozentpunkte", "year_lag": 12}
        result = economic_data._record("RATE", config, observations, "Official", "https://example.test", time.time())
        self.assertIsNone(result["year_over_year_pct"])
        self.assertAlmostEqual(result["change_value"], 0.08)


class IndicatorTests(unittest.TestCase):
    def test_rsi_rising_falling_and_flat(self):
        self.assertEqual(calculate_rsi(pd.Series(range(50), dtype=float)).iloc[-1], 100)
        self.assertEqual(calculate_rsi(pd.Series(range(50, 0, -1), dtype=float)).iloc[-1], 0)
        self.assertEqual(calculate_rsi(pd.Series([100.0] * 50)).iloc[-1], 50)

    def test_no_future_data_in_indicators(self):
        data = candles()
        prefix = build_indicator_frame(data.iloc[:100])
        full = build_indicator_frame(data)
        pd.testing.assert_frame_equal(prefix, full.iloc[:100])
        self.assertTrue(calculate_atr(data.High, data.Low, data.Close).iloc[:13].isna().all())

    def test_no_substitute_ema_periods(self):
        result = run_technical_analysis(candles(70))
        self.assertIsNone(result["ema"]["100"])
        self.assertIsNone(result["ema"]["200"])
        self.assertIsNotNone(result["ema"]["50"])

    def test_two_independent_indicator_groups_and_structure_context_exist(self):
        result = run_technical_analysis(candles())
        groups = result["indicator_groups"]
        self.assertEqual(groups["trend_structure"]["name"], "Trend & Marktstruktur")
        self.assertEqual(groups["momentum_participation"]["name"], "Momentum & Marktteilnahme")
        self.assertGreaterEqual(groups["trend_structure"]["available_components"], 5)
        self.assertGreaterEqual(groups["momentum_participation"]["available_components"], 7)
        self.assertIn(groups["comparison"], ("CONFIRMED_BULLISH", "CONFIRMED_BEARISH", "CONFLICT", "PARTIAL_OR_NEUTRAL"))
        self.assertIn("fair_value_gaps", result["market_structure"])
        self.assertIn("order_blocks", result["market_structure"])

    def test_vwap_resets_and_missing_volume_is_not_invented(self):
        data = candles(4)
        data.index = pd.to_datetime(["2026-10-22 10:00Z", "2026-10-22 11:00Z", "2026-10-23 10:00Z", "2026-10-23 11:00Z"])
        data.loc[:, ["High", "Low", "Close"]] = np.array([100, 100, 200, 200])[:, None]
        self.assertEqual(calculate_vwap(data).iloc[-1], 200)
        data["Volume"] = 0
        self.assertTrue(calculate_vwap(data).isna().all())

    def test_partial_candle_excluded(self):
        data = candles(10)
        now = data.index[-1] + pd.Timedelta(minutes=5)
        self.assertEqual(len(market_data.completed_bars(data, "15m", now)), 9)

    def test_provider_session_timestamp_accepts_timezone_aware_value(self):
        timestamp = pd.Timestamp("2026-10-26 15:45:00", tz="America/New_York")
        attrs = {"session_end": pd.Timestamp("2026-10-26 20:00:00", tz="UTC"), "instrument_type": "EQUITY"}
        self.assertEqual(market_data.candle_end(timestamp, "15m", attrs), pd.Timestamp("2026-10-26 16:00:00", tz="America/New_York"))
        self.assertEqual(market_data._epoch_seconds(attrs["session_end"]), attrs["session_end"].timestamp())


class DecisionTests(unittest.TestCase):
    def test_prepared_entry_conditions_do_not_bypass_distance_limit(self):
        for direction in (1, -1):
            for mode in ("LIMIT", "STOP", "CONFIRMATION"):
                for distance, expected in ((1.5, "accepted"), (2.0, "accepted"), (2.01, "rejected"), (9.0, "rejected")):
                    with self.subTest(direction=direction, mode=mode, distance=distance):
                        price = 100 + direction * distance * (-1 if mode == "STOP" else 1)
                        proposal = {**ai_proposal(direction), "entry_mode": mode,
                                    "entry_condition": "Zukünftige technisch begründete Einstiegszone.",
                                    "invalidation_price": 100 - direction * 12}
                        result = validate_ai_trade_decision("TEST", "15m", {**technical(direction), "latest_close": price},
                                                            proposal, analysis_method="gemini")
                        self.assertEqual(result["validation_status"], expected)
                        if expected == "rejected":
                            self.assertIsNone(result["entry_price"])
                            self.assertIn("Entry-Abstand", result["reasons"][0])

    def test_valid_gemini_long_and_short_are_accepted(self):
        for direction in (1, -1):
            result = validate_ai_trade_decision("TEST", "15m", technical(direction), ai_proposal(direction), analysis_method="gemini")
            self.assertEqual(result["signal_type"], "BUY" if direction == 1 else "SELL")
            self.assertEqual(result["decision_source"], "gemini")
            self.assertEqual(result["validation_status"], "accepted")
            self.assertGreaterEqual(result["risk_reward_ratio"], 1.5)
            entry, stop, tp1, tp2 = [result[name] for name in ("entry_price", "stop_loss", "take_profit_1", "take_profit_2")]
            self.assertGreater(direction * (entry - stop), 0)
            self.assertGreater(direction * (tp1 - entry), 0)
            self.assertGreater(direction * (tp2 - tp1), 0)

    def test_invalid_or_weak_ai_levels_are_rejected(self):
        cases = [
            ({"atr": 0}, ai_proposal()),
            ({}, {**ai_proposal(), "confidence": 40}),
            ({}, {**ai_proposal(), "stop_loss": 101}),
            ({}, {**ai_proposal(), "take_profit_1": 101}),
            ({}, {**ai_proposal(), "entry_price": 120, "entry_zone_min": 119.8, "entry_zone_max": 120.2}),
        ]
        for changes, proposal in cases:
            result = validate_ai_trade_decision("TEST", "15m", {**technical(), **changes}, proposal, analysis_method="gemini")
            self.assertEqual(result["signal_type"], "NEUTRAL")
            self.assertIsNone(result["entry_price"])
            self.assertIsNone(result["risk_reward_ratio"])

    def test_no_python_trade_fallback_without_gemini(self):
        result = validate_ai_trade_decision("TEST", "15m", technical(), ai_proposal(), analysis_method="keywords")
        self.assertEqual(result["signal_type"], "NEUTRAL")
        self.assertEqual(result["decision_source"], "keywords")
        self.assertIn("kein Trade", result["reasons"][0])

    def test_gemini_neutral_is_accepted_without_trade_levels(self):
        proposal = {**ai_proposal(), "signal_type": "NEUTRAL", "entry_price": None, "entry_zone_min": None,
                    "entry_zone_max": None, "stop_loss": None, "take_profit_1": None, "take_profit_2": None}
        result = validate_ai_trade_decision("TEST", "15m", technical(), proposal, analysis_method="gemini")
        self.assertEqual(result["signal_type"], "NEUTRAL")
        self.assertEqual(result["validation_status"], "accepted_neutral")

    def test_scanner_filters_before_ranking(self):
        def candidate(ticker, profit, score=80, signal="BUY", grade="A", stale=False, opened=True):
            return {"ticker": ticker, "decision": {"signal_type": signal, "setup_quality": grade,
                    "strength": max(score, 100-score), "risk_reward_ratio": 2, "potential_profit_pct": profit},
                    "market": {"is_stale": stale, "market_open": opened}}
        items = [candidate("LOW", 2), candidate("BEST", 4, 10, "SELL"), candidate("WEAK", 9, 55),
                 candidate("BAD", 100, grade="C"), candidate("STALE", 99, stale=True), candidate("CLOSED", 50, opened=False)]
        self.assertEqual([item["ticker"] for item in main.rank_candidates(items, 75)], ["BEST", "LOW"])


class BacktestTests(unittest.TestCase):
    def test_old_rule_backtest_is_not_presented_as_ai_strategy(self):
        result = backtesting.run_backtest_strategy(candles())
        self.assertEqual(result["strategy_version"], "4.1-ai")
        self.assertEqual(result["total_trades"], 0)
        self.assertIn("Gemini + Quant", result["error"])
        self.assertIn("Keine rückwirkend erfundenen", result["scope"])

    def test_backtest_rejects_non_positive_capital(self):
        with self.assertRaises(ValueError):
            backtesting.run_backtest_strategy(candles(), initial_capital=0)


class DatabaseAndApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.gettempdir()) / 'ai-trading-tests' / uuid.uuid4().hex
        self.directory.mkdir(parents=True)
        self.environment = patch.dict(os.environ, {"TRADING_DB_PATH": str(self.directory / "test.db"), "GEMINI_API_KEY": ""})
        self.environment.start()
        news_engine._analysis_cache.clear()
        news_engine._feed_cache.clear()
        economic_data._cache = None
        main._analysis_cache.clear()
        main._scan_cache.clear()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.environment.stop()
        intended_root = (Path(tempfile.gettempdir()) / 'ai-trading-tests').resolve()
        if not self.directory.resolve().is_relative_to(intended_root) or self.directory.resolve() == intended_root:
            raise RuntimeError('Unexpected test cleanup path')
        shutil.rmtree(self.directory)

    def test_validates_timeframes_and_symbols(self):
        self.assertEqual(self.client.get("/api/klines?ticker=NVDA&timeframe=2m").status_code, 422)
        self.assertEqual(self.client.get("/api/klines?ticker=../../bad").status_code, 422)
        self.assertEqual(source_ticker(normalize_ticker("gold")), "GC=F")

    def test_provider_failure_never_produces_fake_candles(self):
        with patch("main.fetch_stock_df", side_effect=ConnectionError("offline")):
            response = self.client.get("/api/klines")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("klines", response.json())

    def test_gold_provenance_and_analysis_contract(self):
        data = candles()
        with patch("main.market_frame", return_value=data), patch("main.get_news_context_and_store", return_value=empty_news_context()):
            bars = self.client.get("/api/klines?ticker=XAUUSD").json()
            result = self.client.get("/api/analysis?ticker=XAUUSD")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(bars["market"]["source_ticker"], "GC=F")
        self.assertFalse(bars["is_realtime"])
        self.assertIn("Proxy", bars["market"]["proxy_note"])
        self.assertIn("backtest", result.json())

    def test_websocket_matches_selected_timeframe(self):
        with patch("main.fetch_stock_df", return_value=candles()):
            with self.client.websocket_connect("/ws/quotes/BTC-USD?timeframe=5m") as socket:
                message = socket.receive_json()
                self.assertEqual(message["timeframe"], "5m")
                self.assertEqual(len(message["klines"]), 2)
                self.assertFalse(message["market"]["is_realtime"])

    def test_signal_deduplication(self):
        result = validate_ai_trade_decision("TEST", "15m", technical(), ai_proposal(), as_of=1234567890, analysis_method="gemini")
        database.save_signal(result)
        database.save_signal(result)
        connection = database.get_connection()
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM trade_signals").fetchone()[0], 1)
        finally:
            connection.close()

    def test_analysis_returns_managed_setup_policy_and_history_endpoint(self):
        context = {**empty_news_context(), "analysis_method": "gemini", "ai_trade_proposal": {**ai_proposal(), "entry_condition": "Rücklauf an Support."},
                   "pipeline": {**empty_news_context()["pipeline"], "context_id": "integration-managed"}}
        frame = candles()
        frame.loc[:, ["Open", "High", "Low", "Close"]] -= float(frame.Close.iloc[-1]) - 101
        with patch("main.market_frame", return_value=frame), patch("main.get_news_context_and_store", return_value=context):
            result = self.client.get("/api/analysis?ticker=BTC-USD&timeframe=15m")
        self.assertEqual(result.status_code, 200)
        body = result.json()
        self.assertEqual(body["decision"]["lifecycle_status"], "VORBEREITET")
        self.assertEqual(body["decision"]["signal_label"], "STRONG_BUY")
        self.assertEqual(body["decision"]["entry_price"], 100)
        self.assertEqual(sum(body["decision_policy"]["decision_weights_pct"].values()), 100)
        history = self.client.get("/api/signals/history?ticker=BTC-USD&timeframe=15m").json()
        self.assertEqual(history["events"][0]["decision"]["setup_id"], body["decision"]["setup_id"])

    def test_news_cache_is_per_instrument_and_fallback_labeled(self):
        hard = {"items": [], "snapshot_id": "hard-test", "fetched_at": time.time(), "status": "cache", "sources": []}
        for ticker in ("NVDA", "AMD"):
            item = {"ticker": ticker, "headline": ticker + " announces earnings growth", "summary": "", "source": "Test",
                    "url": "https://example.test/shared", "published_at": "2026-10-25", "published_ts": time.time()}
            feed = {"items": [item], "feed_items_received": 1, "locally_matched": 1, "selection_limit": 20, "sources": ["Test RSS"]}
            with patch("news_engine.get_hard_economic_data", return_value=hard), patch("news_engine.fetch_news_bundle", return_value=feed):
                result = news_engine.get_latest_news_and_store(ticker)
            self.assertEqual(result[0]["ticker"], ticker)
            self.assertEqual(result[0]["analysis_method"], "keywords")
            self.assertIsNone(result[0]["credibility"])
        connection = database.get_connection()
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM news_items").fetchone()[0], 2)
        finally:
            connection.close()

    def test_prefilter_prioritizes_explicit_instrument_matches(self):
        now = time.time()
        vague = {"ticker": "NVDA", "headline": "Markets move before the opening bell", "summary": "", "source": "Yahoo",
                 "feed_source": "Yahoo Finance", "url": "https://example.test/vague", "published_ts": now}
        direct = {"ticker": "NVDA", "headline": "NVIDIA launches a new accelerator", "summary": "", "source": "Publisher",
                  "feed_source": "Google News", "url": "https://example.test/direct", "published_ts": now - 60}
        result = news_engine.prefilter_news("NVDA", [vague, direct], limit=2)
        self.assertEqual(result[0]["url"], direct["url"])
        self.assertEqual(result[0]["prefilter_score"], 1.0)

    def test_context_batches_hard_data_and_news_without_ai_search(self):
        now = time.time()
        hard = {"items": [{"series_id": "TEST", "name": "Testwert", "category": "rates", "period": "2026-10",
                            "value": 4.0, "unit": "%", "source": "Official", "fetched_at": now}],
                "snapshot_id": "batch-test", "fetched_at": now, "status": "live", "sources": ["Official"]}
        item = {"ticker": "NVDA", "headline": "NVIDIA test", "summary": "", "source": "Publisher", "feed_source": "Google News",
                "url": "https://example.test/batch", "published_at": "2026-10-26", "published_ts": now, "prefilter_score": 1.0}
        evaluated = {**item, "sentiment": 0.2, "relevance": 0.9, "credibility": None, "novelty": 0.7,
                     "impact_score": 40, "event_type": "product", "summary": "Relevant", "analysis_method": "gemini",
                     "analyzed_at": now, "context_id": "batch-test"}
        macro = {"ticker": "NVDA", "sentiment": -0.1, "relevance": 0.8, "credibility": 1.0, "novelty": 0.5,
                 "impact_score": 50, "summary": "Macro", "analysis_method": "gemini", "analyzed_at": now,
                 "snapshot_id": "batch-test", "published_ts": now, "event_type": "macro"}
        feed = {"items": [item], "feed_items_received": 1, "locally_matched": 1, "selection_limit": 20, "sources": ["Test RSS"]}
        trade = ai_proposal()
        with patch("news_engine.get_hard_economic_data", return_value=hard), patch("news_engine.fetch_news_bundle", return_value=feed), \
             patch("news_engine._gemini_context_analysis", return_value=(macro, [evaluated], trade, "gemini")) as analyze:
            result = news_engine.get_news_context_and_store("NVDA", "valid-key", technical=technical(), timeframe="15m", analysis_bar_at=123)
        analyze.assert_called_once()
        self.assertEqual(analyze.call_args.args[2], technical())
        self.assertEqual(analyze.call_args.args[3], [item])
        self.assertEqual(analyze.call_args.args[4]["snapshot_id"], hard["snapshot_id"])
        self.assertIn("importance_pct", analyze.call_args.args[4]["items"][0])
        self.assertEqual(sum(analyze.call_args.kwargs["policy"]["decision_weights_pct"].values()), 100)
        self.assertFalse(result["pipeline"]["ai_search_enabled"])
        self.assertEqual(result["news"][0]["analysis_method"], "gemini")
        self.assertEqual(result["ai_trade_proposal"]["signal_type"], "BUY")
        news_engine._analysis_cache.clear()
        with patch("news_engine.get_hard_economic_data", return_value=hard), patch("news_engine.fetch_news_bundle", return_value=feed):
            cached = news_engine.get_news_context_and_store("NVDA", "valid-key", technical=technical(), timeframe="15m", analysis_bar_at=123)
        analyze.assert_called_once()
        self.assertTrue(cached["pipeline"]["cache_hit"])


if __name__ == "__main__":
    unittest.main()
