"""fact_returns_line.

Scenario 3 (Product Fit Problem) is injected here: Chelsea Parka sizes M/L
get an elevated return rate from the injection date onward, with templated
fit-complaint review text attached so keyword analysis can surface it.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..calendar import date_to_week_id
from ..config import Config
from ..io_utils import write_table

_BASELINE_RATE = {"Outerwear": 0.11, "Knitwear": 0.08, "Tailoring": 0.09, "Accessories": 0.05, "Footwear": 0.12}
_REASON_WEIGHTS = {  # generic (non-injected) reason mix
    "Fit-Small": 0.22, "Fit-Large": 0.18, "Fit-Other": 0.12, "Quality": 0.18,
    "Color-Mismatch": 0.08, "Changed-Mind": 0.14, "Late-Delivery": 0.05, "Damaged-Shipping": 0.03,
}
_FIT_SMALL_REVIEWS = [
    "Runs small in the shoulders, had to size up.",
    "Fit was tighter than expected for a Parka, ordering a size up next time.",
    "Loved the design but it ran small -- returning for a bigger size.",
    "The sizing chart was off, this fit at least one size smaller than usual.",
    "Beautiful coat but too snug through the chest, needed a larger size.",
]


def build_returns(cfg: Config, sales: pd.DataFrame, dim_sku: pd.DataFrame, dim_style: pd.DataFrame) -> pd.DataFrame:
    rng = cfg.rng("returns")
    fp = cfg.scenarios["fit_problem"]
    fp_style = fp["style_id"]
    fp_sizes = set(fp["sizes"])
    fp_start_week = date_to_week_id(cfg, dt.date.fromisoformat(fp["start_date"]))
    lo, hi = fp["injected_return_rate_range"]

    df = sales.merge(dim_sku[["sku_id", "size"]], on="sku_id", how="left")
    df = df.merge(dim_style[["style_id", "category"]], on="style_id", how="left", suffixes=("", "_style"))

    base_rate = df["category"].map(_BASELINE_RATE).fillna(0.10).to_numpy()
    is_fp = (df["style_id"] == fp_style).to_numpy() & df["size"].isin(fp_sizes).to_numpy() & (df["week_id"] >= fp_start_week).to_numpy()
    injected_rate = rng.uniform(lo, hi, size=len(df))
    return_rate = np.where(is_fp, injected_rate, base_rate)

    units_returned = rng.binomial(df["units"].to_numpy(), np.clip(return_rate, 0, 1))
    df["units_returned"] = units_returned
    df["is_fit_problem_scn"] = is_fp
    ret = df.loc[df["units_returned"] > 0].copy()

    reason_codes = list(_REASON_WEIGHTS.keys())
    reason_p = np.array(list(_REASON_WEIGHTS.values()))
    reason_p = reason_p / reason_p.sum()
    chosen_reason = np.where(
        ret["is_fit_problem_scn"].to_numpy(),
        "Fit-Small",
        rng.choice(reason_codes, size=len(ret), p=reason_p),
    )
    ret["reason_code"] = chosen_reason

    review_text = np.full(len(ret), None, dtype=object)
    fit_small_mask = (ret["reason_code"] == "Fit-Small").to_numpy() & ret["is_fit_problem_scn"].to_numpy()
    n_fit_small = int(fit_small_mask.sum())
    if n_fit_small:
        idx = np.where(fit_small_mask)[0]
        picks = rng.choice(_FIT_SMALL_REVIEWS, size=n_fit_small)
        for i, p in zip(idx, picks):
            review_text[i] = p
    ret["review_text"] = review_text

    days_after = rng.integers(3, 22, size=len(ret))
    week_start = pd.to_datetime(ret["week_id"].map(lambda w: cfg.fiscal_start_date + dt.timedelta(weeks=int(w) - 1)))
    ret["return_date"] = week_start + pd.to_timedelta(days_after, unit="D")

    ret["return_line_id"] = [f"RET-{i:08d}" for i in range(1, len(ret) + 1)]
    out = ret[["return_line_id", "location_id", "channel", "sku_id", "style_id", "week_id", "return_date",
               "units_returned", "reason_code", "review_text"]].copy()
    write_table(cfg, "fact_returns_line", out)
    return out
