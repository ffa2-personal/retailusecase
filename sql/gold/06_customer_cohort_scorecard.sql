CREATE OR REPLACE TABLE gold.customer_cohort_scorecard AS
WITH orders AS (
    SELECT
        customer_id,
        COUNT(*) AS n_orders,
        SUM(units) AS total_units,
        SUM(gross_revenue) AS total_revenue,
        SUM(CASE WHEN style_id LIKE 'STY-PORTO%' THEN units ELSE 0 END) AS capsule_units
    FROM silver.fact_sales_line
    WHERE customer_id IS NOT NULL
    GROUP BY 1
)
SELECT
    c.customer_id,
    c.home_region,
    c.loyalty_tier,
    c.acquisition_campaign_id,
    COALESCE(c.acquisition_campaign_id = 'CMP-MILAN-TRUNK', FALSE) AS is_milan_cohort,
    COALESCE(o.n_orders, 0) AS n_orders,
    COALESCE(o.total_units, 0) AS total_units,
    COALESCE(o.total_revenue, 0) AS total_revenue,
    COALESCE(o.capsule_units, 0) AS capsule_units,
    COALESCE(o.n_orders, 0) >= 2 AS is_repeat_purchaser
FROM silver.dim_customer c
LEFT JOIN orders o ON o.customer_id = c.customer_id;
