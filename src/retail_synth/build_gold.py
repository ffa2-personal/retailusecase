"""Builds the `gold` schema: decision-ready marts (via sql/gold/*.sql, run in
filename order) plus gold.decision_queue and gold.approved_actions, which are
easier to express as rule-based Python over the marts than as one giant SQL
statement.
"""
from __future__ import annotations

import duckdb
import pandas as pd

from .config import Config, REPO_ROOT

SQL_GOLD_DIR = REPO_ROOT / "sql" / "gold"

# Explicit thresholds -- the "5 decisions" screen is derived from these, not authored text.
THRESHOLDS = {
    "overstock_pct_min": 0.20,
    "stockout_days_max": 14,
    "return_rate_delta_min": 0.05,
    "revenue_at_risk_min": 1_000_000,
    "cohort_lift_min": 1.5,
}


def build_gold(cfg: Config) -> None:
    con = duckdb.connect(str(cfg.duckdb_path))
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")

    for sql_file in sorted(SQL_GOLD_DIR.glob("*.sql")):
        con.execute(sql_file.read_text(encoding="utf-8"))

    _build_decision_queue(con, cfg)

    con.execute("""
        CREATE OR REPLACE TABLE gold.approved_actions (
            action_id VARCHAR, decision_id VARCHAR, option_chosen VARCHAR,
            approved_by VARCHAR, approved_at TIMESTAMP, notes VARCHAR
        )
    """)
    con.close()


def _build_decision_queue(con: duckdb.DuckDBPyConnection, cfg: Config) -> None:
    sig = con.execute("SELECT * FROM gold.inventory_imbalance_signals WHERE location_type='Store'").fetch_df()
    ret = con.execute("SELECT * FROM gold.returns_scorecard").fetch_df()
    risk = con.execute("SELECT * FROM gold.supply_risk_exposure").fetch_df()
    coh = con.execute("SELECT * FROM gold.customer_cohort_scorecard").fetch_df()
    as_of_week = con.execute("SELECT week_id FROM silver.dim_week WHERE is_as_of_week").fetchone()[0]

    # Materiality floors scale with dataset size so the same relative bar
    # applies in dev mode and at full enterprise scale -- without this, thin
    # long-tail combos (a handful of units) generate noisy, meaningless
    # overstock/stockout/return-rate swings that would drown out the real signal.
    scale_ratio = cfg.scale["n_customers"] / cfg.raw["scale"]["full"]["n_customers"]
    min_remaining_demand = max(15 * scale_ratio, 3)
    min_recent_units_sold = max(15 * scale_ratio, 3)
    # A region-style with near-zero trailing velocity makes overstock%/stockout-days
    # blow up from noise (dividing by ~0), regardless of how big projected demand
    # looks -- so also require a minimum trailing sales rate before trusting either.
    min_weekly_rate = max(2.0 * scale_ratio, 0.5)

    rows = []

    # --- overstock / stockout, rolled up to region x style ---
    region_roll = (
        sig.groupby(["region_code", "style_id", "style_name", "category"], as_index=False)
        .agg(on_hand_units=("on_hand_units", "sum"),
             projected_remaining_demand=("projected_remaining_demand", "sum"),
             trailing_avg_weekly_sales=("trailing_avg_weekly_sales", "sum"),
             remaining_season_weeks=("remaining_season_weeks", "max"))
    )
    region_roll["overstock_pct"] = (
        (region_roll["on_hand_units"] - region_roll["projected_remaining_demand"])
        / region_roll["projected_remaining_demand"].replace(0, pd.NA)
    )
    region_roll["stockout_est_days"] = (
        region_roll["on_hand_units"] / region_roll["trailing_avg_weekly_sales"].replace(0, pd.NA) * 7
    )

    for r in region_roll.itertuples():
        if r.projected_remaining_demand < min_remaining_demand:
            continue  # too thin a business to be a meaningful decision
        if r.trailing_avg_weekly_sales < min_weekly_rate:
            continue  # velocity too close to zero for the ratio to be trustworthy
        # Overstock is only an actionable *reallocation* decision when there's real
        # season left to sell into -- inside the last few weeks it's a clearance/
        # markdown problem, not something a transfer decision meaningfully fixes.
        if pd.notna(r.overstock_pct) and r.overstock_pct >= THRESHOLDS["overstock_pct_min"] and r.remaining_season_weeks >= 4:
            rows.append(dict(
                decision_type="Overstock", region_code=r.region_code, style_id=r.style_id,
                style_name=r.style_name, metric_value=round(float(r.overstock_pct) * 100, 1),
                metric_label="% over projected remaining-season demand",
                headline=f"{r.style_name} -- {r.region_code} inventory is projected to exceed remaining-season demand by {r.overstock_pct * 100:.0f}%.",
            ))
        if pd.notna(r.stockout_est_days) and 0 <= r.stockout_est_days <= THRESHOLDS["stockout_days_max"]:
            rows.append(dict(
                decision_type="Stockout", region_code=r.region_code, style_id=r.style_id,
                style_name=r.style_name, metric_value=round(float(r.stockout_est_days), 1),
                metric_label="estimated days to stockout",
                headline=f"{r.style_name} -- {r.region_code} is expected to stock out within {r.stockout_est_days:.0f} days.",
            ))

    # --- returns: recent 4wk return rate vs each style-size's own early-life baseline ---
    ret_sorted = ret.sort_values(["style_id", "size", "week_id"])
    for (style_id, size), g in ret_sorted.groupby(["style_id", "size"]):
        baseline = g.loc[g["week_id"] < g["week_id"].min() + 8, "return_rate"].mean()
        recent_slice = g.loc[g["week_id"] >= as_of_week - 3]
        recent = recent_slice["return_rate"].mean()
        recent_units = recent_slice["units_sold"].sum()
        if recent_units < min_recent_units_sold:
            continue  # too few recent sales for the return rate to be meaningful
        if pd.notna(baseline) and pd.notna(recent) and (recent - baseline) >= THRESHOLDS["return_rate_delta_min"]:
            style_name = g["style_name"].iloc[0]
            rows.append(dict(
                decision_type="Returns", region_code=None, style_id=style_id, style_name=f"{style_name} ({size})",
                metric_value=round(float((recent - baseline) * 100), 1), metric_label="pt increase in return rate",
                headline=f"{style_name} ({size}) -- return rate increased from {baseline * 100:.0f}% to {recent * 100:.0f}%.",
            ))

    # --- supply risk: revenue at risk over the exposure window ---
    if not risk.empty:
        risk = risk.copy()
        risk["units_at_risk"] = risk["trailing_avg_weekly_sales"] * 3  # 3-week exposure window
        risk["revenue_at_risk"] = risk["units_at_risk"] * risk["current_retail_price"]
        agg = risk.groupby("region_code", as_index=False)["revenue_at_risk"].sum()
        total_risk = agg["revenue_at_risk"].sum()
        if total_risk >= THRESHOLDS["revenue_at_risk_min"]:
            dc = risk["dc_id"].iloc[0]
            rows.append(dict(
                decision_type="Supply Risk", region_code=None, style_id=None,
                style_name=f"{dc} delayed supplier shipment", metric_value=round(float(total_risk), 0),
                metric_label="revenue at risk (USD, 3wk window)",
                headline=f"{dc} delay may expose ${total_risk:,.0f} of revenue over the next three weeks.",
            ))

    # --- cohort opportunity ---
    ita_control = coh.loc[(coh["home_region"] == "ITA") & (~coh["is_milan_cohort"]) & (coh["loyalty_tier"].isin(["Gold", "Platinum", "Private Client"]))]
    cohort = coh.loc[coh["is_milan_cohort"]]
    if len(ita_control) > 0 and len(cohort) > 0:
        # Average order COUNT, not the binary is_repeat_purchaser flag: at real
        # sales volume nearly everyone clears a >=2-orders bar, so that flag
        # saturates near 1.0 for both groups and hides a real, large lift that
        # only shows up in how MANY times they repeat-purchase.
        control_rate = ita_control["n_orders"].mean()
        cohort_rate = cohort["n_orders"].mean()
        lift = cohort_rate / control_rate if control_rate > 0 else float("nan")
        if pd.notna(lift) and lift >= THRESHOLDS["cohort_lift_min"]:
            rows.append(dict(
                decision_type="Cohort Opportunity", region_code="ITA", style_id=None,
                style_name="Milan Flagship Private Trunk Show cohort", metric_value=round(float(lift), 2),
                metric_label="x avg orders per customer vs matched control",
                headline=f"Customers acquired through the Milan campaign show {lift:.1f}x higher repeat-purchase probability.",
            ))

    dq = pd.DataFrame(rows)
    if not dq.empty:
        dq.insert(0, "decision_id", [f"DEC-{i:04d}" for i in range(1, len(dq) + 1)])
    con.execute("CREATE OR REPLACE TABLE gold.decision_queue AS SELECT * FROM dq")
