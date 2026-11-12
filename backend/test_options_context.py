"""Independent numeric goldens, provider fixtures, persistence and trigger tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone, timedelta
import json
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient
import requests

import database
import main
import news_engine
import options_context as options
import quantitative_analysis as quant
from analysis_modes import ModeChangedError, set_analysis_mode
from option_greeks import calculate_greeks
from test_gemini_performance import response_sequence
from test_platform import empty_news_context, technical
from test_quantitative import frames

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc).timestamp()


def chain_fixture(ticker="AAPL", verified=True, now=NOW):
    rows = []
    for days in (2, 9, 16):
        expiry = datetime.fromtimestamp(now, options.NY).date() + timedelta(days=days)
        end = datetime.combine(expiry, datetime.min.time(), options.NY).replace(hour=16).timestamp()
        for kind in ("call", "put"):
            for strike in (98., 99., 100., 101., 102.):
                greeks = calculate_greeks(100., strike, (end - now) / (365 * 86400), .04, .3, kind)
                value = greeks["model_price"]
                rows.append({"symbol": ticker + expiry.strftime("%y%m%d") + ("C" if kind == "call" else "P") + f"{int(strike * 1000):08d}",
                             "kind": kind, "expiration": expiry.isoformat(), "strike": strike, "iv": .3,
                             "bid": value * .98, "ask": value * 1.02, "quote_at": now - 10 if verified else None,
                             "last_trade_at": now - 5, "open_interest": 300})
    return {"provider": "alpaca" if verified else "yahoo", "feed": "opra" if verified else "yahoo",
            "spot": 100., "spot_at": now - 20, "spot_time_basis": "fixture", "contracts": rows,
            "partial": False, "verified_feed": verified}


class GreeksMathTests(unittest.TestCase):
    def test_textbook_call_and_put_goldens(self):
        call = calculate_greeks(100, 100, 1, .05, .2, "call")
        put = calculate_greeks(100, 100, 1, .05, .2, "put")
        for key, expected in {"model_price": 10.450583572185565, "delta": .6368306511756191,
                              "gamma": .018762017345846895, "theta": -6.414027546438197 / 365,
                              "vega": .3752403469169379, "rho": .5323248154537634}.items():
            self.assertAlmostEqual(call[key], expected, places=10, msg=key)
        self.assertAlmostEqual(put["model_price"], 5.573526022256971, places=10)
        self.assertAlmostEqual(put["delta"], -.3631693488243809, places=10)
        self.assertAlmostEqual(put["rho"], -.4189046090469506, places=10)
        self.assertEqual(call["gamma"], put["gamma"])
        self.assertEqual(call["vega"], put["vega"])

    def test_all_sensitivities_match_independent_price_differences(self):
        for kind in ("call", "put"):
            args = [102., 100., .31, .035, .27, kind, .015]
            center = calculate_greeks(*args)
            def value(index, step):
                changed = args.copy()
                changed[index] += step
                return calculate_greeks(*changed)["model_price"]
            self.assertAlmostEqual((value(0, .001) - value(0, -.001)) / .002, center["delta"], places=7)
            self.assertAlmostEqual((value(0, .01) - 2 * center["model_price"] + value(0, -.01)) / .0001, center["gamma"], places=7)
            self.assertAlmostEqual((value(4, .00001) - value(4, -.00001)) / .00002 / 100, center["vega"], places=7)
            self.assertAlmostEqual((value(3, .00001) - value(3, -.00001)) / .00002 / 100, center["rho"], places=7)
            self.assertAlmostEqual(-(value(2, .000001) - value(2, -.000001)) / .000002 / 365, center["theta"], places=7)

    def test_put_call_parity_with_dividends(self):
        call = calculate_greeks(80, 85, .4, .03, .4, "call", .02)
        put = calculate_greeks(80, 85, .4, .03, .4, "put", .02)
        self.assertAlmostEqual(call["model_price"] - put["model_price"], 80 * math.exp(-.02 * .4) - 85 * math.exp(-.03 * .4))

    def test_invalid_and_expired_inputs_are_rejected(self):
        for index, value in ((0, 0), (1, -1), (2, 0), (4, 0), (4, 8), (3, float("nan")), (4, float("inf")), (0, True), (5, "wrong")):
            args = [100., 100., .1, .04, .2, "call"]
            args[index] = value
            with self.assertRaises(ValueError):
                calculate_greeks(*args)


class OptionsContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="options-tests-")
        self.environment = patch.dict(os.environ, {"TRADING_DB_PATH": str(Path(self.directory.name) / "test.db"),
            "OPTIONS_PROVIDER": "yahoo", "OPTIONS_RISK_FREE_RATE": ".04", "OPTIONS_DIVIDEND_YIELD": "0", "GEMINI_API_KEY": ""})
        self.environment.start()
        database.init_db()

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def refresh(self, ticker="AAPL", now=NOW):
        return options.refresh(ticker, spot=100., spot_at=now - 20, now=now)

    def test_bounded_balanced_selection_and_units(self):
        result = options._model("AAPL", chain_fixture(), NOW)
        self.assertEqual(len(result["contracts"]), 8)
        self.assertEqual(sum(c["kind"] == "call" for c in result["contracts"]), 4)
        self.assertEqual(len({c["expiration"] for c in result["contracts"]}), 2)
        self.assertTrue(result["usable_for_ai"])
        self.assertEqual(result["direction_weight_pct"], 0)
        self.assertIn("Prozentpunkt", result["units"]["vega"])

    def test_yahoo_trade_time_is_not_quote_freshness(self):
        result = options._model("AAPL", chain_fixture(verified=False), NOW)
        self.assertEqual(result["status"], "REFERENCE")
        self.assertFalse(result["usable_for_ai"])
        compact = options.for_gemini(result)
        self.assertNotIn("contracts", compact)
        self.assertNotIn("spot", compact)
        self.assertTrue(result["contracts"])

    def test_indicative_stale_or_future_quotes_never_become_live(self):
        for mode in ("indicative", "stale", "future", "old_spot"):
            chain = chain_fixture()
            if mode == "indicative":
                chain.update(feed="indicative", verified_feed=False)
            elif mode == "old_spot":
                chain["spot_at"] = NOW - 600
            else:
                for row in chain["contracts"]:
                    row["quote_at"] = NOW - 900 if mode == "stale" else NOW + 90
            self.assertFalse(options._model("AAPL", chain, NOW)["usable_for_ai"], mode)

    def test_bad_contracts_are_excluded_without_breaking_healthy_ones(self):
        chain = chain_fixture()
        rejected = set()
        modifications = ({"iv": 0}, {"bid": -1}, {"ask": .0001}, {"iv": float("nan")},
                         {"expiration": "2026-10-28"}, {"symbol": "AAPL1261030C00100000"}, {"strike": 1000})
        for item, changes in zip(chain["contracts"], modifications):
            rejected.add(item["symbol"])
            item.update(changes)
            rejected.add(item["symbol"])
        result = options._model("AAPL", chain, NOW)
        self.assertFalse(rejected & {c["symbol"] for c in result["contracts"]})

    def test_no_candidate_is_unavailable_instead_of_zero_greeks(self):
        with patch.object(options, "fetch_chain", return_value={**chain_fixture(), "contracts": []}):
            result = self.refresh()
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(result["contracts"], [])

    def test_persistent_cache_is_shared_across_chart_requests(self):
        with patch.object(options, "fetch_chain", return_value=chain_fixture()) as fetch:
            first = self.refresh()
            second = self.refresh(now=NOW + 30)
            with patch.object(options, "_model", side_effect=AssertionError("Polling recomputed")):
                saved = options.saved_context("AAPL", now=NOW + 31)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(first["snapshot_id"], saved["snapshot_id"])
        self.assertTrue(second["cache_hit"])
        with closing(database.get_connection()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM options_context_snapshots").fetchone()[0], 1)
            self.assertIn("contracts", json.loads(connection.execute("SELECT chain_json FROM options_context_cache").fetchone()[0]))

    def test_concurrent_requests_fetch_once(self):
        with patch.object(options, "fetch_chain", return_value=chain_fixture()) as fetch:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: self.refresh(), range(2)))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(results[0]["snapshot_id"], results[1]["snapshot_id"])

    def test_quote_ages_out_before_model_archive_and_payload_stays_stable(self):
        with patch.object(options, "fetch_chain", return_value=chain_fixture()):
            first = self.refresh()
        self.assertEqual(options.for_gemini(first), options.for_gemini(options.saved_context("AAPL", now=NOW + 5)))
        expired = options.saved_context("AAPL", now=NOW + 121)
        self.assertEqual(expired["status"], "REFERENCE")
        self.assertNotIn("contracts", options.for_gemini(expired))
        archived = options.saved_context("AAPL", now=NOW + 301)
        self.assertEqual(archived["status"], "STALE")
        self.assertTrue(archived["contracts"])
        self.assertNotIn("contracts", options.for_gemini(archived))

    def test_provider_failure_preserves_snapshot_and_limits_retries(self):
        with patch.object(options, "fetch_chain", return_value=chain_fixture()):
            first = self.refresh()
        with patch.object(options, "fetch_chain", side_effect=options.OptionsProviderError("failed")) as fetch:
            failed = self.refresh(now=NOW + 61)
            again = self.refresh(now=NOW + 62)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(failed["status"], "STALE")
        self.assertEqual(failed["snapshot_id"], first["snapshot_id"])
        self.assertFalse(again["usable_for_ai"])
        self.assertNotIn("contracts", options.for_gemini(again))

    def test_model_config_change_requires_explicit_refresh(self):
        with patch.object(options, "fetch_chain", return_value=chain_fixture()):
            self.refresh()
            with patch.dict(os.environ, {"OPTIONS_RISK_FREE_RATE": ".06"}):
                self.assertEqual(options.saved_context("AAPL", now=NOW + 10)["status"], "STALE")
                changed = self.refresh(now=NOW + 10)
        self.assertEqual(changed["assumptions"]["rate"], .06)

    def test_fast_spot_change_withdraws_model_without_automatic_computation(self):
        with patch.object(options, "fetch_chain", return_value=chain_fixture()) as fetch:
            self.refresh()
            read = options.saved_context("AAPL", now=NOW + 5, spot=101)
            self.assertEqual(read["status"], "REFERENCE")
            self.assertFalse(read["usable_for_ai"])
            self.assertNotIn("contracts", options.for_gemini(read))
            self.assertEqual(fetch.call_count, 1)
            options.refresh("AAPL", spot=101, spot_at=NOW, now=NOW + 5)
            self.assertEqual(fetch.call_count, 2)

    def test_naive_quote_times_are_not_silently_interpreted_in_pc_timezone(self):
        self.assertIsNone(options.timestamp("2026-10-28T15:00:00"))
        self.assertEqual(options.timestamp("2026-10-28T15:00:00Z"), NOW)

    def test_python_mode_does_not_read_fetch_or_compute_options(self):
        target, source = frames()
        set_analysis_mode("python")
        with patch("main.market_frame", return_value=target), \
             patch.object(options, "refresh", side_effect=AssertionError("Python fetched options")), \
             patch.object(options, "saved_context", side_effect=AssertionError("Python read options")):
            result = main.evaluate("AAPL", "15m", mode="python", with_backtest=False)
        self.assertNotIn("options_context", result)
        self.assertNotIn("options_context", result["technical"])

    def test_unsupported_assets_and_initial_read_never_fetch(self):
        with patch.object(options, "fetch_chain", side_effect=AssertionError("Unexpected provider request")):
            for ticker in ("BTC-USD", "XAUUSD", "SOL-USD", "UNKNOWN"):
                self.assertEqual(self.refresh(ticker)["status"], "UNSUPPORTED")
            self.assertEqual(options.saved_context("AAPL", now=NOW)["status"], "NOT_CALCULATED")

    def test_fresh_fed_proxy_or_explicit_rate_required(self):
        with patch.dict(os.environ):
            os.environ.pop("OPTIONS_RISK_FREE_RATE")
            with self.assertRaises(ValueError):
                options._rate(NOW)
            with closing(database.get_connection()) as connection, connection:
                for series, value in (("DFEDTARL", 4.), ("DFEDTARU", 4.25)):
                    connection.execute("INSERT INTO hard_economic_data(series_id,period,name,category,value,unit,source,source_url,fetched_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                       (series, "2026-10-27", "Fed", "rates", value, "%", "FRED", "https://fred.stlouisfed.org", NOW))
            self.assertAlmostEqual(options._rate(NOW)[0], .04125)
            with self.assertRaises(ValueError):
                options._rate(NOW + 11 * 86400)

    def test_mode_change_during_fetch_does_not_save(self):
        check = Mock(side_effect=[None, ModeChangedError("changed")])
        with patch.object(options, "fetch_chain", return_value=chain_fixture()), self.assertRaises(ModeChangedError):
            options.refresh("AAPL", spot=100, spot_at=NOW, now=NOW, ensure_current=check)
        self.assertEqual(options.saved_context("AAPL", now=NOW)["status"], "NOT_CALCULATED")

    def test_sdk_exception_is_sanitized_without_crashing_analysis(self):
        with patch.object(options, "_yahoo_chain", side_effect=RuntimeError("secret-key-and-provider-url")):
            result = self.refresh()
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertNotIn("secret-key", json.dumps(result))

    def test_alpaca_403_does_not_retry_indicative(self):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("forbidden")
        with patch.dict(os.environ, {"OPTIONS_PROVIDER": "alpaca", "ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "secret"}), \
             patch.object(options.requests, "get", return_value=response) as request:
            result = self.refresh()
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs["params"]["feed"], "opra")

    def test_yahoo_bridge_reads_expiries_but_never_invents_quote_timestamp(self):
        chain = chain_fixture(verified=False)
        expiries = sorted({int(datetime.fromisoformat(c["expiration"]).replace(tzinfo=timezone.utc).timestamp()) for c in chain["contracts"]})
        responses = []
        for expiry in expiries[:2]:
            date = datetime.fromtimestamp(expiry, timezone.utc).date().isoformat()
            item = {"expirationDate": expiry, "calls": [], "puts": []}
            for row in chain["contracts"]:
                if row["expiration"] == date:
                    item["calls" if row["kind"] == "call" else "puts"].append({"contractSymbol": row["symbol"], "strike": row["strike"],
                        "bid": row["bid"], "ask": row["ask"], "impliedVolatility": row["iv"], "lastTradeDate": NOW - 5})
            response = Mock()
            response.json.return_value = {"optionChain": {"result": [{"expirationDates": expiries, "quote": {
                "regularMarketPrice": 100, "regularMarketTime": NOW - 20, "currency": "USD"}, "options": [item]}]}}
            responses.append(response)
        for response in responses:
            response.status_code = 200
        with patch.object(options.curl_requests, "Session") as factory:
            session = factory.return_value.__enter__.return_value
            session.get.side_effect = responses
            result = options._yahoo_chain("AAPL", NOW, 100, NOW)
            self.assertEqual(session.get.call_count, 2)
            self.assertLessEqual(session.get.call_args.kwargs["timeout"], 3)
        self.assertTrue(result["contracts"])
        self.assertTrue(all(c["quote_at"] is None for c in result["contracts"]))

    def test_actual_alpaca_schema_and_pagination_normalize_correctly(self):
        fixture = chain_fixture()
        responses = []
        for index, subset in enumerate((fixture["contracts"][:10], fixture["contracts"][10:])):
            response = Mock()
            response.json.return_value = {"snapshots": {row["symbol"]: {
                "impliedVolatility": row["iv"], "latestQuote": {"bp": row["bid"], "ap": row["ask"],
                    "t": datetime.fromtimestamp(row["quote_at"], timezone.utc).isoformat()},
                "latestTrade": {"t": datetime.fromtimestamp(NOW - 5, timezone.utc).isoformat()}}
                for row in subset}, "next_page_token": "next" if index == 0 else None}
            responses.append(response)
        with patch.dict(os.environ, {"OPTIONS_PROVIDER": "alpaca", "ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "secret"}), \
             patch.object(options.requests, "get", side_effect=responses) as request:
            chain = options._alpaca_chain("AAPL", NOW, 100, NOW - 20)
        self.assertEqual(request.call_count, 2)
        self.assertFalse(chain["partial"])
        self.assertTrue(options._model("AAPL", chain, NOW)["usable_for_ai"])

    def test_yahoo_authentication_and_all_network_calls_share_a_deadline(self):
        response = Mock(status_code=200)
        response.json.return_value = {"optionChain": {"result": [{"expirationDates": [], "quote": {}, "options": []}]}}
        session = Mock()
        session.get.side_effect = [Mock(status_code=401), Mock(status_code=404), Mock(status_code=200, text="test-crumb"), response]
        result = options._yahoo_with_session(session, "AAPL", NOW, 100, NOW)
        self.assertEqual(session.get.call_count, 4)
        self.assertEqual(session.get.call_args.kwargs["params"]["crumb"], "test-crumb")
        self.assertNotIn("test-crumb", json.dumps(result))
        with patch.object(options.time, "monotonic", side_effect=[0, 15]), self.assertRaises(TimeoutError):
            options._yahoo_with_session(Mock(), "AAPL", NOW, 100, NOW)

    def test_manual_endpoint_then_polling_and_disabled_quant(self):
        target, source = frames()
        shift = source.Close.iloc[-1] - 100
        for frame in (target, source):
            frame[["Open", "High", "Low", "Close"]] -= shift
        with TestClient(main.app) as client, patch("main.market_frame", side_effect=lambda ticker, tf: target if tf == "15m" else source), \
             patch("main.get_news_context_and_store", return_value=empty_news_context()) as news, \
             patch.object(options, "fetch_chain", return_value=chain_fixture()) as fetch, patch.object(options.time, "time", return_value=NOW):
            quant.save_settings(True)
            manual = client.post("/api/options/refresh?ticker=AAPL")
            self.assertEqual(manual.status_code, 200, manual.text)
            self.assertEqual(manual.json()["status"], "AVAILABLE")
            self.assertEqual(fetch.call_count, 1)
            news.assert_not_called()
            with patch.object(options, "refresh", side_effect=AssertionError("Polling refreshed options")):
                poll = client.get("/api/analysis?ticker=AAPL&mode=gemini")
            self.assertEqual(poll.status_code, 200)
            self.assertEqual(poll.json()["options_context"]["snapshot_id"], manual.json()["snapshot_id"])
            quant.save_settings(False)
            off=client.get("/api/analysis?ticker=AAPL&mode=gemini")
            self.assertEqual(off.json()["options_context"]["snapshot_id"],manual.json()["snapshot_id"])
            self.assertEqual(news.call_args.kwargs["technical"]["options_context"]["role"],"risk_context_only")

    def test_scanner_never_refreshes_options_implicitly(self):
        target, source = frames()
        shift = source.Close.iloc[-1] - 100
        for frame in (target, source):
            frame[["Open", "High", "Low", "Close"]] -= shift
        with TestClient(main.app) as client, patch("main.DEFAULT_UNIVERSE", ["AAPL", "NVDA", "BTC-USD"]), \
             patch("main.market_frame", side_effect=lambda ticker, tf: target if tf == "15m" else source), \
             patch("main.get_news_context_and_store", return_value=empty_news_context()), \
             patch.object(options, "fetch_chain", side_effect=lambda ticker, *args: chain_fixture(ticker)) as fetch, \
             patch.object(options.time, "time", return_value=NOW):
            quant.save_settings(True)
            result = client.get("/api/scan?mode=gemini")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["analyzed"], 3)
        fetch.assert_not_called()

    def test_gemini_gets_compact_context_in_existing_single_request(self):
        context = {**technical(), "options_context": options.for_gemini(options._model("AAPL", chain_fixture(), NOW))}
        initial = json.loads(response_sequence()[0].text)
        initial["trade"]["signal_type"] = "NEUTRAL"
        initial["technical_direction_score"] = 50
        with patch("google.genai.Client") as client:
            generate = client.return_value.__enter__.return_value.models.generate_content
            generate.return_value = SimpleNamespace(text=json.dumps(initial))
            news_engine._gemini_context_analysis("AAPL", "15m", context, [], {"items": [], "snapshot_id": "opts"}, "key", "opts")
        self.assertEqual(generate.call_count, 1)
        payload = json.loads(generate.call_args.kwargs["contents"].rsplit("\n", 1)[-1])
        received = payload["technical_analysis"]["options_context"]
        self.assertEqual(len(received["contracts"]), 8)
        self.assertEqual(received["direction_weight_pct"], 0)
        self.assertNotIn("options", payload["decision_policy"]["decision_weights_pct"])


if __name__ == "__main__":
    unittest.main()
