# B0-S.3a source handoff

**HOLD_ORIGINAL_B03_CONSUMER_FAILURES_ONBOARDING_AND_POSTEXECUTOR_EF01_PROOF /
NOT_READY_FOR_CANDIDATE.**

Exactly eight add-only files contain the source proposal. The new custody suite
passed **255/255**, zero skips, with twelve separately observed collecting
workers. Overall source acceptance remains HOLD: round2 recorded **4017
passed / 19 failed / 23 skipped**, and **9** prohibited
onboarding tests were not executed. Current original B0.3 is **37 passed /
16 failed / 2 skipped**. This executor released no candidate branch, PR, merge
or live activation.

## Source, authorization and scope

Exact baseline and unchanged HEAD:
`7a4403014d72d203a111f87c0176ef500febd2b0`.
Human @AI Commerce source authorization 2026-11-05:
“tiếp tục thực hiện B0-S.3”. The
[WorkItem](../../work_items/B0-S3-INDEPENDENT-CUSTODY-PREP-001.json) retains the
exact eight-path allowlist, two ACs, local plan, all round/group/worker receipts,
failure/skip/not-executed identities and source hashes.

Preflight observed clean exact source and all eight targets absent. Final
read-only rev-parse/status/diff observations still matched that baseline, showed
an empty tracked diff and exactly the eight authorized additions. A whole
protected-worktree fingerprint excluding those additions stayed identical.
No old B0-S1/S2, B0.1–B0.4, schema, CI, HostOps, runner or EF01/EF02 file changed.
The native B0.3 source lock remains
`32f42b84567e1217a93854389b1c96435bbf454d`, distinct from this engineering base.

S3A_SOURCE_ONLY supplies the
[architecture](../security/B0_S3_CUSTODY_ARCHITECTURE_V1.md),
[protocol](../security/B0_S3_ENROLLMENT_REVOCATION_PROTOCOL_V1.md), four closed
inert drafts, pure denials, synthetic offline tests and a data-only owner.
S3B_LIVE_CUSTODY_UNKNOWN remains separate;
**PRIVATE_CUSTODY_VERIFIED=UNKNOWN**. All actual live transitions are UNSUPPORTED.
Option B is a conditional Brain recommendation, never Human-selected live
device/vendor/broker custody.

The model has no IO, signer, trusted mock HMAC/key, credential/provider adapter,
enrollment, state writer or worker integration. `decide` always HOLDs or REJECTs;
coherent all-gates-true claims end ISSUER_NOT_ENROLLED. PREVIEW_INERT returns
fixed field names and an all-zero sentinel only. Existing S2
HOLD_PRIVATE_VERIFIER_NOT_ENROLLED is unchanged. Tests do not prove malicious
same-SID resistance, authenticated Brain provenance, complete Human consent,
provider rights, spend authority or independent token custody.

## Local onboarding and actual consumer closure

The existing `mt01-component-onboarding` route admitted the data-only `b0-s3`
manifest locally with exactly the two supplied ACs and contracts=[]; no duplicate
ownership or protected registry edit was made. Local impact was AFFECTED,
ONBOARD_CONSUMER_OWNER_CLOSURE, with no missing mappings, unmapped sources/tests
or involved uncertain imports.

Worktree owner data was validated in dependency order; explicit existing
B0-S/B0.1–B0.4 consumers plus their actual reverse imports were expanded to a
fixed point. The 30 selected groups and exact registered/dynamic test argv are
in the WorkItem, with actual path witnesses for A-series/CLI expansion. This is
a complete local mapping analysis, **not immutable-baseline admission or
postexecutor EF01 acceptance**. The protected Git-tree loader uses subcommands
outside this executor's supplied read-only list; it was not invoked, replaced
or represented as having passed. No full-suite fallback was used.

## Two verification rounds and evidence limits

Runtime: local Windows, Python 3.14.7, pytest 9.2.0, pytest-xdist 3.8.0.
Configured runner labels/topology and the Python environment do not establish
registered selfhost run/job identity. **No independent selfhost run/job receipt
was obtained.** Required selfhost policy is 12 actual workers per selected
group; later independent hosted Windows PR CI requires -n4.

A preliminary temporary-directory probe failed before pytest and ran no tests;
its finite setup reason remains in the WorkItem. Round1 then exited pytest **3**
with an INTERNALERROR because the temporary basetemp parent was missing:
zero collected/executed tests and no worker collection callbacks. The selected
node count and actual spawn count were unproven, not a passing empty suite.
Its log/source hashes and verification error remain preserved.

Round2 corrected only temporary harness directory creation. No model or
protected test/source change followed the failed setup. All 30 selections ran
with -n12. Cache/fixture locations were redirected into session temp; child
bytecode cache suppression made no guard/security change. Nine prohibited
onboarding Git fixtures were explicitly deselected. Their subset remains
**incomplete registered verification**, not a full profile PASS. Two rounds
are used; no third executor pytest or repair round was attempted.

Selected counts below include those nine original onboarding cases. Collected
counts exclude their explicit deselection; executed counts exclude skips.
Errors=0 for round2 test outcomes. Round1's verification INTERNALERROR is
separate and is not erased by that zero.

| Group | Selected | Collected | Executed | Passed | Failed | Skipped | Not executed | Actual collecting workers |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| b0-s3 | 255 | 255 | 255 | 255 | 0 | 0 | 0 | 12 |
| b0-s | 194 | 194 | 194 | 194 | 0 | 0 | 0 | 12 |
| onboarding-permitted-subset | 53 | 44 | 44 | 43 | 1 | 0 | 9 | 12 |
| b0-1 | 82 | 82 | 82 | 82 | 0 | 0 | 0 | 12 |
| b0-2 | 60 | 60 | 60 | 60 | 0 | 0 | 0 | 12 |
| a03 | 193 | 193 | 193 | 193 | 0 | 0 | 0 | 12 |
| a04 | 181 | 181 | 170 | 170 | 0 | 11 | 0 | 12 |
| a05 | 217 | 217 | 217 | 215 | 2 | 0 | 0 | 12 |
| a06 | 7 | 7 | 7 | 7 | 0 | 0 | 0 | 12 |
| a07 | 38 | 38 | 38 | 38 | 0 | 0 | 0 | 12 |
| a08 | 10 | 10 | 10 | 10 | 0 | 0 | 0 | 12 |
| a09 | 182 | 182 | 180 | 180 | 0 | 2 | 0 | 12 |
| a10 | 271 | 271 | 271 | 271 | 0 | 0 | 0 | 12 |
| a10-m02 | 77 | 77 | 77 | 77 | 0 | 0 | 0 | 12 |
| a11 | 223 | 223 | 220 | 220 | 0 | 3 | 0 | 12 |
| a12 | 482 | 482 | 480 | 480 | 0 | 2 | 0 | 12 |
| a13 | 197 | 197 | 196 | 196 | 0 | 1 | 0 | 12 |
| a14 | 120 | 120 | 120 | 120 | 0 | 0 | 0 | 12 |
| a14-e04 | 33 | 33 | 33 | 33 | 0 | 0 | 0 | 12 |
| a15 | 43 | 43 | 43 | 43 | 0 | 0 | 0 | 12 |
| a16 | 110 | 110 | 110 | 110 | 0 | 0 | 0 | 12 |
| a17-1 | 136 | 136 | 136 | 136 | 0 | 0 | 0 | 12 |
| a17-2 | 106 | 106 | 106 | 106 | 0 | 0 | 0 | 12 |
| a17-3 | 117 | 117 | 117 | 117 | 0 | 0 | 0 | 12 |
| a18-1 | 135 | 135 | 135 | 135 | 0 | 0 | 0 | 12 |
| a18-2 | 198 | 198 | 198 | 198 | 0 | 0 | 0 | 12 |
| a18-3 | 216 | 216 | 216 | 216 | 0 | 0 | 0 | 12 |
| b0-4 | 69 | 69 | 67 | 67 | 0 | 2 | 0 | 12 |
| cli | 8 | 8 | 8 | 8 | 0 | 0 | 0 | 12 |
| b0-3 | 55 | 55 | 53 | 37 | 16 | 2 | 0 | 12 |
| Distinct round2 total | 4068 | 4059 | 4036 | 4017 | 19 | 23 | 9 | 12 per group |

These totals are **not an aggregate PASS**. The controller exited 0 after
recording individual failing pytest exits; orchestration completion is not
verification acceptance. B0.4 took 629.469 seconds and passed 67 with two skips;
its original bounds/workers were unchanged. B0.3 took 174.216 seconds and
failed 16. No timeout was relabeled PASS, hidden, extended or repaired.

## Separate gw0 through gw11 collection observations

Each round2 group had twelve distinct collection-completion callbacks, matching
ordered node-ID hashes and retained per-worker counts/completion/call-report
counts. Small groups can have fewer than twelve workers execute a case while
all twelve collect. Neither -n12 argv, three runner instances nor Online labels
substitute for this observation. These remain local observations, not an
independent attestation of the observer, run identity, private issuer or custody.

Collection hash format: SHA-256 of UTF-8 LF-joined ordered node IDs without a
trailing LF. Full per-worker gw0–gw11 records are in the WorkItem.

| Group | Ordered collection SHA-256 observed separately on all twelve workers |
|---|---|
| b0-s3 | 6780d30cbdc98dd7c9bee3adc717b8ad556a7696875829eec6a628852b0a0532 |
| b0-s | 4947d78e9b5b3db6ddc10788a2d457224f2733020f403f66f0a2510063463793 |
| onboarding-permitted-subset | d561760c63263eac48b5c42b3bfb4e29c93b5e408f7ba469fac7f6a0d60ccd2f |
| b0-1 | 60adec761b988e38408ff706ceb58604da5023fed1c5fedb18e7df18f68b334d |
| b0-2 | 7f67053a1a69a1686592c53546a4027347e779031953bfa4c433529fd752bafe |
| a03 | ab5b806b0518b479e7aff9b9d7aa4a6ba3929e3e61fe3766ff02db17fb1b6123 |
| a04 | 19ca03b2c48df0a79ff4369b34d6f2e523bfc3f3a73732eeb812fa04cf338157 |
| a05 | 4400dbcdba9a3c9dc2ab8cebec67cb6b1254875b7d32db866250d0f37231326f |
| a06 | dac1e9d3d9b43c3a06f5f30e6b8dc0705bc602a101f751cb646c7e8b43ab4640 |
| a07 | 323a14145d1fd90747de5a63dc2f5687aeb9910e93cb86758ea780c8e574c9d2 |
| a08 | adb91847bff3170bfcf0327f7c07df82b7931c2c74227b1fa7c7399294adf939 |
| a09 | 050e79d77ff7cce48a2486a02efe047be2a97d309c6b6f64bf7d6076578be790 |
| a10 | 5de1700442ad1cdab6897714285e8c9e0324fa6422788acd22749266b28026a2 |
| a10-m02 | c865eb5cbcf55d64f515392bb6c3f1597f177255d592c1b3b3cbfde8cbfc32d3 |
| a11 | 25de08aa08987ba1a8ce1f0b58cb1b74ae590cbd4b20b746e17667696e34c058 |
| a12 | 6b69f8555fe3afaa5f318eac9a92cfd296f5881b1252090447d7a0ae9eff1bc0 |
| a13 | 6cf93127d98fc6a9b78b64d9dad2e7298aeb16ec454f45f954ec2cab4fbc7ba0 |
| a14 | 5a20d44b1a5eaae86c6a6255bddc1f858887a230ca35028b8de6847875d3546f |
| a14-e04 | 9e15c5c23e953496f17f90682d205e4ac5afaf7ea237100d39f8e9da702286ff |
| a15 | 4d59b9e7ca992d1d91a1c12a15465a819a40a20152395651083925eac39f5482 |
| a16 | 84e40ba3320f29987085f9e8983b30a92754f5afaaf1dac2c067b73330b4d22e |
| a17-1 | 134b4c4e8f588f58571b47ce851e07b906c1de54b1722fe8825ff471b041f011 |
| a17-2 | 96a315c02c61d10a67463a278870855961d3731cde00380f719435fb42b92359 |
| a17-3 | 6440bb03a8881844759a0b2bb6bfec1f0bf0cc8cb3b5d83065e5531edfb9eda1 |
| a18-1 | bf83cc1fd6a4b6fb733be83930177abbbc5d9f12abbb1d535ab36c28f823b5ea |
| a18-2 | 7fdca6e7fbcedaa068c2d7d2c0c20b8a40a8e292903f9166f208d6410c0d2c99 |
| a18-3 | 3d5c9d089ce72893c0e4688965acf9ff73bb269f1b1835628dac4114aae23e6d |
| b0-4 | 66d959e591a5291971603b794d53861b60ff1b3171b4fbf6567f7dd3e0e894c4 |
| cli | c9865a25994fadfbb1ae0f8355523b7cd12fb8aca1d70b4b1fce27fe2d519fe6 |
| b0-3 | aecd57397e3b27f9c182fdcfe745c1475b0df07073033b4feade8ac0ad11d2ba |

## Retained failures, skips and prohibited onboarding cases

All nineteen current failing test identities are retained below. Thirteen
B0.3 failures reported TimeoutExpired; three further B0.3 causes remain UNKNOWN.
The native-link onboarding failure and two A05 render failure causes are not
diagnosed or repaired here.

- `tests/test_mt01_v21.py::test_v22_manifest_rejects_missing_or_linked_source` — observed failure; underlying cause UNKNOWN.
- `tests/test_a05_render.py::test_real_single_import_to_playable_h264[image/jpeg-.jpg]` — observed failure; underlying cause UNKNOWN.
- `tests/test_a05_render.py::test_real_single_import_to_playable_h264[image/webp-.webp]` — observed failure; underlying cause UNKNOWN.
- `tests/test_b0_3_attended_worker.py::test_export_conflict_and_atomic_concurrent_byte_identical_publication` — TimeoutExpired; remains FAILED.
- `tests/test_b0_3_attended_worker.py::test_human_import_requires_matching_local_rights_with_freshness[valid]` — TimeoutExpired; remains FAILED.
- `tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[asset]` — TimeoutExpired; remains FAILED.
- `tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[directive-file-bytes]` — TimeoutExpired; remains FAILED.
- `tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[directive]` — TimeoutExpired; remains FAILED.
- `tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[intent-source]` — TimeoutExpired; remains FAILED.
- `tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[journal-corrupt]` — TimeoutExpired; remains FAILED.
- `tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[journal-rollback]` — TimeoutExpired; remains FAILED.
- `tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[output-corrupt]` — TimeoutExpired; remains FAILED.
- `tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[output-foreign]` — TimeoutExpired; remains FAILED.
- `tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[output-missing]` — TimeoutExpired; remains FAILED.
- `tests/test_b0_3_attended_worker.py::test_post_run_tamper_or_rollback_holds_without_rerender[output-partial]` — TimeoutExpired; remains FAILED.
- `tests/test_b0_3_attended_worker.py::test_real_process_kill_at_commit_cuts_never_rerenders[after_output]` — observed failure; underlying cause UNKNOWN.
- `tests/test_b0_3_attended_worker.py::test_real_process_kill_at_commit_cuts_never_rerenders[after_wait]` — observed failure; underlying cause UNKNOWN.
- `tests/test_b0_3_attended_worker.py::test_stale_fence_and_exact_lease_reconciliation` — observed failure; underlying cause UNKNOWN.
- `tests/test_b0_3_attended_worker.py::test_windows_selfhost_real_separate_process_mp4_reopen_and_identical_export` — TimeoutExpired; remains FAILED.

The 23 skipped identities are retained individually in the WorkItem: A04=11,
A09=2, A11=3, A12=2, A13=1, B0.4=2, B0.3=2. Native link/junction/hardlink and
optional media controls remain unproven; another passing case is not a
substitute. Raw logs were hashed in session temp before sandbox cleanup; raw
private-path tracebacks are not public artifacts or durable independent proof.

The nine registered onboarding cases below invoke prohibited Git fixture writes.
They were NOT EXECUTED, not PASS and not hidden as SKIP. No alternate Git option,
hook/guard bypass or environment workaround was attempted.

- `tests/test_mt01_v21.py::test_v21_offline_ef02_two_action_fanin_real_git_pytest_verify_fix`
- `tests/test_mt01_v21.py::test_v21_offline_ef02_scope_review_blocks_downstream_without_provider`
- `tests/test_mt01_v21.py::test_v22_ef02_replays_dynamic_argv_against_immutable_git_baseline`
- `tests/test_mt01_v21.py::test_v22_existing_owner_binding_replays_with_new_owner_on_same_candidate`
- `tests/test_mt01_v21.py::test_v22_cold_component_one_pr_offline_canary`
- `tests/test_mt01_v21.py::test_v22_committed_component_owner_persists_in_followup_pr`
- `tests/test_mt01_v21.py::test_v22_baseline_owner_dependency_order_beats_lexicographic_order`
- `tests/test_mt01_v21.py::test_v22_baseline_dependency_cycle_stays_denied_by_planner_and_oracle`
- `tests/test_mt01_v21.py::test_v22_baseline_unowned_consumer_never_silently_admitted`

Historical siblings remain intact in the unchanged
[S1/S2 handoff](B0_S1_S2_SOURCE_HANDOFF.md): its prior closure had seven failures,
23 skips and nine unexecuted onboarding cases; its prior B0.3 was 49/4/2.
Those are historical observations, not this cut's 37/16/2 B0.3 result. The
current failure is an explicit HOLD trigger under the Human instruction;
any repair outside the eight paths requires separate scope.

## Tested bytes and independent handoff

All six static files below stayed byte-identical across every round2 group
and the final scope check. WorkItem/handoff reporting metadata was finalized
after pytest and therefore still requires EF01 binding of all eight final files.

| Tested static file | SHA-256 |
|---|---|
| docs/security/B0_S3_CUSTODY_ARCHITECTURE_V1.md | fb3f0258aef22a520f179a8b95e3b17cda35d7dae55ec61b1a77f8a3197c36b0 |
| docs/security/B0_S3_ENROLLMENT_REVOCATION_PROTOCOL_V1.md | 028b0742c47ecd7c7646a8ed8adb9f220b6fb963703d8c94fbe3d336ecf6ae54 |
| src/ai_commerce/production_runtime/custody_claims.py | 5ac65b465b257fc2afa075861f68fd5e4d51df04248a4c001f47deec9359d00a |
| src/ai_commerce/production_runtime/custody_gate.py | 2ad2a51952e0c19b67dad1606f4c6682bd968aa866683fc31171f0b27dfc23b5 |
| tests/test_b0_s3_custody_boundary.py | d7d87e903d657a88ae583fe5f7f611b31e5ef499660186631f83244c8c6e0129 |
| work_items/mt01-components/b0-s3.json | a194d778b9cc906e917c53466b155b760300b7eed91dc7df1986a4e1cccbfedc |

Protected-worktree SHA-256 before/after (excluding the eight additions):
`a842a5af268f93e1ad6eca3504ddbcc756599356d05623d961cc776e29080712`. This is a local filesystem-content observation, not an
anti-rollback root or authenticated candidate commit.

Postexecutor EF01 status: **PENDING_NOT_RUN_BY_EXECUTOR**.
Receipt ID: **null**. Receipt SHA-256: **null**. Verified candidate SHA: **null**.
Required base SHA: `7a4403014d72d203a111f87c0176ef500febd2b0`.
Only authentic postexecutor verification can justify EF02 releasing
CANDIDATE_BRANCH. Local counts and content hashes cannot manufacture that receipt.
Later independent hosted Windows PR CI at -n4 and Brain review must bind exact
HEAD/base before trusted merge. No direct merge, PR creation or Git/ref write
was performed by the executor.

## Separate real custody gates

Human must separately select the real Z3 device/broker. Independent parties must
attest out-of-band root admission/possession, token isolation from malicious
same-SID code, independently authenticated complete transaction display/consent,
durable outside-SID replay/semantic-target/budget/epoch enforcement, revoke/KILL/
rotation and rollback behavior, and authenticated provider receipts. Brain
strategy, Human per-transaction consent, provider entitlement, asset rights
and spend stay distinct. DPAPI/Credential Manager/local ACL/TPM/passkey/local
service labels or WebAuthn UV alone do not satisfy these gates.

No live provider/publish/spend, HostOps, CDP/Wake, deployment, service install,
signer/credential enrollment or S3b/S4 dispatch is authorized or performed.
