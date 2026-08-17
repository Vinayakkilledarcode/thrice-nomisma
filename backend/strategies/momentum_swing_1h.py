# strategies/momentum_swing_1h.py
import pandas as pd
import numpy as np
from typing import Dict, Any

from strategies.strategy_utils import (
    compute_atr,
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
def evaluate_momentum_swing_1h(df: pd.DataFrame) -> Dict[str, Any]:
    """
    1-Hour (Momentum Swing) - EMA Cross + Directional Trend Index (ADX).
    Decision model: composite score across the EMA8/EMA21 trend spread and
    ADX trend strength. Previously this only fired on the exact bar an EMA
    cross happened AND only when ADX was already above 25 -- meaning it sat
    on HOLD the entire rest of the time even in an obvious, ongoing trend.
    Now it reads the *current* trend state every bar and scales confidence
    with how strong that trend is, so it stays decisively positioned for as
    long as the trend actually holds.
    """
    if df.empty or len(df) < 28:
        return {"action": "HOLD", "confidence": 0.0, "signal_quality": "NEUTRAL", "indicators": {}}

    df = df.copy()
    df.columns = [c.title() for c in df.columns]

    close = df['Close']
    high = df['High']
    low = df['Low']
    vol = df.get('Volume', pd.Series(1, index=df.index)).fillna(1).replace(0, 1)

    df['EMA8'] = close.ewm(span=8, adjust=False).mean()
    df['EMA21'] = close.ewm(span=21, adjust=False).mean()

    # Session VWAP fallback
    tp = (high + low + close) / 3.0
    cum_vol = vol.cumsum().replace(0, np.nan)
    df['VWAP'] = (tp * vol).cumsum() / cum_vol
    df['VWAP'] = df['VWAP'].fillna(tp.ewm(span=20).mean())

    df['UpMove'] = high.diff()
    df['DownMove'] = -low.diff()
    df['PlusDM'] = np.where((df['UpMove'] > df['DownMove']) & (df['UpMove'] > 0), df['UpMove'], 0.0)
    df['MinusDM'] = np.where((df['DownMove'] > df['UpMove']) & (df['DownMove'] > 0), df['DownMove'], 0.0)

    tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
    atr_series = tr.rolling(window=14).mean().replace(0, np.nan)

    df['PlusDI'] = 100 * (pd.Series(df['PlusDM'], index=df.index).rolling(window=14).mean() / atr_series)
    df['MinusDI'] = 100 * (pd.Series(df['MinusDM'], index=df.index).rolling(window=14).mean() / atr_series)
    dx = 100 * (abs(df['PlusDI'] - df['MinusDI']) / (df['PlusDI'] + df['MinusDI']).replace(0, np.nan))
    df['ADX'] = dx.rolling(window=14).mean()
    df['ATR'] = atr_series

    last = df.iloc[-1]

    price = float(last['Close'])
    ema8 = float(last['EMA8']) if not np.isnan(last['EMA8']) else price
    ema21 = float(last['EMA21']) if not np.isnan(last['EMA21']) else price
    adx = float(last['ADX']) if not np.isnan(last['ADX']) else 0.0
    vwap = float(last['VWAP']) if not np.isnan(last['VWAP']) else price
    atr = float(last['ATR']) if not np.isnan(last['ATR']) else 0.0

    # --- Composite scoring ---
    ema_spread_component = float(np.clip((ema8 - ema21) / max(atr, price * 0.001), -1.3, 1.3))
    # ADX below ~15 is a genuinely rangebound tape and should mute conviction;
    # above ~30 is a strong trend and should sharpen it -- but it no longer
    # gates the signal to a binary "act only above 25".
    adx_multiplier = float(np.clip((adx - 12.0) / 20.0, 0.15, 1.4))
    trend_component = float(np.clip(ema_spread_component * adx_multiplier, -1.3, 1.3))
    price_vs_vwap_component = float(np.clip((price - vwap) / max(atr, price * 0.001), -1.0, 1.0)) * 0.4

    score = composite_score(trend_component, price_vs_vwap_component)
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

    trade_direction = direction_from_action(action)
    risk_block = risk_management_block(price, atr, trade_direction, atr_mult_stop=1.8, rr_ratio=2.2)

    return {
        "action": action,
        "confidence": round(confidence, 2),
        "signal_quality": signal_quality_label(confidence),
        "risk_management": risk_block,
        "indicators": {
            "last_price": price,
            "vwap": round(vwap, 2),
            "upper_band": round(ema8, 2),
            "lower_band": round(ema21, 2),
            "ma20": round(ema21, 2),
            "adx_14": round(adx, 2),
            "atr_14": round(atr, 2),
            "composite_score": round(score, 3)
        }
    }