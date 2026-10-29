# EF02-D / D2 — Trusted Origin & Local Wake Adapter

**Scope:** independent `trung-via/ai-commerce`. **Capability:** fail-closed local
components; production origin/artifact authorities, ChatGPT write and independent
receipt verification are **UNSUPPORTED**. Live Wake and native Brain continuation
are **NOT_PROVEN**.

## Preserved source and failed-attempt lineage

This repair starts from preserved D2 source commit
`a4bc983dc7588c869d0281680ea2211b349ea82f`, exact parent
`d69a6ccffbbe88424513c901bebaf9ecd661794a` (main after PR #94).
Issue #98 / run `40920589256`, action `EF02-D2-01`, attempt
`ef02-d2-01-agy38flash-high-20261030-v1` ended
`GIT_UNEXPECTED_COMMIT / GIT_HEAD_CHANGED`. That attempt and its claims remain
immutable. Its provider stdout is not EF02 verification or candidate publication
evidence. The preserved source is not a verified/published EF02 candidate.

This executor edits only the local D2 component, its existing flat test file and
this document. HEAD stays pinned. EF02 owns any later verified candidate creation.
No automatic merge, PR, dispatch, external posting or live ChatGPT activity is
introduced. D1 passive completion projection and read-only binding retain their
existing meaning and source bytes.

## Independent authority boundaries

`LocalCallerAttestation` and `GitHubArtifactProvenance` are **untrusted hints**,
including when `attestation_verified=True`. Token equality, model memory, Issue
comments, public SHA-256 values and two matching JSON documents cannot establish
trust. The legacy `expected_token` argument alone grants no authority.

- `OriginAuthority.authorize_origin()` must independently authenticate the local
  caller, explicit Human consent, exact target/claim and registry integrity.
  It receives operation context, and the validated registry at the final check.
- `ArtifactAuthority.verify_artifact()` must independently authenticate the
  GitHub repository, run, run attempt and artifact identity, and bind the exact
  event and final batch-result contents to that source. A public artifact digest
  alone is not authentication. The authority must obtain trusted evidence outside
  the request rather than trusting the supplied provenance object.
- Authorities are injected only by trusted local application configuration.
  Never deserialize or choose them from an event, Issue, model output or route
  file. D2 contains no production authority implementation, key, fabricated
  witness or credential discovery. Missing, rejecting, exception-throwing or
  merely truthy authorities fail closed: `UNVERIFIED_ORIGIN` or
  `PROVENANCE_UNVERIFIED`. Only literal `True` passes an authority check.

`verify_provenance()` retains D1 shape/digest checks and compares exact source,
batch, terminal status, action/attempt IDs, optional candidate SHA, aggregates,
Issue, run ID, `wake_event_id` and `wake_event_status=RECORDED_UNBOUND`.
Run/aggregate/version identities reject bool/float coercions. The unchanged D1
batch report has no run-attempt field: authenticating that attempt and its exact
artifact contents is a mandatory authority responsibility. An explicitly supplied
`github_workflow_run_attempt` must also match.

Test-only authorities use separately enrolled caller objects and pinned artifact
snapshots. They exercise the boundary; they are not production authenticity proof.

## Private origin registry and stable binding

Registration requires explicit `consent=True` (default false), exact ChatGPT
conversation UUID, loopback HTTP CDP endpoint and positive integer generation.
There is no current/newest/active-tab discovery or new-chat fallback. Duplicate
route/chat identities, duplicate claims, malformed/duplicate JSON keys, stale or
future generation and cross-chat conflicts are denied. One chat can own multiple
different batch claims. Historical lower-generation claims never become eligible
for the advanced route.

The registry and idempotency file must have distinct absolute paths in an existing
operator-provisioned private directory outside the checkout. Their lock paths must
also be distinct. Secure local ownership/ACLs and trusted application configuration
are prerequisites; these files are not an authentication source by themselves.
Do not put them in repository artifacts, Issue content or command arguments.
Bulk registry/store replacement APIs are denied so they cannot erase claims or
roll back generations. The delivery path pins the validated, independently
authorized registry generation through the bounded adapter call.

## Atomic persistence and uncertain outcomes

`_private_state.py` shares bounded JSON transactions between both stores:

- An OS lock covers the full read/validate/modify/write operation, including the
  atomic claim. Lock files are never unlinked or reclaimed by age. Lock contention
  times out closed; process exit releases the OS lock.
- State and lock files must be regular files with a single hard link. Symlinks,
  parent reparse points/junctions, repository-internal paths, parent traversal
  and UNC paths are rejected. Checks repeat during transactions and validate
  opened file handles, not only the initial pathname.
- Windows pins ancestor directory handles without delete sharing and opens the
  reparse point itself before inspecting its attributes. POSIX uses directory
  descriptors and no-follow relative operations. See the primary
  [CreateFileW documentation](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
  for the Windows handle flags and sharing behavior.
- Each write uses an exclusive random same-directory temporary file, flush/fsync
  and atomic replacement; POSIX also fsyncs the directory. A fixed `.part` name
  cannot redirect writes. Unavailable locking/storage safety fails closed.
  These are process-crash guarantees; no hardware power-loss guarantee is claimed.

`record_intent(event_id, route_id, generation)` returns true to only one writer,
after persisting `UNKNOWN` and before invoking any adapter. All existing claims,
including `UNKNOWN` and `UNSUPPORTED`, prevent another invocation. A crash,
exception, malformed store or uncertain outcome never authorizes blind retry.
Do not delete/restore old state files or their directories to retry an event.

No independent receipt verifier is implemented. Caller-supplied `CONFIRMED`,
delivery booleans, click success, witness dictionaries and legacy confirmed
records cannot prove delivery. They remain `UNKNOWN` and held. Wrong event,
route, generation or continuation claims are rejected. Raw witness payloads are
discarded, never recorded or replayed; only fixed allowlisted diagnostic codes
persist. Exceptions and reprs do not expose caller tokens, cookies, chat URLs or
CDP endpoints. Native Brain continuation always stays false / `NOT_PROVEN`.

## CDP capability and remaining acceptance

`LocalCdpWakeAdapter` is a **probe-only adapter**. With no injected client and
default `probe_enabled=False`, it performs no browser/network operation and
returns `UNSUPPORTED / CDP_PROBE_DISABLED`. Explicit probes use direct bounded
loopback HTTP, with no environment proxy or redirect following.

Exactly one matching page target is required. Missing, malformed, duplicate or
wrong-chat targets fail closed. Injected clients can check authentication on that
exact target; unavailable authentication, captcha/challenge or attach failures
produce fixed diagnostics. The adapter never calls `post_chat_message()` or
`witness_exact_receipt()`, navigates, opens a tab or selects another conversation.
Even an authenticated exact target returns `PLATFORM_WRITE_UNSUPPORTED`.
A caller-supplied custom adapter cannot turn its own receipt into confirmation.

D2's supported outcome is therefore local validation and safe unsupported/unknown
handoff. Actual exact-chat delivery needs independently implemented and reviewed
authorities, a supported authenticated write capability and independent exact-turn
receipt verification. D3 multi-chat live delivery, real receipt recovery and
Brain continuation remain unproven. GitHub feedback/artifacts remain the passive
fallback; no AIOS-Renew runtime, credentials or lifecycle is imported.

## Verification scope

Focused command:
`python -m pytest tests/test_ef02_wake_d1.py tests/test_ef02_wake_d2.py -q -n 12`.

The registered `project-tests` argv is read unchanged from
`.github/ef02/verify-profiles.json`:
`python -m pytest tests -q -n 12`.

Regressions cover forged trust/provenance, pinned independent artifact snapshots,
strict identities, exact-chat probes, route-generation pinning, concurrent registry
updates, concurrent process claims, actual process exit after an UNKNOWN write,
unknown/unsupported replay, forged/wrong receipts, failed persistence, secret
redaction, malformed state, hardlink/reparse metadata and native link checks.
Native link checks explicitly skip if the host lacks permission; metadata tests
do not claim to replace native OS evidence. All browser/transport tests use fakes;
no test in this D2 repair performs live ChatGPT activity.

Actual run results, source/scope/file hashes and final unchanged HEAD must be
reported by the executor/EF02 verification evidence. This document does not
self-certify a successful test run or a published candidate.
