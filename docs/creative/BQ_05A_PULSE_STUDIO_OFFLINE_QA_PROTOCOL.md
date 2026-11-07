# BQ-05a PULSE Studio offline QA interoperability v1

Date: 2026-11-07. R2 source base: `22b4220936cc9eaf1d952ed1e5e2909d20d4e374`.
Action: `BQ05A-R2-CODEX-XHIGH-HOSTED-CI-FAILCLOSED-TRIAGE-01`.
Attempt: `bq05a_r2_codex_sol_xhigh_hosted_positive_hold_main_22b422_20261107_v1`.
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

## Historical R1 executor observation — immutable, not R2 proof

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

## R2 bounded diagnosis and native scratch correction

R1 PR #632 remains Brain HOLD. Its independent hosted run 42381987961 executed
1,155 tests: 1,150 passed, five BQ05a positives failed, zero errors/skips.
Artifact 13578212856 SHA256 is
`707e605c40acf3eb84d5704d666d8f3dca4e3663d3f15ff2e6a59ee1f7d6089f`.
The historical self-host 1,153 PASS / two consumer symlink SKIP receipt does
not override that hosted failure. No R1 attempt or CI was rerun.

Internal `_diagnose_wait_brain` is for a trusted disposable native fixture.
It runs the same production inspection, records only the finite phases
SESSION_PATH, DIRECTORY_LOCK, FILE_LOCK_AND_IDENTITY, OPERATOR_REPLAY,
HANDOFF, A06_CURRENT_BYTES, A13_T1, T2_CAPTURE, Q2_MM_Q5, FINAL_LOCK and
SIDECAR_SEAL, and returns closed technical categories/counts. Entry proves
only entry. Completed block counts prove only successful protected blocks,
not the entire phase; the innermost failed block supplies the failed phase.
ACCESS_DENIED or SHARING_VIOLATION requires an actual native error/category.
No exception text/type name, stack, path, prompt, credentials, local artifact
copies or private state enter the diagnostics. The public sidecar retains
its original generic HOLD/UNKNOWN and never asserts an unproven reason.
The five positive tests request this internal triage only on failure; the
child-process positive diagnoses in that same audited child environment.

The initial R2 five-positive diagnostic passed locally: five collected and
executed, five PASS, zero FAIL/ERROR/SKIP, twelve controller-observed workers,
217.79 seconds, source hashes unchanged. This was before the scratch fix.
Native Python 3.11.9 and 3.14.7 directory-handle/final-path and ordinary-file
lstat/fstat identity probes both passed. A full local Python 3.11 journey
could not run because its missing dependency download failed DNS resolution.
None of these observations proves the historical hosted root cause.

A separate fresh real B0.4 subprocess/FFmpeg journey reproduced a bounded
defect: Windows GetShortPathNameW returned a genuine alias of the bridge's
new scratch directory, independently confirmed to have the same identity.
Default sampling then failed at T2_CAPTURE / CHECK_REJECTED, while explicit
sample=False completed technical-only with native advisory Q5 HOLD. This
reproduces a compatibility failure mode, not the unobserved R1 hosted phase.

The correction expands only the bridge-created scratch spelling with
GetLongPathNameW, then checks the strict long path, native final handle paths,
all reparse-free ancestor locks and original/expanded directory identities
before use and on exit. An expansion to another directory fails identity
checks. Caller paths still reject short aliases. The API requires actual
caller folder/read-attribute permissions; any native failure remains HOLD.
See Microsoft's [GetLongPathNameW contract](https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-getlongpathnamew).
No Windows settings, ACLs, base owners, scheduler, rights or contracts change.

R2 registered native verification is incomplete and exact-head hosted confirmation
is unobserved. The WorkItem distinguishes executor-local diagnostics,
post-executor EF01/EF02 verification and hosted/Brain acceptance. An unexplained
hosted failure keeps acceptance HOLD. The executor publishes no candidate or PR.
After EF02 verifies a candidate, the separate publisher must propose a NEW PR
with exact HEAD/base, fresh hosted Windows CI artifact and independent Brain
PASS before trusted auto-merge; R1 PR #632 never receives R2 PASS.

### R2 registered round 1 — incomplete, HOLD

The unchanged registered selector admitted exactly a06, a13, b0-3, b0-4,
bq-02a, bq-02b, bq-04a, bq-04b, bq-05a with
ONBOARD_CONSUMER_OWNER_CLOSURE, no fallback and no missing mappings.
The existing verifier stopped with exit 3 / VERIFY_INFRASTRUCTURE_ERROR after
B0.3's native subprocess timeouts. Actual JUnit: 259 collected, 256 executed,
237 PASS, 19 FAIL, three SKIP, zero ERROR. Every executed group observed twelve
controller-collected workers and the complete source hash map stayed unchanged.
A06: 7 PASS. A13: 196 PASS / one native-link environment SKIP. B0.3: 34 PASS /
19 timeout FAIL / two native-link environment SKIP. The other six groups were
not executed; no counts or PASS are inferred for them.

These consumer timeout failures are independent of the earlier five-positive
BQ05a diagnostic. Their underlying cause remains unproven. The source/test
owners and timeout policy are outside the six allowed paths and remain untouched.
A separate owner investigation of tests/test_b0_3_attended_worker.py may be
needed; no extension is applied or authorized by this source handoff.

The remaining bounded round is an exact registered BQ05a owner diagnostic of
the final bridge, including real link negatives. It cannot replace the missing
nine-group verification or hosted Windows evidence. Internal FINAL_LOCK stage
tracking is scoped to actual ExitStack exit checks, not the whole observation;
completed blocks never certify a phase that merely started.

### R2 round 2 owner diagnostic — final observed result, HOLD

Exact registered owner argv: `python -m pytest tests/test_bq_05a_pulse_studio_offline_bridge.py -q -n 12`.
JUnit: 39 collected/executed, 36 PASS, three FAIL, zero ERROR/SKIP; twelve actual
controller-collected workers; 455.458 seconds; source hash map unchanged.
JUnit SHA256: `a7046b35592ef7054a246520a39fef000f69f283905d13b0febaec3072802416`.
All FIVE original hosted-positive cases passed, including the audited child.
The additional native temporary short-name test observed a real short alias
and completed T2 OBSERVED with retained bytes unchanged. Substituted scratch
identity rejection, private-error stage redaction and sample=False / Q5 HOLD
also passed. These are executor-local native observations, not hosted proof.

Two mandatory failures remain actual Windows construction denials: os.link
returned WinError 5; mklink /J returned Access is denied. Neither is skipped,
replaced or claimed proven. The third failure was in a newly added diagnostic
assertion: actual FINAL_LOCK was correctly identified and public HOLD passed,
but the test expected NATIVE_IO instead of the observed UNEXPECTED_FAILURE.
Native open_regular translates errors during unwinding to an opaque owner
exception; triage does not infer a native I/O cause from that wrapper. Only
that added category assertion was corrected to the observed constant after
round 2. It has NOT been rerun. Production bridge bytes are unchanged from
round 2; its tested hash is recorded separately in the WorkItem.

No third executor round ran. Reports preserve the actual 36 PASS / three FAIL
receipt without projecting a hypothetical later PASS. The six unexecuted
registered consumer/owner groups, mandatory native link checks, post-executor
EF01/EF02 verification and exact-head hosted Windows/independent Brain proof
remain outstanding. R1 PR #632 remains HOLD. No candidate, PR, Git ref write,
provider effect, publish or spend is accepted or performed by this executor.
