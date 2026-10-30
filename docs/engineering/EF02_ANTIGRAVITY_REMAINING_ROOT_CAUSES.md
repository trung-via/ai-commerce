# Issue #151: scoped headless diagnostics and optional authoring preflight

This patch implements the ordinary-file scope authorized by [#152](https://github.com/trung-via/ai-commerce/issues/152), against `182d7471332c781e4dacdcbff3d6bcdff50b4e36`. It is an executor worktree patch awaiting independent EF01 verification and EF02 publication. No candidate SHA/ref, main merge, or live Antigravity permission success is claimed.

## Evidence and remaining uncertainty

The issue bodies and GitHub Actions bot summaries were read through the GitHub connector. Historical private artifacts were not required, fetched through AGY, or treated as current source.

| Historical action | Observed published evidence | Interpretation |
| --- | --- | --- |
| [#108](https://github.com/trung-via/ai-commerce/issues/108), run 40981584522 | Summary says `CANDIDATE_READY`; request scopes reads to source `adapters.py` and prepared input `instruction.txt` | Evidence of a successful scoped action, not proof of every possible read permission |
| [#126](https://github.com/trung-via/ai-commerce/issues/126), run 41006579806 | Summary says `CANDIDATE_READY`; prompt explicitly restricts inputs to worktree and `agy_input` | Consistent with avoiding external evidence dependencies; does not establish the prior denied target |
| [#124](https://github.com/trung-via/ai-commerce/issues/124), run 41004515078 | Summary says `EXECUTOR_PERMISSION_DENIED` | `DENIED_TARGET_UNKNOWN`: the inspected summary does not contain a structured target |
| [#140](https://github.com/trung-via/ai-commerce/issues/140), run 41036251447 | Summary says `EXECUTOR_PERMISSION_DENIED` | `DENIED_TARGET_UNKNOWN`: the inspected summary does not contain a structured target |

#124 already made historical artifact recovery conditional, and #140 already said no mandatory network artifact download. It would be incorrect to assert that either denial was definitely caused by an external path. The exact historical target and effective external permission remain unproven.

A deterministic current-source gap was reproduced: `_repair_prompt` required reading runner-root logs/diffs even though `command_for` registers only the isolated worktree and disposable `agy_input`. AGY repair guidance now uses the existing mirrored feedback, inline previous invocation diagnostics, and current source. External artifacts are optional provenance references. Both ordinary and compact headless wire prompts explain this boundary and stopping behavior. No new input roots, settings changes, tool-policy bypass, or live AGY call was introduced.

## Safe diagnostics and provenance

`runner.py` adds `denied_action_diagnostics` to each invocation and the final engineering result. It reads only structured top-level provider denial data, accepting explicit path fields directly or within structured `arguments`, `parameters`, or `args`. Missing paths remain `DENIED_TARGET_UNKNOWN`; explicit paths are `DENIED_TARGET_KNOWN` without revealing their value. Prose, agent comments and nested tool payloads cannot supply a target.

Only bounded tool names and lexical classifications are emitted: `WORKTREE`, `AGY_INPUT`, `EXTERNAL`, `PRIVATE_PATH`, `RELATIVE`, `UNCLASSIFIED`, or `UNKNOWN`. Relative paths do not imply a working-directory base. Private ancestry outside a registered root is distinguished from ordinary source; a registered root itself may legitimately reside under `.local` or `.codex`. Classification does not resolve symlinks, open targets, or prove permission. The first 32 denial diagnostics are retained; unknown actions and nonempty malformed denial containers fail closed as `UNKNOWN_TOOL`.

Explicit denials retain `EXECUTOR_PERMISSION_DENIED / HEADLESS_TOOL_PERMISSION_DENIED`, including provider `SUCCESS`, exit zero, partial writes, or a simultaneous quota message. Nonzero completed provider returns are classified before the previous generic early return, so quota/capacity remain distinguishable. Source integrity problems retain precedence. Permission denial and unknown effects do not enable repair or a fallback provider.

| Evidence origin | Representation and authority |
| --- | --- |
| `REQUEST_VALIDATION` | Optional preflight rejection, safe error code, request fingerprint and available source/attempt metadata; no execution |
| `HEADLESS_TOOL_DENIED` | Runner `failure_provenance`, safe tool/target classification; verification is not run |
| `PROVIDER_QUOTA` / `PROVIDER_CAPACITY` | Provider failure classification, separate from internal test observations |
| `AGENT_INTERNAL_PYTEST` | At most 16 structured provider-reported command results in `provider_test_observations`; agent prose cannot establish execution; never sets `verify_exit_code` |
| `EF01_REGISTERED_VERIFY` | Runner provenance only when registered EF01 verification actually starts; manual local verification has separate `EF01_VERIFY` origin |
| `GIT_CANDIDATE_PUBLICATION` | Existing EF02 worker/publisher result stage, including `CANDIDATE_INTEGRITY_FAILED` and `RESULT_PUBLICATION_FAILED`; EF01 cannot attest publication, and this patch does not change that consumer or publisher |
| `UNKNOWN_EFFECTS` | Unconfirmed provider outcomes, interrupted verification or integrity uncertainty; no automatic paid replay |

Runner failure fingerprints bind source, pinned executor/model/effort, platform/sandbox classification, original instruction hash, candidate content hashes and requested verify argv. They exclude raw diagnostics, log text, volatile transport prompts and attempt identity. The repair loop stops with `UNCHANGED_FAILURE_PRECONDITION` if a repair returns the same failure fingerprint and precondition. Attempts remain separately identified in bounded metadata. Existing durable EF02 claims remain the authority against redispatch; no new lifecycle store or global approval gate was added.

These fields are additive in `invocation_result.json` and `engineering_result.json`. Existing batch summaries continue to use their existing status/error fields; consumers needing detailed provenance should read the EF01 artifact. Raw provider/verify evidence remains separately recorded by the existing runner.

## Optional authoring use

Run from any directory before creating an immutable EF02 JSON Issue:

```powershell
python scripts/EF02-Authoring-Preflight.py --request-file draft.json --checkout . --check-source --expected-files expected-files.json
```

`expected-files.json` is a separate map of action IDs to explicit ordinary file paths, for example:

```json
{"action-a": ["src/ai_commerce/example/core.py", "tests/test_example.py"]}
```

The library entrypoint is `ai_commerce.remote.authoring_preflight.preflight_batch`; module execution with `python -m ai_commerce.remote.authoring_preflight` is also available in an installed project or configured source environment. `--help` documents argv.

The check reuses the real `RemoteActionBatch.v1` schema, registered verification profiles, semantic action/DAG checks and `reuse.approve_reuse`. `validation.validate_batch` still requires trusted issuer authorization; only the non-dispatch content checks were extracted for offline reuse. Actor strings and authorization references cannot prove issuer rights. Every offline result reports `authorization=UNPROVEN`, including a structurally valid packet.

Checks include duplicate `allowed_paths` (#143), safe ordinary paths, unsupported globs, segment-boundary coverage of expected files, registered profiles, source SHA format, duplicate action/attempt IDs, missing dependencies and cycles. `--check-source` optionally compares `source_sha` to checkout `HEAD` using read-only `git rev-parse HEAD`. Without it, source existence/currentness is not established.

Reuse without a matching approved entry returns `REUSE_UNAPPROVED` (#142), including absent default registries. The CLI defaults to the checkout's existing `.github/ef02/approved-candidates.v1.json` if present; `--approved-reuse FILE` selects an operator-supplied registry. A local match checks the existing source/profile/scope/verification eligibility contract, not registry authenticity, current candidate bytes, or actor authorization. Output says `LOCAL_REGISTRY_MATCH_ONLY`; authoritative server validation remains required.

Exit 0 means `PREFLIGHT_PASS` for the offline checks only. Exit 2 means `PREFLIGHT_REJECTED`; examples include `DUPLICATE_ALLOWED_PATHS`, `REUSE_UNAPPROVED`, `EXPECTED_PATH_OUTSIDE_SCOPE`, `VERIFICATION_PROFILE_UNREGISTERED`, `DEPENDENCY_CYCLE`, `INVALID_SOURCE_SHA`, and `SOURCE_SHA_MISMATCH`. Unreadable/malformed local input returns `LOCAL_INPUT_INVALID`; schema failures return `REQUEST_SCHEMA_INVALID`. Error output does not echo request content, provider output or full local paths. Argparse usage errors also exit 2.

This optional helper reads local inputs and, only when requested, local HEAD. It performs no dispatch, GitHub mutation, model call or pending-remote ownership inspection. It cannot predict unlisted output files; supplying expected files is an authoring aid, not a replacement for runtime scope reconciliation.

## Verification and publication status

- Regression-first evidence: initial new tests failed on the absent authoring module, missing diagnostics/provenance, ignored malformed denials and nonzero provider classification. A separate regression reproduced the AGY repair instruction's external-log requirement before it was patched.
- Final focused command: `python -m pytest tests/test_ef01_agy_diagnostics.py tests/test_ef02_authoring_preflight.py tests/test_ef01_provider_error_payload.py -q` — **51 passed**, exit 0. Includes known/unknown/private targets, root ancestry, bounded redaction, denial plus exit zero/nonzero, quota/capacity, internal-test separation, unchanged-precondition stopping, valid packet, duplicate paths, unapproved reuse, source/profile/DAG/path failures, CLI use from an unrelated directory and no-child default offline execution.
- Expanded affected checks earlier recorded **169 passed / 20 failed**, exit 1. The failures were existing fixture Git initialization denied by the inherited executor hook.
- Exact registered argv was run locally inside this executor: `python -m pytest tests -q -n 12` — **36 failed, 2712 passed, 21 skipped in 269.09s (0:04:29)**, exit **1**. Reported failures are fixture Git initialization/reference-transaction-hook denials, with some sandbox `0xC0000022` access failures. This is `AGENT_INTERNAL_PYTEST` evidence, not the independent worker's official EF01 result. Final malformed-container/private-target sanitization regressions were added after that full run and are covered by the final focused PASS; final bytes still require worker verification.
- The local full-run stdout/stderr were redirected to sandbox temporary files, which the sandbox removed when the command exited. The tool transcript retains the output tail, exact summary and exit; complete raw local logs are **not retained**. No complete-log hash is claimed. The worker must retain its own official full stdout/stderr/exit and candidate integrity evidence.
- No attempt was made to bypass hooks, change Git config/environment, repair excluded #145/#150 fixture isolation, expand #102 roots, modify `.gitattributes`, or replace #117 publisher integrity. A repeat full run under the unchanged environment would not resolve these failures.
- Source HEAD remained `182d7471332c781e4dacdcbff3d6bcdff50b4e36`; `git diff --check` passes. No Git writes, candidate publication, model fallback or automatic main merge were performed by this executor. Candidate SHA/ref are **NONE / awaiting EF02 worker**, contingent on official scope/test/integrity verification.

Changed ordinary files are exactly `runner.py`, `remote/validation.py`, `remote/authoring_preflight.py`, `scripts/EF02-Authoring-Preflight.py`, `tests/test_ef01_agy_diagnostics.py`, `tests/test_ef02_authoring_preflight.py`, and this report, all in #152's allowlist. The other authorized files did not need edits. The EF02 collector can hash the final files and tracked diff directly; no candidate commit is inferred from these worktree bytes.

## Independent #149 integration review

[#149](https://github.com/trung-via/ai-commerce/issues/149), run 41079392060, was in progress at authorization. Its later bot summary now reports `CANDIDATE_INTEGRITY_FAILED`; this patch neither diagnoses that publication failure nor modifies its attempt, branch or records.

Its broad engineering/remote scope overlaps this patch. Reconcile `runner.py` additive invocation/result fields, provider classification order, repair-prompt signature, fingerprint stopping, and `validation.py`'s shared content-check extraction against the actual later candidate/current source before integration. In particular, preserve any separately reviewed scope-publication semantics from that work. Standalone test evidence does not prove a composed tree. Human must review the combined diff/consumers, require fresh applicable verification, and separately authorize main merge.
