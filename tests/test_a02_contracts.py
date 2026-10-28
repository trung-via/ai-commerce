"""Portable JSON contracts and negative-path validation for A02."""

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from ai_commerce.contracts.schema import (
    SCHEMAS, ContractValidationError, load_schema, validate_record,
)

ROOT = Path(__file__).resolve().parent


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schemas_available_after_install(name: str) -> None:
    assert load_schema(name)["title"] == name


def test_content_brief_can_be_noncommercial_and_freeform() -> None:
    brief = json.loads((ROOT / "fixtures/creative_only_brief.json").read_text(encoding="utf-8"))
    assert validate_record("ContentBrief", brief) is brief
    validate_record("ContentBrief", {**brief, "unusual_custom_field": {"abstract": True}})


def test_invalid_brief_reports_precise_error() -> None:
    invalid = json.loads((ROOT / "fixtures/invalid_brief.json").read_text(encoding="utf-8"))
    with pytest.raises(ContractValidationError, match="INVALID_ContentBrief"):
        validate_record("ContentBrief", invalid)


def test_unknown_schema_does_not_succeed() -> None:
    with pytest.raises(ContractValidationError, match="UNKNOWN_CONTRACT"):
        load_schema("made-up")


def test_wrong_record_type_does_not_succeed() -> None:
    with pytest.raises(ContractValidationError, match="expected JSON object"):
        validate_record("ContentBrief", ["invalid"])


def test_digest_and_time_format_are_checked() -> None:
    from ai_commerce.core.provenance import source_snapshot
    data = source_snapshot(Path("example.json"), b"sample")
    validate_record("SourceSnapshot", data)
    with pytest.raises(ContractValidationError, match="sha256"):
        validate_record("SourceSnapshot", {**data, "sha256": "not-a-sha"})
    with pytest.raises(ContractValidationError, match="captured_at"):
        validate_record("SourceSnapshot", {**data, "captured_at": "someday"})


def test_nested_jobresult_is_validated_not_just_top_level() -> None:
    from ai_commerce.core.provenance import source_snapshot
    snap = source_snapshot(Path("test.json"), b"{}"); snap["sha256"] = "invalid-sha"
    receipt = {
        "job_id": "j", "status": "SUCCEEDED", "provider": "mock", "source_snapshot": snap,
        "artifact": None, "evidence_refs": [], "cost_status": "UNKNOWN", "error_code": None,
    }
    with pytest.raises(ContractValidationError, match="sha256"):
        validate_record("JobResult", receipt)


def test_rights_status_unknown_is_not_granted() -> None:
    from ai_commerce.core.provenance import source_snapshot
    snap = source_snapshot(Path("test.json"), b"{}"); snap["rights_status"] = "UNVERIFIED_GRANTED"
    with pytest.raises(ContractValidationError, match="rights_status"):
        validate_record("SourceSnapshot", snap)
