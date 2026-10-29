# EF02 — Independent Windows runner setup

1. In repository trung-via/ai-commerce open Settings > Actions > Runners > New self-hosted runner > Windows x64. Follow GitHub-generated registration commands in a new directory such as C:\TOOL\ai-commerce-runner; never reuse the AIOS-Renew runner. Do not paste the temporary registration token into ChatGPT or Issues.
2. Add the custom label ai-commerce-ef02 during config.cmd; retain automatic self-hosted and Windows labels. Start run.cmd using the Windows login whose Codex and agy CLIs already work. A Windows service under another account might not inherit CLI logins.
3. Confirm runner Online, Python 3.11+, git, codex --version and agy --help from that same account.
4. Optional repository Actions variable EF02_HOST_SLOTS=2 (host slot cap, adjustable). For an explicitly trusted Brain GitHub bot add its real login to the repository variable EF02_BRAIN_ISSUERS. A JSON actor label alone never authorizes execution.
5. Create a GitHub Issue titled [EF02] followed by a task name. Use a pure JSON body following contracts/RemoteActionBatch.v1.schema.json, with source_sha exact current commit, per-action executor/model/effort, unique attempt IDs, allowed_paths, depends_on and verification_profile from .github/ef02/verify-profiles.json.
6. The issue event triggers .github/workflows/ef02-batch.yml on main. Alternatively go to Actions > EF02 Multi-Action remote engineering > Run workflow and supply issue_number. Hosted validation checks actual issuer first, then Windows executes.
7. Inspect issue feedback and the workflow artifact ef02-results-<run_id>-<attempt>. Missing runner, no result, claim without receipt or upload errors are not PASS. Do not retry the same claimed attempt; reconcile first.

Initial one-runner setup accepts multiple actions in one batch and runs independent actions in overlapping child processes, default two. Independent batches may queue. No automatic main merge, external media publishing, provider fallback or proactive ChatGPT wake.

## 2026-10-28 — Service installed on user's Windows machine

Observed from Human PowerShell screenshot: runner name `AI-COMMERCE-EF02-01` registered with label `ai-commerce-ef02`; service `actions.runner.trung-via-ai-commerce.AI-COMMERCE-EF02-01` installed, `State=Running`, `StartMode=Auto`, `StartName=.\\trung`. This does not independently establish GitHub Online, executor authentication or live remote batch execution.

### Zero-provider-cost runner preflight

Use Actions → **EF02 Windows Service preflight** → **Run workflow** on branch `main`. The job targets `[self-hosted, Windows, X64, ai-commerce-ef02]` and inspects the running service account's PATH and versions of Python, Git, PowerShell 7, Codex and presence of AGY. It does **not** invoke Codex/Antigravity models, register a batch or consume provider quota. Success shows `EF02_SERVICE_PREFLIGHT_PASS_NO_MODEL_CALL`; queued jobs mean the runner still needs connectivity or correct labels. Provider authentication and permissions remain separate live checks.


## 2026-10-29 — Human-adopted interactive runner for Codex Windows MXC

**Why:** GitHub Actions run 40815643432 in Windows service Session 0: direct MSI PowerShell and MXC CMD passed, but MXC MSI PowerShell and MXC pwsh both exited -1073741502 (0xC0000142). Run 40817245103 in interactive Windows login Session 1: the same MXC probes passed with exit 0. This A/B evidence favors interactive runner operation; it does NOT prove a complete Codex EF01-R Verify/Repair outcome.

**Operating decision (Human 2026-10-29):** AI Commerce runner registration AI-COMMERCE-EF02-01 runs through Windows Task Scheduler **only when the existing TRUMP-LAPTOP\TRUNG user is signed in**, instead of a Windows Service. AIOS-Renew, Python Complete Agent and Torrent TV services are out of scope. Preserve windows.sandbox=mxc; do not switch to danger-full-access or modify system security. The old AI Commerce Windows service remains installed but disabled while the interactive task is active. This intentionally sacrifices unattended execution before login / after logout.

**Installer and rollback:** See scripts/EF02-Interactive-Runner-Task.ps1, run in local Administrator PowerShell under the same account that owns AI Commerce runner. The installer creates interactive logon task AI-Commerce-EF02-Interactive with RunLevel Limited, no stored password, laptop battery-safe/long-lived options, one instance only and run.cmd in C:\TOOL\ai-commerce-runner. It records original service start mode/state in .ef02-interactive-runner-backup.json inside runner root, registers task first, stops/disables ONLY the AI Commerce service, starts task, confirms it stays running and restores the original service on installation error. It refuses mismatched accounts, missing run.cmd or an existing task.

Commands from the local folder containing the downloaded script:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\EF02-Interactive-Runner-Task.ps1 -Mode Install
    powershell -NoProfile -ExecutionPolicy Bypass -File .\EF02-Interactive-Runner-Task.ps1 -Mode Status
    powershell -NoProfile -ExecutionPolicy Bypass -File .\EF02-Interactive-Runner-Task.ps1 -Mode Rollback

Do not run Task Scheduler and Windows Service with the same runner registration simultaneously. The other three runners are untouched. The scheduled task starts only after user login, stops on logout and does not prevent laptop sleep.

**Acceptance (after Human locally installs):** scheduled task state Running; AI Commerce Windows service Stopped and Disabled; GitHub runner Online; new EF02 Codex MSI Shell Probe (No Model) workflow run shows SESSION_ID > 0, direct_msi, mxc_cmd, mxc_msi and mxc_pwsh_alias all exit 0 with expected markers. Only afterward create a new unique Codex gpt-6.1-sol/max live conformance to check Verify FAIL -> Repair -> Verify PASS. Never reuse earlier claims or merge a probe candidate.
