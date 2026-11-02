# A14 E04 offline Affiliate Commerce handoff — Issue #170

Pinned source: `adef706456ef531ec187ee42b2686f29465e6042`.
This is an ordinary worktree implementation for EF01 verification and EF02
publication. No prior #171/#172/#173 candidate or proof is reused. Integrated
L04 source and its tests are unchanged.

## Repair round 2: scope blocker confirmed; verification still fails

Attempt: `a14_e04_20261102_mainadef7064_v1`; current feedback:
`invocations/002/repair_feedback.json`, SHA-256
`c77e6afb42f89d90f56a658575134061b6dbf2a7b54a144516b3dfb0ee9facde`.
Artifact paths here are relative to the supplied `evidence_root`. This fresh
invocation read the complete feedback, both prior results, referenced source
snapshots, logs, MT-01 plans/results and diffs. Every referenced artifact hash
matched its recorded value; each feedback result matched its standalone result.

Before this report update, all 611 current source hashes matched
`invocations/001/mt01.plan.json`'s bound candidate map, and all seven changed-file
hashes matched `invocations/001/source.after_verify.json`. The canonical source
map SHA-256 was
`32ee05f69ea5f6cf198f8139706611dca0c69600172611a1a25ca3402e3c7c5f`.
All 604 original baseline files were byte-identical. HEAD and its two recorded
parents remain as listed in the ancestry section below.

The latest parent EF01 execution, invocation 001, actually ran both steps:

| Execution | Actual command | Observed result |
| --- | --- | --- |
| Non-authoritative MT-01 provisional preflight | `python -m pytest tests/test_a14_e04.py -q -n 12` | 33 passed, exit 0, 11.84 pytest seconds |
| Registered `project-tests` full fallback | `python -m pytest tests -q -n 12` | 3525 passed, 1 failed, 12 skipped, exit 1, 138.77 pytest seconds |

MT-01 records `PASS_PREFLIGHT_ONLY` for the provisional step and `FAIL` for
the registered step. The full fallback is not a minimum selection. Requested
workers are 12; observed worker count remains null in the recorded evidence.

A fresh read-only audit of the unchanged selector produced
`(('tests',), 'FULL_PROJECT_REQUIRED', ('NEW_UNMAPPED_TEST_DEFINITION',), ())`
for the failing A12 case. The exact set
`all_tests - registered_tests - KNOWN_UNMAPPED` is
`{'tests/test_a14_e04.py'}`. The return at
`.github/scripts/ef02-pr-ci-tests.py:197` precedes component selection, so an
edit to the E04 implementation, schema or report cannot repair this assertion.
The reviewed A14 group still maps L04 only. No E04 owner registration hook
exists. This audit is diagnostic evidence, not another pytest execution.

The causal repair needs owner integration outside this invocation's allowlist:

- Reconcile the preserved, unexpected `tests/test_a14_e04.py` with the approved
  `tests/test_a14_commerce_e2e.py` path and its test-path assertions. The approved
  path is absent. Adding a duplicate test at that path would leave the offending
  file unmapped. Renaming, deleting or rewriting the unexpected file is not
  authorized by the supplied scope.
- Review the E04 AC, test, source, contract and actual dependency/consumer closure
  in shared `src/ai_commerce/engineering/minimum_test.py`, then add its matching
  registered profile to protected `.github/ef02/verify-profiles.json`. The proposal
  in the historical section below must use the reconciled test path. Preserve
  L04's existing registration. Both shared/protected edits remain outside scope.

Only this allowed report changed in repair round 2. Existing implementation,
schema and test bytes are preserved; `tests/test_a14_e04.py` remains scope-review
evidence with `scope_valid=false`, not publication permission. No new test copy,
selector exception, monkeypatch, test suppression or guard/configuration change
was introduced. No unchanged pytest retry, L04 execution, new attempt, Git write,
publication, merge, network/provider/browser operation or wake was performed.
The requested repair remains incomplete until the owner resolves these scope
and registration needs and EF01 verifies the reconciled bytes.

Latest parent evidence hashes:

| Artifact | SHA-256 |
| --- | --- |
| `invocations/001/invocation_result.json` | `aa81445b4f79f89340df2018cb8c8a8e5cd0a6600c5bccc00e339e41570a9cd5` |
| `invocations/001/source.after_verify.json` | `a36154d2203ebb1a51f4d54fd42c013def4b3564d04f4786ec9446c15a972438` |
| `invocations/001/git.after_verify.diff.patch` (empty tracked diff) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `invocations/001/mt01.plan.json` | `878186ccf8c3c417b5c2db97fca0fc3d38ffc86e8edda83ab3b1c03c2ebf9aee` |
| `invocations/001/mt01.provisional.stdout.log` | `41040d94bf346135f13b04454a47d944548b5d7485fa0f3ae092a35a965602f9` |
| `invocations/001/mt01.results.json` | `d626218b9d3b1c1f3a6161297d0ec5834415dbd725693c1357ed0753f9c42fbf` |
| `invocations/001/verify.stdout.log` | `6983e92144ceec12839401f54cdc103680e411286593c04cc418f8343a3e7f27` |
| `invocations/001/verify.stderr.log` (empty) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

The six code/contract/test hashes in the exact-byte table below still identify
the tested bytes, including test SHA-256
`c18b8a3bed90e738293cb58773e9720dbb9aa14fb6b6a39e79733d8000d638df`.
The report hash in the latest parent snapshot is the pre-round-2 report;
historical local candidate manifests do not bind this updated report. EF01/EF02
must capture the final cumulative worktree and scope violation before any
candidate decision. No candidate commit or new passing result is claimed.

## Repair round 1: shared registration required, not repaired

Attempt: `a14_e04_20261102_mainadef7064_v1`; repair feedback:
`invocations/001/repair_feedback.json`, SHA-256
`26c781ff2a9be233b569906c600ac32b0814fac45e0b8a0aca464b3a4a99ab50`.
All artifact paths in this section are relative to the supplied `evidence_root`.
The full feedback, prior result, source snapshots, logs and diffs were read;
all referenced artifact checksums and the supplied result checksum matched.
The entire current 611-entry source map matched the previous verified snapshot
before this documentation update. Its canonical source-map SHA-256 was
`806998275873c7e9a4199a20006933f4c35bab335f851d53cbf3e082024b08a1`.

The authoritative parent EF01 invocation supersedes the earlier executor-local
results below:

| Execution | Actual command | Observed result |
| --- | --- | --- |
| Non-authoritative MT-01 provisional preflight | `python -m pytest tests/test_a14_e04.py -q -n 12` | 33 passed, exit 0, 11.15 pytest seconds |
| Registered `project-tests` full fallback | `python -m pytest tests -q -n 12` | 3525 passed, 1 failed, 12 skipped, exit 1, 108.47 pytest seconds |

The full suite is a registered fallback, not a minimum test selection. Both
commands actually ran through parent EF01. Observed worker count remains null;
12 is the requested count. The prior executor-local Git fixture failures did
not recur in this parent run and are not the current blocker.

The one remaining failure is
`tests/test_minimum_test.py::test_pr_ci_real_a12_component_can_select_narrow_test_files`.
Read-only causal evaluation on this same worktree reproduced the selector
result `FULL_PROJECT_REQUIRED / NEW_UNMAPPED_TEST_DEFINITION`. Its entire
offending set is exactly `tests/test_a14_e04.py`. The protected PR selector
scans all present root test definitions before selecting A12 coverage; its
reviewed map in `engineering/minimum_test.py::approved_groups` covers A14 L04
only. No owner-directory manifest or registration hook exists. Supplying the
explicit E04 AC still yields `IMPORT_OR_CONSUMER_MAP_INCOMPLETE` and
`PATH_OR_AC_MAP_INCOMPLETE`; changing request metadata alone cannot fix this.

Two scope issues require the owning Brain/registry integration, not an E04
code workaround:

- Shared `src/ai_commerce/engineering/minimum_test.py` needs a reviewed E04
  mapping with its actual test and dependency/consumer closure. Protected
  `.github/ef02/verify-profiles.json` needs the matching registered profile.
  Neither path is authorized here. Existing L04 registration must remain intact.
- The approved test path is `tests/test_a14_commerce_e2e.py`, but the cumulative
  snapshot contains `tests/test_a14_e04.py`. Parent EF01 explicitly records it
  as out of scope and `scope_valid=false`. It was preserved byte-for-byte;
  it was not renamed, deleted, copied, suppressed or treated as publication
  permission. The owner must reconcile the intended test path with the scope,
  registry/profile and provisional-mapping regression before integration.

Only this allowed report was edited in repair round 1. Product code, schema,
test bytes, existing shared/protected files, original HEAD and ancestry remain
unchanged. No unchanged full-suite retry, L04 execution, new EF02 attempt,
candidate publication, network/provider/browser operation or Git write was
performed. The direct selector audit is a diagnostic, not a passing pytest run.
Registered verification remains FAIL; this scoped repair is incomplete because
the causal edit is outside the approved owner scope. After an authorized
companion change, the reconciled source needs fresh verification; old evidence
does not establish a passing result for changed registration or test bytes.

Parent evidence hashes:

| Artifact | SHA-256 |
| --- | --- |
| `invocations/000/invocation_result.json` | `f16475215a31348947bbb8b832a11cac46177add8f7b9226c69379bbdbd0a8da` |
| `invocations/000/source.after_verify.json` | `8e5a05943545fe19f733558357805fe3ff07209b4a4193cf54371eb7cbda93c9` |
| `invocations/000/git.after_verify.diff.patch` (empty tracked diff) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `invocations/000/mt01.provisional.stdout.log` | `c796222060ee0d0e442fbfcd21201aed6ad15be6fa18aa378ce2cbf0be40907e` |
| `invocations/000/verify.stdout.log` | `ec3654e625d980b200f3e20639120fad7a239269a2f625876447563f0c5e5f79` |
| `invocations/000/mt01.results.json` | `38b8dd8daad8afe937db79170f423f2f77cc0a08433f41035bd0486d0a538cd0` |

The six code/contract/test hashes listed below still identify the exact tested
bytes. Historical local manifests describe the report before this repair and
must not be presented as hashes of the updated report. EF01/EF02 must capture
the final cumulative worktree, including this documentation change and the
out-of-scope test, before any candidate decision. No candidate commit exists.

## Scope and consumer reconciliation

New owner component: `src/ai_commerce/affiliate_commerce`.
Approved flat paths: `contracts/AffiliateCommerceRun.v1.schema.json`,
`tests/test_a14_commerce_e2e.py`, and this report. The existing ordinary addition
`tests/test_a14_e04.py` is out-of-scope evidence, as detailed above.
No shared/protected file is changed.
The root CLI, packaging configuration, MT-01 registry, verification profiles,
Git configuration, HEAD and refs remain outside this patch.

The integration reads the actual pinned A06 E01 delivery/manifest and A05 render
receipt, A12 ProductEvidencePack, A13 AffiliateReleaseEvidencePack, A07
ChannelCapabilitySnapshot, A08 DistributionPackage and read-only publication
journal replay, and A14 L04 AffiliateAttributionEvidence. A09 observations are
optional and are not used by this slice. Its stage says NOT_PROVIDED.

Build reopens A06 custody, source assets, composition, render, delivered MP4 and
full decode through A06 verify. It reopens every A08 package byte and uses the
existing A09 read-only A08 journal adapter to replay original publication events
and receipt hashes without writing the journal. Exact upstream JSON bytes and
hashes are retained, with native validators and cross-component identity/pin
checks. A12 retains variant, source facts, claims, offers and rights; A13's
decision stays advisory. Optional missing records yield UNKNOWN while A06's
verified creative remains usable. Mismatched or corrupt supplied identities
fail rather than silently joining unrelated records.

Provider, platform, account namespace/ID, app namespace/ID, market, surface,
channel, product/listing/variant, link alias/URL hash, creative/run/output hash,
and explicit UTC observation window are bound in the request. L04 represents
app ID without namespace and does not represent channel or product identity;
E04 compares every available L04 dimension and retains the richer upstream
identities without asserting those absent L04 dimensions were verified.

## Semantics and limits

AffiliateCommerceRun.v1 is immutable canonical JSON with a content-derived run
ID, per-stage status and exact input hashes. Export is not submission;
submission is not public visibility. READY_FOR_MANUAL_REVIEW is never APPROVED.
Observed click rows create no order record or zero-valued order count. L04's
supplied order statuses, if present, remain unverified upstream evidence.
Entitlement, active link, actual orders, revenue, payout and settlement remain
UNKNOWN. A complete offline fixture therefore remains PARTIAL; affirmative
upstream HOLD remains HOLD. Neither state discards verified creative evidence.

Portable from_bytes replays native contracts and retained JSON snapshots. It
does not reopen current media or journals or establish authenticity of a
completely rewritten snapshot. Build performs the local media/journal checks.
Hashes are not signatures or provider/legal authority. Callers must supply
nonpersonal business evidence; no provider export, secret, payment or browser
adapter exists. The module has no provider, posting or Wake entrypoint.

CommerceRunHistory provides bounded append-only, caller-serialized snapshots,
exact retry no-ops, key collisions and immutable creative/link bindings. New
keys retain prior evidence and never sum snapshots into commercial totals.

The Python API is build_affiliate_commerce_run(request=..., creative_directory=...,
product_pack=..., release_pack=..., capability_snapshot=...,
distribution_directory=..., attribution=...). Native A12/A13/L04 immutable
objects are required when supplied. Optional publication_db/publication_id
select one existing journal record. The standalone module supports `schema`
and bounded stdin/stdout `verify`; no shared CLI change is required.

## Verification handoff

New tests exercise real locally rendered A06 media and actual native component
builders, including positive, missing-evidence, identity, window, corruption,
rights-UNKNOWN, submission, deterministic replay and history collision cases.
All commercial/account fixtures are offline test evidence, not real rights,
orders, receipts or entitlements.

The current protected A14 registration covers L04 only. This new family must
execute through the existing provisional MT-01 preflight and then the exact
registered project-tests fallback (`python -m pytest tests -q -n 12`). That full
suite must not be described as the minimum. Narrow post-merge activation needs
an owner-reviewed companion update to engineering approved_groups and protected
`.github/ef02/verify-profiles.json`; neither is edited by E04.

## Prior executor-local execution and scope result (historical)

Final E04 preflight: **33 passed**, exit 0, in 16.91 pytest seconds
(17.79 seconds including MT-01 launch). The real execute_plan entrypoint ran
the provisional command before the exact registered project-tests command.
Its result is PASS_PREFLIGHT_ONLY and explicitly non-authoritative.

Final registered broad execution: **3469 passed, 46 failed, 23 skipped**, pytest
exit 1, in 179.91 pytest seconds (180.68 seconds including launch).
MT-01 returned exit 3 / UNPROVEN / VERIFY_INFRASTRUCTURE_ERROR. This is neither
a successful registered verification nor a minimum-suite result. Both commands
requested `-n 12`; quiet output did not expose a worker count, so the existing
MT-01 observer records workers_actual=null. No observed-worker claim is made.

The remaining failures are:

- **45 disposable Git fixture setup failures:** inherited reference-transaction
  hooks reject `git init`, with Git Bash NtCreateDirectoryObject `0xC0000022`
  and exit 128. No alternate Git options, hook/configuration/environment
  override or source-ref write was used. EF01's parent verifier must handle
  these fixtures in its supported environment.
- **One shared selector assertion:**
  `tests/test_minimum_test.py::test_pr_ci_real_a12_component_can_select_narrow_test_files`
  expects selective coverage, while the protected selector truthfully returns
  FULL_PROJECT_REQUIRED / NEW_UNMAPPED_TEST_DEFINITION for the pending E04
  test family. This needs the reviewed E04 registration companion, not test
  suppression or a weakened selector. Shared/protected files are unchanged.

The bounded repair corrected an invalid rights-UNKNOWN asset fixture and then
removed test-to-test helper imports that added unrelated CLI/A13 consumer
edges. The final fixtures are self-contained adaptations of pinned main's
native A06/A13 fixtures. The three resulting selector failures are resolved:
round 1 broad execution had 3466 passed / 49 failed / 23 skipped. Both rounds'
plans, logs, candidate snapshots and summaries are retained under the ignored
local evidence directory `build/a14-e04-verification`. No third broad retry,
historic candidate reuse, publisher operation or automatic main merge occurred.

## Shared/protected integration need (reported, not edited)

Before committing this new test family to an integrated source, the registry
owner must review an E04 TestGroup in
`src/ai_commerce/engineering/minimum_test.py` covering:

- AC `A14.E04.offline-affiliate-commerce`;
- `tests/test_a14_e04.py`;
- the four new Python files under `src/ai_commerce/affiliate_commerce`;
- `contracts/AffiliateCommerceRun.v1.schema.json` and this report;
- the actual native dependency/consumer closure, including the runtime-read
  A08 PublicationEvent schema and provider profiles.

Its proposed separate profile is `mt01-a14-e04-tests` with argv
`["python", "-m", "pytest", "tests/test_a14_e04.py", "-q", "-n", "12"]`
in protected `.github/ef02/verify-profiles.json`. Existing L04's a14 group and
mt01-a14-tests profile must remain intact. This is a proposal, not active
registration or proof of a published candidate. EF01/EF02 must verify the
reconciled source after any companion integration.

## Exact byte and ancestry evidence

HEAD remained `adef706456ef531ec187ee42b2686f29465e6042`. Its recorded parents
are `be19c9485ad4a0d703a1034b883fba64174dddfc` (integrated L04 recovery) and
`b3300ca0617db43a99c7bf8637424a34fc30559f`.
All **604 preexisting source files** remain byte-identical. The patch consists
of **seven added ordinary files**, with no modified/deleted baseline path,
protected path, existing L04 path, or shared consumer.

The final tested code/contract bytes are:

| Path | SHA-256 |
| --- | --- |
| contracts/AffiliateCommerceRun.v1.schema.json | `18b8392063f11cfc55ba94a8d6eb2288f2be7fa46325c6b23be29be366e42df9` |
| src/ai_commerce/affiliate_commerce/__init__.py | `92b8b9300463b6d6014085733a1c47284defedc58ec9ee0403330c31c35a0c84` |
| src/ai_commerce/affiliate_commerce/__main__.py | `da76430896f7765b661bfd5ad5efe0139dd673ab1c674eec71daf304b626818a` |
| src/ai_commerce/affiliate_commerce/schemas.py | `7f34dafb11e9306ad7429ae77c1db0a55d52310d7106fd6242878556d9b536d4` |
| src/ai_commerce/affiliate_commerce/service.py | `62a2fb879f266ac7e86283bbc526adc8301323df99c9a87a5e6796894bd27bac` |
| tests/test_a14_e04.py | `c18b8a3bed90e738293cb58773e9720dbb9aa14fb6b6a39e79733d8000d638df` |

Final round candidate-before and candidate-after manifests are identical:
`59276b180fd9fca9d6d77b612c38d1dc272d1b0985f6f97d5344b9c22cb13106`.
The verification summary SHA-256 is
`0293ab253b05a2f1afd5887d34078b02104806927dd28bed9c82fce83fdabaa0`;
the MT-01 results SHA-256 is
`5ce9aceec19816bad81e89c14ebca2bb902ef1fd0f543ded716a3738ce5b46da`.
Final broad stdout SHA-256 is
`a6e281d2dd6f8d7713edf633750810991e96c262052a10bad23c19312bdb27b4`.

Only this documentation receipt was appended after the final tests. Final code,
contract and test bytes match their tested hashes above. The local
`final-candidate.json` manifest binds all final source bytes, including this
report, their raw Git blob identities, artifact hashes and scope classification.
It is an uncommitted worktree fingerprint, not a candidate commit SHA or a clean
Git filter/checkout proof. No candidate SHA/ref was created. Publication and
final candidate acceptance remain with the EF02 worker and EF01 verifier.


## Integration of recovered source (separate from historical EF02 worktree report)

- Original EF02 Issue #223 remained VERIFICATION_FAILED; this report's earlier
  references to test_a14_e04.py and provisional broad fallback describe that
  original immutable run and are retained as historical evidence.
- HostOps2 Issue #228 / run 41426161990 attested all seven SHA256 source files;
  inner archive SHA256 9182200e1fc255a74430f7e78248b18fa70e48718d19e0f25dfadc8f83e5b0d6.
- A separate review branch imports those verified bytes. The E04 test file
  is deliberately renamed to tests/test_a14_commerce_e2e.py and its obsolete
  provisional-mapping assertion replaced by a reviewed registration assertion.
- This changes test bytes, so the original #223 E04 test PASS is historical,
  not proof of final integration. Current-branch tests and hosted CI must run.
- Main merge requires fresh Human approval; no real affiliate provider or
  commercial outcomes are implied.
