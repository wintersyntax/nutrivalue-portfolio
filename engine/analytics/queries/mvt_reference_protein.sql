-- Real Matvaretabellen reference foods AFTER an actual successful import.
-- NO retailer prices, EAN matching or as-sold SKU promises are inferred here.
-- Protein values are grams per 100 grams edible food in the given form.
SELECT
    p.food_id,
    p.food_name,
    g.name AS reference_group,
    ROUND(p.protein_g_per_100g, 1) AS protein_g_per_100g,
    -- Display energy in kcal by default; missing official value stays NULL.
    CAST(f.energy_kcal AS REAL) AS kcal_per_100g,
    p.edible_part_pct,
    p.protein_source_id,
    p.retrieved_at,
    p.source_uri
FROM mvt_latest_protein AS p
JOIN mvt_snapshots AS s
  ON s.locale = p.locale AND s.content_sha256 = p.content_sha256
JOIN mvt_foods AS f
  ON f.snapshot_id = s.id AND f.food_id = p.food_id
JOIN mvt_food_groups AS g
  ON g.snapshot_id = s.id AND g.group_id = p.food_group_id
WHERE p.locale = 'nb'
ORDER BY p.protein_g_per_100g DESC, p.food_name
LIMIT 30;
