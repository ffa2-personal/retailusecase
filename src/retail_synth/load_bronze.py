"""Loads every data/raw/<table>/*.parquet folder into a `bronze` schema table
in the DuckDB warehouse. Table set is discovered from disk, not hardcoded."""
from __future__ import annotations

import duckdb

from .config import Config


def load_bronze(cfg: Config) -> list[str]:
    con = duckdb.connect(str(cfg.duckdb_path))
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")

    table_names = sorted(p.name for p in cfg.raw_dir.iterdir() if p.is_dir())
    for name in table_names:
        glob = str(cfg.raw_dir / name / "*.parquet")
        con.execute(f"CREATE OR REPLACE TABLE bronze.{name} AS SELECT * FROM read_parquet('{glob}')")

    con.close()
    return table_names
