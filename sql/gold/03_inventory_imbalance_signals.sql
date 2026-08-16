CREATE OR REPLACE TABLE gold.inventory_imbalance_signals AS
WITH as_of AS (
    SELECT week_id AS as_of_week FROM silver.dim_week WHERE is_as_of_week
),
trail AS (
    -- 8-week trailing average: long enough to smooth Poisson noise on
    -- slower-moving SKU-location pairs, short enough to reflect current velocity.
    SELECT location_id, sku_id,
           AVG(units_sold) FILTER (
               WHERE week_id BETWEEN (SELECT as_of_week FROM as_of) - 7 AND (SELECT as_of_week FROM as_of)
           ) AS trailing_avg_weekly_sales
    FROM gold.weekly_demand_store_sku
    GROUP BY 1, 2
),
snap AS (
    SELECT * FROM gold.weekly_demand_store_sku WHERE week_id = (SELECT as_of_week FROM as_of)
)
SELECT
    snap.location_id,
    snap.location_type,
    snap.region_code,
    snap.sku_id,
    sku.style_id,
    sty.style_name,
    sty.category,
    sku.color_name,
    sku.size,
    sku.current_retail_price,
    snap.on_hand_units,
    snap.weeks_of_supply,
    COALESCE(trail.trailing_avg_weekly_sales, 0) AS trailing_avg_weekly_sales,
    -- remaining_season_weeks uses the style's TRUE planned end (uncapped by
    -- however far this run's calendar happens to extend), so it isn't
    -- artificially shrunk by a short dev-mode generation window.
    GREATEST(sty.exit_week_uncapped - (SELECT as_of_week FROM as_of), 1) AS remaining_season_weeks,
    COALESCE(trail.trailing_avg_weekly_sales, 0) * GREATEST(sty.exit_week_uncapped - (SELECT as_of_week FROM as_of), 1)
        AS projected_remaining_demand,
    snap.on_hand_units - COALESCE(trail.trailing_avg_weekly_sales, 0)
        * GREATEST(sty.exit_week_uncapped - (SELECT as_of_week FROM as_of), 1) AS overstock_units,
    CASE WHEN COALESCE(trail.trailing_avg_weekly_sales, 0) * GREATEST(sty.exit_week_uncapped - (SELECT as_of_week FROM as_of), 1) > 0
         THEN (snap.on_hand_units - trail.trailing_avg_weekly_sales * GREATEST(sty.exit_week_uncapped - (SELECT as_of_week FROM as_of), 1))
              / (trail.trailing_avg_weekly_sales * GREATEST(sty.exit_week_uncapped - (SELECT as_of_week FROM as_of), 1))
         ELSE NULL END AS overstock_pct,
    CASE WHEN COALESCE(trail.trailing_avg_weekly_sales, 0) > 0
         THEN (snap.on_hand_units / trail.trailing_avg_weekly_sales) * 7
         ELSE NULL END AS stockout_est_days
FROM snap
JOIN silver.dim_sku sku ON sku.sku_id = snap.sku_id
JOIN silver.dim_style sty ON sty.style_id = sku.style_id
LEFT JOIN trail ON trail.location_id = snap.location_id AND trail.sku_id = snap.sku_id;
