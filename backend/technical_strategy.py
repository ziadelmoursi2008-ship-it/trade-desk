"""Legacy 1.2 entry planner and shared zone helpers.

The live Python analysis uses technical_forecast.py for current-price outlooks.
"""

import math
from decision_engine import TECHNICAL_MIN_REWARD_RISK, TECHNICAL_STRATEGY_VERSION, validate_technical_trade_decision
from signal_policy import MAX_ENTRY_DISTANCE_ATR

STRATEGY_VERSION = TECHNICAL_STRATEGY_VERSION
FVG_NEARBY_ATR = 0.25
FVG_SIMILAR_RATIO = 0.9


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def group_context(technical):
    groups = technical.get("indicator_groups") or {}
    trend, momentum = groups.get("trend_structure", {}), groups.get("momentum_participation", {})
    aligned = trend.get("direction") == momentum.get("direction") and trend.get("direction") in ("BULLISH", "BEARISH")
    enough = min(trend.get("available_components", 0), momentum.get("available_components", 0)) >= 3
    mean = (float(trend.get("score", 0)) + float(momentum.get("score", 0))) / 2
    score = max(0, min(100, 50 + mean / 2))
    agreement_strength = min(abs(float(trend.get("score", 0))), abs(float(momentum.get("score", 0))))
    strength = min(95, 55 + 0.35 * agreement_strength)
    if not technical.get("warmup_complete"):
        strength = max(55, strength - 5)
    return (1 if mean > 0 else -1) if aligned and enough else 0, round(score, 1), round(strength, 1)


def reference_zones(technical):
    atr = number(technical.get("atr")) or 0
    zones = []

    def point(value, label, side, priority=3):
        value = number(value)
        if value is not None:
            zones.append({"lower": value - 0.1 * atr, "upper": value + 0.1 * atr,
                          "price": value, "label": label, "side": side, "priority": priority, "structural": True})

    for name, side in (("support", 1), ("local_support", 1), ("resistance", -1), ("local_resistance", -1)):
        point(technical.get(name), name, side)
    structure = technical.get("market_structure") or {}
    for kind, side in (("lows", 1), ("highs", -1)):
        for row in (structure.get("swings") or {}).get(kind, []):
            point(row.get("price"), "Bestätigter Swing " + kind, side, 4)
    for kind, flag, priority in (("fair_value_gaps", "filled", 4), ("order_blocks", "invalidated", 5)):
        for row in structure.get(kind, []):
            lower, upper = number(row.get("lower")), number(row.get("upper"))
            if row.get(flag) or lower is None or upper is None or lower >= upper:
                continue
            side = 1 if row.get("type") == "BULLISH" else -1 if row.get("type") == "BEARISH" else 0
            if side:
                zones.append({"lower": lower, "upper": upper, "price": (lower + upper) / 2,
                              "label": kind, "side": side, "priority": priority, "structural": True,
                              "impulse_body_atr": (number(row.get("impulse_body_atr")) or 0)
                                  if row.get("impulse_aligned", True) else 0,
                              "gap_size": upper - lower})
    profile = technical.get("volume_profile") or {}
    point(profile.get("value_area_low"), "Volumenprofil VAL", 1)
    point(profile.get("value_area_high"), "Volumenprofil VAH", -1)
    point(profile.get("poc"), "Volumenprofil POC", 0, 2)
    for period in ("20", "50"):
        point((technical.get("ema") or {}).get(period), "EMA " + period, 0, 2)
        if zones and zones[-1]["label"] == "EMA " + period:
            zones[-1]["structural"] = False
    point(technical.get("vwap"), "VWAP", 0, 2)
    if zones and zones[-1]["label"] == "VWAP":
        zones[-1]["structural"] = False
    return zones


def select_nearest_plan(plans, direction, price, atr):
    """Distance first; compare FVG quality only inside the nearest FVG's local band."""
    current = [row for row in plans if row[1]["entry_kind"] == "CURRENT"]
    if current:
        return current[0]
    nearest = min(plans, key=lambda row: (abs(row[1]["entry_price"] - price), row[1]["entry_price"]))
    if nearest[0]["label"] != "fair_value_gaps" or nearest[1]["entry_kind"] != "PULLBACK":
        return nearest
    distance = abs(nearest[1]["entry_price"] - price)
    fvgs = [row for row in plans if row[0]["label"] == "fair_value_gaps" and row[1]["entry_kind"] == "PULLBACK"
            and abs(row[1]["entry_price"] - price) <= distance + FVG_NEARBY_ATR * atr]
    strongest = max(row[0].get("impulse_body_atr", 0) for row in fvgs)
    fvgs = [row for row in fvgs if row[0].get("impulse_body_atr", 0) >= strongest * FVG_SIMILAR_RATIO]
    largest = max(row[0]["gap_size"] for row in fvgs)
    fvgs = [row for row in fvgs if row[0]["gap_size"] >= largest * FVG_SIMILAR_RATIO]
    return max(fvgs, key=lambda row: (-direction * row[1]["entry_price"], row[0].get("impulse_body_atr", 0)))


def review_previous(technical, previous):
    if not previous or previous.get("status") not in ("VORBEREITET", "AKTIV"):
        return None
    direction, score, strength = group_context(technical)
    prior_direction = 1 if previous["direction"] == "BUY" else -1
    price = number(technical.get("latest_close"))
    invalidation = number(previous["original_plan"].get("invalidation_price"))
    broken = price is not None and invalidation is not None and prior_direction * (price - invalidation) <= 0
    assessment = "INVALIDATED" if broken else "CONFIRMED" if direction == prior_direction else "AGAINST" if direction else "WEAKENING"
    return {"previous_setup_id": previous["setup_id"], "assessment": assessment,
            "confidence_in_previous": strength if direction == prior_direction else 25 if direction else 55,
            "technical_break": broken, "macro_opposition": False, "macro_opposition_strength": 0,
            "macro_event_id": None, "entry_confirmed": False, "entry_evidence_bar_at": None,
            "reason": "Technische Invalidation gebrochen." if broken else "Beide technischen Gruppen bestätigen das Setup." if direction == prior_direction
                      else "Beide technischen Gruppen zeigen gegen das Setup." if direction else "Technische Gruppen bestätigen die Richtung nicht gemeinsam."}


def build_technical_plan(ticker, timeframe, technical, *, previous=None, as_of=None):
    direction, score, strength = group_context(technical)
    proposal = {"signal_type": "NEUTRAL", "direction_score": score, "confidence": 0, "setup_quality": "C",
                "reasons": [], "contradictions": [], "level_basis": [],
                "group_comparison": (technical.get("indicator_groups") or {}).get("comparison", "UNAVAILABLE"),
                "previous_setup_review": review_previous(technical, previous)}
    price = number(technical.get("current_price")) or number(technical.get("latest_close"))
    atr = number(technical.get("atr"))
    if technical.get("error") or price is None or atr is None:
        proposal["reasons"] = [technical.get("error") or "Keine belastbaren technischen Kurs-/Volatilitätsdaten."]
    elif not direction:
        proposal["reasons"] = ["Beide Indikatorgruppen müssen die gleiche Richtung mit ausreichend Einzelwerten bestätigen."]
    else:
        zones = reference_zones(technical)
        plans = []
        entries = [(zone, "PULLBACK") for zone in zones
                   if zone["side"] in (0, direction) and direction * (price - zone["price"]) >= 0]
        entries.append(({"price": price, "lower": price - 0.05 * atr, "upper": price + 0.05 * atr,
                         "label": "Aktueller Kurs", "side": 0, "structural": False}, "CURRENT"))
        for zone in zones:
            if zone["structural"] and zone["side"] == -direction:
                trigger = (zone["upper"] if direction == 1 else zone["lower"]) + direction * 0.05 * atr
                if direction * (trigger - price) > 0:
                    entries.append(({**zone, "price": trigger}, "BREAKOUT"))
        for zone, entry_kind in entries:
            entry = zone["price"]
            if abs(entry - price) > MAX_ENTRY_DISTANCE_ATR * atr:
                continue
            anchors = [z["lower"] if direction == 1 else z["upper"] for z in zones if z["structural"]
                       and z["side"] in (0, direction) and direction * (entry - (z["lower"] if direction == 1 else z["upper"])) > 0]
            anchor = zone["lower"] if direction == 1 else zone["upper"]
            if not zone["structural"]:
                if not anchors:
                    continue
                anchor = min(anchors, key=lambda value: abs(entry - value))
            stop = anchor - direction * 0.25 * atr
            risk = direction * (entry - stop)
            if stop <= 0 or risk < 0.25 * atr or risk > 5 * atr:
                continue
            obstacles = sorted(((direction * (z["price"] - entry), z) for z in zones
                                if z["structural"] and z["side"] in (0, -direction)
                                and direction * (z["price"] - entry) > max(1e-8, 1e-6 * atr)), key=lambda row: row[0])
            targets = []
            for distance, target in obstacles:
                if not targets or abs(target["price"] - targets[-1]["price"]) > max(1e-8, 1e-6 * atr):
                    targets.append(target)
            # Never skip a nearby resistance/support simply to manufacture an attractive R:R.
            if not targets or direction * (targets[0]["price"] - entry) / risk < TECHNICAL_MIN_REWARD_RISK:
                continue
            width = 0.05 * atr if entry_kind == "BREAKOUT" else min((zone["upper"] - zone["lower"]) / 2, 0.25 * atr)
            invalidation = stop
            if entry_kind == "BREAKOUT" and direction * (price - stop) <= 0:
                waiting_anchors = [z["lower"] if direction == 1 else z["upper"] for z in zones
                                   if z["structural"] and z["side"] in (0, direction)
                                   and direction * (price - (z["lower"] if direction == 1 else z["upper"])) > 0]
                if not waiting_anchors:
                    continue
                invalidation = min(waiting_anchors, key=lambda value: abs(price - value)) - direction * 0.25 * atr
            condition = (f"Einstieg am aktuell beobachteten Kurs {entry:.6f}; technische Richtung und Zielzonen bestätigt."
                         if entry_kind == "CURRENT" else
                         f"Geplanter Ausbruch durch {zone['label']} bei {entry:.6f}; Aktivierung erst beim Erreichen des Triggers."
                         if entry_kind == "BREAKOUT" else
                         f"Geplanter Rücklauf an {zone['label']} bei {entry:.6f}; Aktivierung erst beim Erreichen der Zone.")
            candidate = {**proposal, "signal_type": "BUY" if direction == 1 else "SELL", "confidence": strength,
                         "setup_quality": "A" if strength >= 80 else "B", "entry_price": entry,
                         "entry_zone_min": entry - width, "entry_zone_max": entry + width, "stop_loss": stop,
                         "take_profit_1": targets[0]["price"], "take_profit_2": targets[1]["price"] if len(targets) > 1 else None,
                         "entry_mode": "STOP" if entry_kind == "BREAKOUT" else "LIMIT", "requires_entry_confirmation": False,
                         "entry_kind": entry_kind, "selection_policy": "nearest_valid_entry",
                         "entry_condition": condition,
                         "invalidation_price": invalidation, "invalidation_condition": "Bruch der technischen Zone einschließlich ATR-Puffer.",
                         "valid_for_bars": 12,
                         "reasons": ["Beide technischen Gruppen bestätigen die Richtung.", "Technischer Setup-Score; keine Erfolgswahrscheinlichkeit."],
                         "level_basis": [f"Entry: {zone['label']} ({entry:.6f})", f"Stop: Struktur {anchor:.6f} + 0,25 ATR Puffer",
                                         f"TP1: {targets[0]['label']} ({targets[0]['price']:.6f})"]
                                        + ([f"TP2: {targets[1]['label']} ({targets[1]['price']:.6f})"] if len(targets) > 1 else [])}
            if zone["label"] == "fair_value_gaps":
                candidate["fvg_quality"] = {"impulse_body_atr": zone.get("impulse_body_atr", 0), "gap_size": zone["gap_size"]}
            checked = validate_technical_trade_decision(ticker, timeframe, technical, candidate, as_of=as_of)
            if checked["validation_status"] == "accepted":
                plans.append((zone, candidate, checked))
        if plans:
            _, proposal, checked = select_nearest_plan(plans, direction, price, atr)
            return proposal, checked
        proposal["reasons"] = [f"Kein strukturierter Entry innerhalb von {MAX_ENTRY_DISTANCE_ATR:g} ATR mit einem echten ersten Ziel und mindestens {TECHNICAL_MIN_REWARD_RISK:g}R gefunden."]
    result = validate_technical_trade_decision(ticker, timeframe, technical, proposal, as_of=as_of)
    result["reasons"] = list(proposal["reasons"])
    return proposal, result
