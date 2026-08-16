"""Builds the `silver` schema: typed/conformed tables, one per bronze table.

Most tables are already clean coming out of the generator, so this is a
straight copy unless a hand-written override exists at sql/silver/<name>.sql
(used for the handful of tables that need light cleaning/derived columns).
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from .config import Config, REPO_ROOT

SQL_SILVER_DIR = REPO_ROOT / "sql" / "silver"


def transform_silver(cfg: Config, table_names: list[str]) -> None:
    con = duckdb.connect(str(cfg.duckdb_path))
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")

    for name in table_names:
        override = SQL_SILVER_DIR / f"{name}.sql"
        if override.exists():
            sql = override.read_text(encoding="utf-8")
        else:
            sql = f"CREATE OR REPLACE TABLE silver.{name} AS SELECT * FROM bronze.{name}"
        con.execute(sql)

    con.close()
