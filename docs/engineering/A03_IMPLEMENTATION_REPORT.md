# A03 — Freeform Brief & Creative Reasoner

Date: 2026-10-29. WorkItem: `A03-FREEFORM-CREATIVE-001`.

**Disposition: IMPLEMENTED.** Local A03 behavior is tested: **100 focused tests pass**; **130 A02+A03 tests pass**. Registered `project-tests` reports **246 passed, 16 failed** in this Windows environment. Wheel build/install is **unverified** because the declared `setuptools` build dependency is absent. This report does not claim complete project verification or live model capability.

## Source and bounded scope

Observed detached checkout HEAD: `21e134752f1cb3e5ff7cc202e0dea309f2d0ead6`, read from the checkout's Git worktree metadata. Git commands fail to start here; this is not a statement about current GitHub/main.

Local canonical references read:

- `docs/roadmap/ROADMAP_A_CREATIVE_FIRST_V1_0.json`: A03 = C01+C02, depends on A02.
- `docs/roadmap/WORK_TREE_V1_1.json`: flexible brief; one or more concepts as requested; no invented verified facts.
- Adopted Governance v0.3, especially Constitution C-04, C-06, C-09, C-10 and C-17, and Contract K-07/K-08/K-09.
- Existing A02 ContentBrief JSON Schema, validator, provider capabilities/error types and core provenance helpers.

Authored files are six modules under `src/ai_commerce/creative/`, `tests/test_a03_creative.py`, this report and the A03 WorkItem. All existing tracked files remain unchanged, including A02 schemas/core, shared CLI, packaging configuration, roadmap and engineering runtimes. Runtime scratch/logs are under ignored `.local/`; ordinary Python/pytest caches are ignored.

This is product creative logic. It adds no A05 renderer, MCP/LangGraph, publication path, model credentials, paid calls, engineering executor logic, aesthetic scoring or global approval state.

## Implemented behavior

### C01 intake

`intake_brief(text_or_mapping, brief_id=None)` returns a detached, validated A02 ContentBrief. Freeform Unicode is preserved exactly in `objective` and `prompt`; its default brief ID is deterministic. A supplied JSON object/string retains its existing identity and optional/custom fields. There is no required product, SKU, CTA, scene, storyboard, KPI or concept count; freeform intake sets `product_ref=null`.

This is lossless intake, not a claim of language-model understanding or automatic extraction of audience/aesthetic facts. JSON-looking input beginning with `{` or `[` is parsed strictly. Malformed JSON, duplicate keys, nonfinite numbers, invalid Unicode and invalid A02 data are rejected.

`brief_digest` uses the existing A02 `canonical_bytes` and `sha256_bytes` over the entire validated brief. It identifies the semantic JSON snapshot, not original file formatting. `revise_brief` returns edited content with the same brief ID; saving it creates a new digest-addressed snapshot without changing the original.

### C02 reasoner seam and mock

`CreativeReasoner` is a product protocol exposing A02 `ProviderCapabilities`, `ProviderObservation` and typed `ConceptDraft` results. `generate_concepts(brief, reasoner, count=1)` validates the boundary and returns immutable `ConceptRecord` objects. No reasoner supplied raises `NO_MODEL_CONFIGURED`; there is no automatic mock fallback.

The supplied `MockCreativeReasoner` is **TEST-ONLY deterministic template code**, not a model. Its observation is `provider=deterministic-test-only`, `model=UNKNOWN`, `mode=TEST_ONLY_MOCK`. Default output is one concept. Optional mock families have different mechanisms and visual directions:

| Family | Mechanism |
| --- | --- |
| sensory_poem | Wordless sensory abstraction through texture, light and rhythm |
| object_perspective_comedy | An imagined object misreads human behavior |
| reverse_time_mystery | An unexplained result is reinterpreted through reversed clues |
| impossible_field_guide | A fictional taxonomy invites exploration instead of following a protagonist |

The fixture supports up to four families and reports `MOCK_CAPACITY` for more. This is a fixture limit, not a product quota; the independent adapter test successfully requests seven concepts. The count is an optional API argument, not a mandatory brief field or an inferred natural-language promise.

Adapters must translate their own responses into validated drafts and report provider/model identity actually available to them, otherwise `UNKNOWN`. This task implements no real-model adapter. The current runner rejects network/spending capabilities before calling an adapter. Reserved mock metadata and mock subclasses cannot silently claim model provenance. Malformed metadata/results, duplicate returned families or unchanged mechanisms, unexpected counts and invented source references fail explicitly. Duplicate checks concern returned data, not aesthetic quality.

### Typed records and uncertainty

A concept contains schema version, brief ID, source brief digest, stable concept ID, revision, family, title, premise, visual direction, source refs, assumptions and provider/model observation. Its initial ID hashes the source identity, draft and generation observation. Repeated identical generation is stable; new brief content has new source/concept identity.

Records and nested provenance/change records are frozen dataclasses; list inputs become tuples and serialization returns detached data. Strict deserialization rejects missing/extra fields, malformed IDs/digests/types and unsupported certainty. Concept `factual_status` remains `UNVERIFIED`; `rights_status` remains `UNKNOWN`. References are input locators, not evidence that their assertions were verified. Imported brief fields and creative prose are not promoted into product truth.

### Optional reframe and revision

`revise_concept(original, changes)` edits creative text fields only. `reframe_concept(original, lens=..., changes=...)` is a separate, optional local operation using explicit caller edits. It does not fabricate a model-generated reinterpretation.

Both return a child with the same concept ID, revision + 1, exact parent revision/digest and before/after changes. A reframe additionally records its lens and `REFRAME` operation. Source identity, observation, source refs and certainty cannot be relabeled through edits. The original remains unchanged and is retained in storage. Plain generation does not automatically reframe.

### Immutable storage and consumer API

`CreativeStore` provides:

- `save_brief` / `load_brief`: full canonical ContentBrief snapshots addressed by digest.
- `save_concept` / `load_concept`: explicit numbered revisions, validated against the stored source and parent.
- `select` / `load_selection`: immutable choices pinned to a concept revision and digest.
- `load_selected(selection_id)`: fresh JSON-ready brief, concept and selection for a future A05 consumer.

Equal writes are idempotent. Conflicting bytes at an existing identity/revision raise `VersionConflict`. A03 uses exclusive file creation to prevent a check/write overwrite race; existing A02 helpers are unchanged. Parent/delta/source/provenance inconsistencies are rejected. A new selection never changes an earlier selection and does not grant render/publication authority.

No mutable “latest” pointer or chat history is required. Records are local integrity/lineage data, not signed attestations. A crash may leave a partial new file; it is rejected on subsequent use rather than silently repaired or overwritten. This is not a transactional database.

## Consumer example

```python
from ai_commerce.creative import (
    CreativeStore, MockCreativeReasoner, generate_concepts,
    intake_brief, reframe_concept,
)

store = CreativeStore(".local/a03-example")
brief = intake_brief("Một ý tưởng về cơn mưa Sài Gòn, vừa hài vừa mơ.")
store.save_brief(brief)
original = generate_concepts(brief, MockCreativeReasoner())[0]
store.save_concept(original)

# Optional explicit creative operation:
child = reframe_concept(
    original,
    lens="Điểm nhìn của chiếc ô",
    changes={"premise": "Chiếc ô muốn trời mưa mãi để tiếp tục chuyến đi."},
)
store.save_concept(child)
choice = store.select(child.concept_id, child.revision)

# A later process only needs this directory and the selection ID:
consumer_input = CreativeStore(".local/a03-example").load_selected(choice.selection_id)
```

This example only prepares local JSON input; it performs no A05 render.

## Observed verification

Environment: Windows `win32`, Python **3.14.7**, pytest **8.4.2**, jsonschema **4.26.0**. `setuptools` is not installed.

The initial ordinary pytest invocation stopped before collection when an unrelated auto-loaded plugin imported `_ssl` and encountered a DLL initialization error. After disabling plugin autoload, an interim run passed 81 tests and hit 11 temporary-directory setup errors. The final runs set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `TEMP`/`TMP` to `<checkout>/.local/a03-test-temp`. A reserved pytest parameter name found during authoring was also corrected before the final runs. No tests were skipped or marked xfail to obtain the results.

Because PowerShell could not start, Python wrappers set these environment variables and executed `pytest.__main__` through `runpy` with the exact module arguments below. The project arguments were loaded from the unchanged registered profile.

| Command / check | Observed final result |
| --- | --- |
| `python -m pytest tests/test_a03_creative.py -q` | **100 passed in 13.67s**, exit 0; before final registered run |
| `python -m pytest tests/test_a03_creative.py tests/test_a02_contracts.py tests/test_a02_mock_provider.py -q` | **130 passed in 22.21s**, exit 0 |
| Registered `project-tests`: `python -m pytest tests -q` | **246 passed, 16 failed in 26.84s**, exit 1 |
| Isolated source copy: package import, embedded A02 schema validation, generate/save/select/reload consumer input | Exit 0, `ISOLATED_COPY_IMPORT_SCHEMA_CONSUMER_PASS`; **not an installed-wheel test** |
| Build attempt in disposable source copy via `setuptools.build_meta.build_wheel` | `ModuleNotFoundError: No module named 'setuptools'`; **wheel build/install unverified** |
| AST parse and trailing-whitespace check of the six modules and A03 tests | PASS |
| `git diff --check` | Unavailable: process exit `-1073741502`; no Git PASS claimed |
| Read-only comparison against Git index | All **136** preexisting tracked entries unchanged, none deleted; new authored files within allowed paths |

The existing `pyproject.toml` already discovers packages under `src` and includes the A02 schema resources. No new runtime dependencies or packaging changes are needed by the source. Actual wheel installation must be rechecked in an environment with the declared `setuptools>=68` build dependency. No dependency downloads or installs were attempted.

### Registered-suite failure evidence

The 16 final failures occur in existing engineering tests, before the tested engineering behaviors can execute:

| Existing tests | Count | Observed cause |
| --- | --- | --- |
| `tests/test_ef01.py` | 12 | `git init` process exits `3221225794` |
| `tests/test_ef01_ef02_integrity.py` | 3 | Same `git init` process failure |
| `tests/test_ef02_m0r_isolated_runner_kit.py::test_powershell_control_script_parses_on_windows_without_execution` | 1 | PowerShell creation raises `WinError 1920: The file cannot be accessed by the system` |

Raw final suite output is retained locally at `.local/a03-project-tests.log` (ignored, not a committed evidence artifact). The same process-start limitations were observed outside pytest. No EF01/EF02 or environment repair was made. This result is a failed registered profile, even though all A03 and A02 tests pass.

### Acceptance evidence and limits

`tests/test_a03_creative.py` exercises product_ref null; Vietnamese and other Unicode; existing A02 JSON/custom fields; one/multiple distinct fixture families; explicit no-model behavior; independent adapter behavior; malformed input/metadata/provider results; factual uncertainty; stable IDs; JSON roundtrip; optional reframe lineage; frozen nested records; idempotence; stale/concurrent version conflicts; source/parent/selection tampering; pinned selection; and consumer reload without chat.

The WorkItem records source/test SHA-256 values from the final tested files. The index comparison normalizes CRLF to LF where required by the existing text checkout configuration; binary contents are unchanged. Python whitespace checks are fallback evidence and are not relabeled as `git diff --check`.

No live model, credential access, factual verification, third-party rights, publication, commercial outcome, external acceptance, candidate commit, merge or deployment is claimed. Mock tests verify contract behavior and fixture diversity; they do not establish creative quality or future real-model performance.
