"""
Combines all SMC indicators into final long/short entry signals.

Entry conditions (exact match with Pine script):
    longCondition  = inSession AND bullBias AND longSweep  AND displacementBull
    shortCondition = inSession AND bearBias AND shortSweep AND displacementBear
"""
from __future__ import annotations
import pandas as pd

from indicators import (
    htf_bias,
    align_bias_to_5m,
    prev_day_levels,
    swing_points,
    displacement,
    liquidity_sweeps,
    in_ny_session,
    detect_fvg,
)


def generate_signals(df_5m: pd.DataFrame, df_1h: pd.DataFrame) -> dict:
    """
    Returns a dict of boolean Series aligned to df_5m.index:
        - long_cond
        - short_cond
        - components  (dict of sub-conditions for debugging/explainability)
    """
    bias_1h = htf_bias(df_1h)
    bias_5m = align_bias_to_5m(bias_1h, df_5m)
    bull_bias = (bias_5m == 1)
    bear_bias = (bias_5m == -1)

    pdh, pdl = prev_day_levels(df_5m)
    swing_high, swing_low = swing_points(df_5m)

    long_sweep, short_sweep = liquidity_sweeps(
        df_5m, pdh, pdl, swing_high, swing_low
    )

    disp_bull, disp_bear = displacement(df_5m)
    fvg_bull, fvg_bear = detect_fvg(df_5m)

    session = in_ny_session(df_5m)

    long_cond = session & bull_bias & long_sweep & disp_bull
    short_cond = session & bear_bias & short_sweep & disp_bear

    return {
        "long_cond": long_cond.fillna(False),
        "short_cond": short_cond.fillna(False),
        "components": {
            "in_session": session,
            "bull_bias": bull_bias,
            "bear_bias": bear_bias,
            "long_sweep": long_sweep,
            "short_sweep": short_sweep,
            "disp_bull": disp_bull,
            "disp_bear": disp_bear,
            "fvg_bull": fvg_bull,
            "fvg_bear": fvg_bear,
            "pdh": pdh,
            "pdl": pdl,
            "swing_high": swing_high,
            "swing_low": swing_low,
        },
    }


def explain(components: dict, idx: int) -> str:
    """Human-readable reason why a signal fired at bar idx."""
    c = components
    parts = []
    if c["bull_bias"].iloc[idx]:
        parts.append("1H bias bullish")
    elif c["bear_bias"].iloc[idx]:
        parts.append("1H bias bearish")
    if c["long_sweep"].iloc[idx]:
        parts.append(f"swept PDL/swing low")
    if c["short_sweep"].iloc[idx]:
        parts.append(f"swept PDH/swing high")
    if c["disp_bull"].iloc[idx]:
        parts.append("bullish displacement candle")
    if c["disp_bear"].iloc[idx]:
        parts.append("bearish displacement candle")
    if c["fvg_bull"].iloc[idx]:
        parts.append("+ bullish FVG present")
    if c["fvg_bear"].iloc[idx]:
        parts.append("+ bearish FVG present")
    return " · ".join(parts)
