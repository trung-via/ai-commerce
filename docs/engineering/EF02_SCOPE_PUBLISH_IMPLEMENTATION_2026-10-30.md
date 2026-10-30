# EF02 scope-review candidates — PR #156 integration

## Pinned provenance and publication boundary

The previous scope-review candidate is
`fd947bf6740279b68ce5f1212322eb3e18c351e9`, at
`refs/heads/ef02/candidate/05fa5a560714076a0d78dc7ef07ee47b`, from
[run 41083207739](https://github.com/trung-via/ai-commerce/actions/runs/41083207739).
Its source was `182d7471332c781e4dacdcbff3d6bcdff50b4e36`.
The Human reports that PR #156 could not merge because of conflicts.

This integration starts from pinned main
`6601881e7b498ae139fbe8a8f04c195905b15ed3`, including PR #148 Chrome
CDP acquisition and PR #155 Antigravity diagnostics/authoring preflight.
The exact old 21-file diff was inspected. Only feature code, regression tests,
contract and policy documentation are transplanted. The old local verification
JSON is not copied as evidence for this tree. Current executor-local results,
changed paths and byte checks are recorded in
[the integration verification record](EF02_SCOPE_INTEGRATION_VERIFY_2026-10-31.json).
Old PASS counts do not verify the integrated tree.

Executors edit ordinary worktree files. EF01 independently verifies the final
bytes; EF02 alone may publish a new create-only immutable candidate branch.
No executor commit/ref/config writes, update to PR #156, force push or merge
is part of this reconciliation. Hosted PR CI must run on the integrated branch
before a Human-authorized merge.

## Semantic reconciliation and consumers

The runner's invocation defaults and final report propagation combine the new
review/proof fields with PR #155's denied-target diagnostics, provider test
observations and failure provenance. Antigravity's disposable input context,
mirrored repair feedback, interrupted-outcome classification and unchanged
failure-precondition stop remain. Chrome acquisition, its operator/profile/
process ownership and CDP handshake gates, and authoring-preflight source are
unchanged. The latter consumes the strengthened shared path and reuse checks.

EF01 retains `VERIFIED_SCOPE_REVIEW_REQUIRED` after ordinary drift passes a
fresh broad registered run. EF02 independently checks source/HEAD/worktree
identity, exact file/patch hashes, registered MT-01 proof and artifact hashes.
A verified ordinary drift candidate carries `CANDIDATE_READY_SCOPE_REVIEW`,
`review_required=true`, `scope_valid=false` and exact `out_of_scope_files`.
Canonical commit evidence binds scope, provenance, verification and bytes.
Normal allowed PASS remains `CANDIDATE_READY`.

Scheduler acceptance, direct fan-in, reuse, completion serialization and artifact
provenance retain/enforce review metadata. Immutable commit evidence prevents
mutable flags from laundering a review SHA. Protected/credential paths,
ignored unsafe files, traversal, symlink/junction ancestors, source drift,
denied tools, unknown effects, failed tests and unproven artifacts remain stops.
No automatic Brain acceptance or merge path is introduced.

## Byte integrity and verification

Changed Markdown is UTF-8 LF before verification, preserving the earlier clean
filter repair. Python/JSON retain their exact bytes under existing `-text -eol`
attributes. Every changed file is checked against Git's raw and path-filtered
blob hashing; the publisher's final manifest/filter checks remain in place.
No `.gitattributes`, `.github` or other protected path is edited.

Targeted tests include the existing #148 Chrome acquisition, doorbell and
completion consumers, #155 diagnostics/preflight, scope-review publication,
registered proof tampering, Git guard, MT-01 and byte-consistency regressions.
New composition tests check AGY scope-review diagnostics and repair inputs,
denials with ordinary drift, preflight protected paths/reuse, and a review
completion through the synthetic exact-chat doorbell without acceptance.
Full local verification uses `python -m pytest tests -q -n 12` with at most two
bounded verify/fix rounds. Executor-local tests are distinct from EF01 registered
verification, EF02 publication and hosted CI; none substitutes for the others.

## Observed integration verification (2026-10-31)

The initial targeted run returned **403 passed, 25 failed** in 35.14 seconds.
Two new denial fixtures had a missing Python statement separator; that test-only
error was fixed in the second bounded round. The other 23 failures were
blocked disposable Git initialization. The corrected seven-file integration
suite returned **187 passed**, exit 0, in 15.05 seconds, covering scope review,
Chrome acquisition/doorbell/completion, Antigravity diagnostics and preflight.

The exact full command returned **2811 passed, 21 skipped, 36 failed**, exit 1,
in 246.75 seconds. All 36 failures occurred at disposable `git init` (29
subprocess errors and seven setup assertions). Git Bash reported
`NtCreateDirectoryObject ... 0xC0000022` under the inherited guard. Skips reflect
native link restrictions and one POSIX-only permission test. No guard, hook,
environment or test-skip workaround was applied. No further full retry is useful
under the unchanged initialization precondition.

All 22 changed file byte hashes remained unchanged during the full run.
Afterward only this documentation and the verification JSON were finalized;
source/tests remain exactly the tested bytes. Final file/filter checks and
`git diff --check` pass. Independent EF01 registered verification on final bytes
is still required. This executor publishes no candidate and claims no merge or
hosted CI result. EF02 alone may publish a new immutable candidate after its
gates pass; hosted PR CI must then run on that integrated branch before merge.
