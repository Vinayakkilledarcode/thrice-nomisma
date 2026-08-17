# strategies/long_term_value_1w.py
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
def evaluate_long_term_value_1w(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Weekly (Position / Long-Term Value) - Weekly EMA-200 + Weekly RSI-14 + Coppock Curve.
    Decision model: continuous composite conviction score across trend
    position (price vs EMA-200), RSI lean, and the Coppock long-term
    momentum curve. Replaces the old discrete RSI-band gate (e.g. "only
    ACCUMULATE if RSI < 45 AND above the 200-EMA", else silently fall
    through to a flat, low-information HOLD) with a continuous read, so a
    position call is made whenever the weight of evidence genuinely leans a
    direction. HOLD is now reserved for a true toss-up: price hugging the
    EMA-200 with RSI near 50 and no momentum curve conviction either way.
    """
    if df.empty or len(df) < 14:
        return {"action": "HOLD", "allocation_ratio": 0.0, "signal_quality": "NEUTRAL", "indicators": {}}

    df = df.copy()
    df.columns = [c.title() for c in df.columns]

    close = df['Close']
    high = df.get('High', close)
    low = df.get('Low', close)
    volume = df.get('Volume', pd.Series(1, index=df.index)).fillna(1).replace(0, 1)

    span_val = min(200, len(df))
    df['EMA_200'] = close.ewm(span=span_val, adjust=False).mean()

    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI_14'] = 100 - (100 / (1 + rs))

    # Coppock Curve
    df['ROC_14'] = ((close - close.shift(14)) / close.shift(14).replace(0, np.nan)) * 100
    df['ROC_11'] = ((close - close.shift(11)) / close.shift(11).replace(0, np.nan)) * 100
    roc_sum = df['ROC_14'] + df['ROC_11']
    weights = np.arange(1, 11)
    df['Coppock'] = roc_sum.rolling(window=10).apply(lambda x: np.dot(x, weights) / weights.sum() if len(x) == 10 else 0.0, raw=True)

    df['VWAP'] = session_vwap(df)
    df['ATR'] = compute_atr(df, period=14)

    last = df.iloc[-1]
    price = float(last['Close'])
    ema200 = float(last['EMA_200']) if not np.isnan(last['EMA_200']) else price
    rsi = float(last['RSI_14']) if not np.isnan(last['RSI_14']) else 50.0
    coppock = float(last['Coppock']) if not np.isnan(last['Coppock']) else 0.0
    vwap = float(last['VWAP']) if not np.isnan(last['VWAP']) else price
    atr = float(last['ATR']) if not np.isnan(last['ATR']) else 0.0

    above_ema = price > ema200
    ema_distance_pct = ((price - ema200) / ema200) * 100.0

    # --- Composite scoring ---
    # Trend: distance from EMA-200, scaled by ATR so it's volatility-aware.
    trend_component = float(np.clip((price - ema200) / max(atr * 2.0, price * 0.01), -1.3, 1.3))
    # RSI: a genuine value/accumulation lean -- being oversold is bullish
    # here (buy the dip), being overbought is a distribution/trim signal.
    rsi_component = float(np.clip((50.0 - rsi) / 30.0, -1.4, 1.4))
    # Coppock: long-term momentum curve, already roughly on a workable scale.
    coppock_component = float(np.clip(coppock / 20.0, -1.2, 1.2))

    score = composite_score(trend_component, rsi_component, coppock_component)
    direction = decide_direction(score)

    if direction == "LONG":
        action = "ACCUMULATE_INVESTMENT"
        conviction = score_to_confidence(score)
        allocation_ratio = float(np.clip(0.30 + (conviction - 0.51) * 1.5, 0.30, 1.0))
    elif direction == "SHORT":
        conviction = score_to_confidence(score)
        if rsi < 38.0 and not above_ema:
            # Deep pullback within a broken trend: still a bearish read, but
            # flag it as a speculative watch rather than an outright trim
            # since the position may already be washed out.
            action = "OVERSOLD_WATCH"
            allocation_ratio = float(np.clip(0.40 - (conviction - 0.51) * 0.6, 0.10, 0.40))
        else:
            action = "REDUCE_PORTFOLIO"
            allocation_ratio = float(np.clip(0.20 - (conviction - 0.51) * 0.4, 0.0, 0.20))
    else:
        action = "HOLD"
        allocation_ratio = hold_confidence(score, ceiling=0.55) + 0.35  # maintain a ~65-90% baseline weighting

    allocation_ratio = float(np.clip(allocation_ratio, 0.0, 1.0))

    # Position-level risk framing: a wide, ATR-scaled downside protection
    # band below EMA-200 rather than a tight intraday stop -- appropriate
    # for a multi-month holding horizon.
    trade_direction = direction_from_action(action)
    if trade_direction == "LONG" and atr > 0:
        downside_protection_level = round(float(ema200 - (atr * 3.0)), 2)
    else:
        downside_protection_level = None

    return {
        "action": action,
        "allocation_ratio": round(allocation_ratio, 2),
        "signal_quality": signal_quality_label(allocation_ratio),
        "risk_management": {
            "downside_protection_level": downside_protection_level,
            "time_horizon": "weeks_to_months",
            "note": "Position sizing here reflects conviction, not tight stop-loss risk; use downside_protection_level as a thesis-invalidation check-in point, not an automated stop."
        },
        "indicators": {
            "last_price": price,
            "vwap": round(vwap, 2),
            "weekly_ema_200": round(ema200, 2),
            "weekly_rsi_14": round(rsi, 2),
            "above_ema_200": above_ema,
            "ema_distance_pct": round(ema_distance_pct, 2),
            "coppock": round(coppock, 2),
            "atr_14": round(atr, 2),
            "composite_score": round(score, 3)
        }
    }