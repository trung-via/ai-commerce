"""Append-only, serializable offline history. Persistence is caller-owned."""
from __future__ import annotations

from dataclasses import dataclass

from .evidence import (AffiliateAttributionEvidence,
    build_attribution_evidence, canonical_bytes, digest, identity_bindings,
    parse_json, require, scope_identity)

MAX_HISTORY_BYTES = 16 * 1024 * 1024
MAX_HISTORY_RECORDS = 128


@dataclass(frozen=True)
class Admission:
    evidence_id: str
    duplicate: bool


class AttributionLedger:
    """No database, filesystem, networking or summation of commercial outcomes.

    Corrections replace one complete source snapshot in the active view while
    retaining every earlier byte. A second distinct child of a parent fails.
    The caller must serialize access; this class is not a concurrent database.
    """

    def __init__(self):
        self._records = {}
        self._keys = {}
        self._children = {}

    def append(self, evidence):
        require(isinstance(evidence, AffiliateAttributionEvidence), "EVIDENCE_OBJECT_REQUIRED")
        checked = AffiliateAttributionEvidence.from_bytes(evidence.to_bytes())
        record = checked.to_dict()
        eid = record["evidence_id"]
        if eid in self._records:
            require(self._records[eid] == checked.to_bytes(), "EVIDENCE_HASH_COLLISION")
            return Admission(eid, True)
        require(len(self._records) < MAX_HISTORY_RECORDS, "HISTORY_RECORD_LIMIT")
        document = record["source"]["document"]
        key = digest({"scope": record["scope"], "idempotency_key": document["idempotency_key"]})
        require(key not in self._keys, "IDEMPOTENCY_KEY_COLLISION")
        parent_id = record["correction_of"]
        if parent_id is not None:
            require(parent_id in self._records, "CORRECTION_PARENT_MISSING")
            require(parent_id not in self._children, "CONFLICTING_CORRECTION")
            parent = AffiliateAttributionEvidence.from_bytes(self._records[parent_id])
            replay = build_attribution_evidence(record["source"]["raw_utf8"].encode("utf-8"),
                algorithm=record["algorithm"], correction_of=parent)
            require(replay.to_bytes() == checked.to_bytes(), "CORRECTION_REPLAY_MISMATCH")
        # Compare the entire proposed active view before mutating any index.
        grouped = {}
        proposed = [parse_json(raw) for prior, raw in self._records.items()
                    if prior not in self._children and prior != parent_id] + [record]
        for item in proposed:
            scope = digest(scope_identity(item["scope"]))
            grouped.setdefault(scope, []).extend(item["source"]["document"]["events"])
        for events in grouped.values():
            identity_bindings(events)
        proposed_records = [parse_json(raw) for raw in self._records.values()] + [record]
        require(len(canonical_bytes({"schema_version": "AffiliateAttributionHistory.v1",
                                    "records": proposed_records})) <= MAX_HISTORY_BYTES,
                "HISTORY_SIZE_LIMIT")
        self._records[eid] = checked.to_bytes()
        self._keys[key] = eid
        if parent_id is not None:
            self._children[parent_id] = eid
        return Admission(eid, False)

    def get(self, evidence_id):
        require(evidence_id in self._records, "EVIDENCE_NOT_FOUND")
        return AffiliateAttributionEvidence.from_bytes(self._records[evidence_id])

    def active(self):
        """Snapshots only, never additive totals or inferred entitlements."""
        return tuple(self.get(eid) for eid in self._records if eid not in self._children)

    def to_bytes(self):
        return canonical_bytes({"schema_version": "AffiliateAttributionHistory.v1",
                                "records": [parse_json(raw) for raw in self._records.values()]})

    @classmethod
    def from_bytes(cls, raw):
        document = parse_json(raw, MAX_HISTORY_BYTES)
        require(set(document) == {"schema_version", "records"} and
                document["schema_version"] == "AffiliateAttributionHistory.v1" and
                type(document["records"]) is list and len(document["records"]) <= MAX_HISTORY_RECORDS,
                "INVALID_HISTORY_CONTRACT")
        ledger = cls()
        for record in document["records"]:
            result = ledger.append(AffiliateAttributionEvidence.from_bytes(canonical_bytes(record)))
            require(not result.duplicate, "DUPLICATE_HISTORY_RECORD")
        return ledger
