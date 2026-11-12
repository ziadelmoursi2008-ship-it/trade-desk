"""The visual observatory must stay read-only and retain both applications."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import main
import observatory_data


class ObservatoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "observatory.db"
        self.environment = patch.dict(os.environ, {"TRADING_DB_PATH": str(self.db_path)})
        self.environment.start()
        database.init_db()
        self.client = TestClient(main.app)  # No lifespan or background worker.

    def tearDown(self):
        self.client.close()
        self.environment.stop()
        self.temp.cleanup()

    def insert_price(self, timeframe, at, value):
        connection = database.get_connection()
        try:
            with connection:
                connection.execute(
                    "INSERT INTO background_price_bars VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("NVDA", timeframe, at, at + 60000, value, value, value, value, 100, "USD", "UTC"))
        finally:
            connection.close()

    def test_empty_database_preserves_unavailable_prices(self):
        result = self.client.get("/api/observatory")
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual(len(data["instruments"]), 16)
        self.assertTrue(all(item["price"] is None for item in data["instruments"]))
        self.assertEqual(data["events"], [])
        self.assertFalse(data["background"]["settings"]["enabled"])

    def test_snapshot_reads_latest_series_and_does_not_modify_database(self):
        self.insert_price("15m", 100000, 90)
        self.insert_price("1m", 200000, 100)
        self.insert_price("1m", 260000, 105)
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        with patch.object(main, "evaluate", side_effect=AssertionError("No analyses from the observatory")), \
             patch.object(main, "market_frame", side_effect=AssertionError("No market requests from the observatory")):
            data = self.client.get("/api/observatory").json()
            self.client.get("/api/observatory").raise_for_status()
        item = next(item for item in data["instruments"] if item["symbol"] == "NVDA")
        self.assertEqual(item["price"], 105)
        self.assertEqual(item["chart_timeframe"], "1m")
        self.assertEqual([bar["close"] for bar in item["bars"]], [100, 105])
        self.assertEqual(before, hashlib.sha256(self.db_path.read_bytes()).hexdigest())

    def test_event_identities_and_analysis_profiles_remain_distinct(self):
        state = {"direction": "BUY", "status": "AKTIV", "confidence": 82,
                 "plan": {"entry_price": 100, "take_profit_1": 104}, "reason": "Confirmed"}
        connection = database.get_connection()
        try:
            with connection:
                for table in ("signal_states", "technical_signal_states"):
                    connection.execute(f"INSERT INTO {table} VALUES(?,?,?,?)", ("NVDA", "15m", json.dumps(state), 1000))
                for table, at in (("signal_events", 1000), ("technical_signal_events", 2000)):
                    connection.execute(f"INSERT INTO {table}(ticker,timeframe,event_key,event_type,created_at,state_json) VALUES(?,?,?,?,?,?)",
                                       ("NVDA", "15m", "test", "ACTIVATED", at, json.dumps(state)))
        finally:
            connection.close()
        data = observatory_data.snapshot()
        self.assertEqual([event["profile"] for event in data["events"]], ["python", "gemini"])
        self.assertEqual(len({event["id"] for event in data["events"]}), 2)
        item = next(item for item in data["instruments"] if item["symbol"] == "NVDA")
        self.assertEqual({state["profile"] for state in item["states"]}, {"python", "gemini"})

    def test_missing_archive_uses_latest_saved_analysis_price(self):
        connection = database.get_connection()
        try:
            with connection:
                for table, price, at in (("signal_states", 100, 1000), ("technical_signal_states", 110, 2000)):
                    connection.execute(f"INSERT INTO {table} VALUES(?,?,?,?)",
                                       ("NVDA", "15m", json.dumps({"current_price": price}), at))
        finally:
            connection.close()
        item = next(item for item in observatory_data.snapshot()["instruments"] if item["symbol"] == "NVDA")
        self.assertEqual(item["price"], 110)
        self.assertEqual(item["price_at"], 2000)
        self.assertEqual(item["price_source"], "saved_analysis")

    def test_root_serves_observatory_and_trading_stays_accessible(self):
        if not (main.OBSERVATORY_DIST / "index.html").is_file():
            self.skipTest("Build the observatory before testing static delivery")
        root = self.client.get("/")
        self.assertIn("Systemstatus", root.text)
        self.assertNotIn("/observatory/assets/", root.text)
        self.assertIn("/api/system-status", root.text)
        if (main.SITE_DIST / "index.html").is_file():
            self.assertEqual(self.client.get("/trading").content, (main.SITE_DIST / "index.html").read_bytes())
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")
        self.assertEqual(len(self.client.get("/api/instruments").json()["instruments"]), 16)


if __name__ == "__main__":
    unittest.main()
