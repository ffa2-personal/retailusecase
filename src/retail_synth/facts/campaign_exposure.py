"""fact_campaign_exposure: campaign x customer (sparse)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from ..io_utils import write_table


def build_campaign_exposure(cfg: Config, dim_campaign: pd.DataFrame, dim_customer: pd.DataFrame) -> pd.DataFrame:
    rng = cfg.rng("campaign_exposure")
    hero_campaign_id = cfg.hero_campaign["campaign_id"]
    scale_ratio = cfg.scale["n_customers"] / cfg.raw["scale"]["full"]["n_customers"]

    frames = []
    for row in dim_campaign.itertuples():
        if row.campaign_id == hero_campaign_id:
            exposed = dim_customer.loc[dim_customer["acquisition_campaign_id"] == hero_campaign_id, "customer_id"]
        else:
            pool = dim_customer
            if row.target_segment and row.target_segment in dim_customer["loyalty_tier"].unique():
                pool = dim_customer.loc[dim_customer["loyalty_tier"] == row.target_segment]
                if len(pool) < 100:
                    pool = dim_customer
            n = int(rng.integers(2000, 15000) * scale_ratio)
            n = min(max(n, 50), len(pool))
            exposed = pool["customer_id"].sample(n=n, random_state=int(rng.integers(0, 2**31 - 1)))

        if len(exposed) == 0:
            continue
        offset_days = rng.integers(-3, 3, size=len(exposed))
        frames.append(pd.DataFrame({
            "campaign_id": row.campaign_id,
            "customer_id": exposed.to_numpy(),
            "exposure_date": pd.Timestamp(row.start_date) + pd.to_timedelta(offset_days, unit="D"),
        }))

    df = pd.concat(frames, ignore_index=True)
    write_table(cfg, "fact_campaign_exposure", df)
    return df
