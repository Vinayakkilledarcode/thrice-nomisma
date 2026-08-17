# strategies/intraday_10m.py
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
def evaluate_intraday_10m(df: pd.DataFrame) -> Dict[str, Any]:
    """
    10-Minute (Intraday) - Volatility Expansion Channel Breakout + True Strength Index.
    Decision model: composite score across channel position, TSI momentum and
    volatility expansion. The strategy commits to BUY/SHORT whenever the
    weight of evidence leans clearly one way -- it no longer requires a full
    textbook breakout (price beyond the 20-bar channel AND |TSI| > 15 AND
    vol_ratio > 1.25 all at once) before saying anything other than HOLD.
    """
    if df.empty or len(df) < 25:
        return {"action": "HOLD", "confidence": 0.0, "signal_quality": "NEUTRAL", "indicators": {}}

    df = df.copy()
    df.columns = [c.title() for c in df.columns]

    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df.get('Volume', pd.Series(1, index=df.index)).fillna(1).replace(0, 1)

    df['Donchian_High'] = high.shift(1).rolling(window=20).max()
    df['Donchian_Low'] = low.shift(1).rolling(window=20).min()

    # TSI Smooths
    diff = close.diff()
    abs_diff = abs(diff)
    double_smoothed_diff = diff.ewm(span=25, adjust=False).mean().ewm(span=13, adjust=False).mean()
    double_smoothed_abs_diff = abs_diff.ewm(span=25, adjust=False).mean().ewm(span=13, adjust=False).mean()
    df['TSI'] = 100 * (double_smoothed_diff / double_smoothed_abs_diff.replace(0, np.nan))

    df['VWAP'] = session_vwap(df)
    df['ATR'] = compute_atr(df, period=14)

    df['Short_Vol'] = close.rolling(window=5).std()
    df['Long_Vol'] = close.rolling(window=20).std()
    df['Vol_Ratio'] = df['Short_Vol'] / df['Long_Vol'].replace(0, np.nan)

    last = df.iloc[-1]
    price = float(last['Close'])
    donchian_high = float(last['Donchian_High']) if not np.isnan(last['Donchian_High']) else price
    donchian_low = float(last['Donchian_Low']) if not np.isnan(last['Donchian_Low']) else price
    tsi = float(last['TSI']) if not np.isnan(last['TSI']) else 0.0
    vol_ratio = float(last['Vol_Ratio']) if not np.isnan(last['Vol_Ratio']) else 1.0
    vwap = float(last['VWAP']) if not np.isnan(last['VWAP']) else price
    atr = float(last['ATR']) if not np.isnan(last['ATR']) else 0.0

    # --- Composite scoring (replaces the old rigid AND-gate) ---
    channel_mid = (donchian_high + donchian_low) / 2.0
    channel_half_range = max((donchian_high - donchian_low) / 2.0, price * 0.0015)
    channel_component = float(np.clip((price - channel_mid) / channel_half_range, -1.3, 1.3))
    tsi_component = float(np.clip(tsi / 22.0, -1.3, 1.3))
    # Volatility expansion sharpens whichever direction the other two already lean;
    # it no longer has to clear 1.25x on its own before the strategy can act.
    expansion_component = float(np.clip((vol_ratio - 1.0) * 1.4, -0.6, 1.3))
    expansion_component = expansion_component if np.sign(expansion_component or 1) == np.sign(channel_component or 1) or channel_component == 0 else expansion_component * 0.4

    score = composite_score(channel_component, tsi_component, expansion_component)
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
    risk_block = risk_management_block(price, atr, trade_direction, atr_mult_stop=1.5, rr_ratio=2.0)

    return {
        "action": action,
        "confidence": round(confidence, 2),
        "signal_quality": signal_quality_label(confidence),
        "risk_management": risk_block,
        "indicators": {
            "last_price": price,
            "vwap": round(vwap, 2),
            "upper_band": round(donchian_high, 2),
            "lower_band": round(donchian_low, 2),
            "ma20": round((donchian_high + donchian_low) / 2.0, 2),
            "vol_ratio": round(vol_ratio, 2),
            "atr_14": round(atr, 2),
            "tsi": round(tsi, 2),
            "composite_score": round(score, 3)
        }
    }