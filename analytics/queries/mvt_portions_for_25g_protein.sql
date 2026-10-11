-- Reference-only portion sizing for 25g protein, not retail package comparison.
WITH latest_nb AS (
  SELECT id,retrieved_at FROM mvt_snapshots WHERE locale='nb'
  ORDER BY retrieved_at DESC,id DESC LIMIT 1
),reference AS (
  SELECT f.food_id,f.food_name,g.name AS reference_group,
    CAST(n.quantity_per_100g AS REAL) AS protein_g_per_100g,
    CAST(f.energy_kcal AS REAL) AS kcal_per_100g,
    f.edible_part_pct,f.source_uri,s.retrieved_at
  FROM latest_nb AS s
  JOIN mvt_foods AS f ON f.snapshot_id=s.id
  JOIN mvt_food_groups AS g ON g.snapshot_id=f.snapshot_id AND g.group_id=f.food_group_id
  JOIN mvt_food_nutrients AS n ON n.snapshot_id=f.snapshot_id AND n.food_id=f.food_id
    AND n.nutrient_id='Protein' AND n.unit='g'
  WHERE n.quantity_per_100g IS NOT NULL AND CAST(n.quantity_per_100g AS REAL)>0
    AND f.energy_kcal IS NOT NULL AND CAST(f.energy_kcal AS REAL)>0
)
SELECT food_id,food_name,reference_group,
  ROUND(protein_g_per_100g,2) AS protein_g_per_100g,
  ROUND(kcal_per_100g,1) AS kcal_per_100g,
  ROUND(2500.0/protein_g_per_100g,1) AS edible_grams_for_25g_protein,
  ROUND(25.0*kcal_per_100g/protein_g_per_100g,1) AS kcal_for_25g_protein,
  edible_part_pct AS source_edible_part_pct,retrieved_at,source_uri
FROM reference ORDER BY kcal_for_25g_protein ASC,food_id LIMIT 30;
