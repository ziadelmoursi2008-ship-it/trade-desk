"""Independent boundary checks for data quality, amplitude, direction and disjoint budgets."""
from contextlib import closing
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import database
import market_data
import quantitative_analysis as quant
from quantitative_metrics import normalized_move
from signal_policy import decision_policy, quantitative_policy, summarize_news
from technical_analysis import build_indicator_frame, run_technical_analysis, technical_scoring_context
from decision_engine import validate_ai_trade_decision
from test_platform import technical, ai_proposal, candles
from test_quantitative import frames


class ScoringQualityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="quality-scoring-")
        self.environment = patch.dict(os.environ, {"TRADING_DB_PATH": str(Path(self.directory.name) / "test.db"),
                                                   "GEMINI_API_KEY": ""})
        self.environment.start()
        database.init_db()
        quant.save_settings(True)
        self.revision = quant.settings()["revision"]
        self.previous_cache = dict(market_data._cache)
        market_data._cache.clear()

    def tearDown(self):
        market_data._cache.clear()
        market_data._cache.update(self.previous_cache)
        self.environment.stop()
        self.directory.cleanup()

    def calculate(self, target, source, timeframe="15m"):
        return quant.calculate_and_store("QUALITY", timeframe, target, source, revision=self.revision)

    def test_unknown_volume_is_not_an_observed_zero_or_full_quant_evidence(self):
        target, source = frames()
        source.loc[source.index[-1], "Volume"] = np.nan
        result = self.calculate(target, source)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["missing_volume_bars"], 1)
        self.assertIsNone(result["direction_score"])
        self.assertEqual(quantitative_policy(decision_policy("15m"), result)["quantitative_policy"]["applied_weight_pct"], 0)
        with closing(database.get_connection()) as connection:
            last = connection.execute("SELECT volume,volume_known FROM quantitative_bars ORDER BY bar_at DESC LIMIT 1").fetchone()
        self.assertEqual(last["volume"], 0)
        self.assertEqual(last["volume_known"], 0)

    def test_an_explicit_unknown_flag_cannot_be_hidden_by_a_numeric_volume_value(self):
        target, source = frames()
        source["VolumeKnown"] = True
        source.loc[source.index[-1], "VolumeKnown"] = False
        self.assertEqual(self.calculate(target, source)["status"], "PARTIAL")

    def test_a_genuinely_observed_zero_is_preserved_as_known(self):
        target, source = frames()
        source.loc[source.index[-15:], "Volume"] = 0.
        target.loc[target.index[-1], "Volume"] = 0.
        result = self.calculate(target, source)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["missing_volume_bars"], 0)
        self.assertEqual(result["delta"], 0)

    def test_provider_missingness_is_preserved_through_numeric_compatibility_and_resampling(self):
        source = candles(8)
        source.index = pd.date_range("2026-10-01T13:30Z", periods=8, freq="1h")
        source.loc[source.index[1], "Volume"] = np.nan
        with patch("market_data.yf.Ticker") as provider:
            provider.return_value.history.return_value = source
            provider.return_value.history_metadata = {"exchangeTimezoneName": "UTC", "instrumentType": "EQUITY"}
            result = market_data.fetch_stock_df("QUALITY", "4h")
        self.assertFalse(bool(result.VolumeKnown.iloc[0]))
        self.assertTrue(bool(result.VolumeKnown.iloc[1]))
        self.assertTrue(np.isfinite(result.Volume).all())

    def test_missing_volume_suppresses_affected_indicator_votes_instead_of_fabricating_flow(self):
        frame = candles(260)
        frame["VolumeKnown"] = True
        frame.loc[frame.index[-1], "VolumeKnown"] = False
        built = build_indicator_frame(frame)
        self.assertTrue(pd.isna(built.CMF20.iloc[-1]))
        self.assertTrue(pd.isna(built.MFI14.iloc[-1]))
        self.assertTrue(pd.isna(built.VWAP.iloc[-1]))
        result = run_technical_analysis(frame)
        self.assertTrue(any("Volumenmessungen fehlen" in warning for warning in result["warnings"]))
        self.assertIsNone(result["volume_profile"]["poc"])

    def test_both_trade_directions_are_rejected_inside_the_neutral_band(self):
        for direction in (1, -1):
            for score in (45.01, 50., 54.99):
                proposal = {**ai_proposal(direction), "direction_score": score}
                result = validate_ai_trade_decision("QUALITY", "15m", technical(direction), proposal, analysis_method="gemini")
                self.assertEqual(result["signal_type"], "NEUTRAL")
                self.assertEqual(result["validation_status"], "rejected")
        self.assertEqual(validate_ai_trade_decision("QUALITY", "15m", technical(),
            {**ai_proposal(), "direction_score":55}, analysis_method="gemini")["signal_type"], "BUY")
        self.assertEqual(validate_ai_trade_decision("QUALITY", "15m", technical(-1),
            {**ai_proposal(-1), "direction_score":45}, analysis_method="gemini")["signal_type"], "SELL")

    def test_weak_single_news_stays_neutral_while_strong_evidence_can_move_the_score(self):
        base = {"analysis_method":"gemini", "event_key":"one", "sentiment":1.}
        weak = summarize_news([{**base, "importance_score":1., "relevance":.35, "novelty":.01}])
        strong = summarize_news([{**base, "importance_score":100., "relevance":1., "novelty":1.}])
        self.assertEqual(weak["weighted_direction_score"], 50.)
        self.assertLess(weak["evidence_strength_pct"], .01)
        self.assertEqual(strong["weighted_direction_score"], 100.)
        self.assertEqual(strong["evidence_strength_pct"], 100.)

    def test_duplicate_articles_and_many_weak_events_do_not_create_artificial_strength(self):
        weak = {"analysis_method":"gemini", "importance_score":10., "relevance":1., "novelty":1., "sentiment":1.}
        one = summarize_news([{**weak, "event_key":"one"}])
        many = summarize_news([{**weak, "event_key":str(index)} for index in range(30)])
        duplicate = summarize_news([{**weak, "event_key":"one"} for _ in range(30)])
        self.assertEqual(one["weighted_direction_score"], 55.)
        self.assertEqual(many["weighted_direction_score"], 55.)
        self.assertEqual(duplicate["distinct_events"], 1)
        negative = summarize_news([{**weak, "event_key":"one", "sentiment":-1.}])
        self.assertEqual(negative["weighted_direction_score"], 45.)

    def test_invalid_or_missing_news_numbers_cannot_turn_into_strong_evidence(self):
        article = {"analysis_method":"gemini", "importance_score":100., "relevance":1.,
                   "novelty":1., "sentiment":1., "event_key":"bad"}
        for field, value in [("importance_score",float("nan")),("relevance",None),
                             ("novelty",None),("sentiment",float("inf"))]:
            result = summarize_news([{**article, field:value}])
            self.assertEqual(result["weighted_direction_score"], 50.)
            self.assertEqual(result["distinct_events"], 0)

    def test_legacy_ledger_migration_retains_rows_and_marks_unverified_volume_unknown(self):
        path = str(Path(self.directory.name) / "legacy.db")
        import sqlite3
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("""CREATE TABLE quantitative_bars (
                ticker TEXT,timeframe TEXT,version TEXT,bar_at INTEGER,bar_end INTEGER,
                input_hash TEXT,open REAL,close REAL,volume REAL,delta_sign INTEGER,delta REAL,cvd REAL,
                PRIMARY KEY(ticker,timeframe,version,bar_at))""")
            connection.execute("INSERT INTO quantitative_bars VALUES('LEGACY','1m','1.0-manual-delta',0,60000,'old',100,101,10,1,10,123)")
        with patch.dict(os.environ, {"TRADING_DB_PATH":path}):
            database.init_db()
            with closing(database.get_connection()) as connection:
                row = connection.execute("SELECT * FROM quantitative_bars").fetchone()
                self.assertEqual(row["cvd"], 123)
                self.assertEqual(row["volume_known"], 0)

    def test_recent_and_background_flow_are_disjoint_and_hand_calculable(self):
        target, source = frames("1m")
        source.loc[source.index[-3:], "Close"] = source.Open.iloc[-3:] - .005
        target.loc[target.index[-3:], "Close"] = source.Close.iloc[-3:]
        result = self.calculate(target, source, "1m")
        values = {component["key"]:component for component in result["components"]}
        self.assertEqual(set(values), {"recent_flow","background_flow"})
        self.assertEqual(values["recent_flow"]["score"], 0.)
        self.assertEqual(values["background_flow"]["score"], 100.)
        self.assertEqual(result["direction_score"], 75.)

    def test_tiny_volume_last_bar_cannot_flip_the_quant_score(self):
        target, source = frames("1m")
        target.loc[target.index[-1], "Volume"] = .00001
        source.loc[source.index[-1], "Volume"] = .00001
        bullish = self.calculate(target, source, "1m")
        source.loc[source.index[-1], "Close"] = source.Open.iloc[-1] - .005
        target.loc[target.index[-1], "Close"] = source.Close.iloc[-1]
        bearish = self.calculate(target, source, "1m")
        self.assertLess(abs(bullish["direction_score"] - bearish["direction_score"]), .01)

    def test_price_statistics_do_not_cast_a_second_quant_vote(self):
        target, source = frames()
        first = self.calculate(target, source)
        reversed_target, _ = frames(direction=-1)
        second = self.calculate(reversed_target, source)
        self.assertEqual(first["direction_score"], second["direction_score"])
        self.assertNotEqual(first["statistical_momentum_context_score"], second["statistical_momentum_context_score"])
        self.assertFalse(second["price_context_scored_in_quant"])

    def test_relative_normalization_is_invariant_to_return_scale(self):
        returns = np.array([.001, -.0004, .0007, -.0002])
        self.assertAlmostEqual(normalized_move(returns), normalized_move(returns * 20))
        self.assertEqual(normalized_move(np.zeros(20)), 0.)
        self.assertTrue(np.isfinite(normalized_move(np.ones(20) * .000001)))

    def test_resolution_reduces_only_the_quant_budget_and_all_weights_still_sum_to_100(self):
        for timeframe, expected in [("1m",2.4), ("5m",12.), ("15m",12.), ("4h",9.6)]:
            target, source = frames(timeframe)
            result = self.calculate(target, source, timeframe)
            policy = quantitative_policy(decision_policy(timeframe), result)
            self.assertEqual(policy["quantitative_policy"]["configured_weight_pct"], 12.)
            self.assertAlmostEqual(policy["quantitative_policy"]["applied_weight_pct"], expected)
            self.assertAlmostEqual(sum(policy["decision_weights_pct"].values()), 100.)
        self.assertGreater(decision_policy("1m")["decision_weights_pct"]["technical"],
                           decision_policy("1D")["decision_weights_pct"]["technical"])

    def test_explicit_user_weight_override_is_retained_for_every_horizon(self):
        override = {"technical":60., "hard_data":30., "normal_news":10.}
        with patch.dict(os.environ, {"DECISION_WEIGHTS_JSON":json.dumps(override)}):
            for timeframe in ("1m","1D"):
                self.assertEqual(decision_policy(timeframe)["decision_weights_pct"], override)

    def test_volume_direction_is_removed_from_the_ai_price_groups_only_when_quant_is_active(self):
        data = run_technical_analysis(candles())
        untouched = copy.deepcopy(data)
        active = technical_scoring_context(data, {"decision_weights_pct":{"quantitative":12}}, for_ai=True)
        names = {component["name"] for component in active["indicator_groups"]["momentum_participation"]["components"]}
        self.assertNotIn("VWAP", names)
        self.assertNotIn("OBV/CMF/MFI", names)
        self.assertNotIn("volume", active)
        self.assertNotIn("vwap", active)
        self.assertEqual(data, untouched)
        self.assertEqual(technical_scoring_context(data, {"decision_weights_pct":{}}), untouched)


if __name__ == "__main__":
    unittest.main()
