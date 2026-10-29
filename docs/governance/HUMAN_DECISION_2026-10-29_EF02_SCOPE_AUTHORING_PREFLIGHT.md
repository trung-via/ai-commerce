# Human Decision — EF02 Component-First Authoring Preflight + Evidence-Safe Scope Recovery

Date: 2026-10-29
Repository: `trung-via/ai-commerce`
Authority: Human approved the three improvements proposed after EF02 A11 Issue #95.
Status: **APPROVED / ADOPTED ENGINEERING AUTHORING POLICY**
Implementation surface: **AI Commerce Engineering Workflow plugin v0.3.2** (private, updated); existing repository EF01-P / MT-01 / EF01-R / EF02 runtime contract unchanged.
Scope: AI Commerce only. No new WorkItem/WBS, lifecycle gate, Human approval, pilot, performance benchmark or automatic main merge.

## Why this was needed — observed evidence

- [#95](https://github.com/trung-via/ai-commerce/issues/95) EF02 [run #40910716784](https://github.com/trung-via/ai-commerce/actions/runs/40910716784) returned A11-01 `VERIFIED_SCOPE_REVIEW_REQUIRED` with A11-02/03/04 blocked.
- A11-01 Windows `python -m pytest tests -q -n 12` recorded **1554 passed / 3 skipped**, exit 0.
- The authoring packet granted `src/ai_commerce/learning/core` and `.../reader`, but Codex created sibling `core.py` and `reader.py` plus `schemas.py` and `README.md`. Four ordinary paths fell outside the approved list. There was no candidate SHA/ref to reuse from #95.
- This was an **authoring scope specification error**, not evidence of failing pytest or a defective path validator. EF02 correctly withheld publication.
- [#96](https://github.com/trung-via/ai-commerce/issues/96) is a separately authorized repair with changed scope precondition and new immutable attempts; it must be evaluated on its own evidence. This decision does not edit, replay, bypass or merge #95/#96.

## 1. Component-first as the authoring default

- For new source modules or refactors where executor can organize ordinary files, prefer a narrow, real **component directory prefix**, e.g. `src/ai_commerce/learning`, rather than speculative paths like `src/ai_commerce/learning/core`.
- The path `src/ai_commerce/learning/core` covers descendants in that directory, **not** sibling `src/ai_commerce/learning/core.py`. Prefix matching respects path-segment boundaries; globs are unsupported.
- Flat shared `contracts/*.schema.json`, `src/ai_commerce/cli.py`, tests and governance docs should stay explicit or appropriately owned by their action. Do **not** grant `.`, `src`, or the full repository to avoid reconciliation.
- Check sibling action ownership: one broad parent component prefix that overlaps the other READY action's descendant requires serialization, narrower distinct scopes or explicit shared-resource reconciliation. Distinct worktrees alone do not prevent semantic conflicts at fan-in.

## 2. Lightweight pre-dispatch authoring preflight

As part of the Brain's *existing* EF02 packet authoring (not a new runtime gate or independent approval), compare:
1. WorkItem AC, real consumer/dependency graph and live repository source SHA.
2. Action instruction and expected file **families**: `__init__.py`, `core.py`, `reader.py`, extra schemas, test fixtures, README/docs, shared CLI and other likely outputs.
3. `allowed_paths`: coverage for expected created/modified files; avoid unsupported wildcard, protected paths, global scope and accidental cross-component permissions.
4. Ready-sibling scopes: detect overlapping prefixes, shared contracts/entrypoints and fan-in dependencies; adjust `depends_on` and resource/ownership accordingly.
5. Actual actor authorization, attempt IDs and fingerprint, pinned Human executor/model/effort, registered verification profile and immutable source SHA.

Preflight improves packet quality but cannot predict every file or certify future execution; the existing runtime scope reconciliation remains authoritative.

## 3. Verify-first evidence preservation and provenance-safe reuse

- `PASS + allowed scope`: continue existing EF01/EF02 hash, scope, ownership and publisher checks, without automatic main merge.
- `PASS + ordinary out-of-scope paths`: retain `VERIFIED_SCOPE_REVIEW_REQUIRED`, full changed-path/diff/hash/test evidence, and prohibit silent candidate publication.
- Prefer reuse only where the **real published immutable candidate SHA/ref**, verified run, source/test/dependency/environment provenance and current `reuse_candidate` authorization check permit it. A PASS test log does **not** prove reusable candidate bytes.
- No candidate SHA/ref (as in #95) or stale/ambiguous state: do not claim reuse. Check actual recovery possibility; otherwise issue a fresh bounded attempt after correcting the scope precondition and re-run verification on new bytes.
- Protected `.git`, `.github`, `.local`, secret/credential paths, symlink/path traversal, unexpected HEAD drift and unknown side effects still STOP before candidate-controlled tests or publication. No bypass.
- Sibling PASS is not aggregate PASS; fan-in must revalidate the composed candidate and invalidated tests.
- A comment cannot alter an immutable claimed EF02 Issue; a follow-up uses new attempt IDs and `repair_of` where required.

## Compatibility / rollout truth

- Private plugin **AI Commerce Engineering Workflow v0.3.2** contains component-first/preflight/reuse guidance.
- This decision alone is **not** a modification to `src/ai_commerce/remote` or self-hosted worker, not an automatic preflight code validator, not a recovered #95 candidate, and not evidence that #96 completed.
- Keep user-selected `max_parallel` flexibility and `-n 12` *per eligible action* independent; only claim actual worker usage from run logs.
- Human retains authority over project implementation; creative content remains freeform and advisory insights do not become hard gates.

## Future authoring review checklist (in existing Brain step)

- [ ] New component scope covers plausible `.py` siblings and future ordinary files without unrelated broad access.
- [ ] External schemas/flat tests/shared CLI included exactly when necessary.
- [ ] No overlapping writable ready-sibling scopes without dependency or explicit resource reconciliation.
- [ ] Reuse claim has exact candidate SHA, verified run, current contract authorization and valid hashes; otherwise plan new bounded attempt.
- [ ] Protected/unknown effects still stop; main publication requires independent real evidence.

References: `docs/governance/HUMAN_DECISION_2026-10-29_COMPONENT_SCOPE_VERIFY_FIRST.md`, `contracts/RemoteActionBatch.v1.schema.json`, `src/ai_commerce/remote/validation.py`, EF02 #95/#96.
