# EF02-D5.4 — finite operator-opt-in local completion handoff

Implementation source: `07ce567ee6250d162eaf695fc74a7f1def9bb001`, 2026-10-30.
This adds an optional local hint and finite listener. Default observation does
not access a browser. `LIVE_WAKE_NOT_PROVEN`; both `chat_delivery_proven` and
`brain_continuation_proven` remain false.

## Preconditions

Retain the complete original authorized request locally. Know the exact
`trung-via/ai-commerce` Issue, run ID, workflow ID, run attempt, issuer, actor,
batch and source. Enrollment never discovers a replacement run or uses the
current Issue body as the original request.

The worker and operator must run on the same Windows machine with the same
independently observed user SID and local private destination. Another service
SID, another machine or a network share is unsupported and fails closed.
Provision a dedicated directory outside BOTH the operational checkout and
source checkout. This code creates no directory, ACL, service or scheduler.

On Windows the owner must equal the process SID. A non-NULL, readable DACL must
explicitly allow that SID. Allow ACEs may name only that SID, SYSTEM, local
Administrators, or OWNER RIGHTS (the independently checked owner). Other
groups, unrecognized/object ACEs or unavailable security information block.
SYSTEM/Administrators are trusted OS administrators. POSIX observation/tests
require effective UID ownership and no group/other permission bits on the
directory, state and lock.

The one private record is capped at 16 KiB: one enrollment, one selector-only
hint and at most one D5.3 registration. No token, key, grant, request instruction
or chat route enters it. Its public enrollment checksum detects accidental
changes; it is not a signature. Owner/permission checks, rejection of
links/reparse points/hardlinks, OS locking and atomic replacement protect state.

## Enroll before completion

Use a new dedicated private directory and filename. The operator can inspect
and provision a new directory with ordinary Windows security tools, such as
`Get-Acl` and `icacls`. Prefix an explicit numeric SID with `*` in `icacls`;
do not grant shared Users groups. Inspect the resulting owner and DACL. Do not
relax permissions on a shared directory to make enrollment pass.

Example provisioning for a NEW directory only, performed by the operator:

```powershell
$privateDir = "<ABSOLUTE-NEW-PRIVATE-DIRECTORY>"
if (Test-Path -LiteralPath $privateDir) { throw "Choose a new dedicated directory" }
New-Item -ItemType Directory -Path $privateDir | Out-Null
$operatorSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls $privateDir /inheritance:r /grant:r ("*" + $operatorSid + ":(OI)(CI)F")
if ($LASTEXITCODE -ne 0) { throw "Private ACL provisioning failed" }
Get-Acl -LiteralPath $privateDir | Format-List Owner,Access
```

Compute the fingerprint from the operator's retained full original request:

```powershell
python -c "import json; from pathlib import Path; from ai_commerce.remote.validation import fingerprint; print(fingerprint(json.loads(Path(r'<PRIVATE-ORIGINAL-REQUEST-JSON>').read_text(encoding='utf-8-sig'))))"
```

Enroll once the exact run is known, BEFORE the worker creates its completion
event. UTC timestamps use `YYYY-MM-DDTHH:MM:SSZ`. The closed run-creation window
spans at most 24 hours and starts at most seven days before enrollment and
authentication. Expiry is in the future, at most one hour after enrollment.

```text
python -m ai_commerce.remote.local_completion_handoff enroll --opt-in --state <PRIVATE-ABSOLUTE-HANDOFF-JSON> --checkout <ABSOLUTE-CHECKOUT> --repository trung-via/ai-commerce --issue-number <ISSUE> --run-id <EXACT-RUN-ID> --run-attempt <ATTEMPT> --workflow-id <WORKFLOW-ID> --batch-id <BATCH> --source-sha <40-CHAR-SHA> --issuer-login <ISSUER> --actor HUMAN --request-fingerprint <ORIGINAL-FULL-REQUEST-SHA256> --created-after <UTC-START> --created-before <UTC-END> --expires-at <UTC-EXPIRY>
```

For authorized BRAIN requests, use the actual actor/issuer and explicit local
`--brain-issuer <LOGIN>` allowlist entries. Issue text cannot set the allowlist.
`--opt-in` is mandatory. Local enrollment reports `ENROLLED`, not authenticated
`REGISTERED`. Identical enrollment never rewrites/rearms; changed identity,
mode or expiry conflicts with the existing record.

## Pre-start the observer and explicitly opt in the worker

Start this in the operator terminal before completion. It can also start after
a hint arrives, while the original enrollment/consent remain valid:

```text
python -m ai_commerce.remote.local_completion_handoff listen --state <PRIVATE-ABSOLUTE-HANDOFF-JSON> --checkout <ABSOLUTE-CHECKOUT> --max-polls 120 --interval-seconds 5 --max-seconds 600 --max-reads 200 --read-attempts 3
```

Independently provision `AI_COMMERCE_GITHUB_READ_TOKEN` locally for read-only
repository access. Credentials are never CLI arguments. Without enrollment or
a hint there are no GETs or browser activity, only finite local checks.

Only for the intended worker process, the local operator supplies these entries
BEFORE starting that worker:

```powershell
$env:AI_COMMERCE_LOCAL_HANDOFF_ENABLED = "true"
$env:AI_COMMERCE_LOCAL_HANDOFF_STATE = "<PRIVATE-ABSOLUTE-HANDOFF-JSON>"
```

These are documentation steps, not activated by this implementation/attempt.
An existing service does not inherit interactive shell changes. This change
does not configure/restart services or modify workflows. If the operator lacks
a supported way to supply these entries to the intended worker, use the manual
D5.3 observer and existing GitHub Issue/artifact fallback.

The existing worker boundary calls the hook after BOTH `completion_event.json`
and final `batch_result.json` are written. It checks literal `"true"`, Windows,
exact runtime selectors, original request fingerprint, issuer/actor and
pre-completion enrollment. It attempts one local atomic hint write with zero
lock wait. Busy locks, unsafe paths, permission errors and all other signaling
failures drop the hint without changing report, feedback or exit. It makes no
GET, starts no daemon/thread and never waits for artifact upload. Ordinary
local filesystem I/O is required; this is not a hard-real-time guarantee
against a hung OS/disk.

The hint has only repository, Issue/run/attempt/workflow/batch/source and event
ID. Its numeric workflow ID is an enrolled selector, not authenticated worker
metadata. The listener authenticates it independently with GitHub.

## Authentication, ordering and bounds

Worker exit precedes GitHub results upload. A hint while the run is unfinished,
or before the immutable request/results archives are ready, permits only
bounded read-only polling. D5.3 authenticates repository/workflow/issuer/current
Issue, the full original fingerprint, exact immutable request archive,
run/attempt, results and original action/attempt pairs. D3.1 then independently
re-reads the exact results. Published ZIP digests, when present, must match.
Malformed/duplicate/expired/ambiguous evidence fails closed. Workflow HEAD and
moving main do not establish requested-source identity.

Polls: `1..120`; interval: `1..600` seconds. The shared GET budget allows
`1..5000` reads, `1..3600` seconds, `1..5` transient attempts. Authentication
is additionally clamped to enrollment expiry. Only existing read-only
timeout/429/5xx/explicit-rate-limit rules retry. Permanent denial, identity,
fingerprint, parse/digest or ambiguity failures stop. Each session is finite;
restart cannot extend consent. Listener OS lock acquisition is bounded to five
seconds; the worker uses zero wait. Missing artifacts may become ready in a
later finite session within the same consent window.

Successful observation persists `OBSERVED` and returns
`REGISTERED / CANDIDATE_READY_NOT_SENT`. A consumed record exits without new
GETs. A plausible bogus hint cannot become an authenticated Wake. No automatic
post-run launcher is installed: the operator pre-starts this finite listener.

## Optional, separately consented delivery

Observation is the default even for a delivery-capable record. The operator
must already have provisioned the exact private D2 route/claim, separate
durable idempotency store, HMAC key and BOTH valid signed D3.2 grants:
`resolve_claim_route` and exact-event `deliver_wake`, bound to current registry
and independently observed SID. See [D5.1](EF02_D5_LOCAL_WINDOWS_WAKE_ENTRY.md),
[D3.2](EF02_D3_SIGNED_ORIGIN_CONSENT.md) and
[D4](EF02_D4_CDP_DOORBELL_ADAPTER.md). This component cannot invent a valid
signed grant for an unknown future event.

Only after explicit Windows interactive consent and the operator's preexisting
literal `AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED=true`, enroll with the additional
`--allow-delivery` flag. The same gate/SID, nonzero process session and
accessible interactive input desktop are required again for delivery.
Pre-start the listener with:

```text
python -m ai_commerce.remote.local_completion_handoff listen --deliver --state <PRIVATE-ABSOLUTE-HANDOFF-JSON> --checkout <ABSOLUTE-CHECKOUT> --registry <PRIVATE-ROUTES-JSON> --idempotency <PRIVATE-DELIVERY-JSON> --grants <PRIVATE-GRANTS-JSON> --caller-id <OPERATOR-CALLER> --max-polls 120 --interval-seconds 5 --max-seconds 600 --max-reads 200 --read-attempts 3
```

The listener never sets a send flag/key. Delivery files must be private and
must not alias the handoff file, each other or any corresponding lock file.
The handoff lock covers authentication and terminal persistence. It persists
`UNKNOWN` BEFORE invoking D5.1, which independently retains D3.1, both signed
grants, D2 write-ahead idempotency and the exact-chat D4 gate. There is no
Chrome launch/navigation or access to private ChatGPT endpoints. D4 requires
exactly one already open authorized chat and a safe empty composer. Its
existing bounded browser-operation timeouts still apply; an ambiguous send
is never interrupted and retried to satisfy a polling deadline.

Any outcome, exception or crash after durable claim remains `UNKNOWN`, with
at most one invocation from this record. UI observations do not prove server
delivery or Brain continuation. `LIVE_WAKE_NOT_PROVEN` is persisted before
delivery and never promoted by this component.

## Stop, rollback and test provenance

Stop a waiting listener with Ctrl+C or let its deadline expire. Remove both
handoff environment entries from the locally provisioned worker environment
to disable signaling; changing shell entries alone does not reconfigure a
service. Remove the operator send gate when finished. There is no scheduler
or background service installed by this feature.

Preserve records and `.lock` files for reconciliation. Never delete/reset
UNKNOWN, advance route generations, change filenames or re-enroll to force a
click. A leftover `.lock` file is normal: live OS locking, not age, controls
contention. Atomic failure cannot expose partial state; an OS/process crash
can leave a temporary `.part` file for operator reconciliation. Do not reclaim
locks or import temporary files as state.

Source rollback removes the hook/module/docs/tests and optional zero-wait
lock parameter, preserving external private delivery history. Tests use
synthetic archives, test-only ACL provisioning, fake clocks, synthetic signed
grants and a fake CDP inventory. Windows may skip native link creation.
No actual Chrome/network/chat action, live enrollment, host receipt or Brain
continuation is proven. See [attempt provenance](EF02_D5_4_IMPLEMENTATION_REPORT.md).
