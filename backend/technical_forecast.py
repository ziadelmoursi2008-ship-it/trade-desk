"""Technical continuation outlooks from the observed price. No future-entry search."""

from datetime import datetime, timezone

from decision_engine import TECHNICAL_MIN_REWARD_RISK, validate_technical_trade_decision
from technical_strategy import FVG_NEARBY_ATR, FVG_SIMILAR_RATIO, group_context, number, reference_zones
from timeframe_analysis import MIN_TARGET_DISTANCE_PCT, check_poi_path, forward_target_zones, unchecked_poi_path

FORECAST_VERSION = "2.2-indicator-fixes"


def _invalidation_zone(zones, direction, price, atr):
    candidates = [zone for zone in zones if zone["structural"] and zone["side"] in (0, direction)
                  and direction * (price - (zone["lower"] if direction == 1 else zone["upper"])) > 0]
    if not candidates:
        return None
    distance = lambda zone: abs(price - zone["price"])
    nearest = min(candidates, key=distance)
    if nearest["label"] != "fair_value_gaps":
        return nearest
    nearby = [zone for zone in candidates if zone["label"] == "fair_value_gaps"
              and distance(zone) <= distance(nearest) + FVG_NEARBY_ATR * atr]
    impulse = max(zone.get("impulse_body_atr", 0) for zone in nearby)
    nearby = [zone for zone in nearby if zone.get("impulse_body_atr", 0) >= impulse * FVG_SIMILAR_RATIO]
    size = max(zone["gap_size"] for zone in nearby)
    nearby = [zone for zone in nearby if zone["gap_size"] >= size * FVG_SIMILAR_RATIO]
    return max(nearby, key=lambda zone: -direction * zone["price"])


def build_technical_forecast(ticker, timeframe, technical, *, as_of=None, observed_at=None, market=None,
                             lower_contexts=None):
    """Select a primary-timeframe target beyond its minimum; check the entire path."""
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    price = number(technical.get("current_price")) or number(technical.get("latest_close"))
    atr = number(technical.get("atr"))
    direction, score, strength = group_context(technical)
    result = {
        "ticker": ticker, "timeframe": timeframe, "strategy_version": FORECAST_VERSION,
        "analysis_kind": "CURRENT_PROJECTION", "status": "UNAVAILABLE", "direction": "UNCLEAR",
        "reference_price": price, "target_price": None, "secondary_target_price": None,
        "target_zone_min": None, "target_zone_max": None, "target_basis": None,
        "invalidation_price": None, "invalidation_basis": None,
        "direction_score": score, "indicator_strength": strength if direction else 0,
        "distance_pct": None, "risk_reward_ratio": None, "trade_eligible": False,
        "min_reward_risk": TECHNICAL_MIN_REWARD_RISK,
        "min_distance_pct": MIN_TARGET_DISTANCE_PCT[timeframe],
        "nearest_zone_price": None, "nearest_zone_basis": None,
        "poi_check": unchecked_poi_path(timeframe),
        "summary": "Keine verlässliche aktuelle technische Einschätzung verfügbar.",
        "trade_reason": "Keine vollständige technische Grundlage.", "reasons": [],
        "analysis_bar_at": int(as_of * 1000) if as_of is not None else now,
        "observed_at": int(observed_at if observed_at is not None else now), "generated_at": now,
    }
    if technical.get("error") or price is None or atr is None:
        result["summary"] = technical.get("error") or result["summary"]
        result["indicator_strength"] = 0
        return result
    if (market or {}).get("is_stale"):
        result.update(status="STALE", indicator_strength=0, summary="Kursdaten sind veraltet; kein aktueller technischer Ausblick.")
        return result
    if not direction:
        result.update(status="UNCLEAR", summary="Trend und Momentum bestätigen aktuell keine gemeinsame Richtung.",
                      trade_reason="Keine gemeinsam bestätigte technische Richtung.")
        return result
    result["direction"] = "UP" if direction == 1 else "DOWN"
    zones = reference_zones(technical)
    unique = forward_target_zones(technical, direction, price)
    minimum = result["min_distance_pct"]
    if unique:
        result.update(nearest_zone_price=unique[0]["target_price"], nearest_zone_basis=unique[0]["label"])
    qualified = [zone for zone in unique
                 if direction * (zone["target_price"] - price) / price * 100 >= minimum - 1e-9]
    trend = "weitere Aufwärtsbewegung" if direction == 1 else "weitere Abwärtsbewegung"
    if not qualified:
        result.update(status="NO_TARGET",
                      summary=f"Die Technik zeigt eine Tendenz für eine {trend}, aber keine vorhandene Kurszone mit mindestens {minimum:g} % Strecke im {timeframe}-Timeframe.",
                      trade_reason=f"Kein reales technisches Hauptziel erfüllt die Mindeststrecke von {minimum:g} %.")
        return result
    target = qualified[0]
    target_price = target["target_price"]
    poi_check = check_poi_path(timeframe, technical, direction, price, target_price, lower_contexts)
    result.update(status="AVAILABLE", target_price=target_price, target_zone_min=target["lower"],
                  target_zone_max=target["upper"], target_basis=target["label"],
                  secondary_target_price=qualified[1]["target_price"] if len(qualified) > 1 else None,
                  distance_pct=abs(target_price - price) / price * 100, poi_check=poi_check,
                  summary=f"Die {timeframe}-Technik zeigt ab {price:.2f} eine Tendenz für eine {trend}. Das technische Hauptziel liegt bei {target_price:.2f}; die Freigabe hängt auch von den POIs auf dem Weg ab.",
                  reasons=["Trend und Momentum bestätigen dieselbe Richtung.",
                           f"Hauptziel aus {timeframe}: {target['label']} bei {target_price:.6f}.",
                           f"Mindeststrecke für {timeframe}: {minimum:g} %."])
    anchor = _invalidation_zone(zones, direction, price, atr)
    if anchor is None:
        result["trade_reason"] = "Keine belastbare technische Grenze gegen die Einschätzung verfügbar."
        return result
    invalidation = (anchor["lower"] if direction == 1 else anchor["upper"]) - direction * 0.25 * atr
    if invalidation <= 0:
        result["trade_reason"] = "Keine positive technische Invalidation verfügbar."
        return result
    risk = direction * (price - invalidation)
    reward = direction * (target_price - price)
    rr = reward / risk if risk > 0 else None
    result.update(invalidation_price=invalidation, invalidation_basis=anchor["label"], risk_reward_ratio=rr)
    if not 0.25 * atr <= risk <= 5 * atr:
        result["trade_reason"] = "Der Abstand zur technischen Invalidation liegt außerhalb des Risikorahmens."
    elif poi_check["status"] == "INCOMPLETE":
        result["trade_reason"] = "Keine Trade-Freigabe: POI-Prüfung unvollständig für " + ", ".join(poi_check["unavailable_timeframes"]) + "."
    elif any(item["blocking"] for item in poi_check["obstacles"]):
        first = next(item for item in poi_check["obstacles"] if item["blocking"])
        result["trade_reason"] = f"Keine Trade-Freigabe: entgegenstehender POI im {first['timeframe']}-Timeframe bei {first['price']:.6f} vor dem Hauptziel."
    elif rr is None or rr < TECHNICAL_MIN_REWARD_RISK:
        result["trade_reason"] = f"Das Hauptziel bietet ab dem aktuellen Kurs weniger als {TECHNICAL_MIN_REWARD_RISK:g}R."
    elif not (market or {}).get("market_open", True):
        result["trade_reason"] = "Markt geschlossen; kein aktuelles Handelssignal."
    else:
        result.update(trade_eligible=True, trade_reason=f"Hauptziel erfüllt {minimum:g} % Mindeststrecke und mindestens {TECHNICAL_MIN_REWARD_RISK:g}R; keine blockierenden POIs erkannt.")
    return result


def forecast_decision(forecast, technical):
    """Compatibility snapshot: the entry alias is only the observed reference price."""
    directional = forecast["trade_eligible"]
    proposal = {
        "signal_type": ("BUY" if forecast["direction"] == "UP" else "SELL") if directional else "NEUTRAL",
        "direction_score": forecast["direction_score"], "confidence": forecast["indicator_strength"],
        "setup_quality": "A" if forecast["indicator_strength"] >= 80 else "B",
        "reasons": [forecast["summary"], forecast["trade_reason"]], "level_basis": [],
        "entry_kind": "CURRENT", "selection_policy": "timeframe_projection",
    }
    price, atr = forecast["reference_price"], number(technical.get("atr"))
    if directional:
        proposal.update(entry_price=price, entry_zone_min=price - 0.05 * atr, entry_zone_max=price + 0.05 * atr,
                        stop_loss=forecast["invalidation_price"], take_profit_1=forecast["target_price"],
                        # Later zones are informational; only the checked path is a trade objective.
                        take_profit_2=None, entry_mode="LIMIT",
                        invalidation_price=forecast["invalidation_price"], entry_condition=forecast["summary"])
    decision = validate_technical_trade_decision(forecast["ticker"], forecast["timeframe"], technical, proposal,
                                                as_of=forecast["analysis_bar_at"] / 1000)
    decision.update(strategy_version=FORECAST_VERSION, analysis_kind="CURRENT_PROJECTION",
                    min_distance_pct=forecast["min_distance_pct"], poi_check=forecast["poi_check"])
    return decision
