# routers/indicators.py
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services import market
from services import indicator_engine

router = APIRouter(prefix="/api", tags=["indicators"])


def _parse_list(csv: Optional[str]):
    if not csv:
        return None
    return [x.strip() for x in csv.split(",") if x.strip()]


def _clean_ticker(symbol: str) -> str:
    return symbol.replace("-EQ", "").replace("-BE", "").replace("-SM", "")


async def _resolve_active_symbol(symbol_override: Optional[str] = None) -> str:
    """
    Same resolution flow as stocks.py's active_strategy_signal: an explicit
    `symbol` query param always wins and re-syncs in-memory active-symbol
    state, so the frontend's selected symbol is never mismatched against
    the backend's memory (e.g. after a dev-server hot reload wipes it).
    Falls back to whatever's currently registered as the active symbol.
    """
    if symbol_override:
        base_symbol, exchange = market.parse_symbol_for_angel_one(symbol_override)
        market.angel_service.active_symbol = {
            "token": market.angel_service.active_symbol.get("token", "DUMMY")
                     if market.angel_service.active_symbol else "DUMMY",
            "symbol": base_symbol,
            "exchange": exchange,
            "name": base_symbol,
        }

    active = market.angel_service.active_symbol
    if not active or not active.get("symbol"):
        raise HTTPException(status_code=400, detail="No active asset selected inside Angel One yet.")
    symbol = active["symbol"]
    exchange = active.get("exchange") or "NSE"
    clean = _clean_ticker(symbol)
    return f"{exchange}:{clean}"


# ─── Catalogue ────────────────────────────────────────────────────────────────

@router.get("/indicators/list")
def list_indicators(category: Optional[str] = Query(default=None)):
    """Returns every registered indicator name + description, grouped by category."""
    return indicator_engine.list_available(category)


@router.get("/indicators/configurable/schema")
def get_configurable_schema():
    """Which indicators support adjustable settings, and their default params
    (used by the frontend to build the settings panel per indicator)."""
    return indicator_engine.configurable_schema()


# ─── Single-symbol, single-timeframe ──────────────────────────────────────────

@router.get("/indicators/{symbol}")
async def get_indicators(
    symbol: str,
    period: str = Query(default="6mo"),
    interval: str = Query(default="1d"),
    categories: Optional[str] = Query(default=None, description="Comma-separated, e.g. trend,momentum"),
    names: Optional[str] = Query(default=None, description="Comma-separated exact indicator names"),
):
    df = await market.get_ohlcv_dataframe(symbol, period, interval)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No OHLCV data available for {symbol} ({period}/{interval})")
    return indicator_engine.run(df, categories=_parse_list(categories), names=_parse_list(names))


@router.get("/active/indicators")
async def get_active_indicators(
    period: str = Query(default="6mo"),
    interval: str = Query(default="1d"),
    categories: Optional[str] = Query(default=None),
    names: Optional[str] = Query(default=None),
    symbol: Optional[str] = Query(default=None),
):
    full_symbol = await _resolve_active_symbol(symbol)
    df = await market.get_ohlcv_dataframe(full_symbol, period, interval)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No OHLCV data available for {full_symbol} ({period}/{interval})")
    return indicator_engine.run(df, categories=_parse_list(categories), names=_parse_list(names))


# ─── Single exact Ticker timeframe (ALL 20 intervals, not just the 3 buckets) ──
#
# The 3-bucket multi-timeframe endpoint below only ever computes 15m/1d/1wk
# because get_raw_dataframes() is hard-coded to that trio. This endpoint
# instead accepts ANY of Ticker's exact intervals (15s...1mo) via
# market.get_ohlcv_dataframe_for_ticker_interval(), which maps + resamples
# each one to real candle data (see that function's docstring for the
# 15s/30s/45s caveat). This is what the Indicators tab now calls per
# individually-selected/locked timeframe, mirroring how the Strategy tab
# already runs a distinct model per exact interval.

@router.get("/active/indicators/by-interval")
async def get_active_indicators_by_interval(
    interval: str = Query(default="1d", description="Exact Ticker timeframe, e.g. 15s, 5m, 75m, 4h, 1w, 1mo"),
    categories: Optional[str] = Query(default=None),
    names: Optional[str] = Query(default=None),
    symbol: Optional[str] = Query(default=None),
):
    full_symbol = await _resolve_active_symbol(symbol)
    df, is_approximated = await market.get_ohlcv_dataframe_for_ticker_interval(full_symbol, interval)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No OHLCV data available for {full_symbol} at {interval}")
    result = indicator_engine.run(df, categories=_parse_list(categories), names=_parse_list(names))
    result["resolved_interval"] = interval
    result["is_approximated"] = is_approximated
    return result


# ─── Multi-timeframe (spec section 14: same indicator across 15m/1d/1wk at once) ─

@router.get("/active/indicators/multi-timeframe")
async def get_active_indicators_multi_timeframe(
    categories: Optional[str] = Query(default=None),
    names: Optional[str] = Query(default=None),
    symbol: Optional[str] = Query(default=None),
):
    full_symbol = await _resolve_active_symbol(symbol)
    df_intra, df_daily, df_weekly = await market.get_raw_dataframes(full_symbol)
    dfs = {"15m": df_intra, "1d": df_daily, "1wk": df_weekly}
    dfs = {label: df for label, df in dfs.items() if not df.empty}
    if not dfs:
        raise HTTPException(status_code=404, detail=f"No OHLCV data available for {full_symbol} on any timeframe")
    return indicator_engine.run_multi_timeframe(dfs, categories=_parse_list(categories), names=_parse_list(names))


# ─── Custom formula indicators (MVP scripting path) ───────────────────────────

class CustomIndicatorRequest(BaseModel):
    symbol: str
    formula: str
    period: str = "6mo"
    interval: str = "1d"


@router.post("/indicators/custom")
async def evaluate_custom_indicator(payload: CustomIndicatorRequest):
    df = await market.get_ohlcv_dataframe(payload.symbol, payload.period, payload.interval)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No OHLCV data available for {payload.symbol}")
    return indicator_engine.run_custom_formula(df, payload.formula)


class ConfigurableIndicatorRequest(BaseModel):
    symbol: str
    indicator: str
    params: dict = {}
    period: str = "6mo"
    interval: str = "1d"
    # Optional: when set, this exact Ticker interval (e.g. "75m", "4h", "1w")
    # is resolved via get_ohlcv_dataframe_for_ticker_interval() instead of the
    # raw period/interval pair above, so a custom-settings recompute stays on
    # the same individually-locked timeframe the rest of the tab is showing.
    ticker_interval: Optional[str] = None


@router.post("/indicators/configurable")
async def evaluate_configurable_indicator(payload: ConfigurableIndicatorRequest):
    """Recomputes ONE indicator (sma/ema/wma/hma/atr/rsi/cci/williams_r/macd/
    bollinger/stochastic) at custom settings -- the adjustable-settings path.
    See GET /indicators/configurable/schema for valid `indicator` values and
    their default params."""
    if payload.ticker_interval:
        df, _ = await market.get_ohlcv_dataframe_for_ticker_interval(payload.symbol, payload.ticker_interval)
    else:
        df = await market.get_ohlcv_dataframe(payload.symbol, payload.period, payload.interval)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No OHLCV data available for {payload.symbol}")
    return indicator_engine.run_configurable(df, payload.indicator, payload.params)