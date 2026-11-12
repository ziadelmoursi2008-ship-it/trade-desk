"""Versioned, configurable priorities. No Python-generated market direction."""

import json
import math
import os

POLICY_VERSION = "4.2-quality"
BUY_DIRECTION_MIN = 55.0
SELL_DIRECTION_MAX = 45.0
# Nearby plans only: measured in the selected timeframe's actual volatility.
MAX_ENTRY_DISTANCE_ATR = 2.0
DEFAULT_DECISION_WEIGHTS = {"technical": 50, "hard_data": 25, "normal_news": 25}
# Related observations share a category budget, rather than each receiving its full weight.
DEFAULT_MACRO_WEIGHTS = {
    "DFEDTARL": 9, "DFEDTARU": 9, "DGS10": 12,
    "CUUR0000SA0": 12, "CUUR0000SA0L1E": 8, "PCEPILFE": 10,
    "CES0000000001": 14, "LNS14000000": 5, "LNS11300000": 1,
    "CES0500000003": 5, "GDPC1": 10, "RSAFS": 5,
}


def _weights(env_name, defaults):
    values = json.loads(os.environ[env_name]) if os.getenv(env_name) else defaults
    if set(values) != set(defaults):
        raise ValueError(f"{env_name}: expected keys {sorted(defaults)}")
    values = {key: float(value) for key, value in values.items()}
    if any(not math.isfinite(value) or value < 0 for value in values.values()) or not math.isclose(sum(values.values()), 100):
        raise ValueError(f"{env_name}: weights must be non-negative and total 100")
    return values


def decision_policy(timeframe=None):
    # Explicit user overrides retain precedence. These are conservative start profiles,
    # not fitted success probabilities or claimed optimal trading weights.
    profile = ({"technical": 70, "hard_data": 10, "normal_news": 20} if timeframe in ("1m", "5m", "15m")
               else {"technical": 60, "hard_data": 15, "normal_news": 25} if timeframe in ("30m", "1h")
               else DEFAULT_DECISION_WEIGHTS)
    return {
        "version": POLICY_VERSION,
        "decision_weights_pct": _weights("DECISION_WEIGHTS_JSON", profile),
        "timeframe": timeframe, "weight_profile": "custom" if os.getenv("DECISION_WEIGHTS_JSON") else "horizon_start_weights",
        "calibration_status": "not_empirically_calibrated",
        "direction_thresholds": {"buy_min": BUY_DIRECTION_MIN, "sell_max": SELL_DIRECTION_MAX},
        "macro_weights_pct": _weights("MACRO_WEIGHTS_JSON", DEFAULT_MACRO_WEIGHTS),
        "confidence_bands": {"strong": 80, "normal": 65, "weak": 55, "hold_previous": 45},
        "opposing_bars_to_end": 2,
        "low_confidence_bars_to_end": 2,
        "massive_confidence_threshold": 45,
        "default_valid_for_bars": 12,
        "max_entry_distance_atr": MAX_ENTRY_DISTANCE_ATR,
        "scores_are_win_probabilities": False,
    }


def quantitative_policy(policy, quantitative=None):
    """Allocate one capped volume budget, reduced for coarse source resolution."""
    if quantitative is None:
        return policy
    from quantitative_analysis import TECHNICAL_SHARE, MAX_TOTAL_WEIGHT
    weights = dict(policy["decision_weights_pct"])
    configured = min(MAX_TOTAL_WEIGHT, round(weights["technical"] * TECHNICAL_SHARE, 4))
    quality = quantitative.get("weight_quality_factor", 0)
    quality = float(quality) if isinstance(quality, (int, float)) and math.isfinite(quality) else 0.0
    quality = max(0.0, min(1.0, quality))
    score = quantitative.get("direction_score")
    usable = (quantitative.get("status") == "AVAILABLE" and isinstance(score, (int, float))
              and math.isfinite(score) and 0 <= score <= 100)
    applied = round(configured * quality, 4) if usable else 0.0
    if applied > 0:
        weights["technical"] = round(weights["technical"] - applied, 4)
        weights["quantitative"] = applied
    return {**policy, "version": policy["version"] + "-quant-1",
            "decision_weights_pct": weights,
            "quantitative_policy": {"configured_weight_pct": configured,
                                    "applied_weight_pct": applied, "quality_factor": quality,
                                    "score": score if applied > 0 else None,
                                    "method": quantitative.get("method"),
                                    "status": quantitative.get("status"),
                                    "revision": quantitative.get("selection_revision"),
                                    "scores_are_win_probabilities": False}}


def weighted_hard_bundle(bundle, policy):
    macro_weight = policy["decision_weights_pct"]["hard_data"]
    weights = policy["macro_weights_pct"]
    return {**bundle, "items": [
        {**row, "importance_pct": weights.get(row["series_id"], 0),
         "decision_weight_pct": round(weights.get(row["series_id"], 0) * macro_weight / 100, 4)}
        for row in bundle.get("items", [])
    ], "weight_coverage_pct": sum(weights.get(row["series_id"], 0) for row in bundle.get("items", []) if not row.get("stale"))}


def summarize_macro(assessments, bundle):
    """Arithmetic audit of Gemini's individual judgments; never creates a trade."""
    judgments = {row["series_id"]: row for row in assessments}
    weighted_effect, coverage = 0.0, 0.0
    details = []
    for row in bundle.get("items", []):
        judgment = judgments.get(row["series_id"])
        if judgment is None or row.get("stale"):
            continue
        weight = row["importance_pct"]
        effect = float(judgment["directional_effect"])
        weighted_effect += weight * effect / 100
        coverage += weight
        details.append({**judgment, "importance_pct": weight, "decision_weight_pct": row["decision_weight_pct"]})
    return {"weighted_direction_score": round(50 + 50 * weighted_effect, 2),
            "assessed_weight_pct": coverage, "assessments": details}


def summarize_news(articles):
    """Use Gemini-assigned importance once per event; duplicate coverage adds no votes."""
    events = {}
    def finite(value, low, high):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return max(low, min(high, number)) if math.isfinite(number) else None
    for article in articles:
        importance = finite(article.get("importance_score"), 0, 100)
        relevance = finite(article.get("relevance"), 0, 1)
        novelty = finite(article.get("novelty"), 0, 1)
        sentiment = finite(article.get("sentiment"), -1, 1)
        if (article.get("analysis_method") != "gemini" or any(value is None for value in
                (importance, relevance, novelty, sentiment)) or relevance < 0.35):
            continue
        key = article.get("event_key") or article.get("url")
        weight = importance / 100 * relevance * novelty
        if weight <= 0:
            continue
        if key not in events or weight > events[key][0]:
            events[key] = (weight, sentiment)
    total = sum(weight for weight, effect in events.values())
    effect = sum(weight * effect for weight, effect in events.values()) / total if total else 0
    strength = max((weight for weight, _ in events.values()), default=0.0)
    return {"weighted_direction_score": round(50 + 50 * effect * strength, 2),
            "distinct_events": len(events), "evidence_strength_pct": round(100 * strength, 4),
            "direction_only_score": round(50 + 50 * effect, 2)}
