"""Focused entry/quote regressions. Isolated SQLite, no network or historical backtest."""
import os
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import database
from decision_engine import validate_ai_trade_decision
from signal_manager import manage_signal, monitor_signal_market, signal_history
from test_platform import ai_proposal, technical


def quote(value, timestamp=900000):
    return {"timestamp": timestamp, "observed_at": 1500000, "low": value, "high": value, "close": value}


class EntryQuoteTests(unittest.TestCase):
    def setUp(self):
        with tempfile.NamedTemporaryFile(prefix="entry-quote-", suffix=".db", delete=False) as temporary:
            self.path = Path(temporary.name)
        self.env = patch.dict(os.environ, {"TRADING_DB_PATH": str(self.path)})
        self.env.start()
        database.init_db()

    def tearDown(self):
        self.env.stop()
        self.path.unlink()

    def create(self, direction=1, price=101, context="same", valid_for_bars=12, mode="gemini"):
        proposal = {**ai_proposal(direction), "valid_for_bars": valid_for_bars}
        data = {**technical(direction), "latest_close": price}
        decision = validate_ai_trade_decision("TEST", "5m", data, proposal, analysis_method="gemini")
        return manage_signal("TEST", "5m", decision, proposal, analysis_method=mode,
                             context_id=context, bar_at=900000, completed_bar_times=[900000],
                             market_bars=[{**quote(price), "observed_at": 1000000}], now=1000000,
                             mode=mode, technical=data)

    def test_gemini_uses_observed_quote_instead_of_previous_close(self):
        for direction in (1, -1):
            for mode in ("LIMIT", "STOP", "CONFIRMATION"):
                proposal = {**ai_proposal(direction), "entry_mode": mode,
                            "invalidation_price": 100-direction*12}
                data = {**technical(direction), "latest_close": 100, "current_price": 100+direction*3}
                result = validate_ai_trade_decision("TEST", "5m", data, proposal, analysis_method="gemini")
                self.assertEqual(result["current_price"], data["current_price"])
                self.assertEqual(result["validation_status"], "rejected")
                self.assertIn("Entry-Abstand", result["reasons"][0])

    def test_quote_poll_expires_pending_plan_once_and_preserves_original_levels(self):
        for direction in (1, -1):
            with self.subTest(direction=direction):
                # Separate symbols are not required: reset this isolated database between directions.
                if direction == -1:
                    with closing(database.get_connection()) as connection, connection:
                        connection.execute("DELETE FROM signal_states")
                        connection.execute("DELETE FROM signal_events")
                original = self.create(direction, price=100+direction)
                expired = monitor_signal_market("TEST", "5m", [quote(100+3*direction, 1200000)], now=1500000)
                repeated = monitor_signal_market("TEST", "5m", [quote(100+3*direction, 1200000)], now=1500010)
                self.assertEqual(original["lifecycle_status"], "VORBEREITET")
                self.assertEqual(expired["lifecycle_status"], "ABGELAUFEN")
                self.assertEqual(repeated["signal_type"], "NEUTRAL")
                self.assertEqual(expired["entry_price"], original["entry_price"])
                self.assertEqual(sum(row["event_type"] == "ENTRY_TOO_DISTANT" for row in signal_history("TEST", "5m")), 1)

    def test_legacy_gold_confirmation_plan_is_checked_with_current_atr(self):
        original = self.create(-1, price=99)
        import json
        with closing(database.get_connection()) as connection, connection:
            state = json.loads(connection.execute("SELECT state_json FROM signal_states").fetchone()[0])
            state.pop("entry_atr", None)  # An installed plan created before this fix.
            state["ticker"] = "XAUUSD"
            plan = state["plan"]
            for field in ("current_price", "entry_price", "entry_zone_min", "entry_zone_max", "stop_loss", "take_profit_1", "take_profit_2", "invalidation_price"):
                if plan.get(field) is not None:
                    plan[field] += 3941.5
            plan["entry_mode"] = "CONFIRMATION"
            connection.execute("UPDATE signal_states SET ticker='XAUUSD',state_json=?", (json.dumps(state),))
        result = monitor_signal_market("XAUUSD", "5m", [quote(4030, 1200000)], technical={"atr": 3}, now=1500000)
        self.assertEqual(original["lifecycle_status"], "VORBEREITET")
        self.assertEqual(result["entry_price"], 4041.5)
        self.assertEqual(result["current_price"], 4030)
        self.assertEqual(result["lifecycle_status"], "ABGELAUFEN")

    def test_fresh_nearby_pending_entry_is_not_forced_to_current_price(self):
        original = self.create()
        result = monitor_signal_market("TEST", "5m", [quote(101.5, 1200000)], now=1500000)
        self.assertEqual(result["lifecycle_status"], "VORBEREITET")
        self.assertEqual(result["entry_price"], original["entry_price"])
        self.assertEqual(result["current_price"], 101.5)

    def test_active_entry_is_not_moved_or_expired_by_distance(self):
        original = self.create(price=100)
        result = monitor_signal_market("TEST", "5m", [quote(103, 1200000)], now=1500000)
        self.assertEqual(result["lifecycle_status"], "AKTIV")
        self.assertEqual(result["entry_price"], original["entry_price"])
        self.assertEqual(result["stop_loss"], original["stop_loss"])

    def test_stale_quote_does_not_cancel_pending_entry(self):
        self.create()
        result = monitor_signal_market("TEST", "5m", [quote(110, 1200000)], stale=True, now=1500000)
        self.assertEqual(result["lifecycle_status"], "VORBEREITET")

    def test_lifetime_expires_without_waiting_for_new_gemini_analysis(self):
        self.create(valid_for_bars=2)
        result = monitor_signal_market("TEST", "5m", [quote(101, 1500000)],
                                       completed_bar_times=[900000, 1200000, 1500000], now=1600000)
        self.assertEqual(result["lifecycle_status"], "ABGELAUFEN")
        self.assertIn("Lebensdauer", result["state_reason"])

    def test_cached_context_does_not_skip_lifetime_expiry(self):
        self.create(valid_for_bars=2)
        data = {**technical(), "latest_close": 101}
        proposal = ai_proposal()
        decision = validate_ai_trade_decision("TEST", "5m", data, proposal, analysis_method="gemini")
        result = manage_signal("TEST", "5m", decision, proposal, analysis_method="gemini", context_id="same",
                               bar_at=1500000, completed_bar_times=[900000, 1200000, 1500000],
                               market_bars=[quote(101, 1500000)], now=1600000, technical=data)
        self.assertEqual(result["lifecycle_status"], "ABGELAUFEN")

    def test_websocket_reuses_quote_frame_for_local_checks_without_gemini(self):
        import asyncio
        from types import SimpleNamespace
        import pandas as pd
        import main
        start = pd.Timestamp.now(tz="UTC").floor("5min") - pd.Timedelta(minutes=100)
        frame = pd.DataFrame({"Open": 100., "High": 100.5, "Low": 99.5, "Close": 100., "Volume": 1000.},
                             index=pd.date_range(start, periods=21, freq="5min"))
        frame.loc[frame.index[-1], ["High", "Close"]] = 103.
        socket = SimpleNamespace(headers={}, accept=AsyncMock(), send_json=AsyncMock(), close=AsyncMock())
        with patch.object(main, "get_analysis_mode", return_value="gemini"), \
             patch.object(main, "fetch_stock_df", return_value=frame) as fetch, \
             patch.object(main, "market_metadata", return_value={"is_stale": False}), \
             patch.object(main, "monitor_signal_market", return_value={"signal_type": "NEUTRAL"}) as monitor, \
             patch.object(main, "get_news_context_and_store") as gemini, \
             patch.object(main.asyncio, "sleep", side_effect=main.WebSocketDisconnect()):
            asyncio.run(main.websocket_quotes(socket, "TEST", timeframe="5m", mode="gemini"))
        fetch.assert_called_once()
        gemini.assert_not_called()
        self.assertEqual(monitor.call_args.args[2][-1]["close"], 103)
        self.assertAlmostEqual(monitor.call_args.kwargs["technical"]["atr"], 1)
        self.assertEqual(len(monitor.call_args.kwargs["completed_bar_times"]), 20)


if __name__ == "__main__":
    unittest.main()
