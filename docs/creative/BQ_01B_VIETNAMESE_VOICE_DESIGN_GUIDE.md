# Vietnamese voice and acoustic planning guide v1.0

Status: **BQ-01b / offline source planning / no quality benchmark**. This taxonomy implements the bounded Brain-selected continuation of B-QUALITY-001 decision #373, the adopted Work Tree and ADR B002 at source `381b70f0979258d24a2524c1e8f133338be11ddc`. It does not change those adopted records or authorize a runtime.

Q3 R5 covers caller direction for human performance and audio. Q3 R4 covers intended temporal/spatial continuity and speech synchronization. K5 owns human/audio intent; K6 retains evidence, trust and rights boundaries. Brain retains meaning, creative strategy and brief approval. EF01/EF02 build and verify source. PULSE coordination and every external effect require separate work and grants.

## Source, hypothesis and TEST_ONLY

| Label | Meaning in this cut | Evidence it cannot supply |
|---|---|---|
| SOURCE | Exact supplied A03 intake, behavioral snapshot, causal plan and caller-authored script | Source truth, authenticated speaker, ownership or a license |
| HYPOTHESIS | Caller reasoning about desired delivery or audience response; inherited BQ-01a hypotheses retain assumptions/counterexamples | Observed audience preference, causal lift, quality or revenue |
| SUBJECTIVE_DIRECTION | Requested stress/tone/pause/pacing/intonation/accent and acoustic choices | Phonetic recognition, verified dialect, authentic human performance or measured room behavior |
| UNVERIFIED_SCENE_INTENT | Caller-authored sound fixtures, geometry and time/frame/trim requests | Inspected waveform, measured reverberation, sample rate or spatial fidelity |
| TEST_ONLY | Explicit synthetic feature/language capability metadata bound to an unchanged BQ-01a profile digest | Provider capability observation, benchmark, model ranking or execution permission |
| UNKNOWN / null language | Missing or unresolved information retained as uncertainty | Default silence, a regional pronunciation, compatibility support or a measured number |

Hashes prove canonical byte integrity, not truth or signatures. All ownership, rights and speaker identity remain **UNPROVEN**. Source content may include untrusted assertions; it remains source content and never becomes verification authority. There are no native-speaker trials, blind listening sessions, recordings or generated samples in this cut.

## Voice modes and exact text

`SILENT` means no speech; it works with realistic footage, fiction, animation or stylization. `FULL_MUTE` means no sound at all. Ambient/non-speech sound on silent footage requires the separate explicit `ambient_requested=true` acoustic request and explicit cues. Optional background music and sound effects remain individually requested, with unproven rights. No song or recording is bundled or licensed by this guide.

`VOICEOVER` requests narration with no on-screen lip-sync. Its acoustic scope is `VO_NARRATION`. `ON_SCREEN_SPEECH` uses `ON_SCREEN_SPEAKER`; speech sync is required only when the original causal plan explicitly requests it. `UNKNOWN` stays unresolved and leads to HOLD; it cannot become silent or voiced implicitly. A voiced plan without script or segments is an unresolved draft, never an invitation to invent narration.

The voice builder takes an existing valid CausalShotPlanDraft and exact digest, then caller-supplied segments. Segment `script_start` and `script_end` are half-open Python Unicode code point offsets into the original plan's supplied audio text. Segment text must equal that exact slice. They are not byte, grapheme, syllable, phoneme or audio sample offsets. Combining marks count separately. NFC and NFD remain distinct source bytes and digests. Punctuation, whitespace, diacritics, emoji and Vietnamese lexical spelling are preserved. Selected source spans may omit intervening text; they cannot overlap, translate or introduce words. No pronunciation is invented from spelling.

Language is `vi`, a caller-supplied tag matching `vi-XX` (including `vi-VN`), or null when the original plan says UNKNOWN. The syntax does not verify a real region/locale. Exact TEST_ONLY language support must be supplied; `vi` does not automatically cover `vi-VN` or a caller's `vi-XX` tag. Known original language/mode cannot be replaced or erased. No locale implies a person's ethnicity, residence, demographic identity or private attributes.

## Performance direction and intended failure modes

Every segment direction carries `SUBJECTIVE_DIRECTION`. Null optional fields mean no additional request. Explicit `UNKNOWN` fields mean unresolved requests and make the compiler HOLD.

| Dimension | Caller vocabulary | Intended failure mode to discuss later; not measured here |
|---|---|---|
| Stress | Up to eight increasing nonoverlapping character spans of original segment text | Emphasis on an unintended word or flattening a contrast |
| Tone | PRESERVE_SOURCE_TONES, NEUTRAL, WARM, URGENT, PLAYFUL, SOMBER, UNKNOWN | Losing written Vietnamese tone distinctions; confusing emotional direction with lexical tone |
| Pause | Up to sixteen increasing `after_char` positions and positive intended milliseconds | Breaking a phrase at an awkward position, or timing pauses beyond the whole segment |
| Pacing | SLOW, MODERATE, FAST, UNKNOWN | Rushed syllables, dragging delivery or fitting too much text into the intended shot |
| Intonation | LEVEL, RISING, FALLING, VARIED, UNKNOWN | Unintended question contour or repetitive cadence |
| Regional preference | NORTHERN, CENTRAL, SOUTHERN, OTHER, UNKNOWN plus a bounded caller label such as “Huế” | Treating a broad preference as a verified speaker dialect or a quality ranking |

`PRESERVE_SOURCE_TONES` is a request to retain source distinctions. It does not certify tone recognition or pronunciation. Character spans can cut a grapheme; the system preserves caller offsets and makes no linguistic segmentation claim. Stress, pacing and pause feasibility are bounded arithmetic checks, not an estimate of speaking duration or naturalness. No text-only result certifies lipsync, accent, intelligibility, identity or authentic performance.

## Acoustic taxonomy and binding

An AcousticSceneDraft embeds the exact voice draft and retains original plan, behavioral and A03 source digest pins. Every cue names an existing shot and a positive half-open absolute plan millisecond interval inside that shot. SPEECH cues bind exactly one original segment, its shot and its complete interval. Every voice segment has exactly one speech cue when sound is requested. Scene sound cues have no voice segment reference. Foreground speech cannot borrow a scene environment or background layer. VO and on-screen speech environment scopes cannot be interchanged.

| Dimension | Caller vocabulary | Interpretation |
|---|---|---|
| Scope | SCENE, VO_NARRATION, ON_SCREEN_SPEAKER | Distinct intended acoustic spaces; not captured spaces |
| Geometry | OPEN, SMALL_ENCLOSED, LARGE_ENCLOSED, UNKNOWN | Qualitative requested geometry, no room dimensions |
| Room | BOOTH, ROOM, HALL, OUTDOOR, UNKNOWN | OUTDOOR cannot simultaneously assert enclosed geometry |
| Reverb | DRY, SHORT, LONG, UNKNOWN | Direction tokens, never measured RT60 or milliseconds |
| Noise | QUIET, STEADY, INTERMITTENT, UNKNOWN | Direction tokens, never measured dB/SNR |
| Distance | CLOSE, MID, FAR, UNKNOWN | Requested perspective, no measured distance |
| Cue kind | SPEECH, AMBIENCE, MUSIC, SOUND_EFFECT | Explicit caller request; no generated asset or rights evidence |
| Layer | FOREGROUND, BACKGROUND | Requested perceptual role, not a mix measurement |
| Channel policy | SINGLE, MIX | SINGLE rejects overlapping cue intervals; MIX explicitly permits them |

Intended naturalness failure modes include dry narration in an intended reverberant scene, confusing narration booth sound with an on-screen room, masking foreground words with background sound, discontinuous room tone across shots, or inconsistent distance/reverb cues. They remain design hypotheses. This implementation checks declared scopes, references, conflicts and UNKNOWN values; it does not claim psychoacoustic consistency or measured sonic fidelity.

Optional frame fixtures declare a rational intended frame rate between 1 and 120 fps and half-open frame boundaries relative to the shot. Both boundaries must match cue times exactly by integer arithmetic. Missing frame fixtures leave the cue time-bound without inventing a measured rate. Optional asset windows declare hypothetical source duration and trim bounds, not actual files. Trim length must equal cue duration and stay within the declared duration. Speech cannot be trimmed through this fixture. There are no file paths, samples, sample rates, measured signal statistics or audio decoding seams.

## Capability preview and limits

Import the three new modules explicitly by qualified path; existing `creative_quality.__init__` is unchanged. `build_voice_intent`, `build_acoustic_scene` and `compile_voice_capability_mock` are pure in-memory interfaces. The compiler uses the original `compile_capability_mock` and `ModelCapabilityProfileDraft` unchanged. `VoiceAcousticCapabilityDraft` separately adds finite TEST_ONLY voice/prosody/sync/room/ambient/frame/trim features and exact language support, bound to that base profile digest.

`PLAN_COMPATIBLE_SIMULATED` means only that supplied TEST_ONLY metadata covers the requested source constraints. `HOLD_UNSUPPORTED` retains unsupported and simultaneous unknown reasons. `HOLD_UNKNOWN` covers absent metadata, language uncertainty and unresolved source/voice/acoustic direction. A BQ-01a HOLD always remains a HOLD, even when the new metadata asserts full support. The silent mute path requests neither TTS nor lipsync; explicitly requested non-speech sound still needs its own sound and acoustic features. Replay recomputes the preview and rejects changed results, pins, metadata or negative evidence.

Bounds are inherited from BQ-01a across the entire nested record: 128 KiB canonical UTF-8, 16 KiB per string, depth 12, 4096 value nodes, 128 items per JSON collection, integers up to 2^53-1. Additional bounds: 64 voice segments, 64 cues, 16 environments/channels, eight stress spans, sixteen pauses, sixteen language capability tags, plan time at most 300000 ms. Identifiers are at most 64 ASCII characters; accent labels at most 128 UTF-8 bytes. Aggregate bounds may reject combinations of individually legal maxima. Times, offsets and frames require actual integers, never booleans, floats or numeric strings. Closed fields reject provider-verification, benchmark or measurement fields; duplicate JSON keys, invalid Unicode, cycles and nonfinite input reject.

Every preview retains **media NOT_GENERATED / NOT_INSPECTED; naturalness NOT_MEASURED; dialect accuracy, identity and rights UNPROVEN; costs UNKNOWN; execution authority NONE**. No preferred model, provider ranking, authentic performance claim, live permission or publish/spend decision follows. Q4 actual-asset inspection, R6 Human calibration, Q5/R7 repair and licensed live generation require future scope and evidence. Source verification and candidate readiness remain separate from hosted candidate CI, Brain acceptance and PR merge.
