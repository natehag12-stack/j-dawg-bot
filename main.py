"""
J-Dawg Bot — live loop.

Every POLL_INTERVAL_SECONDS:
    1. Fetch latest 5m + 1H data
    2. Reconcile outcomes of any pending signals → feed Bayesian model
    3. Evaluate the last CLOSED 5m bar for a long/short entry condition
    4. If condition fires AND Bayesian confidence passes threshold → alert Telegram + log to DB
"""
from __future__ import annotations
import time
import traceback
from datetime import datetime, timezone

import pandas as pd

import config
from data import fetch_all
from signals import generate_signals, explain
from indicators import atr as atr_fn
from bayesian import BayesianModel
from tracker import Tracker, reconcile_pending
from telegram_bot import TelegramNotifier


def compute_targets(df: pd.DataFrame, side: str, atr_val: float) -> tuple[float, float, float]:
    """Entry = close of trigger bar. Stop = bar extreme ± 0.5 ATR. Target = RR multiple."""
    last = df.iloc[-1]
    entry = float(last["Close"])
    if side == "long":
        stop = float(last["Low"]) - atr_val * config.ATR_MULT
        risk = entry - stop
        target = entry + risk * config.RISK_RR
    else:
        stop = float(last["High"]) + atr_val * config.ATR_MULT
        risk = stop - entry
        target = entry - risk * config.RISK_RR
    return entry, stop, target


def run() -> None:
    # --- init ---
    tracker = Tracker()
    bayes = BayesianModel.load()

    notifier = None
    if config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID:
        notifier = TelegramNotifier(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
        notifier.send_startup(config.SYMBOL, bayes.summary())
    else:
        print("⚠️  TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set — alerts will print to console only.")

    print(f"[{datetime.now().isoformat(timespec='seconds')}] J-Dawg Bot online.")
    print(f"Symbol: {config.SYMBOL} | Poll every {config.POLL_INTERVAL_SECONDS}s")
    print(f"Bayes: {bayes.summary()}")

    while True:
        try:
            loop_once(tracker, bayes, notifier)
        except KeyboardInterrupt:
            print("\n[shutdown] bye.")
            return
        except Exception as e:
            print(f"[error] {e}\n{traceback.format_exc()}")
            if notifier:
                notifier.send(f"⚠️ Bot error: `{e}`")
        time.sleep(config.POLL_INTERVAL_SECONDS)


def loop_once(tracker: Tracker, bayes: BayesianModel, notifier: TelegramNotifier | None) -> None:
    # 1. data
    df_5m, df_1h = fetch_all(config.SYMBOL)
    if len(df_5m) < 60 or len(df_1h) < 60:
        print("[warn] not enough data yet")
        return

    # 2. reconcile past signals
    closed = reconcile_pending(tracker, df_5m, bayes)
    if closed > 0:
        print(f"[reconcile] closed {closed} signals → {bayes.summary()}")

    # 3. evaluate LAST CLOSED bar only (index -2)
    result = generate_signals(df_5m, df_1h)
    long_cond = result["long_cond"]
    short_cond = result["short_cond"]
    comp = result["components"]

    idx = -2  # last fully-closed 5m bar
    bar_ts = df_5m.index[idx]

    side = None
    if long_cond.iloc[idx]:
        side = "long"
    elif short_cond.iloc[idx]:
        side = "short"

    if side is None:
        return

    # De-dup: don't re-alert same bar
    if tracker.was_logged_for_bar(bar_ts, side):
        return

    # ATR for stop calc
    atr_series = atr_fn(df_5m)
    atr_val = float(atr_series.iloc[idx])
    if pd.isna(atr_val) or atr_val <= 0:
        print("[warn] ATR not available, skipping")
        return

    # up to and including trigger bar
    df_slice = df_5m.iloc[: idx + 1]
    entry, stop, target = compute_targets(df_slice, side, atr_val)

    # 4. Bayesian gate
    confidence = bayes.confidence(side)
    n_samples = bayes.samples(side)
    reason = explain(comp, idx)

    log_line = (
        f"[{datetime.now().isoformat(timespec='seconds')}] "
        f"SIGNAL {side.upper()} @ {bar_ts.isoformat()} "
        f"entry={entry:.2f} stop={stop:.2f} target={target:.2f} "
        f"conf={confidence:.2f} n={n_samples} → {reason}"
    )
    print(log_line)

    if confidence < config.MIN_POSTERIOR_TO_ALERT:
        print(f"[skip] confidence {confidence:.2f} < {config.MIN_POSTERIOR_TO_ALERT}")
        return

    # 5. log + alert
    tracker.log_signal(
        symbol=config.SYMBOL,
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        confidence=confidence,
        reason=reason,
        bar_ts=bar_ts,
    )

    if notifier:
        notifier.send_signal(
            symbol=config.SYMBOL,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            confidence=confidence,
            reason=reason,
            samples=n_samples,
        )


if __name__ == "__main__":
    run()
