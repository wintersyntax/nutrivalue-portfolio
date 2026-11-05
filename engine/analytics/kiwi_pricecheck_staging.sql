-- PRIVATE, review-only first-party weekly KIWI price-check snapshots.
-- NOT verified shelf offers, no EAN/product match and no automatic ranking.
CREATE TABLE IF NOT EXISTS weekly_pricecheck_snapshots (
    sha256 TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    iso_year INTEGER NOT NULL,
    iso_week INTEGER NOT NULL CHECK (iso_week BETWEEN 1 AND 53),
    valid_through TEXT NOT NULL,
    original_html TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_pricecheck_candidates (
    snapshot_sha256 TEXT NOT NULL REFERENCES weekly_pricecheck_snapshots(sha256),
    row_no INTEGER NOT NULL,
    chain_name TEXT NOT NULL CHECK (chain_name = 'KIWI'),
    product_text TEXT NOT NULL,
    old_cents INTEGER CHECK (old_cents > 0),
    price_cents INTEGER NOT NULL CHECK (price_cents > 0),
    low_30d_cents INTEGER CHECK (low_30d_cents > 0),
    price_basis TEXT NOT NULL CHECK (price_basis IN ('per_package', 'per_kg', 'unknown')),
    candidate_protein INTEGER NOT NULL CHECK (candidate_protein IN (0, 1)),
    review_state TEXT NOT NULL CHECK (review_state = 'source_only'),
    PRIMARY KEY (snapshot_sha256, row_no)
);
CREATE INDEX IF NOT EXISTS weekly_pricecheck_review_idx
ON weekly_pricecheck_candidates(candidate_protein, price_basis);
