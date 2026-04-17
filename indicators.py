"""
Smart Money Concepts indicators — mirrors the Pine script 1:1.

Each function takes an OHLC DataFrame (columns: Open, High, Low, Close)
and returns a boolean Series or numeric Series aligned to the input index.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytz
from datetime import time as dtime

import config


# ---------- Higher-timeframe bias ----------
def htf_bias(df_1h: pd.DataFrame, ema_period: int = config.BIAS_EMA_PERIOD) -> pd.Series:
    """Returns +1 for bull bias, -1 for bear, 0 for neutral, aligned to 1H index."""
    ema = df_1h["Close"].ewm(span=ema_period, adjust=False).mean()
    bias = pd.Series(0, index=df_1h.index, dtype=int)
    bias[df_1h["Close"] > ema] = 1
    bias[df_1h["Close"] < ema] = -1
    return bias


def align_bias_to_5m(bias_1h: pd.Series, df_5m: pd.DataFrame) -> pd.Series:
    """Forward-fill the 1H bias to every 5m bar."""
    return bias_1h.reindex(df_5m.index, method="ffill").fillna(0).astype(int)


# ---------- Fair Value Gaps ----------
def detect_fvg(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """
    Bullish FVG: low of current candle > high of two candles ago.
    Bearish FVG: high of current candle < low of two candles ago.
    Returns two boolean Series aligned to df.index.
    """
    fvg_bull = df["Low"] > df["High"].shift(2)
    fvg_bear = df["High"] < df["Low"].shift(2)
    return fvg_bull.fillna(False), fvg_bear.fillna(False)


# ---------- Previous Day High / Low ----------
def prev_day_levels(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Returns PDH and PDL aligned to each 5m bar (previous calendar day in UTC)."""
    if df.index.tz is None:
        idx = df.index.tz_localize("UTC")
    else:
        idx = df.index
    idx_et = idx.tz_convert(config.SESSION_TZ)
    date_key = pd.Series(idx_et.date, index=df.index)

    daily_high = df["High"].groupby(date_key).transform("max")
    daily_low = df["Low"].groupby(date_key).transform("min")

    # Shift by one day by using the date_key: build a lookup
    unique_days = sorted(date_key.unique())
    day_high_map = {d: df["High"][date_key == d].max() for d in unique_days}
    day_low_map = {d: df["Low"][date_key == d].min() for d in unique_days}

    prev_map_high = {}
    prev_map_low = {}
    for i, d in enumerate(unique_days):
        if i == 0:
            prev_map_high[d] = np.nan
            prev_map_low[d] = np.nan
        else:
            prev_map_high[d] = day_high_map[unique_days[i - 1]]
            prev_map_low[d] = day_low_map[unique_days[i - 1]]

    pdh = date_key.map(prev_map_high).astype(float)
    pdl = date_key.map(prev_map_low).astype(float)
    return pdh, pdl


# ---------- Swing pivots (Liquidity) ----------
def swing_points(
    df: pd.DataFrame,
    left: int = config.SWING_LEFT,
    right: int = config.SWING_RIGHT,
) -> tuple[pd.Series, pd.Series]:
    """
    Pivot high: bar whose high is the max in window [i-left, i+right].
    Forward-filled so every bar knows the most recent swing level.
    """
    highs = df["High"].values
    lows = df["Low"].values
    n = len(df)
    swing_high = np.full(n, np.nan)
    swing_low = np.full(n, np.nan)

    for i in range(left, n - right):
        window_high = highs[i - left : i + right + 1]
        window_low = lows[i - left : i + right + 1]
        if highs[i] == window_high.max():
            swing_high[i] = highs[i]
        if lows[i] == window_low.min():
            swing_low[i] = lows[i]

    sh = pd.Series(swing_high, index=df.index).ffill()
    sl = pd.Series(swing_low, index=df.index).ffill()
    return sh, sl


# ---------- Displacement candle ----------
def displacement(
    df: pd.DataFrame, threshold: float = config.DISPLACEMENT_THRESHOLD
) -> tuple[pd.Series, pd.Series]:
    """Body-to-range ratio > threshold, directional."""
    body = (df["Close"] - df["Open"]).abs()
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    ratio = body / rng
    disp_bull = (df["Close"] > df["Open"]) & (ratio > threshold)
    disp_bear = (df["Close"] < df["Open"]) & (ratio > threshold)
    return disp_bull.fillna(False), disp_bear.fillna(False)


# ---------- Liquidity sweep ----------
def liquidity_sweeps(
    df: pd.DataFrame, pdh: pd.Series, pdl: pd.Series, swing_high: pd.Series, swing_low: pd.Series
) -> tuple[pd.Series, pd.Series]:
    """
    Long sweep: price wicks BELOW PDL or a prior swing low, then closes back above.
    Short sweep: price wicks ABOVE PDH or a prior swing high, then closes back below.

    Matches the Pine logic:
        longSweep  = crossunder(low, pdl)  or crossunder(low, swingLow)
        shortSweep = crossover(high, pdh)  or crossover(high, swingHigh)
    """
    prev_low = df["Low"].shift(1)
    prev_high = df["High"].shift(1)

    long_sweep_pdl = (prev_low >= pdl) & (df["Low"] < pdl)
    long_sweep_sw = (prev_low >= swing_low) & (df["Low"] < swing_low)
    long_sweep = (long_sweep_pdl | long_sweep_sw).fillna(False)

    short_sweep_pdh = (prev_high <= pdh) & (df["High"] > pdh)
    short_sweep_sw = (prev_high <= swing_high) & (df["High"] > swing_high)
    short_sweep = (short_sweep_pdh | short_sweep_sw).fillna(False)

    return long_sweep, short_sweep


# ---------- NY session filter ----------
def in_ny_session(df: pd.DataFrame) -> pd.Series:
    """True when bar timestamp falls in NY session window."""
    if df.index.tz is None:
        idx = df.index.tz_localize("UTC")
    else:
        idx = df.index
    et = idx.tz_convert(config.SESSION_TZ)
    start_h, start_m = map(int, config.SESSION_START.split(":"))
    end_h, end_m = map(int, config.SESSION_END.split(":"))
    t_start = dtime(start_h, start_m)
    t_end = dtime(end_h, end_m)
    mask = pd.Series(
        [(t.time() >= t_start) and (t.time() <= t_end) for t in et],
        index=df.index,
    )
    return mask


# ---------- ATR ----------
def atr(df: pd.DataFrame, period: int = config.ATR_PERIOD) -> pd.Series:
    """Wilder-ish ATR via simple rolling mean of true range."""
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()
