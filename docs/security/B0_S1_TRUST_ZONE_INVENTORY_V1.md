# B0-S.1 trust-zone inventory v1

Status: **SOURCE_ONLY / OFFLINE_MODEL / PRIVATE_VERIFIER_NOT_ENROLLED**.
Implementation baseline: `trung-via/ai-commerce@0c9753f873ce8aaf758a691664c684c624a8d305`.
Human authorized bounded source preparation on 2026-11-04; the supplied decision
and research are recorded in [Issue #356](https://github.com/trung-via/ai-commerce/issues/356).
This is a research reference, not authenticated Human or Brain transaction proof.

The consumer authority is the pinned [PA2 roadmap](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/docs/roadmap/ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md)
and [ADR B001](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/docs/architecture/ADR_B001_BRAIN_LED_INDEPENDENT_RUNTIME.md).
Their historical baseline descriptions remain historical. The source of this cut
is the SHA above, not a claim about remote main after that observation.

## Domains and evidence ladder

| Domain | Actor / inputs / custody boundary | Observed evidence and ceiling |
|---|---|---|
| Z0 public GitHub, issues, source and CI | Public authors, owner account, GitHub connector, Actions; code and sanitized test artifacts | OBSERVED_SOURCE at the pinned checkout; source review and CI are engineering evidence. Issue text, owner comments, artifacts and hashes are untrusted product-authority inputs. |
| Z1 EF01/EF02 and Windows coding runner | Scoped executor and trusted candidate worker; engineering processes under the existing Windows account | Source config routes EF02 using its engineering label. Three runner instances are configuration/prior operational evidence, not three security principals. Prior selfhost receipts do not attest current private custody. |
| Z2 PULSE attended offline | Native B0.1 contracts/reducer, B0.2 journal, B0.3 local adapter, B0.4 operator | OBSERVED_SOURCE and prior bounded Windows test receipts in the B0.3/B0.4 handoffs. Live PULSE service, current journeys and unattended process state are **UNKNOWN**, not inspected here. Shares a possible SID with Z1; no isolation claim. |
| Z3 future independent private custody/verifier | Separately enrolled issuer/verifier, revocations, replay and budget register; future provider credential custody outside compromised Z1/SID | **NOT_ENROLLED / NOT_DEMONSTRATED**. No private Brain attestor exists. No key, credential, service, broker or transport is created by this cut. |

```text
Z0 public source / Issue / CI
    -> Z1 scoped engineering bytes and tests
    -> Z2 attended local draft + sanitized manual information handoff
    -X-> Z3 future independently enrolled private verifier / effect custody
         -X-> provider / account / surface / money
```

An arrow transfers data, never principal authority. A source-only PASS cannot
cross either blocked edge. Same-SID ACLs, DPAPI, Credential Manager, runner Online
or an ignored/private directory do not independently isolate hostile same-user
code. No secret discovery, disk inventory, user-path inventory or credential
inspection was performed. This inventory names repository interfaces only.

## Entrypoints and exposure paths

All links below are exact baseline source, not active permission grants.

| Entrypoint | Source / actor / intended effect | Data, exposure and authority ceiling |
|---|---|---|
| `issue_ingress` EF02 | [issue-ingress.yml](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/.github/workflows/issue-ingress.yml), public Issue into engineering lane | LOW: source SHA, bounded WorkItem, tests and counts. HIGH prompts or credentials can leak via body/comments/logs/artifacts; forbid them. An Issue is not a PULSE command or BrainDecision. |
| Trusted auto-merge | [brain-pass-auto-merge.yml](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/.github/workflows/brain-pass-auto-merge.yml), [merge gate](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/.github/scripts/pr-merge-gate.py) | GitHub exact-HEAD/base source review + independent CI only. Neither merge nor Brain PASS authenticates a private-origin strategy receipt, transaction consent, provider entitlement or spend grant. No merge action here. |
| HostOps Personal READ | [personal.py](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/hostops_v3/personal.py), [Personal contract](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/docs/engineering/HOSTOPS_PERSONAL_DIRECT.md) | Approved fixed READ operations return public Issue receipts. Public committed text and bounded digests differ from private raw files. No HostOps READ requested here. |
| HostOps Personal WRITE | Same Personal source and ingress; exact fresh owner comment for one existing fixed operation | Source documents a connector/owner-account overlap: owner comment is not independently authenticated Human consent. Personal host operations confer no commerce permission. No WRITE, approval comment or signed request here. |
| Historical W1/v1/v2/v3 signed HostOps | [legacy retirement](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/docs/engineering/HOSTOPS_LEGACY_RETIREMENT_20261104.md) | Historical schemas, tests and signer receipts are audit material. Legacy Issue routes are retired. Never import their identity or treat retained signed source as active authority. |
| B0.1 contracts/state | [contracts.py](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/production_runtime/contracts.py), [state_machine.py](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/production_runtime/state_machine.py) | Closed draft structures and pure transitions. Hypothetical VerificationContext fixtures are not an enrolled verifier or a release service. No frozen schema changes. |
| B0.2 journal/recovery | [journal.py](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/production_runtime/journal.py), [recovery.py](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/production_runtime/recovery.py) | Coordination refs, leases, intents and UNKNOWN; no second business ledger. Same-SID replacement of journal and anchors defeats a local consistency claim. Preserve keys and HOLD; do not retry. |
| B0.3 native local adapter/receipt | [attended_worker.py](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/production_runtime/attended_worker.py), [receipt.py](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/production_runtime/receipt.py) | One reviewed local A06 still-image tool, native source pin and local rights/path checks, WAIT_BRAIN. Private directive/media stays local; receipt projection must be sanitized. Offline MP4 is not provider generation. |
| B0.4 CLI/operator/handoff/metrics | [operator_cli.py](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/production_runtime/operator_cli.py), [operator.py](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/production_runtime/operator.py), [handoff.py](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/production_runtime/handoff.py), [metrics.py](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/production_runtime/metrics.py) | Manual same-chat information transfer, technical QA and local counters. No authenticated Brain ingress. Logs, filenames, freeform text and handoff media can expose HIGH data. Metrics explicitly cannot prove rollback resistance against replacement of both trail and anchors. |
| Future provider adapter | [media generation](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/media/generation.py), [assisted distribution](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/distribution/assisted.py), [direct_post.py](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/src/ai_commerce/channel_adapters/direct_post.py) | Remote generation, posting, analytics and finance require separate exact action rights. Existing seams/synthetic adapters do not prove a configured provider, live entitlement, credentials, actual post or paid effect. B0-S does not connect them. |

Prior [B0.3 handoff](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/docs/engineering/B0_3_ATTENDED_WORKER_HANDOFF_REPORT.md)
and [B0.4 handoff](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/docs/engineering/B0_4_ATTENDED_OPERATOR_HANDOFF.md)
are prior receipt evidence, not a fresh host observation in this cut.

## Confidentiality and semantic ownership

LOW means eligible for deliberate redacted publication, not automatic export:
public repository-relative paths, reviewed source SHA/digests, synthetic fixture
IDs, finite reason codes, aggregate test counts and source-only stage labels.
Even hashes/opaque identifiers may correlate private transactions; omit them
unless specifically reviewed for public release.

HIGH includes credentials, cookies, signing material, recovery codes, private
account identity, private paths, raw prompts/Brain conversation, unpublished
media, asset/customer/analytics inputs and unredacted provider/financial receipts.
Possible exposures: Issue/PR/comment text, logs/tracebacks, argv/env, filenames,
artifact bundles, metrics and manual handoff. No HIGH examples or real locations
are collected here; fixture values contain no real secrets.

The [five semantic owners](https://github.com/trung-via/ai-commerce/blob/0c9753f873ce8aaf758a691664c684c624a8d305/docs/engineering/SEMANTIC_OWNERS.md)
retain authoritative A-series evidence:

| Owner | Authoritative evidence | PULSE ceiling |
|---|---|---|
| Commerce Opportunity | A10/A18 opportunity, market/portfolio hypothesis | Ref/SHA and review request; no demand assertion |
| Product Intelligence | A12 identity, claim/offer/asset-rights evidence and freshness | Ref/SHA; never upgrade stale/revoked rights |
| Media & Creative | A03–A06/A11/A17.1 directive/assets/QA/learning meaning | Directive/version/digest and artifact refs |
| Distribution | A07/A08/A09/A14/A17 account eligibility, post and raw observations | Publication/analytics refs; exported != submitted != confirmed |
| Commerce Operations | A15 retry/cost and A16 settlement evidence | Coordination/reservation refs; UNKNOWN charges remain UNKNOWN |

Engineering Foundation builds source; it is not a sixth commerce truth owner.
PULSE stores refs/SHA only and does not create a duplicate authoritative ledger.

## T01–T14 deterministic source-model outcomes

| ID | Threat | Required result / remaining live gap |
|---|---|---|
| T01 | Forged Issue/CI `verified`, BrainDecision or approval | Closed unknown fields reject; recognized external action always HOLD |
| T02 | Same-SID coding runner claims DPAPI/private-key isolation | No custody inference, no key provision; external HOLD |
| T03 | Duplicate nonce/request or rekey after UNKNOWN | Local comparison fixture HOLD; future durable independent replay register required |
| T04 | Changed source/journey/directive/version/asset/action/account/surface | Local scope mismatch HOLD/reject; all external actions HOLD |
| T05 | Stale, expired, revoked rights/issuer/consent | No external release; local rights window/pins checked; real issuer revocation not implemented |
| T06 | Unverified provider-attestation label or synthetic receipt | Label is data; no authenticated observation or provider result |
| T07 | Corrupt/rolled-back journal and anchors or clock rollback | Marker fixture HOLD; source tests do not prove adversarial host resistance |
| T08 | Secret/cookie/raw-data exposure through public export | Closed drafts, finite data-free decisions, synthetic canary tests; future public export review and real issuer revocation separate |
| T09 | Source Brain PASS / Personal owner WRITE reused as product grant | Reject cross-domain authority; no commerce grant |
| T10 | Submit succeeded but crash lost response | UNKNOWN retained with original token/key; no retry or new key |
| T11 | Expired/revoked/rotated signing key replayed | No enrolled root today; future key/epoch/replay checks required before any effect |
| T12 | Human sees different caption/video/target from execution | Bind exact final bytes and transaction digest; future independent WYSIWYS check |
| T13 | Prompt/media tells worker to approve or publish | Treat as data; no authority fields, commands or plugin loading |
| T14 | Offline network absence or mocked all-grants-true context | **Zero possible ALLOW_EXTERNAL results**; HOLD_PRIVATE_VERIFIER_NOT_ENROLLED |

## Public publication allowlist

Only reviewed/redacted source, these design docs, data-only WorkItem/owner
manifest, synthetic tests, source hashes, finite decision reasons, aggregate
counts, actual worker collection hashes and explicit failure/skip/UNKNOWN records
may be proposed for public publication. No automatic artifact upload exists here.
Exclude raw intent/directive/native session, private file/user paths, env/argv
dumps, tokens, signed requests, raw provider/customer records and media. Do not
paste a whole fixture/debug bundle merely because a parser accepted it.

S.3 actual isolated enrollment/custody and S.4 live tamper/penetration/revocation
proof remain separately authorized future gates. See the [authority protocol](B0_S2_AUTHORITY_PROTOCOL_V1.md)
and [source handoff](../engineering/B0_S1_S2_SOURCE_HANDOFF.md).
