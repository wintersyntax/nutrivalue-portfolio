-- TASK-077: private, append-only read model of the owner's Mealie recipes.
-- Mealie owns the recipe content; these tables are snapshots NutriValue READ,
-- never a second editable recipe store and never written back to Mealie.
-- One row set per snapshot; a changed recipe appears with a new content_sha256.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS mealie_snapshots (
  snapshot_id TEXT PRIMARY KEY,          -- SHA-256 of the saved manifest
  fetched_at TEXT NOT NULL,              -- UTC ISO time of the read
  host TEXT NOT NULL,
  mealie_version TEXT,
  recipe_count INTEGER NOT NULL CHECK (recipe_count >= 0)
);

CREATE TABLE IF NOT EXISTS mealie_recipes (
  snapshot_id TEXT NOT NULL REFERENCES mealie_snapshots(snapshot_id),
  recipe_id TEXT NOT NULL,               -- Mealie UUID, stable across renames
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  servings REAL CHECK (servings IS NULL OR servings > 0),
  yield_quantity REAL,
  yield_text TEXT,
  date_updated TEXT,
  updated_at TEXT,
  content_sha256 TEXT NOT NULL,          -- hash of the saved recipe JSON = recipe revision
  PRIMARY KEY (snapshot_id, recipe_id)
);

CREATE TABLE IF NOT EXISTS mealie_recipe_ingredients (
  snapshot_id TEXT NOT NULL,
  recipe_id TEXT NOT NULL,
  position INTEGER NOT NULL CHECK (position >= 1),
  title TEXT,                            -- section heading shown above this line
  quantity REAL,
  unit_name TEXT,
  unit_abbreviation TEXT,
  unit_standard_quantity REAL,
  unit_standard_unit TEXT,
  food_name TEXT,
  note TEXT,
  display TEXT,
  original_text TEXT,
  reference_id TEXT,
  grams REAL CHECK (grams IS NULL OR grams > 0),
  millilitres REAL CHECK (millilitres IS NULL OR millilitres > 0),
  amount_status TEXT NOT NULL CHECK (amount_status IN
    ('grams', 'volume', 'count', 'other_unit', 'no_quantity', 'no_food')),
  PRIMARY KEY (snapshot_id, recipe_id, position),
  FOREIGN KEY (snapshot_id, recipe_id) REFERENCES mealie_recipes(snapshot_id, recipe_id)
);

-- The newest snapshot is the current state of the recipe library.
CREATE VIEW IF NOT EXISTS v_mealie_current_recipes AS
SELECT r.*
FROM mealie_recipes AS r
WHERE r.snapshot_id = (SELECT snapshot_id FROM mealie_snapshots
                       ORDER BY fetched_at DESC, snapshot_id DESC LIMIT 1);

-- Ingredients of current recipes that cannot be used for nutrition yet.
CREATE VIEW IF NOT EXISTS v_mealie_ingredient_gaps AS
SELECT r.name AS recipe_name, i.position, i.food_name,
       COALESCE(i.display, i.original_text, i.note) AS text, i.amount_status
FROM v_mealie_current_recipes AS r
JOIN mealie_recipe_ingredients AS i
  ON i.snapshot_id = r.snapshot_id AND i.recipe_id = r.recipe_id
WHERE i.amount_status <> 'grams';
