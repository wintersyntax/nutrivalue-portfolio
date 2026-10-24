-- TASK-021: private, append-only five-card review report; no curated promotions.
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS pilot_runs (
  run_sha256 TEXT PRIMARY KEY CHECK(length(run_sha256)=64),
  rema_snapshot_sha256 TEXT NOT NULL CHECK(length(rema_snapshot_sha256)=64),
  rema_fetched_at TEXT NOT NULL,
  rema_source_url TEXT NOT NULL,
  kassalapp_staging_rows_seen INTEGER NOT NULL CHECK(kassalapp_staging_rows_seen>=0),
  assessed_at TEXT NOT NULL,
  matching_scope TEXT NOT NULL CHECK(matching_scope='local_staging_exact_name_only'),
  status TEXT NOT NULL CHECK(status='source_only')
);
CREATE TABLE IF NOT EXISTS pilot_cards (
  run_sha256 TEXT NOT NULL REFERENCES pilot_runs(run_sha256),
  card_no INTEGER NOT NULL CHECK(card_no>0),
  product_name TEXT NOT NULL,
  price_ore INTEGER NOT NULL CHECK(price_ore>0),
  package_quantity TEXT,
  package_unit TEXT,
  price_basis TEXT NOT NULL,
  reported_unit_price_ore INTEGER,
  derived_unit_price_ore INTEGER,
  derived_unit_status TEXT NOT NULL,
  result TEXT NOT NULL CHECK(result IN ('insufficient_evidence','conflicting_evidence')),
  reasons_json TEXT NOT NULL,
  same_visible_fields_card_numbers_json TEXT NOT NULL,
  candidate_staging_ids_json TEXT NOT NULL,
  source_only INTEGER NOT NULL CHECK(source_only=1),
  PRIMARY KEY(run_sha256,card_no)
);
CREATE TABLE IF NOT EXISTS pilot_lookup_attempts (
  run_sha256 TEXT NOT NULL,
  card_no INTEGER NOT NULL,
  provider TEXT NOT NULL CHECK(provider='kassalapp_staging'),
  lookup_scope TEXT NOT NULL CHECK(lookup_scope='exact_normalized_name_in_existing_local_rows'),
  source_rows_scanned INTEGER NOT NULL CHECK(source_rows_scanned>=0),
  matching_rows INTEGER NOT NULL CHECK(matching_rows>=0),
  result TEXT NOT NULL CHECK(result IN ('no_exact_name_in_staging','name_candidate_not_verified')),
  assessed_at TEXT NOT NULL,
  PRIMARY KEY(run_sha256,card_no,provider),
  FOREIGN KEY(run_sha256,card_no) REFERENCES pilot_cards(run_sha256,card_no)
);
CREATE TRIGGER IF NOT EXISTS pilot_runs_no_update BEFORE UPDATE ON pilot_runs BEGIN SELECT RAISE(ABORT,'immutable pilot run'); END;
CREATE TRIGGER IF NOT EXISTS pilot_runs_no_delete BEFORE DELETE ON pilot_runs BEGIN SELECT RAISE(ABORT,'immutable pilot run'); END;
CREATE TRIGGER IF NOT EXISTS pilot_cards_no_update BEFORE UPDATE ON pilot_cards BEGIN SELECT RAISE(ABORT,'immutable pilot card'); END;
CREATE TRIGGER IF NOT EXISTS pilot_cards_no_delete BEFORE DELETE ON pilot_cards BEGIN SELECT RAISE(ABORT,'immutable pilot card'); END;
CREATE TRIGGER IF NOT EXISTS pilot_attempts_no_update BEFORE UPDATE ON pilot_lookup_attempts BEGIN SELECT RAISE(ABORT,'immutable lookup attempt'); END;
CREATE TRIGGER IF NOT EXISTS pilot_attempts_no_delete BEFORE DELETE ON pilot_lookup_attempts BEGIN SELECT RAISE(ABORT,'immutable lookup attempt'); END;
