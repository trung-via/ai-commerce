# Selective external component adoption — A01 preparation

No AIOS-Renew/Python Agent source component has been copied into A01. This file provides the documentation shape for EF01 implementation; it is not an approval to import future components without scope.

For each actual component integration record:

| Field | Required note |
|---|---|
| component | Selected function/class/module, original and new path |
| source | Repository URL, exact commit SHA and Git blob SHA |
| human_decision | Human scope approval and rationale |
| rights/license | Actual rights/license evidence; UNKNOWN must remain UNKNOWN |
| dependencies | Internal/third-party imports; removed foreign lifecycle dependencies |
| compatibility | Python/OS/CLI version and focused tested boundaries |
| credentials | Independent AI Commerce credentials; no shared token/cookie/profile |
| owner | Maintenance responsibility and update strategy |
| rollback | Revert/migration procedure without changing AIOS-Renew |

Reference source snapshot for *future analysis only*: `trung-via/AIOS-renew@cbefb92612f24b55417165736c63753fc560d403`.

No default downstream sync, shared worker or inherited verification is authorized.

## EF01 actual selective adaptation — 2026-10-28

EF01 selectively reimplements explicit model/effort profile validation, Codex/AGY CLI command construction and trusted usage observation from the Human-selected pinned AIOS reference snapshot. **No AIOS modules imported**. Per-file original blob SHAs, adaptation differences, licensing uncertainty, rollback owner and Windows CLI uncertainty are documented in [`EF01_IMPLEMENTATION_REPORT.md`](EF01_IMPLEMENTATION_REPORT.md). No imported AIOS task/Kernel/Publisher, credentials, workflow or sync.
