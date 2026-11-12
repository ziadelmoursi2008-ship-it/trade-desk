"""Browser-independent collection, per-candle analyses and periodic report jobs.

Explicit opt-in. One SQLite-leased leader; persistent per-bar deduplication and
API attempt limits survive restarts. Never backfills historical Gemini calls.
"""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import os
import threading
import time
import uuid

import backtest_archive
import backtest_service
import background_settings as config
from analysis_modes import ModeChangedError, AnalysisBarChangedError
from analysis_scope import background_analysis
from background_prices import store_frame, observed_closed
from database import get_connection
from instruments import TIMEFRAME_MAP
from market_data import candle_end, market_metadata, serialize_klines
from signal_manager import monitor_signal_market
from technical_backtest import strategy_fingerprint
import lightgbm_service as ml

logger = logging.getLogger(__name__)
LEASE_SECONDS = 90


def _runtime(connection, key, fallback):
    row = connection.execute("SELECT value FROM background_runtime WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else fallback


def _write(connection, key, value):
    connection.execute("INSERT INTO background_runtime(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(value, ensure_ascii=False)))


def status():
    selected = config.settings()
    with closing(get_connection()) as connection:
        state = _runtime(connection, "status", {})
        lease = _runtime(connection, "lease", {})
        runs = [dict(row) for row in connection.execute("SELECT profile,ticker,timeframe,bar_end,status,started_at,finished_at,result_json FROM background_runs ORDER BY started_at DESC LIMIT 12")]
        prices = connection.execute("SELECT COUNT(*) FROM background_price_bars").fetchone()[0]
    now = time.time()
    return {"settings": selected, "runtime": state, "api_usage": config.api_usage(now),
            "gemini_configured": bool(os.getenv("GEMINI_API_KEY") and not os.getenv("GEMINI_API_KEY").startswith("your_")),
            "worker_alive": lease.get("expires_at", 0) > now, "price_bars": prices, "recent_runs": runs,
            "backend_observed_at": now, "market_hours_only": True,
            "regular_analyses_per_hour": len(selected["symbols"])*sum(3600//TIMEFRAME_MAP[tf]["seconds"] for tf in selected["timeframes"]),
            "api_limits_apply_to": "background_attempts_including_repairs; manual calls are additional"}


class BackgroundService:
    def __init__(self, evaluate, market_frame, *, asynchronous=True):
        self.evaluate = evaluate
        self.market_frame = market_frame
        self.owner = uuid.uuid4().hex
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.threads = []
        self.last_closed_check = {}
        self.last_price_end = {}
        self.open_symbols = set()
        self.revision = None
        self.asynchronous = asynchronous
        self.analyzer = ThreadPoolExecutor(max_workers=1,thread_name_prefix="background-analysis")
        self.frame_slots = {}
        self.gemini_candidates = {}

    def start(self):
        for name, function in (("background-collector", self._loop), ("background-heartbeat", self._heartbeat)):
            thread = threading.Thread(name=name, target=function, daemon=True)
            self.threads.append(thread)
            thread.start()

    def stop(self):
        self.stopping.set()
        self.wake.set()
        for thread in self.threads:
            thread.join(timeout=2)
        self.analyzer.shutdown(wait=False,cancel_futures=True)
        self._release()

    def _acquire(self, now=None):
        now = time.time() if now is None else now
        with closing(get_connection()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            lease = _runtime(connection, "lease", {})
            if lease.get("owner") not in (None, self.owner) and lease.get("expires_at", 0) > now:
                return False
            if lease.get("owner") != self.owner:
                connection.execute("UPDATE background_runs SET status='INTERRUPTED',finished_at=? WHERE status IN ('QUEUED','RUNNING')",(now,))
            _write(connection, "lease", {"owner": self.owner, "expires_at": now+LEASE_SECONDS})
        return True

    def _release(self):
        with closing(get_connection()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            lease = _runtime(connection, "lease", {})
            if lease.get("owner") == self.owner:
                _write(connection, "lease", {"owner": None, "expires_at": 0})

    def _guard(self, revision, mode):
        if self.stopping.is_set():
            raise ModeChangedError("Hintergrunddienst wird beendet.")
        selected = config.ensure_active(revision)
        if not selected["gemini_enabled" if mode == "gemini" else "python_enabled"]:
            raise ModeChangedError("Hintergrundprofil ausgeschaltet.")
        with closing(get_connection()) as connection:
            lease = _runtime(connection, "lease", {})
        if lease.get("owner") != self.owner or lease.get("expires_at", 0) < time.time():
            raise ModeChangedError("Hintergrunddienst hat seine Ausführungssperre verloren.")

    def _publish(self, **values):
        if self.stopping.is_set():
            return
        with closing(get_connection()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            state = _runtime(connection, "status", {})
            _write(connection, "status", {**state, **values})

    def _heartbeat(self):
        while not self.stopping.wait(15):
            try:
                if self.threads and not self.threads[0].is_alive():
                    self._release()
                    self._publish(status="ERROR",last_error="Hintergrundprozess beendet. Backend neu starten.")
                    return
                selected = config.settings()
                with closing(get_connection()) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    lease = _runtime(connection, "lease", {})
                    if selected["enabled"] and lease.get("owner") == self.owner:
                        _write(connection, "lease", {"owner": self.owner, "expires_at": time.time()+LEASE_SECONDS})
            except Exception as exc:
                logger.warning("Background heartbeat unavailable (%s)", type(exc).__name__)

    def _loop(self):
        while not self.stopping.is_set():
            delay = 25
            try:
                selected = config.settings()
                if not selected["enabled"]:
                    self._release()
                    self._publish(status="DISABLED", heartbeat_at=time.time(), current=None)
                elif self._acquire():
                    self.tick(selected)
                    if len(self.last_closed_check) == len(selected["symbols"]):
                        delay = 60
            except ModeChangedError:
                pass
            except Exception as exc:
                logger.warning("Background tick failed (%s)", type(exc).__name__)
                self._publish(status="ERROR", last_error="Hintergrundlauf fehlgeschlagen. Kursfeed/Backend prüfen.", heartbeat_at=time.time())
                delay = 60
            self.wake.wait(delay)
            self.wake.clear()

    def _claim(self, ticker, timeframe, profile, bar_end):
        code = (strategy_fingerprint(), backtest_service.python_runner_fingerprint()) if profile == "python" else backtest_archive.strategy_key(timeframe)
        key = hashlib.sha256(json.dumps(code,sort_keys=True).encode()).hexdigest()
        identity = uuid.uuid4().hex
        with closing(get_connection()) as connection, connection:
            inserted = connection.execute("INSERT OR IGNORE INTO background_runs(id,profile,ticker,timeframe,bar_end,strategy_key,status,started_at) VALUES(?,?,?,?,?,?,'QUEUED',?)",
                                          (identity,profile,ticker,timeframe,bar_end,key,time.time())).rowcount
        return identity if inserted else None

    def _finish(self, identity, status, details):
        with closing(get_connection()) as connection, connection:
            connection.execute("UPDATE background_runs SET status=?,finished_at=?,result_json=? WHERE id=?",
                               (status,time.time(),json.dumps(details,ensure_ascii=False),identity))

    def _analyze(self, identity, selected, ticker, tf, profile, mode, bar_end):
        try:
            self._guard(selected["revision"],mode)
            if mode == "gemini":
                usage = config.api_usage()
                if usage["hour"] >= selected["api_hour_limit"] or usage["day"] >= selected["api_day_limit"]:
                    self._finish(identity,"API_LIMIT",{"message":"Gemini-Limit erreicht; keine weitere API-Anfrage gestartet."})
                    self._publish(status="API_LIMIT",gemini_message="Gemini-Limit erreicht; Python und Kursbeobachtung laufen weiter.")
                    return
            if time.time()*1000-bar_end >= TIMEFRAME_MAP[tf]["seconds"]*1000:
                self._finish(identity,"SKIPPED_TOO_LATE",{"message":"Warteschlange zu alt; keine rückwirkende KI-Analyse gestartet."})
                return
            self._publish(current=f"{ticker} {tf} · {profile}")
            with closing(get_connection()) as connection,connection:
                connection.execute("UPDATE background_runs SET status='RUNNING' WHERE id=?",(identity,))
            def guard():
                self._guard(selected["revision"],mode)
                if time.time()*1000-bar_end >= TIMEFRAME_MAP[tf]["seconds"]*1000:
                    raise AnalysisBarChangedError("Hintergrundanalyse ist für diese Kerze inzwischen zu spät.")
            reserve = lambda: config.reserve_api_call(selected["revision"],ticker,tf)
            with background_analysis(mode,selected["revision"],guard,reserve):
                result = self.evaluate(ticker,tf,mode=mode,with_backtest=False,expected_bar_end=bar_end)
                guard()
            completed = mode=="python" or result.get("ai_analysis_method")=="gemini"
            quant_state = result.get("quantitative") or {}
            quant_weight = result.get("decision_policy",{}).get("decision_weights_pct",{}).get("quantitative",0)
            quant_ok = True  # Missing ML models are explicitly archived, not mistaken for failed Gemini calls.
            self._finish(identity,"COMPLETED" if completed and quant_ok else "UNAVAILABLE",{
                "analysis_bar_at":result.get("analysis_bar_at"),"method":result.get("ai_analysis_method"),
                "signal":result.get("decision",{}).get("signal_type"),"quant_status":quant_state.get("status"),"quant_weight":quant_weight})
            self._publish(last_analysis_at=time.time())
            if mode=="gemini":
                message = None if completed else "Gemini-Antwort derzeit nicht verwertbar; keine neue KI-Freigabe archiviert."
                self._publish(gemini_message=message)
            if not completed:
                self._publish(status="WAITING_FOR_ANALYSIS",last_error=f"{ticker} {tf}: Gemini-Antwort derzeit nicht verwertbar; keine neue KI-Freigabe archiviert.")
        except AnalysisBarChangedError:
            self._finish(identity,"SKIPPED_BAR_CHANGED",{"message":"Kerze gewechselt; keine verspätete KI-Freigabe übernommen."})
            self.frame_slots.pop((ticker,tf),None)
        except ModeChangedError:
            self._finish(identity,"INTERRUPTED",{"message":"Einstellungen oder Hintergrundbetrieb geändert."})
            if not self.asynchronous: raise
        except Exception as exc:
            self._finish(identity,"FAILED",{"message":f"Analyse fehlgeschlagen ({type(exc).__name__})."})
            self._publish(last_error=f"{ticker} {tf}: Analyse fehlgeschlagen.")
        finally:
            self._publish(current=None)
            self.wake.set()

    def _queue(self, *args):
        if not self.asynchronous:
            return self._analyze(*args)
        identity = args[0]
        future = self.analyzer.submit(self._analyze,*args)
        future.add_done_callback(lambda task:self._finish(identity,"INTERRUPTED",{"message":"Backend wird beendet."}) if task.cancelled() else None)

    def _reports(self, selected, ticker, timeframe, bar_end, *, force=False):
        for profile, enabled in (("python",selected["python_enabled"]),("gemini_quant",selected["gemini_enabled"])):
            if not enabled:
                continue
            key = f"report:{profile}:{ticker}:{timeframe}"
            with closing(get_connection()) as connection:
                previous = _runtime(connection,key,{})
            code = (strategy_fingerprint(),backtest_service.python_runner_fingerprint()) if profile == "python" else backtest_archive.strategy_key(timeframe)
            signature = hashlib.sha256(json.dumps(code,sort_keys=True).encode()).hexdigest()
            count = backtest_archive.archive_status(ticker,timeframe)["activated_setups"] if profile=="gemini_quant" else 0
            force = force or (profile=="gemini_quant" and count>0 and not previous.get("activations",0))
            if previous.get("bar_end") == bar_end and previous.get("signature") == signature:
                continue
            if not force and time.time()-previous.get("at",0) < selected["report_interval_minutes"]*60:
                continue
            self._guard(selected["revision"], "python" if profile == "python" else "gemini")
            job = backtest_service.start(ticker,timeframe,profile)
            with closing(get_connection()) as connection, connection:
                _write(connection,key,{"at":time.time(),"job_id":job["id"],"bar_end":bar_end,"signature":signature,"activations":count})

    def tick(self, selected=None):
        selected = selected or config.settings()
        if not selected["enabled"] or not self._acquire():
            return
        if selected["revision"] != self.revision:
            self.last_closed_check.clear()
            self.frame_slots.clear()
            self.gemini_candidates.clear()
            self.revision = selected["revision"]
            self._publish(gemini_message=None)
        self._publish(status="RUNNING",heartbeat_at=time.time(),last_error=None)
        for ticker in selected["symbols"]:
            if self.stopping.is_set():
                return
            if time.time()-self.last_closed_check.get(ticker,0) < 55:
                continue
            mode = "python" if selected["python_enabled"] else "gemini"
            self._guard(selected["revision"],mode)
            try:
                minute = self.market_frame(ticker,"1m")
                self._guard(selected["revision"],mode)
                store_frame(ticker,"1m",minute)
                meta = market_metadata(minute,ticker,"1m")
                minute_closed = observed_closed(minute,"1m")
                minute_end = int(candle_end(minute_closed.index[-1],"1m",minute_closed.attrs).timestamp()*1000) if len(minute_closed) else 0
                if not meta["market_open"]:
                    self.gemini_candidates = {key:value for key,value in self.gemini_candidates.items() if key[0]!=ticker}
                    changed = self.last_price_end.get(ticker) != minute_end
                    was_open = ticker in self.open_symbols
                    self.open_symbols.discard(ticker)
                    for tf in selected["timeframes"]:
                        if changed:
                            frame = self.market_frame(ticker,tf)
                            self._guard(selected["revision"],mode)
                            store_frame(ticker,tf,frame)
                        self._reports(selected,ticker,tf,minute_end,force=was_open)
                    self.last_price_end[ticker] = minute_end
                    self.last_closed_check[ticker] = time.time()
                    self._publish(status="MARKET_CLOSED",current=ticker,heartbeat_at=time.time())
                    continue
                self.last_closed_check.pop(ticker,None)
                self.open_symbols.add(ticker)
                self.last_price_end[ticker] = minute_end
                if meta["is_stale"]:
                    self._publish(status="WAITING_FOR_DATA",last_error=f"{ticker}: Kursdaten veraltet.")
                    continue
                bars = serialize_klines(minute.tail(2))
                for bar, at in zip(bars, minute.tail(2).index):
                    bar["observed_at"] = int(minute.attrs.get("fetched_at",time.time())*1000)
                    bar["end_timestamp"] = int(candle_end(at,"1m",minute.attrs).timestamp()*1000)
                for tf in selected["timeframes"]:
                    if selected["gemini_enabled"]:
                        with background_analysis("gemini",selected["revision"],lambda: self._guard(selected["revision"],"gemini"),lambda: None):
                            monitor_signal_market(ticker,tf,bars,stale=False,mode="gemini")
                    wall_slot = int(time.time()//TIMEFRAME_MAP[tf]["seconds"])
                    if self.frame_slots.get((ticker,tf)) == wall_slot:
                        self._reports(selected,ticker,tf,minute_end)
                        continue
                    frame = self.market_frame(ticker,tf)
                    self._guard(selected["revision"],mode)
                    store_frame(ticker,tf,frame)
                    closed = observed_closed(frame,tf)
                    if closed.empty:
                        continue
                    end = int(candle_end(closed.index[-1],tf,closed.attrs).timestamp()*1000)
                    if time.time()*1000-end > TIMEFRAME_MAP[tf]["seconds"]*2000:
                        continue
                    if end >= wall_slot*TIMEFRAME_MAP[tf]["seconds"]*1000:
                        self.frame_slots[ticker,tf] = wall_slot
                    for profile, analysis_mode, enabled in (("python","python",selected["python_enabled"]),("gemini_quant","gemini",selected["gemini_enabled"])):
                        if not enabled:
                            continue
                        if analysis_mode == "gemini":
                            if not os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY").startswith("your_"):
                                self._publish(status="MISSING_API_KEY",gemini_message="Gemini-Schlüssel fehlt; Python und Kursbeobachtung laufen weiter.")
                                continue
                            usage = config.api_usage()
                            if usage["hour"] >= selected["api_hour_limit"] or usage["day"] >= selected["api_day_limit"]:
                                self._publish(status="API_LIMIT",gemini_message="Gemini-Limit erreicht; Python und Kursbeobachtung laufen weiter.")
                                continue
                            prediction = ml.assess(ticker,tf,closed,stale=False)
                            probabilities = prediction.get("probabilities") or {}
                            self.gemini_candidates[ticker,tf] = (end,max(probabilities.get("up",0),probabilities.get("down",0)))
                            continue
                        identity = self._claim(ticker,tf,profile,end)
                        if not identity:
                            continue
                        self._queue(identity,selected,ticker,tf,profile,analysis_mode,end)
                    self._reports(selected,ticker,tf,minute_end)
            except ModeChangedError:
                raise
            except Exception as exc:
                logger.warning("Background symbol unavailable (%s)",type(exc).__name__)
                self._publish(status="WAITING_FOR_DATA",last_error=f"{ticker}: Kursabruf oder Verarbeitung fehlgeschlagen.")
        if selected["gemini_enabled"]:
            self._dispatch_gemini(selected)
        self._publish(heartbeat_at=time.time())

    def _dispatch_gemini(self, selected):
        """Spread the fixed budget across time and rotate fairly across symbols/timeframes."""
        now = time.time()
        with closing(get_connection()) as db:
            if db.execute("SELECT 1 FROM background_runs WHERE profile='gemini_quant' AND status IN ('QUEUED','RUNNING') LIMIT 1").fetchone():
                return
            last = db.execute("SELECT MAX(attempted_at) FROM background_api_attempts").fetchone()[0]
            seen = {(r[0],r[1]):r[2] for r in db.execute("SELECT ticker,timeframe,MAX(started_at) FROM background_runs WHERE profile='gemini_quant' AND status IN ('COMPLETED','UNAVAILABLE','QUEUED','RUNNING') GROUP BY ticker,timeframe")}
        if last is not None and now-last < 3600/selected["api_hour_limit"]:
            self._publish(gemini_message="Gemini wartet auf den nächsten Budget-Zeitpunkt; lokale Analyse und Kursbeobachtung laufen weiter.")
            return
        usage = config.api_usage()
        if usage["hour"]>=selected["api_hour_limit"] or usage["day"]>=selected["api_day_limit"]:
            return
        candidates = [(key,value) for key,value in self.gemini_candidates.items()
                      if key[0] in selected["symbols"] and key[1] in selected["timeframes"]
                      and 0 <= now*1000-value[0] < TIMEFRAME_MAP[key[1]]["seconds"]*1000]
        candidates.sort(key=lambda item:(seen.get(item[0],0),-item[1][1],item[0]))
        for (ticker,tf),(end,_) in candidates:
            identity = self._claim(ticker,tf,"gemini_quant",end)
            if identity:
                self._queue(identity,selected,ticker,tf,"gemini_quant","gemini",end)
                return
