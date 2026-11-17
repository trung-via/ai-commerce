"""Separate immutable V1.1 storage; existing CreativeStore never auto-upgrades."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ._validation import CreativeValidationError, VersionConflict, exact_fields, read_json
from .briefs import brief_digest
from .decisions import ActorDecision
from .intent import CreativeIntent
from .models import ConceptRecord, require_digest
from .store import CreativeStore, _put
from .v2 import (
    A03CreativeJob, A03CreativeStageResult, ReasoningRequest, ReasoningResult,
    _output_pin, _stage_concepts, inference_decision, inference_failure,
)


class CreativeSidecarStore:
    """Uses a separate namespace, preserving all historical V1 bytes/paths.

    Event/model metadata is caller or adapter evidence, not a signed attestation.
    Exclusive creation has the same partial-write fail-closed limits as V1.
    """

    def __init__(self, store: CreativeStore | str | Path) -> None:
        self.creative = store if isinstance(store, CreativeStore) else CreativeStore(store)
        self.root = self.creative.root / "sidecars" / "a03-v11"

    def _path(self, kind: str, digest: str) -> Path:
        require_digest(digest)
        return self.root / kind / f"{digest}.json"

    def _save(self, kind: str, digest: str, data: dict[str, Any]) -> str:
        _put(self._path(kind, digest), canonical_bytes(data))
        return digest

    def _read(self, kind: str, digest: str) -> Any:
        raw = self._path(kind, digest).read_bytes()
        if sha256_bytes(raw) != digest:
            raise CreativeValidationError("stored sidecar digest mismatch")
        return read_json(raw.decode("utf-8"))

    @staticmethod
    def _check_bytes(path: Path, expected: dict[str, Any]) -> None:
        if path.read_bytes() != canonical_bytes(expected):
            raise CreativeValidationError("stored artifact bytes mismatch")

    def _validate_intent(self, intent: CreativeIntent) -> None:
        brief = self.creative.load_brief(intent.source_brief_digest)
        if brief["brief_id"] != intent.brief_id:
            raise CreativeValidationError("intent does not match source brief")
        if intent.parent_intent_digest is not None:
            parent = self.load_intent(intent.parent_intent_digest)
            if parent.brief_id != intent.brief_id:
                raise CreativeValidationError("intent revision cannot rebind brief identity")

    def save_intent(self, intent: CreativeIntent) -> str:
        if not isinstance(intent, CreativeIntent):
            raise CreativeValidationError("expected CreativeIntent")
        valid = CreativeIntent.from_dict(intent.to_dict())
        self._validate_intent(valid)
        return self._save("intents", valid.digest, valid.to_dict())

    def load_intent(self, digest: str) -> CreativeIntent:
        record = CreativeIntent.from_dict(self._read("intents", digest))
        if record.digest != digest:
            raise CreativeValidationError("stored intent digest mismatch")
        self._validate_intent(record)
        return record

    def save_decision(self, decision: ActorDecision) -> str:
        if not isinstance(decision, ActorDecision):
            raise CreativeValidationError("expected ActorDecision")
        valid = ActorDecision.from_dict(decision.to_dict())
        self.creative.load_selection(valid.selection_id)
        if valid.kind != "HUMAN_SELECTED":
            self.verify_inference_decision(valid)
        self._save("decisions", valid.decision_id[9:], valid.to_dict())
        return valid.decision_id

    def load_decision(self, decision_id: str) -> ActorDecision:
        if not isinstance(decision_id, str) or not decision_id.startswith("decision:"):
            raise CreativeValidationError("invalid decision_id")
        record = ActorDecision.from_dict(self._read("decisions", decision_id[9:]))
        if record.decision_id != decision_id:
            raise CreativeValidationError("stored actor decision digest mismatch")
        self.creative.load_selection(record.selection_id)
        # V1 records remain readable history, but are never usable inference
        # evidence through verify_inference_decision or the stage owner.
        if record.schema_version == "a03.actor-decision.v2" and record.kind != "HUMAN_SELECTED":
            self.verify_inference_decision(record)
        return record

    def verify_inference_decision(self, decision: ActorDecision) -> ReasoningResult:
        """Resolve actual saved inference, not an arbitrary evidence label.

        Mock receipts remain explicitly TEST_ONLY_MOCK in the binding. This is
        content verification within trusted storage, not AI authentication or
        proof that a provider executed outside the injected adapter boundary.
        """
        valid = ActorDecision.from_dict(decision.to_dict())
        if valid.schema_version != "a03.actor-decision.v2" or valid.inference is None:
            raise CreativeValidationError("AI decision requires saved inference binding")
        binding = valid.inference
        result = self.load_result(binding.receipt_digest)
        if inference_failure(result) is not None:
            raise CreativeValidationError("decision receipt is not bound successful inference")
        selection = self.creative.load_selection(valid.selection_id)
        self._check_bytes(self.creative._selection_path(valid.selection_id), selection.to_dict())
        concept = self.creative.load_concept(selection.concept_id, selection.revision)
        self._check_bytes(self.creative._concept_path(concept.concept_id, concept.revision), concept.to_dict())
        if (binding.request_digest != result.request.digest
                or binding.source_brief_digest != brief_digest(result.request.brief)
                or binding.source_brief_digest != selection.source_brief_digest
                or binding.concept_digest != selection.concept_digest
                or binding.mode != result.observation.mode
                or selection not in tuple(_output_pin(c) for c in _stage_concepts(result))):
            raise CreativeValidationError("decision inference/source/selected concept mismatch")
        return result

    def decide_from_result(self, selection_id: str, receipt_digest: str, *,
                           kind: str = "AUTO_PINNED", actor_ref: str = "a03:run_creative_stage") -> ActorDecision:
        """Bind a system pin or AI proposal to actual saved output; Human is optional."""
        if kind not in ("AUTO_PINNED", "AI_RECOMMENDED"):
            raise CreativeValidationError("inference decision must be AUTO_PINNED or AI_RECOMMENDED")
        selection = self.creative.load_selection(selection_id)
        result = self.load_result(receipt_digest)
        decision = inference_decision(result, selection, kind=kind, actor_ref=actor_ref)
        self.save_decision(decision)
        return decision

    def _validate_result(self, result: ReasoningResult) -> None:
        self.validate_request(result.request)

    def validate_request(self, request: ReasoningRequest) -> None:
        request = ReasoningRequest.from_dict(request.to_dict())
        # The canonical request carries its complete source snapshot; compare
        # against storage rather than accepting a matching brief_id alone.
        brief = self.creative.load_brief(brief_digest(request.brief))
        if brief != request.brief:
            raise CreativeValidationError("stored reasoning brief mismatch")
        self._check_bytes(self.creative.root / "briefs" / f"{brief_digest(brief)}.json", brief)
        if request.intent is not None:
            self._validate_intent(request.intent)
        if request.source is not None:
            saved = self.creative.load_concept(request.source.concept_id, request.source.revision)
            if saved != request.source:
                raise CreativeValidationError("proposal source revision mismatch")
            self._check_bytes(self.creative._concept_path(saved.concept_id, saved.revision), saved.to_dict())

    def save_result(self, result: ReasoningResult) -> str:
        if not isinstance(result, ReasoningResult):
            raise CreativeValidationError("expected ReasoningResult")
        valid = ReasoningResult.from_dict(result.to_dict())
        self._validate_result(valid)
        return self._save("reasoning", valid.digest, valid.to_dict())

    def load_result(self, digest: str) -> ReasoningResult:
        result = ReasoningResult.from_dict(self._read("reasoning", digest))
        if result.digest != digest:
            raise CreativeValidationError("stored reasoning result digest mismatch")
        self._validate_result(result)
        return result

    def claim_job(self, job: A03CreativeJob) -> bool:
        """Immutable attempt binding, not a scheduler, journal or retry lease.

        Exclusive creation reserves one invocation. Partial/uncertain writes
        fail closed under the same trusted-directory limits as CreativeStore.
        """
        valid = A03CreativeJob.from_dict(job.to_dict())
        self.validate_request(valid.request)
        path = self._path("jobs", valid.slot)
        content = canonical_bytes(valid.to_dict())
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as target:
                target.write(content)
        except FileExistsError:
            if path.read_bytes() != content:
                raise VersionConflict("VERSION_CONFLICT: creative job attempt already bound") from None
            return False
        return True

    def _validate_stage(self, stage: A03CreativeStageResult) -> None:
        self._check_bytes(self._path("jobs", stage.job.slot), stage.job.to_dict())
        if self.load_result(stage.reasoning.digest) != stage.reasoning:
            raise CreativeValidationError("stage reasoning receipt mismatch")
        for output in stage.outputs:
            saved = self.creative.load_selection(output.selection_id)
            if saved != output:
                raise CreativeValidationError("stage output pin mismatch")
            self._check_bytes(self.creative._selection_path(output.selection_id), output.to_dict())
            concept = self.creative.load_concept(output.concept_id, output.revision)
            self._check_bytes(self.creative._concept_path(output.concept_id, output.revision), concept.to_dict())
        if stage.decision is not None and self.load_decision(stage.decision.decision_id) != stage.decision:
            raise CreativeValidationError("stage decision receipt mismatch")

    def save_stage(self, stage: A03CreativeStageResult) -> str:
        valid = A03CreativeStageResult.from_dict(stage.to_dict())
        self._validate_stage(valid)
        # Content-addressed receipt plus a single immutable attempt reference.
        # A crash before the reference is committed never permits re-invocation.
        self._save("stages", valid.digest, valid.to_dict())
        self._save("stage-refs", valid.job.slot, {"stage_digest": valid.digest})
        return valid.digest

    def load_stage(self, job: A03CreativeJob) -> A03CreativeStageResult:
        valid = A03CreativeJob.from_dict(job.to_dict())
        self._check_bytes(self._path("jobs", valid.slot), valid.to_dict())
        path = self._path("stage-refs", valid.slot)
        if not path.exists():
            raise CreativeValidationError("UNRESOLVED_CREATIVE_JOB: reserved attempt has no terminal receipt")
        ref = exact_fields(read_json(path.read_text(encoding="utf-8")), {"stage_digest"})
        self._check_bytes(path, ref)
        stage = A03CreativeStageResult.from_dict(self._read("stages", ref["stage_digest"]))
        if stage.job != valid:
            raise CreativeValidationError("stage receipt job mismatch")
        self._validate_stage(stage)
        return stage

    def save_revision(self, result: ReasoningResult) -> ConceptRecord:
        """Deliberately commit proposed edits; conflicts never overwrite a child.

        Store the observed proposal first. A later revision conflict can leave
        that valid observation on disk, without asserting the edit was applied.
        DEVELOP maps to V1 REVISE; this result retains the original V2 operation.
        """
        if not isinstance(result, ReasoningResult):
            raise CreativeValidationError("expected ReasoningResult")
        valid = ReasoningResult.from_dict(result.to_dict())
        proposal = valid.proposed_revision()
        self.save_result(valid)
        return self.creative.save_concept(proposal)
