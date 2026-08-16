"""fact_weather_actual, fact_weather_forecast_snapshot.

Scenario 1 (Weather Shock) is injected here: warm_regions get a genuine
temperature-deviation surprise starting at start_date -- forecasts made
*before* that date still show normal conditions (the surprise), while
forecasts made during/after show the actual shock (the forecast "catches up").
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..calendar import date_to_week_id
from ..config import Config
from ..io_utils import write_table

_FORECAST_HORIZON_WEEKS = 8


def build_weather(cfg: Config, dim_region: pd.DataFrame, dim_week: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = cfg.rng("weather")
    sc = cfg.scenarios["weather_shock"]
    warm_regions = set(sc["warm_regions"])
    shock_start_week = date_to_week_id(cfg, dt.date.fromisoformat(sc["start_date"]))
    dev_lo, dev_hi = sc["temp_dev_c_range"]

    regions = dim_region["region_code"].tolist()
    weeks = dim_week["week_id"].to_numpy()
    n_r, n_w = len(regions), len(weeks)

    region_grid = np.repeat(regions, n_w)
    week_grid = np.tile(weeks, n_r)

    base_noise = rng.normal(0, 1.4, size=n_r * n_w)
    temp_dev = base_noise.copy()

    is_warm = np.isin(region_grid, list(warm_regions))
    is_shock_week = week_grid >= shock_start_week
    shock_mask = is_warm & is_shock_week
    shock_boost = rng.uniform(dev_lo, dev_hi, size=shock_mask.sum())
    temp_dev[shock_mask] = base_noise[shock_mask] + shock_boost

    actual = pd.DataFrame({
        "region_code": region_grid,
        "week_id": week_grid,
        "avg_temp_vs_normal_c": temp_dev.astype("float32"),
    })
    write_table(cfg, "fact_weather_actual", actual)

    # --- forecast snapshots: region x as_of_week x target_week (8wk horizon) ---
    as_of_weeks = weeks
    target_offsets = np.arange(1, _FORECAST_HORIZON_WEEKS + 1)
    r_idx = np.repeat(np.arange(n_r), n_w * len(target_offsets))
    w_idx = np.tile(np.repeat(as_of_weeks, len(target_offsets)), n_r)
    off_idx = np.tile(target_offsets, n_r * n_w)
    target_week = w_idx + off_idx

    valid = target_week <= weeks.max()
    r_idx, w_idx, target_week = r_idx[valid], w_idx[valid], target_week[valid]
    fregion = np.array(regions)[r_idx]

    actual_lookup = actual.set_index(["region_code", "week_id"])["avg_temp_vs_normal_c"]
    actual_at_target = actual_lookup.loc[list(zip(fregion, target_week))].to_numpy()

    noise = rng.normal(0, 0.6, size=len(fregion))
    is_warm_f = np.isin(fregion, list(warm_regions))
    forecast_made_before_shock = w_idx < shock_start_week
    target_in_shock = target_week >= shock_start_week
    is_surprise = is_warm_f & target_in_shock & forecast_made_before_shock

    forecast = np.where(is_surprise, noise, actual_at_target + noise)

    snapshot = pd.DataFrame({
        "region_code": fregion,
        "as_of_week_id": w_idx,
        "target_week_id": target_week,
        "forecast_temp_vs_normal_c": forecast.astype("float32"),
    })
    write_table(cfg, "fact_weather_forecast_snapshot", snapshot)
    return actual, snapshot
