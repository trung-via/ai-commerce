# EF02 Multi-Action v1 — Implementation report

Date: 2026-10-28. Repository: trung-via/ai-commerce. WorkItem: EF02-MULTI-ACTION-001.

## Implemented source

EF02-A: JSON Schema, actual GitHub issue-issuer authorization, unique IDs, source SHA, known verification profiles, dependency DAG checks, GitHub atomic create-only attempt claim refs and fingerprint-based duplicate detection.

EF02-B: Bounded scheduler coordinates separate real EF01 subprocesses, each with its own Git worktree and evidence. Host process slots default to 2, adjustable by EF02_HOST_SLOTS. DAG fan-in composes pinned upstream candidate tree blobs, rejecting divergent edits to the same file rather than overwriting them.

EF02-C: GitHub REST candidate refs, per-action action_result.json, preserved EF01 logs, batch_result.json, GitHub Issue comment and workflow artifact upload even for failing executor jobs. Candidate delivery failure does not automatically rerun a claimed attempt.

Hosted validation and self-hosted execution are separate GitHub Actions jobs. Input is a pure JSON Issue body with title beginning [EF02]. HUMAN requires GitHub issue author = repository owner; BRAIN requires trusted GitHub issuer in EF02_BRAIN_ISSUERS. The request's actor string is not treated as authority. GitHub credentials are removed from child executor environments, and checkout does not persist Git credentials.

## Verification observed

GitHub CI: https://github.com/trung-via/ai-commerce/actions/runs/40648371883 — both ubuntu-latest and windows-latest jobs succeeded. Tests exercise real overlapping Python test subprocesses, DAG scheduling, fake GitHub claim races, candidate fan-in and conflict handling. These are source-level tests, not a remote live provider invocation.

## Outstanding live acceptance

Register a new Windows self-hosted runner labeled ai-commerce-ef02 and use the same Windows user session where Codex/AGY CLIs are authenticated. Submit an authorized [EF02] GitHub Issue with 2+ independent bounded actions and confirm actual live candidate refs, overlap, result comments and artifact feedback. Test quota/model errors, duplicates, crash/runner offline and uncertainty before declaring M0-R complete. Automatic ChatGPT wake/native continuation remains outside this cut.

**Status:** SOURCE_IMPLEMENTED / GITHUB_CI_TESTED. **M0-R:** OPEN. No AIOS-Renew runtime imported, no automatic merge to main.

## 2026-10-28: Windows Service Codex MXC scoped implementation

Evidence: GitHub live run 40654797946 demonstrated two overlapping processes (Codex and Antigravity) but Codex native legacy Windows sandbox failed on Session 0 with runner pipe-in timeout; Antigravity created a verified candidate. No-model diagnostic run 40656197567 showed legacy default fails with 0xC0000142, while the explicit `-c windows.sandbox=mxc` command succeeds (exit 0).

Implementation:
- EF01 `command_for` accepts an optional *explicit* Codex-only `codex_windows_sandbox="mxc"` that prepends the one-shot Codex setting; `--sandbox workspace-write`, provider model and effort remain pinned.
- EF01 CLI exposes `--codex-windows-sandbox mxc`; manual local EF01 defaults unchanged.
- EF02 Windows remote worker alone passes the override only when `os.name == "nt"` AND the chosen executor is `codex`. Linux and Antigravity remain unchanged; no global config mutation, no provider fallback.
- Engineering attempt and remote action result report the selected sandbox backend. CLI and remote unit tests assert exact flags and AGY invariance.
- This source change and tests do not by themselves establish a successful *live* Codex model run under MXC. M0-R stays OPEN pending fresh-attempt GitHub live verification.

## 2026-10-28: Headless Antigravity permission false-success diagnostic

Five-action DAG Issue #4 / run 40658774567 returned 2/5, with Codex A and C candidates and correct B-dependent D/E suppression. Antigravity B returned provider status SUCCESS / process exit 0 **without changing source**, because headless auto-denied a `read_file` tool; artifact 12803297819 documents the exact stderr and `denied_actions` list. No model-or-quota error was evidenced. Cycle-negative Issue #5 / run 40658957811 rejected `DEPENDENCY_CYCLE` before executor, with Windows job skipped.

A read-only Windows Service permissions probe run 40659944924 showed `settings.json` exists, `permissions.allow` already contains a `read_file` entry, no explicit `read_file` entry in ask/deny, but the failed tool's target path was not disclosed. No broad permission escalation or `--dangerously-skip-permissions` is justified by this evidence.

EF01 now parses AGY `denied_actions`, marking `EXECUTOR_PERMISSION_DENIED / HEADLESS_TOOL_PERMISSION_DENIED` even if process exit is zero or some files were changed. Preserves full raw stdout/stderr evidence. No authorization for automatic retry, model/effort substitution, or global AGY settings mutation is inferred. A fresh narrowly prompted action is needed for live AGY conformance.
