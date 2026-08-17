# services/indicators/volume.py
import numpy as np
import pandas as pd

from .registry import indicator, last_float
from strategies.strategy_utils import session_vwap

CATEGORY = "volume"


@indicator("obv", CATEGORY, min_bars=15, description="On-Balance Volume")
def obv(df: pd.DataFrame) -> dict:
    close, volume = df["Close"], df["Volume"].fillna(0)
    direction = np.sign(close.diff()).fillna(0)
    obv_series = (direction * volume).cumsum()
    val = last_float(obv_series, 0.0)
    slope = val - last_float(obv_series.iloc[:-1], val) if len(obv_series) > 1 else 0.0
    return {"value": round(val, 2), "signal": "BULLISH" if slope > 0 else ("BEARISH" if slope < 0 else "NEUTRAL")}


@indicator("session_vwap", CATEGORY, min_bars=5, description="Session-anchored Volume Weighted Average Price")
def vwap_indicator(df: pd.DataFrame) -> dict:
    vwap = session_vwap(df)
    price = float(df["Close"].iloc[-1])
    v = last_float(vwap, price)
    return {"value": round(v, 4), "signal": "BULLISH" if price > v else ("BEARISH" if price < v else "NEUTRAL")}


@indicator("cmf_20", CATEGORY, min_bars=20, description="Chaikin Money Flow (20)")
def cmf_20(df: pd.DataFrame) -> dict:
    high, low, close, volume = df["High"], df["Low"], df["Close"], df["Volume"].fillna(0)
    mf_mult = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
    mf_vol = mf_mult * volume
    cmf = mf_vol.rolling(20).sum() / volume.rolling(20).sum().replace(0, np.nan)
    val = last_float(cmf, 0.0)
    signal = "BULLISH" if val > 0.05 else ("BEARISH" if val < -0.05 else "NEUTRAL")
    return {"value": round(val, 4), "signal": signal}


@indicator("mfi_14", CATEGORY, min_bars=15, description="Money Flow Index (14)")
def mfi_14(df: pd.DataFrame) -> dict:
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    volume = df["Volume"].fillna(0)
    raw_mf = tp * volume
    mf_sign = np.where(tp > tp.shift(1), 1, -1)
    signed_mf = raw_mf * mf_sign
    pos_mf = pd.Series(np.where(signed_mf > 0, signed_mf, 0.0), index=df.index).rolling(14).sum()
    neg_mf = pd.Series(np.where(signed_mf < 0, -signed_mf, 0.0), index=df.index).rolling(14).sum()
    mfr = pos_mf / neg_mf.replace(0, np.nan)
    mfi = 100 - (100 / (1 + mfr))
    val = last_float(mfi, 50.0)
    signal = "OVERBOUGHT" if val >= 80 else ("OVERSOLD" if val <= 20 else "NEUTRAL")
    return {"value": round(val, 2), "signal": signal}


@indicator("ad_line", CATEGORY, min_bars=10, description="Accumulation/Distribution Line")
def ad_line(df: pd.DataFrame) -> dict:
    high, low, close, volume = df["High"], df["Low"], df["Close"], df["Volume"].fillna(0)
    mf_mult = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
    ad = (mf_mult.fillna(0) * volume).cumsum()
    val = last_float(ad, 0.0)
    slope = val - last_float(ad.iloc[:-1], val) if len(ad) > 1 else 0.0
    return {"value": round(val, 2), "signal": "BULLISH" if slope > 0 else ("BEARISH" if slope < 0 else "NEUTRAL")}


@indicator("force_index_13", CATEGORY, min_bars=15, description="Elder's Force Index, EMA-13 smoothed")
def force_index(df: pd.DataFrame) -> dict:
    raw_force = df["Close"].diff(1) * df["Volume"].fillna(0)
    efi = raw_force.ewm(span=13, adjust=False).mean()
    val = last_float(efi, 0.0)
    return {"value": round(val, 2), "signal": "BULLISH" if val > 0 else ("BEARISH" if val < 0 else "NEUTRAL")}


@indicator("ease_of_movement_14", CATEGORY, min_bars=15, description="Ease of Movement (14)")
def ease_of_movement(df: pd.DataFrame) -> dict:
    high, low, volume = df["High"], df["Low"], df["Volume"].fillna(0).replace(0, 1)
    distance = ((high + low) / 2.0) - ((high.shift(1) + low.shift(1)) / 2.0)
    box_ratio = (volume / 1e8) / (high - low).replace(0, np.nan)
    emv_raw = distance / box_ratio.replace(0, np.nan)
    emv = emv_raw.rolling(14).mean()
    val = last_float(emv, 0.0)
    return {"value": round(val, 4), "signal": "BULLISH" if val > 0 else ("BEARISH" if val < 0 else "NEUTRAL")}


@indicator("nvi", CATEGORY, min_bars=15, description="Negative Volume Index (cumulative, up days on falling volume held flat)")
def nvi(df: pd.DataFrame) -> dict:
    close, volume = df["Close"], df["Volume"].fillna(0)
    pct_change = close.pct_change().fillna(0)
    vol_falling = volume < volume.shift(1)
    nvi_series = pd.Series(1000.0, index=df.index)
    for i in range(1, len(df)):
        if vol_falling.iloc[i]:
            nvi_series.iloc[i] = nvi_series.iloc[i - 1] * (1 + pct_change.iloc[i])
        else:
            nvi_series.iloc[i] = nvi_series.iloc[i - 1]
    val = last_float(nvi_series, 1000.0)
    ema255 = nvi_series.ewm(span=min(255, len(df)), adjust=False).mean()
    sig_line = last_float(ema255, val)
    return {"value": round(val, 2), "signal": "BULLISH" if val > sig_line else "BEARISH"}


@indicator("pvi", CATEGORY, min_bars=15, description="Positive Volume Index (cumulative, up days on rising volume held flat)")
def pvi(df: pd.DataFrame) -> dict:
    close, volume = df["Close"], df["Volume"].fillna(0)
    pct_change = close.pct_change().fillna(0)
    vol_rising = volume > volume.shift(1)
    pvi_series = pd.Series(1000.0, index=df.index)
    for i in range(1, len(df)):
        if vol_rising.iloc[i]:
            pvi_series.iloc[i] = pvi_series.iloc[i - 1] * (1 + pct_change.iloc[i])
        else:
            pvi_series.iloc[i] = pvi_series.iloc[i - 1]
    val = last_float(pvi_series, 1000.0)
    ema255 = pvi_series.ewm(span=min(255, len(df)), adjust=False).mean()
    sig_line = last_float(ema255, val)
    return {"value": round(val, 2), "signal": "BULLISH" if val > sig_line else "BEARISH"}


@indicator("klinger_oscillator", CATEGORY, min_bars=60, description="Klinger Volume Oscillator (34,55 EMA of signed volume)")
def klinger_oscillator(df: pd.DataFrame) -> dict:
    close, volume = df["Close"], df["Volume"].fillna(0)
    sv = np.where(close > close.shift(1), volume, -volume)
    kvo = pd.Series(sv, index=df.index).ewm(span=34, adjust=False).mean() - \
        pd.Series(sv, index=df.index).ewm(span=55, adjust=False).mean()
    signal_line = kvo.ewm(span=13, adjust=False).mean()
    k, s = last_float(kvo, 0.0), last_float(signal_line, 0.0)
    return {"value": {"kvo": round(k, 2), "signal_line": round(s, 2)},
            "signal": "BULLISH" if k > s else ("BEARISH" if k < s else "NEUTRAL")}


@indicator("volume_oscillator_5_20", CATEGORY, min_bars=20, description="Volume Oscillator: %diff of fast/slow volume MA")
def volume_oscillator(df: pd.DataFrame) -> dict:
    volume = df["Volume"].fillna(0)
    fast = volume.rolling(5).mean()
    slow = volume.rolling(20).mean()
    vo = ((fast - slow) / slow.replace(0, np.nan)) * 100
    val = last_float(vo, 0.0)
    return {"value": round(val, 2), "signal": "EXPANDING" if val > 0 else "CONTRACTING"}


@indicator("volume_roc_12", CATEGORY, min_bars=13, description="Volume Rate of Change (12)")
def volume_roc(df: pd.DataFrame) -> dict:
    volume = df["Volume"].fillna(0)
    vroc = ((volume - volume.shift(12)) / volume.shift(12).replace(0, np.nan)) * 100
    val = last_float(vroc, 0.0)
    return {"value": round(val, 2), "signal": "NEUTRAL"}