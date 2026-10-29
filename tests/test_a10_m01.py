"""A10 M01 Signal Intake Foundation: offline, provider-neutral trend signal intake regressions."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.opportunity.core.models import (
    OpportunityError,
    SignalLedgerError,
    SignalValidationError,
    digest,
    parse_json,
    validate_contract,
    validate_signal,
    validate_source_snapshot,
    validate_timezone,
    validate_window,
)
from ai_commerce.opportunity.core.schemas import DEFINITIONS, RIGHTS_STATUSES, SOURCE_STATUS, schema
from ai_commerce.opportunity.signals.files import MAX_FILE_BYTES, MAX_ROW_BYTES, MAX_ROWS
from ai_commerce.opportunity.signals.importer import (
    PreparedSignalImport,
    load_mapping,
    load_source_snapshot,
    prepare_bytes,
)
from ai_commerce.opportunity.signals.ledger import SignalLedger
from ai_commerce.opportunity.signals.profiles import (
    GOOGLE_TRENDS_COLUMNS,
    SIGNAL_ROW_FIELDS,
    mapping_profile,
    validate_mapping,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def snapshot():
    return {
        "schema_version": "SignalSourceSnapshot.v1",
        "source_id": "google-trends-vn-coffee",
        "source_kind": "USER_PROVIDED_EXPORT",
        "source_ref": "sample://coffee_trends_202610.csv",
        "captured_at": "2026-10-29T10:00:00+07:00",
        "collected_at": "2026-10-29T10:00:00+07:00",
        "sha256": "0" * 64,
        "rights_status": "UNKNOWN",
        "verification_status": SOURCE_STATUS,
        "jurisdiction": "VN",
        "geo": "VN",
        "locale": "vi-VN",
        "purpose": "Trend intake for creative opportunity testing",
        "terms_url": None,
        "notes": "User declared export; no provider verification",
    }


def row(
    *,
    topic="ca phe muoi",
    signal_type="TOPIC",
    source_type="GOOGLE_TRENDS",
    locale="vi-VN",
    geo="VN",
    query_context="CATEGORY_FOOD_AND_DRINK",
    observed_at="2026-10-01T00:00:00+07:00",
    collected_at="2026-10-29T10:00:00+07:00",
    timezone="UTC+07:00",
    unit="index",
    denominator=None,
    status="AVAILABLE",
    value=75,
    rights_status="UNKNOWN",
    **changes,
):
    item = {
        "topic": topic,
        "signal_type": signal_type,
        "source_type": source_type,
        "locale": locale,
        "geo": geo,
        "query_context": query_context,
        "observed_at": observed_at,
        "collected_at": collected_at,
        "timezone": timezone,
        "unit": unit,
        "denominator": denominator,
        "status": status,
        "value": value,
        "rights_status": rights_status,
    }
    item.update(changes)
    return item


def csv_raw(rows=None, *, delimiter=",", bom=False, headers=SIGNAL_ROW_FIELDS):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, delimiter=delimiter)
    writer.writeheader()
    raw_rows = [row()] if rows is None else rows
    for r in raw_rows:
        writer.writerow({k: ("" if v is None else str(v)) for k, v in r.items() if k in headers})
    encoded = stream.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" if bom else b"") + encoded


def json_raw(rows=None):
    raw_rows = [row()] if rows is None else rows
    envelope = {
        "schema_version": "ManualSignalRows.v1",
        "rows": raw_rows,
    }
    return json.dumps(envelope, ensure_ascii=False, indent=2).encode("utf-8")


# ---------------------------------------------------------------------------
# 1. Contract & Schema Validation Tests
# ---------------------------------------------------------------------------


def test_schemas_published_and_draft202012_valid():
    for name in ("SignalSourceSnapshot", "TrendSignal", "SignalImport"):
        contract_schema = schema(name)
        Draft202012Validator.check_schema(contract_schema)
        published_path = ROOT / f"contracts/{name}.v1.schema.json"
        assert published_path.is_file(), f"Missing schema file: {published_path}"
        published = json.loads(published_path.read_bytes())
        assert published == contract_schema


def test_source_snapshot_validation_happy(snapshot):
    validated = validate_source_snapshot(snapshot)
    assert validated["source_id"] == "google-trends-vn-coffee"
    assert validated["verification_status"] == SOURCE_STATUS


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "SignalSourceSnapshot.v2"),
        ("source_id", ""),
        ("source_id", "invalid space"),
        ("captured_at", "not-a-datetime"),
        ("sha256", "not-a-sha"),
        ("sha256", "0" * 63),
        ("rights_status", "INVALID_RIGHTS"),
        ("verification_status", "PROVIDER_VERIFIED_AUTHENTIC"),
    ],
)
def test_source_snapshot_validation_negative(snapshot, field, value):
    snapshot[field] = value
    with pytest.raises(SignalValidationError):
        validate_source_snapshot(snapshot)


# ---------------------------------------------------------------------------
# 2. Zero vs NOT_AVAILABLE / null Distinction (AC03)
# ---------------------------------------------------------------------------


def test_zero_distinct_from_null_contract():
    valid_zero = {
        "schema_version": "TrendSignal.v1",
        "signal_id": "sig-" + "a" * 64,
        "import_id": "a10-" + "b" * 64,
        "ordinal": 1,
        "topic": "test",
        "signal_type": "TOPIC",
        "source_type": "GOOGLE_TRENDS",
        "locale": "vi-VN",
        "geo": "VN",
        "query_context": "GENERAL",
        "observed_at": "2026-10-01T00:00:00+07:00",
        "observation_window": None,
        "collected_at": "2026-10-02T00:00:00+07:00",
        "timezone": "UTC+07:00",
        "unit": "index",
        "denominator": None,
        "status": "AVAILABLE",
        "value": 0,
        "source_status": SOURCE_STATUS,
        "rights_status": "UNKNOWN",
        "evidence_refs": ["sample://ref"],
    }
    assert validate_signal(valid_zero)["value"] == 0

    valid_float_zero = deepcopy(valid_zero)
    valid_float_zero["value"] = 0.0
    assert validate_signal(valid_float_zero)["value"] == 0.0

    invalid_available_null = deepcopy(valid_zero)
    invalid_available_null["value"] = None
    with pytest.raises(SignalValidationError):
        validate_signal(invalid_available_null)

    valid_not_available = deepcopy(valid_zero)
    valid_not_available["status"] = "NOT_AVAILABLE"
    valid_not_available["value"] = None
    assert validate_signal(valid_not_available)["value"] is None

    invalid_not_available_zero = deepcopy(valid_zero)
    invalid_not_available_zero["status"] = "NOT_AVAILABLE"
    invalid_not_available_zero["value"] = 0
    with pytest.raises(SignalValidationError):
        validate_signal(invalid_not_available_zero)


def test_csv_zero_vs_not_available(snapshot):
    rows = [
        row(topic="zero_search", status="AVAILABLE", value=0),
        row(topic="missing_search", status="NOT_AVAILABLE", value=""),
    ]
    raw = csv_raw(rows)
    snapshot["sha256"] = sha256(raw).hexdigest()
    prepared = prepare_bytes(raw, source_snapshot=snapshot, file_format="csv")
    assert len(prepared.signals) == 2
    assert prepared.signals[0]["status"] == "AVAILABLE"
    assert prepared.signals[0]["value"] == 0
    assert isinstance(prepared.signals[0]["value"], int)
    assert prepared.signals[1]["status"] == "NOT_AVAILABLE"
    assert prepared.signals[1]["value"] is None


def test_csv_not_available_with_value_fails(snapshot):
    rows = [row(topic="bad_missing", status="NOT_AVAILABLE", value=0)]
    raw = csv_raw(rows)
    snapshot["sha256"] = sha256(raw).hexdigest()
    with pytest.raises(SignalValidationError) as exc:
        prepare_bytes(raw, source_snapshot=snapshot, file_format="csv")
    assert "NOT_AVAILABLE_REQUIRES_EMPTY_VALUE" in str(exc.value)


def test_csv_available_with_empty_value_fails(snapshot):
    rows = [row(topic="bad_available", status="AVAILABLE", value="")]
    raw = csv_raw(rows)
    snapshot["sha256"] = sha256(raw).hexdigest()
    with pytest.raises(SignalValidationError) as exc:
        prepare_bytes(raw, source_snapshot=snapshot, file_format="csv")
    assert "AVAILABLE_REQUIRES_NUMERIC_VALUE" in str(exc.value)


# ---------------------------------------------------------------------------
# 3. Explicit Configurable Column Mapping Tests
# ---------------------------------------------------------------------------


def test_csv_import_with_custom_mapping(snapshot):
    custom_profile = {
        "profile_id": "custom.radar.v1",
        "mapping_version": "1",
        "adapter_version": "manual-signal.v1",
        "native_export_layout_verified": False,
        "columns": {
            "topic": "TuKhoa",
            "observed_at": "NgayQuanSat",
            "status": "TrangThai",
            "value": "ChiSo",
            "source_type": "KenhNguon",
            "locale": "NgonNgu",
            "geo": "KhuVuc",
            "query_context": "BoiCanh",
            "timezone": "MuiGio",
            "unit": "DonVi",
        },
    }
    csv_text = (
        "TuKhoa,NgayQuanSat,TrangThai,ChiSo,KenhNguon,NgonNgu,KhuVuc,BoiCanh,MuiGio,DonVi\n"
        "tra sua nuong,2026-10-15T00:00:00+07:00,AVAILABLE,88,TIKTOK_SEARCH,vi-VN,VN,BEVERAGE,UTC+07:00,points\n"
    )
    raw = csv_text.encode("utf-8")
    snapshot["sha256"] = sha256(raw).hexdigest()
    prepared = prepare_bytes(raw, source_snapshot=snapshot, profile=custom_profile, file_format="csv")
    sig = prepared.signals[0]
    assert sig["topic"] == "tra sua nuong"
    assert sig["value"] == 88
    assert sig["source_type"] == "TIKTOK_SEARCH"
    assert sig["geo"] == "VN"
    assert sig["unit"] == "points"


def test_csv_import_with_google_trends_profile(snapshot):
    csv_text = (
        "Query,Type,Source,Locale,Geo,Category,Time,Collected,Timezone,Unit,Denominator,Status,Interest,Rights\n"
        "cold brew,TOPIC,GOOGLE_TRENDS,vi-VN,VN,FOOD_DRINK,2026-10-10T00:00:00+07:00,2026-10-29T10:00:00+07:00,UTC+07:00,index,,AVAILABLE,92,UNKNOWN\n"
    )
    raw = csv_text.encode("utf-8")
    snapshot["sha256"] = sha256(raw).hexdigest()
    prepared = prepare_bytes(raw, source_snapshot=snapshot, profile="google_trends.manual.v1", file_format="csv")
    sig = prepared.signals[0]
    assert sig["topic"] == "cold brew"
    assert sig["value"] == 92
    assert sig["query_context"] == "FOOD_DRINK"


def test_csv_missing_mapped_header_fails(snapshot):
    custom_profile = {
        "profile_id": "missing.header.v1",
        "mapping_version": "1",
        "adapter_version": "manual-signal.v1",
        "native_export_layout_verified": False,
        "columns": {
            "topic": "Keyword",
            "observed_at": "Time",
            "status": "Status",
            "value": "Value",
            "unit": "MissingUnitColumn",
        },
    }
    csv_text = "Keyword,Time,Status,Value\ncoffee,2026-10-01T00:00:00Z,AVAILABLE,10\n"
    raw = csv_text.encode("utf-8")
    snapshot["sha256"] = sha256(raw).hexdigest()
    with pytest.raises(SignalValidationError) as exc:
        prepare_bytes(raw, source_snapshot=snapshot, profile=custom_profile, file_format="csv")
    assert "CSV_HEADER_MISMATCH" in str(exc.value)


def test_csv_duplicate_header_fails(snapshot):
    csv_text = "topic,observed_at,status,value,topic\ncoffee,2026-10-01T00:00:00Z,AVAILABLE,10,coffee\n"
    raw = csv_text.encode("utf-8")
    snapshot["sha256"] = sha256(raw).hexdigest()
    with pytest.raises(SignalValidationError) as exc:
        prepare_bytes(raw, source_snapshot=snapshot, file_format="csv")
    assert "AMBIGUOUS_CSV_HEADER" in str(exc.value)


# ---------------------------------------------------------------------------
# 4. UTF-8 / BOM / Encoding / Malformed CSV & JSON (AC02)
# ---------------------------------------------------------------------------


def test_csv_utf8_bom_support(snapshot):
    raw = csv_raw([row()], bom=True)
    assert raw.startswith(b"\xef\xbb\xbf")
    snapshot["sha256"] = sha256(raw).hexdigest()
    prepared = prepare_bytes(raw, source_snapshot=snapshot, file_format="csv")
    assert len(prepared.signals) == 1
    assert prepared.signals[0]["topic"] == "ca phe muoi"


def test_csv_non_utf8_fails(snapshot):
    raw = b"\xff\xfe\x00\x00not utf8"
    snapshot["sha256"] = sha256(raw).hexdigest()
    with pytest.raises(SignalValidationError) as exc:
        prepare_bytes(raw, source_snapshot=snapshot, file_format="csv")
    assert "CSV_UTF8_REQUIRED" in str(exc.value)


def test_csv_nul_byte_fails(snapshot):
    csv_text = "topic,observed_at,status,value\nco\x00ffee,2026-10-01T00:00:00Z,AVAILABLE,10\n"
    raw = csv_text.encode("utf-8")
    snapshot["sha256"] = sha256(raw).hexdigest()
    with pytest.raises(SignalValidationError) as exc:
        prepare_bytes(raw, source_snapshot=snapshot, file_format="csv")
    assert "CSV_NUL_BYTE" in str(exc.value)


def test_csv_row_width_mismatch_fails(snapshot):
    custom_profile = {
        "profile_id": "width.test.v1",
        "mapping_version": "1",
        "adapter_version": "manual-signal.v1",
        "native_export_layout_verified": False,
        "columns": {
            "topic": "topic",
            "observed_at": "observed_at",
            "status": "status",
            "value": "value",
        },
    }
    csv_text = "topic,observed_at,status,value\ncoffee,2026-10-01T00:00:00Z,AVAILABLE\n"
    raw = csv_text.encode("utf-8")
    snapshot["sha256"] = sha256(raw).hexdigest()
    with pytest.raises(SignalValidationError) as exc:
        prepare_bytes(raw, source_snapshot=snapshot, profile=custom_profile, file_format="csv")
    assert "CSV_ROW_WIDTH" in str(exc.value)


def test_canonical_json_happy(snapshot):
    raw = json_raw([row(topic="vietnamese drip coffee", value=99)])
    snapshot["sha256"] = sha256(raw).hexdigest()
    prepared = prepare_bytes(raw, source_snapshot=snapshot, file_format="json")
    assert len(prepared.signals) == 1
    assert prepared.signals[0]["topic"] == "vietnamese drip coffee"
    assert prepared.signals[0]["value"] == 99


def test_canonical_json_duplicate_key_rejected(snapshot):
    raw = b'{"schema_version": "ManualSignalRows.v1", "rows": [], "schema_version": "duplicate"}'
    snapshot["sha256"] = sha256(raw).hexdigest()
    with pytest.raises(SignalValidationError) as exc:
        prepare_bytes(raw, source_snapshot=snapshot, file_format="json")
    assert "JSON_DUPLICATE_KEY" in str(exc.value)


def test_canonical_json_nonfinite_rejected(snapshot):
    raw = b'{"schema_version": "ManualSignalRows.v1", "rows": [{"topic": "test", "observed_at": "2026-10-01T00:00:00Z", "status": "AVAILABLE", "value": NaN}]}'
    snapshot["sha256"] = sha256(raw).hexdigest()
    with pytest.raises(SignalValidationError) as exc:
        prepare_bytes(raw, source_snapshot=snapshot, file_format="json")
    assert "JSON_NONFINITE" in str(exc.value)


# ---------------------------------------------------------------------------
# 5. Timezone & Window Validation Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tz", ["UTC", "UTC+07:00", "UTC-05:00", "UTC+12:00"])
def test_valid_timezones(tz):
    validate_timezone(tz)


@pytest.mark.parametrize(
    "tz",
    [
        "",
        "   ",
        "UTC+25:00",
        "UTC-15:00",
        "UTC+14:30",
        "../etc/localtime",
        "/etc/timezone",
        "Invented/TzName",
    ],
)
def test_invalid_timezones(tz):
    with pytest.raises(SignalValidationError):
        validate_timezone(tz)


def test_valid_observation_window():
    validate_window(["2026-10-01T00:00:00Z", "2026-10-07T00:00:00Z"])
    validate_window(None)


def test_invalid_observation_window():
    with pytest.raises(SignalValidationError):
        validate_window(["2026-10-07T00:00:00Z", "2026-10-01T00:00:00Z"])  # start > end
    with pytest.raises(SignalValidationError):
        validate_window(["not-date", "2026-10-01T00:00:00Z"])
    with pytest.raises(SignalValidationError):
        validate_window(["2026-10-01T00:00:00Z"])  # not 2 items


# ---------------------------------------------------------------------------
# 6. SQLite Append-Only Ledger & Replay Integrity Tests (AC01)
# ---------------------------------------------------------------------------


def test_ledger_append_only_storage_and_idempotency(tmp_path, snapshot):
    db_path = tmp_path / "signals.sqlite"
    ledger = SignalLedger(db_path)
    raw = csv_raw([row(topic="coffee", value=50), row(topic="tea", value=30)])
    snapshot["sha256"] = sha256(raw).hexdigest()

    # Store
    result1 = ledger.import_bytes(raw, source_snapshot=snapshot, file_format="csv")
    assert result1["duplicate"] is False
    import_id = result1["import"]["import_id"]
    assert len(result1["signals"]) == 2

    # Idempotent same context
    result2 = ledger.import_bytes(raw, source_snapshot=snapshot, file_format="csv")
    assert result2["duplicate"] is True
    assert result2["import"]["import_id"] == import_id

    # Verify SQLite DB properties
    with sqlite3.connect(db_path) as db:
        app_id = db.execute("PRAGMA application_id").fetchone()[0]
        user_ver = db.execute("PRAGMA user_version").fetchone()[0]
        assert app_id == 0x41313031  # A101
        assert user_ver == 1
        raw_count = db.execute("SELECT count(*) FROM raw_evidence").fetchone()[0]
        import_count = db.execute("SELECT count(*) FROM imports").fetchone()[0]
        signal_count = db.execute("SELECT count(*) FROM signals").fetchone()[0]
        assert raw_count == 1
        assert import_count == 1
        assert signal_count == 2

        # Verify append-only trigger prevents updates/deletes
        with pytest.raises(sqlite3.IntegrityError, match="A10_APPEND_ONLY"):
            db.execute("DELETE FROM signals")
        with pytest.raises(sqlite3.IntegrityError, match="A10_APPEND_ONLY"):
            db.execute("UPDATE imports SET relationship='CORRECTION'")


def test_ledger_inspect_and_query_api(tmp_path, snapshot):
    db_path = tmp_path / "signals.sqlite"
    ledger = SignalLedger(db_path)
    raw = csv_raw([
        row(topic="iced latte", geo="VN", value=80),
        row(topic="matcha", geo="VN", value=65),
    ])
    snapshot["sha256"] = sha256(raw).hexdigest()
    res = ledger.import_bytes(raw, source_snapshot=snapshot, file_format="csv")
    import_id = res["import"]["import_id"]

    # inspect()
    inspected = ledger.inspect(import_id)
    assert inspected["verified"] is True
    assert inspected["import_id"] == import_id
    assert inspected["signal_count"] == 2

    # list_imports()
    imports = ledger.list_imports(geo="VN")
    assert len(imports) == 1
    assert imports[0]["import_id"] == import_id

    # query_signals() consumable by M02/M03
    latte = ledger.query_signals(topic="iced latte")
    assert len(latte) == 1
    assert latte[0]["value"] == 80
    assert latte[0]["geo"] == "VN"

    all_vn = ledger.query_signals(geo="VN")
    assert len(all_vn) == 2


# ---------------------------------------------------------------------------
# 7. Lineage Tests (Correction & Reprocess)
# ---------------------------------------------------------------------------


def test_ledger_correction_lineage(tmp_path, snapshot):
    db_path = tmp_path / "signals.sqlite"
    ledger = SignalLedger(db_path)

    # 1. Original import
    raw1 = csv_raw([row(topic="coffee", value=50)])
    snapshot["sha256"] = sha256(raw1).hexdigest()
    orig = ledger.import_bytes(raw1, source_snapshot=snapshot, file_format="csv")
    orig_id = orig["import"]["import_id"]

    # 2. Correction import: different raw bytes, same context
    raw2 = csv_raw([row(topic="coffee", value=55)])
    snapshot2 = deepcopy(snapshot)
    snapshot2["sha256"] = sha256(raw2).hexdigest()
    corrected = ledger.import_bytes(
        raw2,
        source_snapshot=snapshot2,
        file_format="csv",
        correction_of=orig_id,
    )
    assert corrected["import"]["lineage"]["relationship"] == "CORRECTION"
    assert corrected["import"]["lineage"]["parent_import_id"] == orig_id
    assert corrected["import"]["import_id"] != orig_id


def test_ledger_reprocess_lineage(tmp_path, snapshot):
    db_path = tmp_path / "signals.sqlite"
    ledger = SignalLedger(db_path)

    # 1. Original import
    raw = csv_raw([row(topic="coffee", value=50)])
    snapshot["sha256"] = sha256(raw).hexdigest()
    orig = ledger.import_bytes(raw, source_snapshot=snapshot, file_format="csv", dialect="comma")
    orig_id = orig["import"]["import_id"]

    # 2. Reprocess import with unchanged context fails
    with pytest.raises(SignalValidationError, match="REPROCESS_CONTEXT_UNCHANGED"):
        ledger.import_bytes(
            raw,
            source_snapshot=snapshot,
            profile="default.trend.v1",
            file_format="csv",
            dialect="comma",
            reprocess_of=orig_id,
        )

    # 3. Reprocess import with changed context/profile succeeds
    custom_profile = {
        "profile_id": "v2.profile",
        "mapping_version": "1",
        "adapter_version": "manual-signal.v1",
        "native_export_layout_verified": False,
        "columns": {name: name for name in SIGNAL_ROW_FIELDS},
    }
    reprocessed = ledger.import_bytes(
        raw,
        source_snapshot=snapshot,
        profile=custom_profile,
        file_format="csv",
        reprocess_of=orig_id,
    )
    assert reprocessed["import"]["lineage"]["relationship"] == "REPROCESS"
    assert reprocessed["import"]["lineage"]["parent_import_id"] == orig_id


# ---------------------------------------------------------------------------
# 8. Tamper Rejection Tests
# ---------------------------------------------------------------------------


def test_ledger_tamper_detection(tmp_path, snapshot):
    db_path = tmp_path / "signals.sqlite"
    ledger = SignalLedger(db_path)
    raw = csv_raw([row(topic="safe", value=10)])
    snapshot["sha256"] = sha256(raw).hexdigest()
    res = ledger.import_bytes(raw, source_snapshot=snapshot, file_format="csv")
    import_id = res["import"]["import_id"]

    # Tamper with raw evidence BLOB in database by modifying SQLite directly
    # Note: DB triggers prevent UPDATE, so drop the trigger in the test to simulate disk/DB tamper
    raw_db = sqlite3.connect(db_path)
    raw_db.execute("DROP TRIGGER raw_evidence_no_update")
    orig_payload = raw_db.execute("SELECT payload FROM raw_evidence").fetchone()[0]
    corrupted_payload = b"X" * len(orig_payload)
    raw_db.execute(
        "UPDATE raw_evidence SET payload=? WHERE sha256=?",
        (corrupted_payload, snapshot["sha256"]),
    )
    raw_db.commit()
    raw_db.close()

    # ledger.inspect() must detect the tamper
    with pytest.raises(SignalLedgerError, match="RAW_EVIDENCE_TAMPERED"):
        ledger.inspect(import_id)


# ---------------------------------------------------------------------------
# 9. Determinism, Rights UNKNOWN & Offline Safety Tests
# ---------------------------------------------------------------------------


def test_determinism_and_rights_unknown(snapshot):
    raw = csv_raw([row(topic="deterministic coffee", value=42)])
    snapshot["sha256"] = sha256(raw).hexdigest()

    p1 = prepare_bytes(raw, source_snapshot=snapshot, file_format="csv")
    p2 = prepare_bytes(raw, source_snapshot=snapshot, file_format="csv")

    assert p1.record["import_id"] == p2.record["import_id"]
    assert p1.signals[0]["signal_id"] == p2.signals[0]["signal_id"]
    assert p1.record["source_status"] == SOURCE_STATUS
    assert p1.signals[0]["rights_status"] == "UNKNOWN"


def test_import_file_from_disk(tmp_path, snapshot):
    db_path = tmp_path / "ledger.sqlite"
    ledger = SignalLedger(db_path)
    csv_file = tmp_path / "data.csv"
    raw = csv_raw([row(topic="disk_test", value=100)])
    csv_file.write_bytes(raw)
    snapshot["sha256"] = sha256(raw).hexdigest()

    result = ledger.import_file(csv_file, source_snapshot=snapshot, file_format="csv")
    assert result["duplicate"] is False
    assert len(result["signals"]) == 1
    assert result["signals"][0]["topic"] == "disk_test"

    # Raw bytes match exact disk bytes
    with sqlite3.connect(db_path) as db:
        stored_raw = db.execute("SELECT payload FROM raw_evidence WHERE sha256=?", (snapshot["sha256"],)).fetchone()[0]
        assert bytes(stored_raw) == raw


def test_path_safety_checks(tmp_path, snapshot):
    db_path = tmp_path / "ledger.sqlite"
    ledger = SignalLedger(db_path)
    with pytest.raises(OpportunityError, match="PATH_TRAVERSAL|UNSAFE_PATH"):
        ledger.import_file(tmp_path / "../escape.csv", source_snapshot=snapshot)


def test_file_size_and_row_limits(snapshot):
    with pytest.raises(SignalValidationError, match="SOURCE_EMPTY_OR_TOO_LARGE"):
        prepare_bytes(b"", source_snapshot=snapshot)

    # Overly large file
    with pytest.raises(SignalValidationError, match="SOURCE_EMPTY_OR_TOO_LARGE"):
        prepare_bytes(b"x" * (MAX_FILE_BYTES + 1), source_snapshot=snapshot)

    # Empty rows
    empty_csv = "topic,observed_at,status,value\n".encode("utf-8")
    snapshot["sha256"] = sha256(empty_csv).hexdigest()
    custom_profile = {
        "profile_id": "empty.v1",
        "mapping_version": "1",
        "adapter_version": "manual-signal.v1",
        "native_export_layout_verified": False,
        "columns": {"topic": "topic", "observed_at": "observed_at", "status": "status", "value": "value"},
    }
    with pytest.raises(SignalValidationError, match="EMPTY_SOURCE_ROWS"):
        prepare_bytes(empty_csv, source_snapshot=snapshot, profile=custom_profile, file_format="csv")


def test_lineage_negative_cases(tmp_path, snapshot):
    db_path = tmp_path / "ledger.sqlite"
    ledger = SignalLedger(db_path)

    raw = csv_raw([row(topic="base", value=1)])
    snapshot["sha256"] = sha256(raw).hexdigest()
    orig = ledger.import_bytes(raw, source_snapshot=snapshot, file_format="csv")
    orig_id = orig["import"]["import_id"]

    # Nonexistent parent
    with pytest.raises(SignalLedgerError, match="IMPORT_NOT_FOUND"):
        ledger.import_bytes(
            csv_raw([row(topic="base", value=2)]),
            source_snapshot=snapshot,
            file_format="csv",
            correction_of="a10-" + "f" * 64,
        )

    # Correction with unchanged raw source
    with pytest.raises(SignalValidationError, match="CORRECTION_SOURCE_UNCHANGED"):
        ledger.import_bytes(
            raw,
            source_snapshot=snapshot,
            file_format="csv",
            correction_of=orig_id,
        )

    # Lineage scope mismatch (different geo)
    diff_scope_snapshot = deepcopy(snapshot)
    diff_scope_snapshot["geo"] = "US"
    raw_diff = csv_raw([row(topic="base", value=2)])
    diff_scope_snapshot["sha256"] = sha256(raw_diff).hexdigest()
    with pytest.raises(SignalValidationError, match="LINEAGE_SCOPE_MISMATCH"):
        ledger.import_bytes(
            raw_diff,
            source_snapshot=diff_scope_snapshot,
            file_format="csv",
            correction_of=orig_id,
        )

    # Conflicting lineage (both correction_of and reprocess_of)
    with pytest.raises(SignalValidationError, match="CONFLICTING_LINEAGE"):
        ledger.import_bytes(
            raw_diff,
            source_snapshot=snapshot,
            file_format="csv",
            correction_of=orig_id,
            reprocess_of=orig_id,
        )


def test_tamper_signal_payload(tmp_path, snapshot):
    db_path = tmp_path / "ledger.sqlite"
    ledger = SignalLedger(db_path)
    raw = csv_raw([row(topic="safe", value=10)])
    snapshot["sha256"] = sha256(raw).hexdigest()
    res = ledger.import_bytes(raw, source_snapshot=snapshot, file_format="csv")
    import_id = res["import"]["import_id"]

    raw_db = sqlite3.connect(db_path)
    raw_db.execute("DROP TRIGGER signals_no_update")
    raw_db.execute("UPDATE signals SET payload=cast('tampered' as blob) WHERE import_id=?", (import_id,))
    raw_db.commit()
    raw_db.close()

    with pytest.raises(SignalLedgerError, match="SIGNAL_INTEGRITY_MISMATCH"):
        ledger.inspect(import_id)


def test_offline_zero_sockets(monkeypatch, tmp_path, snapshot):
    import socket

    def guarded_socket(*args, **kwargs):
        raise RuntimeError("OFFLINE_VIOLATION: socket attempted")

    monkeypatch.setattr(socket, "socket", guarded_socket)

    db_path = tmp_path / "offline_ledger.sqlite"
    ledger = SignalLedger(db_path)
    raw = csv_raw([row(topic="offline_safe", value=50)])
    snapshot["sha256"] = sha256(raw).hexdigest()
    res = ledger.import_bytes(raw, source_snapshot=snapshot, file_format="csv")
    assert res["duplicate"] is False
    assert ledger.inspect(res["import"]["import_id"])["verified"] is True

