"""Explicit, persistent opt-in and bounded background Gemini request attempts."""
from contextlib import closing
from datetime import datetime, timezone
import json
import time

from analysis_modes import ModeChangedError
from database import get_connection
from instruments import CATALOG

STOCKS = {item["symbol"] for item in CATALOG if item["type"] == "EQUITY"}
DEFAULTS = {"enabled": False, "symbols": ["NVDA"], "timeframes": ["5m", "15m"],
            "python_enabled": True, "gemini_enabled": True, "api_hour_limit": 48,
            "api_day_limit": 256, "report_interval_minutes": 60, "revision": 0}


class BudgetExceeded(RuntimeError):
    pass


def settings():
    with closing(get_connection()) as connection:
        row = connection.execute("SELECT value FROM app_settings WHERE key='background_settings'").fetchone()
    return {**DEFAULTS, **json.loads(row["value"])} if row else {**DEFAULTS, "symbols": list(DEFAULTS["symbols"]), "timeframes": list(DEFAULTS["timeframes"])}


def save_settings(values):
    required = set(DEFAULTS) - {"revision"}
    if set(values) != required:
        raise ValueError("Unvollständige Hintergrund-Einstellungen.")
    if any(type(values[key]) is not bool for key in ("enabled", "python_enabled", "gemini_enabled")):
        raise ValueError("Ungültiger Ein-/Aus-Wert.")
    if (not isinstance(values["symbols"], list) or not 1 <= len(values["symbols"]) <= 10
            or not set(values["symbols"]) <= STOCKS or len(set(values["symbols"])) != len(values["symbols"])):
        raise ValueError("Eine bis zehn eindeutige Katalog-Aktien wählen.")
    if not isinstance(values["timeframes"], list) or not values["timeframes"] or not set(values["timeframes"]) <= {"5m", "15m"} or len(set(values["timeframes"])) != len(values["timeframes"]):
        raise ValueError("5m und/oder 15m wählen.")
    for key, low, high in (("api_hour_limit", 1, 500), ("api_day_limit", 1, 5000), ("report_interval_minutes", 30, 1440)):
        if type(values[key]) is not int or not low <= values[key] <= high:
            raise ValueError(f"{key} muss zwischen {low} und {high} liegen.")
    if not values["python_enabled"] and not values["gemini_enabled"]:
        raise ValueError("Mindestens ein Analyseprofil einschalten.")
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM app_settings WHERE key='background_settings'").fetchone()
        revision = json.loads(row["value"])["revision"] + 1 if row else 1
        result = {**values, "revision": revision}
        connection.execute("INSERT INTO app_settings(key,value) VALUES('background_settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (json.dumps(result),))
    return result


def ensure_active(revision):
    selected = settings()
    if not selected["enabled"] or selected["revision"] != revision:
        raise ModeChangedError("Dauerbetrieb wurde ausgeschaltet oder geändert.")
    return selected


def api_usage(now=None):
    now = time.time() if now is None else now
    midnight = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with closing(get_connection()) as connection:
        hour = connection.execute("SELECT COUNT(*) FROM background_api_attempts WHERE attempted_at>?", (now-3600,)).fetchone()[0]
        day = connection.execute("SELECT COUNT(*) FROM background_api_attempts WHERE attempted_at>=?", (midnight,)).fetchone()[0]
    return {"hour": hour, "day": day, "day_basis": "UTC"}


def reserve_api_call(revision, ticker, timeframe, now=None):
    now = time.time() if now is None else now
    midnight = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM app_settings WHERE key='background_settings'").fetchone()
        selected = json.loads(row["value"]) if row else DEFAULTS
        if not selected["enabled"] or selected["revision"] != revision or not selected["gemini_enabled"]:
            raise ModeChangedError("Automatische Gemini-Analyse gestoppt.")
        hour = connection.execute("SELECT COUNT(*) FROM background_api_attempts WHERE attempted_at>?", (now-3600,)).fetchone()[0]
        day = connection.execute("SELECT COUNT(*) FROM background_api_attempts WHERE attempted_at>=?", (midnight,)).fetchone()[0]
        if hour >= selected["api_hour_limit"] or day >= selected["api_day_limit"]:
            raise BudgetExceeded("Gemini-Limit des Dauerbetriebs erreicht. Kursbeobachtung und Python laufen weiter.")
        connection.execute("INSERT INTO background_api_attempts(ticker,timeframe,attempted_at) VALUES(?,?,?)", (ticker, timeframe, now))
