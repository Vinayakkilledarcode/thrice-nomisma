# backend/main.py
import asyncio
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from routers.auth import router as auth_router 
from routers.stocks import router as stocks_router
from routers.orders import router as orders_router
from routers.indicators import router as indicators_router
from routers.plane_overlap import router as plane_overlap_router
from routers.autotrade import router as autotrade_router

from services.market import angel_service, parse_symbol_for_angel_one
from services.websocket_feed import websocket_feed
from services.auto_trader import auto_trader


# ─── Live Tick Broadcast Engine ───────────────────────────────────────────────

class WebSocketConnectionManager:
    """Manages active browser and desktop socket channels."""
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_json(self, data: dict):
        """Sends data payloads to all connected clients."""
        if not self.active_connections:
            return
        # Broadcast concurrently across active channels
        await asyncio.gather(
            *(conn.send_json(data) for conn in self.active_connections),
            return_exceptions=True
        )

ws_manager = WebSocketConnectionManager()

async def ws_broadcast_broker_ticks_task():
    """Background loop that checks for cached tick updates and broadcasts them to active clients."""
    last_sent_timestamps = {}
    while True:
        try:
            # Check the active ticks dictionary cache
            active_ticks = list(angel_service.active_ticks.items())
            for token, tick_data in active_ticks:
                timestamp = tick_data.get("timestamp")
                if last_sent_timestamps.get(token) != timestamp:
                    payload = {
                        "token": token,
                        "ltp": tick_data.get("ltp"),
                        "close": tick_data.get("close"),
                        "timestamp": timestamp
                    }
                    await ws_manager.broadcast_json(payload)
                    last_sent_timestamps[token] = timestamp
        except Exception as e:
            print(f"[WS BROADCASTER] Warning: Tick processing exception occurred: {e}")
        await asyncio.sleep(0.1)  # Limit CPU utilization during inactive hours


# ─── App Lifetime Lifespan Management ──────────────────────────────────────────

@asynccontextmanager
async def app_lifespan_handler(app: FastAPI):
    """
    Initializes and cleans up background services during the application lifecycle.
    """
    print("[NOMISMA SYSTEM] Running background setups...")
    
    # Pre-populate index lists for subscription (e.g., RELIANCE, TCS)
    # The subscription feed will dynamically expand as you browse different assets
    target_ticker_indexes = ["3045", "11536"]  # SBI, TCS tokens
    
    # Start the real-time background feed
    await websocket_feed.start(initial_tokens=target_ticker_indexes)
    
    # Start the real-time WS publisher task
    broadcast_task = asyncio.create_task(ws_broadcast_broker_ticks_task())

    # Start the autonomous strategy-to-execution engine (entry gating +
    # SL/target/EOD exit watcher). Runs regardless of whether any frontend
    # window is open -- see services/auto_trader.py module docstring for
    # what that does and does NOT guarantee about "even when app is closed".
    # It starts with config["enabled"] = False by default (autotrade_config.json)
    # until you flip it on via POST /api/autotrade/enable.
    await auto_trader.start()
    
    yield
    
    print("[NOMISMA SYSTEM] Shutting down background feed tasks...")
    broadcast_task.cancel()
    await auto_trader.stop()
    await websocket_feed.stop()


# ─── FastAPI Core Initialization ─────────────────────────────────────────────

app = FastAPI(
    title="Thrice Nomisma Core Engine",
    lifespan=app_lifespan_handler
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ActiveSymbolPayload(BaseModel):
    token: str = ""
    symbol: str
    exchange: str = "NSE"
    name: str = ""


_active_symbol_store = {}


@app.post("/api/active-symbol")
async def set_active_symbol(payload: ActiveSymbolPayload):
    global _active_symbol_store
    
    token = payload.token
    exchange = payload.exchange or "NSE"
    symbol = payload.symbol
    name = payload.name or symbol

    if not token or token == "DUMMY":
        try:
            base_symbol, parsed_exchange = parse_symbol_for_angel_one(symbol)
            await angel_service.load_instruments()
            key = f"{base_symbol}_{parsed_exchange}"
            
            if key in angel_service.instruments:
                inst = angel_service.instruments[key]
                token = inst["token"]
                symbol = inst["symbol"]
                exchange = inst["exchange"]
                name = inst.get("name", symbol)
        except Exception as e:
            print(f"[ACTIVE-SYMBOL] Scrip lookup fallback bypassed: {e}")

    _active_symbol_store = {
        "token": token,
        "symbol": symbol,
        "exchange": exchange,
        "name": name
    }
    
    # Synchronize selected ticker inside backend services
    angel_service.active_symbol = _active_symbol_store
    
    # Register the newly selected instrument token with the active WebSocket stream
    if token and token != "DUMMY":
        await websocket_feed.add_tokens([token])
    
    print("\n" + "=" * 60)
    print("  [NOMISMA ACTIVE TICKER REGISTERED]")
    print(f"  TICKER: {name} ({exchange}:{symbol})")
    print(f"  SECURITY TOKEN: #{token if token else 'PENDING'}")
    print("=" * 60 + "\n")
    
    return {"status": "success", "symbol": _active_symbol_store}


@app.get("/api/active-symbol")
def get_active_symbol():
    return _active_symbol_store


# ─── Live WebSocket Connection Port ──────────────────────────────────────────

@app.websocket("/ws/ticks")
async def websocket_tick_endpoint(websocket: WebSocket):
    """
    Real-time tick data stream for client UIs.
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive; discard any unexpected incoming payloads
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


# ─── Router Registrations ─────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(stocks_router)
app.include_router(orders_router)
app.include_router(indicators_router)
app.include_router(plane_overlap_router)
app.include_router(autotrade_router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)