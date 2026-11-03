"""Explicit deterministic rehearsal, with process-local synthetic replay custody.

This store is neither an A08 journal nor a D05 observation source. It has no
durable/external effect guarantee and cannot be extended with a transport.
"""
from __future__ import annotations

from datetime import datetime
from threading import RLock

from ai_commerce.distribution.registry import CapabilityRegistry

from .capability import NativeBinding, capability_for
from .schemas import (MAX_RECORDS, PROFILES, VERSION, HumanConsent, SyntheticReceipt, aware,
                      canonical, checked_request, digest, instant, require, validate)


def propose_human_opt_in(request, *, actor_alias, decided_at, expires_at):
    value = checked_request(request)
    return HumanConsent.from_dict({"schema_version": "HumanDecisionProposal.v1", "request_sha256": request.sha256,
        "target_sha256": digest(value["target"]), "actor_alias": actor_alias, "decision": "OPT_IN",
        "decided_at": decided_at, "expires_at": expires_at, "provenance": "UNVERIFIED_HUMAN_PROPOSAL",
        "authorization_verified": False, "live_authorized": False})


def check_consent(consent, request, at):
    require(type(consent) is HumanConsent, "TYPED_HUMAN_PROPOSAL_REQUIRED")
    value = HumanConsent.from_dict(consent.to_dict()).to_dict()
    req = checked_request(request)
    require(value["request_sha256"] == request.sha256 and value["target_sha256"] == digest(req["target"]),
            "CONSENT_BINDING_MISMATCH")
    require(value["decision"] == "OPT_IN", "OPT_IN_PROPOSAL_REQUIRED")
    require(instant(req["requested_at"]) <= instant(value["decided_at"]) <= aware(at)
            < instant(value["expires_at"]) <= instant(req["expires_at"]), "CONSENT_EXPIRED_OR_OUTSIDE_REQUEST")
    return value


class OfflineDirectPostAdapter:
    """One platform namespace. Every call is a deliberate, effect-free simulation."""
    __slots__ = ("platform", "registry", "_receipts", "_targets", "_callbacks", "_callback_bodies", "_lock")

    def __init__(self, platform: str, registry: CapabilityRegistry):
        require(platform in PROFILES, "CHANNEL_UNKNOWN")
        require(type(registry) is CapabilityRegistry, "NATIVE_A07_REGISTRY_REQUIRED")
        self.platform, self.registry = platform, registry
        self._receipts, self._targets, self._callbacks, self._callback_bodies = {}, {}, {}, {}
        self._lock = RLock()

    def simulate(self, request, *, source: NativeBinding, consent: HumanConsent, at: datetime,
                 outcome="UNKNOWN") -> SyntheticReceipt:
        req = checked_request(request)
        require(req["target"]["platform"] == self.platform, "ADAPTER_PLATFORM_MISMATCH")
        now = aware(at)
        require(instant(req["requested_at"]) <= now < instant(req["expires_at"]), "REQUEST_EXPIRED_OR_FUTURE")
        require(type(outcome) is str and outcome in {"SUBMITTED", "UNKNOWN", "REJECTED", "CONFIRMED"}, "FIXTURE_OUTCOME_INVALID")
        proposal = check_consent(consent, request, now)
        require(type(source) is NativeBinding, "NATIVE_BINDING_REQUIRED")
        # Current rights and source bytes are checked even for an identical replay.
        capability = capability_for(request, self.registry, at=now)
        require(capability["a07_decision"] == "ALLOW", "OFFLINE_CAPABILITY_NOT_ALLOWED")
        native = source.reopen(request)
        intent = validate("Intent", {"schema_version": "DirectPostIntent.v1", "request_sha256": request.sha256,
            "consent_sha256": digest(proposal), "capability_sha256": digest(capability),
            "source_sha256": req["source_sha256"], "reconciliation_sha256": native["reconciliation_sha256"],
            "dashboard_sha256": native["dashboard"].to_dict()["record_sha256"], "native_decision": native["decision"],
            "mode": "OFFLINE", "effect_authorized": False})
        key = req["idempotency_key"]
        target_key = digest({k: req["target"][k] for k in
                             ("platform", "surface", "account_ref", "app_ref", "content_sha256")})
        # Evaluation time is not request identity. Re-evaluating fresh rights must
        # not turn a deterministic replay into a second simulated submission.
        replay_identity = digest({"request": request.sha256, "consent": proposal, "outcome": outcome})
        with self._lock:
            if key in self._receipts:
                identity, receipt = self._receipts[key]
                require(identity == replay_identity, "IDEMPOTENCY_COLLISION_RECONCILE_REQUIRED")
                return receipt
            require(target_key not in self._targets, "REKEYED_SYNTHETIC_TARGET_RECONCILE_REQUIRED")
            require(len(self._receipts) < MAX_RECORDS, "FIXTURE_CAPACITY_LIMIT")
            body = {"schema_version": "DirectPostReceipt.v1", "adapter_version": VERSION,
                "adapter_namespace": "offline." + self.platform.lower(), "target": req["target"],
                "request_sha256": request.sha256, "intent_sha256": digest(intent),
                "source_sha256": req["source_sha256"], "native_decision": native["decision"],
                "fixture_state": outcome, "provenance": "SYNTHETIC_FIXTURE", "publication_status": "UNKNOWN",
                "mode": "OFFLINE", "provider_attested": False, "public_visibility": "UNKNOWN",
                "real_post_created": False, "retry_dispatched": False}
            sha = digest(body)
            receipt = SyntheticReceipt.from_bytes(canonical({**body, "record_sha256": sha,
                "receipt_id": "synthetic-" + self.platform.lower() + "-" + sha}))
            self._receipts[key] = (replay_identity, receipt)
            self._targets[target_key] = key
            return receipt

    def require_issued(self, receipt, request):
        req = checked_request(request)
        require(type(receipt) is SyntheticReceipt, "TYPED_SYNTHETIC_RECEIPT_REQUIRED")
        parsed = SyntheticReceipt.from_bytes(receipt.to_bytes()).to_dict()
        require(req["target"]["platform"] == self.platform and parsed["request_sha256"] == request.sha256
                and parsed["target"] == req["target"] and parsed["source_sha256"] == req["source_sha256"],
                "RECEIPT_REQUEST_MISMATCH")
        with self._lock:
            retained = self._receipts.get(req["idempotency_key"])
            require(retained is not None and retained[1].to_bytes() == receipt.to_bytes(), "RECEIPT_NOT_ISSUED_HERE")
        return parsed

    def inspect_callback(self, request, receipt, callback):
        """Untrusted callbacks can only request reconciliation, never transition state."""
        issued = self.require_issued(receipt, request)
        callback = validate("Callback", callback)
        require(callback["receipt_sha256"] == issued["record_sha256"] and callback["target"] == issued["target"]
                and callback["source_sha256"] == issued["source_sha256"], "CALLBACK_SOURCE_MISMATCH")
        body = {k: v for k, v in callback.items() if k != "event_id"}
        sha, key = digest(body), callback["event_id"]
        with self._lock:
            if key in self._callbacks:
                require(self._callbacks[key] == sha, "CALLBACK_ID_COLLISION")
            else:
                require(sha not in self._callback_bodies, "REKEYED_CALLBACK")
                require(len(self._callbacks) < 256, "CALLBACK_CAPACITY_LIMIT")
                self._callbacks[key] = sha
                self._callback_bodies[sha] = key
        return {"decision": "RECONCILE_REQUIRED", "provider_attested": False,
                "public_visibility": "UNKNOWN", "retry_dispatched": False, "receipt_changed": False}
