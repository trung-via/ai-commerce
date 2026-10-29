"""Separate immutable V1.1 storage; existing CreativeStore never auto-upgrades."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_commerce.core.provenance import canonical_bytes

from ._validation import CreativeValidationError, read_json
from .briefs import brief_digest
from .decisions import ActorDecision
from .intent import CreativeIntent
from .models import ConceptRecord, require_digest
from .store import CreativeStore, _put
from .v2 import ReasoningResult


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
        record = CreativeIntent.from_dict(read_json(self._path("intents", digest).read_text(encoding="utf-8")))
        if record.digest != digest:
            raise CreativeValidationError("stored intent digest mismatch")
        self._validate_intent(record)
        return record

    def save_decision(self, decision: ActorDecision) -> str:
        if not isinstance(decision, ActorDecision):
            raise CreativeValidationError("expected ActorDecision")
        valid = ActorDecision.from_dict(decision.to_dict())
        self.creative.load_selection(valid.selection_id)
        self._save("decisions", valid.decision_id[9:], valid.to_dict())
        return valid.decision_id

    def load_decision(self, decision_id: str) -> ActorDecision:
        if not isinstance(decision_id, str) or not decision_id.startswith("decision:"):
            raise CreativeValidationError("invalid decision_id")
        record = ActorDecision.from_dict(read_json(
            self._path("decisions", decision_id[9:]).read_text(encoding="utf-8")
        ))
        if record.decision_id != decision_id:
            raise CreativeValidationError("stored actor decision digest mismatch")
        self.creative.load_selection(record.selection_id)
        return record

    def _validate_result(self, result: ReasoningResult) -> None:
        request = result.request
        # The canonical request carries its complete source snapshot; compare
        # against storage rather than accepting a matching brief_id alone.
        brief = self.creative.load_brief(brief_digest(request.brief))
        if brief != request.brief:
            raise CreativeValidationError("stored reasoning brief mismatch")
        if request.intent is not None:
            self._validate_intent(request.intent)
        if request.source is not None:
            saved = self.creative.load_concept(request.source.concept_id, request.source.revision)
            if saved != request.source:
                raise CreativeValidationError("proposal source revision mismatch")

    def save_result(self, result: ReasoningResult) -> str:
        if not isinstance(result, ReasoningResult):
            raise CreativeValidationError("expected ReasoningResult")
        valid = ReasoningResult.from_dict(result.to_dict())
        self._validate_result(valid)
        return self._save("reasoning", valid.digest, valid.to_dict())

    def load_result(self, digest: str) -> ReasoningResult:
        result = ReasoningResult.from_dict(read_json(self._path("reasoning", digest).read_text(encoding="utf-8")))
        if result.digest != digest:
            raise CreativeValidationError("stored reasoning result digest mismatch")
        self._validate_result(result)
        return result

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
