-- Mattilsynet/Matvaretabellen is a national reference food composition database.
-- Its food IDs are NOT commercial product/EAN IDs. Never join these tables to
-- price observations by name without separately verified product matching.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS mvt_snapshots (
    id INTEGER PRIMARY KEY,
    locale TEXT NOT NULL CHECK (locale IN ('nb', 'en')),
    content_sha256 TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'Matvaretabellen / Mattilsynet',
    UNIQUE(locale, content_sha256)
);

CREATE TABLE IF NOT EXISTS mvt_endpoint_provenance (
    snapshot_id INTEGER NOT NULL REFERENCES mvt_snapshots(id),
    kind TEXT NOT NULL CHECK (kind IN ('foods', 'nutrients', 'food-groups', 'sources')),
    url TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, kind)
);

CREATE TABLE IF NOT EXISTS mvt_food_groups (
    snapshot_id INTEGER NOT NULL REFERENCES mvt_snapshots(id),
    group_id TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_id TEXT,
    PRIMARY KEY(snapshot_id, group_id)
);

CREATE TABLE IF NOT EXISTS mvt_nutrients (
    snapshot_id INTEGER NOT NULL REFERENCES mvt_snapshots(id),
    nutrient_id TEXT NOT NULL,
    name TEXT NOT NULL,
    unit TEXT NOT NULL,
    eurofir_id TEXT,
    decimal_precision INTEGER,
    PRIMARY KEY(snapshot_id, nutrient_id)
);

CREATE TABLE IF NOT EXISTS mvt_sources (
    snapshot_id INTEGER NOT NULL REFERENCES mvt_snapshots(id),
    source_id TEXT NOT NULL,
    description TEXT,
    PRIMARY KEY(snapshot_id, source_id)
);

CREATE TABLE IF NOT EXISTS mvt_foods (
    snapshot_id INTEGER NOT NULL REFERENCES mvt_snapshots(id),
    food_id TEXT NOT NULL,
    food_name TEXT NOT NULL,
    food_group_id TEXT NOT NULL,
    source_uri TEXT,
    edible_part_pct TEXT,
    energy_kcal TEXT,
    energy_kj TEXT,
    PRIMARY KEY(snapshot_id, food_id),
    FOREIGN KEY(snapshot_id, food_group_id)
        REFERENCES mvt_food_groups(snapshot_id, group_id)
);

CREATE TABLE IF NOT EXISTS mvt_food_nutrients (
    snapshot_id INTEGER NOT NULL,
    food_id TEXT NOT NULL,
    nutrient_id TEXT NOT NULL,
    quantity_per_100g TEXT NOT NULL,
    unit TEXT NOT NULL,
    source_id TEXT,
    PRIMARY KEY(snapshot_id, food_id, nutrient_id),
    FOREIGN KEY(snapshot_id, food_id)
        REFERENCES mvt_foods(snapshot_id, food_id),
    FOREIGN KEY(snapshot_id, nutrient_id)
        REFERENCES mvt_nutrients(snapshot_id, nutrient_id)
);

-- Source-only food constituents can lack a quantity and official nutrient
-- definition. Their missing-value provenance is NOT a numeric zero.
CREATE TABLE IF NOT EXISTS mvt_unquantified_nutrients (
    snapshot_id INTEGER NOT NULL,
    food_id TEXT NOT NULL,
    nutrient_id TEXT NOT NULL,
    source_id TEXT,
    PRIMARY KEY(snapshot_id, food_id, nutrient_id),
    FOREIGN KEY(snapshot_id, food_id)
        REFERENCES mvt_foods(snapshot_id, food_id)
);

-- Official API may publish source-only energy/calorie objects (no numeric
-- quantity or unit). Keep source provenance without filling in missing values.
CREATE TABLE IF NOT EXISTS mvt_unquantified_energy (
    snapshot_id INTEGER NOT NULL,
    food_id TEXT NOT NULL,
    energy_unit TEXT NOT NULL CHECK(energy_unit IN ('kcal', 'kJ')),
    source_id TEXT,
    PRIMARY KEY(snapshot_id, food_id, energy_unit),
    FOREIGN KEY(snapshot_id, food_id)
        REFERENCES mvt_foods(snapshot_id, food_id)
);

CREATE INDEX IF NOT EXISTS idx_mvt_food_group
    ON mvt_foods(snapshot_id, food_group_id);
CREATE INDEX IF NOT EXISTS idx_mvt_food_nutrient
    ON mvt_food_nutrients(snapshot_id, nutrient_id);

-- Reference-food analysis only. No implication that a listed food can be
-- purchased at any retailer for a known price, or that raw/cooked state
-- matches a separate branded grocery item.
CREATE VIEW IF NOT EXISTS mvt_latest_protein AS
SELECT
    f.food_id,
    f.food_name,
    f.food_group_id,
    f.source_uri,
    CAST(n.quantity_per_100g AS REAL) AS protein_g_per_100g,
    CAST(f.edible_part_pct AS REAL) AS edible_part_pct,
    n.source_id AS protein_source_id,
    s.locale,
    s.retrieved_at,
    s.content_sha256
FROM mvt_snapshots AS s
JOIN mvt_foods AS f ON f.snapshot_id = s.id
JOIN mvt_food_nutrients AS n
    ON n.snapshot_id = f.snapshot_id AND n.food_id = f.food_id
WHERE s.id = (
    SELECT id FROM mvt_snapshots
    WHERE locale = s.locale
    ORDER BY retrieved_at DESC, id DESC LIMIT 1
)
AND n.nutrient_id = 'Protein' AND n.unit = 'g'
AND CAST(n.quantity_per_100g AS REAL) > 0;
