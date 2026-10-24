-- A minimal Matvaretabellen-shaped relational schema; the public fixtures are synthetic.
-- Reference food IDs are not retail EAN/SKUs; never automatically join on food names.
PRAGMA foreign_keys = ON;
CREATE TABLE mvt_snapshots (
    id INTEGER PRIMARY KEY,
    locale TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE(locale,content_sha256)
);
CREATE TABLE mvt_food_groups (
    snapshot_id INTEGER NOT NULL REFERENCES mvt_snapshots(id),
    group_id TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY(snapshot_id,group_id)
);
CREATE TABLE mvt_foods (
    snapshot_id INTEGER NOT NULL REFERENCES mvt_snapshots(id),
    food_id TEXT NOT NULL,
    food_name TEXT NOT NULL,
    food_group_id TEXT NOT NULL,
    source_uri TEXT,
    edible_part_pct TEXT,
    energy_kcal TEXT,
    PRIMARY KEY(snapshot_id,food_id),
    FOREIGN KEY(snapshot_id,food_group_id) REFERENCES mvt_food_groups(snapshot_id,group_id)
);
CREATE TABLE mvt_food_nutrients (
    snapshot_id INTEGER NOT NULL,
    food_id TEXT NOT NULL,
    nutrient_id TEXT NOT NULL,
    quantity_per_100g TEXT,
    unit TEXT NOT NULL,
    source_id TEXT,
    PRIMARY KEY(snapshot_id,food_id,nutrient_id),
    FOREIGN KEY(snapshot_id,food_id) REFERENCES mvt_foods(snapshot_id,food_id)
);
