CREATE OR REPLACE TABLE gold.weekly_demand_style_region AS
SELECT
    s.style_id,
    s.region_code,
    s.week_id,
    w.week_start_date,
    w.fiscal_year,
    w.fiscal_season,
    SUM(s.units) AS units,
    SUM(s.gross_revenue) AS gross_revenue,
    COUNT(DISTINCT s.location_id) AS n_locations
FROM silver.fact_sales_line s
JOIN silver.dim_week w ON w.week_id = s.week_id
GROUP BY 1, 2, 3, 4, 5, 6;
