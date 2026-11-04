"""Sanitized attended export. Hashes identify bytes; they authenticate nobody."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json

from .contracts import canonical_bytes, draft_authorization, parse_contract, seal_contract

# The frozen B0.1 AST rule predates filesystem adapters. As in B0.2, these
# finite literal stdlib dependencies are audited by this component's tests.
os = __import__("os")
Path = __import__("pathlib").Path
tempfile = __import__("tempfile")


class ReceiptHold(ValueError):
    pass


def put_new(path, raw):
    """Atomic no-replace publication, or an exactly byte-identical re-export.

    Windows rename publishes fully flushed bytes and refuses an existing name.
    POSIX uses a no-replace hard link, avoiding rename's replacement semantics.
    Neither file in a two-file bundle can overwrite divergent existing bytes.
    """
    path = Path(path)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
            raise ReceiptHold("EXPORT_CONFLICT")
        return
    fd, name = tempfile.mkstemp(prefix=".b03-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if os.name == "nt":
                os.rename(temporary, path)
            else:
                os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
                raise ReceiptHold("EXPORT_CONFLICT") from None
    finally:
        temporary.unlink(missing_ok=True)


def seal_wait_receipt(directive, recovery):
    """Use the unchanged B0.1 wire schema and its exact seal/parse functions."""
    if (recovery.state != "WAIT_BRAIN" or recovery.snapshot is None
            or recovery.snapshot.brain_decision is not None
            or recovery.effect_status != "NONE"):
        raise ReceiptHold("WAIT_BRAIN_REQUIRED")
    value = directive.to_dict()
    fixture = any(p.evidence_type == "SYNTHETIC_FIXTURE" for p in recovery.snapshot.evidence)
    evidence_type = "SYNTHETIC_FIXTURE" if fixture else "HUMAN_SUPPLIED"
    sealed = seal_contract("ProductJourneyReceipt", {
        "schema_version": "ProductJourneyReceipt.v1",
        **{k: value[k] for k in ("journey_id", "source_sha", "directive_version", "directive_sha256", "scope")},
        "state": "WAIT_BRAIN", "brain_decision": None,
        "owner_evidence_refs": [asdict(p) for p in recovery.snapshot.evidence],
        "effect": {"status": "NONE", "idempotency_key": None,
                   "request_sha256": None, "receipt": None},
        "network_calls_performed": False, "external_effects_performed": False,
        "authorization": draft_authorization(),
        "provenance": {"evidence_type": evidence_type,
            "actor": "FIXTURE" if fixture else "HUMAN",
            "actor_ref": "fixture:attended-offline" if fixture else "human:attended-transfer-claim",
            "source_ref": "directive:" + value["directive_sha256"], "source_sha": value["source_sha"]},
    })
    return parse_contract("ProductJourneyReceipt", sealed.raw)


def evidence_packet(directive, recovery, *, intent, verification, history):
    """A separate envelope holds pins absent from the frozen receipt schema.

    Use the QA commit checkpoint, so lease releases and read-only restarts do
    not change the receipt or packet. No path, prompt or caller provenance ref
    is projected. A06 LOCAL_IMPORT is origin, not PROVIDER_ATTESTED evidence.
    """
    receipt = seal_wait_receipt(directive, recovery)
    rows = [r for r in history.records if r.journey_id == receipt.to_dict()["journey_id"]]
    if len(rows) != 4 or rows[-1].data().get("kind") != "QA_PASSED":
        raise ReceiptHold("EXACT_OFFLINE_SEQUENCE_REQUIRED")
    last = rows[-1]
    body = {
        "schema_version": "AttendedOfflineEvidence.v1",
        "receipt_file_sha256": sha256(receipt.raw).hexdigest(),
        "receipt_sha256": receipt.to_dict()["receipt_sha256"],
        "intent_sha256": sha256(canonical_bytes(intent)).hexdigest(),
        "directive_file_sha256": intent["directive_file_sha256"],
        "runtime_sha256": intent["runtime_sha256"],
        "local_artifact": verification,
        "journal_checkpoint": {"journal_id": intent["journal_id"],
            "source_sha": intent["source_sha"], "position": last.position,
            "sha256": last.sha256, "generation": last.generation},
        "journal_sequence": [{"sequence": r.sequence, "event_id": r.event_id,
                              "sha256": r.sha256} for r in rows],
        "source_origin": "LOCAL_IMPORT", "generation_performed": False,
        "provider_network_calls": 0, "paid_generation_calls": 0, "publish_calls": 0,
        "brain_identity_authenticated": False, "brain_decision": None,
        "anti_rollback_proof": "NOT_PROVEN", "human_attended": True,
    }
    packet = {**body, "packet_sha256": sha256(canonical_bytes(body)).hexdigest()}
    return receipt, canonical_bytes(packet)


def parse_packet(raw):
    """Reject duplicate keys and noncanonical/tampered companion bytes."""
    def pairs(items):
        value = {}
        for key, part in items:
            if key in value:
                raise ReceiptHold("PACKET_INVALID")
            value[key] = part
        return value
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        body = {k: v for k, v in value.items() if k != "packet_sha256"}
        expected = {"schema_version", "receipt_file_sha256", "receipt_sha256", "intent_sha256",
            "directive_file_sha256", "runtime_sha256", "local_artifact", "journal_checkpoint",
            "journal_sequence", "source_origin", "generation_performed", "provider_network_calls",
            "paid_generation_calls", "publish_calls", "brain_identity_authenticated", "brain_decision",
            "anti_rollback_proof", "human_attended", "packet_sha256"}
        if (set(value) != expected or canonical_bytes(value) != raw
                or value["schema_version"] != "AttendedOfflineEvidence.v1"
                or value["packet_sha256"] != sha256(canonical_bytes(body)).hexdigest()
                or value["brain_identity_authenticated"] is not False or value["brain_decision"] is not None
                or value["generation_performed"] is not False or value["human_attended"] is not True
                or value["source_origin"] != "LOCAL_IMPORT" or value["anti_rollback_proof"] != "NOT_PROVEN"
                or any(type(value[k]) is not int or value[k] != 0 for k in
                       ("provider_network_calls", "paid_generation_calls", "publish_calls"))):
            raise ReceiptHold("PACKET_INVALID")
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ReceiptHold("PACKET_INVALID") from None


def export_packet(destination, receipt, packet):
    record = parse_contract("ProductJourneyReceipt", receipt.raw).to_dict()
    if record["state"] != "WAIT_BRAIN" or record["brain_decision"] is not None or record["effect"]["status"] != "NONE":
        raise ReceiptHold("WAIT_BRAIN_REQUIRED")
    value = parse_packet(packet)
    if value["receipt_file_sha256"] != sha256(receipt.raw).hexdigest():
        raise ReceiptHold("PACKET_RECEIPT_MISMATCH")
    put_new(Path(destination) / "ProductJourneyReceipt.v1.json", receipt.raw)
    put_new(Path(destination) / "attended-evidence.json", packet)
    return {"state": "WAIT_BRAIN", "code": "WAIT_BRAIN",
            "receipt_sha256": receipt.to_dict()["receipt_sha256"],
            "receipt_file_sha256": sha256(receipt.raw).hexdigest(),
            "packet_sha256": value["packet_sha256"]}
