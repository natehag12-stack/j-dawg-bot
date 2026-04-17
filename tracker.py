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
    closed_at       TEXT,
    bar_ts          TEXT                     -- which bar triggered it
);
CREATE INDEX IF NOT EXISTS idx_signals_outcome ON signals(outcome);
CREATE INDEX IF NOT EXISTS idx_signals_bar_ts  ON signals(bar_ts);
"""


class Tracker:
    def __init__(self, db_path: str = config.DB_PATH):
        self.db_path = db_path
        with self._conn() as c:
            c.executescript(_SCHEMA)

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

    def close_signal(self, signal_id: int, outcome: str, pnl_r: float) -> None:
        with self._conn() as c:
            c.execute(
                """UPDATE signals SET outcome=?, pnl_r=?, closed_at=? WHERE id=?""",
                (outcome, float(pnl_r), datetime.now(timezone.utc).isoformat(), signal_id),
            )

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

    def recent_stats(self) -> dict:
        with self._conn() as c:
            row = c.execute(
                """SELECT
                        SUM(CASE WHEN outcome='win'  THEN 1 ELSE 0 END) AS wins,
                        SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) AS losses,
                        SUM(CASE WHEN outcome='pending' THEN 1 ELSE 0 END) AS pending,
                        SUM(pnl_r) AS total_r
                   FROM signals"""
            ).fetchone()
            return {k: row[k] or 0 for k in row.keys()}


def check_outcome(row: sqlite3.Row, df: pd.DataFrame) -> tuple[str, float] | None:
    """
    Given a pending signal and fresh price data, decide if it hit target/stop or timed out.
    Returns (outcome, pnl_r) or None if still open.
    """
    ts = datetime.fromisoformat(row["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    # slice price action since the signal fired
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

    # Detect whichever happened first, bar-by-bar
    for _, bar in since.iterrows():
        if side == "long":
            hit_stop = bar["Low"] <= stop
            hit_target = bar["High"] >= target
        else:
            hit_stop = bar["High"] >= stop
            hit_target = bar["Low"] <= target

        if hit_stop and hit_target:
            # both in same bar — assume stop hit first (conservative)
            return "loss", -1.0
        if hit_stop:
            return "loss", -1.0
        if hit_target:
            return "win", float(config.RISK_RR)

    # timeout?
    age = datetime.now(timezone.utc) - ts
    if age > timedelta(hours=config.SIGNAL_TIMEOUT_HOURS):
        last = since.iloc[-1]["Close"]
        risk = abs(entry - stop)
        if risk == 0:
            return "loss", 0.0
        if side == "long":
            pnl_r = (last - entry) / risk
        else:
            pnl_r = (entry - last) / risk
        return ("win" if pnl_r > 0 else "loss", float(pnl_r))

    return None


def reconcile_pending(tracker: Tracker, df: pd.DataFrame, bayes) -> int:
    """
    Close out any pending signals whose outcome is now known, and feed the Bayesian model.
    Returns number of signals closed.
    """
    closed = 0
    for row in tracker.pending_signals():
        result = check_outcome(row, df)
        if result is None:
            continue
        outcome, pnl_r = result
        tracker.close_signal(row["id"], outcome, pnl_r)
        bayes.update(row["side"], outcome == "win")
        closed += 1
    if closed > 0:
        bayes.save()
    return closed
