"""Costed TP1 replay of actually archived Gemini+LightGBM activations.

Not a retrospective Gemini call and not a replay of every historic chart candle.
Orders enter at the first minute open strictly AFTER the activation was observed.
"""
from collections import Counter
import math

import pandas as pd

from backtest_archive import strategy_key
from decision_engine import MIN_REWARD_RISK
from technical_backtest import Costs, validate_frame


def replay(ticker, timeframe, frame, activations, rates, *, initial_capital=5000., costs=None):
    validate_frame(frame, "1m")
    if frame.attrs.get("currency") != "USD" or frame.attrs.get("instrument_type") != "EQUITY":
        raise ValueError("USD-Aktien und echte 1m-Ausführungsdaten erforderlich.")
    if not math.isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("Ungültiges Startkapital.")
    costs = costs or Costs()
    signals, rejected = {}, Counter()
    for activation in activations:
        available = pd.Timestamp(activation["available_at"], unit="ms", tz="UTC")
        due = available.floor("min") + pd.Timedelta(minutes=1)
        # Only the exact next minute may fill: missing data never moves a trade
        # to a later session and never invents a historical execution.
        if due not in frame.index:
            rejected["missing_next_minute"] += 1
            continue
        signals.setdefault(due, []).append(activation)
    cash, position, trades, curve = float(initial_capital), None, [], []
    peak, drawdown = cash, 0.

    def value(mark, timestamp):
        return (position["starting_usd"] - position["entry_fee"] + position["units"] * (mark - position["entry_price"])
                - position["units"] * mark * costs.commission) / rates.at(timestamp)

    def close(mark, timestamp, reason):
        nonlocal cash, position
        cash = value(mark, timestamp)
        pnl = cash - position["starting_eur"]
        trades.append({"ticker": ticker, "type": "BUY", "entry_time": position["entry_time"],
                       "time": int(timestamp.timestamp()*1000), "entry_price": position["entry_price"],
                       "exit_price": mark, "pnl": pnl, "reason": reason, "tp1_taken": reason == "TP1",
                       "units": position["units"], "setup_id": position["setup_id"],
                       "stop_loss": position["stop"], "take_profit_1": position["target"],
                       "activation_available_at": position["activation_available_at"]})
        position = None

    for timestamp, row in frame.iterrows():
        if timestamp in signals:
            for activation in sorted(signals[timestamp], key=lambda a: a["setup_id"]):
                if position:
                    rejected["account_occupied"] += 1
                    continue
                state = activation["state"]
                if state.get("direction") != "BUY":
                    rejected["sell_skipped_long_only"] += 1
                    continue
                plan = state["plan"]
                entry = float(row.Open) * (1 + costs.slippage + costs.half_spread)
                stop, target = float(plan["stop_loss"]), float(plan["take_profit_1"])
                entry_cost = entry * (1 + costs.commission)
                stop_proceeds = stop * (1-costs.slippage-costs.half_spread) * (1-costs.commission)
                risk, reward = entry_cost - stop_proceeds, target * (1-costs.commission) - entry_cost
                if not all(math.isfinite(v) and v > 0 for v in (entry, stop, target)) or risk <= 0 or reward / risk < MIN_REWARD_RISK:
                    rejected["entry_gap_or_costs_invalidated_setup"] += 1
                    continue
                usd = cash * rates.at(timestamp)
                units = usd / (entry * (1 + costs.commission))
                position = {"entry_price": entry, "stop": stop, "target": target, "units": units,
                            "starting_eur": cash, "starting_usd": usd, "entry_fee": units*entry*costs.commission,
                            "entry_time": int(timestamp.timestamp()*1000), "setup_id": activation["setup_id"],
                            "activation_available_at": activation["available_at"]}
        if position:
            adverse = costs.slippage + costs.half_spread
            if row.Open <= position["stop"]:
                close(float(row.Open)*(1-adverse), timestamp, "STOP_GAP")
            elif row.Open >= position["target"]:
                close(position["target"], timestamp, "TP1")
            elif row.Low <= position["stop"]:
                close(position["stop"]*(1-adverse), timestamp+pd.Timedelta(minutes=1),
                      "STOP_BOTH_HIT" if row.High >= position["target"] else "STOP")
            elif row.High >= position["target"]:
                close(position["target"], timestamp+pd.Timedelta(minutes=1), "TP1")
        mark = value(float(row.Close), timestamp+pd.Timedelta(minutes=1)) if position else cash
        peak = max(peak, mark)
        drawdown = max(drawdown, (peak-mark)/peak*100)
        curve.append({"timestamp": int((timestamp+pd.Timedelta(minutes=1)).timestamp()*1000), "equity": mark})
    if position:
        close(float(frame.Close.iloc[-1])*(1-costs.slippage-costs.half_spread), frame.index[-1]+pd.Timedelta(minutes=1), "END_OF_DATA")
        curve[-1]["equity"] = cash
        peak = max(peak, cash)
        drawdown = max(drawdown, (peak-cash)/peak*100)
    gains = sum(max(t["pnl"], 0) for t in trades)
    losses = -sum(min(t["pnl"], 0) for t in trades)
    return {"analysis_mode": "gemini_quant", "strategy_version": "archived-gemini-lightgbm-2", "strategy_key": strategy_key(timeframe),
            "cost_rates": costs.__dict__, "allow_short": False,
            "currency": "EUR", "quote_currency": "USD", "symbols": [ticker], "timeframe": timeframe,
            "initial_capital": initial_capital, "final_capital": round(cash, 6),
            "total_return_pct": round((cash/initial_capital-1)*100, 6), "total_trades": len(trades),
            "winning_trades": sum(t["pnl"] > 0 for t in trades), "losing_trades": sum(t["pnl"] < 0 for t in trades),
            "win_rate": sum(t["pnl"] > 0 for t in trades)/len(trades)*100 if trades else 0,
            "profit_factor": gains/losses if losses else None, "max_drawdown_pct": drawdown,
            "start_at": int(frame.index[0].timestamp()*1000), "end_at": int((frame.index[-1]+pd.Timedelta(minutes=1)).timestamp()*1000),
            "trades": trades, "recent_trades": trades[-100:], "markers": [], "equity_curve": curve,
            "execution_rejections": dict(rejected), "news_coverage_bars": 0,
            "scope": "Aufgezeichnete Aktivierungen von Gemini + Python-Indikatoren + dokumentiertem LightGBM-Prüfstatus; gemeinsame TP1-Auswertung. Kein vollständiger KI-Test jeder historischen Kerze.",
            "position_sizing": "5.000 EUR; eine finanzierte Aktienposition; gesamtes verfügbares Kapital; keine Leerverkäufe.",
            "costs": f"Je Ausführung {costs.commission*100:.3f}% Gebühr; Marktorders zusätzlich {costs.slippage*100:.3f}% Slippage + {costs.half_spread*100:.3f}% halber Spread.",
            "assumptions": "Einstieg frühestens am nächsten 1m-Open nach beobachteter Aktivierung. RR nach Kosten mindestens 1,5. Vollausstieg an TP1 oder Stop, Stop zuerst bei Doppeltreffer. Kein TP2, keine spätere KI-Planänderung. Euro-Abrechnung mit letzter abgeschlossener EURUSD-Tageskerze. Nur tatsächlich aufgezeichnete Freigaben, kein lückenloser historischer Strategietest; keine neuen Gemini-Aufrufe."}
