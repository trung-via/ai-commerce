# AI Commerce — 3 Windows self-hosted runner instances

## Human-approved target (2026-11-04)

On one Windows 11 laptop, under the current **single Windows user**:

| Instance | Registration label | Role | Existing? |
|---|---|---|---|
| AI-COMMERCE-EF02-01 | ai-commerce-ef02 | EF02 engineering | Yes; preserve its existing interactive task |
| AI-COMMERCE-EF02-02 | ai-commerce-ef02 | EF02 engineering | New |
| AI-COMMERCE-HOSTOPS-01 | ai-commerce-hostops | Personal HostOps and later CDP diagnostics | New |

GitHub schedules at most one job per registered runner instance; these are
three separate work folders, registrations, listeners and interactive
scheduled tasks **on the same laptop**. No new Windows account, machine,
broker repository, service identity, or model is provisioned.

**Resource decision:** Human explicitly chose **OBSERVE FIRST**. Do not
set CPU/RAM quotas, pytest-worker reductions, or resource throttling in this
phase. A second EF02 job may run while another EF02 job is active; keep the
per-Issue concurrency/idempotency protection unchanged. Two simultaneous
12-worker pytest runs may contend for physical resources; observe rather
than prematurely claim throughput improvements. To prevent an actual
shared-state conflict (not a resource cap), each EF02 job now installs
Python dependencies into a separate venv under its own RUNNER_TEMP, with
a run-ID/attempt-specific folder, rather than simultaneous pip mutation
of the same Windows user Python site-packages.

## Staged change without downtime

**Phase A (this source change):** Add a single fixed, local-attended
installation script and offline registered tests. Do **not** switch
`.github/workflows/issue-ingress.yml` yet; HostOps continues using the
proven `ai-commerce-ef02` runner until HOSTOPS-01 is independently Online.
No arbitrary HostOps shell operation or broad permission is added.

**Phase B (local Human operator, once):** From an **interactive elevated
PowerShell window under the same original Windows account**, using a
checkout that includes this merged source:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\AI-Commerce-Three-Runners.ps1 -Mode Plan
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\AI-Commerce-Three-Runners.ps1 -Mode Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\AI-Commerce-Three-Runners.ps1 -Mode Status
```

- Existing `AI-COMMERCE-EF02-01` installation and interactive scheduled
  task are checked by SID, **never stopped, overwritten or re-registered**.
- Installer checks that both new runner names, folders and scheduled tasks
  do **not already exist**. It rejects unknown/partial state instead of
  overwriting or replaying registration.
- Local `gh` must already authenticate the repository owner with **repo
  Administration write permission**. The script obtains a new one-hour
  registration token for each instance via `gh api`. Do not paste these
  tokens into ChatGPT, issues, PRs, workflow logs or shell transcripts.
- The runner ZIP is downloaded from the official GitHub `actions/runner`
  latest Windows x64 release and must match its published asset SHA256.
  If the release digest is missing, the script **stops** instead of
  bypassing verification.
- Each new `config.cmd` sets the fixed runner name and custom routing
  label, and creates its own `_work` under a distinct root.
- Two interactive, hidden Task Scheduler instances are installed at
  logon, with the existing user SID and limited run level. No duplicate
  Windows service is installed; user sign-out/sleep behavior remains.
- On a partial installation failure, stop and reconcile local task,
  directory and GitHub registration **before any retry**. Do not
  delete credentials or run `config.cmd --replace`.

**Phase C (after live evidence):** Verify
`AI-COMMERCE-EF02-02` and `AI-COMMERCE-HOSTOPS-01` both
show `online` in GitHub Settings → Actions → Runners and in local
`-Mode Status`. Then submit a **separate reviewed PR** changing only the
Personal HostOps job from:

```yaml
runs-on: [self-hosted, Windows, ai-commerce-ef02]
```

to:

```yaml
runs-on: [self-hosted, Windows, ai-commerce-hostops]
```

Keep the existing `personal-hostops-workspace` concurrency for
WRITE non-overlap, the exact Human `APPROVE WRITE #N` gate,
and all fixed Personal operation allowlists. Keep EF02's
`ai-commerce-ef02` label so GitHub can select either EF02 runner.
This stage is explicitly **NOT YET EXECUTED** by Phase A.

**Phase D (independent live acceptance):** Send one harmless Personal
`inventory` READ and verify its job lands on
`AI-COMMERCE-HOSTOPS-01`; separately run an authorized *no-model*
EF02 Windows preflight to observe `AI-COMMERCE-EF02-02`, or record
the lack of evidence if the scheduler picks the original runner.
Only claim true 3-way concurrency after witnessing all three runner
IDs executing distinct independent jobs. Do not dispatch an EF02 paid
model task or Chrome Wake merely to test runner registration.

## Evidence/status and reversibility

Repository source PR and Windows-hosted CI prove the installer *source*,
not successful laptop runner registration. Live installation requires
the authenticated, logged-in owner to execute the local script; existing
ChatGPT GitHub connector and bounded HostOps Personal operations cannot
request registration tokens or run arbitrary PowerShell.

Use `-Mode Status` to check task registration and GitHub Online. If
a new runner fails to launch, retain the current HostOps route and EF02-01
configuration. Do not remove historical runner registrations or disable
the old task automatically. A later rollback can remove each new task
and runner independently with operator authorization and reconciliation.
Older HostOps v1/v2/v3 routes remain retired (PR #343).
