# routers/strategy.py
#
# NOTE: This file previously defined its own GET /api/active/strategy-signal
# route. That route was a duplicate of the one in routers/stocks.py and, because
# FastAPI matches routes in registration order and main.py included stocks_router
# before strategy_router, this file's handler was DEAD CODE — it never actually
# ran, even though it had the more resilient logic (symbol override + on-disk
# cache fallback across hot reloads).
#
# That resilient logic has been merged into the single canonical handler in
# routers/stocks.py (GET /active/strategy-signal) so there is exactly one
# implementation and no ambiguity about which one is live.
#
# This file is kept as an empty, harmless placeholder router in case anything
# else in the project still imports it.

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["stocks"])