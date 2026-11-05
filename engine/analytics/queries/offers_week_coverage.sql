-- TASK-052: per-week, per-chain coverage of the selected collector run.
-- declared = provider catalogue offer_count; listing = /v2/offers rows;
-- recovered = hotspot offers fetched by ID (TASK-051).
SELECT week, chain_name, declared_offers, listing_offers, detail_recovered,
       offers_saved, coverage_pct, status
FROM v_week_coverage
ORDER BY week DESC, coverage_pct;
