# D5 Wake — conditional readiness with incremental evidence reuse

**Implementation status: review candidate; NOT deployed or live-Wake accepted.**

This upgrades D5 from repeated Human-run checklists to one **explicit, bounded
local Windows operation**. It is not a GitHub runner command that launches
interactive Chrome, and it never issues browser Send. It reuses existing
`chrome_acquisition`, `WakePanel`, D5 ledger, enrolled exact-chat route
and original signed-consent/send-once authority.

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
per requested preparation. Reusing a verified browser avoids full process
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
