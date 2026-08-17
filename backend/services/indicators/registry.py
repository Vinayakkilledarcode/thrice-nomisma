# services/indicators/registry.py
"""
Core registry for the Indicator Engine.

This is what turns "a fixed list of indicators" into an actual engine:
every indicator is a small function registered under a name + category via
the @indicator decorator. Adding indicator #86 or #400 later means writing
one short function and decorating it -- nothing else in the engine, the
router, or the frontend needs to change, because they all just iterate the
registry.

Two registries live here:

  INDICATOR_REGISTRY     -- fixed-parameter indicators (rsi_14, sma_20, ...).
                             Registered with @indicator. Every name is a
                             pre-baked, immutable configuration.

  CONFIGURABLE_REGISTRY  -- parameterized indicators (sma, rsi, macd, ...)
                             whose settings (period, std_dev, fast/slow, ...)
                             are supplied per-request from the frontend's
                             settings panel. Registered with @configurable.

Why two registries instead of one:
The fixed registry gives cheap, addressable defaults for the main sweep
(GET /indicators/{symbol}) -- the frontend can ask for "rsi_14" by name
without knowing anything about parameters. The configurable registry
exists specifically for the "Adjust Settings" flow: the user changes RSI's
period to 25 in the UI, and that exact value must be the one that gets
computed -- not silently ignored in favor of a hard-coded default, and not
dropped into an ERROR/blank payload because "rsi_25" was never registered.
@configurable always merges whatever the caller supplies with the
indicator's declared parameter schema, so a request is either computed
with the caller's values (clamped to sane bounds) or it fails loudly with
an explicit error -- it never *silently* returns an empty/blank result.

Every registered indicator (either kind) is automatically:
  - crash-safe: an exception inside one indicator (bad dtype, NaN-heavy
    window, div-by-zero) degrades to an ERROR payload for that indicator
    only, instead of taking down the whole /indicators response. Same
    philosophy as strategies/strategy_utils.py's safe_evaluate.
  - bar-count safe: indicators declare (or derive, for configurable ones)
    a minimum bar requirement and get an INSUFFICIENT_DATA payload instead
    of running on a too-short window.

Every indicator function has the signature:
    def calc(df: pd.DataFrame, **kwargs) -> Dict[str, Any]

`df` is a title-cased OHLCV frame (Open/High/Low/Close/Volume), matching
the convention already used throughout strategies/*.py and market.py.

The returned dict always has at least:
    "value":  float, or a dict of named sub-values for multi-line
              indicators (e.g. MACD -> {"macd":..., "signal":..., "hist":...})
    "signal": one of a small vocabulary -- "BULLISH" / "BEARISH" / "NEUTRAL" /
              "OVERBOUGHT" / "OVERSOLD" / "INSUFFICIENT_DATA" / "ERROR"
and may optionally include "_series" (private): a dict of name -> pd.Series
used by the engine to build chartable tails when include_series=True is
requested. The engine strips "_series" from the final API response.

Configurable-indicator results additionally always include:
    "params_used": {...}   -- the exact, fully-resolved parameter set that
                               was actually used for this computation, so
                               the frontend can render/confirm what it got
                               back instead of guessing.
"""
from __future__ import annotations

import functools
import logging
import math
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("nomisma.indicators")

INDICATOR_REGISTRY: Dict[str, Dict[str, Any]] = {}
CONFIGURABLE_REGISTRY: Dict[str, Dict[str, Any]] = {}


def sanitize_json(obj: Any) -> Any:
    """
    Recursively replaces NaN / +Inf / -Inf with None and coerces numpy
    scalar types to native Python ones.

    Why this exists: real market data occasionally has a bad zero-price or
    zero-volume bar (illiquid pre-market prints, corrupted feed rows, stock
    splits, etc.). A handful of indicators do sequential/compounding math
    (NVI, PVI, Klinger) or unguarded log-returns (historical volatility,
    rolling Sharpe, skew/kurtosis/autocorrelation) that can turn one bad
    input row into NaN or Infinity. Python's strict JSON encoder
    (Starlette's JSONResponse uses allow_nan=False) then raises
    'Out of range float values are not JSON compliant' and the WHOLE
    response 500s -- not just that one indicator.

    This is the safety net: every indicator's result passes through here
    before being returned, so one bad calculation degrades to `null` for
    that field instead of crashing the entire sweep. See also
    indicator_engine.run()'s df.replace([inf, -inf], nan) for the
    complementary root-cause fix (cleaning the input before it's used).
    """
    if isinstance(obj, dict):
        return {k: sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_json(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def indicator(name: str, category: str, min_bars: int = 20, description: str = ""):
    """Decorator factory that registers a function as a fixed-parameter indicator."""
    def decorator(func: Callable[..., Dict[str, Any]]):
        @functools.wraps(func)
        def wrapper(df: pd.DataFrame, **kwargs) -> Dict[str, Any]:
            bars = 0 if df is None else len(df)
            if df is None or df.empty or bars < min_bars:
                return {
                    "value": None,
                    "signal": "INSUFFICIENT_DATA",
                    "error": f"needs >= {min_bars} bars, got {bars}",
                }
            try:
                result = func(df, **kwargs)
                result.setdefault("signal", "NEUTRAL")
                return result
            except Exception as exc:
                logger.exception("Indicator %r raised during computation", name)
                return {"value": None, "signal": "ERROR", "error": f"{type(exc).__name__}: {exc}"}

        if name in INDICATOR_REGISTRY:
            raise ValueError(f"Duplicate indicator name registered: {name!r}")

        INDICATOR_REGISTRY[name] = {
            "func": wrapper,
            "category": category,
            "min_bars": min_bars,
            "description": description or (func.__doc__ or "").strip(),
        }
        return wrapper
    return decorator


def get_categories() -> Dict[str, List[str]]:
    cats: Dict[str, List[str]] = {}
    for name, meta in INDICATOR_REGISTRY.items():
        cats.setdefault(meta["category"], []).append(name)
    return {k: sorted(v) for k, v in cats.items()}


def list_indicators(category: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    if category:
        return {n: m for n, m in INDICATOR_REGISTRY.items() if m["category"] == category}
    return dict(INDICATOR_REGISTRY)


# --------------------------------------------------------------------------- #
# Configurable indicators -- parameterized at request time, never blank
# --------------------------------------------------------------------------- #
def _coerce(value: Any, spec: Dict[str, Any]) -> Any:
    """Cast `value` to the type declared in `spec`, falling back to the
    schema default if the cast fails (bad/garbage input never propagates
    as a silent None -- it's replaced with a known-good default instead)."""
    target_type = spec.get("type", float)
    try:
        if value is None:
            raise ValueError("missing")
        coerced = target_type(value)
    except (TypeError, ValueError):
        coerced = spec["default"]
    if "min" in spec:
        coerced = max(spec["min"], coerced)
    if "max" in spec:
        coerced = min(spec["max"], coerced)
    return coerced


def resolve_params(param_schema: Dict[str, Dict[str, Any]], provided: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merges caller-supplied params with an indicator's declared schema.

    Every key in `param_schema` is guaranteed to be present in the return
    value -- either the caller's (validated/clamped) value, or the schema
    default. This is the mechanism that stops "manual settings" requests
    from ever silently degrading to a blank/empty computation: whatever
    the frontend sends, a complete, valid parameter set always comes out.
    """
    provided = provided or {}
    return {name: _coerce(provided.get(name), spec) for name, spec in param_schema.items()}


def configurable(
    name: str,
    category: str,
    param_schema: Dict[str, Dict[str, Any]],
    min_bars_fn: Optional[Callable[[Dict[str, Any]], int]] = None,
    description: str = "",
):
    """
    Decorator factory for parameterized indicators.

    `param_schema` example:
        {"period": {"type": int, "default": 14, "min": 2, "max": 500}}

    `func` receives the *fully resolved* params as keyword arguments, e.g.
        def _rsi(df: pd.DataFrame, period: int) -> dict: ...

    `min_bars_fn(resolved_params) -> int` lets the minimum bar requirement
    scale with the caller's chosen period (e.g. RSI(200) legitimately needs
    more history than RSI(14)). If omitted, a sensible default is derived
    from the largest numeric param.
    """
    def decorator(func: Callable[..., Dict[str, Any]]):
        @functools.wraps(func)
        def wrapper(df: pd.DataFrame, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            resolved = resolve_params(param_schema, params)

            if min_bars_fn:
                required = min_bars_fn(resolved)
            else:
                numeric_vals = [v for v in resolved.values() if isinstance(v, (int, float))]
                required = int(max(numeric_vals, default=20)) + 10

            bars = 0 if df is None else len(df)
            if df is None or df.empty or bars < required:
                return {
                    "value": None,
                    "signal": "INSUFFICIENT_DATA",
                    "error": f"needs >= {required} bars, got {bars}",
                    "params_used": resolved,
                }
            try:
                result = func(df, **resolved)
                result.setdefault("signal", "NEUTRAL")
                result["params_used"] = resolved
                return sanitize_json(result)
            except Exception as exc:
                logger.exception("Configurable indicator %r raised during computation (params=%s)", name, resolved)
                return {
                    "value": None,
                    "signal": "ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                    "params_used": resolved,
                }

        if name in CONFIGURABLE_REGISTRY:
            raise ValueError(f"Duplicate configurable indicator name registered: {name!r}")

        CONFIGURABLE_REGISTRY[name] = {
            "func": wrapper,
            "category": category,
            "param_schema": param_schema,
            "description": description or (func.__doc__ or "").strip(),
        }
        return wrapper
    return decorator


def evaluate_configurable(name: str, df: pd.DataFrame, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Single entry point the engine/router uses to run a configurable
    indicator by name. Unknown names fail loudly (explicit ERROR payload
    naming the valid options) rather than returning a bare blank result."""
    meta = CONFIGURABLE_REGISTRY.get(name)
    if not meta:
        return {
            "value": None,
            "signal": "ERROR",
            "error": f"Unknown configurable indicator {name!r}. Valid options: {sorted(CONFIGURABLE_REGISTRY)}",
        }
    return meta["func"](df, params=params)


def configurable_schema(name: Optional[str] = None) -> Dict[str, Any]:
    """Used by GET /indicators/configurable/schema to build the frontend's
    settings panel: every configurable indicator's params, types, defaults,
    and bounds."""
    if name:
        meta = CONFIGURABLE_REGISTRY.get(name)
        if not meta:
            return {}
        return {
            "category": meta["category"],
            "description": meta["description"],
            "params": {
                pname: {k: v for k, v in spec.items() if k != "type"} | {"type": spec.get("type", float).__name__}
                for pname, spec in meta["param_schema"].items()
            },
        }
    return {n: configurable_schema(n) for n in CONFIGURABLE_REGISTRY}


# --------------------------------------------------------------------------- #
# Small shared math helpers used across multiple indicator modules
# --------------------------------------------------------------------------- #
def wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1)
    return series.rolling(period).apply(
        lambda x: np.dot(x, weights) / weights.sum() if len(x) == period else np.nan, raw=True
    )


def hma(series: pd.Series, period: int) -> pd.Series:
    half = max(int(period / 2), 1)
    sqrt_p = max(int(np.sqrt(period)), 1)
    raw = 2 * wma(series, half) - wma(series, period)
    return wma(raw, sqrt_p)


def true_range(df: pd.DataFrame) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    return np.maximum(high - low, np.maximum(abs(high - prev_close), abs(low - prev_close)))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).rolling(period).mean()


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def last_float(series: pd.Series, default: float = 0.0) -> float:
    if series is None or len(series) == 0:
        return default
    val = series.iloc[-1]
    return default if pd.isna(val) else float(val)


def cross_signal(fast_last: float, slow_last: float, bull: str = "BULLISH", bear: str = "BEARISH") -> str:
    if fast_last > slow_last:
        return bull
    if fast_last < slow_last:
        return bear
    return "NEUTRAL"