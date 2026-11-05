PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    geographic_scope TEXT NOT NULL DEFAULT 'Norway'
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    brand TEXT,
    category TEXT NOT NULL,
    ean TEXT UNIQUE,
    UNIQUE(canonical_name, brand)
);

CREATE TABLE IF NOT EXISTS nutrition_profiles (
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

CREATE TABLE IF NOT EXISTS price_observations (
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

-- Kassalapp source observations are REVIEW-ONLY. Unlike curated prices,
-- search responses do not establish a reliable price time or offer validity.
-- No FK into curated product/price tables; promotion requires explicit review.
CREATE TABLE IF NOT EXISTS kassalapp_staging (
    id INTEGER PRIMARY KEY,
    provider_product_id TEXT NOT NULL,
    ean TEXT,
    product_name TEXT NOT NULL,
    brand TEXT,
    store_code TEXT,
    price_nok REAL CHECK (price_nok > 0),
    reported_weight_g REAL CHECK (reported_weight_g > 0),
    protein_g_unverified_basis REAL CHECK (protein_g_unverified_basis > 0 AND protein_g_unverified_basis <= 100),
    fetched_at TEXT NOT NULL,
    price_checked_at TEXT,
    review_status TEXT NOT NULL CHECK (review_status IN ('incomplete', 'needs_review')),
    review_notes TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL
);

-- Kassalapp price history is a separate PRIVATE evidence stream.
-- Aggregation is daily minimum by source report, not individual checkout
-- events or a guaranteed local offer validity interval.
CREATE TABLE IF NOT EXISTS kassalapp_history_snapshots (
    id INTEGER PRIMARY KEY,
    requested_eans_json TEXT NOT NULL,
    days INTEGER NOT NULL CHECK (days BETWEEN 1 AND 90),
    aggregation TEXT NOT NULL CHECK (aggregation = 'min'),
    payload_sha256 TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    raw_response_json TEXT NOT NULL,
    UNIQUE(requested_eans_json, days, aggregation, payload_sha256)
);

CREATE TABLE IF NOT EXISTS kassalapp_history_rows (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL
        REFERENCES kassalapp_history_snapshots(id),
    ean TEXT NOT NULL,
    store_code TEXT NOT NULL,
    price_day TEXT NOT NULL,
    price_ore INTEGER NOT NULL CHECK (price_ore > 0),
    -- From stores[].last_checked for the CURRENT store listing;
    -- NEVER claim it is an event timestamp for this historical row.
    store_latest_checked_at TEXT,
    review_status TEXT NOT NULL DEFAULT 'source_only'
        CHECK (review_status = 'source_only'),
    UNIQUE(snapshot_id, ean, store_code, price_day)
);

CREATE INDEX IF NOT EXISTS idx_kassalapp_history_review
    ON kassalapp_history_rows(ean, store_code, price_day);

CREATE INDEX IF NOT EXISTS idx_price_observations_product_store
    ON price_observations(product_id, store_id, valid_until);
CREATE INDEX IF NOT EXISTS idx_nutrition_profiles_product
    ON nutrition_profiles(product_id);
CREATE INDEX IF NOT EXISTS idx_kassalapp_staging_review
    ON kassalapp_staging(review_status, fetched_at);

-- Manually checked branded evidence stays PRIVATE and review-only. These rows
-- never imply a checked shelf price or entry into curated protein rankings.
-- Repeated identical evidence + provider snapshots are immutable/idempotent.
CREATE TABLE IF NOT EXISTS sku_nutrition_reviews (
    id INTEGER PRIMARY KEY,
    ean TEXT NOT NULL,
    review_sha256 TEXT NOT NULL UNIQUE,
    evidence_sha256 TEXT NOT NULL,
    brand TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    net_weight_g REAL NOT NULL CHECK (net_weight_g > 0),
    protein_per_100g REAL NOT NULL CHECK (protein_per_100g > 0 AND protein_per_100g <= 100),
    kcal_per_100g REAL NOT NULL CHECK (kcal_per_100g >= 0),
    carbs_per_100g REAL NOT NULL CHECK (carbs_per_100g >= 0),
    nutrition_basis TEXT NOT NULL CHECK (nutrition_basis = 'per_100g_as_sold'),
    source_url TEXT NOT NULL,
    evidence_checked_on TEXT NOT NULL,
    reviewer_confirmed INTEGER NOT NULL CHECK (reviewer_confirmed IN (0, 1)),
    reviewer_note TEXT NOT NULL,
    identity_status TEXT NOT NULL CHECK (identity_status IN ('verified', 'needs_review')),
    nutrition_status TEXT NOT NULL CHECK (nutrition_status IN ('verified', 'verified_with_warning', 'needs_review')),
    overall_status TEXT NOT NULL CHECK (overall_status IN ('verified', 'verified_with_warning', 'needs_review')),
    price_status TEXT NOT NULL DEFAULT 'source_only' CHECK (price_status = 'source_only'),
    candidate_count INTEGER NOT NULL,
    matched_staging_ids_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    raw_evidence_json TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sku_nutrition_reviews_ean
    ON sku_nutrition_reviews(ean, id DESC);
