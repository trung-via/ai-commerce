# HostOps v1 — bounded GitHub-to-Windows read-only PowerShell

**Status: PR candidate; NOT deployed, merged, or live tested on laptop.**

## Scope
This is a separate diagnostic path, not an EF02 execution batch or a ChatGPT Wake.
After separately authorized merge, an owner-created `[HOSTOPS] ` GitHub Issue
with an exact JSON body triggers hosted validation and only then a Session 1
Windows self-hosted runner. The runner executes one fixed repository-owned
`HostOps-Diagnostics.ps1` script; it does not run arbitrary PowerShell from an Issue.

## Activation and boundaries
The Issue opening event must show a private repository and repository owner as
the authenticated Issue author. Hosted validation rechecks live repository
privacy, strictly parses the immutable Issue event body and pins the SHA.
Windows execution independently rechecks live privacy and request identity.
Do not merge without separate Human authorization. Do not expose untrusted
pull-request code to the runner or expand this allowlist without review.

## Example Issue JSON (replace source_sha with the exact current main SHA)
```json
{
  "schema": "ai-commerce-hostops-v1",
  "request_id": "hostops-20261102-001",
  "repository": "trung-via/ai-commerce",
  "source_sha": "7ae8893724b83ea6342fc5d041ee938590fb9cd4",
  "operation": "d5_readonly",
  "parameters": {}
}
```

Allowed operations: `runner_readonly` gathers Session ID, PowerShell version,
runner and Chrome process counts. `d5_readonly` also checks the OS TCP listen
table for `127.0.0.1:9333`; no browser attachment, navigation, UI access,
clipboard, Wake Send, private state, key, cookie, registry change or process
restart is permitted. `runner_readonly` intentionally sets the CDP field to
false without testing that port; interpret it only for `d5_readonly`.

## Evidence
Workflow stages: validate -> execute -> report. The run uploads one
`hostops-request-<run_id>` and `hostops-result-<run_id>` artifact (7 days).
An Ubuntu report job posts a sanitized `HOSTOPS_RESULT_V1` comment to the
original private Issue. Raw PowerShell stderr/stdout, process command lines,
usernames, browser data and secret environment values are not reported.
If the runner or artifact upload fails, a result comment may not exist.
Read-only diagnostics do not establish a ChatGPT host receipt, cross-plugin
invocation, autonomous Wake, or a successful D5 end-to-end test.
Never replay older D5 UNKNOWN events or use this route to merge/dispatch EF02.

## Tests / rollback
Run `python -m unittest discover -s tests -p test_hostops_request.py -v`.
To roll back, disable the dedicated workflow or revert this scoped PR with
separate Human authorization. No Windows service, Task Scheduler task,
secret store, or browser configuration is modified.
