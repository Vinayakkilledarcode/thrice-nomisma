# services/signal_arbitration.py
#
# The piece that was missing: strategy.py's mean-reversion confidence and
# the Signal Intelligence panel's regime/conflict reads were computed
# independently and never talked to each other. A stock could be at
# composite Trend = +1.00 (maximal bullish) while the 15m mean-reversion
# model fired SELL at 88% confidence purely off "price is ₹3 above the BB
# upper band" -- correct in isolation, misleading shown alone.
#
# This module is the arbitration layer described in that conversation:
# it takes (a) the strategy engine's raw action/confidence and (b) the
# same composite category-plane analysis the plane-overlap engine and
# Signal Intelligence panel already compute, and produces ONE
# regime-adjusted confidence with the reasoning attached -- instead of two
# numbers a user has to reconcile by hand.
#
# Nothing here does I/O. The caller (routers/stocks.py's
# active_strategy_signal) is responsible for fetching the indicator sweep
# and building the CompositeAnalysis; this module is pure computation, so
# it's trivially unit-testable and can't accidentally diverge from the
# regime logic depending on which route calls it.
#
# detect_market_regime() below is a deliberate line-for-line port of
# analysisEngine.ts's detectMarketRegime() (same thresholds, same
# fallthrough order: BREAKOUT -> TRENDING -> CONFLICTED -> VOLATILE ->
# RANGING) so the backend arbitration and the frontend Signal Intelligence
# panel can never disagree about which regime a symbol is in. If you tune
# one, tune the other.
from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .plane_overlap_engine import CategoryPlaneStats, CompositeAnalysis

# Category labels this module looks for, matched case-insensitively against
# CategoryPlaneStats.label. Matching by label rather than category_index
# because category_index ordering comes from indicator_engine.get_categories()
# and isn't guaranteed to match the frontend's hardcoded 0..6 convention.
_TREND_LABELS = {"trend"}
_VOLATILITY_LABELS = {"volatility"}
_VOLUME_LABELS = {"volume", "volume/flow", "volume_flow"}
_PRICE_ACTION_LABELS = {"price action", "price_action"}


def _find_category(categories: List[CategoryPlaneStats], labels: set) -> Optional[CategoryPlaneStats]:
    for c in categories:
        if c.label.lower().strip() in labels:
            return c
    return None


@dataclass
class RegimeResult:
    regime: str          # TRENDING | RANGING | VOLATILE | BREAKOUT | CONFLICTED | LOW_LIQUIDITY_DATA
    confidence: float     # 0..1
    reasoning: str


def detect_market_regime(categories: List[CategoryPlaneStats]) -> RegimeResult:
    """Port of analysisEngine.ts's detectMarketRegime(). See module docstring."""
    trend = _find_category(categories, _TREND_LABELS)
    volatility = _find_category(categories, _VOLATILITY_LABELS)
    volume = _find_category(categories, _VOLUME_LABELS)
    price_action = _find_category(categories, _PRICE_ACTION_LABELS)

    have_evidence = any(c.n > 0 for c in categories)
    if not have_evidence:
        return RegimeResult("LOW_LIQUIDITY_DATA", 0.0, "No indicators returned evidence for this symbol/timeframe yet.")

    trend_strength = abs(trend.mean) if trend else 0.0
    vol_skew = volatility.mean if volatility else 0.0

    with_ev = [c for c in categories if c.n > 0]
    if with_ev:
        total_w = sum(c.weight for c in with_ev) or 1.0
        mean = sum(c.mean * c.weight for c in with_ev) / total_w
        variance = sum(c.weight * (c.mean - mean) ** 2 for c in with_ev) / total_w
        dispersion = math.sqrt(variance)
    else:
        dispersion = 0.0

    trend_sign = 1 if (trend and trend.mean > 0) else -1 if (trend and trend.mean < 0) else 0
    volume_confirms = bool(volume) and (volume.mean * trend_sign) > 0.05
    price_action_break = bool(price_action) and abs(price_action.mean) > 0.4

    if price_action_break and volume_confirms and vol_skew > 0.15:
        return RegimeResult(
            "BREAKOUT",
            min(1.0, (abs(price_action.mean) + abs(vol_skew)) / 2),
            f"Price Action ({price_action.mean:+.2f}) is moving sharply with Volume confirming and "
            f"Volatility expanding ({vol_skew:+.2f}) — consistent with a breakout regime.",
        )

    if trend_strength > 0.3 and dispersion < 0.35:
        return RegimeResult(
            "TRENDING",
            min(1.0, trend_strength),
            f"Trend plane reads {trend.mean:+.2f} with low cross-category dispersion ({dispersion:.2f}) "
            f"— the other planes are largely rowing in the same direction.",
        )

    if dispersion > 0.5:
        return RegimeResult(
            "CONFLICTED",
            min(1.0, dispersion),
            f"Category planes disagree heavily (dispersion {dispersion:.2f}) with no dominant trend read "
            f"— treat any single-category signal with caution here.",
        )

    if abs(vol_skew) > 0.3 and trend_strength < 0.25:
        return RegimeResult(
            "VOLATILE",
            min(1.0, abs(vol_skew)),
            f"Volatility plane reads {vol_skew:+.2f} while Trend stays flat ({trend_strength:.2f}) "
            f"— price is moving without a clear directional bias.",
        )

    trend_display = trend.mean if trend else 0.0

    # RANGING is a fallthrough from two genuinely different situations that
    # were previously reported with the same "Trend plane is flat" wording:
    #   (a) trend_strength really is weak (< 0.3) -- flat is the accurate word.
    #   (b) trend_strength is actually >= 0.3 (a real directional read) but
    #       dispersion is too high (>= 0.35) for the other category planes to
    #       agree with it -- the trend isn't flat, it's just not corroborated.
    # Reporting (b) as "flat" is misleading in exactly the way this whole
    # arbitration layer exists to avoid, so the two are now told apart.
    if trend_strength >= 0.3:
        return RegimeResult(
            "RANGING",
            min(1.0, 1 - dispersion),
            f"Trend plane reads {trend_display:+.2f} — not flat — but cross-category dispersion "
            f"({dispersion:.2f}) is too high for the other planes to corroborate it, so this doesn't "
            f"qualify as a clean trending regime.",
        )

    return RegimeResult(
        "RANGING",
        min(1.0, 1 - trend_strength),
        f"Trend plane is flat ({trend_display:+.2f}) and volatility is contained "
        f"({vol_skew:+.2f}) — price looks range-bound rather than directional.",
    )


# ─── Strategy style classification ─────────────────────────────────────────
#
# Only the 15m model (strategies/intraday_mean_reversion_15m.py) is an
# explicit mean-reversion strategy today (Bollinger/VWAP band-fade). Every
# other timeframe file is directional (scalping, momentum, swing, EMA/MACD
# trend-following, long-term value) -- i.e. it wants the trend WITH it, not
# against it. Add an interval to MEAN_REVERSION_INTERVALS if a future
# strategy file is also band-fade/reversion-based.
MEAN_REVERSION_INTERVALS = {"15m"}


def classify_strategy_style(resolved_interval: str) -> str:
    return "mean_reversion" if resolved_interval in MEAN_REVERSION_INTERVALS else "trend_following"


# ─── Arbitration ────────────────────────────────────────────────────────────

@dataclass
class ArbitrationResult:
    strategy_style: str
    action: str
    raw_confidence: float
    adjusted_confidence: float
    discount_factor: float
    regime: str
    regime_confidence: float
    regime_reasoning: str
    trend_composite: float
    conflict_pct: int
    conflict_cause: str
    reasoning: str
    # --- Six-feature extension ---
    data_integrity: "DataIntegrityResult" = None
    band_walk: "BandWalkResult" = None
    timeframe_alignment: "TimeframeAlignmentResult" = None
    position_size_multiplier: float = 1.0

    def to_dict(self) -> dict:
        out = {
            "strategy_style": self.strategy_style,
            "action": self.action,
            "raw_confidence": round(self.raw_confidence, 4),
            "adjusted_confidence": round(self.adjusted_confidence, 4),
            "discount_factor": round(self.discount_factor, 4),
            "regime": self.regime,
            "regime_confidence": round(self.regime_confidence, 4),
            "regime_reasoning": self.regime_reasoning,
            "trend_composite": round(self.trend_composite, 4),
            "conflict_pct": self.conflict_pct,
            "conflict_cause": self.conflict_cause,
            "reasoning": self.reasoning,
            "position_size_multiplier": round(self.position_size_multiplier, 4),
            "data_integrity": self.data_integrity.to_dict() if self.data_integrity else None,
            "band_walk": self.band_walk.to_dict() if self.band_walk else None,
            "timeframe_alignment": self.timeframe_alignment.to_dict() if self.timeframe_alignment else None,
        }
        return out


# ─── Feature 1: Input-integrity gate ───────────────────────────────────────
#
# Nothing previously checked whether a strategy's own indicator inputs were
# actually populated before it emitted a confidence number -- the exact gap
# behind the EMA-null bug (₹— / ₹— rendered on a card while confidence still
# read 88%). This scans a Technicals-shaped dict (same field names as
# utils/types.ts's Technicals interface, sourced from market.get_technicals)
# for None/NaN among the fields a strategy is documented to depend on, and
# reports completeness rather than assuming it.
CORE_INDICATOR_KEYS = [
    "sma20", "sma50", "sma200", "ema9", "ema21", "rsi",
    "macd", "macdSignal", "macdHist", "bbUpper", "bbLower",
    "atr", "vwap", "obv", "stochK", "stochD",
]


@dataclass
class DataIntegrityResult:
    complete: bool
    completeness_ratio: float
    missing_keys: List[str]
    reasoning: str

    def to_dict(self) -> dict:
        return {
            "complete": self.complete,
            "completeness_ratio": round(self.completeness_ratio, 4),
            "missing_keys": self.missing_keys,
            "reasoning": self.reasoning,
        }


def check_data_integrity(indicators: Optional[dict], required_keys: Optional[List[str]] = None) -> DataIntegrityResult:
    """Scans `indicators` (expected to look like the Technicals interface)
    for None/NaN among `required_keys` (defaults to CORE_INDICATOR_KEYS).
    Does not fetch anything itself -- the caller supplies the dict, keeping
    this a pure function like the rest of the module."""
    keys = required_keys or CORE_INDICATOR_KEYS
    if not indicators:
        return DataIntegrityResult(
            complete=False, completeness_ratio=0.0, missing_keys=list(keys),
            reasoning="No indicator payload was supplied to the arbitration layer; integrity unverified.",
        )
    missing = []
    for k in keys:
        v = indicators.get(k, None)
        if v is None:
            missing.append(k)
            continue
        if isinstance(v, float) and math.isnan(v):
            missing.append(k)
    ratio = 1.0 - (len(missing) / len(keys)) if keys else 1.0
    complete = len(missing) == 0
    if complete:
        reasoning = "All core indicator inputs are populated."
    else:
        reasoning = (
            f"{len(missing)}/{len(keys)} core indicator input(s) missing or null "
            f"({', '.join(missing)}) — confidence built on incomplete inputs should not be trusted at face value."
        )
    return DataIntegrityResult(complete, ratio, missing, reasoning)


# ─── Feature 4: Band-walk detector ─────────────────────────────────────────
#
# The mean-reversion discount above uses a single-snapshot trend-opposition
# check. The specific failure mode originally diagnosed -- price riding
# outside the Bollinger band for N consecutive candles, not one overshoot --
# deserves its own check: the longer the walk, the less a single-candle
# reversion read should be trusted.

@dataclass
class BandWalkResult:
    walking: bool
    consecutive_candles: int
    direction: str  # "upper" | "lower" | "none"
    discount_factor: float
    reasoning: str

    def to_dict(self) -> dict:
        return {
            "walking": self.walking,
            "consecutive_candles": self.consecutive_candles,
            "direction": self.direction,
            "discount_factor": round(self.discount_factor, 4),
            "reasoning": self.reasoning,
        }


def compute_bollinger_bands_from_closes(closes: List[float], period: int = 20, num_std: float = 2.0):
    """Minimal, dependency-free rolling Bollinger Band computation so this
    module doesn't need to guess at indicator_engine's internal series
    format -- callers can hand it a plain list of closes (e.g. df["Close"]
    already fetched by the router) and get aligned upper/lower series back."""
    n = len(closes)
    upper: List[Optional[float]] = [None] * n
    lower: List[Optional[float]] = [None] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1: i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return upper, lower


def detect_band_walk(closes: List[float], bb_upper: List[float], bb_lower: List[float], max_lookback: int = 20) -> BandWalkResult:
    """Counts consecutive closes beyond one band, walking backward from the
    most recent candle, and discounts harder the longer the walk has run."""
    n = min(len(closes), len(bb_upper), len(bb_lower))
    if n == 0:
        return BandWalkResult(False, 0, "none", 1.0, "No price/band series supplied; band-walk check skipped.")

    count_upper = 0
    count_lower = 0
    for i in range(n - 1, max(-1, n - 1 - max_lookback), -1):
        c, u, l = closes[i], bb_upper[i], bb_lower[i]
        if c is None or u is None or l is None:
            break
        if c > u and count_lower == 0:
            count_upper += 1
        elif c < l and count_upper == 0:
            count_lower += 1
        else:
            break

    consecutive = max(count_upper, count_lower)
    direction = "upper" if count_upper > count_lower else ("lower" if count_lower > 0 else "none")

    if consecutive == 0:
        return BandWalkResult(False, 0, "none", 1.0, "Price is not currently outside either Bollinger band.")

    walking = consecutive >= 2
    discount = max(0.3, 1 - (consecutive * 0.12)) if walking else 1.0
    if walking:
        reasoning = (
            f"Price has closed beyond the {direction} band for {consecutive} consecutive candle(s) — "
            f"a band-walk, not a single overshoot. Confidence further discounted "
            f"{round((1 - discount) * 100)}% for the extended walk."
        )
    else:
        reasoning = f"Price touched the {direction} band but hasn't walked it yet (1 candle) — no extra discount."

    return BandWalkResult(walking, consecutive, direction, discount, reasoning)


# ─── Feature 2: Multi-timeframe regime confirmation ────────────────────────
#
# Arbitration previously only looked at the active interval's composite
# plane. Real desks don't trust a daily BUY if the weekly trend disagrees --
# this checks the signal's direction against trend-plane reads from other
# timeframes the router fetched (e.g. 15m/1d/1wk).

@dataclass
class TimeframeAlignmentResult:
    alignment_pct: float
    agreeing: List[str]
    disagreeing: List[str]
    reasoning: str

    def to_dict(self) -> dict:
        return {
            "alignment_pct": round(self.alignment_pct, 2),
            "agreeing": self.agreeing,
            "disagreeing": self.disagreeing,
            "reasoning": self.reasoning,
        }


def compute_timeframe_alignment(action: str, trend_by_interval: Optional[Dict[str, float]]) -> TimeframeAlignmentResult:
    """`trend_by_interval` maps interval label (e.g. "15m", "1d", "1wk") to
    that timeframe's Trend-category composite mean. Intervals the caller
    couldn't fetch should simply be omitted, not passed as None-filled."""
    action_upper = (action or "HOLD").upper()
    direction = 1 if action_upper == "BUY" else -1 if action_upper == "SELL" else 0

    if not trend_by_interval or direction == 0:
        return TimeframeAlignmentResult(
            100.0, [], [],
            "No higher-timeframe trend data available for this call; alignment check skipped.",
        )

    agreeing, disagreeing = [], []
    for tf, trend_mean in trend_by_interval.items():
        if trend_mean is None:
            continue
        if trend_mean * direction > 0.05:
            agreeing.append(tf)
        elif trend_mean * direction < -0.05:
            disagreeing.append(tf)

    total = len(agreeing) + len(disagreeing)
    pct = (len(agreeing) / total * 100.0) if total else 100.0

    if disagreeing:
        reasoning = (
            f"{action_upper} agrees with {', '.join(agreeing) or 'none'} but is opposed by "
            f"{', '.join(disagreeing)} — {round(pct)}% higher-timeframe alignment."
        )
    else:
        reasoning = f"{action_upper} is corroborated across all checked timeframes ({', '.join(agreeing) or 'n/a'})."

    return TimeframeAlignmentResult(pct, agreeing, disagreeing, reasoning)


# ─── Feature 3: Position-size multiplier ───────────────────────────────────
#
# adjusted_confidence and conflict_pct previously just sat in the API
# response as read-only display numbers. This turns adjusted_confidence into
# an actual scalar a caller (routers/orders.py) can multiply proposed
# quantity by, so a 22%-confidence signal proposes a smaller position than
# an 88%-confidence one automatically, not just looks scarier in the UI.
# Floored at 0.1 rather than 0 -- arbitration is a sizing input, not a
# trading halt; a hard block belongs to risk_service's own RMS gate.

def position_size_multiplier(adjusted_confidence: float) -> float:
    return round(max(0.1, min(1.0, adjusted_confidence)), 4)


def _conflict_detector(analysis: CompositeAnalysis) -> tuple[int, str]:
    """Port of analysisEngine.ts's computeConflictDetector() -- same
    dispersion/agreement-derived read the Signal Intelligence panel shows,
    so the arbitration reasoning and the panel's "AI Conflict Detector"
    number are always the same figure."""
    conflict_pct = round((1 - analysis.agreement_ratio) * 100)
    if analysis.most_conflicted_category:
        mc = analysis.most_conflicted_category
        cause = f"{mc.label} is internally split ({mc.bulls} bullish vs {mc.bears} bearish)"
    elif analysis.dispersion > 0.3:
        cause = "Category planes disagree on direction rather than any one plane being split internally"
    else:
        cause = "No significant conflict detected"
    return conflict_pct, cause


def arbitrate_signal(
    action: str,
    raw_confidence: float,
    resolved_interval: str,
    analysis: CompositeAnalysis,
    indicators: Optional[dict] = None,
    closes: Optional[List[float]] = None,
    bb_upper: Optional[List[float]] = None,
    bb_lower: Optional[List[float]] = None,
    trend_by_interval: Optional[Dict[str, float]] = None,
) -> ArbitrationResult:
    """The core arbitration: regime-weight a strategy's raw confidence
    instead of letting it stand alone.

      - mean-reversion signals get discounted when the trend plane is
        strongly against the signal's direction (this is the exact "walk
        the band" failure mode: SELL fired on a mild band overshoot while
        every trend indicator reads maximally bullish).
      - trend-following signals get discounted when the regime is RANGING
        or CONFLICTED (no dominant direction for a directional model to
        actually be riding).
      - otherwise the raw confidence is retained at full weight -- this
        is not "mean-reversion vs trend-following always fight", it's
        "discount only when the regime context says this specific call is
        the fragile one".
    """
    strategy_style = classify_strategy_style(resolved_interval)
    regime = detect_market_regime(analysis.categories)
    conflict_pct, conflict_cause = _conflict_detector(analysis)

    trend_cat = _find_category(analysis.categories, _TREND_LABELS)
    trend_mean = trend_cat.mean if trend_cat else 0.0

    action_upper = (action or "HOLD").upper()
    direction = 1 if action_upper == "BUY" else -1 if action_upper == "SELL" else 0

    discount = 1.0
    reasoning = "No regime conflict detected for this signal; raw confidence retained."

    if strategy_style == "mean_reversion":
        if direction != 0 and (trend_mean * direction) < -0.1:
            opposition = min(1.0, abs(trend_mean))
            discount = max(0.25, 1 - opposition)
            reasoning = (
                f"Mean-reversion {action_upper} is fighting a trend plane reading {trend_mean:+.2f} "
                f"({regime.regime.replace('_', ' ')}) — confidence discounted {round((1 - discount) * 100)}% "
                f"for lack of trend-regime awareness rather than shown at face value."
            )
        elif regime.regime == "RANGING":
            reasoning = "Regime is RANGING — mean-reversion signals carry full weight here; no discount applied."
        else:
            reasoning = f"Trend plane ({trend_mean:+.2f}) does not meaningfully oppose this signal; raw confidence retained."
    else:  # trend_following
        if regime.regime == "CONFLICTED":
            discount = 0.5
            reasoning = (
                "Regime is CONFLICTED — category planes disagree heavily with no dominant direction; "
                "trend-following confidence discounted 50%."
            )
        elif regime.regime == "RANGING":
            discount = 0.7
            reasoning = (
                "Regime is RANGING — no dominant trend for a directional model to ride; "
                "trend-following confidence discounted 30%."
            )
        elif regime.regime in ("TRENDING", "BREAKOUT"):
            reasoning = f"Regime is {regime.regime} — signal aligns with the dominant regime; full weight retained."

    reasoning_parts = [reasoning]

    # ── Feature 1: data-integrity gate ──────────────────────────────────
    integrity = check_data_integrity(indicators)
    if not integrity.complete:
        # Scale by completeness rather than hard-zeroing -- a signal missing
        # one of sixteen fields is fragile, not necessarily worthless.
        discount *= max(0.2, integrity.completeness_ratio)
        reasoning_parts.append(integrity.reasoning)

    # ── Feature 4: band-walk detector (mean-reversion only) ─────────────
    band_walk_result: Optional[BandWalkResult] = None
    if strategy_style == "mean_reversion" and closes and bb_upper and bb_lower:
        band_walk_result = detect_band_walk(closes, bb_upper, bb_lower)
        if band_walk_result.walking:
            discount *= band_walk_result.discount_factor
            reasoning_parts.append(band_walk_result.reasoning)

    # ── Feature 2: multi-timeframe alignment ─────────────────────────────
    timeframe_result = compute_timeframe_alignment(action_upper, trend_by_interval)
    if timeframe_result.disagreeing:
        # Continuous 0.5x (fully opposed) .. 1.0x (fully aligned) scaling,
        # separate from the single-timeframe regime discount above.
        tf_multiplier = 0.5 + (timeframe_result.alignment_pct / 100.0) * 0.5
        discount *= tf_multiplier
        reasoning_parts.append(timeframe_result.reasoning)

    adjusted_confidence = max(0.0, min(1.0, raw_confidence * discount))
    combined_reasoning = " | ".join(reasoning_parts)

    # ── Feature 3: position-size multiplier ──────────────────────────────
    size_multiplier = position_size_multiplier(adjusted_confidence)

    return ArbitrationResult(
        strategy_style=strategy_style,
        action=action_upper,
        raw_confidence=raw_confidence,
        adjusted_confidence=adjusted_confidence,
        discount_factor=discount,
        regime=regime.regime,
        regime_confidence=regime.confidence,
        regime_reasoning=regime.reasoning,
        trend_composite=trend_mean,
        conflict_pct=conflict_pct,
        conflict_cause=conflict_cause,
        reasoning=combined_reasoning,
        data_integrity=integrity,
        band_walk=band_walk_result,
        timeframe_alignment=timeframe_result,
        position_size_multiplier=size_multiplier,
    )


# ─── Features 5 & 6: calibration ledger + regime-transition tracking ──────
#
# These two are the deliberate exception to "nothing here does I/O" --
# arbitrate_signal() above stays a pure function so it's trivially
# unit-testable; these are separate, explicit calls a router opts into,
# so the core arbitration math can never accidentally depend on disk state
# or module-level globals.

_LEDGER_PATH = os.path.join(os.path.dirname(__file__), "arbitration_ledger.jsonl")


def log_arbitration_decision(symbol: str, interval: str, result: ArbitrationResult, extra: Optional[dict] = None) -> None:
    """Append-only ledger of every arbitration decision (raw confidence,
    adjusted confidence, regime, and every discount reason) so the
    hand-picked discount factors (25%/50%/70%/band-walk/etc.) can eventually
    be refit against realized N-bars-later accuracy instead of guessed.
    Best-effort: a write failure never breaks the request path."""
    try:
        record = {"ts": time.time(), "symbol": symbol, "interval": interval, **result.to_dict()}
        if extra:
            record.update(extra)
        with open(_LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


# In-memory regime-transition state. Deliberately module-level and simple
# (no WS manager exists in main.py yet to push these proactively) -- the
# router exposes them via a cheap polling endpoint instead.
_LAST_REGIME_BY_KEY: Dict[str, str] = {}
_REGIME_TRANSITIONS: deque = deque(maxlen=200)

# Latest arbitration result per symbol, so other routers (e.g.
# routers/orders.py sizing an order) can read the most recent
# position_size_multiplier without recomputing arbitration themselves.
_LAST_ARBITRATION_BY_SYMBOL: Dict[str, dict] = {}


def _symbol_key(symbol: str) -> str:
    return symbol.upper().strip()


def track_regime_transition(symbol: str, interval: str, regime: str, regime_confidence: float) -> Optional[dict]:
    """Records a regime flip (e.g. RANGING -> TRENDING) for this
    symbol+interval. Returns the transition event if the regime actually
    changed since the last call, else None."""
    key = f"{_symbol_key(symbol)}:{interval}"
    prev = _LAST_REGIME_BY_KEY.get(key)
    _LAST_REGIME_BY_KEY[key] = regime
    if prev is not None and prev != regime:
        event = {
            "symbol": symbol,
            "interval": interval,
            "from_regime": prev,
            "to_regime": regime,
            "regime_confidence": round(regime_confidence, 4),
            "ts": time.time(),
        }
        _REGIME_TRANSITIONS.append(event)
        return event
    return None


def get_recent_regime_transitions(symbol: Optional[str] = None, limit: int = 20) -> List[dict]:
    """Backs the /active/regime-alerts polling endpoint."""
    items = list(_REGIME_TRANSITIONS)
    if symbol:
        sk = _symbol_key(symbol)
        items = [e for e in items if _symbol_key(e["symbol"]) == sk]
    return list(reversed(items[-limit:]))


def cache_arbitration(symbol: str, interval: str, result: ArbitrationResult) -> None:
    """Stashes the latest arbitration result for a symbol so orders.py can
    look up its position_size_multiplier without recomputing anything."""
    entry = result.to_dict()
    entry["interval"] = interval
    entry["ts"] = time.time()
    _LAST_ARBITRATION_BY_SYMBOL[_symbol_key(symbol)] = entry


def get_cached_arbitration(symbol: str, max_age_seconds: float = 900.0) -> Optional[dict]:
    """Returns the cached arbitration entry for `symbol` if one exists and
    isn't stale (default: 15 minutes), else None."""
    entry = _LAST_ARBITRATION_BY_SYMBOL.get(_symbol_key(symbol))
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > max_age_seconds:
        return None
    return entry