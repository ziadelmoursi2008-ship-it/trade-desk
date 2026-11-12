"""Gemini status and explicit access to the current technical replay."""

import json
import math
from pathlib import Path
from decision_engine import STRATEGY_VERSION


def run_backtest_strategy(df, initial_capital=5000.0, *, ticker="", timeframe="15m",
                          commission_rate=0.0005, slippage_rate=0.0005, historical_news=None,
                          mode="gemini", lower_frames=None, fx_rates=None, allow_short=False):
    if not math.isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("Initial capital must be positive.")
    if mode not in ('python', 'gemini'):
        raise ValueError('Choose python or gemini explicitly.')
    if mode == 'gemini':
        from backtest_service import slot
        archived = slot(ticker, timeframe, 'gemini_quant')
        if (archived['status'] == 'READY' and initial_capital == 5000.
                and commission_rate == .0005 and slippage_rate == .0005 and not allow_short):
            return archived['result']
        if archived['status'] == 'READY':
            archived = {'message': 'Gespeicherter Test passt nicht zu Kapital, Kosten oder Short-Einstellungen.'}
        return {'strategy_version': STRATEGY_VERSION, 'error': 'Gemini + Quant: ' + archived['message'],
                'initial_capital': initial_capital, 'final_capital': initial_capital,
                'total_return_pct': 0., 'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                'win_rate': 0., 'profit_factor': None, 'max_drawdown_pct': 0., 'recent_trades': [],
                'markers': [], 'scope': 'Keine rückwirkend erfundenen Gemini-Entscheidungen. Aufgezeichnete aktuelle Gemini+Quant-Aktivierungen.',
                'news_coverage_bars': 0, 'costs': '', 'assumptions': '', 'position_sizing': ''}
    if mode == 'python':
        from technical_backtest import Costs, EuroRates, Replay, simulate_portfolio
        if lower_frames is None and fx_rates is None:
            return load_technical_backtest(ticker, timeframe, initial_capital=initial_capital,
                                           commission_rate=commission_rate, slippage_rate=slippage_rate,
                                           allow_short=allow_short)
        if lower_frames is None or fx_rates is None:
            raise ValueError('Historical candles and already-known FX rates must be supplied together.')
        frames = {**lower_frames, timeframe: df}
        from timeframe_analysis import lower_timeframes
        required = {'1m', timeframe, *lower_timeframes(timeframe)}
        if not required.issubset(frames):
            raise ValueError('Real execution and all required lower-timeframe candles are required.')
        if any(len(frames[name]) < 200 for name in required):
            raise ValueError('At least 200 warmup candles are required for every replay timeframe.')
        replay = Replay(frames)
        start = max(ends[199] for ends in replay.ends.values() if len(ends) >= 200)
        end = replay.ends['1m'][-1]
        candidates, diagnostics = replay.candidates(ticker, timeframe, start=start, end=end)
        return simulate_portfolio({ticker: frames['1m']}, candidates, EuroRates(fx_rates),
                                  timeframe=timeframe, start=start, end=end,
                                  initial_capital=initial_capital, allow_short=allow_short,
                                  costs=Costs(commission=commission_rate, slippage=slippage_rate),
                                  diagnostics={ticker: diagnostics})


def load_technical_backtest(ticker, timeframe, *, initial_capital=5000.,
                            commission_rate=0.0005, slippage_rate=0.0005, allow_short=False):
    """Only show an actual saved run for THIS stock; no expensive replay on polls."""
    from technical_forecast import FORECAST_VERSION
    # The app validates tickers; keep the file accessor safe independently.
    from instruments import normalize_ticker, TIMEFRAME_MAP
    from technical_backtest import strategy_fingerprint
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError('Unsupported timeframe.')
    ticker = normalize_ticker(ticker) if ticker else ''
    path = Path(__file__).resolve().parent.parent / 'backtest_results' / f'{ticker}_{timeframe}.json'
    reason = 'Für diese Aktie und diesen Timeframe liegt noch kein gespeicherter technischer Replay vor.'
    if path.exists():
        try:
            result = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            result = {}
        if not isinstance(result, dict):
            result = {}
        settings = result.get('cost_rates') or {}
        if not isinstance(settings, dict):
            settings = {}
        if (result.get('strategy_version') == FORECAST_VERSION
                and result.get('strategy_fingerprint') == strategy_fingerprint()
                and result.get('symbols') == [ticker] and result.get('timeframe') == timeframe
                and result.get('currency') == 'EUR' and result.get('analysis_mode') == 'python'
                and result.get('initial_capital') == initial_capital
                and result.get('allow_short') == allow_short
                and settings.get('commission') == commission_rate
                and settings.get('slippage') == slippage_rate):
            return result
        reason = 'Der gespeicherte Replay passt nicht zum aktuellen Strategiecode oder zu den angeforderten Einstellungen.'
    return {
        'strategy_version': FORECAST_VERSION, 'currency': 'EUR',
        'initial_capital': initial_capital, 'final_capital': initial_capital,
        'total_return_pct': 0., 'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
        'win_rate': 0., 'profit_factor': None, 'max_drawdown_pct': 0.,
        'recent_trades': [], 'markers': [], 'news_coverage_bars': 0,
        'scope': 'Aktuelle Python-Technik, Aktien und ausschließlich TP1.',
        'costs': 'Noch kein gespeicherter Lauf für diese Aktie und diesen Timeframe.',
        'position_sizing': '5.000 EUR, jeweils das gesamte verfügbare Kapital.',
        'assumptions': 'Ein echter Replay benötigt historische Haupt- und kleinere Timeframes sowie Euro-Umrechnung.',
        'error': reason + ' run_stock_backtest.py erneut ausführen.',
    }
