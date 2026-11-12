"""Causal, scale-free OHLCV features; targets are not trading success probabilities."""
import hashlib
import json

import numpy as np
import pandas as pd

VERSION = "2.0-calibrated-direction-3bar"
HORIZON = 3
ROUND_TRIP_COST = 0.0022  # Two sides: 0.05% commission + 0.05% slippage + 0.01% half-spread.
FEATURES = ["return_1", "return_3", "return_6", "return_12", "return_24",
            "ema_12_gap", "ema_48_gap", "rsi_14", "atr_pct", "range_pct",
            "body_pct", "close_location", "volatility_12", "volatility_48",
            "volume_ratio", "signed_volume_12", "minute_sin", "minute_cos"]


def validate(frame, timeframe):
    if timeframe not in ("5m", "15m") or frame.attrs.get("timeframe") != timeframe:
        raise ValueError("LightGBM unterstützt passende 5m- oder 15m-Kerzen.")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("Zeitstempel mit Zeitzone erforderlich.")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("Kerzen müssen eindeutig und chronologisch sein.")
    values = frame[["Open", "High", "Low", "Close"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Ungültige OHLC-Kurse.")
    if ((frame.High < frame[["Open", "Close", "Low"]].max(axis=1)) |
            (frame.Low > frame[["Open", "Close", "High"]].min(axis=1))).any():
        raise ValueError("Ungültige Kerzengeometrie.")


def features(frame):
    """Only backward windows. Missing volume stays missing, never fabricated."""
    close, previous = frame.Close, frame.Close.shift()
    result = pd.DataFrame(index=frame.index)
    for window in (1, 3, 6, 12, 24):
        result[f"return_{window}"] = close / close.shift(window) - 1
    for window in (12, 48):
        result[f"ema_{window}_gap"] = close / close.ewm(span=window, adjust=False, min_periods=window).mean() - 1
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    result["rsi_14"] = (gain / (gain + loss).replace(0, np.nan)).fillna(0.5)
    tr = pd.concat([frame.High - frame.Low, (frame.High - previous).abs(),
                    (frame.Low - previous).abs()], axis=1).max(axis=1)
    result["atr_pct"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean() / close
    result["range_pct"] = (frame.High - frame.Low) / close
    result["body_pct"] = (close - frame.Open) / frame.Open
    result["close_location"] = ((close - frame.Low) / (frame.High - frame.Low).replace(0, np.nan)).fillna(0.5)
    returns = np.log(close / previous)
    for window in (12, 48):
        result[f"volatility_{window}"] = returns.rolling(window).std()
    volume = pd.to_numeric(frame.Volume, errors="coerce").where(lambda x: x >= 0)
    if "VolumeKnown" in frame:
        volume = volume.where(frame.VolumeKnown.fillna(False).astype(bool))
    result["volume_ratio"] = volume / volume.rolling(24).mean().replace(0, np.nan)
    result["signed_volume_12"] = (np.sign(delta) * volume).rolling(12).sum() / volume.rolling(12).sum().replace(0, np.nan)
    local = frame.index.tz_convert(frame.attrs.get("exchange_timezone", "UTC"))
    angle = 2 * np.pi * (local.hour * 60 + local.minute) / 1440
    result["minute_sin"], result["minute_cos"] = np.sin(angle), np.cos(angle)
    result = result[FEATURES].replace([np.inf, -np.inf], np.nan)
    result.iloc[:48] = np.nan
    return result


def dataset(frame, timeframe):
    validate(frame, timeframe)
    x = features(frame)
    entry, exit_price = frame.Open.shift(-1), frame.Close.shift(-HORIZON)
    change = exit_price / entry - 1
    labels = pd.Series(np.where(change > ROUND_TRIP_COST, 2,
                      np.where(change < -ROUND_TRIP_COST, 0, 1)), index=frame.index)
    step = pd.Timedelta(minutes=5 if timeframe == "5m" else 15)
    # Target starts at NEXT open and ends after three full candles. No overnight/gap labels.
    contiguous = pd.Series(True, index=frame.index)
    times = frame.index.to_series()
    for offset in range(1, HORIZON + 1):
        contiguous &= times.shift(-offset).eq(times + offset * step)
    label_end = times.shift(-HORIZON) + step
    ready = x.return_24.notna() & x.ema_48_gap.notna() & x.volatility_48.notna() & contiguous & change.notna()
    return x.loc[ready], labels.loc[ready].astype(int), label_end.loc[ready]


def chronological_split(x, y, label_end, timezone="UTC"):
    days = np.asarray(x.index.tz_convert(timezone).date)
    unique = np.unique(days)
    if len(unique) < 30 or len(x) < 600:
        raise ValueError(f"Noch zu wenig Historie: {len(x)} Beispiele / {len(unique)} Handelstage. Benötigt: mindestens 600 / 30.")
    validation_day, test_day = unique[int(len(unique) * .6)], unique[int(len(unique) * .8)]
    validation_start = x.index[np.flatnonzero(days >= validation_day)[0]]
    test_start = x.index[np.flatnonzero(days >= test_day)[0]]
    masks = [(days < validation_day) & (label_end < validation_start).to_numpy(),
             (days >= validation_day) & (days < test_day) & (label_end < test_start).to_numpy(),
             days >= test_day]
    parts = [(x.loc[mask], y.loc[mask]) for mask in masks]
    if any(len(part[0]) < minimum for part, minimum in zip(parts, (300, 100, 100))):
        raise ValueError("Nach zeitlicher Trennung fehlen Trainings-/Validierungs-/Testbeispiele (300/100/100).")
    if any(int((parts[0][1] == label).sum()) < 20 for label in range(3)):
        raise ValueError("Zu wenige Trainingsbeispiele je Klasse (Aufwärts/Seitwärts/Abwärts; mindestens je 20).")
    return parts


def fingerprint(x, y, label_end):
    digest = hashlib.sha256(VERSION.encode())
    for value in (x, y, label_end):
        digest.update(pd.util.hash_pandas_object(value, index=True).values.tobytes())
    digest.update(json.dumps(FEATURES).encode())
    return digest.hexdigest()


def calibration_split(x, y, label_end, timezone="UTC"):
    """Split the original validation period by whole days; preserve the final test."""
    train, (x_val, y_val), test = chronological_split(x, y, label_end, timezone)
    days = np.asarray(x_val.index.tz_convert(timezone).date)
    unique = np.unique(days)
    boundary = x_val.index[np.flatnonzero(days >= unique[len(unique) // 2])[0]]
    early = (x_val.index < boundary) & (label_end.loc[x_val.index] < boundary).to_numpy()
    calibration = x_val.index >= boundary
    validation = (x_val.loc[early], y_val.loc[early])
    calibrate = (x_val.loc[calibration], y_val.loc[calibration])
    if min(len(validation[0]), len(calibrate[0])) < 50:
        raise ValueError("Zu wenig getrennte Validierungs-/Kalibrierungsdaten (je mindestens 50).")
    return train, validation, calibrate, test


def calibrate(probabilities, temperature=1.0):
    """Conservative temperature scaling: never sharpen the model's confidence."""
    p = np.asarray(probabilities, dtype=float)
    if (p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all()
            or (p < 0).any() or not np.allclose(p.sum(axis=1), 1, atol=.001)
            or not np.isfinite(temperature) or not 1 <= temperature <= 4):
        raise ValueError("Ungültige Modellwahrscheinlichkeiten oder Kalibrierung.")
    logits = np.log(np.clip(p, 1e-12, 1)) / temperature
    logits -= logits.max(axis=1, keepdims=True)
    result = np.exp(logits)
    return result / result.sum(axis=1, keepdims=True)


def fit_temperature(probabilities, labels):
    labels = np.asarray(labels, dtype=int)
    # Fixed small grid on separate calibration data; no final-test optimization.
    candidates = np.linspace(1, 4, 31)
    losses = [-np.log(calibrate(probabilities, t)[np.arange(len(labels)), labels]).mean()
              for t in candidates]
    return float(candidates[int(np.argmin(losses))])


def nonoverlapping_positions(index, label_end):
    """Select before inspecting predictions/labels; future return windows do not overlap."""
    positions, previous_end = [], None
    for pos, at in enumerate(index):
        if previous_end is None or at >= previous_end:
            positions.append(pos)
            previous_end = label_end.loc[at]
    return np.asarray(positions, dtype=int)


def metrics(probabilities, labels, prior):
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-9, 1 - 1e-9)
    labels = np.asarray(labels, dtype=int)
    predicted = p.argmax(axis=1)
    loss = -np.log(p[np.arange(len(labels)), labels]).mean()
    baseline = -np.log(prior[labels]).mean()
    majority_accuracy = float((labels == np.argmax(prior)).mean())
    balanced = np.mean([(predicted[labels == k] == k).mean() for k in range(3) if (labels == k).any()])
    directional = (predicted != 1) & (p.max(axis=1) >= .6)
    return {"samples": len(labels), "log_loss": round(float(loss), 6),
            "baseline_log_loss": round(float(baseline), 6),
            "accuracy": round(float((predicted == labels).mean()), 4),
            "baseline_accuracy": round(majority_accuracy, 4), "balanced_accuracy": round(float(balanced), 4),
            "brier_score": round(float(np.square(p - np.eye(3)[labels]).sum(axis=1).mean()), 6),
            "confident_directional_samples": int(directional.sum()),
            "confident_directional_accuracy": round(float((predicted[directional] == labels[directional]).mean()), 4) if directional.any() else None}


def qualifies(result):
    return (result["log_loss"] < .98 * result["baseline_log_loss"]
            and result["accuracy"] > result["baseline_accuracy"] + .02
            and result["balanced_accuracy"] > .36
            and result["confident_directional_samples"] >= 20
            and (result["confident_directional_accuracy"] or 0) >= .55)
