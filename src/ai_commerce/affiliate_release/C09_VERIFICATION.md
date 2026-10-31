# C09 fresh local verification — 2026-10-30

State: **LOCALLY_VERIFIED**. Official EF01 project verification and EF02
candidate publication remain pending. This is neither a media release nor an
external/legal approval. Candidate SHA/ref produced by this executor: **NONE**.

## Attempt, source and scope

- Action: A13-C09, attempt `a13-c09-codex61solmax-20261030-v1-02-scope`,
  the fresh continuation in Issue #139 of previously blocked Issue #135.
- Executor: Codex; request pins `gpt-6.1-sol`, effort `max`. There was no C09
  candidate, executor invocation or verification artifact to reuse from #135.
- Authorized main source: `07ce567ee6250d162eaf695fc74a7f1def9bb001`.
- Supplied isolated detached worktree:
  `C:/Users/TRUNG/AppData/Local/Temp/ai-commerce-ef02-A13-C09-uuoyjaxd`.
- Initial and final unchanged HEAD:
  `0d23e8a1375e9fd23833e77bd141e4d1670958cf`. Its sole parent is the
  authorized main source. The integration commit contains only the seven
  inherited C08 component/test files. Initial status was clean.
- Actual permitted paths were checked against the fresh #139 request:
  `src/ai_commerce/affiliate_release`,
  `contracts/ClaimRightsReleaseReview.v1.schema.json`,
  `tests/test_a13_claim_rights.py`.

No Git write was invoked. HEAD, refs, configuration, guards and environment
were left unchanged. Read-only Git status/diff/log/rev-parse established source,
scope and protected-file preservation. No hook, environment, guard or permission
bypass was attempted. No workflow, EF01/EF02, A12, A07 or A08 implementation
was edited. C08 implementation, schema and tests remain unchanged; the shared
component `__init__.py` has additive C09 exports.

## Executed checks

Runtime observed in pytest: Windows, Python 3.14.7, pytest 8.4.2,
pytest-xdist 3.8.0. The final focused run explicitly reported
`created: 12/12 workers` and `12 workers [82 items]`.

| Check | Observed result |
| --- | --- |
| Fresh inherited C08 dependency, before C09 implementation | 38 passed, 1 skipped; exit 0; 10.33 s |
| New C09 tests before implementation | Import/collection failure for missing `AssetUsage`; exit 1 |
| First C09 implementation | 49 passed, 1 failed; exit 1; missing exported contract file |
| Corrected schema export using this worktree's source | 50 passed; exit 0; 3.56 s |
| Expanded adversarial C09 checks | 74 passed, 6 failed; exit 1; 12.21 s |
| One bounded same-executor implementation repair | 79 passed, 1 failed; exit 1; expected reason-code assertion needed updating |
| C09/C08 plus affected A04–A08 and A12 regression | **1,013 passed, 6 skipped; exit 0; 138.16 s** |
| Final C09, including two additional missing-rights/pure-byte checks | **82 passed; exit 0; 11.84 s** |
| Python 3.11-compatible AST parsing, schema validation, contract mirror, whitespace | PASS |
| Read-only diff/scope/unchanged HEAD and protected implementations | PASS |

The initial schema-export helper imported the installed package, which lacked
this new worktree module. The corrective command prepended this worktree's
`src` to that helper's Python import path and generated only the permitted
contract file. No environment or installed package was modified.

The six adversarial failures identified three causal issues: missing grant
proof could mask sourced revocation (three cases), a positive notice could mask
an UNKNOWN notice (one), and trailing-newline alias acceptance (two). One local
implementation repair moved negative-status assessment ahead of missing-grant
returns, retained UNKNOWN notices, and tightened aliases. The remaining
assertion expected the old generic missing-evidence code; it was corrected to
the specific `RIGHTS_REVOCATION_UNKNOWN` code. There was no provider replay,
executor change or Git write. The final production implementation and contract
were unchanged after the combined regression; only two new C09 test cases were
added before the final 82-test run.

Fresh dependency command:

```text
python -m pytest tests/test_a13_media_inspection.py -q -n 12
```

Combined regression command:

```text
python -m pytest tests/test_a13_claim_rights.py tests/test_a13_media_inspection.py tests/test_a04_media_operations.py tests/test_a04_upgrade_v11.py tests/test_a05_render.py tests/test_a05_composition.py tests/test_a05_integration.py tests/test_a06_e01.py tests/test_a07_capability.py tests/test_a08_distribution.py tests/test_a12_snapshot.py tests/test_a12_identity.py tests/test_a12_claims.py tests/test_a12_offer.py tests/test_a12_rights.py tests/test_a12_registry.py tests/test_a12_integration.py tests/test_a12_cli.py -q -n 12
```

Final focused command:

```text
python -m pytest tests/test_a13_claim_rights.py -n 12
```

The six regression skips are two A04 directory-link cases, one A04 hardlink
case, one C08 native-link case and two A12 registry native-link cases. The
sandbox denied native link creation; these skips do not prove host link
creation permission. C09's real A05/A06/C08 integration tests ran with installed
FFmpeg/FFprobe and passed; they were not mocked or skipped.

The unchanged registered `project-tests` command was inspected:
`python -m pytest tests -q -n 12`. This executor did not invoke the entire
project suite, whose engineering fixtures exercise Git writes. Those remain
with official EF01 under the user's Git write guard. The results above are
affected-component regression evidence, not a claim of full project-tests PASS.

## Acceptance review

| Criterion | Local verdict and evidence |
| --- | --- |
| Actual APIs and C08 prerequisite | SATISFIED: public A12 APIs inspected and used; fresh C08 run; real render/custody/inspection fan-in exercised. |
| Exact pack/product/variant and claim evidence refs | SATISFIED: original snapshot and evidence replay, full subject/projection comparison, explicit request binding and content hashes. Wrong variant/product/listing and false verified summaries tested. |
| Seller assertions and fiction | SATISFIED: unreviewed seller/source claims need review; conflicts/counterevidence persist; exact statements and separate reports required. Nonfactual declarations remain outside factual review. |
| Rights from actual use | SATISFIED: local byte hashes, direct-output or A05 source binding, source omission detection, independent per-purpose checks, derivative scope and unused-asset isolation tested. |
| License, source, territory, time, restriction and revocation | SATISFIED: actual A12 instruments/reviews and concrete assessments reused; status-source binding and complete term checked. Revocation prevails even if grant proof is absent. Missing/expired/revoked/synthetic/source-mismatched cases tested. |
| Four statuses with reasons and no external approval | SATISFIED: schema/result assertions cover all states; external/legal/account/link remain UNKNOWN and publication NOT_ATTEMPTED. |
| Tamper and credible synthetic evidence | SATISFIED: changed bytes, original proof/license/source tamper, false pack summaries, resealed receipt changes, changed status inputs and synthetic subjects are rejected or remain unverified. |
| Offline, deterministic, creative freedom and preserved contracts | SATISFIED locally: blocked network/file/subprocess tests for the pure reviewer, deterministic source-backed restoration, unchanged A12/A07/A08/C08 regression and no creative-flow mutation. |
| Full project verification/candidate publication/main | PENDING EF01/EF02; this executor makes no claim of publication, merge, deployment or real rights. |

This is an explicit second-pass local acceptance review, not an independently
authenticated audit. Source truth, legal permission, inventory completeness,
and future/live revocation remain outside what these deterministic checks prove.
See `CLAIM_RIGHTS.md` for the API and evidence boundary.

## Changed files and content pins

Only these seven ordinary files belong to C09:

```text
src/ai_commerce/affiliate_release/__init__.py
src/ai_commerce/affiliate_release/claim_rights.py
src/ai_commerce/affiliate_release/claim_rights_schemas.py
src/ai_commerce/affiliate_release/CLAIM_RIGHTS.md
src/ai_commerce/affiliate_release/C09_VERIFICATION.md
contracts/ClaimRightsReleaseReview.v1.schema.json
tests/test_a13_claim_rights.py
```

SHA-256 pins of implementation/contract/final tests (the report excludes itself):

| File | SHA-256 |
| --- | --- |
| `__init__.py` | `025fbf4e329c14fca86809c64002edb541f78ed38c5c24c82f4b063cd8ed29b4` |
| `claim_rights.py` | `f190b9b7f6066501900c188e929de45d58a11a0795d2e22a6c3bc9bc1942559f` |
| `claim_rights_schemas.py` | `f9c1a3b464fd8013b38b3ad3c438b0384737686fe9c88b5a4a48194f103e3c95` |
| `CLAIM_RIGHTS.md` | `8806c7ebeb7dcffcdd63e0080ebd8ef1bd30f50e3066fd5f63c0083f51aaab0c` |
| `contracts/ClaimRightsReleaseReview.v1.schema.json` | `196316e8d658d1b54d57175d82dc9724bd1d9b890ea74b8314eea43b05bff990` |
| `tests/test_a13_claim_rights.py` | `4db9304dceb5963884c05dcde85b81a6b8cb75d13542a9177757c9410896a4fd` |
