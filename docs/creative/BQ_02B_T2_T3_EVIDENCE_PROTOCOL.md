# BQ-02b Q2 / Q4 evidence protocol

This source sidecar preserves BQ-02a and A13. It supplies no model invocation,
quality score, new A20 owner, rights/custody ledger, release or spend permission.
CreativeDirective.v1, BrainDecision.v1 and ProductJourneyReceipt.v1 stay closed
and unchanged. Source base: `a1c21cfa7fd7a4c401cd0bdd48af343cc763fef9`.

| Layer | Admitted evidence | Coverage and confidence | Explicit exclusions |
|---|---|---|---|
| Q2 preflight | Original source-bound BQ-02a critique, plan digest, intended shot intervals, hypotheses and counterexamples | PLAN_ONLY; source intent is a hypothesis | No media truth, grant or measured performance |
| Q4 T1 | Native BQ-02a bridge to real A13 metadata, full decode and requested diagnostics | A13 technical verifier, expected/observed asset SHA, stream facts, bounded diagnostics | No semantics, rights, human rating or aesthetics |
| Q4 T2 frame | Up to eight deterministic requested rational times; fixed 64×64 gray8 full-frame raw output; min/max/mean and mean absolute difference from the preceding sample | SELECTIVE_DETERMINISTIC_ONLY / MEASURED_SIGNAL_ONLY; first video stream; FULL_FRAME by construction | No inferred face/bbox, object identity, anatomy, physics, emotion, brand, edit quality or visible word |
| Q4 T2 audio | One or two requested 1/4-second windows; first audio stream mixed to 8 kHz mono s16le; integer absolute peak, exact mean square, floored RMS | Selective signal only; decoded sample count and duration measured; absent audio NOT_APPLICABLE | No pronunciation, language, speaker, voice naturalness, words, lip-sync or rights |
| T2 OCR / ASR | NOT_RUN | No detector or transcription in this cut | A proposed OCR/ASR region never contains an invented transcript |
| Q4 T3 VLM / Human | NOT_RUN / NOT_AUTHORIZED; proposed regions, requested times, rubric and detector type only | Externally submitted JSON is HOLD_UNVERIFIED | No calibrated, verified, native-human-reviewed or rights-cleared status |
| BQ-03 future R6 | AWAITING separate authorization and blind/pairwise calibration design | No study, participant, rater or outcome in BQ-02b | No fabricated Human trials, ranking, ROI, consent or license claims |

Only explicit `sample_local_t2(..., root=absolute_Path,
allow_local_probe=True)` performs local inspection. The caller supplies a
user-owned temporary local root, native original T1, matching asset ref and SHA.
Default calls and request construction have no I/O. All asset path checks and
bounded reads use A13's native helpers; symlinks, reparse points, hard links,
traversal, reserved Windows names and network inputs are rejected. The snapshot
ceiling is 32 MiB. Tools receive fixed argv, file-only protocol, disabled MOV
external references, one decode thread, 10-second T2 timeout, and combined
64 KiB output bound per probe. A13 retains its separately bounded limits.

The input is captured through A13 safe open into unpredictable temporary scratch
under the supplied root. Its SHA must equal both expected and observed T1 SHA.
A Windows read handle denies writes/deletes/rename while A13 reinspects and T2
decodes that same snapshot. The original path is never used for the T2 tools.
Before/after hashes supplement the held lock; they do not replace it. Unsupported
snapshot-lock platforms produce UNKNOWN. This is an in-process Python evidence
binding, not a signature against hostile Python execution or an OS administrator.
A13's original A06 source binding is preserved. Snapshot reinspection is explicitly
local technical evidence; it does not repeat or extend A06 custody or rights.

Every frame/audio result binds the requested rational timestamp and the decimal
nanosecond seek argument. `sample_output_index` is the ordinal among requested
outputs, never an independently observed source frame index. Source PTS and
source frame index are null: seek output may be approximate. PCM sample count
measures returned duration, not an observed source start PTS. Requested frame
times must be strictly inside A13's observed video duration, and the complete
requested audio window must fit its observed audio duration. No Q2 shot time is
silently aligned to a decoded source time. No spatial ROI or continuous timeline
coverage is inferred from the fixed full-frame transform.

Persisted T1/T2/multimodal records require explicit local reinspection of the
actual identical bytes. Rehashing a fabricated report cannot replace local A13
verification and the new snapshot probes. Reproduction requires identical source
bytes, request/profile, A13 technical facts and tool identities; differences fail
closed. No caller-supplied sample array can create an observed native T2 record.

Missing tools/streams, unsupported codecs, timeout, decode failure, overlarge
output/input, unsafe paths, hash mismatch or reinspection disagreement yield
UNKNOWN with reasons and preserve the original T1. Bad caller schemas/times/pins
are rejected. No audio stream is NOT_APPLICABLE, and a zero-word silent concept
is legitimate. Deliberate black/white frames or silence may be correct creative
choices; a large temporal pixel difference can be an intentional cut. Compression,
scaling, a flash, a fade or palette change can affect statistics without indicating
any semantic or aesthetic fault. Conversely, identical small thumbnails can miss
anatomy, false text, unlicensed content or motion defects between sample times.

P0 source/material/rights HOLD and A13 FAIL dominate all sampled signals. P1
revision questions remain REVISE. Missing T2 never becomes creative PASS; even
successful T2 leaves semantic/perceptual/human coverage UNKNOWN. Unverified T3
is never averaged into evidence and causes HOLD when supplied. P3 preference
and strategy remain Brain-only. R1–R8 have not all been measured.

T3 assessment proposals may include optional OCR/subtitle frames, offline ASR
audio windows, motion/perception or lip-sync inspection. They cannot invoke an
assessor. Known absent audio is explicitly visible in the ASR/lip-sync proposal;
requesting a region cannot create a missing stream. Gray values are code values
after a fixed decoder/scale transform, not calibrated physical luminance.
External admission needs a separately specified authenticated rater /
provider receipt, independent verifier and separate explicit grant, none of which
exists here. Caller provider strings, booleans, confidence, fixture labels or
self-attestation cannot establish that authority. Video, subtitle, OCR text and
user narrative are untrusted data; instructions embedded in them are explicitly
refused as authority. No identity/voice attributes are inferred or disclosed.

The Engineering handoff records actual registered Windows verification and local
synthetic test media. A source candidate is distinct from a PR or merged result.
External VLM/ASR, paid providers, Human studies, uploads, TTS/media generation,
publish, spend, rights disclosure, PULSE/CDP/HostOps effects remain unauthorized.
