"""Persistent local LightGBM models, manual background training, cheap inference.

No LLM calls, pickle, automatic retraining or synthetic production data.
The target is cost-adjusted THREE-BAR DIRECTION, not TP-before-SL success.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import threading
import time
import uuid

import numpy as np
import pandas as pd

from database import get_connection
from instruments import normalize_ticker
from market_data import completed_bars
from background_prices import store_frame, merged_frame, observed_closed
import lightgbm_features as f

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lightgbm-training")
_lock = threading.RLock()
_models = OrderedDict()
_prediction_cache = OrderedDict()
MAX_AGE_DAYS = 14


def init_storage():
    with closing(get_connection()) as db, db:
        db.execute("INSERT OR IGNORE INTO app_settings VALUES('lightgbm_enabled','true')")
        db.execute("""CREATE TABLE IF NOT EXISTS lightgbm_models (
            id TEXT PRIMARY KEY, ticker TEXT NOT NULL, timeframe TEXT NOT NULL,
            version TEXT NOT NULL, created_at INTEGER NOT NULL, active INTEGER NOT NULL,
            qualified INTEGER NOT NULL, data_hash TEXT NOT NULL,
            metadata_json TEXT NOT NULL, model_text TEXT NOT NULL)""")
        db.execute("CREATE INDEX IF NOT EXISTS ml_model_lookup ON lightgbm_models(ticker,timeframe,created_at)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ml_one_active ON lightgbm_models(ticker,timeframe) WHERE active=1")
        db.execute("""CREATE TABLE IF NOT EXISTS lightgbm_jobs (
            id TEXT PRIMARY KEY, ticker TEXT NOT NULL, timeframe TEXT NOT NULL,
            status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            message TEXT NOT NULL, model_id TEXT)""")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(lightgbm_jobs)")}
        if "batch_id" not in columns:
            db.execute("ALTER TABLE lightgbm_jobs ADD COLUMN batch_id TEXT")
            db.execute("ALTER TABLE lightgbm_jobs ADD COLUMN queue_order INTEGER NOT NULL DEFAULT 0")
        db.execute("""CREATE TABLE IF NOT EXISTS lightgbm_archive_updates (
            ticker TEXT NOT NULL, timeframe TEXT NOT NULL, checked_at INTEGER NOT NULL,
            changed_at INTEGER, PRIMARY KEY(ticker,timeframe))""")


def recover_jobs():
    with closing(get_connection()) as db, db:
        db.execute("UPDATE lightgbm_jobs SET status='INTERRUPTED',message='Training durch Backend-Neustart unterbrochen. Erneut starten.' WHERE status IN ('QUEUED','RUNNING')")


def dependency_available():
    return importlib.util.find_spec("lightgbm") is not None


def enabled():
    with closing(get_connection()) as db:
        row = db.execute("SELECT value FROM app_settings WHERE key='lightgbm_enabled'").fetchone()
    return row is not None and row[0] == "true"


def cache_revision():
    with closing(get_connection()) as db:
        newest = db.execute("SELECT MAX(created_at) FROM lightgbm_models WHERE active=1").fetchone()[0]
    return enabled(), newest


def set_enabled(value):
    with closing(get_connection()) as db, db:
        db.execute("INSERT OR REPLACE INTO app_settings VALUES('lightgbm_enabled',?)", ("true" if value else "false",))
    return {"enabled": enabled()}


def _check(ticker, timeframe):
    ticker = normalize_ticker(ticker)
    if timeframe not in ("5m", "15m"):
        raise ValueError("LightGBM unterstützt 5m und 15m.")
    return ticker


def _public_model(row):
    if row is None:
        return None
    return {"id": row["id"], "created_at": row["created_at"], "active": bool(row["active"]),
            "qualified": bool(row["qualified"]), **json.loads(row["metadata_json"])}


def status(ticker, timeframe):
    ticker = normalize_ticker(ticker)
    with closing(get_connection()) as db:
        fields = "id,created_at,active,qualified,metadata_json"
        active = db.execute(f"SELECT {fields} FROM lightgbm_models WHERE ticker=? AND timeframe=? AND active=1", (ticker,timeframe)).fetchone()
        latest = db.execute(f"SELECT {fields} FROM lightgbm_models WHERE ticker=? AND timeframe=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (ticker,timeframe)).fetchone()
        job = db.execute("SELECT * FROM lightgbm_jobs WHERE ticker=? AND timeframe=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (ticker,timeframe)).fetchone()
        archive = db.execute("SELECT COUNT(*),MIN(bar_at),MAX(bar_end) FROM background_price_bars WHERE ticker=? AND timeframe=?", (ticker,timeframe)).fetchone()
    return {"ticker": ticker, "timeframe": timeframe, "supported": timeframe in ("5m","15m"),
            "installed": dependency_available(), "enabled": enabled(), "active_model": _public_model(active),
            "latest_model": _public_model(latest), "job": dict(job) if job else None,
            "archive": {"candles": archive[0], "from": archive[1], "through": archive[2]},
            "horizon_bars": f.HORIZON, "round_trip_cost_pct": f.ROUND_TRIP_COST * 100,
            "max_age_days": MAX_AGE_DAYS}


def _job(identity, state, message, model_id=None):
    with closing(get_connection()) as db, db:
        db.execute("UPDATE lightgbm_jobs SET status=?,updated_at=?,message=?,model_id=? WHERE id=?",
                   (state,int(time.time()*1000),message,model_id,identity))


def start(ticker, timeframe, refresh_data=True):
    ticker = _check(ticker, timeframe)
    if not dependency_available():
        raise ValueError("LightGBM fehlt. Bitte LightGBM-installieren.cmd ausführen.")
    with _lock, closing(get_connection()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM lightgbm_jobs WHERE status IN ('QUEUED','RUNNING') ORDER BY created_at LIMIT 1").fetchone()
        if existing:
            if (existing["ticker"],existing["timeframe"]) != (ticker,timeframe):
                raise ValueError(f"Training läuft bereits für {existing['ticker']} {existing['timeframe']}. Bitte Abschluss abwarten.")
            return dict(existing)
        identity, now = uuid.uuid4().hex, int(time.time()*1000)
        db.execute("INSERT INTO lightgbm_jobs(id,ticker,timeframe,status,created_at,updated_at,message,model_id) VALUES(?,?,?,?,?,?,?,NULL)",
                   (identity,ticker,timeframe,"QUEUED",now,now,"Training wartet auf Start."))
    _executor.submit(_run, identity, ticker, timeframe, refresh_data)
    return {"id":identity,"ticker":ticker,"timeframe":timeframe,"status":"QUEUED"}


def inventory():
    """One read-only view of actual archives, refresh timestamps, models and jobs.

    A candle's timestamp is never presented as a fetch or training timestamp.
    Unknown timestamps from pre-migration archives remain null.
    """
    from instruments import CATALOG
    names = {row["symbol"]: row["name"] for row in CATALOG}
    with closing(get_connection()) as db:
        archives = {(r["ticker"],r["timeframe"]):dict(r) for r in db.execute(
            "SELECT ticker,timeframe,COUNT(*) AS candles,MIN(bar_at) AS first_bar_at,MAX(bar_end) AS last_bar_at FROM background_price_bars WHERE timeframe IN ('5m','15m') GROUP BY ticker,timeframe")}
        updates = {(r["ticker"],r["timeframe"]):dict(r) for r in db.execute("SELECT * FROM lightgbm_archive_updates")}
        models, active, jobs = {}, {}, {}
        for row in db.execute("SELECT id,ticker,timeframe,created_at,active,qualified,metadata_json FROM lightgbm_models ORDER BY created_at,rowid"):
            key = row["ticker"],row["timeframe"]
            models[key] = _public_model(row)
            if row["active"]:
                active[key] = models[key]
        for row in db.execute("SELECT * FROM lightgbm_jobs ORDER BY created_at,rowid"):
            jobs[row["ticker"],row["timeframe"]] = dict(row)
        running = [dict(row) for row in db.execute("SELECT * FROM lightgbm_jobs WHERE status IN ('QUEUED','RUNNING') ORDER BY queue_order,created_at,rowid")]
        batch_id = running[0]["batch_id"] if running else None
        batch_jobs = [dict(row) for row in db.execute("SELECT * FROM lightgbm_jobs WHERE batch_id=? ORDER BY queue_order",(batch_id,))] if batch_id else running
    now = int(time.time()*1000)
    rows = []
    for key in sorted(set(archives)|set(models)|set(jobs)):
        current, latest = active.get(key), models.get(key)
        state = "NOT_TRAINED"
        if current:
            state = ("INCOMPATIBLE" if current.get("version") != f.VERSION else
                     "STALE" if now-current.get("data_through",0)>MAX_AGE_DAYS*86400000 else "READY")
        elif latest:
            state = "OBSERVATION"
        rows.append({"ticker":key[0],"name":names.get(key[0],key[0]),"timeframe":key[1],
                     **archives.get(key,{"candles":0,"first_bar_at":None,"last_bar_at":None}),
                     "last_checked_at":updates.get(key,{}).get("checked_at"),
                     "last_updated_at":updates.get(key,{}).get("changed_at"),
                     "trained_at":latest["created_at"] if latest else None,
                     "active_model":current,"latest_model":latest,"model_status":state,"job":jobs.get(key)})
    return {"enabled":enabled(),"installed":dependency_available(),"rows":rows,
            "queue":running,"batch":{"id":batch_id,"total":len(batch_jobs),
                "completed":sum(row["status"] not in ("QUEUED","RUNNING") for row in batch_jobs)} if running else None,
            "generated_at":now}


def start_batch(symbols,timeframes,refresh_data=True):
    if not 1 <= len(symbols) <= 16 or not timeframes or len(timeframes)>2:
        raise ValueError("Eine bis 16 Aktien sowie 5m und/oder 15m auswählen.")
    pairs = [(normalize_ticker(symbol),tf) for symbol in symbols for tf in timeframes]
    if len(set(pairs)) != len(pairs):
        raise ValueError("Aktien und Timeframes dürfen nicht doppelt ausgewählt werden.")
    for ticker,tf in pairs:
        _check(ticker,tf)
    if not dependency_available():
        raise ValueError("LightGBM fehlt. Bitte die Installation ausführen.")
    with _lock,closing(get_connection()) as db,db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM lightgbm_jobs WHERE status IN ('QUEUED','RUNNING') ORDER BY queue_order,created_at").fetchall()
        if existing:
            if {(row["ticker"],row["timeframe"]) for row in existing} == set(pairs):
                return {"batch_id":existing[0]["batch_id"],"jobs":[dict(row) for row in existing],"already_running":True}
            raise ValueError("Ein Training läuft bereits. Die gespeicherte Warteschlange zuerst abschließen lassen.")
        batch,now,jobs = uuid.uuid4().hex,int(time.time()*1000),[]
        for order,(ticker,tf) in enumerate(pairs):
            identity = uuid.uuid4().hex
            db.execute("INSERT INTO lightgbm_jobs(id,ticker,timeframe,status,created_at,updated_at,message,batch_id,queue_order) VALUES(?,?,?,'QUEUED',?,?,?,?,?)",
                       (identity,ticker,tf,now,now,"Wartet in der Trainingswarteschlange.",batch,order))
            jobs.append({"id":identity,"ticker":ticker,"timeframe":tf,"status":"QUEUED","batch_id":batch})
    _executor.submit(_run_batch,jobs,refresh_data)
    return {"batch_id":batch,"jobs":jobs,"already_running":False}


def _run_batch(jobs,refresh_data):
    for job in jobs:
        _run(job["id"],job["ticker"],job["timeframe"],refresh_data)


def download_history(ticker, timeframe):
    """Manual-only longer history than the live quote window. Provider can limit availability."""
    import yfinance as yf
    provider = yf.Ticker(ticker)
    frame = provider.history(period="60d", interval=timeframe, auto_adjust=True,
                                    actions=False, prepost=False, timeout=25)
    if frame.empty:
        raise ValueError("Der Kursanbieter liefert keine historische Kursreihe.")
    frame = frame[["Open","High","Low","Close","Volume"]].copy()
    frame.attrs = {"timeframe":timeframe,"exchange_timezone":str(frame.index.tz),"fetched_at":time.time()}
    # Shared archive rejects merging different currencies/timezones. Use provider metadata.
    metadata = provider.history_metadata or {}
    frame.attrs.update(currency=metadata.get("currency"), exchange_timezone=metadata.get("exchangeTimezoneName",str(frame.index.tz)))
    return completed_bars(frame,timeframe)


def training_frame(ticker, timeframe, refresh_data):
    from instruments import source_ticker
    from run_stock_backtest import load_frame
    if refresh_data:
        frame = download_history(source_ticker(ticker), timeframe)
        # A manual refresh also applies older corrections/split adjustments in the returned history.
        store_frame(ticker,timeframe,frame,revisit_all=True)
    else:
        # Explicitly local-only: retained archive needs neither a provider nor an original snapshot.
        with closing(get_connection()) as db:
            rows = db.execute("SELECT * FROM background_price_bars WHERE ticker=? AND timeframe=? ORDER BY bar_at",(ticker,timeframe)).fetchall()
        if rows:
            if any(row["currency"] != rows[-1]["currency"] or row["exchange_timezone"] != rows[-1]["exchange_timezone"] for row in rows):
                raise ValueError("Archiv enthält abweichende Währung oder Zeitzone.")
            frame = pd.DataFrame({name.title():[row[name] for row in rows] for name in ("open","high","low","close","volume")},
                                 index=pd.to_datetime([row["bar_at"] for row in rows],unit="ms",utc=True))
            frame.attrs = {"timeframe":timeframe,"currency":rows[-1]["currency"],"exchange_timezone":rows[-1]["exchange_timezone"]}
            f.validate(frame,timeframe)
            return frame
        path = ROOT / "backtest_data" / f"{ticker}_{timeframe}.json"
        if not path.is_file():
            raise ValueError("Keine lokale Kursdatei. Zuerst Daten aktualisieren.")
        frame = load_frame(path,timeframe)
    if frame.empty:
        raise ValueError("Keine abgeschlossenen Kurskerzen vorhanden.")
    f.validate(frame,timeframe)
    return merged_frame(ticker,timeframe,frame,window_days=730)


def train(frame, timeframe):
    """Fixed parameters; validation-only early stopping; purged chronological test."""
    import lightgbm as lgb
    frame = observed_closed(frame, timeframe)
    x,y,label_end = f.dataset(frame,timeframe)
    parts = f.calibration_split(x,y,label_end,frame.attrs.get("exchange_timezone","UTC"))
    (x_train,y_train),(x_val,y_val),(x_cal,y_cal),(x_test,y_test) = parts
    params = {"objective":"multiclass","num_class":3,"metric":"multi_logloss","verbosity":-1,
              "num_threads":2,"deterministic":True,"force_col_wise":True,"seed":42,
              "learning_rate":.04,"num_leaves":7,"max_depth":3,"min_data_in_leaf":40,
              "lambda_l2":5,"feature_fraction":.85,"feature_fraction_seed":42}
    booster = lgb.train(params,lgb.Dataset(x_train,label=y_train,feature_name=f.FEATURES),
                        num_boost_round=200,valid_sets=[lgb.Dataset(x_val,label=y_val,reference=None)],
                        callbacks=[lgb.early_stopping(20,verbose=False)])
    prior = (np.bincount(y_train,minlength=3) + 1) / (len(y_train) + 3)
    temperature = f.fit_temperature(booster.predict(x_cal,num_threads=2),y_cal)
    raw = booster.predict(x_test,num_threads=2)
    probabilities = f.calibrate(raw, temperature)
    report = f.metrics(probabilities,y_test,prior)
    positions = f.nonoverlapping_positions(x_test.index,label_end)
    independent = f.metrics(probabilities[positions],y_test.iloc[positions],prior)
    # Fixed chronological halves expose a model whose advantage disappears recently.
    periods = [f.metrics(probabilities[part],y_test.iloc[part],prior)
               for part in np.array_split(np.arange(len(x_test)),2)]
    failures = []
    if not f.qualifies(report):
        failures.append("Gesamttest besteht die Mindestprüfung nicht.")
    if not f.qualifies(independent):
        failures.append("Nicht überlappende Prognosefenster bestehen die Mindestprüfung nicht.")
    if any(p["log_loss"] >= p["baseline_log_loss"] for p in periods):
        failures.append("Kein stabiler Vorteil gegenüber der Basisprognose in beiden Testhälften.")
    importance = sorted(zip(f.FEATURES,booster.feature_importance(importance_type="gain")),key=lambda row:-row[1])
    metadata = {"version":f.VERSION,"features":f.FEATURES,"horizon_bars":f.HORIZON,
                "round_trip_cost_pct":f.ROUND_TRIP_COST*100,"metrics":report,
                "training_samples":len(x_train),"validation_samples":len(x_val),
                "calibration_samples":len(x_cal),
                "calibration_from":int(x_cal.index[0].timestamp()*1000),
                "calibration_through":int(label_end.loc[x_cal.index].max().timestamp()*1000),
                "calibration":{"method":"temperature_scaling","temperature":temperature},
                "raw_metrics":f.metrics(raw,y_test,prior),
                "nonoverlapping_metrics":independent,"period_metrics":periods,
                "release_failures":failures,
                "training_from":int(x_train.index[0].timestamp()*1000),
                "training_through":int(label_end.loc[x_train.index].max().timestamp()*1000),
                "validation_from":int(x_val.index[0].timestamp()*1000),
                "test_from":int(x_test.index[0].timestamp()*1000),
                "validated_through":int(label_end.loc[x_test.index].max().timestamp()*1000),
                "data_through":int((frame.index[-1]+pd.Timedelta(minutes=5 if timeframe=="5m" else 15)).timestamp()*1000),
                "iterations":booster.best_iteration,"parameters":params,
                "top_features":[{"name":name,"gain":round(float(gain),4)} for name,gain in importance[:6]],
                "target":"Richtung über drei Kerzen ab nächstem Open, nach modellierter Kostenhürde; keine Setup-Erfolgswahrscheinlichkeit."}
    return booster.model_to_string(), metadata, f.fingerprint(x,y,label_end), not failures


def _run(identity,ticker,timeframe,refresh_data):
    try:
        _job(identity,"RUNNING","Lade und archiviere abgeschlossene historische Kerzen…")
        frame = observed_closed(training_frame(ticker,timeframe,refresh_data),timeframe)
        x,y,ends = f.dataset(frame,timeframe)
        data_hash = f.fingerprint(x,y,ends)
        with closing(get_connection()) as db:
            existing = db.execute("SELECT id FROM lightgbm_models WHERE ticker=? AND timeframe=? AND version=? AND data_hash=? ORDER BY created_at DESC LIMIT 1",
                                  (ticker,timeframe,f.VERSION,data_hash)).fetchone()
        if existing:
            _job(identity,"UNCHANGED","Keine neuen Trainingsdaten. Gespeichertes Modell wird weiterverwendet.",existing[0])
            return
        _job(identity,"RUNNING",f"Trainiere lokal mit zwei CPU-Threads; {len(x)} Beispiele. Prüfe spätere Zeiträume…")
        model_text,metadata,data_hash,qualified = train(frame,timeframe)
        model_id,now = uuid.uuid4().hex,int(time.time()*1000)
        metadata["model_sha256"] = hashlib.sha256(model_text.encode()).hexdigest()
        with closing(get_connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if qualified:
                db.execute("UPDATE lightgbm_models SET active=0 WHERE ticker=? AND timeframe=?",(ticker,timeframe))
            db.execute("INSERT INTO lightgbm_models VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (model_id,ticker,timeframe,f.VERSION,now,int(qualified),int(qualified),data_hash,json.dumps(metadata,allow_nan=False),model_text))
            # Retain the active model and four most recent attempts, without unbounded model growth.
            db.execute("DELETE FROM lightgbm_models WHERE ticker=? AND timeframe=? AND active=0 AND id NOT IN (SELECT id FROM lightgbm_models WHERE ticker=? AND timeframe=? ORDER BY created_at DESC LIMIT 5)",
                       (ticker,timeframe,ticker,timeframe))
        _job(identity,"READY" if qualified else "OBSERVATION",
             "Zeitliche Prüfung bestanden. Modell für neue Kerzen freigegeben." if qualified else
             "Training gespeichert, Vergleichsprüfung nicht bestanden. Dieses Modell hat keinen Signaleinfluss; ein bisher freigegebenes Modell bleibt erhalten.",model_id)
        with _lock:
            _prediction_cache.clear()
    except ValueError as exc:
        _job(identity,"NEEDS_DATA",str(exc))
    except Exception:
        logger.exception("Local LightGBM training failed for %s %s",ticker,timeframe)
        _job(identity,"FAILED","Training fehlgeschlagen. Vorheriges Modell bleibt erhalten; Details im Backend-Protokoll.")


def predict(ticker,timeframe,frame,*,stale=False):
    result = {"status":"UNSUPPORTED","direction":"NEUTRAL","usable":False,"model_id":None,
              "reason":"LightGBM ist für 5m und 15m vorgesehen.","horizon_bars":f.HORIZON}
    if timeframe not in ("5m","15m"):
        return result
    if not enabled():
        return {**result,"status":"DISABLED","reason":"LightGBM-Filter ist ausgeschaltet."}
    if not dependency_available():
        return {**result,"status":"NOT_INSTALLED","reason":"LightGBM installieren und Backend neu starten."}
    with closing(get_connection()) as db:
        row = db.execute("SELECT id,version,created_at,metadata_json FROM lightgbm_models WHERE ticker=? AND timeframe=? AND active=1",(ticker,timeframe)).fetchone()
    if not row:
        return {**result,"status":"NO_MODEL","reason":"Noch kein freigegebenes Modell. Daten aktualisieren und trainieren."}
    metadata = json.loads(row["metadata_json"])
    result.update(model_id=row["id"],trained_at=row["created_at"],horizon_minutes=f.HORIZON*(5 if timeframe=="5m" else 15))
    if row["version"] != f.VERSION or metadata.get("features") != f.FEATURES:
        return {**result,"status":"INCOMPATIBLE","reason":"Modellversion geändert; bitte neu trainieren."}
    now = int(time.time()*1000)
    if stale or now - metadata["data_through"] > MAX_AGE_DAYS*86400000:
        return {**result,"status":"STALE","reason":"Kursdaten oder Modellhistorie veraltet. Daten aktualisieren und neu trainieren."}
    frame = observed_closed(frame,timeframe)
    f.validate(frame,timeframe)
    if frame.empty:
        return {**result,"status":"WARMUP","reason":"Keine abgeschlossenen Kerzen."}
    bar_end = int((frame.index[-1]+pd.Timedelta(minutes=5 if timeframe=="5m" else 15)).timestamp()*1000)
    if bar_end <= metadata["validated_through"]:
        return {**result,"status":"WAITING_FOR_NEW_BAR","reason":"Modell gespeichert. Filter startet mit der nächsten neuen abgeschlossenen Kerze."}
    values = f.features(frame).iloc[-1:]
    if values[["return_24","ema_48_gap","volatility_48"]].isna().any(axis=None):
        return {**result,"status":"WARMUP","reason":"Mindestens 49 abgeschlossene Kerzen erforderlich."}
    key = (row["id"],bar_end,hashlib.sha256(pd.util.hash_pandas_object(values,index=True).values.tobytes()).hexdigest())
    with _lock:
        if key in _prediction_cache:
            return dict(_prediction_cache[key])
        import lightgbm as lgb
        if row["id"] not in _models:
            with closing(get_connection()) as db:
                stored = db.execute("SELECT model_text FROM lightgbm_models WHERE id=?",(row["id"],)).fetchone()
            if stored is None:
                return {**result,"status":"NO_MODEL","reason":"Modell wurde während der Berechnung aktualisiert. Analyse erneut laden."}
            model_text = stored[0]
            if hashlib.sha256(model_text.encode()).hexdigest() != metadata["model_sha256"]:
                return {**result,"status":"INVALID_MODEL","reason":"Modell-Prüfsumme ungültig. Neu trainieren."}
            _models[row["id"]] = lgb.Booster(model_str=model_text)
            while len(_models)>8:
                _models.popitem(last=False)
        p = np.asarray(_models[row["id"]].predict(values,num_threads=1))[0]
        if len(p)!=3 or not np.isfinite(p).all() or (p<0).any() or abs(p.sum()-1)>.001:
            return {**result,"status":"INVALID_MODEL","reason":"Ungültige Modellausgabe; kein Filtereinfluss."}
        p = f.calibrate(p.reshape(1,3),metadata.get("calibration",{}).get("temperature",1.0))[0]
        direction = "UP" if p[2]>=.6 else "DOWN" if p[0]>=.6 else "NEUTRAL"
        result.update(status="AVAILABLE",usable=True,direction=direction,bar_end=bar_end,
                      probabilities={"down":round(float(p[0]),4),"flat":round(float(p[1]),4),"up":round(float(p[2]),4)},
                      reason="Lokale Richtungsprüfung über drei Kerzen. Modellwerte sind keine garantierten Trefferquoten.")
        _prediction_cache[key] = dict(result)
        while len(_prediction_cache)>256:
            _prediction_cache.popitem(last=False)
    return result


def assess(ticker,timeframe,frame,*,stale=False):
    try:
        return predict(ticker,timeframe,frame,stale=stale)
    except Exception:
        logger.exception("Local ML inference unavailable for %s %s",ticker,timeframe)
        return {"status":"UNAVAILABLE","direction":"NEUTRAL","usable":False,"model_id":None,
                "reason":"LightGBM derzeit nicht verfügbar; kein zusätzlicher Filtereinfluss."}


def filter_decision(decision,prediction,*,previous=None):
    """Only veto NEW contrary signals. Existing managed setups retain their own lifecycle."""
    if previous and previous.get("status") in ("VORBEREITET","AKTIV"):
        return decision, "EXISTING_SETUP"
    direction = decision.get("signal_type")
    opposed = (direction == "BUY" and prediction.get("direction") == "DOWN" or
               direction == "SELL" and prediction.get("direction") == "UP")
    if not prediction.get("usable") or not opposed:
        return decision, "PASS" if prediction.get("usable") else "INACTIVE"
    reason = "LightGBM sperrt diesen neuen Einstieg: deutliche modellierte Gegenrichtung über drei Kerzen."
    return {**decision,"signal_type":"NEUTRAL","strength":0,"setup_quality":"C",
            "entry_price":None,"entry_zone_min":None,"entry_zone_max":None,"stop_loss":None,
            "take_profit_1":None,"take_profit_2":None,"risk_reward_ratio":None,"potential_profit_pct":None,
            "validation_status":"accepted_neutral",
            "reasons":[reason,*decision.get("reasons",[])],"lightgbm_blocked":True}, "BLOCKED"
