# ADR D5-002 — Origin-Affine Workflow Bridge v1 (AI Commerce)

**Date:** 2026-11-06 (Asia/Ho_Chi_Minh)  
**Authority:** Human — build a reusable origin/workflow/return mechanism; **CURRENT LIVE ACCEPTANCE STOPS AT ONE AUTOMATIC SEND CLICK IN CHAT A**.  
**Status:** \`HUMAN_SCOPE_SELECTED / RESEARCH_RECONCILED / REUSABLE_CONTRACT_SOURCE_PROPOSED / ORIGIN_FIRST_LIVE_NOT_PROVEN\`  
**Tracking:** [#489](https://github.com/trung-via/ai-commerce/issues/489); send-only [#482](https://github.com/trung-via/ai-commerce/issues/482); current 9444 source [PR #483](https://github.com/trung-via/ai-commerce/pull/483).

## 1. Distinct goals and evidence levels

The **architecture** MUST support a future flow initiated by the Human in Chat A, verified there before work/event dispatch, with any later independently verified completion returning only to that exact originating Chat A. Chat A is a conversation route, not an EF02 batch, Roadmap task, plugin invocation or PULSE semantic owner. One route can carry multiple independent workflows and one workflow can have separately authorized continuation; route changes must never silently transfer already attempted delivery.

**The first live test** deliberately stops after \`ORIGIN_A_ATTESTED → TEST_COMPLETION_VERIFIED → D2_WRITE_AHEAD_UNKNOWN → D4_ONE_SEND_CLICK → UI_USER_TURN_OBSERVED\`. It MUST NOT auto-invoke Brain, Engineering, PULSE, plugin, next task, model response processing or any business/provider action. A reply generated normally by ChatGPT after Send is outside the Wake adapter's authority.

Separate \`SOURCE_PRESENT\`, \`SYNTHETIC_TEST_PASS\`, \`WINDOWS_CDP_READY\`, \`ORIGIN_A_LIVE_PROVEN\`, \`EVENT_LIVE_VERIFIED\`, \`SEND_CLICK_UI_WITNESSED\`, \`SERVER_RECEIPT_VERIFIED\` (not currently available). Never infer a later level from an earlier one.

## 2. Primary-source evidence and actual previous status

| Evidence | Verified implication | NOT established |
|---|---|---|
| AI Commerce [WorkItem EF02-EXACT-CHAT-WAKE-002](../../work_items/EF02-EXACT-CHAT-WAKE-002.json), [D1](../engineering/EF02_D1_COMPLETION_AND_BINDING.md), [PR #94](https://github.com/trung-via/ai-commerce/pull/94) | Per-conversation route and per-batch claim identity, passive immutable completion event | A trusted issuer for the exact initiating chat |
| AI Commerce [D2](../engineering/EF02_D2_TRUSTED_ORIGIN_AND_WAKE_ADAPTER.md), [PR #105](https://github.com/trung-via/ai-commerce/pull/105) | LocalOriginRegistrar/OriginAuthority; private per-chat registry; intent persisted before browser effects | A live Chat A capture from an initiating ChatGPT message |
| AI Commerce [#179](https://github.com/trung-via/ai-commerce/issues/179), [PR #483](https://github.com/trung-via/ai-commerce/pull/483) | UI turn observed in previously enrolled chat; current D4/D5 9444 \`send_only\` source passed CI | Origin-A→A dynamic first-message full live, independent ChatGPT server ACK |
| AI Commerce [#475](https://github.com/trung-via/ai-commerce/issues/475), [#478](https://github.com/trung-via/ai-commerce/issues/478), [#479](https://github.com/trung-via/ai-commerce/issues/479) | CDP 9444 protocol, two Playwright attaches, post-attach persistence | Private route/enrollment/signing, current ChatGPT selectors at Send |
| AIOS [H4 Origin-Affine](https://github.com/trung-via/AIOS-renew/blob/main/docs/AIOS-H4-ORIGIN-AFFINE-UNATTENDED-WAKE-v1.md) (2026-10-06) | Four **Human-observed live feasibility** cases for exact opaque-token account search: unique same chat, cross-device index delay then exact match, absent token, copied token ambiguity | Automated production resolver, independently verified origin-authoring authority or full origin-to-return lifecycle |
| AIOS [H4C0](https://github.com/trung-via/AIOS-renew/blob/main/docs/AIOS-H4C0-ORIGIN-CAPTURE-CONFORMANCE-v1.md), [origin_bootstrap.py](https://github.com/trung-via/AIOS-renew/blob/main/src/aios_renew/origin_bootstrap.py) | Page-scoped one-trusted-gesture, nonce/document proof, guarded submit and no-blind-resend source | One-command cross-device enrollment, universal current ChatGPT DOM compatibility |
| [OpenAI Plugin Reference](https://developers.openai.com/plugins/reference), [Plugin Extensions](https://developers.openai.com/plugins/build/extensions) (checked 2026-11-06) | Host tool calls may include anonymized \`_meta["openai/session"]\` for **correlation**; extended sending supports current/new chat in specified platforms | A supported API returning a conversation URL/UUID from the session ID, or unattended sending to any arbitrary prior chat |
| [Playwright connect_over_cdp](https://playwright.dev/python/docs/api/class-browsertype), [Chrome remote debug profile](https://developer.chrome.com/blog/remote-debugging-port) | Chromium attach supported but lower fidelity; Chrome 136+ requires nondefault user-data dir | Browser process ownership, authentication, or exact chat identity from TCP connectivity alone |

**Critical distinction:** AIOS one-message opaque rendezvous has **passed the four-case Human feasibility probe**. It was not fully productionized and is not imported into AI Commerce as code, profile, credential, or permission. This ADR REUSES its proof protocol, not historical runtime state.

## 3. Selected architecture: one-command origin, transport-neutral continuation

\`\`\`
Chat A: HUMAN WAKE START (one Human message)
      │ ChatGPT tool invocation via future private origin carrier, if available
      ▼
Origin Carrier — precommit random 256-bit single-use rendezvous marker
      │ returns exact token to the SAME ChatGPT assistant turn; private pending record
      ▼
Origin Resolver on Chrome CDP 9444 — bounded exact-match search and re-proof
      │ 0 / >1 / incomplete results -> HOLD; indexing lag -> bounded same-token wait
      ▼
D2 Private Origin Registrar — route_id + generation + proof receipt
      │ Chat URL/SID/account never enter public GitHub/event contract
      ▼
Workflow Binding — opaque origin_ref + workflow_namespace/work_id
      │ chooses FUTURE workflow owner; NOT selected by Wake transport
      ▼
Authorized Worker (EF02 / PULSE / another future runtime)
      │ authentic immutable completion + exact work lineage
      ▼
Completion Verifier — evidence digest, actor, attempt/source/owner
      ▼
D3 scoped delivery grant -> D2 durable one-shot effect intent
      ▼
D4/D5 attach 9444, exact Chat A, one Send click, exact user-turn witness
      ▼
STOP  ← current live test boundary
\`\`\`

**Front door A — preferred:** \`OPAQUE_ORIGIN_RENDEZVOUS_V1\`. A *trusted* carrier must generate and precommit a high-entropy token BEFORE the assistant renders the token in the same Chat A. It must not be selected by the model, a GitHub Issue, a copied chat URL, a recent tab or host memory. The future carrier can optionally correlate the tool invocation with \`openai/session\`, but this opaque host value is neither a Chat URL nor proof of a matching browser page. The current AI Commerce plugin is **skills-only**; no native precommit/secret-custody entry has yet been independently deployed and observed. Do **not** pretend the user's \`@AI Commerce\` text alone proves an origin.

**Fallback B — separately approved:** AIOS-style page-scoped in-document Human gesture with nonce, exact route/document identity and durable \`ATTEMPTING\` before a click. A click is another Human action; do not describe it as the same one-message UX. Use it if one-message carrier/search cannot be proven. Do not silently fall back from A to B or automatically navigate unrelated tabs.

**Reject:** using the most recent/focused ChatGPT tab, repo default chat, loose keyword semantic search, timestamp matching, assistant output to select a workflow action, fabricated completed EF02 event or untrusted public GitHub Issue as a signing authority.

## 4. Identity and ownership boundaries

There are FIVE independent identifiers:

1. \`origin_handle\` — opaque, conversation-scoped route selector (private proof mapping). No URL in public contracts.
2. \`generation\` — monotonic version of Human-approved route ownership. Old attempted effects remain pinned.
3. \`work_namespace + work_id\` — EF02 batch, PULSE journey or future task identity. This is **not** the chat route; many works may share one origin.
4. \`event_ref\` — immutable completion identity that a **work owner** emits and a separate verifier authenticates.
5. \`delivery_key\` — event + bound route/generation content-free dedupe, consumed before browser click.

\`OriginRegistrar\` owns private route/generation and \`bind_claim\`; EF02's existing D1 deterministic completion format only applies to actual EF02 events. Future PULSE or other owners must have their **own** genuine completion verifier/adapter, then translate to a closed, content-free return ticket. Do not fabricate EF02 batch status to force a Wake. Never make the Wake adapter a scheduler, semantic authority or second PULSE ledger.

**Private:** exact chat URL, account identity, browser profile, session/cookies, raw marker, local signer, attestation material, chat DOM/text.  
**May leave local trust boundary only after redaction:** random opaque route/work/event refs, hashes, bounded status and evidence refs. A 256-bit token in a PUBLIC GitHub Issue is not a private precommit.

**Concurrency:** per-origin route + generation lock around an irreversible Send; independently queued work results may coexist. Pending-not-attempted work may follow only an explicitly reauthorized route generation. After \`UNKNOWN\`/click, destination stays pinned and no automatic retry or route rebind. Human draft/generation in Chat A overrides automation and returns HOLD without clearing user content.

## 5. Reusable bridge contract: structural only, not authority

\`OriginReturnTicket.v1\` is a **data-only draft** that bridges an independently attested origin and a verified work completion without carrying provider private data. It requires \`route_id\`, \`generation\`, \`origin_proof_sha256\`, \`work_namespace\`, \`work_id\`, \`event_ref\`, \`completion_evidence_sha256\`, \`source_sha\`, \`delivery_key\`, \`delivery_mode=SEND_ONLY\`, \`authorization=NOT_VERIFIED\`, \`effect=NOT_ATTEMPTED\`.

Closed JSON schema and pure builder/validator enforce grammar, canonical digest, bound route/work/evidence refs, no raw URL or arbitrary extra keys. **They cannot verify** a real account, consent, source authenticity, registered route, signed grant, ChatGPT login, completed GitHub run, or browser Send. Runtime must call the existing real D1/D2/D3 evidence validators/registrar and only then transition into a separately scoped delivery intent. No new duplicate event journal is introduced by this contract.

Later consumers may recognize the same ticket across EF02, PULSE, or future workflows, but no adapter is enabled by the ticket alone. Workflow owner and Human policy, not the Wake payload, decide subsequent semantic work.

## 6. State machine and explicit no-effect boundaries

| Phase | Evidence required | Effect permitted |
|---|---|---|
| \`REQUESTED\` | Human initiating command is observed in Chat A | Create only a private *pending* origin request |
| \`RENDEZVOUS_PRECOMMITTED\` | Carrier created, durably stored and scoped one opaque marker | Render only that marker once in the same assistant response |
| \`ORIGIN_ATTESTED\` | Unique complete exact-token lookup, opened-result re-proof, stable route, correct browser owner | Local D2 \`register_route\` only |
| \`WORK_BOUND\` | D1 claim + explicit authorized workflow owner/work_id | Dispatch owner-specific work **later**, never by the Wake transport |
| \`COMPLETION_VERIFIED\` | Real owner-specific immutable completion + independent authenticity/evidence validation | Propose one bounded Wake return ticket |
| \`INTENT_RECORDED\` | D3 exact grant, D2 write-ahead idempotency store, chat route still valid | Exactly one D4/D5 UI Send attempt |
| \`UI_USER_TURN_OBSERVED\` | Exact DOM witness post-click in same Chat A | **STOP** current test; report UI only, not server ACK |
| \`UNKNOWN_EFFECT\` / \`HOLD\` | Any missing/ambiguous step including crash-after-intent | Never retry or redirect without exact reconciliation and new authority |

**No unsupported synthetic completion:** a local dummy button, GitHub tracker issue or source test cannot be the authenticated \`COMPLETION_VERIFIED\` event. For first Send-only live acceptance, a minimal *separately defined test event producer* is acceptable only if it is explicitly marked \`TEST_ONLY\`, signed/locally attested, non-EF02, has durable once-only state, and cannot dispatch a workflow. It must not use a fake EF02 SHA or successful batch result.

## 7. Verification matrix — current live stops at Send

**Offline source/negative tests:**
- A vs B same-repo independent route; two workflows in A map back to A.
- Host session correlation stable in a conversation but no URL derivation or authority.
- Exact nonce unique versus copied token, absent token, incomplete search; bounded indexing delay; account/profile mismatch and stale generation.
- Source provenance mismatch, forged \`COMPLETION_VERIFIED\`, GitHub issue body choosing an arbitrary chat, missing grants and duplicate event -> HOLD.
- Two independent results for same route; one draft/busy chat blocks without content mutation; no duplicate click on \`UNKNOWN\`; control plane crash before/after intent.
- Ticket schema rejects URL/cookies/account ID/unknown fields, fake authorizations and mutable status; no network/browser/exec in pure builder.

**One real end-to-end Send-only acceptance (CURRENT GOAL):**
1. Human sends \`@AI Commerce WAKE START\` **in Chat A on the authenticated 9444 profile**, and the carrier proves the request belongs to exactly that chat; if the new carrier is not installed, the result is \`HOLD_ORIGIN_CARRIER_UNAVAILABLE\`, not a fake positive.
2. An actual local evidence receipt confirms one unique full token re-proved in Chat A and a private D2 route created **before** any work/test event.
3. A new independently authenticated, test-only completed event is bound to A. A second Chat B, fake event, ambiguity or stale proof must never be selected.
4. An explicit already authorized one-shot Send-only grant and D2 intent precede the click. Exactly one D4 button.click() and matching user turn in A; check no user turn in B. The user can read the actual text. Store the UI receipt and preserve ambiguous D2 \`UNKNOWN\` after click.
5. **STOP**. No automatic Brain reasoning, Engineering task, plugin call, PULSE workflow, response observation or downstream command.

The Human has already approved the *goal* of one Send-only live acceptance. The missing native origin carrier/route/proof/effect permission cannot be waved into existence by this ADR. Do not request passwords, cookies or private URLs in a public Issue.

## 8. Incremental implementation and roll-out gates

- **F0 — reusable contract/architecture:** current source cut. Add closed ticket schema, pure fixture-validated builder and D1 consumer tests, this ADR. No local/browser/host writes or Wake; independent Windows CI + Brain source review.
- **F1 — origin issuer:** extend D2 registrar with an independently authenticated private carrier and CDP 9444 exact-token resolver. Probe real adapter capability before editing DOM. Source tests for duplicates/zero/partial result and eventual consistency. Preserve private boundaries, no ChatGPT Send.
- **F2 — work-agnostic correlation + genuine completion:** opaque ticket bridges EF02 and PULSE owner refs without changing their event truth, grant ownership or ledger. Run/attempt/receipt verified by each owner. Current live trial uses distinct controlled test-only producer.
- **F3 — single live Send-only acceptance:** source HEAD and actual native HostOps/Chrome receipts, private binding and grant; one Send click and user-turn witness; STOP. Record failures without auto retries.
- **F4 — future workflow:** separate Human design decision later selects Brain/Engineering/PULSE journey orchestration. This ADR prepares identifiers and interfaces; it neither enables nor prescribes that flow now.

## 9. Tradeoffs, blockers and reversibility

- **Opaque rendezvous** best matches one Human command and cross-device future; depends on a real private plugin/carrier entry plus a complete or fail-closed ChatGPT search/index UI. AIOS observed the four Human feasibility cases, but automatic 9444 resolver is still unproved and UI may drift.
- **Host \`openai/session\` metadata** is useful for session-local correlation, but unsupported for deriving exact chat URL and not present simply because a skills-only plugin is mentioned. Never use it as sole route proof.
- **Page-scoped Human gesture** avoids account-history search and is easier to prove locally, but adds a click and requires Chat A to be open in the same Chrome profile. Select only as an explicit alternative after evidence.
- **Playwright CDP** reuses existing live 9444 proof, but is lower fidelity than native Playwright; keep exact DOM predicates, authentication checks, bounded reads, and fail-closed selectors.
- **Rollback** of this F0 cut is remove only its data-only contract/docs/tests; existing D1/D2/D3/D4/D5 and all private idempotency/history remain untouched. After F3 a clicked or ambiguous event is never “rolled back” or automatically resent.

**Non-negotiable functional requirement:** enable future workflow modularity, **but the present acceptance ends at automatic UI Send into the originating Chat A**.
