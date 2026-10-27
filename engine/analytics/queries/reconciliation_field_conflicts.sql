-- Private field-level disagreement. Same upstream family != independent votes.
-- A mismatch here is a REVIEW cue, not proof which value is correct.
WITH family_values AS (
 SELECT a.retailer,a.retailer_snapshot_sha256,a.retailer_card_no,
        a.candidate_ref,a.field_name,s.origin_family,
        a.asserted_value || coalesce(' ' || a.asserted_unit,'') AS field_value
 FROM recon_field_assertions a
 JOIN recon_sources s ON s.source_id=a.source_id
 WHERE a.status='source_only'
 GROUP BY a.retailer,a.retailer_snapshot_sha256,a.retailer_card_no,
          a.candidate_ref,a.field_name,s.origin_family,field_value
), grouped AS (
 SELECT retailer,retailer_snapshot_sha256,retailer_card_no,candidate_ref,field_name,
        count(DISTINCT origin_family) AS independent_families,
        count(DISTINCT field_value) AS distinct_values,
        group_concat(DISTINCT field_value) AS competing_values
 FROM family_values
 GROUP BY retailer,retailer_snapshot_sha256,retailer_card_no,candidate_ref,field_name
)
SELECT *, CASE WHEN distinct_values>1 THEN 'conflicting_evidence'
               ELSE 'candidate_only_not_verified' END AS reconciliation_status
FROM grouped
ORDER BY CASE WHEN distinct_values>1 THEN 0 ELSE 1 END,
         retailer,retailer_card_no,field_name,candidate_ref;
