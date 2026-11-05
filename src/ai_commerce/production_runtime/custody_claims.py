"""B0-S.3a closed, public, INERT drafts. No issuer or custody is established.

Supplied labels, hashes, epochs, keys and times are untrusted data. Canonical
sorted ASCII JSON is content identity only, not RFC 8785, signature verification,
possession, independent display, authenticated time or a durable replay register.
There is no IO, signer, credential access, enrollment or execution connection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import re

from .security_claims import (
    ClaimError, canonical_bytes, closed, decode_json, is_hash, is_ref, logical_time,
)

KINDS = (
    "CustodyEnrollmentCandidate.v1", "CustodyChallengeDraft.v1",
    "RevocationNoticeDraft.v1", "BrokerTransactionDraft.v1",
)
AUDIENCES = ("PULSE_CUSTODY_V1", "PULSE_AUDIT_V1")
ENROLLMENT_STATES = (
    "UNENROLLED", "ENROLLMENT_PENDING", "CHALLENGED", "HUMAN_VERIFIED",
    "ISSUER_PROVEN", "ENROLLED", "REVOKED", "EXPIRED", "UNKNOWN",
)
CUSTODY_MODES = (
    "ATTENDED_EPHEMERAL", "OFF_HOST_BROKER", "PASSKEY_ONLY",
    "WINDOWS_PROTECTION_ONLY", "LOCAL_SERVICE",
)
ACTIONS = (
    "GENERATE_MEDIA", "PUBLISH", "POST", "GO_PUBLISH", "GO_SPEND", "NETWORK",
    "IMPORT_REMOTE", "IMPORT_ANALYTICS", "UPLOAD", "FINANCIAL", "PURCHASE",
    "TRANSFER", "SETTLE",
)
CLAIM_CODES = (
    "CLOSED_FIELDS", "JSON_SIZE", "JSON_COMPLEXITY", "JSON_TYPE", "JSON_TEXT",
    "JSON_NONFINITE", "JSON_INTEGER", "JSON_DUPLICATE_KEY", "JSON_SYNTAX",
    "UNKNOWN_DRAFT_KIND", "INVALID_CUSTODY_PIN", "INVALID_CUSTODY_LABEL",
    "INVALID_CUSTODY_WINDOW", "INVALID_CUSTODY_EPOCH", "INVALID_CUSTODY_SCOPE",
    "INVALID_CUSTODY_SPEND", "CUSTODY_DIGEST_MISMATCH",
)
COMMON_FIELDS = (
    "schema_version", "issuer_ref", "public_key_id", "policy_audience", "source_sha",
    "nonce", "issued_at", "expires_at", "revocation_epoch", "untrusted_labels",
)
TRANSACTION_FIELDS = (
    "journey_id", "directive_version", "directive_sha256", "final_media_sha256",
    "provider_ref", "account_ref", "surface", "action", "semantic_target_ref",
    "semantic_target_sha256", "brain_decision_ref", "brain_decision_sha256",
    "human_grant_ref", "human_grant_sha256", "provider_entitlement_ref",
    "provider_entitlement_sha256", "asset_rights_ref", "asset_rights_sha256",
    "spend_grant_ref", "spend_grant_sha256", "currency", "spend_minor",
    "spend_ceiling_minor", "idempotency_key", "transaction_sha256",
)
SCOPE_FIELDS = (
    "issuer_ref", "public_key_id", "source_sha", "revocation_epoch",
    *tuple(k for k in TRANSACTION_FIELDS if k not in ("idempotency_key", "transaction_sha256")),
)
EXTRA_FIELDS = (
    ("candidate_ref", "trust_root_sha256", "root_origin", "custody_mode"),
    ("challenge_ref", "candidate_sha256", "transaction_sha256", "display_sha256", "idempotency_key"),
    ("notice_ref", "previous_epoch", "effective_at", "reason_code", "replacement_key_id"),
    TRANSACTION_FIELDS,
)


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ClaimError(code)


def _enum(value: object, choices: tuple[str, ...], code: str) -> None:
    _require(type(value) is str and value in choices, code)


def _positive(value: object) -> bool:
    return type(value) is int and 1 <= value <= 2**31 - 1


def _labels(value: object) -> None:
    closed(value, ("signature", "attestation", "verified", "enrolled", "key"))
    _enum(value["signature"], ("ABSENT", "UNPROVEN", "CLAIMED"), "INVALID_CUSTODY_LABEL")
    _enum(value["attestation"], (
        "ABSENT", "UNPROVEN", "CLAIMED", "DPAPI_SAME_SID", "CREDENTIAL_MANAGER_SAME_SID",
        "TPM_ONLY", "PASSKEY_ONLY", "LOCAL_ACL", "LOCAL_SERVICE",
    ), "INVALID_CUSTODY_LABEL")
    _require(type(value["verified"]) is bool and type(value["enrolled"]) is bool,
             "INVALID_CUSTODY_LABEL")
    _enum(value["key"], ("PUBLIC_LABEL_ONLY", "CLAIMED"), "INVALID_CUSTODY_LABEL")


def semantic_target_digest(value: object) -> str:
    """Comparison identity excludes journey, media, grants, nonce, key and TTL.

    The opaque target identifies a logical effect slot. Changing its label does
    not authorize another attempt; only a future independent register could
    detect aliases. This calculation is neither that register nor an authority.
    """
    data = decode_json(value)
    _require(type(data) is dict, "INVALID_CUSTODY_SCOPE")
    for key, prefix in (("provider_ref", "provider"), ("account_ref", "account"),
                        ("surface", "surface"), ("semantic_target_ref", "target")):
        _require(is_ref(data.get(key), prefix), "INVALID_CUSTODY_SCOPE")
    _enum(data.get("action"), ACTIONS, "INVALID_CUSTODY_SCOPE")
    keys = ("provider_ref", "account_ref", "surface", "action", "semantic_target_ref")
    return sha256(canonical_bytes({k: data[k] for k in keys})).hexdigest()


def transaction_digest(value: object) -> str:
    """Content identity, not a signature, grant, receipt or enrollment proof."""
    data = decode_json(value)
    _require(type(data) is dict, "INVALID_CUSTODY_SCOPE")
    return sha256(canonical_bytes({k: v for k, v in data.items()
                                  if k != "transaction_sha256"})).hexdigest()


@dataclass(frozen=True, slots=True)
class CustodyDraft:
    """Detached inert bytes. Direct construction cannot confer authority."""
    raw: bytes = field(repr=False)

    def __post_init__(self):
        _require(type(self.raw) is bytes, "JSON_TYPE")

    def to_dict(self) -> dict:
        data = decode_json(self.raw)
        _require(type(data) is dict, "CLOSED_FIELDS")
        return data

    @property
    def sha256(self) -> str:
        return sha256(canonical_bytes(self.to_dict())).hexdigest()


def validate_draft(value: object) -> CustodyDraft:
    """Validate shape and internal identity only; all live transitions UNSUPPORTED.

    Reuses S.2's bounded decoder: <=16384 bytes, depth 8, 512 nodes, <=64 entries,
    <=1024 characters/string, exact built-in types; duplicate keys, cycles,
    nonfinite values and unknown fields reject with finite data-free diagnostics.
    Explicit time is checked for shape here, and for freshness by custody_gate.
    """
    data = decode_json(value.raw if type(value) is CustodyDraft else value)
    _require(type(data) is dict, "CLOSED_FIELDS")
    kind = data.get("schema_version")
    _enum(kind, KINDS, "UNKNOWN_DRAFT_KIND")
    index = KINDS.index(kind)
    closed(data, (*COMMON_FIELDS, *EXTRA_FIELDS[index]))
    _require(is_ref(data["issuer_ref"], "issuer") and is_ref(data["public_key_id"], "key")
             and is_hash(data["source_sha"], 40) and is_ref(data["nonce"], "nonce"),
             "INVALID_CUSTODY_PIN")
    _enum(data["policy_audience"], AUDIENCES, "INVALID_CUSTODY_SCOPE")
    _require(_positive(data["revocation_epoch"]), "INVALID_CUSTODY_EPOCH")
    issued, expires = data["issued_at"], data["expires_at"]
    maximum = 120 if index == 1 else 900
    _require(logical_time(issued) and logical_time(expires)
             and 0 < expires - issued <= maximum, "INVALID_CUSTODY_WINDOW")
    _labels(data["untrusted_labels"])
    if index == 0:
        _require(is_ref(data["candidate_ref"], "candidate") and is_hash(data["trust_root_sha256"]),
                 "INVALID_CUSTODY_PIN")
        _enum(data["root_origin"], ("OUT_OF_BAND_CLAIM", "PUBLIC_REPOSITORY", "GITHUB_COMMENT", "SAME_SID"),
              "INVALID_CUSTODY_LABEL")
        _enum(data["custody_mode"], CUSTODY_MODES, "INVALID_CUSTODY_LABEL")
    elif index == 1:
        _require(is_ref(data["challenge_ref"], "challenge")
                 and is_ref(data["idempotency_key"], "request")
                 and all(is_hash(data[k]) for k in
                         ("candidate_sha256", "transaction_sha256", "display_sha256")),
                 "INVALID_CUSTODY_PIN")
    elif index == 2:
        _require(is_ref(data["notice_ref"], "notice")
                 and (data["replacement_key_id"] is None or is_ref(data["replacement_key_id"], "key")),
                 "INVALID_CUSTODY_PIN")
        _require(type(data["previous_epoch"]) is int
                 and 0 <= data["previous_epoch"] < data["revocation_epoch"], "INVALID_CUSTODY_EPOCH")
        _require(logical_time(data["effective_at"])
                 and issued <= data["effective_at"] < expires, "INVALID_CUSTODY_WINDOW")
        _enum(data["reason_code"], ("KILL", "COMPROMISE", "ROTATION", "HUMAN_WITHDRAWAL"),
              "INVALID_CUSTODY_LABEL")
    else:
        _transaction(data)
    return CustodyDraft(canonical_bytes(data))


def _transaction(data: dict) -> None:
    refs = (
        ("journey_id", "journey"), ("provider_ref", "provider"), ("account_ref", "account"),
        ("surface", "surface"), ("semantic_target_ref", "target"), ("brain_decision_ref", "brain"),
        ("human_grant_ref", "grant"), ("provider_entitlement_ref", "entitlement"),
        ("asset_rights_ref", "rights"), ("idempotency_key", "request"),
    )
    hashes = ("directive_sha256", "final_media_sha256", "semantic_target_sha256",
              "brain_decision_sha256", "human_grant_sha256", "provider_entitlement_sha256",
              "asset_rights_sha256", "transaction_sha256")
    _require(all(is_ref(data[k], prefix) for k, prefix in refs)
             and all(is_hash(data[k]) for k in hashes) and _positive(data["directive_version"]),
             "INVALID_CUSTODY_PIN")
    _enum(data["action"], ACTIONS, "INVALID_CUSTODY_SCOPE")
    _require(type(data["currency"]) is str and re.fullmatch(r"[A-Z]{3}", data["currency"]) is not None,
             "INVALID_CUSTODY_SPEND")
    amount, ceiling = data["spend_minor"], data["spend_ceiling_minor"]
    _require(type(amount) is int and type(ceiling) is int and 0 <= amount <= ceiling <= 2**63 - 1,
             "INVALID_CUSTODY_SPEND")
    _require((data["spend_grant_ref"] is None and data["spend_grant_sha256"] is None) if ceiling == 0
             else (is_ref(data["spend_grant_ref"], "spend") and is_hash(data["spend_grant_sha256"])),
             "INVALID_CUSTODY_SPEND")
    _require(data["semantic_target_sha256"] == semantic_target_digest(data)
             and data["transaction_sha256"] == transaction_digest(data), "CUSTODY_DIGEST_MISMATCH")
