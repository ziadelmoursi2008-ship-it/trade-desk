"""Persistent signal lifecycle. Direction, levels and market judgments come from Gemini.

Market observations only monitor an existing plan. No broker orders are submitted.
All times are epoch milliseconds; a bar identity is its completed-candle end time.
"""

import copy
import hashlib
import json
import logging
import math
import sqlite3
import time
import uuid
from contextlib import closing

from database import get_connection
from analysis_scope import current_scope
from signal_policy import MAX_ENTRY_DISTANCE_ATR, decision_policy

LIVE = ("VORBEREITET", "AKTIV")


def _tables(mode):
    if mode not in ("gemini", "python"):
        raise ValueError("Unbekannter Analysemodus.")
    if current_scope() is not None and mode == "gemini":
        return ("background_signal_states", "background_signal_events")
    return ("technical_signal_states", "technical_signal_events") if mode == "python" else ("signal_states", "signal_events")


def _read(connection, ticker, timeframe, mode="gemini"):
    states, _ = _tables(mode)
    row = connection.execute(f"SELECT state_json FROM {states} WHERE ticker=? AND timeframe=?", (ticker, timeframe)).fetchone()
    return json.loads(row[0]) if row else None


def get_signal_state(ticker, timeframe, mode="gemini"):
    with closing(get_connection()) as connection:
        return _read(connection, ticker, timeframe, mode)


def previous_signal_context(ticker, timeframe, mode="gemini"):
    state = get_signal_state(ticker, timeframe, mode)
    if not state or not state.get("setup_id"):
        return None
    fields = ("setup_id", "direction", "status", "signal_label", "confidence", "created_at", "created_bar_at",
              "last_verified_at", "signal_strength", "opposing_bars", "low_confidence_bars", "elapsed_bars", "reason", "tp1_reached")
    return {**{key: state.get(key) for key in fields}, "original_plan": state["plan"],
            "macro_baseline": state.get("hard_observations", {})}


def _strength(direction, confidence):
    prefix = "STRONG_" if confidence >= 80 else "" if confidence >= 65 else "WEAK_"
    return prefix + direction


def _public(state):
    live = state["status"] in LIVE
    plan = state["plan"]
    reasons = [state["reason"]] + list(plan.get("reasons") or [])
    return {
        **plan,
        "signal_type": state["direction"] if live else "NEUTRAL",
        "signal_label": state["signal_label"] if live else "NEUTRAL",
        "direction": state.get("direction"), "lifecycle_status": state["status"],
        "setup_id": state.get("setup_id"), "strength": state.get("signal_strength", state["confidence"]),
        "confidence": state["confidence"],
        "hold_previous": live and state["confidence"] < 55,
        "strength_status": "HOLD_PRE" if live and state["confidence"] < 55 else state["signal_label"],
        "created_at": state["created_at"], "activated_at": state.get("activated_at"),
        "ended_at": state.get("ended_at"), "updated_at": state["updated_at"],
        "last_verified_at": state.get("last_verified_at"), "verification_status": state.get("verification_status", "unavailable"),
        "opposing_bars": state["opposing_bars"], "low_confidence_bars": state["low_confidence_bars"],
        "elapsed_bars": state["elapsed_bars"], "valid_for_bars": state.get("valid_for_bars", 12),
        "tp1_reached": state.get("tp1_reached", False), "tp2_reached": state.get("tp2_reached", False),
        "latest_ai_proposal": state.get("latest_ai_proposal"), "state_reason": state["reason"],
        "current_price": state.get("current_price", plan["current_price"]),
        "reasons": list(dict.fromkeys(reasons)),
        "analysis_error": state.get("analysis_error"),
        "analysis_mode": state.get("analysis_mode", "gemini"),
    }


def _write(connection, state, event_key, event_type, now, bar_at=None):
    state["updated_at"] = now
    encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
    states, events = _tables(state.get("analysis_mode", "gemini"))
    connection.execute(f"INSERT INTO {states}(ticker,timeframe,state_json,updated_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(ticker,timeframe) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
                       (state["ticker"], state["timeframe"], encoded, now))
    if event_key:
        connection.execute(f"INSERT OR IGNORE INTO {events}(ticker,timeframe,setup_id,event_key,event_type,analysis_bar_at,created_at,state_json) "
                           "VALUES(?,?,?,?,?,?,?,?)", (state["ticker"], state["timeframe"], state.get("setup_id"),
                           event_key, event_type, bar_at, now, encoded))
        if state.get("backtest_origin"):
            from backtest_archive import record_event
            try:
                record_event(connection, state, event_key, event_type, now)
            except (sqlite3.Error, ValueError, TypeError, OSError) as exc:
                logging.getLogger(__name__).warning("Backtest activation capture unavailable (%s)", type(exc).__name__)


def _end(state, reason, now, bar_at, *, expired=False):
    state.update(status="ABGELAUFEN" if expired else "BEENDET", reason=reason, ended_at=now,
                 ended_bar_at=bar_at, signal_label="NEUTRAL")


def _expire_distant_entry(state, technical, price, now, bar_at):
    """Review only untriggered plans; never move or cancel an active trade for entry distance."""
    if state["status"] != "VORBEREITET" or (technical or {}).get("error"):
        return False
    try:
        entry, price, atr = float(state["plan"]["entry_price"]), float(price), float((technical or {}).get("atr") or state.get("entry_atr") or 0)
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(value) and value > 0 for value in (entry, price, atr)):
        return False
    distance_atr = abs(entry - price) / atr
    if distance_atr <= MAX_ENTRY_DISTANCE_ATR:
        return False
    state["current_price"] = price
    _end(state, f"Vorbereiteter Entry ist mit {distance_atr:.2f} ATR zu weit vom aktuellen Kurs entfernt (Grenze {MAX_ENTRY_DISTANCE_ATR:g} ATR); auf eine nähere Einstiegszone warten.",
         now, bar_at, expired=True)
    return True


def _remember_entry_atr(state, technical):
    try:
        atr = float((technical or {}).get("atr") or 0)
    except (TypeError, ValueError):
        return
    if math.isfinite(atr) and atr > 0:
        state["entry_atr"] = atr


def _expire_lifetime(state, completed_bar_times, now, bar_at):
    if state["status"] not in LIVE:
        return False
    state["elapsed_bars"] = max(state["elapsed_bars"], sum(end > state["created_bar_at"] for end in completed_bar_times))
    if state["elapsed_bars"] < state.get("valid_for_bars", 12):
        return False
    _end(state, "Festgelegte Signal-Lebensdauer abgelaufen.", now, bar_at, expired=True)
    return True


def _macro_observations(bundle):
    return {row["series_id"]: {"period": row.get("period"), "value": row.get("value")}
            for row in (bundle or {}).get("items", []) if not row.get("stale")}


def _monitor(state, bars, now, bar_at, review=None):
    """Monitor known prices only. Historical ranges before plan creation cannot trigger it."""
    if state["status"] not in LIVE:
        return []
    events = []
    plan = state["plan"]
    buy = state["direction"] == "BUY"
    for bar in sorted(bars, key=lambda row: row["timestamp"]):
        if bar["timestamp"] < state.get("last_market_bar_at", 0):
            continue
        if bar.get("observed_at", now) < state["created_at"]:
            continue
        state["current_price"] = bar["close"]
        # Only a candle entirely after creation/activation has an unambiguous usable range.
        use_range = bar["timestamp"] >= state["created_at"]
        low = bar["low"] if use_range else bar["close"]
        high = bar["high"] if use_range else bar["close"]
        state["last_market_bar_at"] = bar["timestamp"]
        hit = lambda value: low <= value if buy else high >= value
        invalidation = plan.get("invalidation_price", plan["stop_loss"])
        if (state["status"] == "AKTIV" and hit(plan["stop_loss"])) or hit(invalidation):
            reason = "Stop-Loss erreicht." if state["status"] == "AKTIV" else "Vorbereitete Einstiegsidee vor Aktivierung invalidiert."
            if use_range and low <= plan["entry_price"] <= high and state["status"] == "VORBEREITET":
                reason += " Entry/Stop-Reihenfolge in dieser Kerze unbekannt; konservativ beendet."
            _end(state, reason, now, bar_at)
            events.append(("STOP_OR_INVALIDATION", copy.deepcopy(state)))
            break
        if state["status"] == "VORBEREITET":
            touched = (low <= plan["entry_price"] <= high) if use_range else plan["entry_zone_min"] <= bar["close"] <= plan["entry_zone_max"]
            confirmation = (review or {}).get("entry_confirmed", False) and (review or {}).get("entry_evidence_bar_at") == bar_at
            if touched and (plan.get("entry_mode") != "CONFIRMATION" or confirmation):
                state.update(status="AKTIV", activated_at=now if not use_range else bar.get("end_timestamp", now),
                             reason="Geplante Einstiegsbedingung erreicht; Setup aktiv (keine Broker-Order).")
                events.append(("ACTIVATED", copy.deepcopy(state)))
            continue  # Never infer profit before entry within the same OHLC candle.
        target_range = bar["timestamp"] >= state.get("activated_at", now)
        target_high = bar["high"] if target_range else bar["close"]
        target_low = bar["low"] if target_range else bar["close"]
        target_hit = lambda value: target_high >= value if buy else target_low <= value
        if target_hit(plan["take_profit_1"]) and not state.get("tp1_reached"):
            state["tp1_reached"] = True
            if plan.get("take_profit_2") is None:
                _end(state, "Einziges Gewinnziel erreicht; Setup abgeschlossen.", now, bar_at)
                events.append(("TARGET_REACHED", copy.deepcopy(state)))
                break
            state["reason"] = "TP1 erreicht; ursprünglicher Stop und TP2 bleiben bestehen."
            events.append(("TP1_REACHED", copy.deepcopy(state)))
        if plan.get("take_profit_2") is not None and target_hit(plan["take_profit_2"]):
            state.update(tp1_reached=True, tp2_reached=True)
            _end(state, "TP2 erreicht; Setup abgeschlossen.", now, bar_at)
            events.append(("TP2_REACHED", copy.deepcopy(state)))
            break
    return events


def signal_history(ticker, timeframe, limit=30, mode="gemini"):
    with closing(get_connection()) as connection:
        _, events = _tables(mode)
        rows = connection.execute(f"SELECT id,event_type,created_at,analysis_bar_at,state_json FROM {events} "
                                  "WHERE ticker=? AND timeframe=? ORDER BY id DESC LIMIT ?", (ticker, timeframe, limit)).fetchall()
        return [{"id": row["id"], "event_type": row["event_type"], "created_at": row["created_at"],
                 "analysis_bar_at": row["analysis_bar_at"], "decision": _public(json.loads(row["state_json"]))} for row in rows]


def manage_signal(ticker, timeframe, decision, proposal, *, analysis_method, context_id, bar_at,
                  completed_bar_times, market_bars, hard_bundle=None, review=None, now=None, market_stale=False, mode="gemini", technical=None, backtest_origin=None):
    now = int(now if now is not None else time.time() * 1000)
    policy = decision_policy()
    context_id = context_id or hashlib.sha256(json.dumps(proposal or {}, sort_keys=True).encode()).hexdigest()
    observations = _macro_observations(hard_bundle)
    verified = analysis_method == mode and decision.get("validation_status") in ("accepted", "accepted_neutral")
    replace_pending = False
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        state = _read(connection, ticker, timeframe, mode)
        if state:
            if not market_stale:
                _remember_entry_atr(state, technical)
            matching_review = review if review and review.get("previous_setup_id") == state.get("setup_id") else None
            market_events = [] if market_stale else _monitor(state, market_bars, now, bar_at, matching_review)
            for event, snapshot in market_events:
                _write(connection, snapshot, f"{state['setup_id']}:market:{event}:{snapshot.get('last_market_bar_at')}", event, now, bar_at)
            # Run before context-cache returns so old persisted plans also receive the new limit.
            current_price = state.get("current_price") if market_bars else (technical or {}).get("current_price", (technical or {}).get("latest_close"))
            was_pending = state["status"] == "VORBEREITET"
            if (not market_stale and bar_at >= state.get("last_evaluated_bar_at", 0)
                    and _expire_distant_entry(state, technical, current_price, now, bar_at)):
                _write(connection, state, f"{state['setup_id']}:entry-too-distant", "ENTRY_TOO_DISTANT", now, bar_at)
            # Even a cached Gemini response must not keep an expired plan alive.
            if not market_stale and _expire_lifetime(state, completed_bar_times, now, bar_at):
                _write(connection, state, f"{state['setup_id']}:expired", "EXPIRED", now, bar_at)
            # Untriggered Python ideas follow the latest selection. Active plans remain frozen.
            if (mode == "python" and was_pending and verified and not market_stale and technical
                    and not technical.get("error") and float(technical.get("atr") or 0) > 0
                    and bar_at >= state.get("last_evaluated_bar_at", 0)):
                fields = ("signal_type", "entry_price", "stop_loss", "take_profit_1", "take_profit_2", "entry_mode", "strategy_version")
                changed = any(state["plan"].get(key) != decision.get(key) for key in fields)
                if decision["signal_type"] in ("BUY", "SELL") and changed:
                    if state["status"] == "VORBEREITET":
                        _end(state, "Vorbereiteter Python-Plan durch den neu ausgewählten nahen Einstieg ersetzt.", now, bar_at)
                        _write(connection, state, f"{state['setup_id']}:replaced:{context_id}", "PENDING_PLAN_REPLACED", now, bar_at)
                    replace_pending = True
                elif decision["signal_type"] == "NEUTRAL" and state["status"] == "VORBEREITET":
                    _end(state, "Aktuelle technische Prüfung findet keinen gültigen nahen Einstieg: " + decision["reasons"][0], now, bar_at)
                    _write(connection, state, f"{state['setup_id']}:withdrawn:{context_id}", "PENDING_PLAN_WITHDRAWN", now, bar_at)
        else:
            matching_review = None
        if state and state.get("setup_id") is None:
            state.update(plan=copy.deepcopy(decision), confidence=decision.get("strength", 0), reason=decision["reasons"][0])
        if state and state.get("last_context_id") == context_id and not replace_pending:
            if market_stale:
                state["verification_status"] = "stale_market"
            _write(connection, state, None, "", now, bar_at)
            return _public(state)
        if state and bar_at < state.get("last_evaluated_bar_at", 0):
            return _public(state)

        if not state or (state["status"] not in LIVE and (replace_pending or bar_at > state.get("ended_bar_at", -1))):
            if verified and decision["signal_type"] in ("BUY", "SELL") and not market_stale:
                state = {
                    "ticker": ticker, "timeframe": timeframe, "setup_id": uuid.uuid4().hex, "analysis_mode": mode,
                    "direction": decision["signal_type"], "plan": copy.deepcopy(decision), "status": "VORBEREITET",
                    "created_at": now, "created_bar_at": bar_at, "updated_at": now, "last_verified_at": now,
                    "confidence": decision["strength"], "signal_label": _strength(decision["signal_type"], decision["strength"]),
                    "opposing_bars": 0, "low_confidence_bars": 0, "elapsed_bars": 0,
                    "valid_for_bars": decision.get("valid_for_bars", 12), "last_counted_bar_at": bar_at,
                    "reason": ("Python" if mode == "python" else "Gemini") + "-Setup vorbereitet; Einstieg laut Handelsplan.",
                    "hard_observations": observations, "tp1_reached": False, "tp2_reached": False,
                    "backtest_origin": backtest_origin,
                }
                _remember_entry_atr(state, technical)
                # Only the latest observed price may activate a newly created idea, never an earlier high/low.
                if market_bars:
                    _monitor(state, [market_bars[-1]], now, bar_at)
                event_type = "CREATED"
            elif not state:
                state = {"ticker": ticker, "timeframe": timeframe, "setup_id": None, "direction": None,
                         "plan": copy.deepcopy(decision), "status": "NEUTRAL", "created_at": now, "updated_at": now, "analysis_mode": mode,
                         "created_bar_at": bar_at, "confidence": decision.get("strength", 0), "signal_label": "NEUTRAL",
                         "opposing_bars": 0, "low_confidence_bars": 0, "elapsed_bars": 0,
                         "reason": decision["reasons"][0], "hard_observations": observations}
                event_type = "NO_SETUP"
            else:
                if state.get("setup_id") is None:
                    state.update(plan=copy.deepcopy(decision), confidence=decision.get("strength", 0), reason=decision["reasons"][0])
                event_type = "OBSERVED"
        else:
            event_type = "OBSERVED"
            if state["status"] in LIVE and verified and not market_stale:
                old_label = state["signal_label"]
                assessment = (matching_review or {}).get("assessment")
                reason = (matching_review or {}).get("reason") or ""
                review_confidence = (matching_review or {}).get("confidence_in_previous")
                same_direction = decision["signal_type"] == state["direction"]
                against = assessment == "AGAINST" or decision["signal_type"] in ("BUY", "SELL") and not same_direction
                confidence = (float(review_confidence) if review_confidence is not None else
                              decision["strength"] if same_direction else min(state["confidence"], 54))
                weakening = assessment in ("WEAKENING", "AGAINST") or (not same_direction and assessment != "CONFIRMED")
                state["confidence"] = max(0, min(100, confidence))
                state["signal_strength"] = min(state["confidence"], 64) if weakening else state["confidence"]
                state["signal_label"] = _strength(state["direction"], state["signal_strength"])
                state["reason"] = reason or ("Vorheriges Setup abgeschwächt; ursprüngliche Levels bleiben erhalten." if weakening
                                            else "Bestehendes Setup durch Gemini bestätigt; ursprüngliche Levels bleiben erhalten.")
                if bar_at > state.get("last_counted_bar_at", 0):
                    preceding = max((end for end in completed_bar_times if end < bar_at), default=None)
                    if preceding != state.get("last_counted_bar_at"):
                        state["opposing_bars"] = state["low_confidence_bars"] = 0
                    state["opposing_bars"] = state["opposing_bars"] + 1 if against else 0
                    state["low_confidence_bars"] = state["low_confidence_bars"] + 1 if confidence < policy["massive_confidence_threshold"] else 0
                    state["last_counted_bar_at"] = bar_at
                old_observations = state.get("hard_observations", {})
                changed_events = {f"{series}:{row['period']}" for series, row in observations.items()
                                  if series in old_observations and row != old_observations[series]}
                technical_break = assessment == "INVALIDATED" and bool((matching_review or {}).get("technical_break")) and bool(reason)
                strong_opposite = against and decision["signal_type"] in ("BUY", "SELL") and not same_direction and decision["strength"] >= 80
                macro_break = (bool((matching_review or {}).get("macro_opposition"))
                               and (matching_review or {}).get("macro_opposition_strength", 0) >= 80
                               and (matching_review or {}).get("macro_event_id") in changed_events and bool(reason))
                if technical_break or strong_opposite or macro_break:
                    prefix = "Technischer Trendbruch" if technical_break else "Starkes Gemini-Gegensignal" if strong_opposite else "Neue harte Daten mit deutlicher Gegenwirkung"
                    _end(state, prefix + (": " + reason if reason else "."), now, bar_at)
                    event_type = "INVALIDATED"
                elif state["opposing_bars"] >= policy["opposing_bars_to_end"]:
                    _end(state, "Zwei aufeinanderfolgende abgeschlossene Analysekerzen gegen das Setup.", now, bar_at)
                    event_type = "OPPOSING_CONFIRMATIONS"
                elif state["low_confidence_bars"] >= policy["low_confidence_bars_to_end"]:
                    _end(state, "Gemini-Konfidenz für das bisherige Setup über zwei abgeschlossene Kerzen unter 45.", now, bar_at)
                    event_type = "CONFIDENCE_COLLAPSE"
                elif old_label != state["signal_label"]:
                    event_type = "STRENGTH_CHANGED"

        state["last_context_id"] = context_id
        state["last_evaluated_bar_at"] = bar_at
        state["analysis_mode"] = mode
        state["verification_status"] = "stale_market" if market_stale else "verified" if verified else "unavailable" if analysis_method != mode else "rejected_proposal"
        state["analysis_error"] = decision.get("analysis_error")
        if verified and not market_stale:
            state["last_verified_at"] = now
            state["hard_observations"] = observations
        state["latest_ai_proposal"] = {"signal_type": (proposal or {}).get("signal_type", "NEUTRAL"),
                                       "confidence": (proposal or {}).get("confidence", 0),
                                       "validation_status": decision.get("validation_status"),
                                       "reason": decision["reasons"][0], "analyzed_at": now, "bar_at": bar_at}
        if not verified and state["status"] in LIVE:
            state["reason"] = "Letztes Setup bleibt gespeichert; aktuelle KI-Bewertung nicht verwertbar. " + decision["reasons"][0]
        _write(connection, state, f"analysis:{context_id}", event_type, now, bar_at)
        return _public(state)


def monitor_signal_market(ticker, timeframe, bars, *, stale=False, now=None, mode="gemini", technical=None, completed_bar_times=None):
    """Called by quote polling; observes an existing plan without calling Gemini."""
    now = int(now if now is not None else time.time() * 1000)
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        state = _read(connection, ticker, timeframe, mode)
        if state is None:
            return None
        if not stale:
            bar_at = max(state.get("last_evaluated_bar_at", 0), max(completed_bar_times or [0]))
            _remember_entry_atr(state, technical)
            events = _monitor(state, bars, now, bar_at)
            for event, snapshot in events:
                _write(connection, snapshot, f"{state['setup_id']}:market:{event}:{snapshot.get('last_market_bar_at')}", event, now)
            if bars and _expire_distant_entry(state, technical, state.get("current_price"), now, bar_at):
                _write(connection, state, f"{state['setup_id']}:entry-too-distant", "ENTRY_TOO_DISTANT", now, bar_at)
            if completed_bar_times and _expire_lifetime(state, completed_bar_times, now, bar_at):
                _write(connection, state, f"{state['setup_id']}:expired", "EXPIRED", now, bar_at)
            _write(connection, state, None, "", now)
        return _public(state)


def retire_pending_python_plan(ticker, timeframe, *, now=None):
    """Retire old waiting entries when switching to the current-price outlook engine."""
    now = int(now if now is not None else time.time() * 1000)
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        state = _read(connection,ticker,timeframe,"python")
        if state and state["status"] == "VORBEREITET":
            bar_at = state.get("last_evaluated_bar_at",state.get("created_bar_at",0))
            _end(state,"Wartender Python-Entry beendet: Der Python-Modus zeigt jetzt einen technischen Ausblick ab aktuellem Kurs.",now,bar_at)
            _write(connection,state,f"{state['setup_id']}:retired-for-projection","RETIRED_FOR_CURRENT_PROJECTION",now,bar_at)
