CREATE OR REPLACE TABLE gold.weekly_demand_store_sku AS
SELECT
    i.location_id,
    i.location_type,
    i.region_code,
    i.sku_id,
    i.week_id,
    i.on_hand_units,
    i.weeks_of_supply,
    COALESCE(s.units, 0) AS units_sold,
    COALESCE(s.gross_revenue, 0) AS gross_revenue
FROM silver.fact_inventory_position i
LEFT JOIN silver.fact_sales_line s
    ON s.location_id = i.location_id AND s.sku_id = i.sku_id AND s.week_id = i.week_id;
