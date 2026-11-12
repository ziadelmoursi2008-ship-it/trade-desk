"""RSS acquisition with explicit errors and a persistent last-successful snapshot."""

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import feedparser
import requests

from database import get_connection

logger = logging.getLogger(__name__)
WINDOW_SECONDS = 72 * 3600


def published_timestamp(value):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (ValueError, TypeError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def _last_success(ticker, source):
    with closing(get_connection()) as connection:
        row = connection.execute("SELECT items_json,fetched_at FROM news_feed_snapshots WHERE ticker=? AND source=?",
                                 (ticker, source)).fetchone()
        return (json.loads(row[0]), row[1]) if row else ([], None)


def _save_success(ticker, source, items, fetched_at):
    with closing(get_connection()) as connection, connection:
        connection.execute("INSERT INTO news_feed_snapshots(ticker,source,items_json,fetched_at) VALUES(?,?,?,?) "
                           "ON CONFLICT(ticker,source) DO UPDATE SET items_json=excluded.items_json,fetched_at=excluded.fetched_at",
                           (ticker, source, json.dumps(items, ensure_ascii=False), fetched_at))


def fetch_feed(url, label, ticker):
    now = time.time()
    items, error, status_code = [], None, None
    for attempt in range(2):
        try:
            response = requests.get(url, timeout=(3, 10), headers={"User-Agent": "AITradingDashboard/4.1"})
            status_code = response.status_code
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            if not parsed.version:
                raise ValueError("invalid_feed")
            if parsed.get("bozo") and not parsed.entries:
                raise ValueError("invalid_feed")
            error = None
            for entry in parsed.entries[:200]:
                link = entry.get("link", "")
                if urlparse(link).scheme not in ("http", "https"):
                    continue
                published = entry.get("published") or entry.get("updated")
                items.append({
                    "ticker": ticker, "headline": entry.get("title", "")[:1000],
                    "source": entry.get("source", {}).get("title") or label, "feed_source": label,
                    "url": link, "published_at": published, "published_ts": published_timestamp(published),
                    "summary": re.sub("<[^>]+>", " ", entry.get("summary", ""))[:1200], "is_macro": False,
                })
            break
        except (requests.RequestException, ValueError) as exc:
            items = []
            error = ("rate_limit" if status_code == 429 else "http_" + str(status_code)
                     if status_code and status_code >= 400 else "timeout" if isinstance(exc, requests.Timeout)
                     else "invalid_feed" if isinstance(exc, ValueError) else "connection_error")
            if attempt == 0 and (status_code in (500, 502, 503, 504) or isinstance(exc, (requests.Timeout, requests.ConnectionError))):
                continue
            break
    if items:
        error = None
        _save_success(ticker, label, items, now)
        last_success = now
        source_status = "live"
        message = "Quelle erfolgreich abgerufen."
    else:
        cached_items, last_success = _last_success(ticker, label)
        items = [item for item in cached_items if item.get("published_ts") is not None
                 and 0 <= now - item["published_ts"] <= WINDOW_SECONDS]
        source_status = "cached" if items else "error" if error else "empty"
        message = ("Abruf fehlgeschlagen; zuletzt erfolgreich geladene Artikel werden verwendet." if error and items else
                   "Abruf fehlgeschlagen; keine gespeicherten Artikel im 72-Stunden-Fenster." if error else
                   "Quelle liefert keine Einträge; gespeicherte Artikel werden verwendet." if items else
                   "Quelle erfolgreich abgerufen, aber ohne Einträge.")
        if error:
            logger.warning("News feed unavailable: %s (%s)", label, error)
    received_count = len(items)
    recent = [item for item in items if item.get("published_ts") is not None
              and 0 <= now - item["published_ts"] <= WINDOW_SECONDS]
    recent.sort(key=lambda item: item["published_ts"], reverse=True)
    maximum = max(10, min(50, int(os.getenv("NEWS_FEED_ITEMS", "30"))))
    return {"items": recent[:maximum], "source": label, "status": source_status,
            "error_code": error, "message": message, "http_status": status_code, "checked_at": now,
            "last_success_at": last_success, "received_count": received_count, "recent_count": len(recent)}


def fetch_sources(ticker, name, source_ticker):
    # Constrain Google at the source, then enforce exact timestamps locally before limiting.
    query = "gold XAU price when:3d" if ticker == "XAUUSD" else f'"{name}" {ticker} financial news when:3d'
    feeds = [
        (requests.Request("GET", "https://news.google.com/rss/search", params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}).prepare().url, "Google News"),
        (requests.Request("GET", "https://feeds.finance.yahoo.com/rss/2.0/headline", params={"s": source_ticker, "region": "US", "lang": "en-US"}).prepare().url, "Yahoo Finance"),
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda spec: fetch_feed(*spec, ticker), feeds))
    has_items = any(row["items"] for row in results)
    degraded = any(row["status"] in ("error", "cached") for row in results)
    status = "partial" if has_items and degraded else "unavailable" if not has_items and degraded else "live" if has_items else "empty"
    return {"items": [item for row in results for item in row["items"]],
            "feed_items_received": sum(row["received_count"] for row in results), "status": status,
            "checked_at": time.time(), "source_statuses": [{k: v for k, v in row.items() if k != "items"} for row in results]}
