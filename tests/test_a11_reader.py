"""A11-01 synthetic offline contracts, source isolation and adversarial reads."""
from __future__ import annotations

import json
import socket
import sqlite3
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.analytics import AnalyticsError, AnalyticsLedger, prepare_bytes
from ai_commerce.analytics.model import digest as a09_digest
from ai_commerce.analytics.profiles import mapping_profile
from ai_commerce.analytics.schemas import DEFINITIONS as A09_DEFINITIONS, VERIFICATION
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.learning import (
    EVIDENCE_STATES, LearningError, ObservationSnapshot, SnapshotPin, content_id,
    parse_contract, read_snapshot, schema, seal_contract, validate_contract,
)
from ai_commerce.learning import core
from ai_commerce.learning.schemas import CONTRACT_IDS

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A11 must not access the network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.fixture
def plan():
    return {
        "schema_version": "MeasurementPlan.v1", "plan_id": "a11-synthetic-plan",
        "campaign_ref": "synthetic-campaign", "primary_metric": "plays",
        "denominator": None, "metric_definition_version": "fixture-1",
        "platform": "TIKTOK", "surface": "VIDEO",
        "account_ref": {"namespace": "internal_alias", "id": "00001"},
        "measurement_window": ["2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"],
        "timezone": "UTC+07:00", "cohort": "synthetic-cohort",
        "source_mode": "MANUAL_IMPORT", "attribution_tier": "NOT_AVAILABLE",
        "missingness_rule": "NOT_AVAILABLE means null; no imputation", "source_status": VERIFICATION,
        "metric_definitions": [{
            "schema_version": "MetricDefinition.v1", "metric_key": "plays",
            "provider_name": "Synthetic plays", "definition_version": "fixture-1",
            "platform": "TIKTOK", "unit": "count", "definition": "Synthetic fixture count only",
            "denominator": None, "cohort": "synthetic-cohort",
            "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE", "source_status": VERIFICATION,
        }],
    }


def raw_rows():
    rows = [{"provider_post_id": post, "public_url": None, "metric_key": "plays",
             "provider_name": "Synthetic plays", "definition_version": "fixture-1",
             "status": status, "value": value}
            for post, status, value in (("zero", "AVAILABLE", 0), ("missing", "NOT_AVAILABLE", None))]
    return canonical_bytes({"schema_version": "ManualAnalyticsRows.v1", "rows": rows})


def import_fixture(tmp_path, plan, *, raw=None, profile="tiktok.manual.v1", source_kind="SYNTHETIC_FIXTURE"):
    raw = raw_rows() if raw is None else raw
    source = tmp_path / "synthetic.json"
    source.write_bytes(raw)
    ledger = AnalyticsLedger(tmp_path / "a09.sqlite")
    result = ledger.import_file(source, plan=plan, profile=profile, file_format="json", source_kind=source_kind)
    record = result["import"]
    pin = SnapshotPin(record["import_id"], record["context_sha256"], record["raw_sha256"],
                      tuple(item["observation_id"] for item in result["observations"]))
    return ledger, result, pin, source


@pytest.fixture
def imported(tmp_path, plan):
    return import_fixture(tmp_path, plan)


def read_import(imported):
    ledger, _, pin, _ = imported
    return read_snapshot(ledger.path, pin=pin)


def reseal(name, value):
    field = CONTRACT_IDS[name][0]
    return seal_contract(name, {key: item for key, item in value.items() if key != field})


def test_stable_complete_snapshot_golden_and_zero_missing(imported):
    ledger, result, pin, source = imported
    before_db, before_raw = Path(ledger.path).read_bytes(), source.read_bytes()
    snapshot = read_import(imported)
    value = snapshot.to_dict()
    assert value["import"] == result["import"]
    assert value["observations"] == result["observations"]
    assert value["pin"] == pin.to_dict()
    assert snapshot.import_id == pin.import_id
    assert snapshot.snapshot_id == content_id("ObservationSnapshot", value)
    assert snapshot.snapshot_id == "a11-snapshot-0861513a4f355cb839487edd12f54377ff44c0abdfd4fa9e3f7ba9b25a3e0e2f"
    assert [(item["status"], item["value"]) for item in value["observations"]] == [
        ("AVAILABLE", 0), ("NOT_AVAILABLE", None)]
    assert snapshot.to_bytes() == read_import(imported).to_bytes()
    assert ObservationSnapshot.from_bytes(snapshot.to_bytes()).to_bytes() == snapshot.to_bytes()
    assert Path(ledger.path).read_bytes() == before_db
    assert source.read_bytes() == before_raw
    assert not Path(str(ledger.path) + "-journal").exists()


def test_snapshots_are_immutable_and_refs_bind_exact_provenance(imported):
    snapshot = read_import(imported)
    value = snapshot.to_dict()
    observation = value["observations"][0]
    original_observation = deepcopy(observation)
    reference = snapshot.observation_ref(observation["observation_id"])
    assert reference == {**snapshot.snapshot_ref(), "observation_id": observation["observation_id"],
                         "observation_sha256": a09_digest(observation),
                         "metric_definition_sha256": a09_digest(observation["metric"])}
    value["observations"][0]["metric"]["definition"] = "caller changed a detached copy"
    assert snapshot.to_dict()["observations"][0] == original_observation
    assert snapshot.observation_ref(observation["observation_id"]) == reference
    with pytest.raises(FrozenInstanceError):
        snapshot._raw = b"changed"
    with pytest.raises(LearningError, match="OBSERVATION_NOT_IN_SNAPSHOT"):
        snapshot.observation_ref("obs-" + "f" * 64)
    with pytest.raises(LearningError, match="INVALID_OBSERVATION_ID"):
        snapshot.observation_ref("not-an-id")


def test_public_inspect_is_used_once_and_never_raw_or_write(imported, monkeypatch):
    ledger, _, pin, _ = imported
    inspected = []
    original = AnalyticsLedger.inspect
    def inspect(self, import_id):
        inspected.append(import_id)
        return original(self, import_id)
    def forbidden(*args, **kwargs):
        pytest.fail("A11 cannot open independent raw reads or mutate A09")
    monkeypatch.setattr(AnalyticsLedger, "inspect", inspect)
    for name in ("raw_bytes", "_store", "import_file", "reprocess"):
        monkeypatch.setattr(AnalyticsLedger, name, forbidden)
    read_snapshot(ledger.path, pin=pin)
    assert inspected == [pin.import_id]


def test_sqlite_connections_are_readonly_and_query_only(imported, monkeypatch):
    ledger, _, _, _ = imported
    connect = sqlite3.connect
    opened, reads = [], []
    def readonly(*args, **kwargs):
        assert "?mode=ro" in args[0] and kwargs["uri"] is True
        db = connect(*args, **kwargs)
        opened.append(db)
        writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                  sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE}
        def authorizer(action, first, second, database, trigger):
            assert action not in writes
            if action == sqlite3.SQLITE_READ:
                reads.append(first)
                assert db.execute("PRAGMA query_only").fetchone()[0] == 1
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorizer)
        return db
    monkeypatch.setattr(sqlite3, "connect", readonly)
    read_import(imported)
    assert len(opened) == 1
    assert {"imports", "raw_evidence", "observations"} <= set(reads)


@pytest.mark.parametrize("field,value", [
    ("import_id", "invalid"), ("import_id", "a09-" + "a" * 64 + "\n"),
    ("raw_sha256", "g" * 64), ("context_sha256", "0" * 63),
    ("observation_ids", ()), ("observation_ids", ("obs-invalid",)),
    ("observation_ids", ["obs-" + "a" * 64]), ("observation_ids", (None,)),
    ("observation_ids", ("obs-" + "a" * 64,) * 2),
    ("observation_ids", ("obs-" + "a" * 64,) * 10001),
])
def test_pin_rejects_invalid_duplicate_or_unbounded_ids(imported, field, value):
    with pytest.raises(LearningError):
        replace(imported[2], **{field: value})


@pytest.mark.parametrize("change", ["raw_sha256", "context_sha256", "missing", "extra", "reorder"])
def test_pin_mismatch_rejects_whole_snapshot(imported, change):
    ledger, _, pin, _ = imported
    if change in ("raw_sha256", "context_sha256"):
        pin = replace(pin, **{change: "f" * 64})
    elif change == "missing":
        pin = replace(pin, observation_ids=pin.observation_ids[:1])
    elif change == "extra":
        pin = replace(pin, observation_ids=(*pin.observation_ids, "obs-" + "f" * 64))
    else:
        pin = replace(pin, observation_ids=tuple(reversed(pin.observation_ids)))
    with pytest.raises(LearningError):
        read_snapshot(ledger.path, pin=pin)


def test_missing_import_or_database_never_creates_source(imported, tmp_path):
    ledger, _, pin, _ = imported
    with pytest.raises(LearningError) as error:
        read_snapshot(ledger.path, pin=replace(pin, import_id="a09-" + "f" * 64))
    assert isinstance(error.value.__cause__, AnalyticsError)
    missing = tmp_path / "absent-parent" / "no.sqlite"
    with pytest.raises(LearningError):
        read_snapshot(missing, pin=pin)
    assert not missing.parent.exists()
    with pytest.raises(LearningError, match="SNAPSHOT_PIN_REQUIRED"):
        read_snapshot(ledger.path, pin=None)


@pytest.mark.parametrize("platform,namespace,surface", [
    ("TIKTOK", "tiktok_open_id", "PROFILE_VIDEO"), ("YOUTUBE", "youtube_channel_id", "SHORT"),
    ("FACEBOOK", "facebook_page_id", "PAGE_REEL"), ("INSTAGRAM", "instagram_user_id", "REEL"),
])
def test_exact_namespaces_and_no_cross_platform_metric_equivalence(tmp_path, plan, platform, namespace, surface):
    plan.update(platform=platform, surface=surface, account_ref={"namespace": namespace, "id": "00001"})
    plan["metric_definitions"][0]["platform"] = platform
    source = import_fixture(tmp_path, plan, profile=platform.lower() + ".manual.v1")
    observation = read_import(source).to_dict()["observations"][0]
    assert all(observation[key] == plan[key] for key in (
        "platform", "surface", "account_ref", "measurement_window", "timezone", "cohort"))
    assert observation["metric"] == plan["metric_definitions"][0]
    assert observation["metric"]["comparison_policy"] == "NO_CROSS_PROVIDER_EQUIVALENCE"


@pytest.mark.parametrize("kind", ["SYNTHETIC_FIXTURE", "USER_AUTHORED_MANUAL", "USER_PROVIDED_PLATFORM_EXPORT"])
def test_user_declarations_never_upgrade_verification(tmp_path, plan, kind):
    snapshot = read_import(import_fixture(tmp_path, plan, source_kind=kind)).to_dict()
    assert snapshot["source_status"] == VERIFICATION
    assert snapshot["import"]["source_kind"] == kind
    observation = snapshot["observations"][0]
    assert observation["source"]["source_kind"] == kind
    assert observation["source_status"] == observation["metric"]["source_status"] == VERIFICATION
    assert observation["source"]["source_status"] == VERIFICATION


def test_csv_logical_provenance_and_bom(tmp_path, plan):
    from ai_commerce.analytics.schemas import ROW_FIELDS
    source = tmp_path / "synthetic.csv"
    source.write_bytes(b"\xef\xbb\xbf" + (",".join(ROW_FIELDS) +
                       "\r\nzero,,plays,Synthetic plays,fixture-1,AVAILABLE,0\r\n").encode())
    ledger = AnalyticsLedger(tmp_path / "a09.sqlite")
    result = ledger.import_file(source, plan=plan, profile="tiktok.manual.v1", file_format="csv",
                                source_kind="SYNTHETIC_FIXTURE")
    record = result["import"]
    pin = SnapshotPin(record["import_id"], record["context_sha256"], record["raw_sha256"],
                      tuple(item["observation_id"] for item in result["observations"]))
    value = read_snapshot(ledger.path, pin=pin).to_dict()
    assert value["import"]["dialect"] == "comma"
    assert value["observations"][0]["source"]["source_row"] == 2
    assert value["observations"][0]["source"]["source_row_semantics"] == "CSV_LOGICAL_RECORD_INCLUDING_HEADER"
    assert value["import"]["raw_sha256"] == sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize("changed", ["timezone", "measurement_window", "cohort", "account_ref", "definition", "mapping"])
def test_context_or_definition_change_requires_new_pin_and_snapshot(tmp_path, plan, changed):
    original = import_fixture(tmp_path, plan)
    before = read_import(original)
    revised = deepcopy(plan)
    profile = mapping_profile("tiktok.manual.v1")
    if changed == "timezone":
        revised["timezone"] = "UTC+00:00"
    elif changed == "measurement_window":
        revised["measurement_window"][1] = "2026-10-04T00:00:00+07:00"
    elif changed == "cohort":
        revised["cohort"] = revised["metric_definitions"][0]["cohort"] = None
    elif changed == "account_ref":
        revised["account_ref"]["namespace"] = "tiktok_open_id"
    elif changed == "definition":
        revised["metric_definitions"][0]["definition"] = "Changed declared counting semantics"
    else:
        profile["mapping_version"] = "fixture-2"
    after_import = import_fixture(tmp_path, revised, profile=profile)
    after = read_import(after_import)
    assert before.snapshot_id != after.snapshot_id
    assert before.to_dict()["import"]["raw_sha256"] == after.to_dict()["import"]["raw_sha256"]
    assert before.to_bytes() == read_import(original).to_bytes()
    with pytest.raises(LearningError):
        read_snapshot(after_import[0].path, pin=replace(original[2], import_id=after_import[2].import_id))


def test_definition_version_correction_and_reprocess_are_frozen(imported, plan, tmp_path):
    ledger, original, pin, source = imported
    before = read_import(imported)
    revised = deepcopy(plan)
    revised["metric_definition_version"] = revised["metric_definitions"][0]["definition_version"] = "fixture-2"
    profile = mapping_profile("tiktok.manual.v1")
    profile["mapping_version"] = "fixture-2"
    corrected_rows = json.loads(raw_rows())
    for item in corrected_rows["rows"]:
        item["definition_version"] = "fixture-2"
    newer = import_fixture(tmp_path, revised, raw=canonical_bytes(corrected_rows), profile=profile)
    assert read_import(newer).snapshot_id != before.snapshot_id
    # A09 versioned reprocess of original bytes is independent of later edits to the file.
    source.write_bytes(b"edited external file")
    replay = ledger.reprocess(pin.import_id, plan=plan, profile=profile)
    replay_pin = SnapshotPin(replay["import"]["import_id"], replay["import"]["context_sha256"], pin.raw_sha256,
                             tuple(item["observation_id"] for item in replay["observations"]))
    value = read_snapshot(ledger.path, pin=replay_pin).to_dict()
    assert value["import"]["lineage"] == {"relationship": "REPROCESS", "parent_import_id": pin.import_id}
    correction = tmp_path / "corrected.json"
    rows = json.loads(raw_rows())
    rows["rows"][0]["value"] = 3
    correction.write_bytes(canonical_bytes(rows))
    corrected = ledger.import_file(correction, plan=plan, profile="tiktok.manual.v1", file_format="json",
                                   source_kind="SYNTHETIC_FIXTURE", correction_of=pin.import_id)
    record = corrected["import"]
    correction_pin = SnapshotPin(record["import_id"], record["context_sha256"], record["raw_sha256"],
                                 tuple(item["observation_id"] for item in corrected["observations"]))
    assert read_snapshot(ledger.path, pin=correction_pin).to_dict()["import"]["lineage"]["relationship"] == "CORRECTION"
    assert before.to_bytes() == read_import(imported).to_bytes()


@pytest.mark.parametrize("mutation", [
    "raw_payload", "import_payload", "observation_payload", "rehashed_observation",
    "rehashed_import", "missing_raw", "missing_observation", "invalid_observation_id",
    "wrong_import_index", "extra_observation", "user_version", "application_id", "not_sqlite",
])
def test_tampered_sqlite_is_rejected_as_a_whole(imported, mutation):
    ledger, _, pin, _ = imported
    if mutation == "not_sqlite":
        Path(ledger.path).write_bytes(b"not a SQLite ledger")
    else:
        with sqlite3.connect(ledger.path) as db:
            for table in ("raw_evidence", "imports", "observations"):
                for operation in ("update", "delete"):
                    db.execute(f"DROP TRIGGER {table}_no_{operation}")
            if mutation.endswith("_payload"):
                table = {"raw_payload": "raw_evidence", "import_payload": "imports",
                         "observation_payload": "observations"}[mutation]
                db.execute(f"UPDATE {table} SET payload=zeroblob(length(payload))")
            elif mutation.startswith("rehashed_"):
                table = "observations" if mutation.endswith("observation") else "imports"
                raw = db.execute(f"SELECT payload FROM {table} LIMIT 1").fetchone()[0]
                value = json.loads(raw)
                if table == "observations":
                    value["value"] = 42
                else:
                    value["mapping"]["adapter_version"] = "manual-analytics.v2"
                raw = canonical_bytes(value)
                db.execute(f"UPDATE {table} SET payload=?,payload_sha256=?", (raw, sha256(raw).hexdigest()))
            elif mutation == "missing_raw":
                db.execute("DELETE FROM raw_evidence")
            elif mutation == "missing_observation":
                db.execute("DELETE FROM observations WHERE ordinal=2")
            elif mutation == "invalid_observation_id":
                db.execute("UPDATE observations SET observation_id='bad' WHERE ordinal=2")
            elif mutation == "wrong_import_index":
                db.execute("UPDATE imports SET context_sha256=?", ("f" * 64,))
            elif mutation == "extra_observation":
                db.execute("INSERT INTO observations VALUES(?,3,?,X'7B7D',?)",
                           (pin.import_id, "obs-" + "f" * 64, "0" * 64))
            else:
                db.execute(f"PRAGMA {mutation}=2")
    before = Path(ledger.path).read_bytes()
    with pytest.raises(LearningError) as error:
        read_snapshot(ledger.path, pin=pin)
    assert len(str(error.value)) < 100
    assert Path(ledger.path).read_bytes() == before


def test_all_or_nothing_uses_one_consistent_sqlite_transaction(imported, monkeypatch):
    ledger, _, pin, _ = imported
    expected = read_import(imported)
    original_raw = AnalyticsLedger._raw
    # Keep an independent WAL writer open; commit between the reader's import/raw
    # and observation reads. The first read must retain its original transaction.
    with sqlite3.connect(ledger.path) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("DROP TRIGGER observations_no_update")
        writer.commit()
        changed = []
        def interleave(db, raw_sha):
            raw = original_raw(db, raw_sha)
            if not changed:
                writer.execute("UPDATE observations SET payload=zeroblob(length(payload)) WHERE ordinal=2")
                writer.commit()
                changed.append(True)
            return raw
        monkeypatch.setattr(AnalyticsLedger, "_raw", staticmethod(interleave))
        assert read_snapshot(ledger.path, pin=pin).to_bytes() == expected.to_bytes()
        with pytest.raises(LearningError):
            read_snapshot(ledger.path, pin=pin)


@pytest.mark.parametrize("relative", ["../a09.sqlite", "missing/../a09.sqlite", "//host/share/a09.sqlite", "NUL"])
def test_unsafe_paths_rejected_without_creation(tmp_path, imported, relative):
    with pytest.raises(LearningError):
        read_snapshot(tmp_path / relative if not relative.startswith("//") else relative, pin=imported[2])


def test_symlinked_database_rejected(imported, tmp_path):
    link = tmp_path / "linked.sqlite"
    try:
        link.symlink_to(imported[0].path)
    except (OSError, NotImplementedError):
        pytest.skip("OS did not permit creating a symlink fixture")
    with pytest.raises(LearningError):
        read_snapshot(link, pin=imported[2])


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b"[]", b"\xff"])
def test_malformed_archive_json_is_bounded(raw):
    with pytest.raises(LearningError):
        ObservationSnapshot.from_bytes(raw)


@pytest.mark.parametrize("change", ["id", "value", "metric", "timezone", "window", "source", "duplicate", "version"])
def test_archive_tampering_even_with_recomputed_outer_hash(imported, change):
    value = read_import(imported).to_dict()
    observation = value["observations"][0]
    if change == "id":
        observation["observation_id"] = "obs-" + "f" * 64
    elif change == "value":
        observation["value"] = 5
    elif change == "metric":
        observation["metric"]["definition_version"] = "fixture-2"
    elif change == "timezone":
        observation["timezone"] = "UTC"
    elif change == "window":
        observation["measurement_window"].reverse()
    elif change == "source":
        observation["source"]["source_status"] = "LIVE_VERIFIED"
    elif change == "duplicate":
        value["observations"][1] = deepcopy(observation)
    else:
        value["reader_version"] = "a11-reader.v2"
    with pytest.raises(LearningError):
        reseal("ObservationSnapshot", value)


@pytest.mark.parametrize("change", ["context", "metric", "provenance", "window", "timezone"])
def test_rehashed_observation_still_requires_consistent_a09_context(imported, change):
    value = read_import(imported).to_dict()
    observation = value["observations"][0]
    if change == "context":
        observation["account_ref"]["id"] = "another"
    elif change == "metric":
        observation["metric"]["unit"] = "different unit"
    elif change == "provenance":
        observation["source"]["mapping_version"] = "different-mapping"
    elif change == "window":
        value["import"]["plan"]["measurement_window"].reverse()
    else:
        value["import"]["plan"]["timezone"] = "UTC+14:30"
    observation["observation_id"] = "obs-" + a09_digest({
        key: item for key, item in observation.items() if key != "observation_id"})
    value["pin"]["observation_ids"][0] = observation["observation_id"]
    with pytest.raises(LearningError):
        reseal("ObservationSnapshot", value)


def test_bounded_validation_before_schema_or_serialization(monkeypatch):
    with pytest.raises(LearningError, match="DOCUMENT_TEXT_LIMIT"):
        core.bounded_bytes({"a": "x" * 4097})
    value = {}
    value["a"] = value
    with pytest.raises(LearningError, match="DOCUMENT_COMPLEXITY_LIMIT"):
        core.bounded_bytes(value)
    with pytest.raises(LearningError):
        core.bounded_bytes({"a": float("nan")})
    with pytest.raises(LearningError):
        core.bounded_bytes({"a": 2 ** 1025})
    with pytest.raises(LearningError):
        core.bounded_bytes({1: "not a JSON key"})
    monkeypatch.setattr(core, "MAX_DOCUMENT_BYTES", 16)
    with pytest.raises(LearningError):
        core.bounded_bytes({"a": "x" * 17})
    with pytest.raises(LearningError):
        parse_contract("ObservationSnapshot", b"x" * 17)


def test_published_schemas_are_self_contained_exact_and_a09_unchanged():
    for name in CONTRACT_IDS:
        definition = schema(name)
        Draft202012Validator.check_schema(definition)
        assert json.loads((ROOT / f"contracts/{name}.v1.schema.json").read_bytes()) == definition
        pending = [definition]
        while pending:
            item = pending.pop()
            if isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, dict):
                if "$ref" in item:
                    assert item["$ref"].startswith("#/$defs/")
                pending.extend(item.values())
        for key, existing in A09_DEFINITIONS.items():
            if key in definition["$defs"]:
                assert definition["$defs"][key] == existing
        definition["properties"].clear()
        assert schema(name)["properties"]


def downstream_bodies(snapshot):
    value = snapshot.to_dict()
    observation = value["observations"][0]
    obs_ref = snapshot.observation_ref(observation["observation_id"])
    context = {key: observation[key] for key in (
        "platform", "surface", "account_ref", "measurement_window", "timezone", "cohort")}
    confounders = [{"code": "UNCONTROLLED_CREATIVE", "description": "No controlled assignment",
                    "status": "DECLARED", "evidence_refs": [obs_ref]}]
    base = {"source_status": VERIFICATION}
    measure = {"status": "AVAILABLE", "value": 0, "unit": "count", "numerator": None,
               "denominator": None, "reason": None}
    report = seal_contract("PerformanceReport", {
        **base, "schema_version": "PerformanceReport.v1", **context,
        "snapshot_refs": [snapshot.snapshot_ref()], "metric": observation["metric"],
        "evidence_refs": [obs_ref], "measurement": measure, "available_count": 1,
        "not_available_count": 0, "confounders": confounders, "caveats": ["Synthetic only"],
        "inference": "DESCRIPTIVE_ONLY",
    })
    hypothesis = seal_contract("LearningHypothesis", {
        **base, "schema_version": "LearningHypothesis.v1", **context,
        "statement": "Synthetic creative may be associated with a different count",
        "creative_ref": {"creative_id": "synthetic-creative", "version": "1", "content_sha256": None},
        "expected_outcome": "Observe a count", "uncertainty": "Uncontrolled observation",
        "confounders": confounders, "inference": "HYPOTHESIS_ONLY",
    })
    evidence = seal_contract("LearningEvidence", {
        **base, "schema_version": "LearningEvidence.v1", "observation_ref": obs_ref,
        "hypothesis_ref": {"hypothesis_id": hypothesis["hypothesis_id"]},
        "role": "COUNTEREVIDENCE", "evidence_state": "AVAILABLE", "observed_outcome": "Zero count",
        "confounders": confounders, "limitations": ["No causal conclusion"], "inference": "DESCRIPTIVE_ONLY",
    })
    record = seal_contract("LearningRecord", {
        **base, "schema_version": "LearningRecord.v1", "learning_key": "synthetic-learning",
        "hypothesis_ref": {"hypothesis_id": hypothesis["hypothesis_id"]}, "evidence_refs": [],
        "counterevidence_refs": [{"evidence_id": evidence["evidence_id"]}], "status": "INCONCLUSIVE",
        "uncertainty": "Zero alone does not establish a cause", "confounders": confounders,
        "inference": "ADVISORY_ONLY",
    })
    comparison = seal_contract("ComparisonResult", {
        **base, "schema_version": "ComparisonResult.v1",
        "left_report_ref": {"report_id": report["report_id"]},
        "right_report_ref": {"report_id": report["report_id"]}, "status": "NOT_COMPARABLE",
        "absolute_change": {**measure, "status": "NOT_COMPARABLE", "value": None, "reason": "Same report"},
        "relative_change": {**measure, "status": "INSUFFICIENT_DATA", "value": None, "reason": "Zero denominator"},
        "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE", "reason_codes": ["SAME_REPORT"],
        "confounders": confounders, "caveats": ["Illustrative contract only"], "inference": "DESCRIPTIVE_ONLY",
    })
    revision = seal_contract("LearningRevision", {
        **base, "schema_version": "LearningRevision.v1", "learning_key": "synthetic-learning",
        "revision": 1, "previous_revision_ref": None, "record_ref": {"record_id": record["record_id"]},
        "operation": "CREATE", "reason": "Synthetic fixture", "actor_ref": "synthetic-human",
        "recorded_at": "2026-10-29T00:00:00Z",
    })
    return {"PerformanceReport": report, "ComparisonResult": comparison,
            "LearningHypothesis": hypothesis, "LearningEvidence": evidence,
            "LearningRecord": record, "LearningRevision": revision}


def test_downstream_contracts_have_stable_references_and_content_ids(imported):
    values = downstream_bodies(read_import(imported))
    for name, value in values.items():
        assert validate_contract(name, value) == value
        assert parse_contract(name, canonical_bytes(value)) == value
        changed = deepcopy(value)
        changed["source_status"] = "LIVE_VERIFIED"
        with pytest.raises(LearningError):
            reseal(name, changed)
        changed = deepcopy(value)
        changed["unknown_field"] = True
        with pytest.raises(LearningError):
            reseal(name, changed)
        for field in schema(name)["required"]:
            changed = {key: item for key, item in value.items() if key != field}
            with pytest.raises(LearningError):
                validate_contract(name, changed)


@pytest.mark.parametrize("state", EVIDENCE_STATES)
def test_measurement_states_keep_zero_separate_from_absence(imported, state):
    value = downstream_bodies(read_import(imported))["PerformanceReport"]
    value["measurement"].update(status=state, value=0 if state == "AVAILABLE" else None,
                                 reason=None if state == "AVAILABLE" else "Explicit missing/incompatible data")
    reseal("PerformanceReport", value)
    value["measurement"]["value"] = None if state == "AVAILABLE" else 0
    with pytest.raises(LearningError):
        reseal("PerformanceReport", value)


def test_revision_contract_requires_explicit_previous_identity(imported):
    first = downstream_bodies(read_import(imported))["LearningRevision"]
    second = {**first, "revision": 2, "operation": "CORRECTION",
              "previous_revision_ref": {"revision_id": first["revision_id"], "revision": 1},
              "reason": "Correction preserves earlier event"}
    assert reseal("LearningRevision", second)["revision_id"] != first["revision_id"]
    second["previous_revision_ref"] = None
    with pytest.raises(LearningError):
        reseal("LearningRevision", second)
    first["operation"] = "UPDATE"
    with pytest.raises(LearningError):
        reseal("LearningRevision", first)


def test_content_identity_detects_changes_without_claiming_external_attestation(imported):
    value = read_import(imported).to_dict()
    value["observations"][0]["value"] = 1
    with pytest.raises(LearningError, match="CONTENT_ID_MISMATCH"):
        validate_contract("ObservationSnapshot", value)
    with pytest.raises(LearningError, match="UNKNOWN_LEARNING_CONTRACT"):
        validate_contract("Unregistered", {})
    with pytest.raises(LearningError):
        seal_contract("ObservationSnapshot", {"snapshot_id": "cannot overwrite"})
