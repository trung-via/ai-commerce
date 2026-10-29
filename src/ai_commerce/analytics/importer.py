"""Deterministic offline normalization; no provider access or implicit periods."""
from __future__ import annotations

import csv
import io
import json
import re
from copy import deepcopy
from dataclasses import dataclass

from jsonschema import Draft202012Validator

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from .files import MAX_FIELD_CHARS, MAX_FILE_BYTES, MAX_PLAN_BYTES, MAX_ROW_BYTES, MAX_ROWS, read_local
from .model import (AnalyticsError, checked_url, digest, metric_identity, parse_json,
                    require, scope, validate_contract, validate_mapping, validate_plan)
from .profiles import mapping_profile
from .schemas import ROW, ROW_FIELDS, SOURCE, VERIFICATION

DELIMITERS = {"comma": ",", "semicolon": ";", "tab": "\t"}
_ROW_VALIDATOR = Draft202012Validator(ROW)


@dataclass(frozen=True)
class PreparedImport:
    raw: bytes
    record: dict
    observations: tuple[dict, ...]


def load_plan(path) -> dict:
    return validate_plan(parse_json(read_local(path, limit=MAX_PLAN_BYTES)))


def load_mapping(path) -> dict:
    return validate_mapping(parse_json(read_local(path, limit=MAX_PLAN_BYTES)))


def _csv_rows(raw: bytes, mapping: dict, dialect: str):
    require(dialect in DELIMITERS, "UNKNOWN_CSV_DIALECT")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise AnalyticsError("CSV_UTF8_REQUIRED") from exc
    require("\x00" not in text, "CSV_NUL_BYTE")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=DELIMITERS[dialect], strict=True)
    source_row = 1
    try:
        header = next(reader, None)
        require(header is not None and len(header) <= len(ROW_FIELDS), "CSV_HEADER_MISMATCH")
        require(len(header) == len(set(header)), "AMBIGUOUS_CSV_HEADER")
        require(set(header) == set(mapping["columns"].values()), "CSV_HEADER_MISMATCH")
        for source_row, values in enumerate(reader, 2):
            require(source_row <= MAX_ROWS + 1, "TOO_MANY_ROWS", row=source_row)
            require(len(values) == len(header), "CSV_ROW_WIDTH", row=source_row)
            require(all(len(v) <= MAX_FIELD_CHARS for v in values)
                    and sum(len(v.encode("utf-8")) for v in values) <= MAX_ROW_BYTES,
                    "ROW_TOO_LARGE", row=source_row)
            columns = dict(zip(header, values))
            item = {key: columns[column] for key, column in mapping["columns"].items()}
            for key in ("provider_post_id", "public_url"):
                item[key] = item[key] or None
            value = item["value"]
            if item["status"] == "NOT_AVAILABLE":
                require(value == "", "NOT_AVAILABLE_REQUIRES_EMPTY_VALUE", row=source_row)
                item["value"] = None
            else:
                # Only JSON numeric syntax, never eval or spreadsheet evaluation.
                try:
                    item["value"] = json.loads(value)
                except (ValueError, RecursionError) as exc:
                    raise AnalyticsError("INVALID_METRIC_VALUE", row=source_row) from exc
            yield source_row, item
    except csv.Error as exc:
        raise AnalyticsError("CSV_MALFORMED", row=source_row) from exc


def _json_rows(raw: bytes):
    envelope = parse_json(raw)
    require(set(envelope) == {"schema_version", "rows"}
            and envelope["schema_version"] == "ManualAnalyticsRows.v1"
            and isinstance(envelope["rows"], list), "INVALID_CANONICAL_JSON_ENVELOPE")
    require(0 < len(envelope["rows"]) <= MAX_ROWS, "EMPTY_OR_TOO_MANY_ROWS")
    for number, row in enumerate(envelope["rows"], 1):
        require(len(canonical_bytes(row)) <= MAX_ROW_BYTES, "ROW_TOO_LARGE", row=number)
        yield number, row


def prepare_bytes(raw: bytes, *, plan: dict, profile: str | dict, file_format: str,
                  dialect: str | None = None, source_kind: str = "USER_AUTHORED_MANUAL",
                  correction_of: str | None = None, reprocess_of: str | None = None) -> PreparedImport:
    """Validate the whole source before any ledger mutation; no partial success."""
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_FILE_BYTES, "SOURCE_EMPTY_OR_TOO_LARGE")
    require(not (correction_of is not None and reprocess_of is not None), "CONFLICTING_LINEAGE")
    require(all(parent is None or (isinstance(parent, str) and re.fullmatch(r"a09-[a-f0-9]{64}", parent))
                for parent in (correction_of, reprocess_of)), "INVALID_PARENT_IMPORT_ID")
    plan = validate_plan(plan)
    require(len(canonical_bytes(plan)) <= MAX_PLAN_BYTES, "PLAN_TOO_LARGE")
    require(plan["source_mode"] == "MANUAL_IMPORT", "OFFLINE_MANUAL_SOURCE_REQUIRED")
    mapping = mapping_profile(profile) if isinstance(profile, str) else validate_mapping(profile)
    require(plan["platform"] == mapping["platform"], "MAPPING_PLATFORM_MISMATCH")
    require(source_kind in SOURCE["properties"]["source_kind"]["enum"], "INVALID_SOURCE_KIND")
    require(file_format in {"csv", "json"}, "UNKNOWN_ANALYTICS_FORMAT")
    if file_format == "csv":
        dialect = "comma" if dialect is None else dialect
    else:
        require(dialect is None, "JSON_HAS_NO_CSV_DIALECT")
    parent = correction_of or reprocess_of
    relationship = "CORRECTION" if correction_of else "REPROCESS" if reprocess_of else "ORIGINAL"
    lineage = {"relationship": relationship, "parent_import_id": parent}
    raw_sha = sha256_bytes(raw)
    context = {"plan": plan, "mapping": mapping, "format": file_format,
               "dialect": dialect, "source_kind": source_kind}
    context_sha = digest(context)
    import_id = "a09-" + digest({"raw_sha256": raw_sha, "context_sha256": context_sha, "lineage": lineage})
    definitions = {metric_identity(metric): metric for metric in plan["metric_definitions"]}
    rows = _csv_rows(raw, mapping, dialect) if file_format == "csv" else _json_rows(raw)
    observations = []
    seen = set()
    for number, item in rows:
        require(_ROW_VALIDATOR.is_valid(item), "INVALID_ANALYTICS_ROW", row=number)
        try:
            canonical_bytes(item)  # NaN/Infinity and invalid Unicode cannot pass.
        except (ValueError, UnicodeError) as exc:
            raise AnalyticsError("INVALID_METRIC_VALUE", row=number) from exc
        key = metric_identity(item)
        require(key in definitions, "UNKNOWN_METRIC_DEFINITION", row=number)
        require(item["provider_post_id"] is None or bool(item["provider_post_id"].strip()),
                "EMPTY_POST_ID", row=number)
        checked_url(item["public_url"], row=number)
        identities = [(field, item[field], *key) for field in ("provider_post_id", "public_url")
                      if item[field] is not None]
        if not identities:
            identities = [("account_aggregate", *key)]
        require(not any(identity in seen for identity in identities), "AMBIGUOUS_DUPLICATE_OBSERVATION", row=number)
        seen.update(identities)
        core = {
            "schema_version": "PerformanceObservation.v1", "import_id": import_id, **scope(plan),
            "provider_post_id": item["provider_post_id"], "public_url": item["public_url"],
            "metric": deepcopy(definitions[key]), "status": item["status"], "value": item["value"],
            "source": {"raw_sha256": raw_sha, "source_row": number,
                       "source_row_semantics": "CSV_LOGICAL_RECORD_INCLUDING_HEADER" if file_format == "csv"
                       else "JSON_ROWS_ONE_BASED", "profile_id": mapping["profile_id"],
                       "mapping_version": mapping["mapping_version"],
                       "adapter_version": mapping["adapter_version"], "source_kind": source_kind,
                       "source_status": VERIFICATION}, "source_status": VERIFICATION,
        }
        core["observation_id"] = "obs-" + digest(core)
        validate_contract("PerformanceObservation", core)
        observations.append(core)
    require(bool(observations), "EMPTY_ANALYTICS_SOURCE")
    record = {
        "schema_version": "AnalyticsImport.v1", "import_id": import_id, "status": "VALIDATED",
        "raw_sha256": raw_sha, "raw_ref": "sha256:" + raw_sha, "raw_size_bytes": len(raw),
        **context, "source_status": VERIFICATION, "context_sha256": context_sha,
        "scope_sha256": digest(scope(plan)), "lineage": lineage, "observation_count": len(observations),
    }
    validate_contract("AnalyticsImport", record)
    return PreparedImport(raw, record, tuple(observations))


def validate_file(path, **kwargs) -> PreparedImport:
    return prepare_bytes(read_local(path), **kwargs)
