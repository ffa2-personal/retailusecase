"""Loads scenario_config.yaml and hands out deterministic, isolated RNGs per module."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "scenario_config.yaml"

# Order matters for reproducibility: each name gets its own child SeedSequence.
_RNG_STREAMS = [
    "stores", "dcs", "suppliers", "campaigns", "customers", "products", "fx",
    "assortment", "weather", "allocation_po", "demand_engine", "returns",
    "campaign_exposure", "digital_engagement",
]


class Config:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw
        self.seed: int = raw["seed"]
        self.dev_mode: bool = raw["dev_mode"]

        seq = np.random.SeedSequence(self.seed)
        children = seq.spawn(len(_RNG_STREAMS))
        self._rngs = {
            name: np.random.default_rng(child)
            for name, child in zip(_RNG_STREAMS, children)
        }

    def rng(self, name: str) -> np.random.Generator:
        if name not in self._rngs:
            raise KeyError(f"Unknown RNG stream '{name}'. Add it to _RNG_STREAMS in config.py.")
        return self._rngs[name]

    # --- paths -------------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        p = REPO_ROOT / self.raw["paths"]["raw_dir"]
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def duckdb_path(self) -> Path:
        p = REPO_ROOT / self.raw["paths"]["duckdb_path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # --- calendar ------------------------------------------------------
    # fiscal_start_date is ALWAYS the real, fixed date -- both modes use the
    # same absolute week numbering, so hero/scenario dates (which are real
    # calendar dates) resolve to the same week_id regardless of dev_mode.
    # dev_mode simply narrows which week_id WINDOW gets generated (see
    # min_week_id/max_week_id), rather than shifting the calendar itself.
    @property
    def fiscal_start_date(self) -> dt.date:
        return dt.date.fromisoformat(self.raw["calendar"]["fiscal_start_date"])

    @property
    def as_of_date(self) -> dt.date:
        return dt.date.fromisoformat(self.raw["calendar"]["as_of_date"])

    @property
    def max_week_id_global(self) -> int:
        return self.raw["calendar"]["n_weeks_full"]

    def _as_of_week_id_raw(self) -> int:
        delta_days = (self.as_of_date - self.fiscal_start_date).days
        return delta_days // 7 + 1

    # How many weeks past as_of_date to keep generating in dev mode. Needs to
    # be generous: "remaining season weeks" for any still-active style is
    # measured against this window boundary, so too short a tail makes every
    # active style look artificially overstocked (denominator collapses to ~1).
    _DEV_TAIL_WEEKS_AFTER_AS_OF = 16

    @property
    def min_week_id(self) -> int:
        if not self.dev_mode:
            return 1
        n_dev = self.raw["calendar"]["n_weeks_dev"]
        return max(self._as_of_week_id_raw() - n_dev + self._DEV_TAIL_WEEKS_AFTER_AS_OF, 1)

    @property
    def max_week_id(self) -> int:
        if not self.dev_mode:
            return self.max_week_id_global
        n_dev = self.raw["calendar"]["n_weeks_dev"]
        return min(self.min_week_id + n_dev - 1, self.max_week_id_global)

    @property
    def n_weeks(self) -> int:
        return self.max_week_id - self.min_week_id + 1

    # --- regions / dcs ---------------------------------------------------
    @property
    def regions(self) -> list[dict]:
        return self.raw["regions"]

    def region_store_count(self, region: dict) -> int:
        return region["stores_dev"] if self.dev_mode else region["stores_full"]

    @property
    def dcs(self) -> list[dict]:
        return self.raw["dcs"]

    @property
    def hero_stores(self) -> list[dict]:
        return self.raw["hero_stores"]

    @property
    def hero_supplier(self) -> dict:
        return self.raw["hero_supplier"]

    @property
    def hero_campaign(self) -> dict:
        return self.raw["hero_campaign"]

    @property
    def hero_styles(self) -> list[dict]:
        return self.raw["hero_styles"]

    @property
    def capsule(self) -> dict:
        return self.raw["capsule"]

    # --- scale -----------------------------------------------------------
    @property
    def scale(self) -> dict:
        return self.raw["scale"]["dev" if self.dev_mode else "full"]

    @property
    def categories(self) -> list[str]:
        return self.raw["categories"]

    @property
    def category_mix(self) -> dict[str, float]:
        return self.raw["category_mix"]

    @property
    def seasonal_drops(self) -> list[str]:
        return self.raw["seasonal_drops"]

    # --- scenarios ---------------------------------------------------------
    @property
    def scenarios(self) -> dict:
        return self.raw["scenarios"]

    @property
    def verify(self) -> dict:
        return self.raw["verify"]


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Config(raw)
