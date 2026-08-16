CREATE OR REPLACE TABLE gold.supply_risk_exposure AS
SELECT
    sig.*,
    po.supplier_id,
    po.dc_id,
    po.original_expected_receipt_date,
    po.revised_expected_receipt_date,
    po.actual_receipt_date
FROM gold.inventory_imbalance_signals sig
JOIN (
    SELECT DISTINCT style_id, supplier_id, dc_id, original_expected_receipt_date,
                     revised_expected_receipt_date, actual_receipt_date
    FROM silver.fact_purchase_order_line
    WHERE is_delayed
) po ON po.style_id = sig.style_id
WHERE sig.location_type = 'Store';
