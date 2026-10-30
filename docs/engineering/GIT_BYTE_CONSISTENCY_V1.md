# Git Byte Consistency v1 — EF01/EF02

**Status:** proposed implementation on PR #117, pending Windows/Ubuntu CI and Human approval to merge. Human authorized this scoped engineering improvement on 2026-10-30. The current code is not the authority for completed A12 WorkItems, GitHub runs and immutable candidate refs are.

## Problem and original evidence

- [#106](https://github.com/trung-via/ai-commerce/issues/106): 2 A11 JSON evidence blobs appeared modified immediately after new Windows worktree checkout.
- [#111](https://github.com/trung-via/ai-commerce/issues/111): A12-01 EF01-R JSON `repair_evidence` caused the next action to fail before Codex.
- [#115](https://github.com/trung-via/ai-commerce/issues/115): A12-02 generated action-specific JSON evidence and CRLF Product JSON Schemas; A12-03 failed `WORKTREE_DIRTY` before model.
- [#116](https://github.com/trung-via/ai-commerce/issues/116): successor batch uses a separately pinned staging source, not automatically changed by this PR.

EF02's GitHub Git Data API publishes the exact bytes verified by EF01. Git-for-Windows can apply `.gitattributes` clean filters when materializing that raw commit. If those raw verified bytes are not in the Git canonical form for their path, `git status` may report a spurious modification on the subsequent dependency worktree.

## Adopted design in PR #117

1. `.gitattributes` sets `*.json -text -eol` for any JSON path. This preserves already published JSON blobs byte-for-byte and avoids guessing folder names (`evidence`, `repair_evidence`, `a12_02_evidence`, new schemas, etc). JSON validity, UTF-8 and source-style LF consistency remain application/test responsibilities, **not automatic Git filters**.
2. EF02 checks `git status --porcelain -z` directly after `git worktree add` and before any EF01 provider checkpoint. It can refresh only stat metadata (`git update-index --refresh`), then recheck. If files still look changed, it fails **`SOURCE_CHECKOUT_INCONSISTENT`** and records bounded original path entries. No file reset/clean, whitelist, skip-worktree, assume-unchanged or unauthorized ref write.
3. Candidate transport, after all existing EF01 tests and SHA-256 manifest checks but before any GitHub blob/commit/ref API writes, runs read-only `git hash-object --stdin --path=...` against raw `git hash-object --stdin`. If Git would clean-filter the bytes, it fails **`CANDIDATE_GIT_FILTER_MISMATCH:<path>`**, not `CANDIDATE_READY`. Never normalize after verification.
4. EF01's existing `WORKTREE_DIRTY` stop remains, but reports changed paths instead of requiring a separate artifact deep dive.
5. Git Write Guard (model child-only hooks), scope checks, claim and pre-spawn semantics, Human-controlled main merge and the no-secret-in-child invariant are unchanged.

## Acceptance

- Raw JSON containing CRLF under **arbitrary** nested paths round-trips a new Git worktree with `core.autocrlf=true`, preserves exact bytes and has clean status.
- Other non-JSON LF-governed files (example `*.md`) with non-round-tripping bytes are rejected **before GitHub candidate publication**. Read-only filter analysis does not rewrite bytes.
- A genuinely modified tracked file remains dirty after Git stat refresh and is not ignored.
- Unsafe paths and unknown Git filter states fail closed; deletions continue through prior manifest rules.
- Full `pytest tests -q` Windows and Ubuntu CI plus project-specific `pytest -n 12` where registered.

## Consumer and rollback

Consumers: EF01 local runner initial snapshot, EF02 Windows worktree fan-in, GitHub candidate transport. No public provider APIs, creative release or affiliate effects.

Rollback: revert this feature PR as one unit, restoring original `.gitattributes` only by explicit Human choice; never rewrite old Git blobs or drop old claim refs. Preserve all A12 candidates/issue evidence. A path-exception-only workaround [PR #114](https://github.com/trung-via/ai-commerce/pull/114) should not be merged concurrently with this global policy. No claim of permanent resolution until CI and post-merge live validation on the actual Windows source.

## Operational caveats

An unrelated file changed in the worktree or an incompatible non-JSON Git filter can still cause an intentional hard stop. That is correct, not a flaw. Repair the source or Git attributes **before** verification and issue a new attempt ID if a prior one was already claimed. Do not blindly replay an `EXECUTION_UNKNOWN` attempt.
