"""Exact A10 provenance into the actual A03 intake, or independent Human text."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache

from jsonschema import Draft202012Validator
from referencing import Registry

from ai_commerce.contracts.schema import _format_checker, validate_record
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative.briefs import intake_brief

from ..core.models import OpportunityError, digest
from ..core.schemas import SOURCE_STATUS
from ..proposals import validate_proposal
from .schemas import EDITS, MAX_RECORD_BYTES, MAX_TEXT_BYTES, schema


class CreativeBridgeError(OpportunityError):
    """Bounded diagnostics; never echo Human text or local file paths."""


def _check(ok: bool, code: str) -> None:
    if not ok:
        raise CreativeBridgeError(code)


def _bounded(value) -> None:
    try:
        _check(len(canonical_bytes(value)) <= MAX_RECORD_BYTES, "BRIDGE_RECORD_TOO_LARGE")
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        raise CreativeBridgeError("BRIDGE_INVALID_JSON_VALUE") from None


def _text(value: str, *, empty: bool = False, creative: bool = False) -> None:
    _check(isinstance(value, str), "BRIDGE_TEXT_REQUIRED")
    _check(empty or bool(value.strip()), "BRIDGE_TEXT_EMPTY")
    try:
        _check(len(value.encode("utf-8")) <= MAX_TEXT_BYTES, "BRIDGE_TEXT_TOO_LARGE")
    except UnicodeError:
        raise CreativeBridgeError("BRIDGE_INVALID_UNICODE") from None
    scanned = unicodedata.normalize("NFKC", value)
    scanned = "".join(c for c in scanned if unicodedata.category(c) != "Cf")
    # URLs, numeric product URLs and @handles are legitimate Human-authored
    # content/CTAs. They are still unverified and are never fetched by A10.
    # Direct contact identifiers remain blocked; evidence has stricter rules.
    pii_scan = re.sub(r"https?://[^\s]+", "", scanned) if creative else scanned
    contact = (
        re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", pii_scan)
        or re.search(r"(?<!\w)(?:\+?\d[\s().-]*){7,}(?!\w)", pii_scan)
        or re.search(r"(?i)\b(?:mailto|tel):", pii_scan)
        or (not creative and re.search(r"(?<!\w)@[\w.]{2,}|https?://", scanned))
    )
    _check(not contact, "BRIDGE_CONTACT_OR_IDENTIFIER_NOT_ALLOWED")
    _check(not any(ord(c) < 32 and c not in "\n\r\t" for c in value), "BRIDGE_CONTROL_CHARACTER")


def _edits(value: Mapping | None) -> dict:
    _check(value is None or isinstance(value, Mapping), "BRIDGE_EDITS_OBJECT_REQUIRED")
    result = deepcopy(dict(value)) if value is not None else {}
    _bounded(result)
    _check(Draft202012Validator(EDITS).is_valid(result), "BRIDGE_INVALID_EDITS")
    for key, item in result.items():
        if item is not None:
            _text(item, empty=key != "objective" and key != "product_ref", creative=True)
    return result


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    definition = schema()
    Draft202012Validator.check_schema(definition)
    return Draft202012Validator(definition, registry=Registry(), format_checker=_format_checker())


def validate_bridge(receipt: dict) -> dict:
    """Validate an immutable transfer receipt, without claiming renewed ledger replay."""
    _bounded(receipt)
    _check(_validator().is_valid(receipt), "BRIDGE_INVALID_RECEIPT")
    body = {k: v for k, v in receipt.items() if k != "bridge_id"}
    _check(receipt["bridge_id"] == "bridge-" + digest(body), "BRIDGE_IDENTITY_MISMATCH")
    _edits(receipt["human_edits"])
    if receipt["mode"] == "OPPORTUNITY":
        proposal = validate_proposal(receipt["proposal"])
        _check(receipt["proposal_sha256"] == digest(proposal), "BRIDGE_PROPOSAL_HASH_MISMATCH")
        refs = [proposal["proposal_id"], *proposal["source_refs"]]
    else:
        source = receipt["human_input"]
        _text(source["text"], creative=True)
        _check(source["text_sha256"] == sha256_bytes(source["text"].encode("utf-8")), "BRIDGE_TEXT_HASH_MISMATCH")
        _check((source["raw_sha256"] is None) == (source["raw_size_bytes"] is None), "BRIDGE_RAW_RECEIPT_MISMATCH")
        refs = ["sha256:" + source["text_sha256"]]
        if source["raw_sha256"] is not None:
            refs.append("sha256:" + source["raw_sha256"])
    _check(receipt["source_refs"] == sorted(set(refs)), "BRIDGE_SOURCE_REFERENCES_MISMATCH")
    return receipt


def _receipt(*, proposal: dict | None, human_input: dict | None, edits: dict, synthetic: bool) -> dict:
    _check(type(synthetic) is bool, "BRIDGE_SYNTHETIC_BOOLEAN_REQUIRED")
    refs = [proposal["proposal_id"], *proposal["source_refs"]] if proposal else ["sha256:" + human_input["text_sha256"]]
    if human_input and human_input["raw_sha256"] is not None:
        refs.append("sha256:" + human_input["raw_sha256"])
    limits = [
        "This transfer validates local serialized integrity, not provider authenticity, demand, audience facts or rights.",
        "Human direction and edits are creative inputs, not new observations or action authority.",
        "Original files and M01/M03 journals retain raw evidence; keep them for source replay.",
        "The receipt describes this transfer; later A03 revisions remain Human-editable.",
        "Synthetic labeling is caller-declared; it is not inferred from provider names or numeric values.",
    ]
    body = {
        "schema_version": "OpportunityCreativeBridge.v1", "mode": "OPPORTUNITY" if proposal else "FREEFORM",
        "proposal": deepcopy(proposal), "proposal_sha256": digest(proposal) if proposal else None,
        "human_input": deepcopy(human_input), "human_edits": edits, "source_refs": sorted(set(refs)),
        "data_kind": "SYNTHETIC_FIXTURE" if synthetic else "USER_SUPPLIED",
        "source_status": SOURCE_STATUS, "factual_status": "UNVERIFIED",
        "epistemic_status": "INFERRED" if proposal else "HUMAN_CREATIVE_INPUT",
        "rights_status": "UNKNOWN", "suggestion_only": True, "publication_status": "NOT_ATTEMPTED",
        "validation_scope": "SERIALIZED_PROPOSAL_INTEGRITY_ONLY" if proposal else "HUMAN_TEXT_ONLY",
        "limitations": limits,
    }
    return validate_bridge({"bridge_id": "bridge-" + digest(body), **body})


def _finish(creative: dict, receipt: dict, brief_id: str | None) -> dict:
    fields = {**creative, **receipt["human_edits"]}
    if brief_id is not None:
        _text(brief_id)
    brief = {
        "brief_id": brief_id if brief_id is not None else "brief:a10:" + digest({"fields": fields, "receipt": receipt}),
        **fields, "source_refs": deepcopy(receipt["source_refs"]), "opportunity_bridge": receipt,
    }
    _bounded(brief)
    validate_record("ContentBrief", brief)
    return intake_brief(brief)


def opportunity_to_brief(proposal: dict, *, edits: Mapping | None = None,
                         brief_id: str | None = None, synthetic: bool = False) -> dict:
    """Map one real M04 proposal to A03 without altering source/status semantics.

    A serialized proposal is validated, not authenticated or replayed here. M04
    propose_opportunities() performs source replay before this transfer.
    """
    _check(isinstance(proposal, dict), "BRIDGE_PROPOSAL_OBJECT_REQUIRED")
    _bounded(proposal)
    proposal = deepcopy(validate_proposal(proposal))
    for key in ("product_ref", "cta"):
        _check(proposal["creative"][key] is None or key in proposal["creative_overrides"],
               "BRIDGE_COMMERCE_INPUT_NOT_EXPLICIT")
    creative = deepcopy(proposal["creative"])
    creative["prompt"] = proposal["manual_input"]["text"] if proposal["manual_input"] else creative["objective"]
    receipt = _receipt(proposal=proposal, human_input=None, edits=_edits(edits), synthetic=synthetic)
    return _finish(creative, receipt, brief_id)


def freeform_brief(text: str, *, edits: Mapping | None = None, brief_id: str | None = None,
                   synthetic: bool = False, raw_bytes: bytes | None = None) -> dict:
    """Bypass every A10 evidence module; preserve exact Human Unicode text.

    Optional original UTF-8 bytes bind a file's BOM/newlines separately from the
    text digest. The source file is never rewritten or claimed provider verified.
    """
    _text(text, creative=True)
    if raw_bytes is not None:
        _check(isinstance(raw_bytes, bytes) and 0 < len(raw_bytes) <= MAX_TEXT_BYTES, "BRIDGE_RAW_BYTES_INVALID")
        try:
            _check(raw_bytes.decode("utf-8-sig") == text, "BRIDGE_RAW_TEXT_MISMATCH")
        except UnicodeError:
            raise CreativeBridgeError("BRIDGE_UTF8_REQUIRED") from None
    original = {"text": text, "text_sha256": sha256_bytes(text.encode("utf-8")),
                "raw_sha256": sha256_bytes(raw_bytes) if raw_bytes is not None else None,
                "raw_size_bytes": len(raw_bytes) if raw_bytes is not None else None}
    receipt = _receipt(proposal=None, human_input=original, edits=_edits(edits), synthetic=synthetic)
    return _finish({"objective": text, "prompt": text, "audience": None,
                    "product_ref": None, "cta": None, "media_hint": None}, receipt, brief_id)
