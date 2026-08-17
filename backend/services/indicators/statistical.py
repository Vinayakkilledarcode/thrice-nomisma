# services/indicators/statistical.py
import numpy as np
import pandas as pd

from .registry import indicator, last_float

CATEGORY = "statistical"


@indicator("zscore_20", CATEGORY, min_bars=20, description="Z-Score of Close vs its 20-period mean/std")
def zscore_20(df: pd.DataFrame) -> dict:
    close = df["Close"]
    mean = close.rolling(20).mean()
    std = close.rolling(20).std().replace(0, np.nan)
    z = (close - mean) / std
    val = last_float(z, 0.0)
    signal = "OVERBOUGHT" if val >= 2 else ("OVERSOLD" if val <= -2 else "NEUTRAL")
    return {"value": round(val, 3), "signal": signal}


@indicator("price_volume_correlation_20", CATEGORY, min_bars=20,
           description="Rolling Pearson correlation between Close returns and Volume (20)")
def price_volume_correlation(df: pd.DataFrame) -> dict:
    ret = df["Close"].pct_change()
    vol = df["Volume"].astype(float)
    corr = ret.rolling(20).corr(vol)
    val = last_float(corr, 0.0)
    return {"value": round(val, 3), "signal": "NEUTRAL"}


@indicator("skewness_20", CATEGORY, min_bars=20, description="Rolling skewness of daily returns (20)")
def skewness_20(df: pd.DataFrame) -> dict:
    ret = df["Close"].pct_change()
    skew = ret.rolling(20).skew()
    val = last_float(skew, 0.0)
    return {"value": round(val, 3), "signal": "NEUTRAL"}


@indicator("kurtosis_20", CATEGORY, min_bars=20, description="Rolling excess kurtosis of daily returns (20)")
def kurtosis_20(df: pd.DataFrame) -> dict:
    ret = df["Close"].pct_change()
    kurt = ret.rolling(20).kurt()
    val = last_float(kurt, 0.0)
    return {"value": round(val, 3), "signal": "FAT_TAILED" if val > 3 else "NEUTRAL"}


@indicator("hurst_exponent_100", CATEGORY, min_bars=100,
           description="Hurst Exponent (rescaled-range style log-log fit) -- <0.5 mean-reverting, >0.5 trending")
def hurst_exponent(df: pd.DataFrame) -> dict:
    series = df["Close"].tail(100).values
    lags = range(2, 20)
    try:
        tau = [np.sqrt(np.std(np.subtract(series[lag:], series[:-lag]))) for lag in lags]
        reg = np.polyfit(np.log(list(lags)), np.log(tau), 1)
        h = float(reg[0] * 2.0)
    except Exception:
        h = 0.5
    signal = "MEAN_REVERTING" if h < 0.45 else ("TRENDING" if h > 0.55 else "RANDOM_WALK")
    return {"value": round(h, 3), "signal": signal}


@indicator("linreg_r2_20", CATEGORY, min_bars=20, description="R^2 of a linear fit to Close over the last 20 bars (trend quality)")
def linreg_r2(df: pd.DataFrame) -> dict:
    y = df["Close"].tail(20).values
    x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1)
    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - (ss_res / ss_tot) if ss_tot else 0.0
    return {"value": round(float(r2), 4), "signal": "STRONG_TREND" if r2 > 0.7 else "WEAK_TREND"}


@indicator("percentile_rank_252", CATEGORY, min_bars=30,
           description="Current Close's percentile rank within its own trailing window (up to 252 bars)")
def percentile_rank(df: pd.DataFrame) -> dict:
    window = df["Close"].tail(min(252, len(df)))
    price = float(df["Close"].iloc[-1])
    rank = float((window < price).sum()) / len(window) * 100
    signal = "NEAR_HIGH" if rank >= 90 else ("NEAR_LOW" if rank <= 10 else "NEUTRAL")
    return {"value": round(rank, 2), "signal": signal}


@indicator("rolling_sharpe_20", CATEGORY, min_bars=21, description="Rolling Sharpe ratio of daily returns (20, rf=0)")
def rolling_sharpe(df: pd.DataFrame) -> dict:
    ret = df["Close"].pct_change()
    mean = ret.rolling(20).mean()
    std = ret.rolling(20).std().replace(0, np.nan)
    sharpe = (mean / std) * np.sqrt(252)
    val = last_float(sharpe, 0.0)
    return {"value": round(val, 3), "signal": "BULLISH" if val > 1 else ("BEARISH" if val < -1 else "NEUTRAL")}


@indicator("autocorrelation_lag1_20", CATEGORY, min_bars=25,
           description="Lag-1 autocorrelation of daily returns over a rolling 20-bar window")
def autocorrelation_lag1(df: pd.DataFrame) -> dict:
    ret = df["Close"].pct_change().dropna().tail(20)
    if len(ret) < 5:
        return {"value": 0.0, "signal": "NEUTRAL"}
    ac = ret.autocorr(lag=1)
    val = 0.0 if pd.isna(ac) else float(ac)
    signal = "MOMENTUM" if val > 0.15 else ("MEAN_REVERTING" if val < -0.15 else "NEUTRAL")
    return {"value": round(val, 3), "signal": signal}
