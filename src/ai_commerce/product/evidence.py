"""Shared A12 subject pins and exact local raw-evidence replay, without IO."""
from __future__ import annotations

import re

from jsonschema import Draft202012Validator

from ai_commerce.core.provenance import sha256_bytes

from .core import canonical_bytes, parse_json, parse_timestamp, require
from .evidence_schemas import MAX_EVIDENCE_RECORDS
from .reader import ProductListingSnapshot
from .schemas import MAX_DOCUMENT_BYTES, MAX_RAW_BYTES, SOURCE_STATUS


def subject_for(snapshot: ProductListingSnapshot, ordinal: int, *, exact_variant=False) -> dict:
    require(type(snapshot) is ProductListingSnapshot, "LISTING_SNAPSHOT_REQUIRED")
    return _subject_from_document(snapshot.to_dict(), ordinal, exact_variant=exact_variant)


def _subject_from_document(value: dict, ordinal: int, *, exact_variant=False) -> dict:
    """Internal projection from an already validated, detached A12-01 document."""
    require(type(ordinal) is int and 1 <= ordinal <= len(value["listings"]), "INVALID_LISTING_ORDINAL")
    row = value["listings"][ordinal - 1]
    identity, variant = row["identity"], row["variant"]
    if exact_variant:
        require(variant["variant_id"] is not None and variant["asserted_variant_key"] is not None,
                "EXACT_VARIANT_REQUIRED")
    return {
        "snapshot_id": value["snapshot_id"],
        "source_snapshot_id": value["source"]["source_snapshot_id"],
        "snapshot_raw_sha256": value["source"]["raw_sha256"], "ordinal": ordinal,
        "source_namespace": identity["marketplace"], "identity_id": identity["identity_id"],
        "variant_record_id": variant["variant_record_id"],
        **{key: identity[key] for key in ("seller_id", "listing_id", "product_id")},
        "variant_id": variant["variant_id"], "variant_key": variant["asserted_variant_key"],
        "synthetic": value["source"]["synthetic"],
    }


def raw_payload(raw: bytes, pointer: str) -> dict:
    require(type(raw) is bytes, "RAW_EVIDENCE_REQUIRED")
    require(0 < len(raw) <= MAX_RAW_BYTES, "SOURCE_EMPTY_OR_TOO_LARGE")
    require(type(pointer) is str and len(pointer) <= 1024
            and (pointer == "" or pointer.startswith("/"))
            and len(pointer.split("/")) <= 17
            and re.search(r"~(?:[^01]|$)", pointer) is None, "INVALID_EVIDENCE_POINTER")
    value = parse_json(raw, limit=MAX_RAW_BYTES)
    for token in pointer.split("/")[1:] if pointer else ():
        token = token.replace("~1", "/").replace("~0", "~")
        if type(value) is dict:
            require(token in value, "EVIDENCE_POINTER_NOT_FOUND")
            value = value[token]
        elif type(value) is list:
            require(re.fullmatch(r"0|[1-9][0-9]*", token) is not None
                    and len(token) <= 6 and int(token) < len(value), "EVIDENCE_POINTER_NOT_FOUND")
            value = value[int(token)]
        else:
            require(False, "EVIDENCE_POINTER_NOT_FOUND")
    require(type(value) is dict, "EVIDENCE_OBJECT_REQUIRED")
    return value


def source_for(raw: bytes, *, source_ref: str, captured_at: str,
               source_kind: str, pointer: str) -> dict:
    raw_payload(raw, pointer)
    parse_timestamp(captured_at)
    raw_hash = sha256_bytes(raw)
    return {"source_ref": source_ref, "source_kind": source_kind, "source_status": SOURCE_STATUS,
            "captured_at": captured_at, "pointer": pointer, "raw_sha256": raw_hash,
            "raw_ref": "sha256:" + raw_hash, "raw_size_bytes": len(raw)}


def verify_source(value: dict, raw: bytes, snapshot, *, exact_variant=False) -> dict:
    source = value["source"]
    payload = raw_payload(raw, source["pointer"])
    require(source["raw_sha256"] == sha256_bytes(raw)
            and source["raw_ref"] == "sha256:" + source["raw_sha256"]
            and source["raw_size_bytes"] == len(raw), "RAW_PROVENANCE_MISMATCH")
    expected = subject_for(snapshot, value["subject"]["ordinal"], exact_variant=exact_variant)
    require(value["subject"] == expected, "PRODUCT_SUBJECT_MISMATCH")
    parse_timestamp(source["captured_at"])
    return payload


def validate_payload(payload: dict, definition: dict, code: str) -> None:
    canonical_bytes(payload)
    require(Draft202012Validator(definition).is_valid(payload), code)


def common_body(name: str, snapshot, ordinal: int, raw: bytes, *, source_ref: str,
                captured_at: str, source_kind: str, pointer: str, exact_variant=False) -> dict:
    return {
        "schema_version": name + ".v1", "source_status": SOURCE_STATUS,
        "verification_scope": "LOCAL_BYTES_HASH_AND_REPLAY_ONLY",
        "rights_status": "UNKNOWN", "publication_status": "NOT_ATTEMPTED",
        "subject": subject_for(snapshot, ordinal, exact_variant=exact_variant),
        "source": source_for(raw, source_ref=source_ref, captured_at=captured_at,
                             source_kind=source_kind, pointer=pointer),
    }


def bounded_records(records, expected_type) -> list:
    require(type(records) in (list, tuple) and len(records) <= MAX_EVIDENCE_RECORDS,
            "EVIDENCE_RECORD_LIMIT")
    require(all(type(item) is expected_type for item in records), "INVALID_EVIDENCE_RECORD")
    require(sum(len(item.to_bytes()) for item in records) <= MAX_DOCUMENT_BYTES,
            "DOCUMENT_TOO_LARGE")
    # Exact replay does not manufacture additional corroboration.
    unique = {item.to_bytes(): item for item in records}
    return [unique[key] for key in sorted(unique)]
