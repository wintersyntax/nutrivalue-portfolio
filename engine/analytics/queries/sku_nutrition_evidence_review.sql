-- PRIVATE TASK-002 exact-EAN and branded-nutrition evidence review.
-- A verified identity/nutrition status NEVER means a verified retail price.
-- All provider prices and prices-bulk histories remain separate source_only.
WITH latest AS (
    SELECT r.*,
           ROW_NUMBER() OVER (PARTITION BY r.ean ORDER BY r.id DESC) AS rank_no
    FROM sku_nutrition_reviews AS r
),
staged AS (
    SELECT ean, COUNT(*) AS price_listings,
           GROUP_CONCAT(DISTINCT store_code) AS provider_stores
    FROM kassalapp_staging
    WHERE ean IS NOT NULL
    GROUP BY ean
)
SELECT s.ean,
       s.price_listings,
       s.provider_stores,
       COALESCE(l.canonical_name, '(no manufacturer review)') AS product,
       COALESCE(l.identity_status, 'needs_review') AS identity_status,
       COALESCE(l.nutrition_status, 'needs_review') AS nutrition_status,
       COALESCE(l.overall_status, 'needs_review') AS overall_status,
       COALESCE(l.price_status, 'source_only') AS price_status,
       l.net_weight_g,
       l.protein_per_100g,
       CASE WHEN l.overall_status IN ('verified', 'verified_with_warning')
            THEN ROUND(l.net_weight_g * l.protein_per_100g / 100.0, 2)
            ELSE NULL END AS manufacturer_pack_protein_g_not_price_rank,
       l.source_url,
       l.evidence_checked_on,
       l.reasons_json
FROM staged AS s
LEFT JOIN latest AS l ON l.ean = s.ean AND l.rank_no = 1
ORDER BY CASE WHEN l.overall_status = 'verified' THEN 0
              WHEN l.overall_status = 'verified_with_warning' THEN 1
              ELSE 2 END, s.ean;
