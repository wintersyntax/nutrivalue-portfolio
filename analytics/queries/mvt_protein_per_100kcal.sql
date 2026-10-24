-- Reference foods only, never assumed to match an EAN or retailer price.
WITH latest_nb AS (
  SELECT id, retrieved_at FROM mvt_snapshots WHERE locale = 'nb'
  ORDER BY retrieved_at DESC, id DESC LIMIT 1
), measured AS (
  SELECT f.food_id, f.food_name, g.name AS reference_group,
    CAST(n.quantity_per_100g AS REAL) AS protein_g_per_100g,
    CAST(f.energy_kcal AS REAL) AS kcal_per_100g, f.source_uri, s.retrieved_at
  FROM latest_nb AS s
  JOIN mvt_foods AS f ON f.snapshot_id=s.id
  JOIN mvt_food_groups AS g ON g.snapshot_id=f.snapshot_id AND g.group_id=f.food_group_id
  JOIN mvt_food_nutrients AS n ON n.snapshot_id=f.snapshot_id AND n.food_id=f.food_id
    AND n.nutrient_id='Protein' AND n.unit='g'
  WHERE n.quantity_per_100g IS NOT NULL
    AND CAST(n.quantity_per_100g AS REAL)>0
    AND f.energy_kcal IS NOT NULL AND CAST(f.energy_kcal AS REAL)>0
)
SELECT food_id, food_name, reference_group,
  ROUND(protein_g_per_100g,2) AS protein_g_per_100g,
  ROUND(kcal_per_100g,1) AS kcal_per_100g,
  ROUND(100.0*protein_g_per_100g/kcal_per_100g,2) AS protein_g_per_100kcal,
  retrieved_at, source_uri
FROM measured ORDER BY protein_g_per_100kcal DESC,food_id LIMIT 30;
