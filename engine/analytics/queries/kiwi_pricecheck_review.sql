-- Review provider-reported prices, not a current verified store ranking.
-- Expires by the retailer's explicit "Gjelder til og med" date.
SELECT
    s.iso_year || '-W' || printf('%02d', s.iso_week) AS retailer_week,
    s.valid_through AS advertised_through,
    CASE WHEN date(s.valid_through) >= date('now')
         THEN 'within_advertised_week' ELSE 'expired' END AS date_state,
    p.product_text,
    printf('%.2f', p.price_cents / 100.0) AS advertised_price_nok,
    p.price_basis,
    CASE WHEN p.candidate_protein = 1 THEN 'possible_protein_food'
         ELSE 'other' END AS category_hint,
    p.review_state,
    s.source_url
FROM weekly_pricecheck_candidates p
JOIN weekly_pricecheck_snapshots s ON s.sha256 = p.snapshot_sha256
WHERE p.candidate_protein = 1
  AND s.fetched_at = (
      SELECT MAX(s2.fetched_at) FROM weekly_pricecheck_snapshots s2
  )
ORDER BY p.price_basis, p.price_cents, p.product_text;
