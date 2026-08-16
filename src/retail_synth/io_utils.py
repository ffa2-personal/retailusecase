"""Shared helpers for writing generated tables to data/raw as parquet."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import Config


def write_table(cfg: Config, name: str, df: pd.DataFrame) -> Path:
    out_dir = cfg.raw_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "part-0000.parquet"
    df.to_parquet(out_path, index=False)
    return out_path


def write_table_part(cfg: Config, name: str, df: pd.DataFrame, part: int) -> Path:
    out_dir = cfg.raw_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"part-{part:04d}.parquet"
    df.to_parquet(out_path, index=False)
    return out_path


def read_table(cfg: Config, name: str) -> pd.DataFrame:
    return pd.read_parquet(cfg.raw_dir / name)
