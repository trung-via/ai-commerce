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

The plan is Human-approved and repository-canonical. It does **not** mean any implementation, executor, GitHub workflow, account, budget, media publication or commercial result has been activated. `EF01` / `EF02` are engineering-tooling planning items, not Commerce Product job runners.

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

The local CLI currently exposes **foundation-only** commands. It cannot dispatch Codex/Antigravity, produce videos, access provider accounts or publish content. See [A01 implementation evidence](docs/engineering/A01_IMPLEMENTATION_REPORT.md), [project state](docs/engineering/PROJECT_STATE.md) and [WorkItem record](work_items/A01-BOOTSTRAP-001.json). Tests ran in a local Linux/Python environment, **not** the user's Windows host; no GitHub CI result is claimed. A02/EF01/EF02 remain unimplemented.

## Status and repository hygiene

- Governance v0.3: explicitly approved by Human in the conversation of **2026-10-27**. Original document headers retain the earlier DRAFT label for provenance; this adoption record supersedes those labels with respect to internal governance once initial commit is made.
- Blueprint v1.2: approved as the initial architecture reference, **not** as proof that publishing access, commissions, compliance or end-to-end code already exist.
- Roadmap A and Work Tree are canonical planning; **A01 bootstrap source is now implemented and locally verified**. No executor dispatch, Engineering Flow, product automation, expenditure, user accounts, external actions or GitHub Actions are enabled.
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
