# M0-R: isolated Windows runner service-loss rehearsal

This kit is PREPARED, NOT EXECUTED. The existing controlled EF02 to EF01 to Codex
interruption succeeded in Issue #47 / run #40715394122, but a genuine Windows
runner SERVICE loss and recovery has not been observed.

Never install or run this kit on AI-COMMERCE-EF02-01, AIOS-Renew or any host
with other GitHub Actions runner services. Use a separate Windows x64 VM or
machine, with separate disk, user/service identity and network. Do not reuse
runner root, _work, labels, provider credentials or production worktrees.

## Provisioning (dedicated VM)

1. Repository trung-via/ai-commerce -> Settings -> Actions -> Runners ->
   New self-hosted runner -> Windows x64. Run GitHub's displayed installation
   commands on the dedicated VM. Registration requires a short-lived token:
   never share it in ChatGPT, Issues, logs or pull requests.
2. Use EXACT runner root C:\TOOL\ai-commerce-m0r-isolated-runner,
   runner name AI-COMMERCE-M0R-ISOLATED-01, custom label
   ai-commerce-m0r-isolated (plus GitHub's default self-hosted/Windows/X64).
   Do NOT assign ai-commerce-ef02 or AIOS labels. Register as a Windows
   service from an admin terminal; verify the expected service name:
   actions.runner.trung-via-ai-commerce.AI-COMMERCE-M0R-ISOLATED-01
3. Confirm Windows service startup type is Automatic; no other
   actions.runner.* service exists on the VM. The guard fails closed if the
   .runner/.service identities, service executable path or any other
   local runner differ.
4. In an administrator PowerShell with a checkout of this repository run
   the following commands in this sequence (they do NOT stop the service):

       ./scripts/EF02-M0R-IsolatedRunner-Control.ps1 -Mode Inspect
       ./scripts/EF02-M0R-IsolatedRunner-Control.ps1 -Mode InstallWatchdog -Confirm:$false
       ./scripts/EF02-M0R-IsolatedRunner-Control.ps1 -Mode Watchdog
       ./scripts/EF02-M0R-IsolatedRunner-Control.ps1 -Mode Inspect

   Check that watchdog_installed is true. Test the scheduled task in Task
   Scheduler. The watchdog runs every minute as SYSTEM from a stable copy of
   the script inside the isolated runner root. It manages ONLY the exact
   isolated runner service; the task is external to GitHub Actions.
   A scheduled task cannot power on an offline VM: use separate VM startup
   settings if VM power-loss recovery is also desired.

## No-model outage exercise, only after real isolation is verified

5. GitHub Actions -> M0-R isolated Windows runner loss rehearsal (no model) ->
   Run workflow -> phase preflight. Confirm success on the dedicated runner.
   GitHub Settings -> Actions -> Runners must show it separately from the
   production AI-COMMERCE-EF02-01 runner (GitHub online status is external proof).
6. Run workflow again with phase hold. Observe M0R_ISOLATED_HOLD_ENTERED
   on the dedicated runner, and note the actual GitHub run ID. The hold does
   nothing except sleep 20 minutes; no EF01 or model invocation.
7. On the SEPARATE isolated VM, from an administrator terminal independent of
   the running Actions job, invoke ONLY after the hold step has started:

       ./scripts/EF02-M0R-IsolatedRunner-Control.ps1 -Mode ArmOutage -RunId ACTUAL_RUN_ID -AttestDedicatedVM -Acknowledgement 'STOP-ISOLATED-M0R-ONLY' -Confirm:$false

   The command checks the exact service, runner root, single-runner VM,
   installed watchdog, explicit attestation and run ID. It creates one marker
   containing restore_not_before_utc 12 minutes in the future and only then
   stops the isolated service. Scheduled Task restores this exact service
   after that timestamp. A second stop is blocked by the marker.
   If the watchdog cannot be confirmed, do NOT run ArmOutage.
8. Independently record from GitHub/another device:
   - Exact workflow run and hold job starting BEFORE the stop
   - Test runner Offline while production runner remains unaffected
   - Actual workflow terminal outcome; no invented timing or PASS
   - Watchdog logs, test service Running again and test runner Online/Idle
   - No provider or EF02 attempt dispatched; this test proves ONLY runner
     service loss/recovery, not exactly-once provider side effects.
9. Keep M0-R parent Issue #10 OPEN until those external observations exist.
   An absent candidate or silent runner never authorizes replay.

## Recovery and rollback

If service does not recover, after the 12-minute marker hold run the control
script with -Mode Watchdog as administrator on ONLY the isolated VM and inspect
the scheduled task. For retirement, disable the isolated watchdog scheduled
task and use the registered runner removal procedure. Do not touch the
production runner. The connected GitHub API here cannot confirm runner
inventory or operate Windows services, so outage acceptance is UNKNOWN.
