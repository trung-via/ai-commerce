"""Provider-neutral product ideation seam, with an explicit offline test fixture."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from ai_commerce.providers.base import ProviderCapabilities, ProviderError

from ._validation import CreativeValidationError, NoReasonerConfigured, json_object, positive_int, require_text
from .briefs import brief_digest, intake_brief
from .models import (
    EDITABLE_FIELDS, ConceptChange, ConceptDraft, ConceptRecord, ParentRevision,
    ProviderObservation, concept_identity,
)


class CreativeReasoner(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    @property
    def observation(self) -> ProviderObservation: ...

    def generate(self, brief: dict[str, Any], *, count: int = 1) -> Sequence[ConceptDraft]: ...


class MockCreativeReasoner:
    """TEST-ONLY deterministic templates, not a language model or quality claim.

    Four distinct mechanisms are fixture coverage, not a product concept quota.
    Other adapters may support any positive requested count.
    """

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("deterministic-test-only", ("concept",), False, False)

    @property
    def observation(self) -> ProviderObservation:
        return ProviderObservation("deterministic-test-only", "UNKNOWN", "TEST_ONLY_MOCK")

    def generate(self, brief: dict[str, Any], *, count: int = 1) -> tuple[ConceptDraft, ...]:
        positive_int(count, "count")
        if count > 4:
            raise ProviderError("MOCK_CAPACITY", "TEST-ONLY fixture has four idea families")
        brief = intake_brief(brief)
        context = brief.get("prompt") or brief["objective"]
        subject = f"Mục tiêu: «{brief['objective']}». Chất liệu: «{context}»."
        refs = tuple(brief.get("source_refs", []))
        assumptions = (
            "TEST-ONLY deterministic template; no model inference.",
            "Creative hypothesis only; source assertions and outcomes remain unverified.",
        )
        directions = (
            (
                "sensory_poem", "Bản đồ của một cảm giác",
                "Biến yêu cầu thành một bài thơ thị giác không lời: mỗi cảm giác là một chuyển động, không cần cốt truyện.",
                "Cận cảnh chất liệu, ánh sáng đổi nhịp và khoảng trống; âm thanh môi trường dẫn cảm xúc.",
            ),
            (
                "object_perspective_comedy", "Đồ vật kể chuyện",
                "Một đồ vật tưởng tượng hiểu sai thói quen con người và tự thuật bằng logic ngược đời; tiếng cười đến từ điểm nhìn.",
                "Góc máy ngang tầm đồ vật, biểu cảm bằng stop motion; đối chiếu điều nó nghĩ với điều người xem thấy.",
            ),
            (
                "reverse_time_mystery", "Bắt đầu từ dấu vết cuối",
                "Mở bằng một kết quả khó hiểu rồi lần ngược các dấu vết; mỗi lần đảo thời gian đổi cách hiểu nguyên nhân.",
                "Chuyển động chạy ngược, bố cục lặp với một chi tiết đổi khác; dấu vết màu sắc nối các lớp thời gian.",
            ),
            (
                "impossible_field_guide", "Cẩm nang về một thế giới không có thật",
                "Dựng một bộ sưu tập loài vật và quy luật hoàn toàn hư cấu liên quan tới chủ đề; người xem khám phá thay vì theo nhân vật.",
                "Minh họa cắt lớp, nhãn phân loại phi lý và bản đồ tưởng tượng; bố cục như trang sổ tay khảo sát.",
            ),
        )
        return tuple(
            ConceptDraft(family, title, subject + " " + premise, visual, refs, assumptions)
            for family, title, premise, visual in directions[:count]
        )


def generate_concepts(
    brief: str | Mapping[str, Any], reasoner: CreativeReasoner | None = None, *, count: int = 1,
) -> tuple[ConceptRecord, ...]:
    """Generate validated proposals; never silently fall back to the mock.

    This A03 runner permits only offline, non-spending adapters. A future adapter
    translates its own model response into ConceptDraft and reports observed
    metadata here; enabling network/spend is outside this implementation.
    """
    valid = intake_brief(brief)
    positive_int(count, "count")
    if reasoner is None:
        raise NoReasonerConfigured("NO_MODEL_CONFIGURED: explicitly supply a CreativeReasoner")
    try:
        caps = reasoner.capabilities
        observation = reasoner.observation
        if not isinstance(caps, ProviderCapabilities) or not isinstance(observation, ProviderObservation):
            raise CreativeValidationError("invalid reasoner metadata")
        observation = ProviderObservation.from_dict(observation.to_dict())
        require_text(caps.name, "capabilities.name")
        if not isinstance(caps.output_kinds, tuple) or any(
            not isinstance(kind, str) or not kind.strip() for kind in caps.output_kinds
        ):
            raise CreativeValidationError("invalid reasoner output_kinds")
        if isinstance(reasoner, MockCreativeReasoner) and observation != MockCreativeReasoner().observation:
            raise CreativeValidationError("mock provenance must remain TEST_ONLY_MOCK")
        if caps.network_required is not False or caps.external_spend_possible is not False:
            raise ProviderError("OFFLINE_ONLY", "A03 forbids network or paid creative providers")
        if "concept" not in caps.output_kinds:
            raise CreativeValidationError("reasoner does not support concepts")
        # A provider receives a detached copy and cannot mutate source identity.
        drafts = reasoner.generate(intake_brief(valid), count=count)
        if not isinstance(drafts, (list, tuple)) or len(drafts) != count:
            raise CreativeValidationError("provider must return exactly the requested concepts")
        if any(not isinstance(draft, ConceptDraft) for draft in drafts):
            raise CreativeValidationError("provider must return typed ConceptDraft records")
        families: set[str] = set()
        mechanisms: set[tuple[str, str]] = set()
        known_refs = set(valid.get("source_refs", []))
        for draft in drafts:
            # Revalidate even records from an adapter that bypassed its constructor.
            ConceptDraft.from_dict(draft.to_dict())
            if not set(draft.source_refs) <= known_refs:
                raise CreativeValidationError("provider source_refs must come from the input brief")
            family = unicodedata.normalize("NFKC", draft.family).strip().casefold()
            mechanism = tuple(unicodedata.normalize("NFKC", text).strip().casefold()
                              for text in (draft.premise, draft.visual_direction))
            if family in families or mechanism in mechanisms:
                raise CreativeValidationError("duplicate idea family or unchanged creative mechanism")
            families.add(family)
            mechanisms.add(mechanism)
        digest = brief_digest(valid)
        return tuple(
            ConceptRecord(
                brief_id=valid["brief_id"], source_brief_digest=digest,
                concept_id=concept_identity(valid["brief_id"], digest, draft, observation),
                revision=1, family=draft.family, title=draft.title, premise=draft.premise,
                visual_direction=draft.visual_direction, source_refs=draft.source_refs,
                assumptions=draft.assumptions, observation=observation,
            ) for draft in drafts
        )
    except ProviderError:
        raise
    except CreativeValidationError as exc:
        raise ProviderError("INVALID_CREATIVE_RESULT", str(exc)) from exc
    except Exception as exc:
        raise ProviderError("CREATIVE_PROVIDER_FAILED", "creative adapter failed") from exc


def _edit(
    concept: ConceptRecord, changes: Mapping[str, str], *, lens: str | None,
) -> ConceptRecord:
    if not isinstance(concept, ConceptRecord):
        raise CreativeValidationError("expected ConceptRecord")
    edits = json_object(changes)
    if not edits or edits.keys() - EDITABLE_FIELDS:
        raise CreativeValidationError("only nonempty creative text edits are supported")
    changed: list[ConceptChange] = []
    for field, after in sorted(edits.items()):
        require_text(after, field)
        before = getattr(concept, field)
        if before != after:
            changed.append(ConceptChange(field, before, after))
    if not changed:
        raise CreativeValidationError("revision must change creative content")
    return replace(
        concept, **edits, revision=concept.revision + 1,
        parent=ParentRevision(concept.concept_id, concept.revision, concept.digest),
        changes=tuple(changed), operation="REFRAME" if lens is not None else "REVISE", lens=lens,
    )


def revise_concept(concept: ConceptRecord, changes: Mapping[str, str]) -> ConceptRecord:
    """Return a child revision while preserving source, identity and mock labeling."""
    return _edit(concept, changes, lens=None)


def reframe_concept(
    concept: ConceptRecord, *, lens: str, changes: Mapping[str, str],
) -> ConceptRecord:
    """Apply explicit creative changes through a lens as an optional operation.

    This local operation does not invent a model response. The original record
    stays intact and the child records the exact parent, lens and text delta.
    """
    require_text(lens, "lens")
    return _edit(concept, changes, lens=lens)
