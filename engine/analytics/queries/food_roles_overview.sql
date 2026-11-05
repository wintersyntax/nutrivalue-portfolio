-- Curated vocabulary; recipe substitutions are directional and contextual.
-- Scalar subqueries avoid duplicate role names when multiple substitutions exist.
SELECT
  f.food_key, f.food_label, f.preference_tier, f.form_hint,
  (SELECT GROUP_CONCAT(r.role, ', ')
     FROM food_role_assignments AS r
     WHERE r.food_key = f.food_key) AS assigned_roles,
  (SELECT COUNT(DISTINCT s.recipe_context)
     FROM food_substitution_rules AS s
     WHERE s.to_food_key = f.food_key) AS recipe_contexts_as_substitute
FROM food_role_items AS f
ORDER BY CASE f.preference_tier
  WHEN 'core' THEN 0
  WHEN 'alternative' THEN 1
  WHEN 'occasional' THEN 2
  ELSE 3 END, f.food_label;
