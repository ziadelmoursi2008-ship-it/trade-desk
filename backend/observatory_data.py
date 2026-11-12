"""Read-only presentation of saved data for the backend's 3D observatory.

Opening the observatory never requests market data, evaluates strategies, starts
training, or changes the selected analysis mode or background settings.
"""
import json
import math
import time
from contextlib import closing

from database import get_connection
from instruments import CATALOG


def _decode(value):
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, ValueError):
        return {}


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def snapshot():
    instruments = {item["symbol"]: {**item, "price": None, "price_at": None,
                   "currency": None, "bars": [], "states": []} for item in CATALOG}
    events = []
    with closing(get_connection()) as connection:
        # Enforced by SQLite as well as by the SELECT-only queries below.
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        for symbol, item in instruments.items():
            series = []
            for timeframe in ("1m", "5m", "15m", "1D"):
                rows = [dict(row) for row in connection.execute(
                    "SELECT bar_at,bar_end,close,currency FROM background_price_bars "
                    "WHERE ticker=? AND timeframe=? ORDER BY bar_at DESC LIMIT 64", (symbol, timeframe))]
                if rows:
                    series.append((rows[0]["bar_end"], timeframe, rows))
            if series:
                _, timeframe, rows = max(series, key=lambda series: series[0])
                item.update(price=_number(rows[0]["close"]), price_at=rows[0]["bar_end"],
                            currency=rows[0]["currency"], chart_timeframe=timeframe,
                            bars=[{"at": row["bar_at"], "close": _number(row["close"])} for row in reversed(rows)])

        for table, profile in (("signal_states", "gemini"), ("technical_signal_states", "python"),
                               ("background_signal_states", "background")):
            for row in connection.execute(f"SELECT ticker,timeframe,state_json,updated_at FROM {table}"):
                if row["ticker"] not in instruments:
                    continue
                state = _decode(row["state_json"])
                plan = state.get("plan") or {}
                instruments[row["ticker"]]["states"].append({
                    "profile": profile, "timeframe": row["timeframe"], "updated_at": row["updated_at"],
                    "direction": state.get("direction", "NEUTRAL"), "status": state.get("status"),
                    "confidence": _number(state.get("confidence")), "reason": state.get("reason", ""),
                    "verification": state.get("verification_status", "unavailable"),
                    "entry": _number(plan.get("entry_price")), "target": _number(plan.get("take_profit_1")),
                    "stop": _number(plan.get("stop_loss")),
                })
                item = instruments[row["ticker"]]
                if (item["price"] is None or (item.get("price_source") == "saved_analysis"
                                             and row["updated_at"] > item["price_at"])):
                    instruments[row["ticker"]].update(
                        price=_number(state.get("current_price", plan.get("current_price"))),
                        price_at=row["updated_at"], price_source="saved_analysis")

        for table, profile in (("signal_events", "gemini"), ("technical_signal_events", "python"),
                               ("background_signal_events", "background")):
            for row in connection.execute(
                f"SELECT id,ticker,timeframe,event_type,created_at,state_json FROM {table} ORDER BY id DESC LIMIT 24"
            ):
                state = _decode(row["state_json"])
                events.append({"id": f"{table}:{row['id']}", "ticker": row["ticker"],
                               "timeframe": row["timeframe"], "kind": row["event_type"],
                               "at": row["created_at"], "profile": profile,
                               "direction": state.get("direction", "NEUTRAL"),
                               "message": state.get("reason", "")})
        for row in connection.execute(
            "SELECT id,ticker,timeframe,profile,status,started_at,finished_at,result_json "
            "FROM background_runs ORDER BY started_at DESC LIMIT 24"
        ):
            result = _decode(row["result_json"])
            events.append({"id": f"run:{row['id']}:{row['status']}", "ticker": row["ticker"],
                           "timeframe": row["timeframe"], "kind": row["status"],
                           "at": int((row["finished_at"] or row["started_at"]) * 1000),
                           "profile": row["profile"], "direction": result.get("signal", "NEUTRAL"),
                           "message": result.get("message", "")})
        for row in connection.execute(
            "SELECT ticker,headline,analyzed_at FROM news_items WHERE analyzed_at IS NOT NULL "
            "ORDER BY analyzed_at DESC LIMIT 12"
        ):
            events.append({"id": f"news:{row['ticker']}:{row['analyzed_at']}:{row['headline']}",
                           "ticker": row["ticker"], "kind": "NEWS", "at": int(row["analyzed_at"] * 1000),
                           "profile": "news", "direction": "NEUTRAL", "message": row["headline"]})

    for item in instruments.values():
        item["states"].sort(key=lambda state: state["updated_at"], reverse=True)
    return {"observed_at": int(time.time() * 1000), "instruments": list(instruments.values()),
            "events": sorted(events, key=lambda event: event["at"], reverse=True)[:48],
            "source": "saved_backend_data", "poll_interval_ms": 5000}
