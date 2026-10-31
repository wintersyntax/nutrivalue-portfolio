-- PRIVATE, source-only REMA locked-price evidence. No current shelf-price claims.
CREATE TABLE IF NOT EXISTS rema_locked_snapshots (
    sha256 TEXT PRIMARY KEY CHECK(length(sha256) = 64),
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    original_html TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rema_locked_cards (
    snapshot_sha256 TEXT NOT NULL REFERENCES rema_locked_snapshots(sha256),
    card_no INTEGER NOT NULL CHECK(card_no > 0),
    product_name TEXT NOT NULL CHECK(length(product_name) > 0),
    price_ore INTEGER NOT NULL CHECK(price_ore > 0),
    price_basis TEXT NOT NULL CHECK(price_basis IN ('per_package', 'unknown')),
    details_text TEXT NOT NULL,
    package_quantity TEXT,
    package_unit TEXT,
    pack_count INTEGER,
    unit_price_ore INTEGER CHECK(unit_price_ore > 0),
    unit_price_unit TEXT CHECK(unit_price_unit IN ('kg','l','stk','tab')),
    review_state TEXT NOT NULL CHECK(review_state = 'source_only'),
    PRIMARY KEY (snapshot_sha256, card_no)
);
CREATE INDEX IF NOT EXISTS rema_locked_review_idx ON rema_locked_cards(review_state, package_unit);
