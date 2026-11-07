"""BQ-05b.0 source-only readiness binder. No live verification or dispatch.

Only fresh, independently replayed BQ-05a native observations can establish
offline technical readiness. All broker inputs remain untrusted design drafts.
Canonical hashes identify bytes; they authenticate neither issuers nor grants.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re

from ai_commerce.creative_quality import pulse_studio_offline_bridge as bridge
from ai_commerce.production_runtime import broker_intake as intake
from ai_commerce.production_runtime import broker_readiness as broker
from ai_commerce.production_runtime.contracts import canonical_bytes

SCHEMA = "BQ05bEffectPreflight.v1"
SOURCE_SHA = "e33a3162aae34e8ffc3b57c85bc1eeef64c9880e"
EFFECT_SCOPES = intake.ACTIONS
OFFLINE_READY = "OFFLINE_QA_READY_FOR_BRAIN"
OFFLINE_HOLD = "FAIL_CLOSED_HOLD"
REASONS = ("CURRENT_NATIVE_Q4_Q5_ADVISORY_ONLY", "CURRENT_NATIVE_REPLAY_REQUIRED",
           "UNSUPPORTED_REQUEST_HOLD")
BINDING_FIELDS = tuple("journey_id directive_version directive_sha256 pulse_source_sha runtime_sha256 "
    "intent_sha256 directive_file_sha256 receipt_sha256 receipt_file_sha256 packet_sha256 "
    "journal_checkpoint packet_file_sha256 artifact_ref artifact_sha256 manifest_sha256 tree_sha256 "
    "a06_verification_ref".split())
Q4_FIELDS = tuple("status full_decode t2_status t1_digest t2_digest multimodal_review_digest review_status".split())
Q5_FIELDS = tuple("choice decision_digest preservation_digest editor executed".split())
SCOPE_FIELDS = tuple("journey_id directive_version directive_sha256 source_sha pulse_source_sha runtime_sha256 "
    "final_media_sha256 provider_ref account_ref surface action semantic_target_ref semantic_target_sha256 "
    "transaction_sha256 request_sha256 display_sha256 idempotency_key nonce provider_model_ref provider_region_ref "
    "spend_minor spend_ceiling_minor currency "
    "issued_at expires_at revocation_epoch asset_rights_sha256".split())
# Field labels only. No request, account, provider, currency, cap or TTL is selected.
PROOF_OWNERS = (
    ("HUMAN_BROKER_SELECTION", "HUMAN", "IndependentBrokerSelection.v1"),
    ("PRIVATE_ISSUER_ENROLLMENT", "PRIVATE_BROKER", "IndependentIssuerEnrollment.v1"),
    ("OFF_HOST_ISOLATION", "PRIVATE_BROKER", "IndependentIsolationWitness.v1"),
    ("AUTHENTICATED_CHALLENGE", "PRIVATE_BROKER", "AuthenticatedEffectChallenge.v1"),
    ("BRAIN_EFFECT_DECISION", "BRAIN", "BrainDecision.v1"),
    ("HUMAN_EFFECT_GRANT", "HUMAN", "AuthenticatedPerEffectGrant.v1"),
    ("PROVIDER_ENTITLEMENT", "PROVIDER", "AuthenticatedProviderEntitlement.v1"),
    ("ASSET_RIGHTS", "HUMAN", "IndependentEffectRights.v1"),
    ("REVOCATION_WITNESS", "PRIVATE_BROKER", "IndependentRevocationWitness.v1"),
    ("ATOMIC_REPLAY_RESERVATION", "PRIVATE_BROKER", "AtomicReplayReservation.v1"),
    ("ATOMIC_BUDGET_RESERVATION", "PRIVATE_BROKER", "AtomicBudgetReservation.v1"),
    ("PROVIDER_EFFECT_RECEIPT", "PROVIDER", "AuthenticatedEffectReceipt.v1"),
)
PROOF_CODES = tuple(row[0] for row in PROOF_OWNERS)
LIMITS = {"creative_quality": "UNKNOWN", "business_quality": "UNKNOWN", "rights": "UNKNOWN",
          "provider_readiness": "UNKNOWN", "private_authority": "UNPROVEN",
          "anti_rollback": "NOT_PROVEN", "brain_authentication": "NOT_PROVEN",
          "currency": None, "spend_ceiling_minor": None}
FLAGS = ["SOURCE_ONLY", "NO_PROVIDER_EFFECT", "NO_NETWORK", "NO_PUBLISH", "NO_SPEND",
         "NOT_AUTHENTICATED_BRAIN", "DIGEST_IS_BYTE_IDENTITY_ONLY", "HUMAN_SELECTION_REQUIRED"]
TOP = set("schema_version source_sha offline_status reason_codes binding provenance q4 q5 broker "
          "effect_matrix private_evidence_checklist limits flags product_ref record_sha256".split())


class PreflightHold(ValueError):
    """Finite diagnostics without caller or native payloads."""


def _need(ok):
    if not ok:
        raise PreflightHold("PREFLIGHT_INVALID")


def _hash(raw):
    return sha256(raw).hexdigest()


def _checklist():
    return {"schema_version": "BQ05bPrivateEvidenceChecklist.v1", "mode": "STATIC_PROPOSAL_ONLY",
            "required_effect_scope_fields": list(SCOPE_FIELDS),
            "receipt_requirement": "AUTHENTICATED_EFFECT_SCOPED_INDEPENDENT_RECEIPTS",
            "on_authenticated_match": "VERIFIED", "on_missing_or_invalid": "HOLD",
            "on_ambiguous_dispatch": "UNKNOWN_EFFECT",
            "items": [{"proof": code, "owner": owner, "proposed_receipt": receipt,
                       "status": "MISSING_INDEPENDENT_PROOF"} for code, owner, receipt in PROOF_OWNERS]}


def _matrix(upstream_reason):
    return [{"effect": effect, "decision": "LIVE_EFFECT_HOLD",
             "reason": "PRIVATE_VERIFIER_NOT_ENROLLED", "upstream_reason": upstream_reason,
             "missing_proofs": list(PROOF_CODES)} for effect in EFFECT_SCOPES]


def _broker_reports(selection, isolation, receipt, state, at):
    # Existing owners alone determine design status and compose S2/S3a/S3b.
    args = dict(at=at, isolation=isolation, receipt=receipt, state=state)
    design = broker.evaluate_readiness(selection, **args)
    external = broker.decide_external_effect(selection, **args)
    _need(external.external_effect_decision == "HOLD_PRIVATE_VERIFIER_NOT_ENROLLED")
    return {"input_trust": "UNTRUSTED", "design_status": design.status,
            "design_reason": design.reason, "external_status": external.status,
            "external_reason": external.reason, "custody_reason": external.custody_gate_reason,
            "external_effect_decision": external.external_effect_decision,
            "native_scope_status": "UNAVAILABLE"}


def _native_scope(receipt, binding):
    if receipt is None:
        return "NOT_PROVIDED"
    try:
        rec = intake.validate_broker_draft(receipt).to_dict()
        pairs = (("journey_id", "journey_id"), ("directive_version", "directive_version"),
                 ("directive_sha256", "directive_sha256"), ("source_sha", "pulse_source_sha"),
                 ("final_media_sha256", "artifact_sha256"))
        if rec["schema_version"] == intake.KINDS[2] and all(rec[a] == binding[b] for a, b in pairs):
            return "MATCHED_UNTRUSTED"
    except (intake.IntakeError, KeyError):
        pass
    return "MISMATCH_HOLD"


def _validate(value):
    _need(type(value) is dict and set(value) == TOP)
    _need(value["schema_version"] == SCHEMA and value["source_sha"] == SOURCE_SHA)
    _need(value["flags"] == FLAGS and value["limits"] == LIMITS and value["product_ref"] is None)
    _need(value["private_evidence_checklist"] == _checklist())
    report = value["broker"]
    _need(type(report) is dict and set(report) == set("input_trust design_status design_reason external_status "
          "external_reason custody_reason external_effect_decision native_scope_status".split()))
    _need(report["input_trust"] == "UNTRUSTED" and report["design_status"] in broker.STATUSES
          and report["design_reason"] in broker.REASONS and report["external_status"] in {"HOLD", "REJECT"}
          and report["external_reason"] in broker.REASONS and report["custody_reason"] in broker.S3A_REASONS
          and report["external_effect_decision"] == "HOLD_PRIVATE_VERIFIER_NOT_ENROLLED"
          and report["native_scope_status"] in {"UNAVAILABLE", "NOT_PROVIDED", "MATCHED_UNTRUSTED", "MISMATCH_HOLD"})
    _need((report["design_reason"] == broker.REASONS[0]) if report["design_status"] == "DESIGN_READY_FOR_HUMAN_SELECTION"
          else report["design_reason"].startswith(report["design_status"] + "_"))
    _need(report["external_reason"].startswith(report["external_status"] + "_"))
    _need(value["effect_matrix"] == _matrix(report["external_reason"]))
    _need(value["offline_status"] in {OFFLINE_READY, OFFLINE_HOLD})
    if value["offline_status"] == OFFLINE_HOLD:
        _need(value["reason_codes"] in [[r] for r in REASONS[1:]])
        _need(all(value[k] is None for k in ("binding", "provenance", "q4", "q5")))
        _need(report["native_scope_status"] == "UNAVAILABLE")
        return
    _need(value["reason_codes"] == [REASONS[0]])
    binding, origin, q4, q5 = (value[k] for k in ("binding", "provenance", "q4", "q5"))
    extra = ("bq05a_source_sha", "bq05a_sidecar_sha256", "bq05a_record_sha256")
    _need(type(binding) is dict and set(binding) == set((*BINDING_FIELDS, *extra)))
    _need(binding["bq05a_source_sha"] == bridge.SOURCE_SHA
          and binding["pulse_source_sha"] == bridge.w.SOURCE_SHA)
    _need(re.fullmatch(r"journey:[0-9a-f]{32,64}", binding["journey_id"]) is not None)
    _need(type(binding["directive_version"]) is int and binding["directive_version"] > 0)
    _need(binding["artifact_ref"] == "local:video"
          and intake.is_ref(binding["a06_verification_ref"], "a06"))
    for key, item in binding.items():
        if key.endswith("sha256"):
            _need(intake.is_hash(item))
    checkpoint = binding["journal_checkpoint"]
    _need(type(checkpoint) is dict and set(checkpoint) == {"journal_id", "source_sha", "position", "sha256", "generation"})
    _need(type(checkpoint["journal_id"]) is str
          and re.fullmatch(r"journal:[0-9a-f]{32}", checkpoint["journal_id"]) is not None
          and checkpoint["source_sha"] == binding["pulse_source_sha"] and intake.is_hash(checkpoint["sha256"]))
    _need(all(type(checkpoint[k]) is int and checkpoint[k] > 0 for k in ("position", "generation")))
    _need(type(origin) is dict and set(origin) == {"evidence_type", "origin", "provider_attested"})
    _need(origin["evidence_type"] in {"SYNTHETIC_FIXTURE", "HUMAN_SUPPLIED"}
          and origin["origin"] == "LOCAL_IMPORT" and origin["provider_attested"] is False)
    _need(type(q4) is dict and set(q4) == set(Q4_FIELDS)
          and q4["status"] == q4["t2_status"] == "OBSERVED" and q4["full_decode"] is True
          and q4["review_status"] in {"HOLD", "REVISE", "UNKNOWN"})
    _need(type(q5) is dict and set(q5) == set(Q5_FIELDS)
          and q5["choice"] in {"HOLD", "KEEP_ORIGINAL", "RETURN_TO_BRAIN"}
          and q5["editor"] == "NONE" and q5["executed"] is False)
    _need(all(intake.is_hash(v) for row in (q4, q5) for k, v in row.items() if k.endswith("digest")))
    _need(report["native_scope_status"] != "UNAVAILABLE")


@dataclass(frozen=True, slots=True)
class BQ05bEffectPreflight:
    """Closed sanitized bytes, never an authority object. Receivers must replay."""

    raw: bytes = field(repr=False)

    def __post_init__(self):
        try:
            _need(type(self.raw) is bytes and 0 < len(self.raw) <= 64 * 1024)
            value = json.loads(self.raw)
            _need(canonical_bytes(value) == self.raw)
            _validate(value)
            body = {k: v for k, v in value.items() if k != "record_sha256"}
            _need(value["record_sha256"] == _hash(canonical_bytes(body)))
        except Exception:
            raise PreflightHold("PREFLIGHT_INVALID") from None

    @property
    def digest(self):
        return _hash(self.raw)

    def to_dict(self):
        return json.loads(self.raw)


def inspect_effect_preflight(session_path, *, effect="REVIEW_ONLY", selection=None,
                             isolation=None, receipt=None, state=None, at=None, context=None):
    """Read a completed native journey; return technical readiness and live HOLD.

    No caller sidecar/report/fixture token is proof. ``at`` is an untrusted
    logical time for design comparison only, never a freshness/authority clock.
    An external-effect request fails before native media observation.
    """
    report = _broker_reports(selection, isolation, receipt, state, at)
    body = {"schema_version": SCHEMA, "source_sha": SOURCE_SHA, "offline_status": OFFLINE_HOLD,
            "reason_codes": [REASONS[1]], "binding": None, "provenance": None, "q4": None, "q5": None,
            "broker": report, "effect_matrix": _matrix(report["external_reason"]),
            "private_evidence_checklist": _checklist(), "limits": LIMITS, "flags": FLAGS, "product_ref": None}
    if type(session_path) is not str or type(effect) is not str or effect != "REVIEW_ONLY" or context is not None:
        body["reason_codes"] = [REASONS[2]]
    else:
        try:
            fresh = bridge.inspect_wait_brain(session_path, sample=True)
            current = bridge.recheck_sidecar(fresh.raw, session_path, sample=True)
            data = current.to_dict()
            _need(data["status"] == "OFFLINE_TECHNICAL_REVIEW_ONLY"
                  and data["q4"]["status"] == data["q4"]["t2_status"] == "OBSERVED"
                  and data["q5"]["executed"] is False and data["q5"]["editor"] == "NONE")
            binding = {k: data["binding"][k] for k in BINDING_FIELDS}
            binding.update(bq05a_source_sha=data["source_sha"], bq05a_sidecar_sha256=current.digest,
                           bq05a_record_sha256=data["record_sha256"])
            body.update(offline_status=OFFLINE_READY, reason_codes=[REASONS[0]], binding=binding,
                        provenance={k: data["provenance"][k] for k in ("evidence_type", "origin", "provider_attested")},
                        q4={k: data["q4"][k] for k in Q4_FIELDS}, q5={k: data["q5"][k] for k in Q5_FIELDS})
            report["native_scope_status"] = _native_scope(receipt, binding)
        except Exception:
            # No partial native observations, paths or exception text escape.
            body.update(offline_status=OFFLINE_HOLD, reason_codes=[REASONS[1]],
                        binding=None, provenance=None, q4=None, q5=None)
            report["native_scope_status"] = "UNAVAILABLE"
    return BQ05bEffectPreflight(canonical_bytes({**body, "record_sha256": _hash(canonical_bytes(body))}))


def recheck_preflight(raw, session_path, **design_inputs):
    """Recompute fresh native evidence; a self-resealed report proves nothing."""
    supplied = BQ05bEffectPreflight(raw)
    current = inspect_effect_preflight(session_path, **design_inputs)
    if current.raw != supplied.raw or current.to_dict()["offline_status"] != OFFLINE_READY:
        raise PreflightHold("CURRENT_NATIVE_REPLAY_REQUIRED")
    return current
