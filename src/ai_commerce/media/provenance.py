"""Evidence-bound provenance sidecars; labels and filenames are not evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .generation import GenerationEvent, GenerationJobRequest, _digest, _text


@dataclass(frozen=True)
class MediaProvenance:
    classification: str = "UNKNOWN"
    original_generation: str = "UNKNOWN"
    generation_performed: bool = False
    evidence_refs: tuple[str, ...] = ()
    output_sha256: str | None = None

    def __post_init__(self) -> None:
        allowed = {"UNKNOWN", "LOCAL_IMPORT", "SYNTHETIC_FIXTURE", "MODEL_GENERATED", "DERIVED_RENDER"}
        if self.classification not in allowed or type(self.generation_performed) is not bool:
            raise ValueError("Invalid provenance classification")
        if self.output_sha256 is not None:
            _digest(self.output_sha256, "provenance output")
        if not isinstance(self.evidence_refs, tuple):
            raise ValueError("Provenance evidence must be immutable references")
        for ref in self.evidence_refs:
            _text(ref, "observed evidence reference")
        expected = {
            "UNKNOWN": ("UNKNOWN", False), "LOCAL_IMPORT": ("UNKNOWN", False),
            "SYNTHETIC_FIXTURE": ("SYNTHETIC_FIXTURE", False),
            "MODEL_GENERATED": ("MODEL_GENERATED", True), "DERIVED_RENDER": ("UNKNOWN", False),
        }
        if (self.original_generation, self.generation_performed) != expected[self.classification]:
            raise ValueError("Provenance label cannot promote unknown generation")
        if self.classification != "UNKNOWN" and (not self.evidence_refs or self.output_sha256 is None):
            raise ValueError("Provenance requires observed evidence and a bound output hash")

    def to_dict(self) -> dict:
        return {**asdict(self), "evidence_refs": list(self.evidence_refs)}

    @classmethod
    def custody(cls, artifact: dict, *, observed_sha256: str) -> MediaProvenance:
        _digest(observed_sha256, "observed output hash")
        if artifact["sha256"] != observed_sha256:
            raise ValueError("Custody evidence does not match output")
        refs = (artifact["path"],)
        if artifact.get("origin") == "LOCAL_IMPORT" and artifact.get("mode") == "import":
            return cls("LOCAL_IMPORT", "UNKNOWN", False, refs, observed_sha256)
        if (artifact.get("origin") == "MOCK" and artifact.get("mode") == "mock"
                and artifact.get("source_generation") == "SYNTHETIC_FIXTURE"):
            return cls("SYNTHETIC_FIXTURE", "SYNTHETIC_FIXTURE", False, refs, observed_sha256)
        return cls(output_sha256=observed_sha256)

    @classmethod
    def generation(cls, request: GenerationJobRequest, event: GenerationEvent, *,
                   observed_sha256: str, synthetic_fixture: bool) -> MediaProvenance:
        _digest(observed_sha256, "observed output hash")
        if (event.request_digest != request.digest or event.state != "SUCCEEDED"
                or event.side_effects != "CONFIRMED" or event.output_sha256 != observed_sha256):
            raise ValueError("Generation provenance requires a matching observed completion and output hash")
        refs = (event.external_request_id, event.event_id)
        if synthetic_fixture and event.output_kind == "SYNTHETIC_FIXTURE":
            return cls("SYNTHETIC_FIXTURE", "SYNTHETIC_FIXTURE", False, refs, observed_sha256)
        if not synthetic_fixture and event.output_kind == "MODEL_GENERATED":
            return cls("MODEL_GENERATED", "MODEL_GENERATED", True, refs, observed_sha256)
        # A success or a user-supplied generation label alone does not prove origin.
        return cls(output_sha256=observed_sha256)

    @classmethod
    def derived_render(cls, *, observed_sha256: str, receipt_output_sha256: str,
                       input_sha256s: tuple[str, ...], renderer_ref: str,
                       receipt_ref: str) -> MediaProvenance:
        """Contract seam for a future observed render receipt, not a render action."""
        _digest(observed_sha256, "observed output")
        if receipt_output_sha256 != observed_sha256 or not input_sha256s:
            raise ValueError("Render receipt must bind observed output and source inputs")
        for digest in input_sha256s:
            _digest(digest, "render input")
        _text(renderer_ref, "observed renderer identity")
        _text(receipt_ref, "render receipt reference")
        return cls("DERIVED_RENDER", "UNKNOWN", False,
                   (receipt_ref, renderer_ref, *input_sha256s), observed_sha256)
