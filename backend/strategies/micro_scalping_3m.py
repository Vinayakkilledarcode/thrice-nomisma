# strategies/micro_scalping_3m.py
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


def _calculate_hma(series: pd.Series, period: int) -> pd.Series:
    """Calculates Hull Moving Average (HMA) to minimize signal lag."""
    def wma(s, w):
        weights = np.arange(1, w + 1)
        return s.rolling(w).apply(lambda x: np.dot(x, weights) / weights.sum() if len(x) == w else np.nan, raw=True)

    half_len = int(period / 2)
    sqrt_len = int(np.sqrt(period))
    wma_half = wma(series, half_len)
    wma_full = wma(series, period)
    raw_hma = 2 * wma_half - wma_full
    return wma(raw_hma, sqrt_len)


@safe_evaluate(default_key="confidence")
def evaluate_micro_scalping_3m(df: pd.DataFrame) -> Dict[str, Any]:
    """
    3-Minute (Micro Scalping) - Double-Smoothed Stochastic %K + Zero-Lag Hull Crossover.
    Decision model: composite score across HMA trend spread, stochastic
    extremity and short-term momentum. No longer requires an exact-bar
    crossover event plus an extreme stochastic reading plus momentum
    agreement all on the same candle -- it acts on the prevailing lean.
    """
    if df.empty or len(df) < 21:
        return {"action": "HOLD", "confidence": 0.0, "signal_quality": "NEUTRAL", "indicators": {}}

    df = df.copy()
    df.columns = [c.title() for c in df.columns]

    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df.get('Volume', pd.Series(1, index=df.index)).fillna(1).replace(0, 1)

    df['HMA_Fast'] = _calculate_hma(close, 9)
    df['HMA_Slow'] = _calculate_hma(close, 21)

    lowest_low = low.rolling(window=5).min()
    highest_high = high.rolling(window=5).max()
    df['Raw_K'] = ((close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)) * 100
    df['Stoch_K'] = df['Raw_K'].ewm(span=3, adjust=False).mean()
    df['Stoch_D'] = df['Stoch_K'].ewm(span=3, adjust=False).mean()

    df['VWAP'] = session_vwap(df)
    df['ATR'] = compute_atr(df, period=10)

    df['Momentum'] = close.diff(3)

    last = df.iloc[-1]

    price = float(last['Close'])
    fast = float(last['HMA_Fast']) if not np.isnan(last['HMA_Fast']) else price
    slow = float(last['HMA_Slow']) if not np.isnan(last['HMA_Slow']) else price
    stoch_k = float(last['Stoch_K']) if not np.isnan(last['Stoch_K']) else 50.0
    stoch_d = float(last['Stoch_D']) if not np.isnan(last['Stoch_D']) else 50.0
    momentum = float(last['Momentum']) if not np.isnan(last['Momentum']) else 0.0
    vwap = float(last['VWAP']) if not np.isnan(last['VWAP']) else price
    atr = float(last['ATR']) if not np.isnan(last['ATR']) else 0.0

    # --- Composite scoring ---
    hma_spread_component = float(np.clip((fast - slow) / max(atr, price * 0.0008), -1.3, 1.3))
    # Stochastic is mean-reversion-flavored at this timeframe: low K favors longs.
    stoch_component = float(np.clip((50.0 - stoch_k) / 35.0, -1.2, 1.2))
    momentum_component = float(np.clip(momentum / max(atr * 0.6, price * 0.0006), -1.2, 1.2))

    score = composite_score(hma_spread_component, stoch_component, momentum_component)
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
    # Scalp horizon: tight stop, modest reward multiple -- these trades are
    # meant to be short-lived and high win-rate, not big-reward swings.
    risk_block = risk_management_block(price, atr, trade_direction, atr_mult_stop=1.0, rr_ratio=1.5)

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
            "stoch_k": round(stoch_k, 2),
            "stoch_d": round(stoch_d, 2),
            "atr_10": round(atr, 2),
            "composite_score": round(score, 3)
        }
    }