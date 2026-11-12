"""Current-price outlooks are independent from old conditional entry plans."""

from contextlib import closing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import main
from analysis_modes import set_analysis_mode
from decision_engine import validate_technical_trade_decision
from signal_manager import get_signal_state, manage_signal, signal_history
from technical_forecast import FORECAST_VERSION, build_technical_forecast, forecast_decision
from test_manual_modes import technical
from test_next_entries import fvg_context, gap
from test_platform import ai_proposal, candles
from timeframe_analysis import lower_timeframes


def clear_lower_contexts(timeframe):
    return {smaller: {"status": "AVAILABLE", "technical": {"atr": 0.01}} for smaller in lower_timeframes(timeframe)}


class CurrentOutlookTests(unittest.TestCase):
    def test_outlook_starts_at_observed_price_in_both_directions(self):
        for direction in (1,-1):
            data={**technical(direction),"current_price":100+direction*0.4}
            result=build_technical_forecast("TEST","15m",data)
            self.assertEqual(result["direction"],"UP" if direction==1 else "DOWN")
            self.assertEqual(result["reference_price"],data["current_price"])
            self.assertGreater(direction*(result["target_price"]-result["reference_price"]),0)
            self.assertNotIn("entry_price",result)
            self.assertNotIn("Rücklauf",result["summary"])

    def test_near_zone_below_minimum_is_an_obstacle_to_a_larger_target(self):
        data={**technical(),"resistance":100.3,"local_resistance":102}
        result=build_technical_forecast("TEST","15m",data,lower_contexts=clear_lower_contexts("15m"))
        self.assertEqual(result["status"],"AVAILABLE")
        self.assertEqual(result["target_price"],102)
        self.assertEqual(result["nearest_zone_price"],100.3)
        self.assertEqual(result["poi_check"]["status"],"BLOCKED")
        self.assertLess(result["risk_reward_ratio"],1)
        self.assertFalse(result["trade_eligible"])
        self.assertEqual(forecast_decision(result,data)["signal_type"],"NEUTRAL")

    def test_no_invented_target_when_no_forward_zone_exists(self):
        data={**technical(),"resistance":None,"local_resistance":None}
        result=build_technical_forecast("TEST","15m",data)
        self.assertEqual(result["status"],"NO_TARGET")
        self.assertIsNone(result["target_price"])

    def test_target_is_shown_without_inventing_a_stop(self):
        data={**technical(),"support":None,"local_support":None}
        result=build_technical_forecast("TEST","15m",data)
        self.assertEqual(result["target_price"],102)
        self.assertIsNone(result["invalidation_price"])
        self.assertFalse(result["trade_eligible"])

    def test_missing_atr_and_stale_quotes_do_not_claim_current_forecasts(self):
        missing=build_technical_forecast("TEST","15m",{**technical(),"atr":None})
        stale=build_technical_forecast("TEST","15m",technical(),market={"is_stale":True})
        self.assertEqual(missing["status"],"UNAVAILABLE")
        self.assertEqual(stale["status"],"STALE")
        self.assertIsNone(stale["target_price"])

    def test_conflicting_groups_are_not_forced_to_predict_a_direction(self):
        data=technical()
        data["indicator_groups"]["momentum_participation"].update(direction="BEARISH",score=-80)
        result=build_technical_forecast("TEST","15m",data)
        self.assertEqual(result["direction"],"UNCLEAR")
        self.assertIsNone(result["target_price"])

    def test_qualified_snapshot_uses_current_reference_only(self):
        data={**technical(),"support":99,"local_support":99.2}
        result=build_technical_forecast("TEST","15m",data,lower_contexts=clear_lower_contexts("15m"))
        self.assertTrue(result["trade_eligible"])
        decision=forecast_decision(result,data)
        self.assertEqual(decision["entry_kind"],"CURRENT")
        self.assertEqual(decision["entry_price"],100)
        self.assertEqual(decision["strategy_version"],FORECAST_VERSION)

    def test_fvg_impulse_priority_is_kept_for_invalidation(self):
        for direction in (1,-1):
            data=fvg_context(direction,[gap(1.1,direction,impulse=1),gap(1.3,direction,width=0.4,impulse=2)])
            result=build_technical_forecast("TEST","15m",data)
            self.assertAlmostEqual(result["invalidation_price"],100-direction*1.75)


class CurrentOutlookApiTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix="current-outlook-tests-")
        self.environment=patch.dict(os.environ,{"TRADING_DB_PATH":str(Path(self.directory.name)/"test.db"),"GEMINI_API_KEY":""})
        self.environment.start()
        self.client=TestClient(main.app)
        self.client.__enter__()
        set_analysis_mode("python")
        self.frame=candles()
        self.frame.loc[:,["Open","Close"]]=100.0
        self.frame.loc[:,"High"],self.frame.loc[:,"Low"]=100.5,99.5

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.environment.stop()
        self.directory.cleanup()

    def test_projection_is_returned_archived_and_deduplicated_without_entries(self):
        with patch("main.market_frame",return_value=self.frame),patch("main.run_technical_analysis",return_value=technical()), \
             patch("main.get_news_context_and_store",side_effect=AssertionError("News must stay off")), \
             patch("google.genai.Client",side_effect=AssertionError("AI must stay off")):
            first=self.client.get("/api/analysis?ticker=TEST&timeframe=15m&mode=python")
            second=self.client.get("/api/analysis?ticker=TEST&timeframe=15m&mode=python")
        self.assertEqual(first.status_code,200)
        self.assertEqual(second.json()["forecast"]["target_price"],102)
        history=self.client.get("/api/forecasts/history?ticker=TEST&timeframe=15m").json()
        self.assertEqual(len(history["forecasts"]),1)
        self.assertIsNone(get_signal_state("TEST","15m","python"))

    def test_old_waiting_entry_is_retired_before_it_can_activate(self):
        proposal=ai_proposal()
        decision=validate_technical_trade_decision("TEST","15m",technical(),proposal)
        manage_signal("TEST","15m",decision,proposal,analysis_method="python",context_id="old",bar_at=1,
                      completed_bar_times=[1],market_bars=[],now=1000,mode="python")
        with patch("main.market_frame",return_value=self.frame),patch("main.run_technical_analysis",return_value=technical()):
            result=self.client.get("/api/analysis?ticker=TEST&timeframe=15m&mode=python")
        self.assertEqual(result.status_code,200)
        self.assertEqual(get_signal_state("TEST","15m","python")["status"],"BEENDET")
        self.assertEqual(signal_history("TEST","15m",mode="python")[0]["event_type"],"RETIRED_FOR_CURRENT_PROJECTION")

    def test_websocket_sends_current_outlook_instead_of_waiting_entry(self):
        with patch("main.fetch_stock_df",return_value=self.frame),patch("main.run_technical_analysis",return_value=technical()):
            with self.client.websocket_connect("/ws/quotes/TEST?timeframe=15m&mode=python") as socket:
                result=socket.receive_json()
        self.assertEqual(result["forecast"]["reference_price"],100)
        self.assertEqual(result["forecast"]["direction"],"UP")
        self.assertNotEqual(result["decision"].get("lifecycle_status"),"VORBEREITET")


if __name__=="__main__":
    unittest.main()
