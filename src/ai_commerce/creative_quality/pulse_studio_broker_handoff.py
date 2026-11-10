"""BQ-05b.1 deterministic, no-secret private-broker trial *proposal*.

This module has no network, provider client, disk write or credential interface.
A LOCAL_TRIAL_INPUT_BOUND result is ONLY a replayed local media-byte binding.
Neither a canonical hash nor a Human-supplied broker draft authorizes an effect.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json

from ai_commerce.creative_quality import pulse_studio_effect_preflight as preflight
from ai_commerce.production_runtime import broker_intake as intake
from ai_commerce.production_runtime import broker_readiness as readiness
from ai_commerce.production_runtime.contracts import canonical_bytes

SCHEMA = "BQ05bBrokerTrialHandoff.v1"
SOURCE_SHA = "0e6e420dc10e390d2c9d3db3b0c98adfebe40cb5"
BOUND = "LOCAL_TRIAL_INPUT_BOUND"
HOLD = "FAIL_CLOSED_HOLD"
REASONS = (
    "CURRENT_NATIVE_QA_BOUND_FOR_OFFLINE_TRIAL_ONLY",
    "CURRENT_NATIVE_REPLAY_REQUIRED",
    "INVALID_UNTRUSTED_SELECTION_HOLD",
    "UNSUPPORTED_REQUEST_HOLD",
)
FLAGS = [
    "SOURCE_ONLY", "NO_PROVIDER_EFFECT", "NO_NETWORK", "NO_SPEND",
    "NO_UPLOAD", "NO_PUBLISH", "NO_ISSUER_ENROLLMENT",
    "NOT_AUTHENTICATED_BRAIN", "DIGEST_IS_IDENTITY_ONLY",
    "NO_BROKER_OR_ACCOUNT_SELECTED",
]
LIMITS = {
    "private_authority": "UNPROVEN",
    "provider_readiness": "UNPROVEN",
    "broker_selection": "HUMAN_REQUIRED",
    "rights": "UNKNOWN",
    "spend_minor": None,
    "currency": None,
    "effect_execution": "NOT_AUTHORIZED",
}
TOP = set((
    "schema_version", "source_sha", "status", "reason_codes", "binding",
    "q4", "q5", "broker_design", "trial_interface", "conformance_vectors",
    "independent_proofs", "limits", "flags", "record_sha256",
))
SCOPE_FIELD_NAMES = preflight.SCOPE_FIELDS
VECTORS = (
    ("CURRENT_NATIVE_BYTES_AND_DISPLAY", "NATIVE_RECEIPT_AND_INDEPENDENT_WYSIWYS"),
    ("PRIVATE_CREDENTIAL_AND_ISSUER_BOUNDARY", "SEPARATELY_ENROLLED_PRIVATE_ISSUER"),
    ("WRONG_ACCOUNT_PROVIDER_RIGHTS_TARGET", "INDEPENDENT_SCOPE_AND_RIGHTS"),
    ("WRONG_CURRENCY_CAP_ACTION_OR_EPOCH", "INDEPENDENT_BUDGET_AND_REVOCATION"),
    ("REPLAY_IDEMPOTENCY_AND_UNKNOWN_EFFECT", "ATOMIC_FENCE_AND_EFFECT_CUSTODY"),
    ("EXTERNAL_PROVIDER_AND_HUMAN_GRANT", "AUTHENTICATED_PROVIDER_AND_HUMAN"),
)
BINDING = set((*preflight.BINDING_FIELDS, "bq05a_source_sha",
               "bq05a_sidecar_sha256", "bq05a_record_sha256",
               "bq05b0_preflight_sha256"))
Q4 = set(preflight.Q4_FIELDS)
Q5 = set(preflight.Q5_FIELDS)


class BrokerTrialHold(ValueError):
    """Finite data-free failure code; never includes user/native paths or secrets."""


def _need(condition):
    if not condition:
        raise BrokerTrialHold("TRIAL_HANDOFF_INVALID")


def _hash(value):
    return sha256(value).hexdigest()


def _vectors():
    return [
        {"vector_id": name, "required_independent_proof": proof,
         "expected": "DENY_UNTIL_AUTHENTICATED",
         "evidence_status": "NOT_OBSERVED"}
        for name, proof in VECTORS
    ]


def _checklist():
    return {
        "schema_version": "BQ05b1PrivateBrokerNoSecretTrial.v1",
        "purpose": "FIELD_NAMES_ONLY_NON_EXECUTABLE",
        "required_effect_scope_fields": list(SCOPE_FIELD_NAMES),
        "proofs": [
            {"proof": code, "independent_owner": owner,
             "proposed_receipt_type": receipt, "status": "UNPROVEN"}
            for code, owner, receipt in preflight.PROOF_OWNERS
        ],
        "on_unknown_effect": "HOLD_ORIGINAL_IDEMPOTENCY_NO_REKEY",
        "on_missing_independent_receipt": "DENY",
    }


def _interface(binding):
    # No provider/account/action/spend request values and no real nonce.
    request = None if binding is None else _hash(canonical_bytes({
        "schema_version": SCHEMA,
        "source_sha": SOURCE_SHA,
        "journey_id": binding["journey_id"],
        "directive_sha256": binding["directive_sha256"],
        "artifact_sha256": binding["artifact_sha256"],
        "preflight_sha256": binding["bq05b0_preflight_sha256"],
    }))
    return {
        "schema_version": "PRIVATE_BROKER_TRIAL_INTERFACE_V1",
        "mode": "INERT_FIELD_ONLY",
        "request_digest": request,
        "challenge_label": "UNAUTHENTICATED_DESIGN_LABEL_NOT_A_NONCE",
        "required_effect_scope_fields": list(SCOPE_FIELD_NAMES),
        "human_selection_required": True,
        "private_issuer_enrolled": False,
        "provider_effect_allowed": False,
        "replay_authorized": False,
        "atomic_budget_reserved": False,
    }


def _validate(data):
    _need(type(data) is dict and set(data) == TOP)
    _need(data["schema_version"] == SCHEMA and data["source_sha"] == SOURCE_SHA)
    _need(data["limits"] == LIMITS and data["flags"] == FLAGS)
    _need(data["independent_proofs"] == _checklist()
          and data["conformance_vectors"] == _vectors())
    status = data["status"]
    _need(status in {BOUND, HOLD})
    _need(data["trial_interface"] == _interface(data["binding"]))
    _need(data["broker_design"] in {
        "HUMAN_BROKER_SELECTION_REQUIRED", "UNTRUSTED_DESIGN_SELECTION",
        "INVALID_UNTRUSTED_SELECTION",
    })
    if status == HOLD:
        _need(data["reason_codes"] in [[reason] for reason in REASONS[1:]])
        _need(data["binding"] is data["q4"] is data["q5"] is None)
        return
    _need(data["reason_codes"] == [REASONS[0]])
    _need(data["broker_design"] != "INVALID_UNTRUSTED_SELECTION")
    binding, q4, q5 = data["binding"], data["q4"], data["q5"]
    _need(type(binding) is dict and set(binding) == BINDING)
    _need(intake.is_hash(binding["bq05b0_preflight_sha256"])
          and intake.is_hash(binding["bq05a_sidecar_sha256"])
          and intake.is_hash(binding["artifact_sha256"]))
    _need(binding["bq05a_source_sha"] == preflight.bridge.SOURCE_SHA)
    _need(binding["pulse_source_sha"] == preflight.bridge.w.SOURCE_SHA)
    _need(binding["artifact_ref"] == "local:video")
    _need(type(q4) is dict and set(q4) == Q4
          and q4["status"] == q4["t2_status"] == "OBSERVED"
          and q4["full_decode"] is True)
    _need(type(q5) is dict and set(q5) == Q5
          and q5["executed"] is False and q5["editor"] == "NONE")
    _need(all(intake.is_hash(value) for k, value in q4.items() if k.endswith("digest")))
    _need(all(intake.is_hash(value) for k, value in q5.items() if k.endswith("digest")))


@dataclass(frozen=True, slots=True)
class BQ05bBrokerTrialHandoff:
    """Caller data is only a non-authoritative snapshot; use recheck for current bytes."""

    raw: bytes = field(repr=False)

    def __post_init__(self):
        try:
            _need(type(self.raw) is bytes and 0 < len(self.raw) <= 64 * 1024)
            data = intake.decode_json(self.raw)
            _need(canonical_bytes(data) == self.raw)
            _validate(data)
            body = {key: value for key, value in data.items() if key != "record_sha256"}
            _need(data["record_sha256"] == _hash(canonical_bytes(body)))
        except Exception:
            raise BrokerTrialHold("TRIAL_HANDOFF_INVALID") from None

    @property
    def digest(self):
        return _hash(self.raw)

    def to_dict(self):
        return json.loads(self.raw)


def prepare_broker_trial_handoff(session_path, *, selection=None, at=None,
                                  effect="REVIEW_ONLY", context=None):
    """Replay BQ-05b.0 over current native B0.4 + Q4/Q5 bytes; never dispatch.

    Any named live effect, caller authority context or untrusted broker structure
    forces a closed HOLD before the native media reader is called.
    """
    design = "HUMAN_BROKER_SELECTION_REQUIRED"
    reason = REASONS[1]
    valid_selection = selection is None
    if selection is not None:
        design = "INVALID_UNTRUSTED_SELECTION"
        try:
            candidate = intake.validate_broker_draft(selection).to_dict()
            report = readiness.preview_selection(candidate, at=at)
            valid_selection = (candidate["schema_version"] == intake.KINDS[0]
                               and report.status == "DESIGN_READY_FOR_HUMAN_SELECTION")
            if valid_selection:
                design = "UNTRUSTED_DESIGN_SELECTION"
            else:
                reason = REASONS[2]
        except Exception:
            reason = REASONS[2]
    body = {
        "schema_version": SCHEMA, "source_sha": SOURCE_SHA, "status": HOLD,
        "reason_codes": [reason], "binding": None, "q4": None, "q5": None,
        "broker_design": design, "trial_interface": _interface(None),
        "conformance_vectors": _vectors(), "independent_proofs": _checklist(),
        "limits": LIMITS, "flags": FLAGS,
    }
    if (type(session_path) is not str or type(effect) is not str
            or effect != "REVIEW_ONLY" or context is not None):
        body["reason_codes"] = [REASONS[3]]
    elif valid_selection:
        try:
            fresh = preflight.inspect_effect_preflight(session_path, selection=selection, at=at)
            checked = preflight.recheck_preflight(fresh.raw, session_path, selection=selection, at=at)
            source = checked.to_dict()
            _need(source["offline_status"] == preflight.OFFLINE_READY
                  and all(row["decision"] == "LIVE_EFFECT_HOLD"
                          for row in source["effect_matrix"])
                  and source["broker"]["external_effect_decision"] == "HOLD_PRIVATE_VERIFIER_NOT_ENROLLED")
            binding = dict(source["binding"])
            binding["bq05b0_preflight_sha256"] = checked.digest
            body.update(status=BOUND, reason_codes=[REASONS[0]],
                        binding=binding, q4=source["q4"], q5=source["q5"],
                        trial_interface=_interface(binding))
        except Exception:
            body.update(status=HOLD, reason_codes=[REASONS[1]], binding=None,
                        q4=None, q5=None, trial_interface=_interface(None))
    return BQ05bBrokerTrialHandoff(
        canonical_bytes({**body, "record_sha256": _hash(canonical_bytes(body))})
    )


def recheck_broker_trial_handoff(raw, session_path, *, selection=None, at=None,
                                 effect="REVIEW_ONLY", context=None):
    """Always recompute source-owned native evidence; never accept self-resealed bytes."""
    supplied = BQ05bBrokerTrialHandoff(raw)
    current = prepare_broker_trial_handoff(
        session_path, selection=selection, at=at, effect=effect, context=context
    )
    if current.raw != supplied.raw or current.to_dict()["status"] != BOUND:
        raise BrokerTrialHold("CURRENT_NATIVE_REPLAY_REQUIRED")
    return current
