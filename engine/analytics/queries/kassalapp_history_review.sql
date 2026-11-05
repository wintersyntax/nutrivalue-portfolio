-- PRIVATE provider-reported daily min history; review-only and NOT a shelf
-- verification, active offer or an independently verified product-value ranking.
SELECT
    r.ean,
    r.store_code,
    r.price_day,
    printf('%.2f', r.price_ore / 100.0) AS provider_daily_min_nok,
    s.aggregation,
    s.fetched_at AS response_fetched_at,
    r.store_latest_checked_at AS current_listing_checked_at_not_history_time,
    r.review_status
FROM kassalapp_history_rows AS r
JOIN kassalapp_history_snapshots AS s ON s.id = r.snapshot_id
ORDER BY r.price_day DESC, r.ean, r.store_code
LIMIT 100;
