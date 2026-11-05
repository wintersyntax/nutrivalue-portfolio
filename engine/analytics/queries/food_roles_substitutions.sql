-- Recipe-context-only, directional and explicitly conditional replacements.
-- A 'partial' substitution is NEVER equal edible mass, calories or protein.
SELECT
  s.recipe_context,
  s.from_food_key,
  f.food_label AS planned_food,
  s.to_food_key,
  t.food_label AS possible_substitute,
  s.substitution_scope,
  s.caution
FROM food_substitution_rules AS s
JOIN food_role_items AS f ON f.food_key=s.from_food_key
JOIN food_role_items AS t ON t.food_key=s.to_food_key
ORDER BY s.recipe_context, s.from_food_key, s.to_food_key;
