"""Timeframe distance policy and opposing POIs along a primary timeframe's path."""

from instruments import TIMEFRAME_MAP
from technical_strategy import number, reference_zones

# Double at each supported timeframe step, rather than per elapsed minute.
MIN_TARGET_DISTANCE_PCT = {
    timeframe: round(0.15 * 2 ** index, 8)
    for index, timeframe in enumerate(TIMEFRAME_MAP)
}
POI_MATERIAL_ATR = 0.25
BAND_ZONE_KINDS = ("fair_value_gaps", "order_blocks")


def lower_timeframes(timeframe):
    seconds = TIMEFRAME_MAP[timeframe]["seconds"]
    return sorted((key for key, info in TIMEFRAME_MAP.items() if info["seconds"] < seconds),
                  key=lambda key: TIMEFRAME_MAP[key]["seconds"], reverse=True)


def zone_contact(zone, direction):
    # Approach opposing bands at their near edge, not at a price inside the POI.
    if zone["label"] in BAND_ZONE_KINDS:
        return zone["lower"] if direction == 1 else zone["upper"]
    return zone["price"]


def forward_target_zones(technical, direction, price):
    epsilon = max(1e-8, 1e-6 * (number(technical.get("atr")) or 0))
    targets = sorted((dict(zone, target_price=zone_contact(zone, direction))
                      for zone in reference_zones(technical)
                      if zone["structural"] and zone["side"] in (0, -direction)
                      and direction * (zone_contact(zone, direction) - price) > epsilon),
                     key=lambda zone: direction * (zone["target_price"] - price))
    unique = []
    for zone in targets:
        if not unique or abs(zone["target_price"] - unique[-1]["target_price"]) > epsilon:
            unique.append(zone)
    return unique


def unchecked_poi_path(timeframe):
    return {"status": "NOT_CHECKED", "required_timeframes": lower_timeframes(timeframe),
            "checked_timeframes": [], "unavailable_timeframes": [], "unavailable_reasons": {},
            "analysis_bars": {}, "obstacles": []}


def check_poi_path(timeframe, technical, direction, price, target, lower_contexts=None):
    """Keep small POIs as warnings; material or cross-timeframe POIs block a trade.

    Material means source ATR or band width >= 0.25 primary ATR. This is an
    explicit heuristic, not a measured probability. Primary opposing levels
    always block; overlapping POIs from different timeframes also block.
    Missing/stale required contexts never count as a clear path.
    """
    result = unchecked_poi_path(timeframe)
    primary_atr = number(technical.get("atr")) or 0
    contexts = [(timeframe, technical)]
    for smaller in result["required_timeframes"]:
        context = (lower_contexts or {}).get(smaller) or {}
        data = context.get("technical") or {}
        if context.get("status") != "AVAILABLE" or data.get("error") or not number(data.get("atr")):
            result["unavailable_timeframes"].append(smaller)
            result["unavailable_reasons"][smaller] = context.get("reason") or data.get("error") or "Keine frischen abgeschlossenen POI-Daten."
            continue
        result["checked_timeframes"].append(smaller)
        result["analysis_bars"][smaller] = context.get("analysis_bar_at")
        contexts.append((smaller, data))

    epsilon = max(1e-8, 1e-6 * primary_atr)
    distance = direction * (target - price)
    seen = set()
    for source_timeframe, data in contexts:
        source_atr = number(data.get("atr")) or 0
        for zone in reference_zones(data):
            if not zone["structural"] or zone["side"] not in (0, -direction):
                continue
            contact = zone_contact(zone, direction)
            near = direction * (contact - price)
            far = (direction * ((zone["upper"] if direction == 1 else zone["lower"]) - price)
                   if zone["label"] in BAND_ZONE_KINDS else near)
            if far < -epsilon or near >= distance - epsilon:
                continue
            key = (source_timeframe, zone["label"], zone["lower"], zone["upper"])
            if key in seen:
                continue
            seen.add(key)
            material = (source_timeframe == timeframe or source_atr >= POI_MATERIAL_ATR * primary_atr
                        or zone["upper"] - zone["lower"] >= POI_MATERIAL_ATR * primary_atr)
            result["obstacles"].append({
                "timeframe": source_timeframe, "kind": zone["label"], "price": contact,
                "lower": zone["lower"], "upper": zone["upper"],
                "distance_pct": max(0, near) / price * 100, "blocking": material,
                "reason": ("Entgegenstehende Zone im Haupt-Timeframe." if source_timeframe == timeframe
                           else "Relevante Gegenzone im Verhältnis zur Schwankungsbreite des Haupt-Timeframes." if material
                           else "Kleine Zwischenzone; als Hindernis beobachten."),
            })

    for obstacle in result["obstacles"]:
        confluence = any(other["timeframe"] != obstacle["timeframe"]
                         and max(other["lower"], obstacle["lower"]) <= min(other["upper"], obstacle["upper"]) + epsilon
                         for other in result["obstacles"])
        if confluence:
            obstacle.update(blocking=True, reason="Überlappende Gegenzonen in mehreren Timeframes.")
    result["obstacles"].sort(key=lambda item: (item["distance_pct"], -TIMEFRAME_MAP[item["timeframe"]]["seconds"]))
    result["status"] = ("INCOMPLETE" if result["unavailable_timeframes"] else
                        "BLOCKED" if any(item["blocking"] for item in result["obstacles"]) else "CLEAR")
    return result
