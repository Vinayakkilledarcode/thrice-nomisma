# routers/plane_overlap.py
#
# API surface for services/plane_overlap_engine.py -- the backend
# counterpart to the frontend's analysisEngine.ts / PlaneOverlayAnalysis.tsx
# / IndicatorWaveform3D.tsx. Runs a real indicator sweep for the active
# symbol, buckets it into the same (category, slot, score) points the
# frontend already builds in TabbedPanel, and reduces it through
# compute_composite_analysis() so the exact same overlap math is available
# server-side -- one canonical engine instead of trusting the in-browser
# JS reimplementation to never drift from it.
#
# INDICATOR_POSITIVE_SIGNALS / INDICATOR_NEGATIVE_SIGNALS below are a
# deliberate mirror of the identically-named sets in TabbedPanel.tsx. They
# have to live in two places (JS runs in the browser, this runs in the
# backend) -- if you add a new signal label to one, add it to the other,
# or the frontend's 3D view and this endpoint's composite score will
# disagree on which indicators are bullish/bearish.
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from services import market, indicator_engine
from services.plane_overlap_engine import (
    CompositeAnalysis,
    compute_composite_analysis,
    points_from_indicator_results,
)
from .indicators import _resolve_active_symbol  # reuse the canonical active-symbol resolution

router = APIRouter(prefix="/api", tags=["plane-overlap"])

INDICATOR_POSITIVE_SIGNALS = {"BULLISH", "OVERSOLD", "GAP_UP", "NEAR_LOW", "BROKE_STRUCTURE_UP"}
INDICATOR_NEGATIVE_SIGNALS = {"BEARISH", "OVERBOUGHT", "GAP_DOWN", "NEAR_HIGH", "BROKE_STRUCTURE_DOWN"}


def _category_order_and_labels():
    """Pulls the live category order straight from the indicator registry
    (indicator_engine.get_categories(), the same source list_available()
    already exposes to GET /indicators/list) instead of hardcoding a second
    copy here that could silently fall out of sync as new indicator
    categories are added upstream."""
    categories = list(indicator_engine.get_categories())
    labels = {cat: cat.replace("_", " ").title() for cat in categories}
    return categories, labels


@router.get("/active/plane-overlap", response_model=CompositeAnalysis)
async def get_active_plane_overlap(
    period: str = Query(default="6mo"),
    interval: str = Query(default="1d"),
    symbol: Optional[str] = Query(default=None),
):
    """Composite plane-overlap analysis for the active symbol: every
    category's indicators reduced to one composite score, a weighted
    dispersion/agreement read, a Wilson confidence interval on the bullish
    share, Shannon entropy over the category verdicts, and the literal
    slot-by-slot points where two category planes cross -- same engine the
    Composite Plane Analysis panel runs client-side, computed here from a
    fresh server-side indicator sweep so it can be trusted as ground truth
    (e.g. for a strategy file, a scheduled scan, or a non-browser client)."""
    full_symbol = await _resolve_active_symbol(symbol)
    df = await market.get_ohlcv_dataframe(full_symbol, period, interval)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No OHLCV data available for {full_symbol} ({period}/{interval})")

    run_result = indicator_engine.run(df)
    all_indicators = run_result["indicators"]

    category_order, category_labels = _category_order_and_labels()
    points, labels = points_from_indicator_results(
        all_indicators, category_order, category_labels,
        INDICATOR_POSITIVE_SIGNALS, INDICATOR_NEGATIVE_SIGNALS,
    )
    return compute_composite_analysis(points, labels)


@router.get("/indicators/{symbol}/plane-overlap", response_model=CompositeAnalysis)
async def get_symbol_plane_overlap(
    symbol: str,
    period: str = Query(default="6mo"),
    interval: str = Query(default="1d"),
):
    """Same composite analysis as above, but for an explicit symbol rather
    than whatever is currently active -- mirrors GET /indicators/{symbol}
    vs GET /active/indicators."""
    df = await market.get_ohlcv_dataframe(f"{symbol}", period, interval)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No OHLCV data available for {symbol} ({period}/{interval})")

    run_result = indicator_engine.run(df)
    all_indicators = run_result["indicators"]

    category_order, category_labels = _category_order_and_labels()
    points, labels = points_from_indicator_results(
        all_indicators, category_order, category_labels,
        INDICATOR_POSITIVE_SIGNALS, INDICATOR_NEGATIVE_SIGNALS,
    )
    return compute_composite_analysis(points, labels)
