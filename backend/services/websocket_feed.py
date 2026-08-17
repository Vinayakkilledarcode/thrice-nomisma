# services/websocket_feed.py
import json
import asyncio
import socket
import httpx
import yfinance as yf
from typing import List, Set, Optional
from datetime import datetime
from services.market import angel_service, to_yfinance_symbol

try:
    import websockets
except ImportError:
    websockets = None

class AngelOneWebSocketFeed:
    def __init__(self):
        self.active_subscriptions: Set[str] = set()
        self._running_task: asyncio.Task = None
        self._fallback_task: asyncio.Task = None
        self._is_active = False
        self.use_fallback_polling = False

    async def start(self, initial_tokens: List[str]):
        """Starts the background WebSocket streaming connection."""
        self.active_subscriptions = set(initial_tokens)
        self._is_active = True
        self._running_task = asyncio.create_task(self._main_connection_loop())
        # Start the background fallback polling monitor
        self._fallback_task = asyncio.create_task(self._fallback_polling_monitor_loop())
        print("[FEED] Background Hybrid Streaming Engine initialized.")

    async def stop(self):
        """Closes active WebSocket connections safely."""
        self._is_active = False
        if self._running_task:
            self._running_task.cancel()
        if self._fallback_task:
            self._fallback_task.cancel()
        print("[FEED] Connection loop shutdown completed.")

    async def add_tokens(self, tokens: List[str]):
        """Adds new instruments to active subscriptions."""
        new_tokens = [t for t in tokens if t not in self.active_subscriptions]
        if new_tokens:
            self.active_subscriptions.update(new_tokens)

    async def _main_connection_loop(self):
        """Primary loop: Attempts connections to Angel One's WS V2 servers."""
        ws_url = "wss://smartapisports.angelone.in/smartapiticks"
        retry_count = 0
        max_retries_before_polling = 3
        
        while self._is_active and not self.use_fallback_polling:
            try:
                if websockets is None:
                    raise ImportError("websockets library missing")
                if not await angel_service.login():
                    await asyncio.sleep(5)
                    continue

                print(f"[FEED] Connecting to Broker WebSocket stream at {ws_url}...")
                async with websockets.connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=20
                ) as ws:
                    retry_count = 0  # Reset on successful connect
                    auth_msg = {
                        "action": 1,
                        "params": {
                            "token": angel_service.jwt_token,
                            "clientcode": angel_service.client_code,
                            "feedtype": angel_service.feed_token,
                            "apikey": angel_service.api_key
                        }
                    }
                    await ws.send(json.dumps(auth_msg))
                    await asyncio.sleep(0.5)
                    
                    sub_list = list(self.active_subscriptions)
                    if sub_list:
                        sub_msg = {
                            "action": 1,
                            "params": {
                                "mode": 3,
                                "tokenList": [{"exchangeType": 1, "tokens": sub_list}]
                            }
                        }
                        await ws.send(json.dumps(sub_msg))
                        print(f"[FEED] Active subscription established for {len(sub_list)} instruments.")

                    while self._is_active:
                        message = await ws.recv()
                        if isinstance(message, bytes):
                            await self._decode_binary_payload(message)
                        else:
                            await self._decode_json_payload(message)
            except Exception as e:
                retry_count += 1
                print(f"[FEED] Broker connection rejected: {e}. Attempt {retry_count}/{max_retries_before_polling}")
                if retry_count >= max_retries_before_polling:
                    print("[FEED] WARNING: Broker WebSocket servers offline or blocked. Activating High-Speed Fallback Polling Engine...")
                    self.use_fallback_polling = True
                    break
                await asyncio.sleep(2)

    async def _fallback_polling_monitor_loop(self):
        """
        Fallback Loop: High-performance polling engine.
        Bypasses any broker/DNS/ISP blocks by retrieving live ticks.
        """
        while self._is_active:
            if self.use_fallback_polling:
                active = angel_service.active_symbol
                if active and active.get("symbol"):
                    symbol = active["symbol"]
                    exchange = active.get("exchange") or "NSE"
                    token = active.get("token", "DUMMY")
                    full_symbol = f"{exchange}:{symbol}"
                    
                    try:
                        # High-speed parallelized ticker fetch
                        yf_sym = to_yfinance_symbol(full_symbol)
                        ticker = yf.Ticker(yf_sym)
                        # Fetch the last 1 day of data at 1-minute intervals
                        df = await asyncio.to_thread(ticker.history, period="1d", interval="1m")
                        if not df.empty:
                            last_row = df.iloc[-1]
                            ltp = float(last_row['Close'])
                            open_p = float(last_row['Open'])
                            high_p = float(last_row['High'])
                            low_p = float(last_row['Low'])

                            # BUGFIX: this used to set "close" (the previous
                            # trading day's close, used for the day's %
                            # change) equal to the live "ltp" itself, which
                            # permanently pinned the Telemetry panel's change
                            # to a fake +0.00% any time this last-resort
                            # fallback was actually used. Pull the real prior
                            # close from daily bars instead.
                            prev_close = ltp
                            try:
                                daily_df = await asyncio.to_thread(ticker.history, period="5d", interval="1d")
                                if len(daily_df) > 1:
                                    prev_close = float(daily_df.iloc[-2]['Close'])
                            except Exception:
                                pass

                            # Write directly to active rest client cache
                            angel_service.active_ticks[str(token)] = {
                                "ltp": ltp,
                                "close": prev_close,
                                "open": open_p,
                                "high": high_p,
                                "low": low_p,
                                "timestamp": datetime.now().isoformat(),
                                "source": "fallback_polling"
                            }
                    except Exception as e:
                        # Suppress errors to prevent thread interruptions
                        pass
            # Poll every 1.5 seconds to maintain high-fidelity ticks in the UI
            await asyncio.sleep(1.5)

    async def _decode_binary_payload(self, raw: bytes):
        try:
            if len(raw) >= 43:
                token = raw[1:26].decode('utf-8').strip('\x00')
                ltp = int.from_bytes(raw[26:34], byteorder='little') / 100.0
                close = int.from_bytes(raw[34:42], byteorder='little') / 100.0
                
                angel_service.active_ticks[token] = {
                    "ltp": ltp,
                    "close": close,
                    "timestamp": datetime.now().isoformat(),
                    "source": "ws_binary"
                }
        except Exception:
            pass

    async def _decode_json_payload(self, raw_str: str):
        try:
            data = json.loads(raw_str)
            if isinstance(data, dict) and "token" in data:
                token = data["token"]
                angel_service.active_ticks[token] = {
                    "ltp": float(data.get("ltp", 0)),
                    "close": float(data.get("close", 0)),
                    "open": float(data.get("open", 0)),
                    "high": float(data.get("high", 0)),
                    "low": float(data.get("low", 0)),
                    "timestamp": datetime.now().isoformat(),
                    "source": "ws_json"
                }
        except Exception:
            pass

# Global Singleton Instance
websocket_feed = AngelOneWebSocketFeed()