-- DEMO ONLY: fake observations explicitly marked demo, NOT independently verified real offers.
-- Comparable price efficiency per gram of protein, using fictional prices only.
SELECT
    p.canonical_name AS synthetic_product,
    s.name AS synthetic_store,
    o.price_nok,
    o.package_net_edible_g,
    n.protein_per_100g,
    ROUND(o.package_net_edible_g * n.protein_per_100g / 100.0, 2) AS protein_g_in_package,
    ROUND(o.price_nok / (o.package_net_edible_g * n.protein_per_100g / 100.0), 2)
        AS nok_per_g_protein
FROM price_observations AS o
JOIN products AS p ON p.id = o.product_id
JOIN stores AS s ON s.id = o.store_id
JOIN nutrition_profiles AS n ON n.product_id = p.id
WHERE o.verification_status = 'demo'
  AND n.measurement_basis = 'as_sold'
  AND n.profile_type = 'exact_branded'
ORDER BY nok_per_g_protein ASC, p.canonical_name ASC;
