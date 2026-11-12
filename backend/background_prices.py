"""Persist completed provider candles so browser uptime/history limits do not lose them."""
from contextlib import closing
import math
import time

import pandas as pd

from database import get_connection
from market_data import candle_end, completed_bars


def observed_closed(frame,timeframe):
    cutoff = pd.Timestamp.now(tz="UTC")
    observed = frame.attrs.get("fetched_at")
    if isinstance(observed,(int,float)) and math.isfinite(observed):
        cutoff = min(cutoff,pd.Timestamp(observed,unit="s",tz="UTC"))
    return completed_bars(frame,timeframe,now=cutoff)


def store_frame(ticker, timeframe, frame, *, revisit_all=False):
    if not isinstance(frame.index,pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("Kursarchiv benötigt eindeutige Zeitstempel mit Zeitzone.")
    closed = observed_closed(frame,timeframe)
    if closed.empty:
        return 0
    with closing(get_connection()) as connection, connection:
        previous = connection.execute("SELECT MAX(bar_at) FROM background_price_bars WHERE ticker=? AND timeframe=?", (ticker, timeframe)).fetchone()[0]
        # Revisit overlapping candles for provider corrections and fill all
        # available gaps after downtime. Historical rows are never deleted.
        selected = closed if previous is None or revisit_all else closed.loc[closed.index >= pd.Timestamp(previous, unit="ms", tz="UTC")-pd.Timedelta(days=1)]
        rows = []
        for at, row in selected.iterrows():
            ohlc = [float(row[key]) for key in ("Open", "High", "Low", "Close")]
            if not all(math.isfinite(v) and v > 0 for v in ohlc):
                continue
            if ohlc[1] < max(ohlc[0],ohlc[2],ohlc[3]) or ohlc[2] > min(ohlc[0],ohlc[1],ohlc[3]):
                continue
            volume = float(row.get("Volume", float("nan")))
            flag = row.get("VolumeKnown")
            volume_known = flag is None or (pd.notna(flag) and bool(flag))
            rows.append((ticker, timeframe, int(at.timestamp()*1000),
                         int(candle_end(at,timeframe,frame.attrs).timestamp()*1000), *ohlc,
                         volume if volume_known and math.isfinite(volume) and volume >= 0 else None,
                         frame.attrs.get("currency"), frame.attrs.get("exchange_timezone", "UTC")))
        fields = ("bar_end","open","high","low","close","volume","currency","exchange_timezone")
        updates = ",".join(f"{field}=excluded.{field}" for field in fields)
        changed = " OR ".join(f"background_price_bars.{field} IS NOT excluded.{field}" for field in fields)
        before = connection.total_changes
        connection.executemany("INSERT INTO background_price_bars(ticker,timeframe,bar_at,bar_end,open,high,low,close,volume,currency,exchange_timezone) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ticker,timeframe,bar_at) DO UPDATE SET "+updates+" WHERE "+changed,rows)
        count = connection.total_changes-before
        if timeframe in ("5m","15m"):
            now = int(time.time()*1000)
            connection.execute("""INSERT INTO lightgbm_archive_updates(ticker,timeframe,checked_at,changed_at)
                VALUES(?,?,?,?) ON CONFLICT(ticker,timeframe) DO UPDATE SET checked_at=excluded.checked_at,
                changed_at=CASE WHEN excluded.changed_at IS NOT NULL THEN excluded.changed_at ELSE lightgbm_archive_updates.changed_at END""",
                (ticker,timeframe,now,now if count else None))
    return count


def merged_frame(ticker, timeframe, frame, *, window_days=30):
    store_frame(ticker, timeframe, frame)
    with closing(get_connection()) as connection:
        rows = connection.execute("SELECT * FROM background_price_bars WHERE ticker=? AND timeframe=? AND bar_at >= (SELECT MAX(bar_at) FROM background_price_bars WHERE ticker=? AND timeframe=?)-? ORDER BY bar_at",
                                  (ticker,timeframe,ticker,timeframe,window_days*86400000)).fetchall()
    if not rows:
        return frame
    if any(row["currency"] != frame.attrs.get("currency") or row["exchange_timezone"] != frame.attrs.get("exchange_timezone", "UTC") for row in rows):
        raise ValueError("Gespeicherte Kurse haben abweichende Währung oder Zeitzone.")
    merged = pd.DataFrame({key.title():[row[key] for row in rows] for key in ("open","high","low","close","volume")},
                          index=pd.to_datetime([row["bar_at"] for row in rows],unit="ms",utc=True))
    merged.attrs = frame.attrs.copy()
    return merged
