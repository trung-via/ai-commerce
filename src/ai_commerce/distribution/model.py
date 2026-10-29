"""Strict A07 portable contracts, canonical identity and evidence sanity checks."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

from ai_commerce.contracts.schema import _format_checker


class CapabilityError(ValueError):
    """Invalid registry evidence, contract, or local storage."""


_REJECT = re.compile(
    r'(?i)(?:"(?:access_token|refresh_token|client_secret|authorization|cookie|set-cookie|upload_token)"\s*:'
    r'|bearer\s+[a-z0-9._-]{8,}|[?&](?:access_token|refresh_token|client_secret|auth)=)'
)


def canonical(record: dict[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise CapabilityError("INVALID_TIME") from exc


def validate(name: str, record: Any) -> dict[str, Any]:
    if name not in ("ChannelCapabilitySnapshot", "ChannelCapabilityRequest"):
        raise CapabilityError("UNKNOWN_A07_CONTRACT")
    if not isinstance(record, dict):
        raise CapabilityError("INVALID_A07_RECORD_TYPE")
    schema = json.loads(files("ai_commerce.distribution").joinpath("schemas", name + ".v1.schema.json").read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema, format_checker=_format_checker()).iter_errors(record))
    if errors:
        error = sorted(errors, key=lambda e: (list(map(str, e.absolute_path)), e.message))[0]
        raise CapabilityError(f"INVALID_{name}: {'/'.join(map(str, error.absolute_path)) or '$'}: {error.message}")
    if _REJECT.search(canonical(record).decode("utf-8")):
        raise CapabilityError("CREDENTIAL_MATERIAL_NOT_ALLOWED")
    return record


def identity(record: dict[str, Any]) -> str:
    """All capability dimensions, including action; absence is not wildcard."""
    return digest({key: record[key] for key in ("platform", "account_ref", "app_ref", "market", "surface", "action")})


def validate_snapshot(record: Any) -> dict[str, Any]:
    obj = validate("ChannelCapabilitySnapshot", record)
    observed, recorded = timestamp(obj["observed_at"]), timestamp(obj["recorded_at"])
    if recorded < observed:
        raise CapabilityError("RECORDED_BEFORE_OBSERVED")
    if obj["expires_at"] is not None and timestamp(obj["expires_at"]) <= observed:
        raise CapabilityError("EXPIRY_NOT_AFTER_OBSERVATION")
    if obj.get("revoked_at") is not None and timestamp(obj["revoked_at"]) < observed:
        raise CapabilityError("REVOKED_BEFORE_OBSERVATION")
    if obj.get("supersedes_snapshot_id") == obj["snapshot_id"]:
        raise CapabilityError("SELF_SUPERSESSION")
    observed_names = [claim["name"] for claim in obj["scope_claims"]]
    if len(observed_names) != len(set(observed_names)):
        raise CapabilityError("DUPLICATE_SCOPE_CLAIM")
    for ev in obj["evidence_refs"]:
        if ev["scope"] == "TARGET":
            if ev["subject"] != {"account_ref": obj["account_ref"], "app_ref": obj["app_ref"]}:
                raise CapabilityError("EVIDENCE_SUBJECT_MISMATCH")
            if ev["integrity"] != "HASHED" or ev["sha256"] is None:
                raise CapabilityError("TARGET_EVIDENCE_MUST_BE_HASHED")
        elif ev["subject"] is not None:
            raise CapabilityError("PLATFORM_EVIDENCE_MUST_HAVE_NO_TARGET")
        if ev["integrity"] == "HASHED" and ev["sha256"] is None:
            raise CapabilityError("HASHED_EVIDENCE_MISSING_DIGEST")
        if ev["kind"] in ("PLATFORM_DOC", "POLICY_DOC") and ev["scope"] != "PLATFORM":
            raise CapabilityError("POLICY_IS_NOT_ACCOUNT_EVIDENCE")
        if timestamp(ev["captured_at"]) > recorded:
            raise CapabilityError("EVIDENCE_AFTER_RECORDING")
    if obj["source_mode"] == "TEST_FIXTURE" and any(ev["kind"] != "TEST_FIXTURE" for ev in obj["evidence_refs"]):
        raise CapabilityError("FIXTURE_EVIDENCE_MUST_BE_LABELED")
    return obj


def validate_request(record: Any) -> dict[str, Any]:
    return validate("ChannelCapabilityRequest", record)


def provider_profiles() -> dict[str, Any]:
    return json.loads(files("ai_commerce.distribution").joinpath("profiles.json").read_text("utf-8"))
