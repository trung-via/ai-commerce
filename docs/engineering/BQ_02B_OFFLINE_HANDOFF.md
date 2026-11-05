# BQ-02b offline source handoff

Local registered source verification: **PASS_LOCAL_REGISTERED** in round 2.
The complete executor worktree contains exactly eight authorized additions.
Source, test, owner manifest and creative protocol bytes equal the round-2
bindings. This handoff and WorkItem were finalized afterward with actual
receipts; outer EF01 must bind and verify the final complete candidate bytes.
Candidate publication belongs to EF02; no PR, merge or publication is claimed.

Source base is exactly `a1c21cfa7fd7a4c401cd0bdd48af343cc763fef9`.
Initial HEAD matched, status was clean and all eight paths were absent. All 844
baseline files remain byte-for-byte unchanged (852 final files). No Git write,
protected source/schema/CI/MT01/Work Tree change or guard bypass was performed.
Human @AI Commerce said “tiếp tục” after BQ-02a PR #426 / issue #423. Research
#342/#346, Issue #373, ADR B002 and the adopted Work Tree are design evidence,
not remote model/provider/Human-study grants. There is no additional owner A20,
agent system, media custody ledger or published quality score.

`config/engineering/model-defaults.v1.json` supplies PROJECT_SOFT_DEFAULT
`codex/gpt-6.1-sol/max`, not a fresh explicit Human model choice. The immutable
source action is pinned. Tools do not attest the actual serving model; no
runtime model identity or fallback attestation is made. No assessor or provider
was invoked. External paid effects, content upload, TTS/product media generation,
study execution, rights/identity disclosure, publish, spend and PULSE/CDP/HostOps
private effects remain unauthorized. Local synthetic test fixture creation was
the expressly authorized exception for verification.

Actual environment: Windows 11 AMD64 (`Windows-11-10.0.26300-SP0`), Python 3.14.7.
RUNNER_ENVIRONMENT=self-hosted, GITHUB_ACTIONS=true and MT01_V21_ENABLED=1 were
observed; initial PYTEST_ADDOPTS/PYTEST_PLUGINS/autoload-disable overrides were
absent. These indicators do not attest hosted CI. Verification helpers imported
workspace/src directly; their origins and SHA-256 are retained in the WorkItem.
The existing pytest configuration, runner and controller hook were unchanged.

MT01 ingress profile is **mt01-component-onboarding**. The unchanged V2 planner
selected exactly **a03 + a06 + a13 + bq-01a + bq-02a + bq-02b**, reason
**ONBOARD_CONSUMER_OWNER_CLOSURE**, with zero unknown AC/test/consumer, mapping
holds or ownership overlap. A06 is selected because the new test reuses its
existing still fixture and service, then binds its manifest through A13. BQ-01a
and A03 are actual source-plan consumers. BQ-01b was not independently selected;
no unselected group or blanket full suite was added. The actual registered plan
has `fallback=false` and `narrow_execution_observed=true`. Independently rebuilt
`verified_onboarding_binding` returned true for bq-01a, bq-02a and bq-02b.

Both registered rounds used these exact commands through unchanged
`execute_plan` / `pytest_observation_env`:

```text
python -m pytest tests/test_a03_creative.py tests/test_a03_upgrade_v11.py -q -n 12
python -m pytest tests/test_a06_e01.py -q -n 12
python -m pytest tests/test_a13_media_inspection.py tests/test_a13_claim_rights.py tests/test_a13_link.py tests/test_a13_evidence_pack.py -q -n 12
python -m pytest tests/test_bq_01a_brief_causality.py -q -n 12
python -m pytest tests/test_bq_02a_critic_t1.py -q -n 12
python -m pytest tests/test_bq_02b_multimodal.py -q -n 12
```

Each selected group in each round independently emitted exactly one
`MT01_XDIST_WORKERS_COLLECTED=12` from completed controller collection. The
WorkItem preserves each group's original stdout/stderr, evidence payloads,
commands, hashes, timestamps, worker provenance and binding. Startup text or
`-n 12` alone was never accepted. Separate exact-file `--collect-only -q`
diagnostics recorded the actual node IDs/counts and source hashes. They execute
no tests, are not registered verification passes and supply no worker proof.
No serial test execution or worker fallback was used.

Final round 2:

| Group | Collected | Executed | Passed | Failed | Skipped | Actual workers | Wall s |
|---|---:|---:|---:|---:|---:|---:|---:|
| a03 | 193 | 193 | 193 | 0 | 0 | 12 | 20.814 |
| a06 | 7 | 7 | 7 | 0 | 0 | 12 | 13.16 |
| a13 | 197 | 196 | 196 | 0 | 1 | 12 | 15.626 |
| bq-01a | 328 | 328 | 328 | 0 | 0 | 12 | 12.315 |
| bq-02a | 282 | 282 | 282 | 0 | 0 | 12 | 23.188 |
| bq-02b | 249 | 249 | 249 | 0 | 0 | 12 | 27.741 |

Totals: **1,256 collected; 1,255 executed/passed; 0 failed; 1 skipped; 0 errors**.
Executed means call-phase passed + failed; skipped/setup-error nodes are not
counted as executed. The one unchanged A13 skip is
`test_real_linked_files_are_rejected_when_os_permits` (line 207): the sandbox
denies hard-link and symbolic-link creation. Existing A13 and new synthetic
link/reparse detection checks ran. **Zero required real-media cases skipped.**
The new group has **249 deterministic cases**, including five genuine accepted
local-media fixture cases. Missing ffmpeg/ffprobe/libx264 or snapshot locking
fails required acceptance with HOLD, rather than skipping or fabricating evidence.

Round 1 is retained as failed verification, not rewritten as success:

| Group | Collected | Executed | Passed | Failed | Skipped | Error events | Actual workers | Wall s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| a03 | 193 | 193 | 193 | 0 | 0 | 0 | 12 | 18.714 |
| a06 | 7 | 7 | 7 | 0 | 0 | 0 | 12 | 14.035 |
| a13 | 197 | 196 | 196 | 0 | 1 | 0 | 12 | 29.041 |
| bq-01a | 328 | 328 | 328 | 0 | 0 | 0 | 12 | 16.985 |
| bq-02a | 282 | 282 | 282 | 0 | 0 | 0 | 12 | 25.583 |
| bq-02b | 237 | 236 | 235 | 1 | 0 | 2 | 12 | 27.345 |

Round 1 BQ-02b had 237 collected nodes: 235 passed, one failed call, and one node
errored during both setup and teardown (two error events). The failed assertion
assigned OBSERVED over existing OBSERVED; it was repaired to make a real PASS
mutation. An oversized negative input received pytest's automatic giant ID,
exceeding Windows' 32,767-character PYTEST_CURRENT_TEST value limit before the
case ran. Explicit short IDs repaired that harness fault while retaining the
negative input. Original logs, hashes and IDs remain in the WorkItem. All five
required genuine media cases already passed in round 1. Source review then made
the pure T2 binder refuse observed payloads and exposed known absent audio in
T3 proposals; the final round covered these changes and added negative closure
cases. There were exactly two registered verify-fix rounds and no third pytest
round. There were no round-2 postprocessing faults. An early PowerShell read
parse fault is also retained in repair history; it changed no files.

The actual installed tool versions were:

```text
ffmpeg version 9.0.1-essentials_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers
ffprobe version 9.0.1-essentials_build-www.gyan.dev Copyright (c) 2007-2026 the FFmpeg developers
```

| Executable | SHA-256 (unchanged before/after verification) |
|---|---|
| ffmpeg | `72a489eccd008c2ec2c0a5856c5c75bc3d8bbfa90166c4566865c246445e6aa3` |
| ffprobe | `19202b23c0043f15ad1b7bce2344f406fd52bd6efd8f995ce02e7392a1cec52f` |

These are actual local synthetic pytest fixtures, not external media or mocked
observations. AAC signal values are decoded integer statistics, not performance
ratings. The five fixture receipts bind base64 MP4 bytes, original T1/A13,
snapshot reinspection, T2 record/digests, test-source SHA and transparently
forwarded actual bounded argv/output-size/output-hash traces.

| Fixture | Observed asset SHA-256 | Gray means | PCM peak/RMS floor | Actual tool calls |
|---|---|---|---|---:|
| a06-existing-fixture | `6d7ac62da3fdb61349ab577387dad9915e1dd62087141dee6f795b82d78a1b81` | 1093/32 | 0/0 | 14 |
| aac-signal | `779858ade7cb52075b4a69f38bd98ed1618bef8615567bd5e2d217ac3a5c981a` | 0 | 4177/2836 | 14 |
| aac-silence | `39bdbee349257d7ae6d1694ad88eb5452134d24e78f7fb26dc217ded9359023c` | 0 | 0/0, 0/0 | 15 |
| contrast | `1c42c56f0c0af9fafe2b95094486d8f9e1c8a3460cd45f84f3bb7f36ab1ca4e2` | 0, 255, 0 | NOT_APPLICABLE | 13 |
| no-audio | `4b6eb223ed9c2cb037c89fcb287d90db8c06ce6c4c7700eef9e3a2eb502861fd` | 0 | NOT_APPLICABLE | 11 |

Captured rawvideo/PCM output hashes match every recorded sample SHA. Captured
media SHA, nested A13 schema/digests and T1/T2 digests also passed integrity
checks. Those postprocessing checks admit no new observed native record and
are not substitutes for local reinspection. Four required MP4-only fixture
receipts retain the identical source bytes needed for full reconstruction.
The fifth, A06, retains its original A13-bound manifest hash but not the scratch
manifest bytes: replay of that original T1 requires the matching original
manifest as well as MP4. Regenerating the A06 fixture starts a fresh chain.
This limitation does not turn its real pytest observation into portable custody
or rights proof. No failed artifact recovery or substituted fake receipt exists.

T2 requires qualified imports, a native original BQ-02a T1, exact asset ref/SHA,
an explicit absolute user-owned local temporary Path root and
`allow_local_probe=True`. It reads through A13 safe paths/open into a bounded
private snapshot, pins expected/observed SHA, and holds a Windows read share-mode
lock denying writes/deletes while real A13 and T2 use the same snapshot bytes.
The original mutable filename is not used for T2 tools. Snapshot/source checks
finish before OBSERVED is bound. A separate postprocessing lock-path inspection
also measured denied file append, file delete and containing-directory rename;
all three were blocked. Unsupported locking hosts yield UNKNOWN.

The fixed profile is `bq02b-t2/gray64-pcm8k-quarter/v1`, detector `integer-pixel-pcm-statistics/1.0`.
Up to eight full-frame 64×64 gray samples and one/two quarter-second 8 kHz mono
s16le windows have bounded fixed argv/output/time. Source PTS/frame indices are
not measured; requested rational time and actual decimal seek argument remain
distinct, with explicit approximate-seek uncertainty. Missing tools/streams,
unsafe paths, hash mismatch, decode/timeout/output faults or A13 disagreement
leave UNKNOWN and preserve original T1. Known absent audio is NOT_APPLICABLE.
A13 and Q2 remain authoritative for their own scopes; no inspector is rewritten.

`BQ02bMultimodalReview.v1` pins original preflight/plan, BQ-02a review/T1/A13,
observed asset, complete T2 samples and T3 proposal/status digests. Persisted
asset-bearing records require explicit local replay with the original bytes
(and any original A06 manifest binding). Native validation rejects detached JSON
as in-memory observation. Rehashing a fake native T1 alone fails actual snapshot
comparison. Replay equality and source/asset substitution were exercised.

T3 is NOT_RUN / NOT_AUTHORIZED. Optional OCR/ASR/motion/lip-sync regions and
rubrics are proposals over coverage and Q2 hypotheses, not observations. Caller
JSON/provider labels/booleans/confidence cannot establish authentication, native
Human review, calibration or rights; supplied results/grants are HOLD_UNVERIFIED.
An authenticated receipt format, independent verifier and separate grant are
absent and deliberately not faked. Media/subtitle/OCR/narrative instructions are
untrusted data. BQ-03 blind R6 calibration remains future authorized work.

T1 FAIL / Q2 P0 HOLD dominates, P1 remains REVISE, P3 remains Brain-only. True T2
signals still leave perceptual/semantic/Human quality UNKNOWN; missing samples
never become creative PASS. There is no quality score, release/rights/spend
authorization, complete R1–R8 claim or averaging of unverified T3. See the
creative evidence protocol for the precise matrix and counterexamples.

Final round-2 immutable source/test/owner/protocol fingerprints:

| Path | SHA-256 |
|---|---|
| `docs/creative/BQ_02B_T2_T3_EVIDENCE_PROTOCOL.md` | `95abb617427a7287a8d2cda324aae1b586d2397590163c6b15a4ecb0199fdcb0` |
| `src/ai_commerce/creative_quality/multimodal_review.py` | `95a7a5a66a721adf542ed7c912d2a0e4e443256f0f535a54ebba5f1e956832b5` |
| `src/ai_commerce/creative_quality/t2_sampling.py` | `e807b469836427d8b3d45386d5ebe351816a32a0154614dc54ab5d1dce718afa` |
| `src/ai_commerce/creative_quality/t3_assessment.py` | `651f249115900362fb353e90f5f8894fc758c719cce31fe365321cc33631f517` |
| `tests/test_bq_02b_multimodal.py` | `c93109fabf16bd29f00d245714bff29803429445dd3ab1fba26b2f087e648074` |
| `work_items/mt01-components/bq-02b.json` | `403bbb004c017857c7e306e86459b98095b48e854451d174e0fb55d2b7e43e3f` |

Only this engineering receipt and WorkItem were finalized after round 2. Their
original tested byte hashes remain in the round binding; neither embeds its own
recursive final hash. Outer EF01 verifies final eight-file bytes before EF02 may
publish a candidate. Later independent Brain PR review requires hosted Windows
four-worker CI plus artifact/product AC review, the exact PASS four-line owner
Conversation comment and COMMENT review wake, followed by trusted repository
auto-merge after all guards. None of those publishing/private effects was issued
by this executor, and no additional Human checkpoint is requested on source PASS.
