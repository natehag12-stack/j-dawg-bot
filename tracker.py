"""
SQLite-backed signal log.

Every fired signal is logged with entry / stop / target.
On each loop we check open signals against new price action:
    - If price hits target → outcome='win',  pnl_r = +RR
    - If price hits stop   → outcome='loss', pnl_r = -1
    - Timeout after N hours → close at market, win/loss from realised PnL
"""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterable
import pandas as pd

import config


_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,        -- ISO UTC
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL,        -- 'long' or 'short'
    entry           REAL    NOT NULL,
    stop            REAL    NOT NULL,
    target          REAL    NOT NULL,
    confidence      REAL,                    -- Bayesian posterior at fire time
    reason          TEXT,
    outcome         TEXT    DEFAULT 'pending', -- 'win' | 'loss' | 'pending'
    pnl_r           REAL,
    exit_price      REAL,                    -- realised fill (paper)
    closed_at       TEXT,
    bar_ts          TEXT                     -- which bar triggered it
);
CREATE INDEX IF NOT EXISTS idx_signals_outcome ON signals(outcome);
CREATE INDEX IF NOT EXISTS idx_signals_bar_ts  ON signals(bar_ts);
"""

_MIGRATIONS = [
    "ALTER TABLE signals ADD COLUMN exit_price REAL",
    "ALTER TABLE signals ADD COLUMN pnl_dollars REAL",
    "ALTER TABLE signals ADD COLUMN units REAL",
]


class Tracker:
    def __init__(self, db_path: str = config.DB_PATH):
        self.db_path = db_path
        with self._conn() as c:
            c.executescript(_SCHEMA)
            # Idempotent migrations for older databases
            for stmt in _MIGRATIONS:
                try:
                    c.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # column already exists

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- writes ----------
    def log_signal(
        self,
        symbol: str,
        side: str,
        entry: float,
        stop: float,
        target: float,
        confidence: float,
        reason: str,
        bar_ts: pd.Timestamp,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO signals
                   (ts, symbol, side, entry, stop, target, confidence, reason, bar_ts)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    symbol,
                    side,
                    float(entry),
                    float(stop),
                    float(target),
                    float(confidence),
                    reason,
                    bar_ts.isoformat(),
                ),
            )
            return cur.lastrowid

    def close_signal(
        self,
        signal_id: int,
        outcome: str,
        pnl_r: float,
        exit_price: float | None = None,
        pnl_dollars: float | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                """UPDATE signals
                   SET outcome=?, pnl_r=?, exit_price=?, pnl_dollars=?, closed_at=?
                   WHERE id=?""",
                (
                    outcome,
                    float(pnl_r),
                    float(exit_price) if exit_price is not None else None,
                    float(pnl_dollars) if pnl_dollars is not None else None,
                    datetime.now(timezone.utc).isoformat(),
                    signal_id,
                ),
            )

    def set_units(self, signal_id: int, units: float) -> None:
        with self._conn() as c:
            c.execute("UPDATE signals SET units=? WHERE id=?", (float(units), signal_id))

    # ---------- reads ----------
    def pending_signals(self) -> list[sqlite3.Row]:
        with self._conn() as c:
            return list(c.execute("SELECT * FROM signals WHERE outcome='pending' ORDER BY id"))

    def was_logged_for_bar(self, bar_ts: pd.Timestamp, side: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM signals WHERE bar_ts=? AND side=? LIMIT 1",
                (bar_ts.isoformat(), side),
            ).fetchone()
            return row is not None

    def recent_stats(self, symbol: str | None = None) -> dict:
        sql = """SELECT
                    SUM(CASE WHEN outcome='win'  THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) AS losses,
                    SUM(CASE WHEN outcome='pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(pnl_r) AS total_r,
                    SUM(pnl_dollars) AS pnl_dollars
                FROM signals"""
        params: tuple = ()
        if symbol:
            sql += " WHERE symbol=?"
            params = (symbol,)
        with self._conn() as c:
            row = c.execute(sql, params).fetchone()
            return {k: row[k] or 0 for k in row.keys()}

    def closed_between(self, start_iso: str, end_iso: str) -> list[sqlite3.Row]:
        """Signals that were closed (win/loss) within the given UTC window."""
        with self._conn() as c:
            return list(
                c.execute(
                    """SELECT * FROM signals
                       WHERE outcome IN ('win','loss')
                         AND closed_at >= ? AND closed_at < ?
                       ORDER BY closed_at""",
                    (start_iso, end_iso),
                )
            )


def check_outcome(row: sqlite3.Row, df: pd.DataFrame) -> tuple[str, float, float, str] | None:
    """
    Given a pending signal and fresh price data, decide if it hit target/stop or timed out.
    Returns (outcome, pnl_r, exit_price, exit_reason) or None if still open.
    """
    ts = datetime.fromisoformat(row["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    idx = df.index
    if idx.tz is None:
        idx_utc = idx.tz_localize("UTC")
    else:
        idx_utc = idx.tz_convert("UTC")
    mask = idx_utc > ts
    since = df[mask]
    if since.empty:
        return None

    side = row["side"]
    entry = row["entry"]
    stop = row["stop"]
    target = row["target"]

    for _, bar in since.iterrows():
        if side == "long":
            hit_stop = bar["Low"] <= stop
            hit_target = bar["High"] >= target
        else:
            hit_stop = bar["High"] >= stop
            hit_target = bar["Low"] <= target

        if hit_stop and hit_target:
            return "loss", -1.0, float(stop), "stop (same-bar conflict)"
        if hit_stop:
            return "loss", -1.0, float(stop), "stop hit"
        if hit_target:
            return "win", float(config.RISK_RR), float(target), "target hit"

    age = datetime.now(timezone.utc) - ts
    if age > timedelta(hours=config.SIGNAL_TIMEOUT_HOURS):
        last = float(since.iloc[-1]["Close"])
        risk = abs(entry - stop)
        if risk == 0:
            return "loss", 0.0, last, "timeout (zero risk)"
        if side == "long":
            pnl_r = (last - entry) / risk
        else:
            pnl_r = (entry - last) / risk
        return ("win" if pnl_r > 0 else "loss", float(pnl_r), last, "timeout")

    return None


def reconcile_pending(
    tracker: Tracker,
    df: pd.DataFrame,
    bayes,
    on_close=None,
    symbol: str | None = None,
    pnl_dollars_fn=None,
) -> int:
    """
    Close out any pending signals whose outcome is now known.
    `pnl_dollars_fn(row, exit_price)` returns realised $ P&L (signed).
    `on_close(row, outcome, pnl_r, pnl_dollars, exit_price, exit_reason)` fires per closure.
    """
    closed = 0
    for row in tracker.pending_signals():
        if symbol is not None and row["symbol"] != symbol:
            continue
        result = check_outcome(row, df)
        if result is None:
            continue
        outcome, pnl_r, exit_price, exit_reason = result
        pnl_dollars = pnl_dollars_fn(row, exit_price) if pnl_dollars_fn else None
        tracker.close_signal(row["id"], outcome, pnl_r, exit_price, pnl_dollars)
        bayes.update(row["symbol"], row["side"], outcome == "win")
        if on_close:
            try:
                on_close(row, outcome, pnl_r, pnl_dollars, exit_price, exit_reason)
            except Exception as e:
                print(f"[reconcile] on_close error: {e}")
        closed += 1
    if closed > 0:
        bayes.save()
    return closed
