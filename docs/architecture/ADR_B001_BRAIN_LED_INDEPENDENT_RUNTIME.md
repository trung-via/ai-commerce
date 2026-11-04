# ADR B001 — Brain-led independent production runtime (PA2)

Date: 2026-11-04. Status: **ARCHITECTURAL DIRECTION HUMAN_SELECTED_PA2; IMPLEMENTATION DETAILS PROPOSED; B0.1 SOURCE ONLY**.

## Decision, exact source and permission

Human explicitly selected Option 2 in the current ChatGPT conversation on 2026-11-04 and authorized EF01/EF02 engineering to build/improve this design and re-research the sequence after Roadmap A. [WorkItem](../../work_items/B0-1-PA2-DESIGN-001.json) preserves that instruction as a supplied decision record, not provider attestation or independent Brain acceptance.

Required source: `trung-via/ai-commerce` main baseline **26398290f8c2440eaa383783dbdf84f8d95610ab**. Observed local `git rev-parse HEAD` matched exactly, `git status --porcelain=v1` was empty and all 12 allowlisted targets were absent before editing. The local commit message identifies merge PR #317. No remote-main freshness, branch protection, account entitlement or production permissions are inferred from that checkout observation. Independent EF02 must recheck source/base and candidate bytes; drift means HOLD, not an executor Git repair.

Authorization is ordinary add-only worktree source, schemas, tests and documentation. The executor may not commit/create refs/PR/merge or change protected workflows/config. Trusted EF02 alone publishes a verified candidate. Independent Brain PASS for exact HEAD/base precedes trusted auto-merge with Windows-only CI; **no second Human merge queue**. Existing Windows self-host tests use 12 workers per group, hosted CI uses 4. No production job, HostOps/CDP effect, secret read, provider call, deployment, external spend or publish is authorized.

## Alternatives

| Option | Evaluation | Decision |
|---|---|---|
| PA1: extend EF02's engineering DAG/watch/runner into commerce scheduling | Reuses engineering orchestration but couples routine product execution to coding agents, Git candidates and public engineering authority. Job retries and engineering repairs have different rights, evidence and cost semantics. | Considered, **NOT selected** |
| PA2: independent production coordinator consuming existing product modules | Keeps routine execution deterministic, separates production credentials/leases/receipts from build authority, preserves five semantic owners and current ChatGPT Brain. Needs a small durable coordination journal and private authorization verifier. | **Human selected architectural direction** |

EF01/EF02 remain the engineering build/repair mechanism. Codex/Antigravity implement changes and exceptional separately scoped repairs, not the normal per-journey creative reasoner or product scheduler. Product executor must follow a Brain directive; ambiguous strategy or creative changes return to Brain.

## Smallest implementation proposed

Windows-first Python process, manual/attended entry first. Dataclasses + existing `jsonschema` dependency provide a pure reducer. No new dependency, agent framework, background service, browser transport or SQLite is added in B0.1. The three draft schemas are exported from an in-memory definition so importing/validating/transitions do not read schema files or resolve remote refs. All wire authorization fields are false/zero; a schema label/digest is never authority.

B0.2 proposes SQLite coordination-only transactional **append-only intent/event journal**, one writer and a fencing lease. Suggested logical tables: immutable journey/directive refs; events `(journey_id, sequence, event_id UNIQUE, payload_sha, previous_sha, source_sha)`; effect reservations with unique semantic target/request keys; append-only lease-acquire/renew/release history plus a CAS current-lease projection. Journal event payload stores safe coordination data and **LINK BY SHA/REF ONLY** to owner evidence. Mutable indexes/materialized state are rebuildable views, not replacement truth. Never create a second authoritative A08 publication, A09 observation, A15 cost/retry or A16 settlement ledger.

Proposed write protocol:

1. Validate contract, source/directive/scope and trusted authority using explicit time; reopen owner refs via their native validators. Acquire lease with monotonically increasing fencing generation; CAS expected sequence in `BEGIN IMMEDIATE` transaction. SQLite has one writer; handle busy explicitly rather than spawning extra writers.
2. Commit immutable event and effect intent **before** any authorized external call. Unique event ID and request/semantic-target index reject duplicate and rekeyed effects. Persist provider/client idempotency key; the key includes exact approved content/account/action identity. B0.1 remembers keys only in the supplied snapshot; durable cross-process/cross-journey dedup is B0.2 work.
3. Release transaction, invoke exactly the separately authorized adapter once, then append observed result with provider request ID/ref/digest and fencing generation. A DB transaction cannot atomically commit a remote provider effect. Crash/lost response after reservation leaves `UNKNOWN_EFFECT`, never permission to submit again.
4. On restart replay journal/hash chain/sequence, reopen linked owner records and check lease generation. Missing/corrupt/rolled-back journal, split ownership or uncertain owner linkage → HOLD. Expired lease permits coordinated recovery, **not** duplicate effect. A stale process cannot apply results/effects using an old fencing generation; adapters without enforceable fencing must remain single-writer and require reconcile/attended handling before takeover.
5. Reconcile with exact provider request/client token and authenticated observed outcome. Confirmed means no retry. Proven no effect means HOLD and a newly reviewed attempt/grant, not automatic replay. UNKNOWN remains blocked. Append correction/reference events, never rewrite history to say the earlier call did not happen.

SQLite durability depends on storage behavior; choose journal mode/synchronous policy by measured Windows crash tests. Propose `synchronous=FULL` and explicit transactions initially; WAL is optional after tests justify it. DB checksums/hash chains do not protect against a malicious same-SID actor replacing both DB and anchors. Anti-rollback/custody require independent control before unattended production. Reconcile owner writes after journal crash using stable refs; do not claim cross-ledger atomicity or global exactly-once.

## State and event semantics

The reducer accepts a typed immutable snapshot, typed event, explicit logical time and **separate** `VerificationContext`; it returns a new snapshot or bounded rejection. It discovers no wall clock, environment, files, credential, network or callbacks. The future private worker must supply a trusted replayed snapshot; an arbitrary caller constructing a dataclass is not proof of journal history.

| From | Explicit event | To / guard |
|---|---|---|
| NEW | INPUT_ACCEPTED | INPUT_VALIDATED; closed CreativeDirective, digest/version/source/journey bound |
| INPUT_VALIDATED | START | RUNNING; declared tool allowlist; this is a pure model, no tool dispatch |
| RUNNING | OUTPUT_READY | TECHNICAL_QA; Media/Creative evidence refs |
| TECHNICAL_QA | QA_PASSED | WAIT_BRAIN; technical evidence, no creative strategy approval |
| WAIT_BRAIN | BRAIN_ACCEPTED | READY_FOR_HUMAN_RELEASE; exact directive/scope/version/source and verified current-chat Brain receipt |
| READY_FOR_HUMAN_RELEASE | OFFLINE_FINISHED | LEARNING_REVIEW; no external intent or release claim |
| READY_FOR_HUMAN_RELEASE | RELEASE_REQUESTED | RELEASE_INTENT; fresh Brain authority, separate Human GO_PUBLISH, actual provider entitlement, final asset rights, allowed tool, exact publish scope, separate GO_SPEND/currency/ceiling if nonzero |
| RELEASE_INTENT | EFFECT_UNCERTAIN | UNKNOWN_EFFECT; retain original key/request identity |
| RELEASE_INTENT / UNKNOWN_EFFECT | PROVIDER_CONFIRMED | RELEASE_CONFIRMED; authenticated provider observation binds key/request and **CONFIRMED** outcome |
| UNKNOWN_EFFECT | RECONCILED_NO_EFFECT | HOLD; independently verified NO_EFFECT observation, used key retained; no automatic retry |
| RELEASE_CONFIRMED | ANALYTICS_ACCEPTED | ANALYTICS_IMPORTED; Distribution owner evidence, original provenance retained |
| ANALYTICS_IMPORTED | REVIEW_LEARNING | LEARNING_REVIEW; Media/Creative evidence |
| LEARNING_REVIEW | LEARNING_RECORDED | COMPLETE; scope completion, not proof of customers/revenue/causality |
| Any nonterminal | HOLD / STOP | HOLD / STOPPED; unresolved effect identity/status retained |

COMPLETE, HOLD and STOPPED have no resume edge in B0.1. A future resume/revision/repair protocol needs its own reviewed scope. Unknown states/events, arbitrary target-state override, duplicate event IDs, non-idempotent replay, rekey during UNKNOWN and missing/unverified authority reject. Idempotent read/query/replay of journal can rebuild the same state; replay is not a second effect invocation. Reducer errors perform no mutation. Snapshot and evidence pins are immutable; an existing evidence hash/ref cannot be relabeled by a later event.

For real generation the final output SHA may be unknown at initial brief time. The initial creative directive can remain draft-only. Before release, Brain must review a new final directive/version binding final asset and exact action scope. **B0.1 does not implement directive revision or silently fill an approved null asset hash.** B0.2/B1 handoff must define that revision protocol.

The B0.1 effect-intent branch models **PUBLISH only**. START/RUNNING is never permission for a paid generation call. B1 must add a separately reviewed generation-intent/reservation and authorization protocol before connecting A04 submit to any real provider; the current pure reducer is not a complete generation-effect scheduler.

## Authority and evidence

`CreativeDirective.v1`: freeform creative and `product_ref=null` are valid; asset/rights pins, allowed tools/action classes, directive version/digest, source and exact scope. No SKU/storyboard/voice requirement. `BrainDecision.v1`: actor/provenance/source SHA, current-chat/receipt refs, directive digest/version, exact scope and approve/revise/hold/stop. Scope is DIRECTIVE_ONLY; it grants neither provider access nor publish/spend. `ProductJourneyReceipt.v1`: coordination state, effect identity/status and owner refs; `network_calls_performed=false` and `external_effects_performed=false` describe this pure foundation, never claim this module executed an observed provider effect.

Every object is closed JSON Schema 2020-12; no token, password, credential or raw-cookie fields. Opaque refs must point to separately controlled evidence. Freeform text is not automatically secret-safe: the private worker must redact/approve any public projection; schema validation alone is not a DLP system. JSON raw parser rejects duplicate keys, nonfinite values, invalid UTF-8 and complexity overflow. Hashes identify canonical bytes and are not signatures.

| Evidence type | Meaning | Authority ceiling |
|---|---|---|
| SYNTHETIC_FIXTURE | Deliberate test/simulation | Never Human/Brain/provider authority or customer outcome |
| HUMAN_SUPPLIED | User/attended transfer of a claim/export/receipt | Not provider verification; attended Brain identity still needs independent current-chat receipt verification |
| PROVIDER_ATTESTED | A claim with provider attestation ref | The label is untrusted until separate verifier authenticates issuer/exact bytes/scope/outcome/revocation |
| UNKNOWN | Missing/unverified origin | No positive authority inferred |

`AuthorityReceipt.verified` in `VerificationContext` is a **trusted-verifier input assumption**, not an implemented attestor, capability token, cryptographic verifier or API admission boundary. Defaults are unverified. Event/JSON never contains or controls this context; fixtures and provider labels cannot make it verified. Tests may construct hypothetical verified contexts to exercise the model and **do not establish actual Brain, Human or provider approval**. A malicious caller allowed to create arbitrary context/snapshots can lie; production security must enforce the private verifier and journal boundary, which is intentionally not implemented here. B0.1 must never be exposed as an authorization service.

Future verifier must authenticate distinct principals and exact decision/grant/entitlement/rights bytes, source SHA, journey ID, directive digest/version, account/provider/surface/content scope, action classes, TTL and revocation. Provider observation additionally binds effect key, request digest and actual CONFIRMED versus NO_EFFECT outcome. Separate Human publishing grant and provider entitlement remain necessary even for free posting. A positive Human budget/spend grant is separate from strategy and publishing; journal must atomically reserve budget across journeys, currency-specific, before paid adapters. Unknown charges/counters stay unknown and reserve exposure until reconcile; no implicit GO_SPEND/GO_PUBLISH.

## Three explicit trust boundaries and public/private handling

1. **Public source CI:** public GitHub code/PR/issues/artifacts are untrusted input for production. Windows-hosted source tests use synthetic/redacted data; no provider tokens, private Brain text, customer/analytics raw data, signing keys or private-control credentials in public artifacts/issues. Public CI PASS proves source tests for its exact bytes only.
2. **EF02 coding runner:** may execute scoped code/build/test and trusted worker may publish an independently verified Git candidate. This is engineering authority, not commerce authorization. A public-repository self-hosted runner sharing the Windows SID can compromise same-user files/process material; do not give it production credentials, private control token, signing key or production authority creation rights.
3. **Future private production credentials/worker:** separate private ingress, evidence storage, verifier/policy and credential custody; independent trust enrollment/source pin/revocation. Prefer attended ephemeral operation until an externally isolated broker is actually demonstrated. No extra Windows account or secure same-SID isolation is claimed. A repo name “private” alone does not make its token safe on the public runner.

Use HostOps [W1 signed contract](../engineering/HOSTOPS_W1_SIGNED_CONTRACT.md), [W1.1/W1.2 custody/intent](../engineering/HOSTOPS_W1_PHASE2_SOURCE_HANDOFF.md), [native fixture ceiling](../engineering/HOSTOPS_W1_NATIVE_PROOF_HANDOFF.md) and [attended setup](../engineering/HOSTOPS_W1_ATTENDED_SETUP.md) as explicit constraints: public source approval/read proofs do not activate writes; per-effect independent authorization and custody remain separate. **No HostOps/CDP/Wake effects** are performed or enabled by this ADR. Do not post private data or tokens into public GitHub feedback to bridge the boundary.

## Framework and external project decisions (researched 2026-11-04)

| Technology / primary source | Evidence read and inference | PA2 decision |
|---|---|---|
| [SQLite transactions](https://www.sqlite.org/lang_transaction.html), [atomic commit](https://www.sqlite.org/atomiccommit.html) | Transactions support one concurrent writer; durability has filesystem/storage assumptions | Smallest B0.2 coordinator; crash/lease/UNKNOWN tests required, not an authority/commerce truth store |
| [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | Interrupt/checkpoint/resume; node code may rerun and pre-interrupt side effects need idempotency | Optional **adapter boundary for interrupts/checkpoints only** after measured branching/agent graph complexity; no dependency now, no strategy/authorization owner |
| [Temporal Python](https://docs.temporal.io/develop/python) | Workflow, activity and worker service architecture | Deferred until real async concurrency, long-running waits and recovery/operations measurements warrant service complexity; async provider alone does not mandate it |
| [OpenMontage](https://github.com/calesthio/OpenMontage) | YAML stage manifests, Markdown stage skills and tools; coding assistant orchestrator; AGPL-3.0 | Selective tooling/stage-contract ideas only. Do not adopt coding-agent-per-video architecture. No AGPL code/skills/source copied/imported without separate license/provenance/dependency assessment |
| [Postiz](https://github.com/gitroomhq/postiz-app) | SaaS/self-host publishing product with API; provider app/analytics approvals remain relevant | Future **separate SaaS/self-host adapter**, initial proof draft-only. No deploy/install/token use, no claim live publishing works |
| [Ruflo](https://github.com/ruvnet/ruflo) | Multi-agent coordination/swarm features described upstream | Inspiration only if measured benefit exceeds coordination/quota cost and separately approved; no automatic swarm/hooks/daemon |
| [GitHub secure use](https://docs.github.com/en/actions/reference/security/secure-use) | Public self-hosted runners do not provide clean persistent isolation; workflow token/secret least privilege matters | Public engineering and private production trust/credential boundary must be independently demonstrated |

These are primary-source research observations and **design inferences**, not AI Commerce compatibility, pricing, license clearance or performance proofs. Future adoption must pin actual version/commit and review rights/dependencies; a moving README is not a source transplant authorization.

## Validation, rollout and reversal

Minimum sufficient B0.1 tests: closed schemas/unknown fields, canonical digest/provenance, null product, no I/O/network/process/SQLite calls, known/unknown states/events, no false Brain approval, separate publish/spend/provider/rights inputs, deterministic transitions, invalid/replayed/UNKNOWN effects, outcome-bound reconciliation. Run owner and registered read-only consumer closure with observed Windows self-host 12 workers per group, retain nonzero/UNKNOWN receipts, max two repair rounds. Independent EF02 re-verifies candidate; later Windows hosted PR CI uses 4, then genuine independent Brain review exact HEAD/base. No source report claims post-merge acceptance.

Next cut B0.2 must implement SQLite/fencing/crash protocol separately with default effect-disabled adapters and journal corruption/rollback/duplicate/lease takeover tests. B0.3 adds independent attended worker and WAIT_BRAIN ingress only after verifier boundary review. B1–B4 are conditional per [Roadmap B](../roadmap/ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md).

Rollback before activation: stop new worker intake, keep journal/owner refs and unresolved effects, use manual draft handoff. After any authorized external effect, “rollback” requires actual provider reconciliation and a separately authorized compensate/remove/refund action; deleting local state cannot undo external effects. No production service is activated by B0.1, so source rollback is a later reviewed engineering change through the existing trusted pipeline.
