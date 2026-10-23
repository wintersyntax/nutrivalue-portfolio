-- Data quality audit of the latest Norwegian reference snapshot.
-- Missing values remain missing, not zero.
WITH latest_nb AS (
  SELECT id, content_sha256, retrieved_at
  FROM mvt_snapshots WHERE locale = 'nb'
  ORDER BY retrieved_at DESC, id DESC LIMIT 1
)
SELECT
  s.content_sha256 AS source_snapshot_sha256,
  s.retrieved_at,
  COUNT(*) AS reference_foods,
  SUM(CASE WHEN n.quantity_per_100g IS NOT NULL AND n.unit = 'g'
             AND CAST(n.quantity_per_100g AS REAL) > 0 THEN 1 ELSE 0 END)
       AS positive_protein_values,
  SUM(CASE WHEN n.food_id IS NULL THEN 1 ELSE 0 END) AS without_quantified_protein,
  SUM(CASE WHEN f.energy_kcal IS NOT NULL
             AND CAST(f.energy_kcal AS REAL) > 0 THEN 1 ELSE 0 END)
       AS positive_kcal_values,
  SUM(CASE WHEN f.energy_kcal IS NULL THEN 1 ELSE 0 END) AS missing_kcal_values,
  SUM(CASE WHEN n.quantity_per_100g IS NOT NULL AND n.unit = 'g'
             AND CAST(n.quantity_per_100g AS REAL) > 0
             AND f.energy_kcal IS NOT NULL AND CAST(f.energy_kcal AS REAL) > 0
           THEN 1 ELSE 0 END) AS usable_protein_kcal_pairs
FROM latest_nb AS s
JOIN mvt_foods AS f ON f.snapshot_id = s.id
LEFT JOIN mvt_food_nutrients AS n
  ON n.snapshot_id = f.snapshot_id AND n.food_id = f.food_id
 AND n.nutrient_id = 'Protein'
GROUP BY s.id, s.content_sha256, s.retrieved_at;
