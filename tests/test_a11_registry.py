"""A11-03 offline contract, provenance, revision and adversarial SQLite tests."""
from __future__ import annotations

import os
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path

import pytest

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.analytics.schemas import VERIFICATION
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.learning import LearningError, SnapshotPin, read_snapshot, seal_contract, validate_contract
from ai_commerce.learning import registry as module
from ai_commerce.learning.registry import LearningRegistry, hypothesis_context
from ai_commerce.learning.reporting import build_performance_report
from ai_commerce.learning.schemas import CONTRACT_IDS


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Learning registry attempted a network operation")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def snapshot_fixture(tmp_path, name="source", *, cohort="synthetic-cohort", account="private-source-account",
                     window=None, definition_version="fixture-1", rows=None, platform="TIKTOK"):
    plan = {
        "schema_version": "MeasurementPlan.v1", "plan_id": "a11-registry-fixture",
        "campaign_ref": "synthetic-campaign", "primary_metric": "plays", "denominator": None,
        "metric_definition_version": definition_version, "platform": platform, "surface": "VIDEO",
        "account_ref": {"namespace": "internal_alias", "id": account},
        "measurement_window": window or ["2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"],
        "timezone": "UTC+07:00", "cohort": cohort, "source_mode": "MANUAL_IMPORT",
        "attribution_tier": "NOT_AVAILABLE", "missingness_rule": "Missing is null, never zero",
        "source_status": VERIFICATION,
        "metric_definitions": [{
            "schema_version": "MetricDefinition.v1", "metric_key": "plays", "provider_name": "Synthetic plays",
            "definition_version": definition_version, "platform": platform, "unit": "count",
            "definition": "Synthetic descriptive count", "denominator": None, "cohort": cohort,
            "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE", "source_status": VERIFICATION,
        }],
    }
    rows = rows or [("private-post-id", "AVAILABLE", 0), ("negative-post", "AVAILABLE", -5),
                    ("missing-post", "NOT_AVAILABLE", None)]
    raw = canonical_bytes({"schema_version": "ManualAnalyticsRows.v1", "rows": [
        {"provider_post_id": post, "public_url": "https://example.test/private-user-post/" + post,
         "metric_key": "plays", "provider_name": "Synthetic plays", "definition_version": definition_version,
         "status": status, "value": value} for post, status, value in rows]})
    source = tmp_path / (name + ".json")
    source.write_bytes(raw)
    ledger = AnalyticsLedger(tmp_path / (name + "-a09.sqlite"))
    result = ledger.import_file(source, plan=plan, profile="tiktok.manual.v1" if platform == "TIKTOK" else "youtube.manual.v1",
                                file_format="json", source_kind="SYNTHETIC_FIXTURE")
    record = result["import"]
    pin = SnapshotPin(record["import_id"], record["context_sha256"], record["raw_sha256"],
                      tuple(item["observation_id"] for item in result["observations"]))
    return read_snapshot(ledger.path, pin=pin), ledger, source


@pytest.fixture
def source(tmp_path):
    return snapshot_fixture(tmp_path)


def reseal(name, value, **changes):
    field = CONTRACT_IDS[name][0]
    return seal_contract(name, {**{key: item for key, item in value.items() if key != field}, **changes})


def evidence_for(snapshot, hypothesis, index=0, *, role="SUPPORTING", state="AVAILABLE", report=None):
    observation = snapshot.to_dict()["observations"][index]
    return seal_contract("LearningEvidence", {
        "schema_version": "LearningEvidence.v1", "source_status": VERIFICATION,
        "hypothesis_ref": {"hypothesis_id": hypothesis["hypothesis_id"]},
        "observation_ref": snapshot.observation_ref(observation["observation_id"]),
        "report_ref": None if report is None else report.report_ref(), "role": role, "evidence_state": state,
        "observed_outcome": "Synthetic observed value; interpretation is advisory",
        "confounders": [], "limitations": ["Uncontrolled observation; no causal lift established"],
        "inference": "DESCRIPTIVE_ONLY",
    })


def packet(snapshot, *, index=0, role="SUPPORTING", state="AVAILABLE", status="SUPPORTED", report=None, key="lesson-1"):
    hypothesis = seal_contract("LearningHypothesis", {
        "schema_version": "LearningHypothesis.v1", "source_status": VERIFICATION,
        **hypothesis_context(snapshot), "statement": "The creative may be associated with a different count",
        "creative_ref": {"creative_id": "synthetic-creative", "version": "1", "content_sha256": "a" * 64},
        "expected_outcome": "A descriptive change in the synthetic metric", "uncertainty": "No controlled assignment",
        "confounders": [], "inference": "HYPOTHESIS_ONLY",
    })
    evidence = evidence_for(snapshot, hypothesis, index, role=role, state=state, report=report)
    record = seal_contract("LearningRecord", {
        "schema_version": "LearningRecord.v1", "source_status": VERIFICATION, "learning_key": key,
        "hypothesis_ref": evidence["hypothesis_ref"],
        "evidence_refs": [] if role == "COUNTEREVIDENCE" else [{"evidence_id": evidence["evidence_id"]}],
        "counterevidence_refs": [{"evidence_id": evidence["evidence_id"]}] if role == "COUNTEREVIDENCE" else [],
        "status": status, "uncertainty": "Synthetic association only, cannot establish a cause",
        "confounders": [], "inference": "ADVISORY_ONLY",
    })
    revision = seal_contract("LearningRevision", {
        "schema_version": "LearningRevision.v1", "source_status": VERIFICATION, "learning_key": key,
        "revision": 1, "previous_revision_ref": None, "record_ref": {"record_id": record["record_id"]},
        "operation": "CREATE", "reason": "Initial synthetic observation", "actor_ref": "human-alias",
        "recorded_at": "2026-10-30T00:00:00Z",
    })
    return {"hypothesis": hypothesis, "evidence": [evidence], "record": record, "revision": revision,
            "snapshots": [snapshot], "reports": [] if report is None else [report]}


def with_record(value, **changes):
    value = {**value, "record": reseal("LearningRecord", value["record"], **changes)}
    value["revision"] = reseal("LearningRevision", value["revision"], record_ref={"record_id": value["record"]["record_id"]})
    return value


def next_revision(value, previous, *, operation="UPDATE", timestamp="2026-10-30T01:00:00Z"):
    old = previous.to_dict()["revision"]
    return {**value, "revision": reseal("LearningRevision", value["revision"], revision=old["revision"] + 1,
        previous_revision_ref={"revision_id": old["revision_id"], "revision": old["revision"]},
        operation=operation, recorded_at=timestamp, reason="Human reviewed synthetic revision")}


def test_valid_append_has_exact_contracts_minimal_provenance_and_no_a09_writes(tmp_path, source, monkeypatch):
    snapshot, a09, raw_file = source
    a09_before, raw_before = Path(a09.path).read_bytes(), raw_file.read_bytes()
    value = packet(snapshot)
    reference = value["evidence"][0]["observation_ref"]
    confounder = {"code": "UNCONTROLLED", "description": "Uncontrolled assignment", "status": "DECLARED",
                  "evidence_refs": [reference]}
    value = with_record(value, confounders=[confounder])
    def forbidden(*args, **kwargs):
        pytest.fail("Registry must not open or write the A09 ledger")
    monkeypatch.setattr(AnalyticsLedger, "_connect", forbidden)
    registry = LearningRegistry(tmp_path / "a11.sqlite")
    entry = registry.append(**value)
    data = entry.to_dict()
    for name, key in (("LearningHypothesis", "hypothesis"), ("LearningRecord", "record"), ("LearningRevision", "revision")):
        assert validate_contract(name, data[key]) == value[key]
    assert data["record"]["confounders"] == [confounder]
    fact = data["observation_sources"][0]
    assert fact["observation_ref"] == reference
    assert fact["value"] == 0 and fact["status"] == "AVAILABLE"
    assert fact["metric"]["definition_version"] == "fixture-1"
    assert fact["metric"]["denominator"] is None
    assert fact["source"]["adapter_version"] == "manual-analytics.v1"
    assert fact["source"]["source_kind"] == "SYNTHETIC_FIXTURE"
    assert fact["reader_version"] == "a11-reader.v1"
    assert fact["snapshot_schema_version"] == "ObservationSnapshot.v1"
    assert fact["import_schema_version"] == "AnalyticsImport.v1"
    assert fact["observation_schema_version"] == "PerformanceObservation.v1"
    assert fact["context"] == hypothesis_context(snapshot)
    for secret in (b"private-source-account", b"private-post-id", b"private-user-post", b"provider_post_id", b"public_url"):
        assert secret not in entry.to_bytes()
    assert Path(a09.path).read_bytes() == a09_before and raw_file.read_bytes() == raw_before
    assert registry.get("lesson-1") == entry
    assert registry.inspect() == (entry,)
    before = (tmp_path / "a11.sqlite").read_bytes()
    assert LearningRegistry(tmp_path / "a11.sqlite").get("lesson-1", revision=1) == entry
    assert (tmp_path / "a11.sqlite").read_bytes() == before


def test_deterministic_idempotent_and_immutable(tmp_path, source):
    value = packet(source[0])
    first = LearningRegistry(tmp_path / "first.sqlite")
    entry = first.append(**value)
    before = (tmp_path / "first.sqlite").read_bytes()
    assert first.append(**value) == entry
    assert (tmp_path / "first.sqlite").read_bytes() == before
    assert LearningRegistry(tmp_path / "second.sqlite").append(**value) == entry
    detached = entry.to_dict()
    detached["record"]["uncertainty"] = "caller mutation"
    assert first.get("lesson-1").to_bytes() == entry.to_bytes()
    with pytest.raises(FrozenInstanceError):
        entry._raw = b"changed"
    value["record"]["uncertainty"] = "changed input"
    with pytest.raises(LearningError, match="CONTENT_ID_MISMATCH"):
        first.append(**value)
    assert first.get("lesson-1") == entry


def test_revision_keeps_negative_evidence_and_old_exact_retry(tmp_path, source):
    snapshot = source[0]
    value = packet(snapshot)
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    one = registry.append(**value)
    negative = evidence_for(snapshot, value["hypothesis"], index=1, role="COUNTEREVIDENCE")
    two_packet = with_record({**value, "evidence": value["evidence"] + [negative]},
                             status="MIXED", counterevidence_refs=[{"evidence_id": negative["evidence_id"]}])
    two_packet = next_revision(two_packet, one)
    two = registry.append(**two_packet)
    assert two.to_dict()["record"]["status"] == "MIXED"
    assert registry.history("lesson-1") == (one, two)
    assert registry.get("lesson-1") == two and registry.get("lesson-1", revision=1) == one
    assert registry.append(**value) == one
    removal = next_revision(value, two, timestamp="2026-10-30T02:00:00Z")
    with pytest.raises(LearningError, match="EVIDENCE_REMOVAL_REQUIRES_CORRECTION"):
        registry.append(**removal)
    correction = next_revision(with_record({**two_packet, "evidence": []}, evidence_refs=[], counterevidence_refs=[], status="PROPOSED"),
                               two, operation="CORRECTION", timestamp="2026-10-30T02:00:00Z")
    three = registry.append(**correction)
    assert three.to_dict()["revision"]["operation"] == "CORRECTION"
    assert registry.history("lesson-1")[1] == two
    retired = next_revision(with_record(correction, status="RETIRED"), three, operation="RETRACT",
                            timestamp="2026-10-30T03:00:00Z")
    four = registry.append(**retired)
    with pytest.raises(LearningError, match="RETIRED_LEARNING_IS_TERMINAL"):
        registry.append(**next_revision(correction, four, operation="CORRECTION", timestamp="2026-10-30T04:00:00Z"))
    assert LearningRegistry(registry.path).history("lesson-1") == (one, two, three, four)


@pytest.mark.parametrize("state,index,denominator", [("AVAILABLE", 0, None), ("NOT_AVAILABLE", 2, None),
                                                    ("INSUFFICIENT_DATA", 0, 0), ("NOT_COMPARABLE", 0, None)])
def test_zero_missing_insufficient_and_incompatible_remain_distinct(tmp_path, source, state, index, denominator):
    snapshot = source[0]
    report = build_performance_report(snapshot, denominator_value=denominator) if denominator is not None else None
    value = packet(snapshot, index=index, role="INCONCLUSIVE", state=state, status="INCONCLUSIVE", report=report)
    if state == "NOT_COMPARABLE":
        hypothesis = reseal("LearningHypothesis", value["hypothesis"], cohort="different-cohort")
        evidence = reseal("LearningEvidence", value["evidence"][0], hypothesis_ref={"hypothesis_id": hypothesis["hypothesis_id"]})
        value = with_record({**value, "hypothesis": hypothesis, "evidence": [evidence]},
                            hypothesis_ref=evidence["hypothesis_ref"], evidence_refs=[{"evidence_id": evidence["evidence_id"]}])
    entry = LearningRegistry(tmp_path / "learning.sqlite").append(**value).to_dict()
    assert entry["evidence"][0]["evidence_state"] == state
    facts = entry["observation_sources"]
    selected = next(f for f in facts if f["observation_ref"] == value["evidence"][0]["observation_ref"])
    assert selected["value"] == (None if state == "NOT_AVAILABLE" else 0)
    if report:
        measurement = entry["report_sources"][0]["body"]["measurement"]
        assert measurement["status"] == "INSUFFICIENT_DATA"
        assert measurement["value"] is None and measurement["denominator"] == 0
    assert entry["hypothesis"]["inference"] == "HYPOTHESIS_ONLY"
    assert entry["evidence"][0]["inference"] == "DESCRIPTIVE_ONLY"
    assert entry["record"]["inference"] == "ADVISORY_ONLY"


def test_negative_and_unobserved_outcomes_are_first_class(tmp_path, source):
    contradicted = packet(source[0], index=1, role="COUNTEREVIDENCE", status="CONTRADICTED")
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    assert registry.append(**contradicted).to_dict()["record"]["status"] == "CONTRADICTED"
    proposed = with_record({**packet(source[0], key="unobserved"), "evidence": []}, evidence_refs=[], status="PROPOSED")
    assert registry.append(**proposed).to_dict()["observation_sources"] == []
    assert len(registry.inspect()) == 2


@pytest.mark.parametrize("change", ["missing_as_zero", "unavailable_support", "wrong_snapshot", "wrong_metric_hash",
    "wrong_report", "wrong_hypothesis", "wrong_record", "missing_evidence", "extra_evidence", "duplicate_observation",
    "counter_as_support", "status_without_counter", "proposed_with_evidence", "missing_uncertainty", "causal_inference",
    "live_verification", "unhashed_account", "pii", "phone_actor", "blank_reason", "nonfinite", "unknown_version"])
def test_invalid_packets_do_not_create_database(tmp_path, source, change):
    snapshot = source[0]
    value = packet(snapshot)
    if change in {"missing_as_zero", "unavailable_support"}:
        value = packet(snapshot, index=2, state="AVAILABLE" if change == "missing_as_zero" else "NOT_AVAILABLE")
    elif change in {"wrong_snapshot", "wrong_metric_hash"}:
        ref = dict(value["evidence"][0]["observation_ref"])
        ref["snapshot_id" if change == "wrong_snapshot" else "metric_definition_sha256"] = (
            "a11-snapshot-" if change == "wrong_snapshot" else "") + "f" * 64
        evidence = reseal("LearningEvidence", value["evidence"][0], observation_ref=ref)
        value = with_record({**value, "evidence": [evidence]}, evidence_refs=[{"evidence_id": evidence["evidence_id"]}])
    elif change == "wrong_report":
        value["evidence"][0] = reseal("LearningEvidence", value["evidence"][0], report_ref={"report_id": "a11-report-" + "f" * 64})
        value = with_record(value, evidence_refs=[{"evidence_id": value["evidence"][0]["evidence_id"]}])
    elif change == "wrong_hypothesis":
        value = with_record(value, hypothesis_ref={"hypothesis_id": "a11-hypothesis-" + "f" * 64})
    elif change == "wrong_record":
        value["revision"] = reseal("LearningRevision", value["revision"], record_ref={"record_id": "a11-learning-" + "f" * 64})
    elif change == "missing_evidence":
        value["evidence"] = []
    elif change == "extra_evidence":
        value["evidence"] += [evidence_for(snapshot, value["hypothesis"], index=1)]
    elif change == "duplicate_observation":
        other = reseal("LearningEvidence", value["evidence"][0], observed_outcome="Another interpretation of same observation")
        value["evidence"] += [other]
        value = with_record(value, evidence_refs=value["record"]["evidence_refs"] + [{"evidence_id": other["evidence_id"]}])
    elif change == "counter_as_support":
        value = with_record(value, counterevidence_refs=value["record"]["evidence_refs"], evidence_refs=[])
    elif change == "status_without_counter":
        value = with_record(value, status="MIXED")
    elif change == "proposed_with_evidence":
        value = with_record(value, status="PROPOSED")
    elif change == "missing_uncertainty":
        value["record"].pop("uncertainty")
    elif change == "causal_inference":
        value["record"]["inference"] = "CAUSAL_LIFT"
    elif change == "live_verification":
        value["hypothesis"]["source_status"] = "LIVE_VERIFIED"
    elif change == "unhashed_account":
        value["hypothesis"] = reseal("LearningHypothesis", value["hypothesis"], account_ref={"namespace": "internal_alias", "id": "raw-person"})
    elif change == "pii":
        value["hypothesis"] = reseal("LearningHypothesis", value["hypothesis"], statement="Contact person@example.test")
    elif change == "phone_actor":
        value["revision"] = reseal("LearningRevision", value["revision"], actor_ref="0987654321")
    elif change == "blank_reason":
        value["revision"] = reseal("LearningRevision", value["revision"], reason="   ")
    elif change == "nonfinite":
        value["record"]["uncertainty"] = float("nan")
    else:
        value["hypothesis"]["schema_version"] = "LearningHypothesis.v2"
    if change in {"pii", "unhashed_account"}:
        evidence = reseal("LearningEvidence", value["evidence"][0], hypothesis_ref={"hypothesis_id": value["hypothesis"]["hypothesis_id"]})
        value = with_record({**value, "evidence": [evidence]}, hypothesis_ref=evidence["hypothesis_ref"],
                            evidence_refs=[{"evidence_id": evidence["evidence_id"]}])
    path = tmp_path / "learning.sqlite"
    with pytest.raises(LearningError):
        LearningRegistry(path).append(**value)
    assert not path.exists()


@pytest.mark.parametrize("change", ["stale_revision", "wrong_previous", "time_reversed", "create_again", "hypothesis_change", "retire_without_retract"])
def test_invalid_revision_transition_preserves_prior_history(tmp_path, source, change):
    value = packet(source[0])
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    one = registry.append(**value)
    updated = next_revision(value, one)
    if change == "stale_revision":
        updated["revision"] = reseal("LearningRevision", value["revision"], reason="Conflicting create replay")
    elif change == "wrong_previous":
        updated["revision"] = reseal("LearningRevision", updated["revision"], previous_revision_ref={"revision_id": "a11-revision-" + "f" * 64, "revision": 1})
    elif change == "time_reversed":
        updated["revision"] = reseal("LearningRevision", updated["revision"], recorded_at="2026-10-29T23:59:59Z")
    elif change == "create_again":
        updated["revision"]["operation"] = "CREATE"
    elif change == "hypothesis_change":
        hypothesis = reseal("LearningHypothesis", updated["hypothesis"], creative_ref={"creative_id": "synthetic-creative", "version": "2", "content_sha256": None})
        evidence = evidence_for(source[0], hypothesis)
        updated = with_record({**updated, "hypothesis": hypothesis, "evidence": [evidence]},
                              hypothesis_ref=evidence["hypothesis_ref"], evidence_refs=[{"evidence_id": evidence["evidence_id"]}])
    else:
        updated = with_record(updated, status="RETIRED")
    with pytest.raises(LearningError):
        registry.append(**updated)
    assert LearningRegistry(registry.path).inspect() == (one,)


def test_correction_can_version_hypothesis_and_creative_without_mutation(tmp_path, source):
    value = packet(source[0])
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    one = registry.append(**value)
    hypothesis = reseal("LearningHypothesis", value["hypothesis"], creative_ref={"creative_id": "synthetic-creative", "version": "2", "content_sha256": None})
    evidence = evidence_for(source[0], hypothesis)
    corrected = with_record({**value, "hypothesis": hypothesis, "evidence": [evidence]},
                            hypothesis_ref=evidence["hypothesis_ref"], evidence_refs=[{"evidence_id": evidence["evidence_id"]}])
    two = registry.append(**next_revision(corrected, one, operation="CORRECTION"))
    assert two.to_dict()["hypothesis"]["creative_ref"]["version"] == "2"
    assert registry.get("lesson-1", revision=1) == one


def test_database_triggers_block_update_delete_and_replace(tmp_path, source):
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    entry = registry.append(**packet(source[0]))
    with sqlite3.connect(registry.path) as db:
        for sql in ("UPDATE a11_events SET learning_key='changed'", "DELETE FROM a11_events",
                    "INSERT OR REPLACE INTO a11_events SELECT * FROM a11_events"):
            with pytest.raises(sqlite3.IntegrityError, match="A11_"):
                db.execute(sql)
        # A REPLACE with a new sequence must not bypass DELETE protection.
        row = list(db.execute("SELECT * FROM a11_events").fetchone())
        row[0], row[3], row[4], row[7], row[8] = 2, 2, entry.revision_id, entry.event_hash, "f" * 64
        with pytest.raises(sqlite3.IntegrityError, match="A11_INVALID_ORDER"):
            db.execute("INSERT OR REPLACE INTO a11_events VALUES(?,?,?,?,?,?,?,?,?)", row)
    assert registry.inspect() == (entry,)


def alter(db, statement, params=()):
    triggers = db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
    for name, _ in triggers:
        db.execute(f'DROP TRIGGER "{name}"')
    db.execute(statement, params)
    for _, sql in triggers:
        db.execute(sql)


@pytest.mark.parametrize("damage", ["payload", "rehashed_payload", "index", "chain", "missing_event", "drop_trigger", "extra_table",
    "application_id", "user_version", "not_sqlite", "invalid_json", "oversized_payload", "rehashed_bad_contract", "similar_internal_name"])
def test_corruption_is_rejected_on_read_and_write(tmp_path, source, damage):
    path = tmp_path / "learning.sqlite"
    registry = LearningRegistry(path)
    value = packet(source[0])
    one = registry.append(**value)
    registry.append(**next_revision(value, one))
    if damage == "not_sqlite":
        path.write_bytes(b"not a SQLite database")
    else:
        with sqlite3.connect(path) as db:
            if damage == "payload":
                alter(db, "UPDATE a11_events SET payload=zeroblob(length(payload)) WHERE seq=1")
            elif damage in {"rehashed_payload", "invalid_json", "rehashed_bad_contract"}:
                data = one.to_dict()
                data["record"]["uncertainty"] = "Changed without preserving identity"
                raw = b'{"duplicate":1,"duplicate":2}' if damage == "invalid_json" else canonical_bytes(data)
                if damage in {"rehashed_bad_contract", "invalid_json"}:
                    row = list(db.execute("SELECT * FROM a11_events WHERE seq=1").fetchone())
                    payload_hash = sha256(raw).hexdigest()
                    event_hash = module._event_hash(row[0], row[1], row[2], row[3], row[4], payload_hash, row[7])
                    alter(db, "UPDATE a11_events SET payload=?,payload_sha256=?,event_hash=? WHERE seq=1", (raw, payload_hash, event_hash))
                else:
                    alter(db, "UPDATE a11_events SET payload=?,payload_sha256=? WHERE seq=1", (raw, sha256(raw).hexdigest()))
            elif damage == "index":
                alter(db, "UPDATE a11_events SET learning_key='wrong-key' WHERE seq=1")
            elif damage == "chain":
                alter(db, "UPDATE a11_events SET previous_event_hash=? WHERE seq=2", ("f" * 64,))
            elif damage == "missing_event":
                alter(db, "DELETE FROM a11_events WHERE seq=1")
            elif damage == "drop_trigger":
                db.execute("DROP TRIGGER a11_no_update")
            elif damage == "extra_table":
                db.execute("CREATE TABLE unexpected(payload)")
            elif damage == "similar_internal_name":
                db.execute("CREATE TABLE sqliteXunexpected(payload)")
            elif damage == "oversized_payload":
                db.execute("PRAGMA ignore_check_constraints=ON")
                alter(db, "UPDATE a11_events SET payload=zeroblob(?) WHERE seq=1", (module.MAX_ENTRY_BYTES + 1,))
            else:
                db.execute(f"PRAGMA {damage}=2")
    before = path.read_bytes()
    for operation in (registry.inspect, lambda: registry.append(**value)):
        with pytest.raises(LearningError) as exc:
            operation()
        assert len(str(exc.value)) < 100
        assert path.read_bytes() == before


def test_transaction_rolls_back_after_insert_and_reopens(tmp_path, source, monkeypatch):
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    value = packet(source[0])
    first = registry.append(**value)
    read = registry._read
    def fail_after_insert(db):
        entries = read(db)
        if len(entries) == 2:
            raise LearningError("INJECTED_AFTER_INSERT")
        return entries
    updated = next_revision(value, first)
    with monkeypatch.context() as patch:
        patch.setattr(registry, "_read", fail_after_insert)
        with pytest.raises(LearningError, match="INJECTED_AFTER_INSERT"):
            registry.append(**updated)
    reopened = LearningRegistry(registry.path)
    assert reopened.inspect() == (first,)
    assert reopened.append(**updated).sequence == 2
    assert len(reopened.inspect()) == 2


def test_concurrent_exact_retry_and_conflicting_writers_are_serialized(tmp_path, source):
    path = tmp_path / "learning.sqlite"
    value = packet(source[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: LearningRegistry(path).append(**value), range(2)))
    assert results[0] == results[1]
    a = next_revision(value, results[0])
    b = next_revision(with_record(value, uncertainty="Different human interpretation"), results[0])
    def attempt(packet_value):
        try:
            return LearningRegistry(path).append(**packet_value)
        except LearningError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (a, b)))
    assert sum(isinstance(result, str) and result == "REVISION_CONFLICT" for result in results) == 1
    assert len(LearningRegistry(path).inspect()) == 2


@pytest.mark.parametrize("path", ["../escape.sqlite", "missing/../escape.sqlite", "//host/share/file.sqlite", "NUL", "file:bad?mode=rwc", "bad:stream"])
def test_unsafe_paths_are_rejected(tmp_path, source, path):
    unsafe = path if path.startswith("//") else tmp_path / path
    with pytest.raises(LearningError):
        LearningRegistry(unsafe).append(**packet(source[0]))


def test_a09_database_is_never_repurposed_as_registry(source):
    snapshot, ledger, _ = source
    before = Path(ledger.path).read_bytes()
    for action in (LearningRegistry(ledger.path).inspect, lambda: LearningRegistry(ledger.path).append(**packet(snapshot))):
        with pytest.raises(LearningError, match="REGISTRY_FORMAT_MISMATCH"):
            action()
        assert Path(ledger.path).read_bytes() == before


def test_symlinked_registry_or_sidecar_is_rejected(tmp_path, source):
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    registry.append(**packet(source[0]))
    link = tmp_path / "link.sqlite"
    try:
        link.symlink_to(registry.path)
    except (OSError, NotImplementedError):
        pytest.skip("OS did not permit creating a symlink fixture")
    with pytest.raises(LearningError):
        LearningRegistry(link).inspect()
    sidecar = Path(str(registry.path) + "-journal")
    sidecar.symlink_to(registry.path)
    with pytest.raises(LearningError):
        registry.append(**packet(source[0]))


def test_hardlinked_registry_rejected(tmp_path, source):
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    registry.append(**packet(source[0]))
    link = tmp_path / "hardlink.sqlite"
    try:
        os.link(registry.path, link)
    except OSError:
        pytest.skip("OS did not permit creating a hardlink fixture")
    with pytest.raises(LearningError, match="REGISTRY_FILE_UNSAFE"):
        registry.inspect()


def test_read_absence_and_query_bounds(tmp_path, source):
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    with pytest.raises(LearningError, match="REGISTRY_NOT_FOUND"):
        registry.inspect()
    assert not Path(registry.path).exists()
    registry.append(**packet(source[0]))
    for revision in (True, 0, -1, 1000001, "1"):
        with pytest.raises(LearningError, match="INVALID_REVISION_NUMBER"):
            registry.get("lesson-1", revision=revision)
    with pytest.raises(LearningError, match="LEARNING_REVISION_NOT_FOUND"):
        registry.get("lesson-1", revision=2)
    with pytest.raises(LearningError, match="INVALID_LEARNING_KEY"):
        registry.history("../bad")


@pytest.mark.parametrize("bound", ["evidence", "snapshots", "reports", "text", "depth"])
def test_bounded_inputs_fail_before_sqlite(tmp_path, source, bound):
    value = packet(source[0])
    if bound == "evidence":
        value["evidence"] *= 201
    elif bound == "snapshots":
        value["snapshots"] *= 17
    elif bound == "reports":
        value["reports"] = [{}] * 17
    elif bound == "text":
        value["hypothesis"]["statement"] = "a" * 4097
    else:
        nested = {}
        for _ in range(35):
            nested = {"nested": nested}
        value["record"]["uncertainty"] = nested
    path = tmp_path / "learning.sqlite"
    with pytest.raises(LearningError):
        LearningRegistry(path).append(**value)
    assert not path.exists()


def test_capacity_failure_rolls_back(tmp_path, source, monkeypatch):
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    value = packet(source[0])
    first = registry.append(**value)
    monkeypatch.setattr(module, "MAX_EVENTS", 1)
    with pytest.raises(LearningError, match="REGISTRY_CAPACITY_EXCEEDED"):
        registry.append(**next_revision(value, first))
    assert registry.inspect() == (first,)


def test_report_refs_and_versioned_metric_context_preserved(tmp_path):
    snapshot, _, _ = snapshot_fixture(tmp_path, definition_version="fixture-2")
    report = build_performance_report(snapshot)
    value = packet(snapshot, report=report)
    entry = LearningRegistry(tmp_path / "learning.sqlite").append(**value).to_dict()
    proof = entry["report_sources"][0]
    assert proof["report_ref"] == report.report_ref()
    assert proof["body"]["measurement"] == report.measurement
    assert proof["body"]["metric"]["definition_version"] == "fixture-2"
    assert proof["body"]["snapshot_refs"] == [snapshot.snapshot_ref()]
    assert proof["body"]["account_ref"] == hypothesis_context(snapshot)["account_ref"]
    forged = report.to_dict()
    forged["available_count"] += 1
    forged = reseal("PerformanceReport", forged)
    value["reports"] = [forged]
    with pytest.raises(LearningError, match="REPORT_COUNTS_MISMATCH"):
        LearningRegistry(tmp_path / "forged.sqlite").append(**value)


@pytest.mark.parametrize("field,new_value", [
    ("measurement_window", ["2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"]),
    ("timezone", "UTC"), ("cohort", None), ("account_ref", {"namespace": "internal_alias", "id": "other-account"}),
    ("metric", None),
])
def test_report_context_and_metric_version_cannot_be_relabelled(tmp_path, source, field, new_value):
    snapshot = source[0]
    report = build_performance_report(snapshot)
    body = report.to_dict()
    if field == "metric":
        new_value = {**body["metric"], "definition_version": "wrong-version", "denominator": "different-denominator"}
    forged = reseal("PerformanceReport", body, **{field: new_value})
    value = packet(snapshot, report=report)
    value["reports"] = [forged]
    with pytest.raises(LearningError, match="REPORT_SOURCE_CONTEXT_MISMATCH"):
        LearningRegistry(tmp_path / "learning.sqlite").append(**value)


def test_first_append_rollback_does_not_leave_a_partial_registry(tmp_path, source, monkeypatch):
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    value = packet(source[0])
    read = registry._read
    def fail_after_insert(db):
        entries = read(db)
        if entries:
            raise LearningError("INJECTED_FIRST_INSERT")
        return entries
    with monkeypatch.context() as patch:
        patch.setattr(registry, "_read", fail_after_insert)
        with pytest.raises(LearningError, match="INJECTED_FIRST_INSERT"):
            registry.append(**value)
    with sqlite3.connect(registry.path) as db:
        assert db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    assert LearningRegistry(registry.path).append(**value).sequence == 1


def test_confounded_or_missing_evidence_never_upgrades_to_support(tmp_path, source):
    value = packet(source[0], index=2, role="INCONCLUSIVE", state="NOT_AVAILABLE", status="INCONCLUSIVE")
    registry = LearningRegistry(tmp_path / "learning.sqlite")
    first = registry.append(**value)
    with pytest.raises(LearningError, match="UNSUPPORTED_STATUS"):
        registry.append(**next_revision(with_record(value, status="SUPPORTED"), first))
    bad_ref = {**value["evidence"][0]["observation_ref"], "metric_definition_sha256": "f" * 64}
    confounder = {"code": "UNRESOLVED", "description": "Unresolved source reference", "status": "UNKNOWN", "evidence_refs": [bad_ref]}
    with pytest.raises(LearningError, match="UNRESOLVED_OBSERVATION_REF"):
        registry.append(**next_revision(with_record(value, confounders=[confounder]), first))
    assert LearningRegistry(registry.path).inspect() == (first,)
