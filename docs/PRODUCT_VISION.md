# NutriValue — Product Vision & Roadmap

> **Status: Active work in progress.** This document describes the **intended product**, not a deployed application. The public repository is a small, synthetic, runnable SQL portfolio snapshot of a larger private project.

## What am I building?

**NutriValue is a personal, nutrition-aware grocery and meal-prep planning system for Norwegian shopping.** The near-term private direction is an **advisory-first, single-retailer pilot**, with transparent approximate nutritional and price evidence; the longer-term goal is to answer a practical question:

> What should I buy and cook for my next three- and four-day meal-prep blocks to meet nutritional goals at a realistic price from stores I can actually use?

The starting point is straightforward: compare **NOK per 25 g protein** using actual edible package mass and credible nutrition data. The broader goal is not to create another generic grocery-price comparison site. It is to turn trustworthy prices, nutrition, recipes, available ingredients and preferences into **explainable shopping decisions**.

**Principle: correct and attributable beats broad and impressive.** An unknown price, mismatched food form or unverified retail product should remain visibly uncertain, not quietly enter a ranking. A useful **indicative** comparison can be shown with explicit limitations; it must never be mislabeled a **verified** current-store offer. The initial private shopping scope is **nine Norwegian retailer chains** rather than a nationwide catalogue; no home proximity, individual-store availability or local price verification is disclosed or implied here.

## Target experience (not implemented)

1. **Review food and price evidence.** Import permitted Norwegian nutrition/reference data and retailer/provider observations; preserve source, identity, date, store, units and verification status.
2. **Compare nutritional value.** Analyze price per package, per kilogram and per 25 g protein only when the observation is sufficiently comparable and documented.
3. **Plan practical meals.** Combine recipes, energy/protein goals, costs, prep time, available inventory and store constraints into a three-day and a four-day shopping/meal-prep block.
4. **Approve the plan.** Show what changed, why items were selected and which prices or substitutions remain uncertain. The user chooses what to buy and cook.
5. **Use a mobile-friendly companion.** A future, explicitly approved integration with Mealie would publish selected meal-calendar entries and shopping requirements, while NutriValue remains the analytical source of truth.

## Advisory-first MVP and two evidence lanes (planned)

The next useful product slice is **small and local**: start with a handful of candidates at one retailer, surface observed catalogue or locked-price information with its date, and show *indicative* calories/protein and cost only where the measurement basis is defensible. Unknown edible yield or missing offer dates remain unknown. A separate private five-card REMA experiment explores this workflow, but it is **not merged into the private main branch** as of this snapshot, and it is not available in the public demo.

- **Indicative advisory lane:** dated, attributed `source_only` retailer cards, optional conservative nutrition estimates, explicit package/edible-mass assumptions, and no claim of current shelf or weekly-offer verification.
- **Strict verified lane:** only independently checked store/date/offer evidence together with accepted exact product identity, edible mass and matching nutrition basis can enter a real NOK-per-protein ranking. There are **currently no qualifying public examples**.
- **Later user experience:** a read-only local-first review dashboard precedes any recipe, inventory, basket or three-/four-day plan optimization; all external shopping-list/calendar writes require explicit approval.

These lanes separate **useful but uncertain advice** from **audited price comparisons**. Neither has been deployed as a product.

## Intended architecture

This diagram **distinguishes the existing private offline foundation from planned components**. Dashed edges represent future wiring. The public repository does **not** run this pipeline; its smaller synthetic demo is shown in the [README](../README.md#architecture).

~~~mermaid
flowchart TB
    subgraph Foundation["Private local foundation — bounded implementation"]
        Retail["Retailer and Kassalapp<br/>source-only observations"] --> Staging["Attributed private staging<br/>with dated snapshots"]
        Nutrition["Matvaretabellen<br/>generic reference nutrition"] --> Ref["Separate reference-food records"]
        Staging --> Reconcile["Read-only identity hints<br/>source-family conflict review"]
        Reconcile --> Eligibility{"Independently reviewed exact product<br/>and store/date price?"}
        Eligibility -->|"Not yet"| Hold["Review queue<br/>no verified retail rankings"]
    end

    subgraph Future["Planned — not deployed"]
        Feed["Permission-gated<br/>weekly offer feed"] -.-> Scheduler["Cloud Scheduler<br/>Cloud Run Job"]
        Scheduler -.-> History["Private Cloud Storage<br/>immutable offer history"]
        History -.-> Staging
        Staging -.-> Indicative["Indicative candidate costs<br/>with uncertainty"]
        Indicative -.-> Cockpit["Local-first NutriValue<br/>review dashboard"]
        Ref -.-> Cockpit
        Cockpit -.-> Planner["Recipes, inventory, budget<br/>3 + 4-day meal plans"]
        Planner -.-> Approval{"User approval"}
        Approval -.->|"Approved"| Export["Selected shopping/calendar export"]
        Export -.-> Mealie["Mealie PWA<br/>planned mobile companion"]
        Export -.-> Offline["Markdown / JSON / CSV<br/>offline fallback"]
    end
~~~

**Important boundaries:** manufacturer nutrition review is not store-price verification; unrelated sites copying one upstream source do not count as independent corroboration. The future weekly offer archive is a **source-only history**, while strict verified-price comparisons remain empty.

### Technical boundaries

- **NutriValue** owns product identity, provenance, analysis, constraints, approval records and the proposed plan.
- **SQLite and SQL** remain first-class tools for inspecting calculations and explaining results.
- **Local-first web dashboard:** planned as a read-only quality/review cockpit before supporting changes or optimization.
- **Mealie:** selected *future* companion for recipes, meal calendars and checkable shopping lists, **not** the source of pricing or nutritional truth.
- **PikaPods:** selected prospective hosting option **for Mealie only**; no deployment or paid hosting is claimed. The NutriValue engine and its dashboard are not planned to be hosted there by default.
- **Provider independence:** export to local Markdown/JSON/CSV is the planned fallback. AnyList could be considered only with a verified supported integration.
- **Weekly offers:** a narrowly scoped, rights-approved **once-weekly** collection and private historical snapshots are the future direction; the owner reports restricted Tjek personal-use access, **not verified redistribution rights**, and no collector is installed.
- **Cloud infrastructure:** optional future Google Cloud Scheduler → Cloud Run Job → private Cloud Storage for weekly collection; local Python/SQLite replay remains available. **No NutriValue cloud job, bucket, scheduler or historical-offer chart exists yet.**

## Roadmap and evidence gates

| Phase | Goal | What counts as evidence |
| --- | --- | --- |
| **Foundation** | Relational schemas, deterministic calculations, tests and clear data boundaries | Queries and tests reproduce expected results without live-price claims |
| **Advisory-first pilot** | Small single-retailer price/nutrition candidate view with clear source and uncertainty labels | Reviewable, dated observations, explicit units, cautious estimates, no verified-price claims |
| **Verified inputs** | Connect exact SKU/EAN, edible mass, branded nutrition basis, observed price, date and store | A small number of independently reviewed, auditable end-to-end matches |
| **SQL portfolio** | Explainable nutritional analyses, documented limitations and reproducible examples | Inspectable SQL and fixtures; findings are labeled by evidence quality |
| **Early dashboard** | Local read-only view of data coverage, freshness, review queues and eligible comparisons | A tested interface that exposes uncertainty rather than inventing rankings |
| **Weekly deals** | Assess permitted nine-chain, date-aware advertised offers and store-specific availability limits | Confirm exact rights, test bounded offline imports, keep **source-only** offer facts distinct from verified store prices |
| **Offer history** | Preserve authorized weekly source-only offer snapshots and later compare like-for-like products | Legal retention permission, immutable revisions, missing-week flags; advertised trends are not verified shelf-price trends |
| **Shopping intelligence** | Recipes, reviewed price evidence, inventory, budget and three-/four-day planning | Explainable constraints and tested selection/quantity calculations |
| **Mobile handoff** | User-approved meal calendar and shopping-list sync | Tested API behavior, non-destructive updates, recovery, backups and offline export |

Phases are **direction**, not a release schedule or list of shipped functionality. The immediate direction is a limited, clearly **indicative** one-retailer advisory experiment; a genuine **verified** NOK/protein comparison still requires high-confidence matching of real store/date price, portion and nutrition evidence.

## What works today?

### In this public repository

- A reproducible **synthetic SQLite database**.
- Six standalone SQL reports covering fictional protein-value comparisons and reference nutrition analysis.
- Python price/protein calculations and six deterministic unit tests.
- An intentionally empty **verified offer** report; it is not a missing feature disguised as real data.

See [README](../README.md) for the quick start and links to the actual code.

### In private development (snapshot: 11 October 2026)

- Norwegian Matvaretabellen reference nutrition imported to local SQLite: **2,121 foods, 57 nutrients and 120,197 measurements**.
- Source-only/review-only collection experiments now cover **KIWI, REMA 1000, Coop Prix, MENY and Kassalapp**, including bounded public-page and offline PDF pilots. These inputs are not verified current shelf prices and are not republished in this repo.
- **Four private manufacturer/SKU nutrition records** were reviewed, but their associated price evidence remains **source-only**.
- A **35-category food-role vocabulary** and review-gated contextual substitutions support future meal-choice reasoning; there is no automatic SKU-to-reference match.
- The **offline evidence-reconciliation/exception-queue foundation** is merged on private main (PR #40), but remains candidate-only and limited in real-world scope. A separate, **unmerged five-card REMA advisory experiment** explores indicative candidate protein costs.
- A private nine-chain scope, reported limited Tjek personal-use access and a future weekly private Google Cloud archive have been selected. **No Tjek adapter, scheduled ingestion, deployed infrastructure or actual price-history visualization exists.**

**Not yet achieved:** an independently verified local retail price and nutrition ranking, a deployed dashboard, current weekly optimized baskets, completed meal plans or Mealie synchronization. See the [development snapshot](DEVELOPMENT_SNAPSHOT.md) for the distinction between private progress and what the public demo actually runs.

## Data integrity rules

Accurate grocery optimization depends on the **meaning** of the input data, not just the presence of numbers:

- An official reference-food ID is **not** a branded retail EAN/SKU.
- Raw, cooked, drained and as-sold measurements require deliberate matching.
- Package weight must not silently substitute for edible mass.
- A provider-reported listing is not proof of a current shelf price or a valid discount.
- Repeated copies of one original retailer promotion or manufacturer declaration do not count as independent evidence.
- Indicative calculations stay explicitly separate from stricter verified comparisons.
- Missing data stays unknown, and unsupported price or product matches remain excluded or flagged.
- Source permissions and redistribution rights must be checked separately from technical access.
- Recipe swaps are contextual, not automatically equal-gram or nutritionally equivalent.
- External shopping-list changes should require approval and must not overwrite manual items.

## Why publish an early snapshot?

This portfolio is intentionally narrower than the product vision. It demonstrates foundational **SQL modeling and analysis**, reproducible results and careful treatment of uncertainty. Publishing synthetic, inspectable examples is more useful than implying that unverified Norwegian grocery prices or a complete optimizer already exist.

The larger project is under active **AI-assisted development**, with human-led goals, review and validation. The public repository is a curated independent extract, **not a mirror** of the private development history. This snapshot updates the narrative; it does **not** add the private integrations, their raw source captures or live price data to the public demo.
