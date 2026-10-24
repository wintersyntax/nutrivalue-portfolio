-- TASK-055: PRIVATE weekly page-promotion extraction on top of TASK-052.
-- Load analytics/tjek_offer_staging.sql and analytics/offer_database.sql
-- first. Two independent vision models read every catalogue page. status:
-- accepted (both report it identically), standing (a known permanent loyalty
-- benefit, data/reference/standing_benefits.json), crosscheck_accepted (one
-- model reported it; the other confirmed it, rejected a decoy and OCR saw
-- the badge), refuted (one model reported it; the other said it is not
-- printed, rejected a decoy, and OCR found no such badge), superseded (the
-- other members-only version of a settled item) or needs_review. A promotion is never a price and never rewrites an offer.
-- Dates always come from the catalogue. Append-only.
PRAGMA foreign_keys=ON;

-- One extraction of one collector run with one prompt version and model pair.
CREATE TABLE IF NOT EXISTS promo_extractions (
  extraction_id TEXT PRIMARY KEY,          -- run_id|prompt_version|model_a+model_b
  run_id TEXT NOT NULL REFERENCES tjek_runs(run_id),
  prompt_version TEXT NOT NULL,
  model_a TEXT NOT NULL,
  model_b TEXT NOT NULL CHECK(model_b <> model_a),
  pages INTEGER NOT NULL CHECK(pages>0),
  invalid_pages_a INTEGER NOT NULL CHECK(invalid_pages_a>=0),
  invalid_pages_b INTEGER NOT NULL CHECK(invalid_pages_b>=0),
  item_errors INTEGER NOT NULL CHECK(item_errors>=0),
  agreed_items INTEGER NOT NULL CHECK(agreed_items>=0),
  disagreeing_items INTEGER NOT NULL CHECK(disagreeing_items>=0),
  agreement_rate REAL CHECK(agreement_rate BETWEEN 0 AND 1),
  ocr_engine TEXT,
  ocr_checked_agreed INTEGER NOT NULL CHECK(ocr_checked_agreed>=0),
  ocr_confirmed_agreed INTEGER NOT NULL CHECK(ocr_confirmed_agreed>=0),
  ocr_confirmed_share REAL CHECK(ocr_confirmed_share BETWEEN 0 AND 1),
  review_queue_size INTEGER NOT NULL CHECK(review_queue_size>=0),
  cost_usd TEXT NOT NULL,
  providers TEXT NOT NULL,                 -- JSON {model: {provider: calls}}
  over_price_calls INTEGER NOT NULL CHECK(over_price_calls>=0),
  results_sha256 TEXT NOT NULL CHECK(length(results_sha256)=64),
  standing_items INTEGER NOT NULL CHECK(standing_items>=0),
  crosscheck_calls INTEGER NOT NULL CHECK(crosscheck_calls>=0),
  crosscheck_accepted INTEGER NOT NULL CHECK(crosscheck_accepted>=0),
  superseded_items INTEGER NOT NULL CHECK(superseded_items>=0),
  refuted_items INTEGER NOT NULL CHECK(refuted_items>=0),
  retried_answers INTEGER NOT NULL CHECK(retried_answers>=0),
  imported_at TEXT NOT NULL
);

-- Every page of the run that was read, with both page classes.
CREATE TABLE IF NOT EXISTS promo_pages (
  extraction_id TEXT NOT NULL REFERENCES promo_extractions(extraction_id),
  catalog_id TEXT NOT NULL,
  page INTEGER NOT NULL CHECK(page>0),
  dealer_id TEXT NOT NULL,
  chain_name TEXT NOT NULL,
  image_sha256 TEXT NOT NULL CHECK(length(image_sha256)=64),
  has_hotspots INTEGER NOT NULL CHECK(has_hotspots IN (0,1)),
  valid_from TEXT NOT NULL,                -- from the catalogue, never the model
  valid_to TEXT NOT NULL,
  page_class_a TEXT,                       -- NULL = no valid answer
  page_class_b TEXT,
  page_status TEXT NOT NULL CHECK(page_status IN ('ok','needs_review')),
  PRIMARY KEY(extraction_id,catalog_id,page)
);

-- The raw answer of each model for each page (evidence, re-scorable).
CREATE TABLE IF NOT EXISTS promo_answers (
  extraction_id TEXT NOT NULL,
  catalog_id TEXT NOT NULL,
  page INTEGER NOT NULL,
  model TEXT NOT NULL,
  provider TEXT,
  cost_usd TEXT,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  prompt_sha256 TEXT,
  content TEXT,
  validation_error TEXT,
  item_errors TEXT NOT NULL,               -- JSON list
  PRIMARY KEY(extraction_id,catalog_id,page,model),
  FOREIGN KEY(extraction_id,catalog_id,page) REFERENCES promo_pages(extraction_id,catalog_id,page)
);

-- One promotion per row; accepted only when both models agree.
CREATE TABLE IF NOT EXISTS page_promotions (
  extraction_id TEXT NOT NULL,
  catalog_id TEXT NOT NULL,
  page INTEGER NOT NULL,
  item_no INTEGER NOT NULL CHECK(item_no>=1),
  kind TEXT NOT NULL CHECK(kind IN ('n_for_m','percent_off','amount_off','bonus_percent')),
  n INTEGER, m INTEGER,
  percent REAL,
  amount_ore INTEGER,
  member_only INTEGER NOT NULL CHECK(member_only IN (0,1)),
  scope TEXT NOT NULL,
  scope_other TEXT,                        -- the other model's scope when both reported it
  status TEXT NOT NULL CHECK(status IN ('accepted','standing','crosscheck_accepted',
                                       'superseded','refuted','needs_review')),
  reported_by TEXT NOT NULL,               -- JSON list of models
  ocr_status TEXT CHECK(ocr_status IN ('confirmed','number_only','absent')),
  method TEXT NOT NULL,                    -- vlm-pair:<prompt_version>
  standing_id TEXT,                        -- registry entry for status 'standing'
  crosscheck TEXT,                         -- JSON outcome of the cross-check, if any
  PRIMARY KEY(extraction_id,catalog_id,page,item_no),
  FOREIGN KEY(extraction_id,catalog_id,page) REFERENCES promo_pages(extraction_id,catalog_id,page),
  CHECK((kind='n_for_m' AND n>m AND m>=1 AND percent IS NULL AND amount_ore IS NULL)
     OR (kind IN ('percent_off','bonus_percent') AND percent>0 AND percent<=100
         AND n IS NULL AND m IS NULL AND amount_ore IS NULL)
     OR (kind='amount_off' AND amount_ore>0 AND n IS NULL AND m IS NULL AND percent IS NULL))
);

-- A person's decisions on the review queue. Append-only: a later decision
-- on the same item supersedes an earlier one; nothing is ever deleted.
CREATE TABLE IF NOT EXISTS promo_reviews (
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  extraction_id TEXT NOT NULL,
  catalog_id TEXT NOT NULL,
  page INTEGER NOT NULL,
  item_no INTEGER,                         -- NULL = decision about the whole page
  decision TEXT NOT NULL CHECK(decision IN ('accept','reject','page_checked')),
  note TEXT,
  reviewed_at TEXT NOT NULL,
  CHECK((item_no IS NULL) = (decision = 'page_checked')),
  FOREIGN KEY(extraction_id,catalog_id,page) REFERENCES promo_pages(extraction_id,catalog_id,page),
  FOREIGN KEY(extraction_id,catalog_id,page,item_no)
    REFERENCES page_promotions(extraction_id,catalog_id,page,item_no)
);
CREATE TRIGGER IF NOT EXISTS promo_reviews_no_update
  BEFORE UPDATE ON promo_reviews BEGIN SELECT RAISE(ABORT,'immutable review'); END;
CREATE TRIGGER IF NOT EXISTS promo_reviews_no_delete
  BEFORE DELETE ON promo_reviews BEGIN SELECT RAISE(ABORT,'immutable review'); END;

-- TASK-058: decisions from the hosted review page arrive as create-only
-- objects in the private bucket (reviews/...) and are replayed here exactly
-- once; an invalid decision is logged and never applied.
CREATE TABLE IF NOT EXISTS promo_review_log (
  decision_id TEXT PRIMARY KEY CHECK(length(decision_id)=32),
  review_id INTEGER REFERENCES promo_reviews(review_id),
  reviewer TEXT,
  outcome TEXT NOT NULL CHECK(outcome IN ('applied','invalid')),
  error TEXT,
  replayed_at TEXT NOT NULL,
  CHECK((outcome = 'applied') = (review_id IS NOT NULL))
);
CREATE TRIGGER IF NOT EXISTS promo_review_log_no_update
  BEFORE UPDATE ON promo_review_log BEGIN SELECT RAISE(ABORT,'immutable review log'); END;
CREATE TRIGGER IF NOT EXISTS promo_review_log_no_delete
  BEFORE DELETE ON promo_review_log BEGIN SELECT RAISE(ABORT,'immutable review log'); END;

-- Latest decision per item / per page.
DROP VIEW IF EXISTS v_promo_latest_review;
CREATE VIEW v_promo_latest_review AS
SELECT extraction_id, catalog_id, page, item_no, decision, note, reviewed_at
FROM (
  SELECT r.*, ROW_NUMBER() OVER (PARTITION BY extraction_id, catalog_id, page, item_no
                                 ORDER BY review_id DESC) AS rn
  FROM promo_reviews r
)
WHERE rn = 1;

CREATE TRIGGER IF NOT EXISTS promo_extractions_no_update
  BEFORE UPDATE ON promo_extractions BEGIN SELECT RAISE(ABORT,'immutable extraction'); END;
CREATE TRIGGER IF NOT EXISTS promo_extractions_no_delete
  BEFORE DELETE ON promo_extractions BEGIN SELECT RAISE(ABORT,'immutable extraction'); END;
CREATE TRIGGER IF NOT EXISTS promo_pages_no_update
  BEFORE UPDATE ON promo_pages BEGIN SELECT RAISE(ABORT,'immutable promo page'); END;
CREATE TRIGGER IF NOT EXISTS promo_pages_no_delete
  BEFORE DELETE ON promo_pages BEGIN SELECT RAISE(ABORT,'immutable promo page'); END;
CREATE TRIGGER IF NOT EXISTS promo_answers_no_update
  BEFORE UPDATE ON promo_answers BEGIN SELECT RAISE(ABORT,'immutable answer'); END;
CREATE TRIGGER IF NOT EXISTS promo_answers_no_delete
  BEFORE DELETE ON promo_answers BEGIN SELECT RAISE(ABORT,'immutable answer'); END;
CREATE TRIGGER IF NOT EXISTS page_promotions_no_update
  BEFORE UPDATE ON page_promotions BEGIN SELECT RAISE(ABORT,'immutable promotion'); END;
CREATE TRIGGER IF NOT EXISTS page_promotions_no_delete
  BEFORE DELETE ON page_promotions BEGIN SELECT RAISE(ABORT,'immutable promotion'); END;

-- The latest extraction of each week's selected run.
DROP VIEW IF EXISTS v_week_extraction;
CREATE VIEW v_week_extraction AS
SELECT week, run_id, extraction_id, prompt_version, model_a, model_b, imported_at
FROM (
  SELECT w.week, e.*, ROW_NUMBER() OVER (PARTITION BY w.week
                                         ORDER BY e.imported_at DESC, e.extraction_id DESC) AS rn
  FROM v_week_run w JOIN promo_extractions e ON e.run_id = w.run_id
)
WHERE rn = 1;

-- Promotions of each week's latest extraction with chain and validity.
-- final_status: accepted (both models) | accepted_by_crosscheck | standing |
-- superseded | rejected_by_crosscheck | accepted_by_review | rejected (the
-- owner's latest decision, also overriding an agreed item) | needs_review.
DROP VIEW IF EXISTS v_week_promotions;
CREATE VIEW v_week_promotions AS
SELECT x.week, p.chain_name, p.dealer_id, q.catalog_id, q.page, q.item_no, q.kind, q.n, q.m,
       q.percent, q.amount_ore, q.member_only, q.scope, q.scope_other, q.status,
       r.decision AS review_decision, r.note AS review_note,
       CASE WHEN r.decision = 'reject' THEN 'rejected'     -- also a spot-check of an agreed item
            WHEN q.status = 'accepted' THEN 'accepted'
            WHEN q.status = 'crosscheck_accepted' THEN 'accepted_by_crosscheck'
            WHEN q.status = 'standing' THEN 'standing'
            WHEN r.decision = 'accept' THEN 'accepted_by_review'
            WHEN q.status = 'superseded' THEN 'superseded'
            WHEN q.status = 'refuted' THEN 'rejected_by_crosscheck'
            ELSE 'needs_review' END AS final_status,
       q.standing_id, q.crosscheck,
       q.reported_by, q.ocr_status, p.valid_from, p.valid_to, p.has_hotspots, q.method,
       x.extraction_id
FROM v_week_extraction x
JOIN page_promotions q ON q.extraction_id = x.extraction_id
JOIN promo_pages p ON p.extraction_id = q.extraction_id AND p.catalog_id = q.catalog_id
                  AND p.page = q.page
LEFT JOIN v_promo_latest_review r
  ON r.extraction_id = q.extraction_id AND r.catalog_id = q.catalog_id AND r.page = q.page
 AND r.item_no = q.item_no;

-- What a person still has to look at for each week's latest extraction.
DROP VIEW IF EXISTS v_week_promotion_review;
CREATE VIEW v_week_promotion_review AS
SELECT x.week, p.chain_name, p.catalog_id, p.page, 'page' AS item, NULL AS item_no,
       'no valid answer from both models' AS reason, x.extraction_id
FROM v_week_extraction x
JOIN promo_pages p ON p.extraction_id = x.extraction_id
LEFT JOIN v_promo_latest_review r
  ON r.extraction_id = p.extraction_id AND r.catalog_id = p.catalog_id AND r.page = p.page
 AND r.item_no IS NULL
WHERE p.page_status = 'needs_review' AND r.decision IS NULL
UNION ALL
SELECT week, chain_name, catalog_id, page, 'promotion', item_no,
       'reported by ' || reported_by || ' only', extraction_id
FROM v_week_promotions WHERE final_status = 'needs_review';

-- Health of each extraction (one row per extraction).
DROP VIEW IF EXISTS v_promotion_monitoring;
CREATE VIEW v_promotion_monitoring AS
SELECT r.week, e.run_id, e.extraction_id, e.pages, e.agreement_rate, e.ocr_confirmed_share,
       e.review_queue_size, e.standing_items, e.crosscheck_calls, e.crosscheck_accepted,
       e.superseded_items, e.refuted_items, e.retried_answers, e.invalid_pages_a, e.invalid_pages_b,
       e.item_errors, e.over_price_calls, e.cost_usd, e.imported_at
FROM promo_extractions e JOIN tjek_runs r ON r.run_id = e.run_id;
