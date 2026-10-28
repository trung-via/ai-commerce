# ADR-0001 — Independent Python application bootstrap

Decision date: 2026-10-28. Status: ACCEPTED_FOR_A01_BY_HUMAN_REQUEST.

## Context and decision

Roadmap A Phase 0 requires an application skeleton *before* its Engineering Flow exists. Build a lightweight installable Python `src/` package using the standard library at runtime, a CLI entrypoint, and focused pytest tests. No database, agent router, background service or network is required for A01.

Human approved selective adoption from AIOS-Renew for **EF01**; A01 does not import any foreign code or runtime. Preserve each adopted component's exact source SHA/rights and rollback evidence in later work.

## Interfaces and boundaries

- `ai_commerce.cli:main`: local entrypoint, currently `hello` and `about`; explicitly does not dispatch or publish.
- `ai_commerce.core.identity.ProjectIdentity`: narrow static package identity; reports EF/product runtime NOT_IMPLEMENTED.
- `contracts/WorkItem.v0.3.schema.json`: existing planning contract, unchanged; EF01 can adapt it later.
- `docs/engineering/PROJECT_STATE.md`: human-readable implementation status, not a canonical execution ledger.

## Trade-offs

Pure stdlib runtime minimizes installation and avoids prescribing providers. `pytest` is an optional dev dependency. A01 does not yet provide persistence, product adapters, GitHub Actions, executor selection, repair or provider usage accounting; those belong to A02/EF01/EF02.

## Verification

A01 verifies `python -m ai_commerce --help`, CLI statuses, negative CLI path, governance presence, schema continuity and static foreign-runtime isolation. Additional CI is optional; no full-suite or heavy audit gate is mandated.

Rollback: revert the A01 commit in `ai-commerce`; no AIOS service or credential is affected.
