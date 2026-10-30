# EF02 scope-review candidates — #149 byte-integrity repair

Source: `182d7471332c781e4dacdcbff3d6bcdff50b4e36` (merged #150/#145).
Executor: Codex / `gpt-6.1-sol` / `xhigh`; two bounded verification/fix rounds maximum.
Only ordinary worktree files are edited. EF02 owns candidate publication; Human
retains merge approval.

## Exact failure and canonical bytes

[Run 41079392060](https://github.com/trung-via/ai-commerce/actions/runs/41079392060),
artifact `12990880066`, returned `CANDIDATE_INTEGRITY_FAILED` with
`CANDIDATE_GIT_FILTER_MISMATCH` for this document. Human-provided registered
evidence was 2750 passed, 11 skipped in 164.94 seconds, exit 0, with 22 in-scope
changed files and no candidate SHA/ref. Those tests belong to the old
`d961d180372d9da4a82cf6341694593389867df1` tree.

The surviving document exactly matches the reported verified SHA-256
`0d95d6a2f8fffb5a1d9be16f797aa4f3aa9c2413cb1947e9e291ca03dca097b2`.
It contains 138 CRLF endings and no UTF-8 BOM. Read-only `git check-attr`
reports `text: set`, `eol: lf`, `filter: unspecified` and
`working-tree-encoding: unspecified`. The raw Git blob is
`6b5f4ab122188c363eb3b6d717b81306a7b67d30`; `git hash-object --stdin --path=...`
instead returns `aae5862f5140cc801e3a98018aef9d021dc33eb6`.
Replacing CRLF with LF gives the latter hash for both commands and SHA-256
`1d81cee271b2ef3e541b622219bdce42d149e5f151a22fce94205e71a51b35bb`.
This isolates the cause to line-ending normalization, not scope or pytest.

All changed Markdown is now UTF-8 LF before independent registered verification.
Python and JSON have `-text -eol`, so their raw bytes are canonical; existing
Python line endings are retained where that keeps the semantic diff small.
Every changed file must pass the same raw-versus-filtered blob preflight.
The publisher's existing byte/hash guard remains unchanged. No attributes,
Git configuration, hooks, HEAD or refs are changed by this repair.

## Reconstruction and current consumers

The GitHub connector identified the exact artifact and returned an archive
reference with advertised digest
`814ee0ffb1e7017c34ac4722e108c7dc453497a08d5244ce069b64858f1dd446`.
Downloading archive bytes failed with DNS resolution errors; archive equality
and its full verified manifest remain unproven. All 22 files in retained
worktree `ai-commerce-ef02-EF02-SCOPE-PUBLISH-01-ngdr94b3` match its local final
manifest, and its HEAD matches the old pin. That local manifest is diagnostic
provenance, not artifact authentication or candidate reuse.

The feature is reconstructed on the new clean pin. Existing shared files are
compared against both source trees. The new `git_write_guard_env(..., repo=...,
git_executable=...)` call and malformed aggregate-plan/binding checks from #150
are preserved. Guard implementation and its newer tests are untouched.
Historical local verification/recovery JSON files are not transplanted as
current evidence. Current test results and file-integrity observations are
recorded separately; old PASS counts do not validate this new tree.

EF01 keeps `VERIFIED_SCOPE_REVIEW_REQUIRED` and explicit review metadata after
ordinary scope drift passes a fresh broad registered run. EF02 independently
checks source/HEAD/worktree identity, file and tracked-patch hashes, registered
MT-01 evidence and artifact hashes. It may publish a create-only immutable
candidate with `CANDIDATE_READY_SCOPE_REVIEW`, `review_required=true`,
`scope_valid=false` and exact out-of-scope files. Its canonical commit message
binds the RemoteCandidateEvidence v1 contract, original scope, provenance and
verification proof. Normal allowed PASS remains `CANDIDATE_READY`.

Scheduler acceptance, direct fan-in, reuse, completion serialization and artifact
provenance retain or enforce review metadata. Fan-in and reuse inspect immutable
commit evidence even if mutable status/flags have been stripped. Automatic
consumers block review candidates pending Brain assessment; no automatic scope
acceptance or merge path is added. Protected/credential paths, unsafe ignored
files, traversal, symlink/junction ancestors, changed HEAD, denied tools,
unknown provider effects, failed tests and missing/tampered proof remain stops.

Regression coverage includes ordinary and allowed PASS, immutable review
publication, no laundering through reuse/fan-in/completion, protected paths,
unknown/failed execution, tampered evidence, ignored caches and credentials,
and real read-only Git CRLF/LF attribute behavior. Full verification uses
`python -m pytest tests -q -n 12`; executor-local results and independent EF01
registered results must remain distinct. No candidate publication is claimed
by this executor.

## Current executor-local verification

The affected eight-file suite returned **269 passed, 17 failed**, exit 1, in
19.87 seconds. All 17 failures occurred at disposable Git fixture commits;
Git Bash reported `NtCreateDirectoryObject ... 0xC0000022`. The 52
candidate-integrity regressions, including the real read-only attribute/byte
test, had no failures. The unchanged #150 fan-in regressions also passed.

One full `python -m pytest tests -q -n 12` run returned **2729 passed,
21 skipped, 36 failed**, exit 1, in 251.26 seconds. All 36 failures occurred at
disposable Git fixture commits (29 subprocess errors and seven guard-fixture
assertions). The tested files remained byte-identical throughout the run.
No guard/environment bypass, artificial skip, source repair for these fixture
failures, or second full retry was performed. The inherited Git Bash sandbox
failure remains a parent-environment verification requirement.

The [verification record](EF02_SCOPE_PUBLISH_VERIFY_2026-10-30.json) retains
exact commands, summaries, failing IDs, diagnostic excerpts, observed log
digests, reconstruction provenance and per-file source/candidate byte checks.
Raw logs were in ephemeral process-sandbox temporary directories; their paths
are historical locations, not durable artifacts. The record explicitly marks
that limitation. EF01 must preserve its own registered raw logs after this
executor finishes. Current registered PASS, candidate SHA/ref, Brain acceptance
and merge remain unclaimed.
