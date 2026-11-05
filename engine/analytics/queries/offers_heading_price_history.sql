-- TASK-052: same chain + normalized heading seen in more than one week.
-- A shared heading is NOT proof of the same SKU: quantity_variants > 1
-- means the package changed and min/max prices are not comparable.
SELECT chain_name, heading_norm, weeks_seen, first_week, last_week,
       min_price_ore / 100.0 AS min_nok, max_price_ore / 100.0 AS max_nok,
       quantity_variants
FROM v_heading_history
WHERE weeks_seen > 1 AND quantity_variants = 1 AND min_price_ore <> max_price_ore
ORDER BY (max_price_ore - min_price_ore) * 1.0 / max_price_ore DESC
LIMIT 25;
