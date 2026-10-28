# A03/A04 dependency integration candidate

Date: 2026-10-29. This is the historical integration sub-session report for an offline candidate. Its scoped A03/A04 regression passed; during that Codex sub-session, registered `project-tests` returned **347 passed, 16 failed, 2 skipped**, exit 1; the 16 failing IDs matched the pre-edit baseline. **Subsequently, the EF01 wrapper executed the same registered `project-tests` on the candidate worktree and recorded 365 passed, exit 0 (78.73 s).** See the post-executor verification section below. No A05, MP4 renderer or M1 outcome is implied.

## Inspected source and preservation

The supplied dependency-composed detached HEAD is `c92575c951045f19edb397fe339f4686fa6784da`, tree `c2683b486f89c72a1f6f274f470268051c5dbff7`. Its recorded parent is `21e134752f1cb3e5ff7cc202e0dea309f2d0ead6`. These identities were read from local Git metadata/objects, not inferred from a branch name or a current-main lookup. Both supplied parent implementations were already present before edits.

Actual APIs, parent tests/reports/work items, A02 contracts, existing CLI tests and the verification registry were inspected before implementation. Preserved Git tree/blob identities at that supplied HEAD:

| Parent path | Git object |
| --- | --- |
| `src/ai_commerce/creative/` | tree `548c4e9db9fb96db95b518433dd4a0fc457e9dc3` |
| `src/ai_commerce/media/` | tree `50edba283f75314a9eec0fa428042c97a41e98e4` |
| `src/ai_commerce/operations/` | tree `d4b8e63527b6ed3d031ae09a66fd00be506cbe55` |
| `tests/test_a03_creative.py` | blob `2587beb4609dee20ca5b0debb1ff3c00aa35fa4c` |
| `tests/test_a04_media_operations.py` | blob `a6123c950aa4f648f5484e23d82586353dd4e46d` |

All **20 parent-owned files** (modules, tests, implementation reports and work items) retain their pre-edit byte SHA-256 values. A read-only comparison against all **156 pre-existing Git index entries**, with checkout CRLF normalization where needed, identifies only these three modifications: `src/ai_commerce/cli.py`, `README.md`, `docs/engineering/PROJECT_STATE.md`. Only `tests/test_a03_a04_integration.py` and this report are added as project files. No deletions.

No parent module, EF01/EF02 implementation, governance, Roadmap, schema, pyproject or sibling work-item file was edited. Verification artifacts are under the already ignored `.local/` directory. Git status/diff commands cannot run here: `git --version` itself exits `0xC0000142` (DLL initialization failure). The index/hash and Python whitespace checks are separate fallback evidence, not a claimed successful Git diff check.

## Compatible CLI integration

Existing `hello`, `about`, `validate` and A02 text-only `mock-generate` behavior is retained and covered by the unchanged regression tests. ProjectIdentity/about status was not promoted to a complete product runtime.

| Command | Implemented API and result |
| --- | --- |
| `concept-create --store DIR (--text TEXT / --brief FILE) --mock [--count N]` | `intake_brief`, explicit `MockCreativeReasoner`, `generate_concepts`, `CreativeStore.save_brief/save_concept/select`; JSON `concepts` array containing saved brief/concept/selection payloads. One concept by default; fixture capacity errors stay errors. |
| `concept-inspect --store DIR --selection-id ID` | `CreativeStore.load_selected`; reconstructs a pinned selection without chat. |
| `concept-reframe --store DIR --selection-id ID --lens TEXT --changes FILE` | Strict JSON creative edits passed to `reframe_concept`; saves a new revision and selection while retaining the original. |
| `asset-run --store DIR --selection-id ID --out-dir DIR --mode import --source FILE --mime-type MIME` | Validates selection first, then `LocalMediaRunner.run(MediaRequest(...))`; preserves actual local source bytes/reference. Supported MIME types are non-interlaced PNG and the parent's AVI container format. |
| `asset-run ... --mode mock` | Explicit A04 synthetic PNG fixture. No source file, model or credentials. |
| `asset-inspect --store DIR --out-dir DIR --handoff REF` | Reopens the pinned A03 selection and calls `LocalMediaRunner.load`, including verification of actual stored output bytes. |

New command JSON is ASCII-escaped for portable redirected Windows output; decoding preserves original Unicode. Brief files accept UTF-8, including BOM. Reframe edits are explicit caller-supplied text; no model response is invented.

Exit 0 means the creative operation succeeded or the asset manifest is `SUCCEEDED`. Asset `FAILED`/`UNKNOWN` manifests remain available as JSON with exit **1**. Input, validation, custody, missing/corrupt output and conflicting-version errors return **2** with an error on stderr. There is no CLI retry loop or UNKNOWN-to-success substitution.

## Handoff and correctness boundaries

A03 exposes pinned concept selections; A04's `MediaRequest` has no concept/prompt field and its manifest rejects extra top-level fields. Neither parent was rewritten to force a shared request type.

The CLI instead writes an immutable, content-addressed `handoffs/<sha256>.json` association in the existing artifact registry. Its version-1 payload records the A03 `selection_id`, A04 `job_id`/`attempt`, and exact A04 manifest `record_sha256`. The selection identity already binds the concept revision/digest and source brief digest. The association leaves both parents' serialization and custody semantics intact.

An asset-inspection JSON response includes the handoff, reconstructed `creative` payload, A04 `asset_manifest_ref` and unchanged `asset_manifest`. A consumer can independently reconstruct it with:

~~~python
handoff = json.loads(registry.read(handoff_ref))
creative = CreativeStore(creative_root).load_selected(handoff["selection_id"])
manifest = LocalMediaRunner(registry).load(handoff["job_id"], handoff["attempt"])
assert manifest.to_dict()["record_sha256"] == handoff["manifest_record_sha256"]
result = validate_record("JobResult", manifest.result)
artifact = result["artifact"]
if manifest.state == "SUCCEEDED":
    media_bytes = registry.verify(validate_record("MediaArtifact", artifact))
~~~

This is an A05-readable data handoff tested through the existing contracts and loaders, not an implemented A05 renderer. Keep both persisted roots. The association records the user's selected concept for a chosen local asset; it does not establish that the mock pixel visually realizes that concept. An imported asset's source snapshot always identifies the actual media file. The constant mock fixture can be associated with multiple revisions through different handoff records without changing media identity.

Factual status stays `UNVERIFIED`; creative model identity and default media rights stay `UNKNOWN`. Imported source generation remains `UNKNOWN`; mock source generation is `SYNTHETIC_FIXTURE`. Media custody success does not verify assertions in the brief. Publication remains `NOT_ATTEMPTED`. Unknown cost remains null/UNKNOWN. No network/model credential, external media generation, publishing, rendering or transcoding is used.

## Verifiable test results

Environment: Windows `win32`; Python **3.14.7** at `C:\Users\TRUNG\AppData\Local\Python\pythoncore-3.14-64\python.exe`; pytest **8.4.2**; jsonschema **4.26.0**.

Both full-suite runs execute the unchanged registered `project-tests` argv from `.github/ef02/verify-profiles.json`:

~~~text
python -m pytest tests -q
~~~

The final run resolves that registry through the existing `ai_commerce.remote.validation.verify_registry` function and asserts the exact argv above. No EF02 action or remote executor is launched.

All runs use the same explicit environment overrides:

~~~text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
PYTHONDONTWRITEBYTECODE=1
PYTEST_ADDOPTS=-p no:cacheprovider
TEMP=<checkout>/.local/integration-pytest-temp
TMP=<checkout>/.local/integration-pytest-temp
~~~

Plugin autoload is disabled because the parent reports document an unrelated installed plugin's SSL DLL startup failure. A workspace temp root avoids the restricted default temp area. These overrides do not deselect tests, add xfail marks, hide failures or edit the verification profile.

| Run | Passed | Failed | Skipped | Pytest duration | Exit |
| --- | ---: | ---: | ---: | ---: | ---: |
| Pre-edit composed `project-tests` baseline | 317 | 16 | 2 | 46.52 s | 1 |
| Focused A01/A02/A03/A04 + integration regression | 242 | 0 | 2 | 91.77 s | 0 |
| Final composed registered `project-tests` | 347 | 16 | 2 | 114.04 s | 1 |

Exact focused command:

~~~text
python -m pytest tests/test_a03_a04_integration.py tests/test_a03_creative.py tests/test_a04_media_operations.py tests/test_a02_contracts.py tests/test_a02_mock_provider.py tests/test_cli.py tests/test_foundation.py tests/test_isolation.py -q
~~~

The **30 new integration cases all pass** in the final suite. Coverage includes an actual Unicode brief with no product/CTA -> saved concept/selection -> local PNG import or explicit synthetic mock -> validated `MediaArtifact` -> reopened consumer handoff after the original imported file is removed. It also covers JSON/custom briefs, multiple fixture concepts, optional reframe lineage, shared asset identity across selections, actual module CLI invocation outside the checkout, malformed/duplicate/empty briefs, missing explicit mock choice, invalid requests, corrupted imports, missing/corrupt stored assets, handoff tampering/version/binding errors, unknown selections, rejected factual/rights promotions, and provider FAILED/UNKNOWN receipts without replay.

Final failure IDs equal the baseline failure IDs exactly (`UNCHANGED_BASELINE_FAILURE_SET True`):

| Existing tests | Failures | Cause observed in both runs |
| --- | ---: | --- |
| `tests/test_ef01.py` | 12 | Fixture `git init` exits `3221225794` before the engineering behavior under test. |
| `tests/test_ef01_ef02_integrity.py` | 3 | Same `git init` startup failure. |
| `tests/test_ef02_m0r_isolated_runner_kit.py::test_powershell_control_script_parses_on_windows_without_execution` | 1 | PowerShell process creation raises `WinError 1920: The file cannot be accessed by the system`. |

The two skips are the unchanged A04 actual symlink/junction tests at `tests/test_a04_media_operations.py:257`: this host permits neither directory symlinks nor junctions. Native link boundary verification remains unproven here. These skips are reported, not replaced with success.

The 16 sub-session failures identify an environment startup issue. The later EF01 wrapper verification is green, but that alone does **not** prove the underlying Git/PowerShell startup incident has been repaired. A05, MP4 and M1 remain outside the scope; GitHub main publication is a separate promotion step.

### Local evidence hashes

Logs are retained locally under ignored `.local/`; they are not committed source artifacts.

| File | SHA-256 |
| --- | --- |
| `.local/integration-baseline.log` | `2af991d58aa6eb2c18c5422970f3704ac614a9a26f102f3dedf543099e467ffa` |
| `.local/integration-focused.log` | `3d10b73d0c56d8ed06bc02d873b39b8473c79035c67e4776fcffc77ec772a390` |
| `.local/integration-project-tests.log` | `8e2f0a981465d0d3a551132e216510223d980fc4703ba269c28ea8acc0100974` |
| `src/ai_commerce/cli.py` | `1679df883804fffb8d684879e6b06915dc7f769c719b33bd9f0bd633639ece07` |
| `tests/test_a03_a04_integration.py` | `fc56975aee55ee4c64455eb55618aa929f80441c1fe7f56e1f7f1615777b3b8f` |

Python AST parsing, final-newline and trailing-whitespace checks pass for both changed Python files. Final scope checks preserve every parent file and all other pre-existing project paths. No commit, push, publication or merge was performed by this integration turn.

## Subsequent EF01 registered verification (observed from GitHub artifact)

This section was added during the Human-approved promotion review; it does not replace the sub-session results documented above. EF02 [workflow run 40735271832](https://github.com/trung-via/ai-commerce/actions/runs/40735271832) completed with **3/3 CANDIDATE_READY**. In artifact `ef02-results-40735271832-1` (artifact ID `12842317100`), the Integration action's `ef01/engineering_result.json` records `status=BUILD_VERIFIED_FOR_SCOPE`, `verify_exit_code=0`, `source_base_sha=c92575c951045f19edb397fe339f4686fa6784da`, and `changed_files` limited to README, CLI, state/report and integration tests. Its independent `ef01/verify.stdout.log` records:

~~~text
python -m pytest tests -q
365 passed in 78.73s (0:01:18)
~~~

This is the subsequent EF01 registered test run on the candidate, not a retrospective change to the earlier Codex sub-session's `347 passed / 16 failed / 2 skipped` result. The reported Git/PowerShell startup incident remains without a root-cause repair proof. A03/A04 capability is an offline mock/import MVP; there is no actual LLM creative inference, AI media generation, A05 video render, publication, or M1 acceptance. Candidate integration commit: `22adb25000fedb640c9525ed92ca9d6ae1d19215`.
