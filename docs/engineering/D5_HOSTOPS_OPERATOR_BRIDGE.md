# D5 HostOps operator bridge — source candidate r6 (Root Closure)

Authority: Human correction [#220 comment 6655660004](https://github.com/trung-via/ai-commerce/issues/220#issuecomment-6655660004), single-user architecture #293, original positive/negative AC #428, PR #440 merged bridge source, sanitized receipt #444, and human request 2026-11-05. Base commit: `655668b41eebb721762bcc5b4bf6aadf6a153351`. PR #436's corrected ACL fixture is baseline and remains untouched.

This is SOURCE ONLY. Native installation, interactive login, subsequent logon, stable CDP and recovery acceptance are **PENDING_GENUINE_HOSTOPS_DEPLOYMENT**. No current CLI, public Issue route or Actions workflow enables this bridge. Candidate publication belongs to the EF02 worker; the code executor edits only the eight granted paths. CI is not Brain PASS, merge, enrollment or laptop effect. Code alone does NOT activate Windows.

The selected behavior is audited dedicated Chrome acquisition followed by Playwright attach, preserving the existing config/profile/ACL guard. Old Downloads scripts are unavailable as trusted bytes; their names and historical READY_ATTACH are workflow evidence only. No Downloads content is fetched, executed or packaged. The new reset chain is not selected. No automatic #179 replay or D5 manifest/authenticated chat claim exists.

## Three Root Gaps Addressed

1. **GitHub HostOps route only bounded READ/WRITE not D5**: `.github/workflows/issue-ingress.yml` only routes `[HOSTOPS READ]` (diagnostics, status) and `[HOSTOPS WRITE]` (file write, git sync). Public Personal READ permission does NOT grant D5 WRITE. Protected workflow integration is specified in the exact patch handoff below under the mandatory truth label `PROTECTED_INGRESS_NOT_MERGED`.
2. **PR #440 merged Bridge but lacked private operator receiver/logon service**: Implemented source-side `D5Receiver` and `D5IngressBoundary` with named-pipe logon trigger (`AI-Commerce-D5-Operator-v1`), client attestation (SID, interactive session > 0, limited non-elevated token, input desktop), enrollment verification, and durable receipts. Bounded tests cover logon delivery without local EF02 service installation.
3. **ChatGPT cannot issue approved D5 operations and obtain native receipts**: Defined fail-closed ingress boundary with parameterless fixed operation schema, strict off-host signing/custody checks, journal serialization, durable native effect receipts, and field-level public receipt sanitization.

## Operational Bug Fix: Graceful Stop under Active Profile without Listener

In PR #440, `Bridge.execute` checked `if snapshot["state"] == "PROFILE_ALREADY_ACTIVE_NO_CDP": return receipt(op, "PROFILE_ALREADY_ACTIVE_NO_CDP")` at the beginning of execution, preventing `STOP_OWNED_D5_CHROME` from executing.

A verified exact owned D5 root in `PROFILE_ALREADY_ACTIVE_NO_CDP` state now permits a bounded graceful close:
- `execute` allows `STOP_OWNED_D5_CHROME` to proceed when `snapshot["state"] == "PROFILE_ALREADY_ACTIVE_NO_CDP"`.
- Native adapter and PowerShell script revalidate the root process identity (exact owner SID, interactive session, creation timestamp, executable path, command-line arguments matching port 9333, address 127.0.0.1, user-data-dir, profile-directory, and no `--type=` arguments).
- Bounded graceful termination via `CloseMainWindow()` with 10-second timeout; NEVER permits force-kill (`Kill()`), profile deletion, lock deletion, or relaunch under ambiguity.
- If stopping fails to reach `ABSENT` state, raises `UNKNOWN_EFFECT_HOLD` fail-closed.
- All other non-read operations under `PROFILE_ALREADY_ACTIVE_NO_CDP` hold without mutation.

## Closed call path and one-time enrollment

Brain → private authenticated HostOps receiver → `Bridge.invoke` → native operator-session adapter → native durable receipt → sanitized journal response. There are no request command, path, PID, script, shell, task or environment parameters. `op`, random 32-hex request ID, 60-second issued/expiry, exact release source SHA, enrollment epoch, boolean dry-run and HMAC are the entire request shape. The private control plane holds the signing capability; public GitHub carries no credentials. Revocation and signed enrollment are reloaded on every call, including logon. Ordinary operations then need zero routine Human PowerShell.

`Enrollment` defaults disabled, revoked grants fail closed, and `private_bridge` has no unsigned verifier or standalone CLI. The private trusted service must provide `verify_signed(envelope)` with an independently provisioned trust anchor, verify the enrollment's signature AND current revocation record, then return exactly the policy fields accepted by `load_enrollment`. Never use an identity callback, Issue-provided public key, boolean from an Issue, or the current working tree as authority. One initial physical enrollment/account login is separate from routine operations.

Signed policy fields are `source_sha`, `epoch`, `sid`, `expires`, `release_root`, `files`, `chrome_config`, `local_app_data`, `state_root`, `key_file`, `legacy_tasks`, `runner_tasks`, `enabled`, `revoked`, `private_control_plane`, `public_runner_exposed`. `files` pins the entire installed ai_commerce Python import closure and reviewed PS bytes, including all package initializers/dependencies. The executing code must be inside that pinned private release. Install optional Playwright from a separately verified private runtime; its absence returns HOLD, never downloads on demand. `state_root` is the source-defined per-operator AI-Commerce/D5-Operator directory and `key_file` is its `control.key` (32 random bytes). Actual paths/keys/configs never go to GitHub. Exact-SID owner/ACL and reparse checks apply; no ACL weakening is implemented.

**Same account is not secret isolation.** Both `ai-commerce` and `ai-commerce-hostops-control` repositories report visibility PUBLIC. All three public self-host runners (`AI-Commerce-EF02-Interactive`, `AI-Commerce-EF02-02-Interactive`, `AI-Commerce-HostOps-01-Interactive`) run under the SAME Windows SID as the operator. Their same-SID ability to read operator files is incompatible with storing private broker credentials or signing keys on that host while executing untrusted public jobs. There is no default assertion that ACLs solve this. If the private deployment cannot demonstrate independent secret custody (e.g., off-host signing capability or hardware token), `public_runner_exposed` must remain `true` and the bridge DISABLED (`BLOCKED_NOT_DEPLOYED`).

## Deterministic operation contracts

Every operation first validates enrollment/source/session and inventories all three exact runner tasks plus D5 task/launcher ownership. Unknown/duplicate/wrong-location D5 tasks, a running legacy task, unknown native intent or an ambiguous profile cause HOLD. A dry-run authenticates and reserves a request but performs only inventory/ownership checks; it does not attach or mutate.

| Operation | Precondition and observed postcondition |
|---|---|
| STATUS | Inspect owned process/listener, CDP version and read-only Playwright connection; distinguish NOT_RUNNING, ROOT_PROCESS_LOST, PROFILE_ALREADY_ACTIVE_NO_CDP, listener loss, restart and READY_ATTACH. |
| MIGRATION_PREFLIGHT | All three runners exist; every discovered D5 task and launcher matches enrolled metadata. Returns MIGRATION_READY without change. |
| RETIRE_NEW_AUTOSTART | Browser absent; exact D5 legacy XML/launcher hashes; versioned metadata backup; remove only those verified task/launcher entries; verify absent and runners unchanged. |
| STOP_OWNED_D5_CHROME | Exact signed exe/argv/root SID/session/PID/creation; recheck immediately; graceful CloseMainWindow and bounded exit; verify absent. No forced termination, profile or lock deletion. Operates from OWNED or PROFILE_ALREADY_ACTIVE_NO_CDP state. |
| INSTALL_VERIFIED_OLD_BEHAVIOR_LAUNCHER | Browser absent, conflicts retired; create only source-pinned launcher, or verify existing exact bytes. |
| START_OR_REUSE_D5_CHROME | Reuse exact owned listener, otherwise launch once only from absent/no-claim/no-lock state. Check returned PID/creation, tolerate up to 20 × 0.5 seconds stabilization. |
| VERIFY_CDP_9333_AND_PLAYWRIGHT | Exact loopback owner; bounded `/json/version`; Playwright connects without accessing contexts/pages/content, then stops its client transport (never Browser.close). Repeat ownership and attach after 0.5 seconds. |
| INSTALL_SINGLE_LOGON_AUTOSTART | Conflicts retired, pinned launcher installed; register exactly one current-SID InteractiveToken/Limited at-logon task with IgnoreNew. A second call verifies its pinned XML and does not register another. |
| REMOVE_D5_AUTOSTART | Browser absent, verified task XML; remove only D5 operator task and its hash pin; profile and runners unchanged. |
| RECOVER_BOUNDED | Safe absent/unclaimed/unlocked state only, no conflicting old task; one launch, at most one recovery request per 300 seconds. Owned state is reused. Active exact profile with missing listener always HOLD. |
| ROLLBACK | Browser absent; remove only verified operator task/launcher and retain versioned metadata. Return to disabled baseline. Never resurrect the unproven reset/autostart chain automatically. |

The existing acquisition CLI remains backward compatible (including historical ports). The enrolled bridge alone fixes port 9333. Registration, launch, listener binding, Playwright attachment and stability are independent receipt fields. Listener/attach proves neither actual ChatGPT login nor exact-chat/manifest readiness. D5 manifest is a future independent gate.

## Native migration and durability

The three immutable runner names are `AI-Commerce-EF02-Interactive`, `AI-Commerce-EF02-02-Interactive`, `AI-Commerce-HostOps-01-Interactive`. Native code hashes their task XML before and after every call and never mutates them. Personal Chrome, dedicated profiles, auth/cookies and singleton locks are never modified/deleted. No machine-wide enumeration of files or arbitrary PID/path from a request is available.

One-time private enrollment inventories legacy D5 task names and XML hashes plus exact launcher/hash. Source permits only AI-Commerce-D5-* tasks in the root task folder and `.ps1` launchers directly in the dedicated AI-Commerce/D5-Autostart directory, outside Chrome profile storage. Unknown legacy locations/ownership, including Downloads, HOLD for separately reviewed migration; the bridge never broadens deletion scope. It saves only known reviewed task XML/launcher bytes under versioned private backups, not Chrome files/secrets. Backups stay local and are not posted to GitHub.

SQLite reserves an INTENT with synchronous FULL before any native effect. A crash leaves INTENT; unexpected errors/timeouts become UNKNOWN. Neither is retried, regardless of a fresh request ID or restarted process. The native adapter separately MAC-authenticates a 30-second, one-use nonce, stores it before effect and fsyncs a correlated native receipt. Failed postconditions hold. Recovery from UNKNOWN requires independent native reconciliation and a separately reviewed private control-plane action; there is intentionally no public clear/retry endpoint.

The journal caps at 2048 requests and never drops replay IDs silently. The private operator must archive it and rotate enrollment epoch/key with no pending intents, preserving old replay evidence before creating a fresh journal. Native nonce/receipt storage must be archived in that same attended bounded maintenance; no age-based deletion or automatic retry exists.

Native logon action invokes only the pinned launcher with `-Logon`, without input prompts. It sends fixed `D5_LOGON_START_V1` to the private receiver over `AI-Commerce-D5-Operator-v1`, which authenticates the client desktop and calls `Bridge.logon()`. Delivery means only LOGON_TRIGGER_DELIVERED. No claim of browser start is derived from task registration or trigger delivery. Bridge journal serialization and process/port guards prevent duplicate browsers even if the trigger arrives twice.

## Protected Ingress Patch Handoff (Truth Label: PROTECTED_INGRESS_NOT_MERGED)

> [!IMPORTANT]
> **Truth Label: `PROTECTED_INGRESS_NOT_MERGED`**
> The current public Personal READ permission does NOT grant D5 WRITE. GitHub Issue Ingress currently routes only `[HOSTOPS READ]` and `[HOSTOPS WRITE]`. Workflow files in `.github/workflows/` are protected and CANNOT be edited by this EF01/EF02 invocation. An independently reviewed protected workflow owner must approve and merge the following change separately once genuine private custody is proven.

### Proposed Protected Diff for `.github/workflows/issue-ingress.yml`

```diff
--- a/.github/workflows/issue-ingress.yml
+++ b/.github/workflows/issue-ingress.yml
@@ -37,7 +37,12 @@ jobs:
          github.event.comment.user.login == github.repository_owner &&
          startsWith(github.event.issue.title, '[HOSTOPS WRITE] ') &&
-         github.event.comment.body == format('APPROVE WRITE #{0}', github.event.issue.number))
+         github.event.comment.body == format('APPROVE WRITE #{0}', github.event.issue.number))) ||
+        (github.event_name == 'issue_comment' &&
+         github.event.action == 'created' &&
+         github.event.issue.user.login == github.repository_owner &&
+         github.event.comment.user.login == github.repository_owner &&
+         startsWith(github.event.issue.title, '[HOSTOPS D5] ') &&
+         startsWith(github.event.comment.body, 'APPROVE D5 #'))
       )
     # Dedicated operator-interactive instance; EF02 uses only ai-commerce-ef02.
     runs-on: [self-hosted, Windows, ai-commerce-hostops]
@@ -58,6 +63,14 @@ jobs:
         env:
           HOSTOPS_PERSONAL_MODE: ${{ github.event_name == 'issues' && 'read' || 'write' }}
+          HOSTOPS_D5_TRIGGER: ${{ startsWith(github.event.issue.title, '[HOSTOPS D5] ') && 'true' || 'false' }}
         run: |
           $env:PYTHONPATH = Join-Path $env:GITHUB_WORKSPACE "src"
+          if ($env:HOSTOPS_D5_TRIGGER -eq 'true') {
+            # Require off-host private signature and fail-closed receiver boundary.
+            # Never execute arbitrary shell, commands, or unsigned scripts.
+            python -m ai_commerce.hostops_v3.d5_control --ingress-event "$env:RUNNER_TEMP\d5-event.json" --output "$env:RUNNER_TEMP\personal-hostops-result.json"
+            exit $LASTEXITCODE
+          }
           python -m ai_commerce.hostops_v3.personal --mode "$env:HOSTOPS_PERSONAL_MODE" --output "$env:RUNNER_TEMP\personal-hostops-result.json"
           exit $LASTEXITCODE
```

### Ingress Safety Constraints
1. **No Fake Transport**: Requests must never fabricate local pipe or HTTP listeners to simulate private connectivity from CI.
2. **Fail-Closed on Unproven Custody**: If private off-host signing/control infrastructure is absent, the boundary returns `BLOCKED_NOT_DEPLOYED` with minimal attended enrollment metadata.
3. **Field Whitelist Only**: Public comments on GitHub Issues carry strictly sanitized receipts (`sanitize_receipt`), never raw process command-lines, CIM properties, file paths, or private cookies.

## Verification and remaining acceptance

Compile/import smoke precedes tests. Run new component and acquisition tests, the corrected registered ef02-wake consumer, then the MT01-derived hostops-d5-control + hostops-v3 + hostops-personal + ef02-wake owner/consumer closure using registered 12-worker Windows commands. An informational in-memory impact analysis selected these four groups (26 test files), `AFFECTED`, `ONBOARD_CONSUMER_OWNER_CLOSURE`, with no missing mapping; this is not independently executed registered verification. Collect actual xdist IDs/JUnit and counts; request count is not worker proof. All base/temp/key/evidence/scratch paths must be absolute and external to checkout under OS RUNNER_TEMP, created/checked in the same command. Before/after each verification inspect `git status --porcelain --untracked-files=all`, reject anything outside the eight paths. Do not modify .gitignore or the corrected #436 fixture. Verify-Fix ≤2.

Automated tests use simulated authenticated lifecycle and synthetic process/port/session ownership only. Native PowerShell tests parse AST or isolate pure functions with synthetic values; they must not invoke the entrypoint, scheduler, real desktop or real Chrome. Tests cover fixed lifecycle and repeated logon, all dry-runs, signature tamper/replay/stale epoch/source, private custody and revocation, missing/wrong tasks, session/elevation/SID, listener/root loss/restart, rate limit, bounded stabilization, crash/UNKNOWN and private disclosure.

Still pending genuine HostOps receipts: exact migration inventory + backup + selective retirement, graceful stop preserving profiles/runners, one logon task registration, actual post-login launch followed by subsequent stable attach, observed port loss root cause, bounded safe recovery and rollback; two logon deliveries must show one browser. Receipts must correlate private intent/native ID/source/epoch/SID/session/timing, pre/post task XML and root PID/creation/port owner, CDP version, both Playwright checks, and independent local receipt readback. Publish only sanitized enums/digests and acceptance status. CI alone cannot satisfy these native gates.
