CREATE OR REPLACE TABLE gold.returns_scorecard AS
WITH sales_agg AS (
    SELECT s.style_id, sku.size, s.week_id, SUM(s.units) AS units_sold
    FROM silver.fact_sales_line s
    JOIN silver.dim_sku sku ON sku.sku_id = s.sku_id
    GROUP BY 1, 2, 3
),
returns_agg AS (
    SELECT r.style_id, sku.size, r.week_id, SUM(r.units_returned) AS units_returned
    FROM silver.fact_returns_line r
    JOIN silver.dim_sku sku ON sku.sku_id = r.sku_id
    GROUP BY 1, 2, 3
)
SELECT
    sa.style_id,
    sty.style_name,
    sa.size,
    sa.week_id,
    w.week_start_date,
    sa.units_sold,
    COALESCE(ra.units_returned, 0) AS units_returned,
    COALESCE(ra.units_returned, 0)::DOUBLE / NULLIF(sa.units_sold, 0) AS return_rate
FROM sales_agg sa
JOIN silver.dim_week w ON w.week_id = sa.week_id
JOIN silver.dim_style sty ON sty.style_id = sa.style_id
LEFT JOIN returns_agg ra ON ra.style_id = sa.style_id AND ra.size = sa.size AND ra.week_id = sa.week_id;
