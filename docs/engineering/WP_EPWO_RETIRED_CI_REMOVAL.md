# WP/EPWO — retired by Human (2026-11-11)

This is a **source and CI-only** retirement record, not an active WP roadmap.

Human decision: entirely retire EPWO WP01–WP06 and remove it from AI COMMERCE; do not call any coding executor to carry out retirement. Existing GitHub Issue #466 closed as not_planned; WP02 PR #784 closed unmerged; #791 and #797 closed not_planned.

This first commit stops **new** WP ingress via both GitHub Issue event and EF02 workflow_dispatch. The active roadmap register no longer promotes WP milestones. Windows hosted CI must test the exact source HEAD before acceptance. This stage does **not yet remove WorkOwner runtime** inside src/ai_commerce/remote/worker.py, src/ai_commerce/remote/transport.py and EF01 owner_fence_client; complete runtime deletion is a separate CI-verified patch, not a claim made by this stage. Do not silently claim full uninstallation before independent proof.

The remaining migration must preserve working non-WP Engineering / EF01 / EF02, MT-01 owner/consumer tests, Brain PASS and BQ/WH/Production without bypassing provider permissions. WorkOwner history (especially #710, #794, #797) is immutable forensic evidence, not an executable replay queue. Do not delete history or mutate active external effects. No model invoked for this CI-first retirement PR.


## 2026-11-11 — Source retirement audit after merged PR #811

Observed `main@2d2aef011af7c323fe7a05ce136961c89ee6d74a`:
- Stage 3C1–3C3B2 merged (#807, #808, #809, #810, #811). WP #710 G3/G3B/G4 jobs and EF01 owner IPC are absent. WorkOwner's production writer methods are physically removed; legacy `read`, strict `_transition` and `reconcile` remain for historical immutable evidence.
- All 17 active workflow YAML files inspected: `issue-ingress.yml` / `ef02-batch.yml` deny WP Issue/dispatch identifiers, while a fixed WH03B #745 read-only forensic job and HostOps #698 read-only forensic jobs remain. Their preservation is not a WP execution authorization.
- GitHub Actions query at audit time reported zero `in_progress` and zero `queued` runs; this does **not** attest local Windows processes or private native/external effects.
- Gap identified: `remote.validation._validate_actions` lacked the corresponding Python pre-executor WP identifier rejection for direct CLI/worker paths. A CI-only follow-up adds `reject_retired_wp_identifiers` to live validation and strict worker admission, with negative tests. `strict_ac=False` in the `reconcile` command retains historical read-only access.
- GitHub-hosted Windows CI and exact source checks from #811 are independent of a real self-hosted Windows process inventory. Do **not** label complete live-system retirement, BQ/WH launch, PULSE external effects or a zero-process assertion without separately captured native receipts. No retired provider effect may be replayed.

Acceptance for this follow-up requires exact-HEAD Windows MT-01, genuine independent Brain review and verified trusted merge. Human-retired WP remains inactive even if the source PR is pending or rejected.
