# strategies/strategy_utils.py
"""
Shared institutional-grade utilities used by every strategy module.

Centralizing this logic means:
  - VWAP is correctly session-anchored (resets each trading day) instead of
    accumulating from the first row of whatever slice of history was passed in.
  - Every strategy emits the same risk-management contract (stop_loss,
    take_profit, risk_reward_ratio, position_size_pct) so the frontend/Gemini
    layer can rely on a stable schema regardless of which strategy fired.
  - A single crash-safe decorator guarantees the API route never 500s because
    one symbol had a NaN-heavy or too-short OHLCV window.
  - A shared composite-scoring model so every strategy makes a genuine
    directional CALL (BUY/SHORT) whenever the weight of evidence leans one
    way, instead of defaulting to HOLD unless every single condition lines
    up perfectly. HOLD is reserved for when the evidence is genuinely mixed
    or absent -- not treated as the "safe default" -- and confidence now
    means what it says: a HOLD is never reported with a high confidence
    number, because a strategy that is 88% "confident" and still says HOLD
    is not actually confident about anything.
"""
import functools
from typing import Any, Dict, Optional, Sequence

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Core indicator helpers
# --------------------------------------------------------------------------- #
def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Standard Wilder-style True Range, simple-mean smoothed."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = np.maximum(
        high - low,
        np.maximum(abs(high - prev_close), abs(low - prev_close)),
    )
    return tr.rolling(window=period).mean()


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Volume-weighted average price, correctly reset at the start of each
    trading session when a DatetimeIndex is available (this is how VWAP is
    actually defined -- it is NOT a running average since the dawn of time).
    Falls back to a single cumulative VWAP for non-datetime-indexed frames
    (e.g. synthetic/backtest data) so nothing breaks upstream.
    """
    high, low, close = df["High"], df["Low"], df["Close"]
    volume = df.get("Volume", pd.Series(1, index=df.index)).fillna(1).replace(0, 1)
    tp = (high + low + close) / 3.0

    if isinstance(df.index, pd.DatetimeIndex):
        session_key = df.index.date
        tp_vol = tp * volume
        cum_tp_vol = tp_vol.groupby(session_key).cumsum()
        cum_vol = volume.groupby(session_key).cumsum().replace(0, np.nan)
        vwap = cum_tp_vol / cum_vol
    else:
        cum_vol = volume.cumsum().replace(0, np.nan)
        vwap = (tp * volume).cumsum() / cum_vol

    return vwap.fillna(tp.ewm(span=20, adjust=False).mean())


def adx_regime(df: pd.DataFrame, period: int = 14) -> float:
    """
    Lightweight ADX read used purely as a trend/range regime filter (not a
    trading signal on its own). Returns the latest ADX value, or 0.0 if it
    can't be computed on the available window.
    """
    try:
        high, low, close = df["High"], df["Low"], df["Close"]
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        prev_close = close.shift(1)
        tr = np.maximum(high - low, np.maximum(abs(high - prev_close), abs(low - prev_close)))
        atr = tr.rolling(window=period).mean().replace(0, np.nan)

        plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(window=period).mean() / atr)
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(window=period).mean() / atr)
        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan))
        adx = dx.rolling(window=period).mean().iloc[-1]
        return float(adx) if not np.isnan(adx) else 0.0
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# Composite decision scoring
#
# Every strategy reduces its indicator readings to a handful of "components",
# each normalized to roughly [-1, +1] (positive = bullish lean, negative =
# bearish lean). We average them into a single score and act decisively off
# it, rather than requiring every single component to agree in the same
# direction before doing anything. This is what a real discretionary trader
# does -- weighs the evidence and takes the higher-probability side -- rather
# than waiting for unanimous consensus that rarely arrives.
# --------------------------------------------------------------------------- #
DECISION_THRESHOLD = 0.06  # net score beyond which the strategy commits to a side
# Tuned deliberately low: HOLD should be the exception, not the default. A
# strategy only reports HOLD when its composite score falls inside this
# tiny dead-zone around zero -- i.e. the components are genuinely
# cancelling each other out (true toss-up), not merely "not unanimous".
# Any real net lean, however slight, now produces an actionable BUY/SHORT
# call. This trades off some whipsaw risk in choppy/rangebound conditions
# for far fewer "no signal" outputs -- worth knowing if you're backtesting
# win-rate/drawdown before running this live.


def composite_score(*components: Optional[float]) -> float:
    """Mean of the given components (each pre-clipped to [-1, 1] by the caller),
    ignoring any that are None/NaN. Returns 0.0 if nothing usable was passed."""
    vals = [float(c) for c in components if c is not None and not (isinstance(c, float) and np.isnan(c))]
    if not vals:
        return 0.0
    return float(np.clip(np.mean(vals), -1.0, 1.0))


def decide_direction(score: float, threshold: float = DECISION_THRESHOLD) -> str:
    """Maps a composite score to 'LONG', 'SHORT', or 'NONE'."""
    if score >= threshold:
        return "LONG"
    if score <= -threshold:
        return "SHORT"
    return "NONE"


def score_to_confidence(score: float, min_conf: float = 0.52, max_conf: float = 0.96) -> float:
    """
    Confidence for an actual directional call, scaled by how strong the
    evidence was (|score| in [threshold, 1.0] maps to [min_conf, max_conf]).
    """
    magnitude = float(np.clip(abs(score), DECISION_THRESHOLD, 1.0))
    span = (magnitude - DECISION_THRESHOLD) / max(1e-6, (1.0 - DECISION_THRESHOLD))
    return float(np.clip(min_conf + span * (max_conf - min_conf), min_conf, max_conf))


def hold_confidence(score: float, ceiling: float = 0.60) -> float:
    """
    Confidence for a genuine HOLD. A HOLD means the evidence is mixed or
    thin, so this is deliberately capped well below the confidence band used
    for actual BUY/SHORT calls -- a strategy should never report itself as
    highly 'confident' while simultaneously refusing to take a side.
    """
    magnitude = float(np.clip(abs(score) / max(DECISION_THRESHOLD, 1e-6), 0.0, 1.0))
    return float(np.clip(0.35 + magnitude * (ceiling - 0.35), 0.35, ceiling))


# --------------------------------------------------------------------------- #
# Risk management
# --------------------------------------------------------------------------- #
def risk_management_block(
    price: float,
    atr: float,
    direction: str,
    atr_mult_stop: float = 1.5,
    rr_ratio: float = 2.0,
    account_risk_pct: float = 1.0,
) -> Dict[str, Any]:
    """
    Builds an ATR-scaled stop-loss / take-profit / position-size block.

    direction: "LONG", "SHORT", or "NONE"
    atr_mult_stop: how many ATRs away the stop sits (tighter for scalps,
        wider for swing/position strategies)
    rr_ratio: reward multiple relative to risk (take-profit distance =
        stop distance * rr_ratio)
    account_risk_pct: % of account equity risked per trade, used only to
        derive a *suggested* position_size_pct (this is guidance, not
        execution sizing -- the caller's own risk desk / RMS should have
        final say)
    """
    if direction == "NONE" or atr <= 0 or price <= 0 or np.isnan(atr):
        return {
            "stop_loss": None,
            "take_profit": None,
            "risk_reward_ratio": None,
            "position_size_pct": 0.0,
            "risk_per_share": None,
        }

    stop_distance = atr * atr_mult_stop
    if direction == "LONG":
        stop_loss = price - stop_distance
        take_profit = price + stop_distance * rr_ratio
    else:  # SHORT
        stop_loss = price + stop_distance
        take_profit = price - stop_distance * rr_ratio

    risk_per_share = abs(price - stop_loss)
    position_size_pct = (
        float(np.clip((account_risk_pct / 100.0) * (price / risk_per_share) * 100.0, 0.0, 100.0))
        if risk_per_share > 0
        else 0.0
    )

    return {
        "stop_loss": round(float(stop_loss), 2),
        "take_profit": round(float(take_profit), 2),
        "risk_reward_ratio": rr_ratio,
        "position_size_pct": round(position_size_pct, 2),
        "risk_per_share": round(float(risk_per_share), 2),
    }


def direction_from_action(action: str) -> str:
    """Maps any of this codebase's action strings to LONG / SHORT / NONE."""
    a = action.upper()
    if "BUY" in a or "ACCUMULATE" in a:
        return "LONG"
    if "SHORT" in a or "SELL" in a or "REDUCE" in a:
        return "SHORT"
    return "NONE"


def signal_quality_label(confidence: float) -> str:
    if confidence >= 0.80:
        return "STRONG"
    if confidence >= 0.65:
        return "MODERATE"
    if confidence >= 0.50:
        return "WEAK"
    return "NEUTRAL"


# --------------------------------------------------------------------------- #
# Crash safety
# --------------------------------------------------------------------------- #
def safe_evaluate(default_key: str = "confidence"):
    """
    Decorator factory. Wraps a strategy's evaluate_* function so that any
    exception (bad data, unexpected dtypes, empty slices, etc.) degrades to a
    safe HOLD payload instead of raising and taking down the /strategy route.

    default_key: "confidence" for intraday/swing strategies, or
        "allocation_ratio" for the long-term investment strategies, so the
        fallback payload matches each strategy's real schema.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                payload = {
                    "action": "HOLD",
                    default_key: 0.0,
                    "signal_quality": "NEUTRAL",
                    "error": f"{type(exc).__name__}: {exc}",
                    "indicators": {},
                }
                return payload
        return wrapper
    return decorator