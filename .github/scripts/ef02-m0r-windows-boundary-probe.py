"""One-time, owner-only Windows EF02 boundary proof; NO provider/model calls.

Real GitHub claim/pre-spawn and Git worktree. A private inert Python child
stands in for EF01, followed by controlled SystemExit at communicate().
Neither self-hosted runner nor provider is interrupted.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from ai_commerce.remote.transport import GitHubTransport, RemoteTransportError
from ai_commerce.remote.worker import LocalBatchWorker

TITLE = "[M0R] Windows EF02 after-spawn UNKNOWN probe"

def validate_context(env, event):
    repo, owner = env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_REPOSITORY_OWNER", "")
    sha, run = env.get("GITHUB_SHA", ""), env.get("GITHUB_RUN_ID", "")
    if (env.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or env.get("RUNNER_OS") != "Windows"
        or event.get("repository", {}).get("full_name") != repo
        or env.get("GITHUB_ACTOR") != owner
        or event.get("sender", {}).get("login") != owner
        or env.get("GITHUB_REF") != "refs/heads/main"
        or event.get("ref") != "refs/heads/main"
        or not owner or not repo.startswith(owner + "/")
        or re.fullmatch("[0-9a-f]{40}", sha) is None
        or not run.isdecimal() or int(run) < 1):
        raise ValueError("M0R_WINDOWS_PROBE_GUARD_REJECTED")
    return repo, sha, run

def run_probe(env, event, checkout):
    repo, source, run = validate_context(env, event)
    attempt = "m0r-win-afterspawn-" + run
    action_id = "m0r-afterspawn"
    action = {"action_id": action_id, "attempt_id": attempt, "executor": "codex",
              "model": "NO_MODEL_PROBE", "effort": "none",
              "instruction": "TEST ONLY: never start a model",
              "allowed_paths": ["tests/m0r-probe-never-created.py"],
              "verification_profile": "probe-never-run", "depends_on": []}
    root = checkout / ".local" / "ef02-m0r" / ("win-afterspawn-" + run)
    transport = GitHubTransport(repo, env.get("GITHUB_TOKEN", ""))
    worker = LocalBatchWorker(
        checkout=checkout,
        batch={"batch_id": "m0r-afterspawn-" + run, "source_sha": source},
        profiles={"probe-never-run": ["python", "-m", "pytest", "tests/test_ef02.py", "-q"]},
        github=transport, root=root)
    original_popen = subprocess.Popen
    child = None
    worktree = None
    starts = 0
    evidence = root / (action_id + "-" + attempt)

    class InterruptedChild:
        def __init__(self, owned):
            self.pid = owned.pid
        def communicate(self, timeout=None):
            raise SystemExit("M0R_INJECTED_AFTER_CHILD_PID")

    def protected_popen(argv, *args, **kwargs):
        nonlocal child, worktree, starts
        if (isinstance(argv, (tuple, list)) and len(argv) >= 4 and
                list(argv[1:3]) == ["-m", "ai_commerce.engineering"]):
            starts += 1
            if starts != 1:
                raise RuntimeError("MODEL_PATH_REEXECUTION_REFUSED")
            worktree = Path(argv[argv.index("--repo") + 1])
            child_env = {k: env.get(k, "") for k in ("PATH", "SYSTEMROOT", "WINDIR")}
            # This is an inert Python sleeper, NOT the supplied EF01 argv.
            child = original_popen([sys.executable, "-c", "import time; time.sleep(90)"],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env=child_env)
            return InterruptedChild(child)
        return original_popen(argv, *args, **kwargs)

    subprocess.Popen = protected_popen
    try:
        try:
            worker.execute_action(action)
        except SystemExit as exc:
            if str(exc) != "M0R_INJECTED_AFTER_CHILD_PID":
                raise
        else:
            raise RuntimeError("EXPECTED_WORKER_INTERRUPTION_NOT_OBSERVED")
        def record(name):
            return json.loads((evidence / name).read_text(encoding="utf-8"))
        before = record("invocation_boundary.json")
        remote = record("invocation_remote_confirmed.json")
        spawned = record("invocation_spawned.json")
        remote_spawned = record("invocation_spawned_remote_confirmed.json")
        if (remote_spawned.get("stage") != "SPAWNED_REMOTE_REF_CONFIRMED"
            or remote_spawned.get("process_id") != spawned.get("process_id")
            or remote_spawned.get("provider_invocation_proven") is not False):
            raise RuntimeError("REMOTE_SPAWN_JOURNAL_UNVERIFIED")
        if ((evidence / "action_result.json").exists() or child is None
            or spawned.get("process_id") != child.pid or starts != 1
            or before.get("status") != "EXECUTION_UNKNOWN"
            or remote.get("stage") != "PRESPAWN_REMOTE_REF_CONFIRMED"
            or spawned.get("stage") != "EF01_CHILD_PID_OBSERVED"
            or spawned.get("provider_invocation_proven") is not False):
            raise RuntimeError("UNKNOWN_JOURNAL_NOT_PRESERVED")

        key = transport.attempt_key(attempt)
        claim_ref = transport.api("GET", "/git/ref/heads/ef02/claims/" + key)
        claim_sha = claim_ref["object"]["sha"]
        claim_commit = transport.api("GET", "/git/commits/" + claim_sha)
        pre_ref = transport.api("GET", "/git/ref/heads/ef02/pre-spawn/" + key)
        pre_commit = transport.api("GET", "/git/commits/" + pre_ref["object"]["sha"])
        fp = before["fingerprint"]
        spawned_ref = transport.api("GET", "/git/ref/heads/ef02/spawned/" + key)
        spawned_commit = transport.api(
            "GET", "/git/commits/" + spawned_ref["object"]["sha"])
        if (claim_commit["message"] != "EF02 CLAIM\n\nfingerprint=" + fp + "\nattempt_id=" + attempt
            or [x["sha"] for x in claim_commit["parents"]] != [source]
            or pre_commit["message"] != "EF02 PRESPAWN\n\nfingerprint=" + fp + "\nattempt_id=" + attempt
            or [x["sha"] for x in pre_commit["parents"]] != [claim_sha]
            or pre_commit["tree"]["sha"] != claim_commit["tree"]["sha"]
            or remote.get("pre_spawn_remote_ref") != "refs/heads/ef02/pre-spawn/" + key
            or spawned_commit["message"] !=
                "EF02 SPAWNED\n\nfingerprint=" + fp + "\nattempt_id=" + attempt +
                "\nchild_pid=" + str(child.pid)
            or [x["sha"] for x in spawned_commit["parents"]] !=
                [pre_ref["object"]["sha"]]
            or spawned_commit["tree"]["sha"] != pre_commit["tree"]["sha"]
            or remote_spawned.get("spawned_remote_ref") !=
                "refs/heads/ef02/spawned/" + key):
            raise RuntimeError("REMOTE_REF_LINEAGE_UNVERIFIED")
        try:
            transport.api("GET", "/git/ref/heads/ef02/candidate/" + key)
        except RemoteTransportError as exc:
            if str(exc) != "GITHUB_HTTP_404":
                raise
        else:
            raise RuntimeError("UNEXPECTED_CANDIDATE")
        repeat = worker.execute_action(action)
        if (repeat["status"] != "DUPLICATE_CLAIM_NO_REEXECUTION"
            or repeat["executor_invoked"] is not False or starts != 1):
            raise RuntimeError("CLAIM_DEDUP_DID_NOT_STOP_REPLAY")
        result = {"status": "EXECUTION_UNKNOWN", "probe": "M0R_WIN_AFTERSPAWN_NO_MODEL",
                  "source_sha": source, "attempt_id": attempt,
                  "claim": "CLAIM_REF_VERIFIED",
                  "pre_spawn": "PRESPAWN_REF_VERIFIED_NOT_PROVIDER_PROOF",
                  "spawned": "SPAWNED_PID_REF_VERIFIED_NOT_PROVIDER_PROOF",
                  "pid_observed": True, "first_receipt_absent": True,
                  "repeat": repeat["status"], "candidate_created": False,
                  "provider_invoked": False, "model_invoked": False,
                  "runner_crashed": False, "replay_authorized": False}
        dest = root / "probe_result.json"
        dest.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result
    finally:
        subprocess.Popen = original_popen
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
        if worktree is not None and worktree.name.startswith("ai-commerce-ef02-" + action_id + "-"):
            subprocess.run(["git", "-C", str(checkout), "worktree", "remove",
                            "--force", str(worktree)], capture_output=True, check=False)

def main():
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        result = run_probe(os.environ, event, Path.cwd())
    except Exception as exc:
        print("M0R_WINDOWS_BOUNDARY_NONPASS=" + type(exc).__name__, file=sys.stderr)
        return 1
    print("M0R_WINDOWS_BOUNDARY_PASS=" + json.dumps(result, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
