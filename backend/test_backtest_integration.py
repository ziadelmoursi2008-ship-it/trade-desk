"""Saved technical results are versioned, isolated from AI and refreshed in API."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
import pandas as pd

import backtesting
import database
import main
from technical_backtest import Costs, EuroRates, simulate_portfolio
from technical_forecast import FORECAST_VERSION
from test_technical_backtest import candles, candidate
from test_platform import candles as analysis_candles


class SavedBacktestTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.gettempdir())/'replay-integration-tests'/uuid.uuid4().hex
        (self.root/'backend').mkdir(parents=True)
        (self.root/'backtest_results').mkdir()
        self.source_patch=patch.object(backtesting,'__file__',str(self.root/'backend/backtesting.py'))
        self.source_patch.start()
        self.environment = patch.dict(os.environ, {'TRADING_DB_PATH': str(self.root/'test.sqlite')})
        self.environment.start()
        database.init_db()
        self.path=self.root/'backtest_results/AAPL_5m.json'
        frame=candles()
        frame.loc[frame.index[1],'High']=103.
        rates=EuroRates(pd.Series([1.],index=pd.DatetimeIndex(['2026-10-20T00:00Z'])))
        self.report=simulate_portfolio({'AAPL':frame},[candidate(frame.index[1])],rates,
            timeframe='5m',start=frame.index[0],end=frame.index[-1]+pd.Timedelta(minutes=1),costs=Costs())
        self.save(self.report)

    def tearDown(self):
        self.environment.stop()
        self.source_patch.stop()
        # Only this newly created isolated fixture may be recursively removed.
        if not self.root.resolve().is_relative_to((Path(tempfile.gettempdir())/'replay-integration-tests').resolve()):
            raise AssertionError('Unsafe test cleanup path.')
        shutil.rmtree(self.root)

    def save(self,report):
        self.path.write_text(json.dumps(report,allow_nan=False),encoding='utf-8')

    def test_current_stock_replay_loads_with_eur_and_actual_metrics(self):
        result=backtesting.run_backtest_strategy(None,ticker='AAPL',timeframe='5m',mode='python')
        self.assertNotIn('error',result)
        self.assertEqual(result['strategy_version'],FORECAST_VERSION)
        self.assertEqual(result['currency'],'EUR')
        self.assertEqual(result['initial_capital'],5000.)
        self.assertEqual(result['total_trades'],1)
        self.assertEqual(result['final_capital'],self.report['final_capital'])

    def test_old_strategy_is_not_relabelled_as_current(self):
        self.report['strategy_version']='2.1-timeframe-poi'
        self.save(self.report)
        result=backtesting.load_technical_backtest('AAPL','5m')
        self.assertIn('error',result)
        self.assertEqual(result['total_trades'],0)

    def test_changed_source_fingerprint_is_rejected_even_with_same_version(self):
        self.report['strategy_fingerprint']['technical_analysis.py']='old-source'
        self.save(self.report)
        self.assertIn('error',backtesting.load_technical_backtest('AAPL','5m'))

    def test_other_stock_or_timeframe_cannot_be_returned(self):
        for field,value in [('symbols',['MSFT']),('timeframe','15m')]:
            report=copy.deepcopy(self.report)
            report[field]=value
            self.save(report)
            self.assertIn('error',backtesting.load_technical_backtest('AAPL','5m'))

    def test_different_capital_costs_or_short_mode_cannot_reuse_run(self):
        for changes in [{'initial_capital':10000.},{'commission_rate':0.},
                        {'slippage_rate':0.},{'allow_short':True}]:
            self.assertIn('error',backtesting.load_technical_backtest('AAPL','5m',**changes))

    def test_incomplete_or_invalid_json_is_an_explicit_unavailable_result(self):
        for text in ['{','[]','{"cost_rates":null}']:
            self.path.write_text(text,encoding='utf-8')
            self.assertIn('error',backtesting.load_technical_backtest('AAPL','5m'))

    def test_unsupported_timeframe_cannot_be_used_as_a_file_path(self):
        with self.assertRaises(ValueError):
            backtesting.load_technical_backtest('AAPL','../outside')

    def test_gemini_needs_matching_recorded_activations(self):
        result=backtesting.run_backtest_strategy(None,ticker='AAPL',timeframe='5m',mode='gemini')
        self.assertEqual(result['strategy_version'],'4.1-ai')
        self.assertIn('aufgezeichnete Aktivierungen',result['error'])

    def test_partial_replay_inputs_never_fall_back_to_saved_statistics(self):
        with self.assertRaises(ValueError):
            backtesting.run_backtest_strategy(None,ticker='AAPL',timeframe='5m',mode='python',lower_frames={})

    def test_missing_required_lower_timeframe_is_not_silently_ignored(self):
        with self.assertRaises(ValueError):
            backtesting.run_backtest_strategy(candles(260),ticker='AAPL',timeframe='15m',mode='python',
                lower_frames={'1m':candles(260)},fx_rates=pd.Series([1.],index=pd.DatetimeIndex(['2026-10-20T00:00Z'])))

    def test_python_api_returns_current_replay_and_refreshes_same_bar(self):
        frame=analysis_candles()
        frame.attrs['timeframe']='5m'
        database_path=self.root/'api.sqlite'
        with patch.dict(os.environ,{'TRADING_DB_PATH':str(database_path)}), \
             patch('main.market_frame',return_value=frame), \
             patch('main.get_news_context_and_store',side_effect=AssertionError('AI/news must stay off')):
            with TestClient(main.app) as client:
                self.assertEqual(client.post('/api/analysis-mode',json={'mode':'python'}).status_code,200)
                response=client.get('/api/analysis?ticker=AAPL&timeframe=5m&mode=python')
                self.assertEqual(response.status_code,200)
                result=response.json()['backtest']
                self.assertNotIn('error',result)
                self.assertEqual(result['total_trades'],1)
                self.assertEqual(result['strategy_version'],FORECAST_VERSION)
                self.report['scope']='freshly-replaced-replay'
                self.save(self.report)
                response=client.get('/api/analysis?ticker=AAPL&timeframe=5m&mode=python')
                self.assertEqual(response.json()['backtest']['scope'],'freshly-replaced-replay')


if __name__=='__main__':
    unittest.main()
