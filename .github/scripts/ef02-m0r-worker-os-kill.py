"""One-shot real OS termination of only this probe's EF02 worker (NO MODEL).

The GitHub-hosted job and Windows runner service remain alive. The worker
launches one inert Python sleeper INSTEAD of EF01, publishes immutable
claim/pre-spawn/spawned markers, then is terminated by its supervising process.
Not evidence of an actual provider call or self-hosted runner service crash.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from ai_commerce.remote.transport import GitHubTransport, RemoteTransportError
from ai_commerce.remote.worker import LocalBatchWorker

TITLE = "[M0R] Windows EF02 actual worker termination probe"
ERROR = "M0R_WORKER_KILL_GUARD_REJECTED"


def validate_context(env: dict, event: dict) -> tuple[str, str, str]:
    repo = env.get("GITHUB_REPOSITORY", "")
    owner = env.get("GITHUB_REPOSITORY_OWNER", "")
    sha = env.get("GITHUB_SHA", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    if (env.get("GITHUB_EVENT_NAME") != "issues" or env.get("RUNNER_OS") != "Windows"
            or event.get("action") != "opened"
            or event.get("issue", {}).get("title") != TITLE
            or event.get("issue", {}).get("user", {}).get("login") != owner
            or event.get("repository", {}).get("full_name") != repo
            or not owner or not repo.startswith(owner + "/")
            or re.fullmatch(r"[0-9a-f]{40}", sha) is None
            or not run_id.isdecimal() or int(run_id) <= 0):
        raise ValueError(ERROR)
    return repo, sha, run_id


def action_for(run: str) -> dict:
    return {"action_id": "m0r-killed-worker", "attempt_id": "m0r-os-worker-kill-" + run,
            "executor": "codex", "model": "NO_MODEL_INERT_FIXTURE", "effort": "none",
            "instruction": "fixture only; real EF01 must never be launched",
            "allowed_paths": ["tests/m0r-never-written.py"],
            "verification_profile": "m0r-inert", "depends_on": []}


def local_paths(checkout: Path, run: str, action: dict) -> tuple[Path, Path]:
    root = checkout / ".local" / "ef02-m0r" / ("killed-worker-" + run)
    evidence = root / (action["action_id"] + "-" + action["attempt_id"])
    return root, evidence


def worker_mode(env: dict, event: dict, checkout: Path) -> int:
    repo, sha, run = validate_context(env, event)
    action = action_for(run)
    root, evidence = local_paths(checkout, run, action)
    worker = LocalBatchWorker(checkout=checkout,
        batch={"batch_id": "m0r-killed-worker-" + run, "source_sha": sha},
        profiles={"m0r-inert": [sys.executable, "-c", "raise AssertionError('NEVER_VERIFY')"]},
        github=GitHubTransport(repo, env.get("GITHUB_TOKEN", "")), root=root)

    real_popen = subprocess.Popen

    class InertChild:
        def __init__(self, owned: subprocess.Popen):
            self.pid = owned.pid

        def communicate(self, timeout=None):
            marker = evidence / "invocation_spawned_remote_confirmed.json"
            deadline = time.monotonic() + 65
            while not marker.is_file():
                if time.monotonic() > deadline:
                    raise RuntimeError("REMOTE_SPAWNED_MARKER_MISSING")
                time.sleep(0.05)
            record = json.loads(marker.read_text(encoding="utf-8"))
            if (record.get("process_id") != self.pid or
                    record.get("provider_invocation_proven") is not False):
                raise RuntimeError("SPAWNED_MARKER_INVALID")
            ready = root / "worker_ready.json"
            ready.write_text(json.dumps({"worker_pid": os.getpid(),
                                         "child_pid": self.pid,
                                         "stage": "BLOCKING_AFTER_REMOTE_SPAWN"}) + "\n",
                             encoding="utf-8")
            # The supervisor MUST physically terminate this worker PID.
            # A timeout here is a failure; no simulated exception qualifies.
            time.sleep(110)
            raise RuntimeError("WORKER_WAS_NOT_TERMINATED")

    def intercept(argv, *args, **kwargs):
        if (isinstance(argv, (tuple, list)) and len(argv) > 3 and
                list(argv[1:3]) == ["-m", "ai_commerce.engineering"]):
            sleeper_env = {k: v for k, v in env.items()
                           if k.upper() in ("PATH", "SYSTEMROOT", "WINDIR")}
            owned = real_popen([sys.executable, "-c", "import time; time.sleep(115)"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=sleeper_env)
            root.mkdir(parents=True, exist_ok=True)
            (root / "owned_child.json").write_text(
                json.dumps({"child_pid": owned.pid,
                            "worktree": str(argv[argv.index("--repo") + 1])}) + "\n",
                encoding="utf-8")
            return InertChild(owned)
        return real_popen(argv, *args, **kwargs)

    subprocess.Popen = intercept
    try:
        worker.execute_action(action)
    finally:
        subprocess.Popen = real_popen
    raise RuntimeError("EF02_WORKER_RETURNED_WITHOUT_PHYSICAL_TERMINATION")


def inspect_remote(gh: GitHubTransport, source: str, attempt: str,
                   fp: str, child_pid: int) -> dict:
    key = gh.attempt_key(attempt)
    names = ["claims", "pre-spawn", "spawned"]
    ref_shas = [gh.api("GET", "/git/ref/heads/ef02/" + name + "/" + key)
                ["object"]["sha"] for name in names]
    commits = [gh.api("GET", "/git/commits/" + sha) for sha in ref_shas]
    claim, pre, spawned = commits
    expected = [
        "EF02 CLAIM\n\nfingerprint=" + fp + "\nattempt_id=" + attempt,
        "EF02 PRESPAWN\n\nfingerprint=" + fp + "\nattempt_id=" + attempt,
        "EF02 SPAWNED\n\nfingerprint=" + fp + "\nattempt_id=" + attempt +
        "\nchild_pid=" + str(child_pid),
    ]
    if ([c.get("message") for c in commits] != expected or
            [p["sha"] for p in claim.get("parents", [])] != [source] or
            [p["sha"] for p in pre.get("parents", [])] != [ref_shas[0]] or
            [p["sha"] for p in spawned.get("parents", [])] != [ref_shas[1]] or
            len({c.get("tree", {}).get("sha") for c in commits}) != 1):
        raise RuntimeError("KILLED_WORKER_REMOTE_CHAIN_UNVERIFIED")
    try:
        gh.api("GET", "/git/ref/heads/ef02/candidate/" + key)
    except RemoteTransportError as exc:
        if str(exc) != "GITHUB_HTTP_404":
            raise
    else:
        raise RuntimeError("KILLED_WORKER_UNEXPECTED_CANDIDATE")
    return {"claim_sha": ref_shas[0], "pre_spawn_sha": ref_shas[1],
            "spawned_sha": ref_shas[2], "candidate_present": False}


def supervisor_mode(env: dict, event: dict, checkout: Path) -> dict:
    repo, sha, run = validate_context(env, event)
    action = action_for(run)
    root, evidence = local_paths(checkout, run, action)
    gh = GitHubTransport(repo, env.get("GITHUB_TOKEN", ""))
    owned_pid = None
    worktree = None
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--child-worker"],
        cwd=checkout, env=dict(env), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    terminated = False
    try:
        deadline = time.monotonic() + 75
        while not (root / "worker_ready.json").is_file():
            if proc.poll() is not None:
                raise RuntimeError("WORKER_EXITED_EARLY_BEFORE_TERMINATION")
            if time.monotonic() >= deadline:
                raise RuntimeError("WORKER_READY_TIMEOUT")
            time.sleep(0.1)
        ready = json.loads((root / "worker_ready.json").read_text(encoding="utf-8"))
        details = json.loads((root / "owned_child.json").read_text(encoding="utf-8"))
        owned_pid = details.get("child_pid")
        worktree = details.get("worktree")
        if (ready.get("worker_pid") != proc.pid or
                type(owned_pid) is not int or owned_pid <= 0 or
                ready.get("child_pid") != owned_pid or
                ready.get("stage") != "BLOCKING_AFTER_REMOTE_SPAWN"):
            raise RuntimeError("OWNED_PROCESS_IDENTITY_UNVERIFIED")
        # Real Windows TerminateProcess via Python Popen.kill; ONLY our worker.
        proc.kill()
        proc.communicate(timeout=10)
        terminated = proc.returncode != 0
        if not terminated:
            raise RuntimeError("WORKER_OS_TERMINATION_UNCONFIRMED")

        first_result_absent = not (evidence / "action_result.json").exists()
        boundary = json.loads(
            (evidence / "invocation_boundary.json").read_text(encoding="utf-8"))
        spawned = json.loads(
            (evidence / "invocation_spawned_remote_confirmed.json").read_text(encoding="utf-8"))
        if (not first_result_absent or boundary.get("status") != "EXECUTION_UNKNOWN"
                or spawned.get("process_id") != owned_pid
                or spawned.get("provider_invocation_proven") is not False):
            raise RuntimeError("UNKNOWN_AFTER_REAL_WORKER_TERMINATION_UNVERIFIED")
        observed = inspect_remote(gh, sha, action["attempt_id"],
                                  boundary["fingerprint"], owned_pid)

        # Safeguard the duplication test: any unexpected EF01 launch is stopped
        # before the actual executor argv could be invoked.
        real_popen = subprocess.Popen
        def forbid_provider(argv, *args, **kwargs):
            if isinstance(argv, (tuple, list)) and list(argv[1:3]) == [
                    "-m", "ai_commerce.engineering"]:
                raise AssertionError("NO_REAL_EF01_OR_PROVIDER_PERMITTED")
            return real_popen(argv, *args, **kwargs)
        subprocess.Popen = forbid_provider
        try:
            again = LocalBatchWorker(checkout=checkout,
                batch={"batch_id": "m0r-killed-worker-" + run,
                       "source_sha": sha},
                profiles={"m0r-inert": [sys.executable, "-c", "pass"]},
                github=gh, root=root).execute_action(action)
        finally:
            subprocess.Popen = real_popen
        if (again["status"] != "DUPLICATE_CLAIM_NO_REEXECUTION"
                or again.get("executor_invoked") is not False
                or again.get("candidate_sha") is not None):
            raise RuntimeError("KILLED_WORKER_DUPLICATE_REPLAY_NOT_BLOCKED")

        result = {"probe": "M0R_REAL_OS_KILLED_EF02_WORKER_NO_MODEL",
                  "status": "EXECUTION_UNKNOWN",
                  "source_sha": sha, "attempt_id": action["attempt_id"],
                  "worker_pid": proc.pid, "worker_terminated": True,
                  "inert_child_pid": owned_pid, "first_result_absent": True,
                  "claim": "CLAIM_REF_VERIFIED",
                  "pre_spawn": "PRESPAWN_REF_VERIFIED_NOT_PROVIDER_PROOF",
                  "spawned": "SPAWNED_PID_REF_VERIFIED_NOT_PROVIDER_PROOF",
                  "remote": observed, "duplicate": again["status"],
                  "provider_invoked": False, "model_invoked": False,
                  "runner_service_interrupted": False,
                  "candidate_created": False, "replay_authorized": False}
        root.mkdir(parents=True, exist_ok=True)
        (root / "os_kill_result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=10)
        # Only the inert child whose PID THIS job's worker wrote may be killed.
        if owned_pid is None and (root / "owned_child.json").is_file():
            details = json.loads((root / "owned_child.json").read_text(encoding="utf-8"))
            owned_pid, worktree = details.get("child_pid"), details.get("worktree")
        if type(owned_pid) is int and owned_pid > 0 and owned_pid != os.getpid():
            try:
                os.kill(owned_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if (isinstance(worktree, str) and
                Path(worktree).name.startswith("ai-commerce-ef02-" + action["action_id"] + "-")):
            subprocess.run(["git", "-C", str(checkout), "worktree", "remove",
                            "--force", worktree], capture_output=True,
                           check=False, text=True)


def main() -> int:
    try:
        env = dict(os.environ)
        event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        if len(sys.argv) == 2 and sys.argv[1] == "--child-worker":
            return worker_mode(env, event, Path.cwd())
        if len(sys.argv) != 1:
            raise ValueError(ERROR)
        result = supervisor_mode(env, event, Path.cwd())
    except Exception as exc:
        print("M0R_WORKER_KILL_NONPASS=" + type(exc).__name__, file=sys.stderr)
        return 1
    print("M0R_WORKER_KILL_PASS=" + json.dumps(result, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
