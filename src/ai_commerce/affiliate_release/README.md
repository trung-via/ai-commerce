# C08 media technical inspection

`inspect_media_release` inspects an explicit, nonempty sequence of local release
assets. Each `ReleaseAsset` contains a relative path and the SHA-256 pinned by
the caller's release request. No directory scanning or automatic inclusion of
creative drafts occurs. Current inspection supports MP4 audio/video streams;
other declared containers return `UNKNOWN / MEDIA_CONTAINER_UNSUPPORTED`.

```python
from ai_commerce.affiliate_release import (
    DiagnosticOptions, ReleaseAsset, inspect_media_release,
)

receipt = inspect_media_release(
    release_directory,
    [ReleaseAsset("video.mp4", expected_sha256)],
    diagnostics=DiagnosticOptions(black=True, silence=True),
)
encoded_receipt = receipt.to_bytes()
```

The immutable `MediaReleaseInspection.v1` receipt contains observed container,
codecs, dimensions, frame rate/count, audio properties, size, duration, tool
versions, hashes and completion state. Times use exact rational seconds. There
are no clock timestamps, random IDs, absolute paths or tool logs in the receipt;
identical bytes, requests and tool environment produce identical receipts.
The self-contained JSON schema is exposed by `media_inspection_schema()`.
Read receipts with
`MediaReleaseInspection.from_bytes` for strict JSON, digest and semantic checks.
Its checksum detects changes; it is not a signature or proof of an issuer's
authority. Reading a receipt does not inspect its source files again. Run the
inspection again when release bytes change or need rechecking.

`PASS` requires structural MP4 checks, real FFprobe evidence and a complete,
error-strict FFmpeg decode, with matching source and captured hashes before and
after inspection. Audio is optional. Codec, dimensions, FPS and duration are
observations rather than channel limits or aesthetic requirements. Corruption,
digest mismatch and unsafe paths produce `FAIL`. Missing/broken tools, resource
limits, unsupported containers/stream types and incomplete facts produce
`UNKNOWN`. The overall status is `FAIL` if any asset fails, otherwise `UNKNOWN`
if any asset is unknown, otherwise `PASS`; empty requests are rejected.

Black and silence diagnostics are optional observations. Their measurement
parameters are retained: blackdetect minimum duration 0.1 s, pixel threshold
0.1 and picture ratio 0.98; silencedetect minimum duration 0.1 s and noise
threshold -50 dB. These are diagnostic settings, not acceptance criteria. Black
frames and silent art pass technical inspection when valid. No audio yields
`NOT_APPLICABLE` for silence. A diagnostic tool failure yields diagnostic
`UNKNOWN` and does not veto an otherwise complete technical inspection.

## Existing provenance and consumers

`asset_from_e01(directory, expected_manifest_sha256=...)` uses the real A06
public `e2e.inspect` API on a bounded captured manifest and pins its delivered
`video.mp4` hash. The inspector reopens and checks that same manifest before and
after media inspection. The receipt records both the raw manifest hash and
A06 record hash/run ID. It explicitly reports full custody verification as
`NOT_PERFORMED`: checking all imported source assets would exceed the requested
release-asset scope. A05 render conformance and A06 full custody verification
remain available through their existing APIs.

C08 reuses `media.files.open_regular`, its handle/ancestor checks and the
existing bounded MP4 box parser (`_mp4_boxes`). Its subprocess runner adds live
output bounds suitable for release inspection. The existing A05 verifier binds
specific render plans and cannot serve as a universal release constraint.

This component supplies technical evidence for commercial placement review.
Product claims/rights stay in A12's `ai_commerce.product.ProductEvidencePack`,
account decisions in A07's `ai_commerce.distribution.evaluator.evaluate`, and
assisted handoff in A08's `ai_commerce.distribution.assisted.export_package`
and `inspect_package`. C08 does not replace those APIs or infer their conclusions
from media. Every receipt leaves rights, account eligibility and link activation
`UNKNOWN`, and publication `NOT_ATTEMPTED`. It adds no gate to creative ideation
or rendering and invokes no handoff or publishing action.

## Resource and path boundary

Defaults are 16 requested assets, 256 MiB per asset, 512 MiB total captured
input, 30 seconds per tool invocation and 1 MiB combined stdout/stderr per
invocation. `InspectionLimits` permits bounded configuration (up to 64 assets,
1 GiB per asset, 4 GiB total and 60 seconds per invocation). These limits are
inspection resources, not platform limits; exceeding them returns `UNKNOWN`.
Each FFmpeg/FFprobe allocation is also capped at 128 MiB. This is a per-allocation
guard, not an OS process memory quota. Decoder/filter threads are limited to one.

Files must be local regular files with one hard link. Absolute/network refs,
traversal, drive/ADS syntax, Windows devices, symlinks, reparse points and linked
ancestors are rejected before tool discovery. Capture and integrity rereads
are byte bounded. The tools see only a private temporary copy, forced through
the MOV/MP4 demuxer with file-only protocols and external data references
disabled. There is no shell invocation, OAuth, network validation, download,
publication or spending. Child environments contain only OS/tool necessities.

The inspector retains no media copy after returning. `PASS` describes the bytes
observed during that inspection; it does not promise that a caller-controlled
path remains unchanged afterwards.
