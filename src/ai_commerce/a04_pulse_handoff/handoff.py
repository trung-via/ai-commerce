"""Strict offline A04 -> PULSE handoff contract and validator.

Pure Python data/validation owner only. Reads existing A04CreativeHandoffReceipt.v1
and CreativeDirective.v1 models without modifying them. Never executes PULSE,
never claims WAIT_BRAIN, and performs no network, provider, publish, Windows
host, or WakeHub side effects.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from hashlib import sha256
import json
import math
import re
from typing import Collection, Mapping, Sequence

from ai_commerce.core.provenance import (
    canonical_bytes as a04_canonical_bytes,
    sha256_bytes,
)
from ai_commerce.media.generation import (
    A04_CREATIVE_CONSUMER_ID,
    A04_HANDOFF_RECEIPT_SCHEMA,
    A04CreativeHandoffReceipt,
)
from ai_commerce.production_runtime.contracts import (
    ContractError,
    ValidatedContract,
    draft_authorization,
    parse_contract,
    validate_contract,
)

HANDOFF_SCHEMA_VERSION = "A04PulseOfflineHandoffPacket.v1"
DIRECTIVE_SCHEMA_VERSION = "CreativeDirective.v1"
HANDOFF_ROUTE_ID = "a04-to-pulse-offline-contract"
HANDOFF_PRODUCER_ID = "ai_commerce.a04_pulse_handoff:build_a04_pulse_handoff_packet"
HANDOFF_STATUS = "HANDOFF_CONTRACT_VALIDATED"
PULSE_RECEIVER_EXECUTION = "NOT_ATTEMPTED"
WAIT_BRAIN_STATUS = "NOT_CLAIMED"

OFFLINE_ALLOWED_ACTION_CLASSES = frozenset({"PLAN_OFFLINE", "EXPORT_DRAFT"})
SUPPORTED_AUDIO_MODES = frozenset(
    {"SILENCE", "AMBIENT_ONLY", "SPOKEN", "MUSIC_ONLY", "MUSIC_AND_SPOKEN"}
)
SUPPORTED_CTA_MODES = frozenset({"NONE", "EXPLICIT"})
SUPPORTED_ASSET_EVIDENCE_TYPES = frozenset({"HUMAN_SUPPLIED", "SYNTHETIC_FIXTURE"})
SUPPORTED_DIRECTIVE_ACTORS = frozenset({"HUMAN", "FIXTURE"})

MAX_PACKET_BYTES = 131072
MAX_ASSET_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 4096
MAX_ASSETS = 64
MAX_TOOLS = 32

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SOURCE_SHA_RE = re.compile(r"[0-9a-f]{40}")
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:@-]*")
_PACKET_ID_RE = re.compile(r"a04pulse-[0-9a-f]{64}")
_LOGICAL_ID_RE = re.compile(r"logical:[0-9a-f]{64}")
_RECEIPT_ID_RE = re.compile(r"a04handoff-[0-9a-f]{64}")
_PACKAGE_ID_RE = re.compile(r"package:[0-9a-f]{64}")
_SELECTION_ID_RE = re.compile(r"selection:[0-9a-f]{64}")
_CONCEPT_ID_RE = re.compile(r"concept:[0-9a-f]{64}")


class A04PulseHandoffError(ValueError):
    """Fail-closed diagnostic with a bounded machine-readable error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise A04PulseHandoffError(code)


def _require_sha256(value: object, code: str = "INVALID_SHA256") -> None:
    _require(type(value) is str and _SHA256_RE.fullmatch(value) is not None, code)


def _require_source_sha(value: object, code: str = "INVALID_SOURCE_SHA") -> None:
    _require(type(value) is str and _SOURCE_SHA_RE.fullmatch(value) is not None, code)


def _require_ref(value: object, code: str = "INVALID_REFERENCE") -> None:
    _require(
        type(value) is str
        and 0 < len(value) <= 512
        and _REF_RE.fullmatch(value) is not None,
        code,
    )


def _require_text(value: object, code: str = "INVALID_TEXT", *, max_len: int = 1024) -> None:
    _require(
        type(value) is str
        and 0 < len(value) <= max_len
        and value == value.strip()
        and not any(ord(ch) < 32 for ch in value),
        code,
    )
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise A04PulseHandoffError(code) from None


def _require_pos_int(
    value: object,
    code: str = "INVALID_POSITIVE_INT",
    *,
    maximum: int = 2147483647,
) -> None:
    _require(type(value) is int and 1 <= value <= maximum, code)


def canonical_handoff_bytes(value: object) -> bytes:
    """Deterministic ASCII canonical JSON serialization with complexity/size bounds."""
    pending: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    size = 0
    try:
        while pending:
            item, depth = pending.pop()
            nodes += 1
            _require(depth <= MAX_JSON_DEPTH and nodes <= MAX_JSON_NODES, "JSON_COMPLEXITY_LIMIT")
            if type(item) is dict:
                _require(
                    len(item) <= 128 and all(type(k) is str for k in item),
                    "INVALID_JSON_OBJECT",
                )
                for k, v in item.items():
                    pending.append((k, depth + 1))
                    pending.append((v, depth + 1))
            elif type(item) is list:
                _require(len(item) <= 256, "JSON_ARRAY_LIMIT")
                for part in item:
                    pending.append((part, depth + 1))
            elif type(item) is str:
                _require(len(item) <= 8192, "JSON_TEXT_LIMIT")
                size += len(item.encode("utf-8"))
            elif type(item) is float:
                _require(math.isfinite(item), "JSON_NONFINITE")
                size += 32
            elif type(item) is int:
                _require(item.bit_length() <= 64, "JSON_INTEGER_LIMIT")
                size += 24
            elif item is None or type(item) is bool:
                size += 5
            else:
                raise A04PulseHandoffError("INVALID_JSON_VALUE")
            _require(size <= MAX_PACKET_BYTES, "DOCUMENT_TOO_LARGE")
        raw = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        _require(len(raw) <= MAX_PACKET_BYTES, "DOCUMENT_TOO_LARGE")
        return raw
    except A04PulseHandoffError:
        raise
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        raise A04PulseHandoffError("INVALID_JSON_VALUE") from exc


def _decode_strict_json(raw: bytes, *, invalid_code: str, duplicate_code: str) -> object:
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_PACKET_BYTES, invalid_code)

    def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, val in items:
            _require(key not in result, duplicate_code)
            result[key] = val
        return result

    def _constant(_: str) -> object:
        raise A04PulseHandoffError("JSON_NONFINITE")

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
    except A04PulseHandoffError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise A04PulseHandoffError(invalid_code) from exc


def derive_logical_identity(
    *,
    journey_id: str,
    cycle_id: str,
    job_id: str,
    attempt: int,
    package_id: str,
    package_revision: int,
    directive_version: int,
) -> str:
    """Derive a deterministic logical handoff identity independent of cosmetic metadata."""
    _require_ref(journey_id, "INVALID_JOURNEY_ID")
    _require_text(cycle_id, "INVALID_CYCLE_ID")
    _require_text(job_id, "INVALID_JOB_ID")
    _require_pos_int(attempt, "INVALID_ATTEMPT")
    _require(type(package_id) is str and _PACKAGE_ID_RE.fullmatch(package_id) is not None, "INVALID_PACKAGE_ID")
    _require_pos_int(package_revision, "INVALID_PACKAGE_REVISION")
    _require_pos_int(directive_version, "INVALID_DIRECTIVE_VERSION")
    body = {
        "attempt": attempt,
        "cycle_id": cycle_id,
        "directive_version": directive_version,
        "job_id": job_id,
        "journey_id": journey_id,
        "package_id": package_id,
        "package_revision": package_revision,
    }
    return "logical:" + sha256(canonical_handoff_bytes(body)).hexdigest()


@dataclass(frozen=True, kw_only=True)
class A04PulseAssetPin:
    """Exact asset ref, content SHA-256, provenance class, and offline rights binding."""

    asset_ref: str
    asset_sha256: str
    evidence_type: str
    rights_status: str
    rights_ref: str | None = None
    rights_sha256: str | None = None
    byte_length: int | None = None

    def __post_init__(self) -> None:
        _require_ref(self.asset_ref, "INVALID_ASSET_REF")
        _require_sha256(self.asset_sha256, "INVALID_ASSET_SHA256")
        _require(
            type(self.evidence_type) is str and self.evidence_type in SUPPORTED_ASSET_EVIDENCE_TYPES,
            "UNPROVEN_ASSET_EVIDENCE",
        )
        _require(
            type(self.rights_status) is str
            and self.rights_status in {"GRANTED", "NOT_APPLICABLE"},
            "RIGHTS_ABSENT_OR_RESTRICTED",
        )
        if self.evidence_type == "SYNTHETIC_FIXTURE":
            _require(
                self.rights_status == "NOT_APPLICABLE"
                and self.rights_ref is None
                and self.rights_sha256 is None,
                "FIXTURE_RIGHTS_MISMATCH",
            )
        else:
            _require(self.rights_status == "GRANTED", "RIGHTS_ABSENT_OR_RESTRICTED")
            _require(
                self.rights_ref is not None and self.rights_sha256 is not None,
                "RIGHTS_ABSENT_OR_RESTRICTED",
            )
            _require_ref(self.rights_ref, "INVALID_RIGHTS_REF")
            _require_sha256(self.rights_sha256, "INVALID_RIGHTS_SHA256")
        if self.byte_length is not None:
            _require_pos_int(self.byte_length, "INVALID_ASSET_BYTE_LENGTH", maximum=MAX_ASSET_BYTES)

    def to_dict(self) -> dict[str, object]:
        return {
            "asset_ref": self.asset_ref,
            "asset_sha256": self.asset_sha256,
            "byte_length": self.byte_length,
            "evidence_type": self.evidence_type,
            "rights_ref": self.rights_ref,
            "rights_sha256": self.rights_sha256,
            "rights_status": self.rights_status,
        }

    @classmethod
    def from_dict(cls, record: object) -> A04PulseAssetPin:
        names = {f.name for f in fields(cls)}
        _require(type(record) is dict and set(record) == names, "INVALID_ASSET_PIN_FIELDS")
        return cls(**record)


@dataclass(frozen=True, kw_only=True)
class A04PulseOfflineAuthority:
    """Explicit offline-only authority. Never grants network, spend, publish, or PULSE execution."""

    mode: str = "OFFLINE_CONTRACT_ONLY"
    allow_network: bool = False
    allow_spend: bool = False
    allow_publish: bool = False
    provider_entitlement: bool = False
    spend_ceiling_minor: int = 0
    currency: None = None
    authorization_ref: None = None
    pulse_receiver_execution: str = PULSE_RECEIVER_EXECUTION
    wait_brain_claimed: bool = False
    wakehub_effect: str = "NONE"

    def __post_init__(self) -> None:
        _require(self.mode == "OFFLINE_CONTRACT_ONLY", "OFFLINE_AUTHORITY_VIOLATION")
        _require(
            self.allow_network is False
            and self.allow_spend is False
            and self.allow_publish is False
            and self.provider_entitlement is False,
            "OFFLINE_AUTHORITY_VIOLATION",
        )
        _require(
            type(self.spend_ceiling_minor) is int and self.spend_ceiling_minor == 0,
            "OFFLINE_AUTHORITY_VIOLATION",
        )
        _require(
            self.currency is None and self.authorization_ref is None,
            "OFFLINE_AUTHORITY_VIOLATION",
        )
        _require(
            self.pulse_receiver_execution == PULSE_RECEIVER_EXECUTION
            and self.wait_brain_claimed is False
            and self.wakehub_effect == "NONE",
            "OFFLINE_AUTHORITY_VIOLATION",
        )

    def to_directive_authorization(self) -> dict[str, object]:
        return {
            "allow_network": False,
            "allow_publish": False,
            "allow_spend": False,
            "authorization_ref": None,
            "currency": None,
            "provider_entitlement": False,
            "spend_ceiling_minor": 0,
        }

    def to_dict(self) -> dict[str, object]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, record: object) -> A04PulseOfflineAuthority:
        names = {f.name for f in fields(cls)}
        _require(type(record) is dict and set(record) == names, "OFFLINE_AUTHORITY_VIOLATION")
        return cls(**record)


@dataclass(frozen=True, kw_only=True)
class A04PulseHandoffScope:
    """Closed offline execution scope bound to CreativeDirective.v1."""

    action_class: str
    action_classes: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    provider_ref: None = None
    account_ref: None = None
    surface: None = None
    asset_sha256: str | None = None

    def __post_init__(self) -> None:
        _require(
            type(self.action_class) is str and self.action_class in OFFLINE_ALLOWED_ACTION_CLASSES,
            "OFFLINE_SCOPE_REQUIRED",
        )
        _require(
            type(self.action_classes) is tuple and 1 <= len(self.action_classes) <= len(OFFLINE_ALLOWED_ACTION_CLASSES),
            "OFFLINE_SCOPE_REQUIRED",
        )
        _require(len(set(self.action_classes)) == len(self.action_classes), "DUPLICATE_SCOPE_ENTRY")
        _require(
            all(type(a) is str and a in OFFLINE_ALLOWED_ACTION_CLASSES for a in self.action_classes),
            "OFFLINE_SCOPE_REQUIRED",
        )
        _require(self.action_class in self.action_classes, "SCOPE_ACTION_NOT_DECLARED")
        _require(
            type(self.allowed_tools) is tuple and 1 <= len(self.allowed_tools) <= MAX_TOOLS,
            "OFFLINE_SCOPE_REQUIRED",
        )
        _require(len(set(self.allowed_tools)) == len(self.allowed_tools), "DUPLICATE_SCOPE_ENTRY")
        for tool in self.allowed_tools:
            _require_ref(tool, "OFFLINE_SCOPE_REQUIRED")
        _require(
            self.provider_ref is None and self.account_ref is None and self.surface is None,
            "OFFLINE_SCOPE_REQUIRED",
        )
        if self.asset_sha256 is not None:
            _require_sha256(self.asset_sha256, "INVALID_SCOPE_ASSET_SHA256")

    def to_dict(self) -> dict[str, object]:
        return {
            "account_ref": self.account_ref,
            "action_class": self.action_class,
            "action_classes": list(self.action_classes),
            "allowed_tools": list(self.allowed_tools),
            "asset_sha256": self.asset_sha256,
            "provider_ref": self.provider_ref,
            "surface": self.surface,
        }

    @classmethod
    def from_dict(cls, record: object) -> A04PulseHandoffScope:
        names = {f.name for f in fields(cls)}
        _require(type(record) is dict and set(record) == names, "OFFLINE_SCOPE_REQUIRED")
        _require(type(record["action_classes"]) is list and type(record["allowed_tools"]) is list, "OFFLINE_SCOPE_REQUIRED")
        return cls(
            action_class=record["action_class"],
            action_classes=tuple(record["action_classes"]),
            allowed_tools=tuple(record["allowed_tools"]),
            provider_ref=record["provider_ref"],
            account_ref=record["account_ref"],
            surface=record["surface"],
            asset_sha256=record["asset_sha256"],
        )


@dataclass(frozen=True, kw_only=True)
class A04PulseHandoffProvenance:
    """Combined read-only provenance from CreativeDirective.v1 and A04CreativeHandoffReceipt.v1."""

    directive_actor: str
    directive_evidence_type: str
    directive_actor_ref: str
    directive_source_ref: str
    a04_actor_ref: str
    a04_job_actor_ref: str
    a04_consumer_id: str
    source_sha: str

    def __post_init__(self) -> None:
        _require(
            type(self.directive_actor) is str and self.directive_actor in SUPPORTED_DIRECTIVE_ACTORS,
            "INVALID_PROVENANCE",
        )
        _require(
            type(self.directive_evidence_type) is str
            and self.directive_evidence_type in SUPPORTED_ASSET_EVIDENCE_TYPES,
            "INVALID_PROVENANCE",
        )
        _require(
            (self.directive_actor, self.directive_evidence_type)
            in {("HUMAN", "HUMAN_SUPPLIED"), ("FIXTURE", "SYNTHETIC_FIXTURE")},
            "PROVENANCE_ACTOR_EVIDENCE_MISMATCH",
        )
        _require_ref(self.directive_actor_ref, "INVALID_PROVENANCE")
        _require_ref(self.directive_source_ref, "INVALID_PROVENANCE")
        _require_text(self.a04_actor_ref, "INVALID_PROVENANCE")
        _require_text(self.a04_job_actor_ref, "INVALID_PROVENANCE")
        _require(self.a04_consumer_id == A04_CREATIVE_CONSUMER_ID, "INVALID_A04_CONSUMER_ID")
        _require_source_sha(self.source_sha, "INVALID_SOURCE_SHA")

    def to_dict(self) -> dict[str, object]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, record: object) -> A04PulseHandoffProvenance:
        names = {f.name for f in fields(cls)}
        _require(type(record) is dict and set(record) == names, "INVALID_PROVENANCE")
        return cls(**record)


def _packet_identity(body: dict[str, object]) -> str:
    payload = {k: v for k, v in body.items() if k not in {"packet_id", "packet_sha256"}}
    return "a04pulse-" + sha256(canonical_handoff_bytes(payload)).hexdigest()


def _packet_digest(body: dict[str, object]) -> str:
    payload = {k: v for k, v in body.items() if k != "packet_sha256"}
    return sha256(canonical_handoff_bytes(payload)).hexdigest()


@dataclass(frozen=True, kw_only=True)
class A04PulseHandoffPacket:
    """Closed, self-hashed offline contract binding A04 handoff receipt to CreativeDirective.v1.

    Proves offline handoff contract integrity ONLY. Never claims PULSE receiver
    execution, WAIT_BRAIN, media generation, rights grant, or external publish.
    """

    schema_version: str = HANDOFF_SCHEMA_VERSION
    packet_id: str
    logical_identity: str
    status: str = HANDOFF_STATUS
    route_id: str = HANDOFF_ROUTE_ID
    producer_id: str = HANDOFF_PRODUCER_ID
    journey_id: str
    cycle_id: str
    job_id: str
    attempt: int
    source_sha: str
    directive_schema_version: str = DIRECTIVE_SCHEMA_VERSION
    directive_version: int
    directive_sha256: str
    directive_bytes_sha256: str
    a04_receipt_schema_version: str = A04_HANDOFF_RECEIPT_SCHEMA
    a04_receipt_id: str
    a04_receipt_digest: str
    a04_receipt_status: str = "SOURCE_HANDOFF_VERIFIED"
    a03_result_ref: str
    a03_result_digest: str
    package_id: str
    package_revision: int
    parent_package_digest: str | None
    package_ref: str
    package_digest: str
    brief_id: str
    source_brief_digest: str
    selection_id: str
    concept_id: str
    concept_revision: int
    concept_digest: str
    cycle_job_digest: str
    cycle_result_digest: str
    product_ref: str | None
    audio_mode: str
    cta_mode: str = "NONE"
    cta_text: str | None = None
    assets: tuple[A04PulseAssetPin, ...]
    scope: A04PulseHandoffScope
    provenance: A04PulseHandoffProvenance
    authority: A04PulseOfflineAuthority
    packet_sha256: str

    def __post_init__(self) -> None:
        _require(self.schema_version == HANDOFF_SCHEMA_VERSION, "INVALID_SCHEMA_VERSION")
        _require(self.status == HANDOFF_STATUS, "INVALID_HANDOFF_STATUS")
        _require(self.route_id == HANDOFF_ROUTE_ID, "INVALID_HANDOFF_ROUTE")
        _require(self.producer_id == HANDOFF_PRODUCER_ID, "INVALID_PRODUCER_ID")
        _require(self.directive_schema_version == DIRECTIVE_SCHEMA_VERSION, "INVALID_SCHEMA_VERSION")
        _require(self.a04_receipt_schema_version == A04_HANDOFF_RECEIPT_SCHEMA, "INVALID_SCHEMA_VERSION")
        _require(self.a04_receipt_status == "SOURCE_HANDOFF_VERIFIED", "INVALID_A04_RECEIPT_STATUS")

        _require_ref(self.journey_id, "INVALID_JOURNEY_ID")
        _require_text(self.cycle_id, "INVALID_CYCLE_ID")
        _require_text(self.job_id, "INVALID_JOB_ID")
        _require_pos_int(self.attempt, "INVALID_ATTEMPT")
        _require_source_sha(self.source_sha, "INVALID_SOURCE_SHA")
        _require_pos_int(self.directive_version, "INVALID_DIRECTIVE_VERSION")
        _require_sha256(self.directive_sha256, "INVALID_DIRECTIVE_SHA256")
        _require_sha256(self.directive_bytes_sha256, "INVALID_DIRECTIVE_BYTES_SHA256")

        _require(
            type(self.a04_receipt_id) is str and _RECEIPT_ID_RE.fullmatch(self.a04_receipt_id) is not None,
            "INVALID_A04_RECEIPT_ID",
        )
        _require_sha256(self.a04_receipt_digest, "INVALID_A04_RECEIPT_DIGEST")

        for text_field in ("a03_result_ref", "package_ref", "brief_id"):
            _require_text(getattr(self, text_field), "INVALID_A03_REFERENCE")
        _require(
            type(self.package_id) is str and _PACKAGE_ID_RE.fullmatch(self.package_id) is not None,
            "INVALID_PACKAGE_ID",
        )
        _require(
            type(self.selection_id) is str and _SELECTION_ID_RE.fullmatch(self.selection_id) is not None,
            "INVALID_SELECTION_ID",
        )
        _require(
            type(self.concept_id) is str and _CONCEPT_ID_RE.fullmatch(self.concept_id) is not None,
            "INVALID_CONCEPT_ID",
        )
        _require_pos_int(self.package_revision, "INVALID_PACKAGE_REVISION")
        _require_pos_int(self.concept_revision, "INVALID_CONCEPT_REVISION")

        for digest_field in (
            "a03_result_digest",
            "package_digest",
            "source_brief_digest",
            "concept_digest",
            "cycle_job_digest",
            "cycle_result_digest",
        ):
            _require_sha256(getattr(self, digest_field), "INVALID_A03_DIGEST")

        if self.package_revision == 1:
            _require(self.parent_package_digest is None, "FORGED_PACKAGE_LINEAGE")
        else:
            _require_sha256(self.parent_package_digest, "FORGED_PACKAGE_LINEAGE")
            _require(self.parent_package_digest != self.package_digest, "FORGED_PACKAGE_LINEAGE")

        if self.product_ref is not None:
            _require_ref(self.product_ref, "INVALID_PRODUCT_REF")

        _require(
            type(self.audio_mode) is str and self.audio_mode in SUPPORTED_AUDIO_MODES,
            "INVALID_AUDIO_MODE",
        )
        _require(
            type(self.cta_mode) is str and self.cta_mode in SUPPORTED_CTA_MODES,
            "INVALID_CTA_MODE",
        )
        if self.cta_mode == "NONE":
            _require(self.cta_text is None, "INVALID_CTA_PAYLOAD")
        else:
            _require_text(self.cta_text, "INVALID_CTA_PAYLOAD", max_len=256)

        _require(
            type(self.scope) is A04PulseHandoffScope
            and type(self.provenance) is A04PulseHandoffProvenance
            and type(self.authority) is A04PulseOfflineAuthority,
            "TYPED_HANDOFF_COMPONENTS_REQUIRED",
        )
        _require(self.provenance.source_sha == self.source_sha, "PROVENANCE_SOURCE_MISMATCH")

        _require(
            type(self.assets) is tuple and 1 <= len(self.assets) <= MAX_ASSETS,
            "ASSETS_REQUIRED",
        )
        seen_refs: set[str] = set()
        seen_shas: set[str] = set()
        for asset in self.assets:
            _require(type(asset) is A04PulseAssetPin, "INVALID_ASSET_PIN_FIELDS")
            _require(asset.asset_ref not in seen_refs, "DUPLICATE_ASSET_IDENTITY")
            _require(asset.asset_sha256 not in seen_shas, "DUPLICATE_ASSET_IDENTITY")
            seen_refs.add(asset.asset_ref)
            seen_shas.add(asset.asset_sha256)
            if asset.evidence_type == "SYNTHETIC_FIXTURE":
                _require(
                    self.provenance.directive_evidence_type == "SYNTHETIC_FIXTURE",
                    "FIXTURE_RIGHTS_MISMATCH",
                )

        if self.scope.asset_sha256 is not None:
            _require(self.scope.asset_sha256 in seen_shas, "SCOPE_ASSET_MISMATCH")

        expected_logical = derive_logical_identity(
            journey_id=self.journey_id,
            cycle_id=self.cycle_id,
            job_id=self.job_id,
            attempt=self.attempt,
            package_id=self.package_id,
            package_revision=self.package_revision,
            directive_version=self.directive_version,
        )
        _require(
            type(self.logical_identity) is str
            and _LOGICAL_ID_RE.fullmatch(self.logical_identity) is not None
            and self.logical_identity == expected_logical,
            "LOGICAL_IDENTITY_MISMATCH",
        )

        body = self.to_dict()
        expected_id = _packet_identity(body)
        _require(
            type(self.packet_id) is str
            and _PACKET_ID_RE.fullmatch(self.packet_id) is not None
            and self.packet_id == expected_id,
            "PACKET_ID_MISMATCH",
        )
        expected_sha = _packet_digest(body)
        _require(
            type(self.packet_sha256) is str
            and _SHA256_RE.fullmatch(self.packet_sha256) is not None
            and self.packet_sha256 == expected_sha,
            "PACKET_DIGEST_MISMATCH",
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if f.name == "assets":
                result[f.name] = [asset.to_dict() for asset in self.assets]
            elif f.name in {"scope", "provenance", "authority"}:
                result[f.name] = val.to_dict()
            else:
                result[f.name] = val
        return result

    def to_canonical_bytes(self) -> bytes:
        return canonical_handoff_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, record: object) -> A04PulseHandoffPacket:
        names = {f.name for f in fields(cls)}
        _require(type(record) is dict and set(record) == names, "INVALID_PACKET_FIELDS")
        raw_assets = record["assets"]
        _require(type(raw_assets) is list and 1 <= len(raw_assets) <= MAX_ASSETS, "ASSETS_REQUIRED")
        assets = tuple(A04PulseAssetPin.from_dict(item) for item in raw_assets)
        scope = A04PulseHandoffScope.from_dict(record["scope"])
        provenance = A04PulseHandoffProvenance.from_dict(record["provenance"])
        authority = A04PulseOfflineAuthority.from_dict(record["authority"])
        kwargs = {
            k: v
            for k, v in record.items()
            if k not in {"assets", "scope", "provenance", "authority"}
        }
        return cls(
            **kwargs,
            assets=assets,
            scope=scope,
            provenance=provenance,
            authority=authority,
        )


@dataclass(frozen=True, kw_only=True)
class A04PulseHandoffValidationResult:
    """Result of strict offline handoff validation; never asserts PULSE execution or WAIT_BRAIN."""

    valid: bool
    status: str
    packet: A04PulseHandoffPacket
    packet_id: str
    packet_sha256: str
    logical_identity: str
    bytes_verified: tuple[str, ...]
    pulse_receiver_execution: str = PULSE_RECEIVER_EXECUTION
    wait_brain_claimed: bool = False


def _coerce_receipt(
    receipt: A04CreativeHandoffReceipt | Mapping[str, object] | bytes,
    *,
    receipt_bytes: bytes | None = None,
) -> tuple[A04CreativeHandoffReceipt, bool]:
    bytes_checked = False
    if type(receipt) is bytes:
        _require(receipt_bytes is None or receipt_bytes == receipt, "RECEIPT_BYTES_MISMATCH")
        receipt_bytes = receipt
        parsed = _decode_strict_json(
            receipt,
            invalid_code="INVALID_A04_RECEIPT_JSON",
            duplicate_code="DUPLICATE_JSON_KEY",
        )
        try:
            model = A04CreativeHandoffReceipt.from_dict(parsed)
        except (ValueError, TypeError) as exc:
            raise A04PulseHandoffError("INVALID_A04_RECEIPT") from exc
    elif isinstance(receipt, A04CreativeHandoffReceipt):
        try:
            model = A04CreativeHandoffReceipt.from_dict(receipt.to_dict())
        except (ValueError, TypeError) as exc:
            raise A04PulseHandoffError("INVALID_A04_RECEIPT") from exc
    elif type(receipt) is dict:
        try:
            model = A04CreativeHandoffReceipt.from_dict(receipt)
        except (ValueError, TypeError) as exc:
            raise A04PulseHandoffError("INVALID_A04_RECEIPT") from exc
    else:
        raise A04PulseHandoffError("INVALID_A04_RECEIPT")

    if receipt_bytes is not None:
        _require(type(receipt_bytes) is bytes and len(receipt_bytes) > 0, "RECEIPT_BYTES_MISMATCH")
        parsed_raw = _decode_strict_json(
            receipt_bytes,
            invalid_code="RECEIPT_BYTES_MISMATCH",
            duplicate_code="DUPLICATE_JSON_KEY",
        )
        try:
            from_bytes = A04CreativeHandoffReceipt.from_dict(parsed_raw)
        except (ValueError, TypeError) as exc:
            raise A04PulseHandoffError("RECEIPT_BYTES_MISMATCH") from exc
        canonical = a04_canonical_bytes(model.to_dict())
        _require(
            from_bytes == model
            and receipt_bytes == canonical
            and sha256_bytes(receipt_bytes) == model.digest,
            "RECEIPT_BYTES_MISMATCH",
        )
        bytes_checked = True
    return model, bytes_checked


def _coerce_directive(
    directive: ValidatedContract | Mapping[str, object] | bytes,
    *,
    directive_bytes: bytes | None = None,
) -> tuple[ValidatedContract, dict[str, object], bool]:
    bytes_checked = False
    if type(directive) is bytes:
        _require(directive_bytes is None or directive_bytes == directive, "DIRECTIVE_BYTES_MISMATCH")
        directive_bytes = directive
        try:
            contract = parse_contract("CreativeDirective", directive)
        except ContractError as exc:
            raise A04PulseHandoffError("INVALID_DIRECTIVE_CONTRACT") from exc
    elif type(directive) is ValidatedContract:
        _require(directive.name == "CreativeDirective", "INVALID_DIRECTIVE_CONTRACT")
        try:
            contract = parse_contract("CreativeDirective", directive.raw)
        except ContractError as exc:
            raise A04PulseHandoffError("INVALID_DIRECTIVE_CONTRACT") from exc
        _require(contract.raw == directive.raw, "DIRECTIVE_BYTES_MISMATCH")
    elif type(directive) is dict:
        try:
            contract = validate_contract("CreativeDirective", directive)
        except ContractError as exc:
            raise A04PulseHandoffError("INVALID_DIRECTIVE_CONTRACT") from exc
    else:
        raise A04PulseHandoffError("INVALID_DIRECTIVE_CONTRACT")

    if directive_bytes is not None:
        _require(type(directive_bytes) is bytes and len(directive_bytes) > 0, "DIRECTIVE_BYTES_MISMATCH")
        try:
            parsed_from_bytes = parse_contract("CreativeDirective", directive_bytes)
        except ContractError as exc:
            raise A04PulseHandoffError("DIRECTIVE_BYTES_MISMATCH") from exc
        _require(
            parsed_from_bytes.raw == contract.raw and directive_bytes == contract.raw,
            "DIRECTIVE_BYTES_MISMATCH",
        )
        bytes_checked = True
    return contract, contract.to_dict(), bytes_checked


def _verify_asset_and_rights_bytes(
    assets: tuple[A04PulseAssetPin, ...],
    *,
    asset_bytes_by_ref: Mapping[str, bytes] | None,
    rights_bytes_by_ref: Mapping[str, bytes] | None,
) -> list[str]:
    verified: list[str] = []
    if asset_bytes_by_ref is not None:
        _require(isinstance(asset_bytes_by_ref, Mapping), "ASSET_BYTES_SET_MISMATCH")
        expected_refs = {pin.asset_ref for pin in assets}
        supplied_refs = set(asset_bytes_by_ref.keys())
        _require(
            all(type(k) is str for k in supplied_refs) and supplied_refs == expected_refs,
            "ASSET_BYTES_SET_MISMATCH",
        )
        for pin in assets:
            raw = asset_bytes_by_ref[pin.asset_ref]
            _require(type(raw) is bytes and 0 < len(raw) <= MAX_ASSET_BYTES, "ASSET_BYTES_MISMATCH")
            if pin.byte_length is not None:
                _require(len(raw) == pin.byte_length, "ASSET_BYTES_MISMATCH")
            _require(sha256_bytes(raw) == pin.asset_sha256, "ASSET_BYTES_MISMATCH")
        verified.append("asset_bytes")

    if rights_bytes_by_ref is not None:
        _require(isinstance(rights_bytes_by_ref, Mapping), "RIGHTS_BYTES_SET_MISMATCH")
        expected_rights: dict[str, str] = {}
        for pin in assets:
            if pin.rights_ref is not None:
                _require(pin.rights_sha256 is not None, "RIGHTS_ABSENT_OR_RESTRICTED")
                if pin.rights_ref in expected_rights:
                    _require(expected_rights[pin.rights_ref] == pin.rights_sha256, "RIGHTS_BYTES_MISMATCH")
                expected_rights[pin.rights_ref] = pin.rights_sha256
        supplied_rights = set(rights_bytes_by_ref.keys())
        _require(
            all(type(k) is str for k in supplied_rights)
            and supplied_rights == set(expected_rights.keys()),
            "RIGHTS_BYTES_SET_MISMATCH",
        )
        for ref, expected_sha in expected_rights.items():
            raw = rights_bytes_by_ref[ref]
            _require(type(raw) is bytes and 0 < len(raw) <= MAX_PACKET_BYTES, "RIGHTS_BYTES_MISMATCH")
            _require(sha256_bytes(raw) == expected_sha, "RIGHTS_BYTES_MISMATCH")
        verified.append("rights_bytes")
    return verified


def _build_asset_pins_from_directive(
    directive_data: dict[str, object],
    *,
    asset_bytes_by_ref: Mapping[str, bytes] | None,
) -> tuple[A04PulseAssetPin, ...]:
    raw_assets = directive_data["assets"]
    _require(type(raw_assets) is list and 1 <= len(raw_assets) <= MAX_ASSETS, "ASSETS_REQUIRED")
    pins: list[A04PulseAssetPin] = []
    for item in raw_assets:
        rights = item["rights"]
        ref = item["ref"]
        byte_len: int | None = None
        if asset_bytes_by_ref is not None and isinstance(asset_bytes_by_ref, Mapping) and ref in asset_bytes_by_ref:
            raw = asset_bytes_by_ref[ref]
            _require(type(raw) is bytes and 0 < len(raw) <= MAX_ASSET_BYTES, "ASSET_BYTES_MISMATCH")
            byte_len = len(raw)
        pins.append(
            A04PulseAssetPin(
                asset_ref=ref,
                asset_sha256=item["sha256"],
                evidence_type=item["evidence_type"],
                rights_status=rights["status"],
                rights_ref=rights["ref"],
                rights_sha256=rights["sha256"],
                byte_length=byte_len,
            )
        )
    return tuple(pins)


def build_a04_pulse_handoff_packet(
    *,
    a04_receipt: A04CreativeHandoffReceipt | Mapping[str, object] | bytes,
    directive: ValidatedContract | Mapping[str, object] | bytes,
    cta_mode: str = "NONE",
    cta_text: str | None = None,
    directive_bytes: bytes | None = None,
    receipt_bytes: bytes | None = None,
    package_bytes: bytes | None = None,
    a03_result_bytes: bytes | None = None,
    asset_bytes_by_ref: Mapping[str, bytes] | None = None,
    rights_bytes_by_ref: Mapping[str, bytes] | None = None,
    expected_source_sha: str | None = None,
    expected_journey_id: str | None = None,
    expected_cycle_id: str | None = None,
    min_directive_version: int | None = None,
    min_package_revision: int | None = None,
    seen_identities: Collection[str] | None = None,
) -> A04PulseHandoffPacket:
    """Construct and strictly validate a deterministic offline A04 -> PULSE handoff packet."""
    receipt_model, _ = _coerce_receipt(a04_receipt, receipt_bytes=receipt_bytes)
    directive_contract, directive_data, _ = _coerce_directive(directive, directive_bytes=directive_bytes)

    _require(directive_data["schema_version"] == DIRECTIVE_SCHEMA_VERSION, "INVALID_SCHEMA_VERSION")
    _require(receipt_model.schema_version == A04_HANDOFF_RECEIPT_SCHEMA, "INVALID_SCHEMA_VERSION")
    _require(directive_data["authorization"] == draft_authorization(), "OFFLINE_AUTHORITY_VIOLATION")

    _require(receipt_model.journey_id == directive_data["journey_id"], "JOURNEY_MISMATCH")
    _require(receipt_model.source_sha == directive_data["source_sha"], "SOURCE_SHA_MISMATCH")

    pins = _build_asset_pins_from_directive(directive_data, asset_bytes_by_ref=asset_bytes_by_ref)
    d_scope = directive_data["scope"]
    scope = A04PulseHandoffScope(
        action_class=d_scope["action_class"],
        action_classes=tuple(directive_data["action_classes"]),
        allowed_tools=tuple(directive_data["allowed_tools"]),
        provider_ref=d_scope["provider_ref"],
        account_ref=d_scope["account_ref"],
        surface=d_scope["surface"],
        asset_sha256=d_scope["asset_sha256"],
    )
    d_prov = directive_data["provenance"]
    provenance = A04PulseHandoffProvenance(
        directive_actor=d_prov["actor"],
        directive_evidence_type=d_prov["evidence_type"],
        directive_actor_ref=d_prov["actor_ref"],
        directive_source_ref=d_prov["source_ref"],
        a04_actor_ref=receipt_model.actor_ref,
        a04_job_actor_ref=receipt_model.job_actor_ref,
        a04_consumer_id=receipt_model.consumer_id,
        source_sha=directive_data["source_sha"],
    )
    authority = A04PulseOfflineAuthority()
    logical_id = derive_logical_identity(
        journey_id=receipt_model.journey_id,
        cycle_id=receipt_model.cycle_id,
        job_id=receipt_model.job_id,
        attempt=receipt_model.attempt,
        package_id=receipt_model.package_id,
        package_revision=receipt_model.revision,
        directive_version=directive_data["directive_version"],
    )

    body: dict[str, object] = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "logical_identity": logical_id,
        "status": HANDOFF_STATUS,
        "route_id": HANDOFF_ROUTE_ID,
        "producer_id": HANDOFF_PRODUCER_ID,
        "journey_id": receipt_model.journey_id,
        "cycle_id": receipt_model.cycle_id,
        "job_id": receipt_model.job_id,
        "attempt": receipt_model.attempt,
        "source_sha": directive_data["source_sha"],
        "directive_schema_version": DIRECTIVE_SCHEMA_VERSION,
        "directive_version": directive_data["directive_version"],
        "directive_sha256": directive_data["directive_sha256"],
        "directive_bytes_sha256": sha256_bytes(directive_contract.raw),
        "a04_receipt_schema_version": receipt_model.schema_version,
        "a04_receipt_id": receipt_model.receipt_id,
        "a04_receipt_digest": receipt_model.digest,
        "a04_receipt_status": receipt_model.status,
        "a03_result_ref": receipt_model.a03_result_ref,
        "a03_result_digest": receipt_model.a03_result_digest,
        "package_id": receipt_model.package_id,
        "package_revision": receipt_model.revision,
        "parent_package_digest": receipt_model.parent_package_digest,
        "package_ref": receipt_model.package_ref,
        "package_digest": receipt_model.package_digest,
        "brief_id": receipt_model.brief_id,
        "source_brief_digest": receipt_model.source_brief_digest,
        "selection_id": receipt_model.selection_id,
        "concept_id": receipt_model.concept_id,
        "concept_revision": receipt_model.concept_revision,
        "concept_digest": receipt_model.concept_digest,
        "cycle_job_digest": receipt_model.cycle_job_digest,
        "cycle_result_digest": receipt_model.cycle_result_digest,
        "product_ref": directive_data["creative"]["product_ref"],
        "audio_mode": receipt_model.audio_mode,
        "cta_mode": cta_mode,
        "cta_text": cta_text,
        "assets": [pin.to_dict() for pin in pins],
        "scope": scope.to_dict(),
        "provenance": provenance.to_dict(),
        "authority": authority.to_dict(),
    }
    body["packet_id"] = _packet_identity(body)
    body["packet_sha256"] = _packet_digest(body)
    packet = A04PulseHandoffPacket.from_dict(body)

    validate_a04_pulse_handoff_packet(
        packet,
        a04_receipt=receipt_model,
        directive=directive_contract,
        directive_bytes=directive_bytes,
        receipt_bytes=receipt_bytes,
        package_bytes=package_bytes,
        a03_result_bytes=a03_result_bytes,
        asset_bytes_by_ref=asset_bytes_by_ref,
        rights_bytes_by_ref=rights_bytes_by_ref,
        expected_source_sha=expected_source_sha,
        expected_journey_id=expected_journey_id,
        expected_cycle_id=expected_cycle_id,
        min_directive_version=min_directive_version,
        min_package_revision=min_package_revision,
        seen_identities=seen_identities,
    )
    return packet


def parse_a04_pulse_handoff_packet(raw: bytes) -> A04PulseHandoffPacket:
    """Parse canonical UTF-8/ASCII JSON bytes into a validated A04PulseHandoffPacket."""
    parsed = _decode_strict_json(
        raw,
        invalid_code="INVALID_PACKET_JSON",
        duplicate_code="DUPLICATE_JSON_KEY",
    )
    packet = A04PulseHandoffPacket.from_dict(parsed)
    _require(packet.to_canonical_bytes() == raw, "PACKET_BYTES_NONCANONICAL")
    return packet


def check_unique_logical_identities(packets: Sequence[A04PulseHandoffPacket]) -> tuple[str, ...]:
    """Fail closed if any packets share logical identity, packet_id, receipt_id, or package slot."""
    seen_logical: set[str] = set()
    seen_packet_ids: set[str] = set()
    seen_receipt_ids: set[str] = set()
    seen_package_slots: set[tuple[str, str, str, int]] = set()
    ordered: list[str] = []
    for packet in packets:
        _require(type(packet) is A04PulseHandoffPacket, "TYPED_PACKET_REQUIRED")
        slot = (packet.journey_id, packet.cycle_id, packet.package_id, packet.package_revision)
        _require(packet.logical_identity not in seen_logical, "DUPLICATE_LOGICAL_IDENTITY")
        _require(packet.packet_id not in seen_packet_ids, "DUPLICATE_LOGICAL_IDENTITY")
        _require(packet.a04_receipt_id not in seen_receipt_ids, "DUPLICATE_LOGICAL_IDENTITY")
        _require(slot not in seen_package_slots, "DUPLICATE_LOGICAL_IDENTITY")
        seen_logical.add(packet.logical_identity)
        seen_packet_ids.add(packet.packet_id)
        seen_receipt_ids.add(packet.a04_receipt_id)
        seen_package_slots.add(slot)
        ordered.append(packet.logical_identity)
    return tuple(ordered)


def validate_a04_pulse_handoff_packet(
    packet: A04PulseHandoffPacket | Mapping[str, object] | bytes,
    *,
    a04_receipt: A04CreativeHandoffReceipt | Mapping[str, object] | bytes,
    directive: ValidatedContract | Mapping[str, object] | bytes,
    directive_bytes: bytes | None = None,
    receipt_bytes: bytes | None = None,
    package_bytes: bytes | None = None,
    a03_result_bytes: bytes | None = None,
    asset_bytes_by_ref: Mapping[str, bytes] | None = None,
    rights_bytes_by_ref: Mapping[str, bytes] | None = None,
    expected_source_sha: str | None = None,
    expected_journey_id: str | None = None,
    expected_cycle_id: str | None = None,
    expected_directive_version: int | None = None,
    min_directive_version: int | None = None,
    expected_package_revision: int | None = None,
    min_package_revision: int | None = None,
    seen_identities: Collection[str] | None = None,
    seen_packets: Sequence[A04PulseHandoffPacket] | None = None,
) -> A04PulseHandoffValidationResult:
    """Strictly verify an offline A04 -> PULSE handoff packet against its read-only inputs.

    Validates exact actual bytes whenever supplied and fails closed on
    mismatched/stale/forged versions, wrong journey/cycle, absent rights/scope,
    offline authority violations, and duplicated logical identities.
    """
    bytes_verified: list[str] = []

    if type(packet) is bytes:
        checked_packet = parse_a04_pulse_handoff_packet(packet)
        bytes_verified.append("packet_bytes")
    elif type(packet) is A04PulseHandoffPacket:
        checked_packet = A04PulseHandoffPacket.from_dict(packet.to_dict())
    elif type(packet) is dict:
        checked_packet = A04PulseHandoffPacket.from_dict(packet)
    else:
        raise A04PulseHandoffError("INVALID_PACKET_FIELDS")

    receipt_model, receipt_bytes_checked = _coerce_receipt(
        a04_receipt,
        receipt_bytes=receipt_bytes,
    )
    if receipt_bytes_checked:
        bytes_verified.append("receipt_bytes")

    directive_contract, directive_data, directive_bytes_checked = _coerce_directive(
        directive,
        directive_bytes=directive_bytes,
    )
    if directive_bytes_checked:
        bytes_verified.append("directive_bytes")

    # 1. Version, source SHA, and journey/cycle bindings
    _require(
        checked_packet.directive_schema_version == directive_data["schema_version"] == DIRECTIVE_SCHEMA_VERSION,
        "INVALID_SCHEMA_VERSION",
    )
    _require(
        checked_packet.a04_receipt_schema_version == receipt_model.schema_version == A04_HANDOFF_RECEIPT_SCHEMA,
        "INVALID_SCHEMA_VERSION",
    )
    _require(
        checked_packet.journey_id == receipt_model.journey_id == directive_data["journey_id"],
        "JOURNEY_MISMATCH",
    )
    _require(checked_packet.cycle_id == receipt_model.cycle_id, "CYCLE_MISMATCH")
    _require(
        checked_packet.job_id == receipt_model.job_id and checked_packet.attempt == receipt_model.attempt,
        "A04_JOB_BINDING_MISMATCH",
    )
    _require(
        checked_packet.source_sha == receipt_model.source_sha == directive_data["source_sha"],
        "SOURCE_SHA_MISMATCH",
    )

    if expected_source_sha is not None:
        _require_source_sha(expected_source_sha, "STALE_OR_MISMATCHED_SOURCE_SHA")
        _require(checked_packet.source_sha == expected_source_sha, "STALE_OR_MISMATCHED_SOURCE_SHA")
    if expected_journey_id is not None:
        _require_ref(expected_journey_id, "JOURNEY_MISMATCH")
        _require(checked_packet.journey_id == expected_journey_id, "JOURNEY_MISMATCH")
    if expected_cycle_id is not None:
        _require_text(expected_cycle_id, "CYCLE_MISMATCH")
        _require(checked_packet.cycle_id == expected_cycle_id, "CYCLE_MISMATCH")

    # 2. Directive and package version / freshness checks
    _require(
        checked_packet.directive_version == directive_data["directive_version"],
        "DIRECTIVE_VERSION_MISMATCH",
    )
    if expected_directive_version is not None:
        _require_pos_int(expected_directive_version, "DIRECTIVE_VERSION_MISMATCH")
        _require(checked_packet.directive_version == expected_directive_version, "DIRECTIVE_VERSION_MISMATCH")
    if min_directive_version is not None:
        _require_pos_int(min_directive_version, "STALE_DIRECTIVE_VERSION")
        _require(checked_packet.directive_version >= min_directive_version, "STALE_DIRECTIVE_VERSION")

    _require(checked_packet.package_revision == receipt_model.revision, "PACKAGE_VERSION_MISMATCH")
    if expected_package_revision is not None:
        _require_pos_int(expected_package_revision, "PACKAGE_VERSION_MISMATCH")
        _require(checked_packet.package_revision == expected_package_revision, "PACKAGE_VERSION_MISMATCH")
    if min_package_revision is not None:
        _require_pos_int(min_package_revision, "STALE_PACKAGE_REVISION")
        _require(checked_packet.package_revision >= min_package_revision, "STALE_PACKAGE_REVISION")

    # 3. Exact directive + A04 receipt + A03 source/package digests
    _require(
        checked_packet.directive_sha256 == directive_data["directive_sha256"]
        and checked_packet.directive_bytes_sha256 == sha256_bytes(directive_contract.raw),
        "DIRECTIVE_HASH_MISMATCH",
    )
    _require(
        checked_packet.a04_receipt_id == receipt_model.receipt_id
        and checked_packet.a04_receipt_digest == receipt_model.digest
        and checked_packet.a04_receipt_status == receipt_model.status == "SOURCE_HANDOFF_VERIFIED",
        "A04_RECEIPT_HASH_MISMATCH",
    )
    _require(
        checked_packet.a03_result_ref == receipt_model.a03_result_ref
        and checked_packet.a03_result_digest == receipt_model.a03_result_digest
        and checked_packet.package_id == receipt_model.package_id
        and checked_packet.parent_package_digest == receipt_model.parent_package_digest
        and checked_packet.package_ref == receipt_model.package_ref
        and checked_packet.package_digest == receipt_model.package_digest
        and checked_packet.brief_id == receipt_model.brief_id
        and checked_packet.source_brief_digest == receipt_model.source_brief_digest
        and checked_packet.selection_id == receipt_model.selection_id
        and checked_packet.concept_id == receipt_model.concept_id
        and checked_packet.concept_revision == receipt_model.concept_revision
        and checked_packet.concept_digest == receipt_model.concept_digest
        and checked_packet.cycle_job_digest == receipt_model.cycle_job_digest
        and checked_packet.cycle_result_digest == receipt_model.cycle_result_digest,
        "A03_PACKAGE_LINEAGE_MISMATCH",
    )
    _require(checked_packet.audio_mode == receipt_model.audio_mode, "AUDIO_MODE_MISMATCH")
    _require(checked_packet.product_ref == directive_data["creative"]["product_ref"], "PRODUCT_REF_MISMATCH")

    # 4. Exact provenance, offline scope, and offline authority bindings
    d_prov = directive_data["provenance"]
    prov = checked_packet.provenance
    _require(
        prov.directive_actor == d_prov["actor"]
        and prov.directive_evidence_type == d_prov["evidence_type"]
        and prov.directive_actor_ref == d_prov["actor_ref"]
        and prov.directive_source_ref == d_prov["source_ref"]
        and prov.a04_actor_ref == receipt_model.actor_ref
        and prov.a04_job_actor_ref == receipt_model.job_actor_ref
        and prov.a04_consumer_id == receipt_model.consumer_id == A04_CREATIVE_CONSUMER_ID
        and prov.source_sha == checked_packet.source_sha,
        "PROVENANCE_MISMATCH",
    )

    d_scope = directive_data["scope"]
    p_scope = checked_packet.scope
    _require(
        p_scope.action_class == d_scope["action_class"]
        and p_scope.action_classes == tuple(directive_data["action_classes"])
        and p_scope.allowed_tools == tuple(directive_data["allowed_tools"])
        and p_scope.provider_ref == d_scope["provider_ref"] is None
        and p_scope.account_ref == d_scope["account_ref"] is None
        and p_scope.surface == d_scope["surface"] is None
        and p_scope.asset_sha256 == d_scope["asset_sha256"],
        "SCOPE_BINDING_MISMATCH",
    )
    _require(
        directive_data["authorization"] == checked_packet.authority.to_directive_authorization() == draft_authorization(),
        "OFFLINE_AUTHORITY_VIOLATION",
    )

    # 5. Exact 1:1 asset ref, SHA-256, evidence type, and rights binding
    directive_assets = directive_data["assets"]
    _require(len(checked_packet.assets) == len(directive_assets), "ASSET_SET_MISMATCH")
    for pin, d_asset in zip(checked_packet.assets, directive_assets):
        d_rights = d_asset["rights"]
        _require(
            pin.asset_ref == d_asset["ref"]
            and pin.asset_sha256 == d_asset["sha256"]
            and pin.evidence_type == d_asset["evidence_type"]
            and pin.rights_status == d_rights["status"]
            and pin.rights_ref == d_rights["ref"]
            and pin.rights_sha256 == d_rights["sha256"],
            "ASSET_BINDING_MISMATCH",
        )

    # 6. Exact actual bytes verification whenever raw bytes are supplied
    if package_bytes is not None:
        _require(
            type(package_bytes) is bytes
            and len(package_bytes) > 0
            and sha256_bytes(package_bytes) == checked_packet.package_digest,
            "PACKAGE_BYTES_MISMATCH",
        )
        bytes_verified.append("package_bytes")
    if a03_result_bytes is not None:
        _require(
            type(a03_result_bytes) is bytes
            and len(a03_result_bytes) > 0
            and sha256_bytes(a03_result_bytes) == checked_packet.a03_result_digest,
            "A03_RESULT_BYTES_MISMATCH",
        )
        bytes_verified.append("a03_result_bytes")

    bytes_verified.extend(
        _verify_asset_and_rights_bytes(
            checked_packet.assets,
            asset_bytes_by_ref=asset_bytes_by_ref,
            rights_bytes_by_ref=rights_bytes_by_ref,
        )
    )

    # 7. Duplicate logical identity rejection
    if seen_identities is not None:
        _require(
            checked_packet.logical_identity not in seen_identities
            and checked_packet.packet_id not in seen_identities
            and checked_packet.a04_receipt_id not in seen_identities,
            "DUPLICATE_LOGICAL_IDENTITY",
        )
    if seen_packets is not None:
        check_unique_logical_identities((*seen_packets, checked_packet))

    return A04PulseHandoffValidationResult(
        valid=True,
        status=HANDOFF_STATUS,
        packet=checked_packet,
        packet_id=checked_packet.packet_id,
        packet_sha256=checked_packet.packet_sha256,
        logical_identity=checked_packet.logical_identity,
        bytes_verified=tuple(sorted(bytes_verified)),
        pulse_receiver_execution=PULSE_RECEIVER_EXECUTION,
        wait_brain_claimed=False,
    )


PULSE_LOCAL_ROUTE_ID = "a04-to-pulse-offline"
PULSE_LOCAL_CONSUMER_ID = (
    "ai_commerce.production_runtime.attended_worker:receive_a04_pulse_handoff"
)
PULSE_LOCAL_CONSUMER_PATH = "src/ai_commerce/production_runtime/attended_worker.py"
PULSE_INTAKE_CONTRACT_VERSION = "A04PulseAutonomousOfflineIntake.v1"
PULSE_OFFLINE_TOOL = "tool:a06-e01-offline-local-stills"

_SANITIZED_JOURNEY_RE = re.compile(r"journey:[0-9a-f]{32,64}")
_SANITIZED_PRODUCT_RE = re.compile(r"product:[0-9a-f]{64}")
_SANITIZED_ASSET_RE = re.compile(r"asset:[0-9a-f]{64}")
_SANITIZED_RIGHTS_RE = re.compile(r"rights:[0-9a-f]{64}")
_SANITIZED_PROVENANCE_REF_RE = re.compile(r"(?:human|fixture|local|directive):[0-9a-f]{32,64}")


@dataclass(frozen=True, kw_only=True)
class A04PulseReceiverAuthorization:
    """Distinct caller authorization for bounded autonomous LOCAL/OFFLINE PULSE intake.

    Explicitly separates autonomous local offline dispatch from legacy B0.3
    ``human_attended=True`` CLI execution and binds exact pinned source_sha,
    packet identity, logical identity, and consumer route. Never grants
    network, spend, publish, provider entitlement, or Brain approval.
    """

    schema_version: str = PULSE_INTAKE_CONTRACT_VERSION
    authorization_ref: str
    caller_id: str
    journey_id: str
    cycle_id: str
    job_id: str
    attempt: int
    source_sha: str
    packet_id: str
    packet_sha256: str
    logical_identity: str
    route_id: str = PULSE_LOCAL_ROUTE_ID
    consumer_id: str = PULSE_LOCAL_CONSUMER_ID
    mode: str = "AUTONOMOUS_OFFLINE_LOCAL"
    human_attended: bool = False
    allow_network: bool = False
    allow_spend: bool = False
    allow_publish: bool = False
    provider_entitlement: bool = False

    def __post_init__(self) -> None:
        _require(
            self.schema_version == PULSE_INTAKE_CONTRACT_VERSION,
            "INVALID_RECEIVER_AUTH_SCHEMA",
        )
        _require_ref(self.authorization_ref, "INVALID_RECEIVER_AUTH_REF")
        _require_ref(self.caller_id, "INVALID_RECEIVER_CALLER")
        _require(
            type(self.journey_id) is str
            and _SANITIZED_JOURNEY_RE.fullmatch(self.journey_id) is not None,
            "SANITIZED_JOURNEY_ID_REQUIRED",
        )
        _require_text(self.cycle_id, "INVALID_CYCLE_ID")
        _require_text(self.job_id, "INVALID_JOB_ID")
        _require_pos_int(self.attempt, "INVALID_ATTEMPT")
        _require_source_sha(self.source_sha, "INVALID_SOURCE_SHA")
        _require(
            type(self.packet_id) is str and _PACKET_ID_RE.fullmatch(self.packet_id) is not None,
            "INVALID_PACKET_ID",
        )
        _require_sha256(self.packet_sha256, "INVALID_PACKET_SHA256")
        _require(
            type(self.logical_identity) is str
            and _LOGICAL_ID_RE.fullmatch(self.logical_identity) is not None,
            "INVALID_LOGICAL_IDENTITY",
        )
        _require(self.route_id == PULSE_LOCAL_ROUTE_ID, "INVALID_HANDOFF_ROUTE")
        _require(self.consumer_id == PULSE_LOCAL_CONSUMER_ID, "INVALID_PULSE_CONSUMER")
        _require(self.mode == "AUTONOMOUS_OFFLINE_LOCAL", "OFFLINE_AUTHORITY_VIOLATION")
        _require(self.human_attended is False, "AUTONOMOUS_MODE_CANNOT_CLAIM_HUMAN_ATTENDED")
        _require(
            self.allow_network is False
            and self.allow_spend is False
            and self.allow_publish is False
            and self.provider_entitlement is False,
            "OFFLINE_AUTHORITY_VIOLATION",
        )

    def to_dict(self) -> dict[str, object]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_handoff_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, record: object) -> A04PulseReceiverAuthorization:
        names = {f.name for f in fields(cls)}
        _require(type(record) is dict and set(record) == names, "INVALID_RECEIVER_AUTH_FIELDS")
        return cls(**record)


def validate_a04_pulse_receiver_intake(
    packet: A04PulseHandoffPacket | Mapping[str, object] | bytes,
    *,
    a04_receipt: A04CreativeHandoffReceipt | Mapping[str, object] | bytes,
    directive: ValidatedContract | Mapping[str, object] | bytes,
    authorization: A04PulseReceiverAuthorization,
    expected_source_sha: str,
    directive_bytes: bytes | None = None,
    receipt_bytes: bytes | None = None,
    package_bytes: bytes | None = None,
    a03_result_bytes: bytes | None = None,
    asset_bytes_by_ref: Mapping[str, bytes] | None = None,
    rights_bytes_by_ref: Mapping[str, bytes] | None = None,
    expected_journey_id: str | None = None,
    expected_cycle_id: str | None = None,
    expected_directive_version: int | None = None,
    min_directive_version: int | None = None,
    expected_package_revision: int | None = None,
    min_package_revision: int | None = None,
    seen_identities: Collection[str] | None = None,
    seen_packets: Sequence[A04PulseHandoffPacket] | None = None,
) -> A04PulseHandoffValidationResult:
    """Strict pure validation for autonomous LOCAL/OFFLINE PULSE receiver admission."""
    _require(
        type(authorization) is A04PulseReceiverAuthorization,
        "RECEIVER_AUTHORIZATION_REQUIRED",
    )
    _require_source_sha(expected_source_sha, "STALE_OR_MISMATCHED_SOURCE_SHA")
    _require(
        authorization.source_sha == expected_source_sha,
        "STALE_OR_MISMATCHED_SOURCE_SHA",
    )
    if expected_journey_id is not None:
        _require(authorization.journey_id == expected_journey_id, "JOURNEY_MISMATCH")
    if expected_cycle_id is not None:
        _require(authorization.cycle_id == expected_cycle_id, "CYCLE_MISMATCH")

    result = validate_a04_pulse_handoff_packet(
        packet,
        a04_receipt=a04_receipt,
        directive=directive,
        directive_bytes=directive_bytes,
        receipt_bytes=receipt_bytes,
        package_bytes=package_bytes,
        a03_result_bytes=a03_result_bytes,
        asset_bytes_by_ref=asset_bytes_by_ref,
        rights_bytes_by_ref=rights_bytes_by_ref,
        expected_source_sha=expected_source_sha,
        expected_journey_id=authorization.journey_id,
        expected_cycle_id=authorization.cycle_id,
        expected_directive_version=expected_directive_version,
        min_directive_version=min_directive_version,
        expected_package_revision=expected_package_revision,
        min_package_revision=min_package_revision,
        seen_identities=seen_identities,
        seen_packets=seen_packets,
    )
    checked = result.packet
    _require(
        authorization.journey_id == checked.journey_id
        and authorization.cycle_id == checked.cycle_id
        and authorization.job_id == checked.job_id
        and authorization.attempt == checked.attempt
        and authorization.source_sha == checked.source_sha
        and authorization.packet_id == checked.packet_id
        and authorization.packet_sha256 == checked.packet_sha256
        and authorization.logical_identity == checked.logical_identity,
        "RECEIVER_AUTH_PACKET_MISMATCH",
    )
    _require(
        _SANITIZED_JOURNEY_RE.fullmatch(checked.journey_id) is not None,
        "SANITIZED_JOURNEY_ID_REQUIRED",
    )
    _require(
        checked.product_ref is None
        or _SANITIZED_PRODUCT_RE.fullmatch(checked.product_ref) is not None,
        "OPAQUE_PRODUCT_REQUIRED",
    )
    _require(
        checked.scope.action_class == "EXPORT_DRAFT"
        and checked.scope.action_classes == ("EXPORT_DRAFT",)
        and checked.scope.provider_ref is None
        and checked.scope.account_ref is None
        and checked.scope.surface is None
        and checked.scope.asset_sha256 is None,
        "OFFLINE_EXPORT_SCOPE_REQUIRED",
    )
    _require(
        checked.scope.allowed_tools == (PULSE_OFFLINE_TOOL,),
        "OFFLINE_TOOL_ALLOWLIST_REQUIRED",
    )
    _require(
        _SANITIZED_PROVENANCE_REF_RE.fullmatch(checked.provenance.directive_actor_ref) is not None
        and _SANITIZED_PROVENANCE_REF_RE.fullmatch(checked.provenance.directive_source_ref) is not None,
        "SANITIZED_SOURCE_REF_REQUIRED",
    )
    _require(1 <= len(checked.assets) <= 4, "ASSET_SELECTION_MISMATCH")
    for pin in checked.assets:
        _require(_SANITIZED_ASSET_RE.fullmatch(pin.asset_ref) is not None, "ASSET_SELECTION_MISMATCH")
        if pin.rights_ref is not None:
            _require(
                _SANITIZED_RIGHTS_RE.fullmatch(pin.rights_ref) is not None,
                "SANITIZED_RIGHTS_REF_REQUIRED",
            )
    return result
