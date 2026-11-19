"""V2 generation intent and asynchronous provider observations, separate from V1.

The async contract describes jobs that finish later; methods make one bounded
local interaction and never poll, retry, or start a background worker.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Protocol

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from .base import Cost
from .files import SUPPORTED_MIME_KINDS

DIGEST = re.compile(r"[0-9a-f]{64}")
STATES = {"SUBMITTED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "UNKNOWN"}
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError(f"Invalid {name}")


def _digest(value: str, name: str) -> None:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ValueError(f"Invalid {name}")


@dataclass(frozen=True)
class GenerationJobRequest:
    provider: str
    model: str
    source_ref: str
    source_sha256: str
    mime_type: str = "image/png"
    prompt_sha256: str | None = None
    prompt_version: str | None = None
    rights_status: str = "UNKNOWN"
    rights_ref: str | None = None
    job_version: int = field(default=2, init=False)

    def __post_init__(self) -> None:
        for name in ("provider", "model", "source_ref"):
            _text(getattr(self, name), name)
        _digest(self.source_sha256, "source_sha256")
        if self.mime_type not in SUPPORTED_MIME_KINDS:
            raise ValueError("Unsupported output MIME")
        if self.prompt_sha256 is not None:
            _digest(self.prompt_sha256, "prompt_sha256")
        if self.prompt_version is not None:
            _text(self.prompt_version, "prompt_version")
            if self.prompt_sha256 is None:
                raise ValueError("Prompt version requires a prompt digest")
        if self.rights_status not in {"UNKNOWN", "GRANTED", "RESTRICTED", "NOT_APPLICABLE"}:
            raise ValueError("Invalid rights status")
        if self.rights_ref is not None:
            _text(self.rights_ref, "rights_ref")
        if self.rights_status == "GRANTED" and self.rights_ref is None:
            raise ValueError("GRANTED requires rights evidence; a generation label grants no rights")

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, record: dict) -> GenerationJobRequest:
        if (not isinstance(record, dict) or set(record) != set(cls.__dataclass_fields__)
                or type(record["job_version"]) is not int or record["job_version"] != 2):
            raise ValueError("Invalid generation request fields/version")
        return cls(**{k: v for k, v in record.items() if k != "job_version"})


@dataclass(frozen=True)
class GenerationEvent:
    event_id: str
    external_request_id: str
    request_digest: str
    sequence: int
    state: str
    side_effects: str = "UNKNOWN"
    cost: Cost = field(default_factory=Cost)
    output_sha256: str | None = None
    output_kind: str = "UNKNOWN"

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        _text(self.external_request_id, "external_request_id")
        _digest(self.request_digest, "request_digest")
        if type(self.sequence) is not int or self.sequence < 0 or self.state not in STATES:
            raise ValueError("Invalid provider state/sequence")
        if self.side_effects not in {"NONE", "UNKNOWN", "CONFIRMED"} or not isinstance(self.cost, Cost):
            raise ValueError("Invalid side-effect/cost certainty")
        if self.output_kind not in {"UNKNOWN", "SYNTHETIC_FIXTURE", "MODEL_GENERATED", "DERIVED_RENDER"}:
            raise ValueError("Invalid output provenance")
        if self.output_sha256 is not None:
            _digest(self.output_sha256, "output_sha256")
        if self.output_kind != "UNKNOWN" and (self.output_sha256 is None or self.state != "SUCCEEDED"
                                               or self.side_effects != "CONFIRMED"):
            raise ValueError("Proven output requires observed success, effects and output hash")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, record: dict) -> GenerationEvent:
        if not isinstance(record, dict) or set(record) != set(cls.__dataclass_fields__):
            raise ValueError("Invalid provider event fields")
        if not isinstance(record["cost"], dict) or set(record["cost"]) != {"status", "amount"}:
            raise ValueError("Invalid event cost")
        return cls(**{**record, "cost": Cost(**record["cost"])})


@dataclass(frozen=True)
class GenerationProviderCapabilities:
    name: str
    model: str
    synthetic_fixture: bool = False
    network_required: bool = False
    external_spend_possible: bool = False


@dataclass(frozen=True)
class GenerationFile:
    path: Path
    mime_type: str
    sha256: str
    external_request_id: str


class AsyncGenerationProvider(Protocol):
    @property
    def capabilities(self) -> GenerationProviderCapabilities: ...

    def submit(self, request: GenerationJobRequest, *, client_token: str) -> GenerationEvent: ...

    def observe(self, external_request_id: str) -> tuple[GenerationEvent, ...]: ...

    def fetch(self, external_request_id: str) -> GenerationFile: ...

    def reconcile(self, request: GenerationJobRequest, *, client_token: str,
                  external_request_id: str | None) -> tuple[GenerationEvent, ...]: ...


A04_CREATIVE_CONSUMER_ID = "ai_commerce.media.generation:accept_creative_production_package"
A04_HANDOFF_RECEIPT_SCHEMA = "A04CreativeHandoffReceipt.v1"
_SOURCE_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_PACKAGE_ID = re.compile(r"package:[0-9a-f]{64}")
_SELECTION_ID = re.compile(r"selection:[0-9a-f]{64}")
_CONCEPT_ID = re.compile(r"concept:[0-9a-f]{64}")
_RECEIPT_ID = re.compile(r"a04handoff-[0-9a-f]{64}")
_AUDIO_MODES = frozenset({"SILENCE", "AMBIENT_ONLY", "SPOKEN", "MUSIC_ONLY", "MUSIC_AND_SPOKEN"})
_MAX_A03_RECORD_BYTES = 4 * 1024 * 1024
_REPARSE_ATTR = 0x400


def _strict_text(value: object, name: str) -> None:
    if (type(value) is not str or not 0 < len(value) <= 1024
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        raise ValueError(f"Invalid {name}")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError(f"Invalid {name}") from None


def _strict_digest(value: object, name: str) -> None:
    if type(value) is not str or not DIGEST.fullmatch(value):
        raise ValueError(f"Invalid {name}")


def _strict_int(value: object, name: str, *, minimum: int = 1) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"Invalid {name}")


def _check_not_link(path: Path) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & _REPARSE_ATTR):
        raise ValueError("A04_UNSAFE_LINK_PATH")


def _safe_local_path(value: str | Path, name: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise ValueError(f"Invalid {name}")
    text = str(value)
    if not text or any(ord(c) < 32 for c in text):
        raise ValueError(f"Invalid {name}")
    raw_path = Path(text)
    if ".." in raw_path.parts:
        raise ValueError(f"Unsafe traversal in {name}")
    abs_path = Path(os.path.abspath(raw_path))
    for ancestor in (*reversed(abs_path.parents), abs_path):
        _check_not_link(ancestor)
    return abs_path


def _read_exact_file(path: Path, *, limit: int = _MAX_A03_RECORD_BYTES) -> bytes:
    _check_not_link(path)
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("A04_REGULAR_FILE_REQUIRED")
    if info.st_size > limit:
        raise ValueError("A04_FILE_TOO_LARGE")
    data = path.read_bytes()
    if len(data) > limit:
        raise ValueError("A04_FILE_TOO_LARGE")
    return data


def _write_immutable_receipt(path: Path, content: bytes) -> None:
    safe = _safe_local_path(path, "receipt_path")
    safe.parent.mkdir(parents=True, exist_ok=True)
    _check_not_link(safe.parent)
    try:
        with safe.open("xb") as target:
            written = target.write(content)
            if written != len(content):
                raise OSError("Short write on A04 receipt")
    except FileExistsError:
        if _read_exact_file(safe, limit=131072) != content:
            raise ValueError("A04_RECEIPT_CONFLICT") from None
    if _read_exact_file(safe, limit=131072) != content:
        raise ValueError("A04_RECEIPT_BYTES_MISMATCH")


def _receipt_identity(body: dict) -> str:
    payload = {k: v for k, v in body.items() if k != "receipt_id"}
    return "a04handoff-" + sha256_bytes(canonical_bytes(payload))


@dataclass(frozen=True, kw_only=True)
class A04CreativeHandoffReceipt:
    """Native A04 source-only receipt for a verified A03 production package.

    This proves SOURCE_HANDOFF_VERIFIED only. It NEVER asserts video creation,
    model execution, rights grant, creative QA approval, or PULSE execution.
    """

    receipt_id: str
    consumer_id: str
    journey_id: str
    cycle_id: str
    job_id: str
    attempt: int
    actor_ref: str
    job_actor_ref: str
    source_sha: str
    a03_result_ref: str
    a03_result_digest: str
    package_id: str
    revision: int
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
    reasoning_receipt_digest: str | None
    audio_mode: str
    scene_count: int
    media_target_count: int
    rights_status: str = "UNKNOWN"
    status: str = "SOURCE_HANDOFF_VERIFIED"
    media_execution: str = "NOT_ATTEMPTED"
    factual_status: str = "UNVERIFIED"
    semantic_approval: str = "NOT_ATTEMPTED"
    qa_status: str = "UNVERIFIED"
    pulse_execution: str = "NOT_ATTEMPTED"
    schema_version: str = A04_HANDOFF_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != A04_HANDOFF_RECEIPT_SCHEMA:
            raise ValueError("Invalid A04 handoff receipt schema")
        if self.consumer_id != A04_CREATIVE_CONSUMER_ID:
            raise ValueError("Invalid A04 handoff consumer_id")
        if self.status != "SOURCE_HANDOFF_VERIFIED":
            raise ValueError("A04 source-only handoff status must be SOURCE_HANDOFF_VERIFIED")
        if self.media_execution != "NOT_ATTEMPTED":
            raise ValueError("A04 source-only handoff must keep media_execution=NOT_ATTEMPTED")
        if self.factual_status != "UNVERIFIED":
            raise ValueError("A04 source-only handoff cannot promote factual_status")
        if self.semantic_approval != "NOT_ATTEMPTED":
            raise ValueError("A04 source-only handoff cannot perform semantic approval")
        if self.qa_status != "UNVERIFIED":
            raise ValueError("A04 source-only handoff cannot assert QA pass")
        if self.pulse_execution != "NOT_ATTEMPTED":
            raise ValueError("A04 source-only handoff cannot assert PULSE execution")
        if self.rights_status not in ("UNKNOWN", "EVIDENCE_BOUND"):
            raise ValueError("A04 source-only handoff cannot grant rights")
        for name in ("journey_id", "cycle_id", "job_id", "actor_ref", "job_actor_ref",
                     "a03_result_ref", "package_ref", "brief_id"):
            _strict_text(getattr(self, name), name)
        if type(self.source_sha) is not str or not _SOURCE_SHA.fullmatch(self.source_sha):
            raise ValueError("Invalid source_sha")
        if type(self.package_id) is not str or not _PACKAGE_ID.fullmatch(self.package_id):
            raise ValueError("Invalid package_id")
        if type(self.selection_id) is not str or not _SELECTION_ID.fullmatch(self.selection_id):
            raise ValueError("Invalid selection_id")
        if type(self.concept_id) is not str or not _CONCEPT_ID.fullmatch(self.concept_id):
            raise ValueError("Invalid concept_id")
        if type(self.audio_mode) is not str or self.audio_mode not in _AUDIO_MODES:
            raise ValueError("Invalid audio_mode")
        _strict_int(self.attempt, "attempt", minimum=1)
        _strict_int(self.revision, "revision", minimum=1)
        _strict_int(self.concept_revision, "concept_revision", minimum=1)
        _strict_int(self.scene_count, "scene_count", minimum=1)
        _strict_int(self.media_target_count, "media_target_count", minimum=1)
        for name in ("a03_result_digest", "package_digest", "source_brief_digest",
                     "concept_digest", "cycle_job_digest", "cycle_result_digest"):
            _strict_digest(getattr(self, name), name)
        if self.revision == 1:
            if self.parent_package_digest is not None:
                raise ValueError("Initial package revision cannot carry parent_package_digest")
        else:
            _strict_digest(self.parent_package_digest, "parent_package_digest")
        if self.reasoning_receipt_digest is not None:
            _strict_digest(self.reasoning_receipt_digest, "reasoning_receipt_digest")
        expected_id = _receipt_identity(self.to_dict())
        if type(self.receipt_id) is not str or not _RECEIPT_ID.fullmatch(self.receipt_id) or self.receipt_id != expected_id:
            raise ValueError("A04 handoff receipt_id digest mismatch")

    @classmethod
    def create(cls, **kwargs) -> A04CreativeHandoffReceipt:
        if "receipt_id" in kwargs:
            raise ValueError("receipt_id is derived deterministically")
        body = {
            "consumer_id": A04_CREATIVE_CONSUMER_ID,
            "rights_status": "UNKNOWN",
            "status": "SOURCE_HANDOFF_VERIFIED",
            "media_execution": "NOT_ATTEMPTED",
            "factual_status": "UNVERIFIED",
            "semantic_approval": "NOT_ATTEMPTED",
            "qa_status": "UNVERIFIED",
            "pulse_execution": "NOT_ATTEMPTED",
            "schema_version": A04_HANDOFF_RECEIPT_SCHEMA,
            **kwargs,
        }
        body["receipt_id"] = _receipt_identity(body)
        return cls(**body)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_bytes(self.to_dict()))

    @classmethod
    def from_dict(cls, record: object) -> A04CreativeHandoffReceipt:
        names = {f.name for f in fields(cls)}
        if type(record) is not dict or set(record) != names:
            raise ValueError("Invalid A04 handoff receipt fields")
        return cls(**record)


def a04_handoff_receipt_path(receipt_dir: str | Path, receipt_id: str) -> Path:
    if type(receipt_id) is not str or not _RECEIPT_ID.fullmatch(receipt_id):
        raise ValueError("Invalid A04 handoff receipt_id")
    return _safe_local_path(receipt_dir, "receipt_dir") / f"{receipt_id}.json"


def load_a04_handoff_receipt(path: str | Path) -> A04CreativeHandoffReceipt:
    from ai_commerce.creative._validation import read_json

    safe = _safe_local_path(path, "receipt_path")
    raw = _read_exact_file(safe, limit=131072)
    parsed = read_json(raw.decode("utf-8"))
    receipt = A04CreativeHandoffReceipt.from_dict(parsed)
    if raw != canonical_bytes(receipt.to_dict()):
        raise ValueError("A04_RECEIPT_BYTES_MISMATCH")
    if safe.name != f"{receipt.receipt_id}.json":
        raise ValueError("A04_RECEIPT_FILENAME_MISMATCH")
    return receipt


def accept_creative_production_package(
    *,
    creative_store_root: str | Path,
    package_id: str,
    revision: int,
    expected_package_digest: str,
    a03_result_ref: str | Path,
    expected_result_digest: str,
    source_sha: str,
    journey_id: str,
    cycle_id: str,
    job_id: str,
    attempt: int,
    actor_ref: str,
    receipt_dir: str | Path | None = None,
) -> A04CreativeHandoffReceipt:
    """Strict A03 -> A04 source-only intake on a persisted production package.

    Reads exact raw bytes, digest index, saved A03CreativeProductionResult.v1,
    R2 cycle provenance, selected concept/brief lineage and immutable parent
    chain. Returns a native SOURCE_HANDOFF_VERIFIED receipt with
    media_execution=NOT_ATTEMPTED. Never manufactures GenerationJobRequest,
    media assets, rights grants, QA passes, or PULSE execution.
    """
    from ai_commerce.creative._validation import read_json
    from ai_commerce.creative.briefs import brief_digest, intake_brief
    from ai_commerce.creative.models import ConceptRecord, ConceptSelection
    from ai_commerce.creative.sidecars import CreativeSidecarStore
    from ai_commerce.creative.v2 import A03CreativeProductionResult

    if type(package_id) is not str or not _PACKAGE_ID.fullmatch(package_id):
        raise ValueError("A04_INVALID_PACKAGE_ID")
    _strict_int(revision, "revision", minimum=1)
    _strict_digest(expected_package_digest, "expected_package_digest")
    _strict_digest(expected_result_digest, "expected_result_digest")
    if type(source_sha) is not str or not _SOURCE_SHA.fullmatch(source_sha):
        raise ValueError("A04_INVALID_SOURCE_SHA")
    for name, value in (("journey_id", journey_id), ("cycle_id", cycle_id),
                        ("job_id", job_id), ("actor_ref", actor_ref)):
        _strict_text(value, name)
    _strict_int(attempt, "attempt", minimum=1)

    store_root = _safe_local_path(creative_store_root, "creative_store_root")
    result_path = _safe_local_path(a03_result_ref, "a03_result_ref")
    sidecars = CreativeSidecarStore(store_root)

    expected_result_path = _safe_local_path(sidecars.production_result_path(expected_result_digest), "expected_result_path")
    if result_path != expected_result_path:
        raise ValueError("A04_RESULT_PATH_MISMATCH")

    raw_result = _read_exact_file(result_path)
    if sha256_bytes(raw_result) != expected_result_digest:
        raise ValueError("A04_RESULT_DIGEST_MISMATCH")
    parsed_result = A03CreativeProductionResult.from_dict(read_json(raw_result.decode("utf-8")))
    if raw_result != canonical_bytes(parsed_result.to_dict()) or parsed_result.digest != expected_result_digest:
        raise ValueError("A04_RESULT_BYTES_MISMATCH")

    origin, pkg, handoff = sidecars.verify_production_package_lineage(
        package_id, revision, origin_result_digest=expected_result_digest,
    )
    if origin != parsed_result:
        raise ValueError("A04_RESULT_LINEAGE_MISMATCH")
    if (origin.status != "CREATIVE_COMPLETE" or origin.failure is not None
            or origin.job.purpose != "PRODUCTION" or origin.stage is None
            or origin.stage.status != "CREATIVE_COMPLETE" or origin.package is None):
        raise ValueError("A04_REJECTED_NON_PRODUCTION_RESULT")
    if (origin.job.journey_id != journey_id or origin.job.cycle_id != cycle_id
            or origin.job.job_id != job_id or origin.job.attempt != attempt):
        raise ValueError("A04_JOB_CONTEXT_MISMATCH")

    for checked in (origin.package, pkg):
        exec_obs = checked.execution
        if (checked.observation.mode != "MODEL" or checked.observation.model == "UNKNOWN"
                or checked.configuration is None or exec_obs is None
                or exec_obs.kind != "AUTHENTICATED_CLI_PROBE"
                or exec_obs.effect != "PROCESS_INVOKED"
                or exec_obs.authorization_ref != origin.authorization.authorization_ref
                or (exec_obs.observed_model, exec_obs.observed_effort)
                != (checked.configuration.model, checked.configuration.effort)
                or checked.lineage.cycle_status != "CREATIVE_COMPLETE"):
            raise ValueError("A04_UNVERIFIED_MODEL_PROVENANCE")

    cycle = sidecars.load_cycle_by_digest(pkg.lineage.cycle_job_digest, pkg.lineage.cycle_result_digest)
    if cycle.status != "CREATIVE_COMPLETE" or cycle.job.seed.purpose != "PRODUCTION":
        raise ValueError("A04_UNVERIFIED_CYCLE_PROVENANCE")
    for step in cycle.steps:
        step_exec = step.reasoning.execution
        if (step.status != "CREATIVE_COMPLETE" or step.reasoning.observation.mode != "MODEL"
                or step.reasoning.observation.model == "UNKNOWN"
                or step_exec is None or step_exec.kind != "AUTHENTICATED_CLI_PROBE"
                or step_exec.effect != "PROCESS_INVOKED"):
            raise ValueError("A04_UNVERIFIED_CYCLE_STEP_PROVENANCE")

    package_path = _safe_local_path(sidecars.production_package_path(package_id, revision), "package_path")
    raw_pkg = _read_exact_file(package_path)
    if (sha256_bytes(raw_pkg) != expected_package_digest
            or expected_package_digest != pkg.digest
            or raw_pkg != canonical_bytes(pkg.to_dict())):
        raise ValueError("A04_PACKAGE_BYTES_MISMATCH")

    index_path = _safe_local_path(sidecars.production_package_digest_path(expected_package_digest), "index_path")
    expected_index = canonical_bytes({
        "package_id": pkg.package_id, "revision": pkg.revision, "package_digest": pkg.digest,
    })
    if _read_exact_file(index_path, limit=4096) != expected_index:
        raise ValueError("A04_PACKAGE_INDEX_MISMATCH")

    brief = intake_brief(handoff["brief"])
    if brief["brief_id"] != pkg.brief_id or brief_digest(brief) != pkg.source_brief_digest:
        raise ValueError("A04_BRIEF_LINEAGE_MISMATCH")
    raw_selection = handoff["selection"]
    if type(raw_selection) is not dict or "selection_id" not in raw_selection:
        raise ValueError("A04_CONCEPT_LINEAGE_MISMATCH")
    selection = ConceptSelection.from_dict({k: v for k, v in raw_selection.items() if k != "selection_id"})
    if raw_selection["selection_id"] != selection.selection_id:
        raise ValueError("A04_CONCEPT_LINEAGE_MISMATCH")
    concept = ConceptRecord.from_dict(handoff["concept"])
    if (selection != pkg.selected_concept or concept.digest != selection.concept_digest
            or concept.concept_id != selection.concept_id or concept.revision != selection.revision):
        raise ValueError("A04_CONCEPT_LINEAGE_MISMATCH")

    expected_actor = f"{pkg.edit_actor.kind}:{pkg.edit_actor.ref}"
    if actor_ref != expected_actor:
        raise ValueError("A04_ACTOR_MISMATCH")
    if (pkg.factual_status != "UNVERIFIED" or pkg.rights_status not in ("UNKNOWN", "EVIDENCE_BOUND")
            or any(t.generation_status != "NOT_GENERATED" for t in pkg.spec.media_targets)
            or any(q.verification_status != "UNVERIFIED" for q in pkg.spec.qa_expectations)):
        raise ValueError("A04_PREMATURE_CLAIM_REJECTED")

    receipt = A04CreativeHandoffReceipt.create(
        journey_id=journey_id,
        cycle_id=cycle_id,
        job_id=job_id,
        attempt=attempt,
        actor_ref=expected_actor,
        job_actor_ref=origin.job.provenance.actor_ref,
        source_sha=source_sha,
        a03_result_ref=str(result_path),
        a03_result_digest=origin.digest,
        package_id=pkg.package_id,
        revision=pkg.revision,
        parent_package_digest=pkg.parent.digest if pkg.parent is not None else None,
        package_ref=str(package_path),
        package_digest=pkg.digest,
        brief_id=pkg.brief_id,
        source_brief_digest=pkg.source_brief_digest,
        selection_id=selection.selection_id,
        concept_id=concept.concept_id,
        concept_revision=concept.revision,
        concept_digest=concept.digest,
        cycle_job_digest=pkg.lineage.cycle_job_digest,
        cycle_result_digest=pkg.lineage.cycle_result_digest,
        reasoning_receipt_digest=pkg.reasoning_receipt_digest,
        audio_mode=pkg.spec.audio.mode,
        scene_count=len(pkg.spec.scenes),
        media_target_count=len(pkg.spec.media_targets),
        rights_status=pkg.rights_status,
    )
    if receipt_dir is not None:
        dest = a04_handoff_receipt_path(receipt_dir, receipt.receipt_id)
        _write_immutable_receipt(dest, canonical_bytes(receipt.to_dict()))
        if load_a04_handoff_receipt(dest) != receipt:
            raise ValueError("A04_RECEIPT_PERSIST_MISMATCH")
    return receipt
