# Changelog

Chronological engineering milestones through **31 October 2026**, recorded by implementation/merge date. The public engineering code snapshot was curated on 23 October; updates on 25 October align public descriptions with the accepted private documentation. Earlier entries describe engineering history, **not** separately replayed Git commits or externally available datasets.

## 31 October 2026 — Selective public sync and privacy guard

- Add an explicitly configured GET-only Mealie recipe reader, local snapshot schemas and offline nutrition estimates with synthetic tests; remove the private instance default.
- Publish per-chain weekly offer/promotion fallback and health SQL, invalid-offer isolation and quarantine-aware page selection.
- Strengthen publication checks for unreviewed files, datasets, symlinks, hosted instance URLs and personal addresses; keep the synthetic screenshot hash unchanged.
- Update PikaPods/Mealie architecture boundaries without publishing personal recipes, stock records, infrastructure or provider dumps.

## 27 October 2026 — Taxonomy v6 and W44 logic (public subset)

- Expanded the public offline classifier to taxonomy v6, with 140 generic food definitions and append-only category migration.
- Added scope-containment deduplication for n-for-m promotions and equivalent percentage restatements.
- Public regression examples are synthetic. Real W44 catalogue captures, model results and owner review records are not included.

## 27 October 2026 — Public offline product candidate evidence

- Publish selected offline-only TASK-069/TASK-070-style candidate comparison logic. It normalizes supplied provider-shaped examples, compares pack/brand/name matches, and flags nutrition discrepancies without provider calls or private result files.
- Add fictional regression tests and run them in public CI.
- Clarify that the included review-interface screenshot is a synthetic demonstration of implemented reviewer controls.

## 25 October 2026 — NutriValue identity and documentation alignment

- Confirmed NutriValue as the sole product name; old terminology did **not** describe a second application or module.
- Refined the accepted private product roadmap: versioned recipes and deterministic 3+4-day proposals come first; Grocy inventory snapshots, owner-approved Mealie handoff and any advanced optimizer remain future work.
- Updated the public README, engineering status, architecture and product vision to match these **future versus deployed** boundaries without copying private Nutrition workflows, owner records, source payloads or infrastructure secrets.
- Verified that ten representative public engine/reference/SQL files were byte-identical to the corresponding accepted private source at the time of this documentation update; this is a **targeted comparison**, not a full repository audit.
- Public-history presentation is maintained as a deliberately curated standalone portfolio snapshot; a root-commit reset changes reachable branch history, **not** all GitHub caches, forks, pull-request references or author metadata.

## 23 October 2026 — Public source release

- Rebuild the public portfolio around **actual NutriValue code**: 62 engine source modules, 33 SQL files, 45 CLI scripts, 66 engineering test files, 32 OCR research programs/tests and the curated generic food-role reference.
- Preserve the six-report, six-test **synthetic runnable demo**, entirely separate from provider and personal data.
- Document the full data-to-decision architecture: source provenance, bounded collection, OCR/model extraction, offer interpretation, human review, weekly SQL, product/nutrition evidence and eligibility gates.
- Publish the offer-review implementation and a detailed walkthrough of original-page evidence, two-model responses and **accept / reject / page-checked / skip** decisions.
- Add a public-source tour, a limited offline privacy/release scanner and GitHub Actions for compilation, synthetic demo tests and selected portable engine contracts.
- Rewrite README, development status and product vision as an **actual engineering portfolio**, without claiming a complete independent production runtime or working meal planner.
- Keep branch history at one `Initialize` root commit as requested. **Branch reset is not a guarantee of GitHub object/cache deletion, and author metadata is separate.**

## 22 October 2026 — Review service and operational resilience

- **TASK-058:** Implemented the authenticated review-service approach, create-only decision objects and idempotent reviewer-decision replay.
- **TASK-054:** Added a Mac-to-Google-Drive page-image copy/alert workflow.
- **TASK-059:** Changed image transfer to avoid re-uploading unchanged page images and retain prior references.

## 20 October 2026 — Promotion extraction and cloud orchestration

- **TASK-053:** Implemented a bounded page-promotion model benchmark with structured outputs, source evidence and explicit cost accounting.
- **TASK-056:** Hardened model routing, prompt/version validation, monitored page sets and safeguards.
- **TASK-055:** Added page-image promotion extraction, independent model comparison, printed-promotion conditions, human review and corresponding analytics.
- **TASK-024:** Developed a scheduled weekly cloud-job and private object-storage workflow. Owner-specific infrastructure assets are excluded from the public snapshot.

## 19 October 2026 — Weekly catalogue history and REMA experiments

- **TASK-051:** Recovered catalogue hotspot offers and bounded page-image capture, with hashes and coverage checks. A W43 run reported **883 selected source-only offers and 213 catalogue pages**, not verified checkout prices.
- **TASK-052:** Added hash-validated append-only weekly SQLite imports with run identity, idempotence, coverage and cross-week queries.
- **TASK-047 / TASK-049:** Preserved review-only REMA W40–W43 OCR/layout and historical price-plaque experiments. Printed-price accuracy remains insufficient for production.

## 17 October 2026 — Norwegian offer classification

- **TASK-041:** Expanded food categories, overlapping item tags, assortments, bundles and explicit unresolved statuses.
- Reviewed 863 archived headings in a bounded classification exercise; 845 outcomes were supported by the chosen evidence and 18 remained unresolved. These figures are **not** independent accuracy or overall retail coverage measures.

## 16 October 2026 — Identity and source conditions

- Extended source-only Tjek campaign/standing-benefit interpretation, bounded secondary-evidence reviews and product-identifier diagnostics.
- Strengthened GTIN validation and reviewed a limited five-card REMA reconciliation exercise.
- Clarified boundaries for potential recipe/MCP integrations.

## 15 October 2026 — Offline evidence and reproducibility

- Added bounded nine-chain Tjek collection groundwork, a read-only offer preview, offline weekly evidence audits and CI/PR acceptance refinements.

## 11–12 October 2026 — Retail and nutrition evidence pilots

- Added offline saved Tjek JSON/NDJSON import and immutable source-only staging.
- Extended KIWI/REMA/Coop/MENY/Kassalapp pilots, source-family reconciliation and a handful of reviewed manufacturer/SKU nutrition records.
- Published the first small synthetic SQL/Python portfolio and documentation.

## 10 October 2026 — Foundations

- Built the normalized SQLite retail/nutrition schema, Decimal NOK-per-protein model, synthetic generator, six SQL reports and six deterministic offline tests.
- Introduced a Matvaretabellen reference-food import, explicit nutrition measurement bases and evidence-gated verified-pricing rules.
- Began building the generic food-role classification and explainable substitution vocabulary.

---

**Interpreting these entries:** Data source observations, nutrition reference measurements, human catalogue reviews, indicative calculations and independently checked retail prices are different evidence levels. Engineering dates do not establish source rights, source-completeness guarantees, accurate printed prices, healthy cloud deployment or live availability of store offers.
