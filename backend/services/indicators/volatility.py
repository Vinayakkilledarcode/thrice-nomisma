# services/indicators/volatility.py
import numpy as np
import pandas as pd

from .registry import indicator, atr as _atr, last_float

CATEGORY = "volatility"


@indicator("atr_14", CATEGORY, min_bars=15, description="Average True Range (14)")
def atr_14(df: pd.DataFrame) -> dict:
    val = last_float(_atr(df, 14))
    price = float(df["Close"].iloc[-1])
    pct = (val / price * 100) if price else 0.0
    return {"value": round(val, 4), "signal": "NEUTRAL", "atr_pct_of_price": round(pct, 3)}


@indicator("bollinger_bands_20_2", CATEGORY, min_bars=20, description="Bollinger Bands (20, 2 std) + %B + bandwidth")
def bollinger_bands(df: pd.DataFrame) -> dict:
    close = df["Close"]
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    price = float(close.iloc[-1])
    m, u, l = last_float(mid, price), last_float(upper, price), last_float(lower, price)
    percent_b = (price - l) / (u - l) if (u - l) else 0.5
    bandwidth = (u - l) / m * 100 if m else 0.0
    signal = "OVERBOUGHT" if percent_b >= 1.0 else ("OVERSOLD" if percent_b <= 0.0 else "NEUTRAL")
    return {"value": {"upper": round(u, 4), "mid": round(m, 4), "lower": round(l, 4)},
            "signal": signal, "percent_b": round(percent_b, 3), "bandwidth_pct": round(bandwidth, 3)}


@indicator("keltner_channel_20", CATEGORY, min_bars=20, description="Keltner Channel (EMA20 +/- 2*ATR10)")
def keltner_channel(df: pd.DataFrame) -> dict:
    ema = df["Close"].ewm(span=20, adjust=False).mean()
    band = _atr(df, 10) * 2.0
    upper, lower = ema + band, ema - band
    price = float(df["Close"].iloc[-1])
    u, l, m = last_float(upper, price), last_float(lower, price), last_float(ema, price)
    signal = "OVERBOUGHT" if price >= u else ("OVERSOLD" if price <= l else "NEUTRAL")
    return {"value": {"upper": round(u, 4), "mid": round(m, 4), "lower": round(l, 4)}, "signal": signal}


@indicator("historical_volatility_20", CATEGORY, min_bars=21,
           description="Annualized historical volatility from log returns (20d)")
def historical_volatility(df: pd.DataFrame) -> dict:
    log_ret = np.log(df["Close"] / df["Close"].shift(1))
    hv = log_ret.rolling(20).std() * np.sqrt(252) * 100
    val = last_float(hv, 0.0)
    return {"value": round(val, 2), "signal": "NEUTRAL"}


@indicator("stdev_20", CATEGORY, min_bars=20, description="Standard Deviation of Close (20)")
def stdev_20(df: pd.DataFrame) -> dict:
    val = last_float(df["Close"].rolling(20).std())
    return {"value": round(val, 4), "signal": "NEUTRAL"}


@indicator("chaikin_volatility_10", CATEGORY, min_bars=20,
           description="Chaikin Volatility: % change of EMA(High-Low, 10)")
def chaikin_volatility(df: pd.DataFrame) -> dict:
    hl_range = (df["High"] - df["Low"]).ewm(span=10, adjust=False).mean()
    cv = hl_range.pct_change(10) * 100
    val = last_float(cv, 0.0)
    return {"value": round(val, 2), "signal": "EXPANDING" if val > 0 else "CONTRACTING"}


@indicator("ulcer_index_14", CATEGORY, min_bars=15, description="Ulcer Index (14) -- downside volatility/drawdown depth")
def ulcer_index(df: pd.DataFrame) -> dict:
    close = df["Close"]
    rolling_max = close.rolling(14).max()
    drawdown_pct = ((close - rolling_max) / rolling_max.replace(0, np.nan)) * 100
    ui = np.sqrt((drawdown_pct ** 2).rolling(14).mean())
    val = last_float(ui, 0.0)
    return {"value": round(val, 3), "signal": "NEUTRAL"}


@indicator("atr_percent_14", CATEGORY, min_bars=15, description="ATR expressed as % of price (normalized volatility)")
def atr_percent(df: pd.DataFrame) -> dict:
    a = last_float(_atr(df, 14))
    price = float(df["Close"].iloc[-1])
    val = (a / price * 100) if price else 0.0
    return {"value": round(val, 3), "signal": "NEUTRAL"}


@indicator("choppiness_index_14", CATEGORY, min_bars=15,
           description="Choppiness Index (14) -- >61.8 choppy/rangebound, <38.2 trending")
def choppiness_index(df: pd.DataFrame) -> dict:
    period = 14
    tr = np.maximum(df["High"] - df["Low"],
                     np.maximum(abs(df["High"] - df["Close"].shift(1)), abs(df["Low"] - df["Close"].shift(1))))
    atr_sum = tr.rolling(period).sum()
    hh = df["High"].rolling(period).max()
    ll = df["Low"].rolling(period).min()
    rng = (hh - ll).replace(0, np.nan)
    chop = 100 * np.log10(atr_sum / rng) / np.log10(period)
    val = last_float(chop, 50.0)
    signal = "RANGING" if val >= 61.8 else ("TRENDING" if val <= 38.2 else "NEUTRAL")
    return {"value": round(val, 2), "signal": signal}