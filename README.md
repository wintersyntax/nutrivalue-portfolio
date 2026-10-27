# NutriValue

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)
[![MIT License](https://img.shields.io/badge/License-MIT-2ea44f)](LICENSE)
![Portfolio Project](https://img.shields.io/badge/Portfolio-Project-6f42c1)
![Status: Active development](https://img.shields.io/badge/Status-Active%20development-f59e0b)
[![Public portfolio validation](https://github.com/wintersyntax/nutrivalue-portfolio/actions/workflows/portfolio-ci.yml/badge.svg)](https://github.com/wintersyntax/nutrivalue-portfolio/actions/workflows/portfolio-ci.yml)

**From Norwegian grocery advertisements and nutrition evidence to explainable price comparisons and human-reviewed shopping decisions.**

NutriValue is a personal data-engineering and applied-AI project. It explores how to gather and track Norwegian grocery offers, interpret catalogue pages, extract prices and promotion terms, classify products, check identity and nutritional evidence, and ultimately work out what food is worth buying and cooking.

**One product, one name:** NutriValue is the complete project, from observed offers and source-only price history to its planned recipe, inventory and nutrition-aware meal-planning functionality. Earlier design labels were working names for this **same product**, not extra applications or an independently shipped suite. The starting question is simple:

> **How can grocery offers, food prices and reliable nutrition data support better shopping and meal-prep decisions?**

The engineering challenge is not just fetching prices. A catalogue may show a member discount, a multibuy offer, a previous price, a price per kilogram, an assortment badge, or incomplete OCR. Matvaretabellen may describe a generic food rather than the branded SKU being sold. NutriValue keeps these facts separate and makes uncertainty **visible** instead of silently manufacturing a convincing but unsupported result.

**This is a code portfolio, not just a concept document.** The repository publishes a curated, substantial snapshot of actual Python, SQL, reviewer, OCR-research and test code alongside a small, offline, fully synthetic demo. The review-interface screenshot below uses synthetic demonstration content to illustrate the implemented review controls; it is not a live retailer observation. Raw provider responses, full flyer archives, personal datasets, credentials and owner-specific infrastructure configuration are not published.

## What it does

| Capability | Engineering implemented in this repository | Reality check |
| --- | --- | --- |
| Grocery source intake | KIWI, REMA, Coop, MENY, Kassalapp and Tjek adapter/pilot modules | Requires separately authorized sources and captured data |
| Weekly catalogue collection | Nine-chain scoped collection, date/source identity, duplicate and hotspot handling | A captured listing is not a verified checkout price |
| Product normalization | Norwegian categories, food roles, unit prices, bundles, member and standing promotion terms | Ambiguous items remain flagged; no guessed SKU/nutrition matches |
| Flyer processing | OCR region analysis, archived W40–W43 research, page candidate extraction | Experimental price recognition is **not production-ready** |
| Model-assisted promotion extraction | Strict structured outputs, two-model comparison, source/OCR cross-checks, budgeted benchmarks | Model agreement is not a reference answer |
| Human offer review | Web/terminal review flows, evidence display, explicit decisions and replay-safe logs | Human approval of a page offer does not verify store price |
| Weekly offer history | SQL schemas and queries for imported source-only runs, coverage, promotion monitoring and history | Genuine private/provider data are **not distributed** |
| Offline product candidate matching | Adapted TASK-069/TASK-070 scoring of supplied NGData/Kassalapp-shaped data, pack and brand comparisons, and nutrition flags | Experimental review aid only: no live lookups, confirmed identities or verified prices |
| Nutrition and protein economics | Generic nutrition schemas, GTIN guardrails, SKU review, Decimal NOK per protein | No independently verified real current-store price ranking |
| Meal planning, inventory and approved handoff | Defined, reviewed-first path to versioned recipes, deterministic 3+4-day proposals, package-level shopping, stock snapshots and optional Mealie sync | **Planned**, not shipped; no Grocy, Mealie or TickTick integration is active |

## Private implementation versus this public release

**Documentation sync: 25 October 2026.** The private NutriValue repository
runs a source-only weekly nine-chain Tjek offer/promotion pipeline on a
private Cloud Run job with scheduled collection, and a separate owner-only
review service. This **public portfolio** includes a reviewed subset of its
engineering code, not a copy of its deployment, account configuration or
provider evidence. Selected published modules for weekly collection, offer
storage, promotion extraction/review, food roles and SQL were checked against
the private code base on 25 October and were unchanged.

The broader NutriValue roadmap is **future work**: a transparent,
deterministic three-day plus four-day recipe/meal-prep proposal with
versioned ingredients, nutrition evidence and whole-package budgets first;
then an explicitly approved integration that **reads** inventory from
Grocy/Pantry Raider as the intended inventory authority and optionally
publishes only approved managed recipes, meals and shopping lists to Mealie.
TickTick and consumption evidence are separate intended responsibilities,
**not working connections**. Any advanced multi-objective optimizer is
deferred until basic real-data planning and offline failure cases are
independently validated.

The private and public repositories are **deliberately different release
surfaces**. Historical reference values, personal routines, stock, retailer
captures and third-party content are not distributed here. The code in
`engine/` is the public engineering snapshot; the fully fictional demo is
independently runnable from the repository root.

## Architecture — from observation to a defensible decision

```mermaid
flowchart TB
  subgraph Sources["1 — Evidence sources"]
    T["Weekly retailer / Tjek catalogue observations"]
    W["KIWI, REMA, Coop, MENY, Kassalapp pilots"]
    I["Catalogue pages and images"]
    N["Matvaretabellen generic food nutrients"]
  end
  subgraph Intake["2 — Controlled intake"]
    P["Scope, consent, chain, dates and source identity"]
    H["Listing + hotspot recovery"]
    A["Content hashes and retained provenance"]
  end
  subgraph Interpret["3 — Interpret what was advertised"]
    U["NOK, unit, quantity, dates and conditions"]
    C["Categories, bundles and assortment"]
    OCR["OCR and page geometry"]
    M["Structured promotion extraction"]
    X["Independent model/OCR cross-checks"]
  end
  subgraph Review["4 — Review only what evidence supports"]
    Q["Disagreement / missing evidence queue"]
    UI["Original page + model answers + status"]
    D["Accept, reject, page checked or skip"]
    LOG["Append-only decisions, validated replay"]
  end
  subgraph Storage["5 — Weekly history and analysis"]
    S[("Source-only weekly SQLite")]
    PR[("Promotion states and reviews")]
    SQL["Coverage, categories, history, exceptions"]
  end
  subgraph Value["6 — Eligibility, not guesswork"]
    REF[("Generic nutrient reference")]
    SKU["Exact product, food form and nutrition review"]
    G{"Independently checked store/date price and SKU?"}
    F["Evidence-gated price and nutrition comparisons"]
    IND["Indicative calculations with evidence caveats"]
  end
  FUT["Future: recipe + inventory + approved meal plan"]
  T --> P
  W --> P
  P --> H --> A
  I --> A
  A --> U --> C --> S
  A --> OCR --> M --> X
  X --> Q --> UI --> D --> LOG --> PR
  X --> PR
  S --> SQL
  PR --> SQL
  N --> REF --> SKU
  S --> G
  SKU --> G
  G -->|"Yes"| F
  G -->|"Not yet"| IND
  IND -.-> FUT
```

This diagram separates **implemented source processing**, **human-review operations**, **strict eligibility rules** and the **planned grocery planner**. The complete [architecture and review walkthrough](docs/ARCHITECTURE_AND_REVIEW.md) goes deeper into both technical paths.

## Offer review: the human in the loop

![Synthetic NutriValue offer-review demonstration showing model comparisons and reviewer controls](assets/promotion-review-interface.png)

*Synthetic demonstration of NutriValue's implemented promotion-review interface (October 2026). The displayed products, prices and model interpretations are illustrative; the image does not establish real retailer observations, verified checkout prices or measured extraction accuracy.*

NutriValue has a real review workflow, not a generic “AI detects everything” claim. The interface is implemented in [promotion_review_web.py](engine/src/nutrivalue/promotion_review_web.py) and its [CLI launcher](engine/scripts/review_promotions_web.py), with a hosted variant in [review_service.py](engine/src/nutrivalue/review_service.py).

A reviewer can inspect a specific **chain, week, catalogue and page**, compare the original page image and OCR evidence to both models' structured promotion answers, and see uncertainty, offer type, member scope and notes. From there:

- **Accept** a promotion candidate when the page evidence supports it.
- **Reject** an unsupported result, optionally explaining why.
- Mark a **page checked** when valid structured extraction is missing and the page is inspected manually.
- **Skip** unresolved evidence rather than force a false conclusion.

A [create-only decision log](engine/src/nutrivalue/review_log.py) validates reviewer events and can replay them idempotently into the latest review view. The hosted service does not rewrite the master SQLite file from the browser. The reviewer records the evidence decision; it **does not** magically establish a verified checkout price.


## Code walkthrough

The engine code is intentionally accessible in the repository rather than hidden behind descriptions:

| Start here | What to look for |
| --- | --- |
| [engine/src/nutrivalue/providers/tjek_weekly_collect.py](engine/src/nutrivalue/providers/tjek_weekly_collect.py) | Bounded weekly catalogue collection, identity and hotspot handling |
| [engine/src/nutrivalue/providers/tjek_offer_conditions.py](engine/src/nutrivalue/providers/tjek_offer_conditions.py) | Source-only offer conditions, membership, price and unit caveats |
| [engine/src/nutrivalue/offer_categories.py](engine/src/nutrivalue/offer_categories.py) | Conservative Norwegian category and food-tag vocabulary |
| [engine/src/nutrivalue/promotion_extraction.py](engine/src/nutrivalue/promotion_extraction.py) | Page extraction persistence, promotion records and review candidates |
| [engine/src/nutrivalue/promotion_crosscheck.py](engine/src/nutrivalue/promotion_crosscheck.py) | Agreement/disagreement accounting between structured interpretations |
| [engine/src/nutrivalue/promotion_review_web.py](engine/src/nutrivalue/promotion_review_web.py) | Local evidence-based review browser |
| [engine/src/nutrivalue/review_log.py](engine/src/nutrivalue/review_log.py) | Append-only decision schema, validation and replay |
| [engine/src/nutrivalue/offer_database.py](engine/src/nutrivalue/offer_database.py) | Weekly import and source-only persistence |
| [engine/src/nutrivalue/gtin.py](engine/src/nutrivalue/gtin.py) | Product identifier guardrails |
| [engine/src/nutrivalue/product_candidates.py](engine/src/nutrivalue/product_candidates.py) | Offline-only candidate parsing and scoring with [synthetic tests](engine/tests/test_product_candidates.py) |
| [engine/analytics](engine/analytics) | 33 production-oriented SQL schema and analytic source files |
| [engine/research/rema_flyer_2026](engine/research/rema_flyer_2026) | 32 OCR experiments and reference test programs across W40–W43 |
| [engine/tests](engine/tests) | 68 selected engineering test files across parsing, promotion handling, stores, SQL and identity |

[Read the source tour and optional dependencies](engine/README.md).

## Run the offline synthetic demo

The top-level runnable demo is deliberately independent of the provider pipeline. It shows the same modelling philosophy using invented food and prices.

**Python 3.10+; no package installation, API keys or network needed.**

```bash
git clone https://github.com/wintersyntax/nutrivalue-portfolio.git
cd nutrivalue-portfolio

python3 scripts/build_demo.py
python3 scripts/run_reports.py
python3 -m unittest discover -s tests -v
```

It creates `data/demo.sqlite3` only if that file does not exist. The reports illustrate demo price value, strict verified-value gating, reference-food coverage, nutritional density and edible portions. Six deterministic root tests cover the self-contained fixture.

To inspect the larger source tree without capturing offers:

```bash
python3 -m compileall -q engine/src engine/scripts engine/tests engine/research
PYTHONPATH=engine/src python3 -m unittest discover -s engine/tests -p test_gtin.py -v
PYTHONPATH=engine/src python3 -m unittest discover -s engine/tests -p test_product_candidates.py -v
```

The full `engine/tests` collection is **not** advertised as entirely standalone: some tests depend on local archived evidence, optional packages or historical experimental fixtures. [Portfolio validation](.github/workflows/portfolio-ci.yml) checks the offline release and selected portable contracts.

## Data modelling and nutrition-aware value

NutriValue is **not built around a fixed protein serving**. Useful comparisons depend on the decision: price per package, per kilogram of edible food, per serving, and — when a product's nutrition is reliable — cost per gram of protein, energy density and other relevant nutrients. Future meal planning considers these measures together with the user's budget, recipes, available ingredients and nutritional targets.

```text
Total edible protein (g) = net edible mass (g) × protein per 100 g / 100
NOK per gram of protein = package price (NOK) / total edible protein (g)
```

For example, a **fictional** 500 g product with 10 g protein/100 g at 30 NOK contains 50 g protein and costs **0.60 NOK per gram of protein**. The [synthetic SQL ranking](analytics/queries/demo_protein_value.sql) compares that price efficiency across fictional products. The [strict verified report](analytics/queries/best_protein_value.sql) remains separate and currently has **zero qualifying real observations**.

Price, exact product identity, food form, nutritional basis, store and date all have to describe the *same item* for a defensible comparison. A web result with a plausible food name is not enough.

## Engineering progress and constraints

Private implementation evidence recorded through 22 October 2026 includes Matvaretabellen imports (one bounded snapshot: **2,121 generic foods, 57 nutrients, 120,197 measurements**) and weekly Tjek offer/photo recovery (a bounded W43 run reported **883 selected source-only offers, 213/213 catalogue pages**). These are **scoped historical engineering figures**, not evidence of nationwide price coverage, independent extraction accuracy or active retail shelf prices.

Three important limitations remain: printed-price OCR needs stronger reference accuracy; real retailer/source payloads cannot simply be redistributed; and verified price-to-exact-SKU-to-nutrition matching is not yet an operational nationwide shopping service. **Versioned recipes, Grocy-backed inventory, deterministic 3+4-day meal planning, an optional later optimizer and approved Mealie handoff remain future NutriValue work.**

## Privacy, rights and reproducibility

This portfolio intentionally excludes private offer databases, provider payloads, original flyer imagery, personal shopping/inventory files, owner-linked deployment details, credentials and tokens. **No personal meal-prep parameters, health/training notes or household inventory records from the private documentation are reproduced here.** The included synthetic review screenshot illustrates implemented reviewer controls with example data, not real offer evidence. Sources must be used under their relevant conditions; availability in a private account is not permission to redistribute raw content.

[Changelog](CHANGELOG.md) · [Architecture and offer review](docs/ARCHITECTURE_AND_REVIEW.md) · [Engineering source guide](engine/README.md) · [Development status](docs/DEVELOPMENT_SNAPSHOT.md) · [Product roadmap](docs/PRODUCT_VISION.md)


### Taxonomy v6 and promotion normalization

The published offline engine includes a 140-food generic vocabulary, processed-food and ready-meal safeguards, and a promotion-restatement filter. These algorithms do not establish verified product identities, store prices or measured extraction accuracy.
