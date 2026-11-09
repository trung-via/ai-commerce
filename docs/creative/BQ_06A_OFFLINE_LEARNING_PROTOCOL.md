# BQ-06a offline learning handoff

Source base: `6777b13f9effbf242b2ebd963ee5f7580c485651`. Action
`BQ06A-Q6-NATIVE-SYMLINK-AGY38FLASHHIGH-R4-001`, fresh R4 repair attempt
`bq06a_q6_native_symlink_6777b13f_agy38_flash_high_r4_20261109` by Antigravity
Gemini 3.8 Flash High for Issue #682. R3 Codex quota exhaustion and R2 #679
remain immutable records. EF02 alone publishes a verified candidate branch.
Hosted Windows CI, independent Brain review and trusted canonical merge
remain separate stages.

`behavioral_learning_bridge.review_learning` accepts exactly one existing,
quiescent A11 registry path or independently archived A11 export file. The
receiver supplies its separately retained `expected_export_id`, learning key,
latest revision, complete A09 context and complete metric definition. Never
derive those expected pins from the untrusted incoming archive. The registry
path calls genuine `LearningRegistry.inspect` through `export_advisory`; both
ingresses reconstruct `LearningAdvisory.from_bytes(expected_id=...)` and check
every archived event, contract, hash and revision. Retained export identity
detects rehash replacement and suffix rollback relative to that pin. It cannot
detect a malicious rewrite when the receiver's independent pins are also lost.

Verification always remains `ARCHIVED_LOCAL_HASH_CHAIN_ONLY` and
`ADVISORY_ONLY`, including when reading a current A11 database. A11 source
status is `USER_SUPPLIED_NOT_LIVE_VERIFIED`; synthetic fixtures stay synthetic,
manual/platform-export observations are labelled HUMAN_SUPPLIED. There is no
external provider attestation. Fresh A09 replay is not implemented in this
subcut: an explicit `a09_current` request returns HOLD rather than claiming
that an archived hash is a new replay. Native test setup does exercise real
A09 import/raw replay, A11 snapshots/reports/registry/export/reopen.

The selected complete history must share the exact expected metric, account
alias, cohort, window, timezone and creative source pin. Report numerator,
denominator, unit identity, original evidence roles, negative observations,
counterevidence, uncertainty and confounder references survive projection.
AVAILABLE zero stays numeric zero. NOT_AVAILABLE or INSUFFICIENT_DATA stays
null, with the raw source value retained separately when report availability
differs. An incomplete latest sample, missing denominator/cohort, retired
learning or missing A17 contract produces `INSUFFICIENT_EVIDENCE_HOLD`.

The only sidecar is closed `BQ06aLearningBriefReview.v1`, canonical UTF-8 JSON
with a SHA-256 identity. `review_schema()` closes every nested field. This is
not a signature or authentication capability. `recheck_review(raw, **pins)`
reopens the owners and compares all bytes; deserializing or resealing caller
JSON grants no authority. Output is limited to 512 KiB, 32 A11 events and 512
selected evidence observations. Source enumeration is bounded at 512 files
and 64 MiB; over-limit input holds without truncating selected history.

All free prose, provider labels, metric keys/definition/version/unit, cohort,
creative references, confounder descriptions and uncertainty are represented
by exact SHA-256 references to the validated owner values. Their original
values remain in A11/A17 for authorized private review. Public output retains
the standardized status, platform/surface, hashed A11 account alias, window,
timezone and numbers. No raw account, post ID, private prompt, filesystem path
or Human actor identity is exported. Metric/version/unit hashes identify exact
semantics; they are never interpreted as cross-provider equivalence.

Optional `a17` has exactly `registry_root`, `batch_id`, `source_id`,
`proposal_id`, `expected_proposal_sha256`, `opt_in`. The existing registry
tree already exists and is locked before constructing the native ArtifactRegistry;
all constructor guards run and its idempotent mkdir cannot create a root under
these locks. Native `load_variant_batch`,
`consume_learning_advisory` and `load_proposal` independently recheck source
composition/revision/content and A11 pins. The proposal must already exist,
match the independently retained proposal digest, and have PENDING adoption
with no r2 adoption record. The adapter never calls the writing
`propose_next_creative` or `record_adoption`. Native test setup creates the
proposal through the genuine public A17 API before read-only adapter review.
Without that contract the output gives exact missing requirements; it does
not fabricate a variant or write an A03 brief. Fixed questions and a testable
hypothesis are a HANDOFF ONLY for a separate authorized Brain review.

Optional `pulse_session` invokes native BQ05a `inspect_wait_brain`; a supplied
`q4_sidecar` must pass `recheck_sidecar` against a real current B0.4 WAIT_BRAIN
journey and exact A06 media. Its technical artifact and journal identities
are a separate evidence class. `association=UNBOUND` and
`comparability=NOT_COMPARABLE` are fixed even if names or hashes resemble A11
or A17 names. No cross-owner mapping is implemented. Technical faults cannot
be audience metrics, causal outcomes or evidence of creative popularity.
BQ05b0 remains inert `LIVE_EFFECT_HOLD` in native integration tests.

Native Windows handles lock ancestors and files with deny-write/delete,
check final handle paths and compare file identity/bytes after owner replay.
Reparse points, junctions, symlinks, hardlinks, ADS, traversal, short aliases,
device paths, writable SQLite sidecars and write/rename races fail closed.
An A11 SQLite header must declare rollback mode in both read/write-version
bytes before its owner opens SQLite. A persistent WAL header holds even with
no WAL/SHM files present, preventing a read-only connection from creating them.
Schema reads are detached, including constant flags, limits and questions.
Mandatory native hardlink and junction negative tests do not skip and remain strict rejection gates. If Windows lacks the OS privilege to create a real symlink (WinError 1314), that single test explicitly records `NATIVE_SYMLINK_CREATION_UNPROVEN` and is skipped without being counted as rejection proof, holding that privilege-dependent assertion for hosted Windows CI.

MT01 is data-only onboarding of new owner `bq-06a` with three new ACs. The
executor ingress declares only existing `A11.learning` and
`A17.C07.C10.E06.offline-creative-learning`. The trusted post-executor planner
validates the new manifest and its real existing consumer closure; no parser,
guard or global FULL fallback is changed. Positive/negative test names are
mapped explicitly in the WorkItem. Use isolated pytest scratch outside source,
collect before execution, and record actual xdist workers rather than inferring
them from `-n 12`. Mandatory native failures or skipped owner tests remain
unproven; no candidate acceptance follows from partial passes.

Execution observations and evidence paths are recorded in the WorkItem.
The full 15-group diagnostic collected 2,272 tests, then was interrupted after
no progress in the final native cases; its last progress report was 98%.
There is no final JUnit total or full-profile PASS for that invocation. Final
source has two bounded repair passes; independent EF01/EF02 must replay the
entire registered closure and mandatory native link/PULSE gates. Executor
sandbox scratch is ephemeral; its receipts do not replace the worker's
independently retained immutable attempt artifact.
Final source: 61 new-owner tests collected; 54 affected source tests passed
with twelve native xdist fixture workers observed and unchanged source/test/
manifest bytes. Seven previously failing mandatory native gates were deselected
from that focused verification, remain HOLD, and are not counted as passes.
Final complete closure collection contains 2,274 tests; complete execution,
trusted post-executor verification, candidate, hosted CI and Brain acceptance
remain pending. Exact commands, hashes and failure dispositions are in the
WorkItem. No further executor repair pass is authorized in this attempt.
There are no provider requests, spending, publication, Human adoption, signed
Brain decisions or live commercial outcomes in this source protocol.
