"""Closed sanitized projections of genuine native WAIT_BRAIN evidence only."""
from __future__ import annotations

from hashlib import sha256

from . import attended_worker as w
from .contracts import canonical_bytes, parse_contract
from .receipt import evidence_packet, export_packet, parse_packet, put_new, seal_wait_receipt

RECEIPT = "ProductJourneyReceipt.v1.json"
PACKET = "attended-evidence.json"
SUMMARY = "OFFLINE_DRAFT_SUMMARY.json"
LEARNING = "OFFLINE_LEARNING_TEMPLATE.json"


def verified(root, directive, intent):
    """Caller excludes a cooperating worker; exact replay and native full decode."""
    _, history, result = w._read(root, directive, intent)
    w._need(result.state == "WAIT_BRAIN", "WAIT_BRAIN_REQUIRED")
    checked = w._verify(root, directive, intent)
    w._need(result.snapshot.evidence == w._pins(intent, checked), "OUTPUT_EVIDENCE_CHANGED")
    receipt, packet = evidence_packet(directive, result, intent=intent, verification=checked, history=history)
    w._need(receipt.raw == seal_wait_receipt(directive, result).raw
            == parse_contract("ProductJourneyReceipt", receipt.raw).raw, "RECEIPT_BYTES_MISMATCH")
    parsed = parse_packet(packet)
    w._need(parsed["receipt_file_sha256"] == sha256(receipt.raw).hexdigest(), "RECEIPT_BYTES_MISMATCH")
    return receipt, packet, parsed


def _bundle(session, root, directive, intent):
    receipt, packet, parsed = verified(root, directive, intent)
    record = receipt.to_dict()
    summary = canonical_bytes({"schema_version": "OperatorHandoff.v1", "package": "OFFLINE_DRAFT",
        **{k: record[k] for k in ("journey_id", "source_sha", "directive_version", "directive_sha256", "scope")},
        "operator_baseline_sha": session["operator_baseline_sha"],
        "directive_file_sha256": intent["directive_file_sha256"],
        "receipt_sha256": record["receipt_sha256"], "receipt_file_sha256": sha256(receipt.raw).hexdigest(),
        "companion_file_sha256": sha256(packet).hexdigest(), "companion_packet_sha256": parsed["packet_sha256"],
        "local_media_qa": parsed["local_artifact"], "qa_checkpoint": parsed["journal_checkpoint"],
        "provenance": record["provenance"]["evidence_type"],
        "flags": ["SANITIZED", "NOT_AUTHENTICATED", "NOT_PUBLISHED", "NO_PROVIDER_EFFECT"],
        "manual_transfer": "Manually share ONLY the receipt, attended-evidence and this summary with Brain in the SAME ChatGPT chat. Wait for independent review. No authenticated channel or approval is supplied."})
    expected = {RECEIPT: receipt.raw, PACKET: packet, SUMMARY: summary}
    return receipt, packet, expected


def _check(session, expected):
    dest = w._private(session["export_dir"])
    marker = w.Path(session["operator_dir"]) / "handoff-seal.json"
    seal = canonical_bytes({name: sha256(raw).hexdigest() for name, raw in expected.items()})
    present = []
    for name, raw in expected.items():
        path = w._local(str(dest / name), missing=True)
        present.append(path.exists())
        w._need(not path.exists() or w._bytes(path) == raw, "HANDOFF_CONFLICT_OR_TAMPER")
    w._need(all(present) or not any(present), "HANDOFF_PARTIAL_OR_MISSING")
    if marker.exists():
        w._need(all(present) and w._bytes(marker) == seal, "HANDOFF_PARTIAL_OR_MISSING")
    return dest, marker, seal, present


def check_existing(session, root, directive, intent):
    _, _, expected = _bundle(session, root, directive, intent)
    _, _, _, present = _check(session, expected)
    w._need(all(present), "HANDOFF_PARTIAL_OR_MISSING")


def export(session, root, directive, intent):
    receipt, packet, expected = _bundle(session, root, directive, intent)
    dest, marker, seal, _ = _check(session, expected)
    export_packet(dest, receipt, packet)
    put_new(dest / SUMMARY, expected[SUMMARY])
    put_new(marker, seal)
    return {"receipt_file_sha256": sha256(receipt.raw).hexdigest(),
            "companion_file_sha256": sha256(packet).hexdigest(), "summary_sha256": sha256(expected[SUMMARY]).hexdigest()}


def learning_template(session, root, directive, intent):
    # Also verify any existing draft package. Never upgrade local text to a decision.
    export(session, root, directive, intent)
    body = {"schema_version": "OfflineLearningTemplate.v1", "package": "OFFLINE_LEARNING_TEMPLATE",
        "journey_id": intent["journey_id"], "directive_sha256": intent["directive_sha256"],
        "proposed_observation_fields": ["technical_quality_notes", "human_review_notes",
            "brain_turns_if_manually_counted", "elapsed_time_notes", "unresolved_questions"],
        "observations": None, "status": "INERT_UNFILLED_PROPOSAL",
        "flags": ["NOT_AUTHENTICATED", "NOT_PUBLISHED", "NO_PROVIDER_EFFECT", "NO_AUTOMATIC_LEARNING"],
        "manual_transfer": "Optional separate proposal for Human and Brain review in the SAME ChatGPT chat; grants no decision, Git branch or publishing authority."}
    raw = canonical_bytes(body)
    path = w._local(str(w.Path(session["export_dir"]) / LEARNING), missing=True)
    put_new(path, raw)
    return {"learning_template_sha256": sha256(raw).hexdigest()}
