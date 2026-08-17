# strategies/multi_session_swing_4h.py
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
def evaluate_multi_session_swing_4h(df: pd.DataFrame) -> Dict[str, Any]:
    """
    4-Hour (Multi-Session Swing) - Ichimoku Cloud + Chaikin Money Flow.
    Decision model: composite score across cloud position, Tenkan/Kijun
    spread, and CMF. Replaces the old rigid gate (price above/below the
    cloud AND Tenkan above/below Kijun AND |CMF| > 0.08, all simultaneously)
    which required three independent conditions to line up on the same bar
    -- a combination the Ichimoku system was almost never satisfying at
    once, which is why this strategy sat on HOLD essentially permanently.
    It now weighs the evidence and commits whenever the net lean clears the
    shared decision threshold, consistent with every other strategy in the
    book.
    """
    if df.empty or len(df) < 52:
        return {"action": "HOLD", "confidence": 0.0, "signal_quality": "NEUTRAL", "indicators": {}}

    df = df.copy()
    df.columns = [c.title() for c in df.columns]

    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df.get('Volume', pd.Series(1, index=df.index)).fillna(1).replace(0, 1)

    nine_high = high.rolling(window=9).max()
    nine_low = low.rolling(window=9).min()
    df['Tenkan_Sen'] = (nine_high + nine_low) / 2.0

    twenty_six_high = high.rolling(window=26).max()
    twenty_six_low = low.rolling(window=26).min()
    df['Kijun_Sen'] = (twenty_six_high + twenty_six_low) / 2.0

    fifty_two_high = high.rolling(window=52).max()
    fifty_two_low = low.rolling(window=52).min()
    df['Senkou_Span_B'] = (fifty_two_high + fifty_two_low) / 2.0
    df['Senkou_Span_A'] = ((df['Tenkan_Sen'] + df['Kijun_Sen']) / 2.0).shift(26)

    df['Chikou_Span'] = close.shift(-26)

    mf_multiplier = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
    mf_volume = mf_multiplier * volume
    df['CMF'] = mf_volume.rolling(window=20).sum() / volume.rolling(window=20).sum().replace(0, np.nan)

    df['VWAP'] = session_vwap(df)
    df['ATR'] = compute_atr(df, period=14)

    last = df.iloc[-1]
    price = float(last['Close'])
    tenkan = float(last['Tenkan_Sen']) if not np.isnan(last['Tenkan_Sen']) else price
    kijun = float(last['Kijun_Sen']) if not np.isnan(last['Kijun_Sen']) else price
    span_a = float(last['Senkou_Span_A']) if not np.isnan(last['Senkou_Span_A']) else price
    span_b = float(last['Senkou_Span_B']) if not np.isnan(last['Senkou_Span_B']) else price
    cmf = float(last['CMF']) if not np.isnan(last['CMF']) else 0.0
    vwap = float(last['VWAP']) if not np.isnan(last['VWAP']) else price
    atr = float(last['ATR']) if not np.isnan(last['ATR']) else 0.0

    cloud_top = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)
    cloud_mid = (cloud_top + cloud_bottom) / 2.0
    cloud_half = max((cloud_top - cloud_bottom) / 2.0, atr * 0.5, price * 0.002)

    # --- Composite scoring ---
    # Cloud position: how far price sits above/below the cloud, scaled by
    # cloud thickness (a proxy for how decisive the breakout is).
    cloud_component = float(np.clip((price - cloud_mid) / cloud_half, -1.4, 1.4))
    # Tenkan/Kijun spread: short-term vs medium-term trend line separation.
    tenkan_kijun_component = float(np.clip((tenkan - kijun) / max(atr, price * 0.001), -1.2, 1.2))
    # Chaikin Money Flow: accumulation/distribution pressure.
    cmf_component = float(np.clip(cmf / 0.15, -1.2, 1.2)) * 0.6

    score = composite_score(cloud_component, tenkan_kijun_component, cmf_component)
    direction = decide_direction(score)

    if direction == "LONG":
        action = "BUY_SWING"
        confidence = score_to_confidence(score)
    elif direction == "SHORT":
        action = "SELL_SWING"
        confidence = score_to_confidence(score)
    else:
        action = "HOLD"
        confidence = hold_confidence(score)

    direction_final = direction_from_action(action)
    # Multi-day swing horizon: the widest ATR stop/target among the
    # intraday-and-up strategies, appropriate for a 4H cloud-based system.
    risk_block = risk_management_block(price, atr, direction_final, atr_mult_stop=2.2, rr_ratio=2.8)

    return {
        "action": action,
        "confidence": round(confidence, 2),
        "signal_quality": signal_quality_label(confidence),
        "risk_management": risk_block,
        "indicators": {
            "last_price": price,
            "vwap": round(vwap, 2),
            "upper_band": round(span_a, 2),
            "lower_band": round(span_b, 2),
            "ma20": round(kijun, 2),
            "cmf_20": round(cmf, 2),
            "atr_14": round(atr, 2),
            "composite_score": round(score, 3)
        }
    }