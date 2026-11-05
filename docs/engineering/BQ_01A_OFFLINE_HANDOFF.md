# BQ-01a offline source handoff

Source-only result: **PASS_LOCAL_REGISTERED / CANDIDATE_PUBLICATION_PENDING**. Final Windows verification collected and accepted **521 tests: 328 new BQ-01a + 193 unchanged A03**, with zero failed, skipped, error, xfailed, xpassed or unknown test results. Each registered group observed **12 completed worker collections**. No fallback or skipped group occurred. Two Verify-Fix rounds were used; no test attempt failed. Round 1 accepted 502 tests, then source review tightened UNKNOWN propagation, identity continuity and aggregate bounds; round 2 accepted 521. Original receipts from both rounds remain in the WorkItem.

Baseline is exactly `main@f640a1dac925269596ac652322d72edc95738e5a`. Before edits, the detached checkout was clean and all eight authorized ADD-only paths were absent. Final scope checks found exactly those eight additions, no changed tracked files and unchanged HEAD. This executor issued no Git writes, PR, merge or deployment. EF02 alone publishes the candidate branch; exact candidate-HEAD Windows hosted CI and independent Brain review remain pending.

Human's 2026-11-05 request was “tiếp tục roadmap”. Brain selected this bounded offline P0 subcut; Human did not specifically select a model, provider, credential or paid effect. BQ-00 PR #410 is adopted planning only, not a deployed BQ-00 runtime. Decision #373, ADR B002 and the adopted Work Tree/research sources define the cut. B0-S.3b PR #387 remains an external-effect boundary and grants no provider authority. The preferred executor config is codex / gpt-6.1-sol / max. No fallback, quota-exhaustion, permission or unsupported-model diagnostic was reported by the invoked tools; the tools do not independently attest the model serving this conversation.

## Pure draft interfaces and limitations

`build_behavioral_brief` accepts an already intaken real A03 ContentBrief and its caller-supplied `brief_digest` pin, plus explicit hypotheses, assumptions and counterexamples. A03 retains schema-validation responsibility and its normal packaged-schema reads. The new boundary performs no filesystem, network, provider, environment, clock, randomness or runtime-service operations. All new records are immutable canonical UTF-8 JSON values; dictionary outputs are detached copies. Original freeform/Unicode and optional JSON intake formatting are retained without Unicode normalization. Product reference may be null; there is no SKU, CTA, concept quota or required storyboard.

Every hypothesis or claim references a bound content snapshot. Intake evidence is `USER_INTENT`; it cannot become research by changing its evidence type. External snapshots must refer to sources already declared in A03. `HUMAN_SUPPLIED`, `SYNTHETIC_ONLY` and `UNKNOWN` describe supplied provenance, not authenticated identity or independently observed behavior. Hashes bind bytes, not truth or authenticity. All factual, behavioral metric, research, testimonial and copyright assertions remain **UNPROVEN**. User preference never becomes observed research. Caller-written hypotheses remain **HYPOTHESIS**.

`build_causal_plan` records Q3 R1-R5/R8 and stable K1-K6 mapping, unique shots, a valid acyclic causal graph, explicit chronological time/order, and object/identity/position/motion endpoints. Known identity changes are rejected even across UNKNOWN or absent intermediate appearances. Known position/motion contradictions reject; unknown endpoints remain unknown and make the compiler HOLD. Tokens describe intended states, not measured positions. No emotion is inferred from landmarks. The bounded planner supports sequential nonoverlapping shots, not nonlinear editing or actual-media inspection.

Visual form (`REALISTIC`, `STYLIZED`, `FICTION`, `ANIMATION`) and physics intent are independent caller choices. Intentional fantasy never implies a physical-realism requirement. Silent and voiceover do not require lip-sync; on-screen speech requests it only explicitly. Optional Vietnamese text, accent, delivery and acoustic intent remain caller instructions, not generated or inspected speech. Missing audio remains UNKNOWN rather than becoming silent. Explicit UNKNOWN text, constraints, language, speech, acoustic intent or capability cannot become compatibility support.

`ModelCapabilityProfileDraft.v1` accepts only explicitly provided **TEST_ONLY** metadata. `compile_capability_mock` returns `PLAN_COMPATIBLE_SIMULATED`, `HOLD_UNSUPPORTED` or `HOLD_UNKNOWN`, with deterministic per-feature rationale, complete input snapshots and digest pins. An absent profile or absent feature stays UNKNOWN. Unsupported and unknown reasons are retained together when both exist. Serialized preview replay recomputes the result and rejects tampering. All previews remain **SIMULATED_ONLY**, media **NOT_INSPECTED**, perceptual quality/behavioral outcomes/rights **UNPROVEN**, and execution authority **NONE**. No vendor is selected or endorsed; no model, TTS, media generation or inspection occurs.

Bounds apply together: 128 KiB serialized JSON, 16 KiB per UTF-8 string, depth 12, 4096 value nodes, 128 items per JSON collection; 16 hypotheses/claims/external source snapshots plus the bound intake snapshot, 32 shots, 16 objects per shot, six shot constraints, 64 edges, total plan time at most 300000 ms. Aggregate byte/node limits cover the complete nested preview, so individual maxima cannot necessarily be combined. Duplicate/extra keys, nonfinite numbers, oversized integers, excessive depth/size, custom values, cycles and invalid Unicode reject. Import isolation allows only normal interpreter module loading; there is no application import-time I/O or provider registration.

## Registered Windows evidence

Observed platform: Windows 11 AMD64, Python 3.14.7, pytest 9.2.0, pytest-xdist 3.8.0. Runner indicators reported `RUNNER_ENVIRONMENT=self-hosted`, `GITHUB_ACTIONS=true`, `MT01_V21_ENABLED=1`; plugin autoload was enabled. These are source-test environment observations, not private custody or hosted-CI attestations. The unchanged trusted `execute_plan` ran the registered commands, with the existing controller observer injected through its normal observation environment.

| Group | Collected | Accepted | Failed | Skipped | Unknown | Workers actual | Wall seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| a03 | 193 | 193 | 0 | 0 | 0 | 12 | 32.927 |
| bq-01a | 328 | 328 | 0 | 0 | 0 | 12 | 10.102 |
| Total | 521 | 521 | 0 | 0 | 0 | 12 per group | — |

Commands selected by MT01:

```text
python -m pytest tests/test_a03_creative.py tests/test_a03_upgrade_v11.py -q -n 12
python -m pytest tests/test_bq_01a_brief_causality.py -q -n 12
```

Each actual test run emitted `MT01_XDIST_WORKERS_COLLECTED=12` from the protected controller hook. Round 2 additionally ran `--collect-only -q` diagnostics over exactly the selected test files; unique node IDs, actual collection totals and their hashes are retained in the WorkItem. Quiet round 1 output did not expose a total collection count, so its original null collection fields remain preserved. Its executed counts and completed-worker receipts remain recorded without retroactive invention.

MT01 selected exactly **a03 + bq-01a**, profiles `mt01-a03-tests` and `mt01-component-onboarding`, reason `ONBOARD_CONSUMER_OWNER_CLOSURE`. Missing mappings, unknown ACs/consumers, owner collisions and fallback: none. The protected `verified_onboarding_binding` helper independently replayed the new-owner binding against the exact immutable baseline and returned true. The manifest owns exactly the four new Python files and new test; its four declared consumers are real A03 paths, all owned by the existing `a03` group. They are references, not re-owned files. `contracts=[]`; existing contracts and PULSE closed schemas remain unchanged.

Tests cover real A03 intake/digest, freeform Unicode, null and explicit product references, caller hypotheses, source spoofing, false metrics/claims/rights, DAG/cycle/dangling/reversed-time failures, continuity, UNKNOWN propagation, all declared capability features, silent/voiceover/Vietnamese intent, fiction/animation and physical intent separation, strict JSON/size/depth bounds, tamper/replay rejection, immutable detached data, import isolation, and denied application effects. The A03 consumer integration test explicitly writes existing A03 fixtures to pytest scratch before comparing unchanged stored bytes across BQ calls; the new BQ calls perform no storage operations.

## Exact fingerprints

SHA-256 of the implementation, tests and owner manifest matched the final tested bytes after round 2:

| Path | SHA-256 |
|---|---|
| `src/ai_commerce/creative_quality/__init__.py` | `a0e2c697653e8718dd3b7b7397416f2dc8768983195ad6fe742decc6e1e5bba6` |
| `src/ai_commerce/creative_quality/behavioral.py` | `b36c06ed42f642eee728767e7ecdef77bd9c0a250e6667bb1ea40e7e1f77290b` |
| `src/ai_commerce/creative_quality/causality.py` | `65045ce608d997e6602241d48d0087bc8a789d4bafb85eb51550ac2a7b84e380` |
| `src/ai_commerce/creative_quality/capability.py` | `204aedb3f114222f7ec822297562afe19ca95d7c0353140cbde4d1ad2856583d` |
| `tests/test_bq_01a_brief_causality.py` | `0afd2480d7c2ddefcfd650b4b4aa9f41f62b51e5f54d12441a1d0e3cebaa76df` |
| `work_items/mt01-components/bq-01a.json` | `bff8956d7a80627e488ca04d3455e5a010da4174a2e6615c8ce2e97a115c5946` |

Unchanged genuine A03 consumer and test fingerprints:

| Path | SHA-256 |
|---|---|
| `src/ai_commerce/creative/briefs.py` | `6e4ba06ec1342cfe981488ce0d9b73c15949ff95e8ae438326a54d740079edde` |
| `src/ai_commerce/creative/intent.py` | `20d8e4d3e7f8f923a7b0efa87aeb311b138eb63be4aa6fff45b90799f178ba59` |
| `src/ai_commerce/creative/reasoner.py` | `d270abac197a4a6b4c202f23d7c641b6d08b64e645704738c62d2906374397c5` |
| `src/ai_commerce/creative/sidecars.py` | `6cd277af4aca530c11c9f2d1f8db8a36e7ffe1983f872e0ef715ad3e983537b0` |
| `tests/test_a03_creative.py` | `2a64229cbf0053f780ba2801329d26ba75d09793193ae94cf9a0953e38f33d6f` |
| `tests/test_a03_upgrade_v11.py` | `a8239f45682052de393b4349aea1c0e30bd9d2739602504c4dd3d96b76dd959b` |

Adopted planning source fingerprints:

| Path | SHA-256 |
|---|---|
| `docs/roadmap/WORK_TREE_B_QUALITY_001_V1_0.md` | `c3da35857bfb1d784c71f608f1762aee37a528726c5fbfa7116d002d24eeb9af` |
| `docs/roadmap/WORK_TREE_B_QUALITY_001_V1_0.json` | `2146ff71d66e989edff5b1c8b3d2f1c978fbc41c57c80a98e48a5af0f740b200` |
| `docs/architecture/ADR_B002_CREATIVE_INTELLIGENCE_QUALITY.md` | `6a0b427fcbbe981540b3ad55258e08cb183c08ad1ac20059ea1084f728483f69` |
| `docs/research/B_QUALITY_001_CIMQ_CRI_SYNTHESIS_V1_0.md` | `f3f25934b93acb6d19e267c4fcd6ce64b4788d6114817ce41846ddefeb38d806` |

The WorkItem also pins the unchanged registry, MT01 planner/verification modules and model-default configuration. It retains actual group commands, exit status, accepted/failed/skipped counts, worker-collection evidence, collection node IDs, source hashes and sanitized stdout/stderr from both attempts. Raw-log hashes refer to original bytes; absolute workspace/temp/interpreter prefixes are replaced in the embedded text. No private machine path or secret is included here or in the delivered WorkItem. Receipt-bearing WorkItem/handoff metadata was finalized after execution; EF02 must bind all eight final files when publishing and independently verifying the candidate.

## Ownership and BQ-01b

Q1 proposes source-bound audience hypotheses; Q6 will require A09/A11 observations, source/window/asset metrics, counterevidence and next-brief proposals for Brain. Q1 does not claim behavioral learning or change strategy. Q3 plans requested causal/acoustic constraints; Q5 requires actual Q4 findings, scoped repair, preservation and reinspection. R6 human calibration and R7 repair selection remain future work.

Brain retains creative strategy, meaning and brief approval. PULSE owns permitted product runtime coordination after separate trust and per-effect grants. EF01/EF02 build and verify source; Engineering is not the PULSE product scheduler. This cut adds no wire fields, trusted receiver sidecar compatibility or runtime authority. There were no application accounts, browser/HostOps/CDP operations, provider network calls, credential or secret enrollment, spend, publishing or PULSE service execution.

Next BQ-01b: extend versioned Vietnamese pronunciation/accent/tempo/acoustic intent fixtures and explicit voice/capability semantics offline, including voiceover versus on-screen speech and adverse UNKNOWN/unsupported cases. Actual capability observations, human perceptual calibration, voice rights, generated speech/media and provider access require separately scoped work and authorization. This handoff grants none of those effects.
