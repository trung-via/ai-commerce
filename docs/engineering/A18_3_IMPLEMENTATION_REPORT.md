# A18.3 E07 offline scale and own-brand decision memo

Frozen source: `62a29f0e9938de0f23540458d9c04522c43fc8e9`.
Human authorization: 2026-11-03 @AI Commerce “kiểm tra, tiếp tục”.
The initial checkout HEAD matched that full SHA, porcelain status was empty,
and all ten authorized paths were absent before implementation. The baseline
contains 726 files; its canonical file/hash map SHA256 is
`d129cf2826514ee8880a7e6a0238496f7fbc0dbf6ce0cb2737d765c63a30fec6`.
No existing source, registry, test, profile, roadmap or Git state is changed.
Publication belongs to the EF02 worker after independent verification.

## Scope and entry point

Commerce Opportunity owns `scale_option.build_memo(request, sources)` and
`validate_memo`. This completes the offline E07 memo slice of Roadmap A Phase 5
and Work Tree E07, following merged M06 PR #305 and M07 PR #310. It adds no
provider integration, UI, CLI, evidence store, network client or writer.

`ScaleBrandEvaluation.v1` takes an explicit as_of clock, currency, scope,
Human declarations, bounded experiment scenarios, permission assertions and
external Human policy caps. The private frozen `ScaleBrandDecisionMemo.v1`
retains only canonical bytes; dictionaries returned to callers are detached.
Its body SHA256 identifies `a18-scale-brand-<hash>`. The separate `sha256`
property hashes the complete canonical transport bytes. Both can be pinned
when rereading a memo. The portable Draft 2020-12 contract exactly mirrors the
runtime-generated closed schema; validators use an empty local registry.

Every outcome carries execution_authority=NONE, financial_commitment=NONE,
external_live_verified=false. No outcome can authorize expenditure, accounts,
campaigns, supplier contact, purchasing, stock, capital or commercial launch.
Human policy assertions are unauthenticated data, never an authorization API.

## Independent lanes and arithmetic

The portfolio scale lane compares three explicit Human scenarios for asset
count, provider/channel/operations/fee costs per asset, fixed cost, reserve and
available capacity within one experiment window. Channel identity uses the
explicit platform, surface and account scope. Hypothetical value per asset is
optional only in COST_ONLY_RESEARCH; value-based comparisons require an
explicit hypothetical value in every scenario. There is no inferred uplift,
CTR, conversion, demand, revenue, return rate or probability of success.

All monetary inputs are bounded Decimal strings in one currency. Integer
counts are distinct from null. An isolated 80-digit Decimal context rounds
results HALF_UP to six places. Time intervals use integer microseconds;
throughput calculations use Decimal days. No binary float money or FX exists.
Unit cost is the sum of the four declared per-asset components. Experiment cost
is units times unit cost plus fixed cost. Cash exposure adds the reserve once.
The conservative downside assumes zero hypothetical value and puts every
scenario's full cost and reserve at risk. Every scenario is checked against
budget, spend, downside, size, unit-cost and capacity caps. Sensitivity changes
only the affected scenario; the worst exposure is the maximum across all three.

The own-brand lane reuses M07's native feasibility results unchanged, with its
calculation hash, MOQ, research budget and unit cap. M07 retains ownership of
contribution, inventory, capital, loss and break-even arithmetic. E07 compares
those retained results with the external E07 caps and never repeats M07's
calculation formulas or substitutes a brand idea for verified demand.

Each lane has its own state, blockers, rationale and score-free alternative
paths. DEFER takes precedence for missing, stale, contradictory or incompatible
required evidence. DO_NOT_SCALE records violated Human economics/capacity/risk
limits. CONDITIONAL_VALIDATE means a small bounded Human-controlled offline
research, prototype or scale experiment only, without irreversible capital,
stock, account or campaign effects. No GO_SPEND, GO_PURCHASE, GO_LIVE or GO_STOCK
state exists. The overall memo conservatively combines applicable lane states;
one successful lane never clears the other lane's blockers.

For product_ref=null the own-brand lane is NOT_APPLICABLE. Scale validation
remains optional and independent. With neither lane requested the memo records
DEFER for no requested decision while explicitly allowing optional creative work.

## Native provenance, archive and privacy

M06 uses the real PortfolioRecommendation.from_bytes/to_bytes and its pinned
M07 reference bytes. M07 uses the real OwnBrandDecisionBrief.from_bytes/to_bytes;
CURRENT also requires its exact original request bytes and original typed
OwnBrandSources, rebuilding the original brief with byte-for-byte equality.
Optional A16 uses the real CommercialReview.from_bytes/to_bytes and exact native
run/history/cost reference hashes. Its current custody replays the original
run, settlement ledger and cost selections. A15 uses the actual CostLedger
get/summarize and CostEntry.from_record checksum validation.

M07's retained native ARCHIVED_ONLY/CURRENT_REPLAY grades remain explicit.
E07's current grade additionally requires every original inner M06 source to
have native replay custody. Replaying M07 arithmetic over an archived inner
M06 bundle cannot renew that bundle. A source without its actual original
native custody is historical only. Memo.from_bytes replays content and E07
decisions; it never reopens or attests current custody. Hashes identify content,
not authenticated providers or Human authors.

Original clocks are never replaced on replay. Observation timestamps are checked
at E07 as_of. When M06 does not expose all interior timestamps, a later clock or
stricter age policy defers rather than renewing freshness. Product, account,
market, provider, platform/surface, creative, currency and measurement-window
mismatches defer; partial joins are explicit. M07 cannot turn a null product
into a verified product-specific demand claim. Cross-source IDs and cost
versions must match exactly or validation fails. M06 KEEP/CHANGE/STOP remains
provisional descriptive advice with no action authority.

Changed/revoked/expired rights, incomplete permission pins, proposed live
scaling and irreversible effects defer. Pending commercial evidence, refunds,
revocations and upstream holds remain explicit; the own-brand lane requires
reconciliation. Even a supplied SETTLED receipt does not verify paid orders,
actual settlement, real fees, returns or profit. Those actual outcomes remain
UNKNOWN, and validated provider receipts are NONE on every outcome.

A15/A16 amounts are reference-only. This version deliberately accepts no
automatic ledger attribution or imported subtotal: Human estimates are explicit
scenario components, with HUMAN_ESTIMATES_NO_IMPORTED_TOTALS as the only allowed
lineage basis. Shared cost references are not summed; duplicate cost aliases,
checksums, changed versions and receipt collisions are guarded. Any later
attribution feature would require separately scoped Human attribution and
compatible native scope, with new coverage.

Closed bounded JSON rejects unknown keys, duplicate keys, nonfinite/binary
float values, overflows, deep/cyclic objects, oversized arrays/text, credential
fields, URLs, paths, contacts and Unicode-obfuscated injection. Private Human
rationale/uncertainty/counterevidence text becomes hashes. Native raw envelopes,
receipt paths, supplier quotes and personal contact text never enter the memo.
Errors are fixed codes, including unreadable native custody failures. Exact
native types are checked before invoking native methods; caller callbacks and
freeform performance/order/settlement dictionaries are not accepted.

## Changed file inventory and ownership

Exactly ten files are added:

- src/ai_commerce/opportunity/scale_option/__init__.py
- src/ai_commerce/opportunity/scale_option/schemas.py
- src/ai_commerce/opportunity/scale_option/sources.py
- src/ai_commerce/opportunity/scale_option/memo.py
- contracts/ScaleBrandDecisionMemo.v1.schema.json
- tests/test_a18_scale_option.py
- tests/test_a18_scale_option_integration.py
- work_items/mt01-components/a18-3.json
- work_items/A18-3-SCALE-BRAND-001.json
- docs/engineering/A18_3_IMPLEMENTATION_REPORT.md

The sole MT01ComponentOwner.v1 manifest owns four sources, two tests, one
contract and AC A18.E07.offline-scale-brand-decision-memo. The five consumers
are portfolio/recommend.py, own_brand/brief.py, own_brand/sources.py,
settlement/review.py and operations/cost_ledger.py. An AST test proves these are
all direct production domain imports. The serializer uses standard-library
JSON/SHA256 with the repository's canonical-byte convention, avoiding another
shared source dependency. No extra direct producer owner is introduced.

Protected approved_groups, persisted immutable owner manifests and registered
verify profiles were inspected. The live registered onboarding planner selected
exactly a15, a16, a18-1, a18-2, a18-3 with no missing mapping, unknown AC,
coverage HOLD or broad fallback. Their native upstream integration tests remain
included. Separate A10/A11/A12/A14 or blanket full-suite execution is omitted
because E07 has no direct production import into those domains; the real M06,
M07 and A16 consumer groups cover their retained native contracts. No protected
owner/profile or CI changes are made.

## Verification and delivery boundary

Tests cover conditional synthetic/native research, every cost/capacity/cap
boundary, missing versus zero, both lane directions and content-only scope,
real native rereads versus archived custody, changed pins/contracts/costs,
source conflicts, rights, refunds/pending statuses, denied reads, privacy/size
bounds, immutable deterministic bytes, Decimal isolation, schema mirroring and
no network/writer/process side effects. Owning validators are never bypassed.

Initial owner preflight: 213 tests passed; oversized JSON pytest parameter labels
exceeded Windows' 32767-character environment-variable limit and caused setup
or teardown errors. The first local repair gives those payloads short explicit
IDs, tightens nested native custody and archive projection checks, and adds
regression coverage. The original failed stdout/JUnit hashes are retained in
the WorkItem; its bounded stdout excerpt omits only oversized parameter labels.
No blind retry or third repair is permitted.

Final Windows registered closure receipts, exact tested hashes and provenance
are appended after the frozen run. The WorkItem and this report are then the
only metadata updates; source/test/contract/manifest bytes must remain frozen.
The local executor cannot independently attest the Windows runner-service
identity. Actual platform, per-group completed-worker hook output, JUnit hashes
and controller output are recorded, rather than inferring workers from argv.

No executor commit, branch, publication, PR or merge is performed. Independent
EF01/EF02 verification, subsequent Windows-hosted exact-candidate-head CI with
four workers, and Brain acceptance/auto-merge remain later review gates. This
local result does not stand in for any of those independent attestations.

## Final frozen Windows verification receipt

Local repair round 1 completed the full registered closure: **702 collected,
702 passed, 0 failed/errors/skipped across 9 test files**. Groups ran sequentially
with max_parallel_groups=1. Every registered argv ended in `-q -n 12`; the
protected collection hook emitted exactly `MT01_XDIST_WORKERS_COLLECTED=12`
in each controller. Actual completed collections, rather than requested workers
or startup banners, establish the worker count. Platform was win32 throughout.

| Group | Profile | Files | Passed | Actual collected workers | Duration seconds |
| --- | --- | ---: | ---: | ---: | ---: |
| a18-3 | mt01-component-onboarding | 2 | 216 | 12 | 23.26 |
| a15 | mt01-a15-tests | 1 | 43 | 12 | 4.183 |
| a16 | mt01-a16-tests | 2 | 110 | 12 | 18.322 |
| a18-1 | mt01-component-onboarding | 2 | 135 | 12 | 17.179 |
| a18-2 | mt01-component-onboarding | 2 | 198 | 12 | 23.152 |

| Group | JUnit bytes | JUnit SHA256 | Controller stdout SHA256 |
| --- | ---: | --- | --- |
| a18-3 | 33643 | a83767ed531f95833369f8608035d8cb24398604255b58e999ea70efff972e69 | 8776be3e6d30f7758fc46f6689a3b45a1f01cadb2663c620fc6f276f85f87fca |
| a15 | 5713 | 4fe00f4e677640683bf3483ef70605253fac850a7c92de903d1bceb6cd645dc4 | 75355b04744e7ac60cdb45ba30aca7ebd3489b464a4cc7d79cdce33096894931 |
| a16 | 14608 | 4f107729fd3a07f8590aad1bef519dbf84b6f5252ff5b3387637d6fea8517814 | 2291b5090d47eaaf870654276051d480b99b6094930e1d2944d27472a6dadd50 |
| a18-1 | 20167 | a331c3eb81119a6ae538971c55f743035b2b1cd45e8454ae3e6eb8aadb4f9f4f | 5ed220def75acdd94ce76a42e3fa5f48b4a729c8cc956e8914e4de8b48d5da48 |
| a18-2 | 30358 | bdbabfab6292c45ab9c7fba5ac11a0dbac7f2b0e70b0f6a57c11ebfff69924a4 | dc2684985c051fb0a1c0ff5b4b0b83e313f7878fa30890df179bc1656e074a66 |

All ten file hashes remained unchanged during this entire final run.
HEAD before and after was `62a29f0e9938de0f23540458d9c04522c43fc8e9`. The 726 preexisting files
retained the initial fingerprint recorded above. One local repair was used;
no independent EF01 repair round has been observed by this executor. No
blind retry, second local repair or third repair was performed.

Initial non-admitted owner preflight JUnit SHA256: `99809ffaebc00ea17851a4bbf88da1703674a2985b38c18458214f3c6725865c`.
Initial controller stdout SHA256: `6118f28acbb3da6f6922b802aaf1b860fe2513fd089bae25eb2f9e811eea67b5`.
The initial console reported 213 passed and two setup/teardown errors on one
oversized-parameter testcase; its JUnit testcase projection contained 213
passing cases and one errored case. Short parameter IDs corrected that Windows
environment-variable failure, and the final complete group passed all 216 cases.

JUnit files were created in isolated Windows temporary storage. Exact hashes,
byte lengths, parsed counts and actual controller output are retained in the
WorkItem. Temporary paths are not promised as durable delivery artifacts.

## Exact tested candidate hashes

These eight files remain byte-for-byte frozen after the tests. WorkItem/report
pre-test hashes are also retained in pre_test_all_ten_hashes; only these two
nonexecutable records receive the subsequent verification metadata updates.
An independent worker must bind the final ten-file state before publication.

| Tested added path | SHA256 |
| --- | --- |
| src/ai_commerce/opportunity/scale_option/__init__.py | 00c7eb871434317d27a940165b3984c78d9ad6876efaa655acb28a5d7a3090bb |
| src/ai_commerce/opportunity/scale_option/schemas.py | 604eb7a480c6f326e06631916a84be85117430b64642d5974e66a3a6c9563986 |
| src/ai_commerce/opportunity/scale_option/sources.py | 56c3901c243360a3cd42e32beab48acac650e3c5943f94068b0b3c2833c297c0 |
| src/ai_commerce/opportunity/scale_option/memo.py | a366051c27b60b94fead1a1b1176298e3acb29b4ede930ab58ce6ffb5f1297cf |
| contracts/ScaleBrandDecisionMemo.v1.schema.json | 82932cd08a6f52965e0e697731a6f6e276ee9900d736bf3b143c4d151d1d5143 |
| tests/test_a18_scale_option.py | 02c6928276748afde0062a857f09e59c1175ab233b1984aed0bdc0ced909163d |
| tests/test_a18_scale_option_integration.py | 4f3566dab2e4fd8a44264ca0dfb5196b4378d99834f5925f63f39297c8df6735 |
| work_items/mt01-components/a18-3.json | 1624431cbdd207ef601056d4d078e6983a7446e27d0db79b6097376bc98156ac |

## Frozen baseline contract and consumer pins

All hashes below refer to the required original source HEAD and remained
unchanged throughout implementation and testing. Native content identities
additionally retain their own M06/M07/A16 schema/evaluator/source version pins
at runtime; those historical evaluator source SHAs are not replaced by E07.

| Original source or contract | SHA256 |
| --- | --- |
| src/ai_commerce/opportunity/portfolio/recommend.py | 69fa454fa32e22d9be5310bf7bb7bc036cad55e1fa3f40116b9cf4ef151c7b98 |
| src/ai_commerce/opportunity/portfolio/schemas.py | fd4bab5387de8c90d3df8962d04c89b55910794d4bf55b9e3ce831c5f322cf58 |
| src/ai_commerce/opportunity/own_brand/brief.py | da946e0cd570664cadbd9fcde15cf74767221c1017d9ca9e389c1ba182771f3a |
| src/ai_commerce/opportunity/own_brand/sources.py | 7d9d468ec753da4fb9a3885f63d0d4e941247b0cabf03a39091aa5fd40fb7972 |
| src/ai_commerce/opportunity/own_brand/schemas.py | c73a0672ee8e337546d8dac7369dd7126d029f4760e94341b384320e0d68aea3 |
| src/ai_commerce/settlement/review.py | 44851e8a755b07ff359a2047fe0175dfe261d89b49c75fbd8b5c6a76fa420c86 |
| src/ai_commerce/settlement/ledger.py | d69193aee54e8a357a2bf8b50c99f9f07ba5b867e7bd14ecc6421ea450654fd1 |
| src/ai_commerce/operations/cost_ledger.py | 2afb7d0c05b6e90d75020c8b04df5c218c3f7bc609b84f16bb3c721dc66dc969 |
| contracts/PortfolioRecommendation.v1.schema.json | 62e3b3a023f58b77e8fa86c87b4d51aeda24d6af5787fe0492fbe3a9ef52b8cd |
| contracts/OwnBrandDecisionBrief.v1.schema.json | 35206d6787bd775e39b2e912f5739308958bde1c4879c556f4eb579417ed3ddf |
| contracts/CommercialReview.v1.schema.json | a76916f6cbb97b68f7e4bc78577469e96f7aad4c3977916961eb0b2d20166cbb |
| src/ai_commerce/engineering/minimum_test.py | 20ecbcefa133d40e753d2d9f699801b5c5145ec85a2ad93d40babb84dd407a74 |
| src/ai_commerce/engineering/impact_v21.py | 7f4601c20861c509571f7962df343c585358f3c8708f2350e0b6e771f4e131e6 |
| src/ai_commerce/engineering/pytest_profile.py | 4749d87eee3983274f457af06ae3b7db9dc207508b3934f0fa178325c194e197 |
| src/ai_commerce/engineering/verification.py | 10dd19ac0b050a24c26b71191a5331a4faa605afa119a61ff7ca405aee39f565 |
| .github/ef02/verify-profiles.json | 7135eca52af7bf296a2f2753319ef8e878086d5ff6727ab138673d15fded20d0 |
| work_items/mt01-components/a18-1.json | 7770b4333da699025d3126ed2dae4755b9b174ea462ccf528eb41d659fe27d49 |
| work_items/mt01-components/a18-2.json | f5408245bddf76d264402d7925214cd0757658306ec4edfa6c6a49b439339a26 |
| docs/roadmap/ROADMAP_A_CREATIVE_FIRST_V1_0.md | 4f7d9afc0225231852ed6d3f8d857940cb4885bd94f0f514cd2d8832ce46619f |
| docs/roadmap/WORK_TREE_DETAILED_V1_1.md | 803cc2bc55dbb212cea299ec91ab89103b8cbb0c034d4b4cb15999f76e9de6d4 |

MT01 planner binding SHA256 for the frozen final test run: `a271866ee8b95c9fb04a3e38ec70256859ce3db6b11c604ac00dfd191d48d96d`.
The WorkItem preserves every selected mapping, registered command/profile,
start time, duration, receipt hash and actual worker observation. The final
local status is OFFLINE_IMPLEMENTED_INDEPENDENT_VERIFICATION_REQUIRED. The
Windows runner-service identity, independent candidate audit, four-worker
Windows-hosted exact-head CI and Brain acceptance/auto-merge remain pending.
No candidate SHA, execution authority or commercial verification is claimed.
