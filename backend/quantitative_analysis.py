"""Manual, persistent OHLCV delta estimates; never claims observed aggressor flow."""
from contextlib import closing
import hashlib
import json
import math
import time

import pandas as pd
from quantitative_metrics import WINDOW, FLOW_WEIGHTS, build_snapshot

from analysis_modes import ModeChangedError, ensure_analysis_mode
from database import get_connection
from instruments import CATALOG, TIMEFRAME_MAP
from market_data import candle_end, completed_bars
from analysis_scope import current_scope

QUANT_VERSION = "2.0-volume-evidence"
# The signing algorithm is unchanged: keep and extend all already calculated CVD rows.
LEDGER_VERSION = "1.0-manual-delta"
TECHNICAL_SHARE = 0.4
SOURCE_TIMEFRAMES = {"1m": "1m", "5m": "1m", "15m": "1m", "30m": "5m",
                     "1h": "5m", "4h": "1h", "1D": "1h"}
COMPONENT_WEIGHTS = FLOW_WEIGHTS
# Preserve the old volume budget (20% total * 60% volume) after removing price duplicates.
MAX_TOTAL_WEIGHT = 12.0
METHOD_NOTE = ("Delta/CVD sind OHLCV-Schätzungen aus Kerzenrichtung und Quellvolumen, "
               "keine gemessenen aggressiven Käufe/Verkäufe. Keine Trefferwahrscheinlichkeit.")


def settings():
    scoped = current_scope()
    if scoped is not None:
        scoped.guard()
        return {"enabled": scoped.mode == "gemini", "revision": scoped.revision, "manual_only": False}
    with closing(get_connection()) as connection:
        values = dict(connection.execute("SELECT key,value FROM app_settings "
                      "WHERE key IN ('quantitative_enabled','quantitative_revision')").fetchall())
    return {"enabled": values.get("quantitative_enabled") == "true",
            "revision": int(values.get("quantitative_revision", 0)), "manual_only": True}


def ensure_selection(revision):
    ensure_analysis_mode("gemini")
    if settings()["revision"] != revision:
        raise ModeChangedError("Quant-Auswahl wurde geändert. Bitte Analyse erneut starten.")


def save_settings(enabled):
    ensure_analysis_mode("gemini")
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM app_settings WHERE key='quantitative_enabled'").fetchone()
        if (row[0] == "true" if row else False) != enabled:
            connection.execute("UPDATE app_settings SET value=? WHERE key='quantitative_enabled'",
                               ("true" if enabled else "false",))
            connection.execute("UPDATE app_settings SET value=CAST(value AS INTEGER)+1 "
                               "WHERE key='quantitative_revision'")
    return settings()


def benchmark_for(ticker, frame):
    item = next((item for item in CATALOG if item["symbol"] == ticker), {})
    kind = item.get("type") or frame.attrs.get("instrument_type")
    if kind not in ("EQUITY", "ETF"):
        return None
    return "SPY" if ticker == "QQQ" else ("QQQ" if item.get("exchange") == "NASDAQ" else ("SPY" if ticker != "SPY" else None))


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _number(value):
    return float(value) if pd.notna(value) and math.isfinite(float(value)) else None


def _rows(frame, timeframe):
    if frame.attrs.get("timeframe") != timeframe:
        raise ValueError("Kerzendaten passen nicht zum angeforderten Timeframe.")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates or frame.index.tz is None:
        raise ValueError("Kerzen benötigen eindeutige, sortierte Zeitstempel mit Zeitzone.")
    rows = []
    for timestamp, row in frame.iterrows():
        values = [_number(row.get(key)) for key in ("Open", "High", "Low", "Close", "Volume")]
        if any(value is None or value <= 0 for value in values[:4]):
            raise ValueError("Ungültige Kursdaten für die Quant-Auswertung.")
        volume = values[-1] if values[-1] is not None and values[-1] >= 0 else 0.0
        flag = row.get("VolumeKnown")
        known = values[-1] is not None and values[-1] >= 0 and (flag is None or (pd.notna(flag) and bool(flag)))
        at = int(timestamp.timestamp() * 1000)
        end = int(candle_end(timestamp, timeframe, frame.attrs).timestamp() * 1000)
        session = frame.attrs.get("trading_sessions", {}).get(str(timestamp.tz_convert(
            frame.attrs.get("exchange_timezone", str(frame.index.tz))).date()))
        if session and timeframe != "1D":
            end = min(end, session["end"])
        rows.append({"at": at, "end": end, "open": values[0], "close": values[3],
                     "volume": volume, "volume_known": bool(known), "hash": _hash([at, end, *values, bool(known)])})
    return rows


def main_fingerprint(frame, timeframe):
    return _hash([QUANT_VERSION, timeframe, [row["hash"] for row in _rows(frame.tail(WINDOW + 1), timeframe)]])


def saved_context(ticker, timeframe, frame, *, stale=False):
    """Read only: automatic page polling never calculates or fetches intrabars."""
    with closing(get_connection()) as connection:
        row = connection.execute("SELECT main_hash,snapshot_json FROM quantitative_cache "
                                 "WHERE ticker=? AND timeframe=? AND version=?",
                                 (ticker, timeframe, QUANT_VERSION)).fetchone()
    if row is None:
        return {"status": "NOT_CALCULATED", "ticker": ticker, "timeframe": timeframe,
                "direction": "UNAVAILABLE", "direction_score": None, "method_note": METHOD_NOTE,
                "reason": "Für diese Aktie und diesen Timeframe zuerst Quant berechnen."}
    snapshot = json.loads(row["snapshot_json"])
    try:
        changed = row["main_hash"] != main_fingerprint(frame, timeframe)
    except ValueError:
        changed = True
    if stale or changed:
        snapshot.update(status="STALE", reason="Gespeicherter Stand ist veraltet. Per Knopfdruck aktualisieren.")
    return snapshot


def invalidate_saved(ticker, timeframe, reason):
    """A failed explicit refresh stays invalid across later reads and process restarts."""
    with closing(get_connection()) as connection, connection:
        row = connection.execute("SELECT snapshot_json FROM quantitative_cache WHERE ticker=? "
            "AND timeframe=? AND version=?", (ticker, timeframe, QUANT_VERSION)).fetchone()
        if row:
            snapshot = json.loads(row["snapshot_json"])
            snapshot.update(status="STALE", reason=reason)
            connection.execute("UPDATE quantitative_cache SET input_hash=?,snapshot_json=? "
                "WHERE ticker=? AND timeframe=? AND version=?",
                ("invalidated", json.dumps(snapshot, ensure_ascii=False, allow_nan=False),
                 ticker, timeframe, QUANT_VERSION))


def _update_ledger(connection, ticker, timeframe, incoming):
    """Reuse every unchanged signed bar; only append or repair from first correction."""
    key = (ticker, timeframe, LEDGER_VERSION)
    if not incoming:
        return 0
    existing = {row["bar_at"]: row["input_hash"] for row in connection.execute(
        "SELECT bar_at,input_hash FROM quantitative_bars WHERE ticker=? AND timeframe=? AND version=? "
        "AND bar_at>=? AND bar_at<=?", (*key, incoming[0]["at"], incoming[-1]["at"]))}
    changed = [row for row in incoming if existing.get(row["at"]) != row["hash"]]
    if not changed:
        return 0
    for row in changed:
        connection.execute("INSERT INTO quantitative_bars "
            "(ticker,timeframe,version,bar_at,bar_end,input_hash,open,close,volume,volume_known) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(ticker,timeframe,version,bar_at) DO UPDATE SET bar_end=excluded.bar_end,"
            "input_hash=excluded.input_hash,open=excluded.open,close=excluded.close,volume=excluded.volume,volume_known=excluded.volume_known",
            (*key, row["at"], row["end"], row["hash"], row["open"], row["close"], row["volume"], int(row["volume_known"])))
    first = min(row["at"] for row in changed)
    previous = connection.execute("SELECT close,delta_sign,cvd FROM quantitative_bars WHERE ticker=? "
        "AND timeframe=? AND version=? AND bar_at<? ORDER BY bar_at DESC LIMIT 1", (*key, first)).fetchone()
    previous_close = previous["close"] if previous else None
    previous_sign = previous["delta_sign"] if previous else 0
    cvd = previous["cvd"] if previous else 0.0
    updates = []
    for row in connection.execute("SELECT bar_at,open,close,volume FROM quantitative_bars WHERE ticker=? "
                                 "AND timeframe=? AND version=? AND bar_at>=? ORDER BY bar_at", (*key, first)):
        movement = row["close"] - row["open"]
        if movement == 0 and previous_close is not None:
            movement = row["close"] - previous_close
        sign = 1 if movement > 0 else -1 if movement < 0 else previous_sign
        delta = sign * row["volume"]
        cvd += delta
        updates.append((sign, delta, cvd, *key, row["bar_at"]))
        previous_close, previous_sign = row["close"], sign
    connection.executemany("UPDATE quantitative_bars SET delta_sign=?,delta=?,cvd=? "
                          "WHERE ticker=? AND timeframe=? AND version=? AND bar_at=?", updates)
    return len(updates)


def _calculate(frame, timeframe, main_rows, points, benchmark, benchmark_symbol, computed_at, sessions=None):
    return build_snapshot(frame, timeframe, main_rows, points, benchmark, benchmark_symbol, computed_at,
        version=QUANT_VERSION, source_timeframe=SOURCE_TIMEFRAMES[timeframe], method_note=METHOD_NOTE, sessions=sessions)


def calculate_and_store(ticker, timeframe, frame, source, *, revision, benchmark=None, benchmark_symbol=None):
    ensure_selection(revision)
    if not settings()["enabled"]:
        raise ModeChangedError("Quant ist ausgeschaltet.")
    if timeframe not in SOURCE_TIMEFRAMES:
        raise ValueError("Nicht unterstützter Timeframe.")
    # Both inputs are independently cut off at the completed main candle.
    frame = completed_bars(frame, timeframe)
    main_rows = _rows(frame.tail(WINDOW + 1), timeframe)
    if not main_rows:
        raise ValueError("Keine abgeschlossenen Kerzen für Quant vorhanden.")
    cutoff = pd.Timestamp(main_rows[-1]["end"], unit="ms", tz="UTC")
    source_tf = SOURCE_TIMEFRAMES[timeframe]
    incoming = [row for row in _rows(source, source_tf) if row["end"] <= int(cutoff.timestamp() * 1000)]
    if benchmark is not None and benchmark.attrs.get("timeframe") != timeframe:
        benchmark = None
    if benchmark is not None:
        benchmark = completed_bars(benchmark, timeframe, now=cutoff)
    main_hash = main_fingerprint(frame, timeframe)
    sessions = {**frame.attrs.get("trading_sessions", {}), **source.attrs.get("trading_sessions", {})}
    key = (ticker, timeframe, QUANT_VERSION)
    ledger_key = (ticker, timeframe, LEDGER_VERSION)
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        _update_ledger(connection, ticker, timeframe, incoming)
        supplied = {row["at"] for row in incoming}
        points = [dict(row) for row in connection.execute("SELECT * FROM quantitative_bars WHERE ticker=? AND timeframe=? "
            "AND version=? AND bar_at>=? AND bar_end<=? ORDER BY bar_at",
            (*ledger_key, main_rows[-min(WINDOW, len(main_rows))]["at"], main_rows[-1]["end"])) if row["bar_at"] in supplied]
        anchor = connection.execute("SELECT MIN(bar_at) FROM quantitative_bars WHERE ticker=? AND timeframe=? "
                                    "AND version=? AND bar_end<=?", (*ledger_key, main_rows[-1]["end"])).fetchone()[0]
        benchmark_rows = _rows(benchmark.tail(WINDOW + 1), timeframe) if benchmark is not None else []
        input_hash = _hash([ticker, timeframe, main_hash, [(row["bar_at"], row["input_hash"], row["cvd"]) for row in points],
                            benchmark_symbol, [row["hash"] for row in benchmark_rows], anchor, sessions])
        saved = connection.execute("SELECT input_hash,snapshot_json FROM quantitative_cache WHERE ticker=? "
                                   "AND timeframe=? AND version=?", key).fetchone()
        if saved and saved["input_hash"] == input_hash:
            ensure_selection(revision)
            return json.loads(saved["snapshot_json"])
        snapshot = _calculate(frame, timeframe, main_rows, points, benchmark, benchmark_symbol,
                              int(time.time() * 1000), sessions)
        snapshot.update(ticker=ticker, timeframe=timeframe, input_hash=input_hash, cvd_anchor_at=anchor)
        encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        ensure_selection(revision)
        connection.execute("INSERT INTO quantitative_cache VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(ticker,timeframe,version) DO UPDATE SET input_hash=excluded.input_hash,"
            "main_hash=excluded.main_hash,analysis_bar_at=excluded.analysis_bar_at,snapshot_json=excluded.snapshot_json",
            (*key, input_hash, main_hash, snapshot["analysis_bar_at"], encoded))
        connection.execute("INSERT OR IGNORE INTO quantitative_snapshots VALUES(?,?,?,?,?,?)",
                           (input_hash, ticker, timeframe, QUANT_VERSION, snapshot["computed_at"], encoded))
    return snapshot


def compare_groups(snapshot, technical):
    groups = technical.get("indicator_groups") or {}
    direction = snapshot.get("direction")
    directions = [groups.get(name, {}).get("direction") for name in ("trend_structure", "momentum_participation")]
    if snapshot.get("status") != "AVAILABLE" or direction not in ("BULLISH", "BEARISH"):
        return "NO_DIRECTIONAL_CONFIRMATION"
    if all(value == direction for value in directions):
        return "ALL_ALIGNED"
    if any(value in ("BULLISH", "BEARISH") and value != direction for value in directions):
        return "CONFLICT"
    return "PARTIAL"
