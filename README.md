# AI Commerce

**Independent commerce intelligence project** · Affiliate learning → audience growth → own-brand decision support.

AI Commerce is an independent product and engineering repository. It is **not** Python Agent, AIOS-Renew, a submodule of either, or an inherited task/execution system.

## Current Creative Factory status — 2026-10-29

**A06/E01 and milestone M1: VERIFIED FOR OFFLINE SCOPE.** [PR #71](https://github.com/trung-via/ai-commerce/pull/71) was merged into `main` at `377d3357`. [CI #40819125799](https://github.com/trung-via/ai-commerce/actions/runs/40819125799) verified the final code candidate: **Windows 859 passed; Ubuntu 858 passed, 1 skipped**. Both systems produced a playable H.264/yuv420p MP4 and uploaded complete E01 packages, including verified asset/order/scene/composition/render provenance and FFmpeg full decode.

```powershell
python -m ai_commerce creative-e2e run --prompt "A local original story" --asset .\scene.png --output .\.local\e01
python -m ai_commerce creative-e2e inspect --run .\.local\e01
python -m ai_commerce creative-e2e verify --run .\.local\e01 --full-decode
```

The accepted E01 path renders supplied local PNG/JPEG/WebP **still images**, not generated text-to-video; `generation_performed=false` and `publication_status=NOT_ATTEMPTED`. No social publishing, paid model, platform account or commercial outcome is implied. [A06 engineering evidence](docs/engineering/A06_E01_IMPLEMENTATION_REPORT.md).


**A03/A04 offline MVP evidence (2026-10-29):** The EF02 dependency integration candidate passed the final EF01 registered `project-tests` run (**365 passed, exit 0**) on Windows, after an earlier intra-executor run recorded 16 Git/PowerShell process-start failures already present in its baseline. See the [integration report](docs/engineering/A03_A04_INTEGRATION_REPORT.md) and [EF02 run #40735271832](https://github.com/trung-via/ai-commerce/actions/runs/40735271832). This paragraph is a historical A03/A04-only snapshot; current A06/M1 verification is recorded above.

## Foundation

| Document | Version | Role |
|---|---|---|
| [Manifesto](MANIFESTO.md) | Governance v0.3 | Vision & creative freedom |
| [Constitution](CONSTITUTION.md) | Governance v0.3 | Internal project authority & principles |
| [Project Contract](PROJECT_CONTRACT.md) | Governance v0.3 | Product scope, engineering interfaces and acceptance |
| [Blueprint v1.2](docs/blueprint/BLUEPRINT_V1_2_FULL_TEXT.md) | Research v1.2 | Detailed architecture, NOT evidence of running features |
| [Blueprint illustrated](docs/blueprint/AI_COMMERCE_BLUEPRINT_V1_2.docx) | Research v1.2 | Original illustrated study |
| [Human Adoption Record](docs/governance/HUMAN_ADOPTION_2026-10-27.md) | Decision | Approval and scope |

**Human is the supreme authority over internal project governance, including explicit exceptions.** Internal rules must not impose fixed creative formats or constrain media imagination. Human directives cannot manufacture external API permissions, licenses, platform receipts or legal exemptions.

## Product model

Two distinct product cycles share production and learning capabilities:

- **Audience Growth:** content/retention/community, does not require a product SKU or commerce CTA.
- **Affiliate Commerce:** product evidence, permission-appropriate CTA, conversion and settled economics.

20 capabilities are a strategic **capability map**, not 20 mandatory services or work items. MVP may be delivered as six lean work packages (source/access → brief → media → QA → authorized/assisted publish → evidence/economics). These remain product-design references, not a pre-authorized deployment or expenditure.

## Canonical implementation planning

- [Roadmap A — Creative-First v1.0](docs/roadmap/ROADMAP_A_CREATIVE_FIRST_V1_0.md) (six phases, A01–A18; Phase 0 includes EF01/EF02)
- [Work Tree v1.1 — Detailed](docs/roadmap/WORK_TREE_DETAILED_V1_1.md) / [JSON](docs/roadmap/WORK_TREE_V1_1.json) (63 planning IDs)
- [Human Decision — Engineering Flow](docs/governance/HUMAN_DECISION_2026-10-28_ENGINEERING_FLOW.md) (selective AIOS component reuse; dual Codex/Antigravity; no downstream Runtime)

The plan is Human-approved and repository-canonical. Planning adoption alone did **not** activate any implementation or provider account; A01/A02 and EF01 were implemented later, with bounded verification evidence recorded below. `EF01` / `EF02` are engineering-tooling planning items, not Commerce Product job runners.

## Application bootstrap — A01 (implemented)

The first installable Python foundation has been added without an AIOS Runtime dependency.

```powershell
python -m pip install -e .
python -m ai_commerce --help
python -m ai_commerce hello
python -m ai_commerce about --json
python -m pip install pytest
python -m pytest tests -q
```

The top-level **product** CLI includes A01/A02 commands and the offline A03/A04 candidate commands below. A separate `python -m ai_commerce.engineering` CLI can invoke the manually selected Codex/Antigravity for local engineering changes, but it cannot produce product videos, access publishing accounts or publish content. See [A01 implementation evidence](docs/engineering/A01_IMPLEMENTATION_REPORT.md), [project state](docs/engineering/PROJECT_STATE.md) and [WorkItem record](work_items/A01-BOOTSTRAP-001.json). A01's 11 targeted tests passed in the Linux staging environment, and a Human-provided Windows PowerShell screenshot shows a separate **12 passed in 0.29s**, successful editable install and CLI help/hello. The exact Windows checkout HEAD and full interpreter version were not shown; no GitHub CI result is claimed. A02 is implemented and locally verified; **EF01 was subsequently verified for scope via Windows live Codex and Antigravity conformance screenshots (2026-10-28)**. EF02 status is tracked separately in the project state.

## A02 product contracts and mock provider (implemented)

A02 adds packaged JSON Schema 2020-12 definitions (`ContentBrief`, `SourceSnapshot`, `EvidenceRef`, `MediaArtifact`, `JobResult`) with `jsonschema` runtime validation, a provider-neutral product interface, and a **local text-only mock** with immutable content-digest references. This is **not** a production image/video generation provider or the future EF01 Codex/Antigravity executor.

```powershell
python -m pip install -e ".[dev]"
python -m ai_commerce validate --schema ContentBrief --file tests/fixtures/creative_only_brief.json
python -m ai_commerce mock-generate --brief tests/fixtures/creative_only_brief.json --out-dir .local/a02-smoke
python -m pytest tests -q
```

The mock creates a `.txt` artifact and a `.result.json` manifest under the ignored `.local/` directory. No network/spend/publishing is triggered, and unverified rights are `UNKNOWN`. Linux focused regression after the date-time validation fix observed **41 passed**. A previous Windows revision showed **30 passed / 1 failed** (missing date-time format validation). After the fix, a Human-provided Windows PowerShell screenshot shows **41 passed in 2.08s**. The exact local checkout SHA was not shown. Live provider checks remain unperformed. [A02 evidence](docs/engineering/A02_IMPLEMENTATION_REPORT.md) · [WorkItem](work_items/A02-CONTRACTS-001.json).

## A03/A04 offline dependency integration (reviewed MVP)

The supplied parent implementations are preserved. The shared CLI now saves and inspects pinned concepts, applies explicit reframing edits, and associates a selected revision with a local import/mock asset job. An immutable handoff links the A03 selection to A04's validated manifest; `asset-inspect` reloads both stores and checks actual asset bytes.

~~~powershell
$created = python -m ai_commerce concept-create --text "Rain as a visual poem." --store .local/creative --mock | ConvertFrom-Json
$selectionId = $created.concepts[0].selection.selection_id
python -m ai_commerce concept-inspect --store .local/creative --selection-id $selectionId
$asset = python -m ai_commerce asset-run --store .local/creative --selection-id $selectionId --out-dir .local/assets --mode mock | ConvertFrom-Json
python -m ai_commerce asset-inspect --store .local/creative --out-dir .local/assets --handoff $asset.handoff_ref
~~~

`concept-create` also accepts `--brief FILE` containing UTF-8 prose or ContentBrief JSON. No product or CTA is required. `--mock` explicitly selects test templates; the media mock is a synthetic pixel fixture. For an existing PNG use `--mode import --source FILE --mime-type image/png`; the parent importer also supports its AVI container format. `concept-reframe --store DIR --selection-id ID --lens TEXT --changes FILE` saves caller-supplied JSON creative edits as a new pinned revision.

Focused regression in the Codex sub-session: **242 passed, 2 skipped**; its full registered profile recorded **347 passed, 16 failed, 2 skipped**, exit 1 with 16 unchanged Git/PowerShell startup failures. **Later, EF01's registered verification on the same candidate ran `python -m pytest tests -q` and returned 365 passed, exit 0 (78.73s).** The earlier environment failures are preserved as incident evidence, not claimed root-cause-fixed. All 30 new integration cases passed in the focused sub-session; native link checks there were skipped.

This implementation originated as a scoped candidate and is promoted through a separate Pull Request. The handoff is readable through existing contracts/loaders; it does not implement A05 or establish that a mock asset realizes the concept. No network/model credentials, external media generation or publishing are used. **This is a historical A03/A04-only snapshot; see the current A06/M1 evidence above.** See the [exact evidence, commands and limitations](docs/engineering/A03_A04_INTEGRATION_REPORT.md).

## Status and repository hygiene

- Governance v0.3: explicitly approved by Human in the conversation of **2026-10-27**. Original document headers retain the earlier DRAFT label for provenance; this adoption record supersedes those labels with respect to internal governance once initial commit is made.
- Blueprint v1.2: approved as the initial architecture reference, **not** as proof that publishing access, commissions, compliance or end-to-end code already exist.
- Roadmap A and Work Tree are canonical planning; **A01 bootstrap source is now implemented and locally verified**. EF01 manual local engineering CLI is implemented and live verified for scope; no EF02 remote dispatch, product automation, publishing authorization or GitHub Actions are enabled.
- No copied Python Agent / AIOS secrets, code, workflows or lifecycle were imported.
- Review the [open-evidence record](docs/research/DECISION_REGISTER_AS_RESEARCH_2026-10-27.md) before making live capability claims. Its old proposal statuses are historic research, not current repository activation status.

## Layout

```text
MANIFESTO.md
CONSTITUTION.md
PROJECT_CONTRACT.md
docs/blueprint/     # Blueprint v1.2 and visual reference
docs/governance/    # Human adoption and governance changelog
docs/research/      # Independent audit and open evidence
contracts/          # Draft JSON contract schemas
src/ai_commerce/    # Implemented A01 foundation-only Python package
tests/              # A01 targeted tests
docs/engineering/   # A01 project state, ADR and implementation evidence
work_items/         # Work item execution status records
```

Initial setup from this package: run `./BOOTSTRAP_GITHUB.ps1` in PowerShell from the extracted directory (requires authenticated `gh` and `git`).

## EF01 manual dual-executor core (Windows live verified for scope)

`src/ai_commerce/engineering/` provides a manual entrypoint: `python -m ai_commerce.engineering --help`. Human explicitly chooses the executor (`codex` or `antigravity`), exact model/effort, work/attempt, instruction file, targeted verification argv and allowed paths. Both adapters produce the same `AI_COMMERCE_ENGINEERING_RESULT`. No automatic switching, commit, push or EF02 remote wake. Full diagnostics are written only to the selected local output folder; token/cost values stay UNKNOWN unless provider-observed. **12/12 local synthetic-CLI tests PASS, plus separate Windows live attempts `codex-002` and `agy-002` both `BUILD_VERIFIED_FOR_SCOPE` / `EF01_EXIT=0`, based on Human-supplied screenshots.** See [EF01 evidence](docs/engineering/EF01_IMPLEMENTATION_REPORT.md) and [work item](work_items/EF01-DUAL-EXECUTOR-001.json).

Windows live EF01 conformance (one explicit provider per invocation): [PowerShell helper](scripts/EF01-Windows-Live-Conformance.ps1). Source tests cover UTF-8 BOM inputs from Windows PowerShell; 12/12 synthetic local tests passed. Both selected provider CLI routes ran successfully on Windows for the bounded EF01 acceptance task; underlying local logs/diff remain on that machine, and EF02 remains pending.

## EF02 Multi-Action — historic pre-activation planning snapshot (2026-10-28)

Human approved EF02 Multi-Action v1 on 2026-10-28: multiple Human/Brain actions in a batch, DAG dependencies, at least two truly concurrent independent Windows EF01 executor subprocesses initially (configurable), per-attempt claim/worktree/Git candidate and per-action plus aggregate feedback. EF02-A/B/C implementation remains pending; no GitHub remote workflow or project Windows runner currently proven active. See [Human decision](docs/governance/HUMAN_DECISION_2026-10-28_EF02_MULTI_ACTION.md), [implementation handoff](docs/engineering/EF02_MULTI_ACTION_IMPLEMENTATION_PLAN_V1_0_2026-10-28.md) and [WorkItem](work_items/EF02-MULTI-ACTION-001.json). M0-R stays OPEN; proactive Chat Wake requires separate proof.

## EF02 Multi-Action — historic source-only snapshot (2026-10-28)

Independent remote engineering implementation is in src/ai_commerce/remote/ with hosted ingress, separate Windows runner job, two-slot (configurable) parallel subprocess scheduling, DAG candidate composition, GitHub attempt claims, candidate refs and aggregate feedback. GitHub CI run 40648371883 passed on hosted Windows and Ubuntu. See docs/engineering/EF02_IMPLEMENTATION_REPORT.md and docs/engineering/EF02_WINDOWS_RUNNER_SETUP.md. The real self-hosted Windows runner and automatic remote feedback have not yet been tested; M0-R remains OPEN.

## A07 Channel Capability Foundation — verified offline scope (2026-10-29)

[PR #75](https://github.com/trung-via/ai-commerce/pull/75) merged an offline account×app×market×surface×action evidence-aware registry into `main@0e57caf9d23b0aae01627490a42f3de2068f95c8`. Use `python -m ai_commerce channel-capability --help` for local `validate/import/evaluate/history`. [A07 cross-platform CI](https://github.com/trung-via/ai-commerce/actions/runs/40824806875) passed 65 tests on Windows and Ubuntu; [A06 regression](https://github.com/trung-via/ai-commerce/actions/runs/40824806922) passed 897 Windows and 896 Ubuntu tests (1 skipped). No connected social accounts or real posting rights are implied. A08 assisted distribution and M2 remain unverified.

## A08 Assisted Distribution & Receipts — verified offline (2026-10-29)

[PR #78](https://github.com/trung-via/ai-commerce/pull/78) added offline E01-bound export, manual handoff and append-only user-supplied publication evidence journal (`main@8d02bd15613220c06bcfb67225622674d70b3ffc`). CLI commands: `python -m ai_commerce distribution export|inspect|handoff|event|status --help`. [A08 CI](https://github.com/trung-via/ai-commerce/actions/runs/40831199268) passed 60 affected tests per OS; [A06 full regression](https://github.com/trung-via/ai-commerce/actions/runs/40831199309) passed 907 Windows tests, 906 Ubuntu tests (1 skipped). This closes A08/D02-D04 and M2 for **offline assisted** scope only; neither provider posting nor public visibility has been established. A09 audience observation remains planned.
