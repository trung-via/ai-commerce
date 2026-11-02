# Human Decision — Queue Human-approved merges until exact HEAD CI passes

Date: 2026-11-02. Decision: Human explicitly asked to implement recommendation 1 from the #329 audit, then clarified: do NOT reject/consume merge approval during CI; leave it in **WAIT_CI** and automatically merge when the existing required CI finishes. No new approval request just because CI was still running.

## Scope and behavior

- Applies to AI Commerce PRs whose code is covered by GitHub **EF02 source tests**. It does not make a blanket claim of GitHub branch-protection enforcement for direct external merges; operator/plugin merge paths must use this queue.
- Human approval is posted by the repository owner as a precise GitHub PR issue comment (connector or UI). Exact four lines:

```text
AI_COMMERCE_HUMAN_MERGE_APPROVAL_V1
pr=<number>
head_sha=<40 hex>
base_sha=<40 hex>
```

- After the first approval, if exact HEAD CI is missing or pending: return WAIT_CI; **do not merge, error, repost approval, poll indefinitely, or ask the Human to approve again**.
- An Actions `workflow_run` completion event wakes the GitHub merge queue once. A completed successful PR run is accepted only if its exact HEAD matches, the proper workflow is selected, the HEAD and base match the owner approval, `main` still matches the approved base, PR is open, ready and mergeable, and *both* hosted Ubuntu and Windows show success on the **actual Python + Node test steps**.
- On verified PASS: automatically call the GitHub merge API with exact expected HEAD. On failed CI: WAIT_FIX; a subsequent CI retry on unchanged SHA can complete and merge without reapproval. On missing jobs/proofs: WAIT_EVIDENCE. On changed code HEAD or base SHA: do NOT silently widen Human authorization; require new exact-byte decision. A new owner comment can explicitly revoke by substituting `AI_COMMERCE_HUMAN_MERGE_REVOKE_V1` on line one.
- The workflow is serialized with `cancel-in-progress=false` and runs **default-branch-only code**; it never checks out and runs untrusted PR code with merge-write privileges. It creates no Codex request, no EF02 dispatch, no Wake and no extra pytest. All unknown states fail closed to a WAIT/HOLD verdict with no merge side effect.

## Why

Observed #329 main CI reran Python full suite even though its merge tree equaled the PR tree; the PR exact-HEAD CI was in progress at merge time. Waiting for that CI avoids the unnecessary additional CI full suite while preserving Human authorization and original verification requirements.

## Acceptance

1. Unit suite: issue-comment authorization + WAIT_CI state, then workflow-run success resumes **without a second owner comment**, and exact HEAD merge call occurs once.
2. Missing/failed one OS test, false green wrapper, missing owner approval, revoked or stale SHA, changed main, unrelated workflow and API error never merge.
3. Both OS CI pass on the implementing PR's exact SHA before independent Human merge approval; no claim of live queue until this PR is merged and a new owner-comment/workflow-run event is observed.

**Implementation paths:** `.github/scripts/pr-merge-gate.py`, `.github/workflows/human-merge-queue.yml`, tests and CI path filters. This is not an unconditional GitHub branch ruleset; manual REST/UI merge still needs governance discipline unless branch protection is separately configured.
