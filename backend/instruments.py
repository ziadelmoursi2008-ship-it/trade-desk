import re

TIMEFRAME_MAP = {
    "1m": {"period": "5d", "interval": "1m", "seconds": 60},
    "5m": {"period": "1mo", "interval": "5m", "seconds": 300},
    "15m": {"period": "1mo", "interval": "15m", "seconds": 900},
    "30m": {"period": "1mo", "interval": "30m", "seconds": 1800},
    "1h": {"period": "3mo", "interval": "1h", "seconds": 3600},
    "4h": {"period": "1y", "interval": "1h", "seconds": 14400},
    "1D": {"period": "2y", "interval": "1d", "seconds": 86400},
}
CATALOG = [
    {"symbol": symbol, "name": name, "exchange": exchange, "type": kind}
    for symbol, name, exchange, kind in [
        ("NVDA", "NVIDIA", "NASDAQ", "EQUITY"), ("TSLA", "Tesla", "NASDAQ", "EQUITY"),
        ("AMD", "Advanced Micro Devices", "NASDAQ", "EQUITY"), ("AAPL", "Apple", "NASDAQ", "EQUITY"),
        ("MSFT", "Microsoft", "NASDAQ", "EQUITY"), ("AMZN", "Amazon", "NASDAQ", "EQUITY"),
        ("GOOGL", "Alphabet Google", "NASDAQ", "EQUITY"), ("META", "Meta Platforms", "NASDAQ", "EQUITY"),
        ("PLTR", "Palantir", "NASDAQ", "EQUITY"), ("COIN", "Coinbase", "NASDAQ", "EQUITY"),
        ("SPY", "S&P 500 ETF", "NYSE", "ETF"), ("QQQ", "Nasdaq 100 ETF", "NASDAQ", "ETF"),
        ("XAUUSD", "Gold / XAUUSD — Gold-Futures-Proxy GC=F", "COMEX", "FUTURE"),
        ("BTC-USD", "Bitcoin", "CRYPTO", "CRYPTOCURRENCY"),
        ("ETH-USD", "Ethereum", "CRYPTO", "CRYPTOCURRENCY"),
        ("SOL-USD", "Solana", "CRYPTO", "CRYPTOCURRENCY"),
    ]
]
DEFAULT_UNIVERSE = [item["symbol"] for item in CATALOG]


def normalize_ticker(ticker):
    ticker = ticker.strip().upper()
    ticker = {"GOLD": "XAUUSD", "XAU/USD": "XAUUSD", "XAUUSD=X": "XAUUSD", "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD"}.get(ticker, ticker)
    if not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=\-]{0,29}", ticker):
        raise ValueError("Ungültiges Symbol.")
    return ticker


def source_ticker(ticker):
    return "GC=F" if ticker == "XAUUSD" else ticker


def instrument_name(ticker):
    return next((item["name"] for item in CATALOG if item["symbol"] == ticker), ticker)
