# strategies/scalping_hft_1m.py
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


def _approximate_hurst_exponent(series: pd.Series, max_lag: int = 10) -> float:
    """Approximates the Hurst Exponent to identify mean-reverting (H < 0.5) or trending (H > 0.5) states."""
    if len(series) < max_lag * 2:
        return 0.5
    try:
        lags = range(2, max_lag)
        tau = [np.sqrt(np.std(np.subtract(series[lag:].values, series[:-lag].values))) for lag in lags]
        reg = np.polyfit(np.log(lags), np.log(tau), 1)
        return float(reg[0] * 2.0)
    except Exception:
        return 0.5


@safe_evaluate(default_key="confidence")
def evaluate_scalping_hft_1m(df: pd.DataFrame) -> Dict[str, Any]:
    """
    1-Minute (Scalping / HFT) - Adaptive Volatility bands + Hurst Exponent Regime Filter.
    Decision model: composite score across band position and RSI-5 extremity
    (a mean-reversion lean at this horizon), scaled by how strongly the Hurst
    exponent indicates a genuinely mean-reverting regime and dampened during
    a volatility squeeze (pre-breakout indecision). This replaces the old
    rigid AND-gate (not in_squeeze AND hurst < 0.45 AND price beyond the BB
    band AND RSI past a hard 22/78 extreme, all simultaneously) which almost
    never fired -- it required four independent conditions to align on the
    exact same 1-minute bar, so this strategy sat on HOLD nearly all the
    time even during clear, tradeable moves.
    """
    if df.empty or len(df) < 20:
        return {"action": "HOLD", "confidence": 0.0, "signal_quality": "NEUTRAL", "indicators": {}}

    df = df.copy()
    df.columns = [c.title() for c in df.columns]

    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df.get('Volume', pd.Series(1, index=df.index)).fillna(1).replace(0, 1)

    df['Log_Ret'] = np.log(close / close.shift(1))
    df['Rolling_Vol'] = df['Log_Ret'].rolling(window=10).std()

    df['MA20'] = close.rolling(window=20).mean()
    df['BB_Upper'] = df['MA20'] + (close * df['Rolling_Vol'].fillna(0.02) * 1.8)
    df['BB_Lower'] = df['MA20'] - (close * df['Rolling_Vol'].fillna(0.02) * 1.8)

    df['ATR10'] = compute_atr(df, period=10)
    df['KC_Upper'] = df['MA20'] + (df['ATR10'] * 1.5)
    df['KC_Lower'] = df['MA20'] - (df['ATR10'] * 1.5)

    df['VWAP'] = session_vwap(df)

    hurst = _approximate_hurst_exponent(close.tail(20))

    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=5).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=5).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI_5'] = 100 - (100 / (1 + rs))

    last = df.iloc[-1]
    price = float(last['Close'])
    ma20 = float(last['MA20']) if not np.isnan(last['MA20']) else price
    bb_up = float(last['BB_Upper']) if not np.isnan(last['BB_Upper']) else price
    bb_lo = float(last['BB_Lower']) if not np.isnan(last['BB_Lower']) else price
    kc_up = float(last['KC_Upper']) if not np.isnan(last['KC_Upper']) else price
    kc_lo = float(last['KC_Lower']) if not np.isnan(last['KC_Lower']) else price
    rsi = float(last['RSI_5']) if not np.isnan(last['RSI_5']) else 50.0
    vwap = float(last['VWAP']) if not np.isnan(last['VWAP']) else price
    atr = float(last['ATR10']) if not np.isnan(last['ATR10']) else 0.0

    in_squeeze = (bb_up < kc_up) and (bb_lo > kc_lo)

    # --- Composite scoring ---
    # Mean-reversion lean at this horizon: being LOW in the band / oversold
    # on RSI-5 is bullish, and vice versa.
    band_half = max((bb_up - bb_lo) / 2.0, price * 0.0008)
    band_component = float(np.clip(-(price - ma20) / band_half, -1.3, 1.3))
    rsi_component = float(np.clip((50.0 - rsi) / 28.0, -1.3, 1.3))

    # Hurst < 0.5 means the tape is genuinely mean-reverting -- sharpen
    # conviction. Hurst > 0.5 means it's trending -- a pure mean-reversion
    # read is less trustworthy there, so dampen (but don't zero it out; a
    # short-lived pullback inside a trend is still tradeable at this horizon).
    regime_multiplier = float(np.clip(1.6 - (hurst * 1.6), 0.35, 1.5))
    # A squeeze means the tape is coiled and indecisive -- lower conviction
    # until it actually breaks, rather than gating the signal off entirely.
    squeeze_multiplier = 0.55 if in_squeeze else 1.0

    score = composite_score(band_component, rsi_component)
    score = float(np.clip(score * regime_multiplier * squeeze_multiplier, -1.0, 1.0))
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
    # Tightest horizon in the book: sub-1x ATR stop, quick 1.3x reward target.
    risk_block = risk_management_block(price, atr, direction_final, atr_mult_stop=0.8, rr_ratio=1.3)

    return {
        "action": action,
        "confidence": round(confidence, 2),
        "signal_quality": signal_quality_label(confidence),
        "risk_management": risk_block,
        "indicators": {
            "last_price": price,
            "vwap": round(vwap, 2),
            "upper_band": round(bb_up, 2),
            "lower_band": round(bb_lo, 2),
            "ma20": round(ma20, 2),
            "rsi_5": round(rsi, 2),
            "hurst_exponent": round(hurst, 3),
            "in_squeeze": bool(in_squeeze),
            "atr_10": round(atr, 2),
            "composite_score": round(score, 3)
        }
    }