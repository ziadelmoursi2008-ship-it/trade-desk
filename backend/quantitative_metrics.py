"""Pure volume-direction scoring with separate, volatility-scaled price context."""
import math
import numpy as np
import pandas as pd

WINDOW = 20
RECENT_BARS = 3
FLOW_WEIGHTS = {"recent_flow": 25, "background_flow": 75}
FULL_WEIGHT_SOURCE_BARS = 5


def direction(score):
    return "BULLISH" if score >= 55 else "BEARISH" if score <= 45 else "NEUTRAL"


def normalized_move(returns):
    """Scale by observed return energy; constant nonzero returns have a finite denominator."""
    values = np.asarray(returns, dtype=float)
    energy = float(np.sqrt(np.square(values).sum()))
    return float(values.sum() / energy) if energy > 0 else 0.0


def build_snapshot(frame, timeframe, main_rows, points, benchmark, benchmark_symbol, computed_at,
                   *, version, source_timeframe, method_note, sessions=None):
    close = frame.Close.tail(WINDOW + 1).to_numpy(dtype=float)
    enough = len(close) >= WINDOW + 1
    z_score = efficiency = volatility = momentum_score = relative = relative_z = None
    if enough:
        returns = np.diff(np.log(close))
        volatility = float(np.std(returns, ddof=0))
        price_std = float(np.std(close[-WINDOW:], ddof=0))
        z_score = float((close[-1] - close[-WINDOW:].mean()) / price_std) if price_std > 0 else 0.0
        distance = float(np.abs(np.diff(close)).sum())
        efficiency = float(abs(close[-1] - close[0]) / distance) if distance > 0 else 0.0
        momentum_score = 50 + 50 * math.tanh(normalized_move(returns))
        if benchmark is not None and benchmark.attrs.get("timeframe") == timeframe:
            aligned = benchmark.Close.reindex(frame.index[-WINDOW - 1:])
            if aligned.notna().all() and np.isfinite(aligned).all() and (aligned > 0).all():
                relative = float(((close[-1] / close[0]) / (aligned.iloc[-1] / aligned.iloc[0]) - 1) * 100)
                relative_z = normalized_move(returns - np.diff(np.log(aligned.to_numpy(dtype=float))))

    aggregated, offset = [], 0
    for bar in main_rows[-WINDOW:]:
        local_day = str(pd.Timestamp(bar["at"], unit="ms", tz="UTC").tz_convert(
            frame.attrs.get("exchange_timezone", str(frame.index.tz))).date())
        session = (sessions or {}).get(local_day)
        expected_start = max(bar["at"], session["start"]) if session else bar["at"]
        expected_end = min(bar["end"], session["end"]) if session else bar["end"]
        while offset < len(points) and points[offset]["bar_at"] < bar["at"]:
            offset += 1
        selected, cursor = [], offset
        while cursor < len(points) and points[cursor]["bar_at"] < bar["end"]:
            point = points[cursor]
            if point["bar_end"] <= bar["end"]:
                selected.append(point)
            cursor += 1
        volume = sum(point["volume"] for point in selected)
        known = bool(selected) and all(bool(point.get("volume_known", True)) for point in selected)
        ratio = volume / bar["volume"] if bar["volume"] > 0 else None
        reconciled = ratio is not None and abs(ratio - 1) <= 0.02
        contiguous = bool(selected) and all(previous["bar_end"] == following["bar_at"]
            for previous, following in zip(selected, selected[1:]))
        full_interval = bool(selected) and selected[0]["bar_at"] == expected_start and selected[-1]["bar_end"] == expected_end
        session_interval = (
            not session and timeframe in ("1h", "4h", "1D") and reconciled and bool(selected)
            and frame.attrs.get("instrument_type") != "CRYPTOCURRENCY"
            and (timeframe == "1D" or selected[0]["bar_at"] == bar["at"])
            and math.isclose(selected[0]["open"], bar["open"], rel_tol=1e-7)
            and math.isclose(selected[-1]["close"], bar["close"], rel_tol=1e-7))
        covered = bool(known and contiguous and (full_interval or session_interval))
        aggregated.append({"volume": volume, "delta": sum(point["delta"] for point in selected),
            "coverage_ratio": ratio, "covered": covered, "volume_known": known, "source_count": len(selected),
            "volume_mismatch": bool(covered and not reconciled and (volume > 0 or bar["volume"] > 0))})

    valid = len(aggregated) == WINDOW and all(bar["covered"] for bar in aggregated)
    total_volume = sum(bar["volume"] for bar in aggregated) if valid else 0.0
    baseline_volume = total_volume / WINDOW if total_volume > 0 else 0.0
    components = []
    partitions = [("recent_flow", "Volumenrichtung letzte 3 Kerzen", aggregated[-RECENT_BARS:]),
                  ("background_flow", "Volumenrichtung vorherige 17 Kerzen", aggregated[:-RECENT_BARS])]
    scores = {}
    for key, name, bars in partitions:
        score = None
        if valid and bars and baseline_volume > 0:
            volume = sum(bar["volume"] for bar in bars)
            signed = sum(bar["delta"] for bar in bars)
            # A tiny-volume window cannot get the same influence as normal activity.
            denominator = max(volume, baseline_volume * len(bars))
            score = 50 + 50 * signed / denominator
        scores[key] = score
        components.append({"key": key, "name": name, "weight_pct": FLOW_WEIGHTS[key],
            "score": None if score is None else round(score, 2),
            "direction": "UNAVAILABLE" if score is None else direction(score)})
    available = enough and valid and total_volume > 0
    score = sum(scores[key] * weight / 100 for key, weight in FLOW_WEIGHTS.items()) if available else None
    density = sum(bar["source_count"] for bar in aggregated) / WINDOW if valid else 0.0
    quality = min(1.0, density / FULL_WEIGHT_SOURCE_BARS) if available else 0.0
    latest = aggregated[-1] if aggregated else None
    latest_ok = latest is not None and latest["covered"]
    latest_delta = latest["delta"] if latest_ok else None
    delta_ratio = (latest_delta / latest["volume"] if latest["volume"] > 0 else 0.0) if latest_ok else None
    window_delta = sum(bar["delta"] for bar in aggregated) if valid else None
    pressure = window_delta / total_volume if available else None
    divergence = "NONE"
    if window_delta is not None and enough:
        if close[-1] > close[0] and window_delta < 0:
            divergence = "PRICE_UP_CVD_DOWN"
        elif close[-1] < close[0] and window_delta > 0:
            divergence = "PRICE_DOWN_CVD_UP"
    warnings = []
    if quality < 1 and available:
        warnings.append("Wenige Quellkerzen je Hauptkerze: das Entscheidungsgewicht wird reduziert.")
    if any(bar["volume_mismatch"] for bar in aggregated):
        warnings.append("Yahoo-Volumen unterscheidet sich zwischen Zeitrahmen. Delta/CVD verwenden ausschließlich beobachtetes Quellvolumen.")
    if relative is None:
        warnings.append("Relative Stärke nicht verfügbar; sie erzeugt keine zusätzliche Richtungsstimme.")
    missing_volume = sum(not bar["volume_known"] for bar in aggregated)
    return {
        "version": version, "status": "AVAILABLE" if available else "PARTIAL",
        "analysis_bar_at": main_rows[-1]["end"] if main_rows else None, "computed_at": computed_at,
        "method": "OHLCV_DIRECTION_ESTIMATE", "method_note": method_note,
        "source_timeframe": source_timeframe, "window_bars": WINDOW,
        "direction": direction(score) if score is not None else "UNAVAILABLE",
        "direction_score": None if score is None else round(score, 2),
        "delta": latest_delta, "delta_pct": None if delta_ratio is None else round(delta_ratio * 100, 4),
        "cvd": points[-1]["cvd"] if points else None, "cvd_anchor_at": None,
        "cvd_change_20": window_delta, "cvd_pressure_pct": None if pressure is None else round(pressure * 100, 4),
        "cvd_is_observed_sum": True, "divergence": divergence,
        "z_score_20": None if z_score is None else round(z_score, 4),
        "efficiency_ratio_20": None if efficiency is None else round(efficiency, 4),
        "market_regime": "UNAVAILABLE" if efficiency is None else "TREND" if efficiency >= .6 else "RANGE" if efficiency <= .3 else "MIXED",
        "realized_volatility_20_pct": None if volatility is None else round(volatility * 100, 4),
        "relative_strength_20_pct": None if relative is None else round(relative, 4),
        "relative_strength_normalized": None if relative_z is None else round(relative_z, 4),
        "relative_strength_context_score": None if relative_z is None else round(50 + 50 * math.tanh(relative_z), 2),
        "statistical_momentum_context_score": None if momentum_score is None else round(momentum_score, 2),
        "price_context_scored_in_quant": False, "benchmark": benchmark_symbol, "components": components,
        "covered_bars": sum(bar["covered"] for bar in aggregated),
        "missing_volume_bars": missing_volume, "weight_quality_factor": round(quality, 6),
        "source_bars_per_main": round(density, 3), "full_weight_source_bars": FULL_WEIGHT_SOURCE_BARS,
        "coverage_method": "CONTIGUOUS_SOURCE_INTERVALS",
        "volume_mismatch_bars": sum(bar["volume_mismatch"] for bar in aggregated),
        "volume_coverage_pct": None if latest is None or latest["coverage_ratio"] is None else round(latest["coverage_ratio"] * 100, 2),
        "reason": ("Volumenrichtung aus getrennten Zeitfenstern; Preisstatistik zählt nur als Kontext."
            if available else "Mindestens 21 Hauptkerzen, 20 vollständige Intervalle und bekanntes nutzbares Quellvolumen erforderlich."),
        "warnings": warnings,
    }
