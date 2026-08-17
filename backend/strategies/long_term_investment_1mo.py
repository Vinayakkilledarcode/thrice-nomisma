# strategies/long_term_investment_1mo.py
import pandas as pd
import numpy as np
from typing import Dict, Any

from strategies.strategy_utils import (
    compute_atr,
    session_vwap,
    direction_from_action,
    signal_quality_label,
    safe_evaluate,
    composite_score,
    decide_direction,
    score_to_confidence,
    hold_confidence,
)


@safe_evaluate(default_key="allocation_ratio")
def evaluate_long_term_investment_1mo(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Monthly (Long-Term Investment) - Monthly EMA-50 + Monthly RSI-14 + Klinger Volume Oscillator.
    Decision model: continuous composite conviction score across trend
    position (price vs EMA-50), RSI lean, and the Klinger Volume Oscillator.
    Replaces the old discrete RSI-band gate with a continuous read, so a
    portfolio-construction call is made whenever the weight of evidence
    genuinely leans a direction. HOLD is reserved for a true toss-up: price
    hugging the EMA-50 with RSI near 50 and no volume-flow conviction
    either way.
    """
    if df.empty or len(df) < 14:
        return {"action": "HOLD", "allocation_ratio": 0.0, "signal_quality": "NEUTRAL", "indicators": {}}

    df = df.copy()
    df.columns = [c.title() for c in df.columns]

    close = df['Close']
    high = df.get('High', close)
    low = df.get('Low', close)
    volume = df.get('Volume', pd.Series(1, index=df.index)).fillna(1).replace(0, 1)

    span_val = min(50, len(df))
    df['EMA_50'] = close.ewm(span=span_val, adjust=False).mean()

    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI_14'] = 100 - (100 / (1 + rs))

    df['SV'] = np.where(close > close.shift(1), volume, -volume)
    df['KVO'] = df['SV'].ewm(span=34, adjust=False).mean() - df['SV'].ewm(span=55, adjust=False).mean()
    # Normalize KVO by its own rolling volume scale so this component is
    # comparable across symbols with very different average volumes.
    kvo_scale = volume.rolling(window=34).mean().replace(0, np.nan)

    df['VWAP'] = session_vwap(df)
    df['ATR'] = compute_atr(df, period=14)

    last = df.iloc[-1]
    price = float(last['Close'])
    ema50 = float(last['EMA_50']) if not np.isnan(last['EMA_50']) else price
    rsi = float(last['RSI_14']) if not np.isnan(last['RSI_14']) else 50.0
    kvo = float(last['KVO']) if not np.isnan(last['KVO']) else 0.0
    kvo_scale_last = float(kvo_scale.iloc[-1]) if not np.isnan(kvo_scale.iloc[-1]) and kvo_scale.iloc[-1] > 0 else max(abs(kvo), 1.0)
    vwap = float(last['VWAP']) if not np.isnan(last['VWAP']) else price
    atr = float(last['ATR']) if not np.isnan(last['ATR']) else 0.0

    above_ema = price > ema50
    ema_distance_pct = ((price - ema50) / ema50) * 100.0

    # --- Composite scoring ---
    trend_component = float(np.clip((price - ema50) / max(atr * 2.0, price * 0.01), -1.3, 1.3))
    rsi_component = float(np.clip((50.0 - rsi) / 30.0, -1.4, 1.4))
    kvo_component = float(np.clip(kvo / kvo_scale_last, -1.2, 1.2))

    score = composite_score(trend_component, rsi_component, kvo_component)
    direction = decide_direction(score)

    if direction == "LONG":
        action = "ACCUMULATE_INVESTMENT"
        conviction = score_to_confidence(score)
        allocation_ratio = float(np.clip(0.30 + (conviction - 0.51) * 1.5, 0.30, 1.0))
    elif direction == "SHORT":
        conviction = score_to_confidence(score)
        if rsi < 35.0 and not above_ema:
            action = "OVERSOLD_WATCH"
            allocation_ratio = float(np.clip(0.45 - (conviction - 0.51) * 0.6, 0.10, 0.45))
        else:
            action = "REDUCE_PORTFOLIO"
            allocation_ratio = float(np.clip(0.20 - (conviction - 0.51) * 0.4, 0.0, 0.20))
    else:
        action = "HOLD"
        allocation_ratio = hold_confidence(score, ceiling=0.55) + 0.35  # maintain a ~65-90% baseline weighting

    allocation_ratio = float(np.clip(allocation_ratio, 0.0, 1.0))

    trade_direction = direction_from_action(action)
    if trade_direction == "LONG" and atr > 0:
        downside_protection_level = round(float(ema50 - (atr * 3.0)), 2)
    else:
        downside_protection_level = None

    return {
        "action": action,
        "allocation_ratio": round(allocation_ratio, 2),
        "signal_quality": signal_quality_label(allocation_ratio),
        "risk_management": {
            "downside_protection_level": downside_protection_level,
            "time_horizon": "months",
            "note": "Position sizing here reflects conviction, not tight stop-loss risk; use downside_protection_level as a thesis-invalidation check-in point, not an automated stop."
        },
        "indicators": {
            "last_price": price,
            "vwap": round(vwap, 2),
            "weekly_ema_200": round(ema50, 2),
            "weekly_rsi_14": round(rsi, 2),
            "above_ema_200": above_ema,
            "ema_distance_pct": round(ema_distance_pct, 2),
            "kvo": round(kvo, 2),
            "atr_14": round(atr, 2),
            "composite_score": round(score, 3)
        }
    }