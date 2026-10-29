# A03 v1.1 implementation report

Date: 2026-10-29. Action: A03-V11-CREATIVE / C01+C02.
Source: b88bf37f81aa115ab36320ee1f043283923984ce, observed from the detached worktree HEAD metadata.
Disposition: implemented locally; final scoped verification PASS (253 tests); registered project-tests FAILED (440 passed, 16 failed, 2 skipped).
No product-model API inference, paid call, credential import, publication, merge or external acceptance is claimed.

## Authority and scope

Read the canonical Human decision in docs/governance/HUMAN_DECISION_2026-10-29_CREATIVE_V1_1_A05.md, docs/engineering/A03_A04_V1_1_ENGINEERING_HANDOFF.md, original A03 modules/tests, A02 contracts and the registered verification profile.
The active action in https://github.com/trung-via/ai-commerce/issues/57 confirms these allowed paths:

- src/ai_commerce/creative
- tests/test_a03_upgrade_v11.py
- docs/engineering/A03_V1_1_IMPLEMENTATION_REPORT.md

Authored changes comprise five new creative modules, additive package exports, the new test module and this report. No A04, bridge, CLI, schema, WorkItem, engineering-runtime or project-profile changes were made. The Human-selected engineering route is separate from the product provider/model; the latter remains UNKNOWN.

## Implemented contracts

CreativeIntent is an optional content-addressed sidecar. capture_intent preserves exact original Unicode text, including whitespace, decomposed characters and original JSON formatting when a string is supplied. Mapping callers can provide their original raw_text. No automatic language understanding or field extraction is claimed. Arbitrary optional IntentField values distinguish SOURCE, INFERRED and UNKNOWN. SOURCE requires an exact input quote; INFERRED cannot assert a source quote; UNKNOWN has no asserted value. Source quotes are traceable assertions, not verified facts. ActorRef and ProviderObservation default to UNKNOWN.

ReasoningRequest, ReasoningResult and CreativeReasonerV2 form a separate opt-in interface via reason_v2:

- EXPLORE supports diverge and controlled_variants. The latter permits repeated families/mechanisms and even title-only variants. Identical creative content cannot count twice by changing only provenance metadata. Divergence requires different families and mechanisms without scoring creative quality.
- count is optional; no fixed quota exists. An explicitly supplied count must be positive and match the response.
- REFRAME and DEVELOP return proposed creative text edits tied to the entire source brief snapshot and exact concept revision/digest. They cannot alter facts, rights, sources or the original generation observation.
- CRITIQUE is optional prose. It neither saves a revision nor creates a selection or quality gate.
- Success, NO_MODEL_CONFIGURED, UNAUTHORIZED, REFUSED and ERROR are explicit states. Errors/refusals contain no successful concept output.

LiveCreativeReasoner is a configurable adapter seam for an injected StructuredCreativeClient, not an implemented vendor SDK/HTTP transport. StructuredModelRequest includes ModelConfiguration, the typed creative request and a fresh JSON output schema. StructuredModelResponse carries strict JSON output, reported observation, usage, request ID and refusal/error information. Parsing rejects duplicate keys, nonfinite numbers, missing/extra structured fields, malformed drafts, unsupported edits, invented source references and invalid operation outputs.

No client is supplied by default. Client-declared network/spend capabilities require explicit InvocationAuthorization with a reference before completion. The adapter always advertises live capabilities to V1, so generate_concepts still rejects it with OFFLINE_ONLY. Invocation snapshots pin configuration, client and authorization despite callbacks or concurrent reconfiguration. No retry or provider fallback is performed.

Configured provider/model are recorded separately from reported ProviderObservation. Missing identity, token measurements or cost stay UNKNOWN/null; they are never inferred from the configured target, engineering model, credentials or elapsed time. OBSERVED means client-reported measurement, not independently verified billing. All client responses and authorization references used by these tests are synthetic fixtures, explicitly TEST_ONLY_MOCK with model UNKNOWN.

ActorDecision is a separate immutable sidecar with AUTO_PINNED, AI_RECOMMENDED and HUMAN_SELECTED. Actor kind and evidence reference must agree. HUMAN_SELECTED requires a typed HumanSelectionEvent whose actor, exact selection ID, event reference and timezone-aware timestamp match. Saving a V1 selection never creates a Human decision. Integration callers must supply an actual observed event; these local types validate evidence structure and binding, not identity authentication or the truth of external events. Human events in tests are synthetic.

CreativeSidecarStore stores intents, reasoning observations and decisions under sidecars/a03-v11 without modifying V1 files. It checks saved brief/concept/selection identities, parent intent lineage and content digests on save/reopen. Matching writes are idempotent; conflicts fail without overwrite.

A reasoner call and proposed_revision do not write revisions. save_result records only an observation. An explicit save_revision appends the V1-compatible child and retains the separate editing observation. DEVELOP maps to V1 REVISE while the sidecar retains DEVELOP. A conflicting revision may leave a valid proposal observation on disk, without claiming that proposal was applied. Store operations are local append-only writes, not a multi-file transaction or signed audit service.

## Compatibility and consumer entry points

The original ContentBrief schema, intake_brief, brief_digest, generate_concepts, MockCreativeReasoner, ConceptRecord, ConceptSelection, CreativeStore and original test files are unchanged. Only __init__.py adds exports. Historical identities are checked against literal pre-upgrade golden digests in the new tests, including Unicode normalization-sensitive input. V1 remains offline and preserves TEST_ONLY_MOCK labels, UNVERIFIED facts, UNKNOWN rights, immutable revisions and pinned selections.

New exported APIs include capture_intent, CreativeIntent, IntentField, ActorRef; ReasoningRequest, ReasoningResult, CreativeReasonerV2, reason_v2; ModelConfiguration, InvocationAuthorization, LiveCreativeReasoner, StructuredCreativeClient, StructuredModelRequest, StructuredModelResponse, UsageObservation; ActorDecision, HumanSelectionEvent; and CreativeSidecarStore.

For example, ReasoningRequest.create(brief, intent=optional_intent, mode="controlled_variants", count=3) describes exploration. reason_v2(request) returns NO_MODEL_CONFIGURED. The caller must explicitly supply a configured adapter/client to obtain proposals. Successful result.concepts() returns unsaved V1-compatible records. Reframe/develop callers use operation, source and optional instruction; REFRAME also requires a lens. There are no required SKU, CTA, storyboard, aspect ratio, aesthetic rubric or publishing fields.

## Verification environment and exact commands

Windows win32, Python 3.14.7, pytest 8.4.2, jsonschema 4.26.0. No dependency installation or environment repair was performed.

The ordinary baseline command python -m pytest tests/test_a03_creative.py -q failed before collection because auto-loaded anyio imported _ssl and encountered a DLL initialization error. Disabling unrelated plugin autoload then produced 88 passed / 12 temp-directory setup errors. Using the writable local test temp directory produced the unchanged V1 baseline result below.

Final subprocesses use these environment values:

- PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
- PYTHONDONTWRITEBYTECODE=1
- PYTHONUTF8=1
- PYTEST_ADDOPTS=-p no:cacheprovider
- TEMP and TMP = <checkout>/.local/a03-v11-test-temp

Python launchers set the above environment and invoke [sys.executable, "-m", "pytest", ...] as subprocess argv. The earlier V1 baseline used runpy.run_module("pytest", run_name="__main__") with the same module arguments and environment. Disabling cache/plugin autoload changes no repository test selection. The implementation adds no skip/xfail. The registered suite reports two existing A04 directory-link tests skipped by their unchanged host-capability guard.

| Exact command | Observed result |
| --- | --- |
| python -m pytest tests/test_a03_creative.py -q | Pre-change baseline: 100 passed in 21.45s, exit 0 |
| python -m pytest tests/test_a03_upgrade_v11.py -q | Initial implementation: 71 passed, 1 failed in 72.13s, exit 1; missing structured draft fields were then rejected |
| python -m pytest tests/test_a03_upgrade_v11.py tests/test_a03_creative.py tests/test_a03_a04_integration.py tests/test_a02_contracts.py tests/test_a02_mock_provider.py -q | Interim: 250 passed in 226.49s, exit 0, before snapshot regression fix |
| python -m pytest tests/test_a03_upgrade_v11.py -q -k invocation_snapshot | 3 passed, 90 deselected in 2.50s, exit 0 |
| python -m pytest tests/test_a03_upgrade_v11.py tests/test_a03_creative.py tests/test_a03_a04_integration.py tests/test_a02_contracts.py tests/test_a02_mock_provider.py -q | 253 passed in 206.31s, exit 0 (93 upgrade cases, 100 original A03 cases, 60 A02/integration cases) |
| Registered project-tests: python -m pytest tests -q | 440 passed, 16 failed, 2 skipped in 263.26s, exit 1 |

The registered command was loaded from the unchanged .github/ef02/verify-profiles.json, not reconstructed with a reduced test list. Final logs are local ignored scratch: .local/a03-v11-scoped-final.log and .local/a03-v11-project-tests.log. Earlier diagnostics are .local/a03-v11-upgrade-initial.log, .local/a03-v11-scoped.log and .local/a03-v11-invocation-snapshot.log.

A separate read-only reviewer found a mutable adapter invocation race; the implementation now uses validated invocation snapshots. Three deterministic tests cover configuration mutation during completion, transport swapping during capability lookup and authorization swapping during capability lookup. Follow-up source review found the defect resolved and no further substantive issue. The reviewer did not execute tests or provider calls.

## Registered-suite deficits

The registered profile is FAILED. No A03 upgrade or compatibility test failed.

| Existing tests | Count | Observed failure |
| --- | --- | --- |
| tests/test_ef01.py | 12 | Fixture git init -q <scratch>/repo exits 3221225794 before the executor behavior under test |
| tests/test_ef01_ef02_integrity.py | 3 | Same Git startup failure during fixture initialization |
| tests/test_ef02_m0r_isolated_runner_kit.py::test_powershell_control_script_parses_on_windows_without_execution | 1 | PowerShell process creation raises OSError [WinError 1920]: The file cannot be accessed by the system |

The two skips are unchanged A04 directory symlink/junction checks, through tests/test_a04_media_operations.py:257: "Host permits neither directory symlinks nor junctions". Those host-link cases remain unverified here. They were not marked skipped by the upgrade.

Raw registered log: .local/a03-v11-project-tests.log.
SHA-256: cc26fbee23f01ebb24a0a45aedf48026d99a9ba14bcfaa42c22b1e48c40f59db.
The raw log includes all 16 exact failing node IDs, commands and tracebacks. No EF01/EF02, Git installation, PowerShell or host-link repair was attempted outside the action allowlist.

## Integrity and limits

Read-only Git index parsing observed 163 tracked entries. At the scope check, only src/ai_commerce/creative/__init__.py differed, with additive exports; the other 162 entries were unchanged and none missing. A separate SHA-256 comparison against the initial workspace snapshot agreed. All new authored paths were within the action allowlist. Generated test scratch/caches are ignored runtime artifacts.

Git startup remains broken: Git status and git diff --check return 3221225794 (0xC0000142), with no diagnostic output. git diff --check is therefore UNAVAILABLE, not PASS. Python AST/trailing-whitespace checks are fallback source checks, not a substitute claim that Git ran. Final Python source checks: all 12 creative/test Python files parse successfully and have no trailing whitespace. The final scope comparison confirms only the eight authored paths listed below differ or are new.

A concrete vendor transport/account/model and real product inference remain unimplemented/unverified until separately selected/configured and authorized. No real API inference, token consumption, cost, model quality, Human selection, factual verification, rights verification or external commercial outcome is demonstrated by fake tests. V1 data and observations are not relabeled.

Candidate commit/branch creation was not performed by this coding session because Git cannot start. No main merge, deployment, A04/bridge fan-in, A05, A06 or M1 acceptance is claimed.

## Final authored files

- src/ai_commerce/creative/__init__.py (additive exports)
- src/ai_commerce/creative/intent.py
- src/ai_commerce/creative/decisions.py
- src/ai_commerce/creative/v2.py
- src/ai_commerce/creative/live.py
- src/ai_commerce/creative/sidecars.py
- tests/test_a03_upgrade_v11.py
- docs/engineering/A03_V1_1_IMPLEMENTATION_REPORT.md
