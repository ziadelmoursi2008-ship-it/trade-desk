"""Replay correctness: capital, first target, temporal availability and costs."""
import copy
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from technical_analysis import build_indicator_frame, technical_at
from technical_backtest import Costs, EuroRates, Replay, simulate_portfolio


def candles(count=6, timeframe='1m'):
    index = pd.date_range('2026-10-21 13:30Z', periods=count,
                          freq=pd.Timedelta({'1m': '1min', '5m': '5min'}[timeframe]))
    frame = pd.DataFrame({'Open': 100., 'High': 100.2, 'Low': 99.8, 'Close': 100.,
                          'Volume': 1000.}, index=index)
    frame.attrs = {'timeframe': timeframe, 'currency': 'USD', 'instrument_type': 'EQUITY',
                   'exchange_timezone': 'America/New_York'}
    return frame


def candidate(timestamp, ticker='AAPL', direction=1, strength=80):
    return {'ticker': ticker, 'timeframe': '5m', 'signal_at': int(timestamp.timestamp()*1000),
            'direction': direction, 'reference_price': 100., 'target': 102. if direction == 1 else 98.,
            'stop': 99. if direction == 1 else 101., 'strength': strength, 'atr': 1.,
            'target_basis': 'test existing resistance', 'poi_status': 'CLEAR'}


class ExecutionTests(unittest.TestCase):
    def run_portfolio(self, frames, candidates, **kwargs):
        frame = next(iter(frames.values()))
        return simulate_portfolio(frames, candidates, EuroRates(pd.Series([1.],
            index=pd.DatetimeIndex(['2026-10-20T00:00Z']))), timeframe='5m',
            start=frame.index[0], end=frame.index[-1]+pd.Timedelta(minutes=1),
            costs=kwargs.pop('costs', Costs(0, 0, 0, 0)), **kwargs)

    def test_all_capital_exits_at_first_target_without_tp2(self):
        frame = candles()
        frame.loc[frame.index[1], 'High'] = 103.
        result = self.run_portfolio({'AAPL': frame}, [candidate(frame.index[1])])
        self.assertEqual(result['total_trades'], 1)
        self.assertAlmostEqual(result['final_capital'], 5100.)
        self.assertEqual(result['trades'][0]['reason'], 'TP1')
        self.assertEqual(result['trades'][0]['units'], 50.)
        self.assertEqual(result['trades'][0]['exit_price'], 102.)

    def test_pre_signal_candle_cannot_execute_future_order(self):
        frame = candles()
        frame.loc[frame.index[0], 'High'] = 105.
        result = self.run_portfolio({'AAPL': frame}, [candidate(frame.index[1])])
        self.assertEqual(result['trades'][0]['reason'], 'END_OF_DATA')
        self.assertEqual(result['trades'][0]['entry_time'], int(frame.index[1].timestamp()*1000))
        self.assertEqual(result['trades'][0]['pnl'], 0.)

    def test_both_hit_same_minute_counts_stop_first(self):
        frame = candles()
        frame.loc[frame.index[1], ['Low','High']] = [98., 104.]
        result = self.run_portfolio({'AAPL': frame}, [candidate(frame.index[1])])
        self.assertEqual(result['trades'][0]['reason'], 'STOP_BOTH_HIT')
        self.assertEqual(result['final_capital'], 4950.)

    def test_stop_gap_does_not_fill_at_impossible_better_stop(self):
        frame = candles()
        frame.loc[frame.index[2], ['Open','High','Low','Close']] = [97.,97.2,96.8,97.]
        result = self.run_portfolio({'AAPL': frame}, [candidate(frame.index[1])])
        self.assertEqual(result['trades'][0]['reason'], 'STOP_GAP')
        self.assertEqual(result['trades'][0]['exit_price'], 97.)

    def test_costs_are_paid_on_entry_and_exit_without_extra_capital(self):
        frame = candles()
        frame.loc[frame.index[1], 'High'] = 103.
        result = self.run_portfolio({'AAPL': frame}, [candidate(frame.index[1])], costs=Costs(.001,.001,0,0))
        trade = result['trades'][0]
        units = 5000 / (100.1*1.001)
        self.assertAlmostEqual(trade['units'], units)
        self.assertAlmostEqual(trade['units']*trade['entry_price'] + trade['entry_fee_usd'], 5000.)
        self.assertAlmostEqual(result['final_capital'], units*102*.999, places=5)

    def test_only_one_stock_can_use_account_and_selection_is_deterministic(self):
        a, b = candles(), candles()
        a.loc[a.index[1], 'High'] = 103.
        b.loc[b.index[1], 'High'] = 103.
        result = self.run_portfolio({'AAPL':a,'MSFT':b},
            [candidate(a.index[1], 'AAPL', strength=70), candidate(b.index[1], 'MSFT', strength=90)])
        self.assertEqual(result['total_trades'],1)
        self.assertEqual(result['trades'][0]['ticker'],'MSFT')
        self.assertEqual(result['execution_rejections']['simultaneous_signal_not_selected'],1)
        self.assertEqual(result['final_capital'],5100.)

    def test_second_trade_reinvests_actual_remaining_account(self):
        frame = candles()
        frame.loc[frame.index[1], 'High'] = 103.
        frame.loc[frame.index[3], 'High'] = 103.
        result = self.run_portfolio({'AAPL':frame}, [candidate(frame.index[1]),candidate(frame.index[3])])
        self.assertEqual(result['total_trades'],2)
        self.assertEqual(result['trades'][1]['capital_before'],5100.)
        self.assertEqual(result['trades'][1]['units'],51.)
        self.assertEqual(result['final_capital'],5202.)
        self.assertAlmostEqual(sum(t['pnl'] for t in result['trades']),result['final_capital']-5000)

    def test_no_intraminute_exit_proceeds_used_for_earlier_new_entry(self):
        frame = candles()
        frame.loc[frame.index[2], 'High'] = 103.
        result = self.run_portfolio({'AAPL':frame}, [candidate(frame.index[1]),candidate(frame.index[2])])
        self.assertEqual(result['total_trades'],1)
        self.assertEqual(result['execution_rejections']['capital_busy'],1)

    def test_sell_is_skipped_unless_explicit_short_mode(self):
        frame = candles()
        frame.loc[frame.index[1], 'Low'] = 97.
        signal = candidate(frame.index[1],direction=-1)
        default = self.run_portfolio({'AAPL':frame},[signal])
        short = self.run_portfolio({'AAPL':frame},[signal],allow_short=True)
        self.assertEqual(default['total_trades'],0)
        self.assertEqual(default['final_capital'],5000.)
        self.assertEqual(short['final_capital'],5100.)

    def test_crypto_cannot_be_traded(self):
        frame = candles()
        frame.attrs['instrument_type'] = 'CRYPTOCURRENCY'
        with self.assertRaises(ValueError):
            self.run_portfolio({'BTC-USD':frame},[])

    def test_future_fx_rate_cannot_change_current_accounting(self):
        rates = EuroRates(pd.Series([1., 2.],index=pd.DatetimeIndex(['2026-10-20T00:00Z','2026-10-22T00:00Z'])))
        self.assertEqual(rates.at(pd.Timestamp('2026-10-21T16:00Z')),1.)
        self.assertEqual(rates.at(pd.Timestamp('2026-10-22T16:00Z')),2.)


class CausalityTests(unittest.TestCase):
    def test_cached_indicator_prefix_matches_recalculation_without_future(self):
        frame = candles(280)
        values = 100 + np.arange(280)*.01 + np.sin(np.arange(280)/8)
        frame['Close'],frame['Open'] = values,values-.02
        frame['High'],frame['Low'] = values+.2,values-.2
        full = build_indicator_frame(frame)
        past = technical_at(full,220)
        changed = frame.copy()
        changed.iloc[221:, changed.columns.get_indexer(['Open','High','Low','Close'])] *= 3
        future_changed = technical_at(build_indicator_frame(changed),220)
        separate = technical_at(build_indicator_frame(frame.iloc[:221]))
        self.assertEqual(past, future_changed)
        self.assertEqual(past, separate)

    def test_lower_context_does_not_see_candle_still_in_progress(self):
        frame = candles(250)
        replay = Replay({'1m':frame})
        cutoff = frame.index[220]
        data,end = replay.at('1m',cutoff)
        self.assertEqual(end,cutoff)
        self.assertEqual(data['bars'],220)
        self.assertEqual(data['recent_price_action'][-1]['timestamp'],int(frame.index[219].timestamp()*1000))

    def test_missing_lower_timeframes_never_produce_clear_path(self):
        frame = candles(250,'5m')
        replay = Replay({'5m':frame})
        fake = {'latest_close':100.,'atr':1.,'warmup_complete':True,
                'indicator_groups': {'trend_structure':{'direction':'BULLISH','available_components':6,'score':80},
                                     'momentum_participation':{'direction':'BULLISH','available_components':8,'score':80}},
                'support':99.,'resistance':103.,'market_structure':{},'ema':{}}
        with patch('technical_backtest.technical_at',return_value=fake):
            candidates,diagnostics = replay.candidates('AAPL','5m',start=frame.index[200],end=frame.index[-1])
        self.assertEqual(candidates,[])
        self.assertGreater(diagnostics['POI_INCOMPLETE'],0)


if __name__ == '__main__':
    unittest.main()
