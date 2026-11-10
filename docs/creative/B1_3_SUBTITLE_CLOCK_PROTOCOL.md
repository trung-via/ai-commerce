# Production B1.3-b — Current SRT caption timing vs PCM WAV (offline only)

## Scope and evidence owner

This source-only cut follows B1.3-a PR #780, merged at `main@110fd1ea4093961c08114f51ceafd5eff89e3445`. It is approved as *roadmap source continuation only*, not permission to generate voices/video, authenticate provider accounts, publish or spend. The existing B1.3-a owner `audio_observation.py` remains unchanged; the new component `b1-3-subtitle` reads it and relies on independent native current-file rechecks. Existing BQ-01b intent, A13 custody and Q4 technical owners are read-only.

## Positive technical proof

- Caller provides a `B13PcmAudioObservation.v1` packet and separately pinned SHA/relative ref to the **current** WAV. `recheck_local_pcm_wav` reopens original PCM, validates actual bytes, and returns frame_count/sample_rate from genuine parsed samples. This local identity check is not a signed issuer, share-lock against malicious same-user races or PULSE native effect.
- The optional SRT is identified by an independent expected SHA-256 and a constrained regular local path. A13's reusable regular-file guard rejects symbolic links/reparse/hardlinks/stream aliases. Reading uses a 64 KiB limit, double-read on the same handle and exact content SHA; the PCM record is rechecked after reading SRT.
- SRT parser accepts strict UTF-8 (optional BOM), CRLF/LF, ordinal cues 1..64, canonical HH:MM:SS,mmm intervals, nonempty text, sorted nonoverlapping cues. Each cue end time must satisfy the exact integer comparison `end_ms * sample_rate <= frame_count * 1000`.
- Only source cue milliseconds, Unicode code point counts and SHA-256 of exact original caption text are returned. The packet never copies a caption body into logs/artifacts. It is canonical/hash-bound *local identity only*, and current-source recheck detects a self-resealed fake report.
- `srt_ref=None` and `srt_sha256=None` yield `NO_SUBTITLE_SUPPLIED`, a valid silent or subtitless creative option; this does not infer absence of any embedded subtitle stream.

## Important counterexamples and failure boundaries

**A subtitle clock falling within a WAV's measured duration does not prove phoneme, syllable, Vietnamese tone, spoken-word, screen presentation or lip-sync alignment.** Subtitles may intentionally annotate silent animation or translation. Report fields explicitly `NOT_MEASURED` for audio–speech alignment, Vietnamese pronunciation, transcription, speaker identity, visual render and lip sync; source language remains `NOT_VERIFIED`. Voice and caption rights/consent remain `UNPROVEN`, and provider/publish authority remains `NONE`.

Malformed UTF-8, wrong ordinal, timestamp syntax, overlaps, zero-length cue, out-of-duration interval, stale audio/subtitle SHA, missing native source, forbidden path aliases, symlink/hardlink and tampered record get finite HOLD codes. Inaccessible Windows symlink creation results in fixture SKIP for just that capability, never a claimed negative PASS; mandatory remaining safety regressions must still execute.

No network, remote model, FFmpeg, speech recognizer, artificial voice, credentials, real provider, private broker, new PULSE worker, publish, upload or spend. WorkOwner #646 UNKNOWN and #656 native custody HOLD are unaffected.

## Verification and acceptance

WorkItem: `work_items/B1_3_SUBTITLE_CLOCK_001.json`. Owner `work_items/mt01-components/b1-3-subtitle.json`. Native fixture and adversarial test `tests/test_b1_3_subtitle_clock.py`. Exactly six source paths and append-only Roadmap B are authorized. Verify genuine hosted Windows MT-01 owner + B1.3-a, BQ-01b, A13 and Q4 consumers, collected/executed/JUnit Python and Node; accept only after independent Brain semantic review and trusted merge receipt on exact head/base. Source merge does not demonstrate real PULSE product generation, Vietnamese language accuracy or speech rights.
