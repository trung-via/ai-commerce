# HostOps Personal — direct route in AI Commerce

**Decision:** Human on 2026-11-04 selected single-account personal use within `trung-via/ai-commerce`. The original one-runner Personal implementation was demonstrated live. Human subsequently approved a **three-instance topology** (two EF02 + one dedicated HostOps), without CPU/RAM limits; see [staged rollout](THREE_RUNNER_ROLLOUT_20261104.md). On 2026-11-04 the Human reported both added runner instances Online. The dedicated HostOps routing was approved for a subsequent PR, with an independent READ smoke required after merge. EF02 remains on the `ai-commerce-ef02` label; no CPU/RAM limits are configured. No new Windows account or private broker repository is needed.

## One existing pipeline

`ChatGPT → public AI Commerce Issue → existing Windows self-hosted runner (ai-commerce-ef02) → result comment on the same Issue → ChatGPT`.

The trusted `.github/workflows/issue-ingress.yml` is the only issue ingress. It contains **Personal HostOps** and the existing **EF02** path. On 2026-11-04, the obsolete v1/v2/v3 HostOps caller jobs and their five hosted/reusable workflows were **retired from active GitHub Actions** (see [retirement record](HOSTOPS_LEGACY_RETIREMENT_20261104.md)). Historical source, contracts, tests and signed receipts remain available for audit, but have no active GitHub Issue route.

**READ:** On 2026-11-05 the Human granted standing approval for safe operational READ diagnostics. No recurring Human approval is required for each safe READ. An authenticated GitHub connector creates an owner-authored Issue titled `[HOSTOPS READ] <purpose>` with an exact JSON body; its opening triggers one Windows operation. READ operations support `inventory`, `runner_runtime_inventory` (strictly three predefined AI Commerce runner names, Task Scheduler state, hidden-launcher presence and listener process/session counts, plus total and unidentified listener count), `cdp_9333_status` (fixed loopback TCP owner/session and DevTools version metadata, no chat access, no Wake), `git_snapshot` (HEAD, branch, count of tracked changes only), `tracked_paths` (up to 50 public git-tracked paths), `read_text` (max 8 KiB, committed and unchanged public source/docs), and `file_digest` (SHA-256/size only for approved scratch/local-note files). Untracked names, changed file content, secrets and credential files are not sent to a public Issue. Standing consent does not authorize public disclosure of private data or any WRITE.

**WRITE:** ChatGPT creates a public Issue titled `[HOSTOPS WRITE] <purpose>` containing the exact target and non-sensitive text. Merely opening it NEVER executes the write. The Human opens this Issue in the ordinary GitHub UI and types **exactly** `APPROVE WRITE #N` (N is the Issue number) as a new comment. The dedicated workflow admits this one owner-comment event, then creates **one new** `hostops-personal-*.txt|md|json` file, <=2048 UTF-8 bytes, in either the existing scratch root `C:\TOOL\AI-Commerce-HostOps-Approved` (`root_id=scratch`) or the existing ignored worktree notes directory `C:\TOOL\ai-commerce\.local` (`root_id=workspace_notes`). A missing directory produces HOLD; no directory auto-creation, overwrite, source edit or arbitrary command. No overwrite, delete or arbitrary PowerShell. Success returns SHA256/size; ambiguous effect returns HOLD and must be checked on disk before attempting a different request.

**The owner GitHub account can also be used by ChatGPT's connector:** this confirmation is a simple operational workflow, not cryptographically independent Human authentication. ChatGPT must **never create its own WRITE approval comment**. If strong separation becomes necessary later, a separate non-connector approval factor would be needed; do not misrepresent owner comments as such.

## Exact request examples

Read basic Windows inventory, after Human chat approval:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"inventory","parameters":{}}
```

Read a public tracked document on the laptop:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"read_text","parameters":{"root_id":"workspace","relative_path":"README.md"}}
```

Request a new scratch proof; the Human alone posts `APPROVE WRITE #N` on that Issue:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"create_text","parameters":{"root_id":"scratch","relative_path":"hostops-personal-proof.txt","content_utf8":"AI_COMMERCE_PERSONAL_HOSTOPS_PROOF_V1"}}
```


Read bounded Windows runner runtime inventory directly through the dedicated HostOps
runner, after Human chat authorization (no second Human approval):

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"runner_runtime_inventory","parameters":{}}
```

This invokes a **fixed, source-owned read-only PowerShell CIM/Task Scheduler
probe** for exactly `AI-COMMERCE-EF02-01`, `AI-COMMERCE-EF02-02` and
`AI-COMMERCE-HOSTOPS-01`. The public Issue result contains only fixed
names, `task_state`, `task_interactive`, `hidden_launcher_configured`,
`hidden_launcher_process_count`, `listener_process_count`,
`listener_session_ids`, and total/unmatched runner listener counts.
It omits private executable paths, account identities, raw commands,
environment variables, tokens and identities of unrelated runners.

`TASK=Running` alone is not proof that `Runner.Listener.exe` is alive;
the two states are independently observed. A count of unmatched listeners
means other or unreadable runner processes, **not** a known named runner.
This probe does not confirm GitHub API online state or successful EF02
model execution. No service, task or process is stopped/restarted/modified.

### Chrome CDP 9333 — read-only HostOps bridge

The owner may create a single `[HOSTOPS READ]` Issue with:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"cdp_9333_status","parameters":{}}
```

This executes one **fixed source-owned** Windows read under the dedicated
interactive `AI-COMMERCE-HOSTOPS-01` runner, not a generic remote
PowerShell API. It checks the listener for exact `127.0.0.1:9333`,
unexpected nonloopback binds, whether the owning process is `chrome.exe`
in the calling Windows session, and reads only the local
`/json/version` DevTools metadata (no proxy, no redirects). Public output
contains only a fixed port, counts and status booleans. It never includes
Chrome command lines, profile paths, PID, cookie, bearer credential, chat
URL, tab list, editor contents, message, or WebSocket debugger URL.

`infrastructure_ready=true` means **local CDP foundation only**:
it does **not** prove signed Chrome acquisition, enrolled exact ChatGPT
conversation, composer idle, private D2 state, fresh completion event,
D3 grants, Brain continuation or Send permission. It does not start/restart
Chrome, switch profiles, create a tab, navigate ChatGPT, send a Wake,
change the D2 ledger, dispatch EF02, or attempt to fix the old #179
`UNKNOWN/UI_USER_TURN_OBSERVED` record.

On CDP absent/nonloopback/foreign process/wrong session/invalid version,
the response remains an observed diagnostic with
`infrastructure_ready=false`. The next action is a **separately
authorized interactive D5 readiness flow** per #220 and the existing
guarded local operator contract, not an automatic retry.

## Expanded V2 requests (same schema, same approval)

Git worktree snapshot — READ through ChatGPT, no second Human action:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"git_snapshot","parameters":{}}
```

List up to 50 tracked source paths:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"tracked_paths","parameters":{"prefix":"src/ai_commerce/hostops_v3/"}}
```

Read only public committed source with no uncommitted edits:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"read_text","parameters":{"root_id":"workspace","relative_path":"src/ai_commerce/hostops_v3/personal.py"}}
```

Independently check a prior created file's SHA-256 without publishing its text:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"file_digest","parameters":{"root_id":"scratch","relative_path":"hostops-personal-smoke-20261104.txt"}}
```

Create a worktree-local, ignored handoff note (Human posts `APPROVE WRITE #N` once):

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"create_text","parameters":{"root_id":"workspace_notes","relative_path":"hostops-personal-handoff.md","content_utf8":"Approved handoff note"}}
```

These fixed operations are not general PowerShell, arbitrary shell, repo source edit or a PULSE production runtime. The existing EF02/PR pipeline remains the approved route for editing tracked code. READ requests still produce public Issue receipts, so only request public source and nonsensitive machine metadata.

## Attended Git main sync (fast-forward only)

HostOps also supports the fixed WRITE operation `git_sync_main` in the **existing**
`C:\TOOL\ai-commerce` worktree. The Human's **one** exact GitHub Issue comment
`APPROVE WRITE #N` is required for each request. Never approve by ChatGPT
connector, reuse an earlier approval, or let Issue creation update the laptop.

Before creating the Issue, ChatGPT should read the current laptop `git_snapshot`
receipt and the live GitHub `main` SHA. It pins both values in the immutable
Issue body (the user need not type SHA values):

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"git_sync_main","parameters":{"expected_local_sha":"<40-lowercase-hex-from-local-git-snapshot>","target_sha":"<40-lowercase-hex-from-GitHub-main>"}}
```

Once the Human comments on that new `[HOSTOPS WRITE]` Issue, the Windows runner:
1. Verifies the fixed, non-reparse repo path, attached `main` branch, recognized
   `origin` URL, exact old HEAD, and clean tracked/untracked (excluding ignored)
   worktree.
2. Runs a fixed `git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main`
   with no shell expansion, noninteractive Git and a bounded subprocess timeout.
3. Rechecks HEAD, branch and clean status; insists fetched remote HEAD matches
   the Issue's pinned `target_sha`, and `expected_local_sha` is an ancestor.
4. Uses only `git -c core.hooksPath=NUL merge --ff-only --no-edit refs/remotes/origin/main`
   (or reports `ALREADY_AT_TARGET`), then independently reads back final HEAD and
   worktree status. The receipt contains only before/after/target SHA, branch and
   `fast_forward_only=true`.

Disallowed: `git reset`, `pull --rebase`, stash, force push, code execution from
user-supplied command strings, arbitrary path, switching branch, overwriting local
changes, and automatic replay of an UNKNOWN outcome. If local untracked files,
diverging commits, origin mismatch, or a newer remote target appear, the request
is **HOLD** and no merge is attempted. A network/fetch failure may change Git
remote-tracking metadata but must not be represented as successful worktree sync.
The Human-authorized write has the same GitHub-owner-comment limitation as
`create_text`; the owner identity alone is not an independent signature.

After successful WRITE, use **a new ChatGPT-approved** `git_snapshot` READ Issue
to prove the *actual laptop* `main` HEAD equals the requested target. A GitHub
Action success without the result SHA is insufficient.

## Operational acceptance

1. Merge the reviewed PR with exact-head Windows CI evidence and independent Brain source acceptance.
2. Create a **new** `[HOSTOPS READ]` Issue through ChatGPT. Verify the exact repo Issue receipt and actual `ai-commerce-ef02` self-host Windows job completion. This demonstrates only the allowed read.
3. For a separate new scratch write, create a `[HOSTOPS WRITE]` Issue. Await the Human's one-line comment, then verify the receipt and a separate READ `file_digest` Issue on the Windows runner to compare the recorded SHA. No automatic retry of ambiguous writes.

Old private W1.3 Issues #6/#7 and signed requests remain **CONSUMED / NO_RETRY**. Public Issue bodies/comments expose all request metadata and any returned text: never ask this mode to read secrets, cookies, credentials, personal files or other sensitive data. Running public-repository self-host workflows under the same Windows account does not isolate its private tokens, so keep unrelated private provider credentials outside this route.

## Status claims

`SOURCE_MERGED`, `RUNNER_JOB_OBSERVED`, `READ_RETURNED` and `WRITE_DISK_VERIFIED` are separate. Do not claim a live laptop operation based on CI, source code or an Issue created by ChatGPT alone.

## Standing READ — merged public transport and privacy-limited metadata extension

The parameterless `standing_read_diagnostics` READ has a source-owned
16-category snapshot. Each row has only `category` and an allowlisted `code`;
the Python producer and the protected JavaScript sanitizer independently
validate exact order/types, canonical JSON, issue/source binding and at most
8192 bytes before publishing to a public Issue. The issue cannot choose a
path, profile, port, command, task, process, private file, or extra field.

**Proven source and live READ baseline:** PR #379 merged into
`main@f20ef7409fdbb4732b547657d09cb2d4a7d766b8`.
Windows hosted MT01 run #41943714455 returned 386 passed and
`PASS_BASELINE_CLOSURE`. A new owner-authored READ Issue #381 returned
`OBSERVED` for all 16 sanitized categories on dedicated
`AI-COMMERCE-HOSTOPS-01` in run #41944315328; no recurring Human approval
was required for that safe READ. Its observed Chrome CDP 9333 listener was
`ABSENT`. Historical failed #368/#370 attempts are not replayed.

**Current proposed extension (not yet deployed):** add local-only D5
metadata classification. Only three literal config candidates under
`%LOCALAPPDATA%/AI-Commerce` are permitted, matching the bounded candidate
list in D5 attended source; optional manifest-supplied arbitrary config paths
are *not followed*. The fixed `wake-readiness.json` manifest is checked
by file metadata only, never read. If exactly one known config candidate is
present, the existing `chrome_acquisition.load_environment` validator reads
at most 8192 local bytes and checks version, exact schema, dedicated profile,
local allowed parent and CDP endpoint; the request/Issue sees only one of
the fixed enums `KNOWN_CONFIG_EXACT_9333`, `KNOWN_CONFIG_OTHER_PORT`,
`KNOWN_CONFIG_NOT_VERIFIED`, `KNOWN_CANDIDATES_AMBIGUOUS`,
`KNOWN_CANDIDATES_ABSENT` or `UNAVAILABLE`. It never posts the config,
Chrome executable/profile directory, raw errors or environment variables.

The profile status reports only whether that validator observed a dedicated
directory; lock-marker checks use metadata (stat) for four fixed names only,
never reading marker bytes or mutating them. A lock marker is **not** proof of
a running browser. The D5 manifest existence is **not** proof of readiness.
These statuses are not Chrome signature verification, successful authenticated
exact ChatGPT tab, CDP 9333 connected, effective D5 authority, credential
custody, or an authorization to launch/send. Unknown or unsafe/reparse paths
fail closed to `UNAVAILABLE` or `UNVERIFIED`.

After the protected source vocabulary and workflow sanitizer change passes
exact HEAD/base Windows CI and independent Brain review, merge through the
trusted PASS mechanism. Then issue **one new** fixed owner READ and verify
real runner codes before claiming the metadata extension live. No raw private
bytes are ever sent to GitHub; arbitrary private reading and all HostOps
WRITE/Chrome launch/navigation/Wake actions remain separately gated by Human
authorization and existing trust boundaries.

### Historical #372 recovery notes (preserved)

This recovery is new source authorship: `ARTIFACT_REUSE_UNAVAILABLE`. The normal
repository tool returned #370 artifact 13374903964 from run 41936937393 attempt
1 with the expected archive digest, but its download host could not be resolved
in this executor. Archive and patch bytes could not be verified or reapplied.
#370 remains `EXECUTION_UNKNOWN`; neither its attempt nor its earlier tests are
represented as a verified candidate, and no provider effect is replayed.

The common HostOps-v3 test harness now verifies full readable bytes from the
existing real-pwsh resolver before hashing its fixture executable. A launchable
Store App Execution Alias can be unreadable with `EINVAL` or access denied;
only this fixture falls back to readable `sys.executable` bytes. This pin is
**not production signed PowerShell proof**. Tests requiring native PowerShell
select and execute it separately and still fail specifically if unavailable.
Production Auth, executable hash/signature checks, ACLs, PATH, registry and
execution policy are unchanged. Original negative tests remain in place.

### Recovery verification scope

The registered `mt01-component-onboarding` planner selects `hostops-personal`,
`hostops-v3`, `hostops-w14`, `hostops-w14-api` and `hostops-w14-origin`, reason
`ONBOARD_CONSUMER_OWNER_CLOSURE`, with no unknown AC, test or source mapping and
no fallback. The additional W14 owners import the common `h` fixture. The
persisted `work_items/mt01-components/hostops-personal.json` is unchanged.

Windows self-host diagnostics in new run 41939878199, attempt 1:

| Executed scope | Passed | Failed / errors / skipped | workers_actual |
|---|---:|---:|---:|
| Twelve new standing-READ test nodes (preflight only) | 12 | 0 / 0 / 0 | 12 |
| `tests/test_hostops_v3.py` (one file, preflight only) | 52 | 0 / 0 / 0 | 12 |
| Registered `hostops-w14` group | 11 | 0 / 0 / 0 | 12 |
| Registered `hostops-w14-api` group | 39 | 0 / 0 / 0 | 12 |
| Registered `hostops-w14-origin` group | 23 | 0 / 0 / 0 | 12 |

Each command requested `-n 12`; actual counts come from the existing MT01
xdist controller collection hook. These are current diagnostic observations,
not reused #370 evidence and not aggregate MT01 PASS. No Verify-Fix repair
round was needed. The final reporting update changes documentation only.

The full Personal group contains offline Git commit/push fixtures, which this
executor's explicit Git-write guard forbids. The full v3 group additionally
contains actual native host inventory, outside this executor's no-live-read
scope. Neither registered group was represented as fully verified; no test was
rewritten, skipped or mocked to bypass these limits. EF01 must perform the exact
registered verification under its own authority. Full MT01, exact PR HEAD/base
hosted Windows CI, independent Brain acceptance and the separate protected
sanitizer remain pending. No candidate, PR, Git ref write, live HostOps read,
Chrome action or Wake was performed by this executor.
