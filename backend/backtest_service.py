"""Two explicit, persisted backtest profiles; no expensive work on GET/polling."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid

import pandas as pd

import backtest_archive as archive
from database import get_connection
from instruments import CATALOG
from market_data import candle_end, fetch_stock_df, market_metadata, serialize_klines
from run_stock_backtest import load_frame, hashes
from technical_backtest import Costs, EuroRates, Replay, simulate_portfolio, strategy_fingerprint
from background_prices import merged_frame

ROOT = Path(__file__).resolve().parent.parent
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="manual-backtest")
_lock = threading.Lock()
PROFILES = ("python", "gemini_quant")
STOCKS = {item["symbol"] for item in CATALOG if item["type"] == "EQUITY"}


def _path(ticker, timeframe, profile):
    if ticker not in STOCKS or timeframe not in ("5m", "15m") or profile not in PROFILES:
        raise ValueError("Backtests unterstützen die Katalog-Aktien auf 5m und 15m.")
    return ROOT / "backtest_results" / (f"{ticker}_{timeframe}.json" if profile == "python" else f"gemini_quant/{ticker}_{timeframe}.json")


def _save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _inputs(ticker, timeframe):
    directory = ROOT / "backtest_data"
    frames = {tf: load_frame(directory / f"{ticker}_{tf}.json", tf) for tf in ("1m", "5m", "15m")}
    fx = load_frame(directory / "EURUSD=X_1D.json", "1D")
    rates = EuroRates(pd.Series(fx.Close.to_numpy(), index=fx.index + pd.Timedelta(days=1)))
    data_hash = input_hashes(directory, [f"{ticker}_{tf}.json" for tf in ("1m", "5m", "15m")]+["EURUSD=X_1D.json"])
    return frames, rates, data_hash


def python_runner_fingerprint():
    return hashes(Path(__file__).parent, ["run_stock_backtest.py", "backtest_service.py", "background_prices.py"])


@lru_cache(maxsize=128)
def _input_hash(path, modified, size):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    frame = load_frame(path,payload["timeframe"])
    normalized = {"ticker":payload["ticker"],"timeframe":payload["timeframe"],"currency":payload["currency"],
                  "exchange_timezone":payload["exchange_timezone"],"closed_klines":serialize_klines(frame)}
    return hashlib.sha256(json.dumps(normalized,sort_keys=True,allow_nan=False).encode()).hexdigest()


def input_hashes(directory, names):
    result = {}
    for name in names:
        path = directory/name
        stat = path.stat()
        result[name] = _input_hash(str(path.resolve()),stat.st_mtime_ns,stat.st_size)
    return result


def _refresh_inputs(ticker):
    payloads = {}
    for symbol, tf in [(ticker, tf) for tf in ("1m", "5m", "15m")] + [("EURUSD=X", "1D")]:
        frame = fetch_stock_df(symbol, tf)
        if frame.empty or (symbol == ticker and len(frame) < 200):
            raise ValueError("Kursanbieter liefert nicht genug echte historische Kerzen.")
        meta = market_metadata(frame, symbol, tf)
        frame.attrs = {**frame.attrs,"currency":meta["currency"],"exchange_timezone":meta["exchange_timezone"]}
        frame = merged_frame(symbol,tf,frame)
        payloads[f"{symbol}_{tf}.json"] = {"ticker": symbol, "timeframe": tf, "klines": serialize_klines(frame),
            "currency": meta["currency"], "exchange_timezone": meta["exchange_timezone"],
            "snapshot_at": pd.Timestamp.now(tz="UTC").isoformat(), "source": "Yahoo via existing market_data"}
    # Only replace snapshots once ALL four feeds succeeded; retain the last good
    # dataset on acquisition errors. No retries or polling-driven network calls.
    for filename, payload in payloads.items():
        _save(ROOT / "backtest_data" / filename, payload)


def run(ticker, timeframe, profile, *, refresh_data=False):
    path = _path(ticker, timeframe, profile)
    if profile == "gemini_quant":
        status = archive.archive_status(ticker, timeframe)
        if not status["activated_setups"]:
            return {"status": "NEEDS_ARCHIVE", "message": "Noch keine passende aufgezeichnete Aktivierung der aktuellen Gemini+LightGBM-Version. Gemini mit lokalem LightGBM-Filter nutzen; danach diesen Test erneut starten.", **status}
    code = (strategy_fingerprint(), python_runner_fingerprint()) if profile == "python" else archive.strategy_key(timeframe)
    if refresh_data:
        _refresh_inputs(ticker)
    frames, rates, data_hash = _inputs(ticker, timeframe)
    if profile == "python":
        if len(frames["1m"]) < 200:
            raise ValueError("Zu wenig 1m-Daten für den Warmup.")
        start = candle_end(frames["1m"].index[199], "1m", frames["1m"].attrs)
        end = candle_end(frames["1m"].index[-1], "1m", frames["1m"].attrs)
        candidates, diagnostics = Replay(frames).candidates(ticker, timeframe, start=start, end=end)
        result = simulate_portfolio({ticker: frames["1m"]}, candidates, rates, timeframe=timeframe,
                                    start=start, end=end, diagnostics={ticker: diagnostics})
        result["runner_fingerprint"] = python_runner_fingerprint()
    else:
        from gemini_backtest import replay
        observed = archive.activations(ticker, timeframe,stream=status["stream"])
        # Test only the history following the first actual decision availability.
        first = pd.Timestamp(min(a["available_at"] for a in observed), unit="ms", tz="UTC").floor("min") + pd.Timedelta(minutes=1)
        execution = frames["1m"].loc[frames["1m"].index >= first].copy()
        if execution.empty:
            return {"status": "NEEDS_EXECUTION_DATA", "message": "Aktivierungen sind gespeichert, aber die eingefrorenen 1m-Kursdaten enden vorher. Kursarchiv aktualisieren.", **status}
        result = replay(ticker, timeframe, execution, observed, rates)
        if result["execution_rejections"].get("missing_next_minute"):
            result["coverage_note"] = "Einige Aktivierungen haben noch keine passende nachfolgende Ausführungskerze. Diese wurden nicht simuliert."
        if result["total_trades"] == 0 and result["execution_rejections"].get("missing_next_minute") == len(observed):
            return {"status": "NEEDS_EXECUTION_DATA", "message": "Keine Aktivierung hat eine passende nächste 1m-Kerze im Kursarchiv.", **status}
        result["archive_status"] = status
        result["activation_digest"] = activation_digest(observed)
    after = (strategy_fingerprint(), python_runner_fingerprint()) if profile == "python" else archive.strategy_key(timeframe)
    if code != after:
        raise ValueError("Strategie während des Tests geändert. Test erneut starten.")
    if input_hashes(ROOT / "backtest_data", list(data_hash)) != data_hash:
        raise ValueError("Kursarchiv während des Tests geändert. Test erneut starten.")
    result.update(data_sha256=data_hash,input_fingerprint_version=2, computed_at=int(time.time()*1000), backtest_profile=profile)
    _save(path, result)
    return {"status": "READY", "result": result, "message": "Test gespeichert."}


def activation_digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, allow_nan=False).encode()).hexdigest()


def slot(ticker, timeframe, profile):
    try:
        path = _path(ticker, timeframe, profile)
    except ValueError as exc:
        return {"status": "UNSUPPORTED", "message": str(exc), "result": None}
    status = archive.archive_status(ticker, timeframe) if profile == "gemini_quant" else {}
    if profile == "gemini_quant" and not status["activated_setups"]:
        return {"status": "NEEDS_ARCHIVE", "message": "Es fehlen aufgezeichnete Aktivierungen mit aktuellem Code und dokumentiertem LightGBM-Prüfstatus. Alte Gemini-Antworten werden nicht nachträglich umetikettiert.", "result": None, **status}
    if not path.exists():
        return {"status": "NOT_RUN", "message": "Per Knopfdruck auswerten.", "result": None, **status}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("Invalid saved report")
        matching = (result.get("strategy_fingerprint") == strategy_fingerprint() if profile == "python"
                    else result.get("strategy_key") == status["strategy_key"])
        matching = matching and result.get("symbols") == [ticker] and result.get("timeframe") == timeframe
        matching = matching and result.get("currency") == "EUR" and result.get("initial_capital") == 5000
        matching = matching and result.get("analysis_mode") == profile and result.get("allow_short") is False
        matching = matching and result.get("cost_rates") == Costs().__dict__
        if profile == "python":
            matching = matching and result.get("runner_fingerprint") == python_runner_fingerprint()
        expected = {f"{ticker}_{tf}.json" for tf in ("1m", "5m", "15m")} | {"EURUSD=X_1D.json"}
        matching = matching and set(result.get("data_sha256", {})) == expected
        matching = matching and result.get("input_fingerprint_version") == 2
        if matching:
            matching = input_hashes(ROOT / "backtest_data", sorted(expected)) == result["data_sha256"]
        if profile == "gemini_quant":
            matching = matching and result.get("activation_digest") == activation_digest(archive.activations(ticker, timeframe,stream=status["stream"]))
        if not matching:
            return {"status": "STALE", "message": "Code, Daten oder aufgezeichnete Aktivierungen geändert. Test per Knopfdruck erneuern.", "result": None, **status}
        return {"status": "READY", "message": "Gespeicherter Test mit passender Strategieversion.", "result": result, **status}
    except (ValueError, OSError, TypeError, KeyError):
        return {"status": "UNAVAILABLE", "message": "Gespeicherter Test oder Kursarchiv unvollständig. Erneut auswerten.", "result": None, **status}


def comparison(ticker, timeframe):
    return {"ticker": ticker, "timeframe": timeframe, **{profile: slot(ticker, timeframe, profile) for profile in PROFILES}}


def _update_job(identity, status, payload):
    with closing(get_connection()) as connection, connection:
        connection.execute("UPDATE backtest_jobs SET status=?,result_json=?,updated_at=? WHERE id=?",
                           (status, json.dumps(payload, ensure_ascii=False), int(time.time()*1000), identity))


def _work(identity, ticker, timeframe, profile, refresh_data):
    _update_job(identity, "RUNNING", {"message": "Backtest wird berechnet."})
    try:
        result = run(ticker, timeframe, profile, refresh_data=refresh_data)
        _update_job(identity, "COMPLETED", result)
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else "Backtest fehlgeschlagen. Kursarchiv und Backend-Protokoll prüfen."
        _update_job(identity, "FAILED", {"message": message[:300]})


def start(ticker, timeframe, profile, *, refresh_data=True):
    _path(ticker, timeframe, profile)
    with _lock, closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT id FROM backtest_jobs WHERE ticker=? AND timeframe=? AND profile=? AND status IN ('QUEUED','RUNNING') ORDER BY created_at DESC LIMIT 1",
                                     (ticker, timeframe, profile)).fetchone()
        if current:
            return {"id": current["id"], "status": "RUNNING"}
        identity, now = uuid.uuid4().hex, int(time.time()*1000)
        connection.execute("INSERT INTO backtest_jobs(id,ticker,timeframe,profile,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                           (identity, ticker, timeframe, profile, "QUEUED", now, now))
    _executor.submit(_work, identity, ticker, timeframe, profile, refresh_data)
    return {"id": identity, "status": "QUEUED"}


def job(identity):
    with closing(get_connection()) as connection:
        row = connection.execute("SELECT * FROM backtest_jobs WHERE id=?", (identity,)).fetchone()
    if not row:
        return None
    result = json.loads(row["result_json"] or "{}")
    return {"id": row["id"], "status": row["status"], "profile": row["profile"],
            "ticker": row["ticker"], "timeframe": row["timeframe"],
            "message": result.get("message"), "result": result}


def recover_jobs():
    with closing(get_connection()) as connection, connection:
        connection.execute("UPDATE backtest_jobs SET status='INTERRUPTED',result_json=? WHERE status IN ('QUEUED','RUNNING')",
                           (json.dumps({"message": "Backend neu gestartet. Test erneut per Knopfdruck starten."}),))
