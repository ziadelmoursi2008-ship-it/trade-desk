"""Yahoo OHLCV polling, bounded cache, explicit provenance and completed candles."""
import threading
import time
from collections import OrderedDict

import numpy as np
import pandas as pd
import yfinance as yf

from instruments import TIMEFRAME_MAP, instrument_name, normalize_ticker, source_ticker

_cache = OrderedDict()
_cache_lock = threading.Lock()
_fetch_locks = [threading.Lock() for _ in range(32)]
CACHE_TTL = 25


def trading_sessions(metadata):
    """Keep Yahoo's actual historical sessions, including half days, as plain timestamps."""
    try:
        periods = metadata.get("tradingPeriods")
        if not isinstance(periods, pd.DataFrame) or not {"start", "end"} <= set(periods.columns):
            return {}
        timezone = metadata.get("exchangeTimezoneName", "UTC")
        sessions = {}
        for _, row in periods.iterrows():
            start, end = pd.Timestamp(row["start"]), pd.Timestamp(row["end"])
            if pd.isna(start) or pd.isna(end) or start.tzinfo is None or end.tzinfo is None or end <= start:
                continue
            sessions[str(start.tz_convert(timezone).date())] = {
                "start": int(start.timestamp() * 1000), "end": int(end.timestamp() * 1000)}
        return sessions
    except Exception:
        # Missing schedule does not erase prices; Quant can report incomplete coverage.
        return {}


def _epoch_seconds(value):
    if value is None:
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    timestamp = pd.Timestamp(value)
    timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
    return timestamp.timestamp()


def candle_end(timestamp, timeframe, attrs):
    end = timestamp + pd.Timedelta(seconds=TIMEFRAME_MAP[timeframe]["seconds"])
    if timeframe == "4h":
        end = min(end, timestamp.normalize() + pd.Timedelta(days=1))
    regular_end = attrs.get("session_end")
    if regular_end and attrs.get("instrument_type") != "CRYPTOCURRENCY":
        if isinstance(regular_end, (int, float, np.integer, np.floating)):
            session_end = pd.Timestamp(regular_end, unit="s", tz="UTC")
        else:
            session_end = pd.Timestamp(regular_end)
            session_end = session_end.tz_localize("UTC") if session_end.tzinfo is None else session_end.tz_convert("UTC")
        session_end = session_end.tz_convert(timestamp.tz)
        if session_end.date() == timestamp.date() and session_end > timestamp:
            end = min(end, session_end)
    return end


def completed_bars(df, timeframe, now=None):
    if df.empty:
        return df.copy()
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    ends = [candle_end(timestamp, timeframe, df.attrs) for timestamp in df.index]
    result = df.loc[[end <= now for end in ends]].copy()
    result.attrs.update(df.attrs)
    return result


def fetch_stock_df(ticker, timeframe):
    ticker = normalize_ticker(ticker)
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError("Nicht unterstützter Zeitrahmen.")
    key = (ticker, timeframe)
    with _fetch_locks[hash(key) % len(_fetch_locks)]:
        with _cache_lock:
            cached = _cache.get(key)
            if cached and time.monotonic() - cached[0] < CACHE_TTL:
                _cache.move_to_end(key)
                return cached[1].copy()
        info = TIMEFRAME_MAP[timeframe]
        provider = yf.Ticker(source_ticker(ticker))
        df = provider.history(period=info["period"], interval=info["interval"], auto_adjust=True, timeout=8, raise_errors=True)
        if df.empty:
            return df
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if not all(column in df for column in ("Open", "High", "Low", "Close")):
            raise ValueError("Marktdaten enthalten keine vollständigen OHLC-Kurse.")
        df = df.copy()
        df.index = pd.DatetimeIndex(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df[~df.index.duplicated(keep="last")].sort_index()
        for column in ("Open", "High", "Low", "Close"):
            df[column] = pd.to_numeric(df[column], errors="coerce")
        df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=["Open", "High", "Low", "Close"])
        df = df.loc[(df[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]
        df = df.loc[(df.High >= df[["Open", "Close", "Low"]].max(axis=1)) & (df.Low <= df[["Open", "Close", "High"]].min(axis=1))]
        # Keep the missingness flag even though charts/legacy indicators need a numeric column.
        raw_volume = pd.to_numeric(df["Volume"], errors="coerce") if "Volume" in df else pd.Series(np.nan, index=df.index)
        df["VolumeKnown"] = raw_volume.notna() & np.isfinite(raw_volume) & raw_volume.ge(0)
        df["Volume"] = raw_volume.where(df.VolumeKnown, 0.0)
        metadata = provider.history_metadata or {}
        regular = metadata.get("currentTradingPeriod", {}).get("regular", {})
        attrs = {
            "currency": metadata.get("currency", "USD" if ticker == "XAUUSD" or ticker.endswith("-USD") else None),
            "instrument_type": metadata.get("instrumentType", ""),
            "exchange_timezone": metadata.get("exchangeTimezoneName", str(df.index.tz)),
            "session_start": regular.get("start"), "session_end": regular.get("end"),
            "source_ticker": source_ticker(ticker), "fetched_at": time.time(),
            "timeframe": timeframe,
            "trading_sessions": trading_sessions(metadata) if timeframe != "1D" else {},
        }
        if timeframe == "4h":
            # Anchor each exchange session to its first hourly candle, never across overnight gaps.
            parts = [day.resample("4h", origin=day.index[0]).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum", "VolumeKnown": "all"}).dropna()
                     for _, day in df.groupby(df.index.normalize())]
            df = pd.concat(parts) if parts else df.iloc[:0]
        df.attrs = attrs
        with _cache_lock:
            _cache[key] = (time.monotonic(), df.copy())
            while len(_cache) > 128:
                _cache.popitem(last=False)
        return df


def market_metadata(df, ticker, timeframe):
    attrs = df.attrs
    now = time.time()
    crypto = attrs.get("instrument_type") == "CRYPTOCURRENCY" or ticker.endswith("-USD")
    session_start, session_end = _epoch_seconds(attrs.get("session_start")), _epoch_seconds(attrs.get("session_end"))
    market_open = True if crypto else bool(session_start and session_end and session_start <= now < session_end)
    last = df.index[-1] if not df.empty else None
    age = max(0, now - candle_end(last, timeframe, attrs).timestamp()) if last is not None else None
    stale = age is None or age > max(900, TIMEFRAME_MAP[timeframe]["seconds"] * 3)
    return {
        "ticker": ticker, "name": instrument_name(ticker), "source_ticker": source_ticker(ticker),
        "currency": attrs.get("currency"), "exchange_timezone": attrs.get("exchange_timezone", "UTC"),
        "data_source": "Yahoo Finance · abgefragt, Verzögerung möglich",
        "is_realtime": False, "market_open": market_open, "is_stale": stale,
        "data_status": "stale" if stale and market_open else "market_closed" if not market_open else "delayed",
        "last_bar_at": int(last.timestamp() * 1000) if last is not None else None,
        "fetched_at": int(attrs.get("fetched_at", now) * 1000),
        "proxy_note": "Gold-Futures GC=F als Proxy für XAUUSD; kein Spot-XAU/USD-Kurs." if ticker == "XAUUSD" else None,
    }


def serialize_klines(df):
    return [{"timestamp": int(index.timestamp() * 1000), "open": float(row.Open), "high": float(row.High),
             "low": float(row.Low), "close": float(row.Close), "volume": float(row.Volume)}
            for index, row in df.iterrows()]
