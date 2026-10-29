"""A10 transfer receipt, carried as an extension of the unchanged A03 brief."""
from __future__ import annotations

from copy import deepcopy

from ..core.schemas import SHA, SOURCE_STATUS, obj, text
from ..proposals import schema as proposal_schema

MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_TEXT_BYTES = 256 * 1024
EDITABLE = {
    "objective": text(65536), "prompt": {"type": "string", "maxLength": 65536},
    "audience": {"type": ["string", "null"], "maxLength": 65536},
    "product_ref": {"type": ["string", "null"], "minLength": 1, "maxLength": 4096},
    "cta": {"type": ["string", "null"], "maxLength": 65536},
    "media_hint": {"type": ["string", "null"], "maxLength": 65536},
}
EDITS = obj(EDITABLE, required=[])
HUMAN_INPUT = obj({
    "text": text(65536), "text_sha256": SHA,
    "raw_sha256": {"oneOf": [SHA, {"type": "null"}]},
    "raw_size_bytes": {"type": ["integer", "null"], "minimum": 1, "maximum": MAX_TEXT_BYTES},
})
RECEIPT = obj({
    "schema_version": {"const": "OpportunityCreativeBridge.v1"},
    "bridge_id": {"type": "string", "pattern": "^bridge-[a-f0-9]{64}$"},
    "mode": {"enum": ["OPPORTUNITY", "FREEFORM"]},
    "proposal": {"oneOf": [{"$ref": "#/$defs/proposal"}, {"type": "null"}]},
    "proposal_sha256": {"oneOf": [SHA, {"type": "null"}]},
    "human_input": {"oneOf": [HUMAN_INPUT, {"type": "null"}]},
    "human_edits": EDITS,
    "source_refs": {"type": "array", "uniqueItems": True, "maxItems": 50000, "items": text(256)},
    "data_kind": {"enum": ["USER_SUPPLIED", "SYNTHETIC_FIXTURE"]},
    "source_status": {"const": SOURCE_STATUS},
    "factual_status": {"const": "UNVERIFIED"},
    "epistemic_status": {"enum": ["INFERRED", "HUMAN_CREATIVE_INPUT"]},
    "rights_status": {"const": "UNKNOWN"},
    "suggestion_only": {"const": True},
    "publication_status": {"const": "NOT_ATTEMPTED"},
    "validation_scope": {"enum": ["SERIALIZED_PROPOSAL_INTEGRITY_ONLY", "HUMAN_TEXT_ONLY"]},
    "limitations": {"type": "array", "items": text(1024), "minItems": 1, "maxItems": 100},
})
RECEIPT["allOf"] = [{
    "if": {"properties": {"mode": {"const": "OPPORTUNITY"}}},
    "then": {"properties": {
        "proposal": {"$ref": "#/$defs/proposal"}, "proposal_sha256": SHA,
        "human_input": {"type": "null"}, "epistemic_status": {"const": "INFERRED"},
        "validation_scope": {"const": "SERIALIZED_PROPOSAL_INTEGRITY_ONLY"},
    }},
    "else": {"properties": {
        "proposal": {"type": "null"}, "proposal_sha256": {"type": "null"},
        "human_input": HUMAN_INPUT, "epistemic_status": {"const": "HUMAN_CREATIVE_INPUT"},
        "validation_scope": {"const": "HUMAN_TEXT_ONLY"},
    }},
}]


def schema() -> dict:
    return deepcopy({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:OpportunityCreativeBridge:v1",
        "title": "OpportunityCreativeBridge.v1",
        "description": "A10 receipt in ContentBrief.opportunity_bridge; does not replace ContentBrief.v0.1.",
        "$defs": {"proposal": proposal_schema()}, **RECEIPT,
    })
