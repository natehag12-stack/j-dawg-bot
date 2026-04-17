"""
J-Dawg Bot — live loop (paper trading mode).

Every POLL_INTERVAL_SECONDS:
    1. Poll Telegram for inbound commands (/status, /stats, /pnl, /help)
    2. For each configured symbol:
        a. Fetch latest 5m + 1H data
        b. Reconcile any pending paper positions (push CLOSE notify with tick P&L)
        c. Recompute the adaptive threshold for each side
        d. Evaluate the last CLOSED 5m bar for a long/short entry
        e. If condition fires AND Bayesian confidence ≥ adaptive threshold →
           push OPEN notify + log to DB
    3. Once per ET day at DAILY_SUMMARY_TIME → push P&L recap

The bot does not place real orders — paper trading is the only mode. It is
designed to run forever; on any exception the loop logs, alerts, and continues.
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
from tuner import ThresholdTuner


def compute_targets(df: pd.DataFrame, side: str, atr_val: float) -> tuple[float, float, float]:
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
    tuner = ThresholdTuner.load()

    notifier = None
    if config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID:
        notifier = TelegramNotifier(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
        notifier.send_startup(config.SYMBOLS, bayes.summary(config.SYMBOLS))
    else:
        print("⚠️  TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set — alerts will print to console only.")

    print(f"[{datetime.now().isoformat(timespec='seconds')}] J-Dawg Bot online.")
    print(f"Symbols: {config.SYMBOLS} | Poll every {config.POLL_INTERVAL_SECONDS}s | "
          f"Paper={config.PAPER_TRADING} | Adaptive={config.ADAPTIVE_THRESHOLD}")
    print(bayes.summary(config.SYMBOLS))

    last_summary_date: date_cls | None = None
    consecutive_errors = 0
    last_market_tick = 0.0  # forces an immediate market pass on first iter

    while True:
        try:
            # 1. Inbound commands every COMMAND_POLL_INTERVAL_SECONDS (snappy)
            if notifier:
                handle_commands(notifier, tracker, bayes, tuner)

            # 2. Market data + signal eval at the slower POLL_INTERVAL_SECONDS
            now = time.time()
            if now - last_market_tick >= config.POLL_INTERVAL_SECONDS:
                last_market_tick = now
                for symbol in config.SYMBOLS:
                    try:
                        loop_once(symbol, tracker, bayes, tuner, notifier)
                    except Exception as e:
                        print(f"[error {symbol}] {e}\n{traceback.format_exc()}")
                        if notifier:
                            notifier.send(f"⚠️ Bot error on `{symbol}`: `{e}`")

                if notifier:
                    last_summary_date = maybe_send_daily_summary(notifier, tracker, last_summary_date)

            consecutive_errors = 0  # healthy tick

        except KeyboardInterrupt:
            print("\n[shutdown] bye.")
            return
        except Exception as e:
            consecutive_errors += 1
            print(f"[error] {e}\n{traceback.format_exc()}")
            if notifier:
                notifier.send(f"⚠️ Bot error: `{e}` (#{consecutive_errors})")
            if consecutive_errors >= 3:
                backoff = min(300, config.POLL_INTERVAL_SECONDS * (2 ** (consecutive_errors - 2)))
                print(f"[self-heal] backing off {backoff}s")
                time.sleep(backoff)
                continue

        time.sleep(config.COMMAND_POLL_INTERVAL_SECONDS)


def _close_callback(notifier, symbol):
    """Build a per-symbol close-notification callback."""
    def _on_close(row, outcome, pnl_r, exit_price, exit_reason):
        if not (notifier and config.PAPER_TRADING):
            return
        ts = datetime.fromisoformat(row["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        held = datetime.now(timezone.utc) - ts
        # human-readable held duration
        h, rem = divmod(int(held.total_seconds()), 3600)
        m, _ = divmod(rem, 60)
        held_str = f"{h}h {m}m" if h else f"{m}m"
        notifier.send_close(
            symbol=symbol,
            side=row["side"],
            entry=row["entry"],
            exit_price=exit_price,
            pnl_r=pnl_r,
            exit_reason=exit_reason,
            tick_size=config.tick_size(symbol),
            held=held_str,
        )
    return _on_close


def loop_once(
    symbol: str,
    tracker: Tracker,
    bayes: BayesianModel,
    tuner: ThresholdTuner,
    notifier: TelegramNotifier | None,
) -> None:
    df_5m, df_1h = fetch_all(symbol)
    if len(df_5m) < 60 or len(df_1h) < 60:
        print(f"[warn {symbol}] not enough data yet")
        return

    closed = reconcile_pending(
        tracker,
        df_5m,
        bayes,
        on_close=_close_callback(notifier, symbol),
        symbol=symbol,
    )
    if closed > 0:
        print(f"[reconcile {symbol}] closed {closed}")

    # Recompute adaptive thresholds after any closures
    if config.ADAPTIVE_THRESHOLD and closed > 0:
        for side in ("long", "short"):
            new_t = tuner.recompute(tracker, symbol, side)
            print(f"[tune {symbol} {side}] threshold → {new_t:.2f}")

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

    confidence = bayes.confidence(symbol, side)
    n_samples = bayes.samples(symbol, side)
    reason = explain(comp, idx)
    threshold = tuner.threshold(symbol, side) if config.ADAPTIVE_THRESHOLD else config.MIN_POSTERIOR_TO_ALERT

    log_line = (
        f"[{datetime.now().isoformat(timespec='seconds')}] "
        f"SIGNAL {symbol} {side.upper()} @ {bar_ts.isoformat()} "
        f"entry={entry:.2f} stop={stop:.2f} target={target:.2f} "
        f"conf={confidence:.2f} thr={threshold:.2f} n={n_samples} → {reason}"
    )
    print(log_line)

    if confidence < threshold:
        print(f"[skip {symbol}] confidence {confidence:.2f} < threshold {threshold:.2f}")
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
            tick_size=config.tick_size(symbol),
            paper=config.PAPER_TRADING,
        )


# ---------- inbound commands ----------
_STATS_CMDS = {"/status", "/stats", "/pnl"}
_HELP_CMDS = {"/start", "/help"}


def handle_commands(
    notifier: TelegramNotifier,
    tracker: Tracker,
    bayes: BayesianModel,
    tuner: ThresholdTuner,
) -> None:
    for text in notifier.poll_commands():
        # strip @botname suffix Telegram appends in groups
        cmd = text.split()[0].lower().split("@")[0]
        if cmd in _STATS_CMDS:
            overall = tracker.recent_stats()
            per_symbol = {s: tracker.recent_stats(s) for s in config.SYMBOLS}
            thresholds = tuner.snapshot() if config.ADAPTIVE_THRESHOLD else None
            notifier.send_stats(
                overall,
                per_symbol,
                bayes_lines=bayes.summary_lines(config.SYMBOLS),
                thresholds=thresholds,
            )
        elif cmd in _HELP_CMDS:
            notifier.send(
                "*J-Dawg commands*\n"
                "/status — current win rate, P&L, and adaptive thresholds\n"
                "/stats  — alias for /status\n"
                "/pnl    — alias for /status\n"
                "/help   — this message"
            )


# ---------- daily summary ----------
def maybe_send_daily_summary(
    notifier: TelegramNotifier,
    tracker: Tracker,
    last_summary_date: date_cls | None,
) -> date_cls | None:
    tz = pytz.timezone(config.SESSION_TZ)
    now_et = datetime.now(tz)
    hh, mm = map(int, config.DAILY_SUMMARY_TIME.split(":"))
    trigger_et = now_et.replace(hour=hh, minute=mm, second=0, microsecond=0)

    if now_et < trigger_et:
        return last_summary_date
    if last_summary_date == now_et.date():
        return last_summary_date

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

    notifier.send_daily_summary(f"{now_et.strftime('%a %b')} {now_et.day}", rows, per_symbol)
    return now_et.date()


if __name__ == "__main__":
    run()
