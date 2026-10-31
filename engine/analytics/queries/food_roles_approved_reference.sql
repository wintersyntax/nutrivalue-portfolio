-- Returns ONLY manually approved, snapshot-pinned reference matches.
-- Does not imply a corresponding exact retail SKU, price or cooked yield.
-- An approval from an older revision does not silently carry forward.
WITH latest_nb AS (
  SELECT id, retrieved_at
  FROM mvt_snapshots
  WHERE locale='nb'
  ORDER BY retrieved_at DESC, id DESC LIMIT 1
)
SELECT
  i.food_key, i.food_label, i.preference_tier, r.approved_form,
  f.food_id AS reference_food_id, f.food_name AS reference_food_name,
  CAST(n.quantity_per_100g AS REAL) AS protein_g_per_100g_edible,
  CAST(f.energy_kcal AS REAL) AS kcal_per_100g_edible,
  r.reviewed_at, r.reviewer_note, s.retrieved_at AS source_retrieved_at
FROM food_role_items AS i
JOIN food_reference_reviews AS r ON r.food_key=i.food_key
JOIN latest_nb AS s ON s.id=r.snapshot_id
JOIN mvt_foods AS f
  ON f.snapshot_id=r.snapshot_id AND f.food_id=r.mvt_food_id
LEFT JOIN mvt_food_nutrients AS n
  ON n.snapshot_id=f.snapshot_id AND n.food_id=f.food_id
 AND n.nutrient_id='Protein' AND n.unit='g'
ORDER BY i.food_key;
