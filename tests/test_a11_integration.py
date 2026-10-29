"""Actual A11 fan-in: offline CLI, report/evidence consumers and advisory reload."""
from __future__ import annotations

import json
import socket
from dataclasses import FrozenInstanceError

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.cli import main
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.learning import (
    LearningAdvisory, LearningError, LearningRegistry, PerformanceReport, SnapshotPin, read_snapshot,
    advisory_schema, build_performance_report, export_advisory, seal_contract, validate_contract,
    write_advisory,
)
from ai_commerce.learning.core import MAX_DOCUMENT_BYTES, digest
from ai_commerce.learning.demo import _packet, _source, run_demo
from ai_commerce.learning.schemas import CONTRACT_IDS


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A11 integration attempted a network operation")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def invoke(capsys, *args, code=0):
    assert main(list(map(str, args))) == code
    captured = capsys.readouterr()
    if code:
        assert not captured.out
        return json.loads(captured.err)
    assert not captured.err
    return json.loads(captured.out)


def reseal(name, value, **changes):
    own_id = CONTRACT_IDS[name][0]
    return seal_contract(name, {**{key: item for key, item in value.items() if key != own_id}, **changes})


def seal_export(value):
    body = {key: item for key, item in value.items() if key != "export_id"}
    return {**body, "export_id": "a11-advisory-" + digest(body)}


def history(tmp_path):
    snapshot, a09 = _source(tmp_path, "input", [12, 0, None])
    report = build_performance_report(snapshot)
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    previous = None
    for _ in range(3):
        previous = registry.append(**_packet(snapshot, report, previous=previous),
                                   snapshots=[snapshot], reports=[report]).to_dict()
    return registry, snapshot, a09


def test_cli_snapshot_report_revisions_inspect_export_reload_and_upstream_unchanged(tmp_path, capsys):
    snapshot, a09 = _source(tmp_path, "input", [12, 0, None])
    before = a09.read_bytes()
    snapshot_value = invoke(capsys, "learning", "snapshot", "--db", a09, "--pin", tmp_path / "input-pin.json")
    assert snapshot_value == snapshot.to_dict()
    report_value = invoke(capsys, "learning", "report", "--snapshot", tmp_path / "input-snapshot.json")
    report = PerformanceReport(report_value)
    assert report.measurement_value == 12 and report.available_count == 2 and report.not_available_count == 1
    report_path = tmp_path / "report.json"
    report_path.write_bytes(report.to_bytes())
    previous = None
    for number in range(1, 4):
        packet = _packet(snapshot, report, previous=previous)
        packet_path = tmp_path / ("packet-" + str(number) + ".json")
        packet_path.write_bytes(canonical_bytes(packet))
        previous = invoke(capsys, "learning", "append", "--db", tmp_path / "learning.sqlite", "--file", packet_path,
                          "--snapshot", tmp_path / "input-snapshot.json", "--report", report_path)
    events = invoke(capsys, "learning", "inspect", "--db", tmp_path / "learning.sqlite")["entries"]
    assert [item["entry"]["record"]["status"] for item in events] == ["SUPPORTED", "MIXED", "INCONCLUSIVE"]
    first = invoke(capsys, "learning", "inspect", "--db", tmp_path / "learning.sqlite",
                   "--learning-key", "synthetic-opener", "--revision", 1)
    assert first["entries"] == events[:1]
    archive = tmp_path / "advisory.json"
    output = invoke(capsys, "learning", "export", "--db", tmp_path / "learning.sqlite", "--output", archive)
    assert invoke(capsys, "learning", "reload", "--file", archive, "--expected-id", output["export_id"]) == output
    assert output["entries"] == events and output["inference"] == "ADVISORY_ONLY"
    before_a11 = (tmp_path / "learning.sqlite").read_bytes()
    assert invoke(capsys, "learning", "export", "--db", tmp_path / "learning.sqlite") == output
    assert (tmp_path / "learning.sqlite").read_bytes() == before_a11
    assert a09.read_bytes() == before
    assert invoke(capsys, "analytics", "inspect", "--db", a09, "--import-id", snapshot.import_id)["import"]["import_id"] == snapshot.import_id
    assert main(["hello"]) == 0
    assert "foundation ready" in capsys.readouterr().out
    assert invoke(capsys, "about", "--json")["project"] == "AI Commerce"


def test_deterministic_synthetic_demo_and_golden_states(tmp_path):
    one = run_demo(tmp_path / "one")
    two = run_demo(tmp_path / "two")
    assert one == two
    assert (tmp_path / "one/advisory.json").read_bytes() == (tmp_path / "two/advisory.json").read_bytes()
    assert one["history_statuses"] == ["SUPPORTED", "MIXED", "INCONCLUSIVE"]
    assert one["states"]["zero"]["value"] == 0
    assert one["states"]["zero"]["status"] == "AVAILABLE"
    assert one["states"]["missing"]["value"] is None
    assert one["states"]["missing"]["status"] == "NOT_AVAILABLE"
    assert one["states"]["insufficient"]["status"] == "INSUFFICIENT_DATA"
    assert one["states"]["incompatible"] == "NOT_COMPARABLE"
    assert one["states"]["zero_baseline_relative"]["status"] == "INSUFFICIENT_DATA"
    assert one["a09_unchanged_after_bootstrap"] and one["reload_identical"]
    with pytest.raises(LearningError, match="DEMO_OUTPUT_ALREADY_EXISTS"):
        run_demo(tmp_path / "one")


def test_export_contracts_privacy_uncertainty_and_detached_reads(tmp_path):
    registry, snapshot, _ = history(tmp_path)
    advisory = export_advisory(registry)
    value = advisory.to_dict()
    Draft202012Validator.check_schema(advisory_schema())
    Draft202012Validator(advisory_schema()).validate(value)
    assert value["consumers"] == ["HUMAN", "BRAIN", "CREATIVE"]
    for event in value["entries"]:
        entry = event["entry"]
        for name, key in (("LearningHypothesis", "hypothesis"), ("LearningRecord", "record"), ("LearningRevision", "revision")):
            validate_contract(name, entry[key])
        for evidence in entry["evidence"]:
            validate_contract("LearningEvidence", evidence)
            assert evidence["report_ref"]["report_id"] == entry["report_sources"][0]["report_ref"]["report_id"]
        fact = entry["observation_sources"][0]
        assert fact["metric"]["definition_version"] == "synthetic-1"
        assert fact["source"]["source_kind"] == "SYNTHETIC_FIXTURE"
        assert fact["source"]["source_status"] == "USER_SUPPLIED_NOT_LIVE_VERIFIED"
        assert fact["context"]["measurement_window"] == snapshot.to_dict()["import"]["plan"]["measurement_window"]
        assert fact["context"]["timezone"] == "UTC+07:00"
        assert fact["metric"]["denominator"] is None
        assert entry["record"]["confounders"] and entry["record"]["uncertainty"]
    assert value["entries"][-1]["entry"]["record"]["counterevidence_refs"]
    for private in (b'"provider_post_id"', b'"public_url"', b'synthetic-post-', b'"id":"synthetic-account"', b'"raw_rows"'):
        assert private not in advisory.to_bytes()
    value["entries"].clear()
    assert advisory.to_dict()["event_count"] == 3
    with pytest.raises(FrozenInstanceError):
        advisory._raw = b"changed"


@pytest.mark.parametrize("mutation", ["extra", "version", "trust", "inference", "hash", "count", "head", "sequence",
                                     "reorder", "source", "counterevidence", "report", "revision", "privacy"])
def test_reload_rejects_tampering_even_with_rehashed_envelope(tmp_path, mutation):
    advisory = export_advisory(history(tmp_path)[0])
    value = advisory.to_dict()
    event = value["entries"][-1]
    entry = event["entry"]
    if mutation == "extra": value["publish"] = True
    elif mutation == "version": value["export_version"] = "a11-export.v2"
    elif mutation == "trust": value["source_status"] = "LIVE_VERIFIED"
    elif mutation == "inference": value["inference"] = "CAUSAL_LIFT"
    elif mutation == "hash": event["event_hash"] = "f" * 64
    elif mutation == "count": value["event_count"] = 2
    elif mutation == "head": value["head_event_hash"] = "f" * 64
    elif mutation == "sequence": event["sequence"] = True
    elif mutation == "reorder": value["entries"].reverse()
    elif mutation == "source": entry["observation_sources"][0]["metric"]["definition_version"] = "forged"
    elif mutation == "counterevidence": entry["record"] = reseal("LearningRecord", entry["record"], counterevidence_refs=[])
    elif mutation == "report": entry["report_sources"][0]["body"]["measurement"]["value"] = 999
    elif mutation == "revision": entry["revision"] = reseal("LearningRevision", entry["revision"], reason="changed")
    elif mutation == "privacy": entry["hypothesis"] = reseal("LearningHypothesis", entry["hypothesis"], statement="email person@example.test")
    with pytest.raises(LearningError):
        LearningAdvisory(seal_export(value))


def test_retained_export_pin_detects_valid_prefix_truncation(tmp_path):
    advisory = export_advisory(history(tmp_path)[0])
    value = advisory.to_dict()
    value["entries"].pop()
    value["event_count"] = 2
    value["head_event_hash"] = value["entries"][-1]["event_hash"]
    prefix = seal_export(value)
    assert LearningAdvisory(prefix).to_dict()["event_count"] == 2
    with pytest.raises(LearningError, match="ADVISORY_PIN_MISMATCH"):
        LearningAdvisory.from_bytes(canonical_bytes(prefix), expected_id=advisory.export_id)


@pytest.mark.parametrize("raw", [b'{"schema_version":1,"schema_version":2}', b'{"value":NaN}', b'[]', b'null',
                                  b'{"x":' + b'[' * 40 + b'0' + b']' * 40 + b'}', b'not-json'])
def test_malformed_reload_is_bounded_and_does_not_echo_input(tmp_path, capsys, raw):
    source = tmp_path / "private-file.json"
    source.write_bytes(raw)
    error = invoke(capsys, "learning", "reload", "--file", source, code=60)
    assert set(error) == {"error"}
    assert "private-file" not in json.dumps(error)


def test_oversized_reload_is_rejected_before_json_parsing():
    with pytest.raises(LearningError, match="DOCUMENT_EMPTY_OR_TOO_LARGE"):
        LearningAdvisory.from_bytes(b" " * (MAX_DOCUMENT_BYTES + 1))


def test_read_commands_never_create_or_overwrite_upstream(tmp_path, capsys):
    absent = tmp_path / "absent.sqlite"
    for operation in ("inspect", "export"):
        assert invoke(capsys, "learning", operation, "--db", absent, code=60)["error"] == "REGISTRY_NOT_FOUND"
        assert not absent.exists()
    registry, _, a09 = history(tmp_path)
    before = a09.read_bytes()
    error = invoke(capsys, "learning", "export", "--db", registry.path, "--output", a09, code=60)
    assert error["error"] == "ADVISORY_OUTPUT_FILE_ERROR"
    assert a09.read_bytes() == before
    for path in (tmp_path / ".." / "escape.json", tmp_path / "NUL", tmp_path / "missing-parent/output.json"):
        with pytest.raises(LearningError, match="ADVISORY_OUTPUT_FILE_ERROR"):
            write_advisory(path, export_advisory(registry))


def test_cli_comparison_pin_and_revision_negative_paths(tmp_path, capsys):
    run_demo(tmp_path / "demo")
    comparison = invoke(capsys, "learning", "compare", "--left", tmp_path / "demo/zero-report.json",
                        "--right", tmp_path / "demo/observations-report.json")
    assert comparison["absolute_change"]["value"] == 12
    assert comparison["relative_change"]["status"] == "INSUFFICIENT_DATA"
    assert invoke(capsys, "learning", "inspect", "--db", tmp_path / "demo/learning.sqlite", "--revision", 1,
                  code=60)["error"] == "LEARNING_KEY_REQUIRED"
    bad_pin = json.loads((tmp_path / "demo/observations-pin.json").read_bytes())
    bad_pin["raw_sha256"] = "f" * 64
    (tmp_path / "bad-pin.json").write_bytes(canonical_bytes(bad_pin))
    assert invoke(capsys, "learning", "snapshot", "--db", tmp_path / "demo/observations-a09.sqlite",
                  "--pin", tmp_path / "bad-pin.json", code=60)["error"] == "SNAPSHOT_CONTEXT_MISMATCH"


def test_declared_denominator_without_quantity_stays_insufficient(tmp_path):
    snapshot, _ = _source(tmp_path, "rate", [12], denominator="impressions")
    measurement = build_performance_report(snapshot).measurement
    assert measurement["status"] == "INSUFFICIENT_DATA"
    assert measurement["value"] is None and measurement["numerator"] == 12 and measurement["denominator"] is None


def test_registry_rejects_resealed_forged_report_arithmetic_before_writing(tmp_path):
    snapshot, _ = _source(tmp_path, "input", [12, 0, None])
    report = build_performance_report(snapshot)
    forged = reseal("PerformanceReport", report.to_dict(), measurement={**report.measurement, "value": 999})
    forged_report = PerformanceReport(forged)
    packet = _packet(snapshot, forged_report)
    destination = tmp_path / "forged.sqlite"
    with pytest.raises(LearningError, match="REPORT_ARITHMETIC_MISMATCH"):
        LearningRegistry(destination).append(**packet, snapshots=[snapshot], reports=[forged_report])
    assert not destination.exists()


@pytest.mark.parametrize("ambiguous", [False, True])
def test_source_denominator_uses_one_exact_definition_version(tmp_path, ambiguous):
    snapshot, _ = _source(tmp_path, "base", [12], denominator="impressions")
    plan = snapshot.to_dict()["import"]["plan"]
    versions = ["denom-1", "denom-2" if ambiguous else "denom-1"]
    for version in sorted(set(versions)):
        plan["metric_definitions"].append({**plan["metric_definitions"][0], "metric_key": "impressions",
            "provider_name": "Synthetic impressions", "unit": "count", "denominator": None,
            "definition_version": version})
    rows = [{"provider_post_id": "synthetic-numerator", "public_url": None, "metric_key": "plays",
             "provider_name": "Synthetic plays", "definition_version": "synthetic-1", "status": "AVAILABLE", "value": 12}]
    rows.extend({"provider_post_id": "synthetic-denom-" + str(index), "public_url": None,
                 "metric_key": "impressions", "provider_name": "Synthetic impressions", "definition_version": version,
                 "status": "AVAILABLE", "value": 100} for index, version in enumerate(versions))
    path = tmp_path / "denominators.json"
    path.write_bytes(canonical_bytes({"schema_version": "ManualAnalyticsRows.v1", "rows": rows}))
    ledger = AnalyticsLedger(tmp_path / "denominators.sqlite")
    imported = ledger.import_file(path, plan=plan, profile="tiktok.manual.v1", file_format="json", source_kind="SYNTHETIC_FIXTURE")
    record = imported["import"]
    pin = SnapshotPin(record["import_id"], record["context_sha256"], record["raw_sha256"],
                      tuple(item["observation_id"] for item in imported["observations"]))
    snapshot = read_snapshot(ledger.path, pin=pin)
    if ambiguous:
        with pytest.raises(LearningError, match="AMBIGUOUS_DENOMINATOR_DEFINITION"):
            build_performance_report(snapshot)
    else:
        assert build_performance_report(snapshot).measurement["value"] == 0.06
