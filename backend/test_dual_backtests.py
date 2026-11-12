"""Causality, real-source provenance, job isolation and two-profile API tests."""
from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import pandas as pd

import backtest_archive as archive
import backtest_service as service
import database
import main
from decision_engine import validate_ai_trade_decision
from gemini_backtest import replay
from signal_manager import manage_signal
from technical_backtest import Costs, EuroRates
from test_platform import technical, ai_proposal
from test_technical_backtest import candles


def activation(frame, *, available=None):
    return {"setup_id": "setup-a", "available_at": available if available is not None else int(frame.index[0].timestamp()*1000)+30000,
            "state": {"direction": "BUY", "plan": {"stop_loss": 98., "take_profit_1": 104.}}}


def rates():
    return EuroRates(pd.Series([1.], index=pd.DatetimeIndex(["2026-10-20T00:00Z"])))


class ArchiveExecutionTests(unittest.TestCase):
    def test_next_minute_after_observation_cannot_use_earlier_target(self):
        frame = candles()
        frame.loc[frame.index[0], "High"] = 110.
        result = replay("AAPL", "5m", frame, [activation(frame)], rates(), costs=Costs(0,0,0,0))
        self.assertEqual(result["total_trades"], 1)
        self.assertEqual(result["trades"][0]["entry_time"], int(frame.index[1].timestamp()*1000))
        self.assertEqual(result["trades"][0]["reason"], "END_OF_DATA")
        self.assertEqual(result["final_capital"], 5000.)

    def test_known_target_payout_and_full_capital(self):
        frame = candles()
        frame.loc[frame.index[2], "High"] = 104.1
        result = replay("AAPL", "5m", frame, [activation(frame)], rates(), costs=Costs(0,0,0,0))
        self.assertEqual(result["final_capital"], 5200.)
        self.assertEqual(result["trades"][0]["units"], 50.)
        self.assertEqual(result["trades"][0]["reason"], "TP1")

    def test_commission_slippage_and_spread_match_independent_cash_math(self):
        frame = candles()
        frame.loc[frame.index[2], "High"] = 104.1
        result = replay("AAPL", "5m", frame, [activation(frame)], rates())
        entry = 100 * 1.0006
        units = 5000 / (entry * 1.0005)
        self.assertAlmostEqual(result["final_capital"], units * 104 * .9995, places=5)

    def test_stop_is_first_when_both_hit(self):
        frame = candles()
        frame.loc[frame.index[1], ["Low", "High"]] = [97., 105.]
        result = replay("AAPL", "5m", frame, [activation(frame)], rates(), costs=Costs(0,0,0,0))
        self.assertEqual(result["final_capital"], 4900.)
        self.assertEqual(result["trades"][0]["reason"], "STOP_BOTH_HIT")

    def test_missing_next_minute_does_not_fill_in_later_session(self):
        frame = candles()
        observed = activation(frame)
        frame = frame.drop(frame.index[1])
        result = replay("AAPL", "5m", frame, [observed], rates())
        self.assertEqual(result["total_trades"], 0)
        self.assertEqual(result["execution_rejections"]["missing_next_minute"], 1)

    def test_gap_invalidating_risk_reward_is_rejected(self):
        frame = candles()
        frame.loc[frame.index[1], ["Open", "High", "Low", "Close"]] = [103., 103.2, 102.8, 103.]
        result = replay("AAPL", "5m", frame, [activation(frame)], rates())
        self.assertEqual(result["total_trades"], 0)
        self.assertEqual(result["execution_rejections"]["entry_gap_or_costs_invalidated_setup"], 1)

    def test_same_account_cannot_fund_overlapping_signals(self):
        frame = candles()
        one = activation(frame)
        two = {**one, "setup_id": "setup-b"}
        result = replay("AAPL", "5m", frame, [one, two], rates())
        self.assertEqual(result["total_trades"], 1)
        self.assertEqual(result["execution_rejections"]["account_occupied"], 1)


class DualBacktestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dual-backtests-")
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {"TRADING_DB_PATH": str(self.root/"test.db"), "GEMINI_API_KEY": ""})
        self.environment.start()
        self.root_patch = patch.object(service, "ROOT", self.root)
        self.root_patch.start()
        database.init_db()
        self.now = int(pd.Timestamp("2026-10-21T13:30:00Z").timestamp()*1000)
        self.technical = {**technical(), "lightgbm": {"engine":"lightgbm","status":"AVAILABLE","direction":"UP","model_id":"fixture"}}
        self.news = {"analysis_method": "gemini", "decision_policy": {"decision_weights_pct": {"quantitative": 12}},
                     "pipeline": {"context_id": "current-context", "cache_hit": False},
                     "hard_data": {"items": []}, "news": []}
        self.decision = validate_ai_trade_decision("AAPL", "5m", technical(), ai_proposal(),
                                                 as_of=self.now/1000, analysis_method="gemini")

    def tearDown(self):
        self.root_patch.stop()
        self.environment.stop()
        self.directory.cleanup()

    def record(self):
        with patch.object(archive.time, "time", return_value=self.now/1000):
            return archive.record_context("AAPL", "5m", self.technical, self.news, self.decision,
                                          {"is_stale": False}, None)

    def event(self, origin, event_type="ACTIVATED", setup="setup-a"):
        state = {"ticker": "AAPL", "timeframe": "5m", "setup_id": setup, "status": "AKTIV",
                 "backtest_origin": origin, "direction": "BUY", "plan": ai_proposal()}
        with closing(database.get_connection()) as connection, connection:
            archive.record_event(connection, state, event_type+setup, event_type, self.now+1000)

    def test_capture_includes_inputs_versions_and_earliest_availability(self):
        first = self.record()
        self.now += 10000
        second = self.record()
        self.assertEqual(first, second)
        with closing(database.get_connection()) as connection:
            rows = connection.execute("SELECT * FROM backtest_context_archive").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["available_at"], self.now-10000)
        data = json.loads(rows[0]["snapshot_json"])
        self.assertEqual(data["technical"]["lightgbm"]["direction"], "UP")
        self.assertIn("code_fingerprint", data)

    def test_old_unstamped_cache_hit_is_never_relabelled(self):
        self.news["pipeline"]["cache_hit"] = True
        self.assertIsNone(self.record())
        self.assertEqual(archive.archive_status("AAPL", "5m")["contexts"], 0)

    def test_cached_response_with_existing_provenance_keeps_its_origin(self):
        origin = self.record()
        self.news["pipeline"]["cache_hit"] = True
        self.assertEqual(origin, self.record())

    def test_optional_archive_failure_never_breaks_the_live_analysis(self):
        with patch.object(archive, "_record_context", side_effect=ValueError("serialization failed")):
            self.assertIsNone(self.record())

    def test_optional_event_archive_failure_does_not_roll_back_live_signal(self):
        origin = self.record()
        with patch.object(archive, "record_event", side_effect=ValueError("archive failure")):
            result = manage_signal("AAPL", "5m", self.decision, ai_proposal(), analysis_method="gemini",
                context_id="current-context", bar_at=self.now, completed_bar_times=[self.now],
                market_bars=[{"timestamp":self.now-60000,"open":100.,"high":100.,"low":100.,"close":100.}],
                now=self.now+1500, technical=technical(), backtest_origin=origin)
        self.assertEqual(result["lifecycle_status"], "AKTIV")
        from signal_manager import get_signal_state
        self.assertEqual(get_signal_state("AAPL", "5m")["status"], "AKTIV")

    def test_legacy_contexts_cannot_be_relabelled_as_current_lightgbm_history(self):
        self.technical["lightgbm"].pop("engine")
        self.assertIsNone(self.record())
        self.technical["lightgbm"].update(engine="lightgbm",status="NO_MODEL",model_id=None)
        self.assertIsNotNone(self.record())

    def test_changed_code_excludes_old_activations(self):
        self.event(self.record())
        self.assertEqual(len(archive.activations("AAPL", "5m")), 1)
        with patch.object(archive, "strategy_key", return_value="new-version"):
            self.assertEqual(archive.archive_status("AAPL", "5m")["activated_setups"], 0)
            self.assertEqual(archive.activations("AAPL", "5m"), [])

    def test_distinct_activation_count_ignores_followup_observations(self):
        origin = self.record()
        self.event(origin)
        self.event(origin, "OBSERVED")
        self.assertEqual(archive.archive_status("AAPL", "5m")["activated_setups"], 1)

    def test_actual_signal_manager_records_activation_at_observation_time(self):
        origin = self.record()
        decision = manage_signal("AAPL", "5m", self.decision, ai_proposal(), analysis_method="gemini",
            context_id="current-context", bar_at=self.now, completed_bar_times=[self.now],
            market_bars=[{"timestamp": self.now-60000, "open": 100., "high": 100., "low": 100., "close": 100.}],
            now=self.now+1500, technical=technical(), backtest_origin=origin)
        self.assertEqual(decision["lifecycle_status"], "AKTIV")
        actual = archive.activations("AAPL", "5m")
        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0]["available_at"], self.now+1500)

    def test_manual_run_without_archive_never_fetches_prices_or_calls_ai(self):
        with patch.object(service, "_refresh_inputs", side_effect=AssertionError("Unneeded network")):
            result = service.run("AAPL", "5m", "gemini_quant", refresh_data=True)
        self.assertEqual(result["status"], "NEEDS_ARCHIVE")
        self.assertNotIn("result", result)

    def test_both_profiles_remain_separate_and_reads_never_run(self):
        with patch.object(service, "run", side_effect=AssertionError("GET recalculated")), \
             patch.object(service, "_refresh_inputs", side_effect=AssertionError("GET fetched")):
            data = service.comparison("AAPL", "5m")
        self.assertEqual(data["python"]["status"], "NOT_RUN")
        self.assertEqual(data["gemini_quant"]["status"], "NEEDS_ARCHIVE")
        self.assertIsNone(data["gemini_quant"]["result"])

    def test_api_requires_manual_post_and_deduplicates_jobs(self):
        with TestClient(main.app) as client, patch.object(service._executor, "submit") as submit:
            response = client.get("/api/backtests?ticker=AAPL&timeframe=5m")
            self.assertEqual(response.status_code, 200)
            submit.assert_not_called()
            body = {"ticker": "AAPL", "timeframe": "5m", "profile": "python"}
            first = client.post("/api/backtests/run", json=body)
            second = client.post("/api/backtests/run", json=body)
            self.assertEqual(first.status_code, 202)
            self.assertEqual(first.json()["id"], second.json()["id"])
            self.assertEqual(submit.call_count, 1)
            third = client.post("/api/backtests/run", json={**body, "profile": "gemini_quant"})
            self.assertNotEqual(third.json()["id"], first.json()["id"])
            self.assertEqual(client.get("/api/backtests/jobs/absent").status_code, 404)
            self.assertEqual(client.post("/api/backtests/run", json={**body,"ticker":"../outside"}).status_code, 422)
            self.assertEqual(client.post("/api/backtests/run", json={**body,"timeframe":"1h"}).status_code, 422)

    def test_completed_job_keeps_job_status_distinct_from_report_status(self):
        with patch.object(service._executor, "submit"):
            identity = service.start("AAPL", "5m", "gemini_quant")["id"]
        service._work(identity, "AAPL", "5m", "gemini_quant", False)
        report = service.job(identity)
        self.assertEqual(report["status"], "COMPLETED")
        self.assertEqual(report["result"]["status"], "NEEDS_ARCHIVE")

    def test_restart_marks_unfinished_jobs_interrupted(self):
        with patch.object(service._executor, "submit"):
            identity = service.start("AAPL", "5m", "python")["id"]
        service.recover_jobs()
        self.assertEqual(service.job(identity)["status"], "INTERRUPTED")

    def test_data_acquisition_failure_retains_previous_files(self):
        directory = self.root/"backtest_data"
        directory.mkdir()
        sentinel = directory/"AAPL_1m.json"
        sentinel.write_text("old snapshot")
        with patch.object(service, "fetch_stock_df", side_effect=RuntimeError("provider down")), self.assertRaises(RuntimeError):
            service._refresh_inputs("AAPL")
        self.assertEqual(sentinel.read_text(), "old snapshot")

    def write_prices(self):
        directory = self.root/"backtest_data"
        directory.mkdir()
        for tf in ("1m", "5m", "15m"):
            frame = candles()
            frame.index = pd.date_range(frame.index[0], periods=len(frame), freq={"1m":"1min","5m":"5min","15m":"15min"}[tf])
            frame.loc[frame.index[2], "High"] = 104.1
            payload = {"ticker":"AAPL","timeframe":tf,"currency":"USD","exchange_timezone":"America/New_York",
                       "snapshot_at":"2026-10-22T00:00:00Z", "klines":[{"timestamp":int(at.timestamp()*1000),
                        **{key.lower():float(row[key]) for key in ("Open","High","Low","Close","Volume")}}
                        for at,row in frame.iterrows()]}
            (directory/f"AAPL_{tf}.json").write_text(json.dumps(payload),encoding="utf-8")
        fx = {"ticker":"EURUSD=X","timeframe":"1D","currency":"USD","exchange_timezone":"UTC",
              "snapshot_at":"2026-10-22T00:00:00Z","klines":[{"timestamp":int(pd.Timestamp("2026-10-20T00:00Z").timestamp()*1000),
                "open":1.,"high":1.,"low":1.,"close":1.,"volume":0.}]}
        (directory/"EURUSD=X_1D.json").write_text(json.dumps(fx),encoding="utf-8")

    def test_combined_manual_run_saves_real_metrics_and_new_activations_invalidate(self):
        self.write_prices()
        origin = self.record()
        self.event(origin)
        result = service.run("AAPL", "5m", "gemini_quant")
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["result"]["total_trades"], 1)
        self.assertGreater(result["result"]["final_capital"], 5000.)
        self.assertEqual(service.slot("AAPL", "5m", "gemini_quant")["status"], "READY")
        self.event(origin, setup="another-setup")
        self.assertEqual(service.slot("AAPL", "5m", "gemini_quant")["status"], "STALE")

    def test_source_data_change_invalidates_metrics_without_recalculation(self):
        self.write_prices()
        self.event(self.record())
        service.run("AAPL", "5m", "gemini_quant")
        path = self.root/"backtest_data/AAPL_1m.json"
        payload = json.loads(path.read_text())
        payload["klines"][-1]["close"] = 100.1
        path.write_text(json.dumps(payload))
        with patch.object(service, "run", side_effect=AssertionError("Read recomputed")):
            self.assertEqual(service.slot("AAPL", "5m", "gemini_quant")["status"], "STALE")

    def test_fetch_timestamp_only_does_not_invalidate_valid_metrics(self):
        self.write_prices()
        self.event(self.record())
        service.run("AAPL", "5m", "gemini_quant")
        path = self.root/"backtest_data/AAPL_1m.json"
        payload = json.loads(path.read_text())
        payload["snapshot_at"] = "2026-10-22T00:00:01Z"
        path.write_text(json.dumps(payload))
        self.assertEqual(service.slot("AAPL", "5m", "gemini_quant")["status"], "READY")

    def test_archive_after_price_history_returns_missing_data_not_zero_performance(self):
        self.write_prices()
        self.now += 86400000
        self.event(self.record())
        result = service.run("AAPL", "5m", "gemini_quant")
        self.assertEqual(result["status"], "NEEDS_EXECUTION_DATA")
        self.assertNotIn("result", result)


if __name__ == "__main__":
    unittest.main()
