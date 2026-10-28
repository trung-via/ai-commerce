# AI Commerce — Living project state

Updated: 2026-10-28. Repository: `trung-via/ai-commerce`.

## Source of truth

- Human-approved Governance v0.3: `CONSTITUTION.md`, `PROJECT_CONTRACT.md`, `docs/governance/HUMAN_ADOPTION_2026-10-27.md`.
- Canonical planning: `docs/roadmap/ROADMAP_A_CREATIVE_FIRST_V1_0.json` and `docs/roadmap/WORK_TREE_V1_1.json`.
- Research reference: Blueprint v1.2; research is not runtime proof.
- A01 implementation is the first *application skeleton*, not activation of EF01/EF02 or product capabilities.

## A01 scope

A01 corresponds to G01/G02/G03/G06/G08: packaging, importable CLI, architecture owners, targeted tests, project isolation.

## Implementation truth

- `A01`: source implemented; **Linux 12/12 targeted tests and Windows PowerShell 12/12 tests observed in Human-supplied screenshot**; see `A01_IMPLEMENTATION_REPORT.md` for verification boundaries.
- `A02`: **BUILD_VERIFIED_FOR_SCOPE (Linux local)** — 5 portable JSON Schema contracts, source/artifact SHA-256, mock provider, typed capabilities, CLI validation and example mock artifact. 41/41 project targeted tests after cross-platform date-time fix; an earlier Windows run showed 30 PASS / 1 FAIL on old code; **the corrected build subsequently showed 41/41 PASS on Windows in a Human-provided screenshot**. Evidence: `A02_IMPLEMENTATION_REPORT.md`.
- `EF01`: SOURCE_IMPLEMENTED / LOCAL_SYNTHETIC_TESTS_PASS (12/12); independent Codex and Antigravity CLI adapters available on implementation branch. Neither provider has been run live on Windows; acceptance remains OPEN.
- `EF02`: NOT_IMPLEMENTED; no remote wake, session or runner linked.
- Creative, media generation, platform posting, analytics, settlement: NOT_IMPLEMENTED / NOT_VERIFIED.

## Unknowns and next steps

- A01 Python foundation installation and targeted tests on Windows: **PASS reported with screenshot evidence (2026-10-28)**. Exact `git rev-parse HEAD` of this workstation and interpreter `python --version` were not shown.
- Executor CLI installation, model eligibility, usage and quota on the actual Windows host: UNKNOWN.
- Remote runner installation/permissions and return-to-Brain connection: UNKNOWN.
- Product/media third-party accounts and publishing permissions: UNKNOWN.
- A02 Windows report (2026-10-28): prior revision 30 PASS, 1 FAIL in `test_digest_and_time_format_are_checked`, mock generation succeeded; date-time validator corrected and 41/41 local tests PASS; Windows post-fix retest now PASS per Human-provided screenshot: 41 passed in 2.08s. Exact Windows checkout commit SHA was not shown.
- Suggested next work: EF01 dual-executor engineering core; A03/A04 Creative Factory are product-oriented follow-ups that can use A02 interfaces, EF02 not required.

No git push, external post, media generation, commercial account linkage or paid API call is implied by any A01 test.

## EF01 implementation update — 2026-10-28

The EF01 standalone CLI and adapters have 12/12 local synthetic targeted tests PASS using mocked provider subprocesses and temporary real Git repos. Windows Codex/Antigravity live runs, installed CLI versions/model entitlement and remote EF02 remain unverified. M0 live acceptance is not yet claimed. See `EF01_IMPLEMENTATION_REPORT.md`.
