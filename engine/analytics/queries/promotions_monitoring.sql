-- TASK-055: weekly health of the two-model promotion extraction.
-- Watch agreement_rate and ocr_confirmed_share drop, review_queue_size grow,
-- invalid pages or any over_price_calls.
SELECT week, pages, agreement_rate, ocr_confirmed_share, review_queue_size,
       standing_items, crosscheck_calls, crosscheck_accepted, superseded_items, retried_answers,
       invalid_pages_a, invalid_pages_b, item_errors, over_price_calls, cost_usd
FROM v_promotion_monitoring
ORDER BY week DESC, imported_at DESC;
