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
