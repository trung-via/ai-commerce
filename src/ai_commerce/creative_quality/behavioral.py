"""BQ-01a Q1 proposals over an already intaken A03 brief; entirely in memory.

Call A03 ``intake_brief`` / ``brief_digest`` first, then supply their detached
JSON value and digest here. This seam deliberately does not import A03's schema
loader, reasoners or stores. A digest proves byte binding, never source truth,
Human identity, research, rights, audience observations or authenticity.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, ClassVar


MAX_BYTES = 131_072
MAX_TEXT_BYTES = 16_384
MAX_DEPTH = 12
MAX_NODES = 4096
MAX_ITEMS = 128
MAX_INTEGER = 2**53 - 1
A03_INTAKE_REF = "a03:intake"
PROVENANCES = ("HUMAN_SUPPLIED", "SYNTHETIC_ONLY", "UNKNOWN")
EVIDENCE_TYPES = ("USER_INTENT", "HYPOTHESIS", "RESEARCH_REFERENCE", "TEST_FIXTURE", "UNKNOWN")
CLAIM_KINDS = ("BEHAVIORAL_METRIC", "PRODUCT_FACT", "COPYRIGHT_RIGHTS", "TESTIMONIAL", "RESEARCH_CONCLUSION")


class QualityDraftError(ValueError):
    """Closed, bounded draft input or a digest pin is invalid."""


def _text(value: Any, name: str, *, limit: int = MAX_TEXT_BYTES) -> str:
    if type(value) is not str or not value.strip():
        raise QualityDraftError(name + ": expected nonblank text")
    if len(value) > limit:
        raise QualityDraftError(name + ": text size bound")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise QualityDraftError(name + ": invalid Unicode") from exc
    if size > limit:
        raise QualityDraftError(name + ": text size bound")
    return value


def _enum(value: Any, choices: tuple[str, ...], name: str) -> None:
    if type(value) is not str or value not in choices:
        raise QualityDraftError(name + ": unsupported value")


def _integer(value: Any, name: str, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise QualityDraftError(name + ": integer bound")


def _identifier(value: Any, name: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", value):
        raise QualityDraftError(name + ": invalid identifier")
    return value


def _sha(value: Any, name: str = "digest") -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise QualityDraftError(name + ": expected lowercase SHA-256")
    return value


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _bounded(value: Any) -> None:
    count = 0
    text_bytes = 0
    active: set[int] = set()

    def visit(item: Any, depth: int) -> None:
        nonlocal count, text_bytes
        count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            raise QualityDraftError("JSON complexity bound")
        kind = type(item)
        if item is None or kind is bool:
            return
        if kind is str:
            # Empty strings are legal JSON; field validators decide meaning.
            try:
                if len(item) > MAX_TEXT_BYTES:
                    raise QualityDraftError("JSON text size bound")
                size = len(item.encode("utf-8"))
                if size > MAX_TEXT_BYTES:
                    raise QualityDraftError("JSON text size bound")
                text_bytes += size
                if text_bytes > MAX_BYTES:
                    raise QualityDraftError("JSON byte size bound")
            except UnicodeError as exc:
                raise QualityDraftError("invalid Unicode") from exc
            return
        if kind in (int, float):
            if (kind is float and not math.isfinite(item)) or abs(item) > MAX_INTEGER:
                raise QualityDraftError("finite bounded JSON number required")
            return
        if kind not in (dict, list):
            raise QualityDraftError("plain JSON values required")
        if len(item) > MAX_ITEMS or id(item) in active:
            raise QualityDraftError("JSON collection/cycle bound")
        active.add(id(item))
        if kind is dict:
            for key, nested in item.items():
                _text(key, "JSON key", limit=128)
                text_bytes += len(key.encode("utf-8"))
                if text_bytes > MAX_BYTES:
                    raise QualityDraftError("JSON byte size bound")
                visit(nested, depth + 1)
        else:
            for nested in item:
                visit(nested, depth + 1)
        active.remove(id(item))

    visit(value, 0)


def _canonical(value: Any) -> str:
    _bounded(value)
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise QualityDraftError("JSON byte size bound")
    return text


def _read(text: Any) -> Any:
    if type(text) is not str:
        raise QualityDraftError("expected JSON text")
    try:
        if len(text) > MAX_BYTES or len(text.encode("utf-8")) > MAX_BYTES:
            raise QualityDraftError("JSON byte size bound")

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise QualityDraftError("duplicate JSON key")
                result[key] = value
            return result

        def constant(value: str) -> None:
            raise QualityDraftError("nonfinite JSON constant: " + value)

        result = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
        _canonical(result)
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise QualityDraftError("invalid bounded JSON: " + str(exc)[:160]) from exc


def _copy(value: Any) -> Any:
    return _read(_canonical(value))


def _fields(value: Any, names: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(names.split()):
        raise QualityDraftError("missing or extra fields: " + names)
    return value


def _array(value: Any, name: str, minimum: int = 0, maximum: int = 32) -> list[Any]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise QualityDraftError(name + ": array bound")
    return value


def _texts(value: Any, name: str, minimum: int = 0, maximum: int = 16) -> list[str]:
    items = _array(value, name, minimum, maximum)
    for item in items:
        _text(item, name)
    if len(set(items)) != len(items):
        raise QualityDraftError(name + ": duplicate value")
    return items


def _refs(value: Any, known: set[str], *, minimum: int = 1) -> None:
    if not set(_texts(value, "source_refs", minimum)) <= known:
        raise QualityDraftError("unbound source reference")


@dataclass(frozen=True, slots=True)
class _Draft:
    """Immutable canonical value; every public construction path validates.

    to_dict returns a detached value. Caller-supplied expected digests are
    verified explicitly; these hashes are integrity pins, not signatures.
    """

    _json: str
    SCHEMA: ClassVar[str]

    def __post_init__(self) -> None:
        value = _read(self._json)
        self._validate(value)
        object.__setattr__(self, "_json", _canonical(value))

    def _validate(self, value: Any) -> None:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        return _read(self._json)

    def to_json(self) -> str:
        return self._json

    def to_bytes(self) -> bytes:
        return self._json.encode("utf-8")

    @property
    def digest(self) -> str:
        return _hash(self.to_bytes())

    def verify_digest(self, expected: str) -> None:
        if self.digest != _sha(expected):
            raise QualityDraftError("draft digest pin mismatch")

    @classmethod
    def from_dict(cls, value: Any):
        return cls(_canonical(value))

    @classmethod
    def from_json(cls, text: str):
        return cls(text)


def _a03_snapshot(brief: Any) -> None:
    """Check the bounded ContentBrief projection without invoking its I/O loader.

    The caller retains A03 schema validation responsibility. Existing A03 extra
    fields remain opaque source material, never promoted to Q1 evidence.
    """
    if type(brief) is not dict or not {"brief_id", "objective"} <= set(brief):
        raise QualityDraftError("expected already intaken A03 ContentBrief")
    _text(brief["brief_id"], "brief_id")
    _text(brief["objective"], "objective")
    if "prompt" in brief and type(brief["prompt"]) is not str:
        raise QualityDraftError("A03 prompt must be text")
    for key in ("audience", "product_ref", "cta", "media_hint"):
        if key in brief and brief[key] is not None:
            if type(brief[key]) is not str:
                raise QualityDraftError("A03 optional field must be text or null")
            if key == "product_ref":
                _text(brief[key], key)
    _texts(brief.get("source_refs", []), "A03 source_refs")
    if A03_INTAKE_REF in brief.get("source_refs", []):
        raise QualityDraftError("reserved A03 intake source reference")


class BehavioralBriefDraft(_Draft):
    """BehavioralBriefDraft.v1: hypotheses and source-bound UNPROVEN claims only."""

    __slots__ = ()
    SCHEMA = "BehavioralBriefDraft.v1"

    def _validate(self, value: Any) -> None:
        data = _fields(value, "schema_version execution_state source_brief source_brief_digest raw_intent product_ref provenance sources hypotheses claims")
        _enum(data["schema_version"], (self.SCHEMA,), "schema_version")
        _enum(data["execution_state"], ("SIMULATED_ONLY",), "execution_state")
        _enum(data["provenance"], PROVENANCES, "provenance")
        brief = data["source_brief"]
        _a03_snapshot(brief)
        if _hash(_canonical(brief).encode("utf-8")) != _sha(data["source_brief_digest"]):
            raise QualityDraftError("A03 brief digest pin mismatch")
        raw = _text(data["raw_intent"], "raw_intent")
        expected_raw = brief.get("prompt") or brief["objective"]
        if raw != expected_raw and (not raw.lstrip().startswith("{") or _read(raw) != brief):
            raise QualityDraftError("raw intent must bind to A03 source")
        if data["product_ref"] != brief.get("product_ref"):
            raise QualityDraftError("product_ref must preserve A03 input")
        known: set[str] = set()
        allowed = set(brief.get("source_refs", [])) | {A03_INTAKE_REF}
        for source in _array(data["sources"], "sources", 1, 17):
            _fields(source, "ref sha256 content evidence_type provenance truth")
            ref = _text(source["ref"], "source.ref", limit=240)
            if ref not in allowed or ref in known:
                raise QualityDraftError("unbound or duplicate source")
            known.add(ref)
            content = _text(source["content"], "source.content")
            if _hash(content.encode("utf-8")) != _sha(source["sha256"]):
                raise QualityDraftError("source content hash mismatch")
            _enum(source["evidence_type"], EVIDENCE_TYPES, "evidence_type")
            _enum(source["provenance"], PROVENANCES, "source.provenance")
            _enum(source["truth"], ("UNPROVEN",), "source.truth")
            if ref == A03_INTAKE_REF and (content != raw or source["evidence_type"] != "USER_INTENT"
                                        or source["provenance"] != data["provenance"]):
                raise QualityDraftError("A03 intake evidence cannot be promoted or replaced")
        if A03_INTAKE_REF not in known:
            raise QualityDraftError("missing bound intake source")
        ids: set[str] = set()
        for hypothesis in _array(data["hypotheses"], "explicit hypotheses", 1, 16):
            _fields(hypothesis, "id statement assumptions counterexamples source_refs truth")
            identifier = _identifier(hypothesis["id"], "hypothesis.id")
            if identifier in ids:
                raise QualityDraftError("duplicate hypothesis id")
            ids.add(identifier)
            _text(hypothesis["statement"], "hypothesis.statement")
            _texts(hypothesis["assumptions"], "assumptions", 1)
            _texts(hypothesis["counterexamples"], "counterexamples", 1)
            _refs(hypothesis["source_refs"], known)
            _enum(hypothesis["truth"], ("HYPOTHESIS",), "hypothesis.truth")
        for claim in _array(data["claims"], "claims", maximum=16):
            _fields(claim, "kind text source_refs truth")
            _enum(claim["kind"], CLAIM_KINDS, "claim.kind")
            _text(claim["text"], "claim.text")
            _refs(claim["source_refs"], known)
            _enum(claim["truth"], ("UNPROVEN",), "claim.truth")


def build_behavioral_brief(
    brief: dict[str, Any], *, source_brief_digest: str,
    hypotheses: list[dict[str, Any]], provenance: str = "UNKNOWN",
    raw_intent: str | None = None, sources: list[dict[str, Any]] | None = None,
    claims: list[dict[str, Any]] | None = None,
) -> BehavioralBriefDraft:
    """Bind caller-authored hypotheses to real A03 intake bytes, with no inference.

    ``sources`` adds content snapshots for refs already present in A03. Their
    evidence type and supplied provenance are never independent verification.
    Optional original JSON intake text is retained verbatim when it matches.
    """
    snapshot = _copy(brief)
    _a03_snapshot(snapshot)
    raw = (snapshot.get("prompt") or snapshot["objective"]) if raw_intent is None else raw_intent
    _text(raw, "raw_intent")
    return BehavioralBriefDraft.from_dict({
        "schema_version": BehavioralBriefDraft.SCHEMA, "execution_state": "SIMULATED_ONLY",
        "source_brief": snapshot, "source_brief_digest": source_brief_digest,
        "raw_intent": raw, "product_ref": snapshot.get("product_ref"), "provenance": provenance,
        "sources": [{"ref": A03_INTAKE_REF, "sha256": _hash(raw.encode("utf-8")), "content": raw,
                     "evidence_type": "USER_INTENT", "provenance": provenance, "truth": "UNPROVEN"}]
                   + ([] if sources is None else _array(sources, "sources", maximum=16)),
        "hypotheses": hypotheses, "claims": [] if claims is None else claims,
    })
