"""Standalone live POS event simulator for the customer demo.

Run this once in its own terminal at the top of a live demo:

    python scripts/pos_stream_simulator.py

It ticks baseline orders across a curated set of stores/SKUs, and watches
data/stream/control.json for a trigger (see scripts/pos_stream_trigger.py)
that kicks off a fast-forward Aurora Bomber viral spike concentrated at the
flagship stores -- reproducing the same scenario/config numbers as the batch
`viral_product` scenario, just compressed into seconds instead of weeks.

Writes only to data/stream/ (parquet files). NEVER opens
data/warehouse/retail.duckdb in read-write mode -- DuckDB allows one
read-write connection XOR many read-only ones per file, and the live-view
notebook holds a read-only connection to that file at the same time this
process runs. See the plan doc for why.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import deque
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from retail_synth.config import REPO_ROOT, load_config  # noqa: E402
from retail_synth.live_model import TIER_WEIGHT, viral_ramp_mult  # noqa: E402

STREAM_DIR = REPO_ROOT / "data" / "stream"
EVENTS_DIR = STREAM_DIR / "events"
LIVE_STATE_PATH = STREAM_DIR / "live_state.parquet"
CONTROL_PATH = STREAM_DIR / "control.json"

TICK_SECONDS = 1.5
BASELINE_RATE = 0.35           # baseline per-tick Poisson lambda before tier/style weighting
PRE_TRIGGER_MULT = 0.35        # Aurora Bomber reads as an unremarkable normal seller before the trigger
ROLLING_WINDOW_TICKS = 6        # trailing window used for velocity / ticks-to-stockout
N_AMBIENT_STYLES = 5
N_EXTRA_STORES = 10
STARTING_ON_HAND_RANGE = (15, 45)


def _load_universe(cfg) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """One read-only pass over the batch warehouse to pick a small, real
    'live universe' of stores/SKUs -- then the connection is closed. The
    simulator never touches retail.duckdb again after this."""
    con = duckdb.connect(str(cfg.duckdb_path), read_only=True)

    viral = cfg.scenarios["viral_product"]
    aurora_style_id = viral["style_id"]

    hero_store_ids = [h["store_id"] for h in cfg.hero_stores]
    extra_stores = con.execute(f"""
        SELECT store_id FROM silver.dim_store
        WHERE store_tier IN ('Flagship','A') AND store_id NOT IN ({','.join(f"'{s}'" for s in hero_store_ids)})
        USING SAMPLE {N_EXTRA_STORES}
    """).df()["store_id"].tolist()
    store_ids = hero_store_ids + extra_stores

    stores = con.execute(f"""
        SELECT store_id AS location_id, store_name, store_tier, city, region_code
        FROM silver.dim_store WHERE store_id IN ({','.join(f"'{s}'" for s in store_ids)})
    """).df()

    aurora_skus = con.execute(f"""
        SELECT sku.sku_id, sku.color_name, sku.size, sku.current_retail_price, sty.style_name, sty.style_id
        FROM silver.dim_sku sku JOIN silver.dim_style sty ON sty.style_id = sku.style_id
        WHERE sku.style_id = '{aurora_style_id}'
    """).df()

    ambient_skus = con.execute(f"""
        SELECT sku.sku_id, sku.color_name, sku.size, sku.current_retail_price, sty.style_name, sty.style_id
        FROM silver.dim_sku sku JOIN silver.dim_style sty ON sty.style_id = sku.style_id
        WHERE sty.category != 'Outerwear'
        USING SAMPLE {N_AMBIENT_STYLES}
    """).df()
    con.close()

    skus = pd.concat([aurora_skus, ambient_skus], ignore_index=True)
    skus["is_aurora"] = skus["style_id"] == aurora_style_id
    return stores, skus, aurora_style_id


def _build_listing_grid(stores: pd.DataFrame, skus: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Every store carries the ambient styles; only flagship/A stores carry
    Aurora Bomber pre-spike-trigger, matching how a real viral item would
    already be distributed to top-tier doors before it takes off."""
    frames = []
    for _, sku in skus.iterrows():
        carrying = stores if not sku["is_aurora"] else stores.loc[stores["store_tier"].isin(["Flagship", "A"])]
        g = carrying.copy()
        for col in ["sku_id", "color_name", "size", "current_retail_price", "style_name", "is_aurora"]:
            g[col] = sku[col]
        frames.append(g)
    grid = pd.concat(frames, ignore_index=True)
    grid["on_hand"] = rng.integers(*STARTING_ON_HAND_RANGE, size=len(grid))
    grid["listing_id"] = np.arange(len(grid))
    return grid


def _read_control() -> dict:
    if not CONTROL_PATH.exists():
        return {"triggered": False}
    try:
        return json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"triggered": False}  # mid-write on the trigger side; treat as not-yet-triggered this tick


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    # Unique temp name per call -- a fixed name reused every tick can collide
    # with a reader mid-open on the previous tick's temp file.
    tmp = path.with_name(f"{path.stem}-{os.getpid()}-{time.time_ns()}.tmp")
    df.to_parquet(tmp, index=False)
    # os.replace is atomic, but on Windows it can raise a transient
    # PermissionError if a reader (e.g. the live-view notebook polling this
    # same path) has it open at that exact instant -- retry briefly instead
    # of crashing the whole live demo over a few-millisecond race.
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.05)


def main() -> None:
    cfg = load_config()
    viral = cfg.scenarios["viral_product"]
    spike_ticks = 8       # fast-forward: ~8 ticks (~12s) of 5x, then settle -- see plan's "30-60s payoff"
    flagship_extra = viral["flagship_extra_weight"]
    ecom_extra = viral["ecommerce_extra_weight"]

    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    if not CONTROL_PATH.exists():
        CONTROL_PATH.write_text(json.dumps({"triggered": False}), encoding="utf-8")

    rng = np.random.default_rng()
    print("Loading live universe from retail.duckdb (read-only)...")
    stores, skus, aurora_style_id = _load_universe(cfg)
    grid = _build_listing_grid(stores, skus, rng)
    print(f"Streaming {len(grid)} store-SKU listings across {stores['location_id'].nunique()} stores "
          f"({len(skus)} SKUs, including {skus['is_aurora'].sum()} Aurora Bomber SKUs).")
    print(f"Waiting for trigger -- run: python scripts/pos_stream_trigger.py\n")

    tier_weight = grid["store_tier"].map(TIER_WEIGHT).fillna(0.6).to_numpy()
    is_flagship = (grid["store_tier"] == "Flagship").to_numpy()
    is_aurora = grid["is_aurora"].to_numpy()

    trigger_tick: int | None = None
    velocity_history: dict[int, deque] = {lid: deque(maxlen=ROLLING_WINDOW_TICKS) for lid in grid["listing_id"]}
    tick = 0

    try:
        while True:
            tick += 1
            ctrl = _read_control()
            if ctrl.get("triggered") and trigger_tick is None:
                trigger_tick = tick
                print(f"[tick {tick}] *** TRIGGERED *** Aurora Bomber spike starting now.")

            ticks_since_trigger = np.full(len(grid), -1 if trigger_tick is None else tick - trigger_tick)
            viral_mult = viral_ramp_mult(ticks_since_trigger, viral["spike_multiplier"], viral["settle_multiplier"],
                                          spike_ticks, pre_trigger_mult=PRE_TRIGGER_MULT)
            viral_mult = np.where(is_aurora & is_flagship, viral_mult * flagship_extra, viral_mult)
            viral_mult = np.where(is_aurora, viral_mult, 1.0)

            lam = BASELINE_RATE * tier_weight * viral_mult
            demand = rng.poisson(lam)
            realized = np.minimum(demand, grid["on_hand"].to_numpy())
            grid["on_hand"] = grid["on_hand"].to_numpy() - realized

            for lid, units in zip(grid["listing_id"], realized):
                velocity_history[lid].append(units)

            sold_mask = realized > 0
            if sold_mask.any():
                events = grid.loc[sold_mask, ["listing_id", "location_id", "store_name", "sku_id", "style_name",
                                               "color_name", "size", "current_retail_price"]].copy()
                events["units"] = realized[sold_mask]
                events["gross_revenue"] = events["units"] * events["current_retail_price"]
                events["tick"] = tick
                events["ts"] = pd.Timestamp.now()
                _atomic_write_parquet(events, EVENTS_DIR / f"part-{tick:06d}.parquet")

            velocity = np.array([np.mean(velocity_history[lid]) if velocity_history[lid] else 0.0
                                  for lid in grid["listing_id"]])
            ticks_to_stockout = np.where(velocity > 0, grid["on_hand"].to_numpy() / np.maximum(velocity, 1e-9), np.inf)

            state = grid[["location_id", "store_name", "store_tier", "sku_id", "style_name", "color_name", "size",
                          "on_hand", "is_aurora"]].copy()
            state["trailing_velocity"] = velocity
            state["ticks_to_stockout"] = np.clip(ticks_to_stockout, 0, 999)
            state["tick"] = tick
            state["triggered"] = trigger_tick is not None
            state["ticks_since_trigger"] = ticks_since_trigger
            _atomic_write_parquet(state, LIVE_STATE_PATH)

            n_orders = int(sold_mask.sum())
            aurora_on_hand = int(grid.loc[is_aurora, "on_hand"].sum())
            status = "SPIKING" if trigger_tick is not None and (tick - trigger_tick) < spike_ticks else \
                     ("SETTLED" if trigger_tick is not None else "baseline")
            print(f"[tick {tick:4d}] {n_orders:3d} orders this tick | Aurora Bomber on-hand={aurora_on_hand:4d} "
                  f"| {status}")

            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
