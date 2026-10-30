# Git Byte Consistency v1 — EF01 / EF02

**State:** implementation candidate on PR #114; not active on main until Human-approved merge.
**Origin:** A12 batch failures #106, #111, #115. No change to provider entitlement, Git Write Guard, approval of merges or publication rights.

## Root cause

EF02 `GitHubTransport.candidate` publishes verified file bytes through the GitHub Git Blobs API, **without the Git clean/smudge filter**. Historical `*.json text eol=lf` normalized JSON when the next Windows runner checked out that candidate. Raw CRLF JSON already present in Git blobs then appeared dirty at initial EF01 snapshot and was rejected as `WORKTREE_DIRTY`, before the model was invoked. Repeated directory exceptions (`examples/*/evidence`, `repair_evidence`, `a12_02_evidence`, Product schemas) only hid the symptom for known paths.

## Adopted v1 behavior

1. `.gitattributes`: `*.json -text -eol` means *any* JSON in evidence, schemas or future generated components is byte-stable across checkouts. It is still JSON: parse/schema validation is required; authors and generators should **produce UTF-8 LF** where practical, with explicit test checks rather than a Git checkout mutation.
2. Publisher `assert_candidate_blob_portable`: for each changed regular file after SHA-256 comparison, compare `git hash-object --no-filters --stdin` with `git hash-object --stdin --path=<file>` using the candidate worktree's attributes. If Git's clean filter changes bytes, fail as `CANDIDATE_GIT_FILTER_MISMATCH:<path>` **before making any GitHub blob/tree/ref writes**. Do not normalize after verification. This covers Markdown, PowerShell and any future text rules too.
3. EF02 worker `inspect_fresh_checkout`: after `git worktree add --detach`, **before EF01 child spawn / provider launch**, verify HEAD equals effective source SHA and `git status --porcelain -z --untracked-files=all` is empty. Save `checkout_preflight.json` with bounded status entries and full status SHA-256. On mismatch mark `SOURCE_CHECKOUT_INCONSISTENT` with `GIT_WORKTREE_DIRTY_AFTER_CHECKOUT` or `GIT_CHECKOUT_HEAD_MISMATCH`. Do not reset, clean, suppress Git paths or pretend a provider ran.
4. Existing EF01 `WORKTREE_DIRTY` and Git Write Guard remain fail-closed for ordinary changes. The new checks are prior diagnostics/prevention, not a relaxation of either security or source integrity.
5. Historical candidate refs and source file bytes remain unchanged. Any new WorkItem batch needs its own pinned source, attempt IDs, fingerprint and `repair_of` where appropriate. Candidate verified on one branch is not merged main.

## Boundaries / limitations

- This approach assumes the actual Git clean filter and candidate attributes are available at publication, and uses a bounded local Git subprocess. An unexpectedly configured external clean filter could execute when probed; such filters must remain restricted by trusted runner/repository configuration. The helper fails closed when Git cannot return an answer.
- `*.md text eol=lf` and `*.ps1 text eol=crlf` remain intentional. A mismatched raw GitHub blob under either rule is rejected before candidate publication.
- A clean worktree check is **not** proof of application correctness, runner/provider success, external media rights, real affiliate availability or successful main integration.
- The read-only checkout check has no automatic self-healing of true byte changes: Human-approved recovery and a new immutable attempt may be required after a claim exists.
- Changing `.gitattributes` is Git policy; future Git-add of arbitrary JSON retains its original byte representation. Tests must validate schema, encoding and format constraints where required.

## Verification

- `python -m pytest tests/test_git_byte_consistency.py tests/test_ef02_worktree_line_endings.py -q -n 12`
- `python -m pytest tests -q -n 12` on Ubuntu/Windows and the self-hosted Windows no-model worktree preflight.
- Negative proof: a GitHub-style raw CRLF JSON blob under `eol=lf` must be reported dirty, and publication must reject it without GitHub write.
- Positive proof: raw CRLF JSON under `-text -eol` remains clean; actual modified files are still rejected.
- PR review must check no change to Git Write Guard, no remote secrets, no auto-main-merge.

## Operational handoff

On PR merge, run fresh Windows probe against published candidate sources where possible, and only then continue a previously blocked action with new attempt identity. Preserve #116 execution receipts and pending source branches. Never replay claims from #106/#111/#115/#116.
