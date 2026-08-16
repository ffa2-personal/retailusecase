CREATE OR REPLACE TABLE silver.fact_sales_line AS
SELECT
    order_id,
    location_id,
    channel,
    region_code,
    sku_id,
    style_id,
    category,
    week_id,
    units,
    markdown_pct,
    unit_price,
    gross_revenue,
    customer_id
FROM bronze.fact_sales_line
WHERE units > 0;
