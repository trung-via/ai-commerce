"""Closed draft contracts, constructed in memory. No files, network or attestor.

JSON labels and hashes identify claims, never authenticate their authors. All
wire authorizations are deliberately false. A future PRIVATE verifier supplies
separate trusted inputs to the pure state machine; this module cannot mint them.
The checked-in JSON Schemas are exports of schema(), not runtime dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math

from jsonschema import Draft202012Validator
from referencing import Registry

MAX_BYTES = 131072
STATES = (
    "NEW", "INPUT_VALIDATED", "RUNNING", "TECHNICAL_QA", "WAIT_BRAIN",
    "READY_FOR_HUMAN_RELEASE", "RELEASE_INTENT", "UNKNOWN_EFFECT",
    "RELEASE_CONFIRMED", "ANALYTICS_IMPORTED", "LEARNING_REVIEW", "COMPLETE",
    "HOLD", "STOPPED",
)
EVIDENCE_TYPES = ("SYNTHETIC_FIXTURE", "HUMAN_SUPPLIED", "PROVIDER_ATTESTED", "UNKNOWN")
OWNERS = ("COMMERCE_OPPORTUNITY", "PRODUCT_INTELLIGENCE", "MEDIA_CREATIVE",
          "DISTRIBUTION", "COMMERCE_OPERATIONS")
ACTIONS = ("PLAN_OFFLINE", "GENERATE_MEDIA", "EXPORT_DRAFT", "PUBLISH",
           "IMPORT_ANALYTICS", "REVIEW_LEARNING")
NAMES = ("CreativeDirective", "BrainDecision", "ProductJourneyReceipt")
DIGEST_FIELDS = dict(zip(NAMES, ("directive_sha256", "decision_sha256", "receipt_sha256")))


class ContractError(ValueError):
    """Bounded diagnostic code, without raw creative or private source text."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ContractError(code)


def canonical_bytes(value: object) -> bytes:
    """Bound JSON complexity before canonical serialization; no custom objects."""
    pending, nodes, size = [(value, 0)], 0, 0
    try:
        while pending:
            item, depth = pending.pop()
            nodes += 1
            require(depth <= 20 and nodes <= 8192, "JSON_COMPLEXITY_LIMIT")
            if type(item) is dict:
                require(len(item) <= 128 and all(type(k) is str for k in item), "INVALID_JSON_OBJECT")
                pending.extend((part, depth + 1) for pair in item.items() for part in pair)
            elif type(item) is list:
                require(len(item) <= 256, "JSON_ARRAY_LIMIT")
                pending.extend((part, depth + 1) for part in item)
            elif type(item) is str:
                require(len(item) <= 8192, "JSON_TEXT_LIMIT")
                size += len(item.encode("utf-8"))
            elif type(item) is float:
                require(math.isfinite(item), "JSON_NONFINITE")
                size += 32
            elif type(item) is int:
                require(item.bit_length() <= 64, "JSON_INTEGER_LIMIT")
                size += 24
            elif item is None or type(item) is bool:
                size += 5
            else:
                raise ContractError("INVALID_JSON_VALUE")
            require(size <= MAX_BYTES, "DOCUMENT_TOO_LARGE")
        raw = json.dumps(value, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("ascii")
        require(len(raw) <= MAX_BYTES, "DOCUMENT_TOO_LARGE")
        return raw
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("INVALID_JSON_VALUE") from exc


def _object(properties: dict) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def _array(item: dict, maximum=64) -> dict:
    return {"type": "array", "maxItems": maximum, "uniqueItems": True, "items": item}


def _ref(name: str) -> dict:
    return {"$ref": "#/$defs/" + name}


def _nullable(item: dict) -> dict:
    return {"anyOf": [item, {"type": "null"}]}


def draft_authorization() -> dict:
    """No implicit GO_SPEND, GO_PUBLISH or provider entitlement."""
    return {"allow_network": False, "allow_spend": False, "allow_publish": False,
            "provider_entitlement": False, "spend_ceiling_minor": 0,
            "currency": None, "authorization_ref": None}


def schema(name: str) -> dict:
    """Fresh JSON Schema 2020-12, with local references only; never fetch a URI."""
    require(type(name) is str and name in NAMES, "UNKNOWN_CONTRACT")
    text = {"type": "string", "minLength": 1, "maxLength": 8192, "pattern": r"\S"}
    reference = {"type": "string", "minLength": 1, "maxLength": 512,
                 "pattern": "^[A-Za-z0-9][A-Za-z0-9_./:@-]*$"}
    digest = {"type": "string", "minLength": 64, "maxLength": 64, "pattern": "^[0-9a-f]{64}$"}
    source = {"type": "string", "minLength": 40, "maxLength": 40, "pattern": "^[0-9a-f]{40}$"}
    version = {"type": "integer", "minimum": 1, "maximum": 2147483647}
    evidence = {"enum": list(EVIDENCE_TYPES)}
    defs = {
        "ref": reference, "digest": digest, "source": source,
        "scope": _object({"action_class": {"enum": list(ACTIONS)},
            "provider_ref": _nullable(reference), "account_ref": _nullable(reference),
            "surface": _nullable(reference), "asset_sha256": _nullable(digest)}),
        "provenance": _object({"evidence_type": evidence,
            "actor": {"enum": ["HUMAN", "CHATGPT_BRAIN", "PROVIDER", "FIXTURE", "UNKNOWN"]},
            "actor_ref": reference, "source_ref": reference, "source_sha": source}),
        "authorization": _object({
            **{key: {"type": "boolean", "const": False, "default": False}
               for key in ("allow_network", "allow_spend", "allow_publish", "provider_entitlement")},
            "spend_ceiling_minor": {"type": "integer", "const": 0, "default": 0},
            "currency": {"type": "null", "default": None},
            "authorization_ref": {"type": "null", "default": None}}),
        "evidence": _object({"owner": {"enum": list(OWNERS)}, "ref": reference,
            "sha256": digest, "evidence_type": evidence,
            "attestation_ref": _nullable(reference)}),
        "rights": _object({"status": {"enum": ["UNKNOWN", "GRANTED", "RESTRICTED", "NOT_APPLICABLE"]},
            "ref": _nullable(reference), "sha256": _nullable(digest)}),
        "pin": _object({"ref": reference, "sha256": digest}),
    }
    props = {"schema_version": {"const": name + ".v1"}, "journey_id": reference,
        "source_sha": source, "directive_version": version, "directive_sha256": digest,
        "scope": _ref("scope"), "provenance": _ref("provenance"),
        "authorization": _ref("authorization")}
    if name == "CreativeDirective":
        props.update({"creative": _object({"freeform": text, "product_ref": _nullable(reference)}),
            "assets": _array(_object({"ref": reference, "sha256": digest,
                "evidence_type": evidence, "rights": _ref("rights")})),
            "action_classes": {**_array({"enum": list(ACTIONS)}, len(ACTIONS)), "minItems": 1},
            "allowed_tools": _array(reference, 32)})
    elif name == "BrainDecision":
        props.update({"decision_sha256": digest, "decision_id": reference,
            "decision": {"enum": ["APPROVE_DIRECTIVE", "REVISE", "HOLD", "STOP"]},
            "review_scope": {"const": "DIRECTIVE_ONLY"},
            "channel": {"const": "CURRENT_CHATGPT_CHAT"}, "chat_ref": reference,
            "receipt_ref": reference, "rationale": text})
    else:
        props.update({"receipt_sha256": digest, "state": {"enum": list(STATES)},
            "brain_decision": _nullable(_ref("pin")),
            "owner_evidence_refs": _array(_ref("evidence"), 128),
            "effect": _object({"status": {"enum": ["NONE", "INTENT_RECORDED", "UNKNOWN", "CONFIRMED", "NO_EFFECT"]},
                "idempotency_key": _nullable(reference), "request_sha256": _nullable(digest),
                "receipt": _nullable(_ref("pin"))}),
            "network_calls_performed": {"const": False},
            "external_effects_performed": {"const": False}})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-commerce:production-runtime:" + name + ".v1",
        "title": name + ".v1 (DRAFT, structural claims only)",
        "description": "No credentials or raw cookies. Validation and digests do not attest truth or authorize effects.",
        **_object(props), "$defs": defs}


def _semantic(name: str, value: dict) -> None:
    require(value["provenance"]["source_sha"] == value["source_sha"], "PROVENANCE_SOURCE_MISMATCH")
    provenance = value["provenance"]
    require(provenance["actor"] != "FIXTURE" or provenance["evidence_type"] == "SYNTHETIC_FIXTURE",
            "FIXTURE_PROVENANCE_UPGRADE")
    require(provenance["evidence_type"] != "PROVIDER_ATTESTED" or provenance["actor"] == "PROVIDER",
            "PROVIDER_LABEL_ACTOR_MISMATCH")
    if name == "CreativeDirective":
        require(value["scope"]["action_class"] in value["action_classes"], "SCOPE_ACTION_NOT_DECLARED")
        for asset in value["assets"]:
            rights = asset["rights"]
            require((rights["ref"] is None) == (rights["sha256"] is None), "INCOMPLETE_RIGHTS_PIN")
            require(rights["status"] != "GRANTED" or rights["ref"] is not None, "RIGHTS_EVIDENCE_REQUIRED")
    if name == "ProductJourneyReceipt":
        for pin in value["owner_evidence_refs"]:
            require((pin["evidence_type"] == "PROVIDER_ATTESTED") == (pin["attestation_ref"] is not None),
                    "ATTESTATION_LABEL_MISMATCH")
        effect = value["effect"]
        require((effect["idempotency_key"] is None) == (effect["request_sha256"] is None), "INCOMPLETE_EFFECT_PIN")
        require((effect["status"] == "NONE") == (effect["idempotency_key"] is None), "EFFECT_STATUS_PIN_MISMATCH")
        require((effect["status"] == "CONFIRMED") == (effect["receipt"] is not None), "EFFECT_RECEIPT_MISMATCH")
        expected = {"RELEASE_INTENT": "INTENT_RECORDED", "UNKNOWN_EFFECT": "UNKNOWN", "RELEASE_CONFIRMED": "CONFIRMED"}
        require(value["state"] not in expected or effect["status"] == expected[value["state"]],
                "RECEIPT_STATE_EFFECT_MISMATCH")


@dataclass(frozen=True, slots=True)
class ValidatedContract:
    """Immutable bytes, revalidated at the transition boundary; not authority."""
    name: str
    raw: bytes

    def to_dict(self) -> dict:
        return json.loads(self.raw)


def validate_contract(name: str, value: object) -> ValidatedContract:
    definition = schema(name)
    raw = canonical_bytes(value)
    detached = json.loads(raw)
    require(Draft202012Validator(definition, registry=Registry()).is_valid(detached), "CONTRACT_SCHEMA_INVALID")
    _semantic(name, detached)
    field = DIGEST_FIELDS[name]
    expected = sha256(canonical_bytes({k: v for k, v in detached.items() if k != field})).hexdigest()
    require(detached[field] == expected, "CONTRACT_DIGEST_MISMATCH")
    return ValidatedContract(name, raw)


def seal_contract(name: str, body: dict) -> ValidatedContract:
    require(type(name) is str and name in NAMES and type(body) is dict, "INVALID_CONTRACT_BODY")
    field = DIGEST_FIELDS[name]
    require(field not in body, "DIGEST_ALREADY_PRESENT")
    return validate_contract(name, {**body, field: sha256(canonical_bytes(body)).hexdigest()})


def parse_contract(name: str, raw: bytes) -> ValidatedContract:
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "DOCUMENT_EMPTY_OR_TOO_LARGE")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def constant(_):
        raise ContractError("JSON_NONFINITE")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        return validate_contract(name, value)
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("INVALID_CONTRACT_JSON") from exc
