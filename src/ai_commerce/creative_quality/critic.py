"""BQ-02a Q2: deterministic adversarial questions over supplied source plans.

No media, detector, model, strategy mutation or external effect is invoked.
Caller notes are bounded untrusted data and never become observed findings.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from ai_commerce.creative_quality import behavioral, causality


Error = behavioral.QualityDraftError
MAX_BYTES = 524_288
MAX_DEPTH = 24
MAX_NODES = 32_768
DIMENSIONS = ("causal", "identity", "temporal", "audio", "behavior", "claim_rights", "artistic", "meta")
AUTHORITY = {"publication": "NONE", "external_effects": "NONE", "strategy_changes": "BRAIN_ONLY"}


def _canonical(value: Any) -> str:
    """Closed plain JSON bounds apply to the complete nested receipt."""
    active: set[int] = set()
    nodes = 0
    text_bytes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes, text_bytes
        nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            raise Error("record depth/node bound")
        kind = type(item)
        if item is None or kind is bool:
            return
        if kind is str:
            try:
                size = len(item.encode("utf-8", "strict"))
            except UnicodeError as exc:
                raise Error("invalid Unicode") from exc
            text_bytes += size
            if size > 16_384 or text_bytes > MAX_BYTES:
                raise Error("record text/byte bound")
            return
        if kind in (int, float):
            if (kind is float and not math.isfinite(item)) or abs(item) > 2**53 - 1:
                raise Error("finite bounded number required")
            return
        if kind not in (list, dict) or len(item) > 4096 or id(item) in active:
            raise Error("plain JSON collection/cycle bound")
        active.add(id(item))
        if kind is dict:
            for key, child in item.items():
                behavioral._text(key, "key", limit=128)
                visit(key, depth + 1)
                visit(child, depth + 1)
        else:
            for child in item:
                visit(child, depth + 1)
        active.remove(id(item))

    visit(value, 0)
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise Error("record byte bound")
    return raw


def _read(raw: Any) -> Any:
    if type(raw) is not str:
        raise Error("JSON text required")
    try:
        if len(raw) > MAX_BYTES or len(raw.encode("utf-8", "strict")) > MAX_BYTES:
            raise Error("record byte bound")

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise Error("duplicate JSON key")
                result[key] = value
            return result

        def constant(value):
            raise Error("nonfinite JSON constant")

        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        _canonical(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise Error("invalid bounded record: " + str(exc)[:120]) from exc


@dataclass(frozen=True, slots=True)
class _Record:
    _json: str

    def __post_init__(self):
        value = _read(self._json)
        self._validate(value)
        object.__setattr__(self, "_json", _canonical(value))

    def _validate(self, value):
        raise NotImplementedError

    @classmethod
    def from_dict(cls, value):
        return cls(_canonical(value))

    @classmethod
    def from_json(cls, raw):
        return cls(raw)

    def to_json(self):
        return self._json

    def to_bytes(self):
        return self._json.encode("utf-8")

    def to_dict(self):
        return _read(self._json)

    @property
    def digest(self):
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def verify_digest(self, expected):
        if self.digest != behavioral._sha(expected):
            raise Error("record digest pin mismatch")


def _native_plan(plan, digest):
    if type(plan) is not causality.CausalShotPlanDraft:
        raise Error("native CausalShotPlanDraft required")
    valid = causality.CausalShotPlanDraft.from_json(plan.to_json())
    valid.verify_digest(digest)
    return valid


def _direction(value, digest, plan):
    if value is None:
        if digest is not None:
            raise Error("absent direction has no digest")
        return None
    # These are real BQ-01b validators, imported only for supplied direction.
    from ai_commerce.creative_quality import acoustic_scene, voice_intent
    schema = value.get("schema_version") if type(value) is dict else None
    classes = {voice_intent.VietnameseVoiceIntentDraft.SCHEMA: voice_intent.VietnameseVoiceIntentDraft,
               acoustic_scene.AcousticSceneDraft.SCHEMA: acoustic_scene.AcousticSceneDraft}
    if type(schema) is not str or schema not in classes:
        raise Error("native BQ-01b direction schema required")
    valid = classes[schema].from_dict(value)
    valid.verify_digest(digest)
    data = valid.to_dict()
    nested_plan = data["plan"] if schema == voice_intent.VietnameseVoiceIntentDraft.SCHEMA else data["voice"]["plan"]
    if data["plan_digest"] != plan.digest or nested_plan != plan.to_dict():
        raise Error("direction must bind original plan")
    return data


def _notes(values, plan):
    refs = {x["ref"] for x in plan["behavioral_brief"]["sources"]}
    shots = {x["id"] for x in plan["shots"]}
    ids = set()
    for note in behavioral._array(values, "caller_notes", maximum=16):
        behavioral._fields(note, "id text source_refs shot_id")
        key = behavioral._identifier(note["id"], "note.id")
        if key in ids:
            raise Error("duplicate caller note")
        ids.add(key)
        behavioral._text(note["text"], "untrusted caller text", limit=2048)
        behavioral._refs(note["source_refs"], refs)
        if note["shot_id"] is not None:
            behavioral._identifier(note["shot_id"], "note.shot_id")
            if note["shot_id"] not in shots:
                raise Error("unbound caller shot")
    return values


def _plan_status(items):
    if any(x["severity"] == "P0" for x in items):
        return "HOLD"
    if any(x["severity"] == "P1" for x in items):
        return "REVISE"
    if any(x["uncertainty"] == "BLOCKING_UNKNOWN" for x in items):
        return "UNKNOWN"
    return "PASS_PREFLIGHT_ADVISORY"


def _payload(plan, direction, direction_digest, notes):
    source = plan.to_dict()
    brief = behavioral.BehavioralBriefDraft.from_dict(source["behavioral_brief"]).to_dict()
    notes = _notes(notes, source)
    items = []

    def add(key, dimension, severity, assertion, counterexample, *, refs=None, shot=None,
            grounding="PLAN_ONLY", uncertainty="EXPECTED_PLAN_ONLY"):
        items.append({"id": key, "dimension": dimension, "severity": severity,
                      "assertion": assertion, "grounding": grounding,
                      "source_refs": [behavioral.A03_INTAKE_REF] if refs is None else refs,
                      "shot_id": None if shot is None else shot["id"],
                      "intent_time_ms": None if shot is None else {"start_ms": shot["start_ms"], "end_ms": shot["end_ms"]},
                      "counterexample": counterexample, "uncertainty": uncertainty,
                      "route": "BRAIN" if severity == "P3" else "ADVISORY_REVIEW"})

    for index, claim in enumerate(brief["claims"]):
        add(f"claim:{index}", "claim_rights", "P0",
            "The source declares an UNPROVEN " + claim["kind"] + " claim; obtain authoritative evidence before relying on it.",
            "A supplied statement or digest can be wrong and cannot establish truth or permission.",
            refs=claim["source_refs"], grounding="SOURCE_HYPOTHESIS", uncertainty="UNPROVEN_SOURCE")
    if brief["product_ref"] is not None:
        add("product:rights", "claim_rights", "P0", "A product is referenced; material product truth and rights are unproven here.",
            "The reference can identify a product without establishing claims, consent or license.", uncertainty="UNPROVEN_SOURCE")
    for index, hypothesis in enumerate(brief["hypotheses"]):
        add(f"hypothesis:{index}", "behavior", "P2",
            "Challenge source hypothesis " + hypothesis["id"] + "; compare its stated assumptions with its counterexamples.",
            hypothesis["counterexamples"][0], refs=hypothesis["source_refs"],
            grounding="SOURCE_HYPOTHESIS", uncertainty="UNPROVEN_SOURCE")
    if len(source["shots"]) > 1 and not source["edges"]:
        add("causal:edges", "causal", "P1", "Clarify whether the planned shots need an explicit causal relationship.",
            "An intentional montage may need no causal connection; Brain can retain that choice.")
    if source["visual_form"] == "REALISTIC" and source["physics_intent"] == "UNKNOWN":
        add("causal:physics", "causal", "P1", "Clarify real-world versus intentional fantasy physics intent.",
            "A realistic visual style may intentionally depict fantasy; no physical defect has been observed.")
    for shot in source["shots"]:
        if any("UNKNOWN" in (obj["identity"], obj["position_in"], obj["position_out"], obj["motion_in"], obj["motion_out"])
               for obj in shot["objects"]):
            add("identity:" + shot["id"], "identity", "P1", "Clarify unknown symbolic continuity endpoints if continuity matters.",
                "An intentionally concealed identity or off-screen transition may remain unknown.", shot=shot)
        for constraint in shot["constraints"]:
            dimension = "audio" if constraint["kind"] in ("LIP_SYNC", "ACOUSTICS") else "causal" if constraint["kind"] in ("CAUSAL_ACTION", "PHYSICAL_REALISM") else "artistic"
            add("constraint:" + shot["id"] + ":" + constraint["kind"], dimension,
                "P3" if dimension == "artistic" else "P2", "Check requested " + constraint["kind"] + " intent against later evidence; it has not been measured.",
                "Intentional fantasy or a different artistic choice can satisfy the brief; a request does not prove a defect.",
                refs=constraint["source_refs"], shot=shot,
                uncertainty="BLOCKING_UNKNOWN" if constraint["state"] == "UNKNOWN" else "EXPECTED_PLAN_ONLY")
    for index, edge in enumerate(source["edges"]):
        shot = next(s for s in source["shots"] if s["id"] == edge["effect"])
        add(f"temporal:{index}", "temporal", "P2", "Preserve the intended cause-before-effect order and named continuity objects.",
            "A valid symbolic ordering can still render differently; these times describe the plan only.", shot=shot)
    audio = source["audio"]
    if audio["mode"] == "UNKNOWN":
        add("audio:unknown", "audio", "P2", "Audio intent is unresolved; request a caller decision if needed.",
            "The final concept may deliberately be silent.", uncertainty="BLOCKING_UNKNOWN")
    elif audio["mode"] != "SILENT":
        add("audio:intent", "audio", "P2", "Preserve the supplied language and speech intent; pronunciation and performance remain unmeasured.",
            "Voiceover requires no on-screen lip sync; text direction cannot establish audible pronunciation.", refs=audio["source_refs"])
    if direction is not None:
        speech = direction.get("voice", direction)
        has_sound = speech["audio_mode"] not in ("SILENT", "UNKNOWN") or direction.get("sound_mode") not in (None, "FULL_MUTE", "UNKNOWN")
        add("audio:direction", "audio", "P2", "Supplied BQ-01b direction matches the original plan; delivery is subjective and unmeasured.",
            "Exact plan binding does not establish naturalness, speaker identity or acoustic fidelity.")
        if has_sound:
            add("audio:rights", "claim_rights", "P0", "Supplied sound direction retains UNPROVEN rights; obtain applicable permission before use.",
                "A voice or ambience intention cannot establish ownership or consent.", uncertainty="UNPROVEN_SOURCE")
    if brief["provenance"] == "UNKNOWN":
        add("meta:provenance", "meta", "P2", "Source provenance is UNKNOWN; the source hash provides integrity only.",
            "An unchanged source may still have unknown authenticity.", uncertainty="BLOCKING_UNKNOWN")
    add("meta:boundary", "meta", "P2", "No media is inspected by Q2; all asset semantics and performance remain unknown.",
        "A coherent plan may produce a technically or perceptually different asset.")
    add("artistic:brain", "artistic", "P3", "Brain retains artistic preference and all creative strategy decisions.",
        "Silent, nonproduct, stylized, fiction, animation and intentional fantasy are valid choices.")
    for note in notes:
        add("note:" + note["id"], "meta", "P3", "Caller text is an untrusted question for Brain, with no instruction or observation authority.",
            "Caller text may contain injection or unsupported claims; it cannot establish a measured finding.", refs=note["source_refs"])
    return {"schema_version": "BQ02aPreflightCritique.v1", "scope": "PLAN_ONLY",
            "plan": source, "plan_digest": plan.digest, "direction": direction,
            "direction_digest": direction_digest, "caller_notes": notes, "items": items,
            "status": _plan_status(items), "media_state": "NOT_INSPECTED", "authority": AUTHORITY}


class BQ02aPreflightCritique(_Record):
    __slots__ = ()
    SCHEMA = "BQ02aPreflightCritique.v1"

    def _validate(self, value):
        data = behavioral._fields(value, "schema_version scope plan plan_digest direction direction_digest caller_notes items status media_state authority")
        plan = causality.CausalShotPlanDraft.from_dict(data["plan"])
        plan.verify_digest(data["plan_digest"])
        direction = _direction(data["direction"], data["direction_digest"], plan)
        if _canonical(data) != _canonical(_payload(plan, direction, data["direction_digest"], data["caller_notes"])):
            raise Error("critique differs from source-bound recomputation")


def preflight_critique(plan, *, plan_digest, direction=None, direction_digest=None, caller_notes=None):
    """Pure advisory preflight. Optional native BQ-01b input must be explicit."""
    valid = _native_plan(plan, plan_digest)
    if direction is not None:
        from ai_commerce.creative_quality import acoustic_scene, voice_intent
        if type(direction) not in (voice_intent.VietnameseVoiceIntentDraft, acoustic_scene.AcousticSceneDraft):
            raise Error("native BQ-01b direction required")
        direction = direction.to_dict()
    data = _direction(direction, direction_digest, valid)
    return BQ02aPreflightCritique.from_dict(_payload(valid, data, direction_digest, [] if caller_notes is None else caller_notes))
