"""Post-build sanity checks for the DuckDB warehouse.

Hard failures (exit 1): missing tables, referential-integrity violations,
negative inventory, or a scenario signal not showing up at all.
Soft warnings (printed, don't fail the run): row counts outside the rough
bands in scenario_config.yaml -- those bands are estimates, not contracts,
and will vary with dev vs. full scale.

Usage:
    python scripts/verify_build.py [--config config/scenario_config.yaml]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from retail_synth.config import load_config  # noqa: E402

FAILURES: list[str] = []
WARNINGS: list[str] = []


def fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"[FAIL] {msg}")


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print(f"[WARN] {msg}")


def ok(msg: str) -> None:
    print(f"[ OK ] {msg}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    con = duckdb.connect(str(cfg.duckdb_path), read_only=True)

    expected_tables = [
        "dim_week", "dim_dc", "dim_region", "dim_store", "dim_supplier", "dim_campaign",
        "dim_customer", "dim_style", "dim_sku", "dim_fx_rate", "dim_return_reason",
        "store_sku_assortment", "fact_weather_actual", "fact_weather_forecast_snapshot",
        "fact_purchase_order_line", "fact_shipment_event", "fact_sales_line",
        "fact_inventory_position", "fact_returns_line", "fact_campaign_exposure",
        "fact_digital_engagement",
    ]
    for schema in ("bronze", "silver", "gold"):
        present = {r[0] for r in con.execute(
            f"SELECT table_name FROM information_schema.tables WHERE table_schema='{schema}'"
        ).fetchall()}
        if schema in ("bronze", "silver"):
            missing = set(expected_tables) - present
            if missing:
                fail(f"{schema} missing tables: {sorted(missing)}")
            else:
                ok(f"{schema}: all {len(expected_tables)} expected tables present")
        else:
            gold_expected = {"weekly_demand_style_region", "weekly_demand_store_sku",
                              "inventory_imbalance_signals", "returns_scorecard",
                              "supply_risk_exposure", "customer_cohort_scorecard",
                              "decision_queue", "approved_actions"}
            missing = gold_expected - present
            if missing:
                fail(f"gold missing tables: {sorted(missing)}")
            else:
                ok(f"gold: all {len(gold_expected)} expected marts present")

    # --- row-count sanity (soft) ---
    bands = cfg.verify["row_count_bands_full"] if not cfg.dev_mode else {}
    for table, (lo, hi) in bands.items():
        n = con.execute(f"SELECT COUNT(*) FROM silver.{table}").fetchone()[0]
        if lo <= n <= hi:
            ok(f"silver.{table}: {n:,} rows (within [{lo:,}, {hi:,}])")
        else:
            warn(f"silver.{table}: {n:,} rows (expected roughly [{lo:,}, {hi:,}])")

    # --- referential integrity ---
    fk_checks = [
        ("fact_sales_line", "sku_id", "dim_sku", "sku_id"),
        ("fact_sales_line", "location_id", "dim_store", "store_id", "channel = 'Store'"),
        ("fact_inventory_position", "sku_id", "dim_sku", "sku_id"),
        ("fact_returns_line", "sku_id", "dim_sku", "sku_id"),
    ]
    for check in fk_checks:
        child_tbl, child_col, parent_tbl, parent_col = check[:4]
        where = f"AND {check[4]}" if len(check) > 4 else ""
        n_orphan = con.execute(f"""
            SELECT COUNT(*) FROM silver.{child_tbl} c
            WHERE c.{child_col} IS NOT NULL {where}
              AND NOT EXISTS (SELECT 1 FROM silver.{parent_tbl} p WHERE p.{parent_col} = c.{child_col})
        """).fetchone()[0]
        if n_orphan == 0:
            ok(f"{child_tbl}.{child_col} -> {parent_tbl}: no orphans")
        else:
            fail(f"{child_tbl}.{child_col} -> {parent_tbl}: {n_orphan} orphan rows")

    # --- no negative inventory ---
    n_neg = con.execute("SELECT COUNT(*) FROM silver.fact_inventory_position WHERE on_hand_units < 0").fetchone()[0]
    if n_neg == 0:
        ok("fact_inventory_position: no negative on_hand_units")
    else:
        fail(f"fact_inventory_position: {n_neg} rows with negative on_hand_units")

    # --- scenario signal assertions ---
    th = cfg.verify["scenario_thresholds"]
    sc = cfg.scenarios

    max_dev = con.execute(f"""
        SELECT MAX(avg_temp_vs_normal_c) FROM silver.fact_weather_actual
        WHERE region_code IN ({','.join("'" + r + "'" for r in sc['weather_shock']['warm_regions'])})
    """).fetchone()[0]
    if max_dev is not None and max_dev >= th["weather_temp_dev_min_c"]:
        ok(f"weather shock: warm-region max temp deviation = {max_dev:.1f}C (>= {th['weather_temp_dev_min_c']})")
    else:
        fail(f"weather shock: warm-region max temp deviation = {max_dev} (< {th['weather_temp_dev_min_c']})")

    fp = sc["fit_problem"]
    max_rate = con.execute(f"""
        SELECT MAX(return_rate) FROM gold.returns_scorecard
        WHERE style_id = '{fp['style_id']}' AND size IN ({','.join("'" + s + "'" for s in fp['sizes'])})
    """).fetchone()[0]
    if max_rate is not None and max_rate >= th["fit_return_rate_min"]:
        ok(f"fit problem: peak injected return rate = {max_rate:.2f} (>= {th['fit_return_rate_min']})")
    else:
        fail(f"fit problem: peak injected return rate = {max_rate} (< {th['fit_return_rate_min']})")

    delay = con.execute("""
        SELECT DATE_DIFF('day', original_expected_receipt_date, revised_expected_receipt_date)
        FROM silver.fact_purchase_order_line WHERE is_delayed LIMIT 1
    """).fetchone()
    if delay and delay[0] == th["supply_delay_days"]:
        ok(f"supply disruption: delay = {delay[0]} days (matches config)")
    else:
        fail(f"supply disruption: delay = {delay} (expected {th['supply_delay_days']})")

    # Compares average ORDER COUNT (not the binary is_repeat_purchaser flag,
    # which saturates near 1.0 for nearly everyone at real sales volume)
    # against a matched control of similarly high-tier non-cohort customers.
    lift_row = con.execute("""
        WITH agg AS (
            SELECT is_milan_cohort, AVG(n_orders) rate
            FROM gold.customer_cohort_scorecard
            WHERE home_region = 'ITA' AND loyalty_tier IN ('Gold', 'Platinum', 'Private Client')
            GROUP BY 1
        )
        SELECT (SELECT rate FROM agg WHERE is_milan_cohort) / NULLIF((SELECT rate FROM agg WHERE NOT is_milan_cohort), 0)
    """).fetchone()
    lift = lift_row[0] if lift_row else None
    if lift is not None and lift >= th["milan_cohort_lift_min"]:
        ok(f"milan cohort: repeat-purchase lift = {lift:.2f}x (>= {th['milan_cohort_lift_min']})")
    else:
        fail(f"milan cohort: repeat-purchase lift = {lift} (< {th['milan_cohort_lift_min']})")

    n_decisions = con.execute("SELECT COUNT(*) FROM gold.decision_queue").fetchone()[0]
    if n_decisions > 0:
        ok(f"decision_queue: {n_decisions} rule-derived decisions")
    else:
        fail("decision_queue: empty -- no decisions were derived")

    con.close()

    print(f"\n{len(FAILURES)} failure(s), {len(WARNINGS)} warning(s).")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
