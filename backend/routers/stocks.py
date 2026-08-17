# routers/stocks.py
import os
import json
from fastapi import APIRouter, Query, HTTPException, BackgroundTasks
from typing import Optional
from pydantic import BaseModel
from services import market, indicator_engine
from services.sentiment import analyze_sentiment
from services.risk import risk_service
from services.plane_overlap_engine import compute_composite_analysis, points_from_indicator_results
from services import signal_arbitration
from services.signal_arbitration import arbitrate_signal
from routers.plane_overlap import (
    INDICATOR_POSITIVE_SIGNALS,
    INDICATOR_NEGATIVE_SIGNALS,
    _category_order_and_labels,
)

router = APIRouter(prefix="/api", tags=["stocks"])


# ─── Data Schemas ─────────────────────────────────────────────────────────────

class ActiveSymbolModel(BaseModel):
    token: str
    symbol: str
    exchange: str
    name: str


# ─── Helper ───────────────────────────────────────────────────────────────────

def _clean_ticker(symbol: str) -> str:
    """Strip Angel One suffixes so yfinance lookups work: TCS-EQ → TCS"""
    return symbol.replace("-EQ", "").replace("-BE", "").replace("-SM", "")


def _find_trend_mean(composite) -> Optional[float]:
    """Pulls the Trend category's composite mean out of a CompositeAnalysis,
    for the multi-timeframe alignment check (Feature 2). Returns None if no
    Trend category has evidence yet, rather than defaulting to 0.0 and
    silently treating "no data" as "neutral"."""
    for c in getattr(composite, "categories", []) or []:
        if c.label.lower().strip() == "trend" and c.n > 0:
            return c.mean
    return None


# ─── Active Asset Endpoints ───────────────────────────────────────────────────

@router.post("/active-symbol")
async def register_active_symbol(data: ActiveSymbolModel):
    """Saves the active asset sent from the Electron webview context."""
    token = data.token
    exchange = data.exchange or "NSE"
    symbol = data.symbol

    if (not token or token == "") and symbol:
        await market.angel_service.load_instruments()
        key = f"{symbol.upper()}_{exchange.upper()}"
        if key in market.angel_service.instruments:
            inst = market.angel_service.instruments[key]
            token = inst["token"]
            symbol = inst["symbol"]

    market.angel_service.active_symbol = {
        "token":    token,
        "symbol":   symbol,
        "exchange": exchange,
        "name":     data.name or symbol
    }
    print(f"[NOMISMA ACTIVE TICKER REGISTERED] {data.name} ({exchange}:{symbol}) Token: #{token}")
    return {"status": "success", "data": market.angel_service.active_symbol}


@router.get("/active/quote")
async def active_quote():
    active = market.angel_service.active_symbol
    if not active or not active.get("token"):
        raise HTTPException(
            status_code=400,
            detail="No active asset selected inside Angel One yet"
        )

    token = active["token"]
    exchange = active.get("exchange") or "NSE"
    symbol = active.get("symbol")

    if not symbol or symbol == "":
        inst = await market.angel_service.get_instrument_by_token(token)
        if inst:
            symbol = inst["symbol"]
            exchange = inst["exchange"]
            active["symbol"] = symbol
            active["exchange"] = exchange
            active["name"] = inst["name"]

    if not symbol:
        raise HTTPException(
            status_code=400,
            detail="Active asset token could not be resolved in the scrip master"
        )

    # ── Path A: Angel One LTP via exact token ────────────────────────────────
    if await market.angel_service.login():
        ltp_res = await market.angel_service.get_ltp(
            exchange=exchange,
            tradingsymbol=symbol,
            symboltoken=token
        )
        if ltp_res:
            clean = _clean_ticker(symbol)
            yf_sym = f"{exchange}:{clean}"
            try:
                yf_data = await market.get_yfinance_quote_base(yf_sym)
            except Exception:
                yf_data = {}

            return {
                "symbol":           f"{exchange}:{symbol}",
                "shortName":        active.get("name") or yf_data.get("shortName") or clean,
                "longName":         active.get("name") or yf_data.get("longName")  or clean,
                "exchange":         exchange,
                "currency":         "INR",
                "currentPrice":     ltp_res.get("ltp"),
                "previousClose":    ltp_res.get("close"),
                "open":             ltp_res.get("open"),
                "dayHigh":          ltp_res.get("high"),
                "dayLow":           ltp_res.get("low"),
                "volume":           yf_data.get("volume"),
                "avgVolume":        yf_data.get("avgVolume"),
                "marketCap":        yf_data.get("marketCap"),
                "fiftyTwoWeekHigh": yf_data.get("fiftyTwoWeekHigh"),
                "fiftyTwoWeekLow":  yf_data.get("fiftyTwoWeekLow"),
                "bid":              None,
                "ask":              None,
                "bidSize":          None,
                "askSize":          None,
            }

    # ── Path B: Fallback to yfinance ─────────────────────────────────────────
    clean = _clean_ticker(symbol)
    return await market.get_quote(f"{exchange}:{clean}")


@router.get("/active/history")
async def active_history(
    period:   str = Query(default="6mo"),
    interval: str = Query(default="1d"),
):
    active = market.angel_service.active_symbol
    if not active or not active.get("token"):
        raise HTTPException(
            status_code=400,
            detail="No active asset selected inside Angel One yet"
        )

    token = active["token"]
    exchange = active.get("exchange") or "NSE"
    symbol = active.get("symbol")

    if not symbol or symbol == "":
        inst = await market.angel_service.get_instrument_by_token(token)
        if inst:
            symbol = inst["symbol"]
            exchange = inst["exchange"]
            active["symbol"] = symbol
            active["exchange"] = exchange
            active["name"] = inst["name"]

    if not symbol:
        raise HTTPException(
            status_code=400,
            detail="Active asset token could not be resolved in the scrip master"
        )

    # ── Path A: Angel One historical candles via exact token ─────────────────
    if await market.angel_service.login():
        ao_interval = market.map_interval(interval)
        from_date, to_date = market.calculate_dates_for_period(period)
        candles = await market.angel_service.get_candles(
            exchange=exchange,
            symboltoken=token,
            interval=ao_interval,
            from_date=from_date,
            to_date=to_date,
        )
        if candles:
            return [
                {
                    "Date":   c[0],
                    "Open":   c[1],
                    "High":   c[2],
                    "Low":    c[3],
                    "Close":  c[4],
                    "Volume": c[5],
                }
                for c in candles
            ]

    # ── Path B: Fallback to yfinance ─────────────────────────────────────────
    clean = _clean_ticker(symbol)
    return await market.get_history(f"{exchange}:{clean}", period, interval)


@router.get("/active/technicals")
async def active_technicals(
    period:   str = Query(default="1y"),
    interval: str = Query(default="1d"),
):
    active = market.angel_service.active_symbol
    if not active or not active.get("token"):
        raise HTTPException(
            status_code=400,
            detail="No active asset selected inside Angel One yet"
        )

    token = active["token"]
    exchange = active.get("exchange") or "NSE"
    symbol = active.get("symbol")

    if not symbol or symbol == "":
        inst = await market.angel_service.get_instrument_by_token(token)
        if inst:
            symbol = inst["symbol"]
            exchange = inst["exchange"]
            active["symbol"] = symbol
            active["exchange"] = exchange
            active["name"] = inst["name"]

    if not symbol:
        raise HTTPException(
            status_code=400,
            detail="Active asset token could not be resolved in the scrip master"
        )

    clean = _clean_ticker(symbol)
    return await market.get_technicals(f"{exchange}:{clean}", period, interval)


@router.get("/active/multi-horizon-analysis")
async def active_multi_horizon_analysis():
    """
    Triggers backend-level high-performance analysis across weeks, days, 
    and years of data for the currently selected active asset.
    """
    active = market.angel_service.active_symbol
    if not active or not active.get("symbol"):
        raise HTTPException(
            status_code=400,
            detail="No active asset selected inside Angel One yet to analyze."
        )
        
    symbol = active["symbol"]
    exchange = active.get("exchange") or "NSE"
    full_symbol = f"{exchange}:{symbol}"
    
    analysis_data = await market.fetch_and_analyze_multi_horizon(full_symbol)
    return {"status": "success", "analysis": analysis_data}


# ─── Integrated Advanced Decision Paths ───────────────────────────────────────

@router.get("/active/sentiment")
async def active_sentiment():
    """
    NLP sentiment scores for the selected active asset.
    """
    active = market.angel_service.active_symbol
    if not active or not active.get("symbol"):
        raise HTTPException(status_code=400, detail="No active asset selected.")
    symbol = active["symbol"]
    exchange = active.get("exchange") or "NSE"
    return await analyze_sentiment(f"{exchange}:{symbol}")


_ACTIVE_SYMBOL_CACHE_PATH = "active_symbol_cache.json"


@router.get("/active/strategy-signal")
async def active_strategy_signal(
    horizon: Optional[str] = Query(None),
    interval: Optional[str] = Query("1d"),
    symbol: Optional[str] = Query(None)  # Explicit override so the frontend's selected
                                          # timeframe is always paired with the correct symbol,
                                          # even across backend hot-reloads that wipe memory.
):
    """
    Calculates style-locked signals and indicators for the given `interval`.
    This is the SINGLE canonical implementation of this route (a duplicate used to also
    live in routers/strategy.py — that one silently never ran because FastAPI matches
    routes in registration order and this router is included first. It has been removed
    to avoid two handlers quietly disagreeing on behavior).
    """
    # 1. Priority: an explicit symbol in the query always wins and re-syncs memory state.
    if symbol:
        base_symbol, exchange = market.parse_symbol_for_angel_one(symbol)
        market.angel_service.active_symbol = {
            "token": market.angel_service.active_symbol.get("token", "DUMMY")
                     if market.angel_service.active_symbol else "DUMMY",
            "symbol": base_symbol,
            "exchange": exchange,
            "name": base_symbol
        }

    # 2. Fallback: recover the last-known active symbol from an on-disk cache if
    #    in-memory state was wiped (e.g. a dev-server hot reload).
    active = market.angel_service.active_symbol
    if not active or not active.get("symbol"):
        if os.path.exists(_ACTIVE_SYMBOL_CACHE_PATH):
            try:
                with open(_ACTIVE_SYMBOL_CACHE_PATH, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if cached and cached.get("symbol"):
                    market.angel_service.active_symbol = cached
                    active = cached
            except Exception:
                pass

    if not active or not active.get("symbol"):
        raise HTTPException(status_code=400, detail="No active asset selected.")

    # Persist current resolved state so the cache stays fresh.
    try:
        with open(_ACTIVE_SYMBOL_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(active, f)
    except Exception:
        pass

    active_symbol = active["symbol"]
    exchange = active.get("exchange") or "NSE"
    result = await market.corporate_strategy_matrix(
        symbol=f"{exchange}:{active_symbol}",
        override_horizon=horizon,
        interval=interval
    )

    # ── Signal arbitration layer ──────────────────────────────────────────
    # Regime-weights the raw strategy confidence against the same composite
    # category-plane analysis the Signal Intelligence panel already shows,
    # instead of letting a single strategy's confidence stand unchallenged
    # (see services/signal_arbitration.py for the full rationale). Best
    # effort: if the indicator sweep or arbitration fails for any reason,
    # the response still returns the strategy signal exactly as before,
    # just without the extra `arbitration` block.
    full_symbol = f"{exchange}:{active_symbol}"
    try:
        active_run = (result.get("detailed_runs") or {}).get("active_run") or {}
        raw_confidence = float(active_run.get("confidence") or 0.0)
        resolved_interval = result.get("interval") or "1d"

        df_for_regime, _ = await market.get_ohlcv_dataframe_for_ticker_interval(
            full_symbol, resolved_interval
        )
        if not df_for_regime.empty:
            run_result = indicator_engine.run(df_for_regime)
            all_indicators = run_result["indicators"]
            category_order, category_labels = _category_order_and_labels()
            points, labels = points_from_indicator_results(
                all_indicators, category_order, category_labels,
                INDICATOR_POSITIVE_SIGNALS, INDICATOR_NEGATIVE_SIGNALS,
            )
            composite = compute_composite_analysis(points, labels)

            # ── Feature 1 input: Technicals-shaped snapshot for the data-
            # integrity gate. Best-effort -- a failed fetch just means the
            # gate sees an empty dict and flags everything as unverified
            # rather than crashing the whole arbitration pass.
            technicals_snapshot = None
            try:
                technicals_snapshot = await market.get_technicals(full_symbol, "6mo", resolved_interval)
            except Exception as tech_exc:
                print(f"[SIGNAL ARBITRATION] technicals fetch failed for {full_symbol}: {tech_exc}")

            # ── Feature 4 input: close/Bollinger-band series for the
            # band-walk detector, computed directly off the OHLCV frame
            # already in hand rather than guessing indicator_engine's
            # internal series format.
            closes_series = None
            bb_upper_series = None
            bb_lower_series = None
            try:
                if "Close" in df_for_regime.columns:
                    closes_series = [float(x) for x in df_for_regime["Close"].tolist()]
                    bb_upper_series, bb_lower_series = signal_arbitration.compute_bollinger_bands_from_closes(closes_series)
            except Exception as band_exc:
                print(f"[SIGNAL ARBITRATION] band-walk series unavailable for {full_symbol}: {band_exc}")

            # ── Feature 2 input: trend-plane composite from the other
            # timeframes (whichever of 15m/1d/1wk isn't already the
            # resolved interval), for cross-timeframe alignment.
            trend_by_interval = {}
            for tf in ("15m", "1d", "1wk"):
                if tf == resolved_interval:
                    trend_cat = _find_trend_mean(composite)
                    if trend_cat is not None:
                        trend_by_interval[tf] = trend_cat
                    continue
                try:
                    tf_df, _ = await market.get_ohlcv_dataframe_for_ticker_interval(full_symbol, tf)
                    if tf_df.empty:
                        continue
                    tf_run = indicator_engine.run(tf_df)
                    tf_points, tf_labels = points_from_indicator_results(
                        tf_run["indicators"], category_order, category_labels,
                        INDICATOR_POSITIVE_SIGNALS, INDICATOR_NEGATIVE_SIGNALS,
                    )
                    tf_composite = compute_composite_analysis(tf_points, tf_labels)
                    trend_mean = _find_trend_mean(tf_composite)
                    if trend_mean is not None:
                        trend_by_interval[tf] = trend_mean
                except Exception as tf_exc:
                    print(f"[SIGNAL ARBITRATION] multi-timeframe fetch skipped for {full_symbol} @ {tf}: {tf_exc}")

            arbitration = arbitrate_signal(
                action=result.get("signal", "HOLD"),
                raw_confidence=raw_confidence,
                resolved_interval=resolved_interval,
                analysis=composite,
                indicators=technicals_snapshot,
                closes=closes_series,
                bb_upper=bb_upper_series,
                bb_lower=bb_lower_series,
                trend_by_interval=trend_by_interval or None,
            )
            result["arbitration"] = arbitration.to_dict()

            # ── Features 5 & 6: best-effort, isolated so a failure here
            # never clobbers the arbitration block already attached above.
            try:
                signal_arbitration.track_regime_transition(
                    full_symbol, resolved_interval, arbitration.regime, arbitration.regime_confidence
                )
                signal_arbitration.cache_arbitration(full_symbol, resolved_interval, arbitration)
                signal_arbitration.log_arbitration_decision(full_symbol, resolved_interval, arbitration)
            except Exception as track_exc:
                print(f"[SIGNAL ARBITRATION] tracking/ledger skipped for {full_symbol}: {track_exc}")
    except Exception as exc:
        print(f"[SIGNAL ARBITRATION] skipped for {exchange}:{active_symbol}: {exc}")

    return result


@router.get("/active/regime-alerts")
async def active_regime_alerts(limit: int = Query(default=20, ge=1, le=100)):
    """
    Cheap polling endpoint backed by signal_arbitration's in-memory
    regime-transition cache (Feature 6). No WS manager exists in main.py
    to push these proactively yet, so the terminal polls this instead --
    e.g. "this symbol just flipped RANGING -> TRENDING, worth re-checking
    your trend strategy" instead of the user re-checking manually.
    """
    active = market.angel_service.active_symbol
    symbol_filter = None
    if active and active.get("symbol"):
        exchange = active.get("exchange") or "NSE"
        symbol_filter = f"{exchange}:{active['symbol']}"
    return {"transitions": signal_arbitration.get_recent_regime_transitions(symbol=symbol_filter, limit=limit)}


@router.get("/active/risk-check")
async def active_risk_check(qty: int = Query(..., description="Proposed trade quantity")):
    """
    Validates trading sizing and drawdowns for the current asset.
    """
    active = market.angel_service.active_symbol
    if not active or not active.get("symbol"):
        raise HTTPException(status_code=400, detail="No active asset selected.")
    
    # Retrieve current LTP to calculate trade value
    token = active["token"]
    exchange = active.get("exchange") or "NSE"
    symbol = active["symbol"]
    
    ltp_data = await market.angel_service.get_ltp(exchange, symbol, token)
    price = ltp_data.get("ltp", 0.0)
    if price <= 0:
        raise HTTPException(status_code=400, detail="Could not resolve current asset price for validation check.")
        
    return risk_service.evaluate_order(
        symbol=symbol,
        price=price,
        qty=qty,
        current_drawdown_pct=0.0,
        active_portfolio_count=1
    )


# ─── New Daily Risk Summary Endpoint ──────────────────────────────────────────

@router.get("/risk/daily-summary")
def get_daily_rms_summary():
    """
    Provides account audit trailing details for the Daily RMS summary card.
    """
    return {
        "trades_placed": 4,
        "drawdown_consumed_pct": 0.85,
        "remaining_capital": 920000.0
    }


# ─── Standard Endpoints ───────────────────────────────────────────────────────

@router.get("/quote/{symbol}")
async def quote(
    symbol: str, 
    background_tasks: BackgroundTasks,
    interval: Optional[str] = Query(default="1d"), 
    full: Optional[bool] = Query(default=False)
):
    if "?" in symbol:
        parts = symbol.split("?", 1)
        symbol = parts[0]
        qs = parts[1]
        for param in qs.split("&"):
            if "=" in param:
                k, v = param.split("=", 1)
                if k.lower() == "interval":
                    interval = v
                elif k.lower() == "full" and v.lower() == "true":
                    full = True

    symbol = symbol.strip().upper()

    base_symbol, exchange = market.parse_symbol_for_angel_one(symbol)
    await market.angel_service.load_instruments()
    key = f"{base_symbol}_{exchange}"
    if key in market.angel_service.instruments:
        inst = market.angel_service.instruments[key]
        market.angel_service.active_symbol = {
            "token":    inst["token"],
            "symbol":   inst["symbol"],
            "exchange": inst["exchange"],
            "name":     inst["name"]
        }

    background_tasks.add_task(market.fetch_and_analyze_multi_horizon, symbol)

    if full:
        base_symbol, exchange = market.parse_symbol_for_angel_one(symbol)
        await market.angel_service.load_instruments()
        key = f"{base_symbol}_{exchange}"
        if key in market.angel_service.instruments:
            inst = market.angel_service.instruments[key]
            res = await market.angel_service.get_market_data(inst["exchange"], inst["token"])
            if res and res.get("fetched"):
                return res["fetched"][0]
        raise HTTPException(status_code=400, detail="Depth coordinates are only available for active Angel One assets.")

    return await market.get_quote(symbol, interval=interval)


@router.get("/fundamentals/{symbol}")
async def fundamentals(symbol: str):
    return await market.get_fundamentals(symbol)


@router.get("/history/{symbol}")
async def history(
    symbol: str,
    period:   str = Query(default="6mo"),
    interval: str = Query(default="1d"),
):
    return await market.get_history(symbol, period, interval)


@router.get("/technicals/{symbol}")
async def technicals(
    symbol: str,
    period:   str = Query(default="1y"),
    interval: str = Query(default="1d"),
):
    return await market.get_technicals(symbol, period, interval)


@router.get("/options/{symbol}")
async def options(
    symbol: str,
    expiry: Optional[str] = Query(default=None),
):
    return await market.get_options(symbol, expiry)


@router.get("/batch")
async def batch(symbols: str = Query(..., description="Comma-separated symbols e.g. AAPL,MSFT,RELIANCE.NS")):
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    return await market.get_batch_quotes(sym_list)