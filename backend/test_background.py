"""Autonomous execution, scope isolation, persistent quotas and price retention."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import pandas as pd

import analysis_modes
from analysis_scope import background_analysis, current_scope
import backtest_archive as archive
import background_settings as config
import background_service as service
from background_prices import store_frame, merged_frame
import database
import main
import news_engine
import quantitative_analysis as quant
from signal_manager import get_signal_state, manage_signal
from test_platform import technical, ai_proposal
from decision_engine import validate_ai_trade_decision
from test_quantitative import frames

NOW = pd.Timestamp("2026-10-28T15:00:00Z").timestamp()


def fresh_frames(tf):
    target, source = frames(tf)
    seconds = 300 if tf == "5m" else 900
    shift = pd.Timestamp(NOW,unit="s",tz="UTC")-pd.Timedelta(seconds=seconds)-target.index[-1]
    for frame in (target,source):
        frame.index += shift
        frame.attrs.update(instrument_type="EQUITY",exchange_timezone="America/New_York",fetched_at=NOW)
    return target,source


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="background-tests-")
        self.environment = patch.dict(os.environ,{"TRADING_DB_PATH":str(Path(self.directory.name)/"test.db"),"GEMINI_API_KEY":"unit-test-only"})
        self.environment.start()
        database.init_db()
        self.clock = patch.object(service.time,"time",return_value=NOW)
        self.clock.start()
        self.client = Mock()
        self.outputs = []
        self.main_frames = {tf:fresh_frames(tf)[0] for tf in ("5m","15m")}
        self.minute = fresh_frames("15m")[1]
        self.worker = service.BackgroundService(self.evaluate,lambda symbol,tf:self.minute if tf=="1m" else self.main_frames[tf],asynchronous=False)

    def tearDown(self):
        self.worker.stop()
        self.clock.stop()
        self.environment.stop()
        self.directory.cleanup()

    def selected(self, **changes):
        values = copy.deepcopy(config.DEFAULTS)
        values.pop("revision")
        values.update(enabled=True,symbols=["AAPL"])
        values.update(changes)
        return config.save_settings(values)

    def evaluate(self,ticker,tf,**kwargs):
        mode = kwargs["mode"]
        self.assertEqual(analysis_modes.get_analysis_mode(),mode)
        self.assertEqual(quant.settings()["enabled"],mode=="gemini")
        if mode == "gemini":
            news_engine._generate_content(self.client,model="test",contents="fixture")
        self.outputs.append((ticker,tf,mode))
        return {"analysis_bar_at":int((NOW-(300 if tf=="5m" else 900))*1000),"ai_analysis_method":mode,
                "decision":{"signal_type":"NEUTRAL"},"lightgbm":{"engine":"lightgbm","status":"AVAILABLE"},
                "decision_policy":{"decision_weights_pct":{"quantitative":12}}}

    def tick(self, selected):
        with patch.object(service,"market_metadata",return_value={"market_open":True,"is_stale":False}), \
             patch.object(service.backtest_service,"start",return_value={"id":"offline-job"}):
            self.worker.tick(selected)

    def test_installation_defaults_off_and_starts_no_market_or_api_work(self):
        self.assertFalse(config.settings()["enabled"])
        with patch.object(self.worker,"market_frame",side_effect=AssertionError("Unrequested network")):
            self.worker.tick()
        self.assertEqual(self.outputs,[])
        self.assertEqual(config.api_usage()["day"],0)

    def test_browser_independent_tick_processes_both_profiles_once_per_closed_bar(self):
        selected = self.selected()
        self.tick(selected)
        self.assertEqual(len(self.outputs),3)
        service.time.time.return_value=NOW+76
        self.tick(selected)
        self.assertEqual(set(self.outputs),{("AAPL","5m","python"),("AAPL","5m","gemini"),
                                           ("AAPL","15m","python"),("AAPL","15m","gemini")})
        self.assertEqual(len(self.outputs),4)
        self.assertEqual(config.api_usage()["hour"],2)
        self.assertEqual(self.client.models.generate_content.call_count,2)

    def test_background_selection_does_not_rewrite_manual_mode_or_quant_toggle(self):
        analysis_modes.set_analysis_mode("python")
        selected = self.selected()
        self.tick(selected)
        self.assertEqual(analysis_modes.get_analysis_mode(),"python")
        self.assertFalse(quant.settings()["enabled"])
        self.assertIsNone(current_scope())

    def test_scope_is_thread_local_and_resets_on_exception(self):
        analysis_modes.set_analysis_mode("python")
        with self.assertRaises(ValueError):
            with background_analysis("gemini",1,lambda:None,lambda:None):
                self.assertEqual(analysis_modes.get_analysis_mode(),"gemini")
                with ThreadPoolExecutor(max_workers=1) as executor:
                    self.assertEqual(executor.submit(analysis_modes.get_analysis_mode).result(),"python")
                raise ValueError("scope exit")
        self.assertEqual(analysis_modes.get_analysis_mode(),"python")

    def test_only_one_leased_process_can_lead_and_stale_owner_cannot_continue(self):
        selected = self.selected()
        other = service.BackgroundService(Mock(),Mock())
        self.assertTrue(self.worker._acquire(now=NOW))
        self.assertFalse(other._acquire(now=NOW+1))
        self.assertTrue(other._acquire(now=NOW+service.LEASE_SECONDS+1))
        with self.assertRaises(analysis_modes.ModeChangedError):
            self.worker._guard(selected["revision"],"gemini")
        other.stop()

    def test_restart_preserves_per_bar_deduplication(self):
        selected = self.selected()
        self.tick(selected)
        self.worker._release()
        next_worker = service.BackgroundService(self.evaluate,self.worker.market_frame,asynchronous=False)
        with patch.object(service,"market_metadata",return_value={"market_open":True,"is_stale":False}), \
             patch.object(service.backtest_service,"start",return_value={"id":"job"}):
            next_worker.tick(selected)
        self.assertEqual(len(self.outputs),3)
        next_worker.stop()

    def test_gemini_is_paced_and_rotates_even_when_other_symbol_has_stronger_model(self):
        selected=self.selected(symbols=["AAPL","NVDA"],timeframes=["5m"],api_hour_limit=15)
        self.worker.gemini_candidates={('AAPL','5m'):(int(NOW*1000),.65),('NVDA','5m'):(int(NOW*1000),.9)}
        with patch.object(self.worker,"_queue") as queue:
            self.worker._dispatch_gemini(selected)
            first=queue.call_args.args
            self.assertEqual(first[2],"NVDA")
            self.worker._finish(first[0],"COMPLETED",{})
            config.reserve_api_call(selected["revision"],"NVDA","5m",now=NOW)
            service.time.time.return_value=NOW+60
            self.worker._dispatch_gemini(selected)
            self.assertEqual(queue.call_count,1)
            service.time.time.return_value=NOW+241
            self.worker._dispatch_gemini(selected)
            self.assertEqual(queue.call_count,2)
            self.assertEqual(queue.call_args.args[2],"AAPL")

    def test_slow_analysis_does_not_block_quote_collection(self):
        selected = self.selected(gemini_enabled=False,timeframes=["5m"])
        entered,release = threading.Event(),threading.Event()
        calls = []
        def slow(*args,**kwargs):
            calls.append(1); entered.set(); release.wait(2)
            return {"ai_analysis_method":"python","decision":{"signal_type":"NEUTRAL"}}
        worker = service.BackgroundService(slow,self.worker.market_frame)
        try:
            with patch.object(service,"market_metadata",return_value={"market_open":True,"is_stale":False}), \
                 patch.object(service.backtest_service,"start",return_value={"id":"job"}):
                worker.tick(selected)
                self.assertTrue(entered.wait(1))
                self.minute.loc[self.minute.index[-1],"Close"] += .001
                worker.tick(selected)
                with closing(database.get_connection()) as connection:
                    stored = connection.execute("SELECT close FROM background_price_bars WHERE ticker='AAPL' AND timeframe='1m' ORDER BY bar_at DESC LIMIT 1").fetchone()[0]
                self.assertEqual(stored,self.minute.Close.iloc[-1])
                self.assertEqual(len(calls),1)
        finally:
            release.set()
            worker.analyzer.shutdown(wait=True)
            worker.stop()

    def test_expired_queued_analysis_is_not_backfilled_with_current_information(self):
        selected = self.selected()
        self.worker._acquire()
        end = int((NOW-301)*1000)
        identity = self.worker._claim("AAPL","5m","gemini_quant",end)
        self.worker._analyze(identity,selected,"AAPL","5m","gemini_quant","gemini",end)
        self.assertEqual(self.outputs,[])
        with closing(database.get_connection()) as connection:
            self.assertEqual(connection.execute("SELECT status FROM background_runs WHERE id=?",(identity,)).fetchone()[0],"SKIPPED_TOO_LATE")

    def test_changed_actual_candle_is_rejected_before_technical_or_ai_work(self):
        analysis_modes.set_analysis_mode("python")
        with patch("main.market_frame",return_value=self.main_frames["5m"]), \
             patch("main.run_technical_analysis") as technical_work, \
             self.assertRaises(analysis_modes.AnalysisBarChangedError):
            main.evaluate("AAPL","5m",mode="python",expected_bar_end=int((NOW-300)*1000))
        technical_work.assert_not_called()

    def test_old_inflight_run_is_marked_interrupted_when_a_new_owner_takes_over(self):
        self.selected()
        self.worker._acquire()
        identity = self.worker._claim("AAPL","5m","python",123)
        self.worker._release()
        other = service.BackgroundService(Mock(),Mock())
        other._acquire()
        with closing(database.get_connection()) as connection:
            self.assertEqual(connection.execute("SELECT status FROM background_runs WHERE id=?",(identity,)).fetchone()[0],"INTERRUPTED")
        other.stop()

    def test_hour_limit_blocks_gemini_but_python_and_price_collection_continue(self):
        selected = self.selected(api_hour_limit=1)
        config.reserve_api_call(selected["revision"],"AAPL","5m")
        self.tick(selected)
        self.assertEqual([mode for _,_,mode in self.outputs],["python","python"])
        self.assertEqual(config.api_usage()["hour"],1)
        self.assertGreater(service.status()["price_bars"],0)
        self.assertEqual(service.status()["runtime"]["status"],"API_LIMIT")

    def test_daily_limit_remains_after_the_hour_rolls_over(self):
        selected = self.selected(api_hour_limit=2,api_day_limit=3)
        for _ in range(2): config.reserve_api_call(selected["revision"],"AAPL","5m",now=NOW)
        with self.assertRaises(config.BudgetExceeded): config.reserve_api_call(selected["revision"],"AAPL","5m",now=NOW+1)
        config.reserve_api_call(selected["revision"],"AAPL","5m",now=NOW+3601)
        with self.assertRaises(config.BudgetExceeded): config.reserve_api_call(selected["revision"],"AAPL","5m",now=NOW+3602)

    def test_concurrent_reservations_cannot_overrun_limit(self):
        selected = self.selected(api_hour_limit=3)
        def attempt(_):
            try:
                config.reserve_api_call(selected["revision"],"AAPL","5m")
                return True
            except config.BudgetExceeded: return False
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(sum(executor.map(attempt,range(12))),3)
        self.assertEqual(config.api_usage()["hour"],3)

    def test_each_completion_or_repair_call_is_budgeted_and_sdk_retries_are_disabled(self):
        selected = self.selected(api_hour_limit=2)
        with background_analysis("gemini",selected["revision"],lambda:None,
                                 lambda:config.reserve_api_call(selected["revision"],"AAPL","5m")):
            for _ in range(2): news_engine._generate_content(self.client,config={"http_options":{"timeout":30000}})
            with self.assertRaises(config.BudgetExceeded): news_engine._generate_content(self.client)
        self.assertEqual(self.client.models.generate_content.call_count,2)
        options = self.client.models.generate_content.call_args.kwargs["config"]["http_options"]
        self.assertEqual(options,{"timeout":30000,"retry_options":{"attempts":1}})

    def test_manual_call_is_not_silently_added_to_background_budget(self):
        self.selected()
        news_engine._generate_content(self.client,contents="manual")
        self.assertEqual(config.api_usage()["hour"],0)

    def test_disabling_while_a_response_is_inflight_prevents_followup_work(self):
        selected = self.selected()
        def disable(*args,**kwargs):
            changed = {k:v for k,v in selected.items() if k!="revision"}
            changed["enabled"] = False
            config.save_settings(changed)
            return {"ai_analysis_method":"python"}
        self.worker.evaluate = disable
        with self.assertRaises(analysis_modes.ModeChangedError): self.tick(selected)
        with closing(database.get_connection()) as connection:
            statuses = [row[0] for row in connection.execute("SELECT status FROM background_runs")]
        self.assertEqual(statuses,["INTERRUPTED"])
        self.assertEqual(config.api_usage()["hour"],0)

    def test_closed_market_never_generates_ai_analyses(self):
        selected = self.selected()
        with patch.object(service,"market_metadata",return_value={"market_open":False,"is_stale":True}), \
             patch.object(service.backtest_service,"start",return_value={"id":"job"}):
            self.worker.tick(selected)
        self.assertEqual(self.outputs,[])
        self.assertEqual(config.api_usage()["hour"],0)
        self.assertEqual(service.status()["runtime"]["status"],"MARKET_CLOSED")

    def test_stale_quotes_never_generate_ai_or_python_analyses(self):
        selected = self.selected()
        with patch.object(service,"market_metadata",return_value={"market_open":True,"is_stale":True}):
            self.worker.tick(selected)
        self.assertEqual(self.outputs,[])

    def test_quote_monitoring_uses_background_signal_tables(self):
        selected = self.selected()
        self.worker._acquire()
        decision = validate_ai_trade_decision("AAPL","5m",technical(),ai_proposal(),as_of=NOW,analysis_method="gemini")
        with background_analysis("gemini",selected["revision"],lambda:None,lambda:None):
            result = manage_signal("AAPL","5m",decision,ai_proposal(),analysis_method="gemini",context_id="separate",
                bar_at=int(NOW*1000),completed_bar_times=[],market_bars=[],now=int(NOW*1000),technical=technical())
            self.assertIsNotNone(get_signal_state("AAPL","5m"))
            monitored = service.monitor_signal_market("AAPL","5m",[{"timestamp":int(NOW*1000)+1,
                "open":100.,"high":100.,"low":100.,"close":100.}],now=int(NOW*1000)+1000,mode="gemini")
            self.assertEqual(monitored["lifecycle_status"],"AKTIV")
        self.assertEqual(result["signal_type"],"BUY")
        self.assertIsNone(get_signal_state("AAPL","5m"))

    def test_manual_and_background_activations_are_not_combined_in_one_replay(self):
        technical_data = {**technical(),"lightgbm":{"engine":"lightgbm","status":"AVAILABLE"}}
        news = {"analysis_method":"gemini","pipeline":{"context_id":"same","cache_hit":False},
                "decision_policy":{"decision_weights_pct":{"quantitative":12}}}
        decision = {"validation_status":"accepted_neutral"}
        interactive = archive.record_context("AAPL","5m",technical_data,news,decision,{"is_stale":False},None)
        with background_analysis("gemini",1,lambda:None,lambda:None):
            autonomous = archive.record_context("AAPL","5m",technical_data,news,decision,{"is_stale":False},None)
        with closing(database.get_connection()) as connection,connection:
            for origin,name in ((interactive,"manual"),(autonomous,"background")):
                archive.record_event(connection,{"ticker":"AAPL","timeframe":"5m","setup_id":name,"status":"AKTIV",
                    "plan":ai_proposal(),"direction":"BUY","backtest_origin":origin},"activated","ACTIVATED",int(NOW*1000)+1)
        self.assertEqual(archive.archive_status("AAPL","5m")["stream"],"background")
        self.assertEqual([row["setup_id"] for row in archive.activations("AAPL","5m")],["background"])
        self.assertEqual([row["setup_id"] for row in archive.activations("AAPL","5m",stream="interactive")],["manual"])

    def test_response_that_arrives_after_candle_expiry_is_not_accepted(self):
        selected = self.selected()
        self.worker._acquire()
        end = int(NOW*1000)
        identity = self.worker._claim("AAPL","5m","gemini_quant",end)
        def delayed(*args,**kwargs):
            news_engine._generate_content(self.client,contents="delayed")
            service.time.time.return_value = NOW+301
            self.worker._acquire(now=NOW+301)  # Simulate the independent heartbeat.
            return {"ai_analysis_method":"gemini","decision":{"signal_type":"BUY"}}
        self.worker.evaluate = delayed
        self.worker._analyze(identity,selected,"AAPL","5m","gemini_quant","gemini",end)
        with closing(database.get_connection()) as connection:
            self.assertEqual(connection.execute("SELECT status FROM background_runs WHERE id=?",(identity,)).fetchone()[0],"SKIPPED_BAR_CHANGED")
        self.assertEqual(self.client.models.generate_content.call_count,1)
        self.assertIsNone(get_signal_state("AAPL","5m"))

    def test_cache_keys_distinguish_background_from_manual_state_reviews(self):
        args = ("AAPL","5m",123,{}, {"snapshot_id":"same"},[],True)
        interactive = news_engine._context_id(*args)
        with background_analysis("gemini",1,lambda:None,lambda:None):
            autonomous = news_engine._context_id(*args)
        self.assertNotEqual(interactive,autonomous)

    def test_price_cache_is_incremental_and_preserves_history_across_shorter_provider_windows(self):
        first = self.minute.copy()
        inserted = store_frame("AAPL","1m",first)
        self.assertGreater(inserted,100)
        self.assertEqual(store_frame("AAPL","1m",first),0)
        short = first.iloc[-20:].copy()
        merged = merged_frame("AAPL","1m",short)
        self.assertEqual(len(merged),len(first))
        short.loc[short.index[-1],"Close"] += .001
        self.assertEqual(store_frame("AAPL","1m",short),1)

    def test_partial_future_candle_is_not_archived_as_complete(self):
        frame = self.minute.copy()
        future = frame.iloc[-1:].copy()
        future.index = pd.DatetimeIndex([pd.Timestamp(NOW,unit="s",tz="UTC")])
        combined = pd.concat([frame,future]); combined.attrs=frame.attrs.copy()
        self.assertEqual(store_frame("AAPL","1m",combined),len(frame))

    def test_invalid_settings_are_rejected_without_enabling_anything(self):
        for changes in ({"symbols":[]},{"symbols":["BTC-USD"]},{"timeframes":["1m"]},{"api_hour_limit":0},
                        {"python_enabled":False,"gemini_enabled":False}):
            with self.assertRaises(ValueError): self.selected(**changes)
            self.assertFalse(config.settings()["enabled"])

    def test_api_explicitly_controls_settings_without_modifying_manual_selection(self):
        with patch.object(main.BackgroundService,"start"),TestClient(main.app) as client:
            self.assertFalse(client.get("/api/background").json()["settings"]["enabled"])
            body = {k:v for k,v in copy.deepcopy(config.DEFAULTS).items() if k!="revision"}
            body.update(enabled=True,symbols=["AAPL"])
            self.assertEqual(client.post("/api/background/settings",json=body).status_code,200)
            status = client.get("/api/background").json()
            self.assertEqual(status["regular_analyses_per_hour"],16)
            self.assertTrue(status["settings"]["enabled"])
            self.assertEqual(analysis_modes.get_analysis_mode(),"gemini")
            self.assertFalse(quant.settings()["enabled"])
            self.assertEqual(client.post("/api/background/settings",json={**body,"symbols":[]}).status_code,422)


if __name__ == "__main__": unittest.main()
