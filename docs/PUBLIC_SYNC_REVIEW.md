# Public sync review — 31 October 2026

## Scope and exclusions

Selective source export without importing private commits. On 31 October
2026, the public main branch was rebuilt as one parentless `Initialize` commit
and the obsolete public feature branch was removed, as requested. Reviewed additions include GET-only Mealie recipe snapshots,
offline reviewed-source nutrition estimates, per-chain weekly fallback/health
SQL, rejected-offer isolation and quarantine-aware page selection. PikaPods is
shown as hosting for the external Mealie/Grocy applications, with their authority
and integration boundaries documented in the architecture.

The offline public product-candidate scorer and anonymized generic reference
metadata are intentionally preserved. Live product-search pilots, deployment
scripts/configuration, operational CI, private runbooks/ledgers, personal
nutrition/health/routine documents, stock, real recipes, provider captures,
databases and dumps are excluded. No production account was accessed or changed.
The existing synthetic review screenshot and its approved content hash are unchanged.

## Sanitization

- Removed the Mealie instance default. Execution requires an explicit HTTPS
  address; plan-only and saved-snapshot modes make no requests.
- Retained environment/optional Keychain token lookup without bundling a token.
  Tests use fictional recipes, identifiers and credentials with fake HTTP.
- Added publication checks for unreviewed datasets/formats/binaries, symlinks,
  hosted instance URLs, credential-bearing URLs and non-example email addresses.
  Diagnostics show rule names, never matched content.
- Checked the complete candidate tree and known private hosting identifiers;
  reviewed new code and schema content before staging. Generated test/demo
  databases and reports are ignored and excluded from publication.

## Validation

- Synthetic demo builds and produces six SQL reports; strict real-price
  eligibility remains empty by design.
- 12 root tests pass, including six adversarial privacy-guard tests.
- 832 portable engine tests pass, including local HTTP review, synthetic Mealie,
  nutrition, floating-point price validation, rejected offers and weekly fallback.
- Published Python compiles; tracked Python syntax parses for Python 3.10.
- Offline publication guard and diff-integrity check pass.
- Gitleaks 8.30.1 (release archive checksum verified) reports no findings in the
  complete staged candidate tree. The three pre-existing public commits were
  also scanned: one finding is an explicitly synthetic OpenRouter test sentinel
  in the initial public snapshot. The candidate replaces it with a plainly
  synthetic token. The later one-commit public reset removes that old history
from the public branches; it does not guarantee removal from GitHub caches,
forks or historical pull-request references.

## Limits and remaining risks

The complete historical test discovery was also attempted. Four modules depend
on deliberately unpublished inputs: `test_promotion_pipeline` (standing-benefit
registry), `test_weekly_cloud` (cloud entry point/registry),
`test_rema_flyer_archive` (archive manifest/policy), and
`test_sequential_workflow` (private CI/operator files). The public test runner
reports these exclusions explicitly; their results are not claimed as passing.
Local review HTTP tests pass with permission to bind a loopback port.

Secret scanners and manual review cannot prove the absence of every possible
sensitive string. Old public history, GitHub caches/forks and author metadata
are separate from this candidate-tree review. No real credential was identified
in the reviewed candidate; the historical scanner finding is a test fixture.

Nutrition totals remain estimates for mapped ingredients. Missing amounts,
density, servings or nutrient fields require review; generic references and
user-reported labels do not verify exact branded products or consumed meals.
Fallback weekly data can be older and is marked accordingly. No live API
compatibility, PikaPods health, complete planner, Grocy adapter or Mealie write
integration is claimed. Source redistribution rights remain separate from code.
