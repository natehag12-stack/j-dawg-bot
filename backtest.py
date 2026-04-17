"""
60-day backtest runner.

Walks each configured symbol over the last 60 days of 5m data, replays signal
generation on every closed bar, and simulates entry/stop/target fills using
subsequent bars. Prints per-symbol and aggregate win rate + net R.

Usage:
    python backtest.py                  # all symbols in config.SYMBOLS
    python backtest.py NQ=F             # one symbol
    python backtest.py NQ=F QQQ --days 30
"""
from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

import config
from indicators import atr as atr_fn
from signals import generate_signals


@dataclass
class Trade:
    symbol: str
    side: str
    bar_ts: pd.Timestamp
    entry: float
    stop: float
    target: float
    outcome: str  # 'win' | 'loss' | 'open'
    pnl_r: float


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df


def _download(symbol: str, interval: str, period: str) -> pd.DataFrame:
    df = yf.download(
        tickers=symbol,
        period=period,
        interval=interval,
        progress=False,
        auto_adjust=False,
    )
    df = _flatten(df)
    if df.empty:
        return df
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


def simulate(symbol: str, days: int) -> list[Trade]:
    # yfinance caps 5m history; 60d is the max practical window.
    period_5m = f"{min(days, 60)}d"
    period_1h = f"{max(days, 60)}d"
    df_5m = _download(symbol, "5m", period_5m)
    df_1h = _download(symbol, "1h", period_1h)
    if len(df_5m) < 60 or len(df_1h) < 60:
        print(f"[{symbol}] not enough data (5m={len(df_5m)}, 1h={len(df_1h)})")
        return []

    result = generate_signals(df_5m, df_1h)
    long_cond = result["long_cond"]
    short_cond = result["short_cond"]
    atr_series = atr_fn(df_5m)

    trades: list[Trade] = []
    n = len(df_5m)
    # leave room for forward simulation — skip the last few bars
    for i in range(30, n - 2):
        side = None
        if long_cond.iloc[i]:
            side = "long"
        elif short_cond.iloc[i]:
            side = "short"
        if side is None:
            continue

        atr_val = float(atr_series.iloc[i])
        if pd.isna(atr_val) or atr_val <= 0:
            continue

        bar = df_5m.iloc[i]
        entry = float(bar["Close"])
        if side == "long":
            stop = float(bar["Low"]) - atr_val * config.ATR_MULT
            risk = entry - stop
            target = entry + risk * config.RISK_RR
        else:
            stop = float(bar["High"]) + atr_val * config.ATR_MULT
            risk = stop - entry
            target = entry - risk * config.RISK_RR
        if risk <= 0:
            continue

        outcome = "open"
        pnl_r = 0.0
        for j in range(i + 1, n):
            fwd = df_5m.iloc[j]
            if side == "long":
                hit_stop = fwd["Low"] <= stop
                hit_target = fwd["High"] >= target
            else:
                hit_stop = fwd["High"] >= stop
                hit_target = fwd["Low"] <= target
            if hit_stop and hit_target:
                outcome, pnl_r = "loss", -1.0
                break
            if hit_stop:
                outcome, pnl_r = "loss", -1.0
                break
            if hit_target:
                outcome, pnl_r = "win", float(config.RISK_RR)
                break

        trades.append(
            Trade(
                symbol=symbol,
                side=side,
                bar_ts=df_5m.index[i],
                entry=entry,
                stop=stop,
                target=target,
                outcome=outcome,
                pnl_r=pnl_r,
            )
        )

    return trades


def report(symbol: str, trades: list[Trade]) -> dict:
    closed = [t for t in trades if t.outcome != "open"]
    wins = sum(1 for t in closed if t.outcome == "win")
    losses = sum(1 for t in closed if t.outcome == "loss")
    n = wins + losses
    wr = (wins / n * 100) if n else 0.0
    net_r = sum(t.pnl_r for t in closed)

    longs = [t for t in closed if t.side == "long"]
    shorts = [t for t in closed if t.side == "short"]
    long_wr = (sum(1 for t in longs if t.outcome == "win") / len(longs) * 100) if longs else 0.0
    short_wr = (sum(1 for t in shorts if t.outcome == "win") / len(shorts) * 100) if shorts else 0.0

    print(f"\n=== {symbol} ===")
    print(f"Signals fired : {len(trades)}  (open: {len(trades) - n})")
    print(f"Closed trades : {n}")
    print(f"Wins / Losses : {wins} / {losses}")
    print(f"Win rate      : {wr:.1f}%")
    print(f"Longs  : {len(longs):>3}  WR {long_wr:5.1f}%")
    print(f"Shorts : {len(shorts):>3}  WR {short_wr:5.1f}%")
    print(f"Net R         : {net_r:+.2f}")
    return {"symbol": symbol, "n": n, "wins": wins, "losses": losses, "net_r": net_r}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="J-Dawg 60-day backtest")
    ap.add_argument("symbols", nargs="*", help="Symbols to test (default: config.SYMBOLS)")
    ap.add_argument("--days", type=int, default=60, help="Lookback window in days (default 60)")
    args = ap.parse_args(argv)

    symbols = args.symbols or config.SYMBOLS
    print(f"Backtesting {symbols} over last {args.days} days…")

    summaries = []
    for s in symbols:
        trades = simulate(s, args.days)
        summaries.append(report(s, trades))

    total_w = sum(r["wins"] for r in summaries)
    total_l = sum(r["losses"] for r in summaries)
    total_n = total_w + total_l
    total_r = sum(r["net_r"] for r in summaries)
    agg_wr = (total_w / total_n * 100) if total_n else 0.0

    print("\n=== AGGREGATE ===")
    print(f"Trades  : {total_n}")
    print(f"Win %   : {agg_wr:.1f}%")
    print(f"Net R   : {total_r:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
