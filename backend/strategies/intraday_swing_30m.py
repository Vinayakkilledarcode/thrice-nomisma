# strategies/intraday_swing_30m.py
import pandas as pd
import numpy as np
from typing import Dict, Any

from strategies.strategy_utils import (
    compute_atr,
    session_vwap,
    risk_management_block,
    direction_from_action,
    signal_quality_label,
    safe_evaluate,
    composite_score,
    decide_direction,
    score_to_confidence,
    hold_confidence,
)


@safe_evaluate(default_key="confidence")
def evaluate_intraday_swing_30m(df: pd.DataFrame) -> Dict[str, Any]:
    """
    30-Minute (Intraday Swing) - Adaptive SuperTrend + Volume-Weighted MACD.
    Decision model: composite score across SuperTrend direction, MACD
    momentum and MACD-vs-signal spread, so a trend that's aligned on two of
    the three no longer gets silently overridden into HOLD by the third.
    """
    if df.empty or len(df) < 26:
        return {"action": "HOLD", "confidence": 0.0, "signal_quality": "NEUTRAL", "indicators": {}}

    df = df.copy()
    df.columns = [c.title() for c in df.columns]

    close = df['Close']
    high = df['High']
    low = df['Low']
    vol = df['Volume']

    df['VW_Close'] = (close * vol) / vol.rolling(window=12).mean().replace(0, np.nan)
    df['EMA_Fast'] = df['VW_Close'].ewm(span=12, adjust=False).mean()
    df['EMA_Slow'] = df['VW_Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = df['EMA_Fast'] - df['EMA_Slow']
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

    df['VWAP'] = session_vwap(df)
    df['ATR'] = compute_atr(df, period=10)

    tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
    df['ATR_Adapt'] = tr.rolling(window=10).mean()
    df['Std_ATR'] = df['ATR_Adapt'].rolling(window=10).std()
    df['Adapt_Mult'] = np.clip(3.0 + (df['Std_ATR'] / df['ATR_Adapt'].replace(0, np.nan)), 2.5, 4.0)

    df['Mid'] = (high + low) / 2.0
    df['Upper_Band'] = df['Mid'] + (df['ATR_Adapt'] * df['Adapt_Mult'])
    df['Lower_Band'] = df['Mid'] - (df['ATR_Adapt'] * df['Adapt_Mult'])

    trend_vals = np.ones(len(df))
    for i in range(1, len(df)):
        if df['Close'].iloc[i] > df['Upper_Band'].iloc[i-1]:
            trend_vals[i] = 1
        elif df['Close'].iloc[i] < df['Lower_Band'].iloc[i-1]:
            trend_vals[i] = -1
        else:
            trend_vals[i] = trend_vals[i-1]
    df['Trend'] = trend_vals

    last = df.iloc[-1]
    price = float(last['Close'])
    macd, signal = float(last['MACD']), float(last['Signal'])
    trend = int(last['Trend'])
    vwap = float(last['VWAP']) if not np.isnan(last['VWAP']) else price
    upper = float(last['Upper_Band']) if not np.isnan(last['Upper_Band']) else price
    lower = float(last['Lower_Band']) if not np.isnan(last['Lower_Band']) else price
    atr = float(last['ATR']) if not np.isnan(last['ATR']) else 0.0

    # --- Composite scoring ---
    trend_component = float(trend)  # already +/-1
    macd_spread = macd - signal
    spread_component = float(np.clip(macd_spread / max(abs(price) * 0.0025, 1e-6), -1.3, 1.3))
    macd_level_component = float(np.clip(macd / max(abs(price) * 0.004, 1e-6), -1.0, 1.0)) * 0.5

    score = composite_score(trend_component, spread_component, macd_level_component)
    direction = decide_direction(score)

    if direction == "LONG":
        action = "BUY_INTRADAY"
        confidence = score_to_confidence(score)
    elif direction == "SHORT":
        action = "SHORT_INTRADAY"
        confidence = score_to_confidence(score)
    else:
        action = "HOLD"
        confidence = hold_confidence(score)

    trade_direction = direction_from_action(action)
    risk_block = risk_management_block(price, atr, trade_direction, atr_mult_stop=1.6, rr_ratio=2.0)

    return {
        "action": action,
        "confidence": round(confidence, 2),
        "signal_quality": signal_quality_label(confidence),
        "risk_management": risk_block,
        "indicators": {
            "last_price": price,
            "vwap": round(vwap, 2),
            "upper_band": round(upper, 2),
            "lower_band": round(lower, 2),
            "ma20": round(vwap, 2),
            "macd": round(macd, 4),
            "signal_line": round(signal, 4),
            "atr_10": round(atr, 2),
            "supertrend_direction": "UP" if trend == 1 else "DOWN",
            "composite_score": round(score, 3)
        }
    }