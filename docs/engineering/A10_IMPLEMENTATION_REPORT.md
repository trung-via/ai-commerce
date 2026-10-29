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
