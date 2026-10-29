# A11 audience learning implementation evidence

Date: 2026-10-30. WorkItem `A11-AUDIENCE-LEARNING-001`, Roadmap A `L03/L06`.
Action `A11-04`, attempt `a11-04-codex61solmax-20261030-v1-04`, recovery batch
`a11-issue97-codex61solmax-repair-20261030-v1-04` ([Issue #99](https://github.com/trung-via/ai-commerce/issues/99)).
Human selected `codex / gpt-6.1-sol / max`, candidate branch delivery only.

## Source and actual fan-in

The immutable packet pins A11-02 candidate
`43e5084a093d3c17dd5fca007bdbfe1f6a0f3598`, published at
`refs/heads/ef02/candidate/672624aeefe404a661644c3f1da8322d`. Its parent is the
published A11-01 candidate `575dd29cc06a71f7b059ccc6635d63025436669f`.
Neither action was reimplemented or redispatched. The historical
[Issue #97 run](https://github.com/trung-via/ai-commerce/actions/runs/40920530455)
had A11-02 ready (1587 passed, 3 skipped), A11-03 Antigravity headless
`read_file` permission denial with no changes/tests/candidate, and A11-04 blocked.
The new attempt changes the authorized provider precondition; it does not
change permissions or bypass the sandbox.

Before coding, the existing runner's A11-03 `action_result.json` was read:
`CANDIDATE_READY`, verify exit 0, `codex/gpt-6.1-sol/max`, source
`43e5084a093d3c17dd5fca007bdbfe1f6a0f3598`, candidate
`a3b0b281db430e9370388e66ac63549facc7f83c`, ref
`refs/heads/ef02/candidate/5523f794b929819130f1cbf2aa7d3d00`.
Its actual Windows registered log reports **1664 passed, 4 skipped in 134.04 s**.
Only `learning/registry.py` and `tests/test_a11_registry.py` were changed there.

GitHub's candidate commit was independently read. Its sole parent is the pinned
A11-02 source and its tree is `4782adb3cd4f6a6022261ce9931ce034ff9c0acf`.
This clean detached checkout began at EF02 integration commit
`71b24c25ad80d3900c333d6ed1312be629161c23`, whose sole parent is that same pinned
source and whose tree is **exactly the same**. EF02 composes dependency trees;
the A11-03 commit is not an ancestor of this integration commit. The proven fan-in
is the exact tree equality plus both parent/source identities, not an invented
merge ancestry. Initial `git status --porcelain=v1` was empty.

The WorkItem, canonical roadmap L03/L06, adopted scope decision, existing CLI,
all seven frozen A11 contracts and the actual reader/report/registry consumers
were read before implementation. The registered `project-tests` definition is
unchanged: `python -m pytest tests -q -n 12`.

## Resulting behavior and owned scope

The additive `learning` CLI provides `snapshot`, `report`, strict `compare`,
explicit `append`, `inspect`, `export`, and archive-only `reload`. Existing
commands and their exit conventions remain available. New A11 failures return
bounded JSON on stderr with exit 60. Read commands do not initialize databases;
the optional export destination is created exclusively, without replacing data.

`LearningAdvisory.v1` is a deterministic JSON artifact for Human/Brain/Creative
consultation, with a public envelope schema and immutable Python read object.
It preserves the entire revision history and all negative/inconclusive evidence,
source/metric/window/cohort/version/denominator context, uncertainty and
confounders. Reload checks inner contracts, resolved evidence, report arithmetic,
revision lineage and event hashes. It does not import events or call Creative,
generate concepts, enforce a gate, publish, or increase permissions.

The actual report/registry consumer exposed two integration defects, reproduced
by new tests before repair: a required but absent denominator was labeled
AVAILABLE, and resealing a numerically forged report could pass registry append.
The narrow report fix labels the first INSUFFICIENT_DATA, retaining numerator and
null denominator/value; it also rejects combining denominator observations with
different metric definitions. Registry validation now checks the report's
SUM/MEAN/COUNT numerator and ratio against its archived primary facts. Existing
report signatures and all frozen contract schemas remain unchanged.

Owned writes are `src/ai_commerce/learning` (shared exports, CLI adapter, advisory
export/reload, synthetic demo and reporting/registry consumer reconciliation),
the additive hook in `src/ai_commerce/cli.py`, `tests/test_a11_integration.py`,
`scripts/a11_demo.py`, `examples/a11`, and this report. No root scope was granted.
A11-01 reader/core/schema and all root contracts are unchanged. No A03, A08,
A09, engineering runtime, work item, roadmap, `.github`, `.git`, `.local`, secret,
dependency or permission configuration is modified.

## Verification

Environment observed: Windows 11 build 26300, Python 3.14.7 AMD64, pytest 8.4.2,
pytest-xdist 3.8.0, SQLite 3.50.4. Provider/credential environment variables are
removed by name before launching test children; their values are never logged.
HEAD and changed paths are checked before candidate-controlled tests.

Evidence under `examples/a11/evidence/` preserves the initial determinate
failures and subsequent commands, outputs, source provenance and file hashes.

- Initial new integration suite: **2 failed, 27 passed in 10.03 s**. The failures
  are the two report/consumer defects above; this evidence is retained.
- After the targeted repair, actual reader + reporting + registry + integration
  suite: **218 passed, 3 skipped in 15.84 s**, exit 0. These three skips are the
  host's restricted symlink/hardlink fixtures, not successful native link proofs.
- Final integration suite, including the additional denominator-definition cases:
  **31 passed in 9.70 s**, exit 0.
- `python scripts/a11_demo.py --output examples/a11/synthetic`: exit 0. The
  actual module CLI reopened its advisory JSON successfully. Synthetic numeric
  golden cases show zero AVAILABLE, missing NOT_AVAILABLE, zero denominator and
  zero-baseline relative change INSUFFICIENT_DATA, cohort mismatch NOT_COMPARABLE.
  Three immutable revisions retain support, zero counterevidence and missingness;
  A09 bytes are unchanged after the explicit synthetic bootstrap. Tampering is
  rejected. The integration tests repeat the complete demo in two independent
  directories and compare identical summaries and archive bytes.
- First registered full Windows `project-tests`: **1691 passed, 8 skipped in
  248.84 s**, exit 0. All 169 Python source/test/script hashes were unchanged
  across that run. Its log/result and before-hash manifest are retained with
  `pre-normalization` names.
- The final integrity review read the actual publisher implementation: it uploads
  raw file bytes, whereas local Git text diffs normalize inherited CRLF checkout
  bytes. Only the four already modified Python files were normalized to LF to
  retain the source's line endings in the published blobs. This changes no Python
  statements, signatures or tests. The full profile is rerun on those exact final
  bytes rather than reusing the earlier PASS for different file hashes.
- Final registered full Windows `project-tests` on LF candidate bytes:
  **1691 passed, 8 skipped in 257.73 s**, exit **0**. The actual run requested
  **12 xdist workers**. All **169** Python source/test/script SHA256 values and
  HEAD were identical before and after this final run. Wall time including
  subprocess startup was 258.705 s. The eight skips are the actual restricted
  Windows symlink/junction/hardlink fixtures in A04/A09/A11; all 31 new integration
  cases executed. Full stdout SHA256:
  `44937d58db2150dd9b55f65c83adb5c78ecdff443c6b1e9010c8fd96e8e87d9a`.

The final code-map SHA256 (sorted compact JSON of path to raw-byte SHA256) is
`6e314248744243eb6e533c889d329d734e49847527e92403f3dcc4173b5dd67a`.
`git diff --check` and Python 3.11 syntax parsing of authored Python files pass;
the executing interpreter remains Python 3.14.7. The actual Git API publisher
uploads raw bytes, so final authored Python files retain LF before handoff.

Evidence index:

- `examples/a11/evidence/fan-in.json`: actual A11-03 result and exact dependency
  candidate/integration tree and parent identities, with original test-log hash.
- `verification-before.json` and `verification-after.json` in that directory:
  all code hashes, registered argv, unchanged-code result, elapsed time and logs.
- `initial-integration.txt`, `a11-targeted.txt`, `final-integration.txt`,
  `project-tests-pre-normalization.txt`, `project-tests.txt`: complete observed
  failure/repair/verification output; no determinate failure was hidden.
- `normalization.json`: exact before/after hashes proving the line-ending-only
  Python normalization; the preceding full PASS is retained, not reused for the
  changed bytes. This was an integrity correction, not a new provider attempt.
- `acceptance-review.json`: explicit read-only second pass, with Ubuntu UNKNOWN.
- `candidate-files.json`: final changed-file raw SHA256/Git-blob identities and
  scope/protected-path checks, excluding itself and the following diff artifact
  to avoid circular hashes. `a11-04.diff.patch` contains the complete Git text
  diff, including untracked candidate files and that manifest, excluding only
  the diff artifact itself. Raw hashes are authoritative for line endings.

## Acceptance and limitations

AC01 uses the inherited pinned, replay-checked A09 reader, with byte-equality
checks in the integrated CLI/demo. AC02/03 have descriptive arithmetic and typed
state tests, missing/ambiguous denominator rejection and registry arithmetic
checks. AC04 keeps the fixed noncausal inference and cross-provider policy.
AC05/06 use the actual A11-03 append-only SQLite events/triggers, transaction,
correction, idempotency and corruption tests plus integrated negative revisions.
AC07 exports archived, redacted advisory facts and never changes creative or
publishing data. AC08 has deterministic actual E2E and corruption/negative tests;
Windows aggregate proof is recorded above. **Ubuntu remains
UNPROVEN** in this Windows-only execution. No hosted CI or live-provider result
is claimed.

All sources remain `USER_SUPPLIED_NOT_LIVE_VERIFIED`, including synthetic data.
Snapshot/report loading validates archived identities, not a fresh raw replay;
the explicit `snapshot` command performs the existing A09 replay. Report v1 does
not store aggregation selection; registry validation recognizes its three
existing calculations, but cannot identify the author's intention when they
give identical numbers. A supplied denominator is a user declaration, not an
independently verified exposure quantity. Archived redacted report bodies retain
original report refs; their original content IDs cannot be recomputed after
account redaction. The local event/export hashes cover the redacted derivative.

No raw rows, post identifiers, URLs or full snapshots are duplicated into the
advisory export. Non-personal prose is required, but contact pattern checks are
not comprehensive PII detection. Local hashes are not external attestation and
cannot defeat a coherent whole-history rewrite. A valid resealed prefix requires
a separately retained `expected_id` to detect truncation. The export's bounded
32 MiB document size can reject a larger otherwise valid 64 MiB registry.
Concurrent hostile filesystem replacement is outside the inherited local model.

No OAuth/API/scraping, external spend, model call, automatic posting, earnings,
causal-lift assertion or AIOS runtime was introduced. Delivery remains a verified
isolated worktree for the existing EF02 publisher. This executor keeps HEAD at
the integration SHA; shared Git metadata is outside the writable sandbox.
The publisher assigns the final candidate branch/SHA after its own exact-source,
scope/hash and verification checks. A local PASS is not a published candidate,
main merge, Human acceptance, product release or commercial outcome.
