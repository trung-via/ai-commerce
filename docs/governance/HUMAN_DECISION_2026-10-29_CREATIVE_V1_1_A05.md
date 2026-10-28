# Human decision — A03/A04 v1.1 upgrade and A05 execution

Date: 2026-10-29. Repo: `trung-via/ai-commerce`. Source before planning: `4dcaf5b60ca5d7b389953b4336fcbe8f21bf07d8`.
Status: **HUMAN-APPROVED FOR IMPLEMENTATION**. Applies to internal scope and engineering launch; not a claim that a candidate or video exists.

## Human directive
- Implement A03/A04 v1.1 using Brain's 2026-10-29 research draft, **Option B — Creative-capability-first**, without rewriting the verified v1.0 foundation.
- Finalize A05 from the previously held draft and **authorize engineering implementation** as C03 + C06: Creative Intent → editable Composition IR → provider-neutral render adapter → local FFmpeg/ffprobe playable MP4 with manifest and diagnostics. The previously draft architecture is adopted here only for this bounded scope.
- Existing WBS 63 IDs, six Roadmap A phases, A03/A04/A05/A06 dependencies and M1 definition are unchanged. This is not A06/M1 acceptance.
- Preserve freedom: no forced SKU, CTA, fixed video ratio, concept quota, storyboard, creative rubric, paid media API or publishing gate.
- Reuse A02 contracts, A03 immutable selections and A04 registry without relabeling historical mock/import as model generation, changing historical IDs or overwriting stored artifacts.
- The previous Human-selected EF02 engineering executor, **Codex `gpt-6.1-sol` effort `max`**, is carried forward as the selected implementation route for this adjacent Creative Factory work; model *used inside the product* is a separate integration choice. No automatic executor/model fallback.
- No authorization in this decision to buy provider usage, issue external paid generation requests, publish to a platform or claim third-party rights.

## Delivery approach
A03 and A04 upgrade candidates may run independently (EF02 parallel), then A05 C03 and C06 on the compatible candidates; final fan-in reconciles the whole pipeline. Source and verification must be pinned and every candidate checked before separate main merge. Partial candidates are not automatically production.
See:
- `docs/engineering/A03_A04_V1_1_ENGINEERING_HANDOFF.md`
- `docs/engineering/A05_IMPLEMENTATION_HANDOFF_V1_0.md`
- `work_items/A03-A04-UPGRADE-V1_1-001.json`
- `work_items/A05-SCRIPT-SCENE-RENDER-001.json`

## Decision/evidence limits
A03 v1.0 and A04 v1.0 are on main from PR #55 (reviewed Windows EF01 project-tests: 365 PASS). The source's earlier intra-executor Git/PowerShell startup failures are historic evidence, not silently repaired.
Live product-provider model/account/credentials, GPU availability, local FFmpeg availability, selected API pricing, and end-to-end A06 creative quality remain **UNKNOWN until observed**.
If FFmpeg is missing, report a precise environment dependency; do not claim MP4 PASS or install arbitrary untrusted binaries.
