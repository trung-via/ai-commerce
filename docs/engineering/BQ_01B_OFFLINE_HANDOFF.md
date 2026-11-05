# BQ-01b offline source handoff v1.0

Source-only result: **PASS_LOCAL_REGISTERED / CANDIDATE_PUBLICATION_PENDING**. Final Windows verification collected and executed **971 tests: 450 new BQ-01b + 328 unchanged BQ-01a + 193 unchanged A03**. All 971 passed; zero failed, skipped, errors, xfailed, xpassed or unknown test results. Every registered group independently observed **12 completed worker collections**. No full-suite fallback, omitted group or serial execution substitution occurred. These are synthetic source-contract results; no quality benchmark or real-world voice evaluation exists.

Baseline is exactly the requested `main@381b70f0979258d24a2524c1e8f133338be11ddc`; the observed checkout HEAD equals that SHA. Before edits, the worktree was clean and all eight authorized ADD-only paths were absent; 828 baseline files were fingerprinted. Final comparison found exactly those eight additions, no changed/deleted baseline file, and unchanged HEAD. This executor issued no Git writes, PR, merge or deployment. Outer EF02 alone publishes the verified candidate. Exact candidate-HEAD hosted Windows CI and independent Brain acceptance remain pending; candidate readiness does not mean PR merged.

Human requested “@AI Commerce tiếp tục roadmap” on 2026-11-05. ChatGPT Brain selected this bounded BQ-01b Engineering cut after successful BQ-01a PR #412, under decision #373, the adopted B-QUALITY Work Tree and ADR B002. The immutable preferred executor profile is codex / gpt-6.1-sol / max, selection_source **PROJECT_SOFT_DEFAULT**, not a new explicit Human model/provider choice. No invoked tool returned a quota/model/capacity/permission denial and no model fallback was invoked. Tool outputs do not attest the model serving this conversation. A determinate denial would require preserved failure and HOLD.

## Interfaces and constraints

Import `ai_commerce.creative_quality.voice_intent`, `.acoustic_scene` and `.voice_compile` explicitly. Existing `__init__.py` and all BQ-01a interfaces remain unchanged.

`build_voice_intent(plan, plan_digest=..., segments=...)` returns immutable canonical VietnameseVoiceIntentDraft.v1. Each segment carries an original shot and positive absolute plan interval, monotonic script/code-point offsets, exact original script substring, and existing source refs. No script, translation, pronunciation, Unicode normalization or accent stripping is performed. NFC/NFD, Vietnamese tone spelling, native punctuation, emoji and whitespace remain exact. The nested plan and separate plan/behavioral/A03 digest pins are revalidated. Language/mode must preserve the original plan; absent language remains null/UNKNOWN. Stress/tone/pause/pacing/intonation/region are **SUBJECTIVE_DIRECTION**. Ownership, speaker identity and rights remain UNPROVEN.

`build_acoustic_scene(voice, voice_digest=..., sound_mode=..., ...)` returns AcousticSceneDraft.v1. SILENT means no speech; FULL_MUTE means no sound. Silent footage can have non-speech sound only through an explicit separate ambient request and cues. Fiction/animation and product_ref=null are valid. VO narration and on-screen speakers have distinct environment scopes; speech cues match exact voice segment IDs, shots and intervals. Scene cues cannot borrow speech refs or environments. SINGLE-channel overlaps, dangling/duplicate refs, impossible time/frame/trim intervals, contradictory mute/voice/ambient requests, and provider-verification or measurement fields reject. Optional rational frame boundaries are exact intended shot-relative windows, never measured frame rates. UNKNOWN room/reverb/noise/distance values remain unknown.

`VoiceAcousticCapabilityDraft.v1` adds finite TEST_ONLY voice/prosody/sync/acoustic/frame/trim features and exact caller language tags, bound to the unchanged ModelCapabilityProfileDraft digest. `compile_voice_capability_mock(plan, scene, profile, voice_profile, ...digest pins...)` calls the real unchanged BQ-01a `compile_capability_mock`, preserves its full checks and critical HOLD reasons, and checks the new requested features. Missing profiles/features or language uncertainty yield HOLD_UNKNOWN; explicit unsupported requirements yield HOLD_UNSUPPORTED, retaining simultaneous unknown reasons. The full nested source chain, profiles, rationale, digest pins and negative evidence replay deterministically. A silent full-mute path requires neither TTS/lipsync nor a voice extension; explicitly requested ambience still requires its own feature.

Every preview retains **media NOT_GENERATED / NOT_INSPECTED; naturalness NOT_MEASURED; dialect accuracy, speaker identity and rights UNPROVEN; costs UNKNOWN; execution authority NONE**. These modules have no network, audio/file/model, filesystem, environment, process, credential, browser, broker, CDP, publishing, spend or PULSE effect seam. Test harnesses legitimately use subprocesses, scratch storage and the existing verification infrastructure; those are source-test operations, not product runtime authority.

Bounds cover complete nested records: canonical UTF-8 128 KiB, string 16 KiB, depth 12, 4096 value nodes, 128 collection items, 64 segments/cues, 16 environments/channels, eight stress spans, sixteen pauses/language tags, intended plan duration at most 300000 ms. Individually legal maxima may exceed aggregate bounds together. Duplicate/extra keys, invalid Unicode, custom/cyclic/nonfinite/oversized inputs and boolean/float/string integer substitutions reject. The [design guide](../creative/BQ_01B_VIETNAMESE_VOICE_DESIGN_GUIDE.md) explains source/hypothesis/TEST_ONLY vocabulary, R5 direction, R4 sync and K5/K6 limits.

## Registered Windows receipts

Observed environment: **Windows 11 AMD64, Python 3.14.7, pytest 9.2.0, pytest-xdist 3.8.0**. Indicators reported RUNNER_ENVIRONMENT=self-hosted, GITHUB_ACTIONS=true and MT01_V21_ENABLED=1; no PYTEST_ADDOPTS override or disabled plugin autoload was present. These are environment observations, not independent hosted-CI or private-custody attestations.

The unchanged trusted `execute_plan` selected the registered commands below. Its normal `pytest_observation_env` loaded the existing `ai_commerce.engineering.pytest_profile` controller hook, preserving registered argv. Every group in both rounds emitted exactly one `MT01_XDIST_WORKERS_COLLECTED=12` receipt. Startup text or a `-n 12` command flag alone was not used to establish workers_actual.

```text
python -m pytest tests/test_a03_creative.py tests/test_a03_upgrade_v11.py -q -n 12
python -m pytest tests/test_bq_01a_brief_causality.py -q -n 12
python -m pytest tests/test_bq_01b_voice_acoustics.py -q -n 12
```

Final round 2:

| Group | Collected | Executed | Passed | Failed | Skipped | Workers actual | Wall seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| a03 | 193 | 193 | 193 | 0 | 0 | 12 | 17.747 |
| bq-01a | 328 | 328 | 328 | 0 | 0 | 12 | 10.673 |
| bq-01b | 450 | 450 | 450 | 0 | 0 | 12 | 12.649 |
| Total | 971 | 971 | 971 | 0 | 0 | 12 per group | — |

The 450 new cases include **441 parametrized cases**. They cover real A03 intake/digest and BQ-01a builders, native Vietnamese tones/diacritics/punctuation/NFC/NFD, caller regional labels, optional products and visual forms, mute/silent/ambient-only/VO/on-screen sync, source/shot/segment timelines and refs, false rights/performance/benchmark/metamodel metadata, hash tamper, closed/duplicate fields, size/depth/nonfinite/Unicode bounds, cross-layer UNKNOWN, unsupported/missing features, replay/frozen data, pure imports and denied application effects. Prior BQ-01a/A03 test bytes remained unchanged and passed.

Two bounded Verify-Fix rounds were used; one failed test attempt is preserved. Round 1:

| Group | Collected/executed | Passed | Failed | Skipped | Workers actual |
|---|---:|---:|---:|---:|---:|
| a03 | 193 | 193 | 0 | 0 | 12 |
| bq-01a | 328 | 328 | 0 | 0 | 12 |
| bq-01b | 450 | 449 | 1 | 0 | 12 |

Failed node: `tests/test_bq_01b_voice_acoustics.py::test_registered_new_owner_and_actual_readonly_consumer_closure`. Diagnostic: `assert (() == [])` at the original line 674. The registry returned an empty tuple; the test incorrectly required an empty list. The repair changed that assertion to an emptiness check and tightened the six existing negative admission tests to assert exact refusal reasons using the real legacy manifest mapping. No implementation or protected file changed between rounds. All round-1 stdout/stderr, the full failure traceback, commands, source hashes, worker receipts and collection node IDs remain embedded in the WorkItem. No failed result was rewritten into a pass.

Exact selected-file `--collect-only -q` diagnostics in both rounds recorded unique node IDs and totals (193/328/450). Registered execution summary totals independently match collection. Final collection-node and captured-stdout SHA-256:

| Group | Node IDs SHA-256 | Captured stdout SHA-256 |
|---|---|---|
| a03 | `d36b4e72b3c01411b9705d08721c24aa16b4b157dc9d787628ecdbc26f2fcaca` | `e5d6a9d65fef809c57e357b264471c4f6ffeb59d2294ca37917fbe16bd2c10fb` |
| bq-01a | `1cb76976261fdae766fe65758d3e600917bc1ded26eb90a532febf330c075436` | `402aa06f531db54d7ccf148e602a18d5a5852158963993903cb428f64bb72446` |
| bq-01b | `a36e02ba8a5fe2871f90fff3e11cd27719a93b778938fbf5f4bf5baaf1ab8091` | `089134fe90c3049a7169de14df9843c15741de84e05c58277d5bcb927643a1d7` |

All final stderr captures were empty (SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`). Raw captured logs and full plans are retained in ignored executor scratch; the deliverable WorkItem embeds portable sanitized logs and unique node IDs for every group/round with original captured-byte hashes. Workspace/interpreter/temp prefixes in embedded logs were replaced; no secret was collected.

## Owner and consumer admission

MT01 selected exactly **a03 + bq-01a + bq-01b**, with profiles **mt01-a03-tests + mt01-component-onboarding + mt01-component-onboarding**, reason ONBOARD_CONSUMER_OWNER_CLOSURE. The protected `verified_onboarding_binding` helper independently returned true for both bq-01a and bq-01b against the immutable base. All eight changed paths were present in every selected group's evidence binding. No missing mapping, unknown consumer/AC, owner collision or fallback was observed.

The data-only bq-01b manifest owns exactly three new Python sources and one new test. It references existing BQ-01a `causality.py`, `capability.py`, `behavioral.py` plus genuine A03 `briefs.py`; these are exercised through actual imports/builders/intake/digest calls and remain read-only. Consumers are references, not duplicate ownership. `contracts=[]`; no closed contract, A03/A05 source, .github configuration, MT01 control, B0/B0-S runtime, PULSE schema or legacy Work Tree was edited. The exact two ACs are BQ-01b.vn-voice-intent-and-provenance and BQ-01b.acoustic-scene-and-capability-mock.

## Exact tested fingerprints

The following final bytes matched the registered round-2 source snapshot:

| Path | SHA-256 |
|---|---|
| `src/ai_commerce/creative_quality/voice_intent.py` | `b60aada6c6a9ec8a466cfd57d1782b75bb9c16042ec4e7b85a21b45dc25d437e` |
| `src/ai_commerce/creative_quality/acoustic_scene.py` | `3f7973d74cc3885493734a6162558d73c2d8a85ee3346383f00ff5cf01b807ad` |
| `src/ai_commerce/creative_quality/voice_compile.py` | `f37ff87d21c45459dfb5fe80245c5c4cc721b900a43e15f2b209eb1b92b49b0e` |
| `tests/test_bq_01b_voice_acoustics.py` | `e4b325bdedf75f7ee46ed4df8eb9d55d33615aaf65e94cd7c3ef77d9337fd287` |
| `work_items/mt01-components/bq-01b.json` | `bb90bd44e13264b2db79b4c9fa676b1a9b7faa026e8f68e8146f5aa6afb63613` |
| `docs/creative/BQ_01B_VIETNAMESE_VOICE_DESIGN_GUIDE.md` | `88ee2340a53e5769abb52172ea43a0127cc9333a834dff1acca9bc40a07a0a50` |

Unchanged existing source, tests, registry and adopted planning fingerprints:

| Path | SHA-256 |
|---|---|
| `src/ai_commerce/creative_quality/__init__.py` | `a0e2c697653e8718dd3b7b7397416f2dc8768983195ad6fe742decc6e1e5bba6` |
| `src/ai_commerce/creative_quality/behavioral.py` | `b36c06ed42f642eee728767e7ecdef77bd9c0a250e6667bb1ea40e7e1f77290b` |
| `src/ai_commerce/creative_quality/causality.py` | `65045ce608d997e6602241d48d0087bc8a789d4bafb85eb51550ac2a7b84e380` |
| `src/ai_commerce/creative_quality/capability.py` | `204aedb3f114222f7ec822297562afe19ca95d7c0353140cbde4d1ad2856583d` |
| `tests/test_bq_01a_brief_causality.py` | `0afd2480d7c2ddefcfd650b4b4aa9f41f62b51e5f54d12441a1d0e3cebaa76df` |
| `work_items/mt01-components/bq-01a.json` | `bff8956d7a80627e488ca04d3455e5a010da4174a2e6615c8ce2e97a115c5946` |
| `src/ai_commerce/creative/briefs.py` | `6e4ba06ec1342cfe981488ce0d9b73c15949ff95e8ae438326a54d740079edde` |
| `tests/test_a03_creative.py` | `2a64229cbf0053f780ba2801329d26ba75d09793193ae94cf9a0953e38f33d6f` |
| `tests/test_a03_upgrade_v11.py` | `a8239f45682052de393b4349aea1c0e30bd9d2739602504c4dd3d96b76dd959b` |
| `.github/ef02/verify-profiles.json` | `3ce44393c94890be7ef52ddcd57defcc04b997a35d0c8b8aba508052ef93fa31` |
| `src/ai_commerce/engineering/impact_v21.py` | `46eb9afa2f38a5d9450d615fd57d092a34ec7d3fc5547856bd667f3e1928e8fd` |
| `src/ai_commerce/engineering/minimum_test.py` | `ff7c72b25f322cf96d97920882c57ab5a1883fed48f617b6eb83b2bf260b2c81` |
| `src/ai_commerce/engineering/pytest_profile.py` | `4749d87eee3983274f457af06ae3b7db9dc207508b3934f0fa178325c194e197` |
| `src/ai_commerce/engineering/verification.py` | `10dd19ac0b050a24c26b71191a5331a4faa605afa119a61ff7ca405aee39f565` |
| `config/engineering/model-defaults.v1.json` | `da77e34a5fde0ee704100b75e8d14278a204e90d609433e623923672e7a11f22` |
| `docs/roadmap/WORK_TREE_B_QUALITY_001_V1_0.md` | `c3da35857bfb1d784c71f608f1762aee37a528726c5fbfa7116d002d24eeb9af` |
| `docs/roadmap/WORK_TREE_B_QUALITY_001_V1_0.json` | `2146ff71d66e989edff5b1c8b3d2f1c978fbc41c57c80a98e48a5af0f740b200` |
| `docs/architecture/ADR_B002_CREATIVE_INTELLIGENCE_QUALITY.md` | `6a0b427fcbbe981540b3ad55258e08cb183c08ad1ac20059ea1084f728483f69` |
| `docs/research/B_QUALITY_001_CIMQ_CRI_SYNTHESIS_V1_0.md` | `f3f25934b93acb6d19e267c4fcd6ce64b4788d6114817ce41846ddefeb38d806` |

The WorkItem and this handoff carry receipts finalized after execution; their hashes cannot recursively attest themselves. Outer EF02 must bind all eight final files when publishing and independently verifying the candidate. The verified implementation/test/manifest/design-guide bytes did not change after final execution. Final source scope and unchanged HEAD were checked separately with read-only operations.

## Remaining gates and boundaries

Local source verification is complete. Candidate branch publication belongs to outer EF02; exact candidate-HEAD hosted Windows CI and independent Brain acceptance are next. No executor PR or merge was performed. Brain retains creative strategy and approval; PULSE scheduling and every live effect require separate trusted runtime work and per-effect grants.

No sound, music, recording, TTS, lipsync, FFmpeg, video or media inspection occurred. There was no provider/model selection, benchmark, native-speaker trial, privacy/ethnicity inference, verified accent/identity, rights or fidelity claim. No credentials, secret, browser, CDP, broker, spend, publishing or PULSE operation was authorized by these receipts. Q4 actual inspection, R6 calibration, Q5/R7 repair and real-world evaluation remain future work.
