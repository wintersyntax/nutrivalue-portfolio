-- TASK-052: latest captured week's advertised offers for one broad category.
-- Change 'fish' to any TASK-041 category (meat, dairy, grain, produce, ...).
-- Chain-level advertised prices only (source_only), never a checked shelf price.
SELECT chain_name,
       heading,
       quantity || COALESCE(' ' || unit, '')      AS pack,
       price_ore / 100.0                          AS price_nok,
       unit_price_ore / 100.0                     AS nok_per_unit,
       unit_price_basis,
       acquisition,                                -- listing | hotspot_detail
       CASE WHEN member_condition = 1 THEN 'member' END AS member_flag,
       CASE WHEN bundle_units IS NOT NULL
            THEN bundle_units || ' for ' || bundle_total_ore / 100.0 END AS bundle,
       run_from, run_till
FROM v_week_offers
WHERE week = (SELECT MAX(week) FROM v_week_run)
  AND food_category = 'fish'
ORDER BY unit_price_ore IS NULL, unit_price_ore, price_ore;
