# Development status

**31 October 2026 — selective public engine and PikaPods architecture sync**

NutriValue is a code-focused Norwegian grocery intelligence portfolio. This repository now contains the actual Python engine source, SQL, review interface, import/review CLI scripts, tests and historical research experiments. The project is **more than the small standalone demo** at the root.

## What is in the repository

| Layer | Public source today | Run status |
| --- | --- | --- |
| Offer/pilot adapters | `engine/src/nutrivalue/providers/` | Needs permitted live sources or separately provided synthetic inputs |
| Weekly source capture | `engine/src/nutrivalue/providers/tjek_weekly_collect.py` | Real-world capture is not exercised by the public demo |
| Offer category/condition logic | `engine/src/nutrivalue/offer_categories.py` and provider helpers | Testable logic; missing evidence stays unresolved |
| OCR/page experiments | `engine/src/nutrivalue/rema/` and `engine/research/rema_flyer_2026/` | Research-only; no scans or benchmark inputs redistributed |
| Promotion processing | `engine/src/nutrivalue/promotion_extraction.py`, `page_promotions.py` and `promotion_crosscheck.py` | Source/promotion schemas and code included; external model calls need optional configuration |
| Review UI | `engine/src/nutrivalue/promotion_review_web.py`, `review_service.py`, `review_log.py` | Actual interface logic included; running it needs a suitable existing database and review images |
| Offline candidate evidence | `engine/src/nutrivalue/product_candidates.py`, `engine/tests/test_product_candidates.py` | Synthetic-only parsing, scoring and tests; no source requests or confirmed product matches |
| Historical SQL | `engine/analytics/` | Schemas, coverage, review and history queries inspectable |
| Reference nutrition and identities | `engine/src/nutrivalue/gtin.py`, `sku_nutrition_review.py`, food-role vocabulary | No automatic real-world exact-SKU match |
| Test source | `engine/tests/` | Selected portable tests in public validation; not all experiments are standalone |
| Offline demo | Root `scripts/`, `analytics/`, `src/` and `tests/` | Fully synthetic, dependency-free and built to run without external data |

The public snapshot excludes sensitive/operational CI controls, real offer payloads and source images, personal records, owner account configuration and private deployment runbooks. No source permissions are implied by open-sourcing an adapter.

**Actual private operations (owner-observed, not publicly runnable):** a scoped, scheduled nine-chain source-only weekly collection pipeline uses private Cloud Run/GCS/SQLite, alongside a narrow authenticated human promotion-review service. The generic public source tree illustrates their code contracts, **not** credentials, archives, successful current data refresh or a deployment recipe. Ten representative source/SQL/reference files were byte-identical to private main in a targeted 25 October check; that is not an exhaustive full-tree audit.

## What the numbers mean

- **2,121 generic reference foods, 57 nutrients and 120,197 measurements:** a recorded Matvaretabellen snapshot import, **not** branded retail nutritional coverage.
- **883 selected offers and 213/213 catalogue pages in one W43 run:** a bounded historic capture, **not** independently verified store prices or full retailer inventory.
- **863 offers in a targeted internal category review:** bounded category/tag disposition work, **not** a blind production accuracy rate.
- **Printed current-price recognition:** still experimentally weak in historical REMA flyer analysis; no production-ready accuracy claim.

## Evidence states

A synthetic fixture, generic reference nutrition, a source-only advertised offer, a manufacturer/SKU nutrition review, a human-accepted catalogue promotion and a verified local checkout price each prove **different things**. None of the former states can automatically be promoted to the latter.

An actual verified protein-value comparison must establish the same exact SKU, quantity/edible form, nutrition per 100 g, time and store, and independently checked price. The strict public demo ranking is intentionally empty for real observations.

## What comes next

A wider portable engineering test suite, an end-to-end synthetic offer/review replay, and eventually a measured indicative grocery advisor. The README now includes a synthetic review screenshot reflecting the implemented controls. NutriValue's future deterministic 3+4-day plan builder begins with reviewed, versioned recipes and whole-package shopping constraints. A later optional Grocy/Pantry Raider inventory snapshot is intended as the stock authority; Mealie handoff needs explicit owner approval. The public code now includes an explicitly configured GET-only Mealie reader and offline recipe estimates. Grocy, Mealie write handoff, TickTick/consumption integration and advanced optimization remain future work. Public checks use synthetic inputs; no live hosting health is asserted.

See [README](../README.md), [Architecture & review](ARCHITECTURE_AND_REVIEW.md), [engine source tour](../engine/README.md) and [Changelog](../CHANGELOG.md).

## 31 October selective sync

Added GET-only Mealie recipe snapshots, reviewed-source nutrition estimates,
per-chain weekly fallback/health SQL and invalid-offer isolation. The public
offline candidate scorer and anonymized generic reference metadata are
preserved. Operational CI, account configuration, provider captures, personal
nutrition/workflow documents and actual recipe/stock records remain excluded.
See the [sync review](PUBLIC_SYNC_REVIEW.md) for validation and limitations.
