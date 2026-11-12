"""Offline leakage, persistence, job lifecycle and signal-veto regression tests."""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from database import init_db, get_connection
import lightgbm_features as f
import lightgbm_service as ml


def candles(days=45, timeframe="5m"):
    minutes = 5 if timeframe == "5m" else 15
    rows = []
    for day in pd.bdate_range("2026-06-01", periods=days):
        rows.extend(pd.date_range(str(day.date()) + " 09:30", periods=390//minutes, freq=f"{minutes}min", tz="America/New_York"))
    index = pd.DatetimeIndex(rows)
    rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(0,.004,len(index))))
    opened = np.r_[close[0],close[:-1]]
    frame = pd.DataFrame({"Open":opened,"High":np.maximum(opened,close)*1.001,
                          "Low":np.minimum(opened,close)*.999,"Close":close,
                          "Volume":rng.integers(1000,10000,len(index))},index=index)
    frame.attrs = {"timeframe":timeframe,"currency":"USD","exchange_timezone":"America/New_York","instrument_type":"EQUITY"}
    return frame


class FeatureTests(unittest.TestCase):
    def test_features_only_use_past_data(self):
        frame = candles(4)
        full, prefix = f.features(frame), f.features(frame.iloc[:160])
        pd.testing.assert_frame_equal(full.iloc[:160],prefix)

    def test_missing_volume_is_not_a_fake_zero(self):
        frame = candles(2)
        frame["VolumeKnown"] = False
        value = f.features(frame).iloc[-1]
        self.assertTrue(np.isnan(value.volume_ratio))
        self.assertTrue(np.isnan(value.signed_volume_12))

    def test_labels_start_at_next_open_and_do_not_cross_gaps(self):
        frame = candles(3)
        x,y,ends = f.dataset(frame,"5m")
        at = x.index[0]
        i = frame.index.get_loc(at)
        change = frame.Close.iloc[i+3] / frame.Open.iloc[i+1] - 1
        self.assertEqual(y.loc[at],2 if change>f.ROUND_TRIP_COST else 0 if change<-f.ROUND_TRIP_COST else 1)
        self.assertEqual(ends.loc[at],frame.index[i+3]+pd.Timedelta(minutes=5))
        for at in x.index:
            i = frame.index.get_loc(at)
            self.assertEqual(frame.index[i+3]-at,pd.Timedelta(minutes=15))
        self.assertTrue((ends>x.index).all())

    def test_chronological_partitions_purge_future_labels(self):
        frame = candles()
        x,y,ends = f.dataset(frame,"5m")
        train,val,test = f.chronological_split(x,y,ends,"America/New_York")
        self.assertLess(ends.loc[train[0].index].max(),val[0].index.min())
        self.assertLess(ends.loc[val[0].index].max(),test[0].index.min())
        self.assertFalse(set(train[0].index)&set(test[0].index))

    def test_short_history_cannot_train(self):
        x,y,ends = f.dataset(candles(4),"5m")
        with self.assertRaisesRegex(ValueError,"zu wenig"):
            f.chronological_split(x,y,ends)

    def test_calibration_is_purged_and_final_test_is_unchanged(self):
        for timeframe in ("5m", "15m"):
            frame = candles(45, timeframe)
            x,y,ends = f.dataset(frame,timeframe)
            parts = f.calibration_split(x,y,ends,"America/New_York")
            original = f.chronological_split(x,y,ends,"America/New_York")
            pd.testing.assert_frame_equal(parts[-1][0],original[-1][0])
            for left,right in zip(parts,parts[1:]):
                self.assertLess(ends.loc[left[0].index].max(),right[0].index.min())

    def test_temperature_reduces_overconfidence_without_changing_direction(self):
        p = np.tile([.95,.025,.025],(300,1))
        labels = np.tile([0,1,2],100)
        temperature = f.fit_temperature(p,labels)
        calibrated = f.calibrate(p,temperature)
        self.assertGreater(temperature,1)
        self.assertTrue(np.allclose(calibrated.sum(axis=1),1))
        self.assertTrue((calibrated.argmax(axis=1)==p.argmax(axis=1)).all())
        prior = np.ones(3)/3
        self.assertLess(f.metrics(calibrated,labels,prior)["log_loss"],f.metrics(p,labels,prior)["log_loss"])
        for invalid in (np.array([[np.nan,.1,.9]]),np.array([[.1,.2,.2]])):
            with self.assertRaises(ValueError):
                f.calibrate(invalid)

    def test_evaluation_windows_do_not_overlap(self):
        x,y,ends = f.dataset(candles(4),"5m")
        positions = f.nonoverlapping_positions(x.index,ends)
        for left,right in zip(positions,positions[1:]):
            self.assertLessEqual(ends.iloc[left],x.index[right])

    def test_baseline_and_weak_models_fail_release_gate(self):
        labels = np.tile([0,1,2],100)
        report = f.metrics(np.ones((300,3))/3,labels,np.ones(3)/3)
        self.assertFalse(f.qualifies(report))
        self.assertEqual(report["confident_directional_samples"],0)

    def test_fingerprint_changes_with_corrected_prices(self):
        frame = candles(3)
        x,y,ends = f.dataset(frame,"5m")
        original = f.fingerprint(x,y,ends)
        x.iloc[-1,0] += .001
        self.assertNotEqual(original,f.fingerprint(x,y,ends))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="lightgbm-tests-")
        self.environment = patch.dict(os.environ,{"TRADING_DB_PATH":str(Path(self.directory.name)/"test.db")})
        self.environment.start()
        init_db()
        ml._models.clear()
        ml._prediction_cache.clear()

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def model(self,identity="fixture",active=True,frame=None):
        frame = frame if frame is not None else candles(4)
        text = "fixture-model"
        metadata = {"features":f.FEATURES,"data_through":int(time.time()*1000),
                    "validated_through":int(frame.index[-2].timestamp()*1000),
                    "model_sha256":hashlib.sha256(text.encode()).hexdigest()}
        with closing(get_connection()) as db,db:
            db.execute("INSERT INTO lightgbm_models VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (identity,"AAPL","5m",f.VERSION,int(time.time()*1000),int(active),int(active),identity,json.dumps(metadata),text))
        return metadata

    def test_schema_is_additive_and_idempotent(self):
        init_db()
        self.assertTrue(ml.status("AAPL","5m")["enabled"])
        self.assertIsNone(ml.status("AAPL","5m")["active_model"])
        ml.set_enabled(False)
        init_db()
        self.assertFalse(ml.enabled())

    def test_reads_do_not_train_or_download(self):
        with patch.object(ml,"train") as trainer,patch.object(ml,"download_history") as download:
            ml.status("AAPL","5m")
            trainer.assert_not_called()
            download.assert_not_called()

    def test_local_archive_preserves_missing_volume_and_manual_corrections(self):
        frame = candles(4)
        frame["VolumeKnown"] = False
        ml.store_frame("AAPL","5m",frame,revisit_all=True)
        with patch.object(ml,"download_history") as download:
            restored = ml.training_frame("AAPL","5m",False)
            download.assert_not_called()
        self.assertTrue(restored.Volume.isna().all())
        frame.iloc[0,frame.columns.get_loc("Close")] *= 1.0001
        ml.store_frame("AAPL","5m",frame,revisit_all=True)
        restored = ml.training_frame("AAPL","5m",False)
        self.assertEqual(restored.Close.iloc[0],frame.Close.iloc[0])

    def test_running_jobs_are_deduplicated_and_recovered(self):
        with patch.object(ml,"dependency_available",return_value=True),patch.object(ml._executor,"submit") as submit:
            first = ml.start("AAPL","5m")
            second = ml.start("AAPL","5m")
            self.assertEqual(first["id"],second["id"])
            self.assertEqual(submit.call_count,1)
            with self.assertRaises(ValueError):
                ml.start("AAPL","15m")
        ml.recover_jobs()
        self.assertEqual(ml.status("AAPL","5m")["job"]["status"],"INTERRUPTED")

    def test_failed_training_keeps_existing_model(self):
        self.model()
        with patch.object(ml,"dependency_available",return_value=True),patch.object(ml._executor,"submit"):
            job = ml.start("AAPL","5m")
        with patch.object(ml,"training_frame",side_effect=ValueError("Zu wenig Daten")):
            ml._run(job["id"],"AAPL","5m",True)
        state = ml.status("AAPL","5m")
        self.assertEqual(state["job"]["status"],"NEEDS_DATA")
        self.assertEqual(state["active_model"]["id"],"fixture")

    def test_unqualified_model_is_stored_without_replacing_good_model(self):
        self.model()
        with patch.object(ml,"dependency_available",return_value=True),patch.object(ml._executor,"submit"):
            job = ml.start("AAPL","5m")
        with patch.object(ml,"training_frame",return_value=candles(4)),patch.object(ml,"train",return_value=("new model",{},"newhash",False)):
            ml._run(job["id"],"AAPL","5m",False)
        state = ml.status("AAPL","5m")
        self.assertEqual(state["job"]["status"],"OBSERVATION")
        self.assertEqual(state["active_model"]["id"],"fixture")
        self.assertFalse(state["latest_model"]["qualified"])

    def test_native_model_is_reused_and_historical_inference_is_blocked(self):
        frame = candles(4)
        metadata = self.model(frame=frame)
        predictor = Mock()
        predictor.predict.return_value = np.array([[.1,.1,.8]])
        constructor = Mock(return_value=predictor)
        stub = types.SimpleNamespace(Booster=constructor)
        with patch.object(ml,"dependency_available",return_value=True),patch.dict("sys.modules",{"lightgbm":stub}):
            first = ml.predict("AAPL","5m",frame)
            second = ml.predict("AAPL","5m",frame)
            self.assertEqual(first["direction"],"UP")
            self.assertEqual(first,second)
            self.assertEqual(constructor.call_count,1)
            self.assertEqual(predictor.predict.call_count,1)
            metadata["validated_through"] = int((frame.index[-1]+pd.Timedelta(minutes=5)).timestamp()*1000)
            with closing(get_connection()) as db,db:
                db.execute("UPDATE lightgbm_models SET metadata_json=?",(json.dumps(metadata),))
            self.assertEqual(ml.predict("AAPL","5m",frame)["status"],"WAITING_FOR_NEW_BAR")

    def test_stale_or_disabled_models_have_no_vote(self):
        frame = candles(4)
        self.model(frame=frame)
        with patch.object(ml,"dependency_available",return_value=True):
            self.assertFalse(ml.predict("AAPL","5m",frame,stale=True)["usable"])
            ml.set_enabled(False)
            self.assertEqual(ml.predict("AAPL","5m",frame)["status"],"DISABLED")

    def test_live_prediction_applies_saved_calibration(self):
        frame = candles(4)
        metadata = self.model(frame=frame)
        metadata["calibration"] = {"temperature":4.0}
        with closing(get_connection()) as db,db:
            db.execute("UPDATE lightgbm_models SET metadata_json=?",(json.dumps(metadata),))
        predictor = Mock()
        predictor.predict.return_value = np.array([[.1,.1,.8]])
        stub = types.SimpleNamespace(Booster=Mock(return_value=predictor))
        with patch.object(ml,"dependency_available",return_value=True),patch.dict("sys.modules",{"lightgbm":stub}):
            prediction = ml.predict("AAPL","5m",frame)
        self.assertEqual(prediction["direction"],"NEUTRAL")
        self.assertLess(prediction["probabilities"]["up"],.6)

    def test_open_candle_does_not_change_live_prediction(self):
        frame = candles(4)
        self.model(frame=frame)
        predictor = Mock()
        predictor.predict.return_value = np.array([[.1,.1,.8]])
        stub = types.SimpleNamespace(Booster=Mock(return_value=predictor))
        cutoff = (frame.index[-2]+pd.Timedelta(minutes=5)).timestamp()
        frame.attrs["fetched_at"] = cutoff
        with patch.object(ml,"dependency_available",return_value=True),patch.dict("sys.modules",{"lightgbm":stub}):
            first = ml.predict("AAPL","5m",frame)
            frame.iloc[-1,frame.columns.get_loc("Close")] = 100000
            second = ml.predict("AAPL","5m",frame)
        self.assertEqual(first,second)

    def test_api_settings_and_invalid_training_request(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        self.assertEqual(client.get("/api/lightgbm?ticker=AAPL&timeframe=5m").status_code,200)
        self.assertEqual(client.post("/api/lightgbm/settings",json={"enabled":False}).json(),{"enabled":False})
        self.assertEqual(client.post("/api/lightgbm/train",json={"ticker":"AAPL","timeframe":"1h"}).status_code,422)

    def test_python_live_filter_is_applied_before_forecast_archive(self):
        from fastapi.testclient import TestClient
        from analysis_modes import set_analysis_mode
        from test_current_outlook import clear_lower_contexts
        from test_manual_modes import technical
        from test_platform import candles as live_candles
        from main import app
        set_analysis_mode("python")
        frame = live_candles()
        frame.loc[:,["Open","Close"]] = 100.
        frame.loc[:,"High"],frame.loc[:,"Low"] = 100.5,99.5
        data = {**technical(),"support":99,"local_support":99.2}
        prediction = {"usable":True,"direction":"DOWN","model_id":"fixture","status":"AVAILABLE","reason":"Fixture"}
        with patch("main.market_frame",return_value=frame),patch("main.run_technical_analysis",return_value=data), \
             patch("main.market_metadata",return_value={"is_stale":False,"market_open":True}), \
             patch("main.load_lower_timeframe_contexts",return_value=clear_lower_contexts("15m")), \
             patch.object(ml,"assess",return_value=prediction):
            result = TestClient(app).get("/api/analysis?ticker=TEST&timeframe=15m&mode=python")
        self.assertEqual(result.status_code,200,result.text)
        payload = result.json()
        self.assertEqual(payload["lightgbm"]["filter_action"],"BLOCKED")
        self.assertEqual(payload["decision"]["signal_type"],"NEUTRAL")
        self.assertFalse(payload["forecast"]["trade_eligible"])
        self.assertFalse(payload["forecast_history"][0]["forecast"]["trade_eligible"])


    def test_inventory_keeps_candle_fetch_and_training_times_separate(self):
        frame=candles(4)
        with patch("background_prices.time.time",return_value=1000):
            ml.store_frame("AAPL","5m",frame,revisit_all=True)
        with patch("background_prices.time.time",return_value=2000):
            ml.store_frame("AAPL","5m",frame,revisit_all=True)
        self.model(frame=frame)
        with patch.object(ml,"train") as trainer,patch.object(ml,"download_history") as download:
            row=ml.inventory()["rows"][0]
        self.assertEqual((row["ticker"],row["timeframe"]),("AAPL","5m"))
        self.assertEqual(row["last_updated_at"],1000000)
        self.assertEqual(row["last_checked_at"],2000000)
        self.assertNotEqual(row["trained_at"],row["last_bar_at"])
        self.assertEqual(row["candles"],len(frame))
        trainer.assert_not_called();download.assert_not_called()

    def test_legacy_archive_does_not_invent_an_update_timestamp(self):
        ml.store_frame("AAPL","5m",candles(4))
        with closing(get_connection()) as db,db:
            db.execute("DELETE FROM lightgbm_archive_updates")
        row=ml.inventory()["rows"][0]
        self.assertIsNone(row["last_updated_at"])
        self.assertIsNone(row["last_checked_at"])
        self.assertIsNotNone(row["last_bar_at"])

    def test_batch_is_persisted_once_and_runs_pairs_sequentially(self):
        with patch.object(ml,"dependency_available",return_value=True),patch.object(ml._executor,"submit") as submit:
            batch=ml.start_batch(["AAPL","NVDA"],["5m","15m"])
            repeated=ml.start_batch(["AAPL","NVDA"],["5m","15m"])
        self.assertEqual(batch["batch_id"],repeated["batch_id"])
        self.assertEqual(submit.call_count,1)
        self.assertEqual(len(ml.inventory()["queue"]),4)
        calls=[]
        def run(identity,ticker,tf,refresh):
            calls.append((ticker,tf));ml._job(identity,"NEEDS_DATA","Fixture: not enough data")
        with patch.object(ml,"_run",side_effect=run):
            ml._run_batch(batch["jobs"],True)
        self.assertEqual(calls,[("AAPL","5m"),("AAPL","15m"),("NVDA","5m"),("NVDA","15m")])
        self.assertEqual(ml.inventory()["queue"],[])
        self.assertEqual(len(ml.inventory()["rows"]),4)

    def test_invalid_batch_has_no_side_effect(self):
        for symbols,frames in (([],["5m"]),(["AAPL"],[]),(["AAPL","AAPL"],["5m"]),(["AAPL"],["1m"])):
            with self.assertRaises(ValueError):ml.start_batch(symbols,frames)
        self.assertEqual(ml.inventory()["queue"],[])

    def test_inventory_api_returns_all_pairs_without_network(self):
        from fastapi.testclient import TestClient
        from main import app
        ml.store_frame("AAPL","5m",candles(4))
        ml.store_frame("AAPL","15m",candles(4,"15m"))
        with patch.object(ml,"download_history",side_effect=AssertionError("GET fetched prices")):
            result=TestClient(app).get("/api/lightgbm/inventory")
        self.assertEqual(result.status_code,200)
        self.assertEqual({(row["ticker"],row["timeframe"]) for row in result.json()["rows"]},{("AAPL","5m"),("AAPL","15m")})

    def test_scanner_scope_and_direction_do_not_mix_cached_results(self):
        from fastapi.testclient import TestClient
        from analysis_modes import set_analysis_mode
        from main import app,_scan_cache
        set_analysis_mode("python");_scan_cache.clear()
        def evaluate(ticker,*args,**kwargs):return {"ticker":ticker,"decision":{"signal_type":"BUY" if ticker=="AAPL" else "SELL"}}
        with patch("main.evaluate",side_effect=evaluate),patch("main.rank_candidates",side_effect=lambda rows,*args:rows):
            client=TestClient(app)
            first=client.get("/api/scan?mode=python&symbols=AAPL,NVDA&direction=BUY").json()
            second=client.get("/api/scan?mode=python&symbols=AAPL,NVDA&direction=SELL").json()
        self.assertEqual(first["best"]["ticker"],"AAPL")
        self.assertEqual(second["best"]["ticker"],"NVDA")
        self.assertEqual(first["scanned"],2)
        self.assertEqual(first["universe"],["AAPL","NVDA"])

    def test_invalid_scanner_scope_never_fetches_market_data(self):
        from fastapi.testclient import TestClient
        from analysis_modes import set_analysis_mode
        from main import app
        set_analysis_mode("python")
        with patch("main.evaluate") as evaluate:
            response=TestClient(app).get("/api/scan?mode=python&symbols=INVALID")
        self.assertEqual(response.status_code,422)
        evaluate.assert_not_called()


class VetoTests(unittest.TestCase):
    def test_only_new_opposing_setups_are_blocked(self):
        original = {"signal_type":"BUY","strength":80,"reasons":["Original"],"entry_price":100}
        opposing = {"usable":True,"direction":"DOWN"}
        blocked,action = ml.filter_decision(original,opposing)
        self.assertEqual(action,"BLOCKED")
        self.assertEqual(blocked["signal_type"],"NEUTRAL")
        self.assertIsNone(blocked["entry_price"])
        self.assertEqual(original["signal_type"],"BUY")
        for previous in ({"status":"AKTIV"},{"status":"VORBEREITET"}):
            result,action = ml.filter_decision(original,opposing,previous=previous)
            self.assertIs(result,original)
            self.assertEqual(action,"EXISTING_SETUP")
        result,_ = ml.filter_decision(original,{"usable":False,"direction":"DOWN"})
        self.assertIs(result,original)

    def test_model_cannot_create_a_trade(self):
        neutral = {"signal_type":"NEUTRAL"}
        self.assertIs(ml.filter_decision(neutral,{"usable":True,"direction":"UP"})[0],neutral)


@unittest.skipUnless(importlib.util.find_spec("lightgbm"),"Native LightGBM wheel is not installed in this sandbox")
class NativeTrainingTests(unittest.TestCase):
    def test_real_cpu_training_and_text_model_round_trip(self):
        import lightgbm as lgb
        frame = candles(45,"15m")
        text,metadata,_,_ = ml.train(frame,"15m")
        loaded = lgb.Booster(model_str=text)
        probabilities = loaded.predict(f.features(frame).iloc[-1:],num_threads=1)
        self.assertEqual(probabilities.shape,(1,3))
        self.assertAlmostEqual(float(probabilities.sum()),1.,places=5)
        self.assertLess(metadata["training_through"],metadata["validation_from"])
        self.assertGreater(metadata["metrics"]["samples"],100)


if __name__ == "__main__":
    unittest.main()
