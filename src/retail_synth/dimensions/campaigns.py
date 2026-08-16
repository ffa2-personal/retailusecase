"""dim_campaign."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ..config import Config
from ..io_utils import write_table

_TYPES = ["Acquisition", "Seasonal Launch", "Loyalty", "Trunk Show", "Clearance", "Digital Awareness"]
_SEGMENTS = [None, None, "Private Client", "New Customer", "Lapsed Customer", "VIP"]


def build_dim_campaign(cfg: Config, dim_store: pd.DataFrame) -> pd.DataFrame:
    rng = cfg.rng("campaigns")
    hero = cfg.hero_campaign
    rows = [{
        "campaign_id": hero["campaign_id"],
        "campaign_name": hero["name"],
        "campaign_type": "Trunk Show",
        "start_date": dt.date.fromisoformat(hero["start_date"]),
        "end_date": dt.date.fromisoformat(hero["end_date"]),
        "target_store_id": hero["target_store_id"],
        "target_segment": hero["target_segment"],
    }]

    flagship_ids = dim_store.loc[dim_store["store_tier"] == "Flagship", "store_id"].tolist()
    start = cfg.fiscal_start_date
    n_days = cfg.n_weeks * 7
    n = cfg.scale["n_campaigns"] - 1
    for i in range(1, n + 1):
        offset = int(rng.integers(0, max(n_days - 28, 1)))
        c_start = start + dt.timedelta(days=offset)
        duration = int(rng.integers(7, 28))
        c_type = _TYPES[rng.integers(0, len(_TYPES))]
        targeted = rng.random() < 0.35 and flagship_ids
        target_store = flagship_ids[rng.integers(0, len(flagship_ids))] if targeted else None
        rows.append({
            "campaign_id": f"CMP-{i:03d}",
            "campaign_name": f"{c_type} Campaign {i:03d}",
            "campaign_type": c_type,
            "start_date": c_start,
            "end_date": c_start + dt.timedelta(days=duration),
            "target_store_id": target_store,
            "target_segment": _SEGMENTS[rng.integers(0, len(_SEGMENTS))],
        })

    df = pd.DataFrame(rows)
    write_table(cfg, "dim_campaign", df)
    return df
