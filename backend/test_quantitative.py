"""Offline numeric, persistence, isolation, trigger and Gemini-weighting checks."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd

import database
import main
import news_engine
import quantitative_analysis as quant
from market_data import trading_sessions
from analysis_modes import ModeChangedError, set_analysis_mode
from instruments import TIMEFRAME_MAP
from signal_policy import decision_policy, quantitative_policy
from test_gemini_performance import response_sequence
from test_platform import ai_proposal, empty_news_context, technical


def frames(timeframe="15m", count=26, direction=1):
    source_tf = quant.SOURCE_TIMEFRAMES[timeframe]
    seconds = TIMEFRAME_MAP[timeframe]["seconds"]
    source_seconds = TIMEFRAME_MAP[source_tf]["seconds"]
    per_bar = seconds // source_seconds
    index = pd.date_range("2026-10-01T00:00Z", periods=count * per_bar, freq=pd.Timedelta(seconds=source_seconds))
    opening = 100 + np.arange(len(index)) * .01
    source = pd.DataFrame({"Open": opening, "High": opening + .02, "Low": opening - .02,
                           "Close": opening + .005, "Volume": 10.0}, index=index)
    if direction < 0:
        source["Open"], source["Close"] = 300 - source.Open, 300 - source.Close
        source["High"], source["Low"] = 300 - source.Low, 300 - source.High
    target = source.resample(pd.Timedelta(seconds=seconds), origin=index[0]).agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
    attributes = {"currency": "USD", "instrument_type": "CRYPTOCURRENCY", "exchange_timezone": "UTC",
                  "fetched_at": pd.Timestamp.now(tz="UTC").timestamp()}
    target.attrs = {**attributes, "timeframe": timeframe}
    source.attrs = {**attributes, "timeframe": source_tf}
    return target, source


def daily_stock_frames():
    days = pd.bdate_range("2026-09-21", periods=26, tz="America/New_York")
    rows, daily, sessions = [], [], {}
    for index, day in enumerate(days):
        start = day + pd.Timedelta(hours=9, minutes=30)
        end = day + pd.Timedelta(hours=13 if index == 20 else 16)
        timestamps = pd.date_range(start, end, freq="1h", inclusive="left")
        sessions[str(day.date())] = {"start": int(start.timestamp() * 1000), "end": int(end.timestamp() * 1000)}
        for timestamp in timestamps:
            price = 100 + len(rows) * .01
            rows.append((timestamp, price, price + .02, price - .02, price + .005, 10.0))
        daily.append((day, rows[-len(timestamps)][1] + .1, rows[-1][2], rows[-len(timestamps)][3],
                      rows[-1][4] + .01, len(timestamps) * 12.5))
    def frame(records, timeframe):
        result = pd.DataFrame(records, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"]).set_index("Timestamp")
        result.attrs = {"timeframe": timeframe, "exchange_timezone": "America/New_York",
                        "instrument_type": "EQUITY", "trading_sessions": sessions if timeframe == "1h" else {}}
        return result
    return frame(daily, "1D"), frame(rows, "1h")


class QuantitativeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="quantitative-tests-")
        self.environment = patch.dict(os.environ, {"TRADING_DB_PATH": str(Path(self.directory.name) / "test.db"),
                                                   "GEMINI_API_KEY": ""})
        self.environment.start()
        database.init_db()
        set_analysis_mode("gemini")
        quant.save_settings(True)
        self.revision = quant.settings()["revision"]
        main._scan_cache.clear()

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def calculate(self, target=None, source=None, ticker="TEST", timeframe="15m", **kwargs):
        if target is None:
            target, source = frames(timeframe)
        return quant.calculate_and_store(ticker, timeframe, target, source, revision=self.revision, **kwargs)

    def rows(self, table="quantitative_bars"):
        with closing(database.get_connection()) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM " + table)]

    def test_numeric_delta_cvd_and_zscore_for_both_directions(self):
        for direction, label in ((1, "BULLISH"), (-1, "BEARISH")):
            target, source = frames(direction=direction)
            result = self.calculate(target, source, ticker=label)
            self.assertEqual(result["status"], "AVAILABLE")
            self.assertEqual(result["direction"], label)
            self.assertEqual(result["delta"], direction * 150)
            self.assertEqual(result["cvd_change_20"], direction * 3000)
            self.assertEqual(result["cvd"], direction * 3900)
            self.assertAlmostEqual(result["efficiency_ratio_20"], 1)
            expected = (target.Close.iloc[-1] - target.Close.tail(20).mean()) / target.Close.tail(20).std(ddof=0)
            self.assertAlmostEqual(result["z_score_20"], expected, places=4)
            self.assertEqual(result["method"], "OHLCV_DIRECTION_ESTIMATE")

    def test_every_supported_timeframe_has_an_independent_saved_result(self):
        for timeframe in TIMEFRAME_MAP:
            target, source = frames(timeframe)
            result = self.calculate(target, source, timeframe=timeframe)
            self.assertEqual(result["status"], "AVAILABLE", timeframe)
            self.assertEqual(result["source_timeframe"], quant.SOURCE_TIMEFRAMES[timeframe])
            self.assertEqual(quant.saved_context("TEST", timeframe, target), result)
        self.assertEqual(len(self.rows("quantitative_cache")), 7)

    def test_identical_inputs_skip_statistical_calculation_and_all_ledger_updates(self):
        target, source = frames()
        first = self.calculate(target, source)
        with patch.object(quant, "_calculate", side_effect=AssertionError("Unchanged data recomputed")):
            second = self.calculate(target, source)
        self.assertEqual(first, second)
        self.assertEqual(len(self.rows("quantitative_snapshots")), 1)

    def test_new_bar_appends_only_new_source_rows_and_preserves_previous_cvd(self):
        self.calculate(*frames("5m", 26), timeframe="5m")
        before = self.rows()
        result = self.calculate(*frames("5m", 27), timeframe="5m")
        after = self.rows()
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(len(after) - len(before), 5)
        self.assertEqual(result["cvd"], 1350)

    def test_provider_correction_repairs_cvd_from_the_changed_bar(self):
        target, source = frames("5m")
        first = self.calculate(target, source, timeframe="5m")
        source.loc[source.index[2], "Close"] = source.Open.iloc[2] - .005
        second = self.calculate(target, source, timeframe="5m")
        self.assertEqual(second["cvd"], first["cvd"] - 20)
        self.assertNotEqual(first["input_hash"], second["input_hash"])
        self.assertEqual(len(self.rows("quantitative_snapshots")), 2)

    def test_cached_source_rows_cannot_hide_a_gap_in_the_current_provider_response(self):
        target, source = frames()
        self.calculate(target, source)
        partial = self.calculate(target, source.drop(source.index[-10]))
        self.assertEqual(partial["status"], "PARTIAL")
        self.assertIsNone(partial["direction_score"])
        self.assertEqual(quantitative_policy(decision_policy(), partial)["quantitative_policy"]["applied_weight_pct"], 0)

    def test_incomplete_or_zero_volume_never_produces_a_direction(self):
        for target, source in (frames(count=5), frames()):
            if len(target) > 5:
                target["Volume"], source["Volume"] = 0.0, 0.0
            result = self.calculate(target, source)
            self.assertEqual(result["status"], "PARTIAL")
            self.assertIsNone(result["direction_score"])

    def test_nonadditive_chart_volume_does_not_invalidate_complete_source_intervals(self):
        target, source = frames()
        expected = self.calculate(target, source)
        target["Volume"] *= 2
        target.loc[target.index[-1], "Volume"] = 0
        actual = self.calculate(target, source)
        self.assertEqual(actual["status"], "AVAILABLE")
        self.assertEqual(actual["covered_bars"], 20)
        self.assertEqual(actual["volume_mismatch_bars"], 20)
        self.assertEqual(actual["delta"], expected["delta"])
        self.assertEqual(actual["cvd_change_20"], expected["cvd_change_20"])
        self.assertTrue(any("Yahoo-Volumen" in message for message in actual["warnings"]))
        self.assertEqual(quantitative_policy(decision_policy(), actual)["quantitative_policy"]["applied_weight_pct"], 12)

    def test_missing_zero_volume_intrabar_is_still_a_gap_even_when_volumes_match(self):
        target, source = frames()
        missing = source.index[-10]
        source.loc[missing, "Volume"] = 0
        target.loc[target.index[-1], "Volume"] -= 10
        self.assertEqual(self.calculate(target, source)["status"], "AVAILABLE")
        result = self.calculate(target, source.drop(missing))
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["covered_bars"], 19)
        self.assertIsNone(result["direction_score"])

    def test_shortened_four_hour_session_requires_consistent_volume_and_prices(self):
        target, source = frames("4h")
        target.attrs["instrument_type"] = "EQUITY"
        source.attrs["instrument_type"] = "EQUITY"
        source = source.iloc[:-1].copy()
        target.loc[target.index[-1], "Close"] = source.Close.iloc[-1]
        target.loc[target.index[-1], "Volume"] -= 10
        self.assertEqual(self.calculate(target, source, timeframe="4h")["status"], "AVAILABLE")
        target.loc[target.index[-1], "Volume"] *= 2
        self.assertEqual(self.calculate(target, source, timeframe="4h")["status"], "PARTIAL")

    def test_daily_stock_uses_actual_sessions_including_half_day_and_nonadditive_volume(self):
        target, source = daily_stock_frames()
        result = self.calculate(target, source, timeframe="1D")
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["covered_bars"], 20)
        self.assertEqual(result["volume_mismatch_bars"], 20)

    def test_schedule_cannot_hide_missing_daily_source_bars(self):
        target, source = daily_stock_frames()
        for missing in (source.index[-1], source.index[-4], source.index[-7]):
            result = self.calculate(target, source.drop(missing), timeframe="1D")
            self.assertEqual(result["status"], "PARTIAL")

    def test_four_hour_cutoff_includes_the_hourly_candle_clipped_to_actual_close(self):
        _, source = daily_stock_frames()
        parts = [day.resample("4h", origin=day.index[0]).agg({
            "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
            for _, day in source.groupby(source.index.normalize())]
        target = pd.concat(parts)
        target.attrs = {**source.attrs, "timeframe": "4h"}
        result = self.calculate(target, source, timeframe="4h")
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["covered_bars"], 20)

    def test_session_parser_preserves_half_days_and_timezone_without_inventing_a_calendar(self):
        _, source = daily_stock_frames()
        periods = pd.DataFrame([{"start": pd.Timestamp(row["start"], unit="ms", tz="UTC"),
                                 "end": pd.Timestamp(row["end"], unit="ms", tz="UTC")}
                                for row in source.attrs["trading_sessions"].values()])
        actual = trading_sessions({"tradingPeriods": periods, "exchangeTimezoneName": "America/New_York"})
        self.assertEqual(actual, source.attrs["trading_sessions"])
        self.assertEqual(trading_sessions({}), {})

    def test_a_zero_volume_latest_interval_is_neutral_when_prior_source_volume_is_usable(self):
        target, source = frames()
        source.loc[source.index[-15:], "Volume"] = 0
        target.loc[target.index[-1], "Volume"] = 0
        result = self.calculate(target, source)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["delta"], 0)
        self.assertEqual(result["delta_pct"], 0)

    def test_new_snapshot_version_reuses_existing_signed_ledger(self):
        target, source = frames()
        with patch.object(quant, "QUANT_VERSION", "1.0-manual-delta"):
            self.calculate(target, source)
        original = self.rows()
        self.calculate(target, source)
        self.assertEqual(original, self.rows())
        self.assertEqual(len(self.rows("quantitative_cache")), 2)

    def test_future_source_bars_do_not_change_current_result(self):
        target, source = frames("5m")
        first = self.calculate(target, source, timeframe="5m")
        _, more_source = frames("5m", 27)
        second = self.calculate(target, more_source, timeframe="5m")
        self.assertEqual(first, second)

    def test_equal_price_doji_retains_preceding_direction_and_flat_start_is_unknown(self):
        target, source = frames("1m")
        source.loc[source.index[-1], ["Open", "Close"]] = source.Close.iloc[-2]
        target.loc[target.index[-1], ["Open", "Close"]] = source.Close.iloc[-2]
        self.assertEqual(self.calculate(target, source, timeframe="1m")["delta"], 10)
        target, source = frames("1m")
        target.loc[:, ["Open", "High", "Low", "Close"]] = 100.0
        source.loc[:, ["Open", "High", "Low", "Close"]] = 100.0
        self.assertEqual(self.calculate(target, source, timeframe="1m", ticker="FLAT")["delta"], 0)

    def test_stored_results_are_read_only_and_new_candles_make_them_stale(self):
        target, source = frames("5m")
        first = self.calculate(target, source, timeframe="5m")
        with patch.object(quant, "_calculate", side_effect=AssertionError("Read must not calculate")):
            self.assertEqual(quant.saved_context("TEST", "5m", target), first)
            changed, _ = frames("5m", 27)
            self.assertEqual(quant.saved_context("TEST", "5m", changed)["status"], "STALE")
            self.assertEqual(quant.saved_context("OTHER", "5m", target)["status"], "NOT_CALCULATED")
        self.assertEqual(len(self.rows("quantitative_snapshots")), 1)

    def test_wrong_timeframe_is_rejected_without_persisting_mislabelled_values(self):
        target, source = frames("5m")
        with self.assertRaises(ValueError):
            self.calculate(target, source, timeframe="15m")
        self.assertEqual(self.rows(), [])

    def test_failed_refresh_remains_invalid_until_a_successful_manual_calculation(self):
        target, source = frames()
        first = self.calculate(target, source)
        quant.invalidate_saved("TEST", "15m", "Source unavailable")
        self.assertEqual(quant.saved_context("TEST", "15m", target)["status"], "STALE")
        recovered = self.calculate(target, source)
        self.assertEqual(recovered["status"], "AVAILABLE")
        self.assertEqual(recovered["cvd"], first["cvd"])

    def test_live_analysis_never_applies_the_retired_hand_weighted_quant_score(self):
        target,_=frames()
        with TestClient(main.app) as client, patch("main.market_frame",return_value=target), patch("main.get_news_context_and_store",return_value=empty_news_context()):
            result=client.get("/api/analysis?ticker=TEST&mode=gemini")
        self.assertEqual(result.status_code,200,result.text)
        self.assertIsNone(result.json()["quantitative"])
        self.assertEqual(result.json()["decision_policy"]["decision_weights_pct"].get("quantitative",0),0)
        self.assertEqual(result.json()["lightgbm"]["engine"],"lightgbm")

    def test_concurrent_same_symbol_requests_reuse_one_committed_snapshot(self):
        target, source = frames()
        with patch.object(quant, "_calculate", wraps=quant._calculate) as calculate:
            with ThreadPoolExecutor(max_workers=2) as pool:
                values = list(pool.map(lambda _: self.calculate(target, source), range(2)))
        self.assertEqual(values[0], values[1])
        self.assertEqual(calculate.call_count, 1)

    def test_relative_strength_uses_only_matching_candles(self):
        target, source = frames()
        benchmark = target.copy()
        benchmark["Close"] = 100.
        result = self.calculate(target, source, benchmark=benchmark, benchmark_symbol="QQQ")
        expected = (target.Close.iloc[-1] / target.Close.iloc[-21] - 1) * 100
        self.assertAlmostEqual(result["relative_strength_20_pct"], expected, places=4)
        missing = self.calculate(target, source, benchmark=benchmark.drop(benchmark.index[-2]), benchmark_symbol="QQQ")
        self.assertIsNone(missing["relative_strength_20_pct"])

    def test_only_lightgbm_can_be_enabled_through_live_settings(self):
        with TestClient(main.app) as client, patch("main.market_frame") as market, patch.object(quant,"_calculate") as calc:
            self.assertEqual(client.post("/api/quantitative/settings",json={"enabled":True}).status_code,410)
            self.assertTrue(client.get("/api/quantitative/settings").json()["retired"])
            self.assertFalse(client.get("/api/quantitative/settings").json()["enabled"])
            for value in (False,True):
                self.assertEqual(client.post("/api/lightgbm/settings",json={"enabled":value}).json(),{"enabled":value})
            market.assert_not_called()
            calc.assert_not_called()

    def test_retired_endpoint_never_starts_a_second_quant_engine(self):
        target,_=frames()
        with TestClient(main.app) as client, patch("main.market_frame",return_value=target), patch("main.get_news_context_and_store",return_value=empty_news_context()), patch.object(quant,"calculate_and_store") as calculate:
            self.assertEqual(client.post("/api/quantitative/calculate?ticker=TEST").status_code,410)
            poll=client.get("/api/analysis?ticker=TEST&mode=gemini")
            self.assertEqual(poll.status_code,200,poll.text)
            self.assertIsNone(poll.json()["quantitative"])
            calculate.assert_not_called()

    def test_python_and_disabled_quant_cannot_run_the_manual_endpoint(self):
        with TestClient(main.app) as client, patch("main.market_frame") as market:
            quant.save_settings(False)
            self.assertEqual(client.post("/api/quantitative/calculate").status_code, 410)
            quant.save_settings(True)
            set_analysis_mode("python")
            self.assertEqual(client.post("/api/quantitative/calculate").status_code, 410)
            self.assertEqual(client.post("/api/quantitative/settings", json={"enabled": True}).status_code, 410)
            market.assert_not_called()

    def test_old_selection_revision_is_rejected_before_any_market_request(self):
        quant.save_settings(False)
        with TestClient(main.app) as client, patch("main.market_frame") as market:
            self.assertEqual(client.get("/api/analysis?mode=gemini&quant_revision=" + str(self.revision)).status_code, 410)
            self.assertEqual(client.get("/api/scan?mode=gemini&quant_revision=" + str(self.revision)).status_code, 410)
            market.assert_not_called()

    def test_scanner_never_runs_the_retired_quant_calculation(self):
        target, source = frames()
        with TestClient(main.app) as client, patch("main.DEFAULT_UNIVERSE", ["ONE", "TWO"]), \
             patch("main.market_frame", side_effect=lambda ticker, tf: target if tf == "15m" else source), \
             patch("main.get_news_context_and_store", return_value=empty_news_context()), \
             patch.object(quant, "calculate_and_store", wraps=quant.calculate_and_store) as calculate:
            result = client.get("/api/scan?mode=gemini")
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["analyzed"], 2)
            self.assertFalse(result.json()["quantitative_enabled"])
            self.assertEqual(calculate.call_count, 0)
            quant.save_settings(False)
            calculate.reset_mock()
            result = client.get("/api/scan?mode=gemini")
            self.assertEqual(result.json()["analyzed"], 2)
            calculate.assert_not_called()

    def test_fixed_quant_weight_changes_actual_gemini_breakdown(self):
        snapshot = self.calculate()
        snapshot.update(selection_revision=self.revision, direction_score=100)
        context = {**technical(), "quantitative": snapshot}
        initial = json.loads(response_sequence()[0].text)
        initial["trade"] = {**initial["trade"], **ai_proposal(), "take_profit_1": 104}
        with patch("google.genai.Client") as client:
            generate = client.return_value.__enter__.return_value.models.generate_content
            generate.return_value = SimpleNamespace(text=json.dumps(initial))
            _, _, trade, method = news_engine._gemini_context_analysis("TEST", "15m", context, [],
                {"items": [], "snapshot_id": "quant"}, "key", "quant-test")
        self.assertEqual(method, "gemini")
        breakdown = trade["decision_breakdown"]
        self.assertEqual(breakdown["weights_pct"], {"technical": 58, "quantitative": 12, "hard_data": 10, "normal_news": 20})
        # 80*.58 + 50*.10 + 50*.20 + 100*.12, independently hand-calculated.
        self.assertEqual(breakdown["weighted_direction_score"], 73.4)
        payload = json.loads(generate.call_args.kwargs["contents"].rsplit("\n", 1)[-1])
        self.assertEqual(payload["technical_analysis"]["quantitative"]["method"], "OHLCV_DIRECTION_ESTIMATE")

    def test_stale_quant_has_zero_effect_and_default_weights_are_preserved(self):
        baseline = decision_policy()
        self.assertEqual(quantitative_policy(baseline), baseline)
        snapshot = self.calculate()
        snapshot["status"] = "STALE"
        policy = quantitative_policy(baseline, snapshot)
        self.assertEqual(policy["decision_weights_pct"], baseline["decision_weights_pct"])
        self.assertEqual(policy["quantitative_policy"]["applied_weight_pct"], 0)


if __name__ == "__main__":
    unittest.main()
