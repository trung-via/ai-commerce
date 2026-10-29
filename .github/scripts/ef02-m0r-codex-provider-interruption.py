"""One-shot Codex 6.1 Sol max provider interruption, scoped to THIS job.

EF02 GitHub claim, pre-spawn and spawned refs are REAL. A real Codex CLI is
launched against a disposable worktree, and its own process tree is killed
only after a streamed JSON command_execution event proves a model tool call.

This is NOT a LocalBatchWorker/EF01 end-to-end run or runner-service crash.
An interrupted provider's external side effects remain EXECUTION_UNKNOWN.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ai_commerce.engineering.adapters import command_for
from ai_commerce.engineering.profile import ExecutorSelection
from ai_commerce.remote.transport import GitHubTransport, RemoteTransportError

TITLE = "[M0R] Codex 6.1 Sol max live provider interruption"
EXECUTOR = "codex"
MODEL = "gpt-6.1-sol"
EFFORT = "max"
MAX_EVENT_SECONDS = 180
PROBE_KIND = "M0R_CODEX_61_MAX_PROVIDER_OS_INTERRUPT"
PROMPT = (
    "This is an authorized isolated AI Commerce EF02 interruption test. "
    "Do not edit files, create commits, fetch data, or invoke another model. "
    "As your first action, use the shell/terminal tool to run this harmless "
    "long-running command in the current isolated worktree: "
    'python -c "import time; time.sleep(120)" . '
    "Do not give a final answer before the command finishes."
)


def validate_context(env: dict, event: dict) -> tuple[str, str, str]:
    repo = env.get("GITHUB_REPOSITORY", "")
    owner = env.get("GITHUB_REPOSITORY_OWNER", "")
    source = env.get("GITHUB_SHA", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    if (env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or event.get("repository", {}).get("full_name") != repo
            or env.get("GITHUB_ACTOR") != owner
            or event.get("sender", {}).get("login") != owner
            or env.get("GITHUB_REF") != "refs/heads/main"
            or event.get("ref") != "refs/heads/main"
            or env.get("RUNNER_OS") != "Windows"
            or not owner or not repo.startswith(owner + "/")
            or not re.fullmatch(r"[0-9a-f]{40}", source)
            or not run_id.isdecimal() or int(run_id) <= 0):
        raise ValueError("M0R_PROVIDER_PROBE_GUARD_REJECTED")
    return repo, source, run_id


def provider_tool_event(event: object) -> bool:
    """Require an emitted Codex command_execution item, not just a CLI PID."""
    return (
        isinstance(event, dict) and event.get("type") == "item.started"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "command_execution"
        and isinstance(event["item"].get("command"), str)
        and bool(event["item"]["command"].strip())
    )


def terminate_owned_codex(proc: subprocess.Popen) -> dict:
    """Never target the GitHub runner, service or a PID from external input."""
    if proc.poll() is not None:
        return {"termination_confirmed": False,
                "reason": "CODEX_EXITED_BEFORE_TERMINATION"}
    # taskkill /T targets descendants of only this Popen-owned Codex PID.
    result = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            capture_output=True, text=True, timeout=20,
                            check=False)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        return {"termination_confirmed": False,
                "reason": "OWNED_CODEX_STILL_RUNNING"}
    return {"termination_confirmed": result.returncode == 0 and proc.returncode != 0,
            "taskkill_exit_code": result.returncode,
            "codex_exit_code": proc.returncode}


def _consume(stream, destination: Path, lines: queue.Queue | None) -> None:
    with destination.open("w", encoding="utf-8") as out:
        for line in stream:
            out.write(line)
            out.flush()
            if lines is not None:
                lines.put(line)
    if lines is not None:
        lines.put(None)


def _read_ref_chain(github: GitHubTransport, source: str,
                    attempt: str, fp: str, pid: int) -> dict:
    key = github.attempt_key(attempt)
    shas = [
        github.api("GET", "/git/ref/heads/ef02/" + name + "/" + key)["object"]["sha"]
        for name in ("claims", "pre-spawn", "spawned")
    ]
    commits = [github.api("GET", "/git/commits/" + sha) for sha in shas]
    expected = [
        "EF02 CLAIM\n\nfingerprint=" + fp + "\nattempt_id=" + attempt,
        "EF02 PRESPAWN\n\nfingerprint=" + fp + "\nattempt_id=" + attempt,
        "EF02 SPAWNED\n\nfingerprint=" + fp + "\nattempt_id=" + attempt +
        "\nchild_pid=" + str(pid),
    ]
    if ([c.get("message") for c in commits] != expected
            or [p.get("sha") for p in commits[0].get("parents", [])] != [source]
            or [p.get("sha") for p in commits[1].get("parents", [])] != [shas[0]]
            or [p.get("sha") for p in commits[2].get("parents", [])] != [shas[1]]
            or len({c.get("tree", {}).get("sha") for c in commits}) != 1):
        raise RuntimeError("M0R_PROVIDER_REF_CHAIN_UNVERIFIED")
    try:
        github.api("GET", "/git/ref/heads/ef02/candidate/" + key)
    except RemoteTransportError as exc:
        if str(exc) != "GITHUB_HTTP_404":
            raise
    else:
        raise RuntimeError("M0R_PROVIDER_CANDIDATE_UNEXPECTED")
    return {"claim_sha": shas[0], "pre_spawn_sha": shas[1],
            "spawned_sha": shas[2], "candidate_ref_present": False}


def run_probe(env: dict, event: dict, checkout: Path) -> dict:
    repo, source, run_id = validate_context(env, event)
    attempt = "m0r-codex61-max-provider-" + run_id
    root = checkout / ".local" / "ef02-m0r" / ("codex-provider-" + run_id)
    root.mkdir(parents=True, exist_ok=False)
    github = GitHubTransport(repo, env.get("GITHUB_TOKEN", ""))
    fp = hashlib.sha256(json.dumps({
        "kind": PROBE_KIND, "repo": repo, "source_sha": source,
        "run_id": run_id, "attempt_id": attempt,
        "executor": EXECUTOR, "model": MODEL, "effort": EFFORT
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    result = {"probe": PROBE_KIND, "source_sha": source, "attempt_id": attempt,
              "executor": EXECUTOR, "model": MODEL, "effort": EFFORT,
              "status": "EXECUTION_UNKNOWN", "provider_effects_reconciled": False,
              "replay_authorized": False, "candidate_created": False,
              "runner_service_interrupted": False, "local_batch_worker_used": False,
              "source_changes_published": False, "scope": "PROVIDER_CLI_ONLY_NOT_EF01_E2E"}
    worktree = None
    proc = None
    stdout_thread = None
    stderr_thread = None
    killed = None
    try:
        claim = github.claim(source_sha=source, attempt_id=attempt, fingerprint=fp)
        result["claim"] = claim
        if claim != "CLAIMED":
            raise RuntimeError("M0R_PROVIDER_CLAIM_NOT_FRESH")
        checkpoint = github.pre_spawn_checkpoint(
            source_sha=source, attempt_id=attempt, fingerprint=fp)
        result["pre_spawn_ref"] = checkpoint

        # The entire provider workspace is detached and disposable. No changes
        # can be published: this probe never calls candidate().
        worktree = Path(tempfile.mkdtemp(prefix="ai-commerce-m0r-codex-"))
        worktree.rmdir()
        added = subprocess.run(["git", "-C", str(checkout), "worktree", "add",
                                "--detach", str(worktree), source],
                               capture_output=True, text=True, check=False)
        if added.returncode != 0:
            raise RuntimeError("M0R_PROVIDER_WORKTREE_ADD_FAILED")
        selection = ExecutorSelection(executor=EXECUTOR, model=MODEL, effort=EFFORT)
        argv, stdin = command_for(selection, worktree, PROMPT,
                                  codex_windows_sandbox="mxc")
        if not argv or not isinstance(stdin, str) or argv[0] != "codex":
            raise RuntimeError("M0R_PROVIDER_SELECTION_MISMATCH")

        # Never supply GitHub or Actions tokens to the Codex subprocess.
        child_env = dict(env)
        for name in list(child_env):
            if name in ("GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN",
                        "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL") or (
                        name.startswith("EF02_") and "TOKEN" in name):
                child_env.pop(name, None)
        proc = subprocess.Popen(
            argv, cwd=worktree, env=child_env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        result["owned_codex_pid"] = proc.pid
        (root / "provider_process.json").write_text(
            json.dumps({"attempt_id": attempt, "pid": proc.pid,
                        "executor": EXECUTOR, "model": MODEL, "effort": EFFORT,
                        "provider_invocation_proven": False,
                        "replay_authorized": False}, sort_keys=True) + "\n", encoding="utf-8")
        # A real subprocess PID is the only claim made by this remote boundary.
        github.spawned_checkpoint(source_sha=source, attempt_id=attempt,
                                  fingerprint=fp, child_pid=proc.pid)
        result["remote_spawned_checkpoint"] = "CONFIRMED_NOT_PROVIDER_PROOF"

        stream_queue = queue.Queue()
        stdout_thread = threading.Thread(target=_consume, args=(
            proc.stdout, root / "codex.events.jsonl", stream_queue), daemon=True)
        stderr_thread = threading.Thread(target=_consume, args=(
            proc.stderr, root / "codex.stderr.log", None), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        proc.stdin.write(stdin)
        proc.stdin.close()

        saw_turn = False
        saw_tool = False
        command_item = None
        deadline = time.monotonic() + MAX_EVENT_SECONDS
        while time.monotonic() < deadline:
            try:
                line = stream_queue.get(timeout=0.3)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            if line is None:
                break
            try:
                event_data = json.loads(line)
            except (ValueError, TypeError):
                continue
            if event_data.get("type") == "turn.started":
                saw_turn = True
            if provider_tool_event(event_data):
                saw_tool = True
                command_item = event_data["item"].get("id")
                # The observed model-emitted tool call is a stronger boundary
                # than CLI launch or a generic turn.started event.
                if proc.poll() is not None:
                    break
                killed = terminate_owned_codex(proc)
                break
        result["codex_turn_started_observed"] = saw_turn
        result["provider_tool_event_observed"] = saw_tool
        result["command_item_id"] = command_item
        result["owned_process_termination"] = killed
        if not saw_tool or not killed or not killed["termination_confirmed"]:
            raise RuntimeError("M0R_PROVIDER_INTERRUPTION_NOT_PROVEN")
        result["remote"] = _read_ref_chain(github, source, attempt, fp, proc.pid)
        # Claim is immutable: an identical invocation can never be authorized.
        again = github.claim(source_sha=source, attempt_id=attempt, fingerprint=fp)
        result["duplicate_claim"] = again
        if again != "DUPLICATE_CLAIM_NO_REEXECUTION":
            raise RuntimeError("M0R_PROVIDER_CLAIM_REPLAY_NOT_BLOCKED")
        result["interruption_proven"] = True
        return result
    finally:
        # Even on provider failure: NEVER leave a job-owned Codex process alive.
        if proc is not None and proc.poll() is None:
            cleanup = terminate_owned_codex(proc)
            result["cleanup_termination"] = cleanup
        if stdout_thread is not None:
            stdout_thread.join(timeout=4)
        if stderr_thread is not None:
            stderr_thread.join(timeout=4)
        if worktree is not None:
            if proc is None or proc.poll() is not None:
                subprocess.run(["git", "-C", str(checkout), "worktree", "remove",
                                "--force", str(worktree)], capture_output=True,
                               check=False, text=True)
        (root / "probe_result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")


def main() -> int:
    try:
        env = dict(os.environ)
        event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        outcome = run_probe(env, event, Path.cwd())
    except Exception as exc:
        print("M0R_CODEX_LIVE_NONPASS=" + type(exc).__name__, file=sys.stderr)
        return 1
    print("M0R_CODEX_LIVE_PASS=" + json.dumps(outcome, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
