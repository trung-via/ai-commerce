# Human Decision — Verify First, Publish Candidate, Brain Scope Review

Date: 2026-10-30. Status: **HUMAN APPROVED / ADOPTED ENGINEERING POLICY**.
Authority: the Human's direct EF01/EF02 engineering policy instruction for EF02-SCOPE-PUBLISH-01. Applies only to AI Commerce. Human still approves merge; there is no automatic merge.

The Human reports that [Issue #144, run 41044999329](https://github.com/trung-via/ai-commerce/actions/runs/41044999329) verified **1994 passed, 6 skipped, 0 failed**, with seven changed files and only `src/ai_commerce/remote/worker.py` outside `allowed_paths`. The old behavior withheld the candidate and required a paid follow-up. These are Human-provided incident facts, not a claim that this implementation inspected remote artifacts. This decision does not infer an outcome for #145 or modify any original immutable Issue/action.

This decision supersedes the **ordinary verified drift publication prohibition** in the 2026-10-29 [component scope decision](HUMAN_DECISION_2026-10-29_COMPONENT_SCOPE_VERIFY_FIRST.md), [authoring preflight decision](HUMAN_DECISION_2026-10-29_EF02_SCOPE_AUTHORING_PREFLIGHT.md), and [EF01-R reconciliation design](../engineering/EF01R_VERIFY_FIRST_SCOPE_RECONCILE_2026-10-29.md). Component-first authoring, immutable action scope, integrity checks, registered tests and protected-path restrictions continue to apply.

| Verified outcome | EF01 receipt | EF02 publication and acceptance |
| --- | --- | --- |
| Allowed ordinary changes, PASS | `BUILD_VERIFIED_FOR_SCOPE` | `CANDIDATE_READY`, `scope_valid=true`, `review_required=false` |
| Ordinary drift, PASS with intact proof | `VERIFIED_SCOPE_REVIEW_REQUIRED` | `CANDIDATE_READY_SCOPE_REVIEW`, immutable candidate SHA/ref, `scope_valid=false`, `review_required=true` |
| Protected/unsafe path, HEAD/source drift, ambiguous provider/tool outcome, FAIL, unknown, missing/tampered proof | Existing failure/unknown receipt | Hard stop; no candidate publication |

Publication preserves evidence so Brain can assess whether ordinary drift is essential to the acceptance criteria and affected consumers. Publication **does not approve the drift** or widen `allowed_paths`. Brain may accept the exact candidate in its assessment or reject/request repair. A renamed status, an Issue comment, a `brain_approved` boolean, or a rewritten reuse registry cannot promote the original candidate. Human approval remains necessary before merge. The current automatic consumers conservatively block review candidates; they do not implement an automatic Brain acceptance/promotion path.

The EF02 publisher owns create-only `refs/heads/ef02/candidate/<attempt-key>` publication. Executors edit ordinary worktree files only, with no Git commit/ref/config writes. A ref conflict or uncertain GitHub write is a publication failure, never permission to force-update or rerun the provider.

The [RemoteCandidateEvidence v1 contract](../../contracts/RemoteCandidateEvidence.v1.schema.json) is canonical JSON embedded in the candidate commit message after `EF02-CANDIDATE-EVIDENCE-V1`. The commit SHA binds:

- Original source SHA, effective isolated source HEAD, immutable action fingerprint, action/attempt identity and original allowed paths.
- Changed paths, deletions, per-file SHA-256 manifest, tracked binary patch SHA-256 and exact ordinary out-of-scope paths.
- Distinct candidate status, `scope_valid`, `review_required`, registered verification profile/argv, EF01 verification status/exit, artifact hashes and engineering report hash.
- Executor/model/effort, dependency candidate SHAs and available workflow run/attempt provenance. Unknown run provenance remains null.

Before publication, EF02 independently reconciles the final workspace against EF01's verified snapshot and rehashes authoritative artifacts. Registered MT-01 proof must match source, workspace, candidate files and registered argv. Ordinary drift additionally requires fresh broad verification; a sibling PASS or stale reused proof cannot stand in for it. Transport snapshots bytes, rejects unsafe paths and symlink/junction ancestors, checks Git byte-filter consistency, and rechecks HEAD/changed paths/patch/file hashes before any GitHub write. Ignored credential/control files are checked as unsafe untracked content; ordinary caches and the runner's ignored `.local` evidence store are not source deltas. Tracked/explicit `.local` changes remain protected.

Scheduler dependencies and successful counts accept only normal candidates without review indicators. Direct fan-in and reuse also inspect the pinned commit's immutable evidence, so stripping flags from a mutable result/registry cannot launder a review SHA. Completion events retain the distinct status, explicit review flag and evidence digest; artifact provenance validation checks the same metadata. Full review details remain in `action_result.json` / `batch_result.json` and the commit-bound evidence. Legacy candidates remain readable under their original contract; legacy reuse still returns `UNPROVEN` without current MT-01 proof.

No `.github` workflow/registry edit, blanket source allowlist, source pin relaxation, provider retry, concurrency change or automatic merge is authorized by this implementation. Keep Brain-selected `max_parallel` with default 2, `python -m pytest tests -q -n 12` for this registered action, and two bounded determinate verify-fix rounds. FAIL/unknown or exhausted rounds cannot produce a candidate. This document records policy and implementation semantics; actual test counts belong to the execution evidence.
