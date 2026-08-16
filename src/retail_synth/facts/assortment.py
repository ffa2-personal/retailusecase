"""store_sku_assortment: which SKUs are listed where (Store or Ecommerce-by-region)
and for how long. This is the sparse bridge that drives all downstream demand /
inventory generation -- nothing is generated for a location-sku pair outside its
listing window.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..calendar import date_to_week_id
from ..config import Config
from ..io_utils import write_table

_STORE_TIER_COVERAGE = {"Flagship": 0.12, "A": 0.06, "B": 0.03, "C": 0.015}
_ECOM_BULK_COVERAGE = 0.08
_CLEARANCE_WEEKS = 6


def build_store_sku_assortment(cfg: Config, dim_style: pd.DataFrame, dim_sku: pd.DataFrame,
                                dim_store: pd.DataFrame, dim_region: pd.DataFrame) -> pd.DataFrame:
    rng = cfg.rng("assortment")

    hero_style_ids = {h["style_id"] for h in cfg.hero_styles}
    cap = cfg.capsule
    capsule_style_ids = {f"{cap['style_prefix']}-{i:02d}" for i in range(1, cap["n_styles"] + 1)}
    forced_style_ids = hero_style_ids | capsule_style_ids

    win_lo, win_hi = cfg.min_week_id, cfg.max_week_id
    style_weeks: dict[str, tuple[int, int]] = {}
    for row in dim_style.itertuples():
        s = date_to_week_id(cfg, row.launch_date)
        e = date_to_week_id(cfg, row.planned_exit_date + pd.Timedelta(weeks=_CLEARANCE_WEEKS))
        s, e = max(s, win_lo), min(e, win_hi)
        if s <= e:
            style_weeks[row.style_id] = (s, e)
    in_window = dim_sku["style_id"].isin(style_weeks.keys())
    sku_style = dim_sku.set_index("sku_id")["style_id"]

    forced_skus = dim_sku.loc[in_window & dim_sku["style_id"].isin(forced_style_ids), "sku_id"].tolist()
    bulk_skus = dim_sku.loc[in_window & ~dim_sku["style_id"].isin(forced_style_ids), "sku_id"].tolist()

    frames: list[pd.DataFrame] = []

    # ---- Store listings ----
    for tier, coverage in _STORE_TIER_COVERAGE.items():
        stores = dim_store.loc[dim_store["store_tier"] == tier, "store_id"].tolist()
        if not stores:
            continue
        n_bulk_pick = max(int(len(bulk_skus) * coverage), 1)
        for store_id in stores:
            picked_bulk = rng.choice(bulk_skus, size=min(n_bulk_pick, len(bulk_skus)), replace=False)
            skus_here = np.concatenate([forced_skus, picked_bulk])
            frames.append(_rows_for_location(skus_here, store_id, "Store", sku_style, style_weeks, forced_style_ids))

    # ---- Ecommerce listings (one virtual location per region) ----
    n_ecom_bulk = max(int(len(bulk_skus) * _ECOM_BULK_COVERAGE), 1)
    for region in dim_region.itertuples():
        location_id = f"ECOM-{region.region_code}"
        picked_bulk = rng.choice(bulk_skus, size=min(n_ecom_bulk, len(bulk_skus)), replace=False)
        skus_here = np.concatenate([forced_skus, picked_bulk])
        frames.append(_rows_for_location(skus_here, location_id, "Ecommerce", sku_style, style_weeks, forced_style_ids,
                                          region_code=region.region_code))

    df = pd.concat(frames, ignore_index=True)
    store_region = dim_store.set_index("store_id")["region_code"]
    missing = df["region_code"].isna()
    df.loc[missing, "region_code"] = df.loc[missing, "location_id"].map(store_region)
    write_table(cfg, "store_sku_assortment", df)
    return df


def _rows_for_location(skus: np.ndarray, location_id: str, location_type: str, sku_style: pd.Series,
                        style_weeks: dict, forced_style_ids: set, region_code: str | None = None) -> pd.DataFrame:
    styles = sku_style.loc[skus].to_numpy()
    starts = np.array([style_weeks[s][0] for s in styles], dtype="int32")
    ends = np.array([style_weeks[s][1] for s in styles], dtype="int32")
    is_core = np.isin(styles, list(forced_style_ids))
    return pd.DataFrame({
        "location_id": location_id,
        "location_type": location_type,
        "region_code": region_code,
        "sku_id": skus,
        "assortment_start_week": starts,
        "assortment_end_week": ends,
        "is_core_flag": is_core,
    })
