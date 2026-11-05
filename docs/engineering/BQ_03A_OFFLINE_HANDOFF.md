# BQ-03a source handoff

Immutable source: `6ed23280b954e276a06bfc1bef2810927889f9b0`; clean entry and
eight absent paths were checked before additions. Live main and PR #433 were
read and matched this source; preferred gpt-6.1-sol/xhigh is a soft default
(`hard_default=false`). ADR B002, adopted Issue #373 and the BQ-03 Work Tree
entry were read. Human authorized “ok tiếp tục” for this bounded source cut.

Exact ADD-only diff:

1. `src/ai_commerce/creative_quality/blind_trials.py`
2. `src/ai_commerce/creative_quality/rater_receipts.py`
3. `src/ai_commerce/creative_quality/calibration_stats.py`
4. `tests/test_bq_03a_blind_calibration.py`
5. `work_items/mt01-components/bq-03a.json`
6. `work_items/BQ-03A_BLIND_CALIBRATION_001.json`
7. `docs/engineering/BQ_03A_OFFLINE_HANDOFF.md`
8. `docs/creative/BQ_03A_R6_BLIND_PAIRWISE_PROTOCOL.md`

No existing source, package initializer, schema, model default, CI, MT01,
README, Q1–Q4 consumer or PULSE runtime is owned/edited by this cut. Consumer
mapping names only exercised sources: native BQ02b linkage runs mm/t3/t2 and
Q2/Q4 pre-render composition; its reference-only plan helper runs A03 intake,
BQ01a behavioral/causal plan, critic and not-inspected T1. No media is opened
by the new BQ03a tests. No Human, VLM, OCR, ASR, TTS or provider is observed.

Registered ingress: **mt01-component-onboarding**, unchanged V2 planner and
`execute_plan` with `pytest_observation_env`. Maximum two bounded Verify-Fix
rounds; all failures and postprocessing faults must remain in the WorkItem.
No full suite is justified by this addition. Candidate publication belongs
solely to EF02 after outer EF01 verification; this executor performs no Git
writes, PR creation or merge. Candidate != PR merged. Hosted Windows CI and
independent Brain review remain later gates at exact HEAD/BASE, including
artifact/JUnit/oracle, canonical four-line PASS Conversation comment and
authenticated exact-head COMMENT wake to existing trusted auto-merge.

Final source verification is **BUILD_VERIFIED_FOR_SCOPE**. Both registered
rounds and the original failing output are retained in the WorkItem. No worker
count or PASS is inferred from argv.

Human calibration NOT_RUN; observed Human count 0; consent NOT_ESTABLISHED;
rights and production quality UNKNOWN. No additional Human consent checkpoint
is needed for a genuine source-only PASS. A future BQ03b real study requires its
own authenticated consent, sampling/privacy plan and independent attestation.

## Actual Windows MT01 receipt

Environment: `Windows-11-10.0.26300-SP0`, Python 3.14.7;
RUNNER_ENVIRONMENT=self-hosted, GITHUB_ACTIONS=true, MT01_V21_ENABLED=1.
Imports came from this worktree `src`; the unchanged registered controller
plugin was loaded through `pytest_observation_env`, with unchanged pytest argv.
These local receipts do not attest hosted CI.

The unchanged V2 planner selected **AFFECTED**: **a03 + bq-01a + bq-02a +
bq-02b + bq-03a**, reason **ONBOARD_CONSUMER_OWNER_CLOSURE**. Unknown ACs,
tests, consumers and missing mappings: zero. `fallback=false`; actual narrow
execution observed. No full-project fallback or unrelated group was run.
Registered commands (each group ran afresh in both rounds):

```text
python -m pytest tests/test_a03_creative.py tests/test_a03_upgrade_v11.py -q -n 12
python -m pytest tests/test_bq_01a_brief_causality.py -q -n 12
python -m pytest tests/test_bq_02a_critic_t1.py -q -n 12
python -m pytest tests/test_bq_02b_multimodal.py -q -n 12
python -m pytest tests/test_bq_03a_blind_calibration.py -q -n 12
```

**Workers requested: 12; actual: 12 for each of all five groups in both rounds.**
Each original stdout has exactly one `MT01_XDIST_WORKERS_COLLECTED=12` from
completed controller collection. Groups ran sequentially in this executor
session; global EF02 action concurrency was not observed. Twelve pytest workers
does not mean twelve EF02 concurrent jobs. Zero groups were reused or left
unproven at final verification. Historical round 1 evidence was not substituted
for fresh round 2 execution.

### Final round 2

| Group | Collected | Executed | Passed | Failed | Skips | Errors | Requested / actual workers | Wall s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| a03 | 193 | 193 | 193 | 0 | 0 | 0 | 12 / 12 | 20.057 |
| bq-01a | 328 | 328 | 328 | 0 | 0 | 0 | 12 / 12 | 10.328 |
| bq-02a | 282 | 282 | 282 | 0 | 0 | 0 | 12 / 12 | 14.936 |
| bq-02b | 249 | 249 | 249 | 0 | 0 | 0 | 12 / 12 | 25.221 |
| bq-03a | 330 | 330 | 330 | 0 | 0 | 0 | 12 / 12 | 16.193 |

Total: **1,382 collected, 1,382 executed/passed, 0 failed, 0 skipped, 0 errors**.
BQ-03a contributes **330 deterministic cases**. Executed means call-phase
passed + failed; no missing response is converted to a scored zero. The new
test file never opens or generates media. Unchanged legacy Q2/Q4 regression
groups retain their accepted local test fixtures; they establish no Human
observation or new production content.

### Original round 1 (retained failure)

| Group | Collected | Executed | Passed | Failed | Skips | Errors | Actual workers | Wall s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| a03 | 193 | 193 | 193 | 0 | 0 | 0 | 12 | 17.488 |
| bq-01a | 328 | 328 | 328 | 0 | 0 | 0 | 12 | 9.914 |
| bq-02a | 282 | 282 | 282 | 0 | 0 | 0 | 12 | 14.882 |
| bq-02b | 249 | 249 | 249 | 0 | 0 | 0 | 12 | 19.209 |
| bq-03a | 322 | 322 | 321 | 1 | 0 | 0 | 12 | 11.388 |

Round 1: **1,374 collected/executed; 1,373 passed, 1 failed, 0 skips/errors**.
The failing compatibility test guarded Path.open before building its A03 intake
fixture, which legitimately reads unchanged ContentBrief schemas. The repair
constructs that original source plan before the guard; actual Q2/T2/T3/mm
composition, source linkage, replay, export and reporting still execute with
filesystem/media/process/network effects blocked. No source logic was changed.
Eight additional nested-statistic/source-substitution replay cases were added.
Original failing stdout/stderr, test identity and tested hashes remain intact.

Exactly **two** registered Verify-Fix rounds ran. No third execution round.
Separate final exact-file `--collect-only` diagnostics obtained all node IDs
and counts (193/328/282/249/330) at pinned test hashes. They executed no test
calls, supplied no worker attestation and are not extra verification passes.
Original round 1 collection counts are evidenced by its complete outcome
summaries with zero setup/teardown errors or skips.

## Integrity and finalization

The WorkItem embeds every original stdout/stderr and native plan/results/
evidence payload with its SHA256, so per-call temporary sandbox directories
are not required for review. Independent postprocessing rebuilt the artifact
files, checked every byte hash and replayed native `Evidence.valid_for`: four
successful groups in round 1, all five in round 2. Independent
`verified_onboarding_binding` returned true for bq-01a, bq-02a, bq-02b and bq-03a
in both rounds. There were no verification/postprocessing exceptions. The
read-only discovery parse/path failures and lost pre-verification per-call
TEMP snapshot are explicitly retained in repair history; no test ran during
that failed receipt preparation. Baseline reconstruction followed read-only
`git diff --exit-code`, proving all existing tracked files remained unchanged.

Live main was re-read after verification and still matched the exact source.
All original worktree file hashes match the stored baseline. Only the exact
eight allowed files were added. Final immutable executable/review fingerprints:

| Path | SHA256 |
|---|---|
| `src/ai_commerce/creative_quality/blind_trials.py` | `62ee333b3deede0f3e164e9328c5e385fd85fa56cc18566885c8380c9609383d` |
| `src/ai_commerce/creative_quality/rater_receipts.py` | `29fe023f8a43e084ccd068bd3d0a39ccf8dea10151e053b319bba156933b4654` |
| `src/ai_commerce/creative_quality/calibration_stats.py` | `2cc2e1222f96a8170b85c336a1d92ab18fdcabbfec2c31d02987f18382200ac9` |
| `tests/test_bq_03a_blind_calibration.py` | `343ef452ba4b646e3a1d7419bf419c8a12ac7cb4b1840d5716e026f24a67f1d0` |
| `work_items/mt01-components/bq-03a.json` | `af0994a9edc5d0b53592e2f3711fb290039fb4737ea3991551d5e5544feaf8cb` |
| `docs/creative/BQ_03A_R6_BLIND_PAIRWISE_PROTOCOL.md` | `3f381dfaf6b4fd1148632583cda465026ce2740a2b988e38f9c98289581e1156` |

Only this handoff and the WorkItem are finalized after round 2. Source, tests,
owner and creative protocol still match the exact tested hashes above. Original
round bindings retain their historical WorkItem/handoff byte hashes; they are
not relabelled as hashes of these final receipts. No self-recursive final
WorkItem hash is embedded. Outer EF01 must verify final eight-file bytes before
EF02 alone publishes a candidate. No candidate/PR/merge/hosted CI/Brain PASS is
claimed by this source executor. Human study remains NOT_RUN.
