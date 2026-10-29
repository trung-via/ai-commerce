"""Configurable structured-output client seam. No SDK, HTTP, env or credentials.

A separately supplied client owns transport. Every invocation checks its actual
capabilities against explicit authorization. Constructing/configuring this
adapter performs no call; tests inject an explicitly labeled fake client.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Literal, Protocol

from ai_commerce.providers.base import ProviderCapabilities

from ._validation import CreativeValidationError, exact_fields, json_object, read_json, require_text
from .models import EDITABLE_FIELDS, ConceptChange, ConceptDraft, ProviderObservation
from .v2 import ModelConfiguration, ReasoningRequest, ReasoningResult, UsageObservation


@dataclass(frozen=True, slots=True)
class InvocationAuthorization:
    allow_network: bool = False
    allow_spend: bool = False
    authorization_ref: str | None = None

    def __post_init__(self) -> None:
        if type(self.allow_network) is not bool or type(self.allow_spend) is not bool:
            raise CreativeValidationError("authorization flags must be booleans")
        if self.authorization_ref is not None:
            require_text(self.authorization_ref, "authorization_ref")
        if (self.allow_network or self.allow_spend) and self.authorization_ref is None:
            raise CreativeValidationError("live authorization needs an explicit reference")


def structured_output_schema() -> dict[str, Any]:
    """Fresh provider-neutral JSON Schema; semantic checks remain local."""
    text = {"type": "string", "minLength": 1}
    texts = {"type": "array", "items": {"type": "string", "minLength": 1}}
    draft = {
        "type": "object", "additionalProperties": False,
        "required": ["family", "title", "premise", "visual_direction", "source_refs", "assumptions"],
        "properties": {**{n: dict(text) for n in EDITABLE_FIELDS},
                       "source_refs": dict(texts), "assumptions": dict(texts)},
    }
    return {
        "type": "object", "additionalProperties": False,
        "required": ["drafts", "edits", "critique"],
        "properties": {
            "drafts": {"type": "array", "items": draft},
            "edits": {"type": "object", "additionalProperties": False,
                      "properties": {n: dict(text) for n in EDITABLE_FIELDS}},
            "critique": {"type": ["string", "null"]},
        },
    }


@dataclass(frozen=True, slots=True)
class StructuredModelRequest:
    config: ModelConfiguration
    creative: ReasoningRequest

    def __post_init__(self) -> None:
        if not isinstance(self.config, ModelConfiguration) or not isinstance(self.creative, ReasoningRequest):
            raise CreativeValidationError("expected typed structured model request")
        ModelConfiguration(**self.config.to_dict())
        ReasoningRequest.from_dict(self.creative.to_dict())

    @property
    def output_schema(self) -> dict[str, Any]:
        return structured_output_schema()

    def to_dict(self) -> dict[str, Any]:
        return {"config": self.config.to_dict(), "creative": self.creative.to_dict(),
                "output_schema": self.output_schema}


@dataclass(frozen=True, slots=True)
class StructuredModelResponse:
    state: Literal["OK", "REFUSED", "ERROR"]
    output_json: str | None = None
    observation: ProviderObservation = ProviderObservation()
    usage: UsageObservation = UsageObservation()
    request_id: str | None = None
    error_code: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if self.state not in ("OK", "REFUSED", "ERROR"):
            raise CreativeValidationError("invalid model response state")
        if not isinstance(self.observation, ProviderObservation) or not isinstance(self.usage, UsageObservation):
            raise CreativeValidationError("expected typed model metadata")
        ProviderObservation.from_dict(self.observation.to_dict())
        UsageObservation.from_dict(self.usage.to_dict())
        for name in ("request_id", "error_code", "message"):
            if getattr(self, name) is not None:
                require_text(getattr(self, name), name)
        if self.state == "OK":
            require_text(self.output_json, "output_json")
            if self.error_code is not None or self.message is not None:
                raise CreativeValidationError("successful model response cannot assert error")
        elif self.output_json is not None:
            raise CreativeValidationError("refusal/error cannot contain successful output")

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state, "output_json": self.output_json,
            "observation": self.observation.to_dict(), "usage": self.usage.to_dict(),
            "request_id": self.request_id, "error_code": self.error_code, "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: object) -> StructuredModelResponse:
        value = exact_fields(data, {f.name for f in fields(cls)})
        value["observation"] = ProviderObservation.from_dict(value["observation"])
        value["usage"] = UsageObservation.from_dict(value["usage"])
        return cls(**value)


class StructuredCreativeClient(Protocol):
    """SDK integration boundary; return reported identity, usage and request ID."""

    @property
    def capabilities(self) -> ProviderCapabilities: ...

    @property
    def execution_mode(self) -> Literal["UNKNOWN", "MODEL", "TEST_ONLY_MOCK"]: ...

    def complete(self, request: StructuredModelRequest) -> StructuredModelResponse: ...


class LiveCreativeReasoner:
    """V2-only adapter. No default client, implicit authorization or retry."""

    def __init__(
        self, config: ModelConfiguration | None = None, client: StructuredCreativeClient | None = None,
        *, authorization: InvocationAuthorization = InvocationAuthorization(),
    ) -> None:
        if config is not None and not isinstance(config, ModelConfiguration):
            raise CreativeValidationError("expected ModelConfiguration")
        if not isinstance(authorization, InvocationAuthorization):
            raise CreativeValidationError("expected InvocationAuthorization")
        # Revalidate even objects whose construction was bypassed by a caller.
        if config is not None:
            ModelConfiguration(**config.to_dict())
        InvocationAuthorization(authorization.allow_network, authorization.allow_spend, authorization.authorization_ref)
        self.config, self.client, self.authorization = config, client, authorization

    @property
    def capabilities(self) -> ProviderCapabilities:
        # V1 must continue rejecting this live seam, even with an offline fake.
        return ProviderCapabilities("v2-live-adapter", ("concept",), True, True)

    @property
    def observation(self) -> ProviderObservation:
        return ProviderObservation()

    def reason(self, request: ReasoningRequest) -> ReasoningResult:
        request = ReasoningRequest.from_dict(request.to_dict())
        # Pin a single invocation. Reconfiguration or client callbacks cannot
        # switch transport/authorization or relabel the target after the call.
        config, client, authorization = self.config, self.client, self.authorization
        try:
            if config is not None:
                if not isinstance(config, ModelConfiguration):
                    raise CreativeValidationError("expected ModelConfiguration")
                config = ModelConfiguration(**config.to_dict())
            if not isinstance(authorization, InvocationAuthorization):
                raise CreativeValidationError("expected InvocationAuthorization")
            authorization = InvocationAuthorization(
                authorization.allow_network, authorization.allow_spend, authorization.authorization_ref,
            )
        except (CreativeValidationError, TypeError, ValueError):
            return ReasoningResult(request, "ERROR", error_code="INVALID_CONFIGURATION",
                                   message="Invalid model configuration or authorization")

        def outcome(state: str, *output: Any, **metadata: Any) -> ReasoningResult:
            return ReasoningResult(request, state, *output, configuration=config, **metadata)

        if config is None or client is None or "UNKNOWN" in (config.provider, config.model):
            return outcome("NO_MODEL_CONFIGURED", error_code="NO_MODEL_CONFIGURED",
                                   message="Supply an explicit model configuration and structured client")
        try:
            caps = client.capabilities
            mode = client.execution_mode
            if not isinstance(caps, ProviderCapabilities):
                raise CreativeValidationError("invalid client capabilities")
            require_text(caps.name, "client name")
            if (type(caps.network_required) is not bool or type(caps.external_spend_possible) is not bool
                    or not isinstance(caps.output_kinds, tuple) or "concept" not in caps.output_kinds
                    or any(not isinstance(k, str) or not k.strip() for k in caps.output_kinds)
                    or mode not in ("UNKNOWN", "MODEL", "TEST_ONLY_MOCK")):
                raise CreativeValidationError("invalid client capability or execution mode")
        except Exception:
            return outcome("ERROR", error_code="INVALID_CLIENT",
                                   message="Client must declare valid capabilities and execution mode")
        if ((caps.network_required and not authorization.allow_network)
                or (caps.external_spend_possible and not authorization.allow_spend)):
            return outcome("UNAUTHORIZED", error_code="UNAUTHORIZED",
                                   message="Client network/spend capabilities require explicit authorization")
        try:
            response = client.complete(StructuredModelRequest(config, request))
        except Exception:
            return outcome("ERROR", error_code="MODEL_ERROR",
                                   message="Model client failed; outcome/usage UNKNOWN, no retry performed")
        try:
            if not isinstance(response, StructuredModelResponse):
                raise CreativeValidationError("expected typed structured model response")
            response = StructuredModelResponse.from_dict(response.to_dict())
            if response.observation.mode != mode:
                raise CreativeValidationError("response disagrees with declared client execution mode")
            if mode == "TEST_ONLY_MOCK" and response.observation.model != "UNKNOWN":
                raise CreativeValidationError("fake client cannot assert real-model inference")
        except (CreativeValidationError, TypeError, ValueError):
            return outcome("ERROR", error_code="INVALID_MODEL_RESPONSE",
                                   message="Invalid model response or provenance")
        metadata = dict(observation=response.observation, usage=response.usage,
                        provider_request_id=response.request_id)
        if response.state != "OK":
            return outcome(
                response.state, **metadata,
                error_code=response.error_code or ("MODEL_REFUSED" if response.state == "REFUSED" else "MODEL_ERROR"),
                message=response.message or "Model returned no creative output",
            )
        try:
            output = exact_fields(read_json(response.output_json), {"drafts", "edits", "critique"})
            if not isinstance(output["drafts"], list):
                raise CreativeValidationError("drafts must be an array")
            drafts = tuple(ConceptDraft.from_dict(exact_fields(
                d, set(EDITABLE_FIELDS) | {"source_refs", "assumptions"},
            )) for d in output["drafts"])
            edits = json_object(output["edits"])
            if edits.keys() - EDITABLE_FIELDS or (edits and request.source is None):
                raise CreativeValidationError("unsupported creative edits")
            changes = tuple(ConceptChange(n, getattr(request.source, n), v) for n, v in sorted(edits.items()))
            return outcome("OK", drafts, changes, output["critique"], **metadata)
        except (CreativeValidationError, TypeError, ValueError):
            return outcome("ERROR", **metadata, error_code="INVALID_STRUCTURED_OUTPUT",
                                   message="Model output failed strict creative contract validation")
