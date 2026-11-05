"""B0-S structural drafts only. Pure JSON handling, never issuance or authority.

UTC times are integer Unix seconds supplied by the caller. No wall clock,
signature, secret, issuer registry, persistent replay store or effect exists here.
Canonical bytes use sorted ASCII JSON; this is not RFC 8785 or RFC 9421.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re

MAX_BYTES = 16384
MAX_DEPTH = 8
MAX_NODES = 512
MAX_WINDOW_SECONDS = 900
LOCAL_ACTIONS = ("PLAN_OFFLINE", "RENDER_LOCAL_OFFLINE", "READ_STATUS", "EXPORT_DRAFT", "HANDOFF")
EXTERNAL_ACTIONS = ("NETWORK", "GENERATE_MEDIA", "PUBLISH", "POST", "GO_PUBLISH",
                    "GO_SPEND", "IMPORT_REMOTE", "IMPORT_ANALYTICS", "FINANCIAL",
                    "UPLOAD", "PURCHASE", "TRANSFER", "SETTLE")
KINDS = ("OfflineOperationIntent.v1", "ExternalEffectIntentDraft.v1")
CLAIM_CODES = (
    "JSON_SIZE", "JSON_COMPLEXITY", "JSON_TYPE", "JSON_TEXT", "JSON_NONFINITE",
    "JSON_INTEGER", "JSON_DUPLICATE_KEY", "JSON_SYNTAX", "CLOSED_FIELDS",
    "UNKNOWN_KIND", "UNKNOWN_DOMAIN", "UNKNOWN_ACTION", "INVALID_PIN",
    "INVALID_LOCAL_SCOPE", "INVALID_EXTERNAL_SCOPE", "INVALID_WINDOW",
    "WINDOW_NOT_CURRENT", "INVALID_SPEND", "REQUEST_DIGEST_MISMATCH",
)


class ClaimError(ValueError):
    """Finite diagnostics only; never include input text or private values."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ClaimError(code)


def canonical_bytes(value: object) -> bytes:
    """Bound before serialization, reject custom types, cycles and nonfinite JSON."""
    pending, nodes, size = [(value, 0)], 0, 0
    try:
        while pending:
            item, depth = pending.pop()
            nodes += 1
            require(depth <= MAX_DEPTH and nodes <= MAX_NODES, "JSON_COMPLEXITY")
            if type(item) is dict:
                require(len(item) <= 64 and all(type(k) is str for k in item), "JSON_TYPE")
                pending.extend((part, depth + 1) for pair in item.items() for part in pair)
            elif type(item) is list:
                require(len(item) <= 64, "JSON_COMPLEXITY")
                pending.extend((part, depth + 1) for part in item)
            elif type(item) is str:
                require(len(item) <= 1024, "JSON_TEXT")
                size += len(item.encode("utf-8"))
            elif type(item) is float:
                require(math.isfinite(item), "JSON_NONFINITE")
                size += 32
            elif type(item) is int:
                require(item.bit_length() <= 63, "JSON_INTEGER")
                size += 24
            elif item is None or type(item) is bool:
                size += 5
            else:
                raise ClaimError("JSON_TYPE")
            require(size <= MAX_BYTES, "JSON_SIZE")
        raw = json.dumps(value, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode("ascii")
        require(len(raw) <= MAX_BYTES, "JSON_SIZE")
        return raw
    except ClaimError:
        raise
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        raise ClaimError("JSON_TYPE") from None


def decode_json(value: object) -> object:
    """Raw bytes preserve duplicate-key detection; dict input is detached."""
    if type(value) is not bytes:
        return json.loads(canonical_bytes(value))
    require(0 < len(value) <= MAX_BYTES, "JSON_SIZE")

    def pairs(items):
        result = {}
        for key, part in items:
            require(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = part
        return result

    def constant(_):
        raise ClaimError("JSON_NONFINITE")

    try:
        detached = json.loads(value.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except ClaimError:
        raise
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise ClaimError("JSON_SYNTAX") from None
    canonical_bytes(detached)
    return detached


def closed(value: object, fields: tuple[str, ...]) -> None:
    require(type(value) is dict and set(value) == set(fields), "CLOSED_FIELDS")


def is_hash(value: object, length: int = 64) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is not None


def is_ref(value: object, prefix: str) -> bool:
    return type(value) is str and re.fullmatch(re.escape(prefix) + r":[0-9a-f]{64}", value) is not None


def logical_time(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


def request_digest(value: dict) -> str:
    """Public content hash, not a signature or authenticated transaction proof."""
    require(type(value) is dict, "JSON_TYPE")
    return sha256(canonical_bytes({k: v for k, v in value.items() if k != "request_sha256"})).hexdigest()


def target_digest(value: dict) -> str:
    """Local comparison key ignores request rekeying; not a durable replay register."""
    fields = ("domain", "journey_id", "source_sha", "directive_version", "directive_sha256",
              "approved_asset_sha256", "action", "provider_ref", "account_ref", "surface")
    return sha256(canonical_bytes({k: value[k] for k in fields})).hexdigest()


@dataclass(frozen=True, slots=True)
class StructuralDraft:
    """Inert bytes. Even construction of this class cannot grant permission."""
    raw: bytes

    def to_dict(self) -> dict:
        return json.loads(self.raw)


def validate_intent(value: object, *, at: int) -> StructuralDraft:
    """Strictly CLOSED drafts; structural success never means issued or valid authority."""
    data = decode_json(value)
    fields = ("schema_version", "domain", "attendance", "journey_id", "source_sha",
              "directive_version", "directive_sha256", "approved_asset_sha256", "action",
              "provider_ref", "account_ref", "surface", "currency", "spend_minor",
              "spend_ceiling_minor", "spend_ref", "rights", "provider_entitlement",
              "nonce", "idempotency_key", "issued_at", "expires_at", "request_sha256")
    closed(data, fields)
    require(data["schema_version"] in KINDS, "UNKNOWN_KIND")
    require(data["domain"] in ("LOCAL_UNPAID", "EXTERNAL_EFFECT"), "UNKNOWN_DOMAIN")
    require(type(data["action"]) is str and data["action"] in (*LOCAL_ACTIONS, *EXTERNAL_ACTIONS), "UNKNOWN_ACTION")
    require(is_ref(data["journey_id"], "journey") and is_hash(data["source_sha"], 40)
            and is_hash(data["directive_sha256"]) and is_hash(data["approved_asset_sha256"])
            and type(data["directive_version"]) is int and 1 <= data["directive_version"] <= 2**31 - 1
            and is_ref(data["nonce"], "nonce") and is_ref(data["idempotency_key"], "request")
            and is_hash(data["request_sha256"]), "INVALID_PIN")
    issued, expires = data["issued_at"], data["expires_at"]
    require(logical_time(at) and logical_time(issued) and logical_time(expires)
            and 0 < expires - issued <= MAX_WINDOW_SECONDS, "INVALID_WINDOW")
    require(issued <= at < expires, "WINDOW_NOT_CURRENT")
    rights, provider = data["rights"], data["provider_entitlement"]
    closed(rights, ("ref", "sha256", "status", "expires_at"))
    closed(provider, ("ref", "sha256", "status", "expires_at"))
    require(is_ref(rights["ref"], "rights") and is_hash(rights["sha256"])
            and rights["status"] in ("SYNTHETIC_FIXTURE", "LOCAL_PERMISSION", "CLAIMED", "UNKNOWN", "STALE", "EXPIRED", "REVOKED")
            and logical_time(rights["expires_at"])
            and is_hash(provider["sha256"]) and logical_time(provider["expires_at"]), "INVALID_PIN")
    require(type(data["spend_minor"]) is int and type(data["spend_ceiling_minor"]) is int
            and 0 <= data["spend_minor"] <= data["spend_ceiling_minor"] <= 2**63 - 1, "INVALID_SPEND")
    if data["schema_version"] == KINDS[0]:
        require(data["domain"] == "LOCAL_UNPAID" and data["attendance"] == "HUMAN_ATTENDED"
                and data["action"] in LOCAL_ACTIONS
                and (data["provider_ref"], data["account_ref"], data["surface"], data["currency"]) ==
                    ("LOCAL_OFFLINE", "LOCAL_ATTENDED", "LOCAL_DRAFT", "NONE")
                and data["spend_minor"] == data["spend_ceiling_minor"] == 0 and data["spend_ref"] is None
                and provider["ref"] == "NOT_APPLICABLE_OFFLINE"
                and provider["status"] == "NOT_APPLICABLE_OFFLINE", "INVALID_LOCAL_SCOPE")
    else:
        require(data["domain"] == "EXTERNAL_EFFECT" and data["action"] in EXTERNAL_ACTIONS
                and data["attendance"] in ("HUMAN_ATTENDED", "UNATTENDED")
                and all(is_ref(data[k], prefix) for k, prefix in
                        (("provider_ref", "provider"), ("account_ref", "account"), ("surface", "surface")))
                and is_ref(provider["ref"], "entitlement")
                and provider["status"] in ("CLAIMED", "UNKNOWN", "STALE", "EXPIRED", "REVOKED")
                and type(data["currency"]) is str and re.fullmatch(r"[A-Z]{3}", data["currency"]) is not None,
                "INVALID_EXTERNAL_SCOPE")
        require((data["spend_ref"] is None if data["spend_ceiling_minor"] == 0
                 else is_ref(data["spend_ref"], "spend")), "INVALID_SPEND")
    require(data["request_sha256"] == request_digest(data), "REQUEST_DIGEST_MISMATCH")
    return StructuralDraft(canonical_bytes(data))
