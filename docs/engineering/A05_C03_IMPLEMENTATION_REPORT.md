# A05 C03 implementation report

Date: 2026-10-29. Action: A05-C03-COMPOSITION.
Attempt: a05-c03-20261029-solmax-01.

**Disposition: implemented and locally verified for C03.** The final targeted command
passed **130 tests**. Registered project-tests is **FAILED: 570 passed, 16 failed,
2 skipped**. The 16 failures have the same Git/PowerShell startup signatures
documented by the A03 parent. No composition test failed in the registered run.

## Authority, actual source and scope

Read the Human decision in
docs/governance/HUMAN_DECISION_2026-10-29_CREATIVE_V1_1_A05.md and the C03 requirements in
docs/engineering/A05_IMPLEMENTATION_HANDOFF_V1_0.md. The active
[EF02 action in issue 57](https://github.com/trung-via/ai-commerce/issues/57)
confirms exactly these allowed paths:

- src/ai_commerce/composition/
- tests/test_a05_composition.py
- docs/engineering/A05_C03_IMPLEMENTATION_REPORT.md

Observed detached worktree HEAD and ORIG_HEAD:
64850ccd67b2dc33d2ce72e7b7df124018cd6810.
Its local commit object identifies parent
b88bf37f81aa115ab36320ee1f043283923984ce, tree
58adb1d30d0f3b6bf64e9f6e093ed3c84b82db23, and message
"EF02 dependency integration for a05-c03-20261029-solmax-01".
These are local observations, not a claim about current GitHub main.
A new candidate commit/ref was not created in this session: Git cannot start.
The authored work remains local source changes for the worker's later delivery.

Inspected actual A03 v1.1 source: intent.py, decisions.py, v2.py, live.py,
sidecars.py, package exports and the A03 v1.1 implementation report/tests.
Inspected unchanged V1 models.py, store.py, briefs.py, reasoner.py and the A03/A04
integration tests. Actual A04 inputs here are the original manifest.py,
registry.py, runner.py, media/base.py, media/local.py, A02 schemas and the legacy
CLI handoff functions. This checkout does not contain the independent A04 v1.1,
C06 renderer or fan-in bridge candidates. Their APIs were not assumed.

Seven new composition modules, one new test module and this report are the entire
authored change. App CLI, registry, A03 parent source, existing tests, project
profiles, WorkItems and governance documents remain unchanged. No dependencies
were added.

## Data contract and authoring

CreativeComposition is a frozen typed record serialized with schema_version
CreativeComposition.v1. Nested tracks/items/settings/canvas are frozen typed
values; full source briefs and media artifact metadata use canonical JSON bytes,
with fresh detached dictionaries on inspection. Source dictionaries and caller
lists cannot mutate a record.

The record retains exact original_intent and separately editable current intent.
Whitespace, emoji and decomposed Unicode are preserved without normalization.
An optional source_brief retains the entire A02-compatible brief, including custom
fields. Passing an A03 CreativeIntent retains the sidecar's exact raw text,
SOURCE/INFERRED/UNKNOWN fields and observed/UNKNOWN metadata. It must bind to the
same brief and original raw text.

Optional selection accepts the actual V1 ConceptSelection, its original JSON,
the consumer selection with selection_id, or CreativeStore.load_selected's
complete brief/concept/selection payload. Complete payloads validate the exact
concept digest, revision and source brief. Bare pins are structurally validated
references; they do not authenticate an external selection event. No Human
approval or model-generation status is inferred from a pin.

The compatibility test pins the historical V1 values:

- selection_id: selection:ac7eb5e385210355e8acf01e08ed05251467b011a1bff03c86da5a442ca16b9f
- concept digest: 5ac3559ec4f3f57ce4808070d69d00dd4be7db934b53bf4fbe5c01ddd7f90f7b

Canvas width/height are explicit positive integers. FrameRate is an exact positive
rational, normalized by greatest common divisor; integer fps is also accepted
by the typed Canvas constructor. Fractional float fps is rejected. Timing uses
integer frames: start >= 0, item duration >= 1, source_start_frame >= 0, and every
item must fit within composition duration. Empty compositions may have duration
0. No aspect ratio, frame length, model, product, CTA, storyboard or score is
required.

Tracks may be empty, absent, or multiple. Visual tracks composite bottom to top
in their stored order; items within one track must be chronological and
nonoverlapping. Parallel visuals use multiple tracks. Timeline gaps and trailing
canvas duration are valid. Image/video items reference exact artifact IDs;
intent items retain an explicit unresolved request. Image trims, unsupported
track/item kinds, dangling references and duplicate IDs are rejected.
Text/audio overlays and transitions are not implemented by this C03 contract.

Public entry points:

- create_composition: original freeform intent, optional A03 source/pin, arbitrary
  manually authored visual tracks and optional media.
- direct_image: a supplied image plus an explicit duration; without an image it
  retains unresolved intent.
- compose_images: any caller-supplied ordered image/frame pairs; no scene quota.
- edit_composition: returns an unsaved child changing intent, canvas, timing,
  tracks, media or settings. Original source evidence and pins remain fixed.
- CompositionStore.save/load/edit: explicit immutable revisions.
- compile_composition: resolve used media into CompositionPlan.v1 data.

For an existing successful A04 manifest and its registry:

~~~python
image = MediaReference.from_manifest(existing_a04_manifest)
composition = direct_image(
    raw_text, image, canvas=Canvas(1280, 720, 24), duration_frames=48,
)
saved = CompositionStore(composition_root).save(composition)
result = compile_composition(saved, RegistryMediaResolver(existing_registry))
~~~

Creating or saving data does not generate media or execute a renderer.

## Digests, revisions and storage

A root composition_id is "composition:" plus SHA-256 of its complete initial
canonical content, excluding composition_id/revision/parent. Every revision
digest hashes its entire canonical record. Canonical bytes use the existing A02
UTF-8 JSON convention: sorted keys, compact separators, ensure_ascii=False and
finite numbers only. Duplicate JSON keys, invalid Unicode, nonfinite values,
unknown versions and extra/missing serialized fields fail validation.

Each child retains composition_id and pins its exact preceding revision/digest.
The store checks all saved ancestors iteratively, original evidence retention
and material content change. It has no mutable latest pointer.

Storage reference:
compositions/<composition SHA-256>/r<revision>.json.
The stored envelope includes composition and its digest. Reads validate envelope
checksum, path identity, record identity and parent links. Save uses the existing
ArtifactRegistry exclusive-create/immutable-write implementation without changing
it. Matching complete bytes are idempotent. Competing revisions, damaged bytes
and partial writes cannot overwrite a saved revision. The existing protected
custody paths reject traversal, ADS, links/reparse points and hard-linked files.

This is a local append-only API, not a signed audit log, multi-file transaction,
or protection against a hostile process concurrently renaming ancestors.
Consumers should retain the composition digest and recheck asset identity at
render time.

## Media references and compilation truth

MediaReference preserves the original A04 MediaArtifact metadata, including
rights and import/mock labels, plus optional manifest reference/checksum and
legacy handoff association. Artifact ID and objects/<sha256> path must match its
lowercase SHA-256, MIME/kind and positive integer byte count. References are
declarations until compilation resolves actual bytes.

The actual V1 handoff is the five-field record handoff_version, selection_id,
job_id, attempt and manifest_record_sha256. Its content-addressed handoff ref is
checked separately from the A04 manifest record checksum. This association does
not assert that the asset was generated to realize the selected concept.

RegistryMediaResolver reuses LocalMediaRunner.load for manifest/job/attempt,
request.json binding and actual artifact checks, and validates any supplied
handoff. It preserves the actual parent registry's capability limits: PNG and
AVI container validation/hash checks. JPEG/WebP/MP4 can be represented as media
references, but require a capable explicit resolver. Their MIME declaration
alone cannot make this parent's resolver succeed. A later A04 v1.1 adapter can
implement the MediaResolver protocol and return ResolvedMedia.

CompileResult has three explicit states:

- ASSET_REQUIRED: empty visual timeline, an unresolved intent item, or no resolver
  configured to verify declared media. It contains diagnostics and no plan.
- SOURCE_ERROR: missing/corrupt/mismatched sources, custody bindings or resolver
  failures. It contains diagnostics and no plan; exception causes are retained
  for actionable dependency details.
- READY: every used visual asset resolved, with a typed CompositionPlan.v1.
  It contains composition identity/revision/digest, canvas, frame timing, settings,
  ordered tracks and exact source references/metadata.

Unused library entries do not become rendered scenes or imply readiness.
An unresolved item blocks the whole plan even if another item has an image.
There is no model call, implicit fixture, automatic retry or fallback.

The plan is provider-neutral structured data, without shell commands or FFmpeg
filter strings. READY means resolution succeeded under the configured resolver,
not that an encoder supports the timeline or that a playable video exists.
The default resolver provides the original A04 validation strength; full decode,
video trim/stream limits, engine capabilities and source revalidation belong to
C06. An injected resolver is responsible for truthful source verification.

## Exact verification

Environment observed: Windows win32; Python 3.14.7; pytest 8.4.2;
jsonschema 4.26.0.
Interpreter:
C:\Users\TRUNG\AppData\Local\Python\pythoncore-3.14-64\python.exe.

The following are actual child module argv, invoked with subprocess.run through
Python launchers. Windows command-shell quoting/length constraints required
encoded/chunked launcher input; test selection was unchanged. All test children
used these environment overrides, matching the parent report's local test setup:

- PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
- PYTHONDONTWRITEBYTECODE=1
- PYTHONUTF8=1
- PYTEST_ADDOPTS=-p no:cacheprovider
- TEMP and TMP = <checkout>/.local/a05-c03-test-temp

No package installation, repository test skip/xfail, Git/PowerShell repair or
verification-profile edit was performed.

| Exact module command | Observed result | Exit |
| --- | --- | --- |
| python -m pytest tests/test_a03_a04_integration.py -q | Pre-change parent baseline: 30 passed in 46.08s | 0 |
| python -m pytest tests/test_a05_composition.py -q | Initial: 121 passed, 1 failed in 22.60s | 1 |
| python -m pytest tests/test_a05_composition.py -q | Interim: 129 passed, 1 failed in 27.60s | 1 |
| python -m pytest tests/test_a05_composition.py -q | Final targeted: 130 passed in 27.16s | 0 |
| Registered project-tests: python -m pytest tests -q | 570 passed, 16 failed, 2 skipped in 303.12s | 1 |

The registered argv was read and asserted directly from the unchanged
.github/ef02/verify-profiles.json. Its actual subprocess argv starts with the
absolute interpreter above. Metadata records UTC start
2026-10-28T21:04:51.115422+00:00 and wrapper elapsed 304.072s.

The initial failure reproduced acceptance of a damaged A04 request.json binding.
Repair routes through the existing A04 reopen validator; that regression passes.
The interim failure found an Unsupported MIME cause hidden by A04's
ArtifactCorruptError wrapper. The compiler now retains the cause chain instead of
losing the dependency diagnostic. Both failed logs remain available.

Local ignored raw logs and SHA-256:

| Log under .local/ | SHA-256 |
| --- | --- |
| a05-c03-parent-baseline.log | abb246cb5dd8fc1632eb2f52084252fda5795e4bb159b335245b98ff889d11da |
| a05-c03-targeted-initial.log | 95469358f74681d26e1528a2c20993cb3cadf92d2c6b30dfd4135a27775f5db1 |
| a05-c03-targeted-final.log (interim failure) | bc0e6cc3c1cb1b6399dea3c09b8afbb2ec4ea64aba0beb4dda2980acc3641b2c |
| a05-c03-targeted-verified.log | df10fabe860bd6e2acae908122f825c997904bc6a5a361d3642c870c630ea800 |
| a05-c03-project-tests.log | 6f3bd43b34d0f7bd59e46a134fb395be232b868db6a182fda6832f719c3303b5 |

.local/a05-c03-project-tests-result.json records actual argv, environment, exit,
time and raw-log hash. No new code changes followed the final targeted/full runs.

### Registered-suite deficits

Fifteen unchanged tests fail during fixture git init with status 3221225794
(0xC0000142), before their executor behavior. One unchanged test cannot create the
PowerShell process: WinError 1920, "The file cannot be accessed by the system".
These match the failure groups and signatures in the actual A03 parent report.

Exact failing node IDs:

- tests/test_ef01.py::test_both_executor_subprocesses_and_normalized_result[codex]
- tests/test_ef01.py::test_both_executor_subprocesses_and_normalized_result[antigravity]
- tests/test_ef01.py::test_missing_binary_returns_non_pass_and_no_verification
- tests/test_ef01.py::test_scope_violation_cannot_pass
- tests/test_ef01.py::test_dirty_worktree_rejected
- tests/test_ef01.py::test_cli_full_path_negative_missing_binary
- tests/test_ef01.py::test_verify_out_of_scope_mutation_is_failure
- tests/test_ef01.py::test_cli_accepts_windows_utf8_bom_verify_json
- tests/test_ef01.py::test_agy_headless_soft_denial_is_explicit_nonpass
- tests/test_ef01.py::test_agy_headless_denied_actions_are_not_ignored_even_after_write
- tests/test_ef01.py::test_real_mock_executor_timeout_is_unknown_and_no_verification
- tests/test_ef01.py::test_parent_lost_around_provider_boundary_remains_unknown
- tests/test_ef01_ef02_integrity.py::test_staged_git_rename_reports_old_and_new_paths
- tests/test_ef01_ef02_integrity.py::test_verify_undoes_change_cannot_be_ef01_pass
- tests/test_ef01_ef02_integrity.py::test_candidate_rename_deletes_original_and_adds_destination
- tests/test_ef02_m0r_isolated_runner_kit.py::test_powershell_control_script_parses_on_windows_without_execution

The two skips are unchanged tests/test_a04_media_operations.py:257 host-capability
guards: "Host permits neither directory symlinks nor junctions". No new test
was skipped. The complete project profile remains FAILED; host-link cases and
those executor fixture cases remain unverified.

## Reviewable local evidence example

.local/a05-c03-evidence/ contains persisted r1/r2, an explicit A04 mock fixture,
compile-result.json and summary.json. Fresh store/registry instances reopened and
verified the saved records and source bytes; revision 1 returns ASSET_REQUIRED,
and revision 2 returns READY.

| Observed value | Exact evidence |
| --- | --- |
| composition_id | composition:4beced3564e65a912a63fcb2927de91c819a3bb51a283123fb94a667dbbb8e83 |
| r1 / parent digest | 2e2b4c4516b2aa23936922ca9fd0265c53e251fe86b073d55cc9a75199270bee |
| r2 digest | 6a509ae108fa0665f1b6a7033b76c050d8af64fee147c86bc9b1e8c0d6c828cb |
| source artifact SHA-256 | 884216073bb51af03174db57102bb3e74b484d3c4c4e661dac522e48470097b2 |
| A04 manifest record checksum | 8a34ad2b4f5e5663bcae4c93daec614ac3472ea7b6e04ae53ffac51953137ad6 |
| compile-result.json SHA-256 | 77a523ad631b91433f08dc9905e8dabeee25602ddff2131025fc277475dee1a6 |
| canvas and duration | 721 x 479; fps 30000/1001; 37 frames |
| provenance | MOCK / SYNTHETIC_FIXTURE; generation_performed=False |

Composition files are under
.local/a05-c03-evidence/composition/compositions/<composition SHA-256>/r1.json
and r2.json. The media manifest ref is
jobs/media-ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b/attempt-1.manifest.json
under .local/a05-c03-evidence/media/.

The first example launcher exited -1066598274 without output after writing
compile-result.json, while collecting additional environment metadata. The
native cause is UNKNOWN. Existing artifacts were inspected and a separate
read-only reopen/compile comparison completed with exit 0 and produced
summary.json. The media producer was not replayed.

This is an explicit synthetic fixture and a compiled data plan. No video render,
model generation, semantic realization, API inference, paid usage or publication
is claimed.

## Scope integrity and second-pass check

Initial SHA-256 snapshot covers all 170 pre-existing source files. Final
comparison found none modified or missing. The read-only V2 Git index comparison
also covers 170 entries: 78 exact blob matches and 92 matches after CRLF-to-LF
normalization, with no differences or missing files. It is an index comparison,
not a successful Git status/diff command. Snapshot SHA-256:
f80f6d7ec55a9f5afad73a96810d39edd85438c778963d319e7c45e6149daa0a.

git status --short and git diff --check each fail at startup with signed exit
-1073741502 (0xC0000142), without diagnostics. git diff --check is UNAVAILABLE,
not PASS. Reading the full commit tree was also unavailable because some tree
objects were not present as loose objects; no pack traversal or Git repair was
attempted. Metadata, initial-file hashes and the index are the observed evidence.

All eight new Python files parse successfully and have no trailing whitespace.
Their full hashes and line counts are in .local/a05-c03-code-checks.json.
The exact file/source comparisons are .local/a05-c03-source-snapshot.json and
.local/a05-c03-index-check.json. These are local ignored evidence artifacts.

A separate same-agent read-only checklist revisited the stored source and actual
test outputs; no external reviewer or independent agent audit is claimed:

| Criterion | Verdict and evidence |
| --- | --- |
| Versioned immutable model, stable hashes and parent revisions | SATISFIED: golden digest, save/edit/reopen, conflict, concurrent-write and tamper tests |
| Original Unicode intent, optional A03 source/pin | SATISFIED: four Unicode cases, historical V1 ID cases, invalid payload/source-binding cases |
| Zero/multiple tracks, manual timing and ordered images | SATISFIED: empty, ordered stills, manual tracks/gaps/video-trim data and invalid timing tests |
| Prompt-only truth and provider-neutral output | SATISFIED: ASSET_REQUIRED/intent-item cases and structured plan assertions |
| Actual source resolution and A04 compatibility | SATISFIED within parent capabilities: real PNG import, explicit mock, legacy handoff and request-binding/corruption tests |
| Authored changes stay within allowed paths | SATISFIED: nine new authored files; 170 existing files unchanged |
| Full registered verification | UNSATISFIED: 16 host/tool failures and 2 pre-existing host-capability skips detailed above |
| C06 playable output, full A04 v1.1/bridge fan-in, A06/M1 | OUTSIDE THIS ACTION; no completion claim |

## Final authored files and delivery limits

- src/ai_commerce/composition/__init__.py
- src/ai_commerce/composition/_validation.py
- src/ai_commerce/composition/models.py
- src/ai_commerce/composition/sources.py
- src/ai_commerce/composition/authoring.py
- src/ai_commerce/composition/store.py
- src/ai_commerce/composition/compiler.py
- tests/test_a05_composition.py
- docs/engineering/A05_C03_IMPLEMENTATION_REPORT.md

CLI integration and the C06 rendering adapter remain with the separate fan-in
and renderer actions. No main merge, deployment, external product-provider
selection, rights upgrade, creative quality acceptance, A06 E01 or M1 acceptance
was performed.
