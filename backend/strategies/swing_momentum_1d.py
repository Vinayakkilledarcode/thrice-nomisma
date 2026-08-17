# strategies/swing_momentum_1d.py
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
def evaluate_swing_momentum_1d(df: pd.DataFrame, sentiment_score: float = 0.0) -> Dict[str, Any]:
    """
    Daily (Swing Momentum) - EMA Crossover + MACD + Elder's Force Index.
    Decision model: composite score across EMA8/21-equivalent trend spread
    (via MACD/EMA relationship), MACD-vs-signal spread, and Elder's Force
    Index, with a small sentiment tilt. Replaces the old rigid AND-gate
    (EMA fast>slow AND MACD>signal AND EFI>0, all simultaneously, to say
    anything but HOLD) which required three independent momentum reads to
    agree on the same bar -- on the daily chart, the most-watched timeframe,
    that combination is often broken by one lagging component even during
    an otherwise clear trend, which is why this strategy defaulted to HOLD
    so often.
    """
    if df.empty or len(df) < 26:
        return {"action": "HOLD", "confidence": 0.0, "signal_quality": "NEUTRAL", "indicators": {}}

    df = df.copy()
    df.columns = [c.title() for c in df.columns]

    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df.get('Volume', pd.Series(1, index=df.index)).fillna(1).replace(0, 1)

    df['EMA_Fast'] = close.ewm(span=12, adjust=False).mean()
    df['EMA_Slow'] = close.ewm(span=26, adjust=False).mean()
    df['MACD'] = df['EMA_Fast'] - df['EMA_Slow']
    df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['Histogram'] = df['MACD'] - df['Signal_Line']

    df['VWAP'] = session_vwap(df)
    df['ATR'] = compute_atr(df, period=14)

    df['Raw_Force'] = close.diff(1) * volume
    df['EFI'] = df['Raw_Force'].ewm(span=13, adjust=False).mean()
    # Normalize EFI onto a stable, price-scale-independent range using its
    # own rolling volatility, so this component behaves consistently across
    # very differently-priced symbols (a ₹50 stock vs a ₹5,000 stock).
    efi_scale = df['Raw_Force'].abs().rolling(window=20).mean().replace(0, np.nan)

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last

    price = float(last['Close'])
    fast = float(last['EMA_Fast']) if not np.isnan(last['EMA_Fast']) else price
    slow = float(last['EMA_Slow']) if not np.isnan(last['EMA_Slow']) else price
    macd = float(last['MACD']) if not np.isnan(last['MACD']) else 0.0
    signal = float(last['Signal_Line']) if not np.isnan(last['Signal_Line']) else 0.0
    hist = float(last['Histogram']) if not np.isnan(last['Histogram']) else 0.0
    prev_hist = float(prev['Histogram']) if not np.isnan(prev['Histogram']) else 0.0
    efi = float(last['EFI']) if not np.isnan(last['EFI']) else 0.0
    efi_scale_last = float(efi_scale.iloc[-1]) if not np.isnan(efi_scale.iloc[-1]) else 0.0
    efi_norm = efi_scale_last if efi_scale_last > 0 else max(abs(efi), 1.0)
    vwap = float(last['VWAP']) if not np.isnan(last['VWAP']) else price
    atr = float(last['ATR']) if not np.isnan(last['ATR']) else 0.0

    histogram_expanding = abs(hist) > abs(prev_hist)

    # --- Composite scoring ---
    trend_component = float(np.clip((fast - slow) / max(atr, price * 0.001), -1.3, 1.3))
    macd_spread_component = float(np.clip((macd - signal) / max(abs(price) * 0.003, 1e-6), -1.2, 1.2))
    efi_component = float(np.clip(efi / efi_norm, -1.2, 1.2))
    sentiment_component = float(np.clip(sentiment_score, -1.0, 1.0)) * 0.3

    score = composite_score(trend_component, macd_spread_component, efi_component, sentiment_component)
    direction = decide_direction(score)

    if direction == "LONG":
        action = "STRONG_BUY_SWING" if (histogram_expanding and hist > 0) else "BUY_SWING"
        confidence = score_to_confidence(score)
    elif direction == "SHORT":
        action = "STRONG_SELL_SWING" if (histogram_expanding and hist < 0) else "SELL_SWING"
        confidence = score_to_confidence(score)
    else:
        action = "HOLD"
        confidence = hold_confidence(score)

    direction_final = direction_from_action(action)
    rr_ratio = 3.0 if "STRONG" in action else 2.5
    risk_block = risk_management_block(price, atr, direction_final, atr_mult_stop=2.5, rr_ratio=rr_ratio)

    return {
        "action": action,
        "confidence": round(confidence, 2),
        "signal_quality": signal_quality_label(confidence),
        "risk_management": risk_block,
        "indicators": {
            "last_price": price,
            "vwap": round(vwap, 2),
            "upper_band": round(fast, 2),
            "lower_band": round(slow, 2),
            "ma20": round(slow, 2),
            "macd": round(macd, 4),
            "signal_line": round(signal, 4),
            "histogram": round(hist, 4),
            "atr_14": round(atr, 2),
            "composite_score": round(score, 3)
        }
    }