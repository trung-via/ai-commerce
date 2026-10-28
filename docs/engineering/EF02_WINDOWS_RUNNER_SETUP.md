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
