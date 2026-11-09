# BQ-06b — Fresh A09 replay pinned to A11, independent reauthoring

**Status:** BOOTSTRAP_REGISTERED / PRODUCT_NOT_IMPLEMENTED. This file specifies acceptance only; no BQ-06b implementation, actor, provider result, A09 replay or commercial effect can be inferred from its presence.

## Human decision, provenance and scope
Human instruction 2026-11-09: "chạy lại bq 06b độc lập". Retain immutable #694 Brain-HOLD candidate and orphaned #698 R2 WorkOwner `PROVIDER_INTENT/HOLD_UNKNOWN_EFFECT` for audit; do not reuse either source or candidate bytes. Commission independent fresh coding from the main tree **after** this bootstrap source/MT-01 owner registration is reviewed and merged. Engineering default Codex `gpt-6.1-sol` effort `xhigh`, no fallback without independent approval. No PULSE live, marketing provider, spend, posting, rights or BrainDecision permission.

The first PR is deliberately **CI-first owner/admission bootstrap**, not the BQ-06b product. Its new source module and test are inert/negative; subsequent EF02 source action must replace them with authentic replay and negative source cases.

## Required source semantic criteria
- Read A09 `AnalyticsLedger.inspect(import_id)` and `AnalyticsLedger.history(plan=selected_import['plan'],limit<=100,offset=...)` under bounded read-only source custody; consume A11 `SnapshotPin`, `read_snapshot`, independently retained `observation_ref` and A11 revision/export; reuse read-only BQ-06a `recheck_review`, retain A17 opt-in and pending-only status.
- Native A09 lineage is a **dict** with `relationship: ROOT|CORRECTION|REPROCESS` and `parent_import_id`. Enumerate full bounded history, follow monotonic next_offset and detect descendants transitively through parent graph; any genuine later descendant of the independently pinned old source is `A09_LINEAGE_STALE_OR_CORRECTED` / HOLD. Never infer freshness from a one-import snapshot or malformed fake list.
- Genuine `AnalyticsLedger.import_file(correction_of=old_import_id)` and `AnalyticsLedger.reprocess(old_import_id,...)` native tests must verify stale old pins HOLD, two-hop descendants HOLD, unrelated ROOT does not contaminate, latest genuine source can PASS only if A11 independent current pins align. Complete pagination >100 or typed `HISTORY_UNPROVEN`; incomplete, cycle, duplicate or wrong-scope remain HOLD.
- Match full A11 observation_ref identity, metric version/denominator/timezone/window/cohort/status and null vs zero; don't synthesize source corroboration, causal lift, ROI or provider attestation. Local source replay is at most `LOCAL_A09_RAW_REPLAY`, not provider evidence.
- Native Windows guards on symlink/junction/hardlink/ADS, SQLite WAL/SHM, source bytes before/after immutable read and cross-DB atomicity; reject or HOLD when evidence is unprovable. WinError1314 from *native* symlink creation is a capability SKIP, never a PASS.
- A09/A11/A17/BQ-06a owners are read-only consumers. Real positive + adversarial Windows tests with owner/consumer group closure and no external effects.

## Independent scope, admission and release
This bootstrap commits the reviewed `bq-06b` component owner manifest, exact test argv, source placeholder and product protocol so that baseline MT-01 can recognize it. It does NOT implement the feature. After trusted merge, a new uniquely identified EF02 batch may modify **only**
`src/ai_commerce/creative_quality/behavioral_learning_fresh_replay.py` and
`tests/test_bq_06b_fresh_a09_replay.py`.
All WorkItem, owner manifest and docs remain source-pinned on that fresh main. Declare exactly one fingerprinted `MT01_ACCEPTANCE_JSON:["A09.analytics","A11.learning","A17.C07.C10.E06.offline-creative-learning"]` at the start of action instructions, and derive the additional reviewed BQ-06b ACs from the committed owner manifest; never weaken the requirement for consumers.
Maximum two bounded repair rounds, selected/collected/executed Windows MT-01 proof, actual model/tool-write/delta evidence, candidate ancestry, hosted exact-head CI, independent Brain PASS and trusted merge. No new model call when claim is UNKNOWN, no stale candidate reuse.