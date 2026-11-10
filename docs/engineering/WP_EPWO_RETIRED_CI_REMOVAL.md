# WP/EPWO — retired by Human (2026-11-11)

This is a **source and CI-only** retirement record, not an active WP roadmap.

Human decision: entirely retire EPWO WP01–WP06 and remove it from AI COMMERCE; do not call any coding executor to carry out retirement. Existing GitHub Issue #466 closed as not_planned; WP02 PR #784 closed unmerged; #791 and #797 closed not_planned.

This first commit stops **new** WP ingress via both GitHub Issue event and EF02 workflow_dispatch. The active roadmap register no longer promotes WP milestones. Windows hosted CI must test the exact source HEAD before acceptance. This stage does **not yet remove WorkOwner runtime** inside src/ai_commerce/remote/worker.py, src/ai_commerce/remote/transport.py and EF01 owner_fence_client; complete runtime deletion is a separate CI-verified patch, not a claim made by this stage. Do not silently claim full uninstallation before independent proof.

The remaining migration must preserve working non-WP Engineering / EF01 / EF02, MT-01 owner/consumer tests, Brain PASS and BQ/WH/Production without bypassing provider permissions. WorkOwner history (especially #710, #794, #797) is immutable forensic evidence, not an executable replay queue. Do not delete history or mutate active external effects. No model invoked for this CI-first retirement PR.
