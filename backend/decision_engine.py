"""Shared plan validation; Gemini and explicitly selected technical plans have separate entry points."""

import math
from datetime import datetime, timezone

from signal_policy import MAX_ENTRY_DISTANCE_ATR, BUY_DIRECTION_MIN, SELL_DIRECTION_MAX

STRATEGY_VERSION = "4.1-ai"
TECHNICAL_STRATEGY_VERSION = "1.2-technical"
MIN_REWARD_RISK = 1.5
TECHNICAL_MIN_REWARD_RISK = 1.0


def minimum_reward_risk(mode):
    return TECHNICAL_MIN_REWARD_RISK if mode == "python" else MIN_REWARD_RISK


def _neutral(ticker, timeframe, price, as_of, reason, proposal=None, analysis_method=None, validation_status=None):
    breakdown = (proposal or {}).get("decision_breakdown") or {}
    score = float(breakdown.get("weighted_direction_score", (proposal or {}).get("direction_score", 50)))
    confidence = float((proposal or {}).get("confidence", 0))
    extra_reasons = list((proposal or {}).get("reasons") or [])
    contradictions = list((proposal or {}).get("contradictions") or [])
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "signal_type": "NEUTRAL",
        "score": round(max(0, min(100, score)), 1),
        "strength": round(max(0, min(100, confidence)), 1),
        "setup_quality": "C",
        "current_price": price,
        "entry_price": None,
        "entry_zone_min": None,
        "entry_zone_max": None,
        "stop_loss": None,
        "take_profit_1": None,
        "take_profit_2": None,
        "risk_reward_ratio": None,
        "potential_profit_pct": None,
        "risk_pct": None,
        "reasons": [reason] + extra_reasons + [f"Widerspruch: {item}" for item in contradictions],
        "strategy_version": STRATEGY_VERSION,
        "as_of": int(as_of * 1000),
        "decision_source": analysis_method or "unavailable",
        "validation_status": validation_status or ("rejected" if analysis_method in ("gemini", "python") else "unavailable"),
        "group_comparison": (proposal or {}).get("group_comparison"),
        "level_basis": list((proposal or {}).get("level_basis") or []),
        "decision_breakdown": (proposal or {}).get("decision_breakdown"),
        "analysis_error": (proposal or {}).get("analysis_error"),
    }


def _validate_trade_plan(ticker, timeframe, technical, proposal, *, as_of=None, analysis_method=None, allowed_source="gemini"):
    """Accept only geometrically and numerically valid AI plans; otherwise return NEUTRAL."""
    as_of = as_of if as_of is not None else datetime.now(timezone.utc).timestamp()
    # Validate both engines against the observed quote, while indicators remain on closed bars.
    price = float(technical.get("current_price") or technical.get("latest_close") or 0)
    if "error" in technical or price <= 0:
        return _neutral(ticker, timeframe, price, as_of, technical.get("error", "Keine verlässlichen Marktdaten."), proposal, analysis_method)
    if analysis_method != allowed_source or not proposal:
        return _neutral(ticker, timeframe, price, as_of, "Keine gültige Gemini-Entscheidung; aus Sicherheitsgründen kein Trade.", proposal, analysis_method)

    signal = proposal.get("signal_type")
    if signal not in ("BUY", "SELL", "NEUTRAL"):
        return _neutral(ticker, timeframe, price, as_of, "Ungültige KI-Richtung.", proposal, analysis_method)
    if signal == "NEUTRAL":
        return _neutral(ticker, timeframe, price, as_of, "Gemini bewertet das Gesamtbild als ABWARTEN.", proposal, analysis_method, "accepted_neutral")

    single_target = allowed_source == "python" and proposal.get("take_profit_2") is None
    names = ("entry_price", "entry_zone_min", "entry_zone_max", "stop_loss", "take_profit_1")
    if not single_target:
        names += ("take_profit_2",)
    try:
        values = {name: float(proposal[name]) for name in names}
    except (KeyError, TypeError, ValueError):
        return _neutral(ticker, timeframe, price, as_of, "Gemini hat unvollständige Trade-Level geliefert.", proposal, analysis_method)
    if any(not math.isfinite(value) or value <= 0 for value in values.values()):
        return _neutral(ticker, timeframe, price, as_of, "Gemini hat ungültige oder nicht positive Trade-Level geliefert.", proposal, analysis_method)

    entry = values["entry_price"]
    zone_min, zone_max = values["entry_zone_min"], values["entry_zone_max"]
    stop, tp1, tp2 = values["stop_loss"], values["take_profit_1"], values.get("take_profit_2")
    if not zone_min <= entry <= zone_max:
        return _neutral(ticker, timeframe, price, as_of, "Der KI-Entry liegt außerhalb der vorgeschlagenen Entry-Zone.", proposal, analysis_method)

    direction = 1 if signal == "BUY" else -1
    if direction * (entry - stop) <= 0 or direction * (tp1 - entry) <= 0 or (tp2 is not None and direction * (tp2 - tp1) <= 0):
        return _neutral(ticker, timeframe, price, as_of, "Stop oder Ziele liegen auf der falschen Kursseite.", proposal, analysis_method)
    reported_direction_score = float(proposal.get("direction_score", 50))
    breakdown = proposal.get("decision_breakdown")
    # Fixed-weight arithmetic is deterministic. Do not discard valid levels because the LLM mis-added scores.
    # The market judgments, proposed direction and every price level still come exclusively from Gemini.
    direction_score = float(breakdown["weighted_direction_score"]) if breakdown else reported_direction_score
    if not math.isfinite(direction_score):
        return _neutral(ticker, timeframe, price, as_of, "Ungültiger KI-Richtungsscore.", proposal, analysis_method)
    if allowed_source == "gemini" and SELL_DIRECTION_MAX < direction_score < BUY_DIRECTION_MIN:
        return _neutral(ticker, timeframe, price, as_of,
                        "Gewichteter Richtungsscore liegt in der Neutralzone 45–55; kein neuer Trade.",
                        proposal, analysis_method)
    if (signal == "BUY" and direction_score < 50) or (signal == "SELL" and direction_score > 50):
        return _neutral(ticker, timeframe, price, as_of, "KI-Richtung und Richtungsscore widersprechen sich.", proposal, analysis_method)
    confidence = float(proposal.get("confidence", 0))
    if not math.isfinite(confidence) or confidence < 55:
        return _neutral(ticker, timeframe, price, as_of, "KI-Konfidenz unter 55/100; kein Trade.", proposal, analysis_method)

    risk = abs(entry - stop)
    reward = abs(tp1 - entry)
    reward_risk = reward / risk if risk else 0
    min_rr = minimum_reward_risk(allowed_source)
    if reward_risk < min_rr:
        return _neutral(ticker, timeframe, price, as_of,
                        f"KI-Setup unterschreitet das Mindestverhältnis von {min_rr:g}:1.", proposal, analysis_method)
    atr = float(technical.get("atr") or 0)
    if atr <= 0 or not math.isfinite(atr):
        return _neutral(ticker, timeframe, price, as_of, "Keine belastbare ATR zur Plausibilitätsprüfung.", proposal, analysis_method)
    mode = proposal.get("entry_mode", "LIMIT")
    if proposal.get("requires_entry_confirmation"):
        mode = "CONFIRMATION"
    if mode not in ("LIMIT", "STOP", "CONFIRMATION"):
        return _neutral(ticker, timeframe, price, as_of, "Ungültige Entry-Bedingung.", proposal, analysis_method)
    # A future-entry condition must not turn a distant historical level into a nearby opportunity.
    distance_atr = abs(entry - price) / atr
    if distance_atr > MAX_ENTRY_DISTANCE_ATR:
        return _neutral(ticker, timeframe, price, as_of,
                        f"Entry-Abstand {distance_atr:.2f} ATR überschreitet die Grenze von {MAX_ENTRY_DISTANCE_ATR:g} ATR; auf eine nähere Einstiegszone warten.",
                        proposal, analysis_method)
    if risk > 5 * atr or abs(zone_max - zone_min) > 3 * atr:
        return _neutral(ticker, timeframe, price, as_of, "KI-Level liegen außerhalb des plausiblen Volatilitätsbereichs.", proposal, analysis_method)

    invalidation = proposal.get("invalidation_price")
    if mode == "STOP" and direction * (price - stop) <= 0 and invalidation is None:
        return _neutral(ticker, timeframe, price, as_of, "Vorbereiteter Ausbruch benötigt eine eigene technische Invalidation vor dem Entry.", proposal, analysis_method)
    if invalidation is not None and (not math.isfinite(float(invalidation)) or float(invalidation) <= 0
                                     or direction * (entry - float(invalidation)) <= 0):
        return _neutral(ticker, timeframe, price, as_of, "Ungültige technische Invalidation.", proposal, analysis_method)

    reasons = list(proposal.get("reasons") or [])
    contradictions = list(proposal.get("contradictions") or [])
    if contradictions:
        reasons.extend(f"Berücksichtigter Widerspruch: {item}" for item in contradictions)
    level_basis = list(proposal.get("level_basis") or [])
    if level_basis:
        reasons.append("Level-Grundlage: " + "; ".join(level_basis))
    quality = proposal.get("setup_quality") if proposal.get("setup_quality") in ("A+", "A", "B", "C") else "C"
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "signal_type": signal,
        "score": round(max(0, min(100, direction_score)), 1),
        "strength": round(max(0, min(100, confidence)), 1),
        "setup_quality": quality,
        "current_price": price,
        "entry_price": round(entry, 8),
        "entry_zone_min": round(zone_min, 8),
        "entry_zone_max": round(zone_max, 8),
        "stop_loss": round(stop, 8),
        "take_profit_1": round(tp1, 8),
        "take_profit_2": round(tp2, 8) if tp2 is not None else None,
        "risk_reward_ratio": round(reward_risk, 4),
        "potential_profit_pct": round(reward / entry * 100, 4),
        "risk_pct": round(risk / entry * 100, 4),
        "reasons": reasons or ["Gemini hat ein gültiges Setup geliefert."],
        "strategy_version": STRATEGY_VERSION,
        "as_of": int(as_of * 1000),
        "decision_source": analysis_method,
        "validation_status": "accepted",
        "group_comparison": proposal.get("group_comparison"),
        "level_basis": level_basis,
        "entry_mode": mode,
        "entry_condition": proposal.get("entry_condition") or "Einstieg an der von Gemini bestimmten Entry-Zone.",
        "invalidation_price": float(invalidation) if invalidation is not None else stop,
        "invalidation_condition": proposal.get("invalidation_condition") or "Technische Invalidation am ursprünglichen Stop.",
        "valid_for_bars": max(1, min(100, int(proposal.get("valid_for_bars") or 12))),
        "decision_breakdown": breakdown,
        "reported_direction_score": reported_direction_score,
        "score_weight_adjusted": abs(direction_score - reported_direction_score) > 0.01,
        "requires_entry_confirmation": mode == "CONFIRMATION",
        **{key: proposal[key] for key in ("entry_kind", "selection_policy", "fvg_quality") if key in proposal},
    }


def validate_ai_trade_decision(ticker, timeframe, technical, proposal, *, as_of=None, analysis_method=None):
    return _validate_trade_plan(ticker, timeframe, technical, proposal, as_of=as_of, analysis_method=analysis_method)


def validate_technical_trade_decision(ticker, timeframe, technical, proposal, *, as_of=None):
    result = _validate_trade_plan(ticker, timeframe, technical, proposal, as_of=as_of,
                                  analysis_method="python", allowed_source="python")
    result["strategy_version"] = TECHNICAL_STRATEGY_VERSION
    result["reasons"] = [reason.replace("Gemini", "Python").replace("KI-", "Technischer ") for reason in result["reasons"]]
    return result


def calculate_trade_decision(ticker, timeframe, technical, news_items=None, *, as_of=None, ai_proposal=None, analysis_method=None):
    """Compatibility wrapper. It never creates a trade without a Gemini proposal."""
    return validate_ai_trade_decision(
        ticker, timeframe, technical, ai_proposal, as_of=as_of, analysis_method=analysis_method
    )
