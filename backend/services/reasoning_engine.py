# services/reasoning_engine.py
#
# ── NOMISMA QUANT REASONING ENGINE ─────────────────────────────────────────
# A local, deterministic replacement for the Gemini portfolio-decision
# narrative. No external API calls, no quota, no cost, no network
# dependency, no latency beyond the numbers already sitting in memory.
#
# ── WHY THIS VERSION LOOKS DIFFERENT FROM THE FIRST DRAFT ──────────────────
# The first draft of this file assumed market.py ran all three horizon
# strategies (intraday / swing / investment) on every request and handed
# back three populated model blocks for cross-timeframe confluence. That
# assumption was wrong. Looking at the real backend:
#
#   - Only ONE strategy model actually runs per request — whichever one
#     matches the resolved interval/horizon (evaluate_swing_momentum_1d,
#     evaluate_intraday_mean_reversion_15m, etc.).
#   - Its output lands in a single dict called `active_run`, containing
#     action, confidence, signal_quality, indicators (a free-form dict —
#     field names vary per strategy: ema_fast/macd for swing, vwap/bands
#     for intraday, weekly_rsi_14 for investment, etc.), and
#     risk_management (also free-form — stop_loss/take_profit/
#     position_size_pct for short horizons, downside_protection_level for
#     long ones).
#   - `detailed_runs` mirrors that same active_run into whichever of
#     intraday_mean_reversion / swing_momentum / long_term_value matches
#     the current horizon, leaving the other two as {}. So there is no
#     real multi-model data to reconcile in a single call.
#
# This rewrite drops the fabricated cross-timeframe-confluence logic and
# works directly off the one real signal that exists: active_run, plus
# trend and sentiment context. It reads `indicators` and `risk_management`
# generically (iterates whatever keys are present) instead of hardcoding
# strategy-specific field names, so it doesn't break when a different
# horizon's strategy (with a different indicator set) becomes active.
#
# ── PUBLIC ENTRY POINT ──────────────────────────────────────────────────────
# generate_reasoning_explanation(...) has the EXACT same argument names and
# return type (a markdown string) as the old generate_gemini_explanation()
# in market.py, so it's a true drop-in replacement — swap the import and
# the call, nothing else in market.py needs to change.
#
# analyze_portfolio_signal(payload) is kept as a lower-level entry point
# (returns a structured dict with narrative/conviction/score/risk_flags)
# for anywhere that wants the raw analysis instead of the formatted string.

from typing import Any, Dict, List, Optional

ENGINE_NAME = "Nomisma Quant Reasoning Engine v2 (local, active-run based)"

LOW_CONVICTION_THRESHOLD = 0.35
STRONG_CONVICTION_THRESHOLD = 0.65

RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0

# Indicator keys that get RSI-style overbought/oversold treatment when
# present, regardless of which strategy produced them (rsi_14,
# weekly_rsi_14, rsi, etc. all match).
_RSI_KEY_HINTS = ("rsi",)

# Risk-management keys we specifically call out if present (long/short
# horizons use different subsets of these).
_RISK_KEY_ORDER = [
    "stop_loss", "take_profit", "position_size_pct",
    "downside_protection_level", "risk_reward_ratio", "atr",
]


# ─────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────────────

def _fmt_val(v: Any) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        # Percent-like small ratios (e.g. position_size_pct as 0.0-1.0) read
        # oddly at 4 decimals; pick precision by magnitude instead.
        if abs(v) < 10:
            return f"{v:.4f}".rstrip("0").rstrip(".")
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _action_sign(action: Optional[str]) -> float:
    if not action:
        return 0.0
    a = action.upper()
    if a in ("BUY", "LONG"):
        return 1.0
    if a in ("SHORT", "SELL"):
        return -1.0
    return 0.0


def _is_rsi_key(key: str) -> bool:
    k = key.lower()
    return any(hint in k for hint in _RSI_KEY_HINTS)


# ─────────────────────────────────────────────────────────────────────────
# Core analysis — works entirely off active_run + trend + sentiment
# ─────────────────────────────────────────────────────────────────────────

def analyze_portfolio_signal(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    payload shape (matches what market.py already builds):
      {
        "symbol": str, "signal": "BUY"|"SELL"|"WATCH"|"HOLD",
        "horizon": "intraday"|"swing"|"investment",
        "is_bullish_trend": bool, "trend_label": str,
        "sentiment_score": 0-100 (int),
        "timeframe_label": str, "model_name": str,
        "detailed_runs": {
            "active_run": {
                "action": "BUY"|"SHORT"|"HOLD",
                "confidence": 0.0-1.0,
                "signal_quality": str,
                "indicators": {...free-form...},
                "risk_management": {...free-form, may be {}...}
            },
            ...
        }
      }

    Returns:
        {
          "narrative": str (markdown),
          "conviction": str,
          "composite_score": float (-1..1),
          "risk_flags": [str, ...],
          "engine": ENGINE_NAME,
        }
    """
    symbol = payload.get("symbol", "")
    horizon = payload.get("horizon") or ""
    signal = (payload.get("signal") or "HOLD").upper()
    is_bullish_trend = bool(payload.get("is_bullish_trend"))
    trend_label = payload.get("trend_label") or "SMA-200 (1D)"
    sentiment_score = payload.get("sentiment_score")
    timeframe_label = payload.get("timeframe_label") or "Auto-Detected Timeframe"
    model_name = payload.get("model_name") or horizon.upper()

    active_run = (payload.get("detailed_runs") or {}).get("active_run", {}) or {}
    indicators: Dict[str, Any] = active_run.get("indicators") or {}
    risk_mgmt: Dict[str, Any] = active_run.get("risk_management") or {}
    signal_quality = active_run.get("signal_quality", "NEUTRAL")
    action = active_run.get("action", "HOLD")
    confidence = float(active_run.get("confidence") or 0.0)

    risk_flags: List[str] = []

    # ── RSI-style overbought/oversold check, generic across whichever
    # indicator key actually holds an RSI value on this horizon. ──
    for k, v in indicators.items():
        if _is_rsi_key(k) and isinstance(v, (int, float)):
            if v >= RSI_OVERBOUGHT:
                risk_flags.append(f"{k} at {v:.0f} is in overbought territory — upside follow-through may be limited near-term.")
            elif v <= RSI_OVERSOLD:
                risk_flags.append(f"{k} at {v:.0f} is in oversold territory — downside follow-through may be limited near-term.")

    if confidence and confidence < LOW_CONVICTION_THRESHOLD:
        risk_flags.append(f"{model_name} model confidence is only {confidence*100:.0f}% — treat this as a directional lean, not a definitive call.")

    # ── Counter-trend conflict: does the active call run against the
    # broader structural trend? ──
    active_sign = _action_sign(action)
    trend_sign = 1.0 if is_bullish_trend else -1.0
    conflict_notes: List[str] = []
    if active_sign != 0 and active_sign != trend_sign:
        conflict_notes.append(
            f"The active {model_name} call runs counter to the underlying {trend_label} trend — "
            f"this looks like a counter-trend / mean-reversion trade, not a trend-following one, "
            f"which typically warrants a tighter risk window."
        )
    risk_flags = conflict_notes + risk_flags

    # ── Composite score: confidence-weighted direction, nudged by trend
    # and sentiment (secondary factors, not primary drivers). ──
    composite_score = active_sign * confidence
    trend_adj = 0.15 if is_bullish_trend else -0.15
    sentiment_adj = 0.0
    if isinstance(sentiment_score, (int, float)):
        sentiment_adj = ((sentiment_score - 50.0) / 50.0) * 0.10
    composite_score = max(-1.0, min(1.0, composite_score + trend_adj + sentiment_adj))

    if confidence >= STRONG_CONVICTION_THRESHOLD and active_sign != 0:
        conviction = f"STRONG_{'BUY' if active_sign > 0 else 'SHORT'}"
    elif confidence >= LOW_CONVICTION_THRESHOLD and active_sign != 0:
        conviction = f"MODERATE_{'BUY' if active_sign > 0 else 'SHORT'}"
    elif active_sign != 0:
        conviction = f"WEAK_{'BUY' if active_sign > 0 else 'SHORT'}"
    else:
        conviction = "HOLD"

    narrative = _build_markdown(
        symbol=symbol,
        signal=signal,
        action=action,
        signal_quality=signal_quality,
        confidence=confidence,
        model_name=model_name,
        timeframe_label=timeframe_label,
        is_bullish_trend=is_bullish_trend,
        trend_label=trend_label,
        sentiment_score=sentiment_score,
        indicators=indicators,
        risk_mgmt=risk_mgmt,
        conflict_notes=conflict_notes,
        risk_flags=risk_flags,
        conviction=conviction,
        composite_score=composite_score,
    )

    return {
        "narrative": narrative,
        "conviction": conviction,
        "composite_score": round(composite_score, 3),
        "risk_flags": risk_flags,
        "engine": ENGINE_NAME,
    }


# ─────────────────────────────────────────────────────────────────────────
# Markdown narrative builder — mirrors the structure the old Gemini prompt
# asked for, so the frontend rendering (headers/bullets) looks the same.
# ─────────────────────────────────────────────────────────────────────────

def _build_markdown(
    symbol: str,
    signal: str,
    action: str,
    signal_quality: str,
    confidence: float,
    model_name: str,
    timeframe_label: str,
    is_bullish_trend: bool,
    trend_label: str,
    sentiment_score: Any,
    indicators: Dict[str, Any],
    risk_mgmt: Dict[str, Any],
    conflict_notes: List[str],
    risk_flags: List[str],
    conviction: str,
    composite_score: float,
) -> str:
    trend_word = "BULLISH (Price above " + trend_label + ")" if is_bullish_trend else "BEARISH (Price below " + trend_label + ")"

    if isinstance(sentiment_score, (int, float)):
        sentiment_word = "Positive" if sentiment_score >= 55 else "Negative" if sentiment_score < 45 else "Neutral"
        sentiment_line = f"{sentiment_score:.0f}% ({sentiment_word})"
    else:
        sentiment_line = "N/A"

    lines: List[str] = []

    # ── Section 1 ──
    lines.append("### 1. OPERATIONAL BIAS & DIRECTIONAL BIAS")
    op_desc = {
        "BUY": f"'{signal}' means the {model_name} model is signaling a long entry on the {timeframe_label} timeframe — technicals currently favor upside continuation over this horizon.",
        "SELL": f"'{signal}' means the {model_name} model is signaling downside risk on the {timeframe_label} timeframe — technicals currently favor further weakness over this horizon.",
        "WATCH": f"'{signal}' means the {model_name} model sees a developing but unconfirmed setup on the {timeframe_label} timeframe — conditions are notable but the trigger hasn't fired yet.",
        "HOLD": f"'{signal}' means the {model_name} model does not see a clean directional edge on the {timeframe_label} timeframe right now — the defensible posture is to stay flat.",
    }.get(signal, f"'{signal}' reflects the {model_name} model's current read on the {timeframe_label} timeframe.")
    lines.append(f"- {op_desc}")
    lines.append(f"- **Model confidence:** {confidence*100:.0f}% · **Signal quality:** {signal_quality} · **Composite conviction:** {conviction.replace('_', ' ')} (score {composite_score:+.2f})")

    # ── Section 2 ──
    lines.append("")
    lines.append("### 2. QUANTITATIVE EVIDENCE BREAKDOWN")
    if indicators:
        for k, v in indicators.items():
            lines.append(f"- **{k}**: {_fmt_val(v)}")
    else:
        lines.append("- No indicator readings were attached to this run.")
    lines.append(f"- **Macro trend:** {trend_word}")
    lines.append(f"- **NLP sentiment index:** {sentiment_line}")

    # ── Section 3 ──
    lines.append("")
    lines.append("### 3. ACTIONABLE DIRECTIVES & EXECUTION RATIONALE")
    action_upper = (action or "HOLD").upper()
    if signal == "HOLD":
        lines.append("- Preserve cash / current position; no new entry is justified by the active model.")
        lines.append(f"- Watch for {model_name} confidence to strengthen or for a fresh trigger before acting.")
    elif signal == "BUY":
        lines.append(f"- Entry rationale is driven by the {model_name} model's {action_upper} call.")
        lines.append("- Confirm with the indicator readings above before sizing the position.")
    elif signal == "SELL":
        lines.append(f"- Reduce exposure in line with the {model_name} model's {action_upper} call.")
        lines.append("- Treat any of the risk parameters below as the working stop for this trade.")
    elif signal == "WATCH":
        lines.append("- This is a speculative setup, not a confirmed trigger — size accordingly or wait for confirmation.")
        lines.append(f"- Re-check once {model_name}'s indicators cross into a clean BUY/SELL state.")
    else:
        lines.append(f"- Follow the active {model_name} model's {action_upper} guidance with standard position discipline.")

    # ── Section 4 ──
    lines.append("")
    lines.append("### 4. STRATEGIC RISK WARNING")
    if risk_mgmt:
        for k in _RISK_KEY_ORDER:
            if k in risk_mgmt and risk_mgmt[k] is not None:
                lines.append(f"- **{k}**: {_fmt_val(risk_mgmt[k])}")
        # Any remaining keys not in the preferred order
        for k, v in risk_mgmt.items():
            if k not in _RISK_KEY_ORDER and v is not None:
                lines.append(f"- **{k}**: {_fmt_val(v)}")
    else:
        lines.append("- No active risk parameters are present (flat / HOLD state) — no stop, target, or sizing to report.")

    if risk_flags:
        lines.append("")
        for f in risk_flags:
            lines.append(f"- ⚠ {f}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# Drop-in replacement for generate_gemini_explanation() in market.py.
# Same argument names, same return type (str), no network I/O — so it can
# simply be awaited the same way the Gemini call was.
# ─────────────────────────────────────────────────────────────────────────

async def generate_reasoning_explanation(
    symbol: str,
    signal: str,
    horizon: str,
    sentiment_score: int,
    is_bullish_trend: bool,
    detailed_runs: dict,
    timeframe_label: str = "",
    model_name: str = "",
) -> str:
    payload = {
        "symbol": symbol,
        "signal": signal,
        "horizon": horizon,
        "sentiment_score": sentiment_score,
        "is_bullish_trend": is_bullish_trend,
        "trend_label": (detailed_runs or {}).get("trend_label"),
        "timeframe_label": timeframe_label,
        "model_name": model_name,
        "detailed_runs": detailed_runs,
    }
    result = analyze_portfolio_signal(payload)
    return result["narrative"]