# A03/A04 Upgrade v1.1 — Engineering Handoff (approved to implement)

Canonical Human decision: `docs/governance/HUMAN_DECISION_2026-10-29_CREATIVE_V1_1_A05.md`.
Source `main` baseline: `4dcaf5b60ca5d7b389953b4336fcbe8f21bf07d8`. Supersedes **research/draft** status for the scoped implementation only; original v1.0 records remain valid and readable.
Detailed research basis: `AI_COMMERCE_A03_A04_UPGRADE_RESEARCH_DRAFT_V1_1_2026-10-29.md` provided in conversation on 2026-10-29 (not an existing repo document).

## A03-U — Creative capability with backward compatibility
Keep original `ContentBrief`, `intake_brief`, `CreativeStore`, `ConceptRecord`, `ConceptSelection`, `generate_concepts` and `MockCreativeReasoner` V1 APIs and serialized identities unchanged.
1. Add optional `CreativeIntent` sidecar preserving original text and differentiating user-supplied constraint versus model-inferred hypothesis; `unknown` is allowed.
2. Add `CreativeReasonerV2` or equivalent new opt-in adapter protocol: explore (divergent vs controlled_variants), reframe, develop, optional critique. No fixed count requirement and **controlled variants may share family/mechanism**; exact identical output can still dedup. Existing v1 generator stays unchanged to preserve tests.
3. Distinguish `AUTO_PINNED` from real `HUMAN_SELECTED` and `AI_RECOMMENDED` via a separate immutable decision sidecar with actual actor/ref; merely saving a CLI selection never asserts Human approval.
4. Implement an explicit live-model adapter seam with typed request/response, config and usage metadata, validation/error handling and fake-client tests. No network/spend by default, no implicit bypass of V1 `OFFLINE_ONLY`. Since no product provider/account was chosen, live API enablement is conditional/UNKNOWN; do not invent a successful model call or assume Codex engineering model equals the product's Creative model.
5. Reframe by model should return proposed edits; only a deliberate save creates next immutable revision. Do not overwrite historical observation or promote factual evidence to VERIFIED.

## A04-U — General media custody and future provider jobs
Keep original A04 V1 import/mock/dry-run, `AssetJobManifest`, `LocalMediaRunner`, `ArtifactRegistry` and A02 `MediaArtifact/JobResult` values loadable unchanged.
1. Add opt-in validated `image/jpeg`, `image/webp`, `video/mp4` custody/import. PNG/AVI behavior stays intact. Decode/ffprobe validity must be distinguished from container-sniff-only checks; test invalid/mismatched/truncated media. External tools are detected explicitly, not assumed.
2. Enable large input/output by bounded/chunked hash/stream + temp/atomic commit; do not make large MP4 bytes mandatory in memory. Keep path traversal and symlink/junction/hardlink protections; do not unguardedly reuse insecure file writes.
3. Separate media-generation job intent from stored media bytes. Add versioned generation-job request/receipt/status model with provider/model, optional prompt hash/version, external request ID, costs, source identity, side-effects/retry safety. Keep current V1 manifest's `generation_performed=False` invariant; don't mutate it into paid-model claims.
4. Add provider-neutral async lifecycle contract `submit/observe/fetch/reconcile` with fake backend tests (duplicate/out-of-order event, timeout, no blind retry). No actual paid provider account, cloud queue/webhook listener, or unauthorized generation API invocation.
5. Distinguish `LOCAL_IMPORT`, `SYNTHETIC_FIXTURE`, proven `MODEL_GENERATED`, `DERIVED_RENDER`, `UNKNOWN` with explicit evidence, not filenames. No rights upgrade based solely on a supplied label. Keep null/UNKNOWN costs instead of zero.

## A03↔A04 bridge (fan-in)
Add an immutable sidecar `CreativeAssetLink.v1` (new path/schema, not V1 handoff mutation) supporting direct prompt without selection, or optional selection with source concept revision; preserve creative intent/prompt digest, asset role, provider request ref, artifact and manifest SHA256, verified/provisional generation provenance and decision ref only if observed.
A03's pinned selection plus A04's asset import/mock association is **not** a claim that asset was generated to realize the concept.
Provide backward-compatible read of V1 `handoffs/<sha>.json`, explicit upgrade bridge when necessary, no ID/hash rewrite, tests for old and new consumers.

## Acceptance & limits
- A03 tests: Vietnamese, product_ref=null, no CTA, diversity mode and 3 similar variants, model adapter fake success/failure/malformed, reframe lineage, decision truth.
- A04 tests: PNG/AVI backward compatibility; JPEG/WebP/MP4 good/bad, bounded IO, actual bytes SHA256 and reopened validation; fake async statuses/reconcile, no double-submission; UNKNOWN cost/rights.
- Bridge tests: concept or direct prompt → generation intent (possibly mock) → artifact → legacy + new handoff, A05-readable; true provenance.
- No arbitrary creative QA, published content, real product-model inference or paid media generation is a verification requirement. Record actual paths, environment, tests/exit and external capability limits.
