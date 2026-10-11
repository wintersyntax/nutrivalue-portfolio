# NutriValue

**Norwegian grocery and protein-value analytics with SQL and Python.**

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)
[![MIT License](https://img.shields.io/badge/License-MIT-2ea44f)](LICENSE)
![Portfolio Project](https://img.shields.io/badge/Portfolio-SQL%20Analytics-6f42c1)
![Status: Active WIP](https://img.shields.io/badge/Status-Active%20WIP-f59e0b)

**What does 25 grams of protein actually cost?** NutriValue explores how grocery prices, edible package sizes and nutritional composition can be combined to answer that question without mixing incompatible units or unverified listings.

This is an **active work in progress**. This repository is a **curated, runnable public portfolio extract** of a larger private project. It demonstrates relational data modeling, SQL analysis, source tracking and deliberate data-quality checks. **The public demo is deliberately smaller than the private development work.**

**[Development snapshot — 12 October 2026](docs/DEVELOPMENT_SNAPSHOT.md)** explains the latest private milestones, what remains experimental and why source-only grocery observations do not qualify as verified local prices.

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

The **public, runnable architecture** is deliberately smaller than private development: synthetic retail prices and synthetic generic reference nutrition are stored separately. A demo calculation does not qualify an item for the verified ranking.

~~~mermaid
flowchart LR
    Seed["Synthetic fixtures<br/>build_demo.py"] --> DB[("SQLite demo database")]
    DB --> Retail["Retail products<br/>nutrition and demo prices"]
    DB --> Reference["Generic reference foods<br/>and nutrients"]

    Retail --> Demo["Demo price / protein ranking"]
    Retail --> Gate{"Independently checked real price<br/>and matching product evidence?"}
    Gate -->|"No qualifying records"| Empty["Verified ranking<br/>zero rows"]
    Reference --> Nutrition["Nutrition coverage, density<br/>portions and group reports"]

    Demo --> CLI["run_reports.py"]
    Empty --> CLI
    Nutrition --> CLI
    Tests["Six offline unit tests"] -.-> DB
    Tests -.-> CLI
~~~

The retail side uses **stores**, **products**, **nutrition_profiles** and **price_observations**. The reference side uses snapshot-aware **mvt_snapshots**, **mvt_food_groups**, **mvt_foods** and **mvt_food_nutrients**.

There is **no automatic join by food name** between reference nutrition and retail products. A real match needs independently reviewed SKU/EAN identity, exact variant, nutritional measurement basis, edible mass, locality and dated price evidence. The public database holds only synthetic examples; it contains **no real retailer offers or privately collected source records**.

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

## Private development snapshot (12 October 2026)

The larger **private** codebase has progressed beyond this runnable public extract. Items below describe local/private milestones or explicitly planned work. **Private code and captured data are not automatically mirrored here.**

| Workstream | Private status | Limit |
| --- | --- | --- |
| Official Norwegian reference nutrition | Locally imported **2,121 Matvaretabellen foods, 57 nutrients and 120,197 nutrient measurements** into SQLite. | Reference-food IDs are not retail SKU/EAN identities. |
| Retailer and provider evidence | Bounded **KIWI, REMA 1000, Coop Prix, MENY and Kassalapp** HTML, PDF and API pilots; source-only observations and dated history staged privately. | Advertised/provider observations are **not verified current local shelf prices** or a redistributable public dataset. |
| Exact manufacturer nutrition | Four private SKU/manufacturer nutrition reviews: one `verified`, three `verified_with_warning` **for identity/nutrition only**. | All associated price evidence remains `source_only`. |
| Source-family reconciliation | **Merged on private main:** offline, read-only exception/field-conflict foundation for bounded KIWI/REMA evidence, including safeguards against counting mirrored upstream assertions as independent corroboration. | Candidate matches and calculated unit rates do **not** verify products, nutrition or prices; broader real-food review remains open. |
| Collection and price-history deployment | **Planned only:** permissions-approved once-weekly collection, immutable private offer archive and optional Google Cloud Scheduler → Cloud Run Job → private Cloud Storage; local SQLite replay stays available. | No NutriValue cloud job, stored scheduled feed, historical offer chart or live service deployed. |
| Food roles | Curated **35 general food categories** and contextual/partial substitutions with review-gated reference-food suggestions. | No cost-optimized meal plan or automatic product match. |
| Small advisory experiment | A separate **unmerged private experiment** explores five REMA cards and clearly *indicative* protein costs. | Not a complete weekly catalogue, deployed advisor or verified comparison. |
| Scoped weekly-offer direction | **Nine Norwegian retailer chains** selected for local shopping; reported limited Tjek personal-use access remains subject to exact rights review. | Chain-level selection is not proof of specific-store stock, local prices or permission to publish data. |
| Offline Tjek import (TASK-023, private PR #43) | **Implemented on private main:** offline parser for already-saved JSON or `.ndjson.gz` snapshots, nine-chain allowlist, append-only private SQLite `source_only` staging, source hashes, offer validity and conservative price/unit diagnostics. **18 focused synthetic tests**; one private three-offer REMA sample was replayed and checked locally. | No HTTP collector, scheduled refresh, all-chain real coverage, exact EAN/nutrition matching, independently verified shelf prices or public provider payloads. |

### Evidence architecture: implemented private foundation vs. planned product

Dashed arrows represent **future integrations**, not running workflows. This is an explanatory view of the private direction; it is **not the runtime graph of this public demo**.

~~~mermaid
flowchart TB
    subgraph Existing["Private local/offline foundation — implemented"]
        RetailSources["KIWI, REMA, Coop Prix, MENY<br/>and Kassalapp source-only pilots"] --> Stage["Private attributed snapshots<br/>and source-only staging"]
        MVT["Matvaretabellen import"] --> Generic["Separate generic<br/>nutrition reference"]
        SavedTjek["Saved Tjek snapshots<br/>offline input only"] --> TjekStaging["Private immutable Tjek staging<br/>source-only offers"]
        Stage --> Reconcile["Offline source-family<br/>conflicts and exception review"]
        Reconcile --> Candidates["Unverified product and<br/>price candidates"]
        Manufacturer["Four reviewed manufacturer<br/>SKU / nutrition records"] --> Candidates
        Candidates --> Strict{"Exact identity, edible mass<br/>and independently checked store/date price?"}
        Strict -->|"No qualifying store-price evidence"| None["Verified retail value ranking<br/>still empty"]
    end

    subgraph Roadmap["Planned — not deployed"]
        Licensed["Permission-gated offer feed<br/>personal-use scope only"] -.-> Batch["Weekly Cloud Scheduler<br/>and Cloud Run Job"]
        Batch -.-> Archive["Private Cloud Storage<br/>offer history + local replay"]
        Archive -.-> Stage
        Stage -.-> Advisory["Indicative price/protein<br/>with visible uncertainty"]
        Advisory -.-> Dashboard["Local-first review dashboard"]
        Generic -.-> Dashboard
        Dashboard -.-> Meals["Recipes + inventory<br/>three- and four-day planning"]
        Meals -.-> Mealie["User-approved<br/>Mealie shopping and calendar sync"]
    end
~~~

The private offline Tjek importer is an **input-validation/staging component**, not an online collection integration. Its one-chain, three-offer replay checked transcription, arithmetic and replay behavior against the same saved source; it cannot validate real shelf prices or claim a nine-chain live catalogue. The public demo remains entirely synthetic.

The planned product keeps **two separate evidence lanes**: an **indicative advisory view** for dated, attributed but unverified observations, and a stricter **verified price/protein comparison** that requires independently checked exact-product and store/date evidence. Neither lane is deployed in the public portfolio. Historical advertised-offer prices, if lawfully collected, would be labeled separately from independently verified shelf or receipt prices.

**Still not implemented:** a verified current-store price + exact SKU nutrition ranking, NutriValue dashboard, automatic basket optimizer, three-/four-day meal planning, live Tjek API collector, cloud scheduler or Mealie synchronization. No provider responses, private snapshots, personal location, purchasing evidence or licensed bulk nutrition data are published. See the [development snapshot](docs/DEVELOPMENT_SNAPSHOT.md) for evidence boundaries and the [product roadmap](docs/PRODUCT_VISION.md) for future milestones.

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

**Active WIP.** This public repository remains a **synthetic, tested SQL/Python portfolio**. The private project now also has a nine-chain, offline-only Tjek snapshot importer with private immutable staging, alongside offline evidence reconciliation. It has not shipped a live price comparison, scheduled collection or Grocery OS.

## Next steps

1. **Rights and scope:** review the exact permissions for automated access, private cloud processing and retention; extend tests of the existing *private* offline Tjek importer without redistributing or promoting provider data.
2. **Evidence review:** investigate five concrete private source-only candidates using the new conflict/exception foundation, preserving original source-family lineage and unresolved questions.
3. **Advisory MVP:** test a handful of dated single-retailer cards with explicitly indicative nutrition/cost assumptions; do not present them as verified deals.
4. **Strict verification:** establish independently checked store/date prices, exact branded product identity, measurement basis and edible package mass before real NOK/protein rankings.
5. **Future product:** only then extend to an honest local dashboard, historical-offer analytics, recipe/inventory planning and user-approved Mealie handoff. A weekly Google Cloud archive job is an **optional deployment plan**, not an active pipeline.

## Attribution and development approach

The private project uses [Matvaretabellen / Mattilsynet](https://www.matvaretabellen.no/en/api/) for Norwegian reference nutrition and has explored first-party KIWI, REMA 1000, Coop Prix and MENY offers alongside Kassalapp data. Restricted, owner-reported Tjek personal-use access has supported a bounded owner-local test and an offline saved-file importer in private development; it is **not** a public data license, network collector or scheduled integration. **No proprietary source datasets, Tjek payloads, captured provider responses, private accounts, API keys, precise shopping location or personal purchasing history are included here.**

The work uses substantial **AI-assisted development** together with manually directed problem definition, review, testing and implementation decisions. It is a practical demonstration of foundational SQL and analytical thinking, not a claim of previous professional product-analytics employment.

## License

[MIT License](LICENSE).
