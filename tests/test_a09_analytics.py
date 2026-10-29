"""A09 L01/L02 synthetic, offline contract/import/evidence regressions."""
from __future__ import annotations

import csv
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.analytics import (
    AnalyticsError, AnalyticsLedger, LedgerError, mapping_profile, prepare_bytes,
    validate_contract, validate_file, validate_plan,
)
from ai_commerce.analytics.files import MAX_FILE_BYTES, read_local
from ai_commerce.analytics.schemas import DEFINITIONS, ROW_FIELDS, VERIFICATION, schema
from ai_commerce.cli import main
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.distribution.model import provider_profiles

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def plan():
    return {
        "schema_version": "MeasurementPlan.v1", "plan_id": "synthetic-plan-1",
        "campaign_ref": "synthetic-campaign", "primary_metric": "plays",
        "denominator": None, "metric_definition_version": "fixture-1",
        "platform": "TIKTOK", "surface": "VIDEO",
        "account_ref": {"namespace": "internal_alias", "id": "synthetic-creator"},
        "measurement_window": ["2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"],
        "timezone": "UTC+07:00", "cohort": "synthetic-cohort",
        "source_mode": "MANUAL_IMPORT", "attribution_tier": "NOT_AVAILABLE",
        "missingness_rule": "NOT_AVAILABLE requires null; never impute zero",
        "settlement_lag_days": None, "cost_scope": None, "review_at": None,
        "evidence_refs": ["synthetic://fixture"], "source_status": VERIFICATION,
        "metric_definitions": [{
            "schema_version": "MetricDefinition.v1", "metric_key": "plays",
            "provider_name": "Synthetic user-declared plays", "definition_version": "fixture-1",
            "platform": "TIKTOK", "unit": "count", "definition": "Synthetic fixture count only",
            "denominator": None, "cohort": "synthetic-cohort",
            "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE", "source_status": VERIFICATION,
        }],
    }


def row(*, post="00001", value=0, status="AVAILABLE", **changes):
    result = {"provider_post_id": post, "public_url": None, "metric_key": "plays",
              "provider_name": "Synthetic user-declared plays", "definition_version": "fixture-1",
              "status": status, "value": value}
    result.update(changes)
    return result


def csv_raw(rows=None, *, delimiter=",", bom=False, headers=ROW_FIELDS):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, delimiter=delimiter)
    writer.writeheader()
    writer.writerows([row()] if rows is None else rows)
    return (b"\xef\xbb\xbf" if bom else b"") + stream.getvalue().encode("utf-8")


def json_raw(rows=None):
    return json.dumps({"schema_version": "ManualAnalyticsRows.v1", "rows": [row()] if rows is None else rows},
                      ensure_ascii=False, indent=2).encode("utf-8")


def save(tmp_path, raw, name="manual.csv"):
    path = tmp_path / name
    path.write_bytes(raw)
    return path


def do_import(ledger, path, plan, **kwargs):
    options = {"plan": plan, "profile": "tiktok.manual.v1", "file_format": "csv",
               "source_kind": "SYNTHETIC_FIXTURE", **kwargs}
    return ledger.import_file(path, **options)


def counts(path):
    with sqlite3.connect(path) as db:
        return tuple(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                     for table in ("raw_evidence", "imports", "observations"))


def test_schemas_published_locally_and_draft_preserved(plan):
    draft = json.loads((ROOT / "contracts/MeasurementPlan.v0.2.schema.json").read_bytes())
    assert "DRAFT / NOT CANONICAL" in draft["description"]
    assert set(draft["properties"]) <= set(schema("MeasurementPlan")["properties"])
    for name in DEFINITIONS:
        value = schema(name)
        Draft202012Validator.check_schema(value)
        assert json.loads((ROOT / f"contracts/{name}.v1.schema.json").read_bytes()) == value
    assert validate_plan(plan) == plan
    validate_contract("MetricDefinition", plan["metric_definitions"][0])


@pytest.mark.parametrize("field", ["account_ref", "platform", "surface", "measurement_window", "timezone",
                                   "denominator", "cohort", "metric_definitions", "source_status"])
def test_required_plan_context_fails_without_defaults(plan, field):
    del plan[field]
    with pytest.raises(AnalyticsError):
        prepare_bytes(csv_raw(), plan=plan, profile="tiktok.manual.v1", file_format="csv")


@pytest.mark.parametrize("changes", [
    {"account_ref": "creator"}, {"platform": "tiktok"}, {"surface": "PAGE_REEL"},
    {"account_ref": {"namespace": "youtube_channel_id", "id": "wrong"}},
    {"account_ref": {"namespace": "internal_alias", "id": "  "}},
    {"measurement_window": ["2026-10-01", "2026-10-02"]},
    {"measurement_window": ["2026-10-02T00:00:00Z", "2026-10-01T00:00:00Z"]},
    {"measurement_window": ["2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"]},
    {"timezone": ""}, {"timezone": "UTC+25:00"}, {"timezone": "UTC+14:30"},
    {"timezone": "Invented/Timezone"}, {"timezone": "../etc/localtime"},
    {"source_status": "PROVIDER_VERIFIED"}, {"denominator": "undeclared population"},
])
def test_bad_plan_context_is_bounded(plan, changes):
    plan.update(changes)
    with pytest.raises(AnalyticsError) as error:
        validate_plan(plan)
    assert len(str(error.value)) < 100


@pytest.mark.parametrize("platform,namespace,surface", [
    ("TIKTOK", "tiktok_open_id", "PROFILE_VIDEO"), ("YOUTUBE", "youtube_channel_id", "SHORT"),
    ("FACEBOOK", "facebook_page_id", "PAGE_REEL"), ("INSTAGRAM", "instagram_user_id", "REEL"),
])
def test_provider_metadata_preserves_a08_enums_and_identity(plan, platform, namespace, surface):
    plan.update(platform=platform, surface=surface, account_ref={"namespace": namespace, "id": "00001"})
    plan["metric_definitions"][0]["platform"] = platform
    profile = mapping_profile(platform.lower() + ".manual.v1")
    assert profile["native_export_layout_verified"] is False
    assert surface in provider_profiles()["platforms"][platform]["surfaces"]
    prepared = prepare_bytes(csv_raw(), plan=plan, profile=profile, file_format="csv")
    observation = prepared.observations[0]
    assert observation["account_ref"] == plan["account_ref"]
    assert observation["platform"] == platform and observation["surface"] == surface
    assert observation["source_status"] == VERIFICATION
    assert observation["metric"]["comparison_policy"] == "NO_CROSS_PROVIDER_EQUIVALENCE"


@pytest.mark.parametrize("dialect,delimiter", [("comma", ","), ("semicolon", ";"), ("tab", "\t")])
def test_utf8_bom_dialects_zero_null_and_logical_row_provenance(tmp_path, plan, dialect, delimiter):
    provider_name = 'Số lượt xem; "synthetic",\nmanual'
    plan["metric_definitions"][0]["provider_name"] = provider_name
    raw = csv_raw([row(provider_name=provider_name), row(post="00002", value=None, status="NOT_AVAILABLE",
                                                      provider_name=provider_name)], delimiter=delimiter, bom=True)
    path = save(tmp_path, raw)
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    result = do_import(ledger, path, plan, dialect=dialect)
    imported = result["import"]
    assert imported["status"] == "IMPORTED" and result["duplicate"] is False
    assert ledger.raw_bytes(imported["raw_sha256"]) == raw == path.read_bytes()
    assert imported["raw_sha256"] == sha256(raw).hexdigest()
    a, b = result["observations"]
    assert (a["status"], a["value"]) == ("AVAILABLE", 0)
    assert (b["status"], b["value"]) == ("NOT_AVAILABLE", None)
    assert a["provider_post_id"] == "00001"
    assert a["source"]["source_row"] == 2 and b["source"]["source_row"] == 3
    assert a["source"]["source_row_semantics"] == "CSV_LOGICAL_RECORD_INCLUDING_HEADER"
    assert a["measurement_window"] == plan["measurement_window"]
    assert a["timezone"] == plan["timezone"] and a["metric"]["provider_name"] == provider_name
    assert ledger.inspect(imported["import_id"]) == {k: v for k, v in result.items() if k != "duplicate"}
    for observation in result["observations"]:
        validate_contract("PerformanceObservation", observation)
    validate_contract("AnalyticsImport", imported)


def test_canonical_json_is_internal_and_raw_bytes_unchanged(tmp_path, plan):
    raw = b"\xef\xbb\xbf" + json_raw([row(), row(post=None, value=None, status="NOT_AVAILABLE")]) + b"\n"
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    result = do_import(ledger, save(tmp_path, raw), plan, file_format="json")
    assert ledger.raw_bytes(result["import"]["raw_sha256"]) == raw
    assert result["import"]["dialect"] is None
    assert [x["source"]["source_row"] for x in result["observations"]] == [1, 2]
    assert result["observations"][1]["value"] is None


def test_exact_replay_idempotent_but_contexts_independent(tmp_path, plan):
    path = save(tmp_path, csv_raw())
    db_path = tmp_path / "analytics.sqlite"
    ledger = AnalyticsLedger(db_path)
    original = do_import(ledger, path, plan)
    replay = do_import(ledger, path, plan)
    assert replay == {**original, "duplicate": True}
    ids = {original["import"]["import_id"]}
    for field, value in (("account_ref", {"namespace": "internal_alias", "id": "another"}),
                         ("measurement_window", ["2026-10-04T00:00:00Z", "2026-10-05T00:00:00Z"]),
                         ("timezone", "UTC")):
        other = deepcopy(plan)
        other[field] = value
        result = do_import(ledger, path, other)
        ids.add(result["import"]["import_id"])
        assert ledger.history(plan=other)["count"] == 1
    revised_mapping = mapping_profile("tiktok.manual.v1")
    revised_mapping["mapping_version"] = "user-revision-2"
    result = do_import(ledger, path, plan, profile=revised_mapping)
    ids.add(result["import"]["import_id"])
    assert len(ids) == 5 and counts(db_path) == (1, 5, 5)
    assert ledger.history(plan=plan)["count"] == 2
    first = ledger.history(plan=plan, limit=1)
    assert first["next_offset"] == 1
    assert ledger.history(plan=plan, limit=1, offset=1)["next_offset"] is None


def test_corrections_and_versioned_reprocess_preserve_original(tmp_path, plan):
    path = save(tmp_path, csv_raw())
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    original = do_import(ledger, path, plan)
    ident = original["import"]["import_id"]
    corrected_path = save(tmp_path, csv_raw([row(value=9)]), "correction.csv")
    corrected = do_import(ledger, corrected_path, plan, correction_of=ident)
    assert corrected["import"]["lineage"] == {"relationship": "CORRECTION", "parent_import_id": ident}
    assert corrected["observations"][0]["value"] == 9
    assert do_import(ledger, corrected_path, plan, correction_of=ident)["duplicate"] is True
    path.write_bytes(b"edited external file must not be used by reprocess")
    mapping = mapping_profile("tiktok.manual.v1")
    mapping["mapping_version"] = "replay-2"
    reprocessed = ledger.reprocess(ident, plan=plan, profile=mapping)
    assert reprocessed["import"]["lineage"] == {"relationship": "REPROCESS", "parent_import_id": ident}
    assert reprocessed["import"]["raw_sha256"] == original["import"]["raw_sha256"]
    assert reprocessed["observations"][0]["value"] == 0
    assert reprocessed["observations"][0]["source"]["mapping_version"] == "replay-2"
    assert ledger.reprocess(ident, plan=plan, profile=mapping)["duplicate"] is True
    assert ledger.inspect(ident)["import"] == original["import"]
    assert ledger.history(plan=plan)["count"] == 3
    assert counts(ledger.path) == (2, 3, 3)


def test_lineage_rejects_wrong_context_without_partial_write(tmp_path, plan):
    path = save(tmp_path, csv_raw())
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    ident = do_import(ledger, path, plan)["import"]["import_id"]
    with pytest.raises(AnalyticsError, match="CORRECTION_SOURCE_UNCHANGED"):
        do_import(ledger, path, plan, correction_of=ident)
    with pytest.raises(AnalyticsError, match="REPROCESS_CONTEXT_UNCHANGED"):
        ledger.reprocess(ident, plan=plan, profile="tiktok.manual.v1")
    revised = deepcopy(plan)
    revised["account_ref"]["id"] = "wrong-account"
    correction = save(tmp_path, csv_raw([row(value=10)]), "correction.csv")
    with pytest.raises(AnalyticsError, match="LINEAGE_SCOPE_MISMATCH"):
        do_import(ledger, correction, revised, correction_of=ident)
    with pytest.raises(AnalyticsError, match="LINEAGE_SCOPE_MISMATCH"):
        ledger.reprocess(ident, plan=revised, profile="tiktok.manual.v1")
    mapping = mapping_profile("tiktok.manual.v1")
    mapping["mapping_version"] = "2"
    with pytest.raises(AnalyticsError, match="CORRECTION_CONTEXT_MISMATCH"):
        do_import(ledger, correction, plan, profile=mapping, correction_of=ident)
    with pytest.raises(AnalyticsError, match="IMPORT_NOT_FOUND"):
        do_import(ledger, correction, plan, correction_of="a09-" + "f" * 64)
    assert counts(ledger.path) == (1, 1, 1)


def test_raw_import_and_observations_rollback_together(tmp_path, plan):
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    do_import(ledger, save(tmp_path, csv_raw()), plan)
    with sqlite3.connect(ledger.path) as db:
        db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON observations WHEN NEW.ordinal=2 "
                   "BEGIN SELECT RAISE(ABORT,'synthetic mid-transaction fault'); END")
    correction = save(tmp_path, csv_raw([row(value=8), row(post="second", value=4)]), "new.csv")
    with pytest.raises(LedgerError, match="LEDGER_DATABASE_ERROR"):
        do_import(ledger, correction, plan)
    assert counts(ledger.path) == (1, 1, 1)
    with sqlite3.connect(ledger.path) as db:
        db.execute("DROP TRIGGER fail_second")
    assert do_import(ledger, correction, plan)["duplicate"] is False
    assert counts(ledger.path) == (2, 2, 3)


def test_concurrent_exact_replays_commit_once(tmp_path, plan):
    from concurrent.futures import ThreadPoolExecutor
    path = save(tmp_path, csv_raw())
    database = tmp_path / "analytics.sqlite"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(do_import, AnalyticsLedger(database), path, plan) for _ in range(2)]
        results = [future.result() for future in futures]
    assert sorted(result["duplicate"] for result in results) == [False, True]
    assert len({result["import"]["import_id"] for result in results}) == 1
    assert counts(database) == (1, 1, 1)


@pytest.mark.parametrize("parent", ["", "not-an-import-id"])
def test_invalid_parent_id_cannot_silently_become_original(plan, parent):
    with pytest.raises(AnalyticsError, match="INVALID_PARENT_IMPORT_ID"):
        prepare_bytes(csv_raw(), plan=plan, profile="tiktok.manual.v1", file_format="csv", correction_of=parent)


@pytest.mark.parametrize("table", ["raw_evidence", "imports", "observations"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_ledger_tables_append_only(tmp_path, plan, table, operation):
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    do_import(ledger, save(tmp_path, csv_raw()), plan)
    sql = f"DELETE FROM {table}" if operation == "DELETE" else f"UPDATE {table} SET payload=payload"
    with sqlite3.connect(ledger.path) as db, pytest.raises(sqlite3.IntegrityError, match="A09_APPEND_ONLY"):
        db.execute(sql)
    assert counts(ledger.path) == (1, 1, 1)


@pytest.mark.parametrize("table", ["raw_evidence", "imports", "observations"])
def test_hash_tamper_detected_even_on_duplicate_retry(tmp_path, plan, table):
    path = save(tmp_path, csv_raw())
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    ident = do_import(ledger, path, plan)["import"]["import_id"]
    with sqlite3.connect(ledger.path) as db:
        db.execute(f"DROP TRIGGER {table}_no_update")
        previous = db.execute(f"SELECT payload FROM {table}").fetchone()[0]
        db.execute(f"UPDATE {table} SET payload=?", (b"x" + previous[1:],))
    with pytest.raises(LedgerError):
        ledger.inspect(ident)
    with pytest.raises(LedgerError):
        do_import(ledger, path, plan)


def test_missing_observation_detected(tmp_path, plan):
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    ident = do_import(ledger, save(tmp_path, csv_raw()), plan)["import"]["import_id"]
    with sqlite3.connect(ledger.path) as db:
        db.execute("DROP TRIGGER observations_no_delete")
        db.execute("DELETE FROM observations")
    with pytest.raises(LedgerError, match="OBSERVATION_COUNT_MISMATCH"):
        ledger.inspect(ident)


@pytest.mark.parametrize("raw", [
    b"", b"\xff\xfeBad encoding", b"anything,value\n1,0\n", b"value,value\n0,0\n",
    csv_raw([]), csv_raw() + b"missing,columns\n", csv_raw() + b'"unterminated\n',
    csv_raw() + b"\x00", csv_raw([row(value="=1+2")]), csv_raw([row(value="+SUM(1,2)")]),
    csv_raw([row(value="NaN")]), csv_raw([row(value="Infinity")]), csv_raw([row(value="1e999")]),
    csv_raw([row(value="true")]), csv_raw([row(value=None)]), csv_raw([row(value="N/A")]),
    csv_raw([row(value=0, status="NOT_AVAILABLE")]), csv_raw([row(status="UNKNOWN")]),
    csv_raw([row(provider_name="unknown provider name")]), csv_raw([row(definition_version="unapproved")]),
    csv_raw([row(public_url="http://example.invalid/1")]),
    csv_raw([row(public_url="https://user:password@example.invalid/1")]),
    csv_raw([row(public_url="https://example.invalid:bad/1")]),
    csv_raw([row(), row()]),
])
def test_malformed_csv_never_partially_imports_or_echoes_payload(tmp_path, plan, raw):
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    with pytest.raises(AnalyticsError) as error:
        do_import(ledger, save(tmp_path, raw), plan)
    assert len(str(error.value)) < 120
    assert not Path(ledger.path).exists()


@pytest.mark.parametrize("raw", [
    b'{"schema_version":', b'[]', b'{"rows":[],"rows":[]}', json_raw([]),
    b'{"schema_version":"Other","rows":[]}',
    json_raw([row(value="0")]), json_raw([row(value=None)]), json_raw([row(value=True)]),
    json_raw([row(value=0, status="NOT_AVAILABLE")]), json_raw([row(value=float("nan"))]),
    json_raw([row(value=float("inf"))]), json_raw([{"metric_key": "plays"}]),
    json_raw([[]]), json_raw([row(extra="unknown")]),
])
def test_malformed_canonical_json_fails_bounded(plan, raw):
    with pytest.raises(AnalyticsError) as error:
        prepare_bytes(raw, plan=plan, profile="tiktok.manual.v1", file_format="json")
    assert len(str(error.value)) < 100


def test_bounds_file_rows_and_fields(tmp_path, plan, monkeypatch):
    with pytest.raises(AnalyticsError, match="SOURCE_EMPTY_OR_TOO_LARGE"):
        validate_file(save(tmp_path, b"x" * (MAX_FILE_BYTES + 1)), plan=plan,
                      profile="tiktok.manual.v1", file_format="csv")
    from ai_commerce.analytics import importer
    monkeypatch.setattr(importer, "MAX_ROWS", 1)
    with pytest.raises(AnalyticsError, match="TOO_MANY_ROWS"):
        prepare_bytes(csv_raw([row(), row(post="2")]), plan=plan, profile="tiktok.manual.v1", file_format="csv")
    with pytest.raises(AnalyticsError, match="ROW_TOO_LARGE"):
        prepare_bytes(csv_raw([row(provider_name="x" * 4097)]), plan=plan,
                      profile="tiktok.manual.v1", file_format="csv")
    with pytest.raises(AnalyticsError, match="ROW_TOO_LARGE"):
        prepare_bytes(json_raw([row(provider_name="x" * 65536)]), plan=plan,
                      profile="tiktok.manual.v1", file_format="json")


def test_explicit_custom_mapping_and_ambiguous_headers(plan):
    mapping = mapping_profile("tiktok.manual.v1")
    mapping["profile_id"] = "user-authored.tiktok"
    mapping["mapping_version"] = "user-2026-10-29"
    mapping["columns"]["value"] = "User declared count"
    raw = csv_raw().replace(b",value\r\n", b",User declared count\r\n")
    assert prepare_bytes(raw, plan=plan, profile=mapping, file_format="csv").observations[0]["value"] == 0
    with pytest.raises(AnalyticsError, match="CSV_HEADER_MISMATCH"):
        prepare_bytes(raw, plan=plan, profile="tiktok.manual.v1", file_format="csv")
    mapping["columns"]["status"] = "User declared count"
    with pytest.raises(AnalyticsError, match="AMBIGUOUS_MAPPING_HEADER"):
        prepare_bytes(raw, plan=plan, profile=mapping, file_format="csv")


def test_metric_versions_and_denominators_are_not_flattened(plan):
    second = deepcopy(plan["metric_definitions"][0])
    second.update(provider_name="Different play definition", definition_version="fixture-2",
                  denominator="synthetic impressions", unit="percent")
    plan["metric_definitions"].append(second)
    rows = [row(), row(provider_name=second["provider_name"], definition_version="fixture-2", value=1.5)]
    prepared = prepare_bytes(json_raw(rows), plan=plan, profile="tiktok.manual.v1", file_format="json")
    assert [o["metric"]["denominator"] for o in prepared.observations] == [None, "synthetic impressions"]
    plan["metric_definitions"].append(deepcopy(second))
    with pytest.raises(AnalyticsError, match="AMBIGUOUS_METRIC_DEFINITION"):
        validate_plan(plan)


@pytest.mark.parametrize("rows", [
    [row(), row(public_url="https://example.invalid/same-post")],
    [row(public_url="https://example.invalid/post"), row(post="other", public_url="https://example.invalid/post")],
    [row(post=None), row(post=None)],
])
def test_duplicate_metric_subject_cannot_be_hidden_by_changed_url_or_post(plan, rows):
    with pytest.raises(AnalyticsError, match="AMBIGUOUS_DUPLICATE_OBSERVATION"):
        prepare_bytes(json_raw(rows), plan=plan, profile="tiktok.manual.v1", file_format="json")


def test_standalone_observation_contract_rejects_wrong_platform_identity_and_nonfinite(plan):
    observation = prepare_bytes(csv_raw(), plan=plan, profile="tiktok.manual.v1", file_format="csv").observations[0]
    wrong = deepcopy(observation)
    wrong["account_ref"] = {"namespace": "youtube_channel_id", "id": "wrong"}
    with pytest.raises(AnalyticsError):
        validate_contract("PerformanceObservation", wrong)
    wrong = deepcopy(observation)
    wrong["surface"] = "PAGE_REEL"
    with pytest.raises(AnalyticsError):
        validate_contract("PerformanceObservation", wrong)
    observation["value"] = float("nan")
    with pytest.raises(AnalyticsError):
        validate_contract("PerformanceObservation", observation)


def test_rehashed_parent_metadata_tamper_is_detected(tmp_path, plan):
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    parent = do_import(ledger, save(tmp_path, csv_raw()), plan)["import"]
    corrected = do_import(ledger, save(tmp_path, csv_raw([row(value=1)]), "correction.csv"),
                          plan, correction_of=parent["import_id"])["import"]
    parent["plan"]["plan_id"] = "tampered-but-rehashed"
    raw = canonical_bytes(parent)
    with sqlite3.connect(ledger.path) as db:
        db.execute("DROP TRIGGER imports_no_update")
        db.execute("UPDATE imports SET payload=?,payload_sha256=? WHERE import_id=?",
                   (raw, sha256(raw).hexdigest(), parent["import_id"]))
    with pytest.raises(LedgerError, match="IMPORT_IDENTITY_MISMATCH"):
        ledger.inspect(corrected["import_id"])


@pytest.mark.parametrize("path", ["../outside.csv", "dir/../outside.csv", r"dir\..\outside.csv",
                                   r"\\server\share\file.csv", r"\\.\NUL", "file.csv:alternate",
                                   "dir/.. /file.csv", "dir./file.csv", "NUL", "CON.csv", "COM1", "C:relative.csv"])
def test_unsafe_paths_rejected(path):
    with pytest.raises(AnalyticsError):
        read_local(path)


@pytest.mark.parametrize("parent", [False, True])
def test_real_symlink_inputs_and_ledgers_rejected(tmp_path, parent):
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = save(target_dir, b"original")
    link = tmp_path / "link"
    try:
        link.symlink_to(target_dir if parent else target, target_is_directory=parent)
    except OSError:
        pytest.skip("Host does not permit creating this symlink; no native symlink proof")
    unsafe = link / target.name if parent else link
    with pytest.raises(AnalyticsError, match="PATH_LINK_NOT_ALLOWED"):
        read_local(unsafe)
    with pytest.raises(AnalyticsError, match="PATH_LINK_NOT_ALLOWED"):
        AnalyticsLedger(unsafe).inspect("a09-" + "a" * 64)


def test_reparse_attribute_is_rejected_without_following_it(tmp_path, monkeypatch):
    path = save(tmp_path, b"fixture")
    original = Path.lstat
    class Reparse:
        st_mode = 0o100644
        st_file_attributes = 1024
    def lstat(item, *args, **kwargs):
        return Reparse() if item == path else original(item, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(AnalyticsError, match="PATH_LINK_NOT_ALLOWED"):
        read_local(path)


def test_foreign_database_and_missing_database_not_mutated(tmp_path, plan):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(AnalyticsError, match="LEDGER_NOT_FOUND"):
        AnalyticsLedger(missing).history(plan=plan)
    assert not missing.exists()
    path = tmp_path / "foreign.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated(value TEXT)")
    before = path.read_bytes()
    with pytest.raises(LedgerError, match="LEDGER_FORMAT_MISMATCH"):
        do_import(AnalyticsLedger(path), save(tmp_path, csv_raw()), plan)
    assert path.read_bytes() == before


def test_unsafe_sqlite_sidecar_rejected(tmp_path, plan):
    path = tmp_path / "analytics.sqlite"
    (tmp_path / "analytics.sqlite-journal").mkdir()
    with pytest.raises(AnalyticsError, match="LEDGER_SIDECAR_UNSAFE"):
        do_import(AnalyticsLedger(path), save(tmp_path, csv_raw()), plan)
    assert not path.exists()


def test_input_text_formulas_are_inert_and_no_spreadsheet_export(tmp_path, plan):
    raw = csv_raw([row(post="=HYPERLINK(\"https://example.invalid\")")])
    result = do_import(AnalyticsLedger(tmp_path / "analytics.sqlite"), save(tmp_path, raw), plan)
    assert result["observations"][0]["provider_post_id"].startswith("=HYPERLINK")
    assert set(p.name for p in tmp_path.iterdir()) == {"manual.csv", "analytics.sqlite"}


def test_all_operations_offline(tmp_path, plan, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Analytics attempted network access")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    path = save(tmp_path, csv_raw())
    validate_file(path, plan=plan, profile="tiktok.manual.v1", file_format="csv")
    item = do_import(ledger, path, plan)
    do_import(ledger, path, plan)
    ledger.inspect(item["import"]["import_id"])
    ledger.history(plan=plan)
    mapping = mapping_profile("tiktok.manual.v1")
    mapping["mapping_version"] = "2"
    ledger.reprocess(item["import"]["import_id"], plan=plan, profile=mapping)


def test_cli_validate_import_inspect_history_and_reprocess(tmp_path, plan, capsys):
    plan_path = save(tmp_path, canonical_bytes(plan), "plan.json")
    data_path = save(tmp_path, csv_raw())
    options = ["--plan", str(plan_path), "--profile", "tiktok.manual.v1"]
    source = ["--file", str(data_path), "--format", "csv", "--source-kind", "SYNTHETIC_FIXTURE"]
    assert main(["analytics", "validate", *options, *source]) == 0
    assert json.loads(capsys.readouterr().out)["import"]["status"] == "VALIDATED"
    db_path = tmp_path / "analytics.sqlite"
    assert not db_path.exists()
    database = ["--db", str(db_path)]
    assert main(["analytics", "import", *options, *source, *database]) == 0
    first = json.loads(capsys.readouterr().out)
    ident = first["import"]["import_id"]
    assert main(["analytics", "import", *options, *source, *database]) == 0
    assert json.loads(capsys.readouterr().out)["duplicate"] is True
    assert main(["analytics", "inspect", *database, "--import-id", ident]) == 0
    assert json.loads(capsys.readouterr().out)["import"] == first["import"]
    assert main(["analytics", "history", *database, "--plan", str(plan_path)]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1
    mapping = mapping_profile("tiktok.manual.v1")
    mapping["mapping_version"] = "cli-2"
    mapping_path = save(tmp_path, canonical_bytes(mapping), "mapping.json")
    assert main(["analytics", "import", "--plan", str(plan_path), "--mapping", str(mapping_path),
                 *database, "--reprocess-of", ident]) == 0
    assert json.loads(capsys.readouterr().out)["import"]["lineage"]["relationship"] == "REPROCESS"


@pytest.mark.parametrize("command", [[], ["validate"], ["import"], ["inspect"], ["history"]])
def test_cli_consistent_help(command, capsys):
    with pytest.raises(SystemExit) as error:
        main(["analytics", *command, "--help"])
    assert error.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_cli_error_codes_are_bounded(tmp_path, plan, capsys):
    plan_path = save(tmp_path, canonical_bytes(plan), "plan.json")
    data_path = save(tmp_path, b"do-not-echo-this-private-input\n", "bad.csv")
    args = ["analytics", "import", "--plan", str(plan_path), "--profile", "tiktok.manual.v1",
            "--file", str(data_path), "--format", "csv", "--db", str(tmp_path / "new.sqlite")]
    assert main(args) == 40
    error = capsys.readouterr()
    assert not error.out and len(error.err) < 160 and "private-input" not in error.err
    database = save(tmp_path, b"not a database", "corrupt.sqlite")
    assert main(["analytics", "inspect", "--db", str(database), "--import-id", "a09-" + "0" * 64]) == 50
    assert json.loads(capsys.readouterr().err)["error"] == "LEDGER_DATABASE_ERROR"
    with pytest.raises(SystemExit) as usage:
        main(["analytics", "import"])
    assert usage.value.code == 2


def test_module_cli_runs_outside_repository_without_schema_file_lookup(tmp_path, plan):
    plan_path = save(tmp_path, canonical_bytes(plan), "plan.json")
    data = save(tmp_path, json_raw(), "manual.json")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-m", "ai_commerce", "analytics", "validate", "--plan", str(plan_path),
                             "--profile", "tiktok.manual.v1", "--file", str(data), "--format", "json"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["import"]["status"] == "VALIDATED"
