"""dim_return_reason: small static lookup."""
from __future__ import annotations

import pandas as pd

from ..config import Config
from ..io_utils import write_table

_REASONS = [
    ("Fit-Small", "Runs small / sized up", "Fit"),
    ("Fit-Large", "Runs large / sized down", "Fit"),
    ("Fit-Other", "General fit issue", "Fit"),
    ("Quality", "Quality or defect issue", "Quality"),
    ("Color-Mismatch", "Color looked different than expected", "Quality"),
    ("Changed-Mind", "Customer preference / changed mind", "Preference"),
    ("Late-Delivery", "Arrived too late", "Logistics"),
    ("Damaged-Shipping", "Damaged in transit", "Logistics"),
]


def build_dim_return_reason(cfg: Config) -> pd.DataFrame:
    df = pd.DataFrame(_REASONS, columns=["reason_code", "reason_desc", "reason_category"])
    write_table(cfg, "dim_return_reason", df)
    return df
