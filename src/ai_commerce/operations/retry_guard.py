"""A15 O06: durable offline intent and effect reconciliation, not an executor."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from .manifest import strict_json
from .registry import ArtifactRegistry
from .cost_ledger import DIGEST, IDENTITY


class RetryEvidenceError(ValueError):
    pass


OUTCOMES = frozenset({
    "PENDING", "UNKNOWN_EFFECT", "FAILED_NO_EFFECT",
    "SUCCEEDED", "RECONCILED_NO_EFFECT", "RECONCILED_CONFIRMED",
})


@dataclass(frozen=True)
class AttemptObservation:
    intent_id: str
    attempt: int
    outcome: str
    side_effects: str
    evidence_ref: str | None = None
    evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.intent_id, str) or not IDENTITY.fullmatch(self.intent_id)
                or type(self.attempt) is not int or self.attempt < 1
                or self.outcome not in OUTCOMES
                or self.side_effects not in {"NONE", "CONFIRMED", "UNKNOWN"}):
            raise RetryEvidenceError("INVALID_ATTEMPT_OBSERVATION")
        if self.outcome in {"FAILED_NO_EFFECT", "RECONCILED_NO_EFFECT"} and self.side_effects != "NONE":
            raise RetryEvidenceError("NO_EFFECT_REQUIRES_EXPLICIT_PROOF")
        if self.outcome in {"SUCCEEDED", "RECONCILED_CONFIRMED"} and self.side_effects != "CONFIRMED":
            raise RetryEvidenceError("CONFIRMED_EFFECT_REQUIRED")
        if self.outcome in {"PENDING", "UNKNOWN_EFFECT"} and self.side_effects != "UNKNOWN":
            raise RetryEvidenceError("AMBIGUOUS_EFFECT_MUST_REMAIN_UNKNOWN")
        if (self.evidence_ref is None) != (self.evidence_sha256 is None):
            raise RetryEvidenceError("INCOMPLETE_RECONCILIATION_PROOF")
        if self.side_effects != "UNKNOWN" and self.evidence_ref is None:
            raise RetryEvidenceError("DEFINITIVE_STATE_REQUIRES_EVIDENCE")
        if self.evidence_ref is not None and (
            not isinstance(self.evidence_ref, str) or not 0 < len(self.evidence_ref) <= 512
            or not isinstance(self.evidence_sha256, str)
            or not DIGEST.fullmatch(self.evidence_sha256)
        ):
            raise RetryEvidenceError("INVALID_RECONCILIATION_PROOF")

    def record(self) -> dict:
        body = {
            "version": 1, "intent_id": self.intent_id, "attempt": self.attempt,
            "outcome": self.outcome, "side_effects": self.side_effects,
            "evidence_ref": self.evidence_ref, "evidence_sha256": self.evidence_sha256,
        }
        return {**body, "record_sha256": sha256_bytes(canonical_bytes(body))}




@dataclass(frozen=True)
class ReconciliationObservation:
    """Independent, append-only observation for ONE pre-existing uncertain attempt.

    The source is a supplied claim, not automatic provider authentication.
    This record never performs an external action.
    """
    intent_id: str
    attempt: int
    request_digest: str
    original_record_sha256: str
    side_effects: str
    evidence_ref: str
    evidence_sha256: str
    evidence_origin: str = "USER_SUPPLIED"

    def __post_init__(self) -> None:
        if (not isinstance(self.intent_id, str) or not IDENTITY.fullmatch(self.intent_id)
                or type(self.attempt) is not int or self.attempt < 1
                or not isinstance(self.request_digest, str) or not DIGEST.fullmatch(self.request_digest)
                or not isinstance(self.original_record_sha256, str) or not DIGEST.fullmatch(self.original_record_sha256)
                or self.side_effects not in {"NONE", "CONFIRMED"}
                or self.evidence_origin not in {"USER_SUPPLIED", "SYNTHETIC_FIXTURE"}
                or not isinstance(self.evidence_ref, str) or not self.evidence_ref.strip()
                or len(self.evidence_ref) > 512
                or not isinstance(self.evidence_sha256, str) or not DIGEST.fullmatch(self.evidence_sha256)):
            raise RetryEvidenceError("INVALID_RECONCILIATION_OBSERVATION")

    def record(self) -> dict:
        body = {"version": 1, **asdict(self)}
        return {**body, "record_sha256": sha256_bytes(canonical_bytes(body))}

class RetryGuard:
    """Never invokes a provider; the only affirmative decision is advisory."""

    def __init__(self, registry: ArtifactRegistry):
        self.registry = registry

    @staticmethod
    def _base(intent_id: str) -> str:
        if not isinstance(intent_id, str) or not IDENTITY.fullmatch(intent_id):
            raise RetryEvidenceError("INVALID_INTENT_ID")
        return f"retry-intents/{intent_id}"

    @staticmethod
    def _unseal(record: dict) -> dict:
        if not isinstance(record, dict) or "record_sha256" not in record:
            raise RetryEvidenceError("INVALID_RETRY_RECORD")
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        if record["record_sha256"] != sha256_bytes(canonical_bytes(body)):
            raise RetryEvidenceError("RETRY_CHECKSUM_MISMATCH")
        return body

    def register_intent(self, intent_id: str, request_digest: str) -> dict:
        if not isinstance(request_digest, str) or not DIGEST.fullmatch(request_digest):
            raise RetryEvidenceError("INVALID_INTENT_DIGEST")
        base = self._base(intent_id)
        body = {"version": 1, "intent_id": intent_id, "request_digest": request_digest}
        sealed = {**body, "record_sha256": sha256_bytes(canonical_bytes(body))}
        self.registry.put_immutable(base + "/intent.json", canonical_bytes(sealed) + b"\n")
        return self.read_intent(intent_id)

    def read_intent(self, intent_id: str) -> dict:
        data = self._unseal(strict_json(self.registry.read(self._base(intent_id) + "/intent.json")))
        if (set(data) != {"version", "intent_id", "request_digest"}
                or data["version"] != 1 or data["intent_id"] != intent_id
                or not isinstance(data["request_digest"], str)
                or not DIGEST.fullmatch(data["request_digest"])):
            raise RetryEvidenceError("INTENT_IDENTITY_MISMATCH")
        return data

    def _attempt_ids(self, intent_id: str) -> list[int]:
        """Read on-disk attempt inventory, never trust a caller's last-attempt hint."""
        directory = self.registry.safe_path(self._base(intent_id))
        numbers: list[int] = []
        for path in directory.iterdir():
            name = path.name
            if name == "intent.json":
                continue
            if not name.startswith("attempt-") or not name.endswith(".json"):
                raise RetryEvidenceError("UNEXPECTED_INTENT_STORAGE_ENTRY")
            text = name[len("attempt-"):-len(".json")]
            if not text.isascii() or not text.isdecimal() or text.startswith("0"):
                raise RetryEvidenceError("INVALID_ATTEMPT_FILENAME")
            numbers.append(int(text))
        ordered = sorted(numbers)
        if ordered != list(range(1, len(ordered) + 1)):
            raise RetryEvidenceError("NONCONSECUTIVE_ATTEMPT_HISTORY")
        return ordered

    def record_attempt(self, event: AttemptObservation) -> dict:
        if not isinstance(event, AttemptObservation):
            raise RetryEvidenceError("INVALID_RETRY_ATTEMPT")
        self.read_intent(event.intent_id)
        numbers = self._attempt_ids(event.intent_id)
        if event.attempt > len(numbers) + 1:
            raise RetryEvidenceError("MISSING_PREVIOUS_ATTEMPT")
        if event.attempt == len(numbers) + 1 and numbers:
            previous = self.read_attempt(event.intent_id, numbers[-1])
            reconciled = self.read_reconciliation(event.intent_id, numbers[-1])
            if (previous["outcome"] not in {"FAILED_NO_EFFECT", "RECONCILED_NO_EFFECT"}
                    and (reconciled is None or reconciled["side_effects"] != "NONE")):
                raise RetryEvidenceError("PREVIOUS_EFFECT_RECONCILIATION_REQUIRED")
        base = self._base(event.intent_id)
        ref = base + f"/attempt-{event.attempt}.json"
        self.registry.put_immutable(ref, canonical_bytes(event.record()) + b"\n")
        return self.read_attempt(event.intent_id, event.attempt)

    @staticmethod
    def reconciliation_ref(intent_id: str, attempt: int) -> str:
        if not isinstance(intent_id, str) or not IDENTITY.fullmatch(intent_id) or type(attempt) is not int or attempt < 1:
            raise RetryEvidenceError("INVALID_RECONCILIATION_ID")
        return f"retry-reconciliations/{intent_id}/attempt-{attempt}.json"

    def read_reconciliation(self, intent_id: str, attempt: int) -> dict | None:
        ref = self.reconciliation_ref(intent_id, attempt)
        if not self.registry.exists(ref):
            return None
        raw = self._unseal(strict_json(self.registry.read(ref)))
        expected = set(ReconciliationObservation.__dataclass_fields__) | {"version"}
        if set(raw) != expected or raw["version"] != 1:
            raise RetryEvidenceError("RECONCILIATION_SHAPE_INVALID")
        observation = ReconciliationObservation(**{key: raw[key] for key in ReconciliationObservation.__dataclass_fields__})
        original = self.read_attempt(intent_id, attempt)
        digest = sha256_bytes(canonical_bytes(original))
        intent = self.read_intent(intent_id)
        if (observation.intent_id != intent_id or observation.attempt != attempt
                or observation.request_digest != intent["request_digest"]
                or observation.original_record_sha256 != digest
                or original["outcome"] not in {"PENDING", "UNKNOWN_EFFECT"}):
            raise RetryEvidenceError("RECONCILIATION_IDENTITY_MISMATCH")
        return raw

    def record_reconciliation(self, observation: ReconciliationObservation) -> dict:
        if not isinstance(observation, ReconciliationObservation):
            raise RetryEvidenceError("INVALID_RECONCILIATION")
        original = self.read_attempt(observation.intent_id, observation.attempt)
        intent = self.read_intent(observation.intent_id)
        if (original["outcome"] not in {"PENDING", "UNKNOWN_EFFECT"}
                or observation.request_digest != intent["request_digest"]
                or observation.original_record_sha256 != sha256_bytes(canonical_bytes(original))):
            raise RetryEvidenceError("RECONCILIATION_BINDING_MISMATCH")
        ref = self.reconciliation_ref(observation.intent_id, observation.attempt)
        self.registry.put_immutable(ref, canonical_bytes(observation.record()) + b"\n")
        result = self.read_reconciliation(observation.intent_id, observation.attempt)
        assert result is not None
        return result

    def read_attempt(self, intent_id: str, attempt: int) -> dict:
        if type(attempt) is not int or attempt < 1:
            raise RetryEvidenceError("INVALID_ATTEMPT")
        ref = self._base(intent_id) + f"/attempt-{attempt}.json"
        record = self._unseal(strict_json(self.registry.read(ref)))
        if (set(record) != {"version", "intent_id", "attempt", "outcome",
                            "side_effects", "evidence_ref", "evidence_sha256"}
                or record.get("version") != 1
                or record["intent_id"] != intent_id or record["attempt"] != attempt):
            raise RetryEvidenceError("ATTEMPT_IDENTITY_MISMATCH")
        AttemptObservation(**{k: record[k] for k in
            ("intent_id", "attempt", "outcome", "side_effects",
             "evidence_ref", "evidence_sha256")})
        return record

    def decision(self, intent_id: str, *, last_attempt: int = 0) -> dict:
        self.read_intent(intent_id)
        if type(last_attempt) is not int or last_attempt < 0:
            raise RetryEvidenceError("INVALID_ATTEMPT")
        actual = self._attempt_ids(intent_id)
        if last_attempt != len(actual):
            return {
                "intent_id": intent_id, "last_attempt": last_attempt,
                "decision": "RECONCILE_REQUIRED", "reason": "STALE_ATTEMPT_VIEW",
                "provider_called": False, "retry_dispatched": False,
            }
        if last_attempt == 0:
            decision, reason = "REQUIRES_HUMAN_AUTHORIZATION", "NEW_INTENT_UNEXECUTED"
        else:
            last = self.read_attempt(intent_id, last_attempt)
            reconciled = self.read_reconciliation(intent_id, last_attempt)
            if reconciled is not None and reconciled["side_effects"] == "NONE":
                decision, reason = "REQUIRES_HUMAN_AUTHORIZATION", "RECONCILED_NO_EFFECT_SUPPLIED"
            elif reconciled is not None and reconciled["side_effects"] == "CONFIRMED":
                decision, reason = "NO_RETRY", "RECONCILED_EFFECT_CONFIRMED"
            elif last["outcome"] in {"FAILED_NO_EFFECT", "RECONCILED_NO_EFFECT"}:
                decision, reason = "REQUIRES_HUMAN_AUTHORIZATION", "NO_EFFECT_EVIDENCED"
            elif last["outcome"] in {"SUCCEEDED", "RECONCILED_CONFIRMED"}:
                decision, reason = "NO_RETRY", "EFFECT_CONFIRMED"
            else:
                decision, reason = "RECONCILE_REQUIRED", "EFFECT_NOT_PROVEN"
        return {
            "intent_id": intent_id, "last_attempt": last_attempt,
            "decision": decision, "reason": reason,
            "provider_called": False, "retry_dispatched": False,
        }
