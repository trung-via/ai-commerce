# A12-03 — scoped verification passed; project-tests NOT PASS

This is the new A12-03 attempt on source
`5e17381b5cc1d0ef05a56de0138a3972bc524169`, phase 3/M4. Initial executor
`git status --short` was empty. The source inherits A12-01 and the fresh A12-02
candidate `6161906f7bf5bb17f1332e2d8c4a2d6865c2a5e0`. An actual read-only
candidate-to-source diff contains only `.gitattributes`; this attempt did not
reimplement those dependencies or claim that blocked downstream actions ran.
Historical A12-01/02 verification records remain unchanged.

Only allowed product-component paths, `ProductAssetRights.v1.schema.json`, and
the two authorized new test files were edited. A02 and Creative/Opportunity/A11
contracts were read before implementation. A02/A03/A07–A11 were not edited.
The registry does not import or mutate A11. Freeform `product_ref=None` remains
valid. Eighteen inherited source/schema/test files have empty worktree diffs;
the shared product core/schema changes only add the new rights contract.

Implementation: immutable asset/source hashes and raw provenance; distinct
declared/effective commercial, advertisement and derivative rights; exact
holder/license/scope review with explicit temporal/territory terms; UNKNOWN and
RESTRICTED declaration protections; local asset-byte matching at use assessment.
The independent SQLite registry preserves exact accepted raw bytes, checks
record replay, serializes controlled append transactions, retains correction
and counterevidence lineage, rejects collisions/forks, detects local hash/schema
damage, and rolls back failed replay batches and inserts. Module docstrings and
[RIGHTS_REGISTRY.md](RIGHTS_REGISTRY.md) document bounds, privacy exclusions,
unknowns and the narrow local-review exception. No legal authentication, source
trust upgrade, affiliate permission, live price, publication or external
connector readiness is claimed. No URL or paid provider was called.

## Observed verification

Actual runtime: Windows, CPython 3.14.7, pytest 8.4.2. Python 3.11 grammar was
checked with `ast.parse(feature_version=(3, 11))`; actual Python 3.11/Ubuntu and
hosted-worker verification were not run. Raw stdout/stderr are retained in
`a12_03_evidence/*.log` (the existing ignore rule applies). Adjacent bounded JSON
records pin commands, exits, source hashes, durations and exact stream SHA-256.

| Run | Observed result |
| --- | --- |
| Initial targeted | 159 passed, 2 failed, 2 skipped, 2 errors; 7.84 s |
| Repair 1 targeted | 162 passed, 2 skipped; 7.91 s |
| Repair 1 project-tests | 2,277 passed, 32 failed, 17 skipped; 189.85 s |
| Repair 2 targeted | **167 passed, 2 skipped; 7.87 s** |
| Repair 2 project-tests | **2,284 passed, 30 failed, 17 skipped; 184.03 s** |

Targeted command:
`python -m pytest tests/test_a12_rights.py tests/test_a12_registry.py -q -n 12`.
Registered profile command: `python -m pytest tests -q -n 12`.
Every run's source hashes matched before/after. Final tests include offline
fan-in from the inherited snapshot, claim and offer APIs through rights review,
SQLite reopen/replay, preserved raw BOM/CRLF and freeform Creative intake.
The two targeted skips are unavailable native hardlink/symlink permissions.

Initial failures were an incomplete-scope diagnostic, a test helper that treated
`raw=None` as its default fixture, and an oversized generated pytest parameter
ID exceeding Windows' 32,767-character environment limit. Repair 1 added an
early provenance error and corrected the fixtures/parameter IDs. The initial
derived JSON summary clips oversized IDs and retains full-line digests; the
original raw stdout/stderr bytes are unchanged.

Repair 1's full run exposed an A12 concurrent preflight IO failure. A bounded
deterministic repro confirmed that another SQLite connection removing a
transient journal between presence/lstat checks produced `REGISTRY_IO_ERROR`
with `FileNotFoundError`, while event count stayed zero. Repair 2 checks the
leaf directly and permits a disappearing sidecar only after rechecking parents.
New regressions cover all three sidecar names and still reject directories;
concurrent same/different streams and competing corrections pass. Both repair
rounds stayed in this executor and the authorized component.

## Final profile failure boundary and handoff

The final full run has **zero A12 failures**. All 30 failures are outside the
action's allowed paths:

| Test file | Failures |
| --- | ---: |
| `test_ef01.py` | 13 |
| `test_ef01_git_write_guard.py` | 6 |
| `test_ef01_ef02_integrity.py` | 3 |
| `test_ef01_repair.py` | 7 |
| `test_ef01_performance.py` | 1 |

All 29 Git fixture failure blocks contain a reference-transaction-hook
rejection; some also contain MSYS `NtCreateDirectoryObject` / `0xC0000022`.
The remaining assertion requires `GIT_CONFIG_COUNT` absent from the child
environment, where it is inherited. The earlier full run also observed one
out-of-scope `test_ef02_wake_d2` concurrency/`REGISTRY_UNAVAILABLE` failure; it
did not recur in the final run. No pristine baseline comparison was run, so no
blanket claim of pre-existing code defects is made. Per-block hashes, node IDs
and signatures are in `round{1,2}.project-tests.failures.json`.

The full project-tests profile is **NOT PASS**. No hooks, environment, Git
configuration, EF01/EF02 infrastructure or outside-scope files were changed to
avoid these failures. The two-round repair budget is exhausted. The scoped
worktree and evidence are ready for EF01 review/verification in its authorized
worker environment. HEAD is unchanged; no candidate was published, no Git ref
was changed and no main merge or external publication occurred. Candidate
publication remains exclusively the EF02 worker's responsibility.
