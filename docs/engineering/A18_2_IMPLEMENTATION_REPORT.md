# A18.2 M07 own-brand brief implementation report

Pinned source HEAD: `3ef41b54caa67f1f8bd90a7122868285f42a08a3`.
Exact HEAD, a clean worktree, and absence of all ten allowed targets were checked
before coding. Human authorized this new bounded action after A18.1 PR #305
merged. This action is `A18-2-M07-OWN-BRAND-OFFLINE-01`; no prior PR or
indeterminate EF02 attempt was edited or retried.

Commerce Opportunity owns M07 B01. Planning sources are Roadmap A v1.0 Phase 5
A18 and Work Tree v1.1 M07. WBS dependencies are M06/L05; A18 dependencies are
A10/A11/A15/A16. A18.3 E07 is outside this implementation.

## Delivered behavior

Four new Python modules expose `build_brief`, immutable
`OwnBrandDecisionBrief`, `validate_brief`, `schema`, `PortfolioSource`,
`CommercialSource`, and `OwnBrandSources`. The portable
`OwnBrandDecisionBrief.v1` Draft 2020-12 contract exactly mirrors the runtime
schema. All object declarations are closed; conditional branches refine
properties without asserting an open object. There are no remote references
or schema file reads at runtime.

The explicit evaluation includes campaign/concept/market/currency/as_of, Human
declarations and policy. Product, account and measurement window can be absent
or null; no SKU, listing, offer or account is required. A hypothetical brand
idea can reach bounded research validation without becoming a gate on the
existing creative workflows.

Outputs are canonical JSON bytes. The ID is `a18-own-brand-` plus SHA256 of the
canonical body excluding its ID. A separate request hash binds the complete
normalized request, including original narrative/list order. Human narrative
is projected as graded content hashes, not copied private notes or quote text.
Numeric assumptions retain their explicit grade, currency and basis.
Declarations, clocks, source IDs/native hashes, counterevidence, differentiation,
supplier/market unknowns, financial scenarios and Human validation actions are
retained. Confidence is an evidence rating and has no probability field.

`CONDITIONAL_GO` proposes only bounded Human research, prototype or sample
validation under a complete, currently valid Human policy. It never means
GO_PURCHASE, GO_STOCK or GO_ORDER. The optional broader GO route is deliberately
not exposed: rights, supplier authenticity, live price and paid demand remain
unknown. Every result independently fixes `execution_authority=NONE`,
`financial_commitment=NONE`, and `external_live_verified=false`.

## Native evidence boundary

`PortfolioSource(recommendation, recommendation_id, sha256, reference_bytes,
evidence=None)` requires the exact native PortfolioRecommendation class, an
explicit ID pin and unchanged byte hash. It revalidates through
PortfolioRecommendation.from_bytes and validate_recommendation. The supplied
reference bytes must equal canonical_bytes(own_brand_reference(record)) exactly.
The native M06 source SHA, recommendation ID, scope hash, clock and reference
content are retained unchanged; M07 does not replace M06's historical source
SHA with this action's source HEAD.

An actual native EvidenceBundle triggers recommend_portfolio on the ORIGINAL
M06 request. Its bytes must exactly match the pinned recommendation before
custody is CURRENT_REPLAY. Without that bundle, custody is ARCHIVED_ONLY, with
no renewed authority or current observation count. Loading a saved M07 brief
checks its retained derivation; it does not reread native custody. Call
build_brief with the retained selectors for a fresh read.

M06 KEEP/CHANGE/STOP is preserved as Human-supplied descriptive context. It is
never provider truth or an automatic decision input for ordering. Real native
tests cover all three bands, including explicit supplied numeric zero. A10
proposals remain hypotheses and A11 reports remain local descriptive
observations without causal uplift or proven market demand.

M07 applies its own explicit clock/freshness policy. Scope/product/account/
window/currency conflicts, counterevidence and upstream HOLD defer the brief.
Partial native scope stays explicitly unjoined. M06 does not project interior
timestamps for proposal/advisory/review sources: a later M07 clock OR a stricter
M07 age limit cannot renew their freshness through replay at the old M06 clock.
Those cases DEFER, including the actual old A10 proposal regression case.

`CommercialSource(review, review_id, sha256, native_refs, commerce_run=None,
settlement_ledger=None, cost_ledger=None)` requires the exact native
CommercialReview class and its explicit ID/hash pins. CommercialReview.from_bytes
must reproduce the native review from its retained A14/A15/L05 evidence.
native_refs must be the exact sorted pairs for the commerce_run and
settlement_history envelope raw_sha256 values, plus SHA256 of each complete
canonical native cost record under its cost:entry_id key.

All three optional native custody handles must be supplied together. Their real
commercial_review result must equal the pinned bytes for CURRENT_REPLAY.
Otherwise the review is ARCHIVED_ONLY. Actual paid orders, actual settlement
and realized profit are always UNKNOWN; supplied settled statements and local
hashes do not authenticate providers. Pending money is not zero. Refunds,
revocations and upstream HOLD remain counterevidence requiring Human review.

M06 and optional A16 source references are compared for shared native versions.
Conflicting A15 cost versions fail closed. A15/A16 costs are reference-only:
none of their subtotals or profit claims is added to the Human scenario. There
is no new ledger or financial attribution join. Pin/custody corruption raises
a fixed OwnBrandError code, never a cached recommendation or leaked native
exception.

A12 listing/offer/claim metadata cannot establish a supplier, current wholesale
price, rights or demand. M07 has no direct A10/A12 production dependency; A10
evidence enters only through M06. Existing A18.1 native producer tests and the
new M07 A10 bridge case preserve that boundary.

## Human scenarios and arithmetic

Every monetary input has UNKNOWN, ESTIMATED_USER_SUPPLIED or USER_SUPPLIED
status, explicit currency and explicit PER_UNIT, PER_ORDER or FIXED basis.
Unknown amount is null; known zero is a decimal string. No FX or inferred
supplier/market values exist. Required cost slots are COGS, sale price,
inbound freight, outbound shipping, fulfillment, storage, returns/warranty,
setup, certification, marketing, fees, buffer and available capital.
MOQ is a positive integer. Units per order, lead time, bear/base/bull sales and
shipping/returns stress are also explicit graded assumptions.

Math uses an isolated Decimal context (80 digits, bounded exponents), with
ROUND_HALF_UP to six places at per-order unit conversion, stress multiplication
and final results. Ambient precision, exponent limits and Inexact traps do not
change the result. Decimal input strings allow up to eighteen whole digits and
six fractional places. There is no binary-float money arithmetic.

Let Q be MOQ, P the supplied unit price, I unit inventory costs (COGS, inbound
freight and storage), S other unit selling costs, F fixed costs, and B the cash
buffer. PER_ORDER costs divide by the Human units-per-order value. Freight and
outbound shipping use each scenario's shipping multiplier; returns/warranty
uses its returns multiplier. Fixed inbound/storage costs remain fixed and are
included once.

- Gross contribution per unit is P - I - S.
- MOQ cash requirement is Q * (I + S) + F + B, conservatively reserving all
  selling costs before considering revenue.
- Operating break-even is ceiling(F / contribution), only for a positive
  denominator with the necessary known inputs.
- Batch break-even is ceiling((Q * I + F) / (P - S)), with a positive denominator.
- Scenario profit/loss is sales * P - Q * I - F - sales * S. Loss is max(-profit, 0).
- Unsold units are Q - sales. This is one MOQ batch, with no reorder and no
  salvage: unsold stock is fully written off within the scenario horizon.
- Worst-case inventory exposure uses bear inventory costs, including fixed
  inbound/storage. Worst-case loss is the larger of the no-sale loss and
  full-sale loss at bear stress; selling can deepen losses when marginal
  selling expense exceeds price.
- Worst-case cash exposure adds B to worst-case loss. The buffer is a reserve
  excluded from profit/loss. Capital gap uses the largest complete scenario cash
  requirement minus available capital, floored at known zero.

Bear/base/bull sales must be ordered and cannot exceed the one-batch MOQ.
Bear shipping/returns stress must be at least base, and base at least bull.
Missing dependencies produce null plus exact reason codes, with a Human
validation checklist. No missing value is silently changed to zero.

DEFER takes precedence for incomplete costs/MOQ/capital/budget/risk/unit ceilings,
missing policy or demand/differentiation rationale, unknown lead time, invalid
scenario comparability, material mismatches, stale/future/contradictory evidence
or unresolved refunds/counterevidence. NO_GO applies to complete assumptions
with nonpositive conservative contribution, risk beyond the Human tolerance,
insufficient conservative MOQ capital, or a research budget beyond capital/risk.
Otherwise complete bounded assumptions permit CONDITIONAL_GO for Human review.
Even a fully synthetic fixture can test this research-only path but cannot
prove market demand or provider performance.

## Privacy and effects

Strict JSON rejects extra fields, duplicate keys, NaN/Infinity/overflow,
non-string money, enormous text/arrays/integers, deep/cyclic structures,
credentials, URLs, paths/ref injection and contacts in brief inputs.
Private input/projection checks are distinct from opaque native envelopes:
native owning validators replay retained raw bytes, which may contain existing
renderer/provenance metadata. Those raw envelopes never enter M07 output.
Diagnostics are fixed codes without caller content.

Only explicit native read APIs are used. No file writer, new ledger, transport,
account login, model/provider call, browser, email, SMS, webhook, supplier
contact, order, payment, inventory/capital action, campaign change, posting,
HostOps or Wake interface exists in the new production modules. Tests install
effect-denial guards around real native calls without bypassing their validators.
Human alone controls capital, inventory and vendor outreach.

## MT01 admission and final Windows verification

The single data-only MT01ComponentOwner.v1 manifest owns exactly four sources,
two tests, one contract and the one requested AC. Its five consumers are
portfolio/recommend.py, portfolio/schemas.py, settlement/review.py,
settlement/ledger.py and operations/cost_ledger.py. The actual protected
approved_groups map, immutable baseline owner manifests and registered profile
were read; no owner/profile or other protected file was edited.

The unchanged registered mt01-component-onboarding planner selected exactly
a15, a16, a18-1 and a18-2, with no missing mapping, unknown AC, coverage HOLD or
full-suite fallback. These are the direct producer/consumer owners of the five
declared interfaces. No direct A10/A12 production import adds a separate
producer obligation. The M06 and A16 groups include their native upstream
integration tests.

Final commands used each group's actual planner-generated or registered argv,
ending in `-q -n 12`. Groups ran sequentially, max_parallel=1. The protected
pytest collection observer was loaded through pytest_observation_env.
Additional pytest options only disabled cache, selected short tracebacks, and
routed quoted JUnit/temp paths outside the worktree. JUnit testcase records
provided the counts below; requested workers or startup banners were not used
as proof.

| Group | Files | Collected | Passed | Failed / errors | Skipped | Actual collected workers |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| a18-2 | 2 | 198 | 198 | 0 / 0 | 0 | 12 |
| a15 | 1 | 43 | 43 | 0 / 0 | 0 | 12 |
| a16 | 2 | 110 | 110 | 0 / 0 | 0 | 12 |
| a18-1 | 2 | 135 | 135 | 0 / 0 | 0 | 12 |
| Total | 7 | 486 | 486 | 0 / 0 | 0 | 12 per group |

Each group emitted exactly `MT01_XDIST_WORKERS_COLLECTED=12` and exited zero.
The full worktree hash map remained unchanged throughout the final run, and
HEAD remained `3ef41b54caa67f1f8bd90a7122868285f42a08a3`. Existing-tree fingerprint:
`edaba1e0005672782fa0d30bbc86abfda979145bc15ed99916003d0a150c5ab5`.
This fingerprint hashes the preexisting file/hash map; it is not a Git tree ID.

The WorkItem retains actual controller stdout, command, start time, duration,
JUnit bytes/hash, stdout hash and completed-worker count for every group.

| Group | JUnit SHA256 | Controller stdout SHA256 |
| --- | --- | --- |
| a18-2 | c44c9459e1da705fc407952daca6565c78d5b77445178bb5f4061edb2238fec7 | 67634b2a4e41b333b5ce80a7ba4f0154de06176569ef913f38502816360e90f0 |
| a15 | 2a6999b423de7011adc60c21912919d72a2791bd468aaf026832c30207558de5 | 1a3db927eeebae7d9057e2a112a67a35cb8a923a9bf5a0f8925644fa86c4deee |
| a16 | 0fcb95fbc3f46bd4375b99793474fdc27420b2c2d12a73690ebffc79c0887e97 | cec0168f18746262c03c29a59f2820991f286f61ce953cfef01f6226e435f4de |
| a18-1 | 29b612ced058982f68e9ea55b7cc2203eedaddb5957918bc86b641bfa3ede0a4 | 21636c983c5a4419253ca80944e9725736a7ed7f55af7dff95ac1f0cee6d0ab3 |

The initial owner run passed 185 tests with twelve actual workers, but its
unquoted Windows pytest option paths lost backslashes, placing generated
artifacts in the worktree. Its JUnit/frozen-tree receipt was not admitted.
Those generated artifacts were preserved in temporary storage outside the
worktree. Repair round 1 fixed path quoting and tightened required ID pins,
null budget/risk handling, Decimal isolation, cost-version and private archive
checks; the complete closure passed 485 tests. Final review identified the
stricter M07 freshness gap. Repair round 2 closed it and added the old native
A10 regression; the final complete closure passed 486 tests as recorded above.
Both same-attempt repair rounds are used. No third repair, EF02 retry,
indeterminate-result reuse, provider/model switch or collateral source edit
occurred.

These are actual local win32 checks, not an independent EF01/EF02 candidate or
runner-service identity attestation. The requested execution preference is
codex / gpt-6.1-sol / max; this report does not independently attest model routing.
candidate_sha and build_verified_for_scope remain null.

Only this report and WorkItem verification metadata changed after the final
run. Tested production, contract, tests and owner-manifest bytes remain frozen
in the table below. The closing scope audit checks all ten final files; a file
cannot embed its own complete hash.

## Frozen tested files

| Allowed tested path | SHA256 |
| --- | --- |
| src/ai_commerce/opportunity/own_brand/__init__.py | d0ab352fbeb888c79211dfb3f6660e7d209199335ebed50f83b6d4487cefdb09 |
| src/ai_commerce/opportunity/own_brand/schemas.py | c73a0672ee8e337546d8dac7369dd7126d029f4760e94341b384320e0d68aea3 |
| src/ai_commerce/opportunity/own_brand/sources.py | 7d9d468ec753da4fb9a3885f63d0d4e941247b0cabf03a39091aa5fd40fb7972 |
| src/ai_commerce/opportunity/own_brand/brief.py | da946e0cd570664cadbd9fcde15cf74767221c1017d9ca9e389c1ba182771f3a |
| contracts/OwnBrandDecisionBrief.v1.schema.json | 35206d6787bd775e39b2e912f5739308958bde1c4879c556f4eb579417ed3ddf |
| tests/test_a18_own_brand.py | 3c140b21aa70b8ae76f7b117d1028f60a35ba0030c54a2772a9a24eb475b95a0 |
| tests/test_a18_own_brand_integration.py | 2d5856259b36dfaa79c097ec3326a1b1ec124f856920dc4e2f284b595c43ca09 |
| work_items/mt01-components/a18-2.json | f5408245bddf76d264402d7925214cd0757658306ec4edfa6c6a49b439339a26 |

## Frozen existing interfaces and verification controls

These hashes were read before implementation. The closing audit checks they
remain unchanged.

| Existing path | SHA256 |
| --- | --- |
| src/ai_commerce/opportunity/portfolio/recommend.py | 69fa454fa32e22d9be5310bf7bb7bc036cad55e1fa3f40116b9cf4ef151c7b98 |
| src/ai_commerce/opportunity/portfolio/schemas.py | fd4bab5387de8c90d3df8962d04c89b55910794d4bf55b9e3ce831c5f322cf58 |
| src/ai_commerce/settlement/review.py | 44851e8a755b07ff359a2047fe0175dfe261d89b49c75fbd8b5c6a76fa420c86 |
| src/ai_commerce/settlement/ledger.py | d69193aee54e8a357a2bf8b50c99f9f07ba5b867e7bd14ecc6421ea450654fd1 |
| src/ai_commerce/operations/cost_ledger.py | 2afb7d0c05b6e90d75020c8b04df5c218c3f7bc609b84f16bb3c721dc66dc969 |
| .github/ef02/verify-profiles.json | 7135eca52af7bf296a2f2753319ef8e878086d5ff6727ab138673d15fded20d0 |
| src/ai_commerce/engineering/minimum_test.py | 20ecbcefa133d40e753d2d9f699801b5c5145ec85a2ad93d40babb84dd407a74 |
| src/ai_commerce/engineering/impact_v21.py | 7f4601c20861c509571f7962df343c585358f3c8708f2350e0b6e771f4e131e6 |

## Independent handoff

EF01/EF02 must independently freeze the final ten hashes, check this exact
source head and candidate SHA, accept the owner/consumer closure, and establish
build-verified-for-scope before the EF02 worker publishes a candidate branch.
This executor performs no Git write, commit, branch, push, PR creation,
workflow dispatch or merge.

The next stage is separate exact-head hosted Windows CI with four workers and
independent Brain review. Only a valid exact-head Brain PASS can trigger trusted
auto-merge. No Ubuntu gate or CI/control-plane change is introduced. Source
integrity, publication and Brain acceptance are not inferred from these local
product tests.
