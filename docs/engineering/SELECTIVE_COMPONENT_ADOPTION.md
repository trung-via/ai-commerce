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
