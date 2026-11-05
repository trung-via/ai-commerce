"""Private descriptive synthetic statistics, never a calibrated quality verdict."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math

from ai_commerce.creative_quality import blind_trials as bt, rater_receipts as rr

Z95 = 1.959963984540054


def wilson95(successes, n):
    """Binary Wilson score interval, z=1.959963984540054, six decimal output.

    Ties, abstentions and missing ratings are excluded by the caller. The
    unadjusted binomial calculation does not establish independent sampling.
    """
    bt.integer(n, 0, bt.MAX_ASSIGNMENTS)
    bt.integer(successes, 0, n)
    if n == 0:
        return {"status": "UNDEFINED", "reason": "NO_BINARY_NON_TIED_PREFERENCES", "n": 0, "low": None, "high": None}
    p, z2 = successes / n, Z95 ** 2
    center = (p + z2 / (2 * n)) / (1 + z2 / n)
    margin = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return {"status": "DESCRIPTIVE_UNADJUSTED", "reason": "LOW_N_NO_CONFIDENCE_CLAIM" if n < 30 else "NO_INDEPENDENCE_OR_CLUSTER_ADJUSTMENT",
            "n": n, "low": round(max(0., center - margin), 6), "high": round(min(1., center + margin), 6)}


def _rate(top, n):
    return None if n == 0 else round(top / n, 6)


def _agreement(pair_counts):
    # Canonical A, B, TIE categories, with ABSTAIN/missing excluded. Fleiss'
    # conventional equal-ratings-per-item assumptions are checked explicitly.
    sizes = [sum(c.values()) for c in pair_counts]
    comparable = [c for c, n in zip(pair_counts, sizes) if n >= 2]
    agreements = [sum(v * (v - 1) for v in c.values()) / (sum(c.values()) * (sum(c.values()) - 1)) for c in comparable]
    reasons = []
    if not sizes or any(n < 2 for n in sizes):
        reasons.append("INSUFFICIENT_RATINGS_PER_PAIR")
    if len(set(sizes)) > 1:
        reasons.append("UNEQUAL_RATINGS_PER_PAIR")
    kappa = None
    if not reasons:
        total = sum(sizes)
        totals = Counter()
        for c in pair_counts:
            totals.update(c)
        expected = sum((totals[k] / total) ** 2 for k in ("A", "B", "TIE"))
        if expected == 1:
            reasons.append("ALL_ONE_CLASS_ZERO_CHANCE_DENOMINATOR")
        else:
            kappa = round((sum(agreements) / len(agreements) - expected) / (1 - expected), 6)
    mean = None if not agreements else sum(agreements) / len(agreements)
    return {"method": "FLEISS_KAPPA_EQUAL_N_CANONICAL_A_B_TIE", "category_policy": "ABSTAIN_AND_MISSING_EXCLUDED_TIE_IS_A_CATEGORY",
        "n_comparable_pairs": len(comparable), "available_pair_agreement": None if mean is None else round(mean, 6),
        "available_pair_disagreement": None if mean is None else round(1 - mean, 6),
        "chance_adjusted_status": "UNDEFINED" if reasons else "DESCRIPTIVE_DEFINED", "kappa": kappa,
        "undefined_reasons": reasons, "assumptions": "EQUAL_N_ONLY_NOT_PROOF_OF_INDEPENDENT_RATERS_OR_ITEMS"}


def _payload(plan, judgments):
    data = plan.to_dict()
    entries = {e["task_id"]: e for e in judgments.to_dict()["entries"]}
    pair_results, pair_counts = [], []
    for pair in data["pairs"]:
        tasks = [a for a in data["assignments"] if a["pair_id"] == pair["pair_id"]]
        raw, canonical, arm_results = Counter(), Counter(), {}
        for arm in ("A/B", "B/A"):
            arm_tasks = [t for t in tasks if t["arm"] == arm]
            ac, cc = Counter(), Counter()
            for task in arm_tasks:
                entry = entries.get(task["task_id"])
                if entry is None:
                    continue  # Missing is absent, never a score/response of zero.
                choice = entry["choice"]
                ac[choice] += 1
                canonical_choice = choice
                if choice in ("LEFT", "RIGHT"):
                    canonical_choice = "A" if task[choice] == pair["A"] else "B"
                cc[canonical_choice] += 1
            raw.update(ac)
            canonical.update(cc)
            binary = ac["LEFT"] + ac["RIGHT"]
            arm_results[arm] = {"n_eligible": len(arm_tasks), "n_received": sum(ac.values()),
                "n_missing": len(arm_tasks) - sum(ac.values()), "n_usable": binary + ac["TIE"],
                "n_abstain": ac["ABSTAIN"], "n_binary": binary,
                "counts": {k: ac[k] for k in rr.CHOICES}, "canonical_preferences": {k: cc[k] for k in ("A", "B", "TIE")},
                "left_preference_rate": _rate(ac["LEFT"], binary), "A_preference_rate": _rate(cc["A"], binary)}
        ab, ba = arm_results["A/B"], arm_results["B/A"]
        both = ab["n_binary"] > 0 and ba["n_binary"] > 0
        association = {"label": "DESCRIPTIVE_POSITION_ASSOCIATION_NOT_CAUSAL",
            "status": "AVAILABLE" if both else "UNDEFINED", "reason": "RATER_PAIR_ORDER_AND_SELECTION_CONFOUNDS" if both else "BOTH_SWAP_ARMS_NEED_BINARY_RESPONSES",
            "left_rate_AB_minus_BA": round(ab["counts"]["LEFT"] / ab["n_binary"] - ba["counts"]["LEFT"] / ba["n_binary"], 6) if both else None,
            "A_rate_AB_minus_BA": round(ab["canonical_preferences"]["A"] / ab["n_binary"] - ba["canonical_preferences"]["A"] / ba["n_binary"], 6) if both else None,
            "attribution": "NO_MODEL_BIAS_OR_CAUSAL_EFFECT_ESTABLISHED"}
        usable = canonical["A"] + canonical["B"] + canonical["TIE"]
        counts = Counter({k: canonical[k] for k in ("A", "B", "TIE")})
        pair_counts.append(counts)
        pair_results.append({"pair_id": pair["pair_id"], "canonical_asset_references": {"A": pair["A"], "B": pair["B"]},
            "counts": {k: raw[k] for k in rr.CHOICES}, "canonical_preferences": dict(counts),
            "n_eligible": len(tasks), "n_received": sum(raw.values()), "n_missing": len(tasks) - sum(raw.values()),
            "n_usable": usable, "n_abstain": raw["ABSTAIN"], "n_binary": canonical["A"] + canonical["B"],
            "arms": arm_results, "position_association": association,
            "A_binary_wilson95": wilson95(canonical["A"], canonical["A"] + canonical["B"]),
            "pair_agreement": None if usable < 2 else round(sum(c * (c - 1) for c in counts.values()) / (usable * (usable - 1)), 6)})
    return {"schema_version": BQ03aSyntheticCalibrationReport.SCHEMA, "visibility": "PRIVATE_ANALYSIS_CONTAINS_ASSET_REFERENCES",
        "plan_digest": plan.digest, "judgment_digest": judgments.digest, "source_kind": "SYNTHETIC_SIMULATION_NOT_HUMAN",
        "observed_human_count": 0, "human_receipt": "NONE", "rater_consent": "NOT_ESTABLISHED",
        "production_quality_status": "UNKNOWN", "human_calibration": "NOT_RUN", "BQ03_human_calibration": "NOT_RUN",
        "live_assessment": "NOT_RUN", "copyright_rights": "UNKNOWN", "suggestion_route": "BRAIN_ONLY",
        "decision": "NO_AUTOMATIC_PASS_VETO_OR_CALIBRATED_VERDICT", "pairs": pair_results,
        "agreement": _agreement(pair_counts),
        "uncertainty": ["WILSON_95_Z_1.959963984540054_SIX_DECIMALS", "TIES_ABSTAINS_MISSING_EXCLUDED_FROM_BINARY_INTERVALS",
            "NO_INDEPENDENCE_OR_CLUSTER_ADJUSTMENT", "SMALL_N_DESCRIPTIVE_NO_CONFIDENCE_OR_PRECISION_CLAIM",
            "REPEATED_SLOTS_ACROSS_PAIRS_ARE_CLUSTERED", "NO_RANDOMIZED_EXPOSURE_OR_PARTICIPANT_SESSIONS"],
        "interpretation": "PREFERENCE_IS_NOT_CORRECTNESS_COMMERCIAL_TRUTH_HUMAN_NATURALNESS_RIGHTS_CTR_OR_ROI",
        "counterexamples": ["Always choosing LEFT can cancel canonical asset preference under swapped assignments.",
            "Always preferring asset A changes LEFT rate across arms without establishing a position effect.",
            "Fictional impossible motion can be intentional; perceptual preference cannot prove factual truth."]}


@dataclass(frozen=True, slots=True, init=False)
class BQ03aSyntheticCalibrationReport(bt._Record):
    SCHEMA = "BQ03aSyntheticCalibrationReport.v1"

    @classmethod
    def from_dict(cls, value, *, plan, judgments, plan_digest, judgment_digest, **source):
        expected = synthetic_report(plan, judgments, plan_digest=plan_digest, judgment_digest=judgment_digest, **source)
        if bt.canonical(value) != expected.to_json():
            raise bt.Error("report differs from independent exact source replay")
        return expected

    @classmethod
    def from_json(cls, raw, **kwargs):
        return cls.from_dict(bt.read(raw), **kwargs)


def synthetic_report(plan, judgments, *, plan_digest, judgment_digest, **source):
    plan = bt.validate_plan(plan, **source)
    plan.verify_digest(plan_digest)
    judgments = rr.validate_judgments(judgments, plan=plan, plan_digest=plan_digest,
                                      judgment_digest=judgment_digest, **source)
    return bt.bind(BQ03aSyntheticCalibrationReport, _payload(plan, judgments))
