-- Descriptive source groups: heterogeneous raw/cooked states are NOT harmonized.
WITH latest_nb AS (
 SELECT id FROM mvt_snapshots WHERE locale='nb'
 ORDER BY retrieved_at DESC,id DESC LIMIT 1
), profiles AS (
 SELECT g.group_id,g.name AS reference_group,f.food_id,
   CASE WHEN n.unit='g' AND n.quantity_per_100g IS NOT NULL
     AND CAST(n.quantity_per_100g AS REAL)>0
   THEN CAST(n.quantity_per_100g AS REAL) END AS protein_g,
   CASE WHEN f.energy_kcal IS NOT NULL AND CAST(f.energy_kcal AS REAL)>0
   THEN CAST(f.energy_kcal AS REAL) END AS kcal
 FROM latest_nb AS s
 JOIN mvt_foods AS f ON f.snapshot_id=s.id
 JOIN mvt_food_groups AS g ON g.snapshot_id=s.id AND g.group_id=f.food_group_id
 LEFT JOIN mvt_food_nutrients AS n ON n.snapshot_id=f.snapshot_id AND n.food_id=f.food_id
   AND n.nutrient_id='Protein'
)
SELECT group_id,reference_group,COUNT(*) AS reference_food_count,
 COUNT(protein_g) AS foods_with_positive_protein,
 COUNT(kcal) AS foods_with_positive_kcal,
 ROUND(AVG(protein_g),2) AS mean_protein_g_per_100g,
 ROUND(AVG(kcal),1) AS mean_kcal_per_100g
FROM profiles GROUP BY group_id,reference_group
ORDER BY reference_food_count DESC,group_id LIMIT 30;
