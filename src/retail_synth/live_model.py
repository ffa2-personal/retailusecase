"""Reusable pieces of the demand model, shared between the batch generator
(facts/demand_engine.py) and the live POS stream simulator
(scripts/pos_stream_simulator.py) -- so the live feed stays statistically
consistent with the batch-generated history instead of duplicating the model.

Pure functions/constants only: no DuckDB, no file I/O.
"""
from __future__ import annotations

import numpy as np

TIER_WEIGHT = {"Flagship": 3.0, "A": 1.5, "B": 0.8, "C": 0.4}
CATEGORY_HOLIDAY_FACTOR = {"Outerwear": 1.6, "Knitwear": 1.6, "Tailoring": 1.2, "Accessories": 1.3, "Footwear": 1.3}
BASE_SCALE = 0.55


def lifecycle_mult(t: np.ndarray) -> np.ndarray:
    """Product-lifecycle curve: ramp (0-3 weeks) -> peak (4-16) -> decline
    (17-30) -> tail (31+). `t` is weeks-since-launch (or a fast-forward
    tick-based analogue for the live simulator)."""
    out = np.empty_like(t, dtype="float32")
    ramp = t < 4
    peak = (t >= 4) & (t < 17)
    decline = (t >= 17) & (t < 31)
    tail = t >= 31
    out[ramp] = 0.3 + 0.175 * t[ramp]
    out[peak] = 1.0
    out[decline] = 1.0 - (t[decline] - 17) * (0.65 / 14)
    out[tail] = 0.455
    return out


def viral_ramp_mult(ticks_since_trigger: np.ndarray, spike_multiplier: float, settle_multiplier: float,
                     spike_ticks: int, pre_trigger_mult: float = 1.0) -> np.ndarray:
    """Fast-forward analogue of the batch viral_product scenario bias: a short
    spike window (spike_ticks ticks at spike_multiplier) followed by a settle
    plateau (settle_multiplier) -- driven by ticks elapsed since a live
    trigger instead of weeks elapsed since a scripted calendar date.

    ticks_since_trigger < 0 (not yet triggered) returns pre_trigger_mult
    everywhere -- the live simulator sets this below 1.0 so the item reads as
    an unremarkable normal seller before the demo's trigger moment, instead
    of draining its starting stock during narration.
    """
    mult = np.full(ticks_since_trigger.shape, pre_trigger_mult, dtype="float64")
    triggered = ticks_since_trigger >= 0
    spiking = triggered & (ticks_since_trigger < spike_ticks)
    settled = triggered & (ticks_since_trigger >= spike_ticks)
    mult[spiking] = spike_multiplier
    mult[settled] = settle_multiplier
    return mult
