# A10 M01 Signal Intake Foundation Implementation Report

Date: 2026-10-29. Work item: `A10-OPPORTUNITY-AUDIENCE-001`, M01 only.
Authorization: Human ChatGPT 2026-10-29: "thực hiện theo deep research" / EF02 Issue #87 repair after run 40865316732 PARTIAL_OR_FAILED (0/5).
Executor: Antigravity (Gemini 3.8 Flash High).
Source SHA: canonical main `888181dae482390c3ed4a2ab70408288c82e15de` (fresh worktree, clean initial status, immutable prior evidence preserved, detached HEAD unchanged).

## 1. Context & Scope Repair

Run 40865316732 failed under Codex `gpt-6.1-sol/max` when the executor hit a hard provider quota limit and exited code 1 without completing verification. Additionally, `docs/engineering/A10_IMPLEMENTATION_REPORT.md` was outside the prior action's allowed path set. Human operator explicitly authorized resuming M01 with a new executor (Antigravity), starting from exact canonical main SHA `888181dae482390c3ed4a2ab70408288c82e15de` with `docs/engineering/A10_IMPLEMENTATION_REPORT.md` explicitly allowed.

All previous uncommitted Codex local edits were rejected and not reused. M01 implementation was completed cleanly and verified from scratch.

## 2. Architecture & Implementation

M01 provides the offline-first, provider-neutral signal intake foundation for A10 Opportunity & Audience Intelligence.

### 2.1 Contracts & Schemas

Three portable JSON Schema Draft 2020-12 contracts are established in `contracts/` and mirrored in `ai_commerce.opportunity.core.schemas`:

1. `SignalSourceSnapshot.v1.schema.json`:
   - Records human-supplied source provenance (`source_id`, `source_kind`, `source_ref`, `captured_at`, `collected_at`, `sha256`, `rights_status`, `verification_status`, `geo`, `locale`, `purpose`, `terms_url`, `notes`).
   - Rights and verification semantics strictly enforce user-supplied status: `verification_status` is pinned to `USER_SUPPLIED_NOT_LIVE_VERIFIED`. Access rights default to `UNKNOWN` and represent user declarations, never provider-verified permissions.
2. `TrendSignal.v1.schema.json`:
   - Normalized trend signal observation (`signal_id`, `import_id`, `ordinal`, `topic`, `signal_type`, `source_type`, `locale`, `geo`, `query_context`, `observed_at`, `observation_window`, `collected_at`, `timezone`, `unit`, `denominator`, `status`, `value`, `source_status`, `rights_status`, `evidence_refs`).
   - **AC03 Zero vs Null**: `status="AVAILABLE"` requires numeric `value` (preserving `0` as distinct from missingness), while `status="NOT_AVAILABLE"` requires `value=null`.
3. `SignalImport.v1.schema.json`:
   - Import envelope and ledger lineage record (`import_id`, `status`, `raw_sha256`, `raw_ref`, `raw_size_bytes`, `format`, `dialect`, `source_snapshot`, `mapping`, `context_sha256`, `scope_sha256`, `lineage`, `signal_count`, `source_status`, `recorded_at`).
   - Lineage relationships: `ORIGINAL` (parent is null), `CORRECTION` (same scope, same context, different raw bytes), and `REPROCESS` (same scope, same raw bytes, different mapping/dialect context).

### 2.2 Ingestion & Parsing (`ai_commerce.opportunity.signals.importer`)

- **Encoding & BOM**: Supports UTF-8 with optional Byte Order Mark (`\xef\xbb\xbf`), rejecting non-UTF-8 bytes and NUL bytes.
- **Configurable Column Mapping**: Supports explicit column mapping profiles (e.g. `default.trend.v1`, `google_trends.manual.v1`, and user-defined dictionaries). Rejects header mismatches, ambiguous duplicate headers, and missing required columns.
- **Canonical JSON**: Supports `ManualSignalRows.v1` envelope with strict parsing rejecting nonfinite floats (`NaN`, `Infinity`) and duplicate object keys.
- **Deterministic Identity**:
  - `import_id`: `a10-` followed by SHA-256 of canonical bytes covering raw bytes digest, source context digest, and lineage.
  - `signal_id`: `sig-` followed by SHA-256 of canonical bytes covering `import_id`, `ordinal`, and signal payload.

### 2.3 Append-Only Ledger (`ai_commerce.opportunity.signals.ledger`)

- Implemented in `SignalLedger` using SQLite with `PRAGMA application_id=0x41313031` ('A101') and `PRAGMA user_version=1`.
- Tables: `raw_evidence`, `imports`, and `signals`.
- Enforces immutability: SQLite triggers abort any `UPDATE` or `DELETE` with `A10_APPEND_ONLY`.
- Concurrency: Write transactions use `BEGIN IMMEDIATE`; read operations use `PRAGMA query_only=ON` within consistent read transactions.
- Idempotency: Re-importing identical bytes under the identical source context returns `{..., "duplicate": True}` and does not create duplicate entries or corrupt the ledger.
- Replay & Tamper Detection:
  - `ledger.inspect(import_id)` re-reads raw BLOB bytes, deterministically re-executes parsing, and verifies every record and normalized signal against stored cryptographic digests.
  - Any raw payload corruption, record manipulation, or signal alteration raises `SignalLedgerError`.
- Downstream API for M02/M03:
  - `ledger.query_signals(...)`: filters normalized signals by topic, locale, geo, source_type, unit, query_context, and status.
  - `ledger.list_imports(...)`: inspects imported batches without modifying storage.

### 2.4 Bounded Safety & File Operations (`ai_commerce.opportunity.signals.files`)

- Rejects path traversal (`..`), UNC paths, alternate data streams, symlinks, Windows reserved device names, and reparse points.
- Bounded limits enforced: max 8 MB file size, max 10,000 rows per import, max 64 KB per row, and max 4,096 chars per field.

## 3. Strict Project Boundaries

- **Offline-First**: All signal parsing, validation, and storage operate completely offline. Zero network connections, socket calls, live APIs, scraping, or OAuth flows.
- **No A09 Interference**: A10 uses independent SQLite storage, distinct schema definitions, and separate error types. No shared mutations or semantic changes to A09 `AnalyticsImport`.
- **No Premature Main CLI**: Main CLI (`src/ai_commerce/cli.py`) is preserved intact without edits (deferred to M05 per contract).
- **LF Line Endings**: All created JSON schemas, Python source files, tests, and documentation are strictly formatted with LF (`\n`) newlines.

## 4. Verification Evidence

- Focused M01 test suite: `pytest tests/test_a10_m01.py` executes 51 tests covering happy path, validation errors, zero vs null distinction, UTF-8 BOM, custom column mapping, duplicate keys, nonfinite floats, append-only SQLite triggers, idempotency, correction/reprocess lineage, tamper detection, and offline socket protection.
- Result: **51 passed in 0.32s**.
- Full project regression run (`pytest -n auto`): **1140 passed** (with 2 expected symlink skips on Windows).
- Newline check: `crlf_files = []` across all changed/created paths.

## A10 M05 bridge and composed offline v1 integration — 2026-10-29

This appended section records M05; the original M01 report above remains historic
and unchanged. WorkItem: `A10-OPPORTUNITY-AUDIENCE-001`. Human-selected executor:
Codex `gpt-6.1-sol`, effort `max`, EF02 action `A10-M05`, attempt
`a10-m05-codex61solmax-20261029-v1-04`. No main merge is performed by this action.

### Exact source and prerequisite evidence

The clean effective worktree starts at
`e8e482ef041be7d66650b438123c27143665bb73` (EF02 dependency integration), directly
above requested source `7e1f5f22274ea8ae1e238d0126f16ef4fc7f7e5d`.
The runner's saved M04 result is `CANDIDATE_READY`, candidate
`4f1f40f38711401fdb90195c898a5e90386051e3`, ref
`refs/heads/ef02/candidate/d83306f97716712e8b836048c52794df`.
Its registered worker log records **1409 passed, 2 skipped**, exit 0.
All seven candidate payload hashes match this integration's exact Git blobs.
Four M04 Python files have ordinary Windows LF-to-CRLF checkout conversion; their
raw checkout hashes were captured independently and their bytes are preserved.
M01 core/signals, M02/M03/M04, existing A03 contracts and all other components
remain unchanged. The shared opportunity `__init__` receives only its authorized
M05 description/export additions; existing M01 callables are preserved.

The source map read before implementation covered the WorkItem, README, registered
verification profile, actual M01 importer/ledger, M02 build/evaluate API and local
tests, M03 notes/ledger/evidence refs, M04 source/schema/tests, actual A03
ContentBrief schema, intake/edit/store consumers and their tests. No provider or
GitHub API call was used for this action's source reconciliation.

### Implemented behavior

`src/ai_commerce/opportunity/bridge` maps real M04 proposals into A03's unchanged
`ContentBrief.v0.1`: required brief ID/objective, optional prompt/audience, nullable
product/CTA and optional media hint. It calls `validate_record` and `intake_brief`.
The separate `OpportunityCreativeBridge.v1` contract defines a provenance receipt
inside A03's allowed extension fields. It retains the full immutable source
proposal, original text, source/raw hashes, note statuses, reasons/counterevidence,
zero versus missing values, lineage, explicit Human edits and synthetic label.
Source status remains unverified, interpretation remains inferred and rights
remain unknown; bridge-only validation does not claim renewed ledger replay.

Human freeform intake bypasses signal/trend/audience/proposal requirements.
Exact Unicode/whitespace/newlines and separate optional file-byte hashes are
retained. Human edits and ordinary A03 revisions/reframing remain available.
No media format, product, CTA or concept quota is introduced. The shared CLI
adds real `opportunity import/inspect/trend/audience/propose/bridge` adapters,
strict local JSON reads, bounded diagnostics, explicit source selection and
optional A03 store round-trip. Common contacts are rejected before persistence;
deidentification remains the caller's responsibility for names/indirect identifiers.

`scripts/a10_demo.py` executes the real CLI/API chain from localized synthetic
CSV BOM/CRLF and JSON through independent snapshots, trend/insight, proposal and
A03 intake/store. A sparse branch preserves zero/unavailable null and insufficient
history. Every toy artifact is explicitly synthetic; it is not market evidence or
a claimed native provider export. No model or concept generation is performed.

Fresh M01 imports keep their actual `recorded_at`, so independent fresh journals
can have different full-record hashes. Exact reproducibility is provided by
`--replay-from`: checked retained journals are copied into a new local directory
and all real APIs run again. Final artifacts/journals match byte for byte; a
separate execution receipt preserves actual fresh versus duplicate flags. No clock
patching, source mutation or fabricated verification is used.

```powershell
python scripts/a10_demo.py --output .local/a10-demo-first
python scripts/a10_demo.py --replay-from .local/a10-demo-first --output .local/a10-demo-replay
$env:PYTHONPATH = "src"
python -m ai_commerce opportunity --help
```

### Windows verification, preserved failures and limits

Observed environment: Windows, Python 3.14.7, pytest 8.4.2, xdist 3.8.0. The
registered `project-tests` argv is `python -m pytest tests -q -n 12`.
Observation runs append only `-v` to expose actual worker creation. Tests use a
child environment with token/secret/credential variables removed. M02 component
tests live outside the registered `tests/` selection, so affected verification
explicitly includes them.

The initial observational root run reported **1406 passed, 5 skipped**, exit 0,
with 12 workers, but implementation files were edited during it. Its binding is
explicitly **INVALIDATED** and is not reused as untouched-baseline or final proof.
Raw logs remain available. Two local preflight/tooling errors are also retained:
an initial raw-hash assertion exposed the documented CRLF conversion, and a
schema-generation helper initially lacked the checkout `src` import path. Neither
was a pytest result or an uncertain external operation.

Affected command for all three implementation verification rounds:

```text
python -m pytest tests/test_a10_m05.py tests/test_a10_m04.py tests/test_a10_m03.py tests/test_a10_m01.py src/ai_commerce/opportunity/trends/tests/test_m02.py -q -n 12 -v
```

| Round | Actual result | Diagnosis / change |
| --- | --- | --- |
| Initial | 331 passed, 1 failed, 5 errors, 0 skipped; exit 1 | Demo passed both comparison contexts when selecting one import; M02 rejected unbound context. |
| Repair 1 | 338 passed, 1 failed, 0 errors, 0 skipped; exit 1 | Exact fresh-journal reproduction exposed M01's real ingestion timestamp; sources were preserved. |
| Repair 2 | 339 passed, 0 failed, 0 errors, 0 skipped; exit 0 | Explicit retained-journal replay establishes exact reproducibility. |

All three logs show **12/12 workers created**, and the source hashes were stable
through each affected invocation. Both bounded repair rounds are used; no further
repair is authorized by this action. Actual fresh/replay script commands also
passed, with identical stdout SHA-256
`f1ac1014031f81e6145aef3d2094aab6d7d215eeefbd1ffca62477842491ada4`.
The A03 brief digest in those observed demo packages is
`1860e2425251481e1672c8f5d491315d49c701f9651bcc8629187260c128b48c`.

Full registered project verification, exact test counts, skipped reasons, code
hashes, scope reconciliation and the final AC review are recorded in the M05
component's `verification.json`, with raw artifacts under `.local/a10-m05/`.
Full root verification includes A03/A06/A07/A08/A09 regressions; component-local
M02 coverage is established by the separate affected run above.

Only the granted bridge/contract, top CLI/opportunity exports, M05 tests/demo and
this report are changed. All returned text uses LF; unchanged dependencies retain
their captured starting bytes. `git diff --check` is required. No Ubuntu execution
or CI PASS is claimed. A10 remains suggestion-only, without external providers,
live APIs, OAuth, scraping, account access, paid models, posting, affiliate effects,
dashboard or changes to A09 semantics. Candidate publication remains the existing
EF01/EF02 source/hash/scope/verification publisher's responsibility after this
executor returns; no local commit, push, external publication or main write occurs.

Final registered Windows run on the composed M05 source:
`python -m pytest tests -q -n 12 -v` — **1460 passed, 0 failed, 0 errors,
5 skipped in 170.68s**, exit 0. The log shows **12/12 workers created** and
1465 collected items; all source bytes were stable during verification. Stdout
SHA-256: `62051b022dd5b6385fbb426e8acec42b608f89c6a41035205dd2d9421a6f2d54`.
Two skips are A04 directory symlink/junction permission checks, one is an A04
hardlink permission check, and two are A09 symlink permission checks. These
represent unavailable native link proof on this Windows sandbox, not test PASS.
The final affected suite adds M02's component-local coverage: **339 passed,
0 failed, 0 errors, 0 skipped**, exit 0, 12 workers, 11.28s. M05 contributes
54 tests. Metadata/report results are finalized after these successful runs;
the tested implementation, schema, tests and demo bytes remain unchanged.
