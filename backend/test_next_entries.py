"""Nearest-entry, one-R, FVG quality and pending-plan regressions. No live services."""

import os
import json
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import database
import main
from decision_engine import validate_ai_trade_decision, validate_technical_trade_decision
from signal_manager import manage_signal, monitor_signal_market, signal_history
from technical_analysis import _fair_value_gaps
from technical_strategy import build_technical_plan
from test_manual_modes import technical
from test_platform import ai_proposal


def gap(distance, direction, *, width=0.2, impulse=1):
    entry = 100 - direction * distance
    return {"type": "BULLISH" if direction == 1 else "BEARISH", "lower": entry - width / 2,
            "upper": entry + width / 2, "filled": False, "impulse_body_atr": impulse, "impulse_aligned": True}


def fvg_context(direction, gaps):
    return {**technical(direction), "support": None if direction == 1 else 99,
            "local_support": None if direction == 1 else 98,
            "resistance": 101 if direction == 1 else None,
            "local_resistance": 102 if direction == 1 else None,
            "market_structure": {"fair_value_gaps": gaps}}


class NearestEntryTests(unittest.TestCase):
    def test_near_one_r_pullback_beats_far_high_reward(self):
        for direction in (1, -1):
            with self.subTest(direction=direction):
                data = {**technical(direction), "support": 98 if direction == 1 else 100.3,
                        "local_support": 99.3 if direction == 1 else 99,
                        "resistance": 99.7 if direction == 1 else 102,
                        "local_resistance": 101 if direction == 1 else 100.7}
                _, decision = build_technical_plan("TEST", "15m", data)
                self.assertAlmostEqual(decision["entry_price"], 100 - direction * 0.7)
                self.assertGreaterEqual(decision["risk_reward_ratio"], 1)
                self.assertLess(decision["risk_reward_ratio"], 1.5)

    def test_current_quote_is_selected_when_structurally_valid(self):
        for direction in (1, -1):
            with self.subTest(direction=direction):
                current = 100 + direction * 0.15
                data = {**technical(direction), "current_price": current,
                        "support": 99.5 if direction == 1 else 98,
                        "local_support": 99.2 if direction == 1 else 97,
                        "resistance": 102 if direction == 1 else 100.5,
                        "local_resistance": 103 if direction == 1 else 100.8}
                _, decision = build_technical_plan("TEST", "15m", data)
                self.assertEqual(decision["entry_kind"], "CURRENT")
                self.assertAlmostEqual(decision["entry_price"], current)
                self.assertAlmostEqual(decision["current_price"], current)
                self.assertGreater(direction * (decision["entry_price"] - decision["stop_loss"]), 0)

    def test_near_breakout_is_considered_without_forcing_a_pullback(self):
        for direction in (1, -1):
            with self.subTest(direction=direction):
                data = {**technical(direction), "support": 95 if direction == 1 else 99.7,
                        "local_support": None if direction == 1 else 99,
                        "resistance": 100.3 if direction == 1 else 105,
                        "local_resistance": 101 if direction == 1 else None,
                        "market_structure": {"swings": {"highs" if direction == 1 else "lows": [{"price": 100 + direction * 3}]}}}
                _, decision = build_technical_plan("TEST", "15m", data)
                self.assertEqual(decision["entry_kind"], "BREAKOUT")
                self.assertEqual(decision["entry_mode"], "STOP")
                self.assertAlmostEqual(decision["entry_price"], 100 + direction * 0.45)
                self.assertGreaterEqual(decision["risk_reward_ratio"], 1)

    def test_tiny_nearby_obstacle_is_not_skipped_for_a_distant_target(self):
        data = {**technical(), "current_price":100.5, "support": 99.5, "local_support": None,
                "resistance": 99.6, "local_resistance": 101}
        _, decision = build_technical_plan("TEST", "15m", data)
        self.assertEqual(decision["signal_type"], "NEUTRAL")

    def test_python_one_r_does_not_lower_gemini_minimum(self):
        proposal = {**ai_proposal(), "stop_loss": 99, "take_profit_1": 101, "take_profit_2": 102}
        self.assertEqual(validate_technical_trade_decision("TEST", "15m", technical(), proposal)["validation_status"], "accepted")
        self.assertEqual(validate_ai_trade_decision("TEST", "15m", technical(), proposal, analysis_method="gemini")["validation_status"], "rejected")

    def test_wrong_direction_groups_do_not_create_a_countertrend_trade(self):
        data = technical()
        data["indicator_groups"]["momentum_participation"].update(direction="BEARISH", score=-80)
        _, decision = build_technical_plan("TEST", "15m", data)
        self.assertEqual(decision["signal_type"], "NEUTRAL")


class FvgQualityTests(unittest.TestCase):
    def test_valid_current_entry_precedes_even_an_equally_near_fvg(self):
        for direction in (1, -1):
            data = fvg_context(direction, [gap(0, direction, impulse=1), gap(0.2, direction, impulse=10)])
            _, decision = build_technical_plan("TEST", "15m", data)
            self.assertEqual(decision["entry_kind"], "CURRENT")
            self.assertEqual(decision["entry_price"], 100)

    def test_real_middle_candle_body_and_direction_are_measured(self):
        for direction in (1, -1):
            rows = [{"Open":100, "Close":100.2, "High":100.4, "Low":99.8, "ATR":1},
                    {"Open":100.2, "Close":102, "High":102.1, "Low":100.1, "ATR":1},
                    {"Open":102, "Close":102.2, "High":102.4, "Low":101.5, "ATR":1}]
            if direction == -1:
                rows = [{"Open":200-row["Open"], "Close":200-row["Close"],
                         "High":200-row["Low"], "Low":200-row["High"], "ATR":1} for row in rows]
            frame = pd.DataFrame(rows, index=pd.date_range("2026-10-26", periods=3, freq="30min", tz="UTC"))
            value = _fair_value_gaps(frame)[0]
            self.assertAlmostEqual(value["impulse_body_atr"], 1.8)
            self.assertAlmostEqual(value["gap_size"], 1.1)
            self.assertTrue(value["impulse_aligned"])
            self.assertEqual(value["impulse_at"], int(frame.index[1].timestamp() * 1000))

    def test_stronger_middle_candle_wins_between_nearby_fvgs(self):
        for direction in (1, -1):
            data = fvg_context(direction, [gap(1.1, direction, impulse=1), gap(1.3, direction, impulse=2)])
            _, decision = build_technical_plan("TEST", "15m", data)
            self.assertAlmostEqual(decision["entry_price"], 100 - direction * 1.3)

    def test_larger_gap_wins_when_impulses_are_similar(self):
        for direction in (1, -1):
            data = fvg_context(direction, [gap(1.1, direction, width=0.1, impulse=2),
                                           gap(1.3, direction, width=0.3, impulse=1.95)])
            _, decision = build_technical_plan("TEST", "15m", data)
            self.assertAlmostEqual(decision["entry_price"], 100 - direction * 1.3)

    def test_comparable_fvgs_prefer_higher_bearish_and_lower_bullish(self):
        for direction in (1, -1):
            data = fvg_context(direction, [gap(1.3, direction, impulse=1.95), gap(1.1, direction, impulse=2)])
            _, decision = build_technical_plan("TEST", "15m", data)
            self.assertAlmostEqual(decision["entry_price"], 100 - direction * 1.3)

    def test_strong_far_fvg_does_not_displace_a_near_entry(self):
        for direction in (1, -1):
            data = fvg_context(direction, [gap(1.1, direction), gap(1.6, direction, width=0.4, impulse=10)])
            _, decision = build_technical_plan("TEST", "15m", data)
            self.assertAlmostEqual(decision["entry_price"], 100 - direction * 1.1)


class ScannerSelectionTests(unittest.TestCase):
    def item(self, ticker, entry, *, price=100, rr=1.1, potential=1):
        return {"ticker":ticker, "market":{"is_stale":False, "market_open":True},
                "decision":{"signal_type":"BUY", "setup_quality":"A", "strength":80,
                            "current_price":price, "entry_price":entry,
                            "risk_reward_ratio":rr, "potential_profit_pct":potential}}

    def test_python_scan_selects_nearest_instead_of_largest_profit(self):
        near, far = self.item("NEAR",100.2), self.item("FAR",102,rr=5,potential=20)
        self.assertEqual(main.rank_candidates([far,near],75,"python")[0]["ticker"], "NEAR")
        self.assertEqual(main.rank_candidates([near,far],75,"gemini")[0]["ticker"], "FAR")

    def test_scanner_compares_percentage_distance_across_instruments(self):
        small = self.item("SMALL",100.2)
        big = self.item("BIG",10010,price=10000)
        self.assertEqual(main.rank_candidates([small,big],75,"python")[0]["ticker"], "BIG")


class PendingPythonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="next-entry-tests-")
        self.environment = patch.dict(os.environ, {"TRADING_DB_PATH":str(Path(self.directory.name)/"test.db")})
        self.environment.start()
        database.init_db()

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def observe(self, proposal, *, context="first", technical_data=None, now=1000000, bars=None):
        data = technical_data or technical()
        decision = validate_technical_trade_decision("TEST","15m",data,proposal)
        return manage_signal("TEST","15m",decision,proposal,analysis_method="python",context_id=context,
                             bar_at=900000,completed_bar_times=[900000],market_bars=bars or [],
                             now=now,mode="python",technical=data)

    def test_pending_plan_is_replaced_on_the_same_bar_with_history_preserved(self):
        old = {**ai_proposal(),"entry_price":98,"entry_zone_min":97.9,"entry_zone_max":98.1,"stop_loss":97,
               "take_profit_1":100,"take_profit_2":102}
        first = self.observe(old)
        new = {**ai_proposal(),"entry_price":99.5,"entry_zone_min":99.4,"entry_zone_max":99.6,
               "stop_loss":99,"take_profit_1":100.5,"take_profit_2":102}
        second = self.observe(new,context="nearer",now=1001000)
        self.assertEqual(first["lifecycle_status"],"VORBEREITET")
        self.assertNotEqual(first["setup_id"],second["setup_id"])
        self.assertEqual(second["entry_price"],99.5)
        events = signal_history("TEST","15m",mode="python")
        replaced = next(row for row in events if row["event_type"]=="PENDING_PLAN_REPLACED")
        self.assertEqual(replaced["decision"]["entry_price"],98)

    def test_active_python_levels_remain_frozen(self):
        bars = [{"timestamp":900000,"open":100,"high":100,"low":100,"close":100}]
        first = self.observe(ai_proposal(),bars=bars)
        new = {**ai_proposal(),"entry_price":99.5,"entry_zone_min":99.4,"entry_zone_max":99.6,"stop_loss":99}
        second = self.observe(new,context="nearer",now=1001000,bars=bars)
        self.assertEqual(first["lifecycle_status"],"AKTIV")
        self.assertEqual(second["setup_id"],first["setup_id"])
        self.assertEqual(second["stop_loss"],98)

    def test_fresh_neutral_withdraws_a_pending_plan(self):
        self.observe(ai_proposal())
        neutral = {"signal_type":"NEUTRAL","direction_score":50,"confidence":0,"reasons":["Kein naher Plan mit 1R."]}
        result = self.observe(neutral,context="neutral",now=1001000)
        self.assertEqual(result["signal_type"],"NEUTRAL")
        self.assertTrue(any(row["event_type"]=="PENDING_PLAN_WITHDRAWN" for row in signal_history("TEST","15m",mode="python")))

    def test_single_target_is_stored_without_fake_tp2_and_deduplicated(self):
        proposal = {**ai_proposal(), "take_profit_2":None}
        decision = validate_technical_trade_decision("TEST","15m",technical(),proposal,as_of=900)
        database.save_signal(decision)
        database.save_signal(decision)
        with closing(database.get_connection()) as connection:
            rows = connection.execute("SELECT decision_json FROM technical_trade_plans").fetchall()
            self.assertEqual(len(rows),1)
            self.assertIsNone(json.loads(rows[0]["decision_json"])["take_profit_2"])

    def test_single_target_ends_plan_when_reached_and_stop_keeps_priority(self):
        for stopped in (False,True):
            proposal = {**ai_proposal(), "take_profit_2":None}
            bars = [{"timestamp":900000,"open":100,"high":100,"low":100,"close":100}]
            with closing(database.get_connection()) as connection, connection:
                connection.execute("DELETE FROM technical_signal_states WHERE ticker='TEST'")
            first = self.observe(proposal,context="target"+str(stopped),bars=bars)
            self.assertEqual(first["lifecycle_status"],"AKTIV")
            quote = {"timestamp":1200000,"open":100,"close":104,"low":97 if stopped else 100,"high":104}
            result = monitor_signal_market("TEST","15m",[quote],now=1500000,mode="python")
            self.assertEqual(result["lifecycle_status"],"BEENDET")
            self.assertEqual(result["tp1_reached"],not stopped)
            self.assertFalse(result["tp2_reached"])


if __name__ == "__main__":
    unittest.main()
