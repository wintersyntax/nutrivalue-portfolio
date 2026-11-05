-- Public portfolio subset of the privately developed NutriValue schema.
-- No real SKU matches, provider payloads, API keys or user transactions.
PRAGMA foreign_keys = ON;

CREATE TABLE stores (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    geographic_scope TEXT NOT NULL DEFAULT 'Norway'
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    brand TEXT,
    category TEXT NOT NULL,
    ean TEXT UNIQUE,
    UNIQUE(canonical_name, brand)
);
CREATE TABLE nutrition_profiles (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    protein_per_100g REAL NOT NULL CHECK (protein_per_100g > 0 AND protein_per_100g <= 100),
    kcal_per_100g REAL CHECK (kcal_per_100g >= 0),
    measurement_basis TEXT NOT NULL CHECK (measurement_basis IN ('as_sold', 'raw', 'cooked', 'drained')),
    profile_type TEXT NOT NULL CHECK (profile_type IN ('exact_branded', 'generic_estimate')),
    source_name TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    UNIQUE(product_id, source_ref, measurement_basis)
);
CREATE TABLE price_observations (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    store_id INTEGER NOT NULL REFERENCES stores(id),
    price_nok REAL NOT NULL CHECK (price_nok > 0),
    package_net_edible_g REAL NOT NULL CHECK (package_net_edible_g > 0),
    observed_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK (verification_status IN ('verified', 'estimated', 'demo')),
    conditions TEXT,
    CHECK (valid_until >= valid_from)
);
CREATE INDEX idx_price_by_product ON price_observations(product_id, store_id, valid_until);
CREATE INDEX idx_nutrition_by_product ON nutrition_profiles(product_id);
