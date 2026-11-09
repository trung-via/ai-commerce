# BQ-06b #698 — R0.2 fixed forensic read (source-only)

## Authority and immutable attempt
Human: 2026-11-09, "@AI Commerce ok tiếp tục" after R0.1 native #720 and unresolved WorkOwner PROVIDER_INTENT. Original R2 issue #698, source `b9abe11c31954706caa816d9c53ca6c0519e913e`, action `BQ06B-R2-AGY38FLASHHIGH-A09-LINEAGE-SUPERSESSION-ROOTFIX-01`, attempt `bq06b_r2_agy38flashhigh_legit_correction_reprocess_main_b9abe11c_20261109`. No new executor, provider, spend, publish, journal, Git worktree mutation or WorkOwner transfer authorized.

## Previous findings
- EF02 original run #42572795120: parent ended without authenticated child terminal and after WorkOwner provider intent.
- Native #720 (R0.1, merged PR #719): PID absent, one isolated old worktree, source bytes unverified, no terminal at corrected EF02 output root. Current result is **HOLD_UNKNOWN_EFFECT**; a missing terminal does not establish no provider effect.

## R0.2 exact read-only contract
This cut is owned by HostOps, not by BQ-06b implementer. After exact-HEAD Windows CI and independent Brain PASS + trusted merge, one authenticated GitHub repository-owner event opens an Issue with **exact title**:
`[HOSTOPS EF02 FORENSIC] #698 R0.2 read-only`

Its body must match one byte-for-byte UTF-8 JSON object:
`{"schema":"ai-commerce.hostops.ef02-698-r02.v1","operation":"EF02_698_FORENSIC_READONLY_R02","issue_number":698,"parameters":{}}`

The workflow pins the source, issue, action, attempt, allowed paths, and Windows `ai-commerce-hostops` runner. It rejects other event bodies/titles/actors. It executes only the fixed `ef02_attempt_r02_readonly` observer and posts closed/sanitized values. No user-supplied PID/path/shell command.

- **EF02 root:** distinguish batch absent, action absent, action present, unsafe and unreadable; inspect only fixed `provider_boundary.json`, `provider_return_observed.json`, and `engineering_result.json` with exact attempt/work ID checks. Presence is **unverified local evidence**, never terminal authority.
- **Source scope:** only the original six R2 allowed files (five BQ06b additions and Roadmap B appendix). Read no source code outside these exact paths. Bounded file sizes, checked metadata before/after open, deny reparse/hardlink/alias. Compare the Roadmap B appendix to the exact original source SHA via bounded `git show` local-only; five newly added source paths remain unverified for authorship. Public outputs include aggregate seen/missing/roadmap-changed counts plus SHA256 aggregate of six fixed source file digests, not raw content.
- **WorkOwner:** workflow re-reads matching immutable GitHub branch ref stage with bound issue/action/generation. This is not the private Windows journal, not a verified provider outcome and not an authenticated transfer proof.
- **Public release:** exact schema and enum values, counts bounded 0..6, optional 64-byte hex digest, `HOLD_UNKNOWN_EFFECT`, `provider_outcome_proven=false`, `replay_authorized=false`, `write_attempted=false`, `next_action=NO_R3_DISPATCH`; any invalid/missing result emits a HOLD-only fallback.
- **Excluded:** private accounts, Antigravity cache/session history, model billing, prompts/log raw bytes, provider API/network calls, arbitrary disk paths, native process lifecycle controls, journal edits, worktree cleanup and re-execution.

## Decision tree after native receipt
- **Evidence folder missing, source partial/unsafe:** preserve R2 HOLD. No salvage without a new independent source reconstruction and distinct owner approval.
- **All six files visible with aggregate digest:** this is a *forensic observation only*. An independently reviewed custody/bytes-to-source proof is still required before any source-only salvage. Source file presence does NOT prove Gemini authored them or that they pass MT-01.
- **Local return/boundary JSON present:** status is `PRESENT_UNVERIFIED`; independent WorkOwner/provider authentication and terminal reconciliation remain necessary.
- **Any absent terminal / unknown provider:** original WorkOwner remains PROVIDER_INTENT/UNKNOWN; no automatic release, transfer or R3.

A subsequent WorkOwner recovery/change would require a new exact protocol and explicit Human approval. BQ-06b semantic AC (legitimate append-only A09 CORRECTION and REPROCESS descendants across full bounded history, A11 pin freshness, negative evidence) and real BQ-06b Windows MT-01 remain outstanding.
