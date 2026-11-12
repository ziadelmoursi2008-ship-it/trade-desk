"""Manual-only, persisted options risk context; never a fifth direction score.

Raw chains and models are reused across chart timeframes. Ordinary analysis only
reads SQLite. Provider failure preserves the archive, but withdraws its AI values.
"""
import copy
import hashlib
import json
import math
import os
import re
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from curl_cffi import requests as curl_requests

from database import get_connection
from instruments import CATALOG
from option_greeks import calculate_greeks

VERSION = "1.0-options-risk"
CACHE_SECONDS = 60
MODEL_MAX_AGE = 300
QUOTE_MAX_AGE = 120
MAX_SPOT_DRIFT = .005
SUPPORTED = {item["symbol"] for item in CATALOG if item["type"] in ("EQUITY", "ETF")}
_locks = [threading.Lock() for _ in range(64)]
NY = ZoneInfo("America/New_York")
UNITS = {"delta": "USD/1 USD Basiswert", "gamma": "Delta/1 USD Basiswert",
         "theta": "USD/Kalendertag", "vega": "USD/1 Prozentpunkt IV", "rho": "USD/1 Prozentpunkt Zins"}
WARNINGS = ["BSM-Näherung für europäische Ausübung; US-Aktien-/ETF-Optionen sind üblicherweise amerikanisch.",
            "Keine Modellierung diskreter Dividenden oder vorzeitiger Ausübung. Werte je Optionsanteil, nicht je 100er-Kontrakt.",
            "Options-Delta ist kein Volumen-Delta/CVD. Kein Bullish-/Bearish-Score und keine zusätzliche Richtungsgewichtung.",
            "Keine Dealerpositionen bekannt: Open Interest erlaubt keine Aussage über Netto-Gamma oder Hedge-Käufe."]


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


class OptionsProviderError(RuntimeError):
    """Sanitized boundary for requests AND yfinance/curl transport failures."""


def timestamp(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() if parsed.tzinfo is not None else None
        except ValueError:
            return None
    return number(value)


def _config():
    key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    provider = os.getenv("OPTIONS_PROVIDER", "auto").lower()
    provider = ("alpaca" if key and secret else "yahoo") if provider == "auto" else provider
    feed = os.getenv("OPTIONS_ALPACA_FEED", "opra").lower()
    if provider not in ("yahoo", "alpaca") or feed not in ("opra", "indicative"):
        raise ValueError("Optionsanbieter/Feed ungültig.")
    return provider, feed, key, secret


def _settings_key():
    # Never persist credentials. A config change invalidates cached model assumptions.
    provider, feed, _, _ = _config()
    return hashlib.sha256(json.dumps([VERSION, provider, feed, os.getenv("OPTIONS_RISK_FREE_RATE"),
                                      os.getenv("OPTIONS_DIVIDEND_YIELD")]).encode()).hexdigest()


def _empty(ticker, status, reason):
    return {"version": VERSION, "ticker": ticker, "status": status, "reason": reason,
            "role": "risk_context_only", "direction_weight_pct": 0, "usable_for_ai": False,
            "contracts": [], "warnings": WARNINGS, "units": UNITS, "manual_only": True}


def _rate(now):
    configured = os.getenv("OPTIONS_RISK_FREE_RATE")
    if configured is not None:
        rate = number(configured)
        if rate is None or not -0.1 <= rate <= 1:
            raise ValueError("OPTIONS_RISK_FREE_RATE muss eine Dezimalzahl zwischen -0.1 und 1 sein.")
        return rate, "Konfigurierter Modellzins (Dezimalzahl); kein automatisch aktueller Marktzinssatz."
    # Existing, sourced Fed observations only; no network work or silent fixed rate.
    with closing(get_connection()) as connection:
        rows = [connection.execute("SELECT value,period FROM hard_economic_data WHERE series_id=? ORDER BY period DESC LIMIT 1",
                                   (series,)).fetchone() for series in ("DFEDTARL", "DFEDTARU")]
    if all(rows) and rows[0]["period"] == rows[1]["period"]:
        try:
            observed = datetime.fromisoformat(rows[0]["period"]).replace(tzinfo=timezone.utc).timestamp()
            rate = (float(rows[0]["value"]) + float(rows[1]["value"])) / 200
            if 0 <= now - observed <= 10 * 86400 and -0.1 <= rate <= 1:
                return rate, "Fed-Zielband-Mitte vom " + rows[0]["period"] + " als Modellproxy, keine Laufzeitenkurve."
        except (ValueError, TypeError):
            pass
    raise ValueError("Aktueller Modellzins fehlt. Erst Wirtschaftsdaten laden oder OPTIONS_RISK_FREE_RATE konfigurieren.")


def _yahoo_chain(ticker, now, spot, spot_at):
    # yfinance's public option_chain does not expose a timeout and its private
    # cookie helper can use 30-second defaults. Use its installed curl transport
    # with our own bounded session; do not mutate the shared market-data client.
    with curl_requests.Session(impersonate="chrome") as session:
        return _yahoo_with_session(session, ticker, now, spot, spot_at)


def _yahoo_with_session(session, ticker, now, spot, spot_at):
    deadline = time.monotonic() + 14
    crumb = None

    def request(url, params=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Options timeout")
        return session.get(url, params=params, timeout=min(3, remaining))

    def get(expiry=None):
        nonlocal crumb
        url = f"https://query2.finance.yahoo.com/v7/finance/options/{ticker}"
        params = {} if expiry is None else {"date": expiry}
        if crumb:
            params["crumb"] = crumb
        response = request(url, params)
        if response.status_code in (401, 403) and not crumb:
            # Yahoo authentication cookie then crumb, all inside the same budget.
            # Cookie endpoint commonly returns 404 together with Set-Cookie.
            request("https://fc.yahoo.com")
            auth = request("https://query1.finance.yahoo.com/v1/test/getcrumb")
            auth.raise_for_status()
            crumb = auth.text.strip()
            if not crumb or len(crumb) > 128 or "<" in crumb:
                raise ValueError("Yahoo-Autorisierung nicht verfügbar.")
            params["crumb"] = crumb
            response = request(url, params)
        response.raise_for_status()
        results = response.json().get("optionChain", {}).get("result", [])
        if not results:
            raise ValueError("Keine Optionskette verfügbar.")
        return results[0]

    first = get()
    quote = first.get("quote", {})
    if quote.get("currency", "USD") != "USD":
        raise ValueError("Optionsmodell unterstützt hier nur USD-Kontrakte.")
    source_spot = number(quote.get("regularMarketPrice"))
    source_at = timestamp(quote.get("regularMarketTime"))
    yahoo_spot = source_spot is not None and source_spot > 0 and source_at is not None
    if yahoo_spot:
        spot, spot_at = source_spot, source_at
    today = datetime.fromtimestamp(now, NY).date()
    expiries = sorted(int(exp) for exp in first.get("expirationDates", [])
                      if 1 <= (datetime.fromtimestamp(exp, timezone.utc).date() - today).days <= 45)[:2]
    contracts = []
    for expiry in expiries:
        initial = first.get("options", [])
        data = first if initial and int(initial[0].get("expirationDate", 0)) == expiry else get(expiry)
        for chain in data.get("options", []):
            for kind, field in (("call", "calls"), ("put", "puts")):
                for item in chain.get(field, []):
                    if item.get("contractSize", "REGULAR") != "REGULAR" or item.get("currency", "USD") != "USD":
                        continue
                    contracts.append({"symbol": item.get("contractSymbol"), "kind": kind,
                                      "expiration": datetime.fromtimestamp(expiry, timezone.utc).date().isoformat(),
                                      "strike": number(item.get("strike")), "iv": number(item.get("impliedVolatility")),
                                      "bid": number(item.get("bid")), "ask": number(item.get("ask")),
                                      "quote_at": None, "last_trade_at": timestamp(item.get("lastTradeDate")),
                                      "open_interest": number(item.get("openInterest"))})
    return {"provider": "yahoo", "feed": "Yahoo Options (Quote-Zeitpunkt unbekannt)",
            "spot": spot, "spot_at": spot_at, "spot_time_basis": "Yahoo regularMarketTime" if yahoo_spot else "Zeitstempel des Basiswert-Kursbalkens (konservativ)",
            "contracts": contracts, "partial": False, "verified_feed": False}


def _alpaca_chain(ticker, now, spot, spot_at):
    _, feed, key, secret = _config()
    if not key or not secret:
        raise ValueError("Alpaca-Zugangsdaten fehlen.")
    today = datetime.fromtimestamp(now, NY).date()
    params = {"feed": feed, "limit": 1000, "expiration_date_gte": (today + timedelta(days=1)).isoformat(),
              "expiration_date_lte": (today + timedelta(days=45)).isoformat(),
              "strike_price_gte": round(spot * .92, 3), "strike_price_lte": round(spot * 1.08, 3)}
    contracts = []
    page = None
    for _ in range(2):
        if page:
            params["page_token"] = page
        response = requests.get(f"https://data.alpaca.markets/v1beta1/options/snapshots/{ticker}", params=params,
                                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}, timeout=(2, 4))
        # Never retry a forbidden OPRA request using the indicative feed silently.
        response.raise_for_status()
        body = response.json()
        for symbol, item in body.get("snapshots", {}).items():
            match = re.fullmatch(r"([A-Z]+)(\d{6})([CP])(\d{8})", symbol)
            if not match or match[1] != ticker:
                continue  # Reject adjusted/nonstandard roots.
            expiration = datetime.strptime(match[2], "%y%m%d").date().isoformat()
            quote = item.get("latestQuote") or {}
            trade = item.get("latestTrade") or {}
            contracts.append({"symbol": symbol, "kind": "call" if match[3] == "C" else "put",
                              "expiration": expiration, "strike": int(match[4]) / 1000,
                              "iv": number(item.get("impliedVolatility")), "bid": number(quote.get("bp")), "ask": number(quote.get("ap")),
                              "quote_at": timestamp(quote.get("t")), "last_trade_at": timestamp(trade.get("t")),
                              "open_interest": None})
        page = body.get("next_page_token")
        if not page:
            break
    return {"provider": "alpaca", "feed": feed, "spot": spot, "spot_at": spot_at,
            "spot_time_basis": "Zeitstempel des Basiswert-Kursbalkens (konservativ)",
            "contracts": contracts, "partial": bool(page), "verified_feed": feed == "opra"}


def fetch_chain(ticker, now, spot, spot_at):
    provider, _, _, _ = _config()
    try:
        return (_alpaca_chain if provider == "alpaca" else _yahoo_chain)(ticker, now, spot, spot_at)
    except Exception as exc:
        # SDK transport exceptions need not derive from requests.RequestException.
        # No provider exception string may expose headers/credentials to UI/logs.
        raise OptionsProviderError("Optionsanbieter nicht erreichbar oder keine geeigneten Daten/Berechtigung vorhanden.") from exc


def _model(ticker, chain, now):
    spot, spot_at = number(chain.get("spot")), timestamp(chain.get("spot_at"))
    if spot is None or spot <= 0:
        raise ValueError("Options-Basiswertkurs fehlt.")
    rate, rate_source = _rate(now)
    dividend = number(os.getenv("OPTIONS_DIVIDEND_YIELD", "0"))
    if dividend is None or not 0 <= dividend <= 1:
        raise ValueError("OPTIONS_DIVIDEND_YIELD muss eine Dezimalzahl zwischen 0 und 1 sein.")
    today = datetime.fromtimestamp(now, NY).date()
    candidates = []
    for raw in chain.get("contracts", []):
        try:
            kind = raw.get("kind")
            strike, iv, bid, ask = [number(raw.get(key)) for key in ("strike", "iv", "bid", "ask")]
            if kind not in ("call", "put") or any(v is None for v in (strike, iv, bid, ask)):
                continue
            expiration = datetime.fromisoformat(raw["expiration"]).date()
            if not 1 <= (expiration - today).days <= 45 or strike <= 0 or abs(strike / spot - 1) > .15:
                continue
            if not 0 < iv <= 5 or bid <= 0 or ask < bid or (ask - bid) / ((ask + bid) / 2) > .35:
                continue
            symbol_match = re.fullmatch(r"([A-Z]+)(\d{6})([CP])(\d{8})", str(raw.get("symbol", "")))
            if not symbol_match or symbol_match[1] != ticker:
                continue
            if (symbol_match[2] != expiration.strftime("%y%m%d") or
                    symbol_match[3] != ("C" if kind == "call" else "P") or
                    abs(int(symbol_match[4]) / 1000 - strike) > .0001):
                continue
            # Standard PM equity/ETF expiration approximation; never use 0DTE.
            expiry_at = datetime.combine(expiration, datetime.min.time(), NY).replace(hour=16).timestamp()
            values = calculate_greeks(spot, strike, (expiry_at - now) / (365 * 86400), rate, iv, kind, dividend)
            # Quote/spot/model consistency: provider IV must not imply a price far
            # outside the current spread. These are references, not execution quotes.
            if not bid * .65 <= values["model_price"] <= ask * 1.35:
                continue
            quote_at = timestamp(raw.get("quote_at"))
            fresh = bool(chain.get("verified_feed") and quote_at is not None and spot_at is not None
                         and -5 <= now - quote_at <= QUOTE_MAX_AGE and -5 <= now - spot_at <= QUOTE_MAX_AGE
                         and abs(quote_at - spot_at) <= QUOTE_MAX_AGE)
            candidates.append({**raw, "strike": strike, "iv": iv, "bid": bid, "ask": ask,
                               "quote_at": quote_at, "greeks": {k: round(v, 10) for k, v in values.items()},
                               "fresh_quote": fresh})
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    contracts = []
    for expiry in sorted({item["expiration"] for item in candidates})[:2]:
        for kind in ("call", "put"):
            group = [item for item in candidates if item["expiration"] == expiry and item["kind"] == kind]
            # Prefer quality, then proximity; no OI-based directional inference.
            contracts.extend(sorted(group, key=lambda item: (not item["fresh_quote"], abs(item["strike"] - spot),
                                                             item["symbol"]))[:2])
    if not contracts:
        raise ValueError("Keine geeigneten kursnahen Optionen mit plausibler IV und Geld-/Brief-Spanne verfügbar.")
    usable = any(item["fresh_quote"] for item in contracts)
    reason = ("Zeitnahe OPRA-Quotes; BSM-Modellwerte ausschließlich als Optionsrisiko."
              if usable else "Nur Referenzwerte: Quote-Aktualität nicht bestätigt oder Basiswertkurs/Feed verzögert. Für Intraday-KI ausgeblendet.")
    warnings = list(WARNINGS)
    if chain.get("partial"):
        warnings.append("Anbieterauswahl begrenzt: maximal 2 Seiten, keine vollständige Optionskette.")
    result = {**_empty(ticker, "AVAILABLE" if usable else "REFERENCE", reason),
              "provider": chain.get("provider"), "feed": chain.get("feed"), "computed_at": now,
              "spot": spot, "spot_at": spot_at, "spot_time_basis": chain.get("spot_time_basis"),
              "usable_for_ai": usable, "contracts": contracts, "warnings": warnings,
              "model": "Black–Scholes–Merton (europäische Näherung)",
              "assumptions": {"rate": rate, "rate_source": rate_source, "dividend_yield": dividend,
                              "dividend_source": "Konfiguriert" if "OPTIONS_DIVIDEND_YIELD" in os.environ else "Modellannahme q=0; keine gemessene Dividendenrendite",
                              "expiry_time": "16:00 America/New_York, keine 0DTE", "days_per_year": 365}}
    result["snapshot_id"] = hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return result


def _read(ticker):
    with closing(get_connection()) as connection:
        row = connection.execute("SELECT * FROM options_context_cache WHERE ticker=? AND version=?", (ticker, VERSION)).fetchone()
    return dict(row) if row else None


def _spot_changed(context, spot):
    spot = number(spot)
    previous = number(context.get("spot"))
    return bool(spot is not None and spot > 0 and previous and abs(spot / previous - 1) > MAX_SPOT_DRIFT)


def _view(ticker, row, now, spot=None):
    if not row or not row["snapshot_json"]:
        return _empty(ticker, "UNAVAILABLE" if row else "NOT_CALCULATED",
                      (row or {}).get("last_error") or "Mit Quant berechnen + Gemini analysieren einmalig laden.")
    context = json.loads(row["snapshot_json"])
    config_changed = row["config_hash"] != _settings_key()
    if row["last_error"] or config_changed or not 0 <= now - context["computed_at"] <= MODEL_MAX_AGE:
        context.update(status="STALE", usable_for_ai=False,
                       reason=row["last_error"] or ("Optionskonfiguration geändert. Erneut per Knopfdruck laden." if config_changed
                                                    else "Gespeicherte Optionswerte älter als 5 Minuten. Per Quant-Knopf aktualisieren."))
    else:
        # Reassess freshness without recomputing any Greek or fetching data.
        for contract in context["contracts"]:
            at = contract.get("quote_at")
            contract["fresh_quote"] = bool(contract["fresh_quote"] and at is not None and -5 <= now - at <= QUOTE_MAX_AGE
                                           and context.get("spot_at") is not None and -5 <= now - context["spot_at"] <= QUOTE_MAX_AGE)
        context["usable_for_ai"] = any(c["fresh_quote"] for c in context["contracts"])
        if context["status"] == "AVAILABLE" and not context["usable_for_ai"]:
            context.update(status="REFERENCE", reason="Quote oder Basiswertkurs älter als 2 Minuten. Per Quant-Knopf aktualisieren; keine Intraday-KI-Nutzung.")
        if _spot_changed(context, spot):
            context.update(status="REFERENCE", usable_for_ai=False,
                           reason="Basiswertkurs hat sich seit der Berechnung um über 0,5 % verändert. Per Quant-Knopf aktualisieren.")
            for contract in context["contracts"]:
                contract["fresh_quote"] = False
    context["cache_hit"] = True
    return context


def saved_context(ticker, *, now=None, spot=None):
    if ticker not in SUPPORTED:
        return _empty(ticker, "UNSUPPORTED", "Optionskontext nur für US-Aktien/ETFs im Katalog; kein Ersatzsymbol für Krypto oder Gold.")
    try:
        return _view(ticker, _read(ticker), time.time() if now is None else now, spot)
    except ValueError as exc:
        return _empty(ticker, "UNAVAILABLE", str(exc))


def refresh(ticker, *, spot, spot_at, now=None, ensure_current=None):
    if ticker not in SUPPORTED:
        return saved_context(ticker, now=now)
    live_clock = now is None
    now = time.time() if live_clock else now
    with _locks[hash(ticker) % len(_locks)]:
        if ensure_current:
            ensure_current()
        row = _read(ticker)
        try:
            config_hash = _settings_key()
            # Raw chains/models are shared by all timeframes; rapid retries on a
            # failed provider also respect the 60-second cache to avoid floods.
            if row and row["config_hash"] == config_hash and 0 <= now - row["attempted_at"] < CACHE_SECONDS:
                saved = json.loads(row["snapshot_json"]) if row["snapshot_json"] else {}
                if row["last_error"] or not _spot_changed(saved, spot):
                    return _view(ticker, row, now, spot)
            if number(spot) is None or spot <= 0:
                raise ValueError("Basiswertkurs fehlt.")
            chain = fetch_chain(ticker, now, spot, spot_at)
            if live_clock:
                now = time.time()
            snapshot = _model(ticker, chain, now)
            if ensure_current:
                ensure_current()
            encoded = json.dumps(snapshot, sort_keys=True, allow_nan=False)
            raw = json.dumps(chain, sort_keys=True, allow_nan=False)
            with closing(get_connection()) as connection, connection:
                connection.execute("INSERT OR IGNORE INTO options_context_snapshots(id,ticker,version,computed_at,snapshot_json) VALUES(?,?,?,?,?)",
                                   (snapshot["snapshot_id"], ticker, VERSION, now, encoded))
                connection.execute("INSERT OR REPLACE INTO options_context_cache(ticker,version,config_hash,attempted_at,snapshot_json,chain_json,last_error) VALUES(?,?,?,?,?,?,NULL)",
                                   (ticker, VERSION, config_hash, now, encoded, raw))
            return {**_view(ticker, _read(ticker), now, spot), "cache_hit": False}
        except (ValueError, OptionsProviderError, requests.RequestException, TimeoutError, KeyError, TypeError, OSError) as exc:
            if ensure_current:
                ensure_current()
            # No credentials, URLs or arbitrary provider error text in UI/DB.
            message = str(exc) if isinstance(exc, ValueError) else "Optionsanbieter nicht erreichbar oder Zugriff abgelehnt; kein Echtzeit-Ersatzfeed verwendet."
            if len(message) > 220:
                message = "Optionsdaten konnten nicht sicher verarbeitet werden."
            try:
                config_hash = _settings_key()
            except ValueError:
                config_hash = "invalid"
            with closing(get_connection()) as connection, connection:
                connection.execute("INSERT INTO options_context_cache(ticker,version,config_hash,attempted_at,last_error) VALUES(?,?,?,?,?) ON CONFLICT(ticker,version) DO UPDATE SET config_hash=excluded.config_hash,attempted_at=excluded.attempted_at,last_error=excluded.last_error",
                                   (ticker, VERSION, config_hash, now, message))
            return _view(ticker, _read(ticker), now) if config_hash != "invalid" else _empty(ticker, "UNAVAILABLE", message)


def for_gemini(context):
    """Stable compact payload: no moving age counters that defeat Gemini caches.

Unverified/delayed/archived numeric Greeks are deliberately absent from AI input.
The UI may still display them, explicitly labelled as reference/archive values.
"""
    if not context:
        return None
    result = {key: copy.deepcopy(context.get(key)) for key in
              ("version", "ticker", "snapshot_id", "status", "reason", "role", "direction_weight_pct",
               "usable_for_ai", "provider", "feed", "computed_at", "warnings")}
    if context.get("usable_for_ai"):
        result.update(spot=context.get("spot"), spot_at=context.get("spot_at"), model=context.get("model"),
                      assumptions=context.get("assumptions"), units=UNITS,
                      contracts=[{key: c.get(key) for key in ("symbol", "kind", "expiration", "strike", "iv", "quote_at", "greeks")}
                                 for c in context.get("contracts", []) if c.get("fresh_quote")])
    return result
