import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).with_name("trading_platform.db")


def get_connection():
    connection = sqlite3.connect(os.getenv("TRADING_DB_PATH", str(DB_PATH)), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with closing(get_connection()) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('analysis_mode','gemini')")
        connection.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('quantitative_enabled','false')")
        connection.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('quantitative_revision','0')")
        connection.execute("""CREATE TABLE IF NOT EXISTS background_api_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
            timeframe TEXT NOT NULL, attempted_at REAL NOT NULL
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS background_api_time ON background_api_attempts(attempted_at)")
        connection.execute("""CREATE TABLE IF NOT EXISTS background_runs (
            id TEXT PRIMARY KEY, profile TEXT NOT NULL, ticker TEXT NOT NULL, timeframe TEXT NOT NULL,
            bar_end INTEGER NOT NULL, strategy_key TEXT NOT NULL, status TEXT NOT NULL,
            started_at REAL NOT NULL, finished_at REAL, result_json TEXT,
            UNIQUE(profile,ticker,timeframe,bar_end,strategy_key)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS background_runtime (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS background_signal_states (
            ticker TEXT NOT NULL, timeframe TEXT NOT NULL, state_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL, PRIMARY KEY(ticker,timeframe)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS background_signal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, timeframe TEXT NOT NULL,
            setup_id TEXT, event_key TEXT NOT NULL, event_type TEXT NOT NULL,
            analysis_bar_at INTEGER, created_at INTEGER NOT NULL, state_json TEXT NOT NULL,
            UNIQUE(ticker,timeframe,event_key)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS background_price_bars (
            ticker TEXT NOT NULL, timeframe TEXT NOT NULL, bar_at INTEGER NOT NULL, bar_end INTEGER NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
            volume REAL, currency TEXT, exchange_timezone TEXT NOT NULL,
            PRIMARY KEY(ticker,timeframe,bar_at)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS backtest_context_archive (
            id TEXT PRIMARY KEY, ticker TEXT NOT NULL, timeframe TEXT NOT NULL,
            strategy_key TEXT NOT NULL, context_id TEXT NOT NULL, available_at INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS backtest_context_lookup ON backtest_context_archive(ticker,timeframe,strategy_key)")
        connection.execute("""CREATE TABLE IF NOT EXISTS backtest_event_archive (
            id TEXT PRIMARY KEY, context_id TEXT NOT NULL, ticker TEXT NOT NULL, timeframe TEXT NOT NULL,
            strategy_key TEXT NOT NULL, setup_id TEXT, event_type TEXT NOT NULL,
            available_at INTEGER NOT NULL, state_json TEXT NOT NULL
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS backtest_event_lookup ON backtest_event_archive(ticker,timeframe,strategy_key,setup_id)")
        for table in ("backtest_context_archive", "backtest_event_archive"):
            columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if "stream" not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN stream TEXT NOT NULL DEFAULT 'interactive'")
        connection.execute("""CREATE TABLE IF NOT EXISTS backtest_jobs (
            id TEXT PRIMARY KEY, ticker TEXT NOT NULL, timeframe TEXT NOT NULL, profile TEXT NOT NULL,
            status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, result_json TEXT
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS options_context_cache (
            ticker TEXT NOT NULL, version TEXT NOT NULL, config_hash TEXT NOT NULL,
            attempted_at REAL NOT NULL, snapshot_json TEXT, chain_json TEXT, last_error TEXT,
            PRIMARY KEY(ticker,version)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS options_context_snapshots (
            id TEXT PRIMARY KEY, ticker TEXT NOT NULL, version TEXT NOT NULL,
            computed_at REAL NOT NULL, snapshot_json TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS quantitative_bars (
            ticker TEXT NOT NULL, timeframe TEXT NOT NULL, version TEXT NOT NULL,
            bar_at INTEGER NOT NULL, bar_end INTEGER NOT NULL, input_hash TEXT NOT NULL,
            open REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
            delta_sign INTEGER NOT NULL DEFAULT 0, delta REAL NOT NULL DEFAULT 0,
            cvd REAL NOT NULL DEFAULT 0, PRIMARY KEY(ticker,timeframe,version,bar_at)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS quantitative_cache (
            ticker TEXT NOT NULL, timeframe TEXT NOT NULL, version TEXT NOT NULL,
            input_hash TEXT NOT NULL, main_hash TEXT NOT NULL, analysis_bar_at INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL, PRIMARY KEY(ticker,timeframe,version)
        )""")
        quant_columns = {row["name"] for row in connection.execute("PRAGMA table_info(quantitative_bars)")}
        if "volume_known" not in quant_columns:
            connection.execute("ALTER TABLE quantitative_bars ADD COLUMN volume_known INTEGER NOT NULL DEFAULT 0")
        connection.execute("""CREATE TABLE IF NOT EXISTS quantitative_snapshots (
            id TEXT PRIMARY KEY, ticker TEXT NOT NULL, timeframe TEXT NOT NULL,
            version TEXT NOT NULL, computed_at INTEGER NOT NULL, snapshot_json TEXT NOT NULL
        )""")
        # Preserve the legacy news table and all previous signals.
        connection.execute("""CREATE TABLE IF NOT EXISTS news_items (
            ticker TEXT NOT NULL, url TEXT NOT NULL, headline TEXT NOT NULL,
            source TEXT, published_at TEXT, published_ts REAL, sentiment REAL,
            relevance REAL, credibility REAL, novelty REAL, impact_score REAL,
            event_type TEXT, summary TEXT, analysis_method TEXT,
            analyzed_at REAL, context_id TEXT, prefilter_score REAL,
            PRIMARY KEY (ticker, url)
        )""")
        news_columns = {row["name"] for row in connection.execute("PRAGMA table_info(news_items)")}
        for column, definition in (("context_id", "TEXT"), ("prefilter_score", "REAL"),
                                   ("importance_score", "REAL"), ("importance_reason", "TEXT"),
                                   ("event_key", "TEXT"), ("effect_horizon", "TEXT")):
            if column not in news_columns:
                connection.execute(f"ALTER TABLE news_items ADD COLUMN {column} {definition}")
        connection.execute("""CREATE TABLE IF NOT EXISTS hard_economic_data (
            series_id TEXT NOT NULL, period TEXT NOT NULL, name TEXT NOT NULL,
            category TEXT NOT NULL, value REAL NOT NULL, unit TEXT NOT NULL,
            previous_value REAL, previous_period TEXT, change_value REAL,
            change_unit TEXT, year_over_year_pct REAL, source TEXT NOT NULL,
            source_url TEXT NOT NULL, preliminary INTEGER NOT NULL DEFAULT 0,
            fetched_at REAL NOT NULL, PRIMARY KEY (series_id, period)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS macro_analyses (
            ticker TEXT NOT NULL, snapshot_id TEXT NOT NULL, sentiment REAL NOT NULL,
            relevance REAL NOT NULL, credibility REAL NOT NULL, novelty REAL NOT NULL,
            impact_score REAL NOT NULL, summary TEXT NOT NULL,
            analysis_method TEXT NOT NULL, analyzed_at REAL NOT NULL,
            hard_data_json TEXT NOT NULL, PRIMARY KEY (ticker, snapshot_id)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS ai_analysis_contexts (
            context_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, timeframe TEXT NOT NULL,
            analysis_bar_at INTEGER, hard_snapshot_id TEXT, model TEXT NOT NULL,
            response_json TEXT NOT NULL, created_at REAL NOT NULL
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS ai_context_lookup ON ai_analysis_contexts(ticker, timeframe, analysis_bar_at)")
        context_columns = {row["name"] for row in connection.execute("PRAGMA table_info(ai_analysis_contexts)")}
        if "request_key" not in context_columns:
            connection.execute("ALTER TABLE ai_analysis_contexts ADD COLUMN request_key TEXT")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS ai_request_lookup ON ai_analysis_contexts(request_key)")
        connection.execute("""CREATE TABLE IF NOT EXISTS signal_states (
            ticker TEXT NOT NULL, timeframe TEXT NOT NULL, state_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL, PRIMARY KEY (ticker, timeframe)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS signal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, timeframe TEXT NOT NULL,
            setup_id TEXT, event_key TEXT NOT NULL, event_type TEXT NOT NULL,
            analysis_bar_at INTEGER, created_at INTEGER NOT NULL, state_json TEXT NOT NULL,
            UNIQUE (ticker, timeframe, event_key)
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS signal_history_lookup ON signal_events(ticker, timeframe, id DESC)")
        connection.execute("""CREATE TABLE IF NOT EXISTS technical_signal_states (
            ticker TEXT NOT NULL, timeframe TEXT NOT NULL, state_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL, PRIMARY KEY (ticker, timeframe)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS technical_signal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, timeframe TEXT NOT NULL,
            setup_id TEXT, event_key TEXT NOT NULL, event_type TEXT NOT NULL,
            analysis_bar_at INTEGER, created_at INTEGER NOT NULL, state_json TEXT NOT NULL,
            UNIQUE (ticker, timeframe, event_key)
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS technical_signal_history_lookup ON technical_signal_events(ticker,timeframe,id DESC)")
        connection.execute("""CREATE TABLE IF NOT EXISTS news_feed_snapshots (
            ticker TEXT NOT NULL, source TEXT NOT NULL, items_json TEXT NOT NULL,
            fetched_at REAL NOT NULL, PRIMARY KEY (ticker, source)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS trade_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
            timeframe TEXT NOT NULL, signal_type TEXT NOT NULL, score REAL NOT NULL,
            setup_quality TEXT, current_price REAL NOT NULL, entry_price REAL NOT NULL,
            entry_zone_min REAL NOT NULL, entry_zone_max REAL NOT NULL,
            stop_loss REAL NOT NULL, take_profit_1 REAL NOT NULL, take_profit_2 REAL NOT NULL,
            risk_reward_ratio REAL NOT NULL, reasons TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            strategy_version TEXT DEFAULT 'legacy', as_of INTEGER
        )""")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(trade_signals)")}
        for column, definition in (("strategy_version", "TEXT DEFAULT 'legacy'"), ("as_of", "INTEGER")):
            if column not in columns:
                connection.execute(f"ALTER TABLE trade_signals ADD COLUMN {column} {definition}")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS signal_candle_version ON trade_signals(ticker, timeframe, as_of, strategy_version)")
        # Additive storage: the legacy flat table requires TP2, new Python plans may have one target.
        connection.execute("""CREATE TABLE IF NOT EXISTS technical_trade_plans (
            ticker TEXT NOT NULL, timeframe TEXT NOT NULL, as_of INTEGER NOT NULL,
            strategy_version TEXT NOT NULL, setup_id TEXT NOT NULL, decision_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker,timeframe,as_of,strategy_version,setup_id)
        )""")


    # Separate transactions keep migrations away from the existing signal/archive tables.
    from lightgbm_service import init_storage
    init_storage()


def save_signal(decision: dict):
    if decision["signal_type"] == "NEUTRAL":
        return
    if decision.get("decision_source") == "python":
        setup_id = decision.get("setup_id") or json.dumps([
            decision.get(key) for key in ("signal_type", "entry_price", "stop_loss", "take_profit_1", "take_profit_2")])
        with closing(get_connection()) as connection, connection:
            connection.execute("INSERT OR IGNORE INTO technical_trade_plans "
                               "(ticker,timeframe,as_of,strategy_version,setup_id,decision_json) VALUES(?,?,?,?,?,?)",
                               (decision["ticker"],decision["timeframe"],decision["as_of"],decision["strategy_version"],
                                setup_id,json.dumps(decision,ensure_ascii=False,allow_nan=False)))
        return
    fields = ["ticker", "timeframe", "signal_type", "score", "setup_quality", "current_price", "entry_price", "entry_zone_min", "entry_zone_max", "stop_loss", "take_profit_1", "take_profit_2", "risk_reward_ratio", "strategy_version", "as_of"]
    with closing(get_connection()) as connection, connection:
        connection.execute(
            f"INSERT OR IGNORE INTO trade_signals ({','.join(fields)},reasons) VALUES ({','.join('?' for _ in range(len(fields) + 1))})",
            [decision[field] for field in fields] + [json.dumps(decision["reasons"], ensure_ascii=False)],
        )


def save_forecast(context_id, forecast):
    with closing(get_connection()) as connection, connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS technical_forecast_snapshots (
            context_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, timeframe TEXT NOT NULL,
            generated_at INTEGER NOT NULL, forecast_json TEXT NOT NULL
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS technical_forecast_history ON technical_forecast_snapshots(ticker,timeframe,generated_at)")
        connection.execute("INSERT OR IGNORE INTO technical_forecast_snapshots VALUES(?,?,?,?,?)",
                           (context_id,forecast["ticker"],forecast["timeframe"],forecast["generated_at"],
                            json.dumps(forecast,ensure_ascii=False,allow_nan=False)))


def forecast_history(ticker, timeframe, limit=8):
    with closing(get_connection()) as connection:
        exists = connection.execute("SELECT name FROM sqlite_master WHERE name='technical_forecast_snapshots'").fetchone()
        if not exists:
            return []
        rows = connection.execute("SELECT context_id,forecast_json FROM technical_forecast_snapshots "
                                  "WHERE ticker=? AND timeframe=? ORDER BY generated_at DESC,rowid DESC LIMIT ?",
                                  (ticker,timeframe,limit)).fetchall()
        return [{"id":row["context_id"],"forecast":json.loads(row["forecast_json"])} for row in rows]
