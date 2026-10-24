-- Observed source-structure diagnostics, not protein/value ranking.
-- Only the latest private KIWI snapshot by retrieval timestamp.
WITH latest AS (
  SELECT sha256, iso_year, iso_week, valid_through, fetched_at, source_url
  FROM weekly_pricecheck_snapshots
  ORDER BY fetched_at DESC, sha256 DESC
  LIMIT 1
)
SELECT
  s.iso_year || '-W' || printf('%02d', s.iso_week) AS source_week,
  s.valid_through AS advertised_through,
  CASE WHEN date('now') <= date(s.valid_through)
       THEN 'within_advertised_week' ELSE 'expired' END AS current_date_state,
  COUNT(*) AS source_rows,
  SUM(CASE WHEN p.candidate_protein = 1 THEN 1 ELSE 0 END)
    AS protein_name_candidates,
  SUM(CASE WHEN p.price_basis = 'per_package' THEN 1 ELSE 0 END)
    AS package_price_rows,
  SUM(CASE WHEN p.price_basis = 'per_kg' THEN 1 ELSE 0 END)
    AS per_kg_price_rows,
  SUM(CASE WHEN p.price_basis = 'unknown' THEN 1 ELSE 0 END)
    AS unknown_price_basis_rows,
  SUM(CASE WHEN p.review_state = 'source_only' THEN 1 ELSE 0 END)
    AS source_only_rows,
  s.fetched_at,
  s.source_url
FROM latest AS s
JOIN weekly_pricecheck_candidates AS p
  ON p.snapshot_sha256 = s.sha256
GROUP BY s.sha256, s.iso_year, s.iso_week, s.valid_through,
         s.fetched_at, s.source_url;
