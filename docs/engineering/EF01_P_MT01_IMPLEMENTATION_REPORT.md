# EF01-P / MT-01 bounded recovery — 2026-10-29

Status: implementation recovered and updated; targeted and full registered local verification passed. A supplemental local Windows check observed two simultaneous twelve-worker process sets. Authoritative EF02 verification and candidate publication remain pending the enclosing worker; protected registry activation requires a separate change. No overall runtime acceptance or publication PASS is claimed here.

## Parent failure and recovery provenance

The source base is `13c24705e32820e80fe0b9b7c91713b61bee6c77`. Parent Issue #81 / run `40840406005`, artifact `ef02-results-40840406005-1` (ID `12887486123`), attempt `ef01p-mt01-codex61sol-max-20261029-01`, returned executor exit 0 but **SCOPE_VIOLATION / CHANGES_OUTSIDE_ALLOWED_SCOPE**. Its EF01 verification exit was NULL, zero authoritative tests were verified, and it produced no candidate ref. Executor exit 0 and the old executor's local test logs do not establish a verified parent candidate.

The original error was editing five files outside that action's allowlist: `src/ai_commerce/remote/__main__.py`, `src/ai_commerce/remote/scheduler.py`, `tests/test_ef01_performance.py`, `tests/test_ef02_m0r_fault_injection.py`, and `tests/test_minimum_test.py`. The recovery request explicitly permits these paths. The runtime scope guard was preserved.

GitHub's connector confirmed the exact artifact's run/source identity and digest `09a32ce45e09f0dd43909c6788ff011da15a526b369daf0f7990f624a789a539`. The connector returned a ZIP reference, but the local download failed with DNS resolution blocked in the sandbox. ZIP contents and its `engineering_result.json` candidate hashes could therefore not be independently read. **Matching the artifact's candidate hash manifest remains UNPROVEN.** No network or sandbox workaround was used.

The prior worktree `C:\Users\TRUNG\AppData\Local\Temp\ai-commerce-ef02-EF01P-MT01-IMPLEMENT-nccjlvdy` was read only. Both worktrees had the exact pinned HEAD. All nineteen prior changed paths were checked against the current immutable recovery request's allowlist. Sixteen code/test/config files matched the prior local manifest, whose SHA256 is `a2a89e9fcebc490a48779fb7f94abd8b4b676d74fff1668135de6080536c643b`. Those checked bytes were recovered; old test PASS claims were not imported. The prior tracked diff is exactly **52,693 bytes**, SHA256 `7ecb39bdc8d4d2b44ef389b473482c087415661dd1768e9adaeb3a4ca93ff5e2`, matching the reported size. This is corroborating local provenance, not a claim that the inaccessible ZIP bytes were compared.

Recovery records live under ignored `.local/recovery-evidence/`: `provenance.json` contains each recovered SHA256; `prior-worktree-tracked.diff.patch` preserves the observed prior diff. The protected verification registry and RemoteActionBatch v1 schema matched both prior/current source and base:

| Protected file | SHA256 |
| --- | --- |
| `.github/ef02/verify-profiles.json` | `38563e7cdb028ed47f323332b61d5ead94671244ed014d87c62fc8a15ee4d60c` |
| `contracts/RemoteActionBatch.v1.schema.json` | `898fca43dd675089545bf984dca8e6de02532ce085175ff5f2e9eddf760af6c1` |

## Recovered versus reconstructed files

| Treatment | Files |
| --- | --- |
| Recovered unchanged | `engineering/git.py`; `remote/__main__.py`; `remote/scheduler.py`; `tests/test_ef02.py`; `tests/test_ef02_m0r_fault_injection.py` |
| Recovered and revised for this attempt | `pyproject.toml`; `engineering/cli.py`; `engineering/runner.py`; `engineering/minimum_test.py`; `engineering/pytest_profile.py`; `engineering/verification.py`; `remote/validation.py`; `remote/worker.py`; `tests/test_ef01_repair.py`; `tests/test_ef01_performance.py`; `tests/test_minimum_test.py` |
| Reconstructed against current source and final Human decision | This report; `docs/governance/HUMAN_DECISION_2026-10-29_MINIMUM_TEST.md`; `work_items/EF01-DIRECT-GIT-PYTEST-003.json` |

Module paths in this table are under `src/ai_commerce/`. No A03–A09 implementation, protected `.github/` file, provider adapter/configuration or existing schema was edited. The old worktree was not edited. No Git refs, commits or pushes were created by this executor; enclosing EF02 owns candidate publication.

## Implemented behavior

- MT-01 uses finite, reviewed test groups with explicit AC/path/test/consumer/contract mappings, expanded through absolute/relative Python imports. It selects affected and required groups deterministically. This is minimal at declared group granularity. Incomplete maps, unknown ACs/paths, unresolved dynamic imports or missing tests require the approved broad profile or UNPROVEN; they never create an empty PASS.
- Evidence records explain `EXECUTE`, `REUSE`, `INVALIDATED` with reexecution, and `UNPROVEN`. Immutable canonical payloads bind source base, relevant base/candidate hashes including additions/deletions, test definitions, actual argv, interpreter hash, Python/platform, installed dependency versions, hashed environment values, AC/consumer mapping and artifact digests. Reuse is attempt-local and requires observed exit zero with nonempty successful pytest output and unchanged artifact bytes. Unknown interpreter environments cannot establish reuse.
- EF01-R remains the existing bounded same-action repair loop. It keeps executor/model/effort, worktree, original HEAD, claims and shared deadlines. Failed or affected groups rerun after repair; independent valid PASS evidence remains reusable. Permission/quota/unknown outcomes, timeouts, pytest internal/no-tests, worker crashes and observed severe resource/launch errors never authorize provider replay. Each invocation retains immutable source/diff/diagnostic evidence.
- Dependent EF02 fan-in uses the composed effective source and fresh aggregate registered consumer/integration coverage. It never promotes sibling PASS into aggregate PASS. Before publication, the worker validates the aggregate sources, source base, exact registered argv, actual current file manifest, executed group state, immutable evidence payload digest and referenced artifacts. Missing, reused or tampered aggregate proof is UNPROVEN.
- EF02 passes its trusted registry snapshot to MT-01. When all affected groups have exact registered pytest commands, the selector uses that minimal set verbatim, including each command's registered worker count. It accepts no extra filtering, shell tokens or reduced-worker substitutes. Missing registrations use the registered broad profile; aggregate verification remains fresh broad consumer coverage. The registration name/argv participates in evidence binding, so relevant registry edits invalidate reuse. Per-group argv and worker observations remain visible even when serial and parallel registrations coexist.
- RemoteActionBatch v1 requests, source/claim fingerprints, candidate integrity reconciliation, Windows Codex MXC and provider selection remain in place. Legacy approved reuse lacks MT-01 environment/test/dependency bindings; it remains readable but cannot establish a new PASS and returns UNPROVEN without a provider call.
- Direct Git is optional via `--git-bin` or `AI_COMMERCE_GIT_EXECUTABLE`; default is `git` on PATH. Explicit executables use argv without shell interpretation. Missing paths and script/shell wrappers fail clearly. EF01 observations and EF02 source/fetch/worktree administration use the shared builder; administrative credentials remain out of provider child environments.

## Final Human worker decision

[PR #82](https://github.com/trung-via/ai-commerce/pull/82), read at head `a598a9b165f557bbaf797cda1e90745666c9d1c9`, confirms **12 pytest workers for EACH eligible action**. It supersedes 12+2. Human's prior comparison of 2/4/8/12/16 and confidence in host capacity for 24 workers are recorded as **HUMAN_REPORTED**. No new benchmark, performance gate or host capacity gate was introduced.

Local eligible pytest verification defaults to `-n 12`, including every repair round. CPU/RAM observations are diagnostics and never divide the count by action slots. Fixture approval and unknown host capacity cannot reduce it. There is no primary/secondary allocator or host-wide lease. `EF02 max_parallel=2` remains the scheduler default; this recovery batch itself explicitly requested one action and does not demonstrate two live EF02 actions.

`dev` and `parallel-tests` declare `pytest-xdist>=3.6,<4`. The permitted Python environment already has pytest 8.4.2, pytest-xdist 3.8.0 and execnet 2.1.2; no package installation was needed. Runtime records requested workers, configured workers, actual workers when observable, argv, exit and degradation reason. Missing/unloadable xdist permits a logged local serial fallback; registered parallel profiles STOP if the plugin cannot run. Worker/resource/fixture failures STOP rather than silently selecting 2/4/8. Local parallel temp directories are attempt/group scoped. No new fixture approval gate is required.

**Protected registry gap:** The pinned registry currently contains only serial commands. Authoritative EF02 verification preserves them exactly. A serial registration is explicitly recorded as `EF02_REGISTERED_SERIAL_ARGV_COMPANION_REGISTRATION_REQUIRED`, not treated as a resource fallback or 12-worker proof. The runtime accepts a separately registered exact `-n 12` suffix and tests both actions using it. Activating registered parallel execution or narrower EF02 selection requires the separately authorized protected registry/host change. This recovery does not silently add flags, edit `.github/`, or claim that activation occurred.

## Verification

| Verification | Actual result |
| --- | --- |
| Initial targeted live Windows pytest | 208 passed, exit 0; `-n 12 -vv`; log reports `created: 12/12 workers` and `12 workers [208 items]` |
| Reviewed targeted live Windows pytest before final registry plumbing | 215 passed, exit 0; `-n 12 -vv`; log reports twelve launched workers; includes MT-01, Direct Git, EF01-R, EF02 fan-in and M0-R faults |
| Final registry-selection targeted Windows pytest | 220 passed, exit 0; `-n 12 -vv`; exact minimal registered-group selection, registry invalidation and runtime command dispatch are covered |
| Two-action deterministic fake runner | Both registered subprocess argv arrays contain `-n 12`, overlap behind a barrier, and scheduler defaults to two actions; simulated proof only |
| Repair deterministic fake runner | Both verification rounds retain `-n 12`, executor/model/effort and one bounded repair; simulated proof only |
| Registered `python -m pytest tests -q` | 999 passed, 3 skipped in 767.97s (0:12:47); exit 0; before/after source manifests match |
| Live two-action / 24-worker execution | Supplemental local Windows proof: both argv request `-n 12`; both launch twelve workers, pass their targeted tests, and a simultaneous process snapshot observes twelve child workers for each controller. EF02 activation remains pending |

The reviewed targeted argv was:

```text
python -m pytest tests/test_minimum_test.py tests/test_ef01_performance.py tests/test_ef01_repair.py tests/test_ef02.py tests/test_ef02_m0r_fault_injection.py -q -n 12 -vv --basetemp .local/recovery-evidence/review-temp
```

Logs, exits and source manifests are retained under `.local/recovery-evidence/`. The registered full command is run independently of MT-01 selection/reuse. Its source manifest excludes only this report, the WorkItem and this attempt's decision-document updates so post-run reporting cannot circularly change the code/test receipt. Final source stability, full result and receipt hashes are recorded below. A serial registered PASS cannot establish twelve-worker execution.

## Acceptance and remaining gaps

| Acceptance | Evidence / status |
| --- | --- |
| Deterministic sufficient selection | Targeted tests cover explicit ACs, transitive imports/consumers/contracts, narrow groups, missing-map broad fallback and UNPROVEN |
| Strict reuse and invalidation | Targeted tests cover source/test/fixture/import/contract/argv/base/mapping/dependency/environment/artifact changes and unchanged independent evidence |
| Bounded repair preserving valid PASS | Local fake-provider integration covers real pytest fail→repair→pass, preserved group evidence, unchanged executor profile and no unknown-outcome replay |
| Combined-source fan-in | Local Git/observation/API fakes cover dependency composition, fresh registered execution and rejection of stale/tampered source/file/argv/artifact proof |
| Direct Git | Targeted Windows tests exercise safe argv, five snapshot operations, real read-only Git comparison, bad path/wrapper rejection and credential separation |
| Twelve per eligible action | Live scoped twelve-worker PASS plus deterministic two-action and repair argv tests; no CPU/RAM/slot reductions |
| Authoritative EF02 parallel/narrow activation | Pending separate protected registry/host change; current registered serial verification is retained |
| Full project regression / A03–A09 consumers | Registered local verification passed: 999 passed, 3 skipped in 767.97s (0:12:47); exit 0 |
| Parent artifact candidate-hash identity | UNPROVEN because ZIP download is inaccessible; read-only local manifest/diff corroboration documented above |
| Candidate publication, external verifier, runtime rollout | Pending enclosing EF02/external execution; no publication, service change, auto-merge, pilot or release claim |

The bounded static map cannot prove arbitrary reflection, undeclared external behavior or unavailable AC coverage. Such uncertainty expands to the approved full profile or remains UNPROVEN. Cross-action/run and legacy evidence is not silently reused. These are explicit limits of the current proof, not verified deployment claims.

## Observed failures retained during this recovery

The first supplemental two-action run launched twelve workers in each action and observed all twenty-four processes simultaneously, but both runs exited **1** with **214 passed / 1 failed**. The failing test was `test_real_pytest_group_pass_reused_after_other_group_repair`: its helper imposed a shared ten-second budget on two real nested pytest startups. Under concurrent load, the second startup exhausted that test-fixture budget. This was not evidence of insufficient host RAM, a worker launch failure, a product-code PASS, or a reason to reduce workers. The fixture's ordinary real-execution budget was corrected to sixty seconds (ninety-second outer deadline); deterministic timeout tests still inject their exception separately. Production action/verification budgets and the twelve-each policy were unchanged.

Those original failures, argv, exit codes and process snapshot are retained under `.local/recovery-evidence/pre-fixture-fix/`. A subsequent twelve-plus-twelve check passed 215 tests per action and observed twenty-four simultaneous workers; it is retained under `post-fixture-pre-registry/`. These earlier receipts do not establish a final candidate PASS after later source changes.

The first registry-plumbing review returned **196 passed / 23 failed, exit 1** because local test fixtures use an absolute Python interpreter path in one trusted profile. The new internal snapshot validation initially rejected it before verification. Validation was aligned with EF01's existing Python/pytest executable boundary; the external EF02 registry's existing `python`/`py` restriction was preserved. A fresh targeted run then passed **220**, exit 0. The failed intermediate run remains in `registry.stdout.log` / `registry.stderr.log`; no candidate was published from it.

Final full and concurrent receipts below supersede the earlier local source versions. The earlier registered run's 994-pass result remains archived; final verification is rerun after all code and test changes.

## Final local receipts and worker overlap

The exact registered command `python -m pytest tests -q` exited **0**: **999 passed, 3 skipped in 767.97s (0:12:47)**. It ran from `2026-10-29T10:55:54.739361+00:00` to `2026-10-29T11:08:43.951117+00:00`. All 242 non-reporting source files have identical before/after hashes; the same manifest also matches after supplemental parallel verification. The three skipped Windows link-creation checks remain skipped; no permission or sandbox setting was changed. No failed test or worker crash was observed in these final runs.

The supplemental local check used the unchanged scheduler default of **two** concurrent actions. Each launched the reviewed targeted argv above with `-n 12 -vv` and a separate absolute basetemp (`live24-a-temp` / `live24-b-temp`). Both stdout logs contain `created: 12/12 workers`. A normal read-only Windows process snapshot at **`2026-10-29T10:54:27.027775+00:00`** observed these live child processes at the same instant:

| Local action | Controller PID | Simultaneous worker PIDs | Exit / test result |
| --- | --- | --- | --- |
| `live24-a` | `6676` | `21528, 15252, 19656, 15684, 17640, 2248, 9020, 17604, 15712, 22356, 20968, 13980` | 0 / ======================= 220 passed in 76.09s (0:01:16) ======================== |
| `live24-b` | `20996` | `12440, 9444, 21104, 5332, 16736, 4212, 15960, 17844, 7944, 22520, 8472, 19056` | 0 / ======================= 220 passed in 70.28s (0:01:10) ======================== |

This establishes **24 simultaneous local pytest worker processes** on this candidate, in addition to the deterministic fake-runner policy tests. It does not establish two live EF02-dispatched actions or change the protected registered profile. It is a functional concurrency check, with no speedup assertion or new benchmark gate. The receipt stores exact argv, controller/worker PIDs, UTC spans, exits, summaries and stdout hashes.

| Local artifact under `.local/recovery-evidence/` | SHA256 |
| --- | --- |
| `provenance.json` | `ca2386ea3f78c2b1334cfc2ccc7ded434b777e4425b8e6421a8fe38c0f852681` |
| `candidate.before.json` | `671108953505a5f739eba453e9f7264cb9fb59dcac4fd1a2c1162750f6811904` |
| `candidate.after.json` | `671108953505a5f739eba453e9f7264cb9fb59dcac4fd1a2c1162750f6811904` |
| `project-tests.stdout.log` | `91203f13d6772c7e1d305fccbb152f7018cbe11243a9a2103368e1528680c854` |
| `project-tests.stderr.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `project-tests.receipt.json` | `fe669acd527362c66b397cead61ee384dcf97e7a148eddc590b5b0f6b6e8b840` |
| `review.stdout.log` | `a9bf8c28218d737d9af3a23c277e2f55186f23cb54d1c9bfff0c4530c8366273` |
| `review.stderr.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `final-focused.stdout.log` | `e843917514bff9cc0f58213b03825b014a7bd108f54d8d6693f076d0446da3f7` |
| `final-focused.stderr.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `live24.receipt.json` | `76798ec2bc93ce9600dc8059eeea94451c15a2692cdcea235573fca606e53c8e` |
| `live24-a.stdout.log` | `e2e18269c27dfedbe67a9693868228e9221e6a07abba17472fbffa4abc0fabf5` |
| `live24-a.stderr.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `live24-b.stdout.log` | `23e1d3f8f021437f8c95600e07847867d66eaed38e8bf01bc1fcb318af345fda` |
| `live24-b.stderr.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

These are local receipts; their automatic upload by EF02 is not assumed. The report preserves their digests and the observed overlap. Parent EF01 verification remains NULL; this attempt's enclosing EF02 verifier and candidate publication have not yet returned. Remaining gaps are the inaccessible parent artifact candidate-hash comparison, protected registry activation for authoritative parallel/narrow EF02 commands, and external candidate/rollout evidence.

Final scope check: **19 changed files, all within the explicit allowlist; `git diff --check` passed**. HEAD remains the pinned base. Protected registry/schema hashes are unchanged. Only post-run reporting metadata was updated after the verified code/test manifest was frozen.
