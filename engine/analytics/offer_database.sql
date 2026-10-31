-- TASK-052: PRIVATE weekly offer database on top of TASK-023 staging.
-- Load analytics/tjek_offer_staging.sql first. Everything here is
-- append-only, source_only and chain-level: never a checked shelf price.
PRAGMA foreign_keys=ON;

-- One saved TASK-032/TASK-051 collector run directory.
CREATE TABLE IF NOT EXISTS tjek_runs (
  run_id TEXT PRIMARY KEY,                 -- e.g. 2026-W43/run-20261019T225318Z
  week TEXT NOT NULL CHECK(week GLOB '[0-9][0-9][0-9][0-9]-W[0-9][0-9]'),
  fetched_at TEXT NOT NULL,
  as_of TEXT NOT NULL,
  manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64),
  batch_status TEXT NOT NULL,
  halted_reason TEXT,
  http_requests INTEGER NOT NULL CHECK(http_requests>=0),
  hotspots_enabled INTEGER NOT NULL CHECK(hotspots_enabled IN (0,1)),
  total_saved_offers INTEGER NOT NULL CHECK(total_saved_offers>=0),
  imported_at TEXT NOT NULL
);

-- Per-chain coverage for one run; declared = sum of catalogue offer_count.
CREATE TABLE IF NOT EXISTS tjek_run_chains (
  run_id TEXT NOT NULL REFERENCES tjek_runs(run_id),
  dealer_id TEXT NOT NULL,
  chain_name TEXT NOT NULL,
  status TEXT NOT NULL,
  catalogs_selected INTEGER NOT NULL CHECK(catalogs_selected>=0),
  declared_offers INTEGER CHECK(declared_offers>=0),
  offers_saved INTEGER NOT NULL CHECK(offers_saved>=0),
  listing_offers INTEGER CHECK(listing_offers>=0),
  hotspot_offers INTEGER CHECK(hotspot_offers>=0),
  detail_recovered INTEGER CHECK(detail_recovered>=0),
  source_count_shortfall INTEGER NOT NULL CHECK(source_count_shortfall>=0),
  snapshot_sha256 TEXT REFERENCES tjek_snapshots(sha256),
  PRIMARY KEY(run_id,dealer_id)
);

-- How each staged offer was obtained and where it is printed.
CREATE TABLE IF NOT EXISTS tjek_offer_acquisition (
  snapshot_sha256 TEXT NOT NULL,
  catalog_id TEXT NOT NULL,
  offer_id TEXT NOT NULL,
  acquisition TEXT NOT NULL CHECK(acquisition IN ('listing','hotspot_detail')),
  hotspot_pages TEXT,                      -- JSON list of page numbers, NULL if unknown
  hotspot_locations TEXT,                  -- JSON provider page->polygon map, NULL if unknown
  PRIMARY KEY(snapshot_sha256,catalog_id,offer_id),
  FOREIGN KEY(snapshot_sha256,catalog_id,offer_id)
    REFERENCES tjek_offers(snapshot_sha256,catalog_id,offer_id)
);

-- TASK-033 interpretation of the ORIGINAL offer text (versioned).
CREATE TABLE IF NOT EXISTS tjek_offer_conditions (
  snapshot_sha256 TEXT NOT NULL,
  catalog_id TEXT NOT NULL,
  offer_id TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  price_kind TEXT NOT NULL,
  explicit_member_ore INTEGER CHECK(explicit_member_ore>0),
  conditional_member_ore INTEGER CHECK(conditional_member_ore>0),
  review_only_member_ore INTEGER CHECK(review_only_member_ore>0),
  explicit_nonmember_ore INTEGER CHECK(explicit_nonmember_ore>0),
  explicit_previous_ore INTEGER CHECK(explicit_previous_ore>0),
  saving_ore INTEGER CHECK(saving_ore>0),
  bundle_units INTEGER CHECK(bundle_units>1),
  bundle_total_ore INTEGER CHECK(bundle_total_ore>0),
  bundle_member_only INTEGER NOT NULL CHECK(bundle_member_only IN (0,1)),
  percent_condition_count INTEGER NOT NULL CHECK(percent_condition_count>=0),
  pant_mentioned INTEGER NOT NULL CHECK(pant_mentioned IN (0,1)),
  limited_stock INTEGER NOT NULL CHECK(limited_stock IN (0,1)),
  review_reasons TEXT NOT NULL,            -- JSON list
  PRIMARY KEY(snapshot_sha256,catalog_id,offer_id,parser_version),
  FOREIGN KEY(snapshot_sha256,catalog_id,offer_id)
    REFERENCES tjek_offers(snapshot_sha256,catalog_id,offer_id)
);

-- TASK-041 broad category tags (versioned); never a nutrition match.
CREATE TABLE IF NOT EXISTS tjek_offer_categories (
  snapshot_sha256 TEXT NOT NULL,
  catalog_id TEXT NOT NULL,
  offer_id TEXT NOT NULL,
  taxonomy_version INTEGER NOT NULL,
  status TEXT NOT NULL,
  food_category TEXT,
  food_key TEXT,
  form TEXT NOT NULL,
  reason TEXT NOT NULL,
  category_tags TEXT NOT NULL,             -- JSON list
  offer_kind TEXT NOT NULL,
  tag_status TEXT NOT NULL,
  PRIMARY KEY(snapshot_sha256,catalog_id,offer_id,taxonomy_version),
  FOREIGN KEY(snapshot_sha256,catalog_id,offer_id)
    REFERENCES tjek_offers(snapshot_sha256,catalog_id,offer_id)
);

CREATE TRIGGER IF NOT EXISTS tjek_runs_no_update
  BEFORE UPDATE ON tjek_runs BEGIN SELECT RAISE(ABORT,'immutable run'); END;
CREATE TRIGGER IF NOT EXISTS tjek_runs_no_delete
  BEFORE DELETE ON tjek_runs BEGIN SELECT RAISE(ABORT,'immutable run'); END;
CREATE TRIGGER IF NOT EXISTS tjek_run_chains_no_update
  BEFORE UPDATE ON tjek_run_chains BEGIN SELECT RAISE(ABORT,'immutable run chain'); END;
CREATE TRIGGER IF NOT EXISTS tjek_run_chains_no_delete
  BEFORE DELETE ON tjek_run_chains BEGIN SELECT RAISE(ABORT,'immutable run chain'); END;
CREATE TRIGGER IF NOT EXISTS tjek_offer_acquisition_no_update
  BEFORE UPDATE ON tjek_offer_acquisition BEGIN SELECT RAISE(ABORT,'immutable acquisition'); END;
CREATE TRIGGER IF NOT EXISTS tjek_offer_acquisition_no_delete
  BEFORE DELETE ON tjek_offer_acquisition BEGIN SELECT RAISE(ABORT,'immutable acquisition'); END;
CREATE TRIGGER IF NOT EXISTS tjek_offer_conditions_no_update
  BEFORE UPDATE ON tjek_offer_conditions BEGIN SELECT RAISE(ABORT,'immutable conditions'); END;
CREATE TRIGGER IF NOT EXISTS tjek_offer_conditions_no_delete
  BEFORE DELETE ON tjek_offer_conditions BEGIN SELECT RAISE(ABORT,'immutable conditions'); END;
CREATE TRIGGER IF NOT EXISTS tjek_offer_categories_no_update
  BEFORE UPDATE ON tjek_offer_categories BEGIN SELECT RAISE(ABORT,'immutable categories'); END;
CREATE TRIGGER IF NOT EXISTS tjek_offer_categories_no_delete
  BEFORE DELETE ON tjek_offer_categories BEGIN SELECT RAISE(ABORT,'immutable categories'); END;

-- The week's selected run: the latest non-halted run that saved offers.
DROP VIEW IF EXISTS v_week_run;
CREATE VIEW v_week_run AS
SELECT week, run_id, fetched_at, total_saved_offers
FROM (
  SELECT r.*, ROW_NUMBER() OVER (PARTITION BY week ORDER BY fetched_at DESC, run_id DESC) AS rn
  FROM tjek_runs r
  WHERE halted_reason IS NULL AND total_saved_offers > 0
)
WHERE rn = 1;

-- TASK-079: per week and chain, the latest non-halted run in which that chain
-- saved offers. A later run that lost a chain (quarantine, HTTP error, no
-- catalogue) no longer hides the chain's earlier offers of the same week.
DROP VIEW IF EXISTS v_week_chain_run;
CREATE VIEW v_week_chain_run AS
SELECT week, dealer_id, chain_name, run_id, status, offers_saved, fetched_at
FROM (
  SELECT r.week, rc.dealer_id, rc.chain_name, rc.run_id, rc.status, rc.offers_saved,
         r.fetched_at,
         ROW_NUMBER() OVER (PARTITION BY r.week, rc.dealer_id
                            ORDER BY r.fetched_at DESC, r.run_id DESC) AS rn
  FROM tjek_runs r JOIN tjek_run_chains rc ON rc.run_id = r.run_id
  WHERE r.halted_reason IS NULL AND rc.offers_saved > 0 AND rc.snapshot_sha256 IS NOT NULL
)
WHERE rn = 1;

-- One row per offer of each week, per chain from v_week_chain_run, with
-- acquisition, latest-version conditions and categories.
DROP VIEW IF EXISTS v_week_offers;
CREATE VIEW v_week_offers AS
SELECT w.week, w.run_id, rc.chain_name, o.dealer_id, o.catalog_id, o.offer_id,
       o.heading, o.description, o.price_ore, o.pre_price_ore,
       o.quantity, o.unit, o.pack_count, o.unit_price_ore, o.unit_price_basis,
       o.unit_price_confidence, o.run_from, o.run_till,
       o.member_condition, o.multibuy_condition, o.deposit_condition,
       a.acquisition, a.hotspot_pages,
       c.price_kind, c.explicit_member_ore, c.conditional_member_ore,
       c.explicit_previous_ore, c.saving_ore, c.bundle_units, c.bundle_total_ore,
       c.bundle_member_only, c.percent_condition_count, c.pant_mentioned,
       c.limited_stock, c.review_reasons,
       k.food_category, k.food_key, k.category_tags, k.offer_kind, k.tag_status,
       o.review_state
FROM v_week_chain_run w
JOIN tjek_run_chains rc ON rc.run_id = w.run_id AND rc.dealer_id = w.dealer_id
JOIN tjek_offers o ON o.snapshot_sha256 = rc.snapshot_sha256
LEFT JOIN tjek_offer_acquisition a
  ON a.snapshot_sha256=o.snapshot_sha256 AND a.catalog_id=o.catalog_id AND a.offer_id=o.offer_id
LEFT JOIN tjek_offer_conditions c
  ON c.snapshot_sha256=o.snapshot_sha256 AND c.catalog_id=o.catalog_id AND c.offer_id=o.offer_id
 AND c.parser_version = (SELECT MAX(parser_version) FROM tjek_offer_conditions c2
                         WHERE c2.snapshot_sha256=o.snapshot_sha256 AND c2.catalog_id=o.catalog_id
                           AND c2.offer_id=o.offer_id)
LEFT JOIN tjek_offer_categories k
  ON k.snapshot_sha256=o.snapshot_sha256 AND k.catalog_id=o.catalog_id AND k.offer_id=o.offer_id
 AND k.taxonomy_version = (SELECT MAX(taxonomy_version) FROM tjek_offer_categories k2
                           WHERE k2.snapshot_sha256=o.snapshot_sha256 AND k2.catalog_id=o.catalog_id
                             AND k2.offer_id=o.offer_id);

-- Per-week, per-chain coverage of the run each chain's offers come from.
-- A chain with no saved offers in the week is absent here; see v_week_chain_health.
DROP VIEW IF EXISTS v_week_coverage;
CREATE VIEW v_week_coverage AS
SELECT w.week, rc.chain_name, rc.status, rc.declared_offers, rc.listing_offers,
       rc.hotspot_offers, rc.detail_recovered, rc.offers_saved, rc.source_count_shortfall,
       CASE WHEN rc.declared_offers > 0
            THEN ROUND(100.0 * rc.offers_saved / rc.declared_offers, 1) END AS coverage_pct
FROM v_week_chain_run w
JOIN tjek_run_chains rc ON rc.run_id = w.run_id AND rc.dealer_id = w.dealer_id;

-- The same provider offer ID across weeks. Tjek issues new offer IDs for
-- each catalogue, so this mainly links repeated runs and long-running offers.
DROP VIEW IF EXISTS v_offer_history;
CREATE VIEW v_offer_history AS
SELECT dealer_id, chain_name, offer_id,
       MIN(week) AS first_week, MAX(week) AS last_week, COUNT(DISTINCT week) AS weeks_seen,
       MIN(price_ore) AS min_price_ore, MAX(price_ore) AS max_price_ore,
       MAX(heading) AS heading
FROM v_week_offers
GROUP BY dealer_id, chain_name, offer_id;

-- Same chain + normalized heading across weeks: the price-history
-- foundation until reviewed product identity exists. A shared heading is
-- NOT proof of the same SKU/package; compare quantity before trends.
DROP VIEW IF EXISTS v_heading_history;
CREATE VIEW v_heading_history AS
SELECT chain_name, lower(trim(heading)) AS heading_norm,
       COUNT(DISTINCT week) AS weeks_seen, MIN(week) AS first_week, MAX(week) AS last_week,
       MIN(price_ore) AS min_price_ore, MAX(price_ore) AS max_price_ore,
       COUNT(DISTINCT quantity || '|' || COALESCE(unit,'')) AS quantity_variants
FROM v_week_offers
GROUP BY chain_name, lower(trim(heading));
