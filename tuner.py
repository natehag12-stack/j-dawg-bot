"""
Adaptive threshold tuner — the "self-healing" knob.

For each (symbol, side), look at the rolling P&L over the last ADAPTIVE_WINDOW
closed trades. If net R is positive → loosen the gate (lower threshold).
If net R is negative → tighten the gate (raise threshold). Bounded by
ADAPTIVE_MIN / ADAPTIVE_MAX.

The result is a per-(symbol, side) dict of thresholds, persisted to disk so it
survives restarts. main.py uses this to gate alerts in place of the static
config.MIN_POSTERIOR_TO_ALERT when ADAPTIVE_THRESHOLD is enabled.
"""
from __future__ import annotations
import json
import os
import sqlite3

import config


_PATH = os.getenv("TUNER_PATH", "tuner_state.json")


class ThresholdTuner:
    def __init__(self, state: dict | None = None):
        self.state: dict[str, float] = state or {}

    @staticmethod
    def _key(symbol: str, side: str) -> str:
        return f"{symbol}|{side}"

    def threshold(self, symbol: str, side: str) -> float:
        return self.state.get(self._key(symbol, side), config.MIN_POSTERIOR_TO_ALERT)

    def recompute(self, tracker, symbol: str, side: str) -> float:
        """Pull the last ADAPTIVE_WINDOW closed trades for (symbol, side), nudge
        the threshold proportionally to net R, clamp, store."""
        rows = self._recent_trades(tracker, symbol, side, config.ADAPTIVE_WINDOW)
        if len(rows) < 5:  # not enough signal — leave threshold alone
            return self.threshold(symbol, side)

        net_r = sum((r["pnl_r"] or 0.0) for r in rows)
        cur = self.threshold(symbol, side)

        # Step proportional to net R (so a -5R streak moves more than a -1R one)
        step = config.ADAPTIVE_STEP * (1.0 if abs(net_r) >= 1 else 0.5)
        if net_r < 0:
            new = min(config.ADAPTIVE_MAX, cur + step)
        elif net_r > 0:
            new = max(config.ADAPTIVE_MIN, cur - step)
        else:
            new = cur

        if abs(new - cur) > 1e-9:
            self.state[self._key(symbol, side)] = round(new, 4)
            self.save()
        return new

    def snapshot(self) -> dict[str, float]:
        return dict(self.state)

    # ---------- persistence ----------
    def save(self, path: str = _PATH) -> None:
        with open(path, "w") as f:
            json.dump(self.state, f, indent=2)

    @classmethod
    def load(cls, path: str = _PATH) -> "ThresholdTuner":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path) as f:
                return cls(state=json.load(f))
        except Exception:
            return cls()

    # ---------- helpers ----------
    @staticmethod
    def _recent_trades(tracker, symbol: str, side: str, n: int) -> list[sqlite3.Row]:
        with tracker._conn() as c:
            return list(
                c.execute(
                    """SELECT * FROM signals
                       WHERE symbol=? AND side=? AND outcome IN ('win','loss')
                       ORDER BY closed_at DESC LIMIT ?""",
                    (symbol, side, int(n)),
                )
            )
