"""Availability must distinguish a running API from an operational data/AI service."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import main


def background(**changes):
    value = {"settings": {"enabled": True, "python_enabled": True, "gemini_enabled": False},
             "runtime": {"status": "RUNNING"}, "worker_alive": True, "gemini_configured": False}
    value.update(changes)
    return value


class SystemStatusTests(unittest.TestCase):
    def read(self, value=None, error=None):
        # Deliberately skip lifespan: a status request must not start a worker.
        with patch.object(main, "background_status", return_value=value, side_effect=error), \
             patch.object(main, "market_frame") as quotes, patch.object(main, "evaluate") as evaluate:
            client = TestClient(main.app)
            try:
                response = client.get('/api/system-status')
            finally:
                client.close()
            self.assertEqual(response.status_code, 200)
            quotes.assert_not_called()
            evaluate.assert_not_called()
            return response.json()

    def test_api_and_background_are_reported_separately(self):
        result = self.read(background(worker_alive=False))
        self.assertEqual(result['backend']['label'], 'Online')
        self.assertEqual(result['state'], 'warn')
        self.assertNotEqual(result['data']['state'], 'ok')

    def test_missing_ai_key_does_not_claim_full_operation(self):
        result = self.read(background(settings={'enabled':True,'python_enabled':True,'gemini_enabled':True}))
        self.assertEqual(result['analysis']['label'], 'Eingeschränkt')
        self.assertEqual(result['state'], 'warn')

    def test_disabled_worker_is_paused_not_broken(self):
        result = self.read(background(settings={'enabled':False,'python_enabled':True,'gemini_enabled':False}))
        self.assertEqual(result['analysis']['label'], 'Pausiert')
        self.assertEqual(result['data']['label'], 'Auf Abruf')
        self.assertEqual(result['backend']['label'], 'Online')

    def test_ai_budget_limit_is_visible_while_python_remains_available(self):
        result = self.read(background(settings={'enabled':True,'python_enabled':True,'gemini_enabled':True},
                                      runtime={'status':'API_LIMIT'}, gemini_configured=True))
        self.assertEqual(result['analysis']['label'], 'Eingeschränkt')
        self.assertIn('Budget erreicht', result['analysis']['detail'])
        self.assertEqual(result['state'], 'warn')

    def test_storage_failure_does_not_keep_previous_green_status(self):
        result = self.read(error=RuntimeError('storage unavailable'))
        self.assertEqual(result['state'], 'warn')
        self.assertEqual(result['analysis']['label'], 'Unbekannt')


if __name__ == '__main__':
    unittest.main()
