"""Synthetic fixture judgments only. No live intake or personal identity ledger."""

from __future__ import annotations

from dataclasses import dataclass
import math

from ai_commerce.creative_quality import blind_trials as bt

CHOICES = ("LEFT", "RIGHT", "TIE", "ABSTAIN")


def _entries(entries, plan):
    bt.canonical(entries)
    tasks = {a["task_id"]: a for a in plan.to_dict()["assignments"]}
    rubric = plan.to_dict()["config"]["rubric"]
    if type(entries) is not list or len(entries) > len(tasks):
        raise bt.Error("bounded synthetic fixture list required")
    seen, result = set(), []
    for entry in entries:
        bt.fields(entry, "synthetic_reviewer_slot task_id choice scores")
        slot, task_id = entry["synthetic_reviewer_slot"], entry["task_id"]
        if (type(slot) is not str or type(task_id) is not str or task_id not in tasks
                or tasks[task_id]["synthetic_reviewer_slot"] != slot):
            raise bt.Error("exact assigned synthetic slot/task required; no participant identifiers")
        if (slot, task_id) in seen:
            raise bt.Error("duplicate reviewer/trial fixture")
        seen.add((slot, task_id))
        choice = bt.option(entry["choice"], CHOICES)
        scores = entry["scores"]
        if type(scores) is not dict or any(d not in rubric for d in scores):
            raise bt.Error("only eligible planned rubric scores are accepted")
        if choice == "ABSTAIN" and scores:
            raise bt.Error("abstention cannot assert dimension scores")
        for score in scores.values():
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 5:
                raise bt.Error("bounded finite synthetic score 0..5 required")
        result.append({"synthetic_reviewer_slot": slot, "task_id": task_id, "choice": choice, "scores": scores.copy()})
    return sorted(result, key=lambda entry: entry["task_id"])


def _payload(plan, entries):
    return {"schema_version": BQ03aSyntheticJudgmentSet.SCHEMA, "SOURCE_KIND": "SYNTHETIC_FIXTURE_ONLY",
        "plan_digest": plan.digest, "entries": _entries(entries, plan), "observed_human_count": 0,
        "rater_consent": "NOT_ESTABLISHED", "human_receipt": "NONE",
        "live_intake": "NOT_IMPLEMENTED", "authority": "FIXTURES_NEVER_AUTHENTICATE_HUMANS"}


@dataclass(frozen=True, slots=True, init=False)
class BQ03aSyntheticJudgmentSet(bt._Record):
    SCHEMA = "BQ03aSyntheticJudgmentSet.v1"

    @classmethod
    def from_dict(cls, value, *, plan, plan_digest, **source):
        plan = bt.validate_plan(plan, **source)
        plan.verify_digest(plan_digest)
        bt.canonical(value)
        if type(value) is not dict or "entries" not in value:
            raise bt.Error("synthetic fixture payload required")
        expected = _payload(plan, value["entries"])
        if bt.canonical(value) != bt.canonical(expected):
            raise bt.Error("synthetic set differs from exact plan-bound replay")
        return bt.bind(cls, expected)

    @classmethod
    def from_json(cls, raw, **kwargs):
        return cls.from_dict(bt.read(raw), **kwargs)


def synthetic_judgments(plan, entries, *, plan_digest, **source):
    plan = bt.validate_plan(plan, **source)
    plan.verify_digest(plan_digest)
    return bt.bind(BQ03aSyntheticJudgmentSet, _payload(plan, entries))


def validate_judgments(judgments, *, plan, plan_digest, judgment_digest, **source):
    if type(judgments) is not BQ03aSyntheticJudgmentSet:
        raise bt.Error("exact native synthetic judgment set required")
    judgments.verify_digest(judgment_digest)
    return BQ03aSyntheticJudgmentSet.from_json(judgments.to_json(), plan=plan, plan_digest=plan_digest, **source)
