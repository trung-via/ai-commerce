"""BQ-01a Q3 R1-R5/R8 caller-authored causal/continuity planning, no media QA."""

from __future__ import annotations

import re
from typing import Any

from .behavioral import (
    BehavioralBriefDraft, QualityDraftError, _Draft, _array, _copy, _enum,
    _fields, _identifier, _integer, _refs, _text, _texts,
)


KNOWLEDGE_MAP = (
    ("K1", "Cinematic & Visual"), ("K2", "Physical & Causal"),
    ("K3", "Identity & Product"), ("K4", "Temporal & Spatial"),
    ("K5", "Human Performance & Audio"), ("K6", "Evidence/Trust/Rights"),
)
REALISM_AXES = ("R1", "R2", "R3", "R4", "R5", "R8")
VISUAL_FORMS = ("REALISTIC", "STYLIZED", "FICTION", "ANIMATION")
CONSTRAINT_KINDS = ("CAMERA", "LIGHTING", "CAUSAL_ACTION", "PHYSICAL_REALISM", "ACOUSTICS", "LIP_SYNC")


def _audio(value: Any, refs: set[str]) -> None:
    audio = _fields(value, "mode language text accent_intent delivery_intent acoustic_intent lip_sync source_refs")
    _enum(audio["mode"], ("SILENT", "VOICEOVER", "ON_SCREEN_SPEECH", "UNKNOWN"), "audio.mode")
    _text(audio["language"], "language", limit=64)
    if audio["language"] != "UNKNOWN" and not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}", audio["language"]):
        raise QualityDraftError("language must be a bounded language tag or UNKNOWN")
    _enum(audio["lip_sync"], ("REQUIRED", "NOT_REQUIRED", "UNKNOWN"), "lip_sync")
    _refs(audio["source_refs"], refs, minimum=0 if audio["mode"] in ("SILENT", "UNKNOWN") else 1)
    for field in ("text", "accent_intent", "delivery_intent", "acoustic_intent"):
        if audio[field] is not None:
            _text(audio[field], "audio." + field)
    if audio["mode"] == "SILENT":
        if (audio["language"] != "UNKNOWN" or audio["lip_sync"] != "NOT_REQUIRED"
                or any(audio[key] is not None for key in ("text", "accent_intent", "delivery_intent", "acoustic_intent"))):
            raise QualityDraftError("silent intent cannot request voice/acoustics/lip-sync")
    if audio["mode"] == "VOICEOVER" and audio["lip_sync"] != "NOT_REQUIRED":
        raise QualityDraftError("voiceover has no on-screen lip-sync requirement")
    if audio["mode"] == "UNKNOWN" and (audio["language"] != "UNKNOWN" or audio["lip_sync"] != "UNKNOWN"
                                       or any(audio[key] is not None for key in ("text", "accent_intent", "delivery_intent", "acoustic_intent"))):
        raise QualityDraftError("unknown audio cannot assert voice constraints")


def _validate_graph(shots: Any, edges: Any, refs: set[str], audio_mode: str) -> None:
    indexed: dict[str, dict[str, Any]] = {}
    previous: dict[str, dict[str, str]] = {}
    identities: dict[str, str] = {}
    last_end = 0
    for index, shot in enumerate(_array(shots, "shots", 1, 32)):
        _fields(shot, "id order start_ms end_ms description objects constraints")
        identifier = _identifier(shot["id"], "shot.id")
        if identifier in indexed:
            raise QualityDraftError("duplicate shot id")
        indexed[identifier] = shot
        _integer(shot["order"], "order", 0, 31)
        if shot["order"] != index:
            raise QualityDraftError("shot order must be contiguous and explicit")
        _integer(shot["start_ms"], "start_ms", 0, 300_000)
        _integer(shot["end_ms"], "end_ms", 1, 300_000)
        if shot["start_ms"] < last_end or shot["end_ms"] <= shot["start_ms"]:
            raise QualityDraftError("shot time order/interval invalid")
        last_end = shot["end_ms"]
        _text(shot["description"], "shot.description")
        objects: dict[str, dict[str, str]] = {}
        for obj in _array(shot["objects"], "objects", maximum=16):
            _fields(obj, "object_id identity position_in position_out motion_in motion_out")
            oid = _identifier(obj["object_id"], "object_id")
            if oid in objects:
                raise QualityDraftError("duplicate object id in shot")
            objects[oid] = obj
            for field in ("identity", "position_in", "position_out", "motion_in", "motion_out"):
                _text(obj[field], field, limit=240)
            if obj["identity"] != "UNKNOWN":
                if oid in identities and identities[oid] != obj["identity"]:
                    raise QualityDraftError("object continuity mismatch: identity")
                identities[oid] = obj["identity"]
            prior = previous.get(oid)
            if prior:
                for before, after in (("identity", "identity"), ("position_out", "position_in"), ("motion_out", "motion_in")):
                    if "UNKNOWN" not in (prior[before], obj[after]) and prior[before] != obj[after]:
                        raise QualityDraftError("object continuity mismatch: " + before)
            previous[oid] = obj
        kinds: set[str] = set()
        for constraint in _array(shot["constraints"], "constraints", maximum=6):
            _fields(constraint, "kind intent state source_refs")
            _enum(constraint["kind"], CONSTRAINT_KINDS, "constraint.kind")
            if constraint["kind"] in kinds:
                raise QualityDraftError("duplicate shot constraint")
            kinds.add(constraint["kind"])
            _text(constraint["intent"], "constraint.intent")
            _enum(constraint["state"], ("REQUESTED", "UNKNOWN"), "constraint.state")
            _refs(constraint["source_refs"], refs)
            if constraint["kind"] == "LIP_SYNC" and audio_mode != "ON_SCREEN_SPEECH":
                raise QualityDraftError("lip-sync requires explicit on-screen speech intent")
            if constraint["kind"] == "ACOUSTICS" and audio_mode == "SILENT":
                raise QualityDraftError("silent intent cannot request acoustics")
    pairs: set[tuple[str, str]] = set()
    adjacency: dict[str, list[str]] = {key: [] for key in indexed}
    indegree = dict.fromkeys(indexed, 0)
    for edge in _array(edges, "edges", maximum=64):
        _fields(edge, "cause effect continuity_objects")
        _identifier(edge["cause"], "cause")
        _identifier(edge["effect"], "effect")
        pair = edge["cause"], edge["effect"]
        if pair in pairs or pair[0] == pair[1] or any(key not in indexed for key in pair):
            raise QualityDraftError("duplicate/self/dangling causal edge")
        pairs.add(pair)
        adjacency[pair[0]].append(pair[1])
        indegree[pair[1]] += 1
        common = {obj["object_id"] for obj in indexed[pair[0]]["objects"]} & {obj["object_id"] for obj in indexed[pair[1]]["objects"]}
        if not set(_texts(edge["continuity_objects"], "continuity_objects")) <= common:
            raise QualityDraftError("continuity object missing from edge endpoints")
    queue = [key for key in indexed if indegree[key] == 0]
    visited = 0
    while queue:
        key = queue.pop()
        visited += 1
        for child in adjacency[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(indexed):
        raise QualityDraftError("causal graph cycle")
    for cause, effect in pairs:
        before, after = indexed[cause], indexed[effect]
        if before["order"] >= after["order"] or before["end_ms"] > after["start_ms"]:
            raise QualityDraftError("causal-before-effect time/order required")


class CausalShotPlanDraft(_Draft):
    """CausalShotPlanDraft.v1: requested constraints, never media attestations.

    Object state endpoints are caller-authored symbolic tokens, not measured
    positions or inferred emotions. UNKNOWN endpoints stay unknown. Intentional
    fantasy may move objects impossibly within a shot; transitions must still
    name coherent endpoint states. R6 calibration / R7 repair are out of scope.
    """

    __slots__ = ()
    SCHEMA = "CausalShotPlanDraft.v1"

    def _validate(self, value: Any) -> None:
        data = _fields(value, "schema_version execution_state behavioral_brief behavioral_brief_digest knowledge_map realism_axes visual_form physics_intent audio shots edges")
        _enum(data["schema_version"], (self.SCHEMA,), "schema_version")
        _enum(data["execution_state"], ("SIMULATED_ONLY",), "execution_state")
        brief = BehavioralBriefDraft.from_dict(data["behavioral_brief"])
        brief.verify_digest(data["behavioral_brief_digest"])
        if data["knowledge_map"] != dict(KNOWLEDGE_MAP) or data["realism_axes"] != list(REALISM_AXES):
            raise QualityDraftError("stable K1-K6 / R1-R5,R8 mapping required")
        _enum(data["visual_form"], VISUAL_FORMS, "visual_form")
        _enum(data["physics_intent"], ("REAL_WORLD", "INTENTIONAL_FANTASY", "UNKNOWN"), "physics_intent")
        refs = {item["ref"] for item in brief.to_dict()["sources"]}
        _audio(data["audio"], refs)
        _validate_graph(data["shots"], data["edges"], refs, data["audio"]["mode"])


def build_causal_plan(
    brief: BehavioralBriefDraft, *, behavioral_brief_digest: str, shots: list[dict[str, Any]],
    edges: list[dict[str, Any]], visual_form: str, physics_intent: str = "UNKNOWN",
    audio: dict[str, Any] | None = None,
) -> CausalShotPlanDraft:
    """Record explicit caller time, action, continuity and optional Vietnamese intent."""
    if not isinstance(brief, BehavioralBriefDraft):
        raise QualityDraftError("expected BehavioralBriefDraft")
    valid = BehavioralBriefDraft.from_json(brief.to_json())
    valid.verify_digest(behavioral_brief_digest)
    return CausalShotPlanDraft.from_dict({
        "schema_version": CausalShotPlanDraft.SCHEMA, "execution_state": "SIMULATED_ONLY",
        "behavioral_brief": valid.to_dict(), "behavioral_brief_digest": valid.digest,
        "knowledge_map": dict(KNOWLEDGE_MAP), "realism_axes": list(REALISM_AXES),
        "visual_form": visual_form, "physics_intent": physics_intent,
        "audio": _copy(audio) if audio is not None else {
            "mode": "UNKNOWN", "language": "UNKNOWN", "text": None, "accent_intent": None,
            "delivery_intent": None, "acoustic_intent": None, "lip_sync": "UNKNOWN", "source_refs": [],
        },
        "shots": shots, "edges": edges,
    })
