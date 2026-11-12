"""Concurrency and identical Gemini evidence, using deterministic local responses."""
import copy
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import requests

import database
import economic_data
import news_engine
from analysis_modes import ModeChangedError
from test_platform import ai_proposal, technical


def response_sequence():
    review = {"previous_setup_id": None, "assessment": "NONE", "confidence_in_previous": 0,
              "technical_break": False, "macro_opposition": False, "macro_opposition_strength": 0,
              "macro_event_id": None, "entry_confirmed": False, "entry_evidence_bar_at": None, "reason": "No previous plan"}
    plan = {**ai_proposal(), "entry_mode": "LIMIT", "entry_condition": "At support", "invalidation_price": 98,
            "invalidation_condition": "Support broken", "valid_for_bars": 12, "take_profit_1": 101}
    article = {"item_id": 0, "relevant_to_instrument": True, "sentiment": 0.5, "relevance": 1,
               "credibility": None, "novelty": 1, "impact_score": 80, "event_type": "earnings",
               "brief_summary": "Known facts", "importance_score": 80, "importance_reason": "Relevant",
               "event_key": "earnings", "effect_horizon": "intraday"}
    initial = {"macro": {"sentiment": 0, "relevance": 1, "novelty": 0, "impact_score": 0,
                         "brief_summary": "Macro", "assessments": []},
               "articles": [], "trade": plan, "previous_setup_review": review, "technical_direction_score": 80}
    repaired = {**plan, "entry_price": 98.5, "entry_zone_min": 98.3, "entry_zone_max": 98.7,
                "stop_loss": 96.5, "invalidation_price": 96.5, "take_profit_1": 101.5, "take_profit_2": 103.5}
    return [SimpleNamespace(text=json.dumps(value)) for value in
            (initial, {"articles": [article], "macro_assessments": []}, {"trade": repaired})]


class GeminiPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='gemini-performance-')
        self.environment = patch.dict(os.environ, {'TRADING_DB_PATH': str(Path(self.directory.name) / 'test.db')})
        self.environment.start()
        database.init_db()
        news_engine._analysis_cache.clear()
        news_engine._ai_context_retry_after.clear()
        news_engine._ai_retry_after = 0
        self.previous_macro_cache = economic_data._cache
        economic_data._cache = None

    def tearDown(self):
        economic_data._cache = self.previous_macro_cache
        news_engine._analysis_cache.clear()
        news_engine._ai_context_retry_after.clear()
        news_engine._ai_retry_after = 0
        self.environment.stop()
        self.directory.cleanup()

    def test_news_and_macro_load_concurrently_and_keep_the_complete_bundles(self):
        barrier = threading.Barrier(2, timeout=5)
        hard = {'items': [], 'snapshot_id': 'parallel', 'sources': ['BLS', 'FRED']}
        item = {'url': 'https://example.test/parallel', 'headline': 'Full article', 'published_ts': 1}
        feed = {'items': [item], 'sources': ['RSS'], 'feed_items_received': 1,
                'locally_matched': 1, 'selection_limit': 30, 'status': 'live'}
        def load_hard():
            barrier.wait()
            return copy.deepcopy(hard)
        def load_feed(ticker):
            self.assertEqual(ticker, 'NVDA')
            barrier.wait()
            return copy.deepcopy(feed)
        with patch('news_engine.get_hard_economic_data', side_effect=load_hard), \
             patch('news_engine.fetch_news_bundle', side_effect=load_feed), \
             patch('news_engine._gemini_context_analysis', return_value=({}, [], {}, 'keywords')) as analyze, \
             patch('news_engine._store_macro'), patch('news_engine._store_news'):
            news_engine.get_news_context_and_store('NVDA', 'test-key', technical=technical(), timeframe='5m')
        args = analyze.call_args.args
        self.assertEqual(args[3], feed['items'])
        self.assertEqual(args[4]['items'], hard['items'])
        self.assertEqual(args[4]['sources'], hard['sources'])

    def test_bls_and_fred_refresh_concurrently_and_merge_in_the_original_order(self):
        barrier = threading.Barrier(2, timeout=5)
        def bls(timestamp):
            barrier.wait()
            return [{'series_id': 'BLS', 'value': 1}, {'series_id': 'SHARED', 'value': 2}]
        def fred(timestamp):
            barrier.wait()
            return [{'series_id': 'FRED', 'value': 3}, {'series_id': 'SHARED', 'value': 4}]
        with patch('economic_data._latest_stored', return_value=[]), \
             patch('economic_data._fetch_bls', side_effect=bls), \
             patch('economic_data._fetch_fred', side_effect=fred), patch('economic_data._store') as store, \
             patch('economic_data._bundle', side_effect=lambda items, status: {'items': items, 'status': status}):
            result = economic_data.get_hard_economic_data(force=True)
        self.assertEqual(store.call_args.args[0], [{'series_id': 'BLS', 'value': 1}, {'series_id': 'SHARED', 'value': 2},
                                                  {'series_id': 'FRED', 'value': 3}, {'series_id': 'SHARED', 'value': 4}])
        self.assertEqual(result['items'], [{'series_id': 'BLS', 'value': 1}, {'series_id': 'SHARED', 'value': 4},
                                          {'series_id': 'FRED', 'value': 3}])

    def test_one_failed_macro_provider_keeps_other_live_values_and_stored_observations(self):
        stored = [{'series_id': 'BLS', 'value': 1}, {'series_id': 'FRED', 'value': 2}]
        with patch('economic_data._latest_stored', return_value=stored), \
             patch('economic_data._fetch_bls', side_effect=requests.Timeout('offline')), \
             patch('economic_data._fetch_fred', return_value=[{'series_id': 'FRED', 'value': 3}]), \
             patch('economic_data._store'), \
             patch('economic_data._bundle', side_effect=lambda items, status: {'items': items, 'status': status}):
            result = economic_data.get_hard_economic_data(force=True)
        self.assertEqual(result, {'items': [{'series_id': 'BLS', 'value': 1}, {'series_id': 'FRED', 'value': 3}], 'status': 'live'})

    def test_cached_macro_does_not_refetch_and_returns_an_independent_copy(self):
        import time
        economic_data._cache = (time.monotonic(), {'items': [{'value': 1}]})
        with patch('economic_data._fetch_bls') as bls, patch('economic_data._fetch_fred') as fred:
            result = economic_data.get_hard_economic_data()
            result['items'][0]['value'] = 99
            self.assertEqual(economic_data.get_hard_economic_data()['items'][0]['value'], 1)
        bls.assert_not_called()
        fred.assert_not_called()

    def test_one_client_handles_main_completion_and_repair_with_original_timeouts(self):
        item = {'ticker': 'TEST', 'headline': 'TEST earnings', 'url': 'https://example.test/news', 'summary': 'Facts'}
        with patch('google.genai.Client') as client:
            generate = client.return_value.__enter__.return_value.models.generate_content
            generate.side_effect = response_sequence()
            result = news_engine._gemini_context_analysis('TEST', '5m', technical(), [item],
                                                         {'items': [], 'snapshot_id': 'test'}, 'key', 'test')
        client.assert_called_once_with(api_key='key', http_options={'timeout': 60000})
        client.return_value.__exit__.assert_called_once()
        self.assertEqual(generate.call_count, 3)
        configs = [call.kwargs['config'] for call in generate.call_args_list]
        self.assertEqual([c.get('http_options', {}).get('timeout', 60000) for c in configs], [60000, 45000, 30000])
        self.assertEqual([c['max_output_tokens'] for c in configs], [12288, 8192, 3072])
        self.assertEqual(result[3], 'gemini')
        self.assertEqual(result[2]['plan_repair'], 'completed')
        self.assertTrue(result[2]['assessments_completed'])

    def test_client_closes_when_a_provider_fails(self):
        with patch('google.genai.Client') as client:
            client.return_value.__enter__.return_value.models.generate_content.side_effect = requests.Timeout('offline')
            result = news_engine._gemini_context_analysis('TEST', '5m', technical(), [], {'items': []}, 'key', 'error')
        client.return_value.__exit__.assert_called_once()
        self.assertEqual(result[3], 'keywords')

    def test_mode_switch_stops_followups_and_closes_the_client(self):
        with patch('google.genai.Client') as client, \
             patch('news_engine.ensure_analysis_mode', side_effect=[None, None, ModeChangedError('changed')]):
            generate = client.return_value.__enter__.return_value.models.generate_content
            generate.return_value = response_sequence()[0]
            with self.assertRaises(ModeChangedError):
                news_engine._gemini_context_analysis('TEST', '5m', technical(), [], {'items': []}, 'key', 'changed')
        self.assertEqual(generate.call_count, 1)
        client.return_value.__exit__.assert_called_once()

    def test_installed_google_sdk_applies_per_request_timeouts_on_the_shared_connection(self):
        from google import genai
        records = []
        def serve(request):
            records.append(request.extensions['timeout'])
            return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': '{}'}], 'role': 'model'}}]})
        # Injected transports are caller-owned; the SDK owns its normal production pool.
        with httpx.Client(transport=httpx.MockTransport(serve), trust_env=False) as transport_client:
            with genai.Client(api_key='local-test-key', http_options={'timeout': 60000, 'httpx_client': transport_client}) as client:
                for timeout in (None, 45000, 30000):
                    config = {'response_mime_type': 'application/json'}
                    if timeout:
                        config['http_options'] = {'timeout': timeout}
                    client.models.generate_content(model='gemini-3.5-flash-lite', contents='Local transport test', config=config)
        self.assertEqual([record['read'] for record in records], [60, 45, 30])
        self.assertTrue(transport_client.is_closed)


if __name__ == '__main__':
    unittest.main()
