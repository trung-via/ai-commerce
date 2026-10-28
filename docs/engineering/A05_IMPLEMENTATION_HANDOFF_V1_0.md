# A05 — Script, Scene & Video Render (C03+C06) — Implementation Handoff v1.0

**Human-approved for implementation** 2026-10-29; canonical decision: `docs/governance/HUMAN_DECISION_2026-10-29_CREATIVE_V1_1_A05.md`.
Current project: Roadmap A Stage 1 Creative Factory. A05 depends on A03/A04 and is upstream of A06/E01. This is NOT A06 completion nor M1 proof.
Selected path: **Composition-first, FFmpeg-first, provider-neutral**, within independent Python AI Commerce repo.

## C03: Creative Intent → editable Composition IR
Create versioned `CreativeComposition.v1` data model and immutable store, ideally `src/ai_commerce/composition/`; no forced storyboard, scenes, SKU, CTA, audio, 9:16, length or scoring. Preserve freeform intent/source brief; optional pinned A03 concept selection and optional source media from A04.
Composition is a valid render plan after resolving references: canvas width/height/fps; frame-based timing; tracks/items supporting visual image/video and optional text/audio when implemented; render settings; lineage and parent revision/hash; source provenance.
Support simple direct-mode composition with one image, multi-scene ordered images, and optional manually authored scene/track plan. If only an abstract prompt is provided and no media producer is configured, **do not invent visual media**; expose `ASSET_REQUIRED` or explicit synthetic fixture mode.
Make composition serialization independent of FFmpeg commands to support later providers; changes create new revision rather than overwriting.

## C06: provider-neutral local render
Build `src/ai_commerce/render/` with FFmpeg/ffprobe subprocess adapter (argv, bounded timeout and logs, no shell injection). Detect executable/capabilities. The canonical minimum is a valid PNG/JPEG/WebP source → playable H.264/yuv420p MP4, configurable aspect/size/fps, deterministic frame timing; multiple images/scenes composited in order. Audio/text overlay/transition only when supported by real chosen engine; silent video is valid.
Use A04's immutable inputs and streaming MP4 custody; derive render-source provenance without mutating A04 V1 import/mock claims. Return immutable render job manifest with input composition digest, source asset digests, command/encoder version/config, actual output bytes hash, duration/codec/resolution, output/thumbnail refs and diagnostic log. Verify with ffprobe **and actual decode**, not merely file exists; mark missing ffmpeg/codecs as environment dependency, never success.
Rendering is not creative understanding, model-generation or external publication. The video can be technical PASS while semantic creative realization stays UNKNOWN.

## Fan-in CLI and E2E-for-A05
Integrate `composition-create`, `composition-inspect`, `render-run`, `render-inspect` (names may be adjusted minimally to match existing CLI) without changing `concept-create`, `concept-reframe`, `asset-run` and `asset-inspect` semantics. A user can render locally with no product SKU/CTA/provider account. Verify full chain with a real playable MP4 if FFmpeg installed on runner, otherwise record truthful environment blocker; do not silently skip new integration cases as green.
Existing legacy A03/A04 saved refs and old job manifests must still load.

## Required scoped tests / evidence
- composition source/roundtrip/revisions/invalid timing, prompt-only correctly unsupported for offline render without asset;
- one real image -> MP4; multiple stills scenes -> correct ordering/duration and optional silent track; corrupt/missing source, wrong MIME, incompatible encoder/tool absent/timeout, hash mismatch;
- MP4 read/reopen, actual stream/codec/duration, ffprobe+decode and SHA256 manifest validation; no false success after interruption;
- contract compatibility A02/A03/A04, no project regression introduced by scope;
- record real command argv, exit code, FFmpeg version/tool availability, build/test counts, output checksum and exact candidate ref.
No claimed A06 E01 or M1 until separate usable creative artifact E2E is observed.

## Implementation DAG
A03-U and A04-U independent; A05-C03 depends on A03-U; A05-C06 depends on A04-U; A05-INTEGRATION depends on both and reconciles bridge A03↔A04. Required fan-in tests on combined candidate. No default paid model call, provider cost, external release or AIOS-Renew shared runtime.
