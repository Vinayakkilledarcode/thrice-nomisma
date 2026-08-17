# services/indicator_engine.py
"""
Indicator Engine -- the orchestration layer the API routers call into.

This module does NOT contain any indicator math itself. It:
  1. imports the indicator modules so their @indicator/@configurable
     decorators run and populate the two registries in
     services/indicators/registry.py,
  2. cleans/validates the incoming OHLCV frame once, up front, so every
     individual indicator doesn't have to defensively re-check it,
  3. fans a request out across the registry (a full sweep, a filtered
     subset, a single named indicator, or a multi-timeframe batch),
  4. and guarantees every response is JSON-safe (no NaN/Inf) before it
     leaves the process.

Design note on the "manual settings never go blank" requirement:
`run_configurable` is a thin pass-through to
`services.indicators.registry.evaluate_configurable`, which always merges
the caller's params with the indicator's declared schema (see
registry.resolve_params). That means every call here is guaranteed to run
with a complete, valid parameter set -- either the user's own values
(clamped to sane bounds) or documented defaults -- and the response always
echoes "params_used" so the frontend can confirm exactly what was
computed. The only ways this now returns a blank/null value are:
  (a) genuinely insufficient bars for the requested period ("INSUFFICIENT_DATA"), or
  (b) an unknown indicator name ("ERROR", with the valid names listed).
Both are explicit, explained failures -- never a silent empty payload.
"""
from __future__ import annotations

import ast
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .indicators.registry import (
    INDICATOR_REGISTRY,
    evaluate_configurable,
    configurable_schema as _configurable_schema,
    get_categories,
    last_float,
    list_indicators,
    sanitize_json,
)

# Importing these registers every @indicator / @configurable function.
from .indicators import (  # noqa: F401
    candlestick,
    momentum,
    price_action,
    statistical,
    trend,
    volatility,
    volume,
    configurable,
)

logger = logging.getLogger("nomisma.indicator_engine")


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
def list_available(category: Optional[str] = None) -> Dict[str, Any]:
    """Powers GET /indicators/list."""
    entries = list_indicators(category)
    return {
        "categories": get_categories(),
        "indicators": {
            name: {
                "category": meta["category"],
                "min_bars": meta["min_bars"],
                "description": meta["description"],
            }
            for name, meta in entries.items()
        },
        "count": len(entries),
    }


def configurable_schema() -> Dict[str, Any]:
    """Powers GET /indicators/configurable/schema -- everything the
    settings-panel UI needs to render per-indicator controls (types,
    defaults, min/max bounds)."""
    return _configurable_schema()


# --------------------------------------------------------------------------- #
# Data hygiene
# --------------------------------------------------------------------------- #
def _clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """Replaces +/-Inf with NaN across OHLCV columns before any indicator
    touches the frame. Complements sanitize_json(), which cleans up on the
    way OUT -- this cleans on the way IN, so a single corrupted feed row
    (e.g. a zero-division upstream) can't propagate Inf through an entire
    rolling window's worth of computations."""
    if df is None or df.empty:
        return df
    cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    cleaned = df.copy()
    cleaned[cols] = cleaned[cols].replace([np.inf, -np.inf], np.nan)
    return cleaned


# --------------------------------------------------------------------------- #
# Fixed-registry sweep
# --------------------------------------------------------------------------- #
def run(
    df: pd.DataFrame,
    categories: Optional[List[str]] = None,
    names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Powers GET /indicators/{symbol} and GET /active/indicators."""
    df = _clean_df(df)
    entries = dict(INDICATOR_REGISTRY)

    if categories:
        wanted = set(categories)
        entries = {n: m for n, m in entries.items() if m["category"] in wanted}
    if names:
        wanted_names = set(names)
        entries = {n: m for n, m in entries.items() if n in wanted_names}

    results: Dict[str, Any] = {}
    for name, meta in entries.items():
        try:
            result = meta["func"](df)
        except Exception as exc:  # belt-and-braces: @indicator already catches internally
            logger.exception("Indicator %r crashed at the engine layer", name)
            result = {"value": None, "signal": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        # The frontend (TabbedPanel.tsx) buckets indicators into tabs via
        # `v.category === indicatorCategory`. The registered indicator
        # functions themselves only return {"value", "signal", ...} -- the
        # category lives solely in the registry metadata (meta["category"])
        # and was never being attached to the actual computed payload. That
        # made every category's filter match zero entries regardless of how
        # much real data came back, which is why the panel showed
        # "No <Category> data for this timeframe yet" even with a fully
        # populated, correctly-fetched dataframe. Stamping it here is the fix.
        result["category"] = meta["category"]
        results[name] = result

    return {
        "bars_used": 0 if df is None else len(df),
        "indicators": sanitize_json(results),
    }


def run_multi_timeframe(
    dfs: Dict[str, pd.DataFrame],
    categories: Optional[List[str]] = None,
    names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Powers GET /active/indicators/multi-timeframe -- same indicator set,
    evaluated independently per timeframe (spec section 14)."""
    return {timeframe: run(frame, categories=categories, names=names) for timeframe, frame in dfs.items()}


# --------------------------------------------------------------------------- #
# Configurable (manual-settings) single indicator
# --------------------------------------------------------------------------- #
def run_configurable(df: pd.DataFrame, indicator_name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Powers POST /indicators/configurable.

    `params` is whatever the frontend's settings panel currently holds
    (e.g. {"period": 25}). These are the caller's manual values and are
    always honored: resolve_params() (inside evaluate_configurable) fills
    in any *missing* keys from the indicator's schema defaults, but never
    overrides a value the caller actually supplied -- it only clamps to
    the documented min/max so an out-of-range input can't produce
    nonsensical output instead of silently failing."""
    df = _clean_df(df)
    if df is None or df.empty:
        return {"value": None, "signal": "ERROR", "error": "No OHLCV data available for this request."}
    return evaluate_configurable(indicator_name, df, params)


# --------------------------------------------------------------------------- #
# Custom formula (MVP scripting path)
# --------------------------------------------------------------------------- #
_ALLOWED_AST_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd,
    ast.Call, ast.Attribute, ast.Compare, ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq,
    ast.Index, ast.Slice, ast.Subscript, ast.keyword,
)
_ALLOWED_NAMES = {"Open", "High", "Low", "Close", "Volume", "abs", "min", "max"}


def _validate_formula_ast(formula: str) -> ast.AST:
    tree = ast.parse(formula, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_AST_NODES):
            raise ValueError(f"Disallowed expression element: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            raise ValueError(f"Unknown identifier: {node.id!r}. Allowed: {sorted(_ALLOWED_NAMES)}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("Access to private/dunder attributes is not allowed.")
    return tree


def run_custom_formula(df: pd.DataFrame, formula: str) -> Dict[str, Any]:
    """Powers POST /indicators/custom.

    Deliberately NOT a bare eval(): only a whitelisted AST (arithmetic,
    comparisons, and attribute/method calls on Open/High/Low/Close/Volume
    -- e.g. `Close.rolling(20).mean()`) is permitted, and builtins are
    stripped from the eval environment. This is an internal scripting MVP,
    not a hardened public sandbox -- it is not meant to accept untrusted
    input from outside the user's own app.
    """
    df = _clean_df(df)
    if df is None or df.empty:
        return {"value": None, "signal": "ERROR", "error": "No OHLCV data available for this request.", "formula": formula}

    local_vars: Dict[str, Any] = {
        "Open": df["Open"], "High": df["High"], "Low": df["Low"],
        "Close": df["Close"], "Volume": df["Volume"],
        "abs": abs, "min": min, "max": max,
    }
    try:
        tree = _validate_formula_ast(formula)
        result = eval(compile(tree, "<custom_formula>", "eval"), {"__builtins__": {}}, local_vars)  # noqa: S307
        if isinstance(result, pd.Series):
            val = last_float(result)
        elif isinstance(result, (int, float, np.floating, np.integer)):
            val = float(result)
        else:
            raise ValueError(f"Formula must evaluate to a number or a Series, got {type(result).__name__}")
        return sanitize_json({"value": round(val, 6), "signal": "NEUTRAL", "formula": formula})
    except Exception as exc:
        logger.warning("Custom formula rejected/failed: %r -> %s", formula, exc)
        return {"value": None, "signal": "ERROR", "error": f"{type(exc).__name__}: {exc}", "formula": formula}