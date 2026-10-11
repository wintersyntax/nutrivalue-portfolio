# NutriValue — development snapshot

**As of 11 October 2026 · Status: active WIP**

This document describes publicly shareable **progress in a larger private NutriValue project**. It is not a claim that the code, data or user interface described below ships in this portfolio repository. The [README](../README.md) explains how to run the **synthetic public SQL demo**.

## At a glance

| Area | Public portfolio — runnable now | Broader private development |
| --- | --- | --- |
| Pricing mathematics | Decimal-based NOK per 25 g protein on synthetic product data | Same conservative basis with distinct source and eligibility checks |
| SQL | Six inspectable synthetic/reference reports | Additional source-review, food-role, SKU evidence and reconciliation queries |
| Nutrition | Synthetic reference-food fixture | Official local Matvaretabellen import: 2,121 foods, 57 nutrients, 120,197 measurements |
| Retail observation intake | Fictional example prices only; verified report returns **zero rows** | Bounded **source-only** KIWI, REMA 1000, Coop Prix, MENY and Kassalapp pilots |
| Product identity and nutrition | No real retail SKU joins | Four privately reviewed manufacturer/SKU nutrition records; retailer prices **not verified** |
| Food selection | No planner | Curated 35-category role vocabulary, with contextual substitutions and review gates |
| Reconciliation | No deployed multi-source pipeline | **Merged private-main** offline exception/field-conflict foundation; candidate-only outputs with limited retailer coverage |
| Weekly offer scope / historical data | No real offer history or scheduled collection | Nine Norwegian retailer chains selected; restricted personal-use Tjek access reported, pending terms review; weekly private offer-history archive and Google Cloud execution **planned** only |
| Interface / shopping | No dashboard, service or mobile integration | A local dashboard, 3+4-day planning and Mealie handoff remain **planned**, not shipped |

Counts describe specific local reference imports or scoped reviews; they are **not live catalogue coverage, current prices, or public API availability**.

## What changed since the first public extract

The initial public demo was created on **10 October 2026** to make foundational SQL and Python work reproducible without private provider data. Work recorded in private development on **11 October** expanded the evidence-oriented approach:

1. **Retail source coverage.** Bounded first-party HTML, locked-price and PDF extraction pilots investigate price, pack-size, campaign and date evidence from several Norwegian stores. Parsing a displayed offer creates a candidate, **not** a verified shelf price, legally redistributable dataset or guaranteed active promotion.
2. **SKU/manufacturer nutrition review.** A narrowly scoped private pilot reviewed four manufacturer-linked product/nutrition records (one marked `verified`, three `verified_with_warning` **for the nutrition-review dimension**). The associated retail-price dimension remains `source_only`; these cannot enter a verified NOK/protein ranking.
3. **Explainable meal roles.** Thirty-five generic ingredient categories and directional/partial substitutions help explain which foods could serve similar recipe purposes. Suggestions from generic reference nutrition require explicit review before being treated as approved associations.
4. **Cross-source evidence foundation.** Private PR #40 merged the read-only exception/field-conflict foundation into main. It compares bounded staged evidence and avoids double-counting mirrored source families; it does **not** establish independent local-price verification or approve any real retailer ranking.
5. **A smaller advisory MVP experiment.** An **unmerged private feature branch** explores five REMA card candidates with **indicative** price/protein estimates. It is deliberately not a complete current-week catalogue, verified local offer ranking or finished meal planner. No part of that branch is claimed as shipped in the public demo.
6. **Scoped weekly intake direction.** A later private planning update selected nine Norwegian retailer *chains* (not a claim of verified physical-store coverage). The owner reports limited personal-use Tjek API permission and a bounded three-offer probe outside the repo, but the exact rights wording has not been checked. A weekly immutable private offer archive, local offline replay and an optional **Cloud Scheduler → Cloud Run Job → private Cloud Storage** arrangement are **future designs**, not jobs or price-history reports that currently run.

## Evidence labels that matter

| Label / concept | What it means | What it does **not** mean |
| --- | --- | --- |
| **Synthetic demo** | Reproducible invented fixtures exercise SQL and arithmetic | A real sale, current price or nutrition label |
| **Reference nutrition** | A generic food and its sourced nutritional measurements | An exact branded SKU/EAN |
| **Source-only observation** | An attributed published/provider listing or locally captured candidate | Independently checked, valid local shelf/checkout price |
| **Manufacturer nutrition reviewed** | Identity, variant, pack and label basis were checked within a bounded review | Its retailer price, stock, offer and location are valid |
| **Indicative advisory result** | Calculation using explicitly uncertain or generic evidence and visible assumptions | An audited real-world bargain or complete meal recommendation |
| **Verified comparison** | Requires compatible product, edible mass, nutrition and independently checked store/date price evidence | Achieved by copying the same upstream advertisement across several sites |

A store label is not proof of store-specific stock. A saved page's retrieval timestamp is not necessarily the offer's start date. Different sites repeating one manufacturer's declared protein value do **not** constitute independent manufacturer evidence. Unknown mass, nutrient values, conditions and validity periods remain visible unknowns.

## Near-term direction

The first practical advisor is intended to be **small, single-retailer and explicit about uncertainty**: show a few dated grocery candidates, source/conditions, package sizes where known, approximate kcal/protein values where defensible, and indicative cost. This is a *planned product lane*, separate from a stricter **verified comparison lane** that remains empty until independently checked store/date and product/nutrition evidence exists.

Alongside this, source permissions and a small offline-first weekly offer-history workflow need validation before any scheduled requests. Advertised offer histories would be source-only, not verified shelf-price trends, and previous snapshots would be retained privately only when permitted. **No NutriValue Google Cloud resources, Tjek collector, historical price chart or routine offer refresh exists yet.** After evidence review, the path is a truthful local-first dashboard; recipe and inventory constraints for three- and four-day cooking blocks; and an **explicitly user-approved** shopping-list/meal-calendar handoff to Mealie. None is presented as live today. The [product roadmap](PRODUCT_VISION.md) distinguishes the goals from the accepted public demo.

## Publication boundary

This public repository contains **no** scraped provider responses, Tjek offer payloads, retailer PDF/image copies, source snapshots, credentials, precise home/store locations, personal purchase histories, private product-review manifests or licensed bulk Matvaretabellen exports. Public SQL fixtures remain **synthetic**. Source access, verification and public redistribution rights are independent checks.

Development is substantially **AI-assisted**, with human-defined requirements, review and testing. The public code should be evaluated for what it actually reproduces: schema design, inspectable SQL, deterministic calculations, input validation and honest data-quality boundaries.
