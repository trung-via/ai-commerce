# A12-01 repair round 1 — scoped tests verified; full profile NOT PASS

Attempt: `a12-01-codex61solmax-20261030-v1-02`, EF01 repair round **1** of **2**.
Original and final HEAD: `36d1290e068342747bf0953010581d0e9cb934ab`.
This fresh executor invocation repaired the existing worktree; it did not reuse
the stopped Issue #106 claim or publish a candidate.

## Evidence and causal repair

The full repair feedback, source snapshot, empty tracked diff, stdout, stderr
and previous invocation result were read and their supplied SHA-256 values
verified under the Human-provided `evidence_root`:

```text
C:/TOOL/ai-commerce-runner/_work/ai-commerce/ai-commerce/.local/ef02/
a12-product-evidence-codex61solmax-20261030-v1-02-worktree-repair/
A12-01-a12-01-codex61solmax-20261030-v1-02/ef01/
```

Inputs are `invocations/001/repair_feedback.json`,
`invocations/000/source.after_verify.json`, `git.after_verify.diff.patch`,
`verify.stdout.log`, `verify.stderr.log` and `invocation_result.json` (the last
five within `invocations/000/`). All 12 cumulative candidate files matched
their recorded hashes before this repair. The worker's prior registered run
reported **2 failed, 1991 passed, 6 skipped**; both failures were the A12 local
reader's `SOURCE_CHANGED_DURING_READ` check.

A read-only probe reproduced unequal `lstat().st_ctime_ns` and
`fstat().st_ctime_ns` for an unchanged existing contract file, with identical
device, inode, size and modification time. Windows path and descriptor APIs
can give ctime different semantics. The repair compares device, inode, size
and modification time across APIs, then compares ctime separately between
the two path inspections and between the two descriptor inspections. It
preserves bounded reads, root containment, link/reparse rejection, size checks
and replacement/mutation detection.

Only `files.py` and `tests/test_a12_snapshot.py` changed in the implementation
and tests. Twelve new cases cover reproducible BOM JSON/CSV reads with unequal
API ctimes and changes to device, inode, size, modification time or ctime in
either API. The [repair diff](repair_evidence/round1.repair.patch) was generated
against reconstructed originals whose bytes match the feedback hashes.
All remaining implementation files and all three schemas retain their prior
bytes. This report was extended and repair evidence added within the allowed
product path.

## Observed verification

Windows 11, CPython **3.14.7**. No Python 3.11 runtime or Ubuntu run was performed.

| Stage | Command | Exit | Observed result |
| --- | --- | --- | --- |
| New regression before the fix | `python -m pytest tests/test_a12_snapshot.py -q -k "stat_api_ctime_difference or file_change_within_stat_api"` | 1 | 2 failed, 10 passed, 124 deselected, 0.40 s |
| Complete A12 tests after the fix | `python -m pytest tests/test_a12_snapshot.py -q` | 0 | **136 passed, 12.24 s** |
| Registered project-tests | `python -m pytest tests -q -n 12` | 1 | **30 failed, 1966 passed, 15 skipped, 213.78 s** |

The full profile has **no A12 failures**. Its failures are in
`test_ef01.py` (13), `test_ef01_ef02_integrity.py` (3),
`test_ef01_git_write_guard.py` (6), `test_ef01_performance.py` (1), and
`test_ef01_repair.py` (7). Git fixture setup returns 128 with a
reference-transaction hook launch failure, `NtCreateDirectoryObject` /
`0xC0000022`; the performance test asserts that inherited `GIT_CONFIG_COUNT`
must be absent. These are observed failures in this sandbox. No pristine
baseline comparison or outside-scope repair was performed. No environment,
hooks, Git options or guard were altered to rerun those failures.
Skips report unavailable native symlink, hardlink or junction permissions.

Exact stdout/stderr bytes are retained locally in
`repair_evidence/round1.{repro,targeted,project-tests}.{stdout,stderr}.log`.
The `.log` files follow the existing ignore rules. The corresponding JSON
records retain their hashes, commands, exits and tested source hashes;
[failure IDs](repair_evidence/round1.failures.json) and
[static results](repair_evidence/round1.static.json) are also preserved.
The complete full-profile log was saved before the tool display truncated it.
Tested implementation/schema/test hashes were unchanged after the full run.

All product Python files and the A12 test file pass
`ast.parse(feature_version=(3, 11))` and trailing-whitespace checks.
`git diff --check` returns 0; the cumulative files are untracked, so their
whitespace was also checked directly. Final scope checks found only allowed
paths and the original HEAD. No executor Git write command was issued.

AC01 and baseline AC02 are **SATISFIED within the observed offline A12 tests**.
The registered profile remains **NOT PASS** in this executor sandbox;
authoritative acceptance and candidate publication remain with EF01/EF02.
Rights, registry, shared CLI and other descendants remain outside this action.

---

## Prior invocation report — retained historical evidence

# A12-01 local verification — NOT PASS

Source: `trung-via/ai-commerce`, SHA
`36d1290e068342747bf0953010581d0e9cb934ab`.
Initial `git status --porcelain=v1` was empty. HEAD was unchanged after tests.
Only new files under the action's allowed paths were authored. No Git publishing
or configuration command was issued by the executor.

Observed runtime: Windows, Python **3.14.7**, pytest 8.4.2. Python 3.11 syntax was
also checked with `ast.parse(feature_version=(3, 11))`; an actual Python 3.11 or
Ubuntu run was not performed. This is worktree evidence, not an EF01 acceptance,
candidate publication, hosted CI result, merge or external product verification.

## Observed commands and results

| Stage | Command | Exit | Result |
| --- | --- | --- | --- |
| Initial targeted | `python -m pytest tests/test_a12_snapshot.py -q` | 1 | 118 passed, 1 failed, 2 errors, 1.57 s |
| Repair 1 targeted | same command | 1 | 115 passed, 8 failed, 9.05 s |
| Repair 2 targeted | same command | 1 | 123 passed, 1 failed, 8.00 s |
| Registered project-tests | `python -m pytest tests -q -n 12` | 1 | **1951 passed, 33 failed, 15 skipped, 254.63 s** |

Initial failures were a test's incorrect expected mapping error and Windows'
32767-character environment-variable limit: pytest included a 4 MiB payload in
its generated parameter ID. Explicit short parameter IDs fixed the latter.
Repair 1's failures exposed timestamp error-code ordering after adding the
strict timestamp schema pattern. Repair 2 validates the observation timestamp
before general row validation. No third repair was attempted.

Successful cases include exact BOM/raw hashes, deterministic canonical bytes,
immutable snapshots, detached access, raw replay and rehashed-forgery rejection,
namespace isolation, GTIN/SKU assertions, unknown versus zero, JSON/CSV failures,
provider-export projection, bounded inputs, a complete 2000-row source, simulated
symlink/reparse rejection, A02 schema isolation and optional Creative product refs.
Runtime schemas equal the three portable JSON mirrors. Static Python 3.11 syntax
and new-file whitespace checks passed; `git diff --check` returned exit 0.

## Unresolved A12 failure

Fingerprint: `local-file-open | Windows/Python 3.14.7 | ProductError |
SOURCE_CHANGED_DURING_READ | files.py:68 | source 36d1290e...`.

The final full profile failed these two A12 tests:

* `test_csv_bom_multiline_attributes_leading_zeros_and_optional_empty_cells`
* `test_outside_root_directory_missing_and_unknown_format`

Both fail before parsing, at the comparison of `lstat()` and `fstat()` signatures.
The final targeted run failed only the second test. A subsequent **read-only**
probe compared two existing component files 32 times each: 64/64 comparisons had
equal device, inode, size and `mtime_ns`, but unequal `ctime_ns`. Example from
`contracts/ProductListingSnapshot.v1.schema.json`:

```text
lstat: size=15358, mtime_ns=1793322464323059100, ctime_ns=1793322161526056700
fstat: size=15358, mtime_ns=1793322464323059100, ctime_ns=1793322464323059100
```

This directly supports a platform-specific false rejection by the current
signature comparison. The next authorized repair must reconcile these timestamp
semantics while retaining file identity, bounded-read, link and concurrent-change
checks, and rerun targeted and registered tests. It must not weaken the Git guard.

## Other full-profile failures and limits

The other **31 failures** are outside A12, in EF01/EF02 tests. Observed signatures
include fixture `git init` exit 128 / reference-transaction hook launch failure
`0xC0000022`, an inherited `GIT_CONFIG_COUNT` assertion, and
`test_concurrent_registry_writers_preserve_routes_and_claims` raising
`REGISTRY_UNAVAILABLE`. The required profile's existing tests attempted these
fixture operations; the executor did not retry them with altered Git options,
configuration, hooks or environment. No outside-scope files were repaired. A
pristine baseline comparison was not run, so these are observations rather than
a claim that all 31 are pre-existing defects. Skips report unavailable native
symlink/hardlink/junction permissions in this host.

Actual stdout evidence is in the execution transcript: initial command chunk
`f23e73`; repair 1 completion `ae0f3f`; repair 2 `b4b5f0`; full profile start
`03d77f` and completion `1af8f3`. Large failure outputs were truncated by the
tool transport (the first run generated oversized parameter IDs); this report
does not claim to contain complete raw stdout. Test totals and failure summaries
above were observed in the returned logs.

AC01: **UNSATISFIED** as a complete local reader, pending the Windows file guard
repair. AC02 baseline identity primitives: **SATISFIED within the tested offline
scope**, without canonical product resolution, registry or correction history.
Rights, registry, shared CLI, evidence packs and other descendants remain out of
this action. The worktree must not be represented as a verified candidate.
