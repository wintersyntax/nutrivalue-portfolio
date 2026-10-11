# NutriValue

**Norwegian grocery and protein-value analytics with SQL and Python.**

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)
[![MIT License](https://img.shields.io/badge/License-MIT-2ea44f)](LICENSE)
![Portfolio Project](https://img.shields.io/badge/Portfolio-SQL%20Analytics-6f42c1)
![Status: Active WIP](https://img.shields.io/badge/Status-Active%20WIP-f59e0b)

**What does 25 grams of protein actually cost?** NutriValue explores how grocery prices, edible package sizes and nutritional composition can be combined to answer that question without mixing incompatible units or unverified listings.

This is an **active work in progress**. This repository is a **curated, runnable public portfolio extract** of a larger private project. It demonstrates relational data modeling, SQL analysis, source tracking and deliberate data-quality checks. **The public demo is deliberately smaller than the private development work.**

**[Development snapshot — 11 October 2026](docs/DEVELOPMENT_SNAPSHOT.md)** explains the latest private milestones, what remains experimental and why source-only grocery observations do not qualify as verified local prices.

**[Read the product vision and roadmap](docs/PRODUCT_VISION.md)** to see the planned Grocery OS, local-first dashboard, three-/four-day meal-prep planning and future Mealie mobile handoff, clearly distinguished from what works today.

> **Portfolio demo, not live grocery advice.** All products, stores, prices and food-composition rows in this repository are **synthetic**. No actual Norwegian retail offers have been verified here, and the verified-price report intentionally returns **zero rows**.

## Highlights

- **Relational SQLite model** for products, stores, nutrition profiles and price observations.
- **Six SQL analyses** covering protein cost, nutritional density, missing data and reference-food groups.
- **Reproducible Python demo**, no API keys, packages or live requests required.
- **Six deterministic tests** checking arithmetic, invalid inputs and the separation of demo versus verified prices.
- **Explicit source and measurement boundaries:** a generic food is not automatically a branded retail product.

## Quick start

**Requires Python 3.10+** and its standard library. Clone and run:

~~~bash
git clone https://github.com/wintersyntax/nutrivalue-portfolio.git
cd nutrivalue-portfolio

python3 scripts/build_demo.py
python3 scripts/run_reports.py
python3 -m unittest discover -s tests -v
~~~

The generator creates a local SQLite file at **data/demo.sqlite3**, which is ignored by Git. It refuses to overwrite an existing file. Delete the local file before rebuilding the demo.

If the SQLite command-line utility is installed, individual SQL files can be inspected directly:

~~~bash
sqlite3 -header -column data/demo.sqlite3 < analytics/queries/demo_protein_value.sql
~~~

## Example output

Price efficiency is calculated from edible mass, protein concentration and price:

~~~text
Protein in package (g) = edible package mass (g) × protein (g / 100 g) / 100
NOK per 25 g protein   = package price (NOK) × 25 / protein in package (g)
~~~

**Fictional examples only:**

| Product | Edible package mass | Protein / 100 g | Example price | NOK / 25 g protein |
| --- | ---: | ---: | ---: | ---: |
| Demo Protein B | 500 g | 10 g | 30 NOK | **15.00** |
| Demo Protein A | 400 g | 18 g | 60 NOK | 20.83 |

The [demo ranking](analytics/queries/demo_protein_value.sql) is deliberately separate from the [verified-value ranking](analytics/queries/best_protein_value.sql). Demo prices never appear in the verified report.

## Architecture

Two kinds of information are intentionally kept apart: **retail products and their price observations** versus **generic reference foods and nutrient measurements**.

~~~mermaid
flowchart LR
    Seed["Synthetic data<br/>build_demo.py"] --> DB[("SQLite demo database")]
    DB --> Retail["Products + stores<br/>nutrition + demo prices"]
    DB --> Reference["Reference foods<br/>+ nutrients"]

    Retail --> Demo["Demo value ranking<br/>NOK / 25 g protein"]
    Retail --> Gate{"Independently verified<br/>and currently valid?"}
    Gate -->|"No qualifying rows"| Empty["Verified ranking<br/>0 results"]

    Reference --> Nutrition["Coverage · density<br/>portions · groups"]

    Demo --> Reports["run_reports.py"]
    Empty --> Reports
    Nutrition --> Reports

    Tests["unittest: six tests"] -.-> DB
    Tests -.-> Reports
~~~

The retail side uses **stores**, **products**, **nutrition_profiles** and **price_observations**. The reference side uses snapshot-aware **mvt_snapshots**, **mvt_food_groups**, **mvt_foods** and **mvt_food_nutrients**.

There is **no automatic join by food name** between reference nutrition and retail products. An exact match would require separately reviewed SKU/EAN identity, measurement basis, edible mass, location and date evidence.

## SQL portfolio

The six standalone reports are in [analytics/queries](analytics/queries/):

| Analysis | Question | SQL techniques |
| --- | --- | --- |
| [Demo protein value](analytics/queries/demo_protein_value.sql) | Which **fictional** package has the best protein value? | Multi-table JOIN, arithmetic, ROUND, ORDER BY |
| [Verified protein value](analytics/queries/best_protein_value.sql) | Which real offers satisfy evidence, nutrition and date rules? **None yet.** | JOIN, WHERE, source/date filters |
| [Nutrition coverage](analytics/queries/mvt_nutrition_coverage.sql) | Which reference-food records lack usable protein or kcal values? | CTE, LEFT JOIN, conditional aggregation |
| [Protein per 100 kcal](analytics/queries/mvt_protein_per_100kcal.sql) | Which food forms are more protein-dense? | Composite-key JOIN, CTE, calculated ratios |
| [25 g protein portions](analytics/queries/mvt_portions_for_25g_protein.sql) | How much edible food provides 25 g protein? | Calculated fields, validated denominators |
| [Reference group summary](analytics/queries/mvt_reference_group_summary.sql) | How do food groups differ in composition and coverage? | GROUP BY, COUNT, AVG, NULL handling |

The [tests](tests/test_portfolio.py) assert the ranking order and arithmetic, preserve missing values, exercise reference reports and reject invalid or non-finite inputs. The [Python pricing function](src/nutrivalue/pricing.py) uses **Decimal** for deterministic calculations.

## Data quality before optimization

A convincing price comparison requires more than a calculation:

- **Identity:** Matvaretabellen food IDs are not supermarket product identifiers.
- **Form:** raw, cooked, drained and as-sold nutrition values are not interchangeable.
- **Mass:** package weight and usable edible mass may differ.
- **Evidence:** a provider-reported price does not establish a current price on a specific shelf.
- **Independence:** several sites repeating the same retailer advertisement or manufacturer declaration are not several independent confirmations.
- **Missing values:** absent source measurements must not silently become zero.

The verified-value query filters by explicit verification status, matching as-sold branded nutrition and price validity dates. Real ingestion would still need independent review of source evidence and exact product mapping.

## Private development snapshot (11 October 2026)

The larger **private** codebase has progressed beyond the public synthetic example. These are private-local milestones or bounded experiments, **not features runnable from this repository**.

| Workstream | Private progress | Important limit |
| --- | --- | --- |
| Norwegian nutrition reference | Imported **2,121 Matvaretabellen foods, 57 nutrients and 120,197 nutrient measurements** into local SQLite. | Generic foods are not identified retail SKUs. |
| Retailer and provider intake | Staged review-only observations from **KIWI, REMA 1000, Coop Prix, MENY and Kassalapp**; tested bounded HTML/PDF/source pilots. | Source-only observations are not independently checked local shelf prices or reusable public datasets. |
| Exact-product nutrition review | Reviewed **four private manufacturer/SKU nutrition records** (one `verified`, three `verified_with_warning` on the nutrition-review dimension). | **All associated price evidence remains `source_only`.** Nutrition review does not verify retailer prices. |
| Food roles | Curated **35 general food categories** and contextual substitutions for later recipe planning, with review-gated reference-food suggestions. | No automatic branded-product match or cost-optimized meal plan. |
| Evidence reconciliation | Added an initial **offline, read-only cross-source exception and field-conflict foundation** to private main. | It is an early foundation, not an accepted multi-store price verification pipeline. |
| Small retailer advisory experiment | A separate **unmerged private feature branch** explores five REMA product candidates and clearly indicative protein costs. | No independently verified weekly price, eligible live offer ranking or finished advisor. |

The planned early product will keep **two evidence lanes** separate: an **indicative advisory view** (dated, attributed, potentially approximate nutrition and explicitly non-verified prices) and a much stricter **verified price/protein comparison**. Neither lane is implemented in this public repository. The current public verified-price report intentionally returns zero results.

**Still not implemented:** a verified current-store price + exact SKU nutrition ranking, deployed dashboard, automatic basket optimizer, full three-/four-day meal planning or Mealie synchronization. The public repo does **not** redistribute provider responses, private snapshots, purchased-product evidence or licensed nutrition datasets. For further scope and maturity details, see the [development snapshot](docs/DEVELOPMENT_SNAPSHOT.md).

## Repository map

~~~text
analytics/
  schema.sql                  Product, store, nutrition and price tables
  reference_schema.sql        Snapshot-aware reference-nutrition tables
  queries/                    Six SQL reports
scripts/
  build_demo.py               Build synthetic SQLite data
  run_reports.py              Execute and print the reports
src/nutrivalue/
  pricing.py                  Decimal-based protein calculations
tests/
  test_portfolio.py           Six deterministic unit tests
LICENSE                       MIT License
~~~

## Project status

**Active WIP.** The broader goal is a local-first, nutrition-aware Grocery OS for Norway. The public code is a small, tested SQL demo, **not** a completed shopping or meal-planning application. The near-term private direction is a **one-retailer, advisory-first pilot** with indicative values clearly separated from verified evidence. The [Product Vision & Roadmap](docs/PRODUCT_VISION.md) describes planned milestones, dashboard and mobile integration without treating them as shipped features.

## Next steps

1. Validate a small, dated **single-retailer advisory** workflow, showing source-only prices and approximate nutrition with visible uncertainty; do not imply verified weekly offers.
2. Extend read-only field-level reconciliation with upstream-source independence, identity checks and an explicit conflict/review queue.
3. Require separately reviewed exact SKU, edible mass, nutrition basis, store, timestamp and offer conditions before any **verified** real-world NOK/protein ranking.
4. Build a truthful local-first review dashboard once candidate and verification statuses can be displayed reliably.
5. Later add recipe, inventory and budget constraints for the three-/four-day planning cycle, followed by user-approved Mealie handoff.

## Attribution and development approach

The private project uses [Matvaretabellen / Mattilsynet](https://www.matvaretabellen.no/en/api/) as a Norwegian nutrition reference and has explored first-party KIWI, REMA 1000, Coop Prix and MENY offers alongside Kassalapp product/price information. **No licensed source datasets, captured responses, private accounts, API keys or personal purchasing history are included in this public repository.**

The work uses substantial **AI-assisted development** together with manually directed problem definition, review, testing and implementation decisions. It is a practical demonstration of foundational SQL and analytical thinking, not a claim of previous professional product-analytics employment.

## License

[MIT License](LICENSE).
