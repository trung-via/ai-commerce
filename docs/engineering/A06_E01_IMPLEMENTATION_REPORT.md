# A06 — E01 Offline Creative Factory Product Completion

Status: IMPLEMENTED_PENDING_VERIFICATION. This report is not evidence of M1 acceptance.
Source: Human A06 Deep Research 2026-10-29; base main 858c5bd54fa348c6c46f2fae82cd35be4ef886f2.

## Product responsibility

A06 is a thin product-service layer on A03/A04/CreativeAssetLink.v1/A05, not a new renderer or AI media provider. One run accepts freeform prompt and explicit local PNG/JPEG/WebP stills, imports through A04, records truthful LOCAL_IMPORT links, pins an immutable CreativeComposition, renders a real H.264 MP4 with A05, and produces a human-openable delivery folder.

CLI:

    python -m ai_commerce creative-e2e run --prompt "Original local creative" --asset picture.png --output .local/e01-run
    python -m ai_commerce creative-e2e inspect --run .local/e01-run
    python -m ai_commerce creative-e2e verify --run .local/e01-run --full-decode

Repeated --asset creates ordered image scenes. --frames controls duration per source still. FFmpeg, ffprobe and libx264 must be installed. Existing CLI entrypoints are unchanged. This does not provide text-to-video, model-generated media, publishing, accounts, CTA/SKU requirements or external spending.

## Result and evidence

The delivery folder contains video.mp4, e01-manifest.json, delivery.json and custody/ with immutable A04 jobs, A03↔A04 CreativeAssetLink sidecars, composition revision, render manifest and content-addressed inputs/output. The E01 manifest is an index, not a second authoritative copy of the underlying records. Its request and output SHA256 digests must reopen unchanged; full decode is performed by the existing A05 verifier. No byte-identical rerender across encoder versions is asserted.

Source provenance remains LOCAL_IMPORT with realization_claim=false. publication_status=NOT_ATTEMPTED. No model generation, license grant, public visibility or commercial result is inferred from the prompt.

## Product demo and verification

    python -m pip install -e ".[dev]" Pillow
    python scripts/a06_demo.py --output .local/e01-demo
    python -m ai_commerce creative-e2e verify --run .local/e01-demo --full-decode
    python -m pytest tests -q

The demo creates two original synthetic PNG scenes locally, then uses the real E01 API and FFmpeg. These fixtures are NOT outputs of an AI generation model. The A06 GitHub workflow should validate Windows and Ubuntu and upload the actual E01 demo package for review. Only observed CI/job/artifact results justify a PASS.

Negative cases include missing/corrupt media, tampered MP4/manifest, partial package and unsafe overwrite. A06 does not require model, SKU, CTA, publishing or account proof to demonstrate E01. Do not mark M1 accepted until the recorded MP4 and evidence package are verified.

## Execution history

EF02 #69 was rejected RESTRICTED_PATH before model invocation. EF02 #70 was admitted and invoked Codex gpt-6.1-sol/max, but native Windows tools failed with 0xC0000142; no files changed and no tests ran. This direct GitHub source branch is a separate authorized implementation route. It must pass hosted CI before merge or E01 acceptance.
