"""Point-in-time capture of real Gemini+Quant decisions and signal activations.

Never relabel legacy AI responses as current-code history. The archive records
what was observed when, not what a model today thinks it knew in the past.
"""
from contextlib import closing
from functools import lru_cache
import hashlib
import json
import logging
import os
from pathlib import Path
import time
import sqlite3

from database import get_connection
from signal_policy import decision_policy
from analysis_scope import current_scope

PROFILE = "gemini_quant"
logger = logging.getLogger(__name__)
FILES = ("main.py", "technical_analysis.py", "technical_strategy.py", "decision_engine.py",
         "market_data.py", "signal_policy.py", "signal_manager.py", "news_engine.py",
         "quantitative_analysis.py", "quantitative_metrics.py", "options_context.py", "option_greeks.py",
         "backtest_archive.py", "gemini_backtest.py", "backtest_service.py", "analysis_scope.py",
         "analysis_modes.py", "background_settings.py", "background_service.py", "background_prices.py",
         "lightgbm_features.py", "lightgbm_service.py")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


@lru_cache(maxsize=16)
def _hashes(signatures):
    return {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() for name, _, _ in signatures}


def fingerprint():
    root = Path(__file__).parent
    signatures = tuple((name, (root / name).stat().st_mtime_ns, (root / name).stat().st_size) for name in FILES)
    return _hashes(signatures)


def strategy_key(timeframe):
    return hashlib.sha256(_json({"code": fingerprint(), "policy": decision_policy(timeframe),
        "model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"), "profile": PROFILE}).encode()).hexdigest()


def record_context(ticker, timeframe, technical, news_context, decision, market, previous_signal):
    try:
        return _record_context(ticker, timeframe, technical, news_context, decision, market, previous_signal)
    except (sqlite3.Error, ValueError, TypeError, OSError) as exc:
        logger.warning("Backtest archive capture unavailable (%s)", type(exc).__name__)
        return None


def _record_context(ticker, timeframe, technical, news_context, decision, market, previous_signal):
    local_model = (technical or {}).get("lightgbm") or {}
    if (news_context.get("analysis_method") != "gemini" or market.get("is_stale")
            or local_model.get("engine") != "lightgbm"
            or decision.get("validation_status") not in ("accepted", "accepted_neutral")):
        return None
    context = news_context.get("pipeline", {}).get("context_id")
    if not context:
        return None
    key = strategy_key(timeframe)
    stream = "background" if current_scope() is not None else "interactive"
    identity = hashlib.sha256(_json([key, ticker, timeframe, context, stream]).encode()).hexdigest()
    with closing(get_connection()) as connection, connection:
        existing = connection.execute("SELECT id FROM backtest_context_archive WHERE id=?", (identity,)).fetchone()
        # An old cache hit has no proof of the code that originally produced it.
        if not existing and news_context.get("pipeline", {}).get("cache_hit"):
            return None
        if not existing:
            snapshot = {"technical": technical, "news_context": news_context, "validated_decision": decision,
                        "market": market, "previous_signal": previous_signal, "code_fingerprint": fingerprint()}
            connection.execute("INSERT INTO backtest_context_archive(id,ticker,timeframe,strategy_key,context_id,available_at,snapshot_json,stream) VALUES(?,?,?,?,?,?,?,?)",
                               (identity, ticker, timeframe, key, context, int(time.time() * 1000), _json(snapshot),stream))
    return {"id": identity, "strategy_key": key, "stream": stream}


def record_event(connection, state, event_key, event_type, now):
    origin = state.get("backtest_origin")
    if not origin or origin.get("strategy_key") != strategy_key(state["timeframe"]):
        return
    context = connection.execute("SELECT available_at FROM backtest_context_archive WHERE id=? AND strategy_key=?",
                                 (origin["id"], origin["strategy_key"])).fetchone()
    if not context or now < context["available_at"]:
        return
    identity = hashlib.sha256(_json([origin["id"], state.get("setup_id"), event_key]).encode()).hexdigest()
    connection.execute("INSERT OR IGNORE INTO backtest_event_archive(id,context_id,ticker,timeframe,strategy_key,setup_id,event_type,available_at,state_json,stream) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (identity, origin["id"], state["ticker"], state["timeframe"], origin["strategy_key"],
                        state.get("setup_id"), event_type, int(now), _json(state),origin.get("stream","interactive")))


def archive_status(ticker, timeframe):
    key = strategy_key(timeframe)
    with closing(get_connection()) as connection:
        has_background = connection.execute("SELECT 1 FROM backtest_context_archive WHERE ticker=? AND timeframe=? AND strategy_key=? AND stream='background' LIMIT 1",(ticker,timeframe,key)).fetchone()
        stream = "background" if has_background else "interactive"
        count = connection.execute("SELECT COUNT(*) FROM backtest_context_archive WHERE ticker=? AND timeframe=? AND strategy_key=? AND stream=?",
                                   (ticker, timeframe, key,stream)).fetchone()[0]
        events = connection.execute("SELECT COUNT(DISTINCT setup_id) FROM backtest_event_archive WHERE ticker=? AND timeframe=? AND strategy_key=? AND stream=? AND (event_type='ACTIVATED' OR json_extract(state_json,'$.status')='AKTIV')",
                                    (ticker, timeframe, key,stream)).fetchone()[0]
    return {"strategy_key": key, "contexts": count, "activated_setups": events, "stream": stream}


def activations(ticker, timeframe, *, stream=None):
    key = strategy_key(timeframe)
    stream = stream or archive_status(ticker,timeframe)["stream"]
    with closing(get_connection()) as connection:
        rows = connection.execute("SELECT setup_id,available_at,state_json FROM backtest_event_archive WHERE ticker=? AND timeframe=? AND strategy_key=? AND stream=? AND (event_type='ACTIVATED' OR json_extract(state_json,'$.status')='AKTIV') ORDER BY available_at,id",
                                  (ticker, timeframe, key,stream)).fetchall()
    unique = {}
    for row in rows:
        unique.setdefault(row["setup_id"], {"setup_id": row["setup_id"], "available_at": row["available_at"],
                                            "state": json.loads(row["state_json"])})
    return list(unique.values())
