# services/indicators/trend.py
import numpy as np
import pandas as pd

from .registry import indicator, wma, hma, atr, last_float, cross_signal

CATEGORY = "trend"


# --------------------------------------------------------------------------- #
# Moving-average family -- generated in a loop instead of copy-pasted, since
# SMA/EMA/WMA/VWMA/HMA at different periods are the same shape of indicator.
# Each still gets its own registry entry (sma_20, ema_50, hma_9, ...) so the
# frontend/API can address them individually.
# --------------------------------------------------------------------------- #
def _register_ma_family(prefix: str, compute_fn, periods, min_bars_pad: int = 5):
    for period in periods:
        def _calc(df: pd.DataFrame, _period=period, _fn=compute_fn) -> dict:
            s = _fn(df, _period)
            last = last_float(s, default=float(df["Close"].iloc[-1]))
            price = float(df["Close"].iloc[-1])
            return {"value": round(last, 4), "signal": cross_signal(price, last)}
        indicator(f"{prefix}_{period}", CATEGORY, min_bars=period + min_bars_pad,
                  description=f"{prefix.upper()} moving average, period {period}")(_calc)


_register_ma_family("sma", lambda df, p: df["Close"].rolling(p).mean(), [10, 20, 50, 100, 200])
_register_ma_family("ema", lambda df, p: df["Close"].ewm(span=p, adjust=False).mean(), [9, 12, 20, 26, 50, 100, 200])
_register_ma_family("wma", lambda df, p: wma(df["Close"], p), [20, 50])
_register_ma_family("vwma", lambda df, p: (df["Close"] * df["Volume"]).rolling(p).sum()
                     / df["Volume"].rolling(p).sum().replace(0, np.nan), [20, 50])
_register_ma_family("hma", lambda df, p: hma(df["Close"], p), [9, 21, 55])


@indicator("dema_20", CATEGORY, min_bars=45, description="Double Exponential Moving Average (20)")
def dema_20(df: pd.DataFrame) -> dict:
    ema1 = df["Close"].ewm(span=20, adjust=False).mean()
    ema2 = ema1.ewm(span=20, adjust=False).mean()
    dema = 2 * ema1 - ema2
    last, price = last_float(dema), float(df["Close"].iloc[-1])
    return {"value": round(last, 4), "signal": cross_signal(price, last)}


@indicator("tema_20", CATEGORY, min_bars=65, description="Triple Exponential Moving Average (20)")
def tema_20(df: pd.DataFrame) -> dict:
    ema1 = df["Close"].ewm(span=20, adjust=False).mean()
    ema2 = ema1.ewm(span=20, adjust=False).mean()
    ema3 = ema2.ewm(span=20, adjust=False).mean()
    tema = 3 * ema1 - 3 * ema2 + ema3
    last, price = last_float(tema), float(df["Close"].iloc[-1])
    return {"value": round(last, 4), "signal": cross_signal(price, last)}


@indicator("kama_10", CATEGORY, min_bars=30, description="Kaufman Adaptive Moving Average (10)")
def kama_10(df: pd.DataFrame) -> dict:
    close = df["Close"]
    period, fast_sc, slow_sc = 10, 2 / (2 + 1), 2 / (30 + 1)
    change = (close - close.shift(period)).abs()
    volatility = (close.diff().abs()).rolling(period).sum().replace(0, np.nan)
    er = (change / volatility).fillna(0.0)
    sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2

    kama_vals = close.copy()
    kama_vals.iloc[:period] = np.nan
    seed_idx = min(period, len(close) - 1)
    kama_vals.iloc[seed_idx] = close.iloc[seed_idx]
    for i in range(seed_idx + 1, len(close)):
        prev = kama_vals.iloc[i - 1]
        kama_vals.iloc[i] = prev + sc.iloc[i] * (close.iloc[i] - prev)

    last, price = last_float(kama_vals), float(close.iloc[-1])
    return {"value": round(last, 4), "signal": cross_signal(price, last)}


@indicator("supertrend_10_3", CATEGORY, min_bars=20, description="Supertrend (ATR 10, multiplier 3)")
def supertrend(df: pd.DataFrame) -> dict:
    period, mult = 10, 3.0
    hl2 = (df["High"] + df["Low"]) / 2.0
    band = atr(df, period) * mult
    upper = hl2 + band
    lower = hl2 - band
    close = df["Close"].values
    trend = np.ones(len(df))
    up = upper.values.copy()
    lo = lower.values.copy()
    for i in range(1, len(df)):
        if close[i - 1] > up[i - 1]:
            up[i] = min(up[i], up[i - 1])
        if close[i - 1] < lo[i - 1]:
            lo[i] = max(lo[i], lo[i - 1])
        if close[i] > up[i - 1]:
            trend[i] = 1
        elif close[i] < lo[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    line = np.where(trend == 1, lo, up)
    return {
        "value": round(float(line[-1]), 4),
        "signal": "BULLISH" if trend[-1] == 1 else "BEARISH",
    }


@indicator("adx_14", CATEGORY, min_bars=30, description="Average Directional Index (14) with +DI/-DI")
def adx_14(df: pd.DataFrame) -> dict:
    period = 14
    high, low, close = df["High"], df["Low"], df["Close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = atr(df, period).replace(0, np.nan)
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(period).mean() / tr)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(period).mean() / tr)
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.rolling(period).mean()
    adx_last, plus_last, minus_last = last_float(adx), last_float(plus_di), last_float(minus_di)
    if adx_last < 20:
        signal = "RANGING"
    else:
        signal = "BULLISH" if plus_last > minus_last else "BEARISH"
    return {"value": {"adx": round(adx_last, 2), "plus_di": round(plus_last, 2), "minus_di": round(minus_last, 2)},
            "signal": signal}


@indicator("ichimoku", CATEGORY, min_bars=60, description="Ichimoku Cloud (Tenkan/Kijun/Senkou A&B)")
def ichimoku(df: pd.DataFrame) -> dict:
    high, low, close = df["High"], df["Low"], df["Close"]
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2.0
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2.0
    span_a = ((tenkan + kijun) / 2.0).shift(26)
    span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2.0).shift(26)
    price = float(close.iloc[-1])
    top, bot = last_float(span_a, price), last_float(span_b, price)
    cloud_top, cloud_bot = max(top, bot), min(top, bot)
    if price > cloud_top:
        signal = "BULLISH"
    elif price < cloud_bot:
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"
    return {
        "value": {
            "tenkan_sen": round(last_float(tenkan, price), 4),
            "kijun_sen": round(last_float(kijun, price), 4),
            "senkou_span_a": round(top, 4),
            "senkou_span_b": round(bot, 4),
        },
        "signal": signal,
    }


@indicator("parabolic_sar", CATEGORY, min_bars=15, description="Parabolic SAR (step 0.02, max 0.2)")
def parabolic_sar(df: pd.DataFrame) -> dict:
    high, low, close = df["High"].values, df["Low"].values, df["Close"].values
    step, max_step = 0.02, 0.2
    n = len(df)
    sar = np.zeros(n)
    trend_up = True
    af = step
    ep = high[0]
    sar[0] = low[0]
    for i in range(1, n):
        prev_sar = sar[i - 1]
        sar[i] = prev_sar + af * (ep - prev_sar)
        if trend_up:
            sar[i] = min(sar[i], low[i - 1], low[max(i - 2, 0)])
            if high[i] > ep:
                ep = high[i]
                af = min(af + step, max_step)
            if low[i] < sar[i]:
                trend_up = False
                sar[i] = ep
                ep = low[i]
                af = step
        else:
            sar[i] = max(sar[i], high[i - 1], high[max(i - 2, 0)])
            if low[i] < ep:
                ep = low[i]
                af = min(af + step, max_step)
            if high[i] > sar[i]:
                trend_up = True
                sar[i] = ep
                ep = high[i]
                af = step
    return {"value": round(float(sar[-1]), 4), "signal": "BULLISH" if trend_up else "BEARISH"}


@indicator("donchian_20", CATEGORY, min_bars=20, description="Donchian Channel (20)")
def donchian_20(df: pd.DataFrame) -> dict:
    upper = df["High"].rolling(20).max()
    lower = df["Low"].rolling(20).min()
    price = float(df["Close"].iloc[-1])
    up, lo = last_float(upper, price), last_float(lower, price)
    mid = (up + lo) / 2.0
    return {"value": {"upper": round(up, 4), "lower": round(lo, 4), "mid": round(mid, 4)},
            "signal": cross_signal(price, mid)}


@indicator("linreg_slope_20", CATEGORY, min_bars=20, description="Linear Regression Slope of Close (20)")
def linreg_slope_20(df: pd.DataFrame) -> dict:
    period = 20
    y = df["Close"].tail(period).values
    x = np.arange(period)
    slope, intercept = np.polyfit(x, y, 1)
    pct_slope = (slope / y.mean()) * 100 if y.mean() else 0.0
    return {"value": round(float(pct_slope), 4),
            "signal": "BULLISH" if slope > 0 else ("BEARISH" if slope < 0 else "NEUTRAL")}
