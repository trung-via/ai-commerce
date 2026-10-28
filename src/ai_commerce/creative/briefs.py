"""Freeform Unicode intake into the existing A02 ContentBrief JSON contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ai_commerce.contracts.schema import validate_record
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ._validation import CreativeValidationError, json_object, read_json, require_text


def intake_brief(request: str | Mapping[str, Any], *, brief_id: str | None = None) -> dict[str, Any]:
    """Preserve freeform text, or validate/copy an existing JSON ContentBrief.

    JSON-looking strings are parsed strictly, so malformed JSON never becomes a
    successful prose intake. Optional A02/custom fields remain intact.
    """
    if isinstance(request, str):
        require_text(request, "request")
        if request.lstrip().startswith(("{", "[")):
            brief = json_object(read_json(request))
        else:
            brief = {
                "brief_id": brief_id if brief_id is not None else "brief:" + sha256_bytes(request.encode("utf-8")),
                "objective": request,
                "prompt": request,
                "product_ref": None,
                "source_refs": [],
            }
    else:
        brief = json_object(request)
    if brief_id is not None:
        require_text(brief_id, "brief_id")
        if brief.get("brief_id") != brief_id:
            raise CreativeValidationError("brief_id conflicts with existing ContentBrief")
    validate_record("ContentBrief", brief)
    require_text(brief["brief_id"], "brief_id")
    require_text(brief["objective"], "objective")
    for ref in brief.get("source_refs", []):
        require_text(ref, "source_refs")
    return brief


def brief_digest(brief: Mapping[str, Any]) -> str:
    """Digest the entire validated brief using A02 canonical UTF-8 JSON bytes."""
    return sha256_bytes(canonical_bytes(intake_brief(brief)))


def revise_brief(brief: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    """Return an edited brief; storage versions use content digests, not mutation."""
    original = intake_brief(brief)
    edits = json_object(changes)
    if not edits or ("brief_id" in edits and edits["brief_id"] != original["brief_id"]):
        raise CreativeValidationError("brief revision needs edits and must preserve brief_id")
    revised = intake_brief({**original, **edits})
    if revised == original:
        raise CreativeValidationError("brief revision must change content")
    return revised
