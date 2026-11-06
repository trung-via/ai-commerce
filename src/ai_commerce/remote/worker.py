"""EF02 Windows-ready worker: isolated EF01 subprocesses and durable GitHub results."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .transport import GitHubTransport, RemoteTransportError, CandidateIntegrityError, WorkOwner
from .validation import fingerprint, check_source, approved_verification_plan, approved_acceptance_plan
from .reuse import load_approved_candidates, approve_reuse
from .candidate_policy import (
    REVIEW_STATUS, VERIFIED_STATUSES, candidate_accepted, evidence_digest, validate_candidate_evidence,
)
from ai_commerce.engineering.runner import (
    DEFAULT_MAX_REPAIR_ROUNDS, DEFAULT_ACTION_TIMEOUT_SECONDS,
    validate_max_repair_rounds, _snapshot, _provider_problem, _outside_scope,
    owner_round_receipt,
)
from ai_commerce.engineering.git import git_argv, resolve_git_executable
from ai_commerce.engineering.git_byte_consistency import (GitByteConsistencyError, checkout_diagnostics as _checkout_diagnostics)
from ai_commerce.engineering.minimum_test import Evidence, file_hashes
from ai_commerce.engineering.profile import diagnose_failure
from ai_commerce.engineering.source_safety import checked_path, protected_change, ordinary_scope_drift


def checkout_diagnostics(repo: Path, *, refresh_index=False, git_executable=None) -> dict:
    """Keep source identity inside the existing native checkout boundary."""
    details = _checkout_diagnostics(repo, refresh_index=refresh_index, git_executable=git_executable)
    if details["clean"]:
        head = subprocess.run(git_argv(repo, "rev-parse", "HEAD", executable=git_executable),
            capture_output=True, text=True, encoding="utf-8", errors="strict", check=False, timeout=30)
        if head.returncode or not re.fullmatch(r"[0-9a-f]{40}", head.stdout.strip()):
            raise GitByteConsistencyError("GIT_SOURCE_IDENTITY_UNPROVEN")
        details["git_head_after"] = head.stdout.strip()
    return details


def _verified_receipt_evidence(report: dict, *, root: Path, workspace: Path,
                               source_sha: str, sources: list[str], profiles: dict,
                               allowed_paths: list[str] | None = None,
                               diagnostics: list[str] | None = None) -> bool:
    """Check actual artifacts and registered PASS bindings, not receipt assertions.

    Emit bounded condition codes, never on-host paths, raw test output, tokens
    or the exception message. A failure is still a hard publication HOLD.
    """
    def rejected(code: str) -> bool:
        if diagnostics is not None:
            diagnostics.append(code)
        return False

    try:
        refs, hashes = report["evidence_files"], report["evidence_sha256"]
        if (report.get("verify_completed") is not True or
                report.get("provider_return_determinate") is not True or
                set(refs) != set(hashes) or
                not {"tracked_diff", "source_after_verify", "verify_stdout", "provider_return_observed"} <= set(refs)):
            return rejected("RECEIPT_PROVENANCE_INCOMPLETE")
        for key, relative in refs.items():
            if hashlib.sha256(checked_path(root, relative).read_bytes()).hexdigest() != hashes[key]:
                return rejected("RECEIPT_ARTIFACT_HASH_MISMATCH")
        if hashes["tracked_diff"] != report["tracked_diff_sha256"]:
            return rejected("RECEIPT_TRACKED_DIFF_HASH_MISMATCH")
        current = file_hashes(workspace)
        covered = set()
        groups = report["minimum_test"]["groups"]
        if not report.get("minimum_test_enabled") or not groups:
            return rejected("MT01_GROUP_EVIDENCE_MISSING")
        for index, group in enumerate(groups):
            binding = group["binding"]
            registered_name = binding.get("registered_profile")
            onboarding_claim = registered_name == "mt01-component-onboarding"
            # The recorded name and exact argv must BOTH be in the trusted
            # registry. An unrelated alias with identical argv is not proof.
            approved_argv = (
                isinstance(registered_name, str)
                and registered_name != "mt01-component-onboarding"
                and profiles.get(registered_name) == binding.get("argv")
            )
            if onboarding_claim:
                from ai_commerce.engineering.impact_v21 import verified_onboarding_binding
                approved_argv = verified_onboarding_binding(
                    workspace=workspace, binding=binding,
                    changed_paths=report.get("changed_files", []),
                    allowed_paths=allowed_paths or [], base_sha=source_sha,
                    registered_profiles=profiles,
                    expected_group=group.get("group"))
            if not approved_argv:
                return rejected(f"MT01_GROUP_{index}_REGISTERED_BINDING_REJECTED")
            if (binding["base_sha"] != source_sha or binding["aggregate_sources"] != sources or
                    binding["workspace"] != str(workspace.resolve()) or
                    binding["candidate_files"] != {p: current.get(p) for p in binding["candidate_files"]}):
                return rejected(f"MT01_GROUP_{index}_SOURCE_OR_HASH_MISMATCH")
            proof = Evidence(checked_path(root, refs[f"mt01_{index}_evidence"]).read_text(encoding="utf-8"),
                             group["evidence_sha256"])
            if not proof.valid_for(binding, root):
                return rejected(f"MT01_GROUP_{index}_EVIDENCE_INVALID")
            covered.update(binding["candidate_files"])
        if not set(report["changed_files"]) <= covered:
            return rejected("MT01_CHANGED_FILES_UNCOVERED")
        return True
    except (ValueError, KeyError, TypeError, OSError):
        return rejected("RECEIPT_EVIDENCE_UNAVAILABLE")


_verified_ef01_proof = _verified_receipt_evidence


def _fresh_aggregate_evidence(report: dict, *, root: Path, workspace: Path,
                              source_sha: str, sources: list[str], argv: list[str]) -> bool:
    """Validate immutable executed proof against the actual combined worktree."""
    try:
        plan = report.get("minimum_test")
        if (not report.get("minimum_test_enabled") or report.get("verify_argv") != argv or
                not isinstance(plan, dict) or plan.get("aggregate_sources") != sources or
                not isinstance(plan.get("groups"), list) or not plan["groups"]):
            return False
        current = file_hashes(workspace)
        for index, group in enumerate(plan["groups"]):
            if not isinstance(group, dict) or not isinstance(group.get("binding"), dict):
                return False
            binding = group["binding"]
            if (group.get("actual_exit_code") != 0 or group.get("state") not in ("EXECUTE", "INVALIDATED") or
                    binding["base_sha"] != source_sha or binding["aggregate_sources"] != sources or
                    binding["argv"] != argv or binding["workspace"] != str(workspace.resolve()) or
                    set(current) - set(binding["candidate_files"]) or
                    binding["candidate_files"] != {p: current.get(p) for p in binding["candidate_files"]}):
                return False
            relative = report["evidence_files"][f"mt01_{index}_evidence"]
            path = (root / relative).resolve()
            if root.resolve() not in path.parents:
                return False
            proof = Evidence(path.read_text(encoding="utf-8"), group["evidence_sha256"])
            if not proof.valid_for(binding, root):
                return False
        return True
    except (ValueError, KeyError, TypeError, OSError, AttributeError):
        return False

def _remote_codex_sandbox_args(executor: str, *, windows: bool) -> list[str]:
    """EF02 Windows runner overrides only Codex; Antigravity is untouched."""
    return ["--codex-windows-sandbox", "mxc"] if windows and executor == "codex" else []

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
    chunks: list[bytes] = []
    for key, name in (("executor_stderr", "executor.stderr.log"),
                      ("executor_stdout", "executor.stdout.log")):
        path = (evidence / report.get("evidence_files", {}).get(key, name)).resolve()
        if evidence.resolve() not in path.parents:
            continue
        if path.exists():
            with path.open("rb") as stream:
                chunks.append(stream.read(1024 * 1024))
    problem = _provider_problem(b"\n".join(chunks))
    if problem and problem[0] in ("MODEL_AT_CAPACITY", "QUOTA_EXHAUSTED", "MODEL_UNSUPPORTED"):
        return problem[0]
    return "EXECUTOR_FAILED"

class WorkOwnerBroker:
    """Fence requests use existing EF01 stdin/stdout; no network or credentials.

    Only the native EF01 process receives the random capability. The runner
    consumes it before starting provider/verifier children. Requests are never
    copied into logs. Parent loss/EOF denies further rounds.
    """
    PREFIX = "EF02_OWNER_FENCE_V1:"

    def __init__(self, owner):
        self.owner, self.token = owner, secrets.token_hex(32)
        self.child = None
        self.output, self.errors, self.threads = [], [], []

    def fence(self, request):
        stage = request.get("stage")
        if (set(request) != ({"stage", "round", "attempt_id", "receipt", "token"}
                if stage == "ROUND_VERIFIED" else {"stage", "round", "attempt_id", "token"}) or
                stage not in ("PROVIDER_INTENT", "ROUND_VERIFIED") or
                request["attempt_id"] != self.owner.identity["attempt_id"] or
                not isinstance(request["token"], str) or
                not secrets.compare_digest(request["token"], self.token)):
            raise ValueError("WORK_FENCE_REQUEST_INVALID")
        data = {"round": request["round"]}
        if stage == "ROUND_VERIFIED":
            data["receipt"] = request["receipt"]
        self.owner.append(stage, **data)

    def environment(self):
        return {"AI_COMMERCE_EF02_OWNER_REQUIRED": "1",
                "AI_COMMERCE_EF02_OWNER_ENDPOINT": "stdio-v1",
                "AI_COMMERCE_EF02_OWNER_TOKEN": self.token}

    def communicate(self, child, timeout):
        if self.child is None:
            self.child = child
            def stdout():
                while line := child.stdout.readline(2049):
                    if line.startswith(self.PREFIX):
                        try:
                            if len(line) > 2048:
                                raise ValueError("WORK_FENCE_REQUEST_INVALID")
                            self.fence(json.loads(line[len(self.PREFIX):]))
                            response = '{"status":"CONFIRMED"}\n'
                        except Exception:
                            response = '{"status":"UNKNOWN"}\n'
                        try:
                            child.stdin.write(response)
                            child.stdin.flush()
                        except OSError:
                            break
                    else:
                        self.output.append(line)
            def stderr():
                while chunk := child.stderr.read(4096):
                    self.errors.append(chunk)
            self.threads = [threading.Thread(target=reader, daemon=True) for reader in (stdout, stderr)]
            for thread in self.threads:
                thread.start()
        child.wait(timeout=timeout)
        for thread in self.threads:
            thread.join(timeout=5)
        if any(thread.is_alive() for thread in self.threads):
            raise RemoteTransportError("WORK_FENCE_DRAIN_UNKNOWN")
        return "".join(self.output), "".join(self.errors)

    def close(self):
        if self.child is not None:
            try:
                self.child.stdin.close()
            except OSError:
                self.owner.uncertain = True


class LocalBatchWorker:
    def __init__(self, *, checkout: Path, batch: dict, profiles: dict[str, list[str]],
                 github: GitHubTransport, root: Path | None = None, git_executable: str | None = None,
                 issue_number: int = 0):
        self.checkout = checkout.resolve()
        self.batch = batch
        self.profiles = profiles
        self.github = github
        self.issue_number = issue_number
        configured_git = git_executable if git_executable is not None else os.environ.get("AI_COMMERCE_GIT_EXECUTABLE")
        self.git_executable = resolve_git_executable(configured_git) if configured_git is not None else None
        self.root = (root or (self.checkout / ".local" / "ef02" / batch["batch_id"])).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.approved_reuse = load_approved_candidates(
            self.checkout / ".github" / "ef02" / "approved-candidates.v1.json")
        check_source(self.checkout, batch["source_sha"],
                     **({"git_executable": self.git_executable} if self.git_executable is not None else {}))

    def _acquire_owner(self, action):
        identity = WorkOwner.identity(self.github, self.issue_number, self.batch, action)
        return WorkOwner.acquire(self.github, identity)

    def reconcile_action(self, action: dict) -> dict:
        submitted = {k: v for k, v in action.items() if k != "resolved_dependencies"}
        identity = WorkOwner.identity(self.github, self.issue_number, self.batch, submitted)
        return WorkOwner(self.github, identity, "").reconcile()

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
                   "review_required": False, "scope_review_required": False,
                   "depends_on": action["depends_on"],
                   "dependency_semantics": "PINNED_DEPENDENCY_TREE_COMPOSITION",
                   "evidence_path": str(path), "executor_invoked": False}
        owner = broker = None
        def finish():
            nonlocal broker
            if broker is not None:
                broker.close()
                broker = None
            if owner is not None and outcome.get("status") not in ("CANDIDATE_READY", REVIEW_STATUS):
                try:
                    owner.append("UNKNOWN")
                except Exception:
                    pass  # Durable last intent still denies replay; never rollback.
            outcome["replay_authorized"] = False
            diagnosis = diagnose_failure(outcome)
            outcome["failure_diagnosis"] = diagnosis.payload() if diagnosis else None
            _write_json(path / "action_result.json", outcome)
            return outcome
        try:
            repair_limit = validate_max_repair_rounds(
                action.get("max_repair_rounds", DEFAULT_MAX_REPAIR_ROUNDS))
        except ValueError as exc:
            outcome.update(status="INVALID_ACTION", error_code="INVALID_MAX_REPAIR_ROUNDS",
                           diagnostic=str(exc))
            return finish()
        outcome["max_repair_rounds"] = repair_limit
        if action.get("reuse_candidate") is not None:
            if action["depends_on"]:
                outcome.update(status="UNPROVEN", error_code="AGGREGATE_REQUIRES_FRESH_VERIFICATION",
                               aggregate_verification={"state": "UNPROVEN", "sibling_pass_reusable": False})
                return finish()
            try:
                entry = approve_reuse(self.batch, action, self.approved_reuse)
                actual = {d["action_id"]: d.get("candidate_sha")
                          for d in action.get("resolved_dependencies", [])}
                for predecessor in entry["depends_on"]:
                    approved_parent = next((v for v in self.approved_reuse["candidates"].values()
                                            if v["action_id"] == predecessor), None)
                    if approved_parent is None or actual.get(predecessor) != approved_parent["candidate_sha"]:
                        raise RemoteTransportError("REUSE_DEPENDENCY_SHA_MISMATCH")
                self.github.verify_reused_candidate(entry)
                # v1 approvals bind tree/profile/run, but do not bind the current
                # test definitions, dependency environment or MT-01 artifacts.
                # Keep the request readable; conservatively withhold a new PASS.
                outcome.update(status="UNPROVEN", error_code="LEGACY_REUSE_MT01_EVIDENCE_REQUIRED",
                               candidate_sha=entry["candidate_sha"],
                               candidate_ref=entry["candidate_ref"], effective_source_sha=entry["parent_sha"],
                               verify_exit_code=None, reused=True, reused_verified=False,
                               reuse_original_run_id=entry["verified_run_id"],
                               reuse_original_attempt_id=entry["attempt_id"],
                               claim_status="APPROVED_REUSE_NO_NEW_CLAIM",
                               executor_invoked=False, changed_files=entry["allowed_paths"])
            except Exception as exc:
                outcome.update(status="REUSE_VERIFICATION_FAILED",
                               error_code=str(exc)[:160], executor_invoked=False,
                               reused=True, reused_verified=False)
            return finish()
        try:
            owner = self._acquire_owner(submitted_action)
            outcome["work_owner"] = {"work_key": owner.identity["work_key"], "generation": 1,
                "lineage_gap": "V1_HAS_NO_TRUSTED_CROSS_ISSUE_REPAIR_OR_TRANSFER"}
        except Exception as exc:
            status = ("DUPLICATE_CLAIM_NO_REEXECUTION" if str(exc) == "WORK_OWNER_EXISTS_RECONCILE_ONLY"
                      else "HOLD_WORK_OWNER")
            outcome.update(status=status, error_code=str(exc)[:160], replay_authorized=False)
            return finish()
        try:
            owner.assert_current()
            claim = self.github.claim(source_sha=source, attempt_id=attempt_id, fingerprint=fp)
            outcome["claim_status"] = claim
            if claim != "CLAIMED":
                outcome["status"] = claim
                outcome["error_code"] = claim
                return finish()
            owner.append("CLAIMED")
        except Exception as exc:
            outcome.update(status="CLAIM_UNAVAILABLE", error_code=type(exc).__name__,
                           diagnostic=str(exc))
            return finish()
        effective = source
        try:
            if any(not candidate_accepted(d) for d in action.get("resolved_dependencies", [])):
                raise RemoteTransportError("DEPENDENCY_BRAIN_SCOPE_REVIEW_REQUIRED")
            owner.assert_current()
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
                    git_argv(self.checkout, "fetch", "--no-tags", "--no-write-fetch-head", "origin",
                             merged["integration_ref"], executable=getattr(self, "git_executable", None)),
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
            p = subprocess.run(git_argv(self.checkout, "worktree", "add", "--detach", str(worktree), effective,
                                        executable=getattr(self, "git_executable", None)),
                               capture_output=True, text=True, check=False)
            if p.returncode != 0:
                outcome.update(status="SOURCE_WORKTREE_FAILED",
                               error_code="GIT_WORKTREE_ADD_NONZERO", diagnostic=p.stderr)
                return finish()
            # Check immediately after checkout and BEFORE any provider checkpoint.
            # Only refresh Git's stat cache; never modify source files or ignore
            # a genuinely dirty worktree. Preserve exact actionable paths.
            try:
                checkout = checkout_diagnostics(
                    worktree, refresh_index=True,
                    git_executable=getattr(self, "git_executable", None))
            except GitByteConsistencyError as exc:
                outcome.update(status="SOURCE_WORKTREE_FAILED",
                               error_code="GIT_STATUS_FAILED", diagnostic=str(exc),
                               provider_invocation_proven=False)
                return finish()
            outcome["source_checkout"] = checkout
            if not checkout["clean"]:
                outcome.update(
                    status="SOURCE_WORKTREE_FAILED",
                    error_code="SOURCE_CHECKOUT_INCONSISTENT",
                    diagnostic={"reason": "DIRTY_IMMEDIATELY_AFTER_GIT_WORKTREE_ADD",
                                "entries": checkout["dirty_entries"],
                                "entry_count": checkout["dirty_entry_count"],
                                "index_refresh_exit": checkout["index_refresh_exit"]},
                    provider_invocation_proven=False)
                return finish()
            outcome["source_preflight"] = {"expected_source_sha": effective,
                "observed_source_sha": checkout.get("git_head_after"), "proves_pass": False}
            if "git_head_after" in checkout and checkout["git_head_after"] != effective:
                outcome.update(status="SOURCE_WORKTREE_FAILED", error_code="SOURCE_PREFLIGHT_MISMATCH",
                               provider_invocation_proven=False)
                return finish()
            if action.get("verification_intent") == "HUMAN_FULL" and self.batch.get("actor") != "HUMAN":
                outcome.update(status="INVALID_ACTION", error_code="UNAUTHORIZED_HUMAN_FULL")
                return finish()
            plan = approved_verification_plan(action, self.profiles)
            acceptance = approved_acceptance_plan(submitted_action)
            plan["acceptance"] = acceptance
            outcome["mt01_acceptance"] = acceptance
            outcome["verification_plan"] = plan
            dependencies = action.get("resolved_dependencies", [])
            outcome["aggregate_verification"] = {
                "state": "UNPROVEN" if dependencies else "NOT_AGGREGATE",
                "sibling_pass_reusable": False, "effective_source_sha": effective,
                "dependency_candidates": [d.get("candidate_sha") for d in dependencies]}
            _write_json(path / "verification_plan.json", plan)
            if plan["state"] == "UNPROVEN":
                outcome.update(status="UNPROVEN", error_code="APPROVED_BROAD_VERIFICATION_UNAVAILABLE")
                return finish()
            instruction_file = path / "instruction.txt"
            instruction_file.write_text(action["instruction"], encoding="utf-8")
            verify_file = path / "verify.json"
            verify_file.write_text(json.dumps(plan["argv"]),
                                   encoding="utf-8")
            registry_file = path / "verification_registry.json"
            _write_json(registry_file, self.profiles)
            efdir = path / "ef01"
            argv = [sys.executable, "-m", "ai_commerce.engineering",
                    "--repo", str(worktree), "--work-id", self.batch["batch_id"] + ":" + aid,
                    "--attempt-id", attempt_id, "--executor", action["executor"],
                    "--model", action["model"], "--effort", action["effort"],
                    "--instruction-file", str(instruction_file), "--verify-json", str(verify_file),
                    "--out-dir", str(efdir), "--max-repair-rounds", str(repair_limit),
                    "--action-timeout-seconds", str(DEFAULT_ACTION_TIMEOUT_SECONDS)]
            argv.extend(["--minimum-test", "--registered-only", "--host-slots",
                         str(max(int(os.environ.get("EF02_HOST_SLOTS", "2")), self.batch.get("max_parallel", 2)))])
            argv.extend(["--registered-profiles-json", str(registry_file)])
            if plan.get("human_full_suite_authorized"):
                argv.append("--human-full-suite-authorized")
            for acceptance_id in acceptance["ids"]:
                argv.extend(["--acceptance", acceptance_id])
            for dependency in dependencies:
                candidate_sha = dependency.get("candidate_sha")
                if not candidate_sha:
                    outcome.update(status="UNPROVEN", error_code="DEPENDENCY_CANDIDATE_EVIDENCE_MISSING")
                    return finish()
                argv.extend(["--aggregate-source", candidate_sha])
            if getattr(self, "git_executable", None) is not None:
                argv.extend(["--git-bin", self.git_executable])
            for allowed in action["allowed_paths"]:
                argv.extend(["--allow-path", allowed])
            sandbox_argv = _remote_codex_sandbox_args(action["executor"], windows=(os.name == "nt"))
            argv.extend(sandbox_argv)
            outcome["sandbox_backend"] = "mxc" if sandbox_argv else "executor-default"
            # The distributed claim has already been committed. Record an
            # UNKNOWN write-ahead boundary before touching the EF01 child.
            # Parent interruption before Popen and after Popen must NOT become
            # a "model did not run" or retry-safe claim.
            started = time.time()
            progress = {"action_id": aid, "attempt_id": attempt_id,
                        "source_sha": source, "effective_source_sha": effective,
                        "fingerprint": fp, "claim_status": "CLAIMED",
                        "stage": "BEFORE_EF01_CHILD_SPAWN",
                        "status": "EXECUTION_UNKNOWN",
                        "ef01_child_pid_observed": False,
                        "provider_invocation_proven": False,
                        "replay_authorized": False,
                        "recorded_at": started}
            _write_json(path / "invocation_boundary.json", progress)
            # This independent create-only GitHub ref is chained to the exact
            # distributed claim. Remote uncertainty is fail-closed: do NOT
            # spawn EF01 if confirmation was not received, even if the ref
            # may have been created before an API transport timeout.
            try:
                remote_stage = self.github.pre_spawn_checkpoint(
                    source_sha=source, attempt_id=attempt_id, fingerprint=fp)
            except Exception as exc:
                outcome.update(status="PRESPAWN_CHECKPOINT_UNCONFIRMED",
                               error_code="PRESPAWN_CHECKPOINT_UNCONFIRMED",
                               diagnostic=type(exc).__name__,
                               replay_authorized=False)
                return finish()
            outcome["pre_spawn_remote_ref"] = remote_stage
            owner.append("SPAWN_INTENT")
            _write_json(path / "invocation_remote_confirmed.json",
                        {**progress, "stage": "PRESPAWN_REMOTE_REF_CONFIRMED",
                         "pre_spawn_remote_ref": remote_stage,
                         "remote_confirmation_observed_at": time.time()})
            # Never pass GitHub credentials into untrusted model/verification process.
            owner.assert_current()
            broker = WorkOwnerBroker(owner)
            child_env = _safe_child_env(self.checkout)
            child_env.update(broker.environment())
            child = subprocess.Popen(argv, cwd=self.checkout, env=child_env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     encoding="utf-8", errors="replace")
            outcome.update(executor_invoked=True, process_id=child.pid,
                           process_started_at=started)
            try:
                owner.append("CHILD_OBSERVED", pid=child.pid)
            except Exception:
                outcome["work_owner_child_observation"] = "UNKNOWN"
            # This records an EF01 process PID, not proof that EF01 launched or
            # completed a provider invocation. Keep the immutable pre-spawn
            # boundary alongside it for reconciliation after parent termination.
            _write_json(path / "invocation_spawned.json",
                        {**progress, "stage": "EF01_CHILD_PID_OBSERVED",
                         "ef01_child_pid_observed": True, "process_id": child.pid,
                         "process_observed_at": time.time()})
            # A remotely readable PID observation survives loss of the
            # Windows workspace. This is NOT evidence of provider invocation.
            # If GitHub confirmation fails, keep running/collecting the child
            # result; never interpret absence as permission to replay.
            try:
                owner.assert_current()
                spawned_ref = self.github.spawned_checkpoint(
                    source_sha=source, attempt_id=attempt_id,
                    fingerprint=fp, child_pid=child.pid)
                outcome["spawned_remote_ref"] = spawned_ref
                outcome["spawned_remote_checkpoint"] = "CONFIRMED_NOT_PROVIDER_PROOF"
                _write_json(path / "invocation_spawned_remote_confirmed.json",
                            {**progress, "stage": "SPAWNED_REMOTE_REF_CONFIRMED",
                             "ef01_child_pid_observed": True, "process_id": child.pid,
                             "spawned_remote_ref": spawned_ref,
                             "provider_invocation_proven": False,
                             "remote_confirmation_observed_at": time.time()})
            except Exception as exc:
                owner.uncertain = True
                outcome["spawned_remote_checkpoint"] = "READ_UNKNOWN"
                outcome["spawned_remote_error_type"] = type(exc).__name__
            timed_out = False
            try:
                stdout, stderr = broker.communicate(child, timeout=7500)
            except subprocess.TimeoutExpired:
                child.kill()
                stdout, stderr = broker.communicate(child, timeout=30)
                timed_out = True
                outcome.update(status="EXECUTION_UNKNOWN",
                               error_code="EF01_PROCESS_TIMEOUT")
            outcome["process_finished_at"] = time.time()
            outcome["ef01_exit"] = child.returncode
            (path / "ef01-wrapper.stdout").write_text(stdout, encoding="utf-8")
            (path / "ef01-wrapper.stderr").write_text(stderr, encoding="utf-8")
            result_file = efdir / "engineering_result.json"
            if timed_out:
                # A receipt from a killed child can be stale or partially written.
                # Preserve it on disk, but never classify it as verified or publish it.
                outcome["receipt_after_timeout"] = result_file.exists()
                return finish()
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
            outcome["out_of_scope_files"] = report.get("out_of_scope_files", [])
            outcome["scope_review_required"] = report.get("scope_review_required", False)
            outcome["review_required"] = report.get("review_required", False)
            for key in ("scope_valid", "tracked_diff_sha256", "candidate_file_sha256"):
                outcome[key] = report.get(key)
            outcome["verify_exit_code"] = report.get("verify_exit_code")
            outcome["ef01_invocations"] = report.get("invocations", [])
            outcome["invocation_count"] = report.get("invocation_count")
            outcome["repair_rounds_used"] = report.get("repair_rounds_used", 0)
            outcome["repair_stop_reason"] = report.get("stop_reason")
            outcome["ef01_usage"] = report.get("usage")
            outcome["ef01_usage_status"] = report.get("usage_status", "UNKNOWN")
            outcome["pytest_profile"] = report.get("pytest_profile")
            outcome["pytest_profiles"] = report.get("pytest_profiles")
            outcome["observed_pytest_workers"] = report.get("observed_pytest_workers")
            outcome["workers_actual"] = report.get("workers_actual")
            outcome["workers_actual_by_group"] = report.get("workers_actual_by_group")
            outcome["worker_count_provenance"] = report.get("worker_count_provenance")
            outcome["minimum_test"] = report.get("minimum_test")
            outcome["hypothesis_trail"] = report.get("hypothesis_trail", [])[-4:]
            outcome["ef01_failure_diagnosis"] = report.get("failure_diagnosis")
            # Trust neither a success-shaped receipt after a nonzero process
            # exit nor an EF01 PASS receipt contradicted by verification.
            # Both cases can arise when the EF01 process dies after writing
            # its result. Never promote either to a schedulable PASS.
            if child.returncode != 0 and report.get("status") in VERIFIED_STATUSES:
                outcome.update(status="EXECUTION_UNKNOWN",
                               error_code="EF01_EXIT_NONZERO_WITH_SUCCESS_RECEIPT",
                               receipt_status_untrusted=True, replay_authorized=False)
                return finish()
            if (report.get("status") in VERIFIED_STATUSES
                    and report.get("verify_exit_code") != 0):
                outcome.update(status="VERIFICATION_FAILED",
                               error_code="EF01_VERIFY_EXIT_CONTRADICTS_SUCCESS_RECEIPT",
                               receipt_status_untrusted=True)
                return finish()
            if report.get("status") not in VERIFIED_STATUSES or child.returncode:
                classified = _classify_failed_executor(report, efdir)
                if classified in ("CANDIDATE_READY", REVIEW_STATUS, *VERIFIED_STATUSES):
                    # These are not legitimate EF01 failure statuses and
                    # would otherwise be mistaken for a successful action.
                    outcome.update(status="EXECUTION_UNKNOWN",
                                   error_code="EF01_NONPASS_STATUS_INVALID",
                                   receipt_status_untrusted=True, replay_authorized=False)
                    return finish()
                outcome["status"] = classified
                # Surface exact provider reason instead of generic EXECUTOR_NONZERO.
                outcome["error_code"] = (
                    outcome["status"] if outcome["status"] in (
                        "MODEL_AT_CAPACITY", "QUOTA_EXHAUSTED", "MODEL_UNSUPPORTED")
                    else report.get("error_code") or "EF01_NONPASS")
                return finish()
            if report.get("source_base_sha") != effective:
                outcome.update(status="SOURCE_MISMATCH", error_code="EF01_BASE_MISMATCH")
                return finish()
            if dependencies:
                if not _fresh_aggregate_evidence(
                        report, root=efdir, workspace=worktree, source_sha=effective,
                        sources=[d["candidate_sha"] for d in dependencies], argv=plan["argv"]):
                    outcome.update(status="UNPROVEN", error_code="AGGREGATE_FRESH_COVERAGE_UNCONFIRMED")
                    return finish()
                outcome["aggregate_verification"].update(state="VERIFIED_COMBINED_SCOPE",
                                                         profile=plan["profile"], verify_exit_code=0)
            hashes = report.get("candidate_file_sha256")
            changed = report.get("changed_files")
            if (not isinstance(hashes, dict) or not isinstance(changed, list)
                    or not changed or len(set(changed)) != len(changed)
                    or set(hashes) != set(changed)):
                outcome.update(status="CANDIDATE_INTEGRITY_FAILED",
                               error_code="EF01_HASH_MANIFEST_INVALID")
                return finish()
            outside = _outside_scope(changed, action["allowed_paths"])
            review = bool(outside)
            if (report.get("git_head_after") != effective or
                    report.get("scope_valid") is not (not review) or
                    report.get("status") != VERIFIED_STATUSES[int(review)] or
                    report.get("out_of_scope_files", []) != outside or
                    report.get("scope_review_required", False) is not review or
                    report.get("review_required", False) is not review or protected_change(changed) or
                    not ordinary_scope_drift(outside)
                    or report.get("executor_exit_code") != 0 or report.get("denied_actions")):
                outcome.update(status="CANDIDATE_INTEGRITY_FAILED",
                               error_code="EF01_SOURCE_SCOPE_OR_PERMISSION_UNCONFIRMED")
                return finish()
            # Reconcile the exact final source/tree again after the EF01 process
            # exits. Candidate transport separately checks bytes before any write.
            snapshot, _ = _snapshot(worktree, action["allowed_paths"], time.monotonic() + 30,
                                   **({"git_executable": self.git_executable} if getattr(self, "git_executable", None) is not None else {}))
            if (snapshot.get("git_dir") != report.get("git_dir") or
                    snapshot["scope_valid"] is not (not review) or any(
                    snapshot[key] != report.get(key) for key in (
                        "git_head_after", "changed_files", "candidate_file_sha256",
                        "tracked_diff_sha256"))):
                outcome.update(status="CANDIDATE_INTEGRITY_FAILED",
                               error_code="EF01_VERIFIED_TREE_CHANGED")
                return finish()
            # Scope drift requires a fresh registered broad PASS on these bytes.
            # Never convert an old/partial success assertion into publication proof.
            proof_diagnostics: list[str] = []
            proof_confirmed = _verified_receipt_evidence(
                report, root=efdir, workspace=worktree, source_sha=effective,
                sources=[d["candidate_sha"] for d in dependencies], profiles=self.profiles,
                allowed_paths=action["allowed_paths"], diagnostics=proof_diagnostics)
            aggregate_confirmed = (
                not review or (proof_confirmed and _fresh_aggregate_evidence(
                    report, root=efdir, workspace=worktree, source_sha=effective,
                    sources=[d["candidate_sha"] for d in dependencies], argv=plan["argv"])))
            if not proof_confirmed or not aggregate_confirmed:
                outcome.update(
                    status="UNPROVEN", error_code="EF01_VERIFICATION_PROOF_UNCONFIRMED",
                    verification_proof_diagnostics=proof_diagnostics or
                    ["AGGREGATE_FRESH_EVIDENCE_UNCONFIRMED"])
                return finish()
            evidence = validate_candidate_evidence({
                "version": 1, "status": REVIEW_STATUS if review else "CANDIDATE_READY",
                "review_required": review, "scope_valid": not review,
                "out_of_scope_files": outside, "changed_files": changed,
                "allowed_paths": action["allowed_paths"], "candidate_file_sha256": hashes,
                "tracked_diff_sha256": report["tracked_diff_sha256"],
                "source_sha": source, "effective_source_sha": effective,
                "action_id": aid, "attempt_id": attempt_id, "fingerprint": fp,
                "verification": {"status": report["status"], "exit_code": 0,
                                 "profile": plan["profile"], "argv": plan["argv"],
                                 "evidence_sha256": report["evidence_sha256"],
                                 "report_sha256": hashlib.sha256(result_file.read_bytes()).hexdigest()},
                "provenance": {"executor": action["executor"], "model": action["model"],
                               "effort": action["effort"],
                               "dependency_candidates": [d["candidate_sha"] for d in dependencies],
                               "run_id": os.environ.get("GITHUB_RUN_ID"),
                               "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT")},
            })
            outcome.update(candidate_evidence=evidence, candidate_evidence_sha256=evidence_digest(evidence))
            try:
                broker.close()
                broker = None
                chain = owner.assert_current()
                rounds = [r for r in chain if r["stage"] == "ROUND_VERIFIED"]
                invocations = report.get("invocations", [])
                if (not rounds or len(rounds) != len(invocations) or
                        any(inv.get("work_id") != self.batch["batch_id"] + ":" + aid or
                            inv.get("attempt_id") != attempt_id or
                            inv.get("source_base_sha") != effective or
                            inv.get("invocation_id") != f"{attempt_id}:invocation-{index:03d}" or
                            inv.get("repair_round") != index or
                            inv.get("provider_return_determinate") is not True or
                            inv.get("verify_completed") is not True
                            for index, inv in enumerate(invocations)) or
                        any(r["data"]["receipt"] != owner_round_receipt(inv)
                            for r, inv in zip(rounds, invocations))):
                    raise CandidateIntegrityError("WORK_PROVIDER_RECEIPT_MISMATCH")
                owner.append("RESULT_VERIFIED", receipt=evidence_digest(evidence))
                owner.append("PUBLISH_INTENT", receipt=evidence_digest(evidence))
                candidate = self.github.candidate(source_sha=effective, attempt_id=attempt_id,
                                                  workspace=worktree, paths=changed,
                                                  expected_hashes=hashes, evidence=evidence, owner=owner)
                owner.append("PUBLISHED", receipt=evidence_digest(evidence),
                             candidate=WorkOwner.digest(candidate["candidate_sha"]))
            except GitByteConsistencyError as exc:
                outcome.update(status="CANDIDATE_INTEGRITY_FAILED",
                               error_code=str(exc).split(":", 1)[0], diagnostic=str(exc))
                return finish()
            except CandidateIntegrityError as exc:
                outcome.update(status="CANDIDATE_INTEGRITY_FAILED",
                               error_code=str(exc).split(":", 1)[0], diagnostic=str(exc))
                return finish()
            except Exception as exc:
                outcome.update(status="RESULT_PUBLICATION_FAILED",
                               error_code=type(exc).__name__, diagnostic=str(exc))
                return finish()
            outcome.update(status=evidence["status"], candidate_sha=candidate["candidate_sha"],
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
