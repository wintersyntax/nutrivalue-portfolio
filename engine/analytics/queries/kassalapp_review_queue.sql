-- TASK-002: Inspect provider candidates, NOT verified protein-price rankings.
-- "protein_g_unverified_basis" must not be interpreted as per 100 g until checked.
-- Retrieved timestamps indicate when WE fetched the provider API, not when the
-- store price was checked. API Search has no reliable price-check timestamp.
SELECT
    fetched_at,
    product_name,
    ean,
    store_code,
    price_nok,
    reported_weight_g,
    protein_g_unverified_basis,
    review_status,
    review_notes,
    source_ref
FROM kassalapp_staging
ORDER BY fetched_at DESC, product_name, store_code;
