# EF02-D5.3 scope reconciliation report

Date: 2026-10-30. Pinned source and unchanged executor HEAD: `9ff35b2936c45aeb43811bd4dabd9290ac6a7577` (merged PR #121).

Result: implementation and final affected regression **PASS**; full registered acceptance remains **UNPROVEN** in the executor's mandatory Git-guard environment. No candidate SHA/ref or merge is claimed.

## Attempt provenance and recovery decision

Original [Issue #123](https://github.com/trung-via/ai-commerce/issues/123), batch `ef02-d53-local-run-registration-20261030-v1`, attempt `ef02-d53-codex61solmax-20261030-v1`, [run 41002877562](https://github.com/trung-via/ai-commerce/actions/runs/41002877562), [artifact 12955013861](https://github.com/trung-via/ai-commerce/actions/runs/41002877562/artifacts/12955013861) ended `VERIFIED_SCOPE_REVIEW_REQUIRED`. Human-provided original Windows registered evidence is **2087 passed / 6 skipped, exit 0**. The failure was `OUT_OF_SCOPE_FILES_REQUIRE_RECONCILIATION` for this report and `tests/test_ef02_github_read_retry.py`, not a pytest failure. No original candidate SHA/ref exists and no `reuse_candidate` approval is asserted.

New [Issue #125](https://github.com/trung-via/ai-commerce/issues/125), [run 41005816276](https://github.com/trung-via/ai-commerce/actions/runs/41005816276), workflow attempt 1, batch `ef02-d53-scope-reconcile-20261030-v2`, action attempt `ef02-d53-codex61solmax-20261030-v2-scope`, `repair_of=ef02-d53-codex61solmax-20261030-v1`, keeps Codex / `gpt-6.1-sol` / `max`, registered `project-tests` and at most two EF01 repair rounds. Its immutable allowed paths cover all nine expected files. The original Issue JSON and new request were read through connected GitHub and the runner's current request; the original workflow, source, contract and related tests were inspected. Current runner `verification_plan.json` says `EXECUTE`, `FULL_REGISTERED_SUITE`, `registered_workers=12`, with the exact registered argv; this is a plan, not proof of post-executor PASS.

**Reconstruction, not verified recovery.** Before authoring, this new worktree was clean at the exact pinned source. Read-only inspection found the old `ai-commerce-ef02-EF02-D5-03-rsfv2jb1` worktree at the same base with exactly the nine expected changed/new paths, including all five untracked files. The old worktree was not modified. Connected GitHub confirmed the original result artifact name/run/source and archive SHA256 `58310cc413f2651ef592084368edd89bce11c383e39521cb9d4b2079b185a376`; original execute-job logs confirm upload and wrapper failure.

The connector returned an archive download reference, but this shell could not resolve its host (`getaddrinfo failed`). The runner's original `.local/ef02` evidence had already been replaced by the new batch; bounded read-only checks did not find a retained copy of the original manifest. Thus raw original `action_result`, `engineering_result`, registered `verify` and MT-01 JSON/per-file hashes were **not readable here**. Their bytes/hashes are UNKNOWN. No old source/test file was copied or patched into this attempt, and no old unpublished tree is trusted as a candidate. Implementation was reconstructed from the pinned canonical source, original AC1–AC5 and observed logs, as authorized by #125's fallback.

The retained old implementation report also records a separate old **executor-local** full-suite result (36 failed / 2042 passed / 15 skipped, exit 1) under its Git guard. That historical local result is distinct from the Human-provided old registered PASS above. Neither verifies this attempt.

## AC and scope evidence

| Criterion | Implementation / evidence |
| --- | --- |
| AC1 | Explicit local enrollment binds repository, issuer/actor, original full-batch fingerprint, Issue/source/batch/workflow/attempt and a closed finite window. Authenticated repository/workflow/Issue reads precede bounded complete-run discovery. Both immutable request archive and exact completion artifact/event must authenticate the selected run and action/attempt pairs. Duplicate matches, unfinished competitors, forged titles, malformed identities and incomplete lists fail closed. Explicit run IDs stay pinned. |
| AC2 | Private registration uses existing OS-locked, atomic `PrivateJsonState`, excludes operational/source checkouts, validates record shape/selectors/provenance and writes no credentials, grants, instructions or chat routes. Identical registration does not rewrite. Reopening reauthenticates only its saved run. Atomic failure and concurrency tests exercise integrity. |
| AC3 | Registration hands off into default D5.2 observe with `REGISTERED` / `CANDIDATE_READY_NOT_SENT`, without browser access. Explicit Windows deliver retains the environment/SID gate, independent D3.1 artifact authority, D3.2 grants, D2 idempotency and D4 guard. A single UNKNOWN receipt exits without retry. No sending/Brain authority is added. |
| AC4 | A shared injectable GET count/deadline/retry budget covers enrollment, polling and independent D3.1 reads before delivery. Only 429/5xx, timeout and header-evidenced rate-limit 403 retry. Server delays are honored or rejected as over budget. Permanent denial, identity/JSON/ZIP/digest and ambiguity failures do not retry; send/click stays outside this policy. |
| AC5 | Two focused test files cover authentication, wrong identities/attempts, duplicate and unfinished runs, display-name forgery, delayed upload, budget exhaustion, transient/permanent errors, private integrity, observe/no-send and explicit UNKNOWN handoff. Existing D5.2 tests are preserved. All feature transport/browser inputs are mocked. |

Exact changed-path boundary:

```text
docs/engineering/EF02_D5_3_IMPLEMENTATION_REPORT.md
docs/engineering/EF02_D5_LOCAL_RUN_TRIGGER.md
src/ai_commerce/remote/github_artifact_authority.py
src/ai_commerce/remote/github_read_retry.py
src/ai_commerce/remote/local_run_registration.py
src/ai_commerce/remote/local_wake_entry.py
src/ai_commerce/remote/local_wake_trigger.py
tests/test_ef02_d5_run_registration.py
tests/test_ef02_github_read_retry.py
```

No `.git`, `.github`, runner, secret/data paths, D2 store, signed-grant implementation, CDP guard or unrelated tests are edited. Git publication remains the EF02 worker's responsibility. No executor Git commit/ref/config writes, main merge, real browser session or ChatGPT send is authorized by this change.

## This attempt's verification

The initial targeted command actually ran on this worktree:

```text
python -m pytest tests/test_ef02_github_read_retry.py tests/test_ef02_d5_run_registration.py tests/test_ef02_d5_local_wake_trigger.py -q
146 passed in 1.22s
exit 0
```

A subsequent review tightened private intent type equality and added its regression. The affected D1–D5 command then returned **375 passed / 7 skipped in 11.86s, exit 0**. No test-result reuse from #123 is used. No delegation, provider replay or extra status gate is introduced.

The actual registered command ran in this isolated attempt with all Git guard variables retained. One unrelated inherited host credential was excluded from the test child environment; no credential values were printed and no guard setting was removed or overridden.

```text
python -m pytest tests -q -n 12
36 failed, 2050 passed, 15 skipped in 263.56s (0:04:23)
exit 1
EXACT_CONTENT_UNCHANGED True
AFTER_HEAD 9ff35b2936c45aeb43811bd4dabd9290ac6a7577
```

Thirty-five failures were unchanged temporary Git-fixture setup failures, including `git init` exit 128, `update aborted by the reference-transaction hook`, and `NtCreateDirectoryObject(...): 0xC0000022`. The other failure was `tests/test_ef01_performance.py::test_ef02_direct_git_admin_fetch_credentials_do_not_enter_child`, whose assertion requires `GIT_CONFIG_COUNT` to be absent. The mandatory executor guard requires that injected configuration. The failed test families and counts were:

| Unchanged test file | Failures |
| --- | ---: |
| tests/test_ef01.py | 13 |
| tests/test_ef01_git_write_guard.py | 6 |
| tests/test_ef01_ef02_integrity.py | 3 |
| tests/test_ef01_repair.py | 7 |
| tests/test_git_byte_consistency.py | 6 |
| tests/test_ef01_performance.py | 1 |

The decoded stdout SHA256 was `061d178107fe9a16be495c6317748992371de00c39e16eb12d0994a0645ba7af`; stderr was empty. The tool transcript retains the observed diagnostic tail and failure IDs. No D5/registration/retry test failed. These are this attempt's local results, not the old attempt's registered PASS and not a new EF01/EF02 acceptance result. No bypass or identical retry of the guard-blocked full suite was performed.

After that run, primary-source review of [GitHub's rate-limit guidance](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#handle-rate-limit-errors-appropriately) prompted one source correction: rate-limit responses without a usable server timing hint wait at least one minute, then require an increasing wait or a bounded stop. Two deterministic cases cover it. This final source/test change invalidates any full-suite reuse; EF01 must execute its registered post-provider verification on the final bytes.

Final byte review also detected CRLF in the three edited baseline modules (`github_artifact_authority.py`, `local_wake_entry.py`, `local_wake_trigger.py`). They were normalized to LF within this worktree; AST equality before/after confirmed unchanged semantics. The CLI test composition clock was pinned explicitly so a future CI date cannot expire its synthetic enrollment window. These final bytes require fresh EF01 verification, not reuse of any earlier full-suite checkpoint.

One affected repeat on these final sources returned **1 failed / 376 passed / 7 skipped in 12.94s**, exit 1: unchanged `tests/test_ef02_wake_d2.py::test_concurrent_registry_writers_preserve_routes_and_claims` raised `OriginError: REGISTRY_UNAVAILABLE` from native private-state handling. It had passed preceding runs. A single isolated diagnostic, forwarding native handle/replace operations unchanged and observing error codes without printing private contents, returned **1 passed in 0.16s**; it did not reproduce the underlying OS error. The exact native root cause remains UNKNOWN. No D2/private-state source or existing test was modified. The subsequent full affected rerun below passed; the intermittent result is retained as a limitation, not erased or described as a proven fix.

Final affected regression on the corrected, LF-normalized source and clock-pinned tests:

```text
python -m pytest tests/test_ef02_github_read_retry.py tests/test_ef02_d5_run_registration.py tests/test_ef02_d5_local_wake_trigger.py tests/test_ef02_d5_local_wake_entry.py tests/test_ef02_d3_github_artifact_authority.py tests/test_ef02_wake_d1.py tests/test_ef02_wake_d2.py tests/test_ef02_d3_signed_origin_consent.py tests/test_ef02_d3_two_grant_delivery.py tests/test_ef02_cdp_doorbell.py -q
377 passed, 7 skipped in 12.44s
exit 0
```

The seven affected-suite skips are five native hardlink operations denied (Windows error 5) and two native symlink operations unavailable (1314). They are not native-link PASS evidence. `git diff --check`, exact changed-path/allowed-path reconciliation, syntax parsing and imports from this worktree passed. All nine changed files now use LF bytes. Git emits the existing checkout CRLF-conversion warning for baseline Python paths; no Git attributes/configuration were changed and actual candidate byte-integrity acceptance remains the worker's responsibility. Source/test bytes remain fixed after this final affected run; only this factual report is completed afterward. EF01/EF02 must independently verify the final complete tree, including this report, before publication.

AC verdicts: **AC1–AC4 SATISFIED by the bounded implementation and affected deterministic evidence; AC5 PARTIAL / full registered post-executor verification UNKNOWN**. Ubuntu execution, live browser delivery and Brain continuation are not claimed. Candidate ref/SHA remain **UNKNOWN / not published by this executor**; only the EF02 worker may publish after registered PASS, accepted scope and byte-integrity checks. Human merge approval remains separate.

## Operational limitations

Original-request fingerprints and retained request/result artifacts are prerequisites. Current REST metadata alone cannot prove an original edited Issue request, absent future triggers or indexing completeness. Unfinished competitors, missing snapshots, stale windows and bounded exhaustion stop observation safely. Private-directory ACL provisioning remains local operator responsibility. Ubuntu execution and real Windows CDP delivery are not established by mocked tests. `LIVE_WAKE_NOT_PROVEN`; authenticated receipt and Brain continuation remain UNPROVEN. Human reviews merge separately.
