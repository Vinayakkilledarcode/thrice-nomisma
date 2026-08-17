# strategies/intraday_mean_reversion_15m.py
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
def evaluate_intraday_mean_reversion_15m(df: pd.DataFrame) -> Dict[str, Any]:
    """
    15-Minute (Intraday Mean Reversion) - Volume-Profile Adjusted Bollinger Bands + RVI Confirmation.
    Decision model: composite score across band position, RVI momentum and
    VWAP dislocation. Acts on a clear lean rather than requiring price to be
    fully outside the 2-sigma band AND RVI past +-0.15 simultaneously.
    """
    if df.empty or len(df) < 20:
        return {"action": "HOLD", "confidence": 0.0, "signal_quality": "NEUTRAL", "indicators": {}}

    df = df.copy()
    df.columns = [c.title() for c in df.columns]

    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df.get('Volume', pd.Series(1, index=df.index)).fillna(1).replace(0, 1)

    df['V_W_Price'] = close * volume
    df['VWMA'] = df['V_W_Price'].rolling(window=20).sum() / volume.rolling(window=20).sum().replace(0, np.nan)
    df['VW_Std'] = close.rolling(window=20).std()
    df['UpperBand'] = df['VWMA'] + (df['VW_Std'] * 2.0)
    df['LowerBand'] = df['VWMA'] - (df['VW_Std'] * 2.0)

    df['VWAP'] = session_vwap(df)
    df['ATR'] = compute_atr(df, period=14)

    num = (close - close.shift(4)) + 2 * (close.shift(1) - close.shift(5)) + 2 * (close.shift(2) - close.shift(6)) + (close.shift(3) - close.shift(7))
    den = (high - low) + 2 * (high.shift(1) - low.shift(1)) + 2 * (high.shift(2) - low.shift(2)) + (high.shift(3) - low.shift(3))
    df['RVI'] = num.rolling(window=10).sum() / den.rolling(window=10).sum().replace(0, np.nan)

    last = df.iloc[-1]
    price = float(last['Close'])
    vwma = float(last['VWMA']) if not np.isnan(last['VWMA']) else price
    upper = float(last['UpperBand']) if not np.isnan(last['UpperBand']) else price
    lower = float(last['LowerBand']) if not np.isnan(last['LowerBand']) else price
    vwap = float(last['VWAP']) if not np.isnan(last['VWAP']) else price
    rvi = float(last['RVI']) if not np.isnan(last['RVI']) else 0.0
    atr = float(last['ATR']) if not np.isnan(last['ATR']) else 0.0
    band_width = ((upper - lower) / vwma * 100) if vwma > 0 else 0.0

    # --- Composite scoring ---
    # Mean-reversion: being LOW in the band is bullish (buy-the-dip lean), so
    # this component is inverted relative to a breakout strategy.
    band_half = max((upper - lower) / 2.0, price * 0.0015)
    band_component = float(np.clip(-(price - vwma) / band_half, -1.3, 1.3))
    rvi_component = float(np.clip(-rvi / 0.35, -1.3, 1.3))
    vwap_component = float(np.clip(-(price - vwap) / max(atr, price * 0.001), -1.0, 1.0)) * 0.5

    score = composite_score(band_component, rvi_component, vwap_component)
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
    # Mean-reversion stop sits just past the band, target is the VWMA (mean) itself
    risk_block = risk_management_block(price, atr, trade_direction, atr_mult_stop=1.2, rr_ratio=1.5)

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
            "ma20": round(vwma, 2),
            "band_width_pct": round(band_width, 2),
            "atr_14": round(atr, 2),
            "rvi": round(rvi, 3),
            "composite_score": round(score, 3)
        }
    }