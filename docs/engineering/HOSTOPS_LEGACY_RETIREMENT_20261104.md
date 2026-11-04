# HostOps legacy retirement — 2026-11-04

## Human decision and boundary

Personal/single-account use; simplify operator interaction through **the existing**
`trung-via/ai-commerce` public repository and `AI-COMMERCE-EF02-01` Windows
self-hosted runner. ChatGPT-approved READ has no second approval. Each WRITE
requires one exact, fresh **Human** owner comment `APPROVE WRITE #N` on its own
`[HOSTOPS WRITE]` GitHub Issue. This is a per-request action, not general-purpose
PowerShell or a permission to issue Chrome Wake.

The known working implementation is
`src/ai_commerce/hostops_v3/personal.py`, launched **only** by the
`hostops-personal` job in `.github/workflows/issue-ingress.yml`.
The `ef02` reusable job and the trusted Brain PASS → auto-merge workflow
are independently retained without changing permissions.

## Removed from active GitHub Actions

| Retired route | Disposition |
|---|---|
| Issue titles `[HOSTOPS] ` (v1) | Remove `hostops-readonly` caller and `hostops-readonly.yml` |
| Issue titles `[HOSTOPS2] ` (v2) | Remove `hostops-v2` caller and `hostops-v2.yml` |
| Issue titles `[HOSTOPS3] ` (v3 signed broker queue) | Remove `hostops-v3` caller and `hostops-v3.yml` |
| Independent old v1 hosted tests | Remove `hostops-ci.yml`; coverage retained in project MT-01 |
| Independent old v2 hosted tests | Remove `hostops-v2-ci.yml`; coverage retained in project MT-01 |

Old workflow paths are also removed from the skipped-run cleanup **active
allowlist**. Historic per-file MT-01/merge-gate review mappings remain
intentionally registered so removal and future reintroduction attempts
cannot silently bypass the independent review process. Tests enforce that
the five retired workflow files no longer exist in the active tree.

The retained repository files in `scripts/hostops_*.py`,
`src/ai_commerce/hostops_v3/` other than `personal.py`,
`config/hostops/`, `config/hostops-v3/`, contracts, historical
`HOSTOPS_*.md` documents and tests are **reference/rollback and audit
evidence only**, not an active dispatch path. Retaining a historical public
signer record is **not a currently approved Human WRITE**. No private
keys are committed by this change.

## Evidence and non-goals

Prior Personal live receipts:
- READ inventory: [Issue #331](https://github.com/trung-via/ai-commerce/issues/331).
- Human-approved scratch create: [Issue #332](https://github.com/trung-via/ai-commerce/issues/332).
- Independent SHA-256 readback: [Issue #334](https://github.com/trung-via/ai-commerce/issues/334).
- Git main fast-forward: [Issue #339](https://github.com/trung-via/ai-commerce/issues/339).
- Independent local HEAD readback: [Issue #340](https://github.com/trung-via/ai-commerce/issues/340).

This change **does not** stop a separately installed local broker, a private
`ai-commerce-hostops-control` process, a pre-existing Windows scheduled task,
or Chrome/D5 private services. Their live presence is **UNKNOWN** and must be
inspected through a separate approved laptop operation before revocation;
this GitHub source PR cannot prove they are disabled. The earlier separate
private-repo PR #8 was already closed unmerged; no new private repo or runner
is introduced here.

No historical Issues, receipts, signatures, error/UNKNOWN ledgers or evidence
are deleted. In particular, never re-dispatch old W1 writes or D5
`UNKNOWN` sends (#161/#177/#179). The Personal pipeline still does not
launch Chrome, access the operator desktop, issue private grants, execute
arbitrary shell, edit tracked source or send Wake.

## Release acceptance

1. Changed-file review ensures the remaining active Issue ingress has only
   `ef02` and `hostops-personal`; neither job's effect scope expands.
2. Windows-only hosted CI selects owner/consumer tests via MT-01, including
   legacy retirement and trusted merge-gate regression.
3. Independent Brain review on exact HEAD/base gives PASS only after evidence
   and accepted source gates; trusted automation merges. Do not delete
   historical receipts or call CI a live desktop retirement proof.
4. A separate **READ-only** Personal `git_snapshot` smoke after merge is
   sufficient to verify the existing Windows runner route still works, if
   useful. No Human WRITE is needed for retirement of source-only CI paths.

Future Chrome CDP integration must add individually reviewed, fixed Personal
operations with their own operator-desktop proof; do not reactivate these
retired v1/v2/v3 workflows.
