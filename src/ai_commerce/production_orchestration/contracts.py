"""Strict production contracts. Hashes bind claims; they do not attest execution.

Route authorization must be supplied independently by the owner of this API.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from hashlib import sha256
import json
import re
from types import MappingProxyType
from typing import ClassVar, Literal, Self


class OrchestrationError(ValueError):
    """Bounded diagnostic code without source content."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise OrchestrationError(code)


def _text(value: object) -> None:
    require(type(value) is str and 0 < len(value) <= 512
            and value == value.strip() and not any(ord(c) < 32 for c in value), "INVALID_REFERENCE")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise OrchestrationError("INVALID_REFERENCE") from None


def _hash(value: object) -> None:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "INVALID_SHA256")


def bytes_digest(value: bytes) -> str:
    require(type(value) is bytes, "BYTES_REQUIRED")
    return sha256(value).hexdigest()


def canonical_bytes(value: object) -> bytes:
    """Detached strict JSON, compatible with the existing ContentBrief digest."""
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        require(depth <= 20 and nodes <= 8192, "JSON_COMPLEXITY_LIMIT")
        if type(item) is dict:
            require(all(type(k) is str for k in item), "INVALID_JSON_OBJECT")
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            pending.extend((v, depth + 1) for v in item)
        else:
            require(item is None or type(item) in (str, bool, int, float), "INVALID_JSON_VALUE")
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise OrchestrationError("INVALID_JSON_VALUE") from None
    require(len(raw) <= 131072, "DOCUMENT_TOO_LARGE")
    return raw


def digest(value: object) -> str:
    return bytes_digest(canonical_bytes(value))


# These are existing registered source owners, not executable routing entries.
# M05 consumes OpportunityProposal.v1; A03 consumes ContentBrief.v0.1;
# A04 consumes verified A03CreativeProductionPackage.v1 (source-only intake).
CONSUMER_ROUTES = MappingProxyType({
    "a10-m05-content-brief": (
        "ai_commerce.opportunity.bridge.briefs:opportunity_to_brief",
        "src/ai_commerce/opportunity/bridge/briefs.py",
    ),
    "a03-creative-brief": (
        "ai_commerce.creative.briefs:intake_brief",
        "src/ai_commerce/creative/briefs.py",
    ),
    "a04-media-preparation": (
        "ai_commerce.media.generation:accept_creative_production_package",
        "src/ai_commerce/media/generation.py",
    ),
    "a04-to-pulse-offline": (
        "ai_commerce.production_runtime.attended_worker:receive_a04_pulse_handoff",
        "src/ai_commerce/production_runtime/attended_worker.py",
    ),
})

ROUTE_OUTPUT_CONTRACTS = MappingProxyType({
    "a10-m05-content-brief": "ContentBrief.v0.1",
    "a03-creative-brief": "ContentBrief.v0.1",
    "a04-media-preparation": "A03CreativeProductionPackage.v1",
    "a04-to-pulse-offline": "A04PulseOfflineHandoffPacket.v1",
})

# P1/R4/W2 execute only these deterministic local edges. P0 routes remain snapshots.
LOCAL_ROUTES = MappingProxyType({
    "a10-m05-to-a03": ("A10.M05", "a03-creative-brief"),
    "a03-to-a04": ("A03.creative", "a04-media-preparation"),
    "a04-to-pulse-offline": ("A04.pulse_handoff", "a04-to-pulse-offline"),
})


class _Record:
    VERSION: ClassVar[str]

    def to_dict(self) -> dict:
        value = asdict(self)
        # Preserve original P0/P1 wire bytes/digests, including nested records.
        for name in ("spec", "receipt", "pulse_handoff"):
            child = getattr(self, name, None)
            if isinstance(child, _Record):
                value[name] = child.to_dict()
        if type(self) is StageSpec:
            if self.local_handoff is None:
                del value["local_handoff"]
            if self.package_handoff is None:
                del value["package_handoff"]
            if self.pulse_handoff is None:
                del value["pulse_handoff"]
        if type(self) is ProductionStageReceipt and not self.spec.has_local_handoff:
            if self.outcome == "SUCCESS":
                del value["outcome"]
            if self.artifact_ref is None:
                del value["artifact_ref"]
        if type(self) is ProductionStageDispatched and not self.spec.has_local_handoff:
            if self.phase == "RECEIPT_RECORDED":
                del value["phase"]
            if self.dispatch_ref is None:
                del value["dispatch_ref"]
        return value

    @property
    def sha256(self) -> str:
        return digest(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> Self:
        names = {f.name for f in fields(cls)}
        # P1/R4/W2 extensions have defaults so original P0/P1 detached records still load.
        optional = names & {"local_handoff", "package_handoff", "pulse_handoff", "outcome", "artifact_ref"}
        require(type(value) is dict and names - optional <= set(value) <= names, "CONTRACT_FIELDS")
        args = dict(value)
        nested = {
            "provenance": Provenance,
            "authorization": RouteAuthorization,
            "spec": StageSpec,
            "local_handoff": LocalHandoff,
            "package_handoff": A03PackageHandoff,
            "pulse_handoff": A04PulseHandoff,
        }
        for key, kind in nested.items():
            if key in args and args[key] is not None:
                args[key] = kind.from_dict(args[key])
        return cls(**args)

    def _version(self) -> None:
        require(type(self.schema_version) is str and self.schema_version == self.VERSION, "SCHEMA_VERSION")


@dataclass(frozen=True, kw_only=True)
class Provenance(_Record):
    actor_ref: str
    source_ref: str
    source_sha256: str
    evidence_type: Literal["HUMAN_SUPPLIED", "SYNTHETIC_FIXTURE", "UNKNOWN"]

    def __post_init__(self) -> None:
        _text(self.actor_ref)
        _text(self.source_ref)
        _hash(self.source_sha256)
        require(type(self.evidence_type) is str and self.evidence_type in
                {"HUMAN_SUPPLIED", "SYNTHETIC_FIXTURE", "UNKNOWN"}, "PROVENANCE_TYPE")


@dataclass(frozen=True, kw_only=True)
class LocalAuthorization(_Record):
    """Trusted caller grant, never read from an event or producer manifest."""
    journey_id: str
    cycle_id: str
    job_id: str
    source_sha: str
    authorization_ref: str
    route_id: str = "a10-m05-to-a03"
    scope: Literal["SOURCE_ONLY_LOCAL"] = "SOURCE_ONLY_LOCAL"

    def __post_init__(self) -> None:
        for value in (self.journey_id, self.cycle_id, self.job_id, self.authorization_ref):
            _text(value)
        require(type(self.source_sha) is str and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", self.source_sha)
                is not None, "SOURCE_SHA_REQUIRED")
        require(type(self.route_id) is str and self.route_id in LOCAL_ROUTES, "LOCAL_ROUTE_UNREGISTERED")
        require(self.scope == "SOURCE_ONLY_LOCAL", "AUTHORIZATION_SCOPE")


@dataclass(frozen=True, kw_only=True)
class LocalHandoff(_Record):
    journey_id: str
    cycle_id: str
    job_id: str
    source_sha: str
    producer_event_id: str
    producer_result_ref: str
    producer_result_sha256: str
    brief_ref: str
    brief_bytes_sha256: str
    route_id: str = "a10-m05-to-a03"

    def __post_init__(self) -> None:
        LocalAuthorization(journey_id=self.journey_id, cycle_id=self.cycle_id, job_id=self.job_id,
                           source_sha=self.source_sha, authorization_ref="binding", route_id=self.route_id)
        require(self.route_id == "a10-m05-to-a03", "LOCAL_ROUTE_MISMATCH")
        for value in (self.producer_event_id, self.producer_result_ref, self.brief_ref):
            _text(value)
        for value in (self.producer_result_sha256, self.brief_bytes_sha256):
            _hash(value)


@dataclass(frozen=True, kw_only=True)
class A03PackageHandoff(_Record):
    """Exact source-pinned A03 -> A04 local handoff reference."""
    journey_id: str
    cycle_id: str
    job_id: str
    attempt: int
    source_sha: str
    actor_ref: str
    creative_store_root: str
    a03_result_ref: str
    a03_result_digest: str
    package_id: str
    revision: int
    parent_package_digest: str | None
    package_ref: str
    package_bytes_sha256: str
    route_id: str = "a03-to-a04"

    def __post_init__(self) -> None:
        LocalAuthorization(journey_id=self.journey_id, cycle_id=self.cycle_id, job_id=self.job_id,
                           source_sha=self.source_sha, authorization_ref="binding", route_id=self.route_id)
        require(self.route_id == "a03-to-a04", "LOCAL_ROUTE_MISMATCH")
        require(type(self.attempt) is int and self.attempt >= 1, "INVALID_ATTEMPT")
        require(type(self.revision) is int and self.revision >= 1, "INVALID_REVISION")
        for value in (self.actor_ref, self.creative_store_root, self.a03_result_ref, self.package_ref):
            _text(value)
        require(type(self.package_id) is str and re.fullmatch(r"package:[0-9a-f]{64}", self.package_id)
                is not None, "INVALID_PACKAGE_ID")
        for value in (self.a03_result_digest, self.package_bytes_sha256):
            _hash(value)
        if self.revision == 1:
            require(self.parent_package_digest is None, "INVALID_PARENT_REVISION")
        else:
            _hash(self.parent_package_digest)


@dataclass(frozen=True, kw_only=True)
class A04PulseHandoff(_Record):
    """Exact source-pinned A04 -> PULSE bounded offline local handoff reference."""
    journey_id: str
    cycle_id: str
    job_id: str
    attempt: int
    source_sha: str
    packet_id: str
    packet_sha256: str
    logical_identity: str
    packet_ref: str
    packet_bytes_sha256: str
    a04_receipt_id: str
    a04_receipt_digest: str
    a04_receipt_ref: str
    directive_version: int
    directive_sha256: str
    directive_bytes_sha256: str
    directive_ref: str
    asset_root: str
    assets: tuple[str, ...]
    rights: tuple[str, ...] = ()
    package_id: str
    package_revision: int
    package_ref: str | None = None
    a03_result_ref: str | None = None
    route_id: str = "a04-to-pulse-offline"

    def to_dict(self) -> dict:
        value = asdict(self)
        value["assets"] = list(self.assets)
        value["rights"] = list(self.rights)
        return value

    @classmethod
    def from_dict(cls, value: object) -> Self:
        names = {f.name for f in fields(cls)}
        optional = {"rights", "package_ref", "a03_result_ref", "route_id"}
        require(type(value) is dict and names - optional <= set(value) <= names, "CONTRACT_FIELDS")
        args = dict(value)
        require(type(args["assets"]) is list, "CONTRACT_FIELDS")
        args["assets"] = tuple(args["assets"])
        if "rights" in args:
            require(type(args["rights"]) is list, "CONTRACT_FIELDS")
            args["rights"] = tuple(args["rights"])
        return cls(**args)

    def __post_init__(self) -> None:
        LocalAuthorization(journey_id=self.journey_id, cycle_id=self.cycle_id, job_id=self.job_id,
                           source_sha=self.source_sha, authorization_ref="binding", route_id=self.route_id)
        require(self.route_id == "a04-to-pulse-offline", "LOCAL_ROUTE_MISMATCH")
        require(type(self.source_sha) is str and re.fullmatch(r"[0-9a-f]{40}", self.source_sha) is not None,
                "SOURCE_SHA_REQUIRED")
        require(type(self.attempt) is int and self.attempt >= 1, "INVALID_ATTEMPT")
        require(type(self.directive_version) is int and self.directive_version >= 1, "INVALID_DIRECTIVE_VERSION")
        require(type(self.package_revision) is int and self.package_revision >= 1, "INVALID_REVISION")
        require(type(self.packet_id) is str and re.fullmatch(r"a04pulse-[0-9a-f]{64}", self.packet_id) is not None,
                "INVALID_PACKET_ID")
        require(type(self.logical_identity) is str
                and re.fullmatch(r"logical:[0-9a-f]{64}", self.logical_identity) is not None,
                "INVALID_LOGICAL_IDENTITY")
        require(type(self.a04_receipt_id) is str
                and re.fullmatch(r"a04handoff-[0-9a-f]{64}", self.a04_receipt_id) is not None,
                "INVALID_A04_RECEIPT_ID")
        require(type(self.package_id) is str
                and re.fullmatch(r"package:[0-9a-f]{64}", self.package_id) is not None,
                "INVALID_PACKAGE_ID")
        for value in (
            self.packet_sha256, self.packet_bytes_sha256,
            self.a04_receipt_digest, self.directive_sha256, self.directive_bytes_sha256,
        ):
            _hash(value)
        for value in (self.packet_ref, self.a04_receipt_ref, self.directive_ref, self.asset_root):
            _text(value)
        for optional_ref in (self.package_ref, self.a03_result_ref):
            if optional_ref is not None:
                _text(optional_ref)
        require(type(self.assets) is tuple and 1 <= len(self.assets) <= 4, "INVALID_ASSETS")
        for item in self.assets:
            _text(item)
        require(type(self.rights) is tuple and len(self.rights) <= 4, "INVALID_RIGHTS")
        for item in self.rights:
            _text(item)


@dataclass(frozen=True, kw_only=True)
class RouteAuthorization(_Record):
    route_id: str
    consumer_id: str
    consumer_path: str
    authorization_ref: str
    scope: Literal["OFFLINE_RECEIPT_ONLY", "SOURCE_ONLY_LOCAL"] = "OFFLINE_RECEIPT_ONLY"

    def __post_init__(self) -> None:
        for value in (self.route_id, self.consumer_id, self.consumer_path, self.authorization_ref):
            _text(value)
        require(CONSUMER_ROUTES.get(self.route_id) == (self.consumer_id, self.consumer_path), "CONSUMER_BINDING")
        require(type(self.scope) is str and self.scope in
                {"OFFLINE_RECEIPT_ONLY", "SOURCE_ONLY_LOCAL"}, "AUTHORIZATION_SCOPE")


@dataclass(frozen=True, kw_only=True)
class StageSpec(_Record):
    VERSION: ClassVar[str] = "StageSpec.v1"
    stage_id: str
    route_id: str
    consumer_id: str
    consumer_path: str
    source_sha256: str
    input_sha256: str
    output_sha256: str
    idempotency_key: str
    provenance: Provenance
    authorization: RouteAuthorization
    output_contract: Literal[
        "ContentBrief.v0.1", "A03CreativeProductionPackage.v1", "A04PulseOfflineHandoffPacket.v1"
    ] = "ContentBrief.v0.1"
    schema_version: Literal["StageSpec.v1"] = "StageSpec.v1"
    local_handoff: LocalHandoff | None = None
    package_handoff: A03PackageHandoff | None = None
    pulse_handoff: A04PulseHandoff | None = None

    @property
    def has_local_handoff(self) -> bool:
        return (
            self.local_handoff is not None
            or self.package_handoff is not None
            or self.pulse_handoff is not None
        )

    def __post_init__(self) -> None:
        self._version()
        for value in (self.stage_id, self.route_id, self.consumer_id, self.consumer_path, self.idempotency_key):
            _text(value)
        for value in (self.source_sha256, self.input_sha256, self.output_sha256):
            _hash(value)
        require(type(self.provenance) is Provenance and type(self.authorization) is RouteAuthorization,
                "TYPED_BINDING_REQUIRED")
        require(self.provenance.source_sha256 == self.source_sha256, "PROVENANCE_SOURCE_MISMATCH")
        require(CONSUMER_ROUTES.get(self.route_id) == (self.consumer_id, self.consumer_path), "CONSUMER_BINDING")
        auth = self.authorization
        require((auth.route_id, auth.consumer_id, auth.consumer_path) ==
                (self.route_id, self.consumer_id, self.consumer_path), "ROUTE_AUTHORIZATION_MISMATCH")
        require(type(self.output_contract) is str and self.output_contract == ROUTE_OUTPUT_CONTRACTS[self.route_id],
                "OUTPUT_CONTRACT")
        handoff_count = sum(
            h is not None for h in (self.local_handoff, self.package_handoff, self.pulse_handoff)
        )
        require(handoff_count <= 1, "HANDOFF_CONFLICT")
        if self.local_handoff is not None:
            require(type(self.local_handoff) is LocalHandoff, "LOCAL_HANDOFF_REQUIRED")
            require((self.stage_id, self.route_id) == LOCAL_ROUTES[self.local_handoff.route_id],
                    "LOCAL_ROUTE_MISMATCH")
            require(auth.scope == "SOURCE_ONLY_LOCAL", "AUTHORIZATION_SCOPE")
        if self.package_handoff is not None:
            require(type(self.package_handoff) is A03PackageHandoff, "LOCAL_HANDOFF_REQUIRED")
            require((self.stage_id, self.route_id) == LOCAL_ROUTES[self.package_handoff.route_id],
                    "LOCAL_ROUTE_MISMATCH")
            require(auth.scope == "SOURCE_ONLY_LOCAL", "AUTHORIZATION_SCOPE")
        if self.pulse_handoff is not None:
            require(type(self.pulse_handoff) is A04PulseHandoff, "LOCAL_HANDOFF_REQUIRED")
            require((self.stage_id, self.route_id) == LOCAL_ROUTES[self.pulse_handoff.route_id],
                    "LOCAL_ROUTE_MISMATCH")
            require(auth.scope == "SOURCE_ONLY_LOCAL", "AUTHORIZATION_SCOPE")


@dataclass(frozen=True, kw_only=True)
class ProductionStageCompleted(_Record):
    VERSION: ClassVar[str] = "ProductionStageCompleted.v1"
    spec: StageSpec
    completion_ref: str
    source_sha256: str
    input_sha256: str
    output_sha256: str
    schema_version: Literal["ProductionStageCompleted.v1"] = "ProductionStageCompleted.v1"

    def __post_init__(self) -> None:
        self._version()
        require(type(self.spec) is StageSpec, "STAGE_SPEC_REQUIRED")
        _text(self.completion_ref)
        for name in ("source_sha256", "input_sha256", "output_sha256"):
            _hash(getattr(self, name))
            require(getattr(self, name) == getattr(self.spec, name), "COMPLETED_HASH_MISMATCH")


@dataclass(frozen=True, kw_only=True)
class ProductionStageReceipt(_Record):
    VERSION: ClassVar[str] = "ProductionStageReceipt.v1"
    spec: StageSpec
    completed_sha256: str
    output_sha256: str
    consumer_id: str
    receipt_ref: str
    execution_ref: str
    provenance: Provenance
    consumer_executed: Literal[True]
    schema_version: Literal["ProductionStageReceipt.v1"] = "ProductionStageReceipt.v1"
    outcome: Literal["SUCCESS"] = "SUCCESS"
    artifact_ref: str | None = None

    def __post_init__(self) -> None:
        self._version()
        require(type(self.spec) is StageSpec and type(self.provenance) is Provenance, "TYPED_BINDING_REQUIRED")
        for value in (self.completed_sha256, self.output_sha256):
            _hash(value)
        for value in (self.consumer_id, self.receipt_ref, self.execution_ref):
            _text(value)
        require(self.consumer_executed is True, "CONSUMER_NOT_EXECUTED")
        require(type(self.outcome) is str and self.outcome == "SUCCESS", "RECEIPT_OUTCOME")
        if self.spec.has_local_handoff or self.artifact_ref is not None:
            _text(self.artifact_ref)
        require(self.consumer_id == self.spec.consumer_id == self.provenance.actor_ref, "RECEIPT_CONSUMER_MISMATCH")
        require(self.output_sha256 == self.spec.output_sha256, "RECEIPT_OUTPUT_MISMATCH")
        require(self.provenance.source_sha256 == self.spec.source_sha256
                and self.provenance.source_ref == self.spec.provenance.source_ref
                and self.provenance.evidence_type == self.spec.provenance.evidence_type, "RECEIPT_PROVENANCE_MISMATCH")


@dataclass(frozen=True, kw_only=True)
class ProductionStageDispatched(_Record):
    """P1/R4 dispatch evidence or P0-compatible retrospective receipt linkage.

    DISPATCHED proves only a local reservation. Only RECEIPT_RECORDED links
    receiver completion; neither wire representation grants execution authority.
    """
    VERSION: ClassVar[str] = "ProductionStageDispatched.v1"
    spec: StageSpec
    completed_sha256: str
    receipt: ProductionStageReceipt | None
    receipt_sha256: str | None
    schema_version: Literal["ProductionStageDispatched.v1"] = "ProductionStageDispatched.v1"
    phase: Literal["DISPATCHED", "RECEIPT_RECORDED"] = "RECEIPT_RECORDED"
    dispatch_ref: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> Self:
        names = {f.name for f in fields(cls)}
        require(type(value) is dict and names - {"phase", "dispatch_ref"} <= set(value) <= names, "CONTRACT_FIELDS")
        return cls(**{**value, "spec": StageSpec.from_dict(value["spec"]),
                      "receipt": None if value["receipt"] is None else ProductionStageReceipt.from_dict(value["receipt"])})

    def __post_init__(self) -> None:
        self._version()
        require(type(self.spec) is StageSpec, "STAGE_SPEC_REQUIRED")
        _hash(self.completed_sha256)
        require(type(self.phase) is str and self.phase in {"DISPATCHED", "RECEIPT_RECORDED"}, "DISPATCH_PHASE")
        if self.phase == "DISPATCHED":
            require(self.spec.has_local_handoff and self.spec.authorization.scope == "SOURCE_ONLY_LOCAL",
                    "LOCAL_HANDOFF_REQUIRED")
            require(self.receipt is None and self.receipt_sha256 is None, "DISPATCH_NOT_COMPLETION")
            _text(self.dispatch_ref)
            return
        require(type(self.receipt) is ProductionStageReceipt, "RECEIPT_REQUIRED")
        if self.dispatch_ref is not None:
            _text(self.dispatch_ref)
        _hash(self.receipt_sha256)
        require(self.receipt.spec == self.spec and self.receipt.completed_sha256 == self.completed_sha256,
                "DISPATCH_RECEIPT_BINDING")
        require(self.receipt_sha256 == self.receipt.sha256, "DISPATCH_RECEIPT_HASH_MISMATCH")


@dataclass(frozen=True, kw_only=True)
class LocalBrainDecisionInput:
    """Bounded local single-owner BrainDecision.v1 transfer input.

    Records local origin/chat_ref and exact decision bytes for traceability.
    Labels are informational only: this object never claims private verifier
    attestation, cryptographic identity proof, or live CDP operation.
    """
    decision_bytes: bytes
    origin_ref: str
    origin_kind: Literal["LOCAL_CHATGPT_TRANSFER", "TEST_ONLY"] = "LOCAL_CHATGPT_TRANSFER"
    chat_ref: str | None = None
    evidence_request_sha256: str | None = None

    @property
    def is_test_only(self) -> bool:
        return self.origin_kind == "TEST_ONLY"

    def __post_init__(self) -> None:
        require(type(self.decision_bytes) is bytes and 0 < len(self.decision_bytes) <= 131072,
                "BRAIN_DECISION_INVALID")
        _text(self.origin_ref)
        require(
            type(self.origin_kind) is str
            and self.origin_kind in {"LOCAL_CHATGPT_TRANSFER", "TEST_ONLY"},
            "INVALID_BRAIN_ORIGIN_KIND",
        )
        if self.origin_kind == "TEST_ONLY":
            require(self.origin_ref.startswith("TEST_ONLY:"), "TEST_ONLY_ORIGIN_LABEL_REQUIRED")
        else:
            require(not self.origin_ref.startswith("TEST_ONLY:"), "TEST_ONLY_ORIGIN_LABEL_MISMATCH")
        if self.chat_ref is not None:
            _text(self.chat_ref)
        if self.evidence_request_sha256 is not None:
            _hash(self.evidence_request_sha256)


@dataclass(frozen=True, kw_only=True)
class BrainDecisionIntakeResult:
    """Typed fail-closed result of W3a local single-owner BrainDecision.v1 intake.

    READY_FOR_HUMAN_RELEASE is the unchanged B0.1 state name only, not a
    required routine Human review stage. Labels are informational only and
    never claim private-authenticated identity or live CDP operation.
    """
    status: Literal["BRAIN_ACCEPTED", "NO_DECISION", "HOLD"]
    state: Literal["WAIT_BRAIN", "READY_FOR_HUMAN_RELEASE", "UNKNOWN_EFFECT", "HOLD"]
    reason: str
    accepted: bool = False
    idempotent_replay: bool = False
    journey_id: str | None = None
    source_sha: str | None = None
    logical_identity: str | None = None
    directive_sha256: str | None = None
    evidence_request_sha256: str | None = None
    artifact_sha256: str | None = None
    q4_qa_sha256: str | None = None
    decision_id: str | None = None
    decision_sha256: str | None = None
    decision_bytes_sha256: str | None = None
    chat_ref: str | None = None
    event_id: str | None = None
    origin_ref: str | None = None
    origin_kind: Literal["NONE", "LOCAL_CHATGPT_TRANSFER", "TEST_ONLY"] = "NONE"
    accepted_receipt_sha256: str | None = None
    accepted_receipt_file_sha256: str | None = None
    accepted_receipt_ref: str | None = None
    journal_seq: int | None = None
    pulse_journal_checkpoint: dict | None = None
    identity_authenticated: Literal[False] = False
    live_cdp_proven: Literal[False] = False
    rights_granted: Literal[False] = False
    provider_grant: Literal[False] = False
    publish_authorized: Literal[False] = False
    spend_authorized: Literal[False] = False
    network_calls_performed: Literal[0] = 0
    publish_calls: Literal[0] = 0
    paid_generation_calls: Literal[0] = 0
    human_attended_required: Literal[False] = False

    def __post_init__(self) -> None:
        require(
            type(self.status) is str
            and self.status in {"BRAIN_ACCEPTED", "NO_DECISION", "HOLD"},
            "INVALID_BRAIN_INTAKE_STATUS",
        )
        require(
            type(self.state) is str
            and self.state in {"WAIT_BRAIN", "READY_FOR_HUMAN_RELEASE", "UNKNOWN_EFFECT", "HOLD"},
            "INVALID_BRAIN_INTAKE_STATE",
        )
        _text(self.reason)
        require(type(self.accepted) is bool and self.accepted is (self.status == "BRAIN_ACCEPTED"),
                "INVALID_BRAIN_INTAKE_STATUS")
        require(type(self.idempotent_replay) is bool, "INVALID_BRAIN_INTAKE_STATUS")
        require(
            type(self.origin_kind) is str
            and self.origin_kind in {"NONE", "LOCAL_CHATGPT_TRANSFER", "TEST_ONLY"},
            "INVALID_BRAIN_ORIGIN_KIND",
        )
        if self.accepted:
            require(self.state == "READY_FOR_HUMAN_RELEASE", "INVALID_BRAIN_INTAKE_STATE")
            require(self.origin_kind != "NONE", "INVALID_BRAIN_ORIGIN_KIND")
            for value in (
                self.journey_id, self.source_sha, self.logical_identity,
                self.decision_id, self.chat_ref, self.event_id,
                self.origin_ref, self.accepted_receipt_ref,
            ):
                _text(value)
            for value in (
                self.directive_sha256, self.evidence_request_sha256,
                self.artifact_sha256, self.q4_qa_sha256,
                self.decision_sha256, self.decision_bytes_sha256,
                self.accepted_receipt_sha256, self.accepted_receipt_file_sha256,
            ):
                _hash(value)
            require(type(self.journal_seq) is int and self.journal_seq >= 1, "INVALID_JOURNAL_SEQ")
        else:
            require(self.state != "READY_FOR_HUMAN_RELEASE", "INVALID_BRAIN_INTAKE_STATE")
        require(
            self.identity_authenticated is False
            and self.live_cdp_proven is False
            and self.rights_granted is False
            and self.provider_grant is False
            and self.publish_authorized is False
            and self.spend_authorized is False
            and self.network_calls_performed == 0
            and self.publish_calls == 0
            and self.paid_generation_calls == 0
            and self.human_attended_required is False,
            "OFFLINE_AUTHORITY_VIOLATION",
        )
