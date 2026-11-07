# BQ-05a PULSE Studio offline QA interoperability v1

Date: 2026-11-07. Source: `c868b989382763b03f5040390a8adaf99c25e064`.
Action: `BQ05A-PULSE-STUDIO-OFFLINE-QA-BRIDGE-CODEX-XHIGH-01`.
Attempt: `bq05a_pulse_offline_qa_bridge_codex_xhigh_c868b989_20261107_v1`.
Requested executor: Codex, `gpt-6.1-sol`, `xhigh`, no fallback. Native executor
identity and candidate verification remain the EF01/EF02 runner's evidence.

## Existing journey only

`pulse_studio_offline_bridge.inspect_wait_brain(session_path)` returns canonical
`BQ05aStudioQAEvidence.v1` bytes in memory. The adapter does not export a file,
advance state, render, acquire a journal lease, retry or repair. A caller may
retain the returned bytes separately from the sealed native receipt package.
Its only ingress is an existing native B0.4 `SESSION.json` absolute local path.
No supplied receipt, claimed state, fixture token, provider label, or context
dictionary authenticates a journey. Any context or effect other than
`REVIEW_ONLY` returns `FAIL_CLOSED_HOLD` before native media inspection.

The adapter reopens the session with the native owner, verifies B0.4 admission
and completed attempt trail, replays the journal/checkpoints, and invokes
`handoff.verified` and existing handoff consistency checks. The handoff must
already exist. This performs actual A06 custody verification, FFprobe and full
decode, checks the exact four-event sequence ending in WAIT_BRAIN, and compares
the sealed receipt and immutable attended evidence packet. The bridge never
creates the package to make an incomplete journey look complete.

The bridge's source SHA identifies this WorkItem base. The existing B0.3
protocol's `SOURCE_SHA` is intentionally unchanged and appears separately as
`binding.pulse_source_sha`. The packet's `runtime_sha256` hashes every shipped
Python source, including this new adapter. Tests create fresh native sessions
at that code version. Old private sessions whose runtime hash differs HOLD;
there is no rewriting of source pins or reuse of stale private sessions.

## Current bytes and Q4

Acceptance requires native Windows share-mode locks. Every root and parent is
checked for symlink/reparse tags, locked with OPEN_REPARSE_POINT, and compared
with its native final handle path. Each retained input and evidence file is
regular, has exactly one hardlink, and is locked with FILE_SHARE_READ only.
UNC/device/ADS, traversal, short-name aliases and worktree-private paths fail
closed. Reads are bounded to 512 entries and 64 MiB. File identities, current
bytes, directory membership and final context-exit checks must all agree.
Unavailable locks, concurrent writers or unavoidable TOCTOU yield HOLD.

On the actual locked A06 `video.mp4`, A13/T1 inspects the packet-pinned SHA with
black diagnostics and full decode. Optional T2 (default on) measures frame zero
and half the observed duration. A separate hash-identical temporary copy outside
the retained journey is supplied to T2 because its native API creates scratch
snapshots under its root. T2 independently reinspects the captured bytes with
A13 and locks its snapshot. Failed requested T2 leaves overall HOLD/UNKNOWN;
explicit `sample=False` reports T2 NOT_RUN. The original media is never edited.
Native verification tools may use their existing disposable diagnostic spools;
the bridge adds no retained input, journal, receipt, custody or media writes.

The existing critic -> quality_review -> multimodal_review chain binds actual
T1/T2 digests. Because PULSE has no causal-plan translation contract, an explicit
internal UNKNOWN-intent review scaffold satisfies the existing closed Q2 API.
Its required fictional form and 1 ms placeholder interval describe only the
review harness; they do not classify the video or map its shots. No private
creative prompt enters that scaffold or the sidecar. Creative, semantic, T3,
human calibration and rights coverage remain unproven. Black frames, silence,
fiction and animation are permitted; measurements do not establish defects.

## Q5 and serialization

Native `decide_repair` receives the actual multimodal review without any
FixtureSession or caller context. The only accepted outcomes here are HOLD,
KEEP_ORIGINAL and RETURN_TO_BRAIN. The preservation plan uses editor NONE.
No BQ04b white-slate oracle, repair adapter effect, repair witness or synthetic
8-frame 64x64 fixture is applied to PULSE's normal A06 output. The independent
BQ04b consumer tests retain their own exact-fixture positive and false-black
no-edit checks.

The closed sidecar links journey ID, directive version/digest, native source
and current runtime hashes, exact receipt/packet file and record digests,
QA checkpoint, A06 manifest/tree/current artifact SHA, Q4 source/result SHA,
native T1/A13/T2/multimodal/Q5 digests and sanitized measured diagnostics.
`record_sha256` seals canonical JSON excluding itself; the object's `digest`
hashes the complete canonical file. There are no timestamps or local paths.
Nested fields are closed. Private prompts, account/actor references,
credentials, tool error text and exception paths are not projected or logged.

Constructing a sidecar object validates serialization only. Hashes can be
recomputed by anyone. A receiver must call `recheck_sidecar(raw, session_path)`
to perform fresh native verification and current-media reinspection, and demand
exact deterministic equality. It cannot accept a validly rehashed forged fact
or a stale sidecar. Even successful replay is technical-only evidence.

## Offline versus live

`OFFLINE_TECHNICAL_REVIEW_ONLY` means actual bounded local technical signals
were measured on the same native WAIT_BRAIN asset. `FAIL_CLOSED_HOLD` means
that chain or a requested inspection could not be established. Neither is
creative quality approval, source acceptance, an authenticated Brain decision
or private provider proof. The provenance stays SYNTHETIC_FIXTURE or
HUMAN_SUPPLIED with LOCAL_IMPORT; provider generation is never inferred.

All results carry `NO_PROVIDER_EFFECT`, `NOT_AUTHENTICATED_BRAIN`, `NO_PUBLISH`,
`NO_SPEND`, `QUALITY_NOT_APPROVED`, `RIGHTS_UNKNOWN`, `NO_LIVE_PULSE_STUDIO`.
No network/provider dispatch, spend, publication or human study is authorized.
Independent anti-rollback and privileged OS symlink creation are unproven.

BQ-05 live is NOT_STARTED / NOT_AUTHORIZED. It requires separately reviewed
B0-S private custody, authenticated per-effect rights and Human grants,
provider/account entitlement, exact asset/action/spend scope, an attended
provider invocation and independently verified actual provider receipts.
Source tests, GitHub issues, this sidecar and local import receipts supply
none of those permissions. BQ-05b live planning follows verified source CI,
independent Brain PASS and trusted merge; it requires separate authorization.

## Verification and ownership

The new MT01 data-only owner lists the bridge and its tests and references the
existing production/A06/Q4/Q5 owners and closed PULSE contracts as READ_ONLY
consumers. Use `mt01-component-onboarding` and its genuine selected consumer
closure. Do not expand `.github` or another owner to satisfy registration.
Native Windows FFmpeg and FFprobe are mandatory, with zero skips for mandatory
link cases. Only controller-observed xdist evidence establishes actual workers.
Test cache, bytecode, JUnit and temporary media belong outside the repository.
At most two Verify–Fix rounds are authorized. The executor cannot publish a
candidate or claim PR/CI/Brain acceptance; those remain EF01/EF02 and independent
reviewer responsibilities.

## Executor observation — verification remains HOLD

The first owner run timed out at 900 seconds without a determinate summary.
A separate streamed disposable native journey completed SETUP -> WAIT_BRAIN ->
sealed handoff -> real Q4 OBSERVED / Q5 KEEP_ORIGINAL. The second owner run
collected 36 tests on 12 controller-observed workers: 32 passed, 4 failed,
0 skipped, 0 errors in 588.96 seconds. The actual A06/T1/T2/canonical-sidecar/
deterministic-read-only integration positive passed. The input manifest was
unchanged throughout that execution.

Native junction and hardlink creation were denied by this Windows sandbox.
Those mandatory tests remain failing environment evidence; no skip or alternate
privilege/creation route was used. Two code defects were corrected in the
second fix step: Q5 now uses its native observed asset pin (null when T2 is
unobserved), and the tampering test resolves the A06 custody video through its
owner manifest's content-addressed output ref rather than an `.mp4` glob.
These final changes have NOT been retested. No third executor test round was
run. Selected consumers were NOT executed after the owner failed. This source
therefore remains HOLD for independent EF01/EF02 verification; no candidate,
complete MT01 proof, PR acceptance or Brain acceptance is claimed.
