"""The core weekly demand/supply engine: produces fact_sales_line and
fact_inventory_position.

Design: for every (location, sku) listing, compute an *unconstrained* weekly
demand rate D_t (Poisson mean) driven by style popularity, location weight,
product-lifecycle curve, seasonality, and the weather/viral demand-side
scenario biases. Independently compute a *supply schedule* S_t (cumulative
units ever made available to that location for that sku), driven by an
initial-allocation + linear-replenishment model, with the poor-allocation and
supply-disruption scenario biases applied to its parameters.

Because S_t is monotonic and independent of realized sales, realized
cumulative sales is exactly min(cumsum(D_t), S_t) -- this lets the whole
156-week simulation be computed with vectorized groupby/cumsum operations
instead of a slow row-by-row event loop.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..calendar import date_to_week_id
from ..config import Config
from ..io_utils import write_table
from ..live_model import BASE_SCALE as _BASE_SCALE
from ..live_model import CATEGORY_HOLIDAY_FACTOR as _CATEGORY_HOLIDAY_FACTOR
from ..live_model import TIER_WEIGHT as _TIER_WEIGHT
from ..live_model import lifecycle_mult as _lifecycle_mult

_AVG_MULT_FOR_SIZING = 0.45
_TARGET_SELLTHROUGH_NORMAL = 0.80
_INITIAL_ALLOC_FRACTION = 0.55


def build_demand_and_inventory(
    cfg: Config,
    dim_week: pd.DataFrame,
    dim_region: pd.DataFrame,
    dim_store: pd.DataFrame,
    dim_style: pd.DataFrame,
    dim_sku: pd.DataFrame,
    dim_customer: pd.DataFrame,
    assortment: pd.DataFrame,
    weather_actual: pd.DataFrame,
    disruption_styles: list[str],
    allocation_extra_styles: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = cfg.rng("demand_engine")

    # ---------------------------------------------------------------
    # 1. Per-listing static attributes
    # ---------------------------------------------------------------
    style_idx = dim_style.set_index("style_id")
    style_pop = pd.Series(np.exp(rng.normal(0, 0.5, size=len(style_idx))), index=style_idx.index, name="style_pop")
    launch_week = style_idx["launch_date"].apply(lambda d: date_to_week_id(cfg, d))
    launch_week.name = "launch_week"

    listings = assortment.merge(dim_sku[["sku_id", "style_id", "current_retail_price", "size"]], on="sku_id", how="left")
    listings = listings.merge(dim_style[["style_id", "category", "warmth_rating"]], on="style_id", how="left")
    listings = listings.merge(style_pop, left_on="style_id", right_index=True, how="left")
    listings = listings.merge(launch_week, left_on="style_id", right_index=True, how="left")

    store_tier = dim_store.set_index("store_id")["store_tier"]
    listings["store_tier"] = np.where(listings["location_type"] == "Store", listings["location_id"].map(store_tier), None)

    store_count_by_region = dim_store.groupby("region_code").size()
    is_store = listings["location_type"] == "Store"
    loc_weight = np.empty(len(listings), dtype="float32")
    loc_weight[is_store.to_numpy()] = listings.loc[is_store, "store_tier"].map(_TIER_WEIGHT).fillna(0.6).to_numpy()
    ecom_weight_by_region = (store_count_by_region * 0.5)
    is_ecom = ~is_store
    loc_weight[is_ecom.to_numpy()] = listings.loc[is_ecom, "region_code"].map(ecom_weight_by_region).fillna(1.0).to_numpy()
    listings["location_weight"] = loc_weight

    n_active_weeks = (listings["assortment_end_week"] - listings["assortment_start_week"] + 1).clip(lower=1)
    listings["expected_total_demand"] = (
        listings["style_pop"] * listings["location_weight"] * n_active_weeks * _AVG_MULT_FOR_SIZING
    )
    supply_cap = listings["expected_total_demand"] / _TARGET_SELLTHROUGH_NORMAL
    initial_alloc = supply_cap * _INITIAL_ALLOC_FRACTION
    replenish_rate = (supply_cap - initial_alloc) / n_active_weeks.clip(lower=1)

    # ---- scenario 5: poor allocation (Store listings only) ----
    pa = cfg.scenarios["poor_allocation"]
    hero_style_ids = [h["style_id"] for h in cfg.hero_styles]
    hero_outerwear = [s for s in hero_style_ids if style_idx.loc[s, "category"] == "Outerwear"]
    pa_styles = set(hero_outerwear) | set(allocation_extra_styles)
    surplus_regions = set(pa["surplus_regions"])
    shortage_regions = set(pa["shortage_regions"])
    is_pa_style = listings["style_id"].isin(pa_styles)
    surplus_mask = (is_store & is_pa_style & listings["region_code"].isin(surplus_regions)).to_numpy()
    shortage_mask = (is_store & is_pa_style & listings["region_code"].isin(shortage_regions)).to_numpy()

    initial_alloc = initial_alloc.to_numpy(dtype="float64").copy()
    replenish_rate = replenish_rate.to_numpy(dtype="float64").copy()
    supply_cap = supply_cap.to_numpy(dtype="float64").copy()

    initial_alloc[surplus_mask] *= (1 + pa["surplus_overallocation_pct"])
    supply_cap[surplus_mask] *= (1 + pa["surplus_overallocation_pct"])
    initial_alloc[shortage_mask] *= (1 - pa["shortage_underallocation_pct"])
    replenish_rate[shortage_mask] *= 0.2

    # ---- scenario 4: supply disruption (Store listings, affected regions) ----
    sd = cfg.scenarios["supply_disruption"]
    affected_regions = set(sd["affected_regions"])
    is_sd_style = listings["style_id"].isin(set(disruption_styles))
    sd_mask = (is_store & is_sd_style & listings["region_code"].isin(affected_regions)).to_numpy()
    freeze_start_week = date_to_week_id(cfg, dt.date.fromisoformat(sd["original_expected_receipt_date"]))

    listings["initial_alloc"] = initial_alloc
    listings["replenish_rate"] = replenish_rate
    listings["supply_cap"] = supply_cap
    listings["freeze_week"] = np.where(sd_mask, freeze_start_week, 10 ** 6)
    listings["is_surplus_scn"] = surplus_mask
    listings["is_shortage_scn"] = shortage_mask
    listings["is_disrupted_scn"] = sd_mask

    listings = listings.reset_index(drop=True)
    listings["listing_id"] = listings.index

    # ---------------------------------------------------------------
    # 2. Explode listings by active week (vectorized)
    # ---------------------------------------------------------------
    starts = listings["assortment_start_week"].to_numpy()
    ends = listings["assortment_end_week"].to_numpy()
    lengths = (ends - starts + 1).astype("int64")
    total = int(lengths.sum())

    listing_idx = np.repeat(listings["listing_id"].to_numpy(), lengths)
    within = np.arange(total) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    week_id = starts[listing_idx] + within

    keep = (week_id >= cfg.min_week_id) & (week_id <= cfg.max_week_id)
    listing_idx, week_id = listing_idx[keep], week_id[keep]

    cols = ["location_id", "location_type", "region_code", "sku_id", "style_id", "category", "warmth_rating",
            "current_retail_price", "size", "style_pop", "location_weight", "launch_week", "store_tier",
            "initial_alloc", "replenish_rate", "supply_cap", "freeze_week", "is_shortage_scn", "is_disrupted_scn"]
    lw = listings.iloc[listing_idx][cols].reset_index(drop=True)
    lw["week_id"] = week_id

    # ---------------------------------------------------------------
    # 3. Merge calendar + weather
    # ---------------------------------------------------------------
    lw = lw.merge(dim_week[["week_id", "is_holiday_week", "days_to_christmas"]], on="week_id", how="left")
    lw = lw.merge(weather_actual, on=["region_code", "week_id"], how="left")
    lw["avg_temp_vs_normal_c"] = lw["avg_temp_vs_normal_c"].fillna(0.0)

    # ---------------------------------------------------------------
    # 4. Demand rate (lambda)
    # ---------------------------------------------------------------
    t = (lw["week_id"] - lw["launch_week"]).to_numpy()
    lifecycle = _lifecycle_mult(np.clip(t, 0, None).astype("float32"))

    holiday_factor = lw["category"].map(_CATEGORY_HOLIDAY_FACTOR).fillna(1.3).to_numpy()
    holiday_mult = np.where(lw["is_holiday_week"].to_numpy(), holiday_factor, 1.0)
    christmas_ramp = 1 + 0.4 * np.clip(1 - np.abs(lw["days_to_christmas"].to_numpy()) / 21.0, 0, 1)

    warmth_thresh = cfg.scenarios["weather_shock"]["warmth_rating_threshold"]
    is_outerwear_warm = (lw["category"] == "Outerwear").to_numpy() & (lw["warmth_rating"] >= warmth_thresh).to_numpy()
    weather_effect = 1 - 0.05 * np.clip(lw["avg_temp_vs_normal_c"].to_numpy(), 0, None) * lw["warmth_rating"].to_numpy() / 5
    weather_mult = np.where(is_outerwear_warm, np.clip(weather_effect, 0.4, 1.05), 1.0)

    vp = cfg.scenarios["viral_product"]
    trigger_week = date_to_week_id(cfg, dt.date.fromisoformat(vp["trigger_date"]))
    is_viral_style = (lw["style_id"] == vp["style_id"]).to_numpy()
    wk = lw["week_id"].to_numpy()
    spike_window = is_viral_style & (wk >= trigger_week) & (wk < trigger_week + vp["spike_weeks"])
    settle_window = is_viral_style & (wk >= trigger_week + vp["spike_weeks"])
    extra = np.ones(len(lw))
    is_flagship = (lw["store_tier"] == "Flagship").to_numpy()
    is_ecom_row = (lw["location_type"] == "Ecommerce").to_numpy()
    extra = np.where(is_flagship, extra * vp["flagship_extra_weight"], extra)
    extra = np.where(is_ecom_row, extra * vp["ecommerce_extra_weight"], extra)
    viral_mult = np.ones(len(lw))
    viral_mult[spike_window] = vp["spike_multiplier"] * extra[spike_window]
    viral_mult[settle_window] = vp["settle_multiplier"] * extra[settle_window]

    lam = (lw["style_pop"].to_numpy() * lw["location_weight"].to_numpy() * lifecycle * holiday_mult
           * christmas_ramp * weather_mult * viral_mult * _BASE_SCALE)
    lam = np.clip(lam, 0, None).astype("float64")

    demand_units = rng.poisson(lam).astype("int32")

    # ---------------------------------------------------------------
    # 5. Supply schedule S(t) and realized sales via cumulative min
    # ---------------------------------------------------------------
    freeze_week = lw["freeze_week"].to_numpy()
    effective_week = np.minimum(wk, freeze_week - 1)
    growth_weeks = np.clip(effective_week - lw["launch_week"].to_numpy(), 0, None)
    s_t = lw["initial_alloc"].to_numpy() + lw["replenish_rate"].to_numpy() * growth_weeks
    s_t = np.minimum(s_t, lw["supply_cap"].to_numpy())

    lw["demand_units"] = demand_units
    lw["s_t"] = s_t
    lw = lw.sort_values(["location_id", "sku_id", "week_id"]).reset_index(drop=True)

    grp = lw.groupby(["location_id", "sku_id"], sort=False)
    cum_demand = grp["demand_units"].cumsum()
    realized_cum = np.minimum(cum_demand.to_numpy(), lw["s_t"].to_numpy())
    lw["realized_cum_sales"] = realized_cum
    prev_cum = grp["realized_cum_sales"].shift(1).fillna(0.0)
    lw["realized_sales"] = (lw["realized_cum_sales"] - prev_cum).clip(lower=0).round().astype("int32")
    lw["on_hand_units"] = (lw["s_t"] - lw["realized_cum_sales"]).clip(lower=0)

    trailing_avg = grp["realized_sales"].transform(lambda s: s.rolling(4, min_periods=1).mean())
    lw["weeks_of_supply"] = lw["on_hand_units"] / trailing_avg.replace(0, np.nan)
    lw["weeks_of_supply"] = lw["weeks_of_supply"].fillna(52.0).clip(upper=52.0)

    # ---------------------------------------------------------------
    # 6. fact_inventory_position
    # ---------------------------------------------------------------
    inv = lw[["location_id", "location_type", "region_code", "sku_id", "week_id", "on_hand_units", "weeks_of_supply"]].copy()
    inv["on_hand_units"] = inv["on_hand_units"].round().astype("int32")
    write_table(cfg, "fact_inventory_position", inv)

    # ---------------------------------------------------------------
    # 7. fact_sales_line (rows with realized_sales > 0)
    # ---------------------------------------------------------------
    sales = lw.loc[lw["realized_sales"] > 0, [
        "location_id", "location_type", "region_code", "sku_id", "style_id", "category", "week_id",
        "realized_sales", "current_retail_price",
    ]].copy()
    sales = sales.rename(columns={"realized_sales": "units", "location_type": "channel"})

    sale_t = (sales["week_id"] - sales["style_id"].map(launch_week)).to_numpy()
    markdown = np.select([sale_t < 17, sale_t < 31], [0.0, 0.20], default=0.40)
    sales["markdown_pct"] = markdown
    sales["unit_price"] = (sales["current_retail_price"] * (1 - sales["markdown_pct"])).round(2)
    sales["gross_revenue"] = (sales["unit_price"] * sales["units"]).round(2)
    sales["order_id"] = (
        sales["location_id"] + "-" + sales["sku_id"] + "-" + sales["week_id"].astype(str)
    )

    sales["customer_id"] = _assign_customers(cfg, sales, dim_customer)

    sales = sales.drop(columns=["current_retail_price"])
    write_table(cfg, "fact_sales_line", sales)
    return sales, inv


def _assign_customers(cfg: Config, sales: pd.DataFrame, dim_customer: pd.DataFrame) -> np.ndarray:
    rng = cfg.rng("demand_engine")
    hero_campaign = cfg.hero_campaign
    milan_campaign_id = hero_campaign["campaign_id"]
    capsule_prefix = cfg.capsule["style_prefix"]
    repeat_lift = cfg.scenarios["milan_cohort"]["repeat_purchase_lift"]
    cap_lo, cap_hi = cfg.scenarios["milan_cohort"]["capsule_affinity_lift_range"]
    capsule_lift = (cap_lo + cap_hi) / 2

    loyalty_weight = {"None": 1.0, "Silver": 1.5, "Gold": 2.0, "Platinum": 3.0, "Private Client": 3.5}
    cust = dim_customer.copy()
    cust["base_weight"] = cust["loyalty_tier"].map(loyalty_weight).fillna(1.0)
    cust["is_milan_cohort"] = cust["acquisition_campaign_id"] == milan_campaign_id

    sales = sales.copy()
    sales["is_capsule_row"] = sales["style_id"].str.startswith(capsule_prefix)
    customer_ids = pd.Series(index=sales.index, dtype=object)

    for (region, is_cap), grp in sales.groupby(["region_code", "is_capsule_row"]):
        pool = cust.loc[cust["home_region"] == region]
        if pool.empty:
            pool = cust
        pool_ids = pool["customer_id"].to_numpy()

        w = pool["base_weight"].to_numpy(dtype="float64").copy()
        if region == "ITA":
            w = np.where(pool["is_milan_cohort"].to_numpy(), w * repeat_lift, w)
        if is_cap:
            w = np.where(pool["is_milan_cohort"].to_numpy(), w * capsule_lift, w)
        w = w / w.sum()

        chosen = rng.choice(pool_ids, size=len(grp), replace=True, p=w)
        customer_ids.loc[grp.index] = chosen

    return customer_ids.to_numpy()
