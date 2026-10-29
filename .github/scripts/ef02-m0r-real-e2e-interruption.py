"""One-shot M0-R real LocalBatchWorker -> EF01 -> Codex interruption.

Only this test's owned worker process tree may be killed; the self-hosted
Windows runner service, other jobs and historical claimed attempts are untouched.
The marker is a provider-command side effect in a unique disposable worktree.
Neither a marker nor a GitHub ref proves completed/verified provider work.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ai_commerce.remote.transport import GitHubTransport, RemoteTransportError
from ai_commerce.remote.worker import LocalBatchWorker

TITLE = "[M0R] EF02 EF01 Codex 6.1 Sol max streamed E2E interruption"
MODEL = "gpt-6.1-sol"
EFFORT = "max"
MAX_OBSERVE_SECONDS = 200


def validate_context(env: dict, event: dict) -> tuple[str, str, str]:
    repo = env.get("GITHUB_REPOSITORY", "")
    owner = env.get("GITHUB_REPOSITORY_OWNER", "")
    sha = env.get("GITHUB_SHA", "")
    run = env.get("GITHUB_RUN_ID", "")
    if (env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or env.get("RUNNER_OS") != "Windows"
            or event.get("repository", {}).get("full_name") != repo
            or env.get("GITHUB_ACTOR") != owner
            or event.get("sender", {}).get("login") != owner
            or env.get("GITHUB_REF") != "refs/heads/main"
            or event.get("ref") != "refs/heads/main"
            or not owner or not repo.startswith(owner + "/")
            or re.fullmatch(r"[0-9a-f]{40}", sha) is None
            or not run.isdecimal() or int(run) <= 0):
        raise ValueError("M0R_E2E_OWNER_WINDOWS_GUARD_REJECTED")
    return repo, sha, run


def action_for(run: str) -> tuple[dict, str, str]:
    aid = "m0r-e2e-" + run
    attempt = "m0r-e2e-codex61-max-" + run
    relative = "tests/m0r_codex_e2e_" + run + ".txt"
    marker = "M0R_E2E_REAL_CODEX_COMMAND_" + run
    # A single tool command writes the marker then deliberately stays alive.
    # The unique marker is in the job-owned worktree, never canonical main.
    command = ("python -c " + '"' +
               "from pathlib import Path; Path('" + relative +
               "').write_text('" + marker + "', encoding='utf-8'); " +
               "import time; time.sleep(150)" + '"')
    instruction = (
        "Authorized AI Commerce M0-R single-attempt live EF02->EF01->Codex "
        "interruption test. Exactly one executable task: use your shell or "
        "terminal command tool to run the exact command below from the "
        "current worktree, not by editing files directly:\n\n" +
        command + "\n\n"
        "Do not commit, push, browse, install, contact external services, "
        "start other agents, or change any other file. If the terminal command "
        "fails, do NOT retry or switch tools; keep this turn open for 150 seconds "
        "without further commands. The supervising test will terminate only "
        "its own process tree after observing the model-emitted command event."
    )
    return ({
        "action_id": aid, "attempt_id": attempt,
        "executor": "codex", "model": MODEL, "effort": EFFORT,
        "instruction": instruction, "allowed_paths": [relative],
        "verification_profile": "ef02-tests", "depends_on": [],
        "delivery": "CANDIDATE_BRANCH",
    }, relative, marker)


def root_for(checkout: Path, run: str) -> Path:
    return checkout / ".local" / "ef02-m0r" / ("real-e2e-" + run)


def marker_worktree(aid: str, relative: str, marker: str) -> Path | None:
    """Match only the exact unique action prefix and literal expected bytes."""
    base = Path(tempfile.gettempdir())
    prefix = "ai-commerce-ef02-" + aid + "-"
    found = []
    for worktree in base.glob(prefix + "*"):
        path = worktree / relative
        if not path.is_file() or path.is_symlink():
            continue
        if path.stat().st_size > 256:
            continue
        if path.read_text(encoding="utf-8") == marker:
            found.append(worktree)
    if len(found) > 1:
        raise RuntimeError("M0R_E2E_MULTIPLE_MATCHING_WORKTREES")
    return found[0] if found else None




def streamed_codex_command_event(log_path: Path, marker: str) -> dict | None:
    """Model-emitted command_execution event; NOT proof the shell ran successfully."""
    if not log_path.is_file():
        return None
    if log_path.stat().st_size > 4 * 1024 * 1024:
        raise RuntimeError("M0R_E2E_STREAM_TOO_LARGE")
    with log_path.open("r", encoding="utf-8", errors="replace") as stream:
        for raw in stream:
            try:
                record = json.loads(raw)
            except (ValueError, TypeError):
                continue
            item = record.get("item") if isinstance(record, dict) else None
            if (record.get("type") == "item.started"
                    and isinstance(item, dict)
                    and item.get("type") == "command_execution"
                    and isinstance(item.get("command"), str)
                    and marker in item["command"]):
                return {"item_id": item.get("id"),
                        "command_sha256": hashlib.sha256(
                            item["command"].encode("utf-8")).hexdigest(),
                        "event": "MODEL_EMITTED_COMMAND_STARTED_NOT_SHELL_SUCCESS"}
    return None

def inspect_remote(github: GitHubTransport, source: str, attempt: str,
                   fingerprint: str, ef01_pid: int) -> dict:
    key = github.attempt_key(attempt)
    names = ("claims", "pre-spawn", "spawned")
    shas = [github.api("GET", "/git/ref/heads/ef02/" + stage + "/" + key)
            ["object"]["sha"] for stage in names]
    commits = [github.api("GET", "/git/commits/" + sha) for sha in shas]
    expected = [
        "EF02 CLAIM\n\nfingerprint=" + fingerprint + "\nattempt_id=" + attempt,
        "EF02 PRESPAWN\n\nfingerprint=" + fingerprint + "\nattempt_id=" + attempt,
        "EF02 SPAWNED\n\nfingerprint=" + fingerprint + "\nattempt_id=" + attempt +
        "\nchild_pid=" + str(ef01_pid),
    ]
    parents = ([source], [shas[0]], [shas[1]])
    if ([c.get("message") for c in commits] != expected
            or any([p.get("sha") for p in c.get("parents", [])] != list(parent)
                   for c, parent in zip(commits, parents))
            or len({c.get("tree", {}).get("sha") for c in commits}) != 1):
        raise RuntimeError("M0R_E2E_REMOTE_CHECKPOINT_LINEAGE_INVALID")
    try:
        github.api("GET", "/git/ref/heads/ef02/candidate/" + key)
    except RemoteTransportError as exc:
        if str(exc) != "GITHUB_HTTP_404":
            raise
    else:
        raise RuntimeError("M0R_E2E_UNEXPECTED_CANDIDATE")
    return {"claim_sha": shas[0], "pre_spawn_sha": shas[1],
            "spawned_sha": shas[2], "candidate_ref_present": False}


def terminate_owned_worker(proc: subprocess.Popen) -> dict:
    if proc.poll() is not None:
        return {"confirmed": False, "reason": "WORKER_EXITED_BEFORE_TERMINATION"}
    # Windows taskkill /T /F targets only the probe-created worker PID and
    # its descendants (EF01 + Codex), never the GitHub runner service.
    killed = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            capture_output=True, text=True, timeout=25,
                            check=False)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        return {"confirmed": False, "reason": "WORKER_STILL_ALIVE"}
    return {"confirmed": killed.returncode == 0 and proc.returncode != 0,
            "worker_exit_code": proc.returncode,
            "taskkill_exit_code": killed.returncode,
            "taskkill_report": killed.stdout.strip()[:1600]}


def worker_mode(env: dict, event: dict, checkout: Path) -> int:
    repo, source, run = validate_context(env, event)
    action, _, _ = action_for(run)
    root = root_for(checkout, run)
    # Explicitly scoped to this owner-only probe. Real EF01 and selected
    # Codex still run; only their JSONL stdout becomes immediately observable.
    os.environ["AI_COMMERCE_EF01_CODEX_STREAM_EVENTS"] = "1"
    worker = LocalBatchWorker(
        checkout=checkout,
        batch={"batch_id": "m0r-real-e2e-" + run, "source_sha": source},
        profiles={"ef02-tests": ["python", "-m", "pytest", "tests/test_ef02.py", "-q"]},
        github=GitHubTransport(repo, env.get("GITHUB_TOKEN", "")),
        root=root,
    )
    outcome = worker.execute_action(action)
    (root / "unexpected_worker_return.json").write_text(
        json.dumps(outcome, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # The supervisor must terminate us while the model is still working.
    return 3


def supervisor_mode(env: dict, event: dict, checkout: Path) -> dict:
    repo, source, run = validate_context(env, event)
    action, relative, marker = action_for(run)
    aid, attempt = action["action_id"], action["attempt_id"]
    root = root_for(checkout, run)
    root.mkdir(parents=True, exist_ok=False)
    evidence = root / (aid + "-" + attempt)
    github = GitHubTransport(repo, env.get("GITHUB_TOKEN", ""))
    result = {
        "probe": "M0R_REAL_LOCALBATCHWORKER_EF01_CODEX_INTERRUPTION",
        "source_sha": source, "attempt_id": attempt,
        "executor": "codex", "model": MODEL, "effort": EFFORT,
        "status": "EXECUTION_UNKNOWN", "provider_effects_reconciled": False,
        "replay_authorized": False, "candidate_created": False,
        "runner_service_interrupted": False, "scope": "FULL_LOCALBATCHWORKER_EF01_CODEX",
        "worktree_side_effect_not_published": True,
        "event_stream_only_no_shell_success_claim": True,
    }
    owned_worktree = None
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--child-worker"],
        cwd=checkout, env=dict(env), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result["worker_pid"] = proc.pid
    try:
        deadline = time.monotonic() + MAX_OBSERVE_SECONDS
        command_event = None
        log_path = evidence / "ef01" / "executor.stdout.live.jsonl"
        while time.monotonic() < deadline:
            command_event = streamed_codex_command_event(log_path, marker)
            if command_event is not None:
                break
            if proc.poll() is not None:
                raise RuntimeError("M0R_E2E_WORKER_EXITED_BEFORE_STREAMED_EVENT")
            time.sleep(0.08)
        if command_event is None:
            raise RuntimeError("M0R_E2E_MODEL_COMMAND_EVENT_NOT_OBSERVED")
        matches = list(Path(tempfile.gettempdir()).glob(
            "ai-commerce-ef02-" + aid + "-*"))
        if len(matches) != 1 or not matches[0].is_dir():
            raise RuntimeError("M0R_E2E_UNIQUE_WORKTREE_NOT_OBSERVED")
        owned_worktree = matches[0]

        # The normal EF02 worker must have written real GitHub checkpoint
        # confirmation before the provider marker is accepted.
        remote_file = evidence / "invocation_spawned_remote_confirmed.json"
        provider_file = evidence / "ef01" / "provider_boundary.json"
        if not remote_file.is_file() or not provider_file.is_file():
            raise RuntimeError("M0R_E2E_LOCAL_BOUNDARIES_MISSING")
        remote = json.loads(remote_file.read_text(encoding="utf-8"))
        boundary = json.loads(provider_file.read_text(encoding="utf-8"))
        ef01_pid = remote.get("process_id")
        if (type(ef01_pid) is not int or ef01_pid <= 0 or
                remote.get("stage") != "SPAWNED_REMOTE_REF_CONFIRMED" or
                remote.get("provider_invocation_proven") is not False or
                boundary.get("stage") != "BEFORE_EXECUTOR_SUBPROCESS" or
                boundary.get("attempt_id") != attempt):
            raise RuntimeError("M0R_E2E_BOUNDARY_CONTENT_INVALID")
        if ((evidence / "action_result.json").exists()
                or (evidence / "ef01" / "engineering_result.json").exists()
                or (evidence / "ef01" / "provider_return_observed.json").exists()):
            raise RuntimeError("M0R_E2E_COMPLETED_BEFORE_INTERRUPTION")

        progress = json.loads(
            (evidence / "invocation_boundary.json").read_text(encoding="utf-8"))
        observed = inspect_remote(github, source, attempt,
                                  progress["fingerprint"], ef01_pid)
        result.update(
            ef01_pid=ef01_pid, remote=observed,
            ef01_provider_boundary_seen=True,
            codex_command_event_observed=True,
            shell_command_success_proven=False,
            command_event=command_event,
        )
        (root / "provider_tool_event.json").write_text(
            json.dumps(command_event, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        kill = terminate_owned_worker(proc)
        result["owned_process_tree_termination"] = kill
        if not kill["confirmed"]:
            raise RuntimeError("M0R_E2E_WORKER_TREE_TERMINATION_NOT_CONFIRMED")
        if ((evidence / "action_result.json").exists()
                or (evidence / "ef01" / "engineering_result.json").exists()):
            raise RuntimeError("M0R_E2E_UNEXPECTED_FINAL_RECEIPT")
        claim = github.claim(source_sha=source, attempt_id=attempt,
                             fingerprint=progress["fingerprint"])
        result["duplicate_claim"] = claim
        if claim != "DUPLICATE_CLAIM_NO_REEXECUTION":
            raise RuntimeError("M0R_E2E_DUPLICATE_NOT_BLOCKED")
        # All required observations made; interruption does not magically
        # reconcile the interrupted provider's external side effects.
        result["e2e_interruption_proven"] = True
        return result
    finally:
        if proc.poll() is None:
            result["cleanup"] = terminate_owned_worker(proc)
        try:
            out, err = proc.communicate(timeout=5)
            (root / "worker.stdout.log").write_text(out, encoding="utf-8")
            (root / "worker.stderr.log").write_text(err, encoding="utf-8")
        except subprocess.TimeoutExpired:
            result["worker_log_read"] = "TIMEOUT"
        if owned_worktree is None:
            # A failed provider attempt may still have created a worktree.
            matches = list(Path(tempfile.gettempdir()).glob(
                "ai-commerce-ef02-" + aid + "-*"))
            if len(matches) == 1:
                owned_worktree = matches[0]
        if owned_worktree is not None and owned_worktree.name.startswith(
                "ai-commerce-ef02-" + aid + "-"):
            removed = subprocess.run(
                ["git", "-C", str(checkout), "worktree", "remove",
                 "--force", str(owned_worktree)],
                capture_output=True, text=True, check=False)
            result["worktree_cleanup_exit"] = removed.returncode
        (root / "probe_result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")


def main() -> int:
    try:
        env = dict(os.environ)
        event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        if sys.argv[1:] == ["--child-worker"]:
            return worker_mode(env, event, Path.cwd())
        if len(sys.argv) != 1:
            raise ValueError("M0R_E2E_UNKNOWN_MODE")
        result = supervisor_mode(env, event, Path.cwd())
    except Exception as exc:
        print("M0R_E2E_NONPASS=" + type(exc).__name__ + ":" + str(exc)[:240],
              file=sys.stderr)
        return 1
    print("M0R_E2E_PASS=" + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
