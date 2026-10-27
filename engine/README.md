# NutriValue Engine — source tour

This directory contains a curated public snapshot of real NutriValue engineering source: Python modules, SQL schemas/queries, CLI programs, regression tests and historical OCR research. The data, secrets and owner-linked deployment settings necessary for live source collection are **not published**.

## Layout

| Path | Purpose |
| --- | --- |
| `src/nutrivalue/providers/` | Retailer/provider adapters, Tjek catalogue intake, validity and condition parsing, PDF evidence |
| `src/nutrivalue/rema/` | REMA flyer OCR, region parsing, page evidence, review and pipeline code |
| `src/nutrivalue/offer_categories.py` | Conservative Norwegian item classification, tags, assortments and bundles |
| `src/nutrivalue/promotion_extraction.py` | Page-promotion persistence, extraction records, cross-checks and review queue |
| `src/nutrivalue/promotion_review_web.py` | Local browser-based review, using archived page images and statuses |
| `src/nutrivalue/review_service.py`, `review_log.py` | Authenticated WSGI review and append-only, replay-safe decision records |
| `src/nutrivalue/sku_nutrition_review.py`, `gtin.py` | Product-identity and nutrition evidence guards |
| `src/nutrivalue/offer_database.py`, `weekly_pipeline.py` | Versioned weekly imports, completeness and history |
| `analytics/` | 33 SQL schemas and review/coverage/promotion/history queries |
| `scripts/` | 45 inspection, review, import, bounded-capture and pilot CLI scripts |
| `tests/` | 66 selected engineering regression files; check each test's fixtures and optional services |
| `research/rema_flyer_2026/` | 32 historical OCR and layout experiment programs/tests from W40–W43 |
| `data/reference/food_roles.json` | Curated generic food-role vocabulary (not a personal inventory) |

## Run safe checks

The **repository-root demo** is the fully self-contained entry point:

```sh
python3 scripts/build_demo.py
python3 scripts/run_reports.py
python3 -m unittest discover -s tests -v
```

To inspect the larger engine without private inputs or making requests:

```sh
python3 -m compileall -q engine/src engine/scripts engine/tests engine/research
PYTHONPATH=engine/src python3 -m unittest discover -s engine/tests -p test_pricing.py -v
PYTHONPATH=engine/src python3 -m unittest discover -s engine/tests -p test_gtin.py -v
```

Not all engine tests are portable without their synthetic fixtures, optional dependencies, archived source-capture data and configuration. **Do not infer complete test-suite or live-pipeline health from the presence of test files**. For live/hosted modes, the original build used optional Google Storage and OAuth packages as well as OCR toolchains; this public code is not an out-of-the-box production setup.

No public run command fetches real offers or opens a live review service. Use separately approved credentials and source permissions, never personal files or secrets committed to Git.

## The offer reviewer

- Local script `scripts/review_promotions_web.py` launches the browser review page against an existing **private or synthetic test** weekly SQLite database and matching page image store.
- The page reports the week and chain, source catalogue/page, image evidence, each model's extracted promotion and cross-check status.
- **Accept**, **reject**, **page checked** and **skip** are distinct human decisions; rejected candidates can carry a note.
- The hosted implementation logs validated immutable decision objects and replays them idempotently; it does not write production SQLite directly from the browser.
- A reviewed catalogue promotion stays **source-only** until independent store/date and exact-product verification is established.

The architecture and detailed review workflow are documented in [Architecture & Review](../docs/ARCHITECTURE_AND_REVIEW.md).

## Public data boundaries

No genuine retailer captures, image scans, private historical offer databases, receipts, personal inventory, account IDs, tokens or infrastructure settings are intentionally bundled. Historical OCR experiments are **source only**, not benchmark datasets or a production quality claim. A synthetic screenshot, if added later, must be clearly marked as fictional and must depict the features that actually exist.
