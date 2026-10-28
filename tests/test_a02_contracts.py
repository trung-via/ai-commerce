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


@pytest.mark.parametrize("timestamp", [
    "someday",
    "2026-10-28",
    "2026-10-28T12:30:40",  # naive timestamp, timezone missing
    "2026-02-30T12:30:40Z",  # impossible calendar date
    "2026-10-28T25:30:40Z",  # impossible hour
    "2026-10-28T12:30:40+25:00",  # impossible offset
])
def test_invalid_timestamps_fail_even_without_optional_formats(timestamp: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from jsonschema import FormatChecker
    from ai_commerce.core.provenance import source_snapshot

    # Emulate a machine where the optional RFC3339 checker is absent.
    monkeypatch.delitem(FormatChecker.checkers, "date-time", raising=False)
    snap = source_snapshot(Path("sample.json"), b"{}"); snap["captured_at"] = timestamp
    with pytest.raises(ContractValidationError, match="captured_at"):
        validate_record("SourceSnapshot", snap)


@pytest.mark.parametrize("timestamp", [
    "2026-10-28T12:30:40Z",
    "2026-10-28T12:30:40+07:00",
    "2026-10-28t12:30:40.123z",
])
def test_valid_rfc3339_dates_still_pass(timestamp: str) -> None:
    from ai_commerce.core.provenance import source_snapshot
    snap = source_snapshot(Path("sample.json"), b"{}"); snap["captured_at"] = timestamp
    validate_record("SourceSnapshot", snap)


def test_evidence_ref_and_nested_job_reject_invalid_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    from jsonschema import FormatChecker
    from ai_commerce.core.provenance import source_snapshot

    monkeypatch.delitem(FormatChecker.checkers, "date-time", raising=False)
    snap = source_snapshot(Path("sample.json"), b"{}")
    evidence = {
        "evidence_id": "e1", "source_kind": "LOCAL_JSON_FILE", "source_ref": "sample.json",
        "observed_at": "not-a-date", "verification_status": "UNKNOWN", "rights_status": "UNKNOWN"
    }
    with pytest.raises(ContractValidationError, match="observed_at"):
        validate_record("EvidenceRef", evidence)
    snap["captured_at"] = "not-a-date"
    result = {
        "job_id": "j", "status": "SUCCEEDED", "provider": "local-mock",
        "source_snapshot": snap, "artifact": None, "evidence_refs": [],
        "cost_status": "UNKNOWN", "error_code": None,
    }
    with pytest.raises(ContractValidationError, match="captured_at"):
        validate_record("JobResult", result)
