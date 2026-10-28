"""Confirm A01 documentation and foundational contracts are distinct and present."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_adopted_governance_source_remains() -> None:
    for name in ("MANIFESTO.md", "CONSTITUTION.md", "PROJECT_CONTRACT.md"):
        assert (ROOT / name).is_file(), name


def test_existing_workitem_contract_is_preserved() -> None:
    import json
    schema = json.loads((ROOT / "contracts" / "WorkItem.v0.3.schema.json").read_text(encoding="utf-8"))
    assert {"work_id", "objective", "status"} <= set(schema["required"])


def test_a01_docs_have_semantic_truth_and_rollback() -> None:
    source = (ROOT / "docs/engineering/SEMANTIC_OWNERS.md").read_text(encoding="utf-8")
    assert all(label in source for label in (
        "Commerce Opportunity", "Product Intelligence", "Media & Creative",
        "Distribution", "Commerce Operations"
    ))
    adoption = (ROOT / "docs/engineering/SELECTIVE_COMPONENT_ADOPTION.md").read_text(encoding="utf-8")
    assert "rollback" in adoption.lower()
    assert "rights" in adoption.lower()
