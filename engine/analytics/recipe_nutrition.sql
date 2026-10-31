-- TASK-080: owner-reviewed nutrition sources for recipe ingredients.
-- Install AFTER matvaretabellen_schema.sql and recipe_read_model.sql in the
-- private nutrivalue.sqlite3. Insert-only: a new review adds a row and the
-- latest one per ingredient is used; nothing is updated or deleted.
PRAGMA foreign_keys = ON;

-- Values the owner copied from a package label (owner-reported, not verified).
CREATE TABLE IF NOT EXISTS owner_product_labels (
  label_name TEXT NOT NULL,
  entered_at TEXT NOT NULL,
  basis TEXT NOT NULL CHECK (basis IN ('per_100g', 'per_100ml')),
  density_g_per_ml REAL CHECK (density_g_per_ml IS NULL OR density_g_per_ml BETWEEN 0.3 AND 2.5),
  kcal REAL NOT NULL CHECK (kcal BETWEEN 0 AND 900),
  protein_g REAL NOT NULL CHECK (protein_g BETWEEN 0 AND 100),
  fat_g REAL CHECK (fat_g IS NULL OR fat_g BETWEEN 0 AND 100),
  carbs_g REAL CHECK (carbs_g IS NULL OR carbs_g BETWEEN 0 AND 100),
  fiber_g REAL CHECK (fiber_g IS NULL OR fiber_g BETWEEN 0 AND 100),
  note TEXT NOT NULL CHECK (length(trim(note)) > 0),
  CHECK (basis = 'per_100g' OR density_g_per_ml IS NOT NULL),
  PRIMARY KEY (label_name, entered_at)
);

-- Which nutrition source a recipe ingredient (Mealie food name, lower case) uses.
CREATE TABLE IF NOT EXISTS recipe_ingredient_sources (
  ingredient_name TEXT NOT NULL,
  reviewed_at TEXT NOT NULL,
  source_kind TEXT NOT NULL CHECK (source_kind IN ('mvt', 'owner_label')),
  mvt_snapshot_id INTEGER,
  mvt_food_id TEXT,
  label_name TEXT,
  density_g_per_ml REAL CHECK (density_g_per_ml IS NULL OR density_g_per_ml BETWEEN 0.3 AND 2.5),
  note TEXT NOT NULL CHECK (length(trim(note)) > 0),
  CHECK ((source_kind = 'mvt' AND mvt_snapshot_id IS NOT NULL AND mvt_food_id IS NOT NULL
          AND label_name IS NULL)
      OR (source_kind = 'owner_label' AND label_name IS NOT NULL
          AND mvt_snapshot_id IS NULL AND mvt_food_id IS NULL)),
  PRIMARY KEY (ingredient_name, reviewed_at)
);

CREATE TRIGGER IF NOT EXISTS owner_product_labels_no_update
  BEFORE UPDATE ON owner_product_labels BEGIN SELECT RAISE(ABORT, 'insert-only'); END;
CREATE TRIGGER IF NOT EXISTS owner_product_labels_no_delete
  BEFORE DELETE ON owner_product_labels BEGIN SELECT RAISE(ABORT, 'insert-only'); END;
CREATE TRIGGER IF NOT EXISTS recipe_ingredient_sources_no_update
  BEFORE UPDATE ON recipe_ingredient_sources BEGIN SELECT RAISE(ABORT, 'insert-only'); END;
CREATE TRIGGER IF NOT EXISTS recipe_ingredient_sources_no_delete
  BEFORE DELETE ON recipe_ingredient_sources BEGIN SELECT RAISE(ABORT, 'insert-only'); END;

DROP VIEW IF EXISTS v_recipe_ingredient_source_current;
CREATE VIEW v_recipe_ingredient_source_current AS
SELECT * FROM (
  SELECT s.*, ROW_NUMBER() OVER (PARTITION BY ingredient_name
                                 ORDER BY reviewed_at DESC) AS rn
  FROM recipe_ingredient_sources AS s)
WHERE rn = 1;

DROP VIEW IF EXISTS v_owner_label_current;
CREATE VIEW v_owner_label_current AS
SELECT * FROM (
  SELECT l.*, ROW_NUMBER() OVER (PARTITION BY label_name ORDER BY entered_at DESC) AS rn
  FROM owner_product_labels AS l)
WHERE rn = 1;
