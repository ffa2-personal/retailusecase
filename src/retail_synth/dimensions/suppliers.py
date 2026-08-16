"""dim_supplier."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from ..io_utils import write_table

_SUPPLIER_COUNTRIES = ["IT", "PT", "CN", "VN", "IN", "TR", "RO", "BD", "GB", "FR"]
_NAME_STEMS = ["Textile Works", "Garment Manufacturing", "Apparel Group", "Fabrics Co", "Knitwear Mills",
               "Outerwear Manufacturing", "Leather Goods", "Tailoring House", "Mill & Co", "Weavers Guild"]


def build_dim_supplier(cfg: Config) -> pd.DataFrame:
    rng = cfg.rng("suppliers")
    hero = cfg.hero_supplier
    rows = [{
        "supplier_id": hero["supplier_id"],
        "supplier_name": hero["name"],
        "country": hero["country"],
        "primary_category": hero["primary_category"],
        "lead_time_days_standard": 45,
        "reliability_score": 0.9,
        "primary_dc_id": hero["primary_dc"],
    }]

    n = cfg.scale["n_suppliers"] - 1
    for i in range(1, n + 1):
        country = _SUPPLIER_COUNTRIES[rng.integers(0, len(_SUPPLIER_COUNTRIES))]
        stem = _NAME_STEMS[rng.integers(0, len(_NAME_STEMS))]
        rows.append({
            "supplier_id": f"SUP-{i:03d}",
            "supplier_name": f"{country} {stem} {i:03d}",
            "country": country,
            "primary_category": cfg.categories[rng.integers(0, len(cfg.categories))],
            "lead_time_days_standard": int(rng.integers(21, 70)),
            "reliability_score": round(float(rng.uniform(0.75, 0.99)), 3),
            "primary_dc_id": None,
        })

    df = pd.DataFrame(rows)
    write_table(cfg, "dim_supplier", df)
    return df
