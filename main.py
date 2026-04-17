"""
J-Dawg Bot — paper-trading live loop.

Tick (every COMMAND_POLL_INTERVAL_SECONDS):
    1. Poll Telegram for inbound commands (/status, /pnl, /help)
Market tick (every POLL_INTERVAL_SECONDS):
    2. For each symbol: fetch data, reconcile open positions, push CLOSE notify
       with $ + tick P&L, recompute adaptive thresholds, evaluate entry conditions
    3. Once per ET day at DAILY_SUMMARY_TIME → push P&L recap

The bot does not place real orders — paper trading is the only mode.
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
from paper import PaperState, position_size


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
    paper = PaperState.load()

    notifier = None
    if config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID:
        notifier = TelegramNotifier(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
        notifier.send_startup(config.SYMBOLS, paper.balance, config.ADAPTIVE_THRESHOLD)
    else:
        print("⚠️  TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set — alerts will print to console only.")

    print(f"[{datetime.now().isoformat(timespec='seconds')}] J-Dawg Bot online.")
    print(f"Symbols: {config.SYMBOLS} | Poll every {config.POLL_INTERVAL_SECONDS}s | "
          f"Paper={config.PAPER_TRADING} balance=${paper.balance:.2f} | Adaptive={config.ADAPTIVE_THRESHOLD}")

    last_summary_date: date_cls | None = None
    consecutive_errors = 0
    last_market_tick = 0.0

    while True:
        try:
            if notifier:
                handle_commands(notifier, tracker, bayes, tuner, paper)

            now = time.time()
            if now - last_market_tick >= config.POLL_INTERVAL_SECONDS:
                last_market_tick = now
                for symbol in config.SYMBOLS:
                    try:
                        loop_once(symbol, tracker, bayes, tuner, paper, notifier)
                    except Exception as e:
                        print(f"[error {symbol}] {e}\n{traceback.format_exc()}")
                        if notifier:
                            notifier.send(f"⚠️ Bot error on `{symbol}`: `{e}`")

                if notifier:
                    last_summary_date = maybe_send_daily_summary(
                        notifier, tracker, paper, last_summary_date,
                    )

            consecutive_errors = 0

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


def _pnl_dollars_for(row, exit_price: float) -> float:
    """Realised $ P&L on a paper trade, given units and entry stored on the row."""
    units = row["units"] or 0.0
    if not units:
        return 0.0
    ts = config.tick_size(row["symbol"])
    tv = config.tick_value(row["symbol"])
    if ts <= 0 or tv <= 0:
        return 0.0
    if row["side"] == "long":
        ticks = (exit_price - row["entry"]) / ts
    else:
        ticks = (row["entry"] - exit_price) / ts
    return float(units) * ticks * tv


def _close_callback(notifier, paper, symbol):
    def _on_close(row, outcome, pnl_r, pnl_dollars, exit_price, exit_reason):
        if pnl_dollars is not None:
            paper.apply_pnl(pnl_dollars)
        if not notifier or not config.PAPER_TRADING:
            return
        ts = datetime.fromisoformat(row["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        held = datetime.now(timezone.utc) - ts
        h, rem = divmod(int(held.total_seconds()), 3600)
        m, _ = divmod(rem, 60)
        held_str = f"{h}h {m}m" if h else f"{m}m"

        tick_sz = config.tick_size(symbol)
        if row["side"] == "long":
            ticks = (exit_price - row["entry"]) / tick_sz if tick_sz else 0.0
        else:
            ticks = (row["entry"] - exit_price) / tick_sz if tick_sz else 0.0

        notifier.send_close(
            symbol=symbol,
            side=row["side"],
            entry=row["entry"],
            exit_price=exit_price,
            pnl_r=pnl_r,
            pnl_dollars=pnl_dollars or 0.0,
            ticks=ticks,
            exit_reason=exit_reason,
            held=held_str,
            balance=paper.balance,
        )
    return _on_close


def loop_once(
    symbol: str,
    tracker: Tracker,
    bayes: BayesianModel,
    tuner: ThresholdTuner,
    paper: PaperState,
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
        on_close=_close_callback(notifier, paper, symbol),
        symbol=symbol,
        pnl_dollars_fn=_pnl_dollars_for,
    )
    if closed > 0:
        print(f"[reconcile {symbol}] closed {closed}  balance=${paper.balance:.2f}")

    if config.ADAPTIVE_THRESHOLD and closed > 0:
        for s in ("long", "short"):
            new_t = tuner.recompute(tracker, symbol, s)
            print(f"[tune {symbol} {s}] threshold → {new_t:.2f}")

    result = generate_signals(df_5m, df_1h)
    long_cond = result["long_cond"]
    short_cond = result["short_cond"]
    comp = result["components"]

    idx = -2
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
        f"conf={confidence:.2f} thr={threshold:.2f} n={n_samples}"
    )
    print(log_line)

    if confidence < threshold:
        print(f"[skip {symbol}] confidence {confidence:.2f} < threshold {threshold:.2f}")
        return

    units, risk_ticks = position_size(symbol, entry, stop, config.PAPER_RISK_PER_TRADE)
    risk_dollars = config.PAPER_RISK_PER_TRADE
    potential_dollars = risk_dollars * config.RISK_RR
    reward_ticks = int(round(abs(target - entry) / config.tick_size(symbol)))
    rr = config.RISK_RR

    sig_id = tracker.log_signal(
        symbol=symbol, side=side, entry=entry, stop=stop, target=target,
        confidence=confidence, reason=reason, bar_ts=bar_ts,
    )
    tracker.set_units(sig_id, units)

    if notifier:
        notifier.send_signal(
            symbol=symbol, side=side, entry=entry, stop=stop, target=target,
            confidence=confidence, reason=reason, samples=n_samples,
            risk_ticks=risk_ticks, reward_ticks=reward_ticks, units=units,
            risk_dollars=risk_dollars, potential_dollars=potential_dollars,
            rr=rr, balance=paper.balance,
        )


# ---------- inbound commands ----------
_STATS_CMDS = {"/status", "/stats", "/pnl"}
_HELP_CMDS = {"/start", "/help"}


def handle_commands(notifier, tracker, bayes, tuner, paper):
    for text in notifier.poll_commands():
        cmd = text.split()[0].lower().split("@")[0]
        if cmd in _STATS_CMDS:
            overall = tracker.recent_stats()
            per_symbol = {s: tracker.recent_stats(s) for s in config.SYMBOLS}
            thresholds = tuner.snapshot() if config.ADAPTIVE_THRESHOLD else None
            notifier.send_stats(
                overall, per_symbol,
                balance=paper.balance,
                starting=paper.starting,
                roi_pct=paper.roi_pct(),
                bayes_lines=bayes.summary_lines(config.SYMBOLS),
                thresholds=thresholds,
            )
        elif cmd in _HELP_CMDS:
            notifier.send(
                "🤖 *J-Dawg commands*\n\n"
                "📊 /status — full P&L report\n"
                "📊 /pnl — alias\n"
                "📊 /stats — alias\n"
                "❓ /help — this message"
            )


# ---------- daily summary ----------
def maybe_send_daily_summary(notifier, tracker, paper, last_summary_date):
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
    net_dollars = 0.0
    for r in rows:
        s = per_symbol.setdefault(r["symbol"], {"wins": 0, "losses": 0, "pnl_r": 0.0, "pnl_dollars": 0.0})
        if r["outcome"] == "win":
            s["wins"] += 1
        elif r["outcome"] == "loss":
            s["losses"] += 1
        s["pnl_r"] += r["pnl_r"] or 0.0
        s["pnl_dollars"] += r["pnl_dollars"] or 0.0
        net_dollars += r["pnl_dollars"] or 0.0

    notifier.send_daily_summary(
        f"{now_et.strftime('%a %b')} {now_et.day}",
        rows, per_symbol,
        net_dollars=net_dollars,
        balance=paper.balance,
    )
    return now_et.date()


if __name__ == "__main__":
    run()
