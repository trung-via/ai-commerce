"""A15 O05: durable, offline-only cost evidence; never a billing oracle."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from .manifest import strict_json
from .registry import ArtifactRegistry

IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MONEY = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,6})?\Z")
CURRENCY = re.compile(r"[A-Z]{3}\Z")
STATES = frozenset({"ESTIMATED", "CHARGED", "PENDING", "UNKNOWN", "NOT_APPLICABLE"})
ORIGINS = frozenset({"USER_SUPPLIED", "SYNTHETIC_FIXTURE", "UNVERIFIED"})


class CostEvidenceError(ValueError):
    """The cost record cannot be treated as a trustworthy offline observation."""


@dataclass(frozen=True)
class CostEntry:
    entry_id: str
    job_id: str
    attempt: int
    intent_digest: str
    provider: str
    model: str | None
    status: str
    amount: str | None
    currency: str | None
    evidence_ref: str | None
    evidence_sha256: str | None
    evidence_origin: str
    observed_at: str

    def __post_init__(self) -> None:
        if (not IDENTITY.fullmatch(self.entry_id) or not IDENTITY.fullmatch(self.job_id)
                or type(self.attempt) is not int or self.attempt < 1
                or not DIGEST.fullmatch(self.intent_digest)):
            raise CostEvidenceError("INVALID_COST_IDENTITY")
        if (not isinstance(self.provider, str) or not 0 < len(self.provider) <= 128
                or self.provider != self.provider.strip()
                or self.model is not None and
                    (not isinstance(self.model, str) or not 0 < len(self.model) <= 128)):
            raise CostEvidenceError("INVALID_PROVIDER_OR_MODEL")
        if self.status not in STATES or self.evidence_origin not in ORIGINS:
            raise CostEvidenceError("INVALID_COST_STATUS_OR_ORIGIN")
        if (self.status in {"ESTIMATED", "CHARGED"}) != (self.amount is not None):
            raise CostEvidenceError("AMOUNT_STATUS_MISMATCH")
        if self.amount is not None:
            if (not isinstance(self.amount, str) or not MONEY.fullmatch(self.amount)
                    or not isinstance(self.currency, str)
                    or not CURRENCY.fullmatch(self.currency)):
                raise CostEvidenceError("INVALID_DECIMAL_CURRENCY")
            try:
                value = Decimal(self.amount)
                if not value.is_finite() or value < 0:
                    raise CostEvidenceError("INVALID_DECIMAL_CURRENCY")
            except InvalidOperation as exc:
                raise CostEvidenceError("INVALID_DECIMAL_CURRENCY") from exc
        elif self.currency is not None and (
            not isinstance(self.currency, str) or not CURRENCY.fullmatch(self.currency)
        ):
            raise CostEvidenceError("INVALID_OPTIONAL_CURRENCY")
        if (self.evidence_ref is None) != (self.evidence_sha256 is None):
            raise CostEvidenceError("INCOMPLETE_EVIDENCE_REFERENCE")
        if self.evidence_ref is not None and (
            not isinstance(self.evidence_ref, str)
            or not 0 < len(self.evidence_ref) <= 512
            or self.evidence_ref != self.evidence_ref.strip()
            or not isinstance(self.evidence_sha256, str)
            or not DIGEST.fullmatch(self.evidence_sha256)
        ):
            raise CostEvidenceError("INVALID_EVIDENCE_REFERENCE")
        if (self.status == "CHARGED" and (
            self.evidence_ref is None or self.evidence_origin == "UNVERIFIED"
        )):
            raise CostEvidenceError("CHARGE_REQUIRES_SUPPLIED_RECEIPT")
        if not isinstance(self.observed_at, str):
            raise CostEvidenceError("INVALID_OBSERVATION_TIME")
        try:
            observed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CostEvidenceError("INVALID_OBSERVATION_TIME") from exc
        if observed.tzinfo is None or observed.utcoffset() != timezone.utc.utcoffset(observed):
            raise CostEvidenceError("OBSERVATION_MUST_BE_UTC")

    def to_record(self) -> dict:
        payload = {"version": 1, **asdict(self)}
        return {**payload, "record_sha256": sha256_bytes(canonical_bytes(payload))}

    @classmethod
    def from_record(cls, record: dict) -> "CostEntry":
        if not isinstance(record, dict):
            raise CostEvidenceError("INVALID_COST_RECORD")
        fields = set(cls.__dataclass_fields__)
        if set(record) != fields | {"version", "record_sha256"} or record.get("version") != 1:
            raise CostEvidenceError("INVALID_COST_RECORD_FIELDS")
        payload = {k: record[k] for k in ("version", *fields)}
        if record.get("record_sha256") != sha256_bytes(canonical_bytes(payload)):
            raise CostEvidenceError("COST_RECORD_CHECKSUM_MISMATCH")
        return cls(**{key: record[key] for key in fields})


class CostLedger:
    """Append-only evidence over A04's no-overwrite ArtifactRegistry.

    This ledger does NOT authenticate providers or prove a real charge. Even a
    self-described external receipt remains a supplied assertion until separately
    reconciled. Synthetic charges are never counted as business spend.
    """

    def __init__(self, registry: ArtifactRegistry):
        self.registry = registry

    @staticmethod
    def ref(entry_id: str) -> str:
        if not isinstance(entry_id, str) or not IDENTITY.fullmatch(entry_id):
            raise CostEvidenceError("INVALID_ENTRY_ID")
        return f"cost-ledger/entries/{entry_id}.json"

    def record(self, entry: CostEntry) -> CostEntry:
        if not isinstance(entry, CostEntry):
            raise CostEvidenceError("INVALID_COST_ENTRY")
        self.registry.put_immutable(self.ref(entry.entry_id),
                                    canonical_bytes(entry.to_record()) + b"\n")
        return self.get(entry.entry_id)

    def get(self, entry_id: str) -> CostEntry:
        record = strict_json(self.registry.read(self.ref(entry_id)))
        entry = CostEntry.from_record(record)
        if entry.entry_id != entry_id:
            raise CostEvidenceError("ENTRY_REF_IDENTITY_MISMATCH")
        return entry

    def summarize(self, entry_ids: Iterable[str]) -> dict:
        """Summarize only caller-selected IDs, never silently scan another job."""
        seen: set[str] = set()
        totals: dict[str, dict[str, Decimal]] = {}
        counts = {state: 0 for state in sorted(STATES)}
        for entry_id in entry_ids:
            if entry_id in seen:
                raise CostEvidenceError("DUPLICATE_PROJECTION_ENTRY")
            seen.add(entry_id)
            entry = self.get(entry_id)
            counts[entry.status] += 1
            if entry.status not in {"CHARGED", "ESTIMATED"}:
                continue
            currency = entry.currency
            bucket = totals.setdefault(currency, {
                "estimated": Decimal("0"),
                "supplied_charged": Decimal("0"),
                "synthetic_charged": Decimal("0"),
            })
            if entry.status == "ESTIMATED":
                bucket["estimated"] += Decimal(entry.amount)
            elif entry.evidence_origin == "SYNTHETIC_FIXTURE":
                bucket["synthetic_charged"] += Decimal(entry.amount)
            else:
                bucket["supplied_charged"] += Decimal(entry.amount)
        return {
            "schema": "A15_COST_SUMMARY_V1",
            "counts": counts,
            "by_currency": {
                currency: {name: format(amount, "f") for name, amount in categories.items()}
                for currency, categories in sorted(totals.items())
            },
            "external_provider_charge_verified": False,
            "business_revenue_or_settlement_verified": False,
        }
