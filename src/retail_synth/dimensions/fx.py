"""dim_fx_rate: currency x week, gently random-walked around a plausible base rate."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from ..io_utils import write_table

_BASE_RATE_TO_USD = {"USD": 1.0, "CAD": 0.74, "EUR": 1.08, "GBP": 1.26, "JPY": 0.0067, "CNY": 0.138}


def build_dim_fx_rate(cfg: Config, dim_week: pd.DataFrame) -> pd.DataFrame:
    rng = cfg.rng("fx")
    weeks = dim_week["week_id"].to_numpy()
    rows = []
    for currency, base in _BASE_RATE_TO_USD.items():
        if currency == "USD":
            rate = np.full(len(weeks), 1.0)
        else:
            shocks = rng.normal(0, 0.004, size=len(weeks))
            rate = base * np.cumprod(1 + shocks)
            rate = np.clip(rate, base * 0.85, base * 1.15)
        rows.append(pd.DataFrame({"currency_code": currency, "week_id": weeks, "rate_to_usd": rate}))
    df = pd.concat(rows, ignore_index=True)
    write_table(cfg, "dim_fx_rate", df)
    return df
