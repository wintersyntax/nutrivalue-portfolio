-- Curated meal decision vocabulary, not matched retail SKU nutrition or pricing.
-- Install AFTER analytics/matvaretabellen_schema.sql in the same SQLite DB.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS food_role_items (
  food_key TEXT PRIMARY KEY,
  food_label TEXT NOT NULL,
  preference_tier TEXT NOT NULL CHECK
    (preference_tier IN ('core', 'alternative', 'occasional', 'supporting')),
  form_hint TEXT NOT NULL CHECK
    (form_hint IN ('raw','cooked','as_sold','drained','dry','unspecified'))
);

CREATE TABLE IF NOT EXISTS food_role_assignments (
  food_key TEXT NOT NULL REFERENCES food_role_items(food_key),
  role TEXT NOT NULL CHECK (
    role IN ('main_meal_protein','breakfast_protein','quick_protein',
             'supporting_protein','carb_base','calorie_booster','freezer_helper',
             'fruit_berries','vegetable','cooking_base',
             'bread_base','sandwich_topping')),
  PRIMARY KEY (food_key, role)
);

CREATE TABLE IF NOT EXISTS food_role_search_terms (
  food_key TEXT NOT NULL REFERENCES food_role_items(food_key),
  search_term TEXT NOT NULL,
  PRIMARY KEY (food_key, search_term)
);

-- Directional, context-specific substitution; no 1:1 mass or macro promise.
CREATE TABLE IF NOT EXISTS food_substitution_rules (
  recipe_context TEXT NOT NULL CHECK (
    recipe_context IN ('curry_stew','mince_sauce','tray_bake','rice_bowl',
                       'potato_bowl','breakfast_oats')),
  from_food_key TEXT NOT NULL REFERENCES food_role_items(food_key),
  to_food_key TEXT NOT NULL REFERENCES food_role_items(food_key),
  caution TEXT NOT NULL,
  substitution_scope TEXT NOT NULL CHECK
    (substitution_scope IN ('contextual', 'partial')),
  CHECK (from_food_key <> to_food_key),
  PRIMARY KEY (recipe_context, from_food_key, to_food_key)
);

-- Human-approved reference-food association ONLY. Food IDs are not EANs.
-- Every association is pinned to one Matvaretabellen snapshot; not silently
-- promoted to a newer reference revision or into a retail product table.
CREATE TABLE IF NOT EXISTS food_reference_reviews (
  food_key TEXT NOT NULL REFERENCES food_role_items(food_key),
  snapshot_id INTEGER NOT NULL,
  mvt_food_id TEXT NOT NULL,
  reviewed_at TEXT NOT NULL CHECK (length(trim(reviewed_at)) > 0),
  reviewer_note TEXT NOT NULL CHECK (length(trim(reviewer_note)) > 0),
  approved_form TEXT NOT NULL CHECK (
    approved_form IN ('raw','cooked','as_sold','drained','dry','unspecified')),
  PRIMARY KEY (food_key, snapshot_id),
  FOREIGN KEY (snapshot_id, mvt_food_id)
    REFERENCES mvt_foods(snapshot_id, food_id)
);

CREATE INDEX IF NOT EXISTS food_reference_reviews_idx
  ON food_reference_reviews(snapshot_id, mvt_food_id);
