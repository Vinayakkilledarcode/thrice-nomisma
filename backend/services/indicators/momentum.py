# services/indicators/momentum.py
import numpy as np
import pandas as pd

from .registry import indicator, atr, last_float

CATEGORY = "momentum"


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _register_rsi_family(periods):
    for period in periods:
        def _calc(df: pd.DataFrame, _period=period) -> dict:
            rsi = _rsi(df["Close"], _period)
            val = last_float(rsi, default=50.0)
            if val >= 70:
                signal = "OVERBOUGHT"
            elif val <= 30:
                signal = "OVERSOLD"
            else:
                signal = "NEUTRAL"
            return {"value": round(val, 2), "signal": signal}
        indicator(f"rsi_{period}", CATEGORY, min_bars=period + 5, description=f"RSI ({period})")(_calc)


_register_rsi_family([7, 14, 21])


@indicator("stochastic_14_3", CATEGORY, min_bars=20, description="Stochastic Oscillator %K(14)/%D(3)")
def stochastic(df: pd.DataFrame) -> dict:
    low_min = df["Low"].rolling(14).min()
    high_max = df["High"].rolling(14).max()
    k = ((df["Close"] - low_min) / (high_max - low_min).replace(0, np.nan)) * 100
    d = k.rolling(3).mean()
    k_last, d_last = last_float(k, 50.0), last_float(d, 50.0)
    if k_last >= 80:
        signal = "OVERBOUGHT"
    elif k_last <= 20:
        signal = "OVERSOLD"
    else:
        signal = "NEUTRAL"
    return {"value": {"k": round(k_last, 2), "d": round(d_last, 2)}, "signal": signal}


@indicator("stochastic_rsi_14", CATEGORY, min_bars=35, description="Stochastic RSI (14)")
def stochastic_rsi(df: pd.DataFrame) -> dict:
    rsi = _rsi(df["Close"], 14)
    low_min = rsi.rolling(14).min()
    high_max = rsi.rolling(14).max()
    srsi = ((rsi - low_min) / (high_max - low_min).replace(0, np.nan)) * 100
    val = last_float(srsi, 50.0)
    signal = "OVERBOUGHT" if val >= 80 else ("OVERSOLD" if val <= 20 else "NEUTRAL")
    return {"value": round(val, 2), "signal": signal}


@indicator("macd_12_26_9", CATEGORY, min_bars=35, description="MACD (12,26,9)")
def macd(df: pd.DataFrame) -> dict:
    ema_fast = df["Close"].ewm(span=12, adjust=False).mean()
    ema_slow = df["Close"].ewm(span=26, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    m, s, h = last_float(macd_line), last_float(signal_line), last_float(hist)
    return {"value": {"macd": round(m, 4), "signal_line": round(s, 4), "histogram": round(h, 4)},
            "signal": "BULLISH" if m > s else ("BEARISH" if m < s else "NEUTRAL")}


@indicator("cci_20", CATEGORY, min_bars=20, description="Commodity Channel Index (20)")
def cci_20(df: pd.DataFrame) -> dict:
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    sma = tp.rolling(20).mean()
    mean_dev = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    cci = (tp - sma) / (0.015 * mean_dev.replace(0, np.nan))
    val = last_float(cci, 0.0)
    signal = "OVERBOUGHT" if val >= 100 else ("OVERSOLD" if val <= -100 else "NEUTRAL")
    return {"value": round(val, 2), "signal": signal}


@indicator("roc_12", CATEGORY, min_bars=15, description="Rate of Change (12)")
def roc_12(df: pd.DataFrame) -> dict:
    close = df["Close"]
    roc = ((close - close.shift(12)) / close.shift(12).replace(0, np.nan)) * 100
    val = last_float(roc, 0.0)
    return {"value": round(val, 2), "signal": "BULLISH" if val > 0 else ("BEARISH" if val < 0 else "NEUTRAL")}


@indicator("momentum_10", CATEGORY, min_bars=12, description="Momentum (Close - Close[10])")
def momentum_10(df: pd.DataFrame) -> dict:
    close = df["Close"]
    mom = close - close.shift(10)
    val = last_float(mom, 0.0)
    return {"value": round(val, 4), "signal": "BULLISH" if val > 0 else ("BEARISH" if val < 0 else "NEUTRAL")}


@indicator("trix_15", CATEGORY, min_bars=50, description="TRIX (15) -- % rate of change of triple-smoothed EMA")
def trix_15(df: pd.DataFrame) -> dict:
    ema1 = df["Close"].ewm(span=15, adjust=False).mean()
    ema2 = ema1.ewm(span=15, adjust=False).mean()
    ema3 = ema2.ewm(span=15, adjust=False).mean()
    trix = ema3.pct_change() * 100
    val = last_float(trix, 0.0)
    return {"value": round(val, 4), "signal": "BULLISH" if val > 0 else ("BEARISH" if val < 0 else "NEUTRAL")}


@indicator("williams_r_14", CATEGORY, min_bars=15, description="Williams %R (14)")
def williams_r_14(df: pd.DataFrame) -> dict:
    high_max = df["High"].rolling(14).max()
    low_min = df["Low"].rolling(14).min()
    wr = ((high_max - df["Close"]) / (high_max - low_min).replace(0, np.nan)) * -100
    val = last_float(wr, -50.0)
    signal = "OVERBOUGHT" if val >= -20 else ("OVERSOLD" if val <= -80 else "NEUTRAL")
    return {"value": round(val, 2), "signal": signal}


@indicator("ultimate_oscillator", CATEGORY, min_bars=30, description="Ultimate Oscillator (7,14,28)")
def ultimate_oscillator(df: pd.DataFrame) -> dict:
    close, low, high = df["Close"], df["Low"], df["High"]
    prev_close = close.shift(1)
    bp = close - np.minimum(low, prev_close)
    tr = np.maximum(high, prev_close) - np.minimum(low, prev_close)
    avg7 = bp.rolling(7).sum() / tr.rolling(7).sum().replace(0, np.nan)
    avg14 = bp.rolling(14).sum() / tr.rolling(14).sum().replace(0, np.nan)
    avg28 = bp.rolling(28).sum() / tr.rolling(28).sum().replace(0, np.nan)
    uo = 100 * ((4 * avg7) + (2 * avg14) + avg28) / 7
    val = last_float(uo, 50.0)
    signal = "OVERBOUGHT" if val >= 70 else ("OVERSOLD" if val <= 30 else "NEUTRAL")
    return {"value": round(val, 2), "signal": signal}


@indicator("awesome_oscillator", CATEGORY, min_bars=34, description="Awesome Oscillator (5,34 on midpoint)")
def awesome_oscillator(df: pd.DataFrame) -> dict:
    mid = (df["High"] + df["Low"]) / 2.0
    ao = mid.rolling(5).mean() - mid.rolling(34).mean()
    val, prev = last_float(ao, 0.0), (last_float(ao.iloc[:-1], 0.0) if len(ao) > 1 else 0.0)
    signal = "BULLISH" if val > 0 else ("BEARISH" if val < 0 else "NEUTRAL")
    return {"value": round(val, 4), "signal": signal, "rising": bool(val > prev)}


@indicator("ppo_12_26_9", CATEGORY, min_bars=35, description="Percentage Price Oscillator (12,26,9)")
def ppo(df: pd.DataFrame) -> dict:
    ema_fast = df["Close"].ewm(span=12, adjust=False).mean()
    ema_slow = df["Close"].ewm(span=26, adjust=False).mean()
    ppo_line = ((ema_fast - ema_slow) / ema_slow.replace(0, np.nan)) * 100
    signal_line = ppo_line.ewm(span=9, adjust=False).mean()
    p, s = last_float(ppo_line), last_float(signal_line)
    return {"value": {"ppo": round(p, 4), "signal_line": round(s, 4)},
            "signal": "BULLISH" if p > s else ("BEARISH" if p < s else "NEUTRAL")}


@indicator("coppock_curve", CATEGORY, min_bars=25, description="Coppock Curve (ROC14+ROC11, WMA10)")
def coppock_curve(df: pd.DataFrame) -> dict:
    close = df["Close"]
    roc14 = ((close - close.shift(14)) / close.shift(14).replace(0, np.nan)) * 100
    roc11 = ((close - close.shift(11)) / close.shift(11).replace(0, np.nan)) * 100
    roc_sum = roc14 + roc11
    weights = np.arange(1, 11)
    coppock = roc_sum.rolling(10).apply(
        lambda x: np.dot(x, weights) / weights.sum() if len(x) == 10 else np.nan, raw=True
    )
    val = last_float(coppock, 0.0)
    return {"value": round(val, 4), "signal": "BULLISH" if val > 0 else ("BEARISH" if val < 0 else "NEUTRAL")}


@indicator("chande_momentum_9", CATEGORY, min_bars=12, description="Chande Momentum Oscillator (9)")
def chande_momentum(df: pd.DataFrame) -> dict:
    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(9).sum()
    loss = (-delta.where(delta < 0, 0.0)).rolling(9).sum()
    cmo = 100 * (gain - loss) / (gain + loss).replace(0, np.nan)
    val = last_float(cmo, 0.0)
    signal = "OVERBOUGHT" if val >= 50 else ("OVERSOLD" if val <= -50 else "NEUTRAL")
    return {"value": round(val, 2), "signal": signal}


@indicator("fisher_transform_10", CATEGORY, min_bars=15, description="Ehlers Fisher Transform (10)")
def fisher_transform(df: pd.DataFrame) -> dict:
    period = 10
    mid = (df["High"] + df["Low"]) / 2.0
    low_min = mid.rolling(period).min()
    high_max = mid.rolling(period).max()
    raw = 2 * ((mid - low_min) / (high_max - low_min).replace(0, np.nan) - 0.5)
    raw = raw.clip(-0.999, 0.999).fillna(0.0)
    fisher = 0.5 * np.log((1 + raw) / (1 - raw))
    fisher = fisher.ewm(span=3, adjust=False).mean()  # light smoothing, standard practice
    val = last_float(fisher, 0.0)
    prev = last_float(fisher.iloc[:-1], val) if len(fisher) > 1 else val
    signal = "BULLISH" if val > prev else ("BEARISH" if val < prev else "NEUTRAL")
    return {"value": round(val, 4), "signal": signal}
