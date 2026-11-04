"""Pure deterministic PA2 reducer. It executes NOTHING and authenticates nobody.

The caller must obtain snapshots from a trusted journal and VerificationContext
from an independent private verifier. Constructing these dataclasses is NOT
verification. B0.1 intentionally implements neither that verifier nor SQLite.
No event-supplied flag or evidence label can replace this separate trust input.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
import re

from .contracts import (
    ContractError, EVIDENCE_TYPES, OWNERS, STATES, ValidatedContract,
    canonical_bytes, validate_contract,
)


class TransitionError(ValueError):
    pass


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise TransitionError(code)


def _text(value) -> bool:
    return type(value) is str and 0 < len(value) <= 512 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]*", value) is not None


def _sha(value, size=64) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value) is not None


State = Enum("State", {name: name for name in STATES}, type=str)


class EventKind(str, Enum):
    INPUT_ACCEPTED = "INPUT_ACCEPTED"
    START = "START"
    OUTPUT_READY = "OUTPUT_READY"
    QA_PASSED = "QA_PASSED"
    BRAIN_ACCEPTED = "BRAIN_ACCEPTED"
    RELEASE_REQUESTED = "RELEASE_REQUESTED"
    EFFECT_UNCERTAIN = "EFFECT_UNCERTAIN"
    PROVIDER_CONFIRMED = "PROVIDER_CONFIRMED"
    RECONCILED_NO_EFFECT = "RECONCILED_NO_EFFECT"
    ANALYTICS_ACCEPTED = "ANALYTICS_ACCEPTED"
    REVIEW_LEARNING = "REVIEW_LEARNING"
    LEARNING_RECORDED = "LEARNING_RECORDED"
    OFFLINE_FINISHED = "OFFLINE_FINISHED"
    HOLD = "HOLD"
    STOP = "STOP"


class AuthorityKind(str, Enum):
    BRAIN = "BRAIN"
    HUMAN_RELEASE = "HUMAN_RELEASE"
    PROVIDER_ENTITLEMENT = "PROVIDER_ENTITLEMENT"
    ASSET_RIGHTS = "ASSET_RIGHTS"
    PROVIDER_OBSERVATION = "PROVIDER_OBSERVATION"


@dataclass(frozen=True, slots=True)
class EvidencePin:
    owner: str
    ref: str
    sha256: str
    evidence_type: str = "UNKNOWN"
    attestation_ref: str | None = None

    def __post_init__(self):
        _need(self.owner in OWNERS and _text(self.ref) and _sha(self.sha256)
              and self.evidence_type in EVIDENCE_TYPES, "INVALID_EVIDENCE_PIN")
        _need((self.evidence_type == "PROVIDER_ATTESTED") == (self.attestation_ref is not None),
              "ATTESTATION_LABEL_MISMATCH")
        _need(self.attestation_ref is None or _text(self.attestation_ref), "INVALID_ATTESTATION_REF")


@dataclass(frozen=True, slots=True)
class AuthorityReceipt:
    """Trusted-verifier INPUT, never a wire event or a claim of implemented auth.

    Tests may construct hypothetical verified inputs. A production caller must
    independently authenticate issuer, exact bytes, scope, expiry and revocation.
    A public runner/coding agent must never supply this context to a private worker.
    """
    kind: AuthorityKind
    journey_id: str
    source_sha: str
    directive_sha256: str
    scope_sha256: str
    record_sha256: str
    actor_ref: str
    channel: str
    actions: tuple[str, ...]
    verifier_ref: str
    valid_from: int
    expires_at: int
    evidence_type: str = "UNKNOWN"
    verified: bool = False
    spend_ceiling_minor: int = 0
    currency: str | None = None
    effect_key: str | None = None
    effect_request_sha256: str | None = None
    effect_outcome: str = "UNKNOWN"

    def __post_init__(self):
        _need(type(self.kind) is AuthorityKind and _text(self.journey_id)
              and _sha(self.source_sha, 40) and all(_sha(v) for v in
              (self.directive_sha256, self.scope_sha256, self.record_sha256)), "INVALID_AUTHORITY_BINDING")
        _need(all(_text(v) for v in (self.actor_ref, self.channel, self.verifier_ref)), "INVALID_AUTHORITY_IDENTITY")
        _need(type(self.actions) is tuple and len(set(self.actions)) == len(self.actions)
              and all(v in {"APPROVE_DIRECTIVE", "GO_PUBLISH", "GO_SPEND", "PUBLISH", "ASSET_RIGHTS", "OBSERVE_EFFECT"}
                      for v in self.actions), "INVALID_AUTHORITY_ACTIONS")
        _need(type(self.valid_from) is int and type(self.expires_at) is int
              and 0 <= self.valid_from < self.expires_at <= 2**63 - 1, "INVALID_AUTHORITY_WINDOW")
        _need(type(self.verified) is bool and self.evidence_type in EVIDENCE_TYPES,
              "INVALID_VERIFICATION_INPUT")
        _need(type(self.spend_ceiling_minor) is int and 0 <= self.spend_ceiling_minor <= 2**63 - 1
              and (self.currency is None or (type(self.currency) is str
                   and re.fullmatch(r"[A-Z]{3}", self.currency) is not None))
              and (self.spend_ceiling_minor == 0 or self.currency is not None), "INVALID_SPEND_ENVELOPE")
        _need((self.effect_key is None) == (self.effect_request_sha256 is None)
              and (self.effect_key is None or (_text(self.effect_key) and _sha(self.effect_request_sha256)))
              and self.effect_outcome in {"UNKNOWN", "CONFIRMED", "NO_EFFECT"}, "INVALID_OBSERVATION_BINDING")


@dataclass(frozen=True, slots=True)
class VerificationContext:
    receipts: tuple[AuthorityReceipt, ...] = ()
    current_chat_ref: str | None = None

    def __post_init__(self):
        _need(type(self.receipts) is tuple and len(self.receipts) <= 128, "INVALID_VERIFICATION_CONTEXT")
        for receipt in self.receipts:
            _need(type(receipt) is AuthorityReceipt, "TYPED_AUTHORITY_REQUIRED")
            receipt.__post_init__()
        _need(self.current_chat_ref is None or _text(self.current_chat_ref), "INVALID_CHAT_BINDING")


@dataclass(frozen=True, slots=True)
class Journey:
    journey_id: str
    source_sha: str
    state: State = State.NEW
    directive: ValidatedContract | None = None
    brain_decision: ValidatedContract | None = None
    evidence: tuple[EvidencePin, ...] = ()
    seen_event_ids: tuple[str, ...] = ()
    effect_keys: tuple[str, ...] = ()
    effect_key: str | None = None
    effect_request_sha256: str | None = None
    effect_status: str = "NONE"

    def __post_init__(self):
        _need(_text(self.journey_id) and _sha(self.source_sha, 40)
              and type(self.state) is State, "INVALID_JOURNEY_STATE_OR_ID")
        for values in (self.seen_event_ids, self.effect_keys):
            _need(type(values) is tuple and len(values) <= 256 and all(_text(v) for v in values)
                  and len(set(values)) == len(values), "INVALID_JOURNEY_HISTORY")
        _need(type(self.evidence) is tuple and len(self.evidence) <= 128, "INVALID_EVIDENCE_HISTORY")
        for pin in self.evidence:
            _need(type(pin) is EvidencePin, "TYPED_EVIDENCE_REQUIRED")
            pin.__post_init__()
        for contract, name in ((self.directive, "CreativeDirective"), (self.brain_decision, "BrainDecision")):
            if contract is not None:
                _need(type(contract) is ValidatedContract and contract.name == name, "TYPED_CONTRACT_REQUIRED")
                value = validate_contract(name, contract.to_dict()).to_dict()
                _need(value["journey_id"] == self.journey_id and value["source_sha"] == self.source_sha,
                      "JOURNEY_CONTRACT_MISMATCH")
        _need(self.state in {State.NEW, State.HOLD, State.STOPPED} or self.directive is not None,
              "DIRECTIVE_REQUIRED")
        _need((self.effect_key is None) == (self.effect_request_sha256 is None), "INCOMPLETE_EFFECT_IDENTITY")
        _need(self.effect_status in {"NONE", "INTENT_RECORDED", "UNKNOWN", "CONFIRMED", "NO_EFFECT"}
              and (self.effect_status == "NONE") == (self.effect_key is None), "INVALID_EFFECT_STATUS")
        if self.effect_key is not None:
            _need(_text(self.effect_key) and self.effect_key in self.effect_keys
                  and _sha(self.effect_request_sha256), "EFFECT_HISTORY_REQUIRED")
        expected = {State.RELEASE_INTENT: "INTENT_RECORDED", State.UNKNOWN_EFFECT: "UNKNOWN",
                    State.RELEASE_CONFIRMED: "CONFIRMED"}
        _need(self.state not in expected or self.effect_status == expected[self.state], "STATE_EFFECT_MISMATCH")


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    kind: EventKind
    journey_id: str
    source_sha: str
    record: ValidatedContract | None = None
    evidence: tuple[EvidencePin, ...] = ()
    tool: str | None = None
    effect_key: str | None = None
    effect_request_sha256: str | None = None
    human_grant_sha256: str | None = None
    provider_entitlement_sha256: str | None = None
    spend_minor: int = 0
    currency: str | None = None

    def __post_init__(self):
        _need(type(self.kind) is EventKind and _text(self.event_id)
              and _text(self.journey_id) and _sha(self.source_sha, 40), "INVALID_EVENT")
        _need(type(self.evidence) is tuple and len(self.evidence) <= 64, "INVALID_EVENT_EVIDENCE")
        for pin in self.evidence:
            _need(type(pin) is EvidencePin, "TYPED_EVIDENCE_REQUIRED")
            pin.__post_init__()
        _need(self.record is None or type(self.record) is ValidatedContract, "TYPED_CONTRACT_REQUIRED")
        _need(all(v is None or _sha(v) for v in (self.effect_request_sha256,
              self.human_grant_sha256, self.provider_entitlement_sha256)), "INVALID_EVENT_DIGEST")
        _need(all(v is None or _text(v) for v in (self.tool, self.effect_key)), "INVALID_EVENT_REFERENCE")
        _need(type(self.spend_minor) is int and 0 <= self.spend_minor <= 2**63 - 1
              and (self.currency is None or (type(self.currency) is str
                   and re.fullmatch(r"[A-Z]{3}", self.currency) is not None)), "INVALID_EVENT_SPEND")
        present = {name for name in ("record", "evidence", "tool", "effect_key", "effect_request_sha256",
                   "human_grant_sha256", "provider_entitlement_sha256", "spend_minor", "currency")
                   if getattr(self, name) not in (None, (), 0)}
        allowed = {
            EventKind.INPUT_ACCEPTED: {"record"}, EventKind.START: {"tool"},
            EventKind.BRAIN_ACCEPTED: {"record"},
            EventKind.RELEASE_REQUESTED: {"tool", "effect_key", "human_grant_sha256",
                "provider_entitlement_sha256", "spend_minor", "currency"},
            EventKind.EFFECT_UNCERTAIN: {"effect_key", "effect_request_sha256"},
            EventKind.PROVIDER_CONFIRMED: {"effect_key", "effect_request_sha256", "evidence"},
            EventKind.RECONCILED_NO_EFFECT: {"effect_key", "effect_request_sha256", "evidence"},
            EventKind.OUTPUT_READY: {"evidence"}, EventKind.QA_PASSED: {"evidence"},
            EventKind.ANALYTICS_ACCEPTED: {"evidence"}, EventKind.REVIEW_LEARNING: {"evidence"},
            EventKind.LEARNING_RECORDED: {"evidence"},
        }.get(self.kind, set())
        _need(present <= allowed, "EVENT_PAYLOAD_NOT_ALLOWED")


def _authority(context, kind, record_sha256, journey, directive, at, action):
    matches = [r for r in context.receipts if r.kind == kind and r.record_sha256 == record_sha256]
    _need(len(matches) == 1, "MISSING_OR_AMBIGUOUS_AUTHORITY")
    receipt = matches[0]
    _need(receipt.verified and receipt.evidence_type not in {"UNKNOWN", "SYNTHETIC_FIXTURE"}, "UNVERIFIED_AUTHORITY")
    _need(receipt.journey_id == journey.journey_id and receipt.source_sha == journey.source_sha
          and receipt.directive_sha256 == directive["directive_sha256"]
          and receipt.scope_sha256 == sha256(canonical_bytes(directive["scope"])).hexdigest()
          and action in receipt.actions and receipt.valid_from <= at < receipt.expires_at,
          "AUTHORITY_SCOPE_OR_TIME_MISMATCH")
    return receipt


def _append_evidence(old, new):
    result = list(old)
    for pin in new:
        _need(not any((p.owner, p.ref) == (pin.owner, pin.ref) and p != pin for p in result)
              and not any(p.sha256 == pin.sha256 and p.evidence_type != pin.evidence_type for p in result),
              "EVIDENCE_PROVENANCE_CONFLICT")
        if pin not in result:
            result.append(pin)
    return tuple(result)


def transition(journey: Journey, event: Event, *, at: int,
               verification: VerificationContext = VerificationContext()) -> Journey:
    """Return a new snapshot or reject; no clock lookup, callback, I/O or effects.

    RELEASE_INTENT is only a modeled reservation. Only a future private worker
    can transactionally persist it before an authorized external call. No B0.1
    result is a GO_PUBLISH/GO_SPEND token, an authenticated receipt or a live run.
    """
    _need(type(journey) is Journey and type(event) is Event
          and type(verification) is VerificationContext, "TYPED_TRANSITION_INPUT_REQUIRED")
    journey.__post_init__()
    event.__post_init__()
    verification.__post_init__()
    _need(type(at) is int and 0 <= at <= 2**63 - 1, "EXPLICIT_TIME_REQUIRED")
    _need(event.journey_id == journey.journey_id and event.source_sha == journey.source_sha,
          "EVENT_SOURCE_OR_JOURNEY_MISMATCH")
    _need(event.event_id not in journey.seen_event_ids, "EVENT_REPLAY_DENIED")
    _need(journey.state not in {State.COMPLETE, State.HOLD, State.STOPPED}, "TERMINAL_STATE")
    changes = {"seen_event_ids": (*journey.seen_event_ids, event.event_id),
               "evidence": _append_evidence(journey.evidence, event.evidence)}
    if event.kind in {EventKind.HOLD, EventKind.STOP}:
        return replace(journey, **changes, state=State.HOLD if event.kind == EventKind.HOLD else State.STOPPED)
    edges = {
        (State.NEW, EventKind.INPUT_ACCEPTED): State.INPUT_VALIDATED,
        (State.INPUT_VALIDATED, EventKind.START): State.RUNNING,
        (State.RUNNING, EventKind.OUTPUT_READY): State.TECHNICAL_QA,
        (State.TECHNICAL_QA, EventKind.QA_PASSED): State.WAIT_BRAIN,
        (State.WAIT_BRAIN, EventKind.BRAIN_ACCEPTED): State.READY_FOR_HUMAN_RELEASE,
        (State.READY_FOR_HUMAN_RELEASE, EventKind.RELEASE_REQUESTED): State.RELEASE_INTENT,
        (State.RELEASE_INTENT, EventKind.EFFECT_UNCERTAIN): State.UNKNOWN_EFFECT,
        (State.RELEASE_INTENT, EventKind.PROVIDER_CONFIRMED): State.RELEASE_CONFIRMED,
        (State.UNKNOWN_EFFECT, EventKind.PROVIDER_CONFIRMED): State.RELEASE_CONFIRMED,
        (State.UNKNOWN_EFFECT, EventKind.RECONCILED_NO_EFFECT): State.HOLD,
        (State.RELEASE_CONFIRMED, EventKind.ANALYTICS_ACCEPTED): State.ANALYTICS_IMPORTED,
        (State.ANALYTICS_IMPORTED, EventKind.REVIEW_LEARNING): State.LEARNING_REVIEW,
        (State.READY_FOR_HUMAN_RELEASE, EventKind.OFFLINE_FINISHED): State.LEARNING_REVIEW,
        (State.LEARNING_REVIEW, EventKind.LEARNING_RECORDED): State.COMPLETE,
    }
    target = edges.get((journey.state, event.kind))
    _need(target is not None, "INVALID_TRANSITION")
    directive = journey.directive.to_dict() if journey.directive is not None else None
    if event.kind == EventKind.INPUT_ACCEPTED:
        _need(event.record is not None and event.record.name == "CreativeDirective", "DIRECTIVE_REQUIRED")
        changes["directive"] = validate_contract("CreativeDirective", event.record.to_dict())
    elif event.kind == EventKind.START:
        _need(event.tool is not None and event.tool in directive["allowed_tools"], "TOOL_NOT_ALLOWED")
    elif event.kind == EventKind.BRAIN_ACCEPTED:
        _need(event.record is not None and event.record.name == "BrainDecision", "BRAIN_DECISION_REQUIRED")
        decision = validate_contract("BrainDecision", event.record.to_dict()).to_dict()
        _need(decision["journey_id"] == journey.journey_id and decision["source_sha"] == journey.source_sha
              and decision["directive_version"] == directive["directive_version"]
              and decision["directive_sha256"] == directive["directive_sha256"]
              and decision["scope"] == directive["scope"], "BRAIN_DECISION_SCOPE_MISMATCH")
        _need(decision["decision"] == "APPROVE_DIRECTIVE" and decision["provenance"]["actor"] == "CHATGPT_BRAIN"
              and decision["provenance"]["evidence_type"] == "HUMAN_SUPPLIED"
              and decision["chat_ref"] == verification.current_chat_ref, "FALSE_BRAIN_APPROVAL")
        proof = _authority(verification, AuthorityKind.BRAIN, decision["decision_sha256"],
                           journey, directive, at, "APPROVE_DIRECTIVE")
        _need(proof.actor_ref == decision["provenance"]["actor_ref"]
              and proof.channel == "CURRENT_CHATGPT_CHAT", "BRAIN_IDENTITY_MISMATCH")
        changes["brain_decision"] = event.record
    elif event.kind == EventKind.RELEASE_REQUESTED:
        _need(journey.brain_decision is not None, "VERIFIED_BRAIN_DECISION_REQUIRED")
        # Recheck freshness/revocation supplied by the trusted verifier at release.
        brain = _authority(verification, AuthorityKind.BRAIN, journey.brain_decision.to_dict()["decision_sha256"],
                           journey, directive, at, "APPROVE_DIRECTIVE")
        decision = journey.brain_decision.to_dict()
        _need(brain.channel == "CURRENT_CHATGPT_CHAT" and brain.actor_ref == decision["provenance"]["actor_ref"]
              and verification.current_chat_ref == decision["chat_ref"], "BRAIN_IDENTITY_MISMATCH")
        scope = directive["scope"]
        _need(scope["action_class"] == "PUBLISH" and "PUBLISH" in directive["action_classes"]
              and all(scope[k] is not None for k in ("provider_ref", "account_ref", "surface", "asset_sha256")),
              "EXACT_PUBLISH_SCOPE_REQUIRED")
        _need(event.tool is not None and event.tool in directive["allowed_tools"], "TOOL_NOT_ALLOWED")
        _need(event.effect_key is not None and event.effect_key not in journey.effect_keys
              and journey.effect_status == "NONE", "NON_IDEMPOTENT_EFFECT_REPLAY_DENIED")
        human = _authority(verification, AuthorityKind.HUMAN_RELEASE, event.human_grant_sha256,
                           journey, directive, at, "GO_PUBLISH")
        provider = _authority(verification, AuthorityKind.PROVIDER_ENTITLEMENT, event.provider_entitlement_sha256,
                              journey, directive, at, "PUBLISH")
        _need(human.channel == "HUMAN_ATTENDED" and provider.channel == "PRIVATE_PROVIDER_VERIFIER"
              and provider.actor_ref == scope["provider_ref"]
              and provider.evidence_type == "PROVIDER_ATTESTED"
              and len({human.record_sha256, provider.record_sha256,
                       journey.brain_decision.to_dict()["decision_sha256"]}) == 3, "SEPARATE_RELEASE_GRANTS_REQUIRED")
        if event.spend_minor:
            _need("GO_SPEND" in human.actions and event.currency == human.currency
                  and event.spend_minor <= human.spend_ceiling_minor, "SPEND_NOT_AUTHORIZED")
        else:
            _need(event.currency is None, "ZERO_SPEND_HAS_NO_CURRENCY")
        _need(bool(directive["assets"]) and any(a["sha256"] == scope["asset_sha256"] for a in directive["assets"]),
              "RELEASE_ASSET_REQUIRED")
        for asset in directive["assets"]:
            _need(asset["evidence_type"] not in {"SYNTHETIC_FIXTURE", "UNKNOWN"}
                  and asset["rights"]["status"] == "GRANTED", "EXTERNAL_ASSET_RIGHTS_REQUIRED")
            _authority(verification, AuthorityKind.ASSET_RIGHTS, asset["rights"]["sha256"],
                       journey, directive, at, "ASSET_RIGHTS")
        request = {"journey_id": journey.journey_id, "directive_sha256": directive["directive_sha256"],
                   "scope": scope, "tool": event.tool, "spend_minor": event.spend_minor, "currency": event.currency}
        changes.update(effect_key=event.effect_key, effect_keys=(*journey.effect_keys, event.effect_key),
                       effect_request_sha256=sha256(canonical_bytes(request)).hexdigest(), effect_status="INTENT_RECORDED")
    elif event.kind in {EventKind.EFFECT_UNCERTAIN, EventKind.PROVIDER_CONFIRMED, EventKind.RECONCILED_NO_EFFECT}:
        _need(event.effect_key == journey.effect_key and event.effect_request_sha256 == journey.effect_request_sha256,
              "EFFECT_IDENTITY_MISMATCH")
        if event.kind != EventKind.EFFECT_UNCERTAIN:
            _need(len(event.evidence) == 1 and event.evidence[0].owner == "DISTRIBUTION"
                  and event.evidence[0].evidence_type == "PROVIDER_ATTESTED", "PROVIDER_ATTESTATION_REQUIRED")
            proof = _authority(verification, AuthorityKind.PROVIDER_OBSERVATION, event.evidence[0].sha256,
                               journey, directive, at, "OBSERVE_EFFECT")
            _need(proof.channel == "PRIVATE_PROVIDER_VERIFIER" and proof.evidence_type == "PROVIDER_ATTESTED"
                  and proof.verifier_ref == event.evidence[0].attestation_ref
                  and proof.effect_key == journey.effect_key
                  and proof.effect_request_sha256 == journey.effect_request_sha256
                  and proof.effect_outcome == ("CONFIRMED" if event.kind == EventKind.PROVIDER_CONFIRMED else "NO_EFFECT"),
                  "UNVERIFIED_PROVIDER_OBSERVATION")
        changes["effect_status"] = {EventKind.EFFECT_UNCERTAIN: "UNKNOWN",
            EventKind.PROVIDER_CONFIRMED: "CONFIRMED", EventKind.RECONCILED_NO_EFFECT: "NO_EFFECT"}[event.kind]
    elif event.kind in {EventKind.OUTPUT_READY, EventKind.QA_PASSED, EventKind.ANALYTICS_ACCEPTED,
                       EventKind.REVIEW_LEARNING, EventKind.LEARNING_RECORDED}:
        owner = "DISTRIBUTION" if event.kind == EventKind.ANALYTICS_ACCEPTED else "MEDIA_CREATIVE"
        _need(bool(event.evidence) and any(p.owner == owner for p in event.evidence), "OWNER_EVIDENCE_REQUIRED")
    return replace(journey, **changes, state=target)
