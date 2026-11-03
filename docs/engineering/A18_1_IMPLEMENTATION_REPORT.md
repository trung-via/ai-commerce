# A18.1 M06 implementation report

Source main verified at start: `b08d33f3195c48559d4964cf36c8b03612e41dd9`.
The worktree was clean and all ten target paths were absent. This is a first
implementation from that source, with no snapshot recovery or replay of another
provider attempt. Authority: Human ChatGPT 2026-11-03 @AI Commerce,
`TRIỂN KHAI a18.1`.

## Product behavior and limits

Commerce Opportunity owns a bounded, deterministic offline API. Native typed
selectors pin source bytes and reopen custody through existing A10, A11, A15 and
A16 read APIs on every evaluation. There is no new ledger. Exact request scope,
evaluation clock, measurement windows, native MetricDefinition.v1 and currency
are required. Product, creative, account and commercial evidence may be absent;
no SKU, offer, settlement, permission, revenue or ad spend is a global gate.

`recommend_portfolio(request, EvidenceBundle(...))` returns immutable
`PortfolioRecommendation.v1` bytes. `recommend_many` evaluates at most sixteen
independent candidates in stable candidate order. No portfolio ranking or shared
privilege exists. `validate_recommendation` checks structure, content identity and
projections; loading an artifact is explicitly **not** renewed native custody.
Call `recommend_portfolio` again to revalidate retained native source pins.

The current native contracts cannot supply provider-authoritative observations:
A10 is a creative hypothesis, A11 is descriptive and user supplied, A15 charges
are supplied assertions, and A16 explicitly sets verified orders/profit/settlement
to false/unknown. Therefore this version returns INCONCLUSIVE even when a Human
threshold would otherwise suggest KEEP, CHANGE or STOP. Its portable contract
also prevents resealing a decisive result. Synthetic tests exercise these safe
outcomes; they are not product live outcome evidence. Confidence is an evidence
rating, never an invented probability. A future decisive route requires a
separately reviewed authoritative native contract and evaluator version.

Human policy is editable data with explicit validity, freshness, metric hash,
currency, KEEP and STOP boundaries and CHANGE between them. It grants no action
authority. Default missing-policy freshness is 2,592,000 seconds (30 days).
Expired policy, future/stale evidence, incompatible windows, scope, metric or
currency, counterevidence and upstream HOLD are explicit reasons for Human review.

Local A11 measurements preserve numeric zero and null independently. Archived
or unlinked measurements cannot assert a numeric value; comparisons require
replayed baseline/current reports, complete native comparability and explicit
windows. A numeric change is descriptive only, with no causal uplift or ROI.
Costs retain job/attempt/intent/provider identity. Estimated, supplied charged
and synthetic charged subtotals stay separate by currency using Decimal strings.
Missing categories remain null. No FX or native campaign allocation is invented.
A16 embedded costs are references and are never summed a second time.

The M07 bridge `own_brand_reference` produces `PortfolioRecommendationRef.v1`
with exact ID, bytes hash, scope hash, source pin and clock, no execution authority
and no financial commitment. It does not implement A18.2 or A18.3. Optional L07
integration is deferred: M06 consumes the authoritative native owners directly.

## Custody, privacy and effects

Decision JSON and projected fields reject extra keys, duplicate keys, nonfinite
numbers, oversized/deep data, URLs, credential fields, control characters and
filesystem references. Retained native `raw_utf8` envelopes can contain existing
renderer/tool metadata and provenance URLs: mandatory native replay validates
those opaque bytes, and M06 never dereferences or projects them. This exception
does not admit a `raw_utf8` field into any decision schema. Source parsers and
native validators run before projection; raw text and private records are not
copied into diagnostics or recommendations. Source receipt hashes and
native references identify local bytes; they never attest provider authenticity.
Missing native custody is explicitly UNVERIFIED or ARCHIVE_VALIDATED. Corrupt or
changed origins raise bounded errors and cannot reuse a previous result. Caller
pins must be retained independently; this pure module cannot detect replacement
when a caller deliberately replaces both source bytes and its retained pin.

Only explicit native read APIs access pinned local stores. There are no file
writers, discovery paths, HTTP, socket, remote model, account login, campaign
mutation, inventory/capital action, publish, mail/SMS, webhook, Wake or HostOps
interfaces. Native store handles are vetted local capability objects supplied by
the caller; they must not be attacker-controlled Python implementations.

## MT01 owner and verification

One data-only `MT01ComponentOwner.v1` manifest declares four new Python sources,
two new tests, one portable contract and the exact M06 acceptance ID. Before
writing it, the actual protected `approved_groups()` owner map and registered
profiles were read. Consumer ownership is A10 planning, A11 export/reporting,
A15 cost ledger and A16 review. No owner or verification registry is edited.
`mt01-component-onboarding` admits the new owner as data and resolves its tests
plus the registered affected consumer closure. Missing coverage must HOLD;
an unrelated full suite cannot substitute for ownership.

Fresh local Windows verification used the unchanged registered onboarding
planner and protected owner/profile maps. It admitted the data-only new owner,
selected exactly `a10`, `a11`, `a15`, `a16`, `a18-1`, resolved all five consumers,
reported no missing mapping, unknown AC or coverage HOLD, and used no full-suite
fallback. Every group executed its registered test files and `-n 12`; additional
pytest arguments only disabled cache and routed temporary/JUnit artifacts outside
the worktree, with short tracebacks. The protected pytest plugin emitted actual
completed-worker collection evidence, independently of startup text.

| Group | Selected files | Collected / selected tests | Passed | Failed / errors | Skipped | workers_actual |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| a18-1 | 2 | 125 | 125 | 0 / 0 | 0 | 12 |
| a10 | 4 | 271 | 271 | 0 / 0 | 0 | 12 |
| a11 | 4 | 223 | 220 | 0 / 0 | 3 | 12 |
| a15 | 1 | 43 | 43 | 0 / 0 | 0 | 12 |
| a16 | 2 | 110 | 110 | 0 / 0 | 0 | 12 |
| Total | 13 | 772 | 769 | 0 / 0 | 3 | 12 per group |

Each group emitted exactly `MT01_XDIST_WORKERS_COLLECTED=12`; counts above were
read from its generated JUnit, not inferred from filenames or requested workers.
The three unchanged A11 skips are `tests/test_a11_reader.py:403` (OS denied a
symlink), `tests/test_a11_registry.py:495` (symlink), and
`tests/test_a11_registry.py:511` (hardlink). No A18 test was skipped. All selected
commands exited zero. The worktree hash map was unchanged during verification.

Initial new-owner verification was 111 passed / 10 failed (121 collected, 12
actual workers). Repair round 1 corrected the native opaque-envelope privacy
boundary, tightened projection/provenance checks and added four relevant cases;
it produced 124 passed / 1 failed (125 collected, 12 actual workers). Repair
round 2 retained the native A15 record checksum for an estimate without inventing
a provider receipt; the complete selected closure then produced the table above.
Both same-attempt repair rounds are exhausted. No unknown attempt was reused.
Only this report and WorkItem verification metadata were updated after the final
run; tested source, contract, manifest and test bytes remain frozen below.

These are local `win32` checks, not proof of EF02 service identity or a published
candidate. No candidate SHA or independent `build-verified-for-scope` is claimed.
Independent EF01/EF02 frozen hashes, accepted closure and build-verified-for-scope
remain required before the EF02 worker publishes a candidate. Hosted Windows PR
CI uses four workers and cannot establish self-host twelve-worker collection.
No other OS gate or workflow is introduced. The requested executor preference
is recorded as `codex / gpt-6.1-sol / max`; this report does not attest platform
model routing or replay an unknown attempt. Maximum same-attempt repair rounds: 2.

## Tested candidate hashes

These eight tested files were unchanged after the final successful execution.
The report and WorkItem receive final hashes in the executor's closing scope
audit; a file cannot embed its own complete content hash.

| Allowed tested path | SHA-256 |
| --- | --- |
| src/ai_commerce/opportunity/portfolio/__init__.py | 88337f52d3f61221927d1039a5d3f7ab49f9549bcfde26e9ef7e90f6df796312 |
| src/ai_commerce/opportunity/portfolio/schemas.py | 0777b9d8324df5057f4d69f52539b2f6121acbc3d3821a97572d4363688dcbad |
| src/ai_commerce/opportunity/portfolio/sources.py | c9a631fb665f61cdcd67e42e142cde009121fdc462e502d72337fbe57e7d13eb |
| src/ai_commerce/opportunity/portfolio/recommend.py | 17a064d2c42a7e210b4c2e4e4e0b06464110c2511d85831e39fe4f31d005554d |
| contracts/PortfolioRecommendation.v1.schema.json | b2d0671b3f90c996d8defd614f1e1a7542f9c778835d0382241f5e9482115a9d |
| tests/test_a18_portfolio.py | 25ecfdbb96bd61427f194054d458863427d9c7ae259f6e0c3a63668f8059d27b |
| tests/test_a18_portfolio_integration.py | 38cf7fd1d1ffc064c03dafe415c5519830bd39ddf6c7e1943753bc121b099760 |
| work_items/mt01-components/a18-1.json | 7770b4333da699025d3126ed2dae4755b9b174ea462ccf528eb41d659fe27d49 |

## Frozen existing interfaces

The following source hashes were read before any implementation edits:

| Existing path | SHA-256 |
| --- | --- |
| opportunity/proposals/planning.py | 1e3234bde87bdb614b9a2335c722116d6de61246ef8091d080b483d84b9b12e7 |
| opportunity/proposals/schemas.py | 04a5c82766b669c1fd443e5aa84123953d72bf384f0fc6d9b7cac3fefb1b1f43 |
| learning/export.py | 41f7cf76279f3e05e1c072fc68e42b54bb6209f2070adeff624af7103ec811b7 |
| learning/reporting.py | 55d347f799e548f97836468d3459da79111db211aa5b91fbaa414200af63e8ab |
| operations/cost_ledger.py | 2afb7d0c05b6e90d75020c8b04df5c218c3f7bc609b84f16bb3c721dc66dc969 |
| operations/status_report.py | 8e8365dc440f14b0fa6d8b9bea1acd987cb698d637436e5cf6ba0a17a35eef97 |
| settlement/review.py | 44851e8a755b07ff359a2047fe0175dfe261d89b49c75fbd8b5c6a76fa420c86 |
| .github/ef02/verify-profiles.json | 7135eca52af7bf296a2f2753319ef8e878086d5ff6727ab138673d15fded20d0 |
| engineering/minimum_test.py | 20ecbcefa133d40e753d2d9f699801b5c5145ec85a2ad93d40babb84dd407a74 |

Source paths in this table are relative to `src/ai_commerce/` except `.github/`.
No Git mutation, candidate branch, workflow dispatch, PR or merge is performed by
this executor. Publication authority remains with the independent EF02 worker.
