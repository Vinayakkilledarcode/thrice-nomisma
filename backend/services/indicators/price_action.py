# services/indicators/price_action.py
import numpy as np
import pandas as pd

from .registry import indicator, last_float

CATEGORY = "price_action"


def _prev_session(df: pd.DataFrame):
    """Previous completed bar's H/L/C, used as the base for all pivot families."""
    if len(df) < 2:
        row = df.iloc[-1]
    else:
        row = df.iloc[-2]
    return float(row["High"]), float(row["Low"]), float(row["Close"])


@indicator("classic_pivot_points", CATEGORY, min_bars=2, description="Classic Floor Pivot Points (PP, S1-S3, R1-R3)")
def classic_pivots(df: pd.DataFrame) -> dict:
    high, low, close = _prev_session(df)
    pp = (high + low + close) / 3.0
    r1, s1 = 2 * pp - low, 2 * pp - high
    r2, s2 = pp + (high - low), pp - (high - low)
    r3, s3 = high + 2 * (pp - low), low - 2 * (high - pp)
    price = float(df["Close"].iloc[-1])
    return {
        "value": {"pp": round(pp, 2), "r1": round(r1, 2), "r2": round(r2, 2), "r3": round(r3, 2),
                  "s1": round(s1, 2), "s2": round(s2, 2), "s3": round(s3, 2)},
        "signal": "BULLISH" if price > pp else ("BEARISH" if price < pp else "NEUTRAL"),
    }


@indicator("camarilla_pivots", CATEGORY, min_bars=2, description="Camarilla Pivot Points (R1-R4, S1-S4)")
def camarilla_pivots(df: pd.DataFrame) -> dict:
    high, low, close = _prev_session(df)
    rng = high - low
    levels = {}
    for i, mult in zip([1, 2, 3, 4], [1.1 / 12, 1.1 / 6, 1.1 / 4, 1.1 / 2]):
        levels[f"r{i}"] = round(close + rng * mult, 2)
        levels[f"s{i}"] = round(close - rng * mult, 2)
    price = float(df["Close"].iloc[-1])
    return {"value": levels, "signal": "BULLISH" if price > close else ("BEARISH" if price < close else "NEUTRAL")}


@indicator("woodie_pivots", CATEGORY, min_bars=2, description="Woodie Pivot Points (weights current-session open more heavily)")
def woodie_pivots(df: pd.DataFrame) -> dict:
    high, low, close = _prev_session(df)
    open_now = float(df["Open"].iloc[-1])
    pp = (high + low + 2 * open_now) / 4.0
    r1, s1 = 2 * pp - low, 2 * pp - high
    r2, s2 = pp + (high - low), pp - (high - low)
    price = float(df["Close"].iloc[-1])
    return {
        "value": {"pp": round(pp, 2), "r1": round(r1, 2), "r2": round(r2, 2), "s1": round(s1, 2), "s2": round(s2, 2)},
        "signal": "BULLISH" if price > pp else ("BEARISH" if price < pp else "NEUTRAL"),
    }


@indicator("fibonacci_retracement_50", CATEGORY, min_bars=50,
           description="Fibonacci retracement levels from the swing high/low over the last 50 bars")
def fibonacci_retracement(df: pd.DataFrame) -> dict:
    window = df.tail(50)
    swing_high, swing_low = float(window["High"].max()), float(window["Low"].min())
    rng = swing_high - swing_low
    ratios = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    levels = {f"fib_{r}": round(swing_high - rng * r, 2) for r in ratios}
    price = float(df["Close"].iloc[-1])
    mid = swing_low + rng * 0.5
    return {"value": {**levels, "swing_high": round(swing_high, 2), "swing_low": round(swing_low, 2)},
            "signal": "BULLISH" if price > mid else ("BEARISH" if price < mid else "NEUTRAL")}


@indicator("rolling_support_resistance_20", CATEGORY, min_bars=20,
           description="Nearest rolling support/resistance from local extrema (20-bar window)")
def support_resistance(df: pd.DataFrame) -> dict:
    resistance = float(df["High"].tail(20).max())
    support = float(df["Low"].tail(20).min())
    price = float(df["Close"].iloc[-1])
    dist_to_r = (resistance - price) / price * 100 if price else 0.0
    dist_to_s = (price - support) / price * 100 if price else 0.0
    signal = "NEAR_RESISTANCE" if dist_to_r < dist_to_s else "NEAR_SUPPORT"
    return {"value": {"resistance": round(resistance, 2), "support": round(support, 2)},
            "signal": signal, "distance_to_resistance_pct": round(dist_to_r, 2),
            "distance_to_support_pct": round(dist_to_s, 2)}


@indicator("swing_points_5", CATEGORY, min_bars=11,
           description="Most recent confirmed swing high/low (5-bar fractal)")
def swing_points(df: pd.DataFrame) -> dict:
    n = 5
    highs, lows = df["High"], df["Low"]
    swing_high = None
    swing_low = None
    for i in range(len(df) - n - 1, n - 1, -1):
        window_h = highs.iloc[i - n:i + n + 1]
        window_l = lows.iloc[i - n:i + n + 1]
        if swing_high is None and highs.iloc[i] == window_h.max():
            swing_high = (df.index[i].isoformat() if hasattr(df.index[i], "isoformat") else str(df.index[i]),
                          round(float(highs.iloc[i]), 2))
        if swing_low is None and lows.iloc[i] == window_l.min():
            swing_low = (df.index[i].isoformat() if hasattr(df.index[i], "isoformat") else str(df.index[i]),
                         round(float(lows.iloc[i]), 2))
        if swing_high and swing_low:
            break
    price = float(df["Close"].iloc[-1])
    sh_val = swing_high[1] if swing_high else None
    sl_val = swing_low[1] if swing_low else None
    if sh_val and price > sh_val:
        signal = "BROKE_STRUCTURE_UP"
    elif sl_val and price < sl_val:
        signal = "BROKE_STRUCTURE_DOWN"
    else:
        signal = "NEUTRAL"
    return {"value": {"last_swing_high": swing_high, "last_swing_low": swing_low}, "signal": signal}


@indicator("gap_detection", CATEGORY, min_bars=2, description="Detects an open-vs-prior-close price gap on the latest bar")
def gap_detection(df: pd.DataFrame) -> dict:
    prev_close = float(df["Close"].iloc[-2])
    open_now = float(df["Open"].iloc[-1])
    gap_pct = ((open_now - prev_close) / prev_close) * 100 if prev_close else 0.0
    if gap_pct > 0.5:
        signal = "GAP_UP"
    elif gap_pct < -0.5:
        signal = "GAP_DOWN"
    else:
        signal = "NO_GAP"
    return {"value": round(gap_pct, 3), "signal": signal}


@indicator("fifty_two_week_range", CATEGORY, min_bars=30,
           description="Position of Close within its trailing high/low range (up to 252 bars)")
def fifty_two_week_range(df: pd.DataFrame) -> dict:
    window = df.tail(min(252, len(df)))
    hi, lo = float(window["High"].max()), float(window["Low"].min())
    price = float(df["Close"].iloc[-1])
    pct_of_range = ((price - lo) / (hi - lo) * 100) if (hi - lo) else 50.0
    signal = "NEAR_HIGH" if pct_of_range >= 90 else ("NEAR_LOW" if pct_of_range <= 10 else "NEUTRAL")
    return {"value": {"high": round(hi, 2), "low": round(lo, 2), "pct_of_range": round(pct_of_range, 2)},
            "signal": signal}
