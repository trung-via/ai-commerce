# AI Commerce

**Independent commerce intelligence project** · Affiliate learning → audience growth → own-brand decision support.

AI Commerce is an independent product and engineering repository. It is **not** Python Agent, AIOS-Renew, a submodule of either, or an inherited task/execution system.

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

The top-level **product** CLI still exposes foundation-only commands. A separate `python -m ai_commerce.engineering` CLI can invoke the manually selected Codex/Antigravity for local engineering changes, but it cannot produce product videos, access publishing accounts or publish content. See [A01 implementation evidence](docs/engineering/A01_IMPLEMENTATION_REPORT.md), [project state](docs/engineering/PROJECT_STATE.md) and [WorkItem record](work_items/A01-BOOTSTRAP-001.json). A01's 11 targeted tests passed in the Linux staging environment, and a Human-provided Windows PowerShell screenshot shows a separate **12 passed in 0.29s**, successful editable install and CLI help/hello. The exact Windows checkout HEAD and full interpreter version were not shown; no GitHub CI result is claimed. A02 is implemented and locally verified; **EF01 was subsequently verified for scope via Windows live Codex and Antigravity conformance screenshots (2026-10-28)**. EF02 remains unimplemented.

## A02 product contracts and mock provider (implemented)

A02 adds packaged JSON Schema 2020-12 definitions (`ContentBrief`, `SourceSnapshot`, `EvidenceRef`, `MediaArtifact`, `JobResult`) with `jsonschema` runtime validation, a provider-neutral product interface, and a **local text-only mock** with immutable content-digest references. This is **not** a production image/video generation provider or the future EF01 Codex/Antigravity executor.

```powershell
python -m pip install -e ".[dev]"
python -m ai_commerce validate --schema ContentBrief --file tests/fixtures/creative_only_brief.json
python -m ai_commerce mock-generate --brief tests/fixtures/creative_only_brief.json --out-dir .local/a02-smoke
python -m pytest tests -q
```

The mock creates a `.txt` artifact and a `.result.json` manifest under the ignored `.local/` directory. No network/spend/publishing is triggered, and unverified rights are `UNKNOWN`. Linux focused regression after the date-time validation fix observed **41 passed**. A previous Windows revision showed **30 passed / 1 failed** (missing date-time format validation). After the fix, a Human-provided Windows PowerShell screenshot shows **41 passed in 2.08s**. The exact local checkout SHA was not shown. Live provider checks remain unperformed. [A02 evidence](docs/engineering/A02_IMPLEMENTATION_REPORT.md) · [WorkItem](work_items/A02-CONTRACTS-001.json).

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
