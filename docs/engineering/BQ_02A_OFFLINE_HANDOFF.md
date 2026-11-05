# BQ-02a offline source handoff

Local source tests: **PASS_LOCAL_REGISTERED**. This is bounded **BQ-02a**, Q2 adversarial preflight + Q4 T1 local-byte inspection bridge. T2/T3 remain planned BQ-02b work. The WorkItem status is `BUILD_VERIFIED_FOR_SCOPE`; candidate publication and outer EF01 final-byte binding remain pending. Candidate does not mean PR merged.

Pinned source is exactly `main@52f5a96a0acde86d9cf35cc080def8b04f5aa79c`. Initial HEAD matched, the worktree was clean, all eight authorized ADD-only paths were absent, and 836 baseline files were fingerprinted. No Git writes, PR, merge, browser/CDP, protected wake, external message, provider API or PULSE action was issued. Outer EF02 alone may publish the verified candidate. Independent Brain review, exact candidate HEAD/BASE hosted Windows CI/oracle and protected review COMMENT wake belong to the later authorized publishing workflow.

Human said “ok tiếp tục BQ-02” on 2026-11-05 after BQ-01b Issue #417 / merged PR #422. The source cut follows adopted Issue #373, ADR B002 and the B-QUALITY Work Tree. Q2 #342 and Q4 #346 are references, not runtime grants. Tools do not attest the serving model; no explicit Human model choice, model fallback, native-human calibration, provider ranking or production generation is claimed.

Actual environment: Windows 11 AMD64 (`Windows-11-10.0.26300-SP0`), Python 3.14.7, pytest 9.2.0, pytest-xdist 3.8.0. RUNNER_ENVIRONMENT=self-hosted, GITHUB_ACTIONS=true, MT01_V21_ENABLED=1 were observed; PYTEST_ADDOPTS / PYTEST_PLUGINS overrides and disabled plugin autoload were absent initially. These indicators do not independently attest hosted CI. ffmpeg/ffprobe were installed; their actually returned version strings follow.

```text
ffmpeg version 9.0.1-essentials_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers
ffprobe version 9.0.1-essentials_build-www.gyan.dev Copyright (c) 2007-2026 the FFmpeg developers
```

Plain Python helpers initially resolve the installed runner package under `C:/TOOL/ai-commerce-runner/_work/ai-commerce/ai-commerce/src`. Its verification.py, minimum_test.py, impact_v21.py and pytest_profile.py bytes were independently compared with the pinned workspace and all four match exactly. Registered pytest uses the unchanged project `pythonpath = ["src"]`. Artifact capture explicitly resolves workspace/src; the import error before that correction is retained below. No installed package or environment guard was edited.

MT01 ingress profile is exactly **mt01-component-onboarding**. The unchanged V2 planner selected exactly **a03 + a13 + bq-01a + bq-01b + bq-02a**, reason **ONBOARD_CONSUMER_OWNER_CLOSURE**, with no unknown AC, test, consumer, unmapped group, ownership overlap or full fallback. The data-only owner manifest registers exactly the three new sources and one new test, with `contracts=[]`. Read-only real consumers are causality.py, behavioral.py, inspection.py, optional voice_intent.py/acoustic_scene.py and A03 briefs.py, all exercised by the new tests. The unchanged `verified_onboarding_binding` independently returned true for bq-01a, bq-01b and bq-02a.

A06 is an explicit supplemental unchanged regression requested by the user because A13 reuses A06 inspection. It uses the exact existing mt01-a06-tests command. It is not an extra selected owner, fabricated consumer mapping or broad fallback.

The actual registered execution commands were identical in both rounds:

```text
python -m pytest tests/test_a03_creative.py tests/test_a03_upgrade_v11.py -q -n 12
python -m pytest tests/test_a13_media_inspection.py tests/test_a13_claim_rights.py tests/test_a13_link.py tests/test_a13_evidence_pack.py -q -n 12
python -m pytest tests/test_bq_01a_brief_causality.py -q -n 12
python -m pytest tests/test_bq_01b_voice_acoustics.py -q -n 12
python -m pytest tests/test_bq_02a_critic_t1.py -q -n 12
python -m pytest tests/test_a06_e01.py -q -n 12
```

Every execution used the normal `execute_plan` / `pytest_observation_env` observer path (the supplemental A06 command used the same observer environment). Each independently returned exactly one **MT01_XDIST_WORKERS_COLLECTED=12** receipt from the unchanged controller collection hook. `-n 12` or startup console text alone was not counted as workers_actual. Exact selected-file `--collect-only -q` diagnostics retain unique node IDs and hashes in the WorkItem; these collection diagnostics are not substituted for registered execution.

Final round 2:

| Group | Collected | Executed | Passed | Failed | Skipped | Workers actual | Wall seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| a03 | 193 | 193 | 193 | 0 | 0 | 12 | 15.816 |
| a13 | 197 | 196 | 196 | 0 | 1 | 12 | 12.833 |
| bq-01a | 328 | 328 | 328 | 0 | 0 | 12 | 9.862 |
| bq-01b | 450 | 450 | 450 | 0 | 0 | 12 | 11.420 |
| bq-02a | 282 | 282 | 282 | 0 | 0 | 12 | 13.460 |
| a06 (supplemental) | 7 | 7 | 7 | 0 | 0 | 12 | 11.221 |
| Total including A06 | 1457 | 1456 | 1456 | 0 | 1 | 12 per group | — |

The single existing A13 skip is `test_real_linked_files_are_rejected_when_os_permits`: the sandbox denies hard-link and symbolic-link creation. A13 separately tests link/reparse detection. **Zero required real-media tests were skipped**; missing ffmpeg/ffprobe fails the new required tests with UNKNOWN/HOLD. All 282 new cases passed, including 273 parametrized cases. Two required cases create their own genuine MP4 in tmp_path (silent video and real silent AAC audio), call the actual A13 inspector, and bind the real output. A third creates segmented black/white/black media and preserves multiple exact A13 intervals. Metadata, full decode, absent audio, silence, per-asset hashes, limits, missing tools, corruption, nested tampering, P0 holds, P3/fiction/silent/nonproduct intent, prompt injection, deterministic replay and denied effects are covered. No new fake OCR/ASR/VLM, pronunciation/brand/emotion/motion result, human ROI or publishing quality claim exists.

Two registered rounds were used; no third pytest round was run. Round 1 had 275 new cases and no pytest failures:

| Group | Collected | Executed/passed | Failed | Skipped | Workers actual |
|---|---:|---:|---:|---:|---:|
| a03 | 193 | 193 | 0 | 0 | 12 |
| a13 | 197 | 196 | 0 | 1 | 12 |
| bq-01a | 328 | 328 | 0 | 0 | 12 |
| bq-01b | 450 | 450 | 0 | 0 | 12 |
| bq-02a | 275 | 275 | 0 | 0 | 12 |
| a06 (supplemental) | 7 | 7 | 0 | 0 | 12 |

After round 1 passed, source review closed a malformed optional direction schema type that could raise TypeError instead of the validation error. Five cases cover the correction; two further cases test native record tampering. The two genuine media tests gained scratch receipt/tool-output artifacts. Round 2 ran all registered groups again against those final source/test bytes. No protected file or A13/A06 interface changed.

All original failures are preserved as postprocessing failures rather than rewritten as successful tests:

1. Round-2 registered pytest returned 0, then the handoff helper exited 1 with `REAL_MEDIA_ARTIFACTS 0` / `AssertionError: Required genuine media artifacts missing/duplicated`. Its glob contained 31 literal characters; pytest truncates the directory prefix to 30 (`test_genuine_mp4_created_inspe`). The original sandbox temporary files disappeared on process teardown and are not claimed as retained artifacts.
2. The first separate artifact-recovery helper exited 1 with an ImportError for inspection_evidence, resolving the installed runner package. It was corrected to use workspace/src. Registered pytest commands, hooks, environment guards and source files were unchanged.
3. WorkItem contract validation initially exited 1 because `SOURCE_VERIFIED_CANDIDATE_PENDING` is outside the existing WorkItem status enum. The data-only receipt now uses `BUILD_VERIFIED_FOR_SCOPE` and stores candidate state separately; existing WorkItem schema validation passes.

The artifact recovery performed two separate genuine test-only local inspections in persistent workspace scratch, using the final tested `make_mp4` fixture helper and unchanged A13. These are **additional offline evidence fixtures, not the original pytest artifacts or an extra pytest group/round**. Actual tool returns, A13/T1 reports, base64 MP4 bytes and hashes are embedded in the WorkItem, so the evidence survives scratch cleanup. No positive finding was mocked. The recovery completed successfully; its exact provenance distinguishes it from the two required pytest cases.

| Recovery fixture | Actual asset SHA-256 | A13 record_sha256 | Returned tool calls |
|---|---|---|---:|
| No audio stream | `4b6eb223ed9c2cb037c89fcb287d90db8c06ce6c4c7700eef9e3a2eb502861fd` | `bcc2ea2b9b0706fd0466cdc7165f04fadb5924b6b6616c826bc35a7c3ddfd8d1` | 5 |
| Silent AAC audio | `39bdbee349257d7ae6d1694ad88eb5452134d24e78f7fb26dc217ded9359023c` | `3734dd65a4de35b063529f508cf930cdb6f2285194bea92bd8531251dcb20ab8` | 6 |

Import the new modules qualified. `critic.preflight_critique` revalidates the complete native plan/source chain and only includes BQ-01b direction actually supplied with its matching digest. Generated challenges retain source refs, intended intervals, counterexamples and uncertainty; unproven material claims/rights stay P0 HOLD. Caller text remains untrusted data. No media or external effect is invoked by preflight.

`inspection_evidence.inspect_local_t1` requires explicit Path + native ReleaseAsset/options/limits and delegates exactly to A13 inspect_media_release. It has no ffprobe/FFmpeg implementation or custody ledger. Hand-entered findings cannot establish OBSERVED through public constructors. Native validation recomputes fields against the authoritative nested A13 receipt; serialized asset-bearing JSON requires explicit actual local A13 reinspection. The in-memory binding is not a signature against hostile Python execution. Asset hashes, tools, streams/duration, full decode and real diagnostic times remain technical facts. Regions remain UNAVAILABLE. A13 FAIL/UNKNOWN preserves reasons and cannot become verified; known absent audio is NOT_APPLICABLE. Black/silence observations do not become creative P0.

`quality_review.adversarial_review` requires independent preflight and evidence digest pins and preserves source P0/P1/UNKNOWN. Its scope is PRE_RENDER_ONLY without asset evidence; after inspection it separates PLAN and ASSET challenges. T1 PASS never proves product truth, rights, semantic/perceptual quality, human/native performance or publication authority. T2/T3 remain NOT_RUN. The creative boundary matrix documents exemptions and counterexamples.

Exact source/test/owner/boundary-document fingerprints match the final round-2 bindings:

| Path | SHA-256 |
|---|---|
| `src/ai_commerce/creative_quality/critic.py` | `bc5ece496956385387153af01ac6ecccbfbee1c5626dbe7381464d93f1d6a8b9` |
| `src/ai_commerce/creative_quality/inspection_evidence.py` | `3e32ae5e8bd01c3a6ee39f415b01ec86b8434ddccf64b64d5b491bb2e142cb74` |
| `src/ai_commerce/creative_quality/quality_review.py` | `6ad54306b63231258ab0a8ccaf0c52bbe7dc66ecabf1a66ce8035cc361758af4` |
| `tests/test_bq_02a_critic_t1.py` | `e414678da51db96eb6e1a3dc7a3f8c3d65aa5f5b954419031e32dc493cfd2ead` |
| `work_items/mt01-components/bq-02a.json` | `4537ffeafaec28c734f0c471b42db57b12ea9f63b34ab44914ba422b980a5c23` |
| `docs/creative/BQ_02A_Q2_Q4_EVIDENCE_BOUNDARY.md` | `d7004db7e37c8dbb5d17d70e19fbd983f5106efe8d59d05571d32488eb09d3d4` |

The WorkItem embeds both rounds’ original commands, counts, node IDs, binding snapshots, stdout/stderr, captured-byte hashes, 12-worker provenance, postprocessing failures and separate recovered media evidence. The source baseline and protected/planning file fingerprints are retained. Source/test/owner/boundary-document bytes did not change after round 2. This engineering handoff and WorkItem were finalized afterward to record real outcomes; their original tested versions remain bound in the receipts. Outer EF01 must verify the final complete eight-file candidate. No recursive self-hash, already-published candidate, merged PR or hosted Brain PASS is claimed.

Final scope audit returned **PASS_SCOPE_AUDIT**: exactly the eight authorized additions (836 baseline files → 844 files), zero changed/deleted baseline files, unchanged pinned HEAD, and the same five MT01 groups with no mapping gaps or fallback. Both portable real-media artifacts passed hash/nested-A13/tool-return integrity checks. No additional runtime, agent, ledger, provider authority or PULSE rights were introduced.
