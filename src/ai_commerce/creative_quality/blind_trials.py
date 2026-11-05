"""R6 synthetic counterbalanced plans over references, without opening media.

The private record is replay material, not a participant session or an exposure
record. Only public_tasks() is suitable for a blind task transport. There is no
Human intake, consent verifier, assessor, UI, provider or PULSE integration.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from itertools import combinations
import re

from ai_commerce.creative_quality import critic
from ai_commerce.creative_quality import multimodal_review as mm, t3_assessment as t3

Error = critic.Error
MAX_ASSETS = 12
MAX_SLOTS = 16
MAX_ASSIGNMENTS = 1056  # C(12, 2) * 16
DIMENSIONS = ("overall_preference", "visual_continuity", "physical_plausibility",
              "audio_preference", "product_consistency", "logo_legibility")
VISUAL = ("UNSPECIFIED", "LIVE_ACTION", "ANIMATION", "FANTASY", "STYLIZED", "VOICE_ONLY")
AUDIO = ("UNSPECIFIED", "SILENT", "VOICE", "MUSIC")
LOGO = ("UNSPECIFIED", "NO_LOGO", "LOGO_PLANNED")


def canonical(value):
    """Reuse the accepted bounded plain-JSON codec; disallow booleans as well.

    Bounds cover depth, nodes, UTF-8 bytes, cycles, nonfinite and huge numbers.
    No field in these three new closed schemas has boolean semantics.
    """
    raw = critic._canonical(value)

    def visit(item):
        if type(item) is bool:
            raise Error("booleans cannot represent counts, scores or authority")
        if type(item) is dict:
            for child in item.values():
                visit(child)
        elif type(item) is list:
            for child in item:
                visit(child)
    visit(value)
    return raw


def read(raw):
    value = critic._read(raw)
    if canonical(value) != raw:
        raise Error("exact canonical JSON replay required")
    return value


def fields(value, names):
    if type(value) is not dict or set(value) != set(names.split()):
        raise Error("closed record fields required")


def sha(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise Error("lowercase SHA256 reference required")
    return value


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise Error("bounded integer required")
    return value


def option(value, values):
    if type(value) is not str or value not in values:
        raise Error("closed planned option required")
    return value


@dataclass(frozen=True, slots=True, init=False)
class _Record:
    _json: str

    def __init__(self, *args, **kwargs):
        raise Error("use the validated factory or explicit replay")

    def to_json(self):
        return self._json

    def to_bytes(self):
        return self._json.encode("utf-8")

    def to_dict(self):
        return read(self._json)

    @property
    def digest(self):
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def verify_digest(self, expected):
        if self.digest != sha(expected):
            raise Error("exact digest mismatch")


def bind(cls, data):
    result = object.__new__(cls)
    object.__setattr__(result, "_json", canonical(data))
    return result


def _linked(review, digest, assets):
    if review is None:
        if digest is not None:
            raise Error("absent BQ02b review cannot have a digest")
        return {"status": "UNVERIFIED", "review_digest": None, "source_plan_digest": None,
                "preflight_digest": None, "expected_asset_sha256": None, "t3_status": "NOT_RUN"}
    if type(review) is not mm.BQ02bMultimodalReview:
        raise Error("exact native BQ02b review required; detached JSON is not evidence")
    review.verify_digest(digest)
    review._validate(review.to_dict())
    data = review.to_dict()
    assessment = t3.T3AssessmentLedgerlessStatus.from_dict(data["t3_assessment"])
    request = assessment.to_dict()["request"]
    if assessment.to_dict()["status"] != "NOT_RUN" or request["expected_asset_sha256"] not in assets:
        raise Error("linked unrun source must match a planned asset reference")
    return {"status": "NATIVE_SOURCE_REFERENCE_ONLY", "review_digest": review.digest,
            "source_plan_digest": sha(data["plan_digest"]), "preflight_digest": sha(data["preflight_digest"]),
            "expected_asset_sha256": request["expected_asset_sha256"], "t3_status": "NOT_RUN"}


def _config(value):
    fields(value, "asset_sha256s seed reviewer_slots max_pairs product_ref visual_mode audio_mode logo_mode rubric")
    canonical(value)
    assets = value["asset_sha256s"]
    if type(assets) is not list or not 2 <= len(assets) <= MAX_ASSETS:
        raise Error("finite asset reference list required")
    for asset in assets:
        sha(asset)
    if len(set(assets)) != len(assets) or assets != sorted(assets):
        raise Error("unique sorted canonical asset references required")
    sha(value["seed"])
    slots = integer(value["reviewer_slots"], 2, MAX_SLOTS)
    if slots % 2:
        raise Error("even synthetic slots required for equal swap arms")
    integer(value["max_pairs"], 1, len(assets) * (len(assets) - 1) // 2)
    product = value["product_ref"]
    if product is not None:
        sha(product)  # A reference only; arbitrary identifiers/PII are not retained.
    visual = option(value["visual_mode"], VISUAL)
    audio = option(value["audio_mode"], AUDIO)
    logo = option(value["logo_mode"], LOGO)
    eligible = {"overall_preference"}
    if visual not in ("UNSPECIFIED", "VOICE_ONLY"):
        eligible.add("visual_continuity")
        if visual not in ("FANTASY", "ANIMATION"):
            eligible.add("physical_plausibility")
        if product is not None:
            eligible.add("product_consistency")
        if logo == "LOGO_PLANNED":
            eligible.add("logo_legibility")
    if audio in ("VOICE", "MUSIC"):
        eligible.add("audio_preference")
    rubric = value["rubric"]
    if (type(rubric) is not list or not 1 <= len(rubric) <= len(DIMENSIONS)
            or any(type(d) is not str or d not in eligible for d in rubric)
            or len(set(rubric)) != len(rubric) or rubric != sorted(rubric)):
        raise Error("unique eligible planned rubric required; unspecified signals are not facts")
    return value


def _payload(config, linked):
    config = _config(config)
    seed = bytes.fromhex(config["seed"])
    # Domain separation and a complete input commitment prevent task reuse
    # across otherwise different plans. HMAC IDs reveal no reference fragment.
    context = canonical({"config": config, "linked": linked})

    def opaque(label):
        return hmac.new(seed, ("BQ03a/v1/" + context + "/" + label).encode("utf-8"), hashlib.sha256).hexdigest()
    pairs = list(combinations(config["asset_sha256s"], 2))
    pairs.sort(key=lambda pair: opaque("select/" + "/".join(pair)))
    slots = [f"synthetic_slot_{i:02d}" for i in range(config["reviewer_slots"])]
    private_pairs, assignments = [], []
    for a, b in pairs[:config["max_pairs"]]:
        pair_id = "p_" + opaque("pair/" + a + b)[:32]
        private_pairs.append({"pair_id": pair_id, "A": a, "B": b})
        order = sorted(slots, key=lambda slot: opaque(pair_id + "/arm/" + slot))
        for index, slot in enumerate(order):
            arm = "A/B" if index < len(slots) // 2 else "B/A"
            assignments.append({"task_id": "t_" + opaque(pair_id + "/task/" + slot)[:32],
                "pair_id": pair_id, "synthetic_reviewer_slot": slot, "arm": arm,
                "LEFT": a if arm == "A/B" else b, "RIGHT": b if arm == "A/B" else a})
    assignments.sort(key=lambda task: task["task_id"])
    if len(assignments) > MAX_ASSIGNMENTS or len({x["task_id"] for x in assignments}) != len(assignments):
        raise Error("assignment bound or opaque ID collision")
    return {"schema_version": BQ03aBlindPairwisePlan.SCHEMA, "visibility": "PRIVATE_REPLAY_MATERIAL",
        "source_type": "SYNTHETIC_PLAN_ONLY", "asset_seen": "NOT_ESTABLISHED", "copyright_rights": "UNKNOWN",
        "live_assessment": "NOT_RUN", "observed_human_count": 0, "rater_consent": "NOT_ESTABLISHED",
        "human_receipt": "NONE", "protocol": "COUNTERBALANCED_PLAN_NOT_A_BLINDED_STUDY",
        "signal_status": "PLANNED_NOT_INSPECTED", "audio_eligibility": "NOT_APPLICABLE" if config["audio_mode"] == "SILENT" else "PLANNED_ONLY",
        "config": config, "seed_commitment": hashlib.sha256(b"BQ03a/seed/v1/" + seed).hexdigest(),
        "linked_bq02b": linked, "synthetic_slots": slots, "pairs": private_pairs, "assignments": assignments,
        "authority": "BRAIN_SUGGESTION_ONLY_NO_EXTERNAL_EFFECTS"}


@dataclass(frozen=True, slots=True, init=False)
class BQ03aBlindPairwisePlan(_Record):
    SCHEMA = "BQ03aBlindPairwisePlan.v1"

    @classmethod
    def from_dict(cls, value, *, linked_review=None, linked_review_digest=None):
        canonical(value)
        if type(value) is not dict or "config" not in value:
            raise Error("native plan payload required")
        config = _config(value["config"])
        expected = _payload(config, _linked(linked_review, linked_review_digest, config["asset_sha256s"]))
        if canonical(value) != canonical(expected):
            raise Error("plan differs from deterministic canonical replay")
        return bind(cls, expected)

    @classmethod
    def from_json(cls, raw, **kwargs):
        return cls.from_dict(read(raw), **kwargs)

    def public_tasks(self, **source):
        data = validate_plan(self, **source).to_dict()
        # An allowlist projection: no source digest, slots, paths, assets,
        # product, model/provider, seed, pair ID or private assignment survives.
        return {"schema_version": "BQ03aPublicBlindTasks.v1", "source_type": "SYNTHETIC_PLAN_ONLY",
                "tasks": [{"task_id": a["task_id"], "LEFT": "LEFT", "RIGHT": "RIGHT",
                           "rubric": data["config"]["rubric"][:]} for a in data["assignments"]]}


def build_plan(asset_sha256s, *, seed, reviewer_slots=4, max_pairs=None, product_ref=None,
               visual_mode="UNSPECIFIED", audio_mode="UNSPECIFIED", logo_mode="UNSPECIFIED",
               rubric=("overall_preference",), linked_review=None, linked_review_digest=None):
    if type(asset_sha256s) is not list or type(rubric) is not tuple:
        raise Error("plain asset list and finite rubric tuple required")
    canonical(asset_sha256s)
    for asset in asset_sha256s:
        sha(asset)
    canonical(list(rubric))
    if any(type(d) is not str for d in rubric):
        raise Error("rubric dimension tokens required")
    config = {"asset_sha256s": sorted(asset_sha256s), "seed": seed, "reviewer_slots": reviewer_slots,
        "max_pairs": len(asset_sha256s) * (len(asset_sha256s) - 1) // 2 if max_pairs is None else max_pairs,
        "product_ref": product_ref, "visual_mode": visual_mode, "audio_mode": audio_mode,
        "logo_mode": logo_mode, "rubric": sorted(rubric)}
    config = _config(config)
    return bind(BQ03aBlindPairwisePlan, _payload(config, _linked(linked_review, linked_review_digest, config["asset_sha256s"])))


def validate_plan(plan, *, linked_review=None, linked_review_digest=None):
    if type(plan) is not BQ03aBlindPairwisePlan:
        raise Error("exact native synthetic plan required")
    return BQ03aBlindPairwisePlan.from_json(plan.to_json(), linked_review=linked_review,
                                          linked_review_digest=linked_review_digest)
