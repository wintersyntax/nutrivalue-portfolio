-- TASK-020: PRIVATE candidate assertions; no FK to curated SKU/price truth.
-- Source family denotes the independent upstream origin, not a website count.
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS recon_sources (
  source_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  origin_family TEXT NOT NULL CHECK(length(trim(origin_family))>0),
  source_ref TEXT NOT NULL,
  snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256)=64),
  fetched_at TEXT NOT NULL,
  derived_from TEXT REFERENCES recon_sources(source_id),
  access_status TEXT NOT NULL CHECK(access_status IN ('permitted_private','review_needed')),
  UNIQUE(provider,source_ref,snapshot_sha256)
);
CREATE TABLE IF NOT EXISTS recon_field_assertions (
  assertion_sha256 TEXT PRIMARY KEY CHECK(length(assertion_sha256)=64),
  source_id TEXT NOT NULL REFERENCES recon_sources(source_id),
  retailer TEXT NOT NULL CHECK(retailer IN ('KIWI','REMA')),
  retailer_snapshot_sha256 TEXT NOT NULL CHECK(length(retailer_snapshot_sha256)=64),
  retailer_card_no INTEGER NOT NULL CHECK(retailer_card_no>0),
  candidate_ref TEXT NOT NULL,
  field_name TEXT NOT NULL CHECK(field_name IN ('ean','brand','variant','package_quantity','package_unit','pack_count','protein_per_100g','nutrition_basis')),
  asserted_value TEXT NOT NULL CHECK(length(trim(asserted_value))>0),
  asserted_unit TEXT,
  checked_at TEXT,
  added_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'source_only' CHECK(status='source_only'),
  UNIQUE(source_id,retailer,retailer_snapshot_sha256,retailer_card_no,candidate_ref,field_name,asserted_value,asserted_unit)
);
CREATE INDEX IF NOT EXISTS recon_assertions_card_field_idx ON recon_field_assertions(retailer,retailer_snapshot_sha256,retailer_card_no,field_name);
-- These are immutable observations: correction = a new source/assertion revision.
CREATE TRIGGER IF NOT EXISTS recon_sources_no_update BEFORE UPDATE ON recon_sources BEGIN SELECT RAISE(ABORT,'immutable source'); END;
CREATE TRIGGER IF NOT EXISTS recon_sources_no_delete BEFORE DELETE ON recon_sources BEGIN SELECT RAISE(ABORT,'immutable source'); END;
CREATE TRIGGER IF NOT EXISTS recon_assertions_no_update BEFORE UPDATE ON recon_field_assertions BEGIN SELECT RAISE(ABORT,'immutable assertion'); END;
CREATE TRIGGER IF NOT EXISTS recon_assertions_no_delete BEFORE DELETE ON recon_field_assertions BEGIN SELECT RAISE(ABORT,'immutable assertion'); END;
