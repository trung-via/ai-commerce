# A04 implementation report

A04 implements canonical Roadmap A **O01-O04 + C04** in the provided worktree, based on HEAD `21e134752f1cb3e5ff7cc202e0dea309f2d0ead6`. Sources: `docs/roadmap/ROADMAP_A_CREATIVE_FIRST_V1_0.json` and `docs/roadmap/WORK_TREE_V1_1.json`. These are independent AI Commerce asset jobs, separate from engineering executors and other projects' runtimes.

Status: **IMPLEMENTED; local verification passed with two native-link checks skipped**. No new commit, merge, release, external API execution, model-generated media, publishing or MP4 rendering is claimed.

## Delivered scope

| WBS | Implementation |
| --- | --- |
| O01 | Frozen `AssetJobManifest`: job/attempt, immutable canonical request digest, stage/state, refs, cost, error/last state, retry safety and embedded trace. |
| O02 | `LocalMediaRunner`: one worker per root, import/mock/dry-run, immutable started/final attempts, same-request replay and explicit consecutive safe retries. |
| O03 | `ArtifactRegistry`: SHA256-addressed bytes, exclusive immutable writes, matching-byte reuse, output verification and protected-root checks. |
| O04 | Typed validation/custody/collision/path/worker/retry errors. Provider failures retain stage and trace; ambiguous outcomes remain unknown. |
| C04 | Typed provider-neutral `MediaProvider` and input/output/capability/cost records; selected `LocalImportAdapter` and separate A04 synthetic PNG mock. |

Only `src/ai_commerce/operations/`, `src/ai_commerce/media/`, `tests/test_a04_media_operations.py`, this report and `work_items/A04-MEDIA-OPERATIONS-001.json` are added. **All 137 pre-existing files retain their SHA256 values.** Shared CLI, schemas, A02 code, `pyproject.toml` and A03 paths are unchanged.

A02 `MediaArtifact`, `JobResult`, `SourceSnapshot` and `EvidenceRef` validators are reused, as are `canonical_bytes`, `sha256_bytes`, `source_snapshot` and `timestamp_utc`. Custody uses exclusive creation rather than the older helper's check-then-write operation.

## Media and source truth

| Mode | Observable result |
| --- | --- |
| `import` | Actual captured file bytes copied unchanged; `origin=LOCAL_IMPORT`, `source_generation=UNKNOWN`, `generation_performed=false`. |
| `mock` | Synthetic pixel PNG with embedded MOCK label; `origin=MOCK` and `source_generation=SYNTHETIC_FIXTURE`. No model generation. |
| `dry-run` | No provider invocation or artifact. Manifest state `DRY_RUN`; JobResult status `UNKNOWN` with `DRY_RUN_NO_EXECUTION`. |

The selected importer supports non-interlaced `image/png` and RIFF `video/x-msvideo` (AVI). PNG checks include headers, chunk CRCs, palette requirements, bounded compressed scanlines and filters, with a 128 MiB decoded scanline limit. AVI checks include container lengths, nested lists, video headers and nonempty frame chunks. General AVI codec playback is not asserted. Other MIME types, including MP4, are rejected by this minimal importer. No rendering or transcoding is included. A02's text mock remains text-only and is rejected at the media boundary.

The source is captured once. Its bytes/hash/size, resolved absolute source reference, requested reference, MIME, rights status/reference and provider identity/capabilities determine request identity. Changes cannot reuse a bound job ID. Identical bytes at two locations can share a stored object while retaining different request/source references.

Provenance identifies the imported media file, never a fabricated JSON brief or the output-copy location. A post-capture source modification cannot alter captured bytes. `load()` still validates preserved output after the original file is removed.

Rights default to `UNKNOWN`. `GRANTED` requires a caller-supplied rights evidence reference, recorded without independent verification. Import does not determine whether the source was originally model-generated.

Built-in import/mock and dry-run costs are `NOT_APPLICABLE`/null. Explicitly unknown cost stays `UNKNOWN`/null, including when returned output fails validation. Network-required, spend-capable and model-generation providers are rejected before `produce()`.

## Custody, replay and failure semantics

The dedicated registry stores `objects/<sha256>`, an immutable `jobs/<job_id>/request.json` binding, `attempt-<n>.started.json` before invocation, and `attempt-<n>.manifest.json` containing the final A02 JobResult and trace. An exclusive `worker.lock` prevents concurrent workers sharing that root.

Replay validates checksum, job/attempt/request binding, trace/state, costs, source/evidence/artifact links, MIME, rights and retry semantics. Successful replay verifies actual object SHA256, size, identity and format. Missing/corrupt outputs raise errors and are never silently recreated. Manifest checksums detect accidental edits; they are not signatures against rewriting the entire registry.

Storage references reject traversal, absolute/drive/UNC paths, backslashes, ADS, ambiguous components and Windows device names. Access rejects symlinks, junction/reparse points, hard-linked storage files, special files and replaced roots. Restrictive creation modes are used where supported. Callers must protect this dedicated directory from hostile concurrent ancestor replacement; this is not an OS sandbox.

Timeouts, unclassified provider errors, explicit unknown effects and interrupted started records become `UNKNOWN`/`RECONCILE_REQUIRED` with no artifact. Operational errors raised inside a provider also remain ambiguous. No automatic retry occurs. Only an explicit next consecutive attempt after `FAILED`/`SAFE_LOCAL` can invoke again. A real process crash can leave a worker lock; it is never automatically cleared. The KeyboardInterrupt test proves interrupted-record handling, not a real process-kill recovery claim.

Validation errors before source/request identity exists raise typed errors. Provider/registration failures after invocation produce diagnostic manifests. Corrupt persisted custody records raise operational errors instead of success receipts.

## A05 handoff

`LocalMediaRunner.run(MediaRequest(...))` returns `AssetJobManifest`. `manifest.result` returns a fresh A02-schema-valid JobResult, and `manifest.to_dict()` is its persisted manifest form. `runner.load(job_id, attempt)` validates the manifest and stored output without needing the original source file.

A05 can call `registry.verify(manifest.result["artifact"])` for verified bytes, or locate the artifact's relative path beneath `registry.root`. It must inspect mode/origin/state: synthetic mock and dry-run are explicit. Shared CLI integration, broader formats, model providers, scene composition and MP4 rendering remain deferred.

## Verification

Windows, Python **3.14.7**, 2026-10-29. Interpreter: `C:\Users\TRUNG\AppData\Local\Python\pythoncore-3.14-64\python.exe`.

Unchanged A01/A02 baseline: **41 passed**, exit 0, 2.78 s. Final combined command:

~~~text
python -m pytest tests/test_a04_media_operations.py tests/test_a02_contracts.py tests/test_a02_mock_provider.py tests/test_cli.py tests/test_foundation.py tests/test_isolation.py -q -p no:cacheprovider --basetemp=.pytest-a04-verified --tb=short
~~~

Environment overrides: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1`. Plugin autoload was disabled because an unrelated installed plugin imports SSL, whose DLL fails initialization here. The workspace basetemp avoids the unwritable default pytest temp area.

**Observed final result: 112 passed, 2 skipped, 0 failed, 22.11 s, exit 0.** This includes 71 passing A04 cases and all 41 unchanged A01/A02 cases. Coverage includes actual PNG/AVI fixtures/import, source modification and identity, collision, missing/corrupt output, traversal, invalid MIME, explicit mock/dry-run, provider policy, timeout/unknown effects, interrupted attempts, safe explicit retries, unknown cost, true source preservation and manifest replay validation.

Two actual-host link tests were skipped: symlink creation lacks the required privilege; a direct junction probe returned Access denied. Deterministic tests of symlink and Windows reparse rejection branches pass, as does a real root-replacement test. **Native link boundary behavior is not verified on this host.**

Python AST parsing, final newlines and trailing-whitespace checks pass. The WorkItem validates against `contracts/WorkItem.v0.3.schema.json`. File hashes confirm 137 pre-existing files unchanged and 11 additions confined to allowed paths.

Git status/diff checks could not execute: Git exits `3221225794` (`0xC0000142`, DLL initialization failure). The scope and whitespace checks above are separate filesystem checks, not a claimed successful `git diff --check`. The whole-repository EF suite and A05 integration were not run.
