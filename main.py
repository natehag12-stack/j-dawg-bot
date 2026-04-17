"""
J-Dawg Bot — live loop.

Every POLL_INTERVAL_SECONDS:
    1. For each configured symbol:
        a. Fetch latest 5m + 1H data
        b. Reconcile outcomes of any pending signals → feed Bayesian model
        c. Evaluate the last CLOSED 5m bar for a long/short entry condition
        d. If condition fires AND Bayesian confidence passes threshold → alert Telegram + log to DB
    2. Handle inbound Telegram commands (/stats)
    3. Once per day at DAILY_SUMMARY_TIME ET → push P&L recap to Telegram
"""
from __future__ import annotations
import time
import traceback
from datetime import datetime, timezone, timedelta, date as date_cls

import pandas as pd
import pytz

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
    tracker = Tracker()
    bayes = BayesianModel.load()

    notifier = None
    if config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID:
        notifier = TelegramNotifier(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
        notifier.send_startup(config.SYMBOLS, bayes.summary())
    else:
        print("⚠️  TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set — alerts will print to console only.")

    print(f"[{datetime.now().isoformat(timespec='seconds')}] J-Dawg Bot online.")
    print(f"Symbols: {config.SYMBOLS} | Poll every {config.POLL_INTERVAL_SECONDS}s")
    print(f"Bayes: {bayes.summary()}")

    last_summary_date: date_cls | None = None

    while True:
        try:
            for symbol in config.SYMBOLS:
                try:
                    loop_once(symbol, tracker, bayes, notifier)
                except Exception as e:
                    print(f"[error {symbol}] {e}\n{traceback.format_exc()}")
                    if notifier:
                        notifier.send(f"⚠️ Bot error on `{symbol}`: `{e}`")

            if notifier:
                handle_commands(notifier, tracker, bayes)
                last_summary_date = maybe_send_daily_summary(notifier, tracker, last_summary_date)

        except KeyboardInterrupt:
            print("\n[shutdown] bye.")
            return
        except Exception as e:
            print(f"[error] {e}\n{traceback.format_exc()}")
            if notifier:
                notifier.send(f"⚠️ Bot error: `{e}`")
        time.sleep(config.POLL_INTERVAL_SECONDS)


def loop_once(
    symbol: str,
    tracker: Tracker,
    bayes: BayesianModel,
    notifier: TelegramNotifier | None,
) -> None:
    df_5m, df_1h = fetch_all(symbol)
    if len(df_5m) < 60 or len(df_1h) < 60:
        print(f"[warn {symbol}] not enough data yet")
        return

    closed = reconcile_pending(tracker, df_5m, bayes)
    if closed > 0:
        print(f"[reconcile {symbol}] closed {closed} signals → {bayes.summary()}")

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

    if tracker.was_logged_for_bar(bar_ts, side):
        return

    atr_series = atr_fn(df_5m)
    atr_val = float(atr_series.iloc[idx])
    if pd.isna(atr_val) or atr_val <= 0:
        print(f"[warn {symbol}] ATR not available, skipping")
        return

    df_slice = df_5m.iloc[: idx + 1]
    entry, stop, target = compute_targets(df_slice, side, atr_val)

    confidence = bayes.confidence(side)
    n_samples = bayes.samples(side)
    reason = explain(comp, idx)

    log_line = (
        f"[{datetime.now().isoformat(timespec='seconds')}] "
        f"SIGNAL {symbol} {side.upper()} @ {bar_ts.isoformat()} "
        f"entry={entry:.2f} stop={stop:.2f} target={target:.2f} "
        f"conf={confidence:.2f} n={n_samples} → {reason}"
    )
    print(log_line)

    if confidence < config.MIN_POSTERIOR_TO_ALERT:
        print(f"[skip {symbol}] confidence {confidence:.2f} < {config.MIN_POSTERIOR_TO_ALERT}")
        return

    tracker.log_signal(
        symbol=symbol,
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
            symbol=symbol,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            confidence=confidence,
            reason=reason,
            samples=n_samples,
        )


# ---------- inbound commands ----------
def handle_commands(
    notifier: TelegramNotifier,
    tracker: Tracker,
    bayes: BayesianModel,
) -> None:
    for text in notifier.poll_commands():
        cmd = text.split()[0].lower()
        if cmd in ("/stats", "/stats@" + (notifier.chat_id or "")):
            overall = tracker.recent_stats()
            per_symbol = {s: tracker.recent_stats(s) for s in config.SYMBOLS}
            notifier.send_stats(overall, per_symbol, bayes.summary())
        elif cmd == "/start" or cmd == "/help":
            notifier.send(
                "*J-Dawg commands*\n"
                "/stats — current win rate and P&L\n"
            )


# ---------- daily summary ----------
def maybe_send_daily_summary(
    notifier: TelegramNotifier,
    tracker: Tracker,
    last_summary_date: date_cls | None,
) -> date_cls | None:
    """Send one summary per trading day once ET time passes DAILY_SUMMARY_TIME."""
    tz = pytz.timezone(config.SESSION_TZ)
    now_et = datetime.now(tz)
    hh, mm = map(int, config.DAILY_SUMMARY_TIME.split(":"))
    trigger_et = now_et.replace(hour=hh, minute=mm, second=0, microsecond=0)

    if now_et < trigger_et:
        return last_summary_date
    if last_summary_date == now_et.date():
        return last_summary_date

    # Window: from this-day trigger back 24h (in UTC, which is how closed_at is stored).
    end_utc = trigger_et.astimezone(timezone.utc)
    start_utc = end_utc - timedelta(hours=24)
    rows = tracker.closed_between(start_utc.isoformat(), end_utc.isoformat())

    per_symbol: dict[str, dict] = {}
    for r in rows:
        s = per_symbol.setdefault(r["symbol"], {"wins": 0, "losses": 0, "pnl_r": 0.0})
        if r["outcome"] == "win":
            s["wins"] += 1
        elif r["outcome"] == "loss":
            s["losses"] += 1
        s["pnl_r"] += r["pnl_r"] or 0.0

    notifier.send_daily_summary(now_et.strftime("%Y-%m-%d"), rows, per_symbol)
    return now_et.date()


if __name__ == "__main__":
    run()
