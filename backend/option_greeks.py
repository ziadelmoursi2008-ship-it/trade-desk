"""Dividend-adjusted Black–Scholes–Merton risk measures, per option share.

European model; US equity/ETF options are generally American. No early exercise,
discrete dividends, dealer inventory or directional forecast is modelled here.
Vega/rho are for ONE percentage point; theta is per calendar day (365).
"""
import math


def calculate_greeks(spot, strike, years, rate, volatility, kind, dividend_yield=0.0):
    values = (spot, strike, years, rate, volatility, dividend_yield)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError("Model inputs must be finite numbers.")
    if min(spot, strike, years, volatility) <= 0 or years > 10 or volatility > 5:
        raise ValueError("Positive spot, strike, maturity and IV required within model limits.")
    if not -0.1 <= rate <= 1 or not 0 <= dividend_yield <= 1 or kind not in ("call", "put"):
        raise ValueError("Invalid rate, dividend yield or option type.")
    root = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate - dividend_yield + volatility ** 2 / 2) * years) / (volatility * root)
    d2 = d1 - volatility * root
    cdf = lambda x: 0.5 * math.erfc(-x / math.sqrt(2))
    density = math.exp(-d1 ** 2 / 2) / math.sqrt(2 * math.pi)
    discounted_spot = spot * math.exp(-dividend_yield * years)
    discounted_strike = strike * math.exp(-rate * years)
    carry = -discounted_spot * density * volatility / (2 * root)
    if kind == "call":
        value = discounted_spot * cdf(d1) - discounted_strike * cdf(d2)
        delta = math.exp(-dividend_yield * years) * cdf(d1)
        theta = carry - rate * discounted_strike * cdf(d2) + dividend_yield * discounted_spot * cdf(d1)
        rho = years * discounted_strike * cdf(d2) / 100
    else:
        value = discounted_strike * cdf(-d2) - discounted_spot * cdf(-d1)
        delta = -math.exp(-dividend_yield * years) * cdf(-d1)
        theta = carry + rate * discounted_strike * cdf(-d2) - dividend_yield * discounted_spot * cdf(-d1)
        rho = -years * discounted_strike * cdf(-d2) / 100
    result = {"model_price": value, "delta": delta,
              "gamma": math.exp(-dividend_yield * years) * density / (spot * volatility * root),
              "theta": theta / 365, "vega": discounted_spot * density * root / 100, "rho": rho}
    if not all(math.isfinite(v) for v in result.values()):
        raise ValueError("Model overflow.")
    return result
