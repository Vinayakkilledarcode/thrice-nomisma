# routers/orders.py
import math
import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from services.market import angel_service
from services.risk import risk_service
from services import signal_arbitration

router = APIRouter(prefix="/api", tags=["orders"])

class OrderPayload(BaseModel):
    symbol: str
    token: str
    exchange: str
    qty: int
    order_type: str  # "MARKET" or "LIMIT"
    price: float = 0.0
    transaction_type: str  # "BUY" or "SELL"


# ─── Feature 3: position-size wiring ────────────────────────────────────────
#
# adjusted_confidence and conflict_pct previously just sat in the strategy
# API response as read-only numbers a human had to notice. This reads the
# most recent arbitration result cached for the symbol (see
# services/signal_arbitration.py's cache_arbitration/get_cached_arbitration,
# written by routers/stocks.py's active_strategy_signal) and uses its
# position_size_multiplier to cap what a low-confidence, regime-conflicted
# signal is allowed to size into -- automatically, not just as a UI warning.
#
# Deliberately conservative: if no cached arbitration exists for this symbol
# (e.g. the strategy panel was never opened, or the cache is stale/>15min
# old), this is a complete no-op and behavior is identical to before this
# feature existed.
def _apply_confidence_sizing(check_result: dict, symbol_key: str, requested_qty: int) -> dict:
    try:
        cached = signal_arbitration.get_cached_arbitration(symbol_key)
        if not cached:
            return check_result

        multiplier = cached.get("position_size_multiplier", 1.0)
        base_max = check_result.get("max_qty_allowed", requested_qty) or requested_qty
        confidence_capped_max = max(0, math.floor(base_max * multiplier))

        check_result["signal_confidence_multiplier"] = multiplier
        check_result["signal_regime"] = cached.get("regime")
        check_result["confidence_adjusted_max_qty"] = confidence_capped_max

        if check_result.get("cleared") and multiplier < 1.0 and requested_qty > confidence_capped_max:
            check_result["cleared"] = False
            check_result["breach_reason"] = (
                f"Position-size gate: regime-adjusted signal confidence is "
                f"{round(multiplier * 100)}% of raw (regime: {cached.get('regime')}), capping this trade at "
                f"{confidence_capped_max} share(s), but {requested_qty} were requested."
            )
            check_result["max_qty_allowed"] = confidence_capped_max
    except Exception as exc:
        print(f"[POSITION SIZING] confidence-sizing skipped for {symbol_key}: {exc}")
    return check_result


@router.get("/active/risk-check")
async def run_pretrade_risk_check(
    qty: int = Query(..., description="Proposed transaction quantity"),
    price: float = Query(..., description="Last traded price / limit price")
):
    """
    Runs pre-trade risk validations for an order.
    """
    active = angel_service.active_symbol
    if not active or not active.get("symbol"):
        raise HTTPException(
            status_code=400,
            detail="No active asset has been selected yet."
        )

    # In a production environment, current drawdown and position counts
    # would be retrieved from active account ledger databases.
    check_result = risk_service.evaluate_order(
        symbol=active["symbol"],
        price=price,
        qty=qty,
        current_drawdown_pct=0.0,  # Dynamically sourced from active session profiles
        active_portfolio_count=1   # Sourced from ledger metrics
    )

    exchange = active.get("exchange") or "NSE"
    symbol_key = f"{exchange}:{active['symbol']}"
    check_result = _apply_confidence_sizing(check_result, symbol_key, qty)

    return check_result

@router.post("/orders/place")
async def place_order(payload: OrderPayload):
    """
    Evaluates risk rules and dispatches order executions to Angel One.
    """
    # 1. Run Pre-trade Risk Management Check
    risk_evaluation = risk_service.evaluate_order(
        symbol=payload.symbol,
        price=payload.price if payload.price > 0 else 100.0,  # Fallback approximation for market orders
        qty=payload.qty,
        current_drawdown_pct=0.0,
        active_portfolio_count=1
    )

    symbol_key = f"{payload.exchange}:{payload.symbol}"
    risk_evaluation = _apply_confidence_sizing(risk_evaluation, symbol_key, payload.qty)

    if not risk_evaluation["cleared"]:
        raise HTTPException(
            status_code=403,
            detail=f"Order rejected by RMS Gate: {risk_evaluation['breach_reason']}"
        )

    # 2. Login verification
    if not await angel_service.login():
        raise HTTPException(
            status_code=503,
            detail="Trading integration offline. SmartAPI authentication failed."
        )

    # 3. Construct Order Payload
    broker_payload = {
        "variety": "NORMAL",
        "tradingsymbol": payload.symbol,
        "symboltoken": payload.token,
        "transactiontype": payload.transaction_type.upper(),
        "exchange": payload.exchange.upper(),
        "ordertype": payload.order_type.upper(),
        "producttype": "INTRADAY",
        "duration": "DAY",
        "price": str(payload.price) if payload.order_type.upper() == "LIMIT" else "0",
        "squareoff": "0",
        "stoploss": "0",
        "quantity": str(payload.qty)
    }

    headers = {
        "Authorization": f"Bearer {angel_service.jwt_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-PrivateKey": angel_service.api_key,
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": "127.0.0.1",
        "X-ClientPublicIP": "127.0.0.1",
        "X-MACAddress": "00:00:00:00:00:00"
    }

    url = f"{angel_service.base_url}/rest/secure/angelbroking/order/v1/placeOrder"

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=broker_payload, headers=headers, timeout=12.0)
            if res.status_code == 200:
                res_data = res.json()
                if res_data.get("status"):
                    return {
                        "status": "success",
                        "order_id": res_data.get("data", {}).get("orderid"),
                        "message": "Order executed successfully."
                    }
                else:
                    raise HTTPException(status_code=400, detail=f"Order rejected by broker: {res_data.get('message')}")
            else:
                raise HTTPException(status_code=res.status_code, detail=f"Broker REST returned error: {res.text}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Broker gateway connection failed: {exc}")