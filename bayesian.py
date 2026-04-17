"""
Self-teaching layer (per symbol, per side).

Each (symbol, side) is a Beta-Bernoulli process:
    Prior: Beta(1, 1)  →  Posterior: Beta(1 + wins, 1 + losses)

Posterior mean = current win-rate estimate.
Lower credible bound = conservative estimate (waits for evidence).

Stored as JSON:
    {"symbols": {"NQ=F": {"alpha_long": 4, "beta_long": 2, ...}, "QQQ": {...}}}

Old single-symbol JSON format is auto-migrated on load.
"""
from __future__ import annotations
import json
import os
from typing import Iterable
from scipy import stats

import config


_LEGACY_KEY = "_legacy"


def _new_state() -> dict:
    return {
        "alpha_long": 1.0,
        "beta_long": 1.0,
        "alpha_short": 1.0,
        "beta_short": 1.0,
    }


class BayesianModel:
    """Container for per-symbol Beta-Bernoulli state."""

    def __init__(self, symbols: dict[str, dict] | None = None):
        self.symbols: dict[str, dict] = symbols or {}

    # ---------- accessors ----------
    def _state(self, symbol: str) -> dict:
        if symbol not in self.symbols:
            self.symbols[symbol] = _new_state()
        return self.symbols[symbol]

    def _params(self, symbol: str, side: str) -> tuple[float, float]:
        st = self._state(symbol)
        if side == "long":
            return st["alpha_long"], st["beta_long"]
        if side == "short":
            return st["alpha_short"], st["beta_short"]
        raise ValueError(f"unknown side: {side}")

    def posterior_mean(self, symbol: str, side: str) -> float:
        a, b = self._params(symbol, side)
        return a / (a + b)

    def posterior_lcb(self, symbol: str, side: str, alpha: float = config.CREDIBLE_ALPHA) -> float:
        a, b = self._params(symbol, side)
        return float(stats.beta.ppf(alpha, a, b))

    def confidence(self, symbol: str, side: str) -> float:
        if config.USE_LCB:
            return self.posterior_lcb(symbol, side)
        return self.posterior_mean(symbol, side)

    def samples(self, symbol: str, side: str) -> int:
        a, b = self._params(symbol, side)
        return int(a + b - 2)

    # ---------- updates ----------
    def update(self, symbol: str, side: str, won: bool) -> None:
        st = self._state(symbol)
        key_a = f"alpha_{side}"
        key_b = f"beta_{side}"
        if key_a not in st:
            raise ValueError(f"unknown side: {side}")
        if won:
            st[key_a] += 1
        else:
            st[key_b] += 1

    # ---------- persistence ----------
    def save(self, path: str = config.BAYES_MODEL_PATH) -> None:
        with open(path, "w") as f:
            json.dump({"symbols": self.symbols}, f, indent=2)

    @classmethod
    def load(cls, path: str = config.BAYES_MODEL_PATH) -> "BayesianModel":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            return cls()

        # New format
        if isinstance(data, dict) and "symbols" in data:
            return cls(symbols={k: {**_new_state(), **v} for k, v in data["symbols"].items()})

        # Legacy format: single global state
        if isinstance(data, dict) and "alpha_long" in data:
            return cls(symbols={_LEGACY_KEY: {**_new_state(), **data}})

        return cls()

    # ---------- summary ----------
    def summary(self, symbols: Iterable[str] | None = None) -> str:
        if symbols is None:
            keys = list(self.symbols.keys()) or [_LEGACY_KEY]
        else:
            keys = list(symbols)
        lines = []
        for sym in keys:
            wL = self._state(sym)["alpha_long"] - 1
            lL = self._state(sym)["beta_long"] - 1
            wS = self._state(sym)["alpha_short"] - 1
            lS = self._state(sym)["beta_short"] - 1
            mL = self.posterior_mean(sym, "long")
            cL = self.posterior_lcb(sym, "long")
            mS = self.posterior_mean(sym, "short")
            cS = self.posterior_lcb(sym, "short")
            lines.append(
                f"{sym:<8} L:{int(wL)}-{int(lL)} mean={mL:.2f} lcb={cL:.2f}  "
                f"S:{int(wS)}-{int(lS)} mean={mS:.2f} lcb={cS:.2f}"
            )
        return "\n".join(lines)
