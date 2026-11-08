# AI Commerce — Protected Source Owner Lane v1

Status: SOURCE-OWNER PR PROCESS, not an EF02 executor permission bypass.
Owner: repository Human owner + independent Brain review.
Related: Issue #560 (EF02 RESTRICTED_PATH), PR #559 (CI HOLD), #549/#551 (WakeHub FFmpeg false positive).

## Problem and root cause

The ordinary EF02 Issue action has three independent source-protection boundaries:

1. `src/ai_commerce/remote/validation.py:safe_path` refuses all `.github/` action paths before any executor starts.
2. `src/ai_commerce/engineering/source_safety.py:protected_change` prevents ordinary EF01 acceptance of `.github/` source deltas and refuses unsafe candidate reads.
3. The EF02 candidate transport/reviewer rechecks protected paths, source identity, independent MT-01 and trusted publication before creating a candidate.

Editing just one checker or supplying an innocent-looking source path **does not grant protected control-plane authority**. It either fails at another layer or undermines the independent verifier.

## Operational route, available now

For changes to `.github/scripts/`, `.github/workflows/`, or equivalent trust anchors, **do not submit an ordinary EF02 source action claiming protected allowed_paths**. Use a distinct **authenticated repository-owner source branch + pull request**:

1. Human authorizes the **exact protected files, goal and intended side effects** for this specific operation. Authenticate repository owner identity separately; an Issue string, model instruction or ordinary BRAIN actor alone is not permission.
2. Pin current `main` SHA and read the existing protected file blob SHAs. Create an isolated `owner/protected-source-<topic>` branch at that SHA. Do **not** write directly to `main`.
3. Prepare a minimal, auditable diff (may be proposed by Codex/Antigravity in a **non-privileged scratch** area, never imported blindly). The authenticated owner stages only the reviewed source bytes on the isolated branch. Keep evidence of before/after blob SHA, target paths and purpose. No API/connector writer may silently transplant an EF02-rejected action or forge its receipts.
4. Create an explicit pull request with exact base/head and an explanation of the trust-boundary change. Require independent hosted Windows CI, source-owner and downstream consumer tests; for selector/verification/merge-gate edits, test bypass and false-PASS negative cases as well as normal success.
5. Independent Brain reviews the **current** exact PR HEAD, base, real CI artifact and all changed protected paths. Only a genuine canonical four-line Brain PASS comment and authenticated COMMENT review wake can ask trusted `main` automation to merge. An issue comment, executor-provided SUCCESS or ordinary green CI alone cannot approve.
6. GitHub must independently report `merged=true`, the exact merge commit, and current `main` containing it. Resume blocked ordinary PRs by refreshing base/re-running CI; preserve all previous failed EF02 attempts.

## Permissions and non-goals

- This **owner PR lane** does not change the default EF02 `RESTRICTED_PATH` policy. Normal executors have broad source-repair autonomy **only** in their authorized non-protected worktree and scratch.
- No generic shell, local Windows privileged writes, secrets, workflows, branch protection, GitHub token forwarding to Codex/Antigravity, Chrome, PULSE, provider spend or publication is authorized by source PR approval.
- A future *automated* protected-change executor requires its own narrow owner-authenticated grant contract, reviewer independent of candidate code, exact-path capability, protected-tool execution isolation and replay protection. That must be introduced as a separate reviewed Engineering work package, not by weakening ordinary EF02 path guards or advancing R1-WP03 out of sequence.
- Do not let candidate-modified CI selector or merge-gate code be its own independent verifier. Pin the authoritative oracle to trusted `main` where supported, and retain negative owner/consumer tests.

## Current incident: FFmpeg detection for PR #559

- Previous #549/#551 corrected *false-positive* FFmpeg provisioning for WakeHub G2; its reviewed exact test files remain `need_ffmpeg=false`.
- PR #559 revealed the opposite *false-negative*: selected BQ02a/BQ02b/BQ04a tests actually require native FFmpeg/A13, but their filenames lack `media`, `video`, `ffmpeg`, etc.
- The source-owner patch adds reviewed exact media-test dependencies to `_FFMPEG_EXTERNAL_TESTS` without changing test selection, source guards, branch protection or CI workflow; regressions assert both the missing-media and WakeHub-only cases.
- Hosted CI must actually select/provision `ffmpeg` and `ffprobe`, run all selected Q4/MT-01 consumer tests, and not mark them skipped or fabricated PASS. A Chocolatey outage is a separate unavailable-dependency condition.
- **PR #559 remains HOLD** until a reviewed owner patch is merged to main, #559 is reconciled to the new base and exact-head hosted Windows CI independently passes.

This document is a source-owned operating contract describing actual repository-owner PR mechanics, not proof of a new EF02 protected grant or of any autonomous executor invocation.
