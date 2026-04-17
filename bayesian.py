"""
Self-teaching layer.

We model each side (long / short) as a Beta-Bernoulli process:
    - Prior:  Beta(alpha=1, beta=1)  (uniform — no info)
    - Each trade outcome (win=1 / loss=0) is a Bernoulli trial
    - Posterior after n observations: Beta(alpha + wins, beta + losses)

Posterior mean = alpha / (alpha + beta) = current win rate estimate.
Lower credible bound = conservative estimate (bot waits for evidence).

The bot gates alerts on the posterior:
    - Early (no data): posterior ~ 0.5 → will alert
    - After losses: posterior drops → bot suppresses signals until win rate improves
    - After wins: posterior rises → bot becomes more confident

This is literal self-teaching: the model rewires its own alert threshold from lived experience.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict
from scipy import stats

import config


@dataclass
class BayesianModel:
    alpha_long: float = 1.0
    beta_long: float = 1.0
    alpha_short: float = 1.0
    beta_short: float = 1.0

    # ---------- Queries ----------
    def posterior_mean(self, side: str) -> float:
        a, b = self._params(side)
        return a / (a + b)

    def posterior_lcb(self, side: str, alpha: float = config.CREDIBLE_ALPHA) -> float:
        """Lower bound of (1 - alpha) credible interval. More conservative."""
        a, b = self._params(side)
        return float(stats.beta.ppf(alpha, a, b))

    def confidence(self, side: str) -> float:
        """The score the bot uses to gate alerts."""
        if config.USE_LCB:
            return self.posterior_lcb(side)
        return self.posterior_mean(side)

    def samples(self, side: str) -> int:
        a, b = self._params(side)
        return int(a + b - 2)  # subtract uniform prior

    # ---------- Updates ----------
    def update(self, side: str, won: bool) -> None:
        if side == "long":
            if won:
                self.alpha_long += 1
            else:
                self.beta_long += 1
        elif side == "short":
            if won:
                self.alpha_short += 1
            else:
                self.beta_short += 1
        else:
            raise ValueError(f"unknown side: {side}")

    # ---------- Persistence ----------
    def save(self, path: str = config.BAYES_MODEL_PATH) -> None:
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: str = config.BAYES_MODEL_PATH) -> "BayesianModel":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path) as f:
                data = json.load(f)
            return cls(**data)
        except Exception:
            return cls()

    # ---------- helpers ----------
    def _params(self, side: str) -> tuple[float, float]:
        if side == "long":
            return self.alpha_long, self.beta_long
        if side == "short":
            return self.alpha_short, self.beta_short
        raise ValueError(f"unknown side: {side}")

    def summary(self) -> str:
        return (
            f"LONG  — wins={self.alpha_long - 1:.0f} losses={self.beta_long - 1:.0f} "
            f"mean={self.posterior_mean('long'):.2f} lcb={self.posterior_lcb('long'):.2f}  |  "
            f"SHORT — wins={self.alpha_short - 1:.0f} losses={self.beta_short - 1:.0f} "
            f"mean={self.posterior_mean('short'):.2f} lcb={self.posterior_lcb('short'):.2f}"
        )
