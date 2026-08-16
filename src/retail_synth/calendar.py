"""Builds dim_week: the weekly fiscal calendar backbone for the whole dataset."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .config import Config
from .io_utils import write_table


def fiscal_year_label(week_start: dt.date) -> str:
    # Fiscal year runs Feb -> Jan. A week starting in Jan belongs to the fiscal
    # year that began the previous February.
    return f"FY{week_start.year}" if week_start.month >= 2 else f"FY{week_start.year - 1}"


def fiscal_season(week_start: dt.date) -> str:
    return "SS" if 2 <= week_start.month <= 7 else "FW"


def build_dim_week(cfg: Config) -> pd.DataFrame:
    fiscal_start = cfg.fiscal_start_date
    as_of = cfg.as_of_date

    rows = []
    for week_id in range(cfg.min_week_id, cfg.max_week_id + 1):
        week_start = fiscal_start + dt.timedelta(weeks=week_id - 1)
        week_end = week_start + dt.timedelta(days=6)
        christmas = dt.date(week_start.year, 12, 25)
        if week_start.month == 1:
            christmas = dt.date(week_start.year - 1, 12, 25)
        days_to_christmas = (christmas - week_start).days
        rows.append(
            {
                "week_id": week_id,
                "week_start_date": week_start,
                "week_end_date": week_end,
                "fiscal_year": fiscal_year_label(week_start),
                "fiscal_season": fiscal_season(week_start),
                "fiscal_month": week_start.month,
                "is_holiday_week": week_start <= christmas <= week_end
                or (week_start <= dt.date(week_start.year, 11, 29) <= week_end),
                "days_to_christmas": days_to_christmas,
                "is_as_of_week": week_start <= as_of <= week_end,
            }
        )
    df = pd.DataFrame(rows)
    write_table(cfg, "dim_week", df)
    return df


def date_to_week_id(cfg: Config, date: dt.date) -> int:
    delta_days = (date - cfg.fiscal_start_date).days
    week_id = delta_days // 7 + 1
    return int(np.clip(week_id, 1, cfg.max_week_id_global))
