"""EF02 Windows-ready worker: isolated EF01 subprocesses and durable GitHub results."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .transport import GitHubTransport, RemoteTransportError
from .validation import fingerprint, check_source

def _safe_child_env(checkout: Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in tuple(env):
        if name in ("GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN",
                    "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL") or (
                    name.startswith("EF02_") and "TOKEN" in name):
            env.pop(name, None)
    env["PYTHONPATH"] = str(checkout / "src")
    return env

def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("x", encoding="utf-8") as output:
        json.dump(obj, output, indent=2, ensure_ascii=False, sort_keys=True)
        output.write("\n")
    temporary.replace(path)

def _classify_failed_executor(report: dict, evidence: Path) -> str:
    if report.get("status") != "EXECUTOR_FAILED":
        return report.get("status", "EXECUTION_UNKNOWN")
    messages = ""
    for name in ("executor.stderr.log", "executor.stdout.log"):
        path = evidence / name
        if path.exists():
            # Preserve full file on disk; classification only needs bounded prefix.
            with path.open("rb") as f:
                messages += f.read(1024 * 1024).decode("utf-8", "replace").lower()
    if any(x in messages for x in ("resource_exhausted", "quota exceeded",
                                    "quota reached", "429 too many requests")):
        return "QUOTA_EXHAUSTED"
    if any(x in messages for x in ("model_not_found", "model is not supported",
                                    "unknown model")):
        return "MODEL_UNSUPPORTED"
    return "EXECUTOR_FAILED"

class LocalBatchWorker:
    def __init__(self, *, checkout: Path, batch: dict, profiles: dict[str, list[str]],
                 github: GitHubTransport, root: Path | None = None):
        self.checkout = checkout.resolve()
        self.batch = batch
        self.profiles = profiles
        self.github = github
        self.root = (root or (self.checkout / ".local" / "ef02" / batch["batch_id"])).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        check_source(self.checkout, batch["source_sha"])

    def execute_action(self, action: dict) -> dict:
        """One independent action; claim precedes any executor invocation."""
        attempt_id = action["attempt_id"]
        aid = action["action_id"]
        path = self.root / (aid + "-" + attempt_id)
        path.mkdir(parents=True, exist_ok=True)
        source = self.batch["source_sha"]
        # Stable fingerprint covers batch/source and exact action profile and scope.
        submitted_action = {k: v for k, v in action.items() if k != "resolved_dependencies"}
        fp = fingerprint({"batch_id": self.batch["batch_id"], "source_sha": source,
                          "action": submitted_action})
        outcome = {"action_id": aid, "attempt_id": attempt_id,
                   "executor": action["executor"], "model": action["model"],
                   "effort": action["effort"], "source_sha": source,
                   "fingerprint": fp, "status": "NOT_STARTED",
                   "error_code": None, "candidate_ref": None, "candidate_sha": None,
                   "depends_on": action["depends_on"],
                   "dependency_semantics": "PINNED_DEPENDENCY_TREE_COMPOSITION",
                   "evidence_path": str(path), "executor_invoked": False}
        def finish():
            _write_json(path / "action_result.json", outcome)
            return outcome
        try:
            claim = self.github.claim(source_sha=source, attempt_id=attempt_id, fingerprint=fp)
            outcome["claim_status"] = claim
            if claim != "CLAIMED":
                outcome["status"] = claim
                outcome["error_code"] = claim
                return finish()
        except Exception as exc:
            outcome.update(status="CLAIM_UNAVAILABLE", error_code=type(exc).__name__,
                           diagnostic=str(exc))
            return finish()
        effective = source
        try:
            merged = self.github.integrated_source(
                source_sha=source, attempt_id=attempt_id,
                dependencies=action.get("resolved_dependencies", []))
            effective = merged["effective_sha"]
            outcome["effective_source_sha"] = effective
            outcome["integration_ref"] = merged["integration_ref"]
            if effective != source:
                # GitHub API created the pinned integration commit: fetch it
                # with credentials scoped to this administrative subprocess.
                token = os.environ.get("GITHUB_TOKEN", "")
                auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
                env = dict(os.environ)
                env["GIT_CONFIG_COUNT"] = "1"
                env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
                env["GIT_CONFIG_VALUE_0"] = "AUTHORIZATION: basic " + auth
                fetched = subprocess.run(
                    ["git", "-C", str(self.checkout), "fetch", "--no-tags",
                     "--no-write-fetch-head", "origin", merged["integration_ref"]],
                    env=env, capture_output=True, text=True, check=False)
                if fetched.returncode:
                    raise RemoteTransportError("INTEGRATION_FETCH_FAILED")
        except Exception as exc:
            outcome.update(status="INTEGRATION_FAILED", error_code=type(exc).__name__,
                           diagnostic=str(exc))
            return finish()
        worktree = Path(tempfile.mkdtemp(prefix="ai-commerce-ef02-" + aid + "-"))
        # Use a new empty directory as parent; git worktree add requires absent target.
        worktree.rmdir()
        outcome["worktree"] = str(worktree)
        try:
            p = subprocess.run(["git", "-C", str(self.checkout), "worktree", "add",
                                "--detach", str(worktree), effective],
                               capture_output=True, text=True, check=False)
            if p.returncode != 0:
                outcome.update(status="SOURCE_WORKTREE_FAILED",
                               error_code="GIT_WORKTREE_ADD_NONZERO", diagnostic=p.stderr)
                return finish()
            instruction_file = path / "instruction.txt"
            instruction_file.write_text(action["instruction"], encoding="utf-8")
            verify_file = path / "verify.json"
            verify_file.write_text(json.dumps(self.profiles[action["verification_profile"]]),
                                   encoding="utf-8")
            efdir = path / "ef01"
            argv = [sys.executable, "-m", "ai_commerce.engineering",
                    "--repo", str(worktree), "--work-id", self.batch["batch_id"] + ":" + aid,
                    "--attempt-id", attempt_id, "--executor", action["executor"],
                    "--model", action["model"], "--effort", action["effort"],
                    "--instruction-file", str(instruction_file), "--verify-json", str(verify_file),
                    "--out-dir", str(efdir)]
            for allowed in action["allowed_paths"]:
                argv.extend(["--allow-path", allowed])
            # Never pass GitHub credentials into untrusted model/verification process.
            started = time.time()
            child = subprocess.Popen(argv, cwd=self.checkout, env=_safe_child_env(self.checkout),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            outcome.update(executor_invoked=True, process_id=child.pid,
                           process_started_at=started)
            try:
                stdout, stderr = child.communicate(timeout=7500)
            except subprocess.TimeoutExpired:
                child.kill()
                stdout, stderr = child.communicate()
                outcome.update(status="EXECUTION_UNKNOWN",
                               error_code="EF01_PROCESS_TIMEOUT")
            outcome["process_finished_at"] = time.time()
            outcome["ef01_exit"] = child.returncode
            (path / "ef01-wrapper.stdout").write_text(stdout, encoding="utf-8")
            (path / "ef01-wrapper.stderr").write_text(stderr, encoding="utf-8")
            result_file = efdir / "engineering_result.json"
            if not result_file.exists():
                outcome.update(status="EXECUTION_UNKNOWN",
                               error_code="EF01_RESULT_MISSING")
                return finish()
            report = json.loads(result_file.read_text(encoding="utf-8"))
            outcome["ef01_status"] = report.get("status")
            outcome["ef01_error_code"] = report.get("error_code")
            outcome["ef01_started_at"] = report.get("started_at")
            outcome["ef01_finished_at"] = report.get("finished_at")
            outcome["changed_files"] = report.get("changed_files", [])
            outcome["verify_exit_code"] = report.get("verify_exit_code")
            if report.get("status") != "BUILD_VERIFIED_FOR_SCOPE" or child.returncode:
                outcome["status"] = _classify_failed_executor(report, efdir)
                outcome["error_code"] = report.get("error_code") or "EF01_NONPASS"
                return finish()
            if report.get("source_base_sha") != effective:
                outcome.update(status="SOURCE_MISMATCH", error_code="EF01_BASE_MISMATCH")
                return finish()
            try:
                candidate = self.github.candidate(source_sha=effective, attempt_id=attempt_id,
                                                  workspace=worktree,
                                                  paths=report["changed_files"])
            except Exception as exc:
                outcome.update(status="RESULT_PUBLICATION_FAILED",
                               error_code=type(exc).__name__, diagnostic=str(exc))
                return finish()
            outcome.update(status="CANDIDATE_READY", candidate_sha=candidate["candidate_sha"],
                           candidate_ref=candidate["candidate_ref"])
            return finish()
        except Exception as exc:
            # A claim now exists. Rerun requires reconciliation, never automatic reinvocation.
            outcome.update(status="EXECUTION_UNKNOWN", error_code=type(exc).__name__,
                           diagnostic=str(exc))
            return finish()
        finally:
            # Keep failing worktrees for evidence; successful are also left for local inspection.
            pass
