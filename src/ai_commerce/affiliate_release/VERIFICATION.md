# C08 fresh local verification — 2026-10-30

This attempt uses the supplied isolated EF02 worktree
`ai-commerce-ef02-A13-C08-ax_eoma1` at source and unchanged HEAD
`07ce567ee6250d162eaf695fc74a7f1def9bb001`. Initial Git status was clean.
No Git configuration, HEAD, refs, existing implementation, or workflow was
changed by this executor. Candidate SHA/ref: **NONE**; publication belongs to
EF02.

The previous C08 design was inspected in the retained `gun396p8` worktree and
reimplemented here against the actual A05/A06 APIs. Its previous test results
and run 41026901401 are historical context only; no candidate or verification
evidence from that run is claimed for this attempt. The supplied #135 repair
reason authorizes the exact new ordinary test path
`tests/test_a13_media_inspection.py`. No allowlist, EF01, EF02, or Git guard was
edited. The schema is exposed within this component; no global contract file
is added outside the declared component/test scope.

## Fresh executed checks

Runtime: Windows, Python 3.14.7, pytest 8.4.2, pytest-xdist 3.8.0. Local FFmpeg
and FFprobe are installed and used by the real-media tests. The unchanged
registered `project-tests` profile is `python -m pytest tests -q -n 12`, and
`SELECTED_WORKERS` is 12. Runs below used `-n 12`; the initial full-suite header
reported `created: 12/12 workers` and subsequent output identified xdist workers.

| Check | Observed result |
| --- | --- |
| Initial full suite, before C08 implementation | 2,532 passed, 36 failed, 17 skipped; exit 1; 269.65 s |
| New C08 suite before implementation | Collection failed because `affiliate_release` did not exist; exit 1 |
| Initial C08 implementation | 37 passed, 1 skipped; exit 0; 9.54 s |
| C08 plus A04/A05/A06/A07/A08 and all A12 test files | 932 passed, 6 skipped; exit 0; 115.28 s |
| Digest-negative reproduction | 1 failed, 37 passed, 1 skipped; exit 1; 7.52 s |
| Final C08 after one same-executor repair | **38 passed, 1 skipped; exit 0; 8.32 s** |

Initial full-suite command: `python -m pytest -n 12`. It collected the 2,585
existing tests. Its 36 failures were in existing engineering tests:
`test_ef01.py` (13), `test_ef01_performance.py` (1),
`test_ef01_ef02_integrity.py` (3), `test_ef01_git_write_guard.py` (6),
`test_ef01_repair.py` (7), and `test_git_byte_consistency.py` (6).
Reported failures included sandbox MSYS access denial (`0xC0000022`) and
reference-transaction-hook rejection while fixtures created temporary Git
repositories. No environment, hook, or guard bypass was attempted; these
unrelated failures were left for official EF01 verification. This attempt
does **not** claim a full project-tests PASS.

Focused C08 command, including the final run:

```text
python -m pytest tests/test_a13_media_inspection.py -q -n 12
```

Combined regression command:

```text
python -m pytest tests/test_a13_media_inspection.py tests/test_a04_media_operations.py tests/test_a04_upgrade_v11.py tests/test_a05_render.py tests/test_a05_composition.py tests/test_a05_integration.py tests/test_a06_e01.py tests/test_a07_capability.py tests/test_a08_distribution.py tests/test_a12_snapshot.py tests/test_a12_identity.py tests/test_a12_claims.py tests/test_a12_offer.py tests/test_a12_rights.py tests/test_a12_registry.py tests/test_a12_integration.py tests/test_a12_cli.py -q -n 12
```

The combined run preceded the final schema-only digest repair. The final C08
run covers that repair; unchanged dependency tests were not repeated. The new
negative test demonstrated that a resealed receipt could contain a newline
after its asset hashes because a regex end anchor alone permitted it. The
schema now requires exactly 64 digest characters. Malformed probe format-name
types also yield `UNKNOWN / MEDIA_FACTS_INCOMPLETE` rather than an exception.

The focused skip is actual OS hardlink/symlink creation denied by the sandbox.
Deterministic tests for hardlink, symlink, reparse, and ancestor-reparse
rejection all passed. The other combined-run skips are three existing A04
and two A12 native-link cases. Those tests do not establish native link
creation permissions on this host.

## Acceptance coverage and limits

- Real MP4 inspection records format, codec, size, exact rational duration,
  stream properties, decoded frame count, tool versions, and full-decode
  completion. Identical requests return byte-identical receipts.
- Audio-free black art and a real silent AAC track pass. Optional black/silence
  measurements remain advisory; diagnostic tool failure does not veto valid
  media. No universal resolution, codec, audio-presence, or black-frame creative
  acceptance rule is introduced.
- Corrupt containers/payloads, changed pinned bytes, same-size source mutation
  with restored mtime, capture/source growth, changed A06 manifests, unsafe
  paths, and altered/resealed inconsistent receipts are rejected.
- Missing tools/decoders, resource limits, incomplete or malformed facts, and
  unsupported declared containers produce UNKNOWN without fabricated PASS.
  Actual subprocess timeout and both stdout/stderr byte bounds are exercised.
- Only requested assets are captured. Paths and linked ancestors are checked
  using the existing media API. Tools see private, bounded copies with file-only
  protocols, forced MP4/MOV demuxing, and disabled external references.
- A real A06 run supplies a hash-bound manifest and delivered media. Full A06
  custody verification remains explicitly `NOT_PERFORMED` because it would
  inspect unrequested source assets. A05 render-plan conformance remains in A05.
- Real A12 ProductEvidencePack, A07 evaluator, and A08 assisted-handoff tests
  ran unchanged. C08 supplies only technical review evidence; all receipts
  retain rights/account/link UNKNOWN and publication NOT_ATTEMPTED.

Current container coverage is MP4 audio/video. A receipt checksum is not an
issuer signature, proof of rights/account eligibility, or a guarantee that
caller-controlled paths cannot change after inspection. No OAuth, platform
call, link-validation network, media publishing, spending, or secret retrieval
was performed.

## Final scope

Only these ordinary worktree files are part of this attempt:

```text
src/ai_commerce/affiliate_release/__init__.py
src/ai_commerce/affiliate_release/inspection.py
src/ai_commerce/affiliate_release/process.py
src/ai_commerce/affiliate_release/schemas.py
src/ai_commerce/affiliate_release/README.md
src/ai_commerce/affiliate_release/VERIFICATION.md
tests/test_a13_media_inspection.py
```

Final static verification checks Python 3.11-compatible parsing, the Draft
2020-12 schema, source whitespace, read-only Git diff/status, exact changed-path
scope, and unchanged HEAD. No existing tracked source file is modified.
