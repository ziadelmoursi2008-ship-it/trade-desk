"""Official, structured macro-economic data without an AI search step."""

import csv
import hashlib
import io
import json
import logging
import math
import os
import sqlite3
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from database import get_connection

logger = logging.getLogger(__name__)
_cache = None
_cache_lock = threading.Lock()

BLS_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
FRED_EXPORT_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

BLS_SERIES = {
    "CUUR0000SA0": {"name": "US-Verbraucherpreise (CPI)", "category": "inflation", "unit": "Index 1982–84=100", "mode": "percent", "change_unit": "% ggü. Vormonat", "year_lag": 12},
    "CUUR0000SA0L1E": {"name": "US-Kerninflation (Core CPI)", "category": "inflation", "unit": "Index 1982–84=100", "mode": "percent", "change_unit": "% ggü. Vormonat", "year_lag": 12},
    "LNS14000000": {"name": "US-Arbeitslosenquote", "category": "labor", "unit": "%", "mode": "difference", "change_unit": "Prozentpunkte ggü. Vormonat", "year_lag": 12},
    "LNS11300000": {"name": "US-Erwerbsquote", "category": "labor", "unit": "%", "mode": "difference", "change_unit": "Prozentpunkte ggü. Vormonat", "year_lag": 12},
    "CES0000000001": {"name": "US-Beschäftigte außerhalb Landwirtschaft", "category": "labor", "unit": "Tsd. Stellen", "mode": "difference", "change_unit": "Tsd. Stellen ggü. Vormonat", "year_lag": 12, "year_mode": "percent"},
    "CES0500000003": {"name": "US-durchschnittlicher Stundenlohn", "category": "wages", "unit": "USD", "mode": "percent", "change_unit": "% ggü. Vormonat", "year_lag": 12},
}

FRED_SERIES = {
    "DFEDTARL": {"name": "Fed-Zielkorridor Untergrenze", "category": "rates", "unit": "%", "mode": "difference", "change_unit": "Prozentpunkte ggü. Vorwert"},
    "DFEDTARU": {"name": "Fed-Zielkorridor Obergrenze", "category": "rates", "unit": "%", "mode": "difference", "change_unit": "Prozentpunkte ggü. Vorwert"},
    "DGS10": {"name": "Rendite 10-jähriger US-Staatsanleihen", "category": "rates", "unit": "%", "mode": "difference", "change_unit": "Prozentpunkte ggü. Vortag"},
    "GDPC1": {"name": "Reales US-BIP", "category": "growth", "unit": "Mrd. verkettete USD", "mode": "annualized", "change_unit": "% annualisiert ggü. Vorquartal", "year_lag": 4},
    "PCEPILFE": {"name": "US-Kern-PCE-Preisindex", "category": "inflation", "unit": "Index 2017=100", "mode": "percent", "change_unit": "% ggü. Vormonat", "year_lag": 12},
    "RSAFS": {"name": "US-Einzelhandelsumsätze", "category": "growth", "unit": "Mio. USD", "mode": "percent", "change_unit": "% ggü. Vormonat", "year_lag": 12},
}


def _safe_float(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _change(current, previous, mode):
    if current is None or previous is None:
        return None
    if mode == "difference":
        return current - previous
    if previous == 0:
        return None
    if mode == "annualized":
        return ((current / previous) ** 4 - 1) * 100
    return (current / previous - 1) * 100


def _record(series_id, config, observations, source, source_url, fetched_at):
    observations = [(period, value, preliminary) for period, value, preliminary in observations if value is not None]
    if not observations:
        return None
    observations.sort(key=lambda row: row[0])
    period, current, preliminary = observations[-1]
    previous_period, previous, _ = observations[-2] if len(observations) > 1 else (None, None, False)
    year_lag = config.get("year_lag")
    year_ago = observations[-1 - year_lag][1] if year_lag and len(observations) > year_lag else None
    year_mode = config.get("year_mode", "percent" if config["mode"] in ("percent", "annualized") else None)
    yoy = (current / year_ago - 1) * 100 if year_mode == "percent" and year_ago not in (None, 0) else None
    changed = _change(current, previous, config["mode"])
    return {
        "series_id": series_id,
        "period": period,
        "name": config["name"],
        "category": config["category"],
        "value": round(current, 6),
        "unit": config["unit"],
        "previous_value": round(previous, 6) if previous is not None else None,
        "previous_period": previous_period,
        "change_value": round(changed, 6) if changed is not None else None,
        "change_unit": config["change_unit"],
        "year_over_year_pct": round(yoy, 6) if yoy is not None else None,
        "source": source,
        "source_url": source_url,
        "preliminary": bool(preliminary),
        "fetched_at": fetched_at,
    }


def _fetch_bls(fetched_at):
    year = datetime.now(timezone.utc).year
    response = requests.post(
        BLS_URL,
        json={"seriesid": list(BLS_SERIES), "startyear": str(year - 2), "endyear": str(year)},
        headers={"User-Agent": "AITradingDashboard/2.0"},
        timeout=(3, 12),
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError("BLS request did not succeed")
    records = []
    for series in payload.get("Results", {}).get("series", []):
        series_id = series.get("seriesID")
        config = BLS_SERIES.get(series_id)
        if not config:
            continue
        observations = []
        for item in series.get("data", []):
            period_code = item.get("period", "")
            if not period_code.startswith("M") or period_code == "M13":
                continue
            period = f"{item.get('year')}-{period_code[1:]}"
            preliminary = any(note.get("code") == "P" for note in item.get("footnotes", []) if isinstance(note, dict))
            observations.append((period, _safe_float(item.get("value")), preliminary))
        result = _record(series_id, config, observations, "U.S. Bureau of Labor Statistics", "https://www.bls.gov/developers/", fetched_at)
        if result:
            records.append(result)
    return records


def _fetch_fred(fetched_at):
    response = requests.get(
        FRED_EXPORT_URL,
        params={"id": ",".join(FRED_SERIES)},
        headers={"User-Agent": "AITradingDashboard/2.0"},
        timeout=(3, 15),
    )
    response.raise_for_status()
    observations = {series_id: [] for series_id in FRED_SERIES}
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        for filename in archive.namelist():
            if not filename.endswith(".csv"):
                continue
            reader = csv.DictReader(io.StringIO(archive.read(filename).decode("utf-8-sig")))
            for row in reader:
                period = row.get("observation_date")
                if not period:
                    continue
                for series_id in set(row).intersection(FRED_SERIES):
                    value = _safe_float(row.get(series_id))
                    if value is not None:
                        observations[series_id].append((period, value, False))
    records = []
    for series_id, config in FRED_SERIES.items():
        result = _record(series_id, config, observations[series_id], "FRED, Federal Reserve Bank of St. Louis", f"https://fred.stlouisfed.org/series/{series_id}", fetched_at)
        if result:
            records.append(result)
    return records


def _latest_stored():
    try:
        connection = get_connection()
        try:
            rows = connection.execute("""
                SELECT data.* FROM hard_economic_data data
                JOIN (SELECT series_id, MAX(period) AS period FROM hard_economic_data GROUP BY series_id) latest
                  ON latest.series_id=data.series_id AND latest.period=data.period
                ORDER BY data.category, data.series_id
            """).fetchall()
            return [{**dict(row), "preliminary": bool(row["preliminary"])} for row in rows]
        finally:
            connection.close()
    except sqlite3.OperationalError:
        return []


def _store(records):
    if not records:
        return
    fields = ["series_id", "period", "name", "category", "value", "unit", "previous_value", "previous_period", "change_value", "change_unit", "year_over_year_pct", "source", "source_url", "preliminary", "fetched_at"]
    connection = get_connection()
    try:
        with connection:
            connection.executemany(
                f"INSERT OR REPLACE INTO hard_economic_data ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                [[int(record[field]) if field == "preliminary" else record.get(field) for field in fields] for record in records],
            )
    finally:
        connection.close()


def hard_data_snapshot_id(records):
    stable = [{key: item.get(key) for key in ("series_id", "period", "value", "previous_value", "change_value", "year_over_year_pct")} for item in records]
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def _bundle(records, status):
    records = sorted(records, key=lambda item: (item["category"], item["series_id"]))
    fetched_at = max((float(item.get("fetched_at") or 0) for item in records), default=0)
    now = time.time()
    items = [{**item, "stale": not item.get("fetched_at") or now - float(item["fetched_at"]) > 24 * 3600} for item in records]
    stale = not items or all(item["stale"] for item in items)
    return {
        "items": items,
        "snapshot_id": hard_data_snapshot_id(items),
        "fetched_at": fetched_at or None,
        "status": "stale" if stale else status,
        "sources": sorted({item["source"] for item in items}),
    }


def get_hard_economic_data(force=False):
    """Return cached official observations and refresh them at most every six hours."""
    global _cache
    cache_seconds = max(900, int(os.getenv("HARD_DATA_CACHE_SECONDS", "21600")))
    with _cache_lock:
        if not force and _cache and time.monotonic() - _cache[0] < cache_seconds:
            return json.loads(json.dumps(_cache[1]))
        stored = _latest_stored()
        newest = max((float(item.get("fetched_at") or 0) for item in stored), default=0)
        if not force and stored and time.time() - newest < cache_seconds:
            result = _bundle(stored, "cache")
            _cache = (time.monotonic(), result)
            return json.loads(json.dumps(result))

        fetched_at = time.time()
        live = []
        # Refresh the independent providers concurrently, then merge in the same order.
        # The surrounding cache lock still coalesces simultaneous refresh requests.
        with ThreadPoolExecutor(max_workers=2) as executor:
            providers = [(provider, executor.submit(fetcher, fetched_at))
                         for provider, fetcher in (("BLS", _fetch_bls), ("FRED", _fetch_fred))]
            for provider, future in providers:
                try:
                    live.extend(future.result())
                except (requests.RequestException, ValueError, KeyError, zipfile.BadZipFile, csv.Error) as exc:
                    logger.warning("Hard-data provider unavailable: %s (%s)", provider, type(exc).__name__)
        if live:
            _store(live)
            by_series = {item["series_id"]: item for item in stored}
            by_series.update({item["series_id"]: item for item in live})
            result = _bundle(list(by_series.values()), "live")
        else:
            result = _bundle(stored, "stale")
        _cache = (time.monotonic(), result)
        return json.loads(json.dumps(result))
