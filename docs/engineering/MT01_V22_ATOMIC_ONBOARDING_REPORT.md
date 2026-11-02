# MT-01 V2.2 — Atomic Component Onboarding engineering checkpoint

## Authorization and status
- Human approved Option C on 2026-11-03; [Issue #260](https://github.com/trung-via/ai-commerce/issues/260) records normative scope and mandatory negative acceptance.
- Main at branch creation: `e96c374a90f4a786710a1b39c126d234b34160d9`.
- **STATUS: PARTIAL IMPLEMENTATION / DRAFT / NO ENGINEERING OR BRAIN ACCEPTANCE.**
- Current patch only introduces a pure bounded, data-only new-owner manifest validator in `impact_v21.py` and adversarial tests. This validation alone does **NOT** enable EF02 onboarding, candidate-based test execution, trusted CI or production merge.

## Current code
`validate_component_onboarding_manifest()` enforces strict version+fields, finite sources/tests/ACs, no executable argv/globs, no overlap with main-owned source/tests, new paths within actual diff+allowed_paths, fixed `work_items/mt01-components/<name>.json`, contract and consumer closure against main, no symlink/path traversal, on-disk manifest byte hash and an explicit `VALIDATED_DATA_ONLY_NOT_VERIFICATION_PASS` status.

Tests cover valid new component, reownership, path traversal, executor-command injection, missing/symlinked files, source family escape, untrusted consumer, duplicate sources/ACs, incomplete allowed_paths and exact on-disk mismatch.

## Critical unfinished scope — do not prematurely merge
1. Base register trusted `mt01-component-onboarding` ingress profile and preserve fixed v1 packet authorization/role/claim validation. Must not allow executor-written arbitrary `.github` paths.
2. Add post-executor candidate manifest load to MT-01 + V2.1 impact selection; derive trusted finite test argv `python -m pytest <approved paths> -q -n 12` and link actual source/test/AC/consumer closure; bind manifest+test/env hashes in EF01 and EF02 receipt/transport.
3. Independent PR CI/base oracle/merge gate must inspect candidate manifest as data from pinned GitHub blobs (not imported code), union existing owners, prohibit omissions and verify both-OS JUnit and ZIP.
4. Replay A17-before-PR#256 **offline only**, simulate unknown/missing/forged source, stale profiles, 12-workers, cross-component tests, fan-in, wrong HEAD/base and existing legacy component regression. No retry EF02 #258; no live provider calls or external publish.
5. Verify exact-HEAD CI on both OS; Brain review only after all ACs are proven. Maintain V2.1 rollback.

Do not use this PR as proof that new components currently run through one EF02 PR. It deliberately remains DRAFT until all five workstreams are implemented.
