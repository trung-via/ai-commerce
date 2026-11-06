# BQ-03b R2 offline source handoff

Immutable source: `41bc841e3e892ef4cfda95cd372ccf7d1c141ac2`.
Fresh local attempt: `BQ03B-R6-OFFLINE-STUDY-READINESS-R2-l7xpvaxk`.
Human continuation: “tiếp tục BQ-03b”, 2026-11-06 09:30 ICT.
The local clean HEAD, all eight absent paths, exact GitHub commit, Issue #373,
BQ-03 Work Tree, BQ-03a protocol and native BQ-03a/BQ-02b sources were read.
PR #435 is the accepted synthetic consumer baseline. Main includes #453 and
#470; #470 is Human risk acceptance with known reconnect classifier limits,
not independent proof that this executor completed.

Exact ADD-only scope:

1. `src/ai_commerce/creative_quality/study_protocol.py`
2. `src/ai_commerce/creative_quality/human_attestation.py`
3. `src/ai_commerce/creative_quality/study_readiness.py`
4. `tests/test_bq_03b_human_study_readiness.py`
5. `work_items/mt01-components/bq-03b.json`
6. `work_items/BQ-03B_HUMAN_STUDY_READINESS_001.json`
7. `docs/engineering/BQ_03B_OFFLINE_HANDOFF.md`
8. `docs/creative/BQ_03B_HUMAN_RATER_CONSENT_ATTESTATION_PROTOCOL.md`

Three immutable canonical native records compose the original plan and exact
digest pins. A missing source has explicit NO_BOUND_SOURCE/HOLD. Public preview
is a fixed sanitized projection; private records carry plan/asset/task pins.
Sampling/power remains pending independent calculation and approval. No actual
participant identifiers or times enter these APIs. Reference identifiers are
request metadata only. All caller submissions are discarded without retention,
serialization, hashing, logging, echo or verification callbacks. Request records
cannot become admitted observations. Rejected metadata leaves fixed reason
codes, never raw contents. Consent, rights and verifier custody remain absent.

Only the three added source files and one added test belong to the new MT01
owner. The five existing consumer references are exercised through native
blind_trials, rater_receipts, calibration_stats, t3_assessment and
multimodal_review. Existing fixtures construct reference-only BQ-02b linkage;
the new test blocks file/media opening, network and subprocess during native
composition. No new required media inspection test is introduced. Existing Q2
P0, Q4 UNKNOWN and T3 NOT_RUN remain authoritative.

Registered ingress: `mt01-component-onboarding`. Unchanged MT01 V2 selects the
minimum owner/consumer closure. Windows self-host verification must observe
`MT01_XDIST_WORKERS_COLLECTED=12` separately for each selected group through
the unchanged controller hook; the `-n 12` flag is insufficient. Up to two
bounded Verify-Fix rounds; retained failures and counts belong in the WorkItem.
Round 1 selected bq-02b, bq-03a and bq-03b with
`ONBOARD_CONSUMER_OWNER_CLOSURE`, `fallback=false`, unknown ACs/tests/consumers
and missing mappings empty. Results: 249 + 330 + 268 = 847 passed; zero failed,
skipped or errors. Each group recorded actual workers=12 through the controller
collection hook. The original temporary artifact directory was unavailable
from the next command because command sandboxes use different temporary roots.
The observed round-1 summary remains in the WorkItem; original logs are not
reconstructed or claimed retained. This evidence retention issue is preserved.

Round 2 completed the same registered closure after adding category/container
and task/asset edge cases. All groups passed. Actual raw stdout, stderr,
per-group evidence, MT01 plan and results are retained inline as 11 artifacts
with SHA256 in `verification_rounds[1].artifacts` in the allowed WorkItem.
Each artifact digest was checked against its retained UTF-8 contents; all four
Python source/test hashes still match round 2. No third verify-fix round ran.

| Group | Collected inventory | Executed/passed in round 2 | Failed | Skipped | Errors | Actual workers |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| bq-02b | 249 | 249 | 0 | 0 | 0 | 12 |
| bq-03a | 330 | 330 | 0 | 0 | 0 | 12 |
| bq-03b | 298 | 298 | 0 | 0 | 0 | 12 |
| Total | 877 | 877 | 0 | 0 | 0 | 12 per group |

Registered execution used unchanged native `execute_plan` and
`pytest_observation_env`; actual worker counts come from exactly one
`MT01_XDIST_WORKERS_COLLECTED=12` in each group's retained stdout. No reuse;
all selected groups ran afresh. `fallback=false`, reason
`ONBOARD_CONSUMER_OWNER_CLOSURE`; unknown ACs/tests/consumers and missing
mappings empty. Round-2 wall times: 16.5553324s, 11.0171560s, 4.1587377s.
Environment: Windows-11-10.0.26300-SP0, Python 3.14.7;
RUNNER_ENVIRONMENT=self-hosted, GITHUB_ACTIONS=true, MT01_V21_ENABLED=1.
These are local observations, not a new GitHub Action or provider attestation.

The registered quiet stdout does not separately print item-collection totals.
The collected column is from one additional **read-only collection inventory**
over the same three test files and unchanged source/test hashes, with
`--collect-only -q`; it executed zero tests and is not a third Verify-Fix round,
registered execution proof or worker-count proof. Its 877 unique IDs, raw
stdout/stderr, counts and digests are retained in `collection_inventory`.

Exact registered commands, each executed with the native observer:

```text
python -m pytest tests/test_bq_02b_multimodal.py -q -n 12
python -m pytest tests/test_bq_03a_blind_calibration.py -q -n 12
python -m pytest tests/test_bq_03b_human_study_readiness.py -q -n 12
```

Final evidence/receipt metadata is written after the source-test run, so outer
EF01/EF02 must independently verify final bytes. Retained plan hashes describe
the actual pre-receipt-update candidate, not a claimed final Git HEAD. Source
and tests were not changed after round 2. Git HEAD remains the immutable source;
the exact worktree diff consists of the eight new untracked allowlisted files
and no tracked modifications. No Git index/ref/config writes were performed.

Local source status: OFFLINE_TESTS_PASS_EIGHT_ADD_ONLY_FILES.
Delivery/candidate status:
HOLD_PENDING_INDEPENDENT_CURRENT_PROVIDER_OUTCOME_AND_EF02_PROOF.
Current provider event completion, new batch/action identity and authenticated
EF02 proof are not available to this code executor and are not inferred from
green tests. The model config retains preferred codex/gpt-6.1-sol/xhigh with
hard_default=false and the existing antigravity/gemini-3.8-flash/high fallback;
actual provider/model/fallback proof remains external. Test-plan fallback=false
is a distinct observed fact.

Historical #438 r1 / Action `42043372885`: EXECUTION_UNKNOWN /
EXECUTOR_OUTCOME_UNCONFIRMED after reconnect, with no candidate or PR. Its
provider boundary remains unreconciled. Nothing here replays that effect,
relabels it as R2, or uses its tests as acceptance. Current run provenance and
provider events require independent EF01 and EF02 artifacts; executor text or
local tests alone cannot establish them. Ambiguous, fatal or incomplete current
provider events require HOLD, with negative evidence retained.

Publication belongs solely to the EF02 worker after independent verification.
This executor edits ordinary worktree files and performs no Git writes or PR
publication. Later exact candidate HEAD/base, hosted Windows CI, artifacts,
JUnit/oracle and independent Brain product review are separate. Only a genuine
Brain PASS with the canonical four-line Conversation comment and authenticated
COMMENT wake may trigger the existing trusted auto-merge. No automatic Brain
PASS, candidate publication, merge or new Human source gate is claimed here.

Status: NOT_READY_FOR_LIVE_STUDY / AUTHENTIC_HUMAN_DATA_NOT_AVAILABLE.
Zero actual participants, zero participant consents, zero rights attestations,
Human calibration NOT_RUN; no quality, ROI or viral result.
