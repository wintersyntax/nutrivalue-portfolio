-- Verified, still-valid offers with a matching as-sold nutrition basis.
-- Rows with ambiguous edible yield / product mapping must be excluded at ingestion.
-- For products with multiple credible profiles, choose one canonical profile before
-- analytics; otherwise a product/offer can appear more than once.
SELECT
    p.canonical_name AS product,
    s.name AS store,
    o.price_nok,
    o.package_net_edible_g,
    n.protein_per_100g,
    ROUND(o.package_net_edible_g * n.protein_per_100g / 100.0, 2)
        AS total_protein_g,
    ROUND(o.price_nok / (o.package_net_edible_g * n.protein_per_100g / 100.0), 2)
        AS nok_per_g_protein,
    o.valid_until,
    o.source_ref AS price_source,
    n.source_ref AS nutrition_source
FROM price_observations AS o
JOIN products AS p ON p.id = o.product_id
JOIN stores AS s ON s.id = o.store_id
JOIN nutrition_profiles AS n ON n.product_id = p.id
WHERE o.verification_status = 'verified'
  AND n.measurement_basis = 'as_sold'
  AND o.valid_from <= date('now')
  AND o.valid_until >= date('now')
ORDER BY nok_per_g_protein ASC, p.canonical_name ASC;
