"""Distance boundaries, opposing POIs and independent lower-timeframe data."""

import copy
import unittest
from unittest.mock import patch

import pandas as pd

import main
from technical_forecast import build_technical_forecast, forecast_decision
from timeframe_analysis import MIN_TARGET_DISTANCE_PCT, check_poi_path, lower_timeframes
from test_manual_modes import technical
from test_platform import candles


def clear_contexts(timeframe):
    return {smaller: {"status": "AVAILABLE", "technical": {"atr": 0.01}, "analysis_bar_at": 1}
            for smaller in lower_timeframes(timeframe)}


def primary(direction=1):
    data = technical(direction)
    data.update(atr=2, support=90 if direction == -1 else 99, local_support=None,
                resistance=110 if direction == 1 else 101, local_resistance=None)
    return data


def opposite_gap(direction, lower, upper):
    return {"type": "BEARISH" if direction == 1 else "BULLISH",
            "lower": lower, "upper": upper, "filled": False}


class TimeframeForecastTests(unittest.TestCase):
    def test_exact_requested_ladder_and_all_smaller_timeframes(self):
        self.assertEqual(MIN_TARGET_DISTANCE_PCT, {
            "1m": 0.15, "5m": 0.3, "15m": 0.6, "30m": 1.2,
            "1h": 2.4, "4h": 4.8, "1D": 9.6,
        })
        self.assertEqual(lower_timeframes("1m"), [])
        self.assertEqual(lower_timeframes("4h"), ["1h", "30m", "15m", "5m", "1m"])
        self.assertEqual(lower_timeframes("1D"), ["4h", "1h", "30m", "15m", "5m", "1m"])

    def test_exact_boundary_is_accepted_in_both_directions_for_every_timeframe(self):
        for timeframe, minimum in MIN_TARGET_DISTANCE_PCT.items():
            for direction in (1, -1):
                with self.subTest(timeframe=timeframe, direction=direction):
                    data = primary(direction)
                    data.update(atr=0.1, support=None, resistance=None)
                    data["support" if direction == 1 else "resistance"] = 100 - direction * 0.05
                    data["resistance" if direction == 1 else "support"] = 100 + direction * minimum
                    result = build_technical_forecast("TEST", timeframe, data, lower_contexts=clear_contexts(timeframe))
                    self.assertEqual(result["status"], "AVAILABLE")
                    self.assertAlmostEqual(result["distance_pct"], minimum)
                    self.assertTrue(result["trade_eligible"], result["trade_reason"])
                    self.assertEqual(forecast_decision(result, data)["signal_type"], "BUY" if direction == 1 else "SELL")

    def test_subminimum_zones_do_not_create_an_invented_target(self):
        for timeframe, minimum in MIN_TARGET_DISTANCE_PCT.items():
            for direction in (1, -1):
                data = primary(direction)
                data.update(support=None, resistance=None)
                data["resistance" if direction == 1 else "support"] = 100 + direction * minimum * 0.99
                result = build_technical_forecast("TEST", timeframe, data, lower_contexts=clear_contexts(timeframe))
                self.assertEqual(result["status"], "NO_TARGET")
                self.assertIsNone(result["target_price"])
                self.assertFalse(result["trade_eligible"])
                self.assertIsNotNone(result["nearest_zone_price"])

    def test_near_primary_obstacle_is_not_silently_skipped_to_satisfy_minimum(self):
        data = {**primary(), "local_resistance": 101}
        result = build_technical_forecast("TEST", "4h", data, lower_contexts=clear_contexts("4h"))
        self.assertEqual(result["target_price"], 110)
        self.assertFalse(result["trade_eligible"])
        self.assertEqual(result["poi_check"]["status"], "BLOCKED")
        self.assertEqual(result["poi_check"]["obstacles"][0]["timeframe"], "4h")

    def test_material_lower_gap_blocks_both_directions_without_changing_primary_levels(self):
        for direction in (1, -1):
            contexts = clear_contexts("4h")
            clear = build_technical_forecast("TEST", "4h", primary(direction), lower_contexts=contexts)
            bounds = (102, 103) if direction == 1 else (97, 98)
            contexts["5m"]["technical"]["market_structure"] = {"fair_value_gaps": [opposite_gap(direction, *bounds)]}
            blocked = build_technical_forecast("TEST", "4h", primary(direction), lower_contexts=contexts)
            self.assertEqual(blocked["target_price"], clear["target_price"])
            self.assertEqual(blocked["invalidation_price"], clear["invalidation_price"])
            self.assertTrue(clear["trade_eligible"])
            self.assertFalse(blocked["trade_eligible"])
            self.assertEqual(blocked["poi_check"]["status"], "BLOCKED")
            self.assertIn("5m", blocked["trade_reason"])

    def test_small_isolated_poi_is_a_warning_and_two_timeframe_confluence_blocks(self):
        contexts = clear_contexts("4h")
        contexts["1m"]["technical"]["market_structure"] = {"fair_value_gaps": [opposite_gap(1, 102, 102.02)]}
        result = build_technical_forecast("TEST", "4h", primary(), lower_contexts=contexts)
        self.assertTrue(result["trade_eligible"])
        self.assertEqual(len(result["poi_check"]["obstacles"]), 1)
        self.assertFalse(result["poi_check"]["obstacles"][0]["blocking"])
        contexts["5m"] = copy.deepcopy(contexts["1m"])
        result = build_technical_forecast("TEST", "4h", primary(), lower_contexts=contexts)
        self.assertFalse(result["trade_eligible"])
        self.assertTrue(all(item["blocking"] for item in result["poi_check"]["obstacles"]))

    def test_primary_target_uses_the_near_edge_of_a_band_for_the_minimum(self):
        for direction in (1, -1):
            data = primary(direction)
            data.update(support=None, resistance=None)
            bounds = (104.7, 106) if direction == 1 else (94, 95.3)
            data["market_structure"] = {"fair_value_gaps": [opposite_gap(direction, *bounds)]}
            result = build_technical_forecast("TEST", "4h", data)
            self.assertEqual(result["status"], "NO_TARGET")
            self.assertAlmostEqual(result["nearest_zone_price"], 100 + direction * 4.7)

    def test_missing_or_stale_lower_data_cannot_approve_a_trade(self):
        for missing in lower_timeframes("4h"):
            contexts = clear_contexts("4h")
            contexts.pop(missing)
            result = build_technical_forecast("TEST", "4h", primary(), lower_contexts=contexts)
            self.assertFalse(result["trade_eligible"])
            self.assertEqual(result["poi_check"]["unavailable_timeframes"], [missing])
        contexts = clear_contexts("4h")
        contexts["1m"] = {"status": "STALE", "reason": "Veraltete Daten"}
        result = build_technical_forecast("TEST", "4h", primary(), lower_contexts=contexts)
        self.assertEqual(result["poi_check"]["status"], "INCOMPLETE")
        self.assertFalse(result["trade_eligible"])

    def test_only_opposing_active_zones_on_the_path_are_considered(self):
        contexts = clear_contexts("4h")
        contexts["1m"]["technical"]["market_structure"] = {
            "fair_value_gaps": [
                {"type": "BULLISH", "lower": 102, "upper": 104},
                {**opposite_gap(1, 102, 104), "filled": True},
                opposite_gap(1, 111, 113), opposite_gap(1, 95, 97)],
            "order_blocks": [{"type": "BEARISH", "lower": 101, "upper": 104, "invalidated": True}],
        }
        result = build_technical_forecast("TEST", "4h", primary(), lower_contexts=contexts)
        self.assertTrue(result["trade_eligible"])
        self.assertEqual(result["poi_check"]["obstacles"], [])

    def test_quote_inside_opposing_band_is_blocked_in_both_directions(self):
        for direction in (1, -1):
            contexts = clear_contexts("4h")
            contexts["1m"]["technical"]["market_structure"] = {
                "fair_value_gaps": [opposite_gap(direction, 99, 101)]}
            result = build_technical_forecast("TEST", "4h", primary(direction), lower_contexts=contexts)
            self.assertFalse(result["trade_eligible"])
            self.assertEqual(result["poi_check"]["obstacles"][0]["distance_pct"], 0)

    def test_no_lower_data_is_required_for_one_minute(self):
        data = primary()
        data.update(atr=0.1, support=99.9, resistance=100.3)
        result = build_technical_forecast("TEST", "1m", data)
        self.assertTrue(result["trade_eligible"])
        self.assertEqual(result["poi_check"]["status"], "CLEAR")
        self.assertEqual(result["poi_check"]["required_timeframes"], [])


class LowerTimeframeDataTests(unittest.TestCase):
    def frames(self):
        frames = {}
        for timeframe in lower_timeframes("4h"):
            frame = candles(60)
            frame.attrs["timeframe"] = timeframe
            frames[timeframe] = frame
        return frames

    def test_each_lower_timeframe_has_its_own_data_and_only_closed_candles(self):
        frames = self.frames()
        cutoff = int(pd.Timestamp("2026-10-27 12:02Z").timestamp() * 1000)
        for timeframe, frame in frames.items():
            frame.index = pd.date_range(end="2026-10-27 12:02Z", periods=60,
                                        freq=pd.Timedelta(seconds=main.TIMEFRAME_MAP[timeframe]["seconds"]))
        seen = {}
        def analyze(frame):
            seen[frame.attrs["timeframe"]] = frame
            return {"atr": 0.01}
        with patch("main.market_frame", side_effect=lambda ticker, tf: frames[tf]), \
             patch("main.run_technical_analysis", side_effect=analyze), \
             patch("main.market_metadata", return_value={"is_stale": False}):
            result = main.load_lower_timeframe_contexts("TEST", "4h", observed_at=cutoff)
        self.assertEqual(list(result), lower_timeframes("4h"))
        self.assertTrue(all(context["status"] == "AVAILABLE" for context in result.values()))
        for timeframe, frame in seen.items():
            for timestamp in frame.index:
                self.assertLessEqual(main.candle_end(timestamp, timeframe, frame.attrs).timestamp() * 1000, cutoff)
        self.assertEqual(result["1m"]["status"], "AVAILABLE")

    def test_mismatched_stale_and_failed_feeds_remain_unavailable(self):
        frames = self.frames()
        frames["1m"].attrs["timeframe"] = "5m"
        def fetch(ticker, timeframe):
            if timeframe == "15m":
                raise main.HTTPException(503, "Feed offline")
            return frames[timeframe]
        with patch("main.market_frame", side_effect=fetch), \
             patch("main.run_technical_analysis", return_value={"atr": 0.01}), \
             patch("main.market_metadata", side_effect=lambda df, ticker, tf: {"is_stale": tf == "5m"}):
            result = main.load_lower_timeframe_contexts("TEST", "4h", observed_at=int(pd.Timestamp.now(tz="UTC").timestamp() * 1000))
        self.assertEqual(result["1m"]["status"], "UNAVAILABLE")
        self.assertEqual(result["5m"]["status"], "STALE")
        self.assertEqual(result["15m"]["status"], "UNAVAILABLE")


class TimeframePipelineTests(unittest.TestCase):
    def test_primary_levels_stay_independent_and_lower_only_changes_invalidate_the_context(self):
        frames, data = {}, {}
        for timeframe in ["4h", *lower_timeframes("4h")]:
            frame = candles(70)
            duration = pd.Timedelta(seconds=main.TIMEFRAME_MAP[timeframe]["seconds"])
            frame.index = pd.date_range(end=pd.Timestamp.now(tz="UTC").floor(duration) - duration,
                                        periods=70, freq=duration)
            frame.loc[:, ["Open", "Close"]] = 100.0
            frame.loc[:, "High"], frame.loc[:, "Low"] = 100.5, 99.5
            frame.attrs["timeframe"] = timeframe
            frames[timeframe] = frame
            data[timeframe] = primary() if timeframe == "4h" else {"atr": 0.01}
        with patch("main.market_frame", side_effect=lambda ticker, tf: frames[tf]) as fetched, \
             patch("main.run_technical_analysis", side_effect=lambda frame: copy.deepcopy(data[frame.attrs["timeframe"]])), \
             patch("main.ensure_analysis_mode"), patch("main.retire_pending_python_plan"), \
             patch("main.monitor_signal_market"), patch("main.previous_signal_context", return_value=None), \
             patch("main.signal_history", return_value=[]), patch("main.forecast_history", return_value=[]), \
             patch("main.save_forecast") as saved:
            clear = main._evaluate("TEST-USD", "4h", mode="python", persist=False, with_backtest=False)
            data["5m"]["market_structure"] = {"fair_value_gaps": [opposite_gap(1, 102, 103)]}
            blocked = main._evaluate("TEST-USD", "4h", mode="python", persist=False, with_backtest=False)
            self.assertNotEqual(saved.call_args_list[0].args[0], saved.call_args_list[1].args[0])
        self.assertTrue(clear["forecast"]["trade_eligible"])
        self.assertEqual(clear["decision"]["signal_type"], "BUY")
        self.assertFalse(blocked["forecast"]["trade_eligible"])
        self.assertEqual(blocked["decision"]["signal_type"], "NEUTRAL")
        self.assertEqual(clear["forecast"]["target_price"], blocked["forecast"]["target_price"])
        self.assertEqual(clear["forecast"]["invalidation_price"], blocked["forecast"]["invalidation_price"])
        self.assertEqual(clear["forecast"]["poi_check"]["checked_timeframes"], lower_timeframes("4h"))
        self.assertEqual({call.args[1] for call in fetched.call_args_list}, {"4h", *lower_timeframes("4h")})

    def test_no_qualifying_primary_target_does_not_fetch_lower_feeds_or_promote_another_trade(self):
        frame = candles()
        frame.attrs["timeframe"] = "4h"
        frame.loc[:, ["Open", "Close"]] = 100.0
        with patch("main.market_frame", return_value=frame), \
             patch("main.run_technical_analysis", return_value={**primary(), "resistance": 101}), \
             patch("main.ensure_analysis_mode"), patch("main.retire_pending_python_plan"), \
             patch("main.monitor_signal_market"), patch("main.previous_signal_context", return_value=None), \
             patch("main.signal_history", return_value=[]), patch("main.forecast_history", return_value=[]), \
             patch("main.save_forecast"), patch("main.load_lower_timeframe_contexts") as smaller:
            result = main._evaluate("TEST-USD", "4h", mode="python", persist=False, with_backtest=False)
        smaller.assert_not_called()
        self.assertEqual(result["forecast"]["status"], "NO_TARGET")
        self.assertEqual(result["decision"]["signal_type"], "NEUTRAL")


if __name__ == "__main__":
    unittest.main()
