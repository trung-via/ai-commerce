"""Bounded append-only L05 journal over A04's atomic no-overwrite custody."""
from itertools import islice

from ai_commerce.operations.registry import ArtifactRegistry
from ai_commerce.operations.errors import MediaOperationError
from .evidence import (SettlementError, SettlementEvidence, canonical_bytes, check_revision,
                       digest, parse_json, require)

MAX_RECORDS = 128
MAX_HISTORY_BYTES = 40 * 1024 * 1024


def order_identity(scope):
    # Real order IDs are not re-namespaced by link, currency, variant or window.
    return digest({key: scope[key] for key in ("provider_ref", "platform", "account_ref", "order_ref")})


def _admit(records, value):
    """Validate identity/lineage against every retained record, without mutation."""
    source = value["source"]["document"]
    current, by_id, keys, receipts, receipt_ids = {}, {}, {}, {}, {}
    for record in records:
        old = record["evidence"]
        old_source = old["source"]["document"]
        by_id[old["evidence_id"]] = old
        keys[old_source["idempotency_key"]] = old["evidence_id"]
        identity = order_identity(old_source["scope"])
        current[identity] = old
        for receipt in old_source["receipts"]:
            receipts[receipt["raw_sha256"]] = (identity, receipt)
            receipt_ids[receipt["receipt_ref"]] = (identity, receipt)
    key = source["idempotency_key"]
    if key in keys:
        require(keys[key] == value["evidence_id"], "IDEMPOTENCY_KEY_COLLISION")
        return False
    require(len(records) < MAX_RECORDS, "LEDGER_RECORD_LIMIT")
    identity = order_identity(source["scope"])
    parent_id = value["correction_of"]
    if parent_id is None:
        require(identity not in current, "DUPLICATE_ORDER")
    else:
        require(parent_id in by_id, "CORRECTION_PARENT_MISSING")
        require(identity in current and current[identity]["evidence_id"] == parent_id, "STALE_CORRECTION")
        check_revision(by_id[parent_id], value)
    for receipt in source["receipts"]:
        for bindings, key in ((receipts, receipt["raw_sha256"]), (receipt_ids, receipt["receipt_ref"])):
            if key in bindings:
                require(bindings[key] == (identity, receipt), "RECEIPT_IDENTITY_COLLISION")
    return True


def validate_history(raw):
    """Portable replay checks hashes, sequence, all native records and latest parents."""
    value = parse_json(raw, MAX_HISTORY_BYTES)
    require(set(value) == {"schema_version", "records"} and value["schema_version"] == "SettlementHistory.v1"
            and type(value["records"]) is list and len(value["records"]) <= MAX_RECORDS, "INVALID_SETTLEMENT_HISTORY")
    checked, previous = [], None
    for sequence, record in enumerate(value["records"], 1):
        require(type(record) is dict and set(record) == {"version", "sequence", "previous_sha256", "evidence", "journal_sha256"},
                "INVALID_JOURNAL_RECORD")
        require(type(record["version"]) is int and record["version"] == 1
                and type(record["sequence"]) is int and record["sequence"] == sequence
                and record["previous_sha256"] == previous, "JOURNAL_CHAIN_MISMATCH")
        payload = {key: val for key, val in record.items() if key != "journal_sha256"}
        require(digest(payload) == record["journal_sha256"], "JOURNAL_HASH_MISMATCH")
        evidence = SettlementEvidence.from_bytes(canonical_bytes(record["evidence"])).to_dict()
        require(_admit(checked, evidence), "DUPLICATE_HISTORY_RECORD")
        checked.append(record)
        previous = record["journal_sha256"]
    return checked


def latest_records(records):
    latest = {}
    for record in records:
        evidence = record["evidence"]
        latest[order_identity(evidence["source"]["document"]["scope"])] = evidence
    return sorted(latest.values(), key=lambda row: row["evidence_id"])


class SettlementLedger:
    """One registry worker serializes appends across processes; reads never write.

    A single immutable numbered record is the transaction: no mutable index or
    secondary receipt file can become inconsistent after a crash. A worker lock
    left by a crashed writer requires local reconciliation, never auto-retry.
    """
    def __init__(self, registry):
        require(type(registry) is ArtifactRegistry, "ARTIFACT_REGISTRY_REQUIRED")
        self.registry = registry

    @staticmethod
    def ref(sequence):
        require(type(sequence) is int and 1 <= sequence <= MAX_RECORDS, "INVALID_JOURNAL_SEQUENCE")
        return f"settlement/records/{sequence:04d}.json"

    def _read(self):
        try:
            root = self.registry.safe_path("settlement/records")
            if not root.exists():
                return []
            require(root.is_dir(), "INVALID_JOURNAL_DIRECTORY")
            names = sorted(path.name for path in islice(root.iterdir(), MAX_RECORDS + 1))
            require(len(names) <= MAX_RECORDS and names == [f"{i:04d}.json" for i in range(1, len(names) + 1)],
                    "JOURNAL_SEQUENCE_GAP_OR_LIMIT")
            records, size = [], 0
            for sequence in range(1, len(names) + 1):
                size += self.registry.safe_path(self.ref(sequence)).stat().st_size
                require(size <= MAX_HISTORY_BYTES, "LEDGER_SIZE_LIMIT")
                records.append(parse_json(self.registry.read(self.ref(sequence))))
            return validate_history(canonical_bytes({"schema_version": "SettlementHistory.v1", "records": records}))
        except (MediaOperationError, OSError) as exc:
            raise SettlementError("LEDGER_STORAGE_UNPROVEN") from exc

    def append(self, evidence):
        require(type(evidence) is SettlementEvidence, "NATIVE_SETTLEMENT_REQUIRED")
        value = SettlementEvidence.from_bytes(evidence.to_bytes()).to_dict()
        try:
            with self.registry.worker():
                records = self._read()
                if not _admit(records, value):
                    return False
                payload = {"version": 1, "sequence": len(records) + 1,
                           "previous_sha256": records[-1]["journal_sha256"] if records else None, "evidence": value}
                record = {**payload, "journal_sha256": digest(payload)}
                raw = canonical_bytes(record)
                require(sum(len(canonical_bytes(row)) for row in records) + len(raw) <= MAX_HISTORY_BYTES - 4096,
                        "LEDGER_SIZE_LIMIT")
                require(self.registry.put_immutable(self.ref(len(records) + 1), raw), "JOURNAL_WRITE_COLLISION")
                return True
        except (MediaOperationError, OSError) as exc:
            raise SettlementError("LEDGER_STORAGE_UNPROVEN") from exc

    def get(self, evidence_id):
        for record in self._read():
            if record["evidence"]["evidence_id"] == evidence_id:
                return SettlementEvidence.from_bytes(canonical_bytes(record["evidence"]))
        raise SettlementError("EVIDENCE_NOT_FOUND")

    def latest(self):
        return tuple(SettlementEvidence.from_bytes(canonical_bytes(row)) for row in latest_records(self._read()))

    def to_bytes(self):
        return canonical_bytes({"schema_version": "SettlementHistory.v1", "records": self._read()})


def append_order(ledger, evidence):
    require(type(ledger) is SettlementLedger, "SETTLEMENT_LEDGER_REQUIRED")
    return ledger.append(evidence)
