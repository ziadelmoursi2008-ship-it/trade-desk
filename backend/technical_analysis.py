"""Causal technical features grouped into independent confirmation families."""

import math

import numpy as np
import pandas as pd

MIN_ANALYSIS_BARS = 50
INDICATOR_VERSION = "1.2-volume-quality"


def _volume_known(frame):
    valid = frame.Volume.notna() & np.isfinite(frame.Volume) & frame.Volume.ge(0)
    return valid & frame.VolumeKnown.fillna(False).astype(bool) if "VolumeKnown" in frame else valid


def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _wilder(series, period):
    result = pd.Series(np.nan, index=series.index, dtype=float)
    valid = series.dropna()
    if len(valid) >= period:
        seeded = valid.iloc[period - 1:].copy()
        seeded.iloc[0] = valid.iloc[:period].mean()
        result.loc[seeded.index] = seeded.ewm(alpha=1 / period, adjust=False).mean()
    return result


def calculate_rsi(series, period=14):
    change = series.diff()
    gain = _wilder(change.clip(lower=0), period)
    loss = _wilder(-change.clip(upper=0), period)
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    rsi = rsi.mask((loss == 0) & (gain > 0), 100.0)
    return rsi.mask((loss == 0) & (gain == 0), 50.0)


def calculate_macd(series, fast=12, slow=26, signal=9):
    line = calculate_ema(series, fast) - calculate_ema(series, slow)
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return {"macd": line, "signal": signal_line, "hist": line - signal_line}


def calculate_bollinger_bands(series, period=20, std_dev=2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    return {"mid": mid, "upper": mid + std_dev * std, "lower": mid - std_dev * std}


def calculate_atr(high, low, close, period=14):
    previous = close.shift(1)
    ranges = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1)
    return _wilder(ranges.max(axis=1), period)


def calculate_vwap(df):
    if df.attrs.get("timeframe") == "1D":
        return pd.Series(np.nan, index=df.index, dtype=float)
    typical = (df.High + df.Low + df.Close) / 3
    volume = df.Volume.fillna(0).clip(lower=0)
    if isinstance(df.index, pd.DatetimeIndex):
        session_index = df.index
        exchange_timezone = df.attrs.get("exchange_timezone")
        if exchange_timezone and session_index.tz is not None:
            session_index = session_index.tz_convert(exchange_timezone)
        # Group by the exchange's calendar day, even when stored bars use UTC.
        sessions = session_index.date
    else:
        sessions = pd.Series(0, index=df.index)
    denominator = volume.groupby(sessions).cumsum()
    known = _volume_known(df).groupby(sessions).cummin()
    return ((typical * volume).groupby(sessions).cumsum() / denominator.replace(0, np.nan)).where(known)


def calculate_adx(high, low, close, period=14):
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = calculate_atr(high, low, close, period)
    smoothed_plus = _wilder(plus_dm, period)
    smoothed_minus = _wilder(minus_dm, period)
    plus_di = 100 * smoothed_plus / atr.replace(0, np.nan)
    minus_di = 100 * smoothed_minus / atr.replace(0, np.nan)
    # A fully warmed-up market with no range/movement has zero direction,
    # rather than a permanently missing ADX. Keep real warmup NaNs intact.
    plus_di = plus_di.mask((atr == 0) & (smoothed_plus == 0), 0.0)
    minus_di = minus_di.mask((atr == 0) & (smoothed_minus == 0), 0.0)
    total_di = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / total_di.replace(0, np.nan)
    dx = dx.mask(total_di == 0, 0.0)
    return {"adx": _wilder(dx, period), "plus_di": plus_di, "minus_di": minus_di}


def calculate_supertrend(high, low, close, period=10, multiplier=3.0):
    atr = calculate_atr(high, low, close, period)
    midpoint = (high + low) / 2
    basic_upper, basic_lower = midpoint + multiplier * atr, midpoint - multiplier * atr
    upper = basic_upper.copy()
    lower = basic_lower.copy()
    direction = pd.Series(np.nan, index=close.index, dtype=float)
    line = pd.Series(np.nan, index=close.index, dtype=float)
    for i in range(1, len(close)):
        if pd.isna(atr.iloc[i]):
            continue
        if pd.notna(upper.iloc[i - 1]) and (basic_upper.iloc[i] >= upper.iloc[i - 1] and close.iloc[i - 1] <= upper.iloc[i - 1]):
            upper.iloc[i] = upper.iloc[i - 1]
        if pd.notna(lower.iloc[i - 1]) and (basic_lower.iloc[i] <= lower.iloc[i - 1] and close.iloc[i - 1] >= lower.iloc[i - 1]):
            lower.iloc[i] = lower.iloc[i - 1]
        previous_direction = direction.iloc[i - 1] if pd.notna(direction.iloc[i - 1]) else 1.0
        if previous_direction < 0 and close.iloc[i] > upper.iloc[i]:
            direction.iloc[i] = 1.0
        elif previous_direction > 0 and close.iloc[i] < lower.iloc[i]:
            direction.iloc[i] = -1.0
        else:
            direction.iloc[i] = previous_direction
        line.iloc[i] = lower.iloc[i] if direction.iloc[i] > 0 else upper.iloc[i]
    return {"line": line, "direction": direction}


def build_indicator_frame(df):
    frame = df.copy()
    known_volume = _volume_known(frame)
    frame["VolumeKnown"] = known_volume
    for period in (9, 20, 50, 100, 200):
        frame[f"EMA{period}"] = calculate_ema(frame.Close, period)
        frame[f"SMA{period}"] = frame.Close.rolling(period).mean()
    frame["RSI"] = calculate_rsi(frame.Close)
    rsi_low, rsi_high = frame.RSI.rolling(14).min(), frame.RSI.rolling(14).max()
    frame["STOCH_RSI_RAW"] = 100 * (frame.RSI - rsi_low) / (rsi_high - rsi_low).replace(0, np.nan)
    frame["STOCH_RSI_K"] = frame.STOCH_RSI_RAW.rolling(3).mean()
    frame["STOCH_RSI_D"] = frame.STOCH_RSI_K.rolling(3).mean()
    for name, values in calculate_macd(frame.Close).items():
        frame[f"MACD_{name}"] = values
    frame["ROC12"] = frame.Close.pct_change(12) * 100
    typical = (frame.High + frame.Low + frame.Close) / 3
    typical_mean = typical.rolling(20).mean()
    mean_deviation = typical.rolling(20).apply(lambda values: np.mean(np.abs(values - values.mean())), raw=True)
    frame["CCI20"] = (typical - typical_mean) / (0.015 * mean_deviation.replace(0, np.nan))
    high14, low14 = frame.High.rolling(14).max(), frame.Low.rolling(14).min()
    frame["WILLIAMS_R"] = -100 * (high14 - frame.Close) / (high14 - low14).replace(0, np.nan)
    for name, values in calculate_bollinger_bands(frame.Close).items():
        frame[f"BB_{name}"] = values
    frame["ATR"] = calculate_atr(frame.High, frame.Low, frame.Close)
    frame["KELTNER_MID"] = calculate_ema(frame.Close, 20)
    frame["KELTNER_UPPER"] = frame.KELTNER_MID + 2 * frame.ATR
    frame["KELTNER_LOWER"] = frame.KELTNER_MID - 2 * frame.ATR
    frame["REALIZED_VOL_20"] = np.log(frame.Close / frame.Close.shift(1)).rolling(20).std(ddof=0) * 100
    frame["VWAP"] = calculate_vwap(frame)
    frame["VOLUME_AVG"] = frame.Volume.shift(1).rolling(20).mean().where(
        known_volume.shift(1).rolling(20).min().eq(1))
    signed_volume = np.sign(frame.Close.diff()).fillna(0) * frame.Volume.fillna(0)
    frame["OBV"] = signed_volume.cumsum()
    money_flow_multiplier = ((frame.Close - frame.Low) - (frame.High - frame.Close)) / (frame.High - frame.Low).replace(0, np.nan)
    money_flow_volume = money_flow_multiplier.fillna(0) * frame.Volume.fillna(0)
    frame["CMF20"] = (money_flow_volume.rolling(20).sum() / frame.Volume.rolling(20).sum().replace(0, np.nan)).where(
        known_volume.rolling(20).min().eq(1))
    raw_money = typical * frame.Volume.fillna(0)
    price_change = typical.diff()
    # The first row has no preceding price: it is not an observed zero flow.
    positive_flow = raw_money.where(price_change > 0, 0.0).where(price_change.notna()).rolling(14).sum()
    negative_flow = raw_money.where(price_change < 0, 0.0).where(price_change.notna()).rolling(14).sum()
    frame["MFI14"] = 100 - 100 / (1 + positive_flow / negative_flow.replace(0, np.nan))
    frame["MFI14"] = frame.MFI14.mask((negative_flow == 0) & (positive_flow > 0), 100.0)
    frame["MFI14"] = frame.MFI14.where(known_volume.rolling(14).min().eq(1))
    for name, values in calculate_adx(frame.High, frame.Low, frame.Close).items():
        frame[name.upper()] = values
    for name, values in calculate_supertrend(frame.High, frame.Low, frame.Close).items():
        frame[f"SUPERTREND_{name.upper()}"] = values
    conversion = (frame.High.rolling(9).max() + frame.Low.rolling(9).min()) / 2
    base = (frame.High.rolling(26).max() + frame.Low.rolling(26).min()) / 2
    frame["ICHIMOKU_CONVERSION"] = conversion
    frame["ICHIMOKU_BASE"] = base
    frame["ICHIMOKU_SPAN_A"] = ((conversion + base) / 2).shift(26)
    frame["ICHIMOKU_SPAN_B"] = ((frame.High.rolling(52).max() + frame.Low.rolling(52).min()) / 2).shift(26)
    # The current candle cannot redefine the level it is breaking through.
    frame["SUPPORT"] = frame.Low.shift(1).rolling(20).min()
    frame["RESISTANCE"] = frame.High.shift(1).rolling(20).max()
    frame["LOCAL_SUPPORT"] = frame.Low.shift(1).rolling(5).min()
    frame["LOCAL_RESISTANCE"] = frame.High.shift(1).rolling(5).max()
    frame["MOMENTUM"] = frame.Close.pct_change(3) * 100
    return frame


def _number(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def _signal(value, bullish, bearish):
    if value is None:
        return 0
    return 1 if bullish(value) else -1 if bearish(value) else 0


def _component(name, signal, value, weight=1.0, detail=None):
    return {"name": name, "signal": int(signal), "value": value, "weight": weight, "detail": detail}


def _score_group(name, components):
    available = [item for item in components if item["value"] is not None]
    total_weight = sum(item["weight"] for item in available)
    score = 100 * sum(item["signal"] * item["weight"] for item in available) / total_weight if total_weight else 0
    direction = "BULLISH" if score >= 20 else "BEARISH" if score <= -20 else "NEUTRAL"
    directional_weight = sum(item["weight"] for item in available if item["signal"] == (1 if direction == "BULLISH" else -1 if direction == "BEARISH" else 0))
    agreement = 100 * directional_weight / total_weight if total_weight else 0
    return {
        "name": name,
        "direction": direction,
        "score": round(score, 1),
        "agreement": round(agreement, 1),
        "available_components": len(available),
        "components": components,
    }


def _confirmed_swings(frame, width=3, limit=6):
    highs, lows = [], []
    start = max(width, len(frame) - 120)
    for i in range(start, len(frame) - width):
        high_window = frame.High.iloc[i - width:i + width + 1]
        low_window = frame.Low.iloc[i - width:i + width + 1]
        if frame.High.iloc[i] == high_window.max():
            highs.append({"timestamp": int(frame.index[i].timestamp() * 1000), "price": float(frame.High.iloc[i])})
        if frame.Low.iloc[i] == low_window.min():
            lows.append({"timestamp": int(frame.index[i].timestamp() * 1000), "price": float(frame.Low.iloc[i])})
    return {"highs": highs[-limit:], "lows": lows[-limit:]}


def technical_scoring_context(technical, policy, *, for_ai=False):
    """Give volume one direction budget when a usable Quant block is active."""
    if not technical or policy.get("decision_weights_pct", {}).get("quantitative", 0) <= 0:
        return technical
    result = dict(technical)
    groups = technical.get("indicator_groups") or {}
    if "trend_structure" in groups and "momentum_participation" in groups:
        momentum = _score_group("Momentum & Preisbewegung", [
            component for component in groups["momentum_participation"]["components"]
            if component["name"] not in ("VWAP", "OBV/CMF/MFI")])
        trend = groups["trend_structure"]
        if trend["direction"] == momentum["direction"] and trend["direction"] != "NEUTRAL":
            comparison = "CONFIRMED_" + trend["direction"]
        elif trend["direction"] != "NEUTRAL" and momentum["direction"] != "NEUTRAL":
            comparison = "CONFLICT"
        else:
            comparison = "PARTIAL_OR_NEUTRAL"
        result["indicator_groups"] = {
            "trend_structure": trend, "momentum_participation": momentum, "comparison": comparison}
    result["direction_partition"] = "price_groups_plus_separate_quant_volume"
    if for_ai:
        result.pop("volume", None)
        result.pop("vwap", None)
        result["recent_price_action"] = [
            {key: value for key, value in candle.items() if key != "volume"}
            for candle in result.get("recent_price_action", [])]
    return result


def _fair_value_gaps(frame, limit=6):
    gaps = []
    start = max(2, len(frame) - 100)
    for i in range(start, len(frame)):
        if frame.Low.iloc[i] > frame.High.iloc[i - 2]:
            lower, upper, kind = float(frame.High.iloc[i - 2]), float(frame.Low.iloc[i]), "BULLISH"
            filled = bool((frame.Low.iloc[i + 1:] <= lower).any())
        elif frame.High.iloc[i] < frame.Low.iloc[i - 2]:
            lower, upper, kind = float(frame.High.iloc[i]), float(frame.Low.iloc[i - 2]), "BEARISH"
            filled = bool((frame.High.iloc[i + 1:] >= upper).any())
        else:
            continue
        impulse = frame.iloc[i - 1]
        body = abs(float(impulse.Close) - float(impulse.Open))
        spread = float(impulse.High) - float(impulse.Low)
        impulse_atr = _number(impulse.get("ATR"))
        aligned = (impulse.Close > impulse.Open) if kind == "BULLISH" else (impulse.Close < impulse.Open)
        gaps.append({"type": kind, "lower": lower, "upper": upper,
                     "created_at": int(frame.index[i].timestamp() * 1000), "filled": filled,
                     "gap_size": upper - lower,
                     "impulse_body": body,
                     "impulse_body_atr": body / impulse_atr if impulse_atr and impulse_atr > 0 else 0,
                     "impulse_body_ratio": body / spread if spread > 0 else 0,
                     "impulse_aligned": bool(aligned),
                     "impulse_at": int(frame.index[i - 1].timestamp() * 1000)})
    active = [gap for gap in gaps if not gap["filled"]]
    return active[-limit:]


def _order_blocks(frame, limit=4):
    blocks = []
    seen = set()
    start = max(20, len(frame) - 100)
    for i in range(start, len(frame)):
        bullish_break = pd.notna(frame.RESISTANCE.iloc[i]) and frame.Close.iloc[i] > frame.RESISTANCE.iloc[i]
        bearish_break = pd.notna(frame.SUPPORT.iloc[i]) and frame.Close.iloc[i] < frame.SUPPORT.iloc[i]
        if not bullish_break and not bearish_break:
            continue
        candidates = frame.iloc[max(0, i - 10):i]
        if bullish_break:
            opposite = candidates[candidates.Close < candidates.Open]
            kind = "BULLISH"
        else:
            opposite = candidates[candidates.Close > candidates.Open]
            kind = "BEARISH"
        if opposite.empty:
            continue
        candle = opposite.iloc[-1]
        created_index = opposite.index[-1]
        key = (kind, created_index)
        if key in seen:
            continue
        seen.add(key)
        lower, upper = float(candle.Low), float(candle.High)
        after = frame.loc[created_index:].iloc[1:]
        invalidated = bool((after.Close < lower).any()) if kind == "BULLISH" else bool((after.Close > upper).any())
        blocks.append({"type": kind, "lower": lower, "upper": upper, "created_at": int(created_index.timestamp() * 1000), "invalidated": invalidated, "method": "heuristic"})
    return [block for block in blocks if not block["invalidated"]][-limit:]


def _volume_profile(frame, bins=24):
    sample = frame.tail(120)
    if not _volume_known(sample).all():
        return {"poc": None, "value_area_low": None, "value_area_high": None, "method": "unavailable: missing volume"}
    volume = sample.Volume.fillna(0).clip(lower=0)
    if sample.empty or volume.sum() <= 0 or sample.High.max() < sample.Low.min():
        return {"poc": None, "value_area_low": None, "value_area_high": None, "method": "unavailable"}
    minimum, maximum = float(sample.Low.min()), float(sample.High.max())
    if minimum == maximum:
        return {"poc": minimum, "value_area_low": minimum, "value_area_high": maximum,
                "method": "120-bar typical-price volume histogram; contiguous 70% area", "approximate": True}
    typical = ((sample.High + sample.Low + sample.Close) / 3).to_numpy()
    weights = volume.to_numpy()
    # NumPy otherwise invents a +/-0.5 price range for constant typical prices.
    histogram, edges = np.histogram(typical, bins=bins, range=(minimum, maximum), weights=weights)
    centers = (edges[:-1] + edges[1:]) / 2
    poc_index = int(np.argmax(histogram))
    lower = upper = poc_index
    accumulated = histogram[poc_index]
    target_volume = histogram.sum() * 0.70
    # Grow a contiguous area around POC, never jump to a detached distant peak.
    # Whole bins may exceed 70%; the returned range and included volume agree.
    while accumulated < target_volume and (lower > 0 or upper < len(histogram) - 1):
        below = histogram[lower - 1] if lower > 0 else -1
        above = histogram[upper + 1] if upper < len(histogram) - 1 else -1
        add_above = above > below or (above == below and upper - poc_index <= poc_index - lower)
        if add_above:
            upper += 1
            accumulated += histogram[upper]
        else:
            lower -= 1
            accumulated += histogram[lower]
    return {
        "poc": round(float(centers[poc_index]), 8),
        "value_area_low": round(float(edges[lower]), 8),
        "value_area_high": round(float(edges[upper + 1]), 8),
        "method": "120-bar typical-price volume histogram; contiguous 70% area",
        "approximate": True,
    }


def _market_structure(frame, row):
    swings = _confirmed_swings(frame)
    swing_bias = "NEUTRAL"
    if len(swings["highs"]) >= 2 and len(swings["lows"]) >= 2:
        higher_high = swings["highs"][-1]["price"] > swings["highs"][-2]["price"]
        higher_low = swings["lows"][-1]["price"] > swings["lows"][-2]["price"]
        lower_high = swings["highs"][-1]["price"] < swings["highs"][-2]["price"]
        lower_low = swings["lows"][-1]["price"] < swings["lows"][-2]["price"]
        swing_bias = "BULLISH" if higher_high and higher_low else "BEARISH" if lower_high and lower_low else "MIXED"
    close = float(row.Close)
    bullish_break = pd.notna(row.RESISTANCE) and close > row.RESISTANCE
    bearish_break = pd.notna(row.SUPPORT) and close < row.SUPPORT
    bullish_sweep = pd.notna(row.SUPPORT) and row.Low < row.SUPPORT < close
    bearish_sweep = pd.notna(row.RESISTANCE) and row.High > row.RESISTANCE > close
    if bullish_break:
        event, bias = "BOS_BULLISH", "BULLISH"
    elif bearish_break:
        event, bias = "BOS_BEARISH", "BEARISH"
    elif bullish_sweep and bearish_sweep:
        event, bias = "LIQUIDITY_SWEEP_BOTH", "NEUTRAL"
    elif bullish_sweep:
        event, bias = "LIQUIDITY_SWEEP_BULLISH", "BULLISH"
    elif bearish_sweep:
        event, bias = "LIQUIDITY_SWEEP_BEARISH", "BEARISH"
    else:
        event = "NONE"
        bias = swing_bias if swing_bias in ("BULLISH", "BEARISH") else "NEUTRAL"
    return {
        "bias": bias,
        "swing_structure": swing_bias,
        "event": event,
        "swings": swings,
        "fair_value_gaps": _fair_value_gaps(frame),
        "order_blocks": _order_blocks(frame),
    }


def _recent_candles(frame, limit=20):
    return [{
        "timestamp": int(index.timestamp() * 1000),
        "open": round(float(row.Open), 8),
        "high": round(float(row.High), 8),
        "low": round(float(row.Low), 8),
        "close": round(float(row.Close), 8),
        "volume": round(float(row.Volume), 4),
    } for index, row in frame.tail(limit).iterrows()]


def technical_at(frame, index=-1):
    visible = frame if index == -1 else frame.iloc[:index + 1]
    row = visible.iloc[-1]
    if any(pd.isna(row[key]) for key in ("EMA50", "ATR", "RSI", "MACD_signal")):
        return {"error": f"Mindestens {MIN_ANALYSIS_BARS} abgeschlossene Kerzen erforderlich."}
    price = float(row.Close)
    ratio = float(row.Volume / row.VOLUME_AVG) if bool(row.get("VolumeKnown", pd.notna(row.Volume))) and row.VOLUME_AVG > 0 else None
    breakout = "NONE"
    if ratio is not None and ratio > 1.2:
        if price > row.RESISTANCE:
            breakout = "BULLISH_BREAKOUT"
        elif price < row.SUPPORT:
            breakout = "BEARISH_BREAKDOWN"
    momentum = "STRONG_UP" if row.MOMENTUM > 0.5 else "STRONG_DOWN" if row.MOMENTUM < -0.5 else "FLAT"
    trend = "BULLISH" if price > row.EMA20 > row.EMA50 else "BEARISH" if price < row.EMA20 < row.EMA50 else "NEUTRAL_CONSOLIDATING"
    structure = _market_structure(visible, row)
    volume_profile = _volume_profile(visible)

    cloud_top = max(row.ICHIMOKU_SPAN_A, row.ICHIMOKU_SPAN_B) if pd.notna(row.ICHIMOKU_SPAN_A) and pd.notna(row.ICHIMOKU_SPAN_B) else np.nan
    cloud_bottom = min(row.ICHIMOKU_SPAN_A, row.ICHIMOKU_SPAN_B) if pd.notna(row.ICHIMOKU_SPAN_A) and pd.notna(row.ICHIMOKU_SPAN_B) else np.nan
    ema_signal = 1 if price > row.EMA20 > row.EMA50 else -1 if price < row.EMA20 < row.EMA50 else 0
    sma_signal = 1 if price > row.SMA20 > row.SMA50 else -1 if price < row.SMA20 < row.SMA50 else 0
    ichimoku_signal = 1 if pd.notna(cloud_top) and price > cloud_top and row.ICHIMOKU_CONVERSION > row.ICHIMOKU_BASE else -1 if pd.notna(cloud_bottom) and price < cloud_bottom and row.ICHIMOKU_CONVERSION < row.ICHIMOKU_BASE else 0
    adx_signal = (_signal(_number(row.PLUS_DI - row.MINUS_DI), lambda value: value > 0, lambda value: value < 0)
                  if _number(row.ADX) is not None and row.ADX >= 18 else 0)
    supertrend_direction = _number(row.SUPERTREND_DIRECTION)
    supertrend_signal = (int(supertrend_direction) if supertrend_direction in (-1, 1)
                         and _number(row.SUPERTREND_LINE) is not None and row.ATR > 0 else 0)
    structure_signal = 1 if structure["bias"] == "BULLISH" else -1 if structure["bias"] == "BEARISH" else 0
    trend_group = _score_group("Trend & Marktstruktur", [
        _component("EMA-Trend", ema_signal, _number(row.EMA20), 1.2, f"Preis/EMA20/EMA50: {ema_signal}"),
        _component("SMA-Trend", sma_signal, _number(row.SMA20), 0.7, f"Preis/SMA20/SMA50: {sma_signal}"),
        _component("Ichimoku", ichimoku_signal, _number(cloud_top), 1.0, "Cloud und Conversion/Base"),
        _component("Supertrend", supertrend_signal,
                   _number(row.SUPERTREND_LINE) if supertrend_direction is not None else None, 1.0),
        _component("ADX/DMI", adx_signal, _number(row.ADX), 1.0, f"+DI {_number(row.PLUS_DI)}, -DI {_number(row.MINUS_DI)}"),
        _component("BOS/Swings/Liquidity", structure_signal, structure["event"], 1.3, structure["swing_structure"]),
    ])

    obv_slope = (_number(row.OBV - visible.OBV.iloc[-min(10, len(visible))])
                 if len(visible) > 1 and _volume_known(visible.tail(10)).all() else None)
    flow_votes = [
        _signal(_number(obv_slope), lambda value: value > 0, lambda value: value < 0),
        _signal(_number(row.CMF20), lambda value: value > 0.05, lambda value: value < -0.05),
        _signal(_number(row.MFI14), lambda value: value > 55, lambda value: value < 45),
    ]
    flow_signal = 1 if sum(flow_votes) >= 2 else -1 if sum(flow_votes) <= -2 else 0
    stochastic_signal = 1 if pd.notna(row.STOCH_RSI_K) and pd.notna(row.STOCH_RSI_D) and row.STOCH_RSI_K > row.STOCH_RSI_D and row.STOCH_RSI_K > 50 else -1 if pd.notna(row.STOCH_RSI_K) and pd.notna(row.STOCH_RSI_D) and row.STOCH_RSI_K < row.STOCH_RSI_D and row.STOCH_RSI_K < 50 else 0
    momentum_group = _score_group("Momentum & Marktteilnahme", [
        _component("RSI", _signal(_number(row.RSI), lambda value: value > 55, lambda value: value < 45), _number(row.RSI), 0.9),
        _component("Stochastic RSI", stochastic_signal, _number(row.STOCH_RSI_K), 0.7, f"D {_number(row.STOCH_RSI_D)}"),
        _component("MACD", _signal(_number(row.MACD_hist), lambda value: value > 0, lambda value: value < 0), _number(row.MACD_hist), 1.1),
        _component("ROC", _signal(_number(row.ROC12), lambda value: value > 0.2, lambda value: value < -0.2), _number(row.ROC12), 0.8),
        _component("CCI", _signal(_number(row.CCI20), lambda value: value > 50, lambda value: value < -50), _number(row.CCI20), 0.7),
        _component("Williams %R", _signal(_number(row.WILLIAMS_R), lambda value: value > -40, lambda value: value < -60), _number(row.WILLIAMS_R), 0.6),
        _component("VWAP", _signal(_number(row.VWAP), lambda value: price > value, lambda value: price < value), _number(row.VWAP), 1.0),
        _component("OBV/CMF/MFI", flow_signal, _number(row.CMF20), 1.2, f"OBV-Slope {obv_slope}, MFI {_number(row.MFI14)}"),
    ])
    if trend_group["direction"] == momentum_group["direction"] and trend_group["direction"] != "NEUTRAL":
        group_comparison = f"CONFIRMED_{trend_group['direction']}"
    elif trend_group["direction"] != "NEUTRAL" and momentum_group["direction"] != "NEUTRAL":
        group_comparison = "CONFLICT"
    else:
        group_comparison = "PARTIAL_OR_NEUTRAL"

    count = len(visible)
    warnings = [] if count >= 200 else ["EMA/SMA100 und 200 sind erst mit ausreichender Historie vollständig."]
    if not visible.Volume.tail(20).fillna(0).gt(0).any():
        warnings.append("Kein nutzbares Volumen im aktuellen Fenster: VWAP und Volumenindikatoren sind eingeschränkt.")
    elif not _volume_known(visible.tail(20)).all():
        warnings.append("Volumenmessungen fehlen: betroffene Volumenindikatoren werden nicht als vollständige Bestätigung gewertet.")
    elif visible.attrs.get("timeframe") == "1D":
        warnings.append("Session-VWAP ist aus Tageskerzen nicht verfügbar; vorhandene Volumenindikatoren bleiben nutzbar.")
    elif pd.isna(row.VWAP):
        warnings.append("Für die aktuelle Sitzung ist noch kein Session-VWAP verfügbar.")
    if pd.isna(row.ICHIMOKU_SPAN_B):
        warnings.append("Ichimoku-Cloud benötigt mindestens 78 Kerzen.")
    result = {
        "indicator_version": INDICATOR_VERSION,
        "latest_close": price,
        "ema": {str(period): _number(row[f"EMA{period}"]) for period in (9, 20, 50, 100, 200)},
        "sma": {str(period): _number(row[f"SMA{period}"]) for period in (9, 20, 50, 100, 200)},
        "rsi": _number(row.RSI),
        "stochastic_rsi": {"k": _number(row.STOCH_RSI_K), "d": _number(row.STOCH_RSI_D)},
        "macd": {name: _number(row[f"MACD_{name}"]) for name in ("macd", "signal", "hist")},
        "roc_12": _number(row.ROC12),
        "cci_20": _number(row.CCI20),
        "williams_r": _number(row.WILLIAMS_R),
        "bollinger": {name: _number(row[f"BB_{name}"]) for name in ("mid", "upper", "lower")},
        "keltner": {"mid": _number(row.KELTNER_MID), "upper": _number(row.KELTNER_UPPER), "lower": _number(row.KELTNER_LOWER)},
        "ichimoku": {"conversion": _number(row.ICHIMOKU_CONVERSION), "base": _number(row.ICHIMOKU_BASE), "span_a": _number(row.ICHIMOKU_SPAN_A), "span_b": _number(row.ICHIMOKU_SPAN_B)},
        "supertrend": {"line": _number(row.SUPERTREND_LINE), "direction": "BULLISH" if supertrend_signal > 0 else "BEARISH" if supertrend_signal < 0 else "NEUTRAL"},
        "adx": {"adx": _number(row.ADX), "plus_di": _number(row.PLUS_DI), "minus_di": _number(row.MINUS_DI)},
        "vwap": _number(row.VWAP),
        "atr": _number(row.ATR),
        "atr_percent": round(float(row.ATR / price * 100), 4),
        "realized_volatility_20": _number(row.REALIZED_VOL_20),
        "volume": {"current": _number(row.Volume), "sma20": _number(row.VOLUME_AVG), "ratio": ratio, "obv": _number(row.OBV), "cmf_20": _number(row.CMF20), "mfi_14": _number(row.MFI14)},
        "volume_profile": volume_profile,
        "support": _number(row.SUPPORT),
        "resistance": _number(row.RESISTANCE),
        "local_support": _number(row.LOCAL_SUPPORT),
        "local_resistance": _number(row.LOCAL_RESISTANCE),
        "market_structure": structure,
        "indicator_groups": {"trend_structure": trend_group, "momentum_participation": momentum_group, "comparison": group_comparison},
        "risk_context": {
            "atr": _number(row.ATR),
            "atr_percent": round(float(row.ATR / price * 100), 4),
            "bollinger": {name: _number(row[f"BB_{name}"]) for name in ("mid", "upper", "lower")},
            "keltner": {"mid": _number(row.KELTNER_MID), "upper": _number(row.KELTNER_UPPER), "lower": _number(row.KELTNER_LOWER)},
            "support": _number(row.SUPPORT),
            "resistance": _number(row.RESISTANCE),
            "local_support": _number(row.LOCAL_SUPPORT),
            "local_resistance": _number(row.LOCAL_RESISTANCE),
            "volume_profile": volume_profile,
            "fair_value_gaps": structure["fair_value_gaps"],
            "order_blocks": structure["order_blocks"],
        },
        "recent_price_action": _recent_candles(visible),
        "trend": trend,
        "momentum": momentum,
        "breakout": breakout,
        "bars": count,
        "warmup_complete": count >= 200,
        "warnings": warnings,
    }
    return result


def run_technical_analysis(df):
    if len(df) < MIN_ANALYSIS_BARS:
        return {"error": f"Mindestens {MIN_ANALYSIS_BARS} abgeschlossene Kerzen erforderlich."}
    return technical_at(build_indicator_frame(df))
