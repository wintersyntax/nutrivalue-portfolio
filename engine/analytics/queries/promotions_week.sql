-- TASK-055: promotions of each week's latest extraction.
-- final_status: accepted = both vision models reported the same kind,
-- numbers and member flag; accepted_by_crosscheck / standing / superseded =
-- decided by the pipeline (see analytics/promotion_extraction.sql); accepted_by_review / rejected = the owner's
-- decision (scripts/review_promotions.py); needs_review = still open. Validity is the catalogue's, never the
-- model's. A promotion is not a price and never changes an offer row.
SELECT week, chain_name, catalog_id, page, kind, n, m, percent,
       amount_ore / 100.0 AS amount_nok, member_only, scope, final_status, ocr_status,
       valid_from, valid_to
FROM v_week_promotions
ORDER BY week DESC, final_status, chain_name, catalog_id, page, item_no;
