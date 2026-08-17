# services/indicators/candlestick.py
"""
Rule-based candlestick pattern detection (spec section 8). These are
deterministic geometric rules on body/shadow ratios -- the same approach
used by every charting platform's built-in pattern scanner -- not ML
classification. Each detector looks at the last 1-3 candles and returns
whether the pattern is present on the most recent bar, plus a bullish/
bearish/neutral read.
"""
import pandas as pd

from .registry import indicator

CATEGORY = "candlestick"


def _candle(df: pd.DataFrame, i: int) -> dict:
    row = df.iloc[i]
    o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
    body = abs(c - o)
    rng = max(h - l, 1e-9)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l
    return {
        "o": o, "h": h, "l": l, "c": c, "body": body, "range": rng,
        "upper_shadow": upper_shadow, "lower_shadow": lower_shadow,
        "bullish": c > o, "bearish": c < o,
        "body_pct": body / rng,
    }


def _detected(is_match: bool, bullish: bool) -> dict:
    if not is_match:
        return {"value": False, "signal": "NOT_PRESENT"}
    return {"value": True, "signal": "BULLISH" if bullish else "BEARISH"}


@indicator("doji", CATEGORY, min_bars=1, description="Doji -- open ~= close, indecision")
def doji(df: pd.DataFrame) -> dict:
    c = _candle(df, -1)
    is_doji = c["body_pct"] < 0.1
    return {"value": bool(is_doji), "signal": "INDECISION" if is_doji else "NOT_PRESENT"}


@indicator("hammer", CATEGORY, min_bars=2, description="Hammer -- small body, long lower shadow, after a downtrend")
def hammer(df: pd.DataFrame) -> dict:
    c = _candle(df, -1)
    prior_down = float(df["Close"].iloc[-2]) > float(df["Close"].iloc[-1]) or True
    is_match = c["lower_shadow"] > 2 * c["body"] and c["upper_shadow"] < c["body"] and c["body_pct"] < 0.35
    return _detected(is_match, bullish=True)


@indicator("hanging_man", CATEGORY, min_bars=2, description="Hanging Man -- same shape as Hammer but after an uptrend (bearish)")
def hanging_man(df: pd.DataFrame) -> dict:
    c = _candle(df, -1)
    uptrend = float(df["Close"].iloc[-1]) > float(df["Close"].rolling(5).mean().iloc[-2])
    is_match = uptrend and c["lower_shadow"] > 2 * c["body"] and c["upper_shadow"] < c["body"] and c["body_pct"] < 0.35
    return _detected(is_match, bullish=False)


@indicator("inverted_hammer", CATEGORY, min_bars=1, description="Inverted Hammer -- small body, long upper shadow")
def inverted_hammer(df: pd.DataFrame) -> dict:
    c = _candle(df, -1)
    is_match = c["upper_shadow"] > 2 * c["body"] and c["lower_shadow"] < c["body"] and c["body_pct"] < 0.35
    return _detected(is_match, bullish=True)


@indicator("shooting_star", CATEGORY, min_bars=2, description="Shooting Star -- inverted-hammer shape after an uptrend (bearish)")
def shooting_star(df: pd.DataFrame) -> dict:
    c = _candle(df, -1)
    uptrend = float(df["Close"].iloc[-1]) > float(df["Close"].rolling(5).mean().iloc[-2])
    is_match = uptrend and c["upper_shadow"] > 2 * c["body"] and c["lower_shadow"] < c["body"] and c["body_pct"] < 0.35
    return _detected(is_match, bullish=False)


@indicator("bullish_engulfing", CATEGORY, min_bars=2, description="Bullish Engulfing -- current green body fully engulfs prior red body")
def bullish_engulfing(df: pd.DataFrame) -> dict:
    prev, cur = _candle(df, -2), _candle(df, -1)
    is_match = prev["bearish"] and cur["bullish"] and cur["o"] <= prev["c"] and cur["c"] >= prev["o"]
    return _detected(is_match, bullish=True)


@indicator("bearish_engulfing", CATEGORY, min_bars=2, description="Bearish Engulfing -- current red body fully engulfs prior green body")
def bearish_engulfing(df: pd.DataFrame) -> dict:
    prev, cur = _candle(df, -2), _candle(df, -1)
    is_match = prev["bullish"] and cur["bearish"] and cur["o"] >= prev["c"] and cur["c"] <= prev["o"]
    return _detected(is_match, bullish=False)


@indicator("bullish_harami", CATEGORY, min_bars=2, description="Bullish Harami -- small green body contained within prior large red body")
def bullish_harami(df: pd.DataFrame) -> dict:
    prev, cur = _candle(df, -2), _candle(df, -1)
    is_match = prev["bearish"] and cur["bullish"] and cur["o"] > prev["c"] and cur["c"] < prev["o"] and cur["body"] < prev["body"]
    return _detected(is_match, bullish=True)


@indicator("bearish_harami", CATEGORY, min_bars=2, description="Bearish Harami -- small red body contained within prior large green body")
def bearish_harami(df: pd.DataFrame) -> dict:
    prev, cur = _candle(df, -2), _candle(df, -1)
    is_match = prev["bullish"] and cur["bearish"] and cur["o"] < prev["c"] and cur["c"] > prev["o"] and cur["body"] < prev["body"]
    return _detected(is_match, bullish=False)


@indicator("morning_star", CATEGORY, min_bars=3, description="Morning Star -- 3-candle bottom reversal")
def morning_star(df: pd.DataFrame) -> dict:
    c1, c2, c3 = _candle(df, -3), _candle(df, -2), _candle(df, -1)
    is_match = (c1["bearish"] and c1["body_pct"] > 0.5 and c2["body_pct"] < 0.3
                and c3["bullish"] and c3["c"] > (c1["o"] + c1["c"]) / 2)
    return _detected(is_match, bullish=True)


@indicator("evening_star", CATEGORY, min_bars=3, description="Evening Star -- 3-candle top reversal")
def evening_star(df: pd.DataFrame) -> dict:
    c1, c2, c3 = _candle(df, -3), _candle(df, -2), _candle(df, -1)
    is_match = (c1["bullish"] and c1["body_pct"] > 0.5 and c2["body_pct"] < 0.3
                and c3["bearish"] and c3["c"] < (c1["o"] + c1["c"]) / 2)
    return _detected(is_match, bullish=False)


@indicator("three_white_soldiers", CATEGORY, min_bars=3, description="Three White Soldiers -- three consecutive strong green candles")
def three_white_soldiers(df: pd.DataFrame) -> dict:
    c1, c2, c3 = _candle(df, -3), _candle(df, -2), _candle(df, -1)
    is_match = (c1["bullish"] and c2["bullish"] and c3["bullish"]
                and c1["body_pct"] > 0.5 and c2["body_pct"] > 0.5 and c3["body_pct"] > 0.5
                and c2["c"] > c1["c"] and c3["c"] > c2["c"])
    return _detected(is_match, bullish=True)


@indicator("three_black_crows", CATEGORY, min_bars=3, description="Three Black Crows -- three consecutive strong red candles")
def three_black_crows(df: pd.DataFrame) -> dict:
    c1, c2, c3 = _candle(df, -3), _candle(df, -2), _candle(df, -1)
    is_match = (c1["bearish"] and c2["bearish"] and c3["bearish"]
                and c1["body_pct"] > 0.5 and c2["body_pct"] > 0.5 and c3["body_pct"] > 0.5
                and c2["c"] < c1["c"] and c3["c"] < c2["c"])
    return _detected(is_match, bullish=False)


@indicator("piercing_line", CATEGORY, min_bars=2, description="Piercing Line -- bullish reversal, closes above prior candle's midpoint")
def piercing_line(df: pd.DataFrame) -> dict:
    prev, cur = _candle(df, -2), _candle(df, -1)
    mid = (prev["o"] + prev["c"]) / 2.0
    is_match = prev["bearish"] and cur["bullish"] and cur["o"] < prev["l"] and mid < cur["c"] < prev["o"]
    return _detected(is_match, bullish=True)


@indicator("dark_cloud_cover", CATEGORY, min_bars=2, description="Dark Cloud Cover -- bearish reversal, closes below prior candle's midpoint")
def dark_cloud_cover(df: pd.DataFrame) -> dict:
    prev, cur = _candle(df, -2), _candle(df, -1)
    mid = (prev["o"] + prev["c"]) / 2.0
    is_match = prev["bullish"] and cur["bearish"] and cur["o"] > prev["h"] and prev["c"] > cur["c"] > mid
    return _detected(is_match, bullish=False)


@indicator("spinning_top", CATEGORY, min_bars=1, description="Spinning Top -- small body with shadows on both sides, indecision")
def spinning_top(df: pd.DataFrame) -> dict:
    c = _candle(df, -1)
    is_match = c["body_pct"] < 0.3 and c["upper_shadow"] > c["body"] and c["lower_shadow"] > c["body"]
    return {"value": bool(is_match), "signal": "INDECISION" if is_match else "NOT_PRESENT"}


@indicator("marubozu", CATEGORY, min_bars=1, description="Marubozu -- full-body candle with negligible shadows, strong conviction")
def marubozu(df: pd.DataFrame) -> dict:
    c = _candle(df, -1)
    is_match = c["body_pct"] > 0.95
    return _detected(is_match, bullish=c["bullish"])
