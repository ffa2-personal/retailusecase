"""fact_digital_engagement: sku x week, ecommerce only.

Derived mostly from realized ecommerce sales (views/add-to-cart scale with
purchases), plus a scenario-2 leading-indicator bump: page views for the
viral style spike 1-2 weeks *before* the sales spike actually lands.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..calendar import date_to_week_id
from ..config import Config
from ..io_utils import write_table

_VIEWS_PER_UNIT = 22.0
_ATC_PER_UNIT = 3.5


def build_digital_engagement(cfg: Config, sales: pd.DataFrame) -> pd.DataFrame:
    rng = cfg.rng("digital_engagement")
    ecom = sales.loc[sales["channel"] == "Ecommerce"]
    agg = ecom.groupby(["sku_id", "week_id"], as_index=False)["units"].sum()

    noise = rng.normal(1.0, 0.15, size=len(agg))
    agg["page_views"] = np.clip((agg["units"] * _VIEWS_PER_UNIT * noise), 1, None).round().astype("int32")
    agg["add_to_cart"] = np.clip((agg["units"] * _ATC_PER_UNIT * noise), 0, None).round().astype("int32")

    vp = cfg.scenarios["viral_product"]
    trigger_week = date_to_week_id(cfg, dt.date.fromisoformat(vp["trigger_date"]))
    is_viral_sku = agg["sku_id"].str.startswith(vp["style_id"])
    for w in (trigger_week - 2, trigger_week - 1):
        mask = is_viral_sku & (agg["week_id"] == w)
        if mask.any():
            agg.loc[mask, "page_views"] = (agg.loc[mask, "page_views"] * 3.5).round().astype("int32")
            agg.loc[mask, "add_to_cart"] = (agg.loc[mask, "add_to_cart"] * 3.0).round().astype("int32")

    agg = agg.drop(columns=["units"])
    write_table(cfg, "fact_digital_engagement", agg)
    return agg
