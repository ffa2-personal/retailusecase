"""Single entrypoint: generates every table under data/raw, then loads
bronze -> silver -> gold into the DuckDB warehouse.

Usage:
    python -m src.retail_synth.run_all [--config config/scenario_config.yaml]
"""
from __future__ import annotations

import argparse
import time

from .build_gold import build_gold
from .calendar import build_dim_week
from .config import load_config
from .dimensions.campaigns import build_dim_campaign
from .dimensions.customers import build_dim_customer
from .dimensions.fx import build_dim_fx_rate
from .dimensions.geo import build_dim_dc, build_dim_region, build_dim_store
from .dimensions.products import build_products
from .dimensions.return_reasons import build_dim_return_reason
from .dimensions.suppliers import build_dim_supplier
from .facts.allocation_po import build_allocation_po, select_scenario_styles
from .facts.assortment import build_store_sku_assortment
from .facts.campaign_exposure import build_campaign_exposure
from .facts.demand_engine import build_demand_and_inventory
from .facts.digital_engagement import build_digital_engagement
from .facts.returns import build_returns
from .facts.weather import build_weather
from .load_bronze import load_bronze
from .transform_silver import transform_silver


def _step(label: str, fn, *args, **kwargs):
    t0 = time.time()
    result = fn(*args, **kwargs)
    print(f"[{time.time() - t0:6.1f}s] {label}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    print(f"dev_mode={cfg.dev_mode}  n_weeks={cfg.n_weeks}  scale={cfg.scale}")

    t_start = time.time()

    dim_week = _step("dim_week", build_dim_week, cfg)
    dim_dc = _step("dim_dc", build_dim_dc, cfg)
    dim_region = _step("dim_region", build_dim_region, cfg)
    dim_store = _step("dim_store", build_dim_store, cfg, dim_region)
    dim_supplier = _step("dim_supplier", build_dim_supplier, cfg)
    dim_campaign = _step("dim_campaign", build_dim_campaign, cfg, dim_store)
    dim_customer = _step("dim_customer", build_dim_customer, cfg, dim_region, dim_store, dim_campaign)
    dim_style, dim_sku = _step("dim_style/dim_sku", build_products, cfg)
    _step("dim_fx_rate", build_dim_fx_rate, cfg, dim_week)
    _step("dim_return_reason", build_dim_return_reason, cfg)

    assortment = _step("store_sku_assortment", build_store_sku_assortment, cfg, dim_style, dim_sku, dim_store, dim_region)
    weather_actual, weather_snapshot = _step("weather", build_weather, cfg, dim_region, dim_week)

    disruption_styles, allocation_extra_styles = _step(
        "scenario style selection", select_scenario_styles, cfg, dim_style
    )
    _step("purchase orders / shipments", build_allocation_po, cfg, dim_style, dim_supplier, disruption_styles)

    sales, inv = _step(
        "demand + inventory (core engine)", build_demand_and_inventory, cfg, dim_week, dim_region, dim_store,
        dim_style, dim_sku, dim_customer, assortment, weather_actual, disruption_styles, allocation_extra_styles,
    )
    _step("returns", build_returns, cfg, sales, dim_sku, dim_style)
    _step("campaign exposure", build_campaign_exposure, cfg, dim_campaign, dim_customer)
    _step("digital engagement", build_digital_engagement, cfg, sales)

    table_names = _step("load bronze", load_bronze, cfg)
    _step("transform silver", transform_silver, cfg, table_names)
    _step("build gold", build_gold, cfg)

    print(f"\nTotal build time: {time.time() - t_start:.1f}s")
    print(f"DuckDB warehouse: {cfg.duckdb_path}")


if __name__ == "__main__":
    main()
