# D5 Wake — conditional readiness with incremental evidence reuse

**Implementation status: PR #215 merged to main and post-merge Windows/Ubuntu source CI PASS. No operator laptop deployment, CDP 9333 activation, or live Wake acceptance has been proven.**

This upgrades D5 from repeated Human-run checklists to one **explicit, bounded
local Windows operation**. It is not a GitHub runner command that launches
interactive Chrome, and it never issues browser Send. It reuses existing
`chrome_acquisition`, `WakePanel`, D5 ledger, enrolled exact-chat route
and original signed-consent/send-once authority.

## Human-attended Chrome CDP activation (Issue #220)

The previous HostOps READ [#361](https://github.com/trung-via/ai-commerce/issues/361)
ran on the dedicated HOSTOPS-01 and observed **0 TCP listeners on 9333**.
It is a **current foundation blocker** only: Chrome may be running
elsewhere without CDP. This new wrapper builds on the existing
\`chrome_acquisition\` guard and does **not** widen the HostOps route.

After merging this source PR and synchronizing the operator checkout
\`C:\TOOL\ai-commerce\` through the already reviewed Git fast-forward
procedure, a Human opens **non-elevated PowerShell in their logged-in
Windows desktop session**. One attended command:

~~~powershell
Set-Location C:\TOOL\ai-commerce
.\scripts\AI-Commerce-D5-Attended-CDP.ps1 -Mode StartCDP
~~~

It first verifies local user/session, exact repo \`main\`, clean tracked
worktree, local Python source, and one preexisting private Chrome config.
Known existing config paths are searched only under
\`%LOCALAPPDATA%\AI-Commerce\`. If none or more than one matches,
the script **HOLDs**; the Human may explicitly select their *real*
preexisting operator-owned config, without posting it publicly:

~~~powershell
.\scripts\AI-Commerce-D5-Attended-CDP.ps1 -Mode StartCDP -ChromeConfig '<existing-private-CDP-config.json>'
~~~

The owner types \`START-CDP-9333\` once locally. The wrapper delegates to
the preexisting \`chrome_acquisition launch --opt-in\`: it refuses
non-Google-signed Chrome, elevated session, wrong/private profile,
untrusted ACL, contested port, nonloopback binding, old singleton locks
or uncertain launcher ownership. No profile copy, arbitrary subprocess
or issue-passed shell command. A successful result:

~~~text
D5_ATTENDED_STATUS=CDP_9333_READY
D5_LAUNCHED_NEW_CHROME=True
D5_WAKE_SENT=False
~~~

\`False\` for \`D5_LAUNCHED_NEW_CHROME\` is also correct when the same
dedicated browser was already verified. There is **one launch attempt**;
if it reports uncertainty, **do not immediately rerun**. The operator
may inspect CDP read-only with \`-Mode Inspect\` and share only the
sanitized \`D5_ATTENDED_STATUS\` lines, not private paths or tokens.

Once CDP is confirmed by a new ChatGPT-initiated HostOps
\`cdp_9333_status\` READ, exact-chat preparation remains a **separate**
private D5 state gate. If an owner-enrolled \`wake-readiness.json\`
already exists with a genuinely new \`READY\` ledger at current main,
the owner can opt into navigation/check only:

~~~powershell
.\scripts\AI-Commerce-D5-Attended-CDP.ps1 -Mode PrepareChat
~~~

This requires typing \`PREPARE-EXACT-CHAT\` locally and returns
\`READY_FOR_PREPARE_NO_SEND\` only when the original readiness flow
independently verifies exact enrollment and chat. No manifest, a stale
SHA, or a \`SUBMISSION_UNKNOWN\` ledger means HOLD; do not repurpose the
historic #179 record. If no existing manifest, use the previously
reviewed \`wake_readiness_bootstrap\` with **actual** owner-private files
and a *separately authorized fresh READY episode*, never a synthetic
ledger or rewritten old record. \`PrepareChat\` may need the optional
\`local-chat-wake\` Playwright dependency; missing dependency blocks
without Send. Neither \`StartCDP\` nor \`PrepareChat\` automatically
dispatches an EF02 batch or sends a message.

The new wrapper is *local source*, **not a HostOps arbitrary-shell
capability** or deployed unattended bridge. Human operating the
interactive session supplies the one Chrome launch opt-in; HostOps
independently measures only public TCP/CDP metadata. A future
fixed, enrolled remote bridge would still need separate Human-scoped
authorization and direct native task/receipt evidence.

## Smart readiness decision tree

~~~text
New Wake preparation (local, operator-approved)
  |
  +-- Private ledger != READY, or UNKNOWN? -> HOLD (no retry)
  +-- Enrolled exact route absent / not 9333? -> HOLD
  +-- GitHub main != pinned ledger SHA? -> HOLD (create a NEW approved episode)
  |
  +-- Cache of prior verified Chrome proof (max 10 minutes)?
  |    +-- YES: same approved config content hash + same live PID +
  |    |         same process creation time + 127.0.0.1:9333 owner +
  |    |         signed executable path + same Windows interactive session +
  |    |         valid live /json/version response -> REUSE
  |    +-- NO: original full Chrome acquisition, ownership/SID/Authenticode
  |              - already running + verified -> REUSE
  |              - absent and pre-authorized opt-in launch -> AUTO-FIX: ONE launch + verify
  |              - unknown, contested, non-loopback, wrong profile -> HOLD
  |
  +-- Local ledger/route/TCP consistent?
  |    +-- NO -> HOLD
  |    +-- YES -> inspect exactly the enrolled chat (one census)
  |                - exactly one tab -> reuse
  |                - missing + pre-authorized opt-in -> AUTO-FIX: one preload; recensus
  |                - ambiguous/unknown -> RECONCILE_ONLY, never retry
  |
  +-- Ledger/route unchanged; recheck live main only if a browser
       launch/preload occurred -> READY_FOR_PREPARE (never READY_TO_SEND)
~~~

**Change-driven invalidation (no arbitrary TTL or periodic full rescan)**: If approved config hash, PID, process creation,
Windows session, browser executable, localhost binding, CDP protocol changes, the cache cannot be reused; perform full
acquisition once. No second launch after uncertain outcome. The source SHA,
ledger, route and tab check still occur at the final action boundary. Final
Send separately rechecks ledger, source, consent and exact composer; a readiness
cache never authorizes Send or an unknown-event retry. No full UI 3/3
revalidation or full suite CI is part of ordinary Wake preparation.

## One-time operator-private manifest

The manifest can be provisioned **once**, using the audited
`ai_commerce.remote.wake_readiness_bootstrap` command (separate reviewed change).
It verifies the existing owned Chrome config, the *already-enrolled* exact
9333 route, the current `READY` D5 ledger and its fresh main source SHA,
then creates the manifest atomically under the operator's private appdata.
It does **not** mint a ledger, invent a route, update consent, launch Chrome or
send a Wake. Existing different manifests are not overwritten. All returned
errors are sanitized reason codes.

Under the Human's Windows interactive session (not an Actions service):

~~~powershell
python -m ai_commerce.remote.wake_readiness_bootstrap `
    --checkout 'C:\TOOL\ai-commerce-d5' `
    --chrome-config '<existing real private chrome configuration file>' `
    --state-file '<existing authorized READY ledger file>' `
    --registry '<existing consented route registry file>' `
    --route-id '<existing enrolled route ID>'
~~~

The paths/route ID above are **intentional placeholders**, not guessed
laptop paths. Supply only previously established local private values; do
not paste them into GitHub Issues. A legacy ledger bound to a pre-merge SHA
requires a separately authorized *new episode*, never an implicit rewrite.

Before first use, provision an ACL-restricted manifest OUTSIDE the repository,
at `%LOCALAPPDATA%\AI-Commerce\wake-readiness.json`. Contents are
illustrative only; substitute existing **real** D5 config, route and a newly
authorized, current `READY` ledger:

~~~json
{
  "version": 1,
  "checkout": "C:\\TOOL\\ai-commerce-d5",
  "chrome_config": "C:\\Users\\OPERATOR\\AppData\\Local\\AI-Commerce\\Chrome-Acquisition\\config.json",
  "state_file": "C:\\Users\\OPERATOR\\AppData\\Local\\AI-Commerce\\private-new-ledger.json",
  "registry": "C:\\Users\\OPERATOR\\AppData\\Local\\AI-Commerce\\enrolled-route-registry.json",
  "route_id": "ENROLLED_ROUTE_ID"
}
~~~

The manifest and `wake-readiness-cache.json` require the existing Windows
exact owner-SID/DACL rules. No raw private paths, chat URLs, credentials,
profile data, or ledger contents may enter a GitHub Issue. **Do not assume a
prior source SHA, expired signature, or historical UNKNOWN event becomes valid
because Chrome is running.**

## One command, one flow

Under a **non-elevated interactive operator session**, after reviewing/deploying
the PR and one-time manifest:

~~~powershell
Set-Location 'C:\TOOL\ai-commerce-d5'
python -m ai_commerce.remote.wake_readiness_flow --flow-config "$env:LOCALAPPDATA\AI-Commerce\wake-readiness.json" --opt-in-launch --opt-in-open-chat
~~~

A successful result looks like:

~~~json
{
  "status": "READY_FOR_PREPARE",
  "chrome": "VERIFIED_9333",
  "chat": "EXACT_CHAT_PRESENT",
  "ledger": "READY",
  "verification": "LIVE_PID_CREATION_AND_CDP_REUSED",
  "launched": false,
  "wake_sent": false,
  "host_plugin_invocation_proven": false
}
~~~

The first cold invocation returns `verification=FULL_ACQUISITION` on
successful full verification. The fast cache is advisory optimization backed
by live PID, process creation and protocol verification: **it does not expire
merely because time has passed**, but invalidates on observed condition changes.
It is **not** a persistent unattended service or blanket authorization. Missing optional launch/preload consent returns
`BLOCKED` without a browser mutation. With the configured explicit opt-ins,
ordinary recoverable conditions are repaired in the same invocation; a
successful result returns `auto_repaired=[...]` listing any actual repairs.
A failed/ambiguous launch never triggers a second launch automatically.

`READY_FOR_PREPARE` permits the existing separately authorized local
`prepare-turn` operation to be considered; it is **not** receipt of a Wake
or permission to perform Send. The existing D3/D2 consent and UNKNOWN
idempotency guards are never bypassed. No new legacy Wake #161/#177/#179.

## Integration and operations

The local D5 operator/Control Center can invoke the same state machine once
per requested preparation. Once enrolled, future Wake preparations reuse the
private manifest, so there is no per-Wake setup or checklist. Reusing a verified browser avoids full process
enumeration + Authenticode on each Wake; a fresh exact-chat census still checks
the route and one live main SHA lookup checks the ledger. A UI can present one
result code rather than expose each low-level command to Human.

GitHub HostOps v2 currently authorizes fixed read and narrowly signed file
writes, **not** arbitrary PowerShell or interactive Chrome launch. The self-
hosted Actions runner is not automatically the logged-in Chrome desktop.
Remote triggering the interactive local operation requires a **separate
reviewed, approved, allowlisted operator-attested execution bridge**;
do not smuggle browser launch into `scratch_marker_write` or
`workspace_file_write`.

## Acceptance criteria before live Wake claims

- Unit tests: cold full vs warm PID+start-time/CDP cache hit, unchanged
  long-lived browser without arbitrary TTL, changed
  config, replaced PID, failed handshake, source drift, contested route,
  UNKNOWN ledger and no duplicate launch.
- CI on actual PR head; no need to repeat the previously accepted 3/3 UI chip
  tests merely to exercise this state machine.
- One separate Human-approved local activation: new operator-approved D5
  ledger, exact route and dedicated Chrome profile, followed by observed
  `READY_FOR_PREPARE`. Chrome startup must first resolve the previously
  observed `CDP_NOT_LOOPBACK_OWNED` if it recurs.
- Send, Host plugin invocation and end-to-end Wake receipt remain separate,
  unproven acceptance thresholds.
