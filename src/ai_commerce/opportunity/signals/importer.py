"""Deterministic offline signal intake; no provider access or live network."""
from __future__ import annotations

import csv
import io
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes, timestamp_utc

from ..core.models import (
    OpportunityError,
    SignalValidationError,
    digest,
    parse_json,
    require,
    scope,
    validate_signal,
    validate_signal_import,
    validate_source_snapshot,
)
from ..core.schemas import SOURCE_STATUS
from .files import (
    MAX_FIELD_CHARS,
    MAX_FILE_BYTES,
    MAX_ROW_BYTES,
    MAX_ROWS,
    MAX_SNAPSHOT_BYTES,
    read_local,
)
from .profiles import mapping_profile, validate_mapping

DELIMITERS = {"comma": ",", "semicolon": ";", "tab": "\t"}
IMPORT_ID_RE = re.compile(r"^a10-[a-f0-9]{64}$")


@dataclass(frozen=True)
class PreparedSignalImport:
    raw: bytes
    record: dict[str, Any]
    signals: tuple[dict[str, Any], ...]


def load_source_snapshot(path: str) -> dict[str, Any]:
    return validate_source_snapshot(parse_json(read_local(path, limit=MAX_SNAPSHOT_BYTES)))


def load_mapping(path: str) -> dict[str, Any]:
    return validate_mapping(parse_json(read_local(path, limit=MAX_SNAPSHOT_BYTES)))


def _csv_rows(raw: bytes, mapping: dict[str, Any], dialect: str, snapshot: dict[str, Any]):
    require(dialect in DELIMITERS, "UNKNOWN_CSV_DIALECT")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise SignalValidationError("CSV_UTF8_REQUIRED") from exc
    require("\x00" not in text, "CSV_NUL_BYTE")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=DELIMITERS[dialect], strict=True)
    source_row = 1
    try:
        header = next(reader, None)
        require(header is not None and bool(header), "CSV_HEADER_EMPTY")
        require(len(header) == len(set(header)), "AMBIGUOUS_CSV_HEADER")
        mapped_headers = set(mapping["columns"].values())
        require(mapped_headers.issubset(set(header)), "CSV_HEADER_MISMATCH")
        for source_row, values in enumerate(reader, 2):
            require(source_row <= MAX_ROWS + 1, "TOO_MANY_ROWS", row=source_row)
            require(len(values) == len(header), "CSV_ROW_WIDTH", row=source_row)
            require(
                all(len(v) <= MAX_FIELD_CHARS for v in values)
                and sum(len(v.encode("utf-8")) for v in values) <= MAX_ROW_BYTES,
                "ROW_TOO_LARGE",
                row=source_row,
            )
            columns = dict(zip(header, values))
            mapped_item = {field: columns[col] for field, col in mapping["columns"].items()}
            
            # Field value resolution with snapshot defaults
            topic = mapped_item["topic"].strip()
            require(bool(topic), "TOPIC_EMPTY", row=source_row)
            
            signal_type = mapped_item.get("signal_type", "").strip() or snapshot.get("default_signal_type") or "TOPIC"
            source_type = mapped_item.get("source_type", "").strip() or snapshot.get("default_source_type") or "MANUAL_RADAR"
            locale = mapped_item.get("locale", "").strip() or snapshot.get("locale") or "en-US"
            geo = mapped_item.get("geo", "").strip() or snapshot.get("geo") or "GLOBAL"
            query_context = mapped_item.get("query_context", "").strip() or snapshot.get("default_query_context") or "GENERAL"
            observed_at = mapped_item["observed_at"].strip()
            require(bool(observed_at), "OBSERVED_AT_EMPTY", row=source_row)
            
            collected_at = (
                mapped_item.get("collected_at", "").strip()
                or snapshot.get("collected_at")
                or snapshot.get("captured_at")
            )
            require(bool(collected_at), "COLLECTED_AT_EMPTY", row=source_row)
            
            timezone = mapped_item.get("timezone", "").strip() or snapshot.get("default_timezone") or "UTC"
            unit = mapped_item.get("unit", "").strip() or snapshot.get("default_unit") or "index"
            denominator = mapped_item.get("denominator", "").strip() or None
            
            rights_status = mapped_item.get("rights_status", "").strip() or snapshot.get("rights_status") or "UNKNOWN"
            
            # Optional observation window
            obs_win = None
            if "observation_window_start" in mapping["columns"] and "observation_window_end" in mapping["columns"]:
                w_start = columns[mapping["columns"]["observation_window_start"]].strip()
                w_end = columns[mapping["columns"]["observation_window_end"]].strip()
                if w_start and w_end:
                    obs_win = [w_start, w_end]
            
            # Evidence refs
            ev_refs = [snapshot.get("source_ref", "local://unknown")]
            if "evidence_refs" in mapping["columns"]:
                raw_refs = columns[mapping["columns"]["evidence_refs"]].strip()
                if raw_refs:
                    ev_refs = [r.strip() for r in re.split(r"[,;]\s*", raw_refs) if r.strip()]

            # Value & Status handling (preserve 0 distinct from NOT_AVAILABLE / null)
            status = mapped_item["status"].strip()
            require(status in ("AVAILABLE", "NOT_AVAILABLE"), "INVALID_SIGNAL_STATUS", row=source_row)
            raw_value = mapped_item["value"].strip()
            if status == "NOT_AVAILABLE":
                require(raw_value == "", "NOT_AVAILABLE_REQUIRES_EMPTY_VALUE", row=source_row)
                value = None
            else:
                require(raw_value != "", "AVAILABLE_REQUIRES_NUMERIC_VALUE", row=source_row)
                try:
                    val = json.loads(raw_value)
                except (ValueError, RecursionError) as exc:
                    raise SignalValidationError("INVALID_METRIC_VALUE", row=source_row) from exc
                require(
                    isinstance(val, (int, float)) and not isinstance(val, bool),
                    "INVALID_METRIC_VALUE",
                    row=source_row,
                )
                value = val

            yield source_row, {
                "topic": topic,
                "signal_type": signal_type,
                "source_type": source_type,
                "locale": locale,
                "geo": geo,
                "query_context": query_context,
                "observed_at": observed_at,
                "observation_window": obs_win,
                "collected_at": collected_at,
                "timezone": timezone,
                "unit": unit,
                "denominator": denominator,
                "status": status,
                "value": value,
                "source_status": SOURCE_STATUS,
                "rights_status": rights_status,
                "evidence_refs": ev_refs,
            }
    except csv.Error as exc:
        raise SignalValidationError("CSV_MALFORMED", row=source_row) from exc


def _json_rows(raw: bytes, snapshot: dict[str, Any]):
    envelope = parse_json(raw)
    require(
        set(envelope) == {"schema_version", "rows"}
        and envelope["schema_version"] == "ManualSignalRows.v1"
        and isinstance(envelope["rows"], list),
        "INVALID_CANONICAL_JSON_ENVELOPE",
    )
    require(0 < len(envelope["rows"]) <= MAX_ROWS, "EMPTY_OR_TOO_MANY_ROWS")
    for number, raw_row in enumerate(envelope["rows"], 1):
        require(isinstance(raw_row, dict), "ROW_NOT_AN_OBJECT", row=number)
        require(len(canonical_bytes(raw_row)) <= MAX_ROW_BYTES, "ROW_TOO_LARGE", row=number)
        
        status = raw_row.get("status")
        require(status in ("AVAILABLE", "NOT_AVAILABLE"), "INVALID_SIGNAL_STATUS", row=number)
        val = raw_row.get("value")
        if status == "NOT_AVAILABLE":
            require(val is None, "NOT_AVAILABLE_REQUIRES_NULL_VALUE", row=number)
        else:
            require(
                isinstance(val, (int, float)) and not isinstance(val, bool),
                "AVAILABLE_REQUIRES_NUMERIC_VALUE",
                row=number,
            )

        row = {
            "topic": raw_row["topic"],
            "signal_type": raw_row.get("signal_type") or snapshot.get("default_signal_type") or "TOPIC",
            "source_type": raw_row.get("source_type") or snapshot.get("default_source_type") or "MANUAL_RADAR",
            "locale": raw_row.get("locale") or snapshot.get("locale") or "en-US",
            "geo": raw_row.get("geo") or snapshot.get("geo") or "GLOBAL",
            "query_context": raw_row.get("query_context") or snapshot.get("default_query_context") or "GENERAL",
            "observed_at": raw_row["observed_at"],
            "observation_window": raw_row.get("observation_window"),
            "collected_at": raw_row.get("collected_at") or snapshot.get("collected_at") or snapshot.get("captured_at"),
            "timezone": raw_row.get("timezone") or snapshot.get("default_timezone") or "UTC",
            "unit": raw_row.get("unit") or snapshot.get("default_unit") or "index",
            "denominator": raw_row.get("denominator"),
            "status": status,
            "value": val,
            "source_status": SOURCE_STATUS,
            "rights_status": raw_row.get("rights_status") or snapshot.get("rights_status") or "UNKNOWN",
            "evidence_refs": raw_row.get("evidence_refs") or [snapshot.get("source_ref", "local://unknown")],
        }
        yield number, row


def prepare_bytes(
    raw: bytes,
    *,
    source_snapshot: dict[str, Any],
    profile: str | dict[str, Any] | None = None,
    file_format: str = "csv",
    dialect: str | None = None,
    correction_of: str | None = None,
    reprocess_of: str | None = None,
) -> PreparedSignalImport:
    """Validate entire source before ledger mutation; atomic and deterministic."""
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_FILE_BYTES, "SOURCE_EMPTY_OR_TOO_LARGE")
    require(file_format in ("csv", "json"), "UNSUPPORTED_FORMAT")
    require(not (correction_of is not None and reprocess_of is not None), "CONFLICTING_LINEAGE")
    for parent in (correction_of, reprocess_of):
        require(
            parent is None or (isinstance(parent, str) and bool(IMPORT_ID_RE.fullmatch(parent))),
            "INVALID_PARENT_IMPORT_ID",
        )

    snapshot = validate_source_snapshot(deepcopy(source_snapshot))
    
    if file_format == "csv":
        if dialect is None:
            dialect = "comma"
        require(dialect in DELIMITERS, "UNKNOWN_CSV_DIALECT")
        mapping = mapping_profile(profile or "default.trend.v1")
    else:
        require(dialect is None, "JSON_CANNOT_HAVE_CSV_DIALECT")
        mapping = None

    raw_sha256 = sha256_bytes(raw)
    parent_id = correction_of or reprocess_of
    relationship = "ORIGINAL" if parent_id is None else ("CORRECTION" if correction_of else "REPROCESS")
    lineage = {
        "relationship": relationship,
        "parent_import_id": parent_id,
    }

    # Source context excludes raw file sha256 and captured_at so corrections retain same context
    context = {
        "snapshot_context": {k: v for k, v in snapshot.items() if k not in ("sha256", "captured_at")},
        "mapping": mapping,
        "format": file_format,
        "dialect": dialect,
    }
    context_sha256 = digest(context)
    scope_sha256 = digest(scope(snapshot))

    import_id = "a10-" + sha256_bytes(
        canonical_bytes({
            "raw_sha256": raw_sha256,
            "context_sha256": context_sha256,
            "lineage": lineage,
        })
    )

    rows_generator = (
        _csv_rows(raw, mapping, dialect, snapshot)
        if file_format == "csv"
        else _json_rows(raw, snapshot)
    )

    signals: list[dict[str, Any]] = []
    for ordinal, (source_row, row_data) in enumerate(rows_generator, 1):
        signal_core = {
            "schema_version": "TrendSignal.v1",
            "import_id": import_id,
            "ordinal": ordinal,
            **row_data,
        }
        signal_id = "sig-" + sha256_bytes(
            canonical_bytes({
                "import_id": import_id,
                "ordinal": ordinal,
                "signal": signal_core,
            })
        )
        full_signal = {
            "signal_id": signal_id,
            **signal_core,
        }
        validate_signal(full_signal, row=source_row)
        signals.append(full_signal)

    require(bool(signals), "EMPTY_SOURCE_ROWS")
    require(len(signals) <= MAX_ROWS, "TOO_MANY_ROWS")

    import_record = {
        "schema_version": "SignalImport.v1",
        "import_id": import_id,
        "status": "VALIDATED",
        "raw_sha256": raw_sha256,
        "raw_ref": f"sha256:{raw_sha256}",
        "raw_size_bytes": len(raw),
        "format": file_format,
        "dialect": dialect,
        "source_snapshot": snapshot,
        "mapping": mapping,
        "context_sha256": context_sha256,
        "scope_sha256": scope_sha256,
        "lineage": lineage,
        "signal_count": len(signals),
        "source_status": SOURCE_STATUS,
        "recorded_at": timestamp_utc(),
    }
    validate_signal_import(import_record)

    return PreparedSignalImport(
        raw=raw,
        record=import_record,
        signals=tuple(signals),
    )
