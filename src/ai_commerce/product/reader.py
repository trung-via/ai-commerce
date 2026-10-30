"""Manual local JSON/CSV snapshots with exact bytes, bounded projection and replay.

No URL is opened. Source URLs, declared verification, titles and descriptions
are inert assertions. Raw input remains available even when fields are mapped.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

from ai_commerce.core.provenance import sha256_bytes

from .core import (ProductError, canonical_bytes, content_id, digest, parse_json,
                   parse_timestamp, require, validate_contract)
from .files import read_local
from .identity import build_identity, build_variant
from .schemas import (MANUAL_ROW, MAX_FIELD_CHARS, MAX_RAW_BYTES, MAX_ROW_BYTES,
                      MAX_ROWS, OPTIONS, READER_VERSION, REQUIRED_ROW_FIELDS,
                      ROW_PROPERTIES, SOURCE, SOURCE_STATUS)

_ROW_VALIDATOR = Draft202012Validator(MANUAL_ROW)
_SOURCE_VALIDATOR = Draft202012Validator(SOURCE)
_OPTIONS_VALIDATOR = Draft202012Validator(OPTIONS)
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def _check_options(options, file_format):
    canonical_bytes(options)
    require(_OPTIONS_VALIDATOR.is_valid(options), "INVALID_READER_OPTIONS")
    mapping = options["field_map"]
    if mapping is not None:
        require(len(set(mapping.values())) == len(mapping), "AMBIGUOUS_FIELD_MAP")
    pointer = options["json_rows_pointer"]
    if file_format == "csv":
        require(pointer is None, "JSON_POINTER_FOR_CSV")
    elif pointer is not None:
        require(pointer == "" or pointer.startswith("/"), "INVALID_JSON_POINTER")
        require(re.search(r"~(?:[^01]|$)", pointer) is None and len(pointer.split("/")) <= 17,
                "INVALID_JSON_POINTER")


def _json_rows(raw, pointer):
    envelope = parse_json(raw, limit=MAX_RAW_BYTES)
    if pointer is None:
        require(type(envelope) is dict and set(envelope) == {"schema_version", "rows"}
                and envelope["schema_version"] == "ManualProductListings.v1",
                "INVALID_MANUAL_JSON_ENVELOPE")
        rows, base = envelope["rows"], "/rows"
    else:
        rows, base = envelope, pointer
        for token in pointer.split("/")[1:] if pointer else ():
            token = token.replace("~1", "/").replace("~0", "~")
            if type(rows) is dict:
                require(token in rows, "JSON_POINTER_NOT_FOUND")
                rows = rows[token]
            elif type(rows) is list:
                require(re.fullmatch(r"0|[1-9][0-9]*", token) is not None
                        and len(token) <= 6 and int(token) < len(rows), "JSON_POINTER_NOT_FOUND")
                rows = rows[int(token)]
            else:
                raise ProductError("JSON_POINTER_NOT_FOUND")
    require(type(rows) is list and 0 < len(rows) <= MAX_ROWS, "EMPTY_OR_TOO_MANY_ROWS")
    for ordinal, row in enumerate(rows, 1):
        yield ordinal, base + "/" + str(ordinal - 1), row


def _check_csv_quotes(text):
    # csv.reader(strict=True) still accepts quotes inside an unquoted field.
    # Enforce the RFC4180 quote grammar, allowing LF or CRLF line endings.
    state = "START"
    for index, char in enumerate(text):
        if state == "QUOTED":
            if char == '"':
                state = "AFTER_QUOTE"
            continue
        if char == "\r":
            require(index + 1 < len(text) and text[index + 1] == "\n", "CSV_MALFORMED")
        if state == "AFTER_QUOTE":
            if char == '"':
                state = "QUOTED"
            elif char == "," or char in "\r\n":
                state = "START"
            else:
                raise ProductError("CSV_MALFORMED")
        elif char == '"':
            require(state == "START", "CSV_MALFORMED")
            state = "QUOTED"
        elif char == "," or char in "\r\n":
            state = "START"
        else:
            state = "UNQUOTED"
    require(state != "QUOTED", "CSV_MALFORMED")


def _csv_rows(raw, mapping):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ProductError("CSV_UTF8_REQUIRED") from exc
    require("\x00" not in text, "CSV_NUL_BYTE")
    _check_csv_quotes(text)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    number = 1
    try:
        header = next(reader, None)
        require(header is not None and 0 < len(header) <= 64
                and all(0 < len(key) <= 256 and key.strip() == key for key in header),
                "INVALID_CSV_HEADER")
        require(len(header) == len(set(header)), "DUPLICATE_CSV_HEADER")
        expected = set(REQUIRED_ROW_FIELDS) if mapping is None else {
            mapping[key] for key in REQUIRED_ROW_FIELDS}
        require(expected.issubset(header), "MISSING_REQUIRED_COLUMN")
        if mapping is None:
            require(set(header).issubset(ROW_PROPERTIES), "UNKNOWN_CSV_COLUMN")
        else:
            require(set(mapping.values()).issubset(header), "MAPPED_COLUMN_NOT_FOUND")
        for number, cells in enumerate(reader, 2):
            require(number <= MAX_ROWS + 1, "EMPTY_OR_TOO_MANY_ROWS", row=number)
            require(len(cells) == len(header), "CSV_ROW_WIDTH", row=number)
            require(all(len(cell) <= MAX_FIELD_CHARS for cell in cells)
                    and sum(len(cell.encode("utf-8")) for cell in cells) <= MAX_ROW_BYTES,
                    "ROW_TOO_LARGE", row=number)
            yield number, None, dict(zip(header, cells))
        require(number > 1, "EMPTY_OR_TOO_MANY_ROWS")
    except csv.Error as exc:
        raise ProductError("CSV_MALFORMED", row=number) from exc


def _project(row, mapping, file_format):
    require(type(row) is dict, "LISTING_OBJECT_REQUIRED")
    require(len(canonical_bytes(row)) <= MAX_ROW_BYTES, "ROW_TOO_LARGE")
    if mapping is None:
        require(set(row).issubset(ROW_PROPERTIES), "UNKNOWN_LISTING_FIELD")
        result = dict(row)
    else:
        result = {field: row[source] for field, source in mapping.items() if source in row}
    if file_format == "csv":
        for key, value in result.items():
            if key in REQUIRED_ROW_FIELDS:
                continue
            if value == "":
                result[key] = {} if key == "variant_attributes" else None
            elif key == "price_amount":
                require(_NUMBER.fullmatch(value) is not None, "INVALID_PRICE")
                result[key] = parse_json(value.encode("utf-8"))
            elif key == "variant_attributes":
                result[key] = parse_json(value.encode("utf-8"))
    if result.get("observed_at") is not None:
        parse_timestamp(result["observed_at"])
    require(_ROW_VALIDATOR.is_valid(result), "INVALID_LISTING_ROW")
    return result


def _normalize(row, *, ordinal, source_row, pointer, captured_at):
    observed = row.get("observed_at")
    if observed is not None:
        require(parse_timestamp(observed) <= captured_at, "OBSERVED_AFTER_CAPTURE")
    amount, currency = row.get("price_amount"), row.get("currency")
    require(amount is None or currency is not None, "PRICE_CURRENCY_REQUIRED")
    identity = build_identity(marketplace=row["marketplace"], seller_key=row["seller_key"],
                              listing_key=row["listing_key"], product_key=row.get("product_key"))
    variant = build_variant(identity, variant_key=row.get("variant_key"), sku=row.get("sku"),
                            gtin=row.get("gtin"), attributes=row.get("variant_attributes", {}))
    return {
        "ordinal": ordinal, "source_row": source_row, "source_pointer": pointer,
        "provided_fields": sorted(row), "identity": identity, "variant": variant,
        "title": row.get("title"), "description": row.get("description"),
        "listing_url": row.get("listing_url"), "observed_at": observed,
        "price": {"status": "UNKNOWN" if amount is None else "SOURCE_ASSERTED_NOT_LIVE",
                  "amount": amount, "currency": currency},
    }


def _build_document(raw: bytes, *, file_format, source_ref, captured_at, source_url=None,
                    source_kind="HUMAN_SUPPLIED", synthetic=False, declared_verification=None,
                    field_map=None, json_rows_pointer=None):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_RAW_BYTES, "SOURCE_EMPTY_OR_TOO_LARGE")
    require(type(file_format) is str and file_format in {"json", "csv"}, "UNKNOWN_SOURCE_FORMAT")
    capture = parse_timestamp(captured_at)
    raw_sha = sha256_bytes(raw)
    source = {
        "source_kind": source_kind, "source_ref": source_ref, "source_url": source_url,
        "source_status": SOURCE_STATUS, "declared_verification": declared_verification,
        "synthetic": synthetic, "captured_at": captured_at, "format": file_format,
        "raw_sha256": raw_sha, "raw_ref": "sha256:" + raw_sha, "raw_size_bytes": len(raw),
    }
    source["source_snapshot_id"] = "a12-source-" + digest(source)
    require(_SOURCE_VALIDATOR.is_valid(source), "INVALID_SOURCE_METADATA")
    options = {"field_map": field_map, "json_rows_pointer": json_rows_pointer}
    _check_options(options, file_format)
    rows = _json_rows(raw, json_rows_pointer) if file_format == "json" else _csv_rows(raw, field_map)
    listings, seen, products = [], {}, {}
    for ordinal, (source_row, pointer, supplied) in enumerate(rows, 1):
        try:
            row = _project(supplied, field_map, file_format)
            item = _normalize(row, ordinal=ordinal, source_row=source_row,
                              pointer=pointer, captured_at=capture)
            listing_id = item["identity"]["listing_id"]
            product_key = row.get("product_key")
            require(listing_id not in products or products[listing_id] == product_key,
                    "CONFLICTING_LISTING_PRODUCT")
            products[listing_id] = product_key
            key = listing_id, item["variant"]["variant_id"]
            fingerprint = digest({key: value for key, value in item.items()
                                  if key not in {"ordinal", "source_row", "source_pointer"}})
            require(key not in seen, "DUPLICATE_LISTING_VARIANT" if seen.get(key) == fingerprint
                    else "CONFLICTING_LISTING_VARIANT")
            seen[key] = fingerprint
            listings.append(item)
        except ProductError as exc:
            if exc.row is not None:
                raise
            raise ProductError(exc.code, row=source_row) from exc
    body = {
        "schema_version": "ProductListingSnapshot.v1", "reader_version": READER_VERSION,
        "verification_scope": "LOCAL_BYTES_HASH_AND_REPLAY_ONLY", "rights_status": "UNKNOWN",
        "affiliate_status": "UNKNOWN", "publication_status": "NOT_ATTEMPTED",
        "source": source, "options": options, "listings": listings,
    }
    return {**body, "snapshot_id": content_id("ProductListingSnapshot", body)}


def _verify_snapshot(value: dict, raw: bytes | None) -> None:
    require(type(raw) is bytes, "RAW_EVIDENCE_REQUIRED")
    require(0 < len(raw) <= MAX_RAW_BYTES, "SOURCE_EMPTY_OR_TOO_LARGE")
    source = value["source"]
    require(source["raw_sha256"] == sha256_bytes(raw) and source["raw_size_bytes"] == len(raw)
            and source["raw_ref"] == "sha256:" + source["raw_sha256"], "RAW_PROVENANCE_MISMATCH")
    expected = _build_document(
        raw, file_format=source["format"], source_ref=source["source_ref"],
        source_url=source["source_url"], captured_at=source["captured_at"],
        source_kind=source["source_kind"], synthetic=source["synthetic"],
        declared_verification=source["declared_verification"], **value["options"],
    )
    require(canonical_bytes(value) == canonical_bytes(expected), "SNAPSHOT_REPLAY_MISMATCH")


@dataclass(frozen=True, slots=True, init=False)
class ProductListingSnapshot:
    """Immutable normalized bytes and original evidence; accessors return copies.

    Persist both to_bytes() and raw_bytes() when archiving. Restoring requires
    both; serialized claims alone cannot acquire a hash/replay-verified status.
    """

    _encoded: bytes = field(repr=False)
    _raw_evidence: bytes = field(repr=False)

    def __init__(self, value: dict, *, raw: bytes):
        checked = validate_contract("ProductListingSnapshot", value, raw=raw)
        object.__setattr__(self, "_encoded", canonical_bytes(checked))
        object.__setattr__(self, "_raw_evidence", raw)

    @classmethod
    def from_bytes(cls, encoded: bytes, *, raw: bytes) -> ProductListingSnapshot:
        return cls(parse_json(encoded), raw=raw)

    def to_dict(self) -> dict:
        return parse_json(self._encoded)

    def to_bytes(self) -> bytes:
        return self._encoded

    def raw_bytes(self) -> bytes:
        return self._raw_evidence

    @property
    def snapshot_id(self) -> str:
        return self.to_dict()["snapshot_id"]

    @property
    def source_snapshot_id(self) -> str:
        return self.to_dict()["source"]["source_snapshot_id"]


def prepare_bytes(raw: bytes, *, file_format: str, source_ref: str, captured_at: str,
                  source_url: str | None = None, source_kind: str = "HUMAN_SUPPLIED",
                  synthetic: bool = False, declared_verification: str | None = None,
                  field_map: dict | None = None,
                  json_rows_pointer: str | None = None) -> ProductListingSnapshot:
    """Prepare all rows atomically in memory; no IO, network, writes or clock.

    Default JSON is {schema_version: ManualProductListings.v1, rows: [...]};
    explicit JSON Pointer and a flat field_map support pre-exported API JSON.
    CSV is comma-separated RFC4180 with an optional UTF-8 BOM and a header.
    """
    value = _build_document(
        raw, file_format=file_format, source_ref=source_ref, captured_at=captured_at,
        source_url=source_url, source_kind=source_kind, synthetic=synthetic,
        declared_verification=declared_verification, field_map=field_map,
        json_rows_pointer=json_rows_pointer,
    )
    return ProductListingSnapshot(value, raw=raw)


def read_snapshot(path, *, root, file_format: str | None = None, **metadata) -> ProductListingSnapshot:
    """Read a local file inside root; supply prepare_bytes() metadata explicitly.

    Source ref/URL are metadata only, independent of the actual input path.
    Only .json and .csv are inferred; pass file_format for any other suffix.
    """
    raw = read_local(path, root=root)
    if file_format is None:
        file_format = {".json": "json", ".csv": "csv"}.get(Path(path).suffix.lower())
    return prepare_bytes(raw, file_format=file_format, **metadata)
