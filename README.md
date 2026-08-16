# AI-Native Retail Demo — Local DuckDB + Notebooks

A large, enterprise-scale **synthetic luxury-apparel dataset** and a set of Jupyter
notebooks proving the single integrated proof recommended in
[`ai_native_retail_strategy.md`](ai_native_retail_strategy.md): **AI-Native Seasonal
Planning and Inventory Orchestration**.

The dataset is generated locally with DuckDB + Python — no cloud dependency — but is
laid out in bronze/silver/gold medallion schemas inside one DuckDB file so it can be
lifted onto a Microsoft Fabric Lakehouse later with minimal change.

## What's in here

- **280 stores** + 6 distribution centers across 18 regions (Canada, US, Japan,
  China, APAC, UK/Ireland, France, DACH, Italy, Nordics, Benelux/Iberia, Middle East)
- **1,200 styles → ~25,000 SKUs**, **500,000 customers**, **3 fiscal years** of weekly
  history (156 weeks), with `AS_OF_DATE = 2025-12-01` as the "Monday morning" planner
  snapshot
- **~19M fact rows** (sales, inventory, returns, purchase orders/shipments, weather,
  campaign exposure, digital engagement) generated sparsely off a real assortment
  bridge — not a dense cross join
- **Six deliberately engineered scenarios** woven into otherwise ordinary history:
  a weather shock, a viral product, a fit/returns problem, a supplier delay, a poor
  inventory allocation, and a high-value customer cohort — see
  `config/scenario_config.yaml` for every parameter and named entity involved
- **Nine notebooks** that discover each scenario by querying the data (not by
  printing hardcoded narrative numbers), culminating in a live "5 Decisions Require
  Attention" executive planner workspace
- A **live POS feed** for the demo itself: a presenter-triggered viral spike you can
  watch unfold in real time, separate from the batch dataset above (see below)

## Setup

```
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Requires Python with `duckdb` wheel support (3.10–3.14 all work as of duckdb 1.5.x).

## Build the dataset

```
python -m src.retail_synth.run_all --config config\scenario_config.yaml
```

This writes parquet under `data/raw/` and loads bronze → silver → gold into
`data/warehouse/retail.duckdb`. Full-scale build takes ~4-5 minutes on a laptop.

Flip `dev_mode: true` in `config/scenario_config.yaml` for a much smaller, faster
dataset while iterating on the generator code itself — every scale/scenario
parameter lives in that one file.

Then verify:

```
python scripts/verify_build.py
```

## Run the notebooks

```
jupyter lab
```

Open `notebooks/` in order:

| # | Notebook | What it proves |
|---|---|---|
| 00 | `00_data_model_overview` | Table catalog, schema, medallion → Fabric mapping |
| 01 | `01_build_and_load_data` | Scale vs. config targets, verification checks |
| 02 | `02_weather_shock` | Demand sensing, geographic imbalance, markdown risk |
| 03 | `03_viral_product` | Anomaly detection, reforecast, stockout prioritization |
| 04 | `04_product_fit_problem` | Returns anomaly detection, fit diagnosis, financial impact |
| 05 | `05_supply_disruption` | Supply risk translated into revenue/customer exposure |
| 06 | `06_poor_allocation` | **Flagship notebook** — Toronto/Vancouver 3-option transfer decision + human approval |
| 07 | `07_high_value_customer_cohort` | Cohort discovery, next-best-client ranking |
| 08 | `08_executive_planner_workspace` | The "5 Decisions Require Attention" screen, live from `gold.decision_queue`, and the executive exposure/recovery rollup |
| 09 | `09_live_pos_feed` | *(live demo only)* Watches `scripts/pos_stream_simulator.py`'s real-time feed; the same stockout decision as notebook 03, happening on screen |

## Live POS feed (for the live demo)

A separate, presenter-controlled real-time layer that dramatizes the Aurora Bomber
viral-product scenario (notebook 03) live instead of from history. It never touches
`retail.duckdb` in read-write mode — it hands off data through parquet files in
`data/stream/` (gitignored, ephemeral), so the verified batch warehouse is never at risk.

Terminal 1 (start it once, leave it running):
```
python scripts/pos_stream_simulator.py
```

Then open `notebooks/09_live_pos_feed.ipynb` and run its live-view cell — it polls
the stream and renders a rolling ticker plus an Aurora Bomber on-hand-by-store panel
that lights up as a live stockout decision.

Terminal 2, whenever you're ready for the spike (~30–60s to visible stockouts):
```
python scripts/pos_stream_trigger.py
```

## Repo layout

```
config/scenario_config.yaml   single source of truth: scale, seeds, scenario params
src/retail_synth/             generators (dimensions/, facts/) + bronze/silver/gold pipeline
src/retail_synth/live_model.py  demand-model pieces shared by the batch generator and the live simulator
sql/silver/, sql/gold/        SQL transforms run by the pipeline
scripts/verify_build.py       post-build sanity + scenario-signal checks
scripts/pos_stream_simulator.py, pos_stream_trigger.py   the live POS feed for the demo
notebooks/                    the 10 demo notebooks (00-08 batch, 09 live)
data/                         generated output (gitignored, regenerable via run_all / the simulator)
```

## Next step: Microsoft Fabric

`sql/silver/*.sql` and `sql/gold/*.sql` are plain SQL against `bronze.*` / `silver.*`
tables. Moving to Fabric means pointing the same queries at a Lakehouse SQL endpoint
(bronze/silver as Lakehouse tables, gold as a Warehouse or semantic model) instead of
the local DuckDB file — the generation code in `src/retail_synth/` stays local either
way, only the destination of `load_bronze.py` changes.
