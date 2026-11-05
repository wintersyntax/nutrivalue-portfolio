-- PRIVATE first-party KIWI Prislås cards; never a current shelf price or verified SKU.
CREATE TABLE IF NOT EXISTS kiwi_locked_snapshots (
    sha256 TEXT PRIMARY KEY CHECK(length(sha256) = 64),
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    original_html TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kiwi_locked_cards (
    snapshot_sha256 TEXT NOT NULL REFERENCES kiwi_locked_snapshots(sha256),
    card_no INTEGER NOT NULL CHECK(card_no > 0),
    product_name TEXT NOT NULL CHECK(length(product_name) > 0),
    details_text TEXT NOT NULL,
    price_ore INTEGER NOT NULL CHECK(price_ore > 0),
    price_basis TEXT NOT NULL CHECK(price_basis = 'per_package'),
    deposit_applies INTEGER NOT NULL CHECK(deposit_applies IN (0,1)),
    package_quantity TEXT,
    package_unit TEXT,
    pack_count INTEGER,
    source_unit_text TEXT NOT NULL,
    unit_price_ore INTEGER CHECK(unit_price_ore > 0),
    unit_price_unit TEXT,
    unit_price_check TEXT NOT NULL CHECK(unit_price_check IN ('match','mismatch','unchecked')),
    review_state TEXT NOT NULL CHECK(review_state = 'source_only'),
    PRIMARY KEY (snapshot_sha256, card_no)
);
CREATE INDEX IF NOT EXISTS kiwi_locked_review_idx
ON kiwi_locked_cards(unit_price_check, package_unit);
