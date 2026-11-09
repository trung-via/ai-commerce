# BQ-06b fresh evidence replay protocol

Source base: `76589c479a10ff6788f5739cb3952dcf06c37d3b`. Action
`BQ06B-Q6-FRESH-A09-A11-REPLAY-AGY38HIGH-01`, fresh attempt
`bq06b_q6_fresh_a09_a11_replay_76589c47_agy38flashhigh_20261109_v1` by Antigravity
Gemini 3.8 Flash High. Builds on independently Brain-PASS merged BQ-06a PR #688.
EF02 alone publishes a verified candidate branch. Hosted Windows CI, independent
Brain review and trusted canonical auto-merge remain separate stages.

## Overview & Architecture

BQ-06a establishes a strict offline bridge that reviews retained A11 history into
a detached Brain advisory with optional current Q4 technical context, while
intentionally refusing live A09 replay (`A09_CURRENT_REPLAY_NOT_SUPPORTED`).
BQ-06b implements the read-only, OPTIONAL current A09 evidence recheck against
the exact archived A11 *selected* observation refs, while strictly retaining A17
opt-in and BQ-06a review semantics.

The new owner `behavioral_learning_fresh_replay.review_fresh_replay` consumes:
- Receiver-supplied exact A09 `SnapshotPin(import_id, context_sha256, raw_sha256, observation_ids)` for one import;
- Quiescent A11 registry path or independently archived A11 export file;
- Expected A11 export ID, learning key, revision, context, and metric definition pins;
- Optional genuine A17 pending proposal contract and optional Q4 technical sidecar.

Replay modes are explicitly distinguished in `BQ06bFreshEvidenceReview.v1`:
- `LOCAL_A09_RAW_REPLAY`: Both A09 fresh replay and A11/A17 review succeed and observation refs match byte-for-byte;
- `ARCHIVED_A11_ONLY`: A09 replay was not requested; valid A11/A17 review is preserved without claiming current A09 replay;
- `INSUFFICIENT_EVIDENCE_HOLD`: Missing evidence, drift, corruption, or unverified trust boundaries produce a closed, typed hold.

## Cross-Owner Matching and Atomicity

1. **Two-Database Trust Boundary**: A09 ledger and A11 archive reside in separate SQLite databases. Cross-DB single transaction atomicity is never faked. Native Windows handles freeze both databases with deny-write/delete locks across ancestor directories before reading, and check handle paths, file attributes, metadata, and bytes before and after owner operations.
2. **Path & Sidecar Controls**: Reparse points, junctions, symlinks, hardlinks, ADS (`:stream`), dot traversal (`\..\`), short aliases (`~`), device paths, and SQLite sidecars (`-wal`, `-shm`, `-journal`) fail closed before opening. The SQLite header must declare rollback mode in both read/write version bytes (`\x01\x01`). If cross-owner atomicity cannot be proven, the adapter holds `CROSS_OWNER_ATOMICITY_UNPROVEN`.
3. **Exact Observation Reference Match**: Cross-owner matching requires exact full `ObservationReference` dictionary equality (`snapshot_id`, `import_id`, `context_sha256`, `raw_sha256`, `observation_id`, `observation_sha256`, `metric_definition_sha256`) and raw observation bytes. Account alias, creative name, or display title alone are never used for matching.
4. **Subset Projection**: Only the exact independently rechecked observation subset selected by A11 history is claimed as replayed. Unselected observations in the A09 snapshot are not conflated, and no per-video or post IDs are exposed.
5. **Missingness & Drift Detection**: Numeric zero (`AVAILABLE` with `value=0`) and missingness (`NOT_AVAILABLE` with `value=null`) remain strictly distinct. Drift in metric definition version, denominator, cohort, measurement window, timezone, account alias, or source status is detected and reported as bounded reason codes. Stale or corrected A09 ledger lineage reports `A09_LINEAGE_STALE_OR_CORRECTED`.

## Non-Authoritative Brain Handoff

`BQ06bFreshEvidenceReview.v1` is canonical UTF-8 JSON sealed with a SHA-256 identity digest (`record_sha256`). Reconstructing or deserializing caller JSON confers zero authority; `recheck_fresh_replay(raw, **pins)` reopens genuine owners and verifies every byte.

Local A09 replay never upgrades `HUMAN_SUPPLIED` or `SYNTHETIC_FIXTURE` observations into provider-attested metrics. No provider grant, audience virality, comparative lift, ROI, budget, posting, BrainDecision, or Human adoption is authorized or inferred.
