# services/indicators/configurable.py
"""
Settings-panel indicators (spec: "Adjustable Settings" path).

These are the indicators the frontend lets the user hand-tune -- e.g.
dragging RSI's period from 14 to 25, or Bollinger's std-dev from 2 to 2.5
-- via POST /indicators/configurable. Every function here is registered
with @configurable rather than @indicator, which means:

  1. Whatever params the user supplies are validated/clamped against a
     declared schema and merged with sane defaults -- never dropped.
  2. The computation always runs with that exact resolved parameter set.
     A custom period that was never "pre-registered" as its own named
     indicator (like "rsi_25") is still computed correctly here, instead
     of failing to match a fixed name and coming back blank.
  3. The response always echoes back "params_used" so the UI can confirm
     exactly what was computed, even if some values were clamped.

This module intentionally duplicates a small amount of math already
present in trend.py/momentum.py/volatility.py (fixed-period variants).
That's by design: the fixed registry and the configurable registry serve
different callers (default sweep vs. user-tuned single indicator) and
must not be coupled, so tuning one path can never silently affect the
other.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .registry import configurable, wma as _wma, hma as _hma, atr as _atr, rsi as _rsi, last_float, cross_signal

CATEGORY = "configurable"


# --------------------------------------------------------------------------- #
# Moving averages
# --------------------------------------------------------------------------- #
@configurable(
    "sma", CATEGORY,
    param_schema={"period": {"type": int, "default": 20, "min": 2, "max": 500}},
    min_bars_fn=lambda p: p["period"] + 5,
    description="Simple Moving Average with a user-adjustable period",
)
def sma(df: pd.DataFrame, period: int) -> dict:
    s = df["Close"].rolling(period).mean()
    last = last_float(s, default=float(df["Close"].iloc[-1]))
    price = float(df["Close"].iloc[-1])
    return {"value": round(last, 4), "signal": cross_signal(price, last)}


@configurable(
    "ema", CATEGORY,
    param_schema={"period": {"type": int, "default": 20, "min": 2, "max": 500}},
    min_bars_fn=lambda p: p["period"] + 5,
    description="Exponential Moving Average with a user-adjustable period",
)
def ema(df: pd.DataFrame, period: int) -> dict:
    s = df["Close"].ewm(span=period, adjust=False).mean()
    last = last_float(s, default=float(df["Close"].iloc[-1]))
    price = float(df["Close"].iloc[-1])
    return {"value": round(last, 4), "signal": cross_signal(price, last)}


@configurable(
    "wma", CATEGORY,
    param_schema={"period": {"type": int, "default": 20, "min": 2, "max": 500}},
    min_bars_fn=lambda p: p["period"] + 5,
    description="Weighted Moving Average with a user-adjustable period",
)
def wma(df: pd.DataFrame, period: int) -> dict:
    s = _wma(df["Close"], period)
    last = last_float(s, default=float(df["Close"].iloc[-1]))
    price = float(df["Close"].iloc[-1])
    return {"value": round(last, 4), "signal": cross_signal(price, last)}


@configurable(
    "hma", CATEGORY,
    param_schema={"period": {"type": int, "default": 20, "min": 4, "max": 500}},
    min_bars_fn=lambda p: p["period"] + 10,
    description="Hull Moving Average with a user-adjustable period",
)
def hma(df: pd.DataFrame, period: int) -> dict:
    s = _hma(df["Close"], period)
    last = last_float(s, default=float(df["Close"].iloc[-1]))
    price = float(df["Close"].iloc[-1])
    return {"value": round(last, 4), "signal": cross_signal(price, last)}


# --------------------------------------------------------------------------- #
# Volatility
# --------------------------------------------------------------------------- #
@configurable(
    "atr", CATEGORY,
    param_schema={"period": {"type": int, "default": 14, "min": 2, "max": 200}},
    min_bars_fn=lambda p: p["period"] + 5,
    description="Average True Range with a user-adjustable period",
)
def atr(df: pd.DataFrame, period: int) -> dict:
    val = last_float(_atr(df, period))
    price = float(df["Close"].iloc[-1])
    pct = (val / price * 100) if price else 0.0
    return {"value": round(val, 4), "signal": "NEUTRAL", "atr_pct_of_price": round(pct, 3)}


@configurable(
    "bollinger", CATEGORY,
    param_schema={
        "period": {"type": int, "default": 20, "min": 2, "max": 200},
        "std_dev": {"type": float, "default": 2.0, "min": 0.5, "max": 5.0},
    },
    min_bars_fn=lambda p: p["period"] + 5,
    description="Bollinger Bands with a user-adjustable period and standard-deviation multiplier",
)
def bollinger(df: pd.DataFrame, period: int, std_dev: float) -> dict:
    close = df["Close"]
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    price = float(close.iloc[-1])
    m, u, l = last_float(mid, price), last_float(upper, price), last_float(lower, price)
    percent_b = (price - l) / (u - l) if (u - l) else 0.5
    bandwidth = (u - l) / m * 100 if m else 0.0
    signal = "OVERBOUGHT" if percent_b >= 1.0 else ("OVERSOLD" if percent_b <= 0.0 else "NEUTRAL")
    return {"value": {"upper": round(u, 4), "mid": round(m, 4), "lower": round(l, 4)},
            "signal": signal, "percent_b": round(percent_b, 3), "bandwidth_pct": round(bandwidth, 3)}


# --------------------------------------------------------------------------- #
# Momentum / oscillators
# --------------------------------------------------------------------------- #
@configurable(
    "rsi", CATEGORY,
    param_schema={"period": {"type": int, "default": 14, "min": 2, "max": 200}},
    min_bars_fn=lambda p: p["period"] + 5,
    description="Relative Strength Index with a user-adjustable period",
)
def rsi(df: pd.DataFrame, period: int) -> dict:
    s = _rsi(df["Close"], period)
    val = last_float(s, default=50.0)
    signal = "OVERBOUGHT" if val >= 70 else ("OVERSOLD" if val <= 30 else "NEUTRAL")
    return {"value": round(val, 2), "signal": signal}


@configurable(
    "cci", CATEGORY,
    param_schema={"period": {"type": int, "default": 20, "min": 2, "max": 200}},
    min_bars_fn=lambda p: p["period"] + 5,
    description="Commodity Channel Index with a user-adjustable period",
)
def cci(df: pd.DataFrame, period: int) -> dict:
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    sma_tp = tp.rolling(period).mean()
    mean_dev = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    val_series = (tp - sma_tp) / (0.015 * mean_dev.replace(0, np.nan))
    val = last_float(val_series, 0.0)
    signal = "OVERBOUGHT" if val >= 100 else ("OVERSOLD" if val <= -100 else "NEUTRAL")
    return {"value": round(val, 2), "signal": signal}


@configurable(
    "williams_r", CATEGORY,
    param_schema={"period": {"type": int, "default": 14, "min": 2, "max": 200}},
    min_bars_fn=lambda p: p["period"] + 5,
    description="Williams %R with a user-adjustable period",
)
def williams_r(df: pd.DataFrame, period: int) -> dict:
    high_max = df["High"].rolling(period).max()
    low_min = df["Low"].rolling(period).min()
    wr = ((high_max - df["Close"]) / (high_max - low_min).replace(0, np.nan)) * -100
    val = last_float(wr, -50.0)
    signal = "OVERBOUGHT" if val >= -20 else ("OVERSOLD" if val <= -80 else "NEUTRAL")
    return {"value": round(val, 2), "signal": signal}


@configurable(
    "macd", CATEGORY,
    param_schema={
        "fast": {"type": int, "default": 12, "min": 2, "max": 200},
        "slow": {"type": int, "default": 26, "min": 3, "max": 400},
        "signal_period": {"type": int, "default": 9, "min": 2, "max": 200},
    },
    min_bars_fn=lambda p: p["slow"] + p["signal_period"] + 5,
    description="MACD with user-adjustable fast/slow/signal periods",
)
def macd(df: pd.DataFrame, fast: int, slow: int, signal_period: int) -> dict:
    ema_fast = df["Close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["Close"].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    hist = macd_line - signal_line
    m, s, h = last_float(macd_line), last_float(signal_line), last_float(hist)
    return {"value": {"macd": round(m, 4), "signal_line": round(s, 4), "histogram": round(h, 4)},
            "signal": "BULLISH" if m > s else ("BEARISH" if m < s else "NEUTRAL")}


@configurable(
    "stochastic", CATEGORY,
    param_schema={
        "k_period": {"type": int, "default": 14, "min": 2, "max": 200},
        "d_period": {"type": int, "default": 3, "min": 1, "max": 50},
    },
    min_bars_fn=lambda p: p["k_period"] + p["d_period"] + 5,
    description="Stochastic Oscillator with user-adjustable %K and %D periods",
)
def stochastic(df: pd.DataFrame, k_period: int, d_period: int) -> dict:
    low_min = df["Low"].rolling(k_period).min()
    high_max = df["High"].rolling(k_period).max()
    k = ((df["Close"] - low_min) / (high_max - low_min).replace(0, np.nan)) * 100
    d = k.rolling(d_period).mean()
    k_last, d_last = last_float(k, 50.0), last_float(d, 50.0)
    signal = "OVERBOUGHT" if k_last >= 80 else ("OVERSOLD" if k_last <= 20 else "NEUTRAL")
    return {"value": {"k": round(k_last, 2), "d": round(d_last, 2)}, "signal": signal}
