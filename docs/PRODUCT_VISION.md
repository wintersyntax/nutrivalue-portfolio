# NutriValue — product vision & roadmap

**Public portfolio updated 25 October 2026; implementation evidence remains source-scoped.** The public portfolio now contains substantial working Python and SQL engineering source, not just a synthetic proof of concept. The project remains an active, evidence-conscious work in progress.

## Why build it?

NutriValue brings Norwegian grocery offers, price history and trustworthy food-composition data together to build practical, evidence-aware shopping and meal-prep decisions **within NutriValue**:

> Given the food I already have, nutritional goals, recipes, shopping options and offers I can actually use, what should I buy and cook for the next three-day and four-day blocks?

A trustworthy assistant must account for exact products, edible quantity, membership and promotional conditions, price validity, nutrition basis, store restrictions, substitutions, prep time, leftovers and human approval. Depending on the user's goals, comparisons can use price per package, edible kilogram, portion, protein gram or energy. The system must **explain** why it recommends something.

## The implemented engineering foundation

The public [engine](../engine/README.md) includes code for:

- Bounded retailer/provider catalogue and source-only observation workflows.
- Captured-page identity, hashes, OCR/layout research and structured model-assisted promotion extraction.
- Norwegian grocery categories, offer terms, bundles and unresolved-item handling.
- Real local/hosted review interface logic, human decisions and replay-safe append-only review events.
- SQL stores and reports for source-only weekly offers, coverage, promotion review and historical comparisons.
- Generic nutrition imports, GTIN validation, SKU/manufacturer evidence review and Decimal protein economics.

This work is **not** an externally verified current-price database or a ready-made hosted public service. Captured provider data and private deployment configuration are not shipped. The [standalone demo](../README.md#run-the-offline-synthetic-demo) uses fictional values deliberately.

## Roadmap

These are phased capabilities **of NutriValue**, not another product or add-on. The first meaningful planning implementation is deterministic and reviewable; a speculative advanced optimizer is explicitly deferred.

1. **End-to-end synthetic reviewer example.** Add a realistic but fictional database, page image and annotated screenshot so readers can reproduce the human-review flow without raw provider data.
2. **Indicative grocery advisor.** Show a small attributed, dated basket/offer sample and explicit uncertainty, including member-only conditions, deposits, price basis and missing nutrition fields.
3. **Exact-product verification.** Connect evidence for EAN/SKU, food form, edible mass, label nutrition, retailer, date and independently checked price before enabling strict real-price protein rankings.
4. **Versioned recipes and deterministic 3+4-day planning.** Start from reviewed recipe ingredients, raw/cooked/edible forms, reliable nutrition basis, complete package purchases and a realistic budget. Produce drafts with explicit user approval; a plan does not prove a meal was eaten.
5. **Inventory authority and optional handoff.** The planned Grocy/Pantry Raider adapter reads a time-pinned inventory snapshot as the sole source of stock truth; planned Mealie sync publishes only owner-approved managed shopping/recipe/meal entries. Neither adapter, TickTick dependency nor consumption import is deployed. No unsupervised purchases, inferred stock transactions or silent account writes.
6. **Later optimization only if justified.** Compare valid basic plans using optional store-trip, waste and meal-repeat penalties *after* deterministic plans work against reviewed inputs and offline positive/negative tests. No multi-objective solver is running today.

## Deliberate boundaries

Human acceptance of a printed promotion is **not** confirmation of a checkout price. Two model outputs agreeing is **not** independent ground truth. Generic Matvaretabellen nutrition is **not** an exact branded SKU identity. Capturing data through a private authorization is **not** authorization to republish the provider's data.

That discipline matters more than an impressive-sounding but unrepeatable price comparison.

[Architecture & review](ARCHITECTURE_AND_REVIEW.md) · [Development status](DEVELOPMENT_SNAPSHOT.md) · [Changelog](../CHANGELOG.md)
