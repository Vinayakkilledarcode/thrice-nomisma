# backend/services/auto_trader.py
"""
Autonomous strategy-to-execution engine.

Runs as a background asyncio task inside the SAME FastAPI process as the
rest of the backend (main.py already runs a similar background task for
the tick broadcaster -- this follows the identical pattern). It:

  1. ENTRY: polls GET /api/active/strategy-signal for every symbol on the
     watchlist. If arbitration.adjusted_confidence clears the configured
     threshold, it runs the EXISTING risk-check endpoint (which already
     applies position_size_multiplier via signal_arbitration -- see
     routers/orders.py::_apply_confidence_sizing) and places a live order
     through the EXISTING POST /api/orders/place endpoint. No trading
     logic is duplicated -- this engine only decides WHEN to call the
     endpoints you already trust.

  2. EXIT: for every open position it watches live ticks (from
     services.market.angel_service.active_ticks -- the same cache the
     WebSocket broadcaster in main.py reads) and squares the position off
     the instant price crosses the recorded stop-loss or target, OR the
     configured end-of-day square-off time is reached -- whichever comes
     first.

IMPORTANT -- "works even when I close the app":
  This loop lives in the FastAPI backend process, not the Electron/React
  frontend. Closing the desktop window does NOT stop it, AS LONG AS the
  backend process itself keeps running. If Electron spawns/kills the
  Python backend as a child process when the window closes, this dies
  with it -- you need the backend running independently of the Electron
  window (systemd/pm2/a small always-on VPS, or at minimum detached from
  the Electron process tree). This module cannot fix that by itself;
  it's a deployment decision outside this file.

State (open positions + trade log) is persisted to a JSON file on every
change, so a backend restart does not orphan an open position -- on
restart it re-loads and keeps watching for SL/target/square-off.

TWO THINGS I DELIBERATELY DID NOT GUESS AT (see README_AUTOTRADE.md):
  - Stop-loss / target defaults to a flat percentage from entry price.
    Your codebase already does ATR-based risk management in
    strategy_utils.py -- wiring that in here would be better, but I don't
    have that file's exact return shape, so I left a clearly marked hook
    (_compute_stop_and_target) instead of guessing field names that could
    silently break.
  - Multi-symbol watchlists need a way to fetch a live token + LTP for a
    symbol that ISN'T the app's single "active symbol". Right now this
    only trades the currently active/subscribed symbol (same one shown in
    the Ticker tab) because that's the only symbol with a live tick
    stream today. Trading a full watchlist needs market.py extended with
    a per-symbol token/tick lookup -- flagged inline below.
"""

import asyncio
import json
import os
from datetime import datetime, time as dtime
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from services.market import angel_service

IST = ZoneInfo("Asia/Kolkata")

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(_BACKEND_ROOT, "autotrade_state.json")
CONFIG_PATH = os.path.join(_BACKEND_ROOT, "autotrade_config.json")

# Self-referential base URL -- the engine calls the backend's own REST API
# rather than importing router internals, so it always exercises exactly
# the same code path (and risk gates) a human clicking "place order" would.
SELF_BASE_URL = os.getenv("NOMISMA_SELF_URL", "http://127.0.0.1:8000/api")

DEFAULT_CONFIG = {
    "enabled": False,                # master switch -- starts OFF on purpose, flip on when ready
    "watchlist": [],                 # e.g. [{"symbol": "RELIANCE-EQ", "exchange": "NSE"}]
    "confidence_threshold": 0.65,    # arbitration.adjusted_confidence, 0-1 scale
    "base_qty_per_trade": 1,         # pre-sizing quantity -- SET THIS to something real before enabling
    "max_concurrent_positions": 3,
    "poll_interval_seconds": 15,
    "sl_pct": 0.01,                  # 1% stop-loss from entry (flat-% fallback, see module docstring)
    "target_pct": 0.02,              # 2% target from entry
    "market_open": "09:15",
    "market_close": "15:30",
    "square_off_time": "15:15",      # forced exit ahead of market close
    "horizon": None,                 # optional pass-through to strategy-signal (intraday/swing/investment)
    "interval": None,                # optional pass-through to strategy-signal
    "auth_token": None,              # optional bearer token, only if your endpoints require auth
}


def _load_json(path: str, default: dict) -> dict:
    if not os.path.exists(path):
        return dict(default)
    try:
        with open(path, "r") as f:
            data = json.load(f)
        merged = dict(default)
        merged.update(data)
        return merged
    except Exception as e:
        print(f"[AUTOTRADE] Failed to load {path}, using defaults: {e}")
        return dict(default)


def _save_json(path: str, data: dict):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


class AutoTrader:
    def __init__(self):
        self.config: dict = _load_json(CONFIG_PATH, DEFAULT_CONFIG)
        state = _load_json(STATE_PATH, {"positions": {}, "history": []})
        self.positions: dict = state.get("positions", {})   # symbol_key -> position dict
        self.history: list = state.get("history", [])
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None

    # ─── persistence ──────────────────────────────────────────────
    def _persist_state(self):
        _save_json(STATE_PATH, {"positions": self.positions, "history": self.history[-200:]})

    def _persist_config(self):
        _save_json(CONFIG_PATH, self.config)

    def update_config(self, patch: dict):
        self.config.update(patch)
        self._persist_config()

    # ─── lifecycle (call from main.py's lifespan, same as websocket_feed) ──
    async def start(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            print("[AUTOTRADE] Engine task started (enabled="
                  f"{self.config['enabled']}, watchlist={len(self._resolve_watchlist())} symbol(s)).")

    async def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None

    # ─── time helpers ─────────────────────────────────────────────
    @staticmethod
    def _parse_hhmm(s: str) -> dtime:
        h, m = s.split(":")
        return dtime(int(h), int(m))

    def _now_ist(self) -> datetime:
        return datetime.now(IST)

    def _within_market_hours(self) -> bool:
        now = self._now_ist().time()
        return self._parse_hhmm(self.config["market_open"]) <= now <= self._parse_hhmm(self.config["market_close"])

    def _past_square_off_time(self) -> bool:
        now = self._now_ist().time()
        return now >= self._parse_hhmm(self.config["square_off_time"])

    # ─── data access ──────────────────────────────────────────────
    def _get_ltp(self, token: str) -> Optional[float]:
        tick = angel_service.active_ticks.get(token)
        if not tick:
            return None
        return tick.get("ltp")

    async def _fetch_strategy_signal(self, symbol_key: str) -> Optional[dict]:
        params = {"symbol": symbol_key}
        if self.config.get("horizon"):
            params["horizon"] = self.config["horizon"]
        if self.config.get("interval"):
            params["interval"] = self.config["interval"]
        headers = {}
        if self.config.get("auth_token"):
            headers["Authorization"] = f"Bearer {self.config['auth_token']}"
        try:
            res = await self._client.get(f"{SELF_BASE_URL}/active/strategy-signal", params=params, headers=headers)
            if res.status_code != 200:
                return None
            return res.json()
        except Exception as e:
            print(f"[AUTOTRADE] strategy-signal fetch failed for {symbol_key}: {e}")
            return None

    async def _risk_check(self, qty: int, price: float) -> Optional[dict]:
        try:
            res = await self._client.get(f"{SELF_BASE_URL}/active/risk-check", params={"qty": qty, "price": price})
            if res.status_code != 200:
                return None
            return res.json()
        except Exception as e:
            print(f"[AUTOTRADE] risk-check failed: {e}")
            return None

    async def _place_order(self, payload: dict) -> Optional[dict]:
        try:
            res = await self._client.post(f"{SELF_BASE_URL}/orders/place", json=payload)
            if res.status_code != 200:
                print(f"[AUTOTRADE] order rejected: {res.status_code} {res.text}")
                return None
            return res.json()
        except Exception as e:
            print(f"[AUTOTRADE] order placement failed: {e}")
            return None

    # ─── stop-loss / target ───────────────────────────────────────
    def _compute_stop_and_target(self, side: str, entry_price: float, symbol_key: str) -> tuple[float, float]:
        """
        Flat-percentage fallback (sl_pct / target_pct from config).

        TODO (you): swap this for the ATR-based risk management you
        already have in strategies/strategy_utils.py -- e.g. call
        GET /api/active/indicators/by-interval?names=atr&symbol=<symbol>
        and read whatever key indicator_engine.run() actually returns for
        ATR in your codebase, then something like:
            sl = entry_price - (atr * 1.5)   # long
            target = entry_price + (atr * 3.0)
        I left this as a flat-% fallback rather than guess your exact
        indicator response schema and risk being silently wrong.
        """
        sl_pct = self.config["sl_pct"]
        tgt_pct = self.config["target_pct"]
        if side == "BUY":
            return round(entry_price * (1 - sl_pct), 2), round(entry_price * (1 + tgt_pct), 2)
        return round(entry_price * (1 + sl_pct), 2), round(entry_price * (1 - tgt_pct), 2)

    # ─── entry ────────────────────────────────────────────────────
    def _resolve_watchlist(self) -> list:
        wl = self.config.get("watchlist") or []
        if wl:
            return wl
        # No explicit watchlist configured -- fall back to whatever's
        # currently the app's single "active symbol" (same pattern
        # indicators.py / orders.py already use).
        active = angel_service.active_symbol
        if active and active.get("symbol"):
            return [{"symbol": active["symbol"], "exchange": active.get("exchange", "NSE")}]
        return []

    async def _try_enter(self, item: dict):
        symbol = item["symbol"]
        exchange = item.get("exchange", "NSE")
        symbol_key = f"{exchange}:{symbol}"

        if symbol_key in self.positions:
            return  # already in a trade for this symbol

        if len(self.positions) >= self.config["max_concurrent_positions"]:
            return

        signal_data = await self._fetch_strategy_signal(symbol_key)
        if not signal_data:
            return

        action = (signal_data.get("action") or signal_data.get("signal") or "HOLD").upper()
        if action not in ("BUY", "SELL", "SHORT"):
            return

        arbitration = signal_data.get("arbitration") or {}
        adjusted_confidence = arbitration.get("adjusted_confidence")
        if adjusted_confidence is None:
            adjusted_confidence = signal_data.get("confidence", 0)
        if adjusted_confidence is None or adjusted_confidence < self.config["confidence_threshold"]:
            return

        # Live token + price is only available today for the app's single
        # "active symbol" (the one subscribed on the tick WebSocket). A
        # multi-symbol watchlist needs market.py extended with a
        # per-symbol token/tick lookup -- flagged in the module docstring.
        active = angel_service.active_symbol
        if not active or active.get("symbol") != symbol:
            print(f"[AUTOTRADE] Skipping {symbol_key}: not the currently active/subscribed symbol "
                  f"(no live token/tick available for it yet).")
            return
        token = active.get("token")
        price = self._get_ltp(token)
        if not price:
            return

        base_qty = int(self.config["base_qty_per_trade"])
        if base_qty <= 0:
            return

        risk = await self._risk_check(base_qty, price)
        if not risk:
            return

        # confidence_adjusted_max_qty is written by orders.py's
        # _apply_confidence_sizing off the SAME cached arbitration result --
        # this is the "use position_size_multiplier" sizing path you asked for.
        capped_qty = risk.get("confidence_adjusted_max_qty", risk.get("max_qty_allowed", base_qty))
        final_qty = max(0, min(base_qty, int(capped_qty)))
        if final_qty <= 0 or not risk.get("cleared", False):
            return

        side = "SELL" if action in ("SELL", "SHORT") else "BUY"

        order_payload = {
            "symbol": symbol,
            "token": token,
            "exchange": exchange,
            "qty": final_qty,
            "order_type": "MARKET",
            "price": 0.0,
            "transaction_type": side,
        }
        result = await self._place_order(order_payload)
        if not result:
            return

        sl_price, target_price = self._compute_stop_and_target(side, price, symbol_key)

        self.positions[symbol_key] = {
            "symbol": symbol,
            "exchange": exchange,
            "token": token,
            "side": side,
            "qty": final_qty,
            "entry_price": price,
            "sl_price": sl_price,
            "target_price": target_price,
            "entry_time": self._now_ist().isoformat(),
            "entry_order_id": result.get("order_id"),
            "entry_confidence": adjusted_confidence,
            "regime": arbitration.get("regime"),
        }
        self._persist_state()
        print(f"[AUTOTRADE] ENTERED {side} {final_qty} {symbol_key} @ {price} | SL {sl_price} | TGT {target_price}")

    # ─── exit ─────────────────────────────────────────────────────
    async def _square_off(self, symbol_key: str, reason: str):
        pos = self.positions.get(symbol_key)
        if not pos:
            return
        exit_side = "SELL" if pos["side"] == "BUY" else "BUY"
        order_payload = {
            "symbol": pos["symbol"],
            "token": pos["token"],
            "exchange": pos["exchange"],
            "qty": pos["qty"],
            "order_type": "MARKET",
            "price": 0.0,
            "transaction_type": exit_side,
        }
        result = await self._place_order(order_payload)
        exit_price = self._get_ltp(pos["token"])
        self.history.append({
            **pos,
            "exit_time": self._now_ist().isoformat(),
            "exit_price": exit_price,
            "exit_reason": reason,
            "exit_order_id": result.get("order_id") if result else None,
            "exit_failed": result is None,
        })
        if result is not None:
            del self.positions[symbol_key]
        else:
            print(f"[AUTOTRADE] WARNING: square-off order for {symbol_key} failed to confirm -- "
                  f"position kept open in state so it retries next cycle. Check manually!")
        self._persist_state()
        print(f"[AUTOTRADE] SQUARE-OFF {symbol_key} ({reason})")

    async def _check_exits(self):
        past_cutoff = self._past_square_off_time()
        for symbol_key, pos in list(self.positions.items()):
            if past_cutoff:
                await self._square_off(symbol_key, "square_off_time")
                continue
            price = self._get_ltp(pos["token"])
            if price is None:
                continue
            if pos["side"] == "BUY":
                if price <= pos["sl_price"]:
                    await self._square_off(symbol_key, "stop_loss")
                elif price >= pos["target_price"]:
                    await self._square_off(symbol_key, "target")
            else:  # SELL / short
                if price >= pos["sl_price"]:
                    await self._square_off(symbol_key, "stop_loss")
                elif price <= pos["target_price"]:
                    await self._square_off(symbol_key, "target")

    # ─── main loop ────────────────────────────────────────────────
    async def _loop(self):
        while True:
            try:
                # Exits run regardless of the enabled flag -- flipping
                # autotrade off mid-day should stop NEW risk, not abandon
                # positions already open.
                if self.positions:
                    await self._check_exits()

                if self.config.get("enabled") and self._within_market_hours() and not self._past_square_off_time():
                    for item in self._resolve_watchlist():
                        await self._try_enter(item)
            except Exception as e:
                print(f"[AUTOTRADE] loop error: {e}")
            await asyncio.sleep(self.config.get("poll_interval_seconds", 15))

    def status(self) -> dict:
        return {
            "config": self.config,
            "open_positions": self.positions,
            "recent_history": self.history[-20:],
            "within_market_hours": self._within_market_hours(),
        }


# Single shared instance -- imported by routers/autotrade.py and started
# from main.py's lifespan, same as the existing websocket_feed singleton.
auto_trader = AutoTrader()
