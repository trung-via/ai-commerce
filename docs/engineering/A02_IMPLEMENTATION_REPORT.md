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

- **Not performed:** Windows A02 verification, real API/provider/model calls, image/video render, commercial product access, sending/receiving external messages, posting, spend, Codex/Antigravity coding executor, EF01/EF02 or wake.
- Cost/rights: source and generated mock artifact rights stay `UNKNOWN`; `NOT_APPLICABLE` cost specifically describes a no-charge local mock execution and is not a price estimate for a real provider.
- Mock `SUCCEEDED` means *local mock text produced*, not product/media/publishing success.
- Local content-hash/receipt paths are preparation for A04/O03, not a complete durable job queue or enterprise artifact store.
- Existing `WorkItem.v0.3`, `AuthorizationEnvelope.v0.3`, `PublicationRecord.v0.2`, `MeasurementPlan.v0.2` remain separate draft root contracts.
- A02 status `BUILD_VERIFIED_FOR_SCOPE` for these tested functions, **not** `LIVE_CONFIRMED` for creative provider.

## Windows confirmation (optional targeted environment step)

After `git pull --ff-only` in `C:\\TOOL\\ai-commerce` on the actual machine, run the README A02 commands. No result from that machine has been supplied for A02 at the time of this record.

## Next independent work

`EF01` code executor integration or `A03/A04` creative provider/media capabilities; `EF02` remote wake is optional and does not block offline creative.
