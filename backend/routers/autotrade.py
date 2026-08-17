# backend/routers/autotrade.py
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from services.auto_trader import auto_trader

router = APIRouter(prefix="/api/autotrade", tags=["autotrade"])


class WatchlistItem(BaseModel):
    symbol: str
    exchange: str = "NSE"


class ConfigPatch(BaseModel):
    enabled: Optional[bool] = None
    watchlist: Optional[List[WatchlistItem]] = None
    confidence_threshold: Optional[float] = None
    base_qty_per_trade: Optional[int] = None
    max_concurrent_positions: Optional[int] = None
    poll_interval_seconds: Optional[int] = None
    sl_pct: Optional[float] = None
    target_pct: Optional[float] = None
    square_off_time: Optional[str] = None
    horizon: Optional[str] = None
    interval: Optional[str] = None
    auth_token: Optional[str] = None


@router.get("/status")
def get_status():
    """Current config, open positions, and recent closed-trade history."""
    return auto_trader.status()


@router.post("/config")
def patch_config(payload: ConfigPatch):
    """Partial update -- only fields you send are changed. Persisted to
    autotrade_config.json so it survives a backend restart."""
    patch = {k: v for k, v in payload.dict(exclude_unset=True).items() if v is not None}
    if payload.watchlist is not None:
        patch["watchlist"] = [item.dict() for item in payload.watchlist]
    auto_trader.update_config(patch)
    return {"status": "success", "config": auto_trader.config}


@router.post("/enable")
def enable():
    """Turns on new-trade entries. Does nothing to positions already open."""
    auto_trader.update_config({"enabled": True})
    return {"status": "success", "enabled": True}


@router.post("/disable")
def disable():
    """Stops NEW entries immediately. Existing open positions are still
    watched and squared off on SL/target/EOD -- this is a 'stop opening
    new risk' switch, not an emergency flatten-everything button."""
    auto_trader.update_config({"enabled": False})
    return {"status": "success", "enabled": False}


@router.post("/force-square-off/{exchange}/{symbol}")
async def force_square_off(exchange: str, symbol: str):
    """Emergency manual exit for one open autotrade position, right now,
    regardless of SL/target/time."""
    symbol_key = f"{exchange}:{symbol}"
    if symbol_key not in auto_trader.positions:
        return {"status": "noop", "message": f"No open autotrade position for {symbol_key}"}
    await auto_trader._square_off(symbol_key, "manual_override")
    return {"status": "success", "message": f"Square-off submitted for {symbol_key}"}
