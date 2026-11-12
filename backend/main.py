import asyncio
import hashlib
import json
import logging
import math
import os
import tempfile
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).with_name(".env"))

from backtesting import run_backtest_strategy
from database import get_connection, init_db, save_signal, save_forecast, forecast_history
from decision_engine import MIN_REWARD_RISK, STRATEGY_VERSION, minimum_reward_risk, validate_ai_trade_decision
from instruments import CATALOG, DEFAULT_UNIVERSE, TIMEFRAME_MAP, normalize_ticker
from market_data import candle_end, completed_bars, fetch_stock_df, market_metadata, serialize_klines
from news_engine import get_news_context_and_store
from technical_analysis import calculate_atr, run_technical_analysis
from signal_manager import manage_signal, monitor_signal_market, previous_signal_context, signal_history, retire_pending_python_plan
from signal_policy import decision_policy
from analysis_modes import get_analysis_mode, set_analysis_mode, ensure_analysis_mode, ModeChangedError, AnalysisBarChangedError
from technical_strategy import FVG_NEARBY_ATR, FVG_SIMILAR_RATIO
from technical_forecast import FORECAST_VERSION, build_technical_forecast, forecast_decision
from timeframe_analysis import MIN_TARGET_DISTANCE_PCT, POI_MATERIAL_ATR, lower_timeframes
import lightgbm_service as ml
import options_context as options
import backtest_archive
import backtest_service as backtests
import background_settings
from background_service import BackgroundService
from background_prices import observed_closed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
yf.set_tz_cache_location(os.getenv("MARKET_CACHE_DIR", str(Path(tempfile.gettempdir()) / "ai-trading-yfinance")))
Timeframe = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1D"]
AnalysisMode = Literal["gemini", "python"]
_analysis_cache = OrderedDict()
_analysis_lock = threading.Lock()
_scan_lock = threading.Lock()
_scan_cache = {}
_evaluation_locks = [threading.Lock() for _ in range(128)]


@asynccontextmanager
async def lifespan(app):
    decision_policy()
    init_db()
    backtests.recover_jobs()
    ml.recover_jobs()
    worker = BackgroundService(evaluate, market_frame)
    app.state.background_worker = worker
    worker.start()
    try:
        yield
    finally:
        worker.stop()


app = FastAPI(title="AI Trading Intelligence Platform", version="4.1", lifespan=lifespan)
origins = os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174").split(",")
origins.extend(["http://127.0.0.1:8000","http://localhost:8000"])
app.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in origins], allow_methods=["GET", "POST"], allow_headers=["*"])
SITE_DIST = Path(__file__).resolve().parent.parent/"frontend"/"dist"
if (SITE_DIST/"assets").is_dir():
    app.mount("/assets",StaticFiles(directory=SITE_DIST/"assets"),name="production-assets")
OBSERVATORY_DIST = Path(__file__).resolve().parent/"observatory"/"dist"
if OBSERVATORY_DIST.is_dir():
    app.mount("/observatory", StaticFiles(directory=OBSERVATORY_DIST), name="backend-observatory")


@app.exception_handler(ModeChangedError)
async def mode_changed_handler(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def selected_mode(mode=None):
    mode = mode or get_analysis_mode()
    ensure_analysis_mode(mode)
    return mode


class ModeSelection(BaseModel):
    mode: AnalysisMode


@app.get("/api/analysis-mode")
def read_analysis_mode():
    return {"mode": get_analysis_mode(), "manual_only": True}


@app.post("/api/analysis-mode")
def change_analysis_mode(selection: ModeSelection):
    return {"mode": set_analysis_mode(selection.mode), "manual_only": True}


class QuantitativeSelection(BaseModel):
    enabled: bool


def quantitative_settings():
    return {"enabled": False, "revision": 0, "configured_weight_pct": 0,
            "retired": True, "replacement": "lightgbm"}


@app.get("/api/quantitative/settings")
def read_quantitative_settings():
    return quantitative_settings()


@app.post("/api/quantitative/settings")
def change_quantitative_settings(selection: QuantitativeSelection):
    raise HTTPException(410, "Die alte Quant-Bewertung wurde durch LightGBM ersetzt. Einstellungen: /api/lightgbm/settings")


@app.post("/api/quantitative/calculate")
def calculate_quantitative(ticker: str = "NVDA", timeframe: Timeframe = "15m", revision: int | None = None):
    raise HTTPException(410, "Training startet über /api/lightgbm/train. Es wird keine zusätzliche Gemini-Analyse gestartet.")


class LightGBMTraining(BaseModel):
    ticker: str
    timeframe: Literal["5m", "15m"] = "15m"
    refresh_data: bool = True


@app.get("/api/lightgbm")
def lightgbm_status(ticker: str = Query("NVDA", max_length=30), timeframe: Timeframe = "15m"):
    return ml.status(valid_ticker(ticker), timeframe)


@app.post("/api/lightgbm/settings")
def lightgbm_settings(selection: QuantitativeSelection):
    result = ml.set_enabled(selection.enabled)
    with _scan_lock:
        _scan_cache.clear()
    return result


@app.post("/api/lightgbm/train", status_code=202)
def train_lightgbm(request: LightGBMTraining):
    try:
        return ml.start(valid_ticker(request.ticker), request.timeframe, request.refresh_data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class LightGBMBatchTraining(BaseModel):
    symbols: list[str]
    timeframes: list[Literal["5m", "15m"]]
    refresh_data: bool = True


@app.get("/api/lightgbm/inventory")
def lightgbm_inventory():
    return ml.inventory()


@app.post("/api/lightgbm/train-batch", status_code=202)
def train_lightgbm_batch(request: LightGBMBatchTraining):
    try:
        return ml.start_batch(request.symbols, request.timeframes, request.refresh_data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/options/refresh")
def refresh_options(ticker: str = Query("NVDA", max_length=30)):
    ticker = valid_ticker(ticker)
    frame = market_frame(ticker, "5m")
    return options.refresh(ticker, spot=float(frame.Close.iloc[-1]), spot_at=frame.index[-1].timestamp())


def valid_ticker(value):
    try:
        return normalize_ticker(value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def market_frame(ticker, timeframe):
    try:
        df = fetch_stock_df(ticker, timeframe)
    except Exception as exc:
        logger.warning("Market feed unavailable for %s (%s)", ticker, type(exc).__name__)
        raise HTTPException(503, f"Marktdaten für {ticker} nicht verfügbar. Bitte später erneut versuchen.") from exc
    if df.empty:
        raise HTTPException(404, f"Keine echten Marktdaten für {ticker} gefunden.")
    return df


def historical_news(ticker):
    connection = get_connection()
    try:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM news_items WHERE ticker=? AND published_ts IS NOT NULL ORDER BY published_ts", (ticker,))]
    finally:
        connection.close()


def load_lower_timeframe_contexts(ticker, timeframe, *, observed_at):
    """Fetch independent, closed-candle POIs for every supported smaller timeframe."""
    required = lower_timeframes(timeframe)
    if not required:
        return {}
    cutoff = pd.Timestamp(observed_at, unit="ms", tz="UTC")

    def load_one(smaller):
        try:
            frame = market_frame(ticker, smaller)
            if frame.attrs.get("timeframe") != smaller:
                return smaller, {"status": "UNAVAILABLE", "reason": "Kerzendaten passen nicht zum angeforderten Timeframe."}
            closed = completed_bars(frame, smaller, now=cutoff)
            data = run_technical_analysis(closed)
            meta = market_metadata(frame, ticker, smaller)
            if data.get("error"):
                return smaller, {"status": "UNAVAILABLE", "reason": data["error"]}
            if meta["is_stale"]:
                return smaller, {"status": "STALE", "reason": "POI-Kursdaten sind veraltet."}
            return smaller, {"status": "AVAILABLE", "technical": data,
                             "analysis_bar_at": int(candle_end(closed.index[-1], smaller, closed.attrs).timestamp() * 1000)}
        except HTTPException as exc:
            return smaller, {"status": "UNAVAILABLE", "reason": str(exc.detail)}
        except Exception as exc:
            logger.warning("POI data unavailable for %s (%s, %s)", ticker, smaller, type(exc).__name__)
            return smaller, {"status": "UNAVAILABLE", "reason": "POI-Daten konnten nicht ausgewertet werden."}

    with ThreadPoolExecutor(max_workers=min(3, len(required))) as executor:
        return dict(executor.map(load_one, required))


def evaluate(ticker, timeframe, *, with_backtest=True, persist=True, mode=None,
             refresh_quantitative=False, quant_revision=None, expected_bar_end=None):
    if isinstance(quant_revision,int):
        raise HTTPException(410, "Alte Quant-Auswahl nicht mehr aktiv. Website neu laden; LightGBM ersetzt die alte Bewertung.")
    mode = selected_mode(mode)
    with _evaluation_locks[hash((ticker, timeframe, mode)) % len(_evaluation_locks)]:
        ensure_analysis_mode(mode)
        return _evaluate(ticker, timeframe, with_backtest=with_backtest, persist=persist, mode=mode,
                         refresh_quantitative=refresh_quantitative, quant_revision=quant_revision,expected_bar_end=expected_bar_end)


def _evaluate(ticker, timeframe, *, with_backtest=True, persist=True, mode="gemini",
              refresh_quantitative=False, quant_revision=None,expected_bar_end=None):
    df = market_frame(ticker, timeframe)
    closed = observed_closed(df,timeframe) if expected_bar_end is not None else completed_bars(df,timeframe)
    as_of = candle_end(closed.index[-1], timeframe, closed.attrs).timestamp() if len(closed) else time.time()
    if expected_bar_end is not None and (not len(closed) or abs(as_of*1000-expected_bar_end)>1):
        raise AnalysisBarChangedError("Abgeschlossene Kerze hat inzwischen gewechselt; keine rückwirkende Analyse.")
    technical = run_technical_analysis(closed)
    meta = market_metadata(df, ticker, timeframe)
    if timeframe in ("5m", "15m"):
        try:
            ml.store_frame(ticker, timeframe, closed)
        except Exception as exc:
            logger.warning("LightGBM candle archive unavailable (%s)", type(exc).__name__)
    options_data = options.saved_context(ticker, spot=float(df.Close.iloc[-1])) if mode == "gemini" else None
    if mode == "gemini":
        technical = {**technical, "options_context": options.for_gemini(options_data)}
    market_bars = serialize_klines(df)
    quote_observed_at = int(df.attrs.get("fetched_at", time.time()) * 1000)
    for bar, timestamp in zip(market_bars, df.index):
        bar["end_timestamp"] = int(candle_end(timestamp, timeframe, df.attrs).timestamp() * 1000)
        bar["observed_at"] = quote_observed_at
    ensure_analysis_mode(mode)
    if mode == "python":
        retire_pending_python_plan(ticker,timeframe)
    monitor_signal_market(ticker, timeframe, market_bars, stale=meta["is_stale"], mode=mode,
                          technical=technical, completed_bar_times=[int(candle_end(stamp, timeframe, closed.attrs).timestamp()*1000) for stamp in closed.index])
    previous = previous_signal_context(ticker, timeframe, mode)
    if mode == "python":
        # The quote locates the entry; indicators and FVGs still use closed candles only.
        technical = {**technical, "current_price": float(df.Close.iloc[-1])}
        forecast = build_technical_forecast(ticker,timeframe,technical,as_of=as_of,observed_at=quote_observed_at,market=meta)
        lower_contexts = {}
        if forecast["status"] == "AVAILABLE" and lower_timeframes(timeframe):
            lower_contexts = load_lower_timeframe_contexts(ticker, timeframe, observed_at=quote_observed_at)
            forecast = build_technical_forecast(ticker, timeframe, technical, as_of=as_of,
                                               observed_at=quote_observed_at, market=meta, lower_contexts=lower_contexts)
        ensure_analysis_mode(mode)
        decision = forecast_decision(forecast,technical)
        proposal = {"signal_type":decision["signal_type"],"forecast":forecast}
        context_id = hashlib.sha256(json.dumps({"mode":mode,"strategy_version":FORECAST_VERSION,
                                               "ticker":ticker,"timeframe":timeframe,"bar":as_of,
                                               "technical":technical,"lower_timeframes":lower_contexts,
                                               "stale":meta["is_stale"],"open":meta["market_open"]},sort_keys=True).encode()).hexdigest()
        news_context = {
            "news": [], "hard_data": {"items": [], "snapshot_id": "disabled", "status": "disabled", "sources": [], "fetched_at": None},
            "macro_analysis": None, "ai_trade_proposal": proposal, "analysis_method": "python",
            "decision_policy": {"version": FORECAST_VERSION,
                                "min_reward_risk": minimum_reward_risk("python"), "entry_selection": "timeframe_projection",
                                "min_target_distance_pct": MIN_TARGET_DISTANCE_PCT[timeframe],
                                "timeframe_min_target_distance_pct": MIN_TARGET_DISTANCE_PCT,
                                "poi_material_atr": POI_MATERIAL_ATR, "poi_timeframes": lower_timeframes(timeframe),
                                "fvg_nearby_atr": FVG_NEARBY_ATR, "fvg_similar_ratio": FVG_SIMILAR_RATIO,
                                "decision_weights_pct": {"technical": 100, "hard_data": 0, "normal_news": 0}, "macro_weights_pct": {}},
            "pipeline": {"normal_sources": [], "hard_sources": [], "normal_candidates": 0, "normal_feed_items": 0,
                         "normal_locally_matched": 0, "normal_relevant": 0, "normal_evaluated": 0,
                         "selection_limit": 0, "ai_search_enabled": False, "ai_batching": "disabled",
                         "ai_status": "disabled", "feed_status": "disabled", "context_id": context_id, "cache_hit": False},
        }
    else:
        news_context = get_news_context_and_store(
            ticker, os.getenv("GEMINI_API_KEY"), technical=technical, timeframe=timeframe,
            analysis_bar_at=int(as_of * 1000), previous_signal=previous,
        )
        decision = validate_ai_trade_decision(
            ticker, timeframe, {**technical, "current_price": float(df.Close.iloc[-1])}, news_context.get("ai_trade_proposal"), as_of=as_of,
            analysis_method=news_context.get("analysis_method"),
        )
    news = news_context["news"]
    ensure_analysis_mode(mode)
    ml_prediction = ml.assess(ticker, timeframe, closed, stale=meta["is_stale"])
    decision, ml_action = ml.filter_decision(decision, ml_prediction, previous=previous if mode == "gemini" else None)
    ml_prediction = {**ml_prediction, "filter_action": ml_action, "engine": "lightgbm"}
    technical = {**technical, "lightgbm": ml_prediction}
    if ml_prediction.get("usable"):
        technical = {**technical, "lightgbm": ml_prediction}
        # Gemini itself is cached independently; a changed local model only rechecks the local veto.
        suffix = ":ml:" + str(ml_prediction["model_id"]) + ":" + ml_action
        news_context = {**news_context, "pipeline": {**news_context["pipeline"],
                         "context_id": str(news_context["pipeline"].get("context_id", "")) + suffix}}
        if mode == "python":
            context_id += suffix
    if mode == "python":
        if ml_action == "BLOCKED":
            forecast = {**forecast, "trade_eligible": False, "trade_reason": decision["reasons"][0]}
        save_forecast(context_id,forecast)
    raw_decision = decision
    proposal = news_context.get("ai_trade_proposal") or {}
    backtest_origin = (backtest_archive.record_context(ticker, timeframe, technical, news_context, raw_decision, meta, previous)
                       if mode == "gemini" else None)
    decision = raw_decision if mode == "python" else manage_signal(
        ticker, timeframe, decision, proposal, analysis_method=news_context.get("analysis_method"),
        context_id=str(news_context.get("pipeline", {}).get("context_id", "")) + ":" + str(news_context.get("analysis_method")),
        bar_at=int(as_of * 1000),
        completed_bar_times=[int(candle_end(stamp, timeframe, closed.attrs).timestamp() * 1000) for stamp in closed.index],
        market_bars=market_bars, hard_bundle=news_context.get("hard_data"),
        review=proposal.get("previous_setup_review"), market_stale=meta["is_stale"], mode=mode, technical=technical,
        backtest_origin=backtest_origin,
    )
    decision["history"] = signal_history(ticker, timeframe, limit=8, mode=mode)
    result = {
        "ticker": ticker, "timeframe": timeframe, "analysis_mode": mode, "technical": technical, "news": news,
        "hard_data": news_context["hard_data"], "macro_analysis": news_context["macro_analysis"],
        "news_pipeline": news_context["pipeline"], "ai_analysis_method": news_context["analysis_method"],
        "decision": decision, "latest_ai_decision": raw_decision,
        "lightgbm": ml_prediction,
        "decision_policy": news_context.get("decision_policy") or decision_policy(timeframe),
        "market": meta, "analyzed_at": int(time.time() * 1000),
        "analysis_bar_at": int(closed.index[-1].timestamp() * 1000) if len(closed) else None,
    }
    if mode == "gemini":
        result["options_context"] = options_data
        result["quantitative"] = None
        result["quantitative_settings"] = quantitative_settings()
    if mode == "python":
        result.update(forecast=forecast,forecast_history=forecast_history(ticker,timeframe))
    if persist:
        save_signal(decision)
    if with_backtest and mode == "python":
        # Saved runs are small local files. Read them fresh so a finished replay
        # replaces a previous unavailable result without waiting for a new bar.
        result["backtest"] = run_backtest_strategy(closed, ticker=ticker, timeframe=timeframe, mode="python")
    elif with_backtest:
        result["backtest"] = run_backtest_strategy(closed, ticker=ticker, timeframe=timeframe, mode="gemini")
    return result


@app.get("/")
def index_root():
    if (OBSERVATORY_DIST/"index.html").is_file():
        return FileResponse(OBSERVATORY_DIST/"index.html", headers={"Cache-Control": "no-cache"})
    return trading_frontend()


@app.get("/trading")
def trading_frontend():
    if (SITE_DIST/"index.html").is_file():
        return FileResponse(SITE_DIST/"index.html",headers={"Cache-Control":"no-cache"})
    return {"service": "AI Trading Intelligence Platform", "docs": "/docs", "health": "/api/health"}


@app.get("/api/observatory")
def observatory_snapshot():
    from observatory_data import snapshot
    return {**snapshot(), "background": background_status()}


class BacktestRun(BaseModel):
    ticker: str
    timeframe: Literal["5m", "15m"] = "15m"
    profile: Literal["python", "gemini_quant"]
    refresh_data: bool = True


class BackgroundSelection(BaseModel):
    enabled: bool
    symbols: list[str]
    timeframes: list[Literal["5m", "15m"]]
    python_enabled: bool = True
    gemini_enabled: bool = True
    api_hour_limit: int = 48
    api_day_limit: int = 256
    report_interval_minutes: int = 60


@app.get("/api/background")
def background_status():
    from background_service import status
    return status()


@app.post("/api/background/settings")
def change_background(selection: BackgroundSelection):
    try:
        result = background_settings.save_settings(selection.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc
    worker = getattr(app.state,"background_worker",None)
    if worker:
        worker.wake.set()
    return result


@app.get("/api/backtests")
def backtest_comparison(ticker: str = "NVDA", timeframe: Timeframe = "15m"):
    try:
        return backtests.comparison(normalize_ticker(ticker), timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/backtests/run", status_code=202)
def start_backtest(request: BacktestRun):
    try:
        return backtests.start(normalize_ticker(request.ticker), request.timeframe, request.profile, refresh_data=request.refresh_data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/backtests/jobs/{identity}")
def backtest_job(identity: str):
    result = backtests.job(identity)
    if result is None:
        raise HTTPException(status_code=404, detail="Backtest-Auftrag nicht gefunden.")
    return result


@app.get("/api/health")
def health():
    key = os.getenv("GEMINI_API_KEY", "")
    project_id = hashlib.sha256(str(Path(__file__).resolve().parent).casefold().encode()).hexdigest()
    return {"status": "ok", "gemini_configured": bool(key and not key.startswith("your_")), "strategy_version": STRATEGY_VERSION,
            "background_supported": True, "project_id": project_id}


@app.get("/api/system-status")
def system_status():
    """Read-only summary; never fetches quotes or starts analyses."""
    result = {"overall": "Backend erreichbar", "state": "ok",
              "backend": {"label": "Online", "state": "ok", "detail": "API ist erreichbar"}}
    try:
        background = background_status()
    except Exception:
        logger.warning("System status storage check failed")
        return {**result, "overall": "Backend erreichbar · Status eingeschränkt", "state": "warn",
                "data": {"label": "Unbekannt", "state": "warn", "detail": "Datenstatus konnte nicht gelesen werden."},
                "analysis": {"label": "Unbekannt", "state": "warn", "detail": "Analysestatus konnte nicht gelesen werden."}}
    settings, runtime = background["settings"], background["runtime"]
    running = settings["enabled"] and background["worker_alive"]
    state = runtime.get("status", "")
    if running and state == "MARKET_CLOSED":
        data_label, data_detail = "Marktpause", "Kursbeobachtung wartet auf die nächste Handelssitzung."
    elif running and state == "RUNNING" and not runtime.get("last_error"):
        data_label, data_detail = "Beobachtung aktiv", "Hintergrundprozess aktiv · Kurse können verzögert sein."
    elif settings["enabled"]:
        data_label, data_detail = "Wartet auf Daten", "Eine aktuelle Datenversorgung ist noch nicht bestätigt."
    else:
        data_label, data_detail = "Auf Abruf", "Kurse werden beim Öffnen des Dashboards geladen."
    result["data"] = {"label": data_label, "state": "ok" if data_label == "Beobachtung aktiv" else "warn", "detail": data_detail}
    last_error = bool(runtime.get("last_error"))
    analysis_label = "Prüfung erforderlich" if running and last_error else "Aktiv" if running else "Pausiert" if not settings["enabled"] else "Wartet auf Prozess"
    profiles = [name for name, enabled in (("Python", settings["python_enabled"]), ("Gemini", settings["gemini_enabled"])) if enabled]
    analysis_detail = " · ".join(profiles) or "Kein Analyseprofil ausgewählt"
    if settings["gemini_enabled"] and not background["gemini_configured"]:
        analysis_detail += " · Gemini-Schlüssel fehlt"
        if running:
            analysis_label = "Eingeschränkt"
    result["analysis"] = {"label": analysis_label, "state": "ok" if analysis_label == "Aktiv" else "warn", "detail": analysis_detail}
    if settings["enabled"] and (not running or last_error or analysis_label == "Eingeschränkt"):
        result.update(overall="Backend erreichbar · Betrieb eingeschränkt", state="warn")
    return result


@app.get("/api/instruments")
def instruments():
    return {"instruments": CATALOG, "timeframes": list(TIMEFRAME_MAP)}


@app.get("/api/search")
def search_stocks(q: str = Query(min_length=1, max_length=100)):
    query = q.strip()
    local = [item for item in CATALOG if query.upper() in item["symbol"] or query.lower() in item["name"].lower()
             or (query.upper() in ("GOLD", "XAU", "XAU/USD") and item["symbol"] == "XAUUSD")]
    try:
        response = requests.get("https://query2.finance.yahoo.com/v1/finance/search",
                                params={"q": query, "quotesCount": 8, "newsCount": 0},
                                headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
        response.raise_for_status()
        remote = [{"symbol": item["symbol"], "name": item.get("shortname") or item.get("longname") or item["symbol"],
                   "exchange": item.get("exchange", ""), "type": item.get("quoteType", "")}
                  for item in response.json().get("quotes", []) if item.get("symbol")]
    except (requests.RequestException, ValueError):
        remote = []
    unique = {item["symbol"]: item for item in local + remote}
    return list(unique.values())[:10]


@app.get("/api/klines")
def get_klines(ticker: str = Query("NVDA", max_length=30), timeframe: Timeframe = "15m"):
    ticker = valid_ticker(ticker)
    df = market_frame(ticker, timeframe)
    meta = market_metadata(df, ticker, timeframe)
    return {**meta, "market": meta, "timeframe": timeframe, "klines": serialize_klines(df)}


@app.get("/api/analysis")
def get_complete_analysis(ticker: str = Query("NVDA", max_length=30), timeframe: Timeframe = "15m",
                          mode: AnalysisMode | None = None, quant_revision: int | None = Query(None, ge=0)):
    return evaluate(valid_ticker(ticker), timeframe, mode=mode, quant_revision=quant_revision)


@app.get("/api/signals/history")
def get_signal_history(ticker: str = Query("NVDA", max_length=30), timeframe: Timeframe = "15m",
                       limit: int = Query(30, ge=1, le=100), mode: AnalysisMode | None = None):
    ticker = valid_ticker(ticker)
    mode = mode or get_analysis_mode()
    return {"ticker": ticker, "timeframe": timeframe, "analysis_mode": mode, "events": signal_history(ticker, timeframe, limit, mode)}


@app.get("/api/forecasts/history")
def read_forecast_history(ticker: str = Query("NVDA",max_length=30),timeframe: Timeframe = "15m",
                          limit: int = Query(30,ge=1,le=100)):
    return {"ticker":valid_ticker(ticker),"timeframe":timeframe,"forecasts":forecast_history(valid_ticker(ticker),timeframe,limit)}


def rank_candidates(candidates, min_strength, mode="gemini"):
    eligible = [item for item in candidates if
                item["decision"]["signal_type"] in ("BUY", "SELL")
                and item["decision"]["setup_quality"] in ("A+", "A", "B")
                and item["decision"]["strength"] >= min_strength
                and item["decision"].get("lifecycle_status", "VORBEREITET") in ("VORBEREITET", "AKTIV")
                and item["decision"].get("verification_status", "verified") == "verified"
                and (item["decision"]["risk_reward_ratio"] or 0) >= minimum_reward_risk(mode)
                and not item["market"]["is_stale"] and item["market"]["market_open"]]
    if mode == "python":
        def distance(item):
            decision = item["decision"]
            try:
                price = float(decision["current_price"])
                target = (item.get("forecast") or {}).get("target_price",decision.get("take_profit_1"))
                if target is None:
                    return float(decision.get("potential_profit_pct") or math.inf) / 100
                target = float(target)
                return abs(target-price)/price if price > 0 and math.isfinite(price) and math.isfinite(target) else math.inf
            except (KeyError, TypeError, ValueError):
                return math.inf
        return sorted(eligible, key=lambda item: (distance(item), -item["decision"]["strength"], item["ticker"]))
    return sorted(eligible, key=lambda item: (
        -item["decision"]["potential_profit_pct"], -item["decision"]["strength"],
        -item["decision"]["risk_reward_ratio"], item["ticker"]))


@app.get("/api/scan")
def scan_market(timeframe: Timeframe = "15m", min_strength: float = Query(75, ge=65, le=95),
                mode: AnalysisMode | None = None, quant_revision: int | None = Query(None, ge=0),
                symbols: str | None = None, direction: Literal["ALL","BUY","SELL"] = "ALL"):
    if isinstance(quant_revision,int):
        raise HTTPException(410,"Die alte Quant-Auswahl wurde durch LightGBM ersetzt. Website neu laden.")
    mode = selected_mode(mode)
    universe = DEFAULT_UNIVERSE
    if symbols is not None:
        universe = list(dict.fromkeys(valid_ticker(value) for value in symbols.split(",") if value.strip()))
        if not universe or not set(universe)<=set(DEFAULT_UNIVERSE):
            raise HTTPException(422,"Für den Scanner eine gültige Auswahl aus den angebotenen Märkten verwenden.")
    quant_enabled = False  # Legacy response field; LightGBM is the sole live quant engine.
    cache_key = (timeframe, min_strength, mode, ml.cache_revision(),tuple(universe),direction)
    with _scan_lock:
        cached = _scan_cache.get(cache_key)
        if cached and not quant_enabled and time.monotonic() - cached[0] < 60:
            return {**cached[1], "cached": True}
    def scan_one(ticker):
        try:
            return evaluate(ticker, timeframe, with_backtest=False, persist=False, mode=mode), None
        except ModeChangedError:
            raise
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else "Analyse nicht verfügbar."
            return None, {"ticker": ticker, "reason": str(detail)}
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(scan_one, universe))
    candidates = [result for result, _ in results if result]
    ensure_analysis_mode(mode)
    ranked = rank_candidates(candidates, min_strength, mode)
    if direction != "ALL":
        ranked = [item for item in ranked if item["decision"]["signal_type"]==direction]
    response = {
        "timeframe": timeframe, "analysis_mode": mode, "min_strength": min_strength, "universe": universe,
        "scanned": len(universe), "analyzed": len(candidates), "eligible": len(ranked), "direction_filter":direction,
        "best": ranked[0] if ranked else None, "errors": [error for _, error in results if error],
        "ranked_at": int(time.time() * 1000), "cached": False,
        "quantitative_enabled": quant_enabled,
        "ranking": ("Technische Hauptziele mit Timeframe-Mindeststrecke, vollständiger POI-Prüfung und mindestens 1R."
                    if mode == "python" else "Größtes TP1-Kurspotenzial in % unter starken A+/A/B-Setups mit mindestens 1,5R."),
        "message": "Keine passende Gelegenheit mit frischen Daten bei geöffnetem Markt gefunden." if not ranked else None,
    }
    with _scan_lock:
        _scan_cache[cache_key] = (time.monotonic(), response)
    return response


@app.websocket("/ws/quotes/{ticker}")
async def websocket_quotes(websocket: WebSocket, ticker: str, timeframe: Timeframe = "15m", mode: AnalysisMode | None = None):
    mode = mode or get_analysis_mode()
    if mode != get_analysis_mode():
        await websocket.close(code=1008)
        return
    origin = websocket.headers.get("origin")
    if origin and origin not in [item.strip() for item in origins]:
        await websocket.close(code=1008)
        return
    try:
        ticker = normalize_ticker(ticker)
    except ValueError:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        while True:
            if mode != get_analysis_mode():
                await websocket.close(code=1000)
                return
            try:
                df = await asyncio.to_thread(fetch_stock_df, ticker, timeframe)
                if not df.empty:
                    last = serialize_klines(df.tail(2))
                    for bar in last:
                        bar["observed_at"] = int(df.attrs.get("fetched_at", time.time()) * 1000)
                    meta = market_metadata(df, ticker, timeframe)
                    outlook = await asyncio.to_thread(evaluate,ticker,timeframe,with_backtest=False,mode=mode) if mode == "python" else None
                    if outlook:
                        monitored = outlook["decision"]
                    else:
                        # Local checks only: reuse this quote frame, never request another Gemini analysis.
                        closed = completed_bars(df, timeframe)
                        atr = float(calculate_atr(closed.High, closed.Low, closed.Close).iloc[-1]) if len(closed) >= 14 else None
                        ends = [int(candle_end(stamp, timeframe, closed.attrs).timestamp()*1000) for stamp in closed.index]
                        monitored = await asyncio.to_thread(monitor_signal_market, ticker, timeframe, last,
                                                            stale=meta["is_stale"], mode=mode,
                                                            technical={"atr": atr}, completed_bar_times=ends)
                    await websocket.send_json({"ticker": ticker, "timeframe": timeframe, "price": last[-1]["close"],
                                               "timestamp": last[-1]["timestamp"], "klines": last,
                                               "market": meta, "decision": monitored, "analysis_mode": mode,
                                               **({"forecast":outlook["forecast"],"forecast_history":outlook["forecast_history"],
                                                   "lightgbm":outlook.get("lightgbm")} if outlook else {})})
            except Exception as exc:
                logger.warning("Quote polling failed (%s)", type(exc).__name__)
                await websocket.send_json({"error": "Kursfeed vorübergehend nicht verfügbar.", "ticker": ticker, "timeframe": timeframe,"analysis_mode":mode})
            await asyncio.sleep(25)
    except (WebSocketDisconnect, RuntimeError):
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
