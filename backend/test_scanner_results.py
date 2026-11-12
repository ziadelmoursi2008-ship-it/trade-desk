"""Scanner responses include all qualifying markets, including cutoff ties."""
import copy
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient
import main


def candidate(ticker, strength, direction, profit):
    return {'ticker':ticker, 'timeframe':'15m', 'decision':{
        'signal_type':direction, 'setup_quality':'B', 'strength':strength,
        'risk_reward_ratio':2, 'potential_profit_pct':profit,
        'verification_status':'verified', 'lifecycle_status':'VORBEREITET',
    }, 'market':{'is_stale':False, 'market_open':True}}


class ScannerResultsTests(unittest.TestCase):
    def setUp(self):
        self.items={
            'NVDA':candidate('NVDA',92,'SELL',1.8),
            'AAPL':candidate('AAPL',65,'BUY',2.5),
            'AMD':candidate('AMD',64,'BUY',3),
        }
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(main,'selected_mode',return_value='gemini'))
        self.stack.enter_context(patch.object(main,'ensure_analysis_mode'))
        self.stack.enter_context(patch.object(main.ml,'cache_revision',return_value=0))
        self.stack.enter_context(patch.dict(main._scan_cache,{},clear=True))
        self.stack.enter_context(patch.object(main,'evaluate',side_effect=lambda ticker,*args,**kwargs:copy.deepcopy(self.items[ticker])))
        self.client=TestClient(main.app)  # No lifespan or real market/API calls.
        self.addCleanup(self.client.close)

    def scan(self, parameters=''):
        response=self.client.get('/api/scan?symbols=NVDA,AAPL,AMD&mode=gemini&min_strength=65'+parameters)
        self.assertEqual(response.status_code,200)
        return response.json()

    def test_returns_all_matches_including_exact_cutoff(self):
        result=self.scan()
        self.assertEqual([item['ticker'] for item in result['matches']],['AAPL','NVDA'])
        self.assertEqual(result['eligible'],2)
        self.assertEqual(result['best'],result['matches'][0])

    def test_direction_filter_applies_to_entire_list(self):
        result=self.scan('&direction=SELL')
        self.assertEqual([item['ticker'] for item in result['matches']],['NVDA'])
        self.assertEqual(result['eligible'],1)

    def test_no_matches_returns_empty_list(self):
        for item in self.items.values():
            item['decision']['strength']=64
        result=self.scan()
        self.assertEqual(result['matches'],[])
        self.assertEqual(result['eligible'],0)
        self.assertIsNone(result['best'])

    def test_cached_response_preserves_all_matches(self):
        first=self.scan()
        second=self.scan()
        self.assertTrue(second['cached'])
        self.assertEqual(first['matches'],second['matches'])

    def test_stale_data_stays_excluded(self):
        self.items['NVDA']['market']['is_stale']=True
        result=self.scan()
        self.assertEqual([item['ticker'] for item in result['matches']],['AAPL'])


if __name__=='__main__':
    unittest.main()
