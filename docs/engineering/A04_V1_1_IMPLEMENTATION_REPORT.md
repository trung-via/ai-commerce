# A04 v1.1 implementation report

Date: 2026-10-29 (Asia/Saigon).
Status: **IMPLEMENTED; SCOPED LOCALLY VERIFIED; FULL PROJECT PROFILE NON-PASS**.
Real JPEG/WebP/MP4 decoder execution and native link boundaries have the environment limits below. No main merge, deployment, real model generation, or media release is claimed.

## Authority, source, and scope

Implemented O01-O04/C04 using:

- docs/governance/HUMAN_DECISION_2026-10-29_CREATIVE_V1_1_A05.md
- docs/engineering/A03_A04_V1_1_ENGINEERING_HANDOFF.md
- Original A04 modules and tests/test_a04_media_operations.py

Observed provided worktree HEAD: **b88bf37f81aa115ab36320ee1f043283923984ce**, read from Git worktree HEAD metadata. The handoff names the earlier baseline 4dcaf5b60ca5d7b389953b4336fcbe8f21bf07d8. Git cannot initialize in this sandbox; no ancestry verification, candidate commit, Git diff PASS, or remote verification is asserted.

Edits are confined to the media/operations modules, requested new test and this report. Historical A04 tests/reports/work items, A02 contracts, shared CLI, packaging, verification profiles, A03, bridge and A05 retain their bytes. The existing project-tests profile automatically collects the new test.

Six existing files changed:

- src/ai_commerce/media/__init__.py
- src/ai_commerce/media/base.py
- src/ai_commerce/operations/__init__.py
- src/ai_commerce/operations/manifest.py
- src/ai_commerce/operations/registry.py
- src/ai_commerce/operations/runner.py

Seven files added:

- src/ai_commerce/media/files.py
- src/ai_commerce/media/generation.py
- src/ai_commerce/media/fake_generation.py
- src/ai_commerce/media/provenance.py
- src/ai_commerce/operations/generation.py
- tests/test_a04_upgrade_v11.py
- docs/engineering/A04_V1_1_IMPLEMENTATION_REPORT.md

SHA256 comparison against a pre-edit inventory of 163 source files found the six authorized existing changes and **157 unchanged files**. Runtime evidence is under ignored .local/a04-v11/. Nonignored temporary pytest directories were removed after verifying their resolved paths stayed inside this worktree.

## Media custody and V1 compatibility

LocalMediaRunner.run() retains V1 PNG/AVI import, mock, dry-run, provider capabilities, request binding, attempt/replay rules and A02 result fields. Default import capability lists are unchanged, preserving request digests. AssetJobManifest remains version 1 and enforces **generation_performed=False**. Its MIME lookup additionally validates the opt-in file-import manifests.

LocalMediaRunner.import_file(MediaRequest(...)) is the new explicit file entry point. It captures the source, records its true reference/hash/size, validates it, and reuses V1 manifest/attempt handling. New MIME types are rejected by the legacy bytes entry point before reading them. Public V1 bytes APIs retain their existing contracts.

ArtifactRegistry.capture_file/register_file/verify_file provide file custody and reopened verification. Capture and hashing use **1 MiB chunks**, with regular-file identity/mutation checks before and after access. Import, verification and fake-provider fetch pass MP4 by path; no MP4 content bytes are required in memory. verify_file() returns the validated stored path. MP4 callers of bytes-returning verify() are directed to the file API.

Immutable writes now stage an exclusive temp file, flush/fsync, and publish atomically without replacing an existing target. Matching content deduplicates through chunked comparison; different existing bytes raise a collision and remain untouched. Windows uses no-replace rename; POSIX uses link/unlink. A crash in the POSIX two-link interval fails closed on later hardlink inspection.

Root identity, ancestor, traversal, reserved-name, ADS, link/reparse and hardlink protections remain. New source capture also rejects linked ancestors, hardlinks and special files. The registry retains its protected-directory assumption: it is not an OS sandbox against hostile concurrent ancestor renaming. Atomic visibility and fsynced files do not establish full power-loss durability for every directory update.

| MIME | Required verification | Bounds/limits |
| --- | --- | --- |
| PNG / AVI | Existing V1 structure/container checks | Existing codec-playback limits remain; opt-in file checks cap encoded input at 128 MiB |
| JPEG | End marker, Pillow format verification **and pixel load** | 128 MiB encoded/decoded image limits |
| WebP | RIFF type/length, Pillow with libwebp and frame decoding | 128 MiB encoded/decoded limits; 1,024 frames maximum |
| MP4 | ISO box/brand checks, ffprobe video/frame evidence and FFmpeg error-strict primary-video decode | Chunked file IO; 60-second tool timeout; disk-spooled diagnostics read with a 1 MiB cap |

MP4 sniffing alone cannot authorize custody. FFprobe and FFmpeg must both be detected on PATH. External data references are disabled; the protocol whitelist is local-file-only. Missing tools, execution denial/native initialization failure, or unavailable Pillow/libwebp raise **MediaDependencyError / MEDIA_DEPENDENCY_UNAVAILABLE**. Corrupt input raises validation errors. No dependency is downloaded or installed.

An exact V1 mock manifest, binding and object captured before edits are embedded in the upgrade test. Bytes/IDs replay unchanged, including request digest ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b. Existing stored output is verified rather than overwritten or silently healed.

## Separate V2 generation contract

GenerationJobRequest is version 2, separate from MediaRequest and AssetJobManifest. It pins provider/model, source ref/hash, output MIME, optional prompt hash/version, and explicit rights claims/references. Intent is distinct from observed output.

AsyncGenerationProvider defines bounded submit/observe/fetch/reconcile interactions for a job completing later. GenerationEvent and version-2 GenerationJobStatus carry external request ID, immutable intent digest, state, provider sequence/event ID, cost certainty, side-effect certainty, diagnostics and retry safety.

GenerationJobRunner persists immutable intent and a pre-submit marker before calling the backend. Checksummed append-only observations live under generation-jobs/<job_id>/. Runner recreation reconstructs status without submitting again. Lost responses and interruptions remain UNKNOWN; reconcile uses the durable client token/existing request ID and never calls submit. There is no automatic retry or polling loop.

Identical duplicate events have no repeated state/cost effect; conflicting event-ID reuse fails validation. Stale observations do not regress completion. Conflicting terminals in either arrival order require reconciliation. Reconciliation cannot promote terminal status back to RUNNING. Known cost and confirmed effects survive later unknown observations; unknown cost stays null. Typed error receipts preserve known costs and NONE/UNKNOWN effect certainty.

Fetch requires an observed successful completion, binds request ID/MIME/output SHA256, uses file custody, and stores an immutable V2 output receipt. Replay revalidates the object without another fetch. Explicit fetch reads by known ID; it never submits or generates.

Only an **offline synthetic fixture backend** is enabled. Provider-neutral types leave a future adapter seam; network, possible spend and non-fixture capabilities are rejected. FakeAsyncMediaProvider supplies tested local lifecycle/output. No paid API client, provider account/credential integration, cloud queue, webhook listener or publishing action is included.

## Provenance, rights, and cost

MediaProvenance is a separate evidence/hash-bound sidecar:

- IMPORT maps to LOCAL_IMPORT; original generation remains UNKNOWN.
- MOCK maps to SYNTHETIC_FIXTURE with generation_performed=False.
- MODEL_GENERATED requires a matching observed successful receipt, confirmed effects, explicit model-output evidence and observed output hash. The enabled fake runner cannot establish it.
- DERIVED_RENDER has a future receipt contract binding renderer, input hashes and observed output hash. No renderer is implemented or invoked.
- Missing/mismatched evidence yields UNKNOWN or validation error. A label or filename cannot assert generation.

Generation intent does not establish that output realizes a concept. A03 selection/Human approval and A05 render execution are not inferred. Historical IDs/hashes/artifacts are not migrated.

Import rights remain UNKNOWN by default. V1 GRANTED claims require a supplied evidence reference; this records a claim, not independently verified ownership. V2 fake output retains UNKNOWN rights even if intent contains a grant claim. Provider names and generation/render classifications grant no rights.

Unknown cost is never converted to zero. Amounts in fake tests are simulated observation data, not real spend.

## Verification evidence

Host: Windows; Python **3.14.7**, C:/Users/TRUNG/AppData/Local/Python/pythoncore-3.14-64/python.exe.
No live product-provider call, credentials, paid generation or publishing was used.

| Run | Exact pytest result | Exit |
| --- | --- | --- |
| Pre-edit A04 + A03/A04 integration baseline | **101 passed, 2 skipped in 61.72s** | 0 |
| Final scoped regression | **207 passed, 15 skipped in 127.90s** | 0 |
| Final registered project-tests | **16 failed, 412 passed, 15 skipped in 169.19s** | 1 |

Baseline command, with PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 and PYTHONDONTWRITEBYTECODE=1:

~~~text
python -m pytest tests/test_a04_media_operations.py tests/test_a03_a04_integration.py -q -p no:cacheprovider --basetemp=.pytest-a04-v11-baseline --maxfail=1
~~~

Final scoped command, with those same two environment overrides:

~~~text
python -m pytest tests/test_a04_media_operations.py tests/test_a04_upgrade_v11.py tests/test_a03_a04_integration.py tests/test_a02_contracts.py tests/test_a02_mock_provider.py tests/test_cli.py tests/test_foundation.py tests/test_isolation.py -q -p no:cacheprovider --basetemp=.local/a04-v11/scoped-final --tb=short
~~~

Final registered command was read directly from .github/ef02/verify-profiles.json without editing the registration:

~~~text
python -m pytest tests -q
~~~

Registered-run environment overrides:

~~~text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
PYTHONDONTWRITEBYTECODE=1
PYTEST_ADDOPTS=--basetemp=.local/a04-v11/project-tests-final -p no:cacheprovider --tb=short
TEMP=<worktree>/.local/a04-v11/system-temp
TMP=<worktree>/.local/a04-v11/system-temp
~~~

The wrapper wall time was **169.81s**. Raw local evidence is in .local/a04-v11/project-tests-final.log and .local/a04-v11/project-tests-final.result.json.

The new test module contributes 78 cases: **65 passed and 13 skipped** within the final scoped/profile runs. Coverage includes real valid/corrupt/mismatched/truncated formats when decoders work; explicit missing/broken-dependency errors; actual-byte hashes/reload; bounded large-file ingestion; failed writes/commits; collision preservation; root/link/traversal guards; exact historical replay; interrupted/timeout jobs; async identity/order/reconciliation; output substitution; and unknown rights/cost.

All **16 full-profile failures are outside A04**:

- Twelve cases in unchanged tests/test_ef01.py and three in unchanged tests/test_ef01_ef02_integrity.py fail at git init, exit **3221225794 / 0xC0000142** (DLL initialization failure).
- tests/test_ef02_m0r_isolated_runner_kit.py::test_powershell_control_script_parses_on_windows_without_execution fails at process creation with **WinError 1920: The file cannot be accessed by the system**.

No A04 assertion failed in the final runs. The registered profile is nevertheless **NON-PASS**, requiring verification on a host with working Git/PowerShell.

The 15 final skips are explicit:

- Eight real JPEG/WebP cases: Pillow is installed, but PIL._imaging cannot load its native DLL.
- Four real MP4 cases: neither ffprobe nor FFmpeg is on PATH.
- Two legacy native symlink/junction cases: host link privileges unavailable.
- One native source-hardlink case: WinError 5, access denied.

Deterministic reparse/hardlink rejection and actual root-replacement tests passed separately. The 20 MiB video IO test deliberately stubs decoding and asserts bounded reads/hash/reload; it proves streaming behavior, **not MP4 validity/playback**. Real JPEG/WebP/MP4 acceptance on this host remains **UNVERIFIED**; precise unavailable-dependency errors are tested.

Python AST parsing succeeded for all 14 A04 module/test files inspected; final-newline and trailing-whitespace checks passed. git diff --check could not start: exit **-1073741502**, the signed DLL initialization failure. SHA256 scope/whitespace checks are separate evidence, not a Git diff PASS.

Superseded development failures were repaired or recorded. The first baseline invocation hit 103 setup errors at sandbox default TEMP; a writable basetemp resolved them. An early dedup test exposed differing Windows pathname/handle ctime after rename; cross-path identity now compares device/inode/size/mtime, while ctime is checked between reads on the same handle. Final dedup, collision and reload tests pass.

## Acceptance assessment

| Criterion | Assessment |
| --- | --- |
| V1 APIs, serialization, identities, import/mock/dry-run and stored replay | SATISFIED by regressions and captured V1 replay |
| Validated opt-in formats and explicit unavailable-dependency errors | IMPLEMENTED; error paths verified; real decoder execution UNKNOWN on this host |
| Chunked capture/hash, atomic commit, reopened bytes and storage guards | SATISFIED by deterministic tests; native links UNKNOWN where skipped |
| V2 intent/receipt/status, fake lifecycle, idempotency and no blind submission retry | SATISFIED for enabled local fake backend |
| Evidence-bound provenance and UNKNOWN rights/cost | SATISFIED for tested contracts and local/fake execution |
| Registered project-tests fully passing | UNSATISFIED: 16 environment failures |
| Real model generation/account/pricing, A03 bridge, A05 rendering, A06/M1 acceptance | Outside this implementation; no execution/acceptance claimed |

Next external evidence: run these same tests with working Pillow/libwebp, FFmpeg/ffprobe, Git/PowerShell and native link privileges. No paid generation is needed to verify this scope.
