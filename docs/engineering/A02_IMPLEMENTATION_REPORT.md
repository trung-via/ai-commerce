# A02 — Contracts & Product Adapters implementation evidence

Date: 2026-10-28. WorkItem: `A02-CONTRACTS-001`. Canonical source base: `trung-via/ai-commerce@544d8c279be64fa38105a93ec6f2d59a2be4f6bd`.

## Scope

- `G04` product schema package: 5 portable and packaged JSON Schema Draft 2020-12 contracts (`ContentBrief`, `MediaArtifact`, `SourceSnapshot`, `EvidenceRef`, `JobResult`), validation with meaningful error path, schema resources included in built wheel. Existing root v0.3 draft schemas retained without modification.
- `G05` provenance: sha256 over original brief bytes, `snapshot_id`, `artifact_id`, observed timestamps, source and local artifact references, content-addressed filenames, immutable output on repeated same-input requests; missing/tampered output refuses to report a previously completed result.
- `G07` provider-neutral PRODUCT interface: `ContentProvider` protocol and `ProviderCapabilities`; `MockContentProvider` produces **TEXT ONLY** to exercise contract and provenance, not an image or video. Mock runner explicitly rejects providers requiring network or potentially charging.
- CLI commands: `ai-commerce validate --schema ContentBrief --file ...` and `ai-commerce mock-generate --brief ... --out-dir ...`.
- Sample content-only brief with `product_ref=null`, `cta=null`; no fixed creative format, SKU, narrative, quantity or product rights precondition for ideation.

## Verified local results

Test environment: isolated Linux runtime; Python 3.13; pytest 9.0.2; `jsonschema` 4.26.0. **This is not a Windows machine-attested test.**

| Command/action | Observed result |
|---|---|
| `python -m pytest tests -q` | **31 passed in 14.00s** (A01:11; A02:20) |
| `python -m pip install --no-build-isolation -e . -q` | Successful |
| `python -m ai_commerce --help` | Shows `hello`, `about`, `validate`, `mock-generate` |
| `python -m ai_commerce validate --schema ContentBrief --file tests/fixtures/creative_only_brief.json` | `VALID ContentBrief` |
| `python -m ai_commerce mock-generate --brief tests/fixtures/creative_only_brief.json --out-dir .local/a02-smoke` | `SUCCEEDED`, `kind=text`, `provider=local-mock`, SHA-bound artifact and receipt |
| `python -m pip wheel --no-build-isolation --no-deps . -w <temporary-dir> -q` | Wheel built; verified all 5 schema files packaged |
| Invalid brief, invalid sha/time, nested JobResult schema invalid, unsupported schema | Rejected in negative tests |
| Same source replay, changed source, missing artifact, tampered receipt, mock provider error | Correct result/diagnostics in tests |

The GitHub-uploaded blobs of all implementation and test files were compared with `git hash-object` of the **exact files under test**. This proves byte identity of these sources before publication, not external runtime execution.

## Truth boundaries

- **Not performed in original local build:** Windows A02 verification (later separately observed in Human screenshot), real API/provider/model calls, image/video render, commercial product access, sending/receiving external messages, posting, spend, Codex/Antigravity coding executor, EF01/EF02 or wake.
- Cost/rights: source and generated mock artifact rights stay `UNKNOWN`; `NOT_APPLICABLE` cost specifically describes a no-charge local mock execution and is not a price estimate for a real provider.
- Mock `SUCCEEDED` means *local mock text produced*, not product/media/publishing success.
- Local content-hash/receipt paths are preparation for A04/O03, not a complete durable job queue or enterprise artifact store.
- Existing `WorkItem.v0.3`, `AuthorizationEnvelope.v0.3`, `PublicationRecord.v0.2`, `MeasurementPlan.v0.2` remain separate draft root contracts.
- A02 status `BUILD_VERIFIED_FOR_SCOPE` for these tested functions, **not** `LIVE_CONFIRMED` for creative provider.

## Windows validation issue and cross-platform fix (2026-10-28)

Human supplied a Windows PowerShell screenshot after commit `92d85f09f84d35f3019b7508eadd7fe884aeea04` showing: editable installation succeeded; mock-generate produced an artifact and returned `SUCCEEDED`; pytest **30 passed, 1 failed (2.00s)**. Failing test: `tests/test_a02_contracts.py::test_digest_and_time_format_are_checked`; changing `captured_at` to `someday` did not raise `ContractValidationError`.

**Root cause:** the application used default `jsonschema.FormatChecker()` for the JSON Schema `format: date-time` keyword. That optional format check can be absent in an environment without the RFC 3339 checker (dependency availability differs by Python installation). Windows screenshot alone does not prove which optional package was installed; the missing-checker behavior was reproduced in the regression test by temporarily removing the date-time checker.

**Fix:** locally register a deterministic timezone-aware date-time checker in `src/ai_commerce/contracts/schema.py` using stdlib regex plus `datetime.fromisoformat`. Validation now rejects unparseable timestamps, absent timezone, impossible dates and offset errors without relying on optional third-party format checkers; embedded EvidenceRef/JobResult references use the same checker. Existing valid `Z`/offset strings continue to pass. No product contract meaning, model configuration or provider rights were widened.

**Observed patch verification:** local Linux Python 3.13.5 `python -m pytest tests -q` **41 passed in 13.25s** (previous 31 + 10 date/time assertions). `python -m compileall -q src/ai_commerce` succeeded; wheel built and included all five packaged schemas and updated validator. Exact updated two source/test file SHA blobs matched the files actually tested.

**Windows status after fix:** RETEST_PENDING. The initial Windows validation remains **30 PASS / 1 FAIL** for the previous revision; no post-fix Windows run has been observed yet. This correction is not an EF01/EF02 implementation or a real media production provider.

## Windows confirmation (optional targeted environment step)

On Windows PowerShell from `C:\\TOOL\\ai-commerce`, fast-forward the Git checkout to the fixing commit, reinstall editable and rerun `python -m pytest tests -q`. The expected result is 41 passed, but **do not mark Windows PASS until the Human provides the actual output**.

## Next independent work

`EF01` code executor integration or `A03/A04` creative provider/media capabilities; `EF02` remote wake is optional and does not block offline creative.


## Windows retest PASS — 2026-10-28 (Human-provided screenshot)

Human provided a **new** Windows PowerShell screenshot showing the A02 date-time fix retest. Observations visible:
- Working directory: `C:\TOOL\ai-commerce`.
- Editable package installation: `Successfully installed ai-commerce-0.1.0`.
- `python -m pytest tests -q`: **41 passed in 2.08s**, no failures shown.
- Python site-packages paths reference `pythoncore-3.14-64` / `Python314`, but the output does not include a direct `python --version` invocation.
- The same screenshot does not show `git rev-parse HEAD`; **exact local checkout SHA is not attested**.

**A02 Windows verification verdict: PASS (Human-supplied screenshot)** for package installation and targeted tests. This supersedes the earlier failed run and the historical RETEST_PENDING notice above; it does not erase that earlier failure from the record. No Windows native executor, EF01, EF02, media generation, provider charge or publish evidence is implied. WorkItem remains `BUILD_VERIFIED_FOR_SCOPE`.
