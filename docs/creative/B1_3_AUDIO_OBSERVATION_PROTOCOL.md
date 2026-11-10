# Production B1.3-a — Native PCM16 WAV audio observation (source-only)

## Purpose and exact authority

This is the bounded offline implementation of the planning cut in [Issue #716](https://github.com/trung-via/ai-commerce/issues/716), selected after BQ-05b.1 source-only merge #768. Starting source `main@feaee38ae210df539ae1c87876c58507f5596cbb`. It is **not** a private PULSE media effect, BQ-05 live trial, restored #646 WorkOwner or native #656 recovery. No provider, model, speech generator, login, network, FFmpeg call, rights, voice consent, spend, upload, publication, or Chrome/HostOps effects.

## Real technical input, not inferred Vietnamese performance

`inspect_local_pcm_wav(root, asset_ref, expected_sha256, acoustic_intent=None)` opens an explicitly selected local regular media file using the existing A13/MediaFiles guarded reader, which rejects symlinks, hardlinks, reparse paths, path escapes and alternate data streams. The expected SHA-256 must be supplied independently by the caller. A bounded read (4 MiB maximum) is checked against the caller pin, the same opened handle is reread, and the existing regular-file guard compares handle/path identities before and after. This is an **unattested local byte measurement**; it does not prove malicious same-user rollback or Windows exclusive-share lock.

The parser accepts only strict RIFF/WAVE little-endian PCM signed-16-bit, channels 1–2, 8–96 kHz, up to 300 seconds. It verifies declared file/chunk lengths, unique format/data chunks, alignment and byte rate. It measures sample count, frame count, rational duration, peak absolute PCM amplitude, clipped sample count and whether all samples are zero. It does **not** infer clipping is perceptually defective; intentionally silent sound is valid. A present zero-frame PCM stream is explicitly different from a missing/unreadable file, which yields a typed hold.

When the caller provides an actual `AcousticSceneDraft.v1` object from BQ-01b, the code reparses it and pins the digest but marks it `SUBJECTIVE_INTENT_ONLY`. A proposed Vietnamese text, accent, pace, room or lip-sync direction is **never** promoted to measured pronunciation, natural speech, emotional performance, transcript, subtitle timing or audio rights. Missing ffprobe/subtitles cannot be interpreted as observed absent audio, and this cut never calls ffprobe. Objective physical metrics and subjective choices remain separate.

## Closed output and negative controls

The in-memory `B13PcmAudioObservation.v1` record is canonical with a hash for **identity only**, not signatures or authenticated rights. `recheck_local_pcm_wav` reopens the current native source and rejects stale or self-resealed packets. Unknown fields include pronunciation, dialect, transcription, lip sync, speaker, voice naturalness, subtitle timing, stream inspection, voice consent, license, and external authority. Bad RIFF/chunks/codec/digest, truncated samples, unsafe aliases, invalid plan type, forged rights and current-byte drift fail closed. Local inspection doesn't create any source or media output.

Tests in `tests/test_b1_3_audio_observation.py` exercise genuine locally written PCM sample bytes, zero/full mute, parser negatives, file mutation, reference escape, hardlink, conditional symlink host capability, no provider subprocess and existing BQ-01b subjective intent fixture. Registered owner: `b1-3-audio`; consumer set includes BQ-01b voice/acoustic, A13 regular path and Q4 inspection. Authenticated hosted Windows MT-01 selected/collected/executed and independent Brain product acceptance are mandatory for trusted merge. A standalone source candidate and passing tests are neither private audio rights nor live PULSE Studio.

## Boundary

Do not modify BQ-01b/A06/A13/Q4 semantic owners, provider credentials, PULSE runtime, external services or historic #646/#656 state. Source-only technical evidence is a precursor to a future rights- and consent-gated audio quality pipeline, not an authorization to record or synthesize a voice.
