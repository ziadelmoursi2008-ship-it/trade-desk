"""Causal replay of technical_forecast 2.1, first target only, one funded position.

Forecasts use closed primary/lower candles. Orders fill at the following minute's
open; that minute's OHLC is used only AFTER the fill to evaluate protective exits.
No live signal tables, news, AI calls or retrospective zone construction.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from decision_engine import TECHNICAL_MIN_REWARD_RISK
from instruments import TIMEFRAME_MAP
from market_data import candle_end
from technical_analysis import build_indicator_frame, technical_at
from technical_forecast import FORECAST_VERSION, build_technical_forecast, forecast_decision
from timeframe_analysis import MIN_TARGET_DISTANCE_PCT, lower_timeframes

BACKTEST_VERSION = '1.1-tp1-pinned-strategy'
STRATEGY_FILES = ('technical_analysis.py', 'technical_forecast.py', 'technical_strategy.py',
                  'timeframe_analysis.py', 'decision_engine.py', 'instruments.py',
                  'market_data.py', 'technical_backtest.py')


def strategy_fingerprint():
    directory = Path(__file__).resolve().parent
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in STRATEGY_FILES}


def milliseconds(timestamp):
    return int(pd.Timestamp(timestamp).timestamp() * 1000)


def validate_frame(frame, timeframe):
    if frame.empty or not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError('Timestamped, timezone-aware real OHLCV data required.')
    if frame.attrs.get('timeframe') != timeframe:
        raise ValueError(f'Wrong timeframe metadata: expected {timeframe}.')
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError('Candles must be sorted and unique.')
    if not all(name in frame for name in ('Open', 'High', 'Low', 'Close', 'Volume')):
        raise ValueError('Complete OHLCV columns required.')
    values = frame[['Open', 'High', 'Low', 'Close']].to_numpy(dtype=float)
    if not np.isfinite(values).all() or not (values > 0).all():
        raise ValueError('OHLC must be finite and positive.')
    if ((frame.High < frame[['Open', 'Close', 'Low']].max(axis=1)) |
            (frame.Low > frame[['Open', 'Close', 'High']].min(axis=1))).any():
        raise ValueError('Invalid candle geometry.')
    if len(frame) > 1 and (frame.index.to_series().diff().dropna().dt.total_seconds() <
                           TIMEFRAME_MAP[timeframe]['seconds']).any():
        raise ValueError('Candle spacing is smaller than the claimed timeframe.')


class Replay:
    """Cache causal features; structure/zone functions always receive a prefix."""
    def __init__(self, frames, *, warmup=200):
        if warmup < 200:
            raise ValueError('Use at least 200 warmup candles for this replay.')
        self.warmup = warmup
        self.frames, self.ends, self.cached = {}, {}, {}
        for timeframe, frame in frames.items():
            validate_frame(frame, timeframe)
            self.frames[timeframe] = build_indicator_frame(frame)
            self.ends[timeframe] = pd.DatetimeIndex([
                candle_end(timestamp, timeframe, frame.attrs) for timestamp in frame.index])

    def at(self, timeframe, cutoff):
        if timeframe not in self.frames:
            return None, None
        index = int(self.ends[timeframe].searchsorted(cutoff, side='right')) - 1
        if index + 1 < self.warmup:
            return None, None
        end = self.ends[timeframe][index]
        if (pd.Timestamp(cutoff) - end).total_seconds() > max(900, TIMEFRAME_MAP[timeframe]['seconds'] * 3):
            return None, None
        key = (timeframe, index)
        if key not in self.cached:
            self.cached[key] = technical_at(self.frames[timeframe], index)
        return self.cached[key], end

    def candidates(self, ticker, timeframe, *, start, end):
        frame = self.frames[timeframe]
        diagnostics, proposals = Counter(), []
        for index in range(self.warmup - 1, len(frame)):
            cutoff = self.ends[timeframe][index]
            if not start <= cutoff < end:
                continue
            # No after-hours order is invented from the final regular-session bar.
            exchange_time = cutoff.tz_convert(frame.attrs.get('exchange_timezone', 'America/New_York'))
            if (exchange_time.hour, exchange_time.minute) >= (16, 0):
                diagnostics['market_closed'] += 1
                continue
            technical, _ = self.at(timeframe, cutoff)
            if not technical or technical.get('error'):
                diagnostics['primary_unavailable'] += 1
                continue
            technical = {**technical, 'current_price': float(frame.Close.iloc[index])}
            common = dict(as_of=cutoff.timestamp(), observed_at=milliseconds(cutoff),
                          market={'is_stale': False, 'market_open': True})
            forecast = build_technical_forecast(ticker, timeframe, technical, **common)
            if forecast['status'] == 'AVAILABLE' and lower_timeframes(timeframe):
                contexts = {}
                for lower in lower_timeframes(timeframe):
                    data, lower_end = self.at(lower, cutoff)
                    contexts[lower] = ({'status': 'AVAILABLE', 'technical': data,
                                        'analysis_bar_at': milliseconds(lower_end)}
                                       if data and not data.get('error') else
                                       {'status': 'UNAVAILABLE', 'reason': 'Historical candles missing, stale or warming up.'})
                forecast = build_technical_forecast(ticker, timeframe, technical, lower_contexts=contexts, **common)
            diagnostics['evaluated_bars'] += 1
            if not forecast['trade_eligible']:
                if forecast['status'] != 'AVAILABLE':
                    reason = forecast['status']
                elif forecast['poi_check']['status'] in ('BLOCKED', 'INCOMPLETE'):
                    reason = 'POI_' + forecast['poi_check']['status']
                elif forecast.get('risk_reward_ratio') is not None and forecast['risk_reward_ratio'] < TECHNICAL_MIN_REWARD_RISK:
                    reason = 'RISK_REWARD'
                else:
                    reason = 'INVALIDATION_OR_RISK'
                diagnostics[reason] += 1
                continue
            decision = forecast_decision(forecast, technical)
            if decision['validation_status'] != 'accepted' or decision['signal_type'] == 'NEUTRAL':
                diagnostics['DECISION_VALIDATION'] += 1
                continue
            proposals.append({
                'ticker': ticker, 'timeframe': timeframe, 'signal_at': milliseconds(cutoff),
                'direction': 1 if forecast['direction'] == 'UP' else -1,
                'reference_price': forecast['reference_price'], 'target': forecast['target_price'],
                'stop': forecast['invalidation_price'], 'strength': forecast['indicator_strength'],
                'atr': technical['atr'], 'target_basis': forecast['target_basis'],
                'poi_status': forecast['poi_check']['status'],
            })
            diagnostics['eligible_signals'] += 1
        return proposals, dict(diagnostics)


@dataclass(frozen=True)
class Costs:
    commission: float = 0.0005
    slippage: float = 0.0005
    half_spread: float = 0.0001
    short_borrow_annual: float = 0.03

    def __post_init__(self):
        for value in (self.commission, self.slippage, self.half_spread, self.short_borrow_annual):
            if not math.isfinite(value) or not 0 <= value < 1:
                raise ValueError('Invalid transaction cost rate.')


class EuroRates:
    """USD per EUR proxy, available only AFTER the daily FX candle has closed."""
    def __init__(self, series):
        self.series = series.sort_index()
        if self.series.empty or self.series.index.tz is None:
            raise ValueError('Historical USD-per-EUR conversion rates required.')
        if not np.isfinite(self.series.to_numpy()).all() or (self.series <= 0).any():
            raise ValueError('Invalid historical conversion rates.')

    def at(self, timestamp):
        index = int(self.series.index.searchsorted(timestamp, side='right')) - 1
        if index < 0:
            raise ValueError('No already-known FX rate at this timestamp.')
        if pd.Timestamp(timestamp) - self.series.index[index] > pd.Timedelta(days=7):
            raise ValueError('Historical conversion rate is stale.')
        return float(self.series.iloc[index])


def simulate_portfolio(execution_frames, candidates, rates, *, timeframe, start, end,
                       initial_capital=5000., allow_short=False, costs=None, diagnostics=None):
    """A single 5k account across stocks, with no concurrent reuse of the capital."""
    if not math.isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError('Initial capital must be finite and positive.')
    costs = costs or Costs()
    events, signals = defaultdict(dict), defaultdict(list)
    for ticker, frame in execution_frames.items():
        validate_frame(frame, '1m')
        if frame.attrs.get('instrument_type') != 'EQUITY' or frame.attrs.get('currency') != 'USD':
            raise ValueError('This EUR replay accepts verified USD-denominated stocks only.')
        for timestamp, row in frame.loc[(frame.index >= start) & (frame.index < end)].iterrows():
            if timestamp + pd.Timedelta(minutes=1) <= end:
                events[timestamp][ticker] = row
    for candidate in candidates:
        signals[pd.Timestamp(candidate['signal_at'], unit='ms', tz='UTC')].append(candidate)
    cash, position, trades = float(initial_capital), None, []
    peak, drawdown, equity_curve = cash, 0., []
    rejected = Counter()
    diagnostics = diagnostics or {}

    def valuation(price, timestamp):
        elapsed = max(0, (timestamp - position['entered']).total_seconds()) / (365.25 * 86400)
        borrow = (position['units'] * position['entry_price'] * costs.short_borrow_annual * elapsed
                  if position['direction'] == -1 else 0.)
        gross = position['direction'] * position['units'] * (price - position['entry_price'])
        exit_fee = position['units'] * price * costs.commission
        equity = (position['starting_usd'] - position['entry_fee_usd'] + gross - exit_fee - borrow) / rates.at(timestamp)
        return equity, gross, exit_fee, borrow

    def close(price, timestamp, reason):
        nonlocal cash, position
        cash, gross, exit_fee, borrow = valuation(price, timestamp)
        pnl = cash - position['starting_eur']
        trades.append({
            'ticker': position['ticker'], 'type': 'BUY' if position['direction'] == 1 else 'SELL',
            'entry_time': milliseconds(position['entered']), 'time': milliseconds(timestamp),
            'signal_at': position['signal_at'], 'entry_price': position['entry_price'], 'exit_price': price,
            'stop_loss': position['stop'], 'take_profit_1': position['target'],
            'units': position['units'], 'pnl': pnl, 'return_pct': pnl / position['starting_eur'] * 100,
            'capital_before': position['starting_eur'], 'capital_after': cash,
            'entry_fee_usd': position['entry_fee_usd'], 'exit_fee_usd': exit_fee,
            'borrow_cost_usd': borrow, 'gross_pnl_usd': gross,
            'entry_usd_per_eur': position['entry_fx'], 'exit_usd_per_eur': rates.at(timestamp),
            'tp1_taken': reason == 'TP1', 'reason': reason, 'target_basis': position['target_basis'],
        })
        position = None

    def exits(row, timestamp):
        direction, stop, target = position['direction'], position['stop'], position['target']
        adverse = costs.slippage + costs.half_spread
        if direction * (row.Open - stop) <= 0:
            close(float(row.Open) * (1 - direction * adverse), timestamp, 'STOP_GAP')
        elif direction * (row.Open - target) >= 0:
            close(target, timestamp, 'TP1')  # Resting limit: never worse than target.
        else:
            stop_hit = row.Low <= stop if direction == 1 else row.High >= stop
            target_hit = row.High >= target if direction == 1 else row.Low <= target
            if stop_hit:
                close(stop * (1 - direction * adverse), timestamp + pd.Timedelta(minutes=1),
                      'STOP_BOTH_HIT' if target_hit else 'STOP')
            elif target_hit:
                close(target, timestamp + pd.Timedelta(minutes=1), 'TP1')

    last_timestamp = None
    last_prices = {}
    for timestamp in sorted(events):
        rows = events[timestamp]
        last_timestamp = timestamp
        was_in_position = position is not None
        if position is not None and position['ticker'] in rows:
            exits(rows[position['ticker']], timestamp)
        todays = signals.get(timestamp, [])
        if was_in_position:
            rejected['capital_busy'] += len(todays)
        elif cash > 0:
            accepted = []
            for candidate in todays:
                if candidate['direction'] < 0 and not allow_short:
                    rejected['sell_skipped_long_only'] += 1
                    continue
                if candidate['ticker'] not in rows:
                    rejected['missing_execution_candle'] += 1
                    continue
                row, direction = rows[candidate['ticker']], candidate['direction']
                entry = float(row.Open) * (1 + direction * (costs.slippage + costs.half_spread))
                risk, reward = direction * (entry - candidate['stop']), direction * (candidate['target'] - entry)
                if (risk <= 0 or reward <= 0 or reward / risk < TECHNICAL_MIN_REWARD_RISK or
                        not 0.25 * candidate['atr'] <= risk <= 5 * candidate['atr'] or
                        reward / entry * 100 < MIN_TARGET_DISTANCE_PCT[timeframe]):
                    rejected['entry_gap_or_costs_invalidated_setup'] += 1
                    continue
                accepted.append((candidate, entry))
            # Deterministic selection using only information known at signal time.
            if accepted:
                accepted.sort(key=lambda item: (-item[0]['strength'], item[0]['ticker']))
                candidate, entry = accepted[0]
                rejected['simultaneous_signal_not_selected'] += len(accepted) - 1
                fx = rates.at(timestamp)
                starting_usd = cash * fx
                units = starting_usd / (entry * (1 + costs.commission))
                position = {**candidate, 'entry_price': entry, 'units': units, 'entered': timestamp,
                            'starting_usd': starting_usd, 'starting_eur': cash, 'entry_fx': fx,
                            'entry_fee_usd': units * entry * costs.commission}
                exits(rows[position['ticker']], timestamp)
        for ticker, row in rows.items():
            last_prices[ticker] = float(row.Close)
        marked = (valuation(last_prices[position['ticker']], timestamp + pd.Timedelta(minutes=1))[0]
                  if position else cash)
        peak = max(peak, marked)
        drawdown = max(drawdown, (peak - marked) / peak * 100)
        equity_curve.append({'timestamp': milliseconds(timestamp + pd.Timedelta(minutes=1)), 'equity': marked})
    if position:
        timestamp = last_timestamp + pd.Timedelta(minutes=1)
        price = last_prices[position['ticker']] * (1 - position['direction'] * (costs.slippage + costs.half_spread))
        close(price, timestamp, 'END_OF_DATA')
        peak = max(peak, cash)
        drawdown = max(drawdown, (peak - cash) / peak * 100)
        equity_curve[-1]['equity'] = cash
    gains = sum(max(trade['pnl'], 0.) for trade in trades)
    losses = -sum(min(trade['pnl'], 0.) for trade in trades)
    winners = sum(trade['pnl'] > 0 for trade in trades)
    markers = [{'timestamp': trade['entry_time'], 'type': trade['type'], 'price': trade['entry_price']}
               for trade in trades]
    return {
        'strategy_version': FORECAST_VERSION, 'backtest_version': BACKTEST_VERSION,
        'strategy_fingerprint': strategy_fingerprint(),
        'cost_rates': {'commission': costs.commission, 'slippage': costs.slippage,
                       'half_spread': costs.half_spread, 'short_borrow_annual': costs.short_borrow_annual},
        'analysis_mode': 'python', 'timeframe': timeframe, 'currency': 'EUR',
        'quote_currency': 'USD', 'symbols': sorted(execution_frames),
        'initial_capital': initial_capital, 'final_capital': round(cash, 6),
        'total_return_pct': round((cash / initial_capital - 1) * 100, 6),
        'total_trades': len(trades), 'winning_trades': winners,
        'losing_trades': sum(trade['pnl'] < 0 for trade in trades),
        'win_rate': round(winners / len(trades) * 100, 4) if trades else 0.,
        'profit_factor': gains / losses if losses else None,
        'max_drawdown_pct': round(drawdown, 6),
        'expectancy_eur': sum(trade['pnl'] for trade in trades) / len(trades) if trades else None,
        'start_at': milliseconds(start), 'end_at': milliseconds(end),
        'recent_trades': trades[-100:], 'trades': trades, 'markers': markers,
        'equity_curve': equity_curve, 'diagnostics': diagnostics,
        'execution_rejections': dict(rejected), 'exit_reasons': dict(Counter(trade['reason'] for trade in trades)),
        'news_coverage_bars': 0, 'allow_short': allow_short,
        'scope': 'Aktien; aktuelle Python-Technik inklusive aller kleineren POI-Timeframes; vollständiger Ausstieg an TP1 oder Stop.',
        'position_sizing': 'Ein Konto; maximal eine Position; 100 % des jeweils verfügbaren Kapitals einschließlich Kaufgebühr. Bruchstücke; keine Hebelung; Gewinne/Verluste werden reinvestiert.',
        'costs': f'Je Ausführung {costs.commission*100:.3f} % Gebühr; Marktorders zusätzlich {costs.slippage*100:.3f} % Slippage + {costs.half_spread*100:.3f} % halber Spread. TP1 als Limit zum Zielkurs.',
        'assumptions': 'Signale einmal je abgeschlossener Hauptkerze, Einstieg nächster 1m-Open. 200 Warmup-Kerzen je Timeframe. Unveränderte ursprüngliche TP1/Stop-Level; kein TP2 und kein Trailing. Bei Stop und TP in derselben Minute zählt Stop zuerst. Offene Position am Datenende geschlossen. Übernachtpositionen erlaubt. Drawdown aus 1m-Schlussbewertungen inklusive Ausstiegskosten. Euro-Abrechnung mit zuletzt abgeschlossener täglicher EURUSD-Kerze als Umrechnungsproxy; keine Intraday-FX-Genauigkeit. Keine KI/News. Keine Parameteroptimierung.',
        'limitations': ['Kurzer gemeinsamer Zeitraum wegen 1m-Daten; kein Langzeitnachweis.',
                        'Heutige feste Aktienauswahl; keine historische Universumsrekonstruktion.',
                        'Bruchstückhandel und Ausführung am TP-Limit ohne Liquiditäts-/Warteschlangenmodell.'] +
                       (['Leerverkäufe modellhaft, 3 % p.a. Leihkosten; tatsächliche Verfügbarkeit und Dividendenausgleich nicht modelliert.'] if allow_short else []),
    }
