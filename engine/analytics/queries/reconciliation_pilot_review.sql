-- Private real-card pilot review ONLY; excludes verified SKU and shelf price.
-- Run against the separate data/private/reconciliation-pilot.sqlite3 DB.
SELECT
  r.rema_snapshot_sha256,
  r.rema_fetched_at,
  r.kassalapp_staging_rows_seen,
  c.card_no,
  c.product_name,
  printf('%.2f', c.price_ore / 100.0) AS advertised_nok_source_only,
  c.package_quantity,
  c.package_unit,
  c.derived_unit_price_ore,
  c.derived_unit_status,
  a.result AS existing_staging_lookup,
  a.matching_rows AS exact_name_staging_candidates,
  c.same_visible_fields_card_numbers_json AS other_visible_same_cards_not_auto_merged,
  c.reasons_json,
  c.result AS reconciliation_status
FROM pilot_runs AS r
JOIN pilot_cards AS c ON c.run_sha256=r.run_sha256
JOIN pilot_lookup_attempts AS a
  ON a.run_sha256=c.run_sha256 AND a.card_no=c.card_no
WHERE r.run_sha256=(
  SELECT run_sha256 FROM pilot_runs ORDER BY assessed_at DESC,run_sha256 DESC LIMIT 1
)
ORDER BY c.card_no;
