"""Human-overridable project soft defaults for NEW EF01/EF02 authoring.

This module never edits a claimed EF02 packet or replays an executor. A quota
signal changes *future* selections only; quota recovery is confirmed by a new,
ordinary Codex invocation, not assumed from the passage of time.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .profile import ExecutorSelection, SelectionError

POLICY_PATH = Path("config/engineering/model-defaults.v1.json")
STATE_PATH = Path(".local/engineering/model-defaults-state.v1.json")


@dataclass(frozen=True, slots=True)
class ModelDecision:
    selection: ExecutorSelection
    source: str
    mode: str
    retry_after: str | None = None

    def as_dict(self) -> dict:
        return {"executor": self.selection.executor, "model": self.selection.model,
                "effort": self.selection.effort, "selection_source": self.source,
                "quota_mode": self.mode, "retry_after": self.retry_after}


def _utc(value: datetime | None = None) -> datetime:
    date = value or datetime.now(timezone.utc)
    if date.tzinfo is None:
        raise SelectionError("MODEL_DEFAULTS_TIME_MUST_BE_TZ_AWARE")
    return date.astimezone(timezone.utc)


def _parse(value: str) -> datetime:
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise SelectionError("MODEL_DEFAULTS_INVALID_TIMESTAMP") from exc
    return _utc(date)


def _policy(repo: Path) -> tuple[ExecutorSelection, ExecutorSelection, int]:
    try:
        obj = json.loads((repo / POLICY_PATH).read_text(encoding="utf-8"))
        if obj.get("version") != 1 or obj.get("hard_default") is not False:
            raise SelectionError("MODEL_DEFAULTS_POLICY_UNSUPPORTED")
        primary = ExecutorSelection(**obj["preferred"])
        backup = ExecutorSelection(**obj["fallback"])
        seconds = obj["recovery_probe_cooldown_seconds"]
        if primary.executor != "codex" or backup.executor != "antigravity":
            raise SelectionError("MODEL_DEFAULTS_EXECUTORS_INVALID")
        if type(seconds) is not int or not 60 <= seconds <= 604800:
            raise SelectionError("MODEL_DEFAULTS_COOLDOWN_INVALID")
        return primary, backup, seconds
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SelectionError("MODEL_DEFAULTS_POLICY_INVALID") from exc


def _state(repo: Path) -> dict:
    file = repo / STATE_PATH
    if not file.is_file():
        return {"version": 1, "mode": "PREFERRED"}
    try:
        obj = json.loads(file.read_text(encoding="utf-8"))
        if obj.get("version") != 1 or obj.get("mode") not in ("PREFERRED", "QUOTA_FALLBACK"):
            raise ValueError("invalid quota state")
        if obj["mode"] == "QUOTA_FALLBACK":
            _parse(obj["retry_after"])
        if obj.get("last_attempt_started_at"):
            _parse(obj["last_attempt_started_at"])
        return obj
    except (OSError, ValueError, KeyError, TypeError, SelectionError) as exc:
        raise SelectionError("MODEL_DEFAULTS_STATE_INVALID") from exc


def resolve_model(repo: Path, *, override: ExecutorSelection | None = None,
                  now: datetime | None = None) -> ModelDecision:
    """Select only for a NEW action. Explicit Human choice always wins."""
    if override is not None:
        return ModelDecision(override, "HUMAN_EXPLICIT", "OVERRIDE")
    preferred, fallback, _ = _policy(Path(repo))
    state = _state(Path(repo))
    if state["mode"] == "QUOTA_FALLBACK":
        retry_after = state["retry_after"]
        if _utc(now) < _parse(retry_after):
            return ModelDecision(fallback, "PROJECT_QUOTA_FALLBACK", "QUOTA_FALLBACK",
                                 retry_after)
        return ModelDecision(preferred, "PROJECT_CODEX_RECOVERY_CHECK", "RECOVERY_DUE",
                             retry_after)
    return ModelDecision(preferred, "PROJECT_SOFT_DEFAULT", "PREFERRED")


def _save(repo: Path, state: dict) -> None:
    path = repo / STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tempname = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, prefix=".model-defaults-",
                                         suffix=".tmp", delete=False) as out:
            tempname = out.name
            json.dump(state, out, sort_keys=True, indent=2)
            out.write("\n")
        os.replace(tempname, path)
    finally:
        if tempname is not None and os.path.exists(tempname):
            os.unlink(tempname)


def observe_result(repo: Path, report: dict, *, now: datetime | None = None) -> str:
    """Ingest an actual EF01 return, never arbitrary Issue/provider tool text.

    A conclusive quota failure enables Gemini for *future* authoring only.
    Any real, determinate Codex completion (including subsequent test failure)
    proves the primary provider is available and restores the preference.
    UNKNOWN/timeout/interruption/capacity/permission never flip selection.
    """
    preferred, _, cooldown = _policy(Path(repo))
    if (report.get("executor"), report.get("model"), report.get("reasoning_effort")) != (
            preferred.executor, preferred.model, preferred.effort):
        return "IGNORED_OTHER_SELECTION"
    calls = report.get("invocations")
    if not isinstance(calls, list) or not calls or not isinstance(calls[-1], dict):
        return "IGNORED_UNPROVEN"
    last = calls[-1]
    if not last.get("executor_invoked") or type(last.get("executor_exit_code")) is not int:
        return "IGNORED_UNPROVEN"
    quota = (report.get("status") == "QUOTA_EXHAUSTED" and
             last.get("status") == "QUOTA_EXHAUSTED")
    completed = last.get("provider_return_determinate") is True
    if not quota and not completed:
        return "IGNORED_UNPROVEN"
    started = report.get("started_at")
    if not isinstance(started, str):
        return "IGNORED_NO_ATTEMPT_TIME"
    start = _parse(started)
    old = _state(Path(repo))
    if old.get("last_attempt_started_at") and start < _parse(old["last_attempt_started_at"]):
        return "IGNORED_STALE_ATTEMPT"
    timestamp = _utc(now)
    next_state = {"version": 1, "mode": "QUOTA_FALLBACK" if quota else "PREFERRED",
                  "last_attempt_started_at": start.isoformat(),
                  "last_attempt_id": str(report.get("attempt_id", "")),
                  "observed_at": timestamp.isoformat(),
                  "evidence": "DETERMINATE_EF01_PROVIDER_QUOTA" if quota
                              else "DETERMINATE_CODEX_RETURN"}
    if quota:
        next_state["retry_after"] = (timestamp + timedelta(seconds=cooldown)).isoformat()
    _save(Path(repo), next_state)
    return "QUOTA_FALLBACK_RECORDED" if quota else "CODEX_PREFERENCE_RESTORED"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve a new AI Commerce model selection; no dispatch")
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(resolve_model(args.repo).as_dict(), sort_keys=True))
    except (SelectionError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
