"""B0-S.3b pure standard-library structural intake for untrusted broker drafts.

All inputs are detached, closed, bounded data and are normalized as UNTRUSTED.
Canonical sorted ASCII JSON provides content identity only, never RFC 8785,
cryptographic signature verification, IAM authentication, secret custody,
live provider receipt validation, or execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
import re

MAX_BYTES = 16384
MAX_DEPTH = 8
MAX_NODES = 512
MAX_ENTRIES = 64
MAX_STRING = 1024
MAX_WINDOW_SECONDS = 900
MAX_REVOKE_SLA_SECONDS = 86400

KINDS = (
    "BrokerSelectionDraft.v1",
    "BrokerIsolationClaimDraft.v1",
    "BrokerReceiptDraft.v1",
)
AUDIENCES = (
    "PULSE_BROKER_SELECTION_V1",
    "PULSE_CUSTODY_V1",
    "PULSE_AUDIT_V1",
)
KIND_AUDIENCE = {
    "BrokerSelectionDraft.v1": "PULSE_BROKER_SELECTION_V1",
    "BrokerIsolationClaimDraft.v1": "PULSE_CUSTODY_V1",
    "BrokerReceiptDraft.v1": "PULSE_CUSTODY_V1",
}
ROLES = (
    "DESIGN_PROPOSAL",
    "ATTENDED_OPERATOR",
    "EXTERNAL_BROKER_CLAIM",
    "CLOUD_DEPLOY_ADMIN",
    "SAME_SID_RUNNER",
    "PUBLIC_GITHUB",
)
KIND_ALLOWED_ROLES = {
    "BrokerSelectionDraft.v1": ("DESIGN_PROPOSAL", "ATTENDED_OPERATOR"),
    "BrokerIsolationClaimDraft.v1": ("DESIGN_PROPOSAL", "EXTERNAL_BROKER_CLAIM"),
    "BrokerReceiptDraft.v1": ("EXTERNAL_BROKER_CLAIM", "ATTENDED_OPERATOR"),
}
PATTERNS = (
    "OFF_HOST_ISOLATED_BROKER",
    "CLOUD_WORKER_DURABLE_STATE",
    "CLOUD_MANAGED_COMPUTE_VAULT",
    "SEPARATE_DEVICE_BROKER",
    "SAME_SID_LOCAL_SERVICE",
    "PASSKEY_UV_ONLY",
    "KMS_OR_SECRET_BINDING_ONLY",
    "EPHEMERAL_ATTENDED_HOST",
)
VENDOR_SLOTS = (
    "UNSELECTED_HUMAN_DECISION_REQUIRED",
    "CLOUDFLARE_CLAIMED",
    "AZURE_CLAIMED",
    "GCP_CLAIMED",
    "DEVICE_CLAIMED",
)
CREDENTIAL_CUSTODY_MODES = (
    "BROKER_HOLDS_PROVIDER_CREDENTIALS",
    "RETURN_BEARER_TO_Z1",
    "RETURN_BEARER_TO_Z2",
    "SAME_SID_DPAPI",
    "SAME_SID_CREDENTIAL_MANAGER",
    "CLOUD_KMS_KEY_ONLY",
)
HUMAN_APPROVAL_MODES = (
    "INDEPENDENT_WYSIWYS_DEVICE",
    "WEBAUTHN_UV_ONLY",
    "SAME_SID_TERMINAL",
    "GITHUB_COMMENT",
    "STANDING_DELEGATION",
)
REPLAY_STORAGE_MODES = (
    "DURABLE_ATOMIC_OUTSIDE_SID",
    "IN_MEMORY_EPHEMERAL",
    "LOCAL_WORKTREE_JOURNAL",
    "ABSENT",
)
BUDGET_RESERVATION_MODES = (
    "ATOMIC_PRE_CALL_RESERVATION",
    "POST_CALL_ESTIMATE",
    "NONE",
)
DEPLOY_ADMIN_MODES = (
    "SEPARATE_ADMIN_AND_AUDITOR",
    "SAME_SID_DEPLOY_ADMIN",
    "GITHUB_RUNNER_DEPLOY_ADMIN",
    "UNPROVEN",
)
ROOT_ENROLLMENT_MODES = (
    "OUT_OF_BAND_HUMAN_CEREMONY",
    "SELF_ASSERTED_OBJECT",
    "PUBLIC_REPO_JSON",
    "SAME_SID_GENERATED",
    "UNENROLLED",
)
AUDITOR_INDEPENDENCE_MODES = (
    "OUT_OF_BAND_INDEPENDENT_CLAIMED",
    "BROKER_SELF_CERTIFIED",
    "CLOUD_IAM_ROLE_ONLY",
    "SAME_SID_RUNNER",
    "ABSENT",
)
SAME_SID_ACCESS_MODES = (
    "DENIED_CLAIMED",
    "READ_ALLOWED",
    "WRITE_ALLOWED",
    "ADMIN_OVERLAP",
    "UNKNOWN",
)
CLOUD_ADMIN_OVERLAP_MODES = (
    "NONE_CLAIMED",
    "SAME_SID_HAS_DEPLOY_TOKEN",
    "GITHUB_WORKFLOW_ADMIN",
    "KMS_DECRYPT_SHARED",
    "UNKNOWN",
)
SECRET_BINDING_MODES = (
    "FULL_POLICY_MEDIATED_BY_BROKER",
    "WORKER_ENV_BINDING_ONLY",
    "KMS_SIGN_DECRYPT_ONLY",
    "LONG_LIVED_BEARER_EXPORT",
    "DPAPI_SAME_SID",
)
WYSIWYS_DISPLAY_MODES = (
    "INDEPENDENT_TRUSTED_DEVICE_DISPLAY",
    "WEBAUTHN_UV_ASSERTION_ONLY",
    "Z1_RENDERED_PREVIEW",
    "Z2_LOCAL_CONSOLE",
    "NONE",
)
CLAIMED_STATUS_KEYS = (
    "isolated",
    "keys_safe",
    "human_verified",
    "provider_ready",
)
ACTIONS = (
    "GENERATE_MEDIA",
    "PUBLISH",
    "POST",
    "GO_PUBLISH",
    "GO_SPEND",
    "NETWORK",
    "IMPORT_REMOTE",
    "IMPORT_ANALYTICS",
    "UPLOAD",
    "FINANCIAL",
    "PURCHASE",
    "TRANSFER",
    "SETTLE",
)
EFFECT_OBSERVATIONS = (
    "NONE",
    "UNKNOWN_EFFECT",
    "CLAIMED_PROVIDER_CONFIRMED",
    "CLAIMED_NO_EFFECT",
    "TIMEOUT",
    "CANCELLED",
)
INTAKE_CODES = (
    "CLOSED_FIELDS",
    "JSON_SIZE",
    "JSON_COMPLEXITY",
    "JSON_TYPE",
    "JSON_TEXT",
    "JSON_NONFINITE",
    "JSON_INTEGER",
    "JSON_DUPLICATE_KEY",
    "JSON_SYNTAX",
    "UNKNOWN_DRAFT_KIND",
    "INVALID_BROKER_PIN",
    "INVALID_BROKER_SCOPE",
    "INVALID_BROKER_AUDIENCE",
    "INVALID_BROKER_ROLE",
    "INVALID_BROKER_WINDOW",
    "INVALID_BROKER_EPOCH",
    "INVALID_BROKER_SPEND",
    "INVALID_UNTRUSTED_LABEL",
    "SECRET_OR_PATH_FORBIDDEN",
    "BROKER_DIGEST_MISMATCH",
)
COMMON_FIELDS = (
    "schema_version",
    "draft_ref",
    "issuer_ref",
    "public_key_id",
    "policy_audience",
    "actor_role",
    "source_sha",
    "nonce",
    "issued_at",
    "expires_at",
    "revocation_epoch",
    "input_trust",
    "draft_sha256",
)
SELECTION_FIELDS = (
    "selection_ref",
    "architecture_pattern",
    "vendor_neutral_slot",
    "credential_custody_mode",
    "bearer_returned_to_caller",
    "human_approval_mode",
    "replay_storage_mode",
    "budget_reservation_mode",
    "deploy_admin_separation",
    "revoke_kill_sla_seconds",
    "trial_mode",
)
ISOLATION_CLAIM_FIELDS = (
    "claim_ref",
    "selection_ref",
    "trust_root_sha256",
    "root_enrollment_mode",
    "auditor_ref",
    "auditor_independence",
    "same_sid_access",
    "cloud_admin_overlap",
    "secret_binding_mode",
    "wysiwys_display_mode",
    "canary_redaction_sha256",
    "claimed_statuses",
)
RECEIPT_FIELDS = (
    "receipt_ref",
    "journey_id",
    "directive_version",
    "directive_sha256",
    "final_media_sha256",
    "provider_ref",
    "account_ref",
    "surface",
    "action",
    "semantic_target_ref",
    "semantic_target_sha256",
    "transaction_sha256",
    "display_sha256",
    "brain_decision_ref",
    "brain_decision_sha256",
    "human_grant_ref",
    "human_grant_sha256",
    "provider_entitlement_ref",
    "provider_entitlement_sha256",
    "asset_rights_ref",
    "asset_rights_sha256",
    "spend_grant_ref",
    "spend_grant_sha256",
    "currency",
    "spend_minor",
    "spend_ceiling_minor",
    "idempotency_key",
    "reservation_ref",
    "effect_observation",
)
EXTRA_FIELDS = (SELECTION_FIELDS, ISOLATION_CLAIM_FIELDS, RECEIPT_FIELDS)
RECEIPT_SCOPE_FIELDS = (
    "issuer_ref",
    "public_key_id",
    "source_sha",
    "revocation_epoch",
    "journey_id",
    "directive_version",
    "directive_sha256",
    "final_media_sha256",
    "provider_ref",
    "account_ref",
    "surface",
    "action",
    "semantic_target_ref",
    "semantic_target_sha256",
    "transaction_sha256",
    "brain_decision_ref",
    "brain_decision_sha256",
    "human_grant_ref",
    "human_grant_sha256",
    "provider_entitlement_ref",
    "provider_entitlement_sha256",
    "asset_rights_ref",
    "asset_rights_sha256",
    "spend_grant_ref",
    "spend_grant_sha256",
    "currency",
    "spend_minor",
    "spend_ceiling_minor",
)
_FORBIDDEN_VALUE_RE = re.compile(
    r"(?:Bearer\s+|[A-Za-z]:\\|\\\\|/Users/|/home/|\.\./|\.\.\\|BEGIN\s+(?:RSA|EC|PRIVATE)\s+KEY)",
    re.IGNORECASE,
)


class IntakeError(ValueError):
    """Finite data-free intake diagnostic; never echoes input values or secrets."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise IntakeError(code)


def _enum(value: object, choices: tuple[str, ...], code: str) -> None:
    require(type(value) is str and value in choices, code)


def _positive_31(value: object) -> bool:
    return type(value) is int and 1 <= value <= 2**31 - 1


def logical_time(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


def is_hash(value: object, length: int = 64) -> bool:
    return (
        type(value) is str
        and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is not None
    )


def is_ref(value: object, prefix: str) -> bool:
    return (
        type(value) is str
        and re.fullmatch(re.escape(prefix) + r":[0-9a-f]{64}", value) is not None
    )


def closed(value: object, fields: tuple[str, ...]) -> None:
    require(type(value) is dict and set(value) == set(fields), "CLOSED_FIELDS")


def canonical_bytes(value: object) -> bytes:
    """Bound complexity and size before serialization; reject cycles and nonfinite numbers."""
    pending, nodes, size = [(value, 0)], 0, 0
    try:
        while pending:
            item, depth = pending.pop()
            nodes += 1
            require(depth <= MAX_DEPTH and nodes <= MAX_NODES, "JSON_COMPLEXITY")
            if type(item) is dict:
                require(
                    len(item) <= MAX_ENTRIES and all(type(k) is str for k in item),
                    "JSON_TYPE",
                )
                pending.extend(
                    (part, depth + 1) for pair in item.items() for part in pair
                )
            elif type(item) is list:
                require(len(item) <= MAX_ENTRIES, "JSON_COMPLEXITY")
                pending.extend((part, depth + 1) for part in item)
            elif type(item) is str:
                require(len(item) <= MAX_STRING, "JSON_TEXT")
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
                raise IntakeError("JSON_TYPE")
            require(size <= MAX_BYTES, "JSON_SIZE")
        raw = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        require(len(raw) <= MAX_BYTES, "JSON_SIZE")
        return raw
    except IntakeError:
        raise
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        raise IntakeError("JSON_TYPE") from None


def decode_json(value: object) -> object:
    """Decode raw UTF-8 JSON bytes with duplicate-key and nonfinite rejection."""
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
        raise IntakeError("JSON_NONFINITE")

    try:
        detached = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=constant,
        )
    except IntakeError:
        raise
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise IntakeError("JSON_SYNTAX") from None
    canonical_bytes(detached)
    return detached


def _check_no_forbidden_strings(value: object) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is dict:
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
        elif type(item) is str:
            require(_FORBIDDEN_VALUE_RE.search(item) is None, "SECRET_OR_PATH_FORBIDDEN")


def semantic_target_digest(value: object) -> str:
    """Content digest of logical target slot; excludes nonce, request key, TTL and spend."""
    data = decode_json(value)
    require(type(data) is dict, "INVALID_BROKER_SCOPE")
    for key, prefix in (
        ("provider_ref", "provider"),
        ("account_ref", "account"),
        ("surface", "surface"),
        ("semantic_target_ref", "target"),
    ):
        require(is_ref(data.get(key), prefix), "INVALID_BROKER_PIN")
    _enum(data.get("action"), ACTIONS, "INVALID_BROKER_SCOPE")
    keys = ("provider_ref", "account_ref", "surface", "action", "semantic_target_ref")
    return sha256(canonical_bytes({k: data[k] for k in keys})).hexdigest()


def draft_digest(value: object) -> str:
    """Canonical content digest excluding draft_sha256; never a signature or grant."""
    data = decode_json(value)
    require(type(data) is dict, "INVALID_BROKER_SCOPE")
    return sha256(
        canonical_bytes({k: v for k, v in data.items() if k != "draft_sha256"})
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class BrokerIntakeDraft:
    """Detached inert bytes explicitly labeled UNTRUSTED. Never confers authority."""

    raw: bytes = field(repr=False)
    trust_label: str = "UNTRUSTED"

    def __post_init__(self) -> None:
        require(type(self.raw) is bytes, "JSON_TYPE")
        require(
            type(self.trust_label) is str and self.trust_label == "UNTRUSTED",
            "INVALID_UNTRUSTED_LABEL",
        )

    def to_dict(self) -> dict:
        data = decode_json(self.raw)
        require(type(data) is dict, "CLOSED_FIELDS")
        return data

    def to_normalized_dict(self) -> dict:
        data = self.to_dict()
        return {
            "trust_label": "UNTRUSTED",
            "verification_status": "UNPROVEN",
            "verified": False,
            "authority": "NONE",
            "schema_version": data["schema_version"],
            "draft_sha256": self.sha256,
            "claim_assessments": {k: "UNPROVEN" for k in CLAIMED_STATUS_KEYS},
            "untrusted_payload": data,
        }

    @property
    def sha256(self) -> str:
        return sha256(canonical_bytes(self.to_dict())).hexdigest()


def validate_broker_draft(value: object) -> BrokerIntakeDraft:
    """Validate strict closed shape and content digest; label input UNTRUSTED."""
    raw_input = value.raw if type(value) is BrokerIntakeDraft else value
    if type(value) is BrokerIntakeDraft:
        require(value.trust_label == "UNTRUSTED", "INVALID_UNTRUSTED_LABEL")
    data = decode_json(raw_input)
    require(type(data) is dict, "CLOSED_FIELDS")
    kind = data.get("schema_version")
    _enum(kind, KINDS, "UNKNOWN_DRAFT_KIND")
    index = KINDS.index(kind)
    closed(data, (*COMMON_FIELDS, *EXTRA_FIELDS[index]))
    _check_no_forbidden_strings(data)
    require(data["input_trust"] == "UNTRUSTED", "INVALID_UNTRUSTED_LABEL")
    _enum(data["policy_audience"], AUDIENCES, "INVALID_BROKER_AUDIENCE")
    require(
        data["policy_audience"] == KIND_AUDIENCE[kind],
        "INVALID_BROKER_AUDIENCE",
    )
    _enum(data["actor_role"], ROLES, "INVALID_BROKER_ROLE")
    require(
        data["actor_role"] in KIND_ALLOWED_ROLES[kind],
        "INVALID_BROKER_ROLE",
    )
    require(
        is_ref(data["draft_ref"], "draft")
        and is_ref(data["issuer_ref"], "issuer")
        and is_ref(data["public_key_id"], "key")
        and is_hash(data["source_sha"], 40)
        and is_ref(data["nonce"], "nonce")
        and is_hash(data["draft_sha256"]),
        "INVALID_BROKER_PIN",
    )
    require(_positive_31(data["revocation_epoch"]), "INVALID_BROKER_EPOCH")
    issued, expires = data["issued_at"], data["expires_at"]
    require(
        logical_time(issued)
        and logical_time(expires)
        and 0 < expires - issued <= MAX_WINDOW_SECONDS,
        "INVALID_BROKER_WINDOW",
    )
    if index == 0:
        _validate_selection(data)
    elif index == 1:
        _validate_isolation_claim(data)
    else:
        _validate_receipt(data)
    require(data["draft_sha256"] == draft_digest(data), "BROKER_DIGEST_MISMATCH")
    return BrokerIntakeDraft(canonical_bytes(data), "UNTRUSTED")


def normalize_untrusted_draft(value: object) -> dict:
    """Parse and return detached normalized representation explicitly labeled UNTRUSTED."""
    return validate_broker_draft(value).to_normalized_dict()


def _validate_selection(data: dict) -> None:
    require(is_ref(data["selection_ref"], "selection"), "INVALID_BROKER_PIN")
    _enum(data["architecture_pattern"], PATTERNS, "INVALID_BROKER_SCOPE")
    _enum(data["vendor_neutral_slot"], VENDOR_SLOTS, "INVALID_BROKER_SCOPE")
    _enum(data["credential_custody_mode"], CREDENTIAL_CUSTODY_MODES, "INVALID_BROKER_SCOPE")
    require(type(data["bearer_returned_to_caller"]) is bool, "INVALID_UNTRUSTED_LABEL")
    _enum(data["human_approval_mode"], HUMAN_APPROVAL_MODES, "INVALID_BROKER_SCOPE")
    _enum(data["replay_storage_mode"], REPLAY_STORAGE_MODES, "INVALID_BROKER_SCOPE")
    _enum(data["budget_reservation_mode"], BUDGET_RESERVATION_MODES, "INVALID_BROKER_SCOPE")
    _enum(data["deploy_admin_separation"], DEPLOY_ADMIN_MODES, "INVALID_BROKER_SCOPE")
    sla = data["revoke_kill_sla_seconds"]
    require(
        type(sla) is int and 0 <= sla <= MAX_REVOKE_SLA_SECONDS,
        "INVALID_BROKER_WINDOW",
    )
    require(
        type(data["trial_mode"]) is str
        and data["trial_mode"] == "NO_CREDENTIALS_SYNTHETIC_ONLY",
        "INVALID_BROKER_SCOPE",
    )


def _validate_isolation_claim(data: dict) -> None:
    require(
        is_ref(data["claim_ref"], "claim")
        and is_ref(data["selection_ref"], "selection")
        and is_ref(data["auditor_ref"], "auditor")
        and is_hash(data["trust_root_sha256"])
        and is_hash(data["canary_redaction_sha256"]),
        "INVALID_BROKER_PIN",
    )
    _enum(data["root_enrollment_mode"], ROOT_ENROLLMENT_MODES, "INVALID_BROKER_SCOPE")
    _enum(data["auditor_independence"], AUDITOR_INDEPENDENCE_MODES, "INVALID_BROKER_SCOPE")
    _enum(data["same_sid_access"], SAME_SID_ACCESS_MODES, "INVALID_BROKER_SCOPE")
    _enum(data["cloud_admin_overlap"], CLOUD_ADMIN_OVERLAP_MODES, "INVALID_BROKER_SCOPE")
    _enum(data["secret_binding_mode"], SECRET_BINDING_MODES, "INVALID_BROKER_SCOPE")
    _enum(data["wysiwys_display_mode"], WYSIWYS_DISPLAY_MODES, "INVALID_BROKER_SCOPE")
    statuses = data["claimed_statuses"]
    closed(statuses, CLAIMED_STATUS_KEYS)
    require(
        all(type(statuses[k]) is bool for k in CLAIMED_STATUS_KEYS),
        "INVALID_UNTRUSTED_LABEL",
    )


def _validate_receipt(data: dict) -> None:
    refs = (
        ("receipt_ref", "receipt"),
        ("journey_id", "journey"),
        ("provider_ref", "provider"),
        ("account_ref", "account"),
        ("surface", "surface"),
        ("semantic_target_ref", "target"),
        ("brain_decision_ref", "brain"),
        ("human_grant_ref", "grant"),
        ("provider_entitlement_ref", "entitlement"),
        ("asset_rights_ref", "rights"),
        ("idempotency_key", "request"),
        ("reservation_ref", "reservation"),
    )
    hashes = (
        "directive_sha256",
        "final_media_sha256",
        "semantic_target_sha256",
        "transaction_sha256",
        "display_sha256",
        "brain_decision_sha256",
        "human_grant_sha256",
        "provider_entitlement_sha256",
        "asset_rights_sha256",
    )
    require(
        all(is_ref(data[k], prefix) for k, prefix in refs)
        and all(is_hash(data[k]) for k in hashes)
        and _positive_31(data["directive_version"]),
        "INVALID_BROKER_PIN",
    )
    _enum(data["action"], ACTIONS, "INVALID_BROKER_SCOPE")
    _enum(data["effect_observation"], EFFECT_OBSERVATIONS, "INVALID_BROKER_SCOPE")
    require(
        type(data["currency"]) is str
        and re.fullmatch(r"[A-Z]{3}", data["currency"]) is not None,
        "INVALID_BROKER_SPEND",
    )
    amount, ceiling = data["spend_minor"], data["spend_ceiling_minor"]
    require(
        type(amount) is int
        and type(ceiling) is int
        and 0 <= amount <= ceiling <= 2**63 - 1,
        "INVALID_BROKER_SPEND",
    )
    require(
        (data["spend_grant_ref"] is None and data["spend_grant_sha256"] is None)
        if ceiling == 0
        else (
            is_ref(data["spend_grant_ref"], "spend")
            and is_hash(data["spend_grant_sha256"])
        ),
        "INVALID_BROKER_SPEND",
    )
    require(
        data["semantic_target_sha256"] == semantic_target_digest(data),
        "BROKER_DIGEST_MISMATCH",
    )
