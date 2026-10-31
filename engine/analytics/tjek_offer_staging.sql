-- TASK-023: PRIVATE immutable Tjek source-only snapshots and observations.
-- Separate from curated products, nutrition_profiles, price_observations
-- and TASK-020 reconciliation assertions. Never implies local shelf prices.
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS tjek_snapshots (
  sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),
  format TEXT NOT NULL CHECK(format IN ('json','kundeavis_ndjson_gz')),
  fetched_at TEXT NOT NULL,
  origin_family TEXT NOT NULL DEFAULT 'tjek' CHECK(origin_family='tjek'),
  raw_payload BLOB NOT NULL,
  raw_bytes INTEGER NOT NULL CHECK(raw_bytes>0),
  review_state TEXT NOT NULL DEFAULT 'source_only' CHECK(review_state='source_only')
);
CREATE TABLE IF NOT EXISTS tjek_catalogs (
  snapshot_sha256 TEXT NOT NULL REFERENCES tjek_snapshots(sha256),
  catalog_id TEXT NOT NULL,
  dealer_id TEXT NOT NULL,
  chain_name TEXT NOT NULL,
  label TEXT,
  run_from TEXT NOT NULL,
  run_till TEXT NOT NULL,
  published_at TEXT,
  claimed_offers INTEGER CHECK(claimed_offers>=0),
  PRIMARY KEY(snapshot_sha256,catalog_id)
);
CREATE TABLE IF NOT EXISTS tjek_offers (
  snapshot_sha256 TEXT NOT NULL,
  catalog_id TEXT NOT NULL,
  offer_id TEXT NOT NULL,
  dealer_id TEXT NOT NULL,
  heading TEXT NOT NULL,
  description TEXT,
  price_ore INTEGER CHECK(price_ore>0),
  pre_price_ore INTEGER CHECK(pre_price_ore>0),
  currency TEXT,
  quantity TEXT,
  unit TEXT,
  pack_count INTEGER CHECK(pack_count>0),
  quantity_diagnostic TEXT NOT NULL,
  unit_price_ore INTEGER CHECK(unit_price_ore>0),
  unit_price_basis TEXT CHECK(unit_price_basis IN ('kg','l')),
  computed_unit_price_ore INTEGER CHECK(computed_unit_price_ore>0),
  printed_unit_price_ore INTEGER CHECK(printed_unit_price_ore>0),
  unit_price_confidence TEXT NOT NULL,
  member_condition INTEGER NOT NULL CHECK(member_condition IN (0,1)),
  multibuy_condition INTEGER NOT NULL CHECK(multibuy_condition IN (0,1)),
  deposit_condition INTEGER NOT NULL CHECK(deposit_condition IN (0,1)),
  run_from TEXT NOT NULL,
  run_till TEXT NOT NULL,
  local_store_id TEXT,
  ean TEXT,
  nutrition_basis TEXT,
  review_state TEXT NOT NULL DEFAULT 'source_only' CHECK(review_state='source_only'),
  PRIMARY KEY(snapshot_sha256,catalog_id,offer_id),
  FOREIGN KEY(snapshot_sha256,catalog_id)
    REFERENCES tjek_catalogs(snapshot_sha256,catalog_id),
  CHECK(local_store_id IS NULL AND ean IS NULL AND nutrition_basis IS NULL)
);
CREATE INDEX IF NOT EXISTS tjek_offers_chain_window_idx
  ON tjek_offers(dealer_id,run_from,run_till);
CREATE INDEX IF NOT EXISTS tjek_offers_provenance_idx
  ON tjek_offers(snapshot_sha256,catalog_id);
CREATE TRIGGER IF NOT EXISTS tjek_snapshots_no_update
  BEFORE UPDATE ON tjek_snapshots BEGIN SELECT RAISE(ABORT,'immutable Tjek snapshot'); END;
CREATE TRIGGER IF NOT EXISTS tjek_snapshots_no_delete
  BEFORE DELETE ON tjek_snapshots BEGIN SELECT RAISE(ABORT,'immutable Tjek snapshot'); END;
CREATE TRIGGER IF NOT EXISTS tjek_catalogs_no_update
  BEFORE UPDATE ON tjek_catalogs BEGIN SELECT RAISE(ABORT,'immutable Tjek catalog'); END;
CREATE TRIGGER IF NOT EXISTS tjek_catalogs_no_delete
  BEFORE DELETE ON tjek_catalogs BEGIN SELECT RAISE(ABORT,'immutable Tjek catalog'); END;
CREATE TRIGGER IF NOT EXISTS tjek_offers_no_update
  BEFORE UPDATE ON tjek_offers BEGIN SELECT RAISE(ABORT,'immutable Tjek offer'); END;
CREATE TRIGGER IF NOT EXISTS tjek_offers_no_delete
  BEFORE DELETE ON tjek_offers BEGIN SELECT RAISE(ABORT,'immutable Tjek offer'); END;
