# BQ-04b R11 source handoff

Action `BQ04B-R11-CODEX-XHIGH-NATIVE-LINK-AC-01`; attempt `bq04b_r11_codex_xhigh_link_proof_20261107_v1`; source `426ef49748ca27ea8d4216743c58b9b48049e6d2`; requested executor `codex/gpt-6.1-sol/xhigh`, no fallback. Initial executor checks found the exact source root, unchanged HEAD, clean worktree, seven absent ADD paths and six existing possible MODIFY paths. This change authors the seven ADDs and modifies only repair_policy and repair_preservation. EF02 alone owns candidate publication.

## Historical evidence and provenance

[Independent Brain HOLD #598](https://github.com/trung-via/ai-commerce/issues/598#issuecomment-6704453503) records R10 Windows MT01: A13 197, BQ02a 282, BQ02b 249, BQ03b 298, BQ04a 152 and BQ04b 74 PASS + 1 SKIP. The skipped native `test_current_link_alias_reinspection_hold[symlink]` was WinError 1314; native junction/hardlink and the direct metadata-helper negative passed. This is historical evidence, not an R11 result or full AC proof.

R10 run 42352888975 / artifact 13567092030 has declared ZIP SHA256 `9ce460d3d1ef85205a8b2155300af590c6679a54b16f3f1135d1671d79faec94`. The actual GitHub execute-job log independently records upload finalization of that digest. The artifact connector returned a ZIP reference; materializing its bytes into this executor failed DNS. Therefore archive and individual archived-file hashes could not be verified here. Local R10 worktree source/tests were read as untrusted advisory only. No archived or quarantined file was copied, promoted, committed or used as a candidate. R11 is freshly authored from pinned main and the source/test contracts. R8b assertion failures, R9 provider UNKNOWN and R10 HOLD remain immutable.

## Native-link diagnosis and narrowed proof

R10's source rejected symlink mode or `FILE_ATTRIBUTE_REPARSE_POINT` in one branch. A native junction can exercise that branch without symlink privilege, but the previous junction test asserted only a HOLD result. The direct metadata test did not call the complete verifier or assert absence of media reads. R11 makes those obligations executable:

- `_reject_metadata` rejects symlink mode, the reparse attribute, **any nonzero reparse tag**, and file link multiplicity before media is opened. Unknown/inconsistent tags also fail closed.
- `_paths_locked` validates all paths before opening any media, then locks all ancestors and files, revalidates them and checks them again on exit. Directory handles are opened with OPEN_REPARSE_POINT and queried for reparse tags.
- Mandatory real hardlink/junction cases call `verify_technical_evidence`, assert `LINK_MULTIPLICITY`/`LINK_OR_REPARSE`, and install tripwires on hash, snapshot and media/tool entrypoints. Both source and saved output bytes must remain identical. The junction must expose native mount-point tag `0xA0000003`.
- Metadata cases inject symlink tag `0xA000000C`, unknown tags, reparse-attribute-only and symlink-mode-only states into source, output and ancestor paths. They run the real verifier and assert the same rejection branch before reads. An ordinary-metadata control must actually remeasure successful BEFORE/AFTER media.
- Mandatory link capability failures are `NATIVE_LINK_ENVIRONMENT_HOLD`, with zero skips/xfails. The optional `optional_native_symlink_probe` function is not a mandatory pytest case and cannot establish guard acceptance.

**SYMLINK_NATIVE_EFFECT_UNPROVEN** remains explicit. Neither a metadata negative nor a native junction proves OS symlink creation/traversal. Acceptance is narrowed to the observed common rejection gate and real native junction/hardlink rejection. Independent Brain must assess that scope. If exact native symlink effects remain mandatory, HOLD that AC and obtain separate Human/HostOps authorization; do not alter OS privilege from this action.

Executor preflight ran before media tests: hardlink WinError 5, junction Access denied, symlink WinError 1314; original probe bytes unchanged. This sandbox capability result does not contradict R10's independent host junction/hardlink success. R11 native host acceptance remains pending; there is no executor candidate.

## Verification contract

Use registered `mt01-component-onboarding`; the owner/consumer selector must choose A13, BQ02a, BQ02b, BQ03b, BQ04a and BQ04b without fallback or missing mappings. The 1178 historical consumer cases are unchanged. R11 retains the previously green BQ04b obligations and strengthens native link/metadata coverage; the old privileged symlink construction case is explicitly replaced by mandatory common-gate proof plus a separate optional probe, not relabeled as an observed native symlink.

All pytest basetemp, cache, bytecode, JUnit and media output belong outside the source checkout. Native R11 self-host worker count, collected/executed totals, skips/errors and exit status must come from EF01. Executor tests are advisory and cannot substitute for that independent receipt. At most two bounded code-error Verify–Fix cycles are allowed; environment denials do not justify code weakening. No `.github`, classifier, EF01, security policy, HostOps or private settings changes are permitted.

## Observed executor result: HOLD

The final BQ04b collection contains 103 cases. Across the explicitly separate local slices, 101 passed and two mandatory native-alias cases failed `NATIVE_LINK_ENVIRONMENT_HOLD` (hardlink WinError 5; junction Access denied). Zero skipped/xfail cases and zero remaining code assertion failures were observed. The 101 passing cases include actual generated BEFORE, deterministic edit, existing-output Q4 AFTER, all-frame/non-target/PTS evidence, metadata negatives through the verifier, positive ordinary metadata, current-byte mutations and final-lock-failure rejection. This is not a 103-pass result. The optional native symlink probe returned `SYMLINK_NATIVE_EFFECT_UNPROVEN`, WinError 1314.

The registered onboarding test file passed 60 cases. The selector assertion passed with exactly A13/BQ02a/BQ02b/BQ03b/BQ04a/BQ04b and no full-project fallback. No R11 independent host consumer execution or host 12-worker count is claimed. The 1178 consumer passes remain R10 historical evidence; those consumer test sources were not modified.

Two bounded code-error correction cycles were used: preserve the specific UnsafePathError code instead of catching it as ValueError, then isolate each fixture's genuine tool trace so serial tests cannot count another fixture's edit. The latter cycle also rejects linked scratch ancestors before temporary-directory creation. Final advisory verification was 101 PASS / 2 native cases deselected in that slice; the two native cases were separately executed and failed for the capability causes above. The JUnit record_property compatibility warnings are warnings, not skipped proof.

The final live GitHub main read returned `da70e0cbf03991bff39f527ac2146d12fcc17cf2` ([merge #605](https://github.com/trung-via/ai-commerce/commit/da70e0cbf03991bff39f527ac2146d12fcc17cf2)), while the isolated worktree stayed at the required `426ef49748ca27ea8d4216743c58b9b48049e6d2`. Therefore the source-main pin is stale. R11 remains **HOLD_SOURCE_STALE_AND_NATIVE_LINK_PROOF_PENDING**. No base adjustment, candidate, PR or issue write was performed. A fresh source authorization and independent host proof are required before publication can be considered; an exact OS symlink AC also remains HOLD if Brain requires it.

After scoped source and independent MT01 proof: EF02 may publish its candidate, exact-head hosted Windows PR CI must pass, and independent Brain acceptance is required before trusted merge. No issue/PR mutation or Git writes are part of this executor handoff.
