"""dim_customer. Vectorized for scale (up to 500k rows)."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..config import Config
from ..io_utils import write_table

_LOYALTY_TIERS = ["None", "Silver", "Gold", "Platinum", "Private Client"]
_LOYALTY_P = [0.55, 0.22, 0.13, 0.07, 0.03]
_CHANNELS = ["Store", "Ecommerce", "Marketing Campaign", "Referral"]
_CHANNEL_P = [0.45, 0.35, 0.15, 0.05]


def build_dim_customer(cfg: Config, dim_region: pd.DataFrame, dim_store: pd.DataFrame,
                        dim_campaign: pd.DataFrame) -> pd.DataFrame:
    rng = cfg.rng("customers")
    hero_campaign = cfg.hero_campaign
    cohort_size = hero_campaign["cohort_size_dev"] if cfg.dev_mode else hero_campaign["cohort_size_full"]
    n_total = cfg.scale["n_customers"]
    n_generic = max(n_total - cohort_size, 0)

    n_days = cfg.n_weeks * 7
    start = cfg.fiscal_start_date

    # store counts per region -> weight generic customer home_region by market presence
    store_counts = dim_store.groupby("region_code").size()
    region_codes = store_counts.index.tolist()
    region_weights = (store_counts / store_counts.sum()).values

    region_to_country = dim_region.set_index("region_code")["country"].to_dict()

    # ---- generic customers (vectorized) ----
    home_region = rng.choice(region_codes, size=n_generic, p=region_weights)
    home_country = np.array([region_to_country[r] for r in home_region])
    offsets = rng.integers(0, n_days, size=n_generic)
    first_seen = np.array([start + dt.timedelta(days=int(o)) for o in offsets])
    acquisition_channel = rng.choice(_CHANNELS, size=n_generic, p=_CHANNEL_P)
    loyalty_tier = rng.choice(_LOYALTY_TIERS, size=n_generic, p=_LOYALTY_P)
    is_vip = np.isin(loyalty_tier, ["Platinum", "Private Client"])
    preferred_channel = rng.choice(["Store", "Ecommerce"], size=n_generic, p=[0.55, 0.45])

    # a minority of generic customers are attributed to a non-hero campaign
    other_campaigns = dim_campaign.loc[dim_campaign["campaign_id"] != hero_campaign["campaign_id"], "campaign_id"].tolist()
    has_campaign = rng.random(n_generic) < 0.12
    campaign_choice = rng.choice(other_campaigns, size=n_generic)
    acquisition_campaign_id = np.where(has_campaign, campaign_choice, None)

    generic_df = pd.DataFrame({
        "customer_id": [f"CUST-{i:07d}" for i in range(1, n_generic + 1)],
        "first_seen_date": first_seen,
        "acquisition_channel": acquisition_channel,
        "acquisition_campaign_id": acquisition_campaign_id,
        "acquisition_store_id": None,
        "home_country": home_country,
        "home_region": home_region,
        "loyalty_tier": loyalty_tier,
        "is_vip_flag": is_vip,
        "preferred_channel": preferred_channel,
    })

    # ---- Milan cohort (hero) ----
    c_start = dt.date.fromisoformat(hero_campaign["start_date"])
    c_end = dt.date.fromisoformat(hero_campaign["end_date"])
    span = (c_end - c_start).days
    cohort_offsets = rng.integers(0, span + 1, size=cohort_size)
    cohort_first_seen = np.array([c_start + dt.timedelta(days=int(o)) for o in cohort_offsets])
    cohort_loyalty = rng.choice(["Gold", "Platinum", "Private Client"], size=cohort_size, p=[0.3, 0.4, 0.3])

    cohort_df = pd.DataFrame({
        "customer_id": [f"CUST-MI-{i:05d}" for i in range(1, cohort_size + 1)],
        "first_seen_date": cohort_first_seen,
        "acquisition_channel": "Marketing Campaign",
        "acquisition_campaign_id": hero_campaign["campaign_id"],
        "acquisition_store_id": hero_campaign["target_store_id"],
        "home_country": "IT",
        "home_region": "ITA",
        "loyalty_tier": cohort_loyalty,
        "is_vip_flag": True,
        "preferred_channel": rng.choice(["Store", "Ecommerce"], size=cohort_size, p=[0.65, 0.35]),
    })

    df = pd.concat([generic_df, cohort_df], ignore_index=True)
    write_table(cfg, "dim_customer", df)
    return df
