# A17.3 D07/D08 fresh offline implementation

This source was rebuilt from clean main `f818b187bdc6287a6d0ef93f053f625dab63ec73`.
Before any write, HEAD, clean status and absence of all twelve authorized paths
were asserted on the actual checkout. No prior A17.3 source, recovery snapshot,
candidate or historical test receipt was used. Canonical product requirements:
[Brain #272](https://github.com/trung-via/ai-commerce/issues/272),
[Brain #270](https://github.com/trung-via/ai-commerce/issues/270), and the original
[product AC #276](https://github.com/trung-via/ai-commerce/issues/276).

The acceptance boundary is **offline interfaces, capability denial and synthetic
channel isolation only**. LIVE posting remains unavailable. Nothing here proves
provider entitlement, authenticated Human authorization, public visibility,
production integration, audience reach, revenue or settlement.

## Implementation

Five source files implement bounded exact-key request, Human proposal, capability,
intent, synthetic receipt, callback, catalog, metric and projection contracts. The
two JSON contracts mirror self-contained runtime schemas, with no remote schema
resolver. Unknown fields, duplicate JSON keys, non-finite numbers, excessive
depth/size, malformed identity and obvious credentials/contact data are rejected
with bounded error codes. Inputs permit business aliases and hashes, not raw
receipts, caption exports, personal profiles or credentials. Pattern screening
cannot identify every possible personal name; callers must supply synthetic,
non-personal aliases.

`NativeBinding` accepts native `PublicationSelection`, `PublicationJournal`,
`RetryGuard`, `ReconciliationLedger` and native scope. Every operation reopens the
actual A08 package/files, journal, A15 custody and D05 source pin. D05 reconciliation
history and the selected latest record are checked. Native L07
`DecisionDashboardSnapshot` is rebuilt from those sources, and source replay is
checked again before returning. No adapter receipt is interpreted as a D05
observation or a publication fact. A08 user-supplied PUBLIC remains reported
evidence; the adapter's public visibility remains UNKNOWN.

The A07 native decoder and evaluator run over an explicitly read-only SQLite
connection, avoiding the existing registry's creating connection on reads.
Current evidence is checked even on replay. A07 ALLOW retains
`OFFLINE_RECORDED_NOT_LIVE_VERIFIED`; it never grants live rights. The live
entrypoint always returns `ENTITLEMENT_UNPROVEN`. It has no transport parameter,
provider client or credential surface. A Human opt-in marker is explicitly an
unverified proposal and cannot turn into authorization or execution.

The explicit offline simulator supports SUBMITTED, UNKNOWN, REJECTED and
CONFIRMED fixture states. Every receipt is `SYNTHETIC_FIXTURE`,
`provider_attested=false`, `public_visibility=UNKNOWN`,
`publication_status=UNKNOWN`, `real_post_created=false` and
`retry_dispatched=false`. Fixture confirmation retains the native D05 decision,
including RECONCILE_REQUIRED or NO_RETRY. Supplied NO_EFFECT cannot enable retry.

Replay custody is bounded, thread-serialized and **process-local only**. Identical
request/proposal/outcome replay returns retained bytes; changed payload/outcome
under a key and re-keyed media targeting are refused. This is no claim of durable
or distributed external-effect idempotency. There is no external effect path.
Receipts parsed from bytes must also match the issuing simulator's retained
custody before callback/projection use. Duplicate callbacks are idempotent holds;
conflicting IDs, re-keyed events and source substitutions reject. All callback
outcomes require reconciliation and never transition native or synthetic state.

The catalog defines distinct TikTok VIDEO, Instagram REEL and YouTube VIDEO
fixtures. Those are illustrative names and semantics, not assertions about
current provider production capabilities. Account/app namespaces, A07 action,
receipt namespace, metric vocabulary and denominators remain channel-specific.
YouTube uses the existing A07 API_UPLOAD action; it is not silently treated as an
A07 direct-post capability. Unsupported surfaces fail closed.

Multichannel projection never calls the simulator. Aggregation accepts typed
projections with native selectors and reopens custody instead of trusting archived
status dictionaries. Missing values stay null/UNKNOWN; explicit synthetic zero
stays zero. Comparisons require equal platform/account/app/surface/market/product,
metric definition/denominator/unit/visibility, measurement window/timezone,
attribution tier, currency and provenance. Cross-platform records are incomparable.
Count fixtures forbid currencies and financial vocabulary. Compatible supplied
synthetic counts may have a delta; reach is never summed. Revenue and settlement
remain null. A projection can be retained by its caller, but no new native ledger
or automatic platform execution is introduced.

## Source integrity and scope

Trusted immutable-main owner mapping was revalidated before writing:

| Native consumer | Owner | Baseline SHA256 |
| --- | --- | --- |
| `distribution/evaluator.py` | a07 | `d4c3b1cff3beac595da2edb4c781c6d5333c0754da88cc9260b07bdbd3ae0d66` |
| `distribution/model.py` | a07 | `e483c9859ad81be4b2f7948c1742e5fba00222ace22a0cea813b5ec2120ec497` |
| `distribution/receipt.py` | a08 | `ec3e59119acd6b45405e929317bf9da906fc4ac1e66526833b10def9842bb33d` |
| `distribution_intelligence/reconciliation.py` | a17-2 | `5229743abe9af0b68c35468f9df77c4ef202e93d5866f20f48e9e7b8e717413e` |
| `distribution_intelligence/dashboard.py` | a17-2 | `eb07677ee667b68c1f1fcb409819ccc250b81b0a1fe410b319db391c0cc00af0` |

Exactly one `MT01ComponentOwner.v1` manifest declares five sources, two tests,
two contracts, the five existing consumers and
`A17.D07.D08.offline-capability-gated-adapters`. No protected control, upstream
source, existing test, dependency or schema registry is changed. Work item and
report use the exact authorized paths. The Git write guard remains in place;
this executor does not publish a candidate, change Git refs or create a PR.

## Verification and delivery rules

The new tests create actual native A07 SQLite evidence, exported A08 package and
PublicationJournal, native A15 uncertain attempt, D05 reconciliation and L07
projection. E01 decoding alone is stubbed for labeled synthetic media; native
A08/D05 custody checks are not stubbed. Existing affected A08/E01 tests supply
their own real local media verification through the registered consumer closure.
Network/socket/HTTP calls, subprocess provider clients, model/provider execution,
A08 writers, A15 writes and D05 reconciliation writes are fail traps during
adapter operations. Mandatory native read-only file/SQLite operations and local
ffprobe reads remain available. Tests cover positive replay, native uncertainty,
independent supplied no-effect/confirmation, synthetic confirmation, stale/revoked
rights, scope/hash substitution, forged/re-keyed receipts and callbacks, UNKNOWN
collisions, cross-platform replay, metric context, missing/zero, malformed
contracts, secret/contact data and unchanged source custody.

`mt01-component-onboarding` is an ingress marker. The unchanged registered
controller must derive new-owner and affected legacy argv, execute self-hosted
Windows pytest with `-n 12`, and retain per-group results and independent
`MT01_XDIST_WORKERS_COLLECTED` receipts. Actual collection/execution and JUnit
counts must be reconciled, along with frozen file hashes, source/oracle integrity,
manifest admission, owner binding and exactly twelve changed paths. No historical
test count is evidence for this source. Test artifacts belong outside the
authorized source worktree. This report does not predeclare a verification PASS;
the new immutable receipt is authoritative. At most two same-attempt Verify-Fix
repairs are allowed. Deterministic mapping/coverage blockers and UNKNOWN stop the
attempt without broad-suite fallback or re-dispatch.

Only the independent EF01/EF02 worker may bind a verified source tree to a new
candidate SHA and publish CANDIDATE_BRANCH. Separate exact-PR-head hosted Ubuntu
and Windows CI/oracle/JUnit and independent Brain review remain required before
protected auto-merge. Hosted CI cannot establish live self-hosted Windows worker
counts. No candidate SHA, PR, review or merge is claimed by this source report.
