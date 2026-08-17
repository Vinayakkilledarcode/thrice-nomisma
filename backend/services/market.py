# services/market.py
import os
import sys
import time
import json
import httpx
import asyncio
import yfinance as yf
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta

# Dynamic path resolution to ensure strategy imports resolve cleanly
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

# Import the 11 separate strategy algorithms
from strategies.scalping_hft_1m import evaluate_scalping_hft_1m
from strategies.micro_scalping_3m import evaluate_micro_scalping_3m
from strategies.short_scalping_5m import evaluate_short_scalping_5m
from strategies.intraday_10m import evaluate_intraday_10m
from strategies.intraday_mean_reversion_15m import evaluate_intraday_mean_reversion_15m
from strategies.intraday_swing_30m import evaluate_intraday_swing_30m
from strategies.momentum_swing_1h import evaluate_momentum_swing_1h
from strategies.multi_session_swing_4h import evaluate_multi_session_swing_4h
from strategies.swing_momentum_1d import evaluate_swing_momentum_1d
from strategies.long_term_value_1w import evaluate_long_term_value_1w
from strategies.long_term_investment_1mo import evaluate_long_term_investment_1mo

# Local deterministic reasoning engine — replaces the Gemini API call below.
# No network dependency, no quota/429 risk, same output shape (a markdown
# string) so the rest of this file and the frontend need zero changes.
from services.reasoning_engine import generate_reasoning_explanation

# Cryptographic TOTP helper
import pyotp

# Environment variable loader
from dotenv import load_dotenv

# Locate the .env file dynamically relative to this file's location
env_path = os.path.join(backend_dir, ".env")

if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path)
    print(f"[NOMISMA] Target .env configuration found and loaded at: {env_path}")
else:
    print(f"[NOMISMA] WARNING: No .env configuration file found at expected path: {env_path}")


def clean_env_var(val: Optional[str]) -> str:
    if not val:
        return ""
    return val.strip().strip('"').strip("'")


# Scrip Master Cache Config
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
CACHE_PATH = "scrip_master.json"

# ─── Symbol Translator Helper ─────────────────────────────────────────────────

def to_yfinance_symbol(symbol: str) -> str:
    """
    Translates standard TradingView / broker symbols to yfinance format dynamically.
    e.g., 'NSE:RELIANCE'   -> 'RELIANCE.NS'
    e.g., 'BSE:TCS'        -> 'TCS.BO'
    """
    sym = symbol.upper().strip()
    if ":" in sym:
        exchange, ticker = sym.split(":", 1)
        ticker = ticker.replace("-EQ", "").replace("-BE", "").replace("-SM", "")
        if exchange == "NSE":
            return f"{ticker}.NS"
        elif exchange == "BSE":
            return f"{ticker}.BO"
        elif exchange in ["NASDAQ", "NYSE", "AMEX"]:
            return ticker
        return ticker
    return sym

def parse_symbol_for_angel_one(symbol: str) -> tuple[str, str]:
    """Parses standard ticker formats into base symbol and exchange, cleaning suffixes."""
    symbol_upper = symbol.upper().strip()
    exchange = "NSE"
    
    if ":" in symbol_upper:
        parts = symbol_upper.split(":")
        exchange = parts[0]
        base_symbol = parts[1]
    elif symbol_upper.endswith(".NS"):
        exchange = "NSE"
        base_symbol = symbol_upper[:-3]
    elif symbol_upper.endswith(".BO"):
        exchange = "BSE"
        base_symbol = symbol_upper[:-3]
    else:
        base_symbol = symbol_upper
        
    base_symbol = base_symbol.replace("-EQ", "").replace("-BE", "").replace("-SM", "")
    return base_symbol, exchange

# ─── Date & Interval Mappers ───────────────────────────────────────────────────

def map_interval(interval: str) -> Optional[str]:
    """Maps custom resolutions to native SmartAPI endpoint values."""
    mapping = {
        "1m": "ONE_MINUTE",
        "3m": "THREE_MINUTE",
        "5m": "FIVE_MINUTE",
        "10m": "TEN_MINUTE",
        "15m": "FIFTEEN_MINUTE",
        "30m": "THIRTY_MINUTE",
        "1h": "ONE_HOUR",
        "1d": "ONE_DAY"
    }
    return mapping.get(interval)


# ─── Ticker Timeframe -> Angel One Candle Resolver ─────────────────────────
#
# BUGFIX (root cause of "values don't match for the respective timeframe"):
#
# The Live Telemetry / Ticker timeframe selector offers 20 exact intervals
# (15s/30s/45s, 1-125m, 1-4h, 1d/1w/1mo -- see TICKER_INTERVAL_TO_YF below).
# get_quote() used to call map_interval(interval) directly, and that
# function only recognizes 8 of those 20 strings (1m,3m,5m,10m,15m,30m,1h,
# 1d). For every OTHER selected timeframe -- 15s/30s/45s/2m/4m/75m/125m/2h/
# 3h/4h/1w/1mo, i.e. more than half the dropdown -- map_interval() returned
# None, `elif ao_interval:` was False, and get_quote() silently gave up on
# Angel One entirely and returned pure yfinance data for that timeframe.
# Meanwhile the Bid/Ask depth ladder and get_ltp() always call real Angel
# One endpoints regardless of Timeframe. So for those 12 timeframes the
# Telemetry panel was structurally guaranteed to disagree with the depth
# ladder -- not a timing bug, a routing bug: two different data providers.
#
# Fix: give every one of the 20 Ticker timeframes a real Angel One candle
# source, using the same "map to nearest native resolution, then resample
# up" technique already used for yfinance (TICKER_INTERVAL_TO_YF /
# TICKER_RESAMPLE_RULE below) and for the strategy engine (YF_INTERVAL_MAP /
# RESAMPLE_TARGET above) -- just built on Angel One's 8 native candle
# resolutions instead of yfinance's.
TICKER_INTERVAL_TO_AO_BASE: Dict[str, str] = {
    "15s": "1m", "30s": "1m", "45s": "1m",   # AO has no sub-minute candles; nearest available
    "1m": "1m", "2m": "1m", "3m": "3m", "4m": "1m", "5m": "5m",
    "10m": "10m", "15m": "15m", "30m": "30m", "75m": "15m", "125m": "5m",
    "1h": "1h", "2h": "1h", "3h": "1h", "4h": "1h",
    "1d": "1d", "1w": "1d", "1mo": "1d",
}

# Pandas resample rule for Ticker intervals whose bar size doesn't match the
# native Angel One resolution fetched above -- upsamples the real broker
# candles into the actual requested bucket (e.g. 5x 15m AO candles -> one
# 75m candle), instead of ever falling through to a different data source.
TICKER_AO_RESAMPLE_RULE: Dict[str, str] = {
    "2m": "2min", "4m": "4min", "75m": "75min", "125m": "125min",
    "2h": "2h", "3h": "3h", "4h": "4h", "1w": "1W", "1mo": "1ME",
}

# How many days of history to request from Angel One's getCandleData per
# Ticker interval -- wide enough that at least a couple of bars exist at
# that resolution after resampling, without over-fetching on fine intervals.
TICKER_INTERVAL_TO_AO_LOOKBACK_DAYS: Dict[str, int] = {
    "15s": 5, "30s": 5, "45s": 5,
    "1m": 5, "2m": 5, "3m": 5, "4m": 5, "5m": 10,
    "10m": 15, "15m": 20, "30m": 30, "75m": 60, "125m": 60,
    "1h": 60, "2h": 90, "3h": 90, "4h": 90,
    "1d": 400, "1w": 800, "1mo": 1500,
}


async def get_angelone_candle_for_interval(inst: dict, ticker_interval: str) -> Optional[dict]:
    """
    Fetches (and, where necessary, resamples) the current/most-recent OHLC
    bar for ANY of the 20 exact Ticker timeframes directly from Angel One --
    never yfinance -- so Telemetry always agrees with the depth ladder for
    every timeframe, not just the 8 AO natively supports.

    Returns {"open","high","low","close","candle_prev_close"} or None if AO
    candle data isn't available (caller should fall back to the raw get_ltp
    snapshot, and ultimately to yfinance, same as before).
    """
    resolved = (ticker_interval or "1d").lower().strip()

    # BUGFIX: this used to default an unrecognized `resolved` string straight
    # to "1d" via TICKER_INTERVAL_TO_AO_BASE.get(resolved, "1d"). That meant
    # any interval value that didn't exactly match a dict key (whitespace,
    # unexpected casing, a value not in the 20-item Ticker set) would
    # silently fetch a full DAILY candle and return it as though it were the
    # requested resolution -- the same "day range disguised as a short bar"
    # failure mode as the get_quote() fallback fixed above, just one layer
    # deeper. Fail loudly (return None) instead, so the caller takes the
    # explicit "no candle data" path rather than rendering plausible-looking
    # but wrong data.
    if resolved not in TICKER_INTERVAL_TO_AO_BASE:
        print(f"[ANGEL ONE] Unrecognized ticker interval '{ticker_interval}' (resolved='{resolved}') "
              f"-- refusing to silently substitute a daily candle. Returning no candle data.")
        return None

    base = TICKER_INTERVAL_TO_AO_BASE[resolved]
    ao_interval = map_interval(base)
    if not ao_interval:
        print(f"[ANGEL ONE] No native SmartAPI interval mapping for base='{base}' "
              f"(ticker interval='{resolved}'). Returning no candle data.")
        return None

    lookback_days = TICKER_INTERVAL_TO_AO_LOOKBACK_DAYS.get(resolved, 10)
    now = datetime.now()
    from_dt = now - timedelta(days=lookback_days)
    from_date = from_dt.strftime("%Y-%m-%d %H:%M")
    to_date = now.strftime("%Y-%m-%d %H:%M")

    candles = await angel_service.get_candles(
        exchange=inst["exchange"],
        symboltoken=inst["token"],
        interval=ao_interval,
        from_date=from_date,
        to_date=to_date,
    )
    if not candles:
        print(f"[ANGEL ONE] getCandleData returned 0 candles for ticker_interval='{resolved}' "
              f"(native={ao_interval}, from={from_date}, to={to_date}). This will force the "
              f"caller's single-point-candle fallback -- check broker session / date range limits "
              f"for this resolution if this repeats.")
        return None

    try:
        df = pd.DataFrame(candles, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        df["Date"] = pd.to_datetime(df["Date"], utc=False)
        df = df.set_index("Date").sort_index()
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if df.empty:
            return None

        resample_rule = TICKER_AO_RESAMPLE_RULE.get(resolved)
        if resample_rule:
            agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
            df = (
                df.resample(resample_rule, label="right", closed="right")
                .agg(agg)
                .dropna(subset=["Open", "High", "Low", "Close"])
            )
        if df.empty:
            print(f"[ANGEL ONE] Resample to '{resample_rule}' left 0 bars for ticker_interval="
                  f"'{resolved}' -- not enough raw {ao_interval} candles in the fetched window.")
            return None

        last_row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) > 1 else last_row
        return {
            "open":  float(last_row["Open"]),
            "high":  float(last_row["High"]),
            "low":   float(last_row["Low"]),
            "close": float(last_row["Close"]),
            "candle_prev_close": float(prev_row["Close"]),
        }
    except Exception as e:
        print(f"[ANGEL ONE] Candle resample bypassed for ticker interval {resolved}: {e}")
        return None

def calculate_dates_for_period(period: str) -> tuple[str, str]:
    now = datetime.now()
    to_date = now.strftime("%Y-%m-%d %H:%M")
    
    if period == "1d":
        from_dt = now - timedelta(days=1)
    elif period == "5d":
        from_dt = now - timedelta(days=5)
    elif period == "1mo":
        from_dt = now - timedelta(days=30)
    elif period == "3mo":
        from_dt = now - timedelta(days=90)
    elif period == "6mo":
        from_dt = now - timedelta(days=180)
    elif period == "1y":
        from_dt = now - timedelta(days=365)
    elif period == "2y":
        from_dt = now - timedelta(days=730)
    elif period == "5y":
        from_dt = now - timedelta(days=1825)
    else:
        from_dt = now - timedelta(days=180)
        
    from_date = from_dt.strftime("%Y-%m-%d %H:%M")
    return from_date, to_date

# ─── Global Cache Downloader ───────────────────────────────────────────────────

def _load_json_file(path: str) -> list:
    """Helper ran in thread-pool to avoid blocking loop during disk read."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_json_file(path: str, data: list):
    """Helper ran in thread-pool to avoid blocking loop during disk write."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

async def get_scrip_master() -> list:
    """Asynchronously fetches and caches local instrument mappings to avoid blocking."""
    if os.path.exists(CACHE_PATH):
        try:
            data = await asyncio.to_thread(_load_json_file, CACHE_PATH)
            if data and isinstance(data, list) and len(data) > 0:
                print(f"[ANGEL ONE] Scrip Master loaded from cache file. Entries: {len(data)}")
                return data
        except Exception as e:
            print(f"[ANGEL ONE] Local Cache Parse Failure: {e}. Removing corrupted cache file.")
            try:
                os.remove(CACHE_PATH)
            except Exception:
                pass
                
    print("[ANGEL ONE] Fetching fresh Scrip Master from Angel One servers (approx 10MB)...")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(SCRIP_MASTER_URL, timeout=30.0)
            if response.status_code == 200:
                data = response.json()
                await asyncio.to_thread(_save_json_file, CACHE_PATH, data)
                print(f"[ANGEL ONE] Fresh Scrip Master cached successfully. Entries: {len(data)}")
                return data
            else:
                print(f"[ANGEL ONE] Scrip Master Download Failed. HTTP Code: {response.status_code}")
    except Exception as e:
        print(f"[ANGEL ONE] Scrip Master Network Fetch Failure: {e}")
        
    return []

# ─── Angel One Connection Service ──────────────────────────────────────────────

class AngelOneRestClient:
    def __init__(self):
        self.api_key = ""
        self.client_code = ""
        self.password = ""
        self.totp_key = ""
        self.jwt_token = None
        self.feed_token = None
        self.is_logged_in = False
        self.instruments = {}
        self.instruments_by_token = {}
        self.base_url = "https://apiconnect.angelone.in"
        
        # Shared real-time tick dictionary cache
        self.active_ticks: Dict[str, Dict[str, Any]] = {}
        
        # Globally synchronized active stock reference
        self.active_symbol = {}

    async def login(self) -> bool:
        if self.is_logged_in and self.jwt_token:
            return True
            
        self.api_key = clean_env_var(os.getenv("ANGEL_ONE_API_KEY", ""))
        self.client_code = clean_env_var(os.getenv("ANGEL_ONE_CLIENT_CODE", ""))
        self.password = clean_env_var(os.getenv("ANGEL_ONE_PASSWORD", ""))
        self.totp_key = clean_env_var(os.getenv("ANGEL_ONE_TOTP_KEY", ""))

        if not all([self.api_key, self.client_code, self.password, self.totp_key]):
            print(f"[ANGEL ONE] Connection skipped. Missing configuration metrics.")
            return False
            
        try:
            print(f"[ANGEL ONE] Attempting SmartAPI connection for client {self.client_code}...")
            totp_code = pyotp.TOTP(self.totp_key).now()
            payload = {
                "clientcode": self.client_code,
                "password": self.password,
                "totp": totp_code
            }
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-PrivateKey": self.api_key,
                "X-UserType": "USER",
                "X-SourceID": "WEB",
                "X-ClientLocalIP": "127.0.0.1",
                "X-ClientPublicIP": "127.0.0.1",
                "X-MACAddress": "00:00:00:00:00:00"
            }
            url = f"{self.base_url}/rest/auth/angelbroking/user/v1/loginByPassword"
            
            async with httpx.AsyncClient() as client:
                res = await client.post(url, json=payload, headers=headers, timeout=10.0)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("status") and data.get("data"):
                        self.jwt_token = data["data"].get("jwtToken")
                        self.feed_token = data["data"].get("feedToken")
                        self.is_logged_in = True
                        print("[ANGEL ONE] REST API authorization handshakes verified. Connection online.")
                        return True
                    else:
                        print(f"[ANGEL ONE] Authentication rejected by broker: {data.get('message')}")
                else:
                    print(f"[ANGEL ONE] HTTP Error {res.status_code} during login: {res.text}")
        except Exception as e:
            print(f"[ANGEL ONE] Handshake connection error: {e}")
        return False

    async def load_instruments(self):
        if self.instruments:
            return
        data = await get_scrip_master()
        temp_dict = {}
        token_dict = {}
        for item in data:
            name = item.get("name", "").upper()
            exchange = (item.get("exch_seg") or item.get("exchange") or "").upper()
            symbol = item.get("symbol", "").upper()
            token = item.get("token", "")
            if name and exchange:
                key = f"{name}_{exchange}"
                inst_data = {
                    "token": token,
                    "symbol": symbol,
                    "name": item.get("name"),
                    "exchange": exchange,
                }
                if symbol.endswith("-EQ") or key not in temp_dict:
                    temp_dict[key] = inst_data
                if token:
                    token_dict[str(token)] = inst_data
                    
        self.instruments = temp_dict
        self.instruments_by_token = token_dict
        print(f"[ANGEL ONE] Scrip Master processed. Instrument dict populated with {len(temp_dict)} elements.")

    async def get_instrument_by_token(self, token: str) -> Optional[dict]:
        await self.load_instruments()
        return self.instruments_by_token.get(str(token))

    async def get_ltp(self, exchange: str, tradingsymbol: str, symboltoken: str) -> dict:
        """
        Sources the live price from Angel One SmartAPI itself.

        BUGFIX: this used to trust self.active_ticks[token] unconditionally.
        That cache is written from three different places in
        services/websocket_feed.py -- genuine Angel One WebSocket ticks
        (source "ws_binary" / "ws_json"), AND a Yahoo Finance polling
        fallback (source "fallback_polling") that silently and permanently
        takes over once the raw broker WebSocket fails 3 times. Because this
        method didn't check `source`, once that fallback engaged, every LTP
        this endpoint served -- at every Timeframe -- quietly became Yahoo
        Finance data instead of Angel One data, and disagreed with the
        market-depth panel (which always calls get_market_data() -> real
        Angel One REST directly, bypassing this cache entirely).

        Fix: only serve the cache when it was actually populated by a real
        broker WebSocket tick. Otherwise hit Angel One's own getLtpData REST
        endpoint directly -- this works independently of whether the raw
        tick WebSocket handshake succeeded, exactly like get_market_data()
        already does successfully for depth. The Yahoo-sourced cache is only
        used as an absolute last resort, if the direct Angel One REST call
        itself fails too (e.g. broker session down).
        """
        token_str = str(symboltoken)
        if token_str in self.active_ticks:
            cached_data = self.active_ticks[token_str]
            if cached_data.get("source") in ("ws_binary", "ws_json"):
                return {
                    "ltp": cached_data.get("ltp"),
                    "close": cached_data.get("close") or cached_data.get("ltp"),
                    "open": cached_data.get("open"),
                    "high": cached_data.get("high"),
                    "low": cached_data.get("low"),
                    "source": "websocket_feed"
                }

        if await self.login():
            try:
                payload = {
                    "exchange": exchange,
                    "tradingsymbol": tradingsymbol,
                    "symboltoken": symboltoken
                }
                headers = {
                    "Authorization": f"Bearer {self.jwt_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-PrivateKey": self.api_key,
                    "X-UserType": "USER",
                    "X-SourceID": "WEB",
                    "X-ClientLocalIP": "127.0.0.1",
                    "X-ClientPublicIP": "127.0.0.1",
                    "X-MACAddress": "00:00:00:00:00:00"
                }
                url = f"{self.base_url}/rest/secure/angelbroking/order/v1/getLtpData"

                async with httpx.AsyncClient() as client:
                    res = await client.post(url, json=payload, headers=headers, timeout=10.0)
                    if res.status_code == 200:
                        data = res.json()
                        if data.get("status") and data.get("data"):
                            result = dict(data.get("data", {}))
                            result["source"] = "angelone_rest"
                            return result
            except Exception as e:
                print(f"[ANGEL ONE] LTP Fetch Failure: {e}")

        # Absolute last resort: the direct Angel One REST call itself failed
        # (e.g. broker session unreachable). Only now fall back to whatever
        # is cached, even if it's Yahoo-sourced, rather than returning nothing.
        if token_str in self.active_ticks:
            cached_data = self.active_ticks[token_str]
            return {
                "ltp": cached_data.get("ltp"),
                "close": cached_data.get("close") or cached_data.get("ltp"),
                "open": cached_data.get("open"),
                "high": cached_data.get("high"),
                "low": cached_data.get("low"),
                "source": cached_data.get("source", "fallback_cache")
            }
        return {}

    async def get_market_data(self, exchange: str, symboltoken: str, mode: str = "FULL") -> dict:
        """Fetches market depth, volume bounds, and circuit locks for an asset."""
        if not await self.login():
            return {}
        try:
            payload = {
                "mode": mode,
                "exchangeTokens": {
                    exchange: [symboltoken]
                }
            }
            headers = {
                "Authorization": f"Bearer {self.jwt_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-PrivateKey": self.api_key,
                "X-UserType": "USER",
                "X-SourceID": "WEB",
                "X-ClientLocalIP": "127.0.0.1",
                "X-ClientPublicIP": "127.0.0.1",
                "X-MACAddress": "00:00:00:00:00:00"
            }
            url = f"{self.base_url}/rest/secure/angelbroking/market/v1/quote"
            
            async with httpx.AsyncClient() as client:
                res = await client.post(url, json=payload, headers=headers, timeout=10.0)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("status") and data.get("data"):
                        return data.get("data") or {}
        except Exception as e:
            print(f"[ANGEL ONE] Market Quote API Fetch Failure: {e}")
        return {}

    async def get_candles(self, exchange: str, symboltoken: str, interval: str, from_date: str, to_date: str) -> list:
        if not await self.login():
            return []
        try:
            payload = {
                "exchange": exchange,
                "symboltoken": symboltoken,
                "interval": interval,
                "fromdate": from_date,
                "todate": to_date
            }
            headers = {
                "Authorization": f"Bearer {self.jwt_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-PrivateKey": self.api_key,
                "X-UserType": "USER",
                "X-SourceID": "WEB",
                "X-ClientLocalIP": "127.0.0.1",
                "X-ClientPublicIP": "127.0.0.1",
                "X-MACAddress": "00:00:00:00:00:00"
            }
            url = f"{self.base_url}/rest/secure/angelbroking/historical/v1/getCandleData"
            
            async with httpx.AsyncClient() as client:
                res = await client.post(url, json=payload, headers=headers, timeout=10.0)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("status") and data.get("data"):
                        return data.get("data") or []
        except Exception as e:
            print(f"[ANGEL ONE] Candles Fetch Failure: {e}")
        return []


# Instantiate singleton REST client
angel_service = AngelOneRestClient()


# ─── Standard Quote Base Methods ──────────────────────────────────────────────

async def get_yfinance_quote_base(symbol: str, interval: str = "1d") -> dict:
    yf_symbol = to_yfinance_symbol(symbol)
    ticker = yf.Ticker(yf_symbol)
    
    is_indian = yf_symbol.endswith(".NS") or yf_symbol.endswith(".BO") or "NSE" in symbol.upper() or "BSE" in symbol.upper()
    default_currency = "INR" if is_indian else "USD"
    default_exchange = "NSE" if is_indian else "Exchange"
    
    # BUGFIX: this used to hand-roll its own 2-case interval mapping
    # ("10m"->"5m", "1w"->"1wk") and pass everything else straight through
    # to yfinance unchanged. yfinance only understands a fixed interval
    # vocabulary (1m,2m,5m,15m,30m,60m,90m,1d,5d,1wk,1mo,3mo) -- any Ticker
    # interval outside that set (3m, 4m, 75m, 125m, 2h, 3h, 4h, ...) was
    # passed through as-is and yfinance rejected it outright:
    #   "Invalid input - interval=3m is not supported."
    # which surfaced as "possibly delisted; no price data found" and an
    # empty quote. TICKER_INTERVAL_TO_YF (defined below in this module) is
    # the single source of truth for this mapping -- reuse it here instead
    # of duplicating/diverging logic.
    yf_interval = TICKER_INTERVAL_TO_YF.get(interval, interval if interval in
        ("1m", "2m", "5m", "15m", "30m", "60m", "90m", "1d", "5d", "1wk", "1mo", "3mo") else "1d")

    try:
        df = await asyncio.to_thread(ticker.history, period="3d", interval=yf_interval)
        if not df.empty:
            df.columns = [c.title() for c in df.columns]
            last_row = df.iloc[-1]
            prev_row = df.iloc[-2] if len(df) > 1 else last_row
            return {
                "symbol":         symbol.upper(),
                "shortName":      yf_symbol,
                "longName":       yf_symbol,
                "exchange":       default_exchange,
                "currency":       default_currency,
                "currentPrice":   float(last_row['Close']),
                "previousClose":  float(prev_row['Close']),
                "open":           float(last_row['Open']),
                "dayHigh":        float(last_row['High']),
                "dayLow":         float(last_row['Low']),
                "volume":         int(last_row['Volume']) if 'Volume' in last_row else None,
                "avgVolume":      None,
                "marketCap":      None,
                "fiftyTwoWeekHigh": None,
                "fiftyTwoWeekLow":  None,
                "bid":            None,
                "ask":            None,
                "bidSize":        None,
                "askSize":        None,
            }
    except Exception as e:
        print(f"[YFINANCE] Interval fallback lookup bypassed: {e}")
        
    return {}

async def get_quote(symbol: str, interval: str = "1d") -> dict:
    base_symbol, exchange = parse_symbol_for_angel_one(symbol)
    
    use_angel_one = False
    angel_info = {}
    
    if await angel_service.login():
        await angel_service.load_instruments()
        key = f"{base_symbol}_{exchange}"
        if key in angel_service.instruments:
            inst = angel_service.instruments[key]

            # Live tick is the single source of truth for LTP + previous
            # close on EVERY timeframe -- exactly like the depth ladder
            # (get_market_data) and get_ltp() already are. Fetched once,
            # then reused below whichever branch runs.
            live_ltp_res = await angel_service.get_ltp(inst["exchange"], inst["symbol"], inst["token"])
            live_ltp = live_ltp_res.get("ltp") if live_ltp_res else None
            live_close = live_ltp_res.get("close") if live_ltp_res else None

            if interval == "1d":
                if live_ltp_res:
                    use_angel_one = True
                    angel_info = {
                        "ltp":   live_ltp,
                        "close": live_close,
                        "open":  live_ltp_res.get("open"),
                        "high":  live_ltp_res.get("high"),
                        "low":   live_ltp_res.get("low"),
                    }
            else:
                # BUGFIX: previously routed through map_interval(interval)
                # directly, which only recognizes 8 of the 20 Ticker
                # timeframes -- every other one (15s/30s/45s/2m/4m/75m/125m/
                # 2h/3h/4h/1w/1mo) silently fell through to yfinance here,
                # while the depth ladder stayed on Angel One, guaranteeing a
                # mismatch. get_angelone_candle_for_interval() now resolves
                # ALL 20 timeframes to a real (resampled where needed) Angel
                # One candle. The live tick is then merged into that bar's
                # High/Low so the displayed LTP always falls inside the
                # displayed range, instead of a stale completed candle's
                # High/Low potentially excluding the current live price.
                candle_info = await get_angelone_candle_for_interval(inst, interval)

                if candle_info:
                    open_p = candle_info["open"]
                    high_p = candle_info["high"]
                    low_p = candle_info["low"]
                    if live_ltp is not None:
                        high_p = max(high_p, live_ltp)
                        low_p = min(low_p, live_ltp)

                    use_angel_one = True
                    angel_info = {
                        "ltp":   live_ltp if live_ltp is not None else candle_info["close"],
                        "close": live_close if live_close is not None else candle_info["candle_prev_close"],
                        "open":  open_p,
                        "high":  high_p,
                        "low":   low_p,
                    }
                elif live_ltp_res:
                    # No candle data at all for this timeframe (e.g. a very
                    # new instrument, or a transient getCandleData failure).
                    #
                    # BUGFIX (root cause of "Ticker shows the full day's
                    # range for a 5-minute selection"): this branch used to
                    # fill open/high/low from live_ltp_res.get("open"/"high"/
                    # "low") -- but those are the FULL SESSION day Open/High/
                    # Low, the exact same fields the `interval == "1d"`
                    # branch above uses. Whenever get_angelone_candle_for_interval()
                    # came back empty for a non-1d timeframe, this silently
                    # dressed the day's range up as if it were that
                    # timeframe's bar, so e.g. a 5m selection would render
                    # OPEN/HIGH/LOW identical to the 1d values -- looking
                    # like a real (but wrong) narrow candle instead of an
                    # honest "no bar data" state. 3h/1d only ever *looked*
                    # correct by coincidence, whenever the session-so-far
                    # happened to fit inside one bucket.
                    #
                    # Fix: never borrow day-level O/H/L for an intraday
                    # timeframe. With no real bar available, degrade to a
                    # single-point candle at the live tick itself
                    # (open=high=low=close=ltp) so the UI honestly reflects
                    # "just the current price" rather than implying a false
                    # range.
                    print(f"[NOMISMA] No Angel One candle available for interval={interval} on "
                          f"{key}; falling back to a single-point candle at the live tick instead "
                          f"of the day's O/H/L.")
                    use_angel_one = True
                    angel_info = {
                        "ltp":   live_ltp,
                        "close": live_close,
                        "open":  live_ltp if live_ltp is not None else live_ltp_res.get("open"),
                        "high":  live_ltp if live_ltp is not None else live_ltp_res.get("high"),
                        "low":   live_ltp if live_ltp is not None else live_ltp_res.get("low"),
                    }
        else:
            print(f"[NOMISMA] Match Failure: Key {key} is missing in loaded instruments!")
    else:
        print("[NOMISMA] Login Failure: SmartAPI session is offline. Falling back to Yahoo Finance.")

    try:
        yf_quote = await get_yfinance_quote_base(symbol, interval=interval)
    except Exception as e:
        print(f"[YFINANCE] Fetch error on {symbol}: {e}")
        yf_quote = {}

    if use_angel_one and angel_info:
        return {
            "symbol":        symbol.upper(),
            "shortName":     yf_quote.get("shortName") or base_symbol,
            "longName":      yf_quote.get("longName") or base_symbol,
            "exchange":      exchange,
            "currency":      "INR",
            "currentPrice":  angel_info.get("ltp"),
            "previousClose": angel_info.get("close"),
            "open":          angel_info.get("open"),
            "dayHigh":       angel_info.get("high"),
            "dayLow":        angel_info.get("low"),
            "volume":        yf_quote.get("volume"),
            "avgVolume":     yf_quote.get("avgVolume"),
            "marketCap":     yf_quote.get("marketCap"),
            "fiftyTwoWeekHigh": yf_quote.get("fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow":  yf_quote.get("fiftyTwoWeekLow"),
            "bid":              None,
            "ask":              None,
            "bidSize":          None,
            "askSize":          None,
        }
    return yf_quote

# ─── Multi-Horizon Historical Analysis Engine ───

async def fetch_and_analyze_multi_horizon(symbol: str) -> dict:
    """Gathers multi-horizon historical data and performs technical calculations."""
    base_symbol, exchange = parse_symbol_for_angel_one(symbol)
    
    df_intra = pd.DataFrame()
    use_ao = False
    
    if await angel_service.login():
        await angel_service.load_instruments()
        key = f"{base_symbol}_{exchange}"
        if key in angel_service.instruments:
            inst = angel_service.instruments[key]
            use_ao = True
            
            now = datetime.now()
            from_dt = now - timedelta(days=30)
            from_date = from_dt.strftime("%Y-%m-%d %H:%M")
            to_date = now.strftime("%Y-%m-%d %H:%M")
            
            candles_intra = await angel_service.get_candles(
                exchange=inst["exchange"],
                symboltoken=inst["token"],
                interval="FIFTEEN_MINUTE",
                from_date=from_date,
                to_date=to_date
            )
            if candles_intra:
                df_intra = pd.DataFrame(candles_intra, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
                df_intra["Date"] = pd.to_datetime(df_intra["Date"])
                df_intra.set_index("Date", inplace=True)
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    df_intra[col] = pd.to_numeric(df_intra[col], errors='coerce')

    if df_intra.empty:
        yf_symbol = to_yfinance_symbol(symbol)
        ticker = yf.Ticker(yf_symbol)
        try:
            df_intra = await asyncio.to_thread(ticker.history, period="1mo", interval="15m")
        except Exception as e:
            print(f"[NOMISMA ANALYSIS] Intraday fetch bypassed: {e}")

    df_daily = pd.DataFrame()
    if use_ao:
        inst = angel_service.instruments[f"{base_symbol}_{exchange}"]
        now = datetime.now()
        from_dt = now - timedelta(days=365)
        from_date = from_dt.strftime("%Y-%m-%d %H:%M")
        to_date = now.strftime("%Y-%m-%d %H:%M")
        candles_daily = await angel_service.get_candles(
            exchange=inst["exchange"],
            symboltoken=inst["token"],
            interval="ONE_DAY",
            from_date=from_date,
            to_date=to_date
        )
        if candles_daily:
            df_daily = pd.DataFrame(candles_daily, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
            df_daily["Date"] = pd.to_datetime(df_daily["Date"])
            df_daily.set_index("Date", inplace=True)
            for col in ["Open", "High", "Low", "Close", "Volume"]:
                df_daily[col] = pd.to_numeric(df_daily[col], errors='coerce')
                
    if df_daily.empty:
        yf_symbol = to_yfinance_symbol(symbol)
        ticker = yf.Ticker(yf_symbol)
        try:
            df_daily = await asyncio.to_thread(ticker.history, period="1y", interval="1d")
        except Exception as e:
            print(f"[NOMISMA ANALYSIS] Daily fetch bypassed: {e}")

    df_weekly = pd.DataFrame()
    yf_symbol = to_yfinance_symbol(symbol)
    ticker = yf.Ticker(yf_symbol)
    try:
        df_weekly = await asyncio.to_thread(ticker.history, period="5y", interval="1wk")
    except Exception as e:
        print(f"[NOMISMA ANALYSIS] Weekly fetch bypassed: {e}")

    analysis_report = {
        "symbol": symbol.upper(),
        "timestamp": time.time(),
        "intraday_metrics": {},
        "medium_term_metrics": {},
        "long_term_metrics": {}
    }

    # Normalize column names in lists of frames to title-case before technical evaluations
    for d_frame in [df_intra, df_daily, df_weekly]:
        if not d_frame.empty:
            d_frame.columns = [c.title() for c in d_frame.columns]

    if not df_intra.empty:
        df_intra['Log_Returns'] = np.log(df_intra['Close'] / df_intra['Close'].shift(1))
        intraday_vol = df_intra['Log_Returns'].std() * np.sqrt(df_intra.shape[0])
        avg_volume_15m = float(df_intra['Volume'].mean())
        last_price = float(df_intra['Close'].iloc[-1])
        
        analysis_report["intraday_metrics"] = {
            "datapoints_fetched": len(df_intra),
            "last_price": last_price,
            "intraday_volatility": float(intraday_vol) if not np.isnan(intraday_vol) else 0.0,
            "avg_candle_volume": avg_volume_15m
        }

    if not df_daily.empty:
        df_daily['SMA_50'] = df_daily['Close'].rolling(window=50).mean()
        df_daily['SMA_200'] = df_daily['Close'].rolling(window=200).mean()
        
        delta = df_daily['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df_daily['RSI_14'] = 100 - (100 / (1 + rs))
        
        last_row = df_daily.iloc[-1]
        analysis_report["medium_term_metrics"] = {
            "datapoints_fetched": len(df_daily),
            "sma_50": float(last_row['SMA_50']) if not np.isnan(last_row['SMA_50']) else None,
            "sma_200": float(last_row['SMA_200']) if not np.isnan(last_row['SMA_200']) else None,
            "rsi_14": float(last_row['RSI_14']) if not np.isnan(last_row['RSI_14']) else None,
            "current_trend": "BULLISH" if last_row['Close'] > last_row['SMA_50'] else "BEARISH"
        }

    if not df_weekly.empty:
        rolling_highs = df_weekly['High'].rolling(window=10, center=True).max()
        rolling_lows = df_weekly['Low'].rolling(window=10, center=True).min()
        
        supports = df_weekly[df_weekly['Low'] == rolling_lows]['Low'].unique()[-3:].tolist()
        resistances = df_weekly[df_weekly['High'] == rolling_highs]['High'].unique()[-3:].tolist()
        
        analysis_report["long_term_metrics"] = {
            "datapoints_fetched": len(df_weekly),
            "macro_supports": [float(s) for s in supports],
            "macro_resistances": [float(r) for r in resistances]
        }

    print(f"[NOMISMA ANALYSIS SUCCESS] Analytics block completed for {symbol}.")
    return analysis_report

# ─── Dynamic Dataframe Downloader for Strategy Evaluators ───────────────────

async def get_raw_dataframes(symbol: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Retrieves standard Raw dataframes for strategy processing."""
    base_symbol, exchange = parse_symbol_for_angel_one(symbol)
    yf_symbol = to_yfinance_symbol(symbol)
    ticker = yf.Ticker(yf_symbol)
    
    # 1. 15m Intraday Dataframe
    try:
        df_intra = await asyncio.to_thread(ticker.history, period="1mo", interval="15m")
    except Exception:
        df_intra = pd.DataFrame()
        
    # 2. Daily Dataframe
    try:
        df_daily = await asyncio.to_thread(ticker.history, period="1y", interval="1d")
    except Exception:
        df_daily = pd.DataFrame()
        
    # 3. Weekly Dataframe
    try:
        df_weekly = await asyncio.to_thread(ticker.history, period="5y", interval="1wk")
    except Exception:
        df_weekly = pd.DataFrame()
        
    # Standardize headers across all technical sources to avoid mixed casings
    for df in [df_intra, df_daily, df_weekly]:
        if not df.empty:
            df.columns = [c.title() for c in df.columns]

    return df_intra, df_daily, df_weekly

# ─── Local Reasoning Explainer (replaces the old Gemini call) ───────────────
#
# This used to call out to the Gemini API (gemini-1.5-flash / 2.5-flash /
# etc.), which kept 404'ing as Google retired model IDs and was subject to
# quota/429 failures. It now delegates to services/reasoning_engine.py — a
# deterministic, local, zero-network module that reads the exact same
# active_run / indicators / risk_management data this function used to
# build a Gemini prompt from, and returns the same shape of markdown
# string. Function name, arguments, and the caller below are unchanged on
# purpose so nothing downstream (the response payload, the frontend) needs
# to be touched.

async def generate_gemini_explanation(
    symbol: str,
    signal: str,
    horizon: str,
    sentiment_score: int,
    is_bullish_trend: bool,
    detailed_runs: dict,
    timeframe_label: str = "",
    model_name: str = ""
) -> str:
    """
    Local drop-in replacement for the old Gemini-backed explainer.
    Same signature, same return type (markdown str) — no network calls,
    no API key required, no quota/429 risk.
    """
    return await generate_reasoning_explanation(
        symbol=symbol,
        signal=signal,
        horizon=horizon,
        sentiment_score=sentiment_score,
        is_bullish_trend=is_bullish_trend,
        detailed_runs=detailed_runs,
        timeframe_label=timeframe_label,
        model_name=model_name,
    )

# ─── Interval → Strategy Horizon Auto-Router ─────────────────────────────────

# Maps TradingView / yfinance interval strings to the appropriate strategy horizon.
INTERVAL_TO_HORIZON: Dict[str, str] = {
    "1m":  "intraday",
    "3m":  "intraday",
    "5m":  "intraday",
    "10m": "intraday",
    "15m": "intraday",
    "30m": "intraday",
    "1h":  "swing",
    "4h":  "swing",
    "1d":  "swing",
    "1w":  "investment",
    "1wk": "investment",
    "1mo": "investment",
}

# Human-readable timeframe labels for Gemini prompt context
INTERVAL_LABEL: Dict[str, str] = {
    "1m":  "1-Minute (Scalping / HFT)",
    "3m":  "3-Minute (Micro Scalping)",
    "5m":  "5-Minute (Short Scalping)",
    "10m": "10-Minute (Intraday)",
    "15m": "15-Minute (Intraday Mean Reversion)",
    "30m": "30-Minute (Intraday Swing)",
    "1h":  "1-Hour (Momentum Swing)",
    "4h":  "4-Hour (Multi-Session Swing)",
    "1d":  "Daily (Swing Momentum)",
    "1w":  "Weekly (Position / Long-Term Value)",
    "1wk": "Weekly (Position / Long-Term Value)",
    "1mo": "Monthly (Long-Term Investment)",
}


# ─── Corporate Multi-Strategy Analytical Matrix ──────────────────────────────

async def corporate_strategy_matrix(
    symbol: str,
    override_horizon: Optional[str] = None,
    interval: Optional[str] = None
) -> Dict[str, Any]:
    """
    Auto-routing Decision Matrix: dynamically selects the appropriate strategy model
    based on the active chart interval (timeframe). No manual horizon selection needed.

    Priority order:
      1. override_horizon (explicit user/API param) — still accepted for legacy calls
      2. interval → INTERVAL_TO_HORIZON mapping (new auto-detection path)
      3. Signal hierarchy fallback
    """
    from services.sentiment import analyze_sentiment

    base_symbol, exchange = parse_symbol_for_angel_one(symbol)
    yf_symbol = to_yfinance_symbol(symbol)
    ticker = yf.Ticker(yf_symbol)

    # 1. Resolve active interval and associated style parameters
    resolved_interval = (interval or "1d").lower()
    horizon = INTERVAL_TO_HORIZON.get(resolved_interval, "swing")
    timeframe_label = INTERVAL_LABEL.get(resolved_interval, "Daily (Swing Momentum)")

    # 2. Download timeframe-specific candle history
    #
    # IMPORTANT: yfinance only understands a fixed set of interval strings:
    # 1m, 2m, 5m, 15m, 30m, 60m/1h, 90m, 1d, 5d, 1wk, 1mo, 3mo.
    # Several of OUR strategy intervals are NOT valid yfinance intervals
    # (3m, 4h, 1w) — passing them straight through silently made yfinance
    # raise, which was caught and produced an EMPTY dataframe. That's why the
    # 3-Minute (and any 4h/1w) strategy always showed null indicators,
    # HOLD, and 0% confidence: it never actually got any candle data.
    # Every strategy interval is now mapped to the closest real yfinance
    # interval it can actually fetch data for.
    YF_INTERVAL_MAP = {
        "1m":  "1m",
        "3m":  "2m",   # yfinance has no native 3m bucket — 2m is the closest supported
        "5m":  "5m",
        "10m": "5m",   # yfinance has no native 10m bucket
        "15m": "15m",
        "30m": "30m",
        "1h":  "60m",
        "4h":  "60m",  # yfinance has no native 4h bucket — hourly candles are aggregated by the strategy itself
        "1d":  "1d",
        "1w":  "1wk",  # yfinance uses "1wk", not "1w"
        "1wk": "1wk",
        "1mo": "1mo",
    }
    yf_interval = YF_INTERVAL_MAP.get(resolved_interval, "1d")

    try:
        period_map = {
            "1m": "1d", "3m": "5d", "5m": "5d", "10m": "7d", "15m": "1mo", "30m": "1mo",
            "1h": "2mo", "4h": "3mo", "1d": "1y", "1w": "5y", "1wk": "5y", "1mo": "max"
        }
        df_target = await asyncio.to_thread(
            ticker.history,
            period=period_map.get(resolved_interval, "1y"),
            interval=yf_interval
        )
    except Exception:
        df_target = pd.DataFrame()

    if not df_target.empty:
        df_target.columns = [c.title() for c in df_target.columns]

    # ── Candle resampling for intervals with no native yfinance bucket ──────
    #
    # YF_INTERVAL_MAP above substitutes the nearest *smaller* native yfinance
    # interval for 3m/10m/4h (e.g. 10m -> 5m raw bars). Previously those raw,
    # smaller-timeframe candles were handed to the strategy AS-IS. That's a
    # real bug: every indicator window in e.g. intraday_10m.py (ATR-14,
    # session VWAP, TSI-25/13, a 20-bar Donchian channel) is tuned assuming
    # each row IS a 10-minute bar. Feeding it 5-minute bars silently halves
    # the effective lookback window in real time, which makes the composite
    # score noisier and pulls it toward zero far more often — i.e. it
    # produces exactly the "stuck on HOLD" behavior being reported. Same
    # root cause for 4h (fed raw 60m bars instead of 4-hour bars) and 3m
    # (fed 2m bars instead of 3-minute bars).
    #
    # Fix: actually resample the fetched OHLCV candles up to the intended
    # bucket size before handing them to the strategy function.
    RESAMPLE_TARGET: Dict[str, str] = {
        "3m":  "3min",
        "10m": "10min",
        "4h":  "4h",
    }
    target_rule = RESAMPLE_TARGET.get(resolved_interval)
    if target_rule and not df_target.empty and isinstance(df_target.index, pd.DatetimeIndex):
        try:
            agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
            if "Volume" in df_target.columns:
                agg["Volume"] = "sum"
            df_target = (
                df_target.resample(target_rule, label="right", closed="right")
                .agg(agg)
                .dropna(subset=["Open", "High", "Low", "Close"])
            )
        except Exception:
            # If resampling fails for any reason, fall back to the raw
            # (previous) behavior rather than breaking the route.
            pass

    # Get current closing price
    current_price = 0.0
    if not df_target.empty:
        current_price = float(df_target['Close'].iloc[-1])
    else:
        quote = await get_quote(symbol)
        current_price = quote.get("currentPrice", 0.0)

    # 2b. Reconcile with the LIVE broker price.
    #
    # The value above comes from yfinance's historical candle download, which
    # can lag the real market by anywhere from a few seconds (websocket-fed
    # ticks) to several minutes (REST polling / delayed feeds) depending on
    # the interval. The ticker/Live Telemetry panel, meanwhile, shows Angel
    # One's real-time LTP. That mismatch is exactly why "Last Price" in the
    # Strategy Workspace could disagree with the ticker's LTP. Whenever a
    # live broker price is available for this exact instrument, it becomes
    # the single source of truth for "current price" everywhere below —
    # trend comparison, signal formulation, and the indicator payload the
    # frontend renders as "Last Price".
    live_price = None
    try:
        await angel_service.load_instruments()
        inst_key = f"{base_symbol}_{exchange}"
        inst = angel_service.instruments.get(inst_key)
        if inst and await angel_service.login():
            ltp_data = await angel_service.get_ltp(
                exchange=inst["exchange"],
                tradingsymbol=inst["symbol"],
                symboltoken=inst["token"]
            )
            if ltp_data and ltp_data.get("ltp"):
                live_price = float(ltp_data["ltp"])
    except Exception:
        live_price = None

    if live_price and live_price > 0:
        current_price = live_price

    # 3. Run NLP sentiment analysis
    sentiment_payload = await analyze_sentiment(symbol)
    sentiment_score = sentiment_payload.get("sentiment_score", 0.0)

    # 4. Route execution dynamically to modular strategy functions
    active_run = {
        "action": "HOLD",
        "confidence": 0.0,
        "signal_quality": "NEUTRAL",
        "risk_management": {},
        "indicators": {}
    }
    model_name = "Default Strategy"

    if resolved_interval == "1m":
        active_run = evaluate_scalping_hft_1m(df_target)
        model_name = "1-Minute (Scalping / HFT)"
    elif resolved_interval == "3m":
        active_run = evaluate_micro_scalping_3m(df_target)
        model_name = "3-Minute (Micro Scalping)"
    elif resolved_interval == "5m":
        active_run = evaluate_short_scalping_5m(df_target)
        model_name = "5-Minute (Short Scalping)"
    elif resolved_interval == "10m":
        active_run = evaluate_intraday_10m(df_target)
        model_name = "10-Minute (Intraday)"
    elif resolved_interval == "15m":
        active_run = evaluate_intraday_mean_reversion_15m(df_target)
        model_name = "15-Minute (Intraday Mean Reversion)"
    elif resolved_interval == "30m":
        active_run = evaluate_intraday_swing_30m(df_target)
        model_name = "30-Minute (Intraday Swing)"
    elif resolved_interval == "1h":
        active_run = evaluate_momentum_swing_1h(df_target)
        model_name = "1-Hour (Momentum Swing)"
    elif resolved_interval == "4h":
        active_run = evaluate_multi_session_swing_4h(df_target)
        model_name = "4-Hour (Multi-Session Swing)"
    elif resolved_interval == "1d":
        active_run = evaluate_swing_momentum_1d(df_target, sentiment_score)
        model_name = "Daily (Swing Momentum)"
    elif resolved_interval in ["1w", "1wk"]:
        active_run = evaluate_long_term_value_1w(df_target)
        model_name = "Weekly (Position / Long-Term Value)"
    elif resolved_interval == "1mo":
        active_run = evaluate_long_term_investment_1mo(df_target)
        model_name = "Monthly (Long-Term Investment)"

    # Keep the strategy's own "last price" indicator in lockstep with the
    # reconciled live price above — prevents the Technical Parameters panel
    # from showing a slightly different number than the ticker/LTP.
    if live_price and live_price > 0 and isinstance(active_run.get("indicators"), dict):
        active_run["indicators"]["last_price"] = live_price

    # 5. Formulate canonical signals
    raw_action = active_run.get("action", "HOLD").upper()
    signal = "HOLD"
    if "BUY" in raw_action or "ACCUMULATE" in raw_action:
        signal = "BUY"
    elif "SHORT" in raw_action or "SELL" in raw_action or "REDUCE" in raw_action:
        signal = "SELL"
    elif "WATCH" in raw_action:
        signal = "WATCH"

    # 6. Compute structural trend using data from the SAME horizon/interval as the
    #    active strategy. Previously this always pulled a daily SMA-200 regardless
    #    of which timeframe/strategy was selected, which meant a 10-minute intraday
    #    strategy could show "BEARISH" against a stale daily trend while price was
    #    actually trending up on its own (correct) timeframe. Now each horizon gets
    #    a trend reference computed on matching data:
    #      - intraday (1m-30m): fast SMA computed on the SAME intraday candles
    #        already fetched for this strategy (df_target)
    #      - swing (1h/4h/1d): SMA-200 on daily candles (unchanged, appropriate lens)
    #      - investment (1w/1mo): SMA-52 on weekly candles
    is_bullish_trend = True
    trend_label = "SMA-200 (1D)"
    trend_reference_price = None

    try:
        if horizon == "intraday":
            # Use the exact dataset already downloaded for this strategy run so the
            # trend context can never disagree with a different, mismatched timeframe.
            window = min(50, max(5, len(df_target) - 1)) if not df_target.empty else 0
            if not df_target.empty and window >= 5:
                sma_intraday = df_target['Close'].rolling(window=window).mean()
                trend_reference_price = float(sma_intraday.iloc[-1])
                is_bullish_trend = current_price > trend_reference_price
                trend_label = f"SMA-{window} ({resolved_interval.upper()})"
            else:
                # Not enough candles on this timeframe yet — fall back to a simple
                # first-vs-last comparison within the fetched window rather than
                # borrowing an unrelated (daily) timeframe.
                if not df_target.empty and len(df_target) >= 2:
                    trend_reference_price = float(df_target['Close'].iloc[0])
                    is_bullish_trend = current_price > trend_reference_price
                    trend_label = f"Session Open ({resolved_interval.upper()})"

        elif horizon == "investment":
            df_weekly = await asyncio.to_thread(ticker.history, period="5y", interval="1wk")
            if not df_weekly.empty:
                df_weekly.columns = [c.title() for c in df_weekly.columns]
                window = min(52, max(5, len(df_weekly) - 1))
                sma_weekly = df_weekly['Close'].rolling(window=window).mean()
                trend_reference_price = float(sma_weekly.iloc[-1])
                is_bullish_trend = current_price > trend_reference_price
                trend_label = f"SMA-{window} (1W)"

        else:  # swing: 1h / 4h / 1d — daily SMA-200 remains the correct, matching lens
            df_daily = await asyncio.to_thread(ticker.history, period="1y", interval="1d")
            if not df_daily.empty and len(df_daily) >= 200:
                df_daily.columns = [c.title() for c in df_daily.columns]
                df_daily['SMA_200'] = df_daily['Close'].rolling(window=200).mean()
                trend_reference_price = float(df_daily['SMA_200'].iloc[-1])
                is_bullish_trend = current_price > trend_reference_price
                trend_label = "SMA-200 (1D)"
    except Exception:
        pass

    sentiment_percentage = int((sentiment_score + 1.0) * 50.0)

    # 7. Package analytical sub-runs for structural integrity
    detailed_runs = {
        "active_run": active_run,
        "raw_sentiment_score": sentiment_score,
        "trend_label": trend_label,
        "intraday_mean_reversion": active_run if horizon == "intraday" else {},
        "swing_momentum": active_run if horizon == "swing" else {},
        "long_term_value": active_run if horizon == "investment" else {}
    }

    # 8. Generate reasoning explanation with LLM structures
    gemini_explanation = await generate_gemini_explanation(
        symbol=symbol,
        signal=signal,
        horizon=horizon,
        sentiment_score=sentiment_percentage,
        is_bullish_trend=is_bullish_trend,
        detailed_runs=detailed_runs,
        timeframe_label=timeframe_label,
        model_name=model_name
    )

    return {
        "symbol": symbol.upper(),
        "signal": signal,
        "sentiment_score": sentiment_percentage,
        "is_bullish_trend": is_bullish_trend,
        "trend_label": trend_label,
        "trend_reference_price": trend_reference_price,
        "current_price": current_price,
        "price_source": "live_broker_ltp" if live_price else "historical_candle_close",
        "horizon": horizon,
        "style": horizon,
        "interval": resolved_interval,
        "timeframe_label": timeframe_label,
        "model_name": model_name,
        "timestamp": time.time(),
        "gemini_reasoning": gemini_explanation,
        "signal_quality": active_run.get("signal_quality", "NEUTRAL"),
        "risk_management": active_run.get("risk_management", {}),
        "detailed_runs": detailed_runs,
    }

# ─── Indicator Engine support: generic OHLCV fetch ──────────────────────────
#
# Used by routers/indicators.py (the new Indicator Engine). Plain OHLCV
# fetch for an arbitrary (period, interval) pair via yfinance, title-cased
# and DatetimeIndex'd the same way get_raw_dataframes() above does. Kept
# separate from get_raw_dataframes() because that function returns a fixed
# 15m/1d/1wk trio for strategy evaluation, whereas the indicator router
# needs to serve any period/interval combination a caller asks for.

async def get_ohlcv_dataframe(symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """
    Fetches a single OHLCV dataframe for the given symbol/period/interval
    via yfinance. Returns an empty DataFrame on failure instead of raising,
    consistent with get_raw_dataframes()'s error handling above.
    """
    yf_symbol = to_yfinance_symbol(symbol)
    ticker = yf.Ticker(yf_symbol)
    try:
        df = await asyncio.to_thread(ticker.history, period=period, interval=interval)
    except Exception as e:
        print(f"[INDICATOR ENGINE] OHLCV fetch bypassed for {symbol} ({period}/{interval}): {e}")
        df = pd.DataFrame()

    if not df.empty:
        df.columns = [c.title() for c in df.columns]
    return df


# ─── Indicator Engine support: EVERY Ticker timeframe, individually ─────────
#
# The Ticker/Live Telemetry timeframe selector offers 20 distinct intervals
# (15s/30s/45s, 1-125m, 1-4h, 1d/1w/1mo). yfinance itself only understands a
# fixed set of native interval strings (1m,2m,5m,15m,30m,60m,90m,1d,5d,1wk,
# 1mo,3mo) — exactly the same constraint documented above
# active_strategy_signal()'s YF_INTERVAL_MAP. Previously get_ohlcv_dataframe()
# passed whatever interval string it was given straight to yfinance, so any
# Ticker interval without a native yfinance match (3m, 4m, 10m, 75m, 125m,
# 2h, 3h, 4h, 1w) silently errored into an empty dataframe, and the
# sub-minute ones (15s/30s/45s) always did too. That's why the Indicator tab
# could only ever really show 3 working buckets.
#
# Fix: mirror the strategy engine's map-to-nearest-native + resample-up
# pattern, but for the full 20-interval set, so every single Ticker
# timeframe returns a real (or best-effort, clearly-labelled) OHLCV frame.
#
# NOTE on 15s/30s/45s: yfinance does not provide sub-1-minute historical
# candles for any symbol/exchange combination — 1m is the finest resolution
# available. There is no way to *downsample* 1-minute bars into true 15/30/
# 45-second bars (that data was never captured). These three therefore fall
# back to raw 1-minute candles as the closest available approximation; the
# `is_approximated` flag below tells the frontend to label this honestly
# instead of implying second-level precision that doesn't exist upstream.
TICKER_INTERVAL_TO_YF: Dict[str, str] = {
    "15s": "1m", "30s": "1m", "45s": "1m",   # no native sub-minute data; nearest available
    "1m": "1m", "2m": "2m", "3m": "2m", "4m": "2m", "5m": "5m",
    "10m": "5m", "15m": "15m", "30m": "30m", "75m": "30m", "125m": "60m",
    "1h": "60m", "2h": "60m", "3h": "60m", "4h": "60m",
    "1d": "1d", "1w": "1wk", "1mo": "1mo",
}

# Lookback window requested from yfinance per Ticker interval — wide enough
# for every indicator's longest lookback (e.g. 200-period MAs) to have real
# data at that bar size, without over-fetching on the finer intervals.
TICKER_INTERVAL_TO_PERIOD: Dict[str, str] = {
    "15s": "5d", "30s": "5d", "45s": "5d",
    "1m": "5d", "2m": "5d", "3m": "5d", "4m": "5d", "5m": "1mo",
    "10m": "1mo", "15m": "1mo", "30m": "1mo", "75m": "3mo", "125m": "3mo",
    "1h": "3mo", "2h": "6mo", "3h": "6mo", "4h": "6mo",
    "1d": "1y", "1w": "5y", "1mo": "max",
}

# Pandas resample rule for Ticker intervals whose bar size doesn't match the
# native yfinance interval fetched above — upsampling raw candles into the
# actual requested bucket (e.g. 5x 15m candles -> one 75m candle), same
# technique as active_strategy_signal()'s RESAMPLE_TARGET.
TICKER_RESAMPLE_RULE: Dict[str, str] = {
    "3m": "3min", "4m": "4min", "10m": "10min",
    "75m": "75min", "125m": "125min",
    "2h": "2h", "3h": "3h", "4h": "4h",
}


async def get_ohlcv_dataframe_for_ticker_interval(symbol: str, ticker_interval: str) -> Tuple[pd.DataFrame, bool]:
    """
    Fetches (and, where necessary, resamples) an OHLCV dataframe for ANY of
    the 20 exact Ticker timeframes, not just the 3 fixed indicator-engine
    buckets. Returns (dataframe, is_approximated) where is_approximated is
    True only for 15s/30s/45s (see note above) so the caller/frontend can
    surface that honestly rather than silently.
    """
    resolved = (ticker_interval or "1d").lower()
    yf_interval = TICKER_INTERVAL_TO_YF.get(resolved, "1d")
    period = TICKER_INTERVAL_TO_PERIOD.get(resolved, "6mo")
    is_approximated = resolved in ("15s", "30s", "45s")

    yf_symbol = to_yfinance_symbol(symbol)
    ticker = yf.Ticker(yf_symbol)

    async def _fetch(fetch_period: str) -> pd.DataFrame:
        try:
            fetched = await asyncio.to_thread(ticker.history, period=fetch_period, interval=yf_interval)
        except Exception as e:
            print(f"[INDICATOR ENGINE] OHLCV fetch bypassed for {symbol} ({fetch_period}/{yf_interval} "
                  f"for ticker interval {resolved}): {e}")
            return pd.DataFrame()
        if not fetched.empty:
            fetched.columns = [c.title() for c in fetched.columns]
        return fetched

    df = await _fetch(period)
    print(f"[INDICATOR ENGINE] {symbol} ticker_interval={resolved} -> yf({yf_interval}/{period}): "
          f"{len(df)} raw bars fetched.")

    # FALLBACK: yfinance intraday endpoints occasionally return a thin or
    # empty frame for the configured lookback window (data lag, a recent
    # market holiday, a symbol with low recent liquidity, etc.) even though
    # the interval itself is valid and real data exists further back. Widen
    # the lookback once before giving up, so a legitimately-supported
    # interval doesn't render as permanently blank just because the default
    # window happened to be too narrow at request time.
    WIDER_PERIOD_FALLBACK = {"5d": "1mo", "1mo": "3mo", "3mo": "6mo", "6mo": "1y"}
    if df.empty and period in WIDER_PERIOD_FALLBACK:
        wider = WIDER_PERIOD_FALLBACK[period]
        print(f"[INDICATOR ENGINE] {symbol} ticker_interval={resolved}: 0 bars at period={period}, "
              f"retrying with wider period={wider}...")
        df = await _fetch(wider)
        print(f"[INDICATOR ENGINE] {symbol} ticker_interval={resolved} -> yf({yf_interval}/{wider}) "
              f"fallback: {len(df)} raw bars fetched.")

    resample_rule = TICKER_RESAMPLE_RULE.get(resolved)
    if resample_rule and not df.empty and isinstance(df.index, pd.DatetimeIndex):
        try:
            agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
            if "Volume" in df.columns:
                agg["Volume"] = "sum"
            resampled = (
                df.resample(resample_rule, label="right", closed="right")
                .agg(agg)
                .dropna(subset=["Open", "High", "Low", "Close"])
            )
            print(f"[INDICATOR ENGINE] {symbol} ticker_interval={resolved}: resampled "
                  f"{len(df)} raw {yf_interval} bars -> {len(resampled)} {resample_rule} bars.")
            df = resampled
        except Exception as e:
            # If resampling fails for any reason, fall back to the raw
            # (native-interval) candles rather than breaking the route.
            print(f"[INDICATOR ENGINE] {symbol} ticker_interval={resolved}: resample to "
                  f"{resample_rule} failed ({e}), using raw {yf_interval} bars instead.")

    if df.empty:
        print(f"[INDICATOR ENGINE] {symbol} ticker_interval={resolved}: returning EMPTY dataframe "
              f"after all fetch/fallback attempts -- indicators for this timeframe will legitimately "
              f"show INSUFFICIENT_DATA / no candlestick data.")

    return df, is_approximated