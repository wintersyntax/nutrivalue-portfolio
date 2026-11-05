-- TASK-020: read-only, PRIVATE source-only exception queue. Not store/price truth.
-- Latest snapshot PER retailer; do not cross-associate cards or infer EAN.
WITH kiwi_latest AS (
  SELECT sha256, fetched_at, source_url FROM kiwi_locked_snapshots
  ORDER BY fetched_at DESC, sha256 DESC LIMIT 1
), rema_latest AS (
  SELECT sha256, fetched_at, source_url FROM rema_locked_snapshots
  ORDER BY fetched_at DESC, sha256 DESC LIMIT 1
), source_cards AS (
  SELECT 'KIWI' AS retailer, c.snapshot_sha256, c.card_no, c.product_name,
         c.details_text, c.price_ore, c.price_basis, c.deposit_applies,
         c.package_quantity, c.package_unit, c.pack_count,
         c.unit_price_ore, c.unit_price_unit, c.unit_price_check,
         s.fetched_at, s.source_url
  FROM kiwi_locked_cards c JOIN kiwi_latest s ON c.snapshot_sha256=s.sha256
  WHERE c.review_state='source_only'
  UNION ALL
  SELECT 'REMA', c.snapshot_sha256, c.card_no, c.product_name,
         c.details_text, c.price_ore, c.price_basis, 0,
         c.package_quantity, c.package_unit, c.pack_count,
         c.unit_price_ore, c.unit_price_unit, 'unchecked',
         s.fetched_at, s.source_url
  FROM rema_locked_cards c JOIN rema_latest s ON c.snapshot_sha256=s.sha256
  WHERE c.review_state='source_only'
), classified AS (
  SELECT *,
      trim(
        CASE WHEN package_quantity IS NULL OR package_unit IS NULL THEN 'missing_package;' ELSE '' END ||
        CASE WHEN price_basis!='per_package' THEN 'unknown_price_basis;' ELSE '' END ||
        CASE WHEN deposit_applies=1 THEN 'deposit_condition;' ELSE '' END ||
        CASE WHEN pack_count IS NOT NULL AND pack_count>1 THEN 'multipack;' ELSE '' END ||
        CASE WHEN unit_price_check='mismatch' THEN 'source_unit_mismatch;' ELSE '' END ||
        CASE WHEN unit_price_ore IS NULL OR unit_price_unit IS NULL THEN 'no_source_unit_rate;' ELSE '' END ||
        CASE WHEN package_unit NOT IN ('g','kg','ml','l','liter','stk','tab') THEN 'unsupported_package_unit;' ELSE '' END,
        ';'
      ) AS exception_reasons,
      CASE WHEN lower(product_name) LIKE '%kylling%' OR lower(product_name) LIKE '%egg%'
            OR lower(product_name) LIKE '%laks%' OR lower(product_name) LIKE '%fisk%'
            OR lower(product_name) LIKE '%torsk%' OR lower(product_name) LIKE '%tunfisk%'
            OR lower(product_name) LIKE '%skyr%' OR lower(product_name) LIKE '%yoghurt%'
            OR lower(product_name) LIKE '%melk%' OR lower(product_name) LIKE '%ost%'
            OR lower(product_name) LIKE '%kjøtt%' OR lower(product_name) LIKE '%linser%'
            OR lower(product_name) LIKE '%bønner%' OR lower(product_name) LIKE '%tofu%'
            OR lower(product_name) LIKE '%protein%'
        THEN 'possible_food_by_name' ELSE 'unclassified_name' END AS food_hint
  FROM source_cards
)
SELECT retailer, snapshot_sha256, card_no, product_name, details_text,
       price_ore, price_basis, deposit_applies, package_quantity,
       package_unit, pack_count, unit_price_ore, unit_price_unit,
       unit_price_check, fetched_at, source_url, exception_reasons,
       food_hint, 'source_only' AS evidence_status
FROM classified
WHERE exception_reasons!=''
ORDER BY CASE food_hint WHEN 'possible_food_by_name' THEN 0 ELSE 1 END,
         CASE WHEN exception_reasons LIKE '%source_unit_mismatch%' THEN 0 ELSE 1 END,
         CASE WHEN exception_reasons LIKE '%missing_package%' THEN 0 ELSE 1 END,
         retailer, card_no, snapshot_sha256;
