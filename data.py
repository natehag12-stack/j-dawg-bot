"""
Market data fetcher.

Uses yfinance (free, no API key).
Default symbol: NQ=F  (NASDAQ 100 E-mini futures continuous contract on Yahoo).

If yfinance returns MultiIndex columns (common with single-symbol), we flatten.
"""
from __future__ import annotations
import pandas as pd
import yfinance as yf

import config


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance sometimes returns a MultiIndex — flatten to OHLCV columns."""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df


def fetch_5m(symbol: str = config.SYMBOL, period: str = "5d") -> pd.DataFrame:
    df = yf.download(
        tickers=symbol,
        period=period,
        interval="5m",
        progress=False,
        auto_adjust=False,
    )
    df = _flatten(df)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


def fetch_1h(symbol: str = config.SYMBOL, period: str = "60d") -> pd.DataFrame:
    df = yf.download(
        tickers=symbol,
        period=period,
        interval="1h",
        progress=False,
        auto_adjust=False,
    )
    df = _flatten(df)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


def fetch_all(symbol: str = config.SYMBOL) -> tuple[pd.DataFrame, pd.DataFrame]:
    return fetch_5m(symbol), fetch_1h(symbol)
