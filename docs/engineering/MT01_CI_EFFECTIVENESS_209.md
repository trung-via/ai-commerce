# MT-01 CI efficiency repair — Issue #209


> **Historical design snapshot (superseded 2026-11-03 by [PR #300](https://github.com/trung-via/ai-commerce/pull/300)).** The live post-merge reuse/Brain PASS gate now checks **one exact-head Windows CI job**, its successful pytest/Node/oracle steps and authentic Windows MT-01 artifact. **Ubuntu is no longer required.** Earlier two-OS examples below are retained only as evidence of the original Issue #209 implementation; they are not operational instructions.


Status: implementation on PR branch; exact CI and post-merge proof required.

## Observed failure
31/31 sampled run logs used FULL_PROJECT_REQUIRED. Every push runs full unconditionally; finite component mapping lacked recent model-defaults code; selected_files=1 denotes a whole directory, not 1 test.

## Implementation
- Preflight on hosted main push checks two-parent merge exact tree == tested PR parent tree using local Git, queries authenticated GitHub PR run(s) for that exact PR HEAD, requires BOTH Ubuntu and Windows jobs actually completed the named pytest and Node verification steps successfully. Network/API/schema failures or any provenance mismatch run ordinary full pytest.
- With valid proof only, reuse the original PR Python results and run a Python stdlib syntax smoke, plus existing Node tests; avoid FFmpeg/pip/full Python setup. This is a provenance-bound cache of GitHub CI, not reuse of arbitrary local data.
- Pull Request events keep existing MT-01 selector including full suite for selector/workflow/protected changes. The reuse proof implementation itself changes a guarded workflow, so its PR CI must be full.
- Register existing Codex default model selection module and config/decision in reviewed EF01 consumer group to prevent their being treated as unknown source.
- Added machine-readable selection target list/type so tests directory is not counted as one test. Hosted CI remains -n 4, self-hosted EF02 registered profiles remain -n 12.
- Test matrix covers exact two-OS proof; a changed merge tree, missing job/step, missing token, wrong SHA, wrong ref, GitHub API outage all fail closed to normal suite; real A12 component path must select related test(s) rather than full.

## Limitations
- Does not waive first PR full tests when CI/selector or protected scopes are modified.
- Requires GitHub Actions read access; if permission unavailable then does not skip Python.
- Does not auto-merge PR, dispatch EF02, bypass guard or create Wake.
- A full integration after a PR is still required if the merge commit's tree differs, no verified run exists, or environment is not proven.
