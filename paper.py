"""
Paper trading state — tracks the virtual account balance.

Persisted to disk so balance survives restarts.
"""
from __future__ import annotations
import json
import os

import config


class PaperState:
    def __init__(self, starting: float, balance: float, peak: float, realized: float):
        self.starting = starting
        self.balance = balance
        self.peak = peak
        self.realized = realized  # cumulative realized P&L in $

    @classmethod
    def load(cls, path: str = config.PAPER_STATE_PATH) -> "PaperState":
        if os.path.exists(path):
            try:
                with open(path) as f:
                    d = json.load(f)
                return cls(
                    starting=d.get("starting", config.PAPER_STARTING_BALANCE),
                    balance=d.get("balance", config.PAPER_STARTING_BALANCE),
                    peak=d.get("peak", config.PAPER_STARTING_BALANCE),
                    realized=d.get("realized", 0.0),
                )
            except Exception:
                pass
        s = config.PAPER_STARTING_BALANCE
        return cls(starting=s, balance=s, peak=s, realized=0.0)

    def save(self, path: str = config.PAPER_STATE_PATH) -> None:
        with open(path, "w") as f:
            json.dump({
                "starting": self.starting,
                "balance": self.balance,
                "peak": self.peak,
                "realized": self.realized,
            }, f, indent=2)

    def apply_pnl(self, dollars: float) -> None:
        self.balance += dollars
        self.realized += dollars
        if self.balance > self.peak:
            self.peak = self.balance
        self.save()

    def roi_pct(self) -> float:
        if self.starting <= 0:
            return 0.0
        return (self.balance - self.starting) / self.starting * 100.0


def position_size(symbol: str, entry: float, stop: float, risk_dollars: float) -> tuple[float, int]:
    """Returns (units, risk_ticks). 'units' = contracts for futures, shares for ETFs."""
    risk_per_unit_price = abs(entry - stop)
    ts = config.tick_size(symbol)
    tv = config.tick_value(symbol)
    if risk_per_unit_price <= 0 or ts <= 0 or tv <= 0:
        return 0.0, 0
    risk_ticks = int(round(risk_per_unit_price / ts))
    risk_per_unit_dollars = risk_ticks * tv
    if risk_per_unit_dollars <= 0:
        return 0.0, risk_ticks
    units = risk_dollars / risk_per_unit_dollars
    return units, risk_ticks
