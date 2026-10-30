# MT01 component-aware minimum verification

Baseline: `main` at `6601881e7b498ae139fbe8a8f04c195905b15ed3`. Local implementation on the isolated issue worktree, 2026-10-31. Human policy, Component-Level Scope + Verify-First, EF01-P/MT-01/EF01-R/EF02 boundaries remain in force. This is a worktree patch for EF01 verification and EF02 publication; it is not a published candidate or registry activation.

## Selection and evidence

`src/ai_commerce/engineering/minimum_test.py::approved_groups` is the finite, trusted map of exact source, Python test, contract, fixture and consumer paths. Its filenames are literals; runtime globbing does not approve new files. The regression suite checks every declared path exists, all current top-level Python tests are mapped exactly once, and A02-A12 plus engineering, recovery, diagnostics, Git integrity and wake families are present. Direct imports, reverse consumers and explicit non-Python contract edges expand the selection. For example, a ProductClaimEvidence schema edit reaches product registry and CLI consumers. Product source changes can select several families because the common CLI imports those components. A declaration-only `tests/test_a12_claims.py` edit selects a12, and `src/ai_commerce/remote/chrome_acquisition.py` selects ef02-wake with matching staged registrations.

Unknown paths or ACs, unresolved dynamic imports, parse failures, uncovered consumers, missing test definitions and missing registrations expand to the exact approved broad argv, or remain UNPROVEN when that argv cannot establish scope. Shared pytest/dependency configuration forces broad verification. Fan-in always needs fresh combined-source verification and cannot reuse sibling evidence. No executor-supplied command is accepted. Matching requires the complete fixed test list, order, `-q`, Python/py pytest prefix and action parallelism suffix. An explicit serial action cannot acquire a parallel companion, and a parallel action cannot acquire a serial group.

Plan/result diagnostics retain changed and affected paths, required and unknown AC IDs, the selected groups' source/test/consumer/contract closure, reason codes, selected profile names and fallback profile/argv. Results add actual argv, observed summary counts, requested/observed workers, UTC spans, selection and total wall time, immutable artifact references and EXECUTE/REUSE/INVALIDATED/UNPROVEN states. Reuse reports zero newly executed tests and retains prior proof references and counts. `narrow_execution_observed` becomes true only after an actual nonempty successful selected execution; no runtime saving is inferred from a plan, reuse or full-suite fallback.

EF01-R retains unaffected immutable successes, reexecutes a proof whose binding changes, and preserves completed successful evidence when a later group times out. Mapping, source/test/import/fixture/contract bytes, base, argv, registry identity, environment, executable and artifact hashes remain bound. All verification groups and repairs share the action deadline; selection also consumes the verification budget. Infrastructure/no-tests/timeout paths retain UNPROVEN receipts and cannot authorize blind provider retry. Partial timeout output preserves any observed worker count; an observed count other than twelve on a twelve-worker action is UNPROVEN even with a success-shaped pytest summary. Eligible parallel profiles keep twelve workers per action. Brain-selected EF02 max_parallel and the scheduler are unchanged; host slots do not divide worker counts.

## Acceptance propagation within immutable RemoteActionBatch v1

The schema has no acceptance field and still rejects extra fields, including acceptance_ids and arbitrary verify_argv. The bounded compatible carrier is one standalone line inside the existing authorized action instruction, for example:

```text
MT01_ACCEPTANCE_JSON: ["A12.product-evidence", "EF01.repair"]
```

The instruction already participates in the existing claim fingerprint and issuer authorization. The worker parses this line before starting the executor, records its source and reviewed ID-to-test map, and passes repeatable `--acceptance` arguments to EF01. It never reads acceptance metadata from executor output, changed files, arbitrary JSON commands or guessed prose. Normal source checks, allowed_paths, fingerprint calculation and issuer validation are unchanged. Applicable changed-path consumers are added even if the declaration omits them.

These named IDs are reviewed test-family coverage IDs, not invented equivalents of arbitrary numbered issue ACs. The issuer must include every applicable ID it can establish. Missing, empty, duplicate or malformed declarations become `MT01.ACCEPTANCE_UNDECLARED`; an unknown declared ID stays unknown. Both force broad fallback/UNPROVEN. Thus old issues with no explicit declaration remain conservative after companion registration. Automatic interpretation of free-text issue ACs cannot safely be implemented under v1; this explicit fingerprinted declaration is the bounded alternative. Unknown requirements are never guessed into a narrow PASS.

## Proposed protected registry companion PR

The current protected `.github/ef02/verify-profiles.json` still contains only project-tests, ef02-tests, foundation-tests and project-tests-serial. It is unchanged by this patch. For covered root-tests scope, missing companions use exactly `python -m pytest tests -q -n 12` for an existing parallel project-tests action. Narrow activation requires a separate authorized GitHub PR integrating the following additional entries into that registry, then dispatch through the normal verified-source path. These entries are proposals, not active registrations. Existing foundation-tests and ef02-tests already match their respective groups and require no duplicate entries.

| Group | Reviewed coverage ID | Parallel profile |
| --- | --- | --- |
| a02 | `A02.contracts-and-provider` | `mt01-a02-tests` (proposed) |
| a03 | `A03.creative` | `mt01-a03-tests` (proposed) |
| a04 | `A04.media-and-operations` | `mt01-a04-tests` (proposed) |
| a05 | `A05.composition-and-render` | `mt01-a05-tests` (proposed) |
| a06 | `A06.e01` | `mt01-a06-tests` (proposed) |
| a07 | `A07.capability` | `mt01-a07-tests` (proposed) |
| a08 | `A08.distribution` | `mt01-a08-tests` (proposed) |
| a09 | `A09.analytics` | `mt01-a09-tests` (proposed) |
| a10 | `A10.opportunity` | `mt01-a10-tests` (proposed) |
| a10-m02 | `A10.trends` | `mt01-a10-m02-tests` (proposed) |
| a11 | `A11.learning` | `mt01-a11-tests` (proposed) |
| a12 | `A12.product-evidence` | `mt01-a12-tests` (proposed) |
| foundation | `A01.foundation` | `foundation-tests` (existing) |
| cli | `A01.cli-and-isolation` | `mt01-cli-tests` (proposed) |
| ef01 | `EF01.execution` | `mt01-ef01-tests` (proposed) |
| repair | `EF01.repair` | `mt01-repair-tests` (proposed) |
| integrity | `EF01.source-integrity` | `mt01-integrity-tests` (proposed) |
| provider | `EF01.provider-boundary` | `mt01-provider-tests` (proposed) |
| ef02 | `EF02.action-and-fanin` | `ef02-tests` (existing) |
| mt01 | `MT01.selection-and-evidence` | `mt01-mt01-tests` (proposed) |
| ef01p | `EF01P.git-and-host-budget` | `mt01-ef01p-tests` (proposed) |
| diagnostics | `EF01.provider-diagnostics` | `mt01-diagnostics-tests` (proposed) |
| git-integrity | `EF01.git-integrity` | `mt01-git-integrity-tests` (proposed) |
| ef02-recovery | `EF02.recovery` | `mt01-ef02-recovery-tests` (proposed) |
| ef02-wake | `EF02.wake-boundaries` | `mt01-ef02-wake-tests` (proposed) |

The following JSON object contains the exact additional names/argv; every listed test exists on the pinned tree.

```json
{
  "mt01-a02-tests": ["python", "-m", "pytest", "tests/test_a02_contracts.py", "tests/test_a02_mock_provider.py", "-q", "-n", "12"],
  "mt01-a03-tests": ["python", "-m", "pytest", "tests/test_a03_creative.py", "tests/test_a03_upgrade_v11.py", "-q", "-n", "12"],
  "mt01-a04-tests": ["python", "-m", "pytest", "tests/test_a04_media_operations.py", "tests/test_a04_upgrade_v11.py", "tests/test_a03_a04_integration.py", "-q", "-n", "12"],
  "mt01-a05-tests": ["python", "-m", "pytest", "tests/test_a05_composition.py", "tests/test_a05_render.py", "tests/test_a05_integration.py", "tests/test_creative_asset_link_v11.py", "-q", "-n", "12"],
  "mt01-a06-tests": ["python", "-m", "pytest", "tests/test_a06_e01.py", "-q", "-n", "12"],
  "mt01-a07-tests": ["python", "-m", "pytest", "tests/test_a07_capability.py", "-q", "-n", "12"],
  "mt01-a08-tests": ["python", "-m", "pytest", "tests/test_a08_distribution.py", "-q", "-n", "12"],
  "mt01-a09-tests": ["python", "-m", "pytest", "tests/test_a09_analytics.py", "tests/test_a09_e02.py", "-q", "-n", "12"],
  "mt01-a10-tests": ["python", "-m", "pytest", "tests/test_a10_m01.py", "tests/test_a10_m03.py", "tests/test_a10_m04.py", "tests/test_a10_m05.py", "-q", "-n", "12"],
  "mt01-a10-m02-tests": ["python", "-m", "pytest", "src/ai_commerce/opportunity/trends/tests/test_m02.py", "-q", "-n", "12"],
  "mt01-a11-tests": ["python", "-m", "pytest", "tests/test_a11_reader.py", "tests/test_a11_registry.py", "tests/test_a11_reporting.py", "tests/test_a11_integration.py", "-q", "-n", "12"],
  "mt01-a12-tests": ["python", "-m", "pytest", "tests/test_a12_snapshot.py", "tests/test_a12_identity.py", "tests/test_a12_claims.py", "tests/test_a12_offer.py", "tests/test_a12_rights.py", "tests/test_a12_registry.py", "tests/test_a12_integration.py", "tests/test_a12_cli.py", "-q", "-n", "12"],
  "mt01-cli-tests": ["python", "-m", "pytest", "tests/test_cli.py", "tests/test_isolation.py", "-q", "-n", "12"],
  "mt01-ef01-tests": ["python", "-m", "pytest", "tests/test_ef01.py", "-q", "-n", "12"],
  "mt01-repair-tests": ["python", "-m", "pytest", "tests/test_ef01_repair.py", "-q", "-n", "12"],
  "mt01-integrity-tests": ["python", "-m", "pytest", "tests/test_ef01_ef02_integrity.py", "-q", "-n", "12"],
  "mt01-provider-tests": ["python", "-m", "pytest", "tests/test_ef01_provider_error_payload.py", "-q", "-n", "12"],
  "mt01-mt01-tests": ["python", "-m", "pytest", "tests/test_minimum_test.py", "-q", "-n", "12"],
  "mt01-ef01p-tests": ["python", "-m", "pytest", "tests/test_ef01_performance.py", "-q", "-n", "12"],
  "mt01-diagnostics-tests": ["python", "-m", "pytest", "tests/test_ef01_agy_diagnostics.py", "tests/test_ef02_authoring_preflight.py", "-q", "-n", "12"],
  "mt01-git-integrity-tests": ["python", "-m", "pytest", "tests/test_git_byte_consistency.py", "tests/test_ef02_worktree_line_endings.py", "tests/test_ef01_git_write_guard.py", "-q", "-n", "12"],
  "mt01-ef02-recovery-tests": ["python", "-m", "pytest", "tests/test_ef02_m0r_codex_provider_interruption.py", "tests/test_ef02_m0r_dispatch_only.py", "tests/test_ef02_m0r_fault_injection.py", "tests/test_ef02_m0r_hosted_checkpoint.py", "tests/test_ef02_m0r_isolated_runner_kit.py", "tests/test_ef02_m0r_live_child_probe.py", "tests/test_ef02_m0r_real_e2e_interruption.py", "tests/test_ef02_m0r_windows_boundary_probe.py", "tests/test_ef02_m0r_worker_os_kill.py", "tests/test_ef02_github_read_retry.py", "-q", "-n", "12"],
  "mt01-ef02-wake-tests": ["python", "-m", "pytest", "tests/test_ef02_wake_d1.py", "tests/test_ef02_wake_d2.py", "tests/test_ef02_cdp_doorbell.py", "tests/test_ef02_d3_github_artifact_authority.py", "tests/test_ef02_d3_signed_origin_consent.py", "tests/test_ef02_d3_two_grant_delivery.py", "tests/test_ef02_d5_chrome_acquisition.py", "tests/test_ef02_d5_local_handoff.py", "tests/test_ef02_d5_local_wake_entry.py", "tests/test_ef02_d5_local_wake_trigger.py", "tests/test_ef02_d5_run_registration.py", "-q", "-n", "12"]
}
```

## Bounded coverage gap

A10 M02's real test file is `src/ai_commerce/opportunity/trends/tests/test_m02.py`, outside the registered project-tests collection root `tests`. The proposed mt01-a10-m02-tests entry collects it explicitly. Until all required companions for an affected trends closure are registered, the plan records the existing project-tests fallback argv but remains UNPROVEN and does not execute it as a sufficient proof. A full tests/ PASS cannot be relabeled as M02 coverage. Shared pytest/dependency configuration affects every family, including M02, and therefore records all those IDs/consumers with the existing broad argv but remains UNPROVEN on this tree. Where all affected tests are under tests/, shared configuration uses the approved broad execution. Fan-in affecting M02 also remains UNPROVEN unless an authorized companion change registers a complete broad scope and teaches the fixed broad-profile validator that exact scope. This patch does not synthesize or silently expand the protected broad command.

Static import/contract analysis is bounded; arbitrary reflection or undisclosed external behavior is not a proof system. Recognized unresolved import calls, including imported aliases, remain conservative. JavaScript watcher/reconcile suites are outside the Python registry: changes to those real test files remain UNPROVEN, because registered pytest cannot collect them. Missing required test definitions and newly affected tests outside the broad collection root also remain UNPROVEN. Other unknown Python engineering paths use broad fallback rather than a fabricated narrow mapping.

## Local validation and integration boundary

Development focused run: `python -m pytest tests/test_minimum_test.py tests/test_ef02.py tests/test_ef01_repair.py::test_ef02_acceptance_reaches_selector_through_actual_child_cli tests/test_ef01_repair.py::test_protected_changed_path_stops_before_verification -q -n 12` passed 130 tests in 10.93s. The earlier focused iteration exposed an old assertion that a contract edit selected only its producer; it was corrected to require the newly preserved downstream consumer as well, then verified fresh.

Broader deterministic run: `python -m pytest tests/test_ef01_repair.py tests/test_ef01_performance.py tests/test_ef01_agy_diagnostics.py tests/test_ef01_provider_error_payload.py tests/test_ef02_authoring_preflight.py -q -n 12 -k "not git"` passed 179 tests in 14.42s. It covers repair/provider stop policy, shared budgets, observation-only worker fixtures, protected-path stops and per-action twelve-worker policy. The Git fixture variants were excluded because this executor is forbidden to run Git writes; no guard was bypassed. These are local scoped receipts, not a registered full project-tests result. Final combined verification is recorded below after the report/argv consistency test.

No .github file, schema, Git write guard, protected control, symlink, remote credential, unrelated product source, Git HEAD/ref or Git configuration is edited. No merge, rebase, deployment, wake or auto-merge is performed or claimed. Issue #158 overlaps engineering files and remains an integration concern: this executor leaves its isolated patch for EF02; if candidate integration later overlaps, the publisher must surface actual conflicts and require fresh verification. No conflict-free integration is claimed before that step.

## Final combined local receipt

The final combined command was:

```text
python -m pytest tests/test_minimum_test.py tests/test_ef02.py tests/test_ef01_repair.py tests/test_ef01_performance.py tests/test_ef01_agy_diagnostics.py tests/test_ef01_provider_error_payload.py tests/test_ef02_authoring_preflight.py -q -n 12 -vv -k "not git"
```

Exit **0**, **299 passed in 20.51s**, with **12 requested / 12 observed workers**. Wall time including startup was 21.21s; UTC span `2026-10-30T18:03:26.923403+00:00` to `2026-10-30T18:03:48.706169+00:00`. All 527 non-reporting source files match before/after; only this report is excluded so the post-run receipt can be recorded. The manifests include protected registry/schema/control files. Git-writing fixture variants remain excluded under the executor guard. This result does not claim registered project-tests execution, a provider/live EF02 action, runtime savings, publication, or activation of the proposed profiles.

The final local artifacts are retained under `.local/mt01-component-aware/verified/`:

| Artifact | SHA256 |
| --- | --- |
| `combined.receipt.json` | `04aa0b23aa83ff2da779153662b1286f07bb6a215ec48084eaac00783bb79ff7` |
| `combined.stdout.log` | `203926e051425b25cbe79da1b76190d703cb95ba8dc4b5f08b14d011a6101c3a` |
| `combined.stderr.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `source.before.json` | `2e2022b1859c7b312a4fa8a70d3d11cb4eced42ab35006dc55a08f5d3cf16044` |
| `source.after.json` | `2e2022b1859c7b312a4fa8a70d3d11cb4eced42ab35006dc55a08f5d3cf16044` |

Final worktree scope: five engineering/remote implementation files, three test files and this ordinary report. `git diff --check` passed; HEAD remains the pinned baseline. The protected registry and v1 schema have no diff. Final acceptance of the candidate, registered project-tests verification and publication belong to the enclosing EF01/EF02 workers.
