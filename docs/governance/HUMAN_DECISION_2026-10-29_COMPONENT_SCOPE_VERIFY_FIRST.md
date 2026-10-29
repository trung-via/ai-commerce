# Human Decision — Component-Level Scope + Verify-First / Scope-Reconcile

Date: 2026-10-29
Repository: `trung-via/ai-commerce`
Human status: **APPROVED / ADOPTED ENGINEERING INTENT**
Code publication status: **PR #85 pending integration**, never infer merged `main` or live Windows deployment.

## Human directive and authority

Use **Component-Level Scope + Verify-First (Scope-Reconcile)** in the existing independent AI Commerce Engineering flow: **EF01-P + MT-01 + EF01-R + EF02**.

Brain should author `allowed_paths` around the affected functional component(s), not try to predict every source file; executor has discretion to create/edit/refactor the necessary ordinary files within that granted scope. Neither a broad scope nor a test PASS authorizes modifications to protected paths or remote publication. This does not import the AIOS-Renew workflow/kernel.

**Human explicitly reserves Brain discretion over per-batch `max_parallel`:** EF02 default remains configurable at 2, but a one-action/dependency-constrained batch can use 1. Do not unconditionally force 2 into every packet. The independent Human decision of `pytest-xdist -n12` **per eligible action** (potentially 24 workers with two concurrent test phases) is unchanged; this document is not proof of actual 12/24-worker runtime activation.

## Action authoring — component scope by default

1. Start with user intent, accepted AC, component owner and known consumers. Map entrypoint → affected component → direct dependencies and test families before authoring.
2. Prefer **specific functional directory prefixes** in `allowed_paths`, e.g. `src/ai_commerce/engineering`, `src/ai_commerce/remote`. The current `_safe_scope` and EF02 validator support relative directory prefixes without a new schema field; do **not** write glob patterns such as `tests/test_ef01*`, which are not supported.
3. Add known dependent component prefixes only when justified. Root-level tests are currently flat, so use exact known test files; `tests` may be included for a cross-cutting engineering change that legitimately needs multiple/new tests, with the larger review surface explicitly understood.
4. Do not default to `src`, `.`, `tests` alone or the whole repository just to avoid scope work. Component scope is a sensible starting boundary, not automatic unrestricted root access.
5. For two independent EF02 actions, choose their separate component prefixes when possible; if they must both touch a shared component or contracts, declare a dependency or explicit shared resource, and verify the reconciled candidate at fan-in.
6. `max_parallel` remains **Brain-selected** using action readiness, dependencies and task needs; default 2 is not a forced constant.

### Example — EF01-P + MT-01

```json
{
  "allowed_paths": [
    "src/ai_commerce/engineering",
    "src/ai_commerce/remote",
    "tests/test_ef01.py",
    "tests/test_ef01_repair.py",
    "tests/test_ef02.py",
    "tests/test_minimum_test.py"
  ],
  "verification_profile": "project-tests",
  "max_repair_rounds": 2
}
```

This is an *action fragment*, not a complete dispatch packet; actual JSON must be validated against the live `RemoteActionBatch.v1` contract and the authoring Brain may add relevant permitted test paths. Verify profiles are registered and deterministic. Never silently create a new mutable request out of an existing immutable EF02 attempt.

## Verify first; reconcile scope before publication

- After executor returns, snapshot HEAD, Git identity, changed paths including renamed/deleted/untracked files, content hashes, diff and out-of-scope paths. Stop on protected control/secret paths, changed symlinks, HEAD/worktree drift, tool/permission/timeout/ambiguous provider or other uncompensated side effects. **Running tests executes candidate code**, so use the actual isolated worktree and keep provider tokens out of the child environment.
- For **ordinary** out-of-scope code/test files, do **not** hard-stop *solely* on the scope list: run the already-registered verification, preserve `verify_exit_code` and evidence, and allow bounded EF01-R repair of determinate pytest failures in the same source/worktree with the same pinned executor/model/effort.
- MT-01 must choose sufficient tests for current AC + affected consumers/contract dependencies, with `EXECUTE/REUSE/INVALIDATED/UNPROVEN` states. New or modified test definitions, code paths, contract/deps or fan-in may invalidate prior evidence. A narrow suite can be PASS only for what it actually establishes.
- **PASS + accepted scope** → `BUILD_VERIFIED_FOR_SCOPE`; EF02 still independently verifies exact source/tree/hash/scope before creating a candidate.
- **PASS + ordinary scope drift** → `VERIFIED_SCOPE_REVIEW_REQUIRED`: preserve full diff, `out_of_scope_files` and verified evidence; **not CANDIDATE_READY**. Brain examines AC/consumer necessity and may prepare an explicitly authorized follow-up scope/reuse handoff for the same verified content where provenance matches. A comment cannot mutate the original action claim.
- **Pytest fail, repairable** → bounded EF01-R; if still failed/unproven, report accurately; never label as PASS because work looked plausible or because only some tests passed.
- **Protected change, credential/permission failure, provider UNKNOWN, source drift** → stop appropriately. Do not bypass sandbox, re-invoke ambiguous calls, auto-merge main, or publish media/externally.

## Role allocation

| Element | Role |
| --- | --- |
| Brain / WorkItem | Select component scope from AC/consumer graph; select executor and max_parallel; review scope delta and provenance |
| EF01-P | Git path/pytest execution; 12 workers/action is a separate Human decision and pending code verification |
| MT-01 | Minimum sufficient registered test selection, invalidation and evidence reuse |
| EF01-R | Same-attempt bounded fail → feedback → fix → reverify; preserve cumulative evidence |
| EF02 | Immutable fingerprint/claim, isolated worktrees, per-action scope, verified candidate publication, DAG fan-in |

## Acceptance criteria (no new lifecycle gate)

1. Valid component directory prefix covers newly created files *within* that component; similar-looking sibling directory does not match. Exact test file scope still works.
2. Protected paths or source drift stop before executing candidate-controlled verification; path traversal, symlinks and credential/control changes are not legalized by broad source prefixes.
3. Ordinary unlisted file with test PASS retains evidence and `VERIFIED_SCOPE_REVIEW_REQUIRED`; worker does not publish it. Pytest failure still uses existing EF01-R repair; fixed scoped content can PASS.
4. EF02 receives explicit out-of-scope path/evidence fields for Brain, not just a generic `SCOPE_VIOLATION`.
5. Separate action proofs are not sufficient to prove merged DAG fan-in; MT-01 invalidates and reruns affected aggregate coverage.
6. Human retains Brain discretion for 1-action `max_parallel=1` or 2+ independent actions `max_parallel=2`, with no forced rewrite of #84.
7. Deterministic regression tests and hosted CI pass; live Windows or xdist 12/24 claims require **observed** evidence, not a document or green wrapper.

## Relation to prior work

- #81: 19 changed files, five outside allowlist; original attempt stopped before tests.
- #84: separately submitted recovery, immutable; no replay/new scope modification implied.
- PR #85: implementation and regression tests for Verify-First; this decision integrates Component-Level Scope into its authoring rules.
- PR #82: separate 12 workers *per action* decision; remains independent.

**No new WBS item, no mandatory benchmarks/pilots, no new formal Human approval gate beyond real authority; no automatic main merge.**
