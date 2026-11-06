# BQ-04b offline handoff

Base: `13e021e0b45cd7a19872144d986473054c68bd85`. Fresh attempt `BQ04B-OFFLINE-DETERMINISTIC-REPAIR-01`.
Status: **POLICY_ONLY_HOLD; EXECUTOR_SCOPED_TESTS_PASS_WITH_ONE_BASELINE_SKIP; INDEPENDENT_EF01_EF02_PENDING**. No media edit is implemented or claimed.

The allowed fallback is necessary: BQ02b's native gray/PCM statistics contain no detector-classified localized defect. Frame times are requested seeks; source frame indices/PTS and spatial ROI are unmeasured. Rights remain UNKNOWN. BQ04a's existing branch tests inject `defect` fields into native-shaped objects; using those as a detector would manufacture authority. A trim/crop/audio edit would also require independent target and non-target preservation oracles absent here. Existing sources cannot be changed in this ADD_ONLY cut.

`repair_offline_adapter.py` replays native BQ04a decision/plan and digests, rejects injected probe shapes, and creates an immutable closed `BQ04bOfflineAttemptReceipt.v1` with HOLD. It accepts no file path, bytes, editor callback, process runner or grant. Supported executable operations are empty. No original is opened or modified; no scratch/output file exists. Original SHA is explicitly a native Q4 pin. Output SHA/location are null, and `input_bytes_verified_by_adapter=false`.

`repair_verified_evidence.py` validates native tool-backed BQ02b BEFORE/AFTER, their A13 report and SHA chain, the original BQ04a request, and receipt replay. It performs no I/O. Native records describe historical measurements; current file identity remains UNKNOWN. An alternate measured clean asset has no candidate or target link. Correction and preservation remain UNPROVEN, including for a same-byte/no-op AFTER. No rights, semantic, Human, Brain/media PASS, quality, cost, release or provider authority is emitted. BQ04a closure remains unchanged.

The in-process native types and hashes are consistency boundaries, not cryptographic authentication against malicious Python with object construction or process access. Serialized replay requires distinct native sources and compares the whole canonical closed schema. Fresh file custody, exact target measurement and technical invariants need a separately authorized subsequent design; the bridge never silently upgrades supplied facts.

Read-only sources reviewed: adopted BQ-04 roadmap and local CIMQ/CRI research synthesis, BQ04a protocol and all three modules/tests/owner, BQ02b T1/T2/multimodal modules/tests, real A13 inspector and runner, A05 renderer/process and report, A06 report, and `.github/ef02/verify-profiles.json`. Local `git log -1 --format=fuller` confirms PR #502 merged at the base. Issue #373's decision was read through the adopted roadmap. Live Issue #373/#495 and research draft #347 contents are **UNKNOWN** because remote network is prohibited. The local synthesis identifies #347 as Q5 research; #347 remains DRAFT, never code authority. #495/#502 are provenance, never effects permission. Live main drift and independently re-fetched Brain review are UNKNOWN.

Exactly seven new files are allowed; no existing source/test, contract, owner registry, schema, workflow, profile or global setting changes. The owner is `Media & Creative`, with two new sources, one test, three exact AC identifiers and no contracts. Consumers are genuine imports of existing BQ04a, BQ02b and A13. Executor preview uses native manifest validation and impact selection against read-only local owners. Trusted EF01/EF02 must independently reconstruct the immutable baseline and bind candidate hashes.

Verification on 2026-11-06: Windows 11, Python 3.14.7, `RUNNER_ENVIRONMENT=self-hosted`, `GITHUB_ACTIONS=true`, `MT01_V21_ENABLED=1`. Native manifest validation and impact selection returned the five groups below with `ONBOARD_CONSUMER_OWNER_CLOSURE`; no mapping problem or full-suite fallback. The onboarding capability constructs the commands from each owner's test list; A13 uses the exact registered `mt01-a13-tests` argv. This is an executor preview, not independent EF02 admission.

| Group | Exact command | Passed | Failed | Skipped | Errors | Collected workers |
|---|---|---:|---:|---:|---:|---:|
| bq-04b | `python -m pytest tests/test_bq_04b_offline_repair.py -q -n 12` | 70 | 0 | 0 | 0 | 12 |
| a13 | `python -m pytest tests/test_a13_media_inspection.py tests/test_a13_claim_rights.py tests/test_a13_link.py tests/test_a13_evidence_pack.py -q -n 12` | 196 | 0 | 1 | 0 | 12 |
| bq-02a | `python -m pytest tests/test_bq_02a_critic_t1.py -q -n 12` | 282 | 0 | 0 | 0 | 12 |
| bq-02b | `python -m pytest tests/test_bq_02b_multimodal.py -q -n 12` | 249 | 0 | 0 | 0 | 12 |
| bq-04a | `python -m pytest tests/test_bq_04a_targeted_repair.py -q -n 12` | 152 | 0 | 0 | 0 | 12 |

Round 1: **949 passed, 0 failed, 1 skipped, 0 errors**, all five commands exit 0. The native controller hook reported `MT01_XDIST_WORKERS_COLLECTED=12` per group. A13's skip at `tests/test_a13_media_inspection.py:207` states: `Sandbox denies hard-link and symbolic-link creation; detection tested independently`. Link-creation integration is therefore unproven in this sandbox. The HOLD adapter has no path or file-opening interface; its no-I/O checks passed. No failed tests were observed; the retained failed-test list is empty. Round 2 of the two-round limit is unused because no source fix or failed check justifies repetition. The WorkItem retains each command's raw decoded output, captured-byte stdout/stderr SHA-256, counts, durations, collected workers, selected impact and source/manifest hashes. The four tested executable/owner bytes were not changed after these checks; final documentary evidence still requires trusted EF02 hash binding.

Each pytest child retained the Git guard environment. Child-only `PYTHONDONTWRITEBYTECODE=1` and `PYTEST_ADDOPTS` appended `-p no:cacheprovider` prevented repository cache writes. The existing `ai_commerce.engineering.pytest_profile` plugin supplied the worker observation. No dependency installation, guard bypass, global configuration or existing file changes occurred. The literal onboarding registry test command is a capability ingress; no global profile was rewritten and no full suite was run.

Required synthetic MP4 tests use fixed FFmpeg argv through the existing bounded native A13 runner, actual fixture bytes and real A13/T2 inspection under pytest temp. Missing tools/locking fail the tests. A separate manual specimen invoked the same fixed fixture helper in a newly owned temporary directory, then compared native records and independently hashed both actual files. Its 28 real bounded tool invocations, versions, command/output hashes, frame measurements and full receipts are retained in WorkItem `synthetic_media_evidence`; the temporary fixture directory was removed on normal context exit. This is not an additional pytest count.

| Specimen identity | Observed SHA-256 |
|---|---|
| Original black synthetic MP4 | `4b6eb223ed9c2cb037c89fcb287d90db8c06ce6c4c7700eef9e3a2eb502861fd` |
| Independently generated white alternate MP4 | `a5a0f08ff63acd8c868dda80ed899e6183f57311034b68acaf0590e648be1366` |
| HOLD attempt receipt digest | `aa94ae090b4a512cc6440f00bbc857fd278f93430cbd7ba5eeab93543cbe42ce` |
| UNPROVEN technical sidecar digest | `16f73aac5d563bf0f63c9e1cbc64045768bb99266978d7e752914e254a6b494b` |

The original fixture remained byte-identical. The alternate was generated independently for a negative test and is **not a derived repair asset**. Attempt output SHA/location remain null. The sidecar records its historical native measurement pin but has no candidate output SHA. No output-byte, correction or repair-success receipt exists because no edit was admitted.

Candidate publication, hosted Windows PR CI, independent Brain review and merge are **PENDING**. Executor performs no Git write, push or PR action. Only trusted EF02 may publish a verified candidate; only a genuine independent Brain PASS may trigger trusted auto-merge. This WorkItem authorizes no production repair or external effects.
