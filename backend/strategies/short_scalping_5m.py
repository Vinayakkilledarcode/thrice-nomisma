# strategies/short_scalping_5m.py
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
def evaluate_short_scalping_5m(df: pd.DataFrame) -> Dict[str, Any]:
    """
    5-Minute (Short Scalping) - Volatility-Scaled VWAP Bands + Money Flow Index (MFI).
    Decision model: composite score across VWAP-band position and MFI
    extremity. Replaces the old rigid gate (price beyond the VWAP band AND
    MFI past a hard 20/80 extreme, simultaneously) which required both a
    genuine band breach AND an extreme oscillator reading on the exact same
    bar -- a combination rare enough that this strategy sat on HOLD almost
    permanently. It now reads the prevailing lean continuously and commits
    whenever the weight of evidence clears the shared decision threshold.
    Session-anchored VWAP bands (unchanged from the earlier fix) remain the
    volatility reference.
    """
    if df.empty or len(df) < 14:
        return {"action": "HOLD", "confidence": 0.0, "signal_quality": "NEUTRAL", "indicators": {}}

    df = df.copy()
    df.columns = [c.title() for c in df.columns]

    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df.get('Volume', pd.Series(1, index=df.index)).fillna(1).replace(0, 1)

    df['VWAP'] = session_vwap(df)
    tp = (high + low + close) / 3.0

    if isinstance(df.index, pd.DatetimeIndex):
        session_key = df.index.date
        variance = (close - df['VWAP']) ** 2
        cum_var = (variance * volume).groupby(session_key).cumsum()
        cum_vol = volume.groupby(session_key).cumsum().replace(0, np.nan)
        df['VWAP_Std'] = np.sqrt(cum_var / cum_vol)
    else:
        variance = (close - df['VWAP']) ** 2
        df['VWAP_Std'] = np.sqrt(variance.cumsum() / volume.cumsum().replace(0, np.nan))

    df['ATR'] = compute_atr(df, period=14)
    df['ATR_Ratio'] = (df['ATR'] / close) * 100.0
    df['Dynamic_Mult'] = np.clip(1.5 + df['ATR_Ratio'], 1.8, 3.0)

    df['Band_Upper'] = df['VWAP'] + (df['VWAP_Std'] * df['Dynamic_Mult'])
    df['Band_Lower'] = df['VWAP'] - (df['VWAP_Std'] * df['Dynamic_Mult'])

    # Money Flow Index (MFI-14)
    df['MF'] = tp * volume
    df['MF_Sign'] = np.where(tp > tp.shift(1), 1, -1)
    df['Signed_MF'] = df['MF'] * df['MF_Sign']

    pos_mf = df['Signed_MF'].where(df['Signed_MF'] > 0, 0.0).rolling(window=14).sum()
    neg_mf = abs(df['Signed_MF'].where(df['Signed_MF'] < 0, 0.0)).rolling(window=14).sum()
    mfr = pos_mf / neg_mf.replace(0, np.nan)
    df['MFI_14'] = 100 - (100 / (1 + mfr))

    last = df.iloc[-1]
    price = float(last['Close'])
    vwap = float(last['VWAP']) if not np.isnan(last['VWAP']) else price
    upper = float(last['Band_Upper']) if not np.isnan(last['Band_Upper']) else price
    lower = float(last['Band_Lower']) if not np.isnan(last['Band_Lower']) else price
    mfi = float(last['MFI_14']) if not np.isnan(last['MFI_14']) else 50.0
    atr = float(last['ATR']) if not np.isnan(last['ATR']) else 0.0

    # --- Composite scoring ---
    # Mean-reversion lean around the VWAP bands: being LOW in the band /
    # oversold on MFI is bullish, and vice versa.
    band_half = max((upper - lower) / 2.0, price * 0.0015)
    band_component = float(np.clip(-(price - vwap) / band_half, -1.3, 1.3))
    mfi_component = float(np.clip((50.0 - mfi) / 30.0, -1.3, 1.3))

    score = composite_score(band_component, mfi_component)
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

    direction_final = direction_from_action(action)
    risk_block = risk_management_block(price, atr, direction_final, atr_mult_stop=1.2, rr_ratio=1.6)

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
            "mfi_14": round(mfi, 2),
            "atr_14": round(atr, 2),
            "composite_score": round(score, 3)
        }
    }