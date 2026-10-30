"""EF02 validation: JSON Schema, real issuer authorization, DAG and path semantics."""
from __future__ import annotations
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from jsonschema import Draft202012Validator
from ai_commerce.engineering.profile import ExecutorSelection
from ai_commerce.engineering.runner import validate_max_repair_rounds, DEFAULT_MAX_REPAIR_ROUNDS
from ai_commerce.engineering.git import git_argv
from ai_commerce.engineering.pytest_profile import registered_workers

IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
HEX_SHA = re.compile(r"^[0-9a-f]{40}$")

class RemoteRequestError(ValueError):
    pass

def canonical(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")

def fingerprint(data: dict) -> str:
    return hashlib.sha256(canonical(data)).hexdigest()

def safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise RemoteRequestError("INVALID_PATH")
    if PurePosixPath(value).is_absolute() or value.startswith("./") or ":" in value or any(
        p in ("", ".", "..") for p in value.split("/")
    ):
        raise RemoteRequestError("INVALID_PATH")
    if value.split("/")[0].lower() in (".git", ".github", ".local"):
        raise RemoteRequestError("RESTRICTED_PATH")
    return value

def verify_registry(path: Path) -> dict[str, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not data:
        raise RemoteRequestError("EMPTY_VERIFICATION_REGISTRY")
    for name, argv in data.items():
        if not IDENTITY.fullmatch(name) or not isinstance(argv, list) or not argv or not all(
            isinstance(arg, str) and arg and "\x00" not in arg for arg in argv
        ):
            raise RemoteRequestError("INVALID_VERIFICATION_PROFILE")
        if argv[:3] not in (["python", "-m", "pytest"], ["py", "-m", "pytest"]):
            raise RemoteRequestError("ONLY_REGISTERED_PYTEST_PROFILES_SUPPORTED")
        if any(".." in arg or arg.startswith("-c") for arg in argv[3:]):
            raise RemoteRequestError("INVALID_VERIFICATION_ARGUMENTS")
        try:
            registered_workers(argv)
        except ValueError as exc:
            raise RemoteRequestError("INVALID_VERIFICATION_WORKERS") from exc
    return data

def validate_batch(data: dict, *, schema: Path, registry: dict[str, list[str]],
                   issuer_login: str, owner_login: str,
                   brain_issuers: tuple[str, ...] = (),
                   approved_reuse: dict | None = None) -> dict:
    Draft202012Validator(json.loads(schema.read_text(encoding="utf-8"))).validate(data)
    owner = issuer_login.casefold() == owner_login.casefold()
    brain = issuer_login.casefold() in {v.casefold() for v in brain_issuers if v}
    if not ((data["actor"] == "HUMAN" and owner) or
            (data["actor"] == "BRAIN" and brain)):
        raise RemoteRequestError("ISSUER_NOT_AUTHORIZED_FOR_ACTOR")
    return _validate_actions(data, registry=registry, approved_reuse=approved_reuse)


def validate_batch_content(data: dict, *, schema: Path, registry: dict[str, list[str]],
                           approved_reuse: dict | None = None) -> dict:
    """Offline content checks only; never establishes issuer authorization.

    Dispatch consumers must continue to use validate_batch with a trusted issuer.
    """
    Draft202012Validator(json.loads(schema.read_text(encoding="utf-8"))).validate(data)
    return _validate_actions(data, registry=registry, approved_reuse=approved_reuse)


def _validate_actions(data: dict, *, registry: dict[str, list[str]],
                      approved_reuse: dict | None) -> dict:
    ids = [a["action_id"] for a in data["actions"]]
    attempts = [a["attempt_id"] for a in data["actions"]]
    if len(set(ids)) != len(ids) or len(set(attempts)) != len(attempts):
        raise RemoteRequestError("DUPLICATE_ACTION_OR_ATTEMPT")
    known = set(ids)
    graph: dict[str, list[str]] = {}
    for action in data["actions"]:
        ExecutorSelection(action["executor"], action["model"], action["effort"])
        try:
            validate_max_repair_rounds(action.get("max_repair_rounds", DEFAULT_MAX_REPAIR_ROUNDS))
        except ValueError as exc:
            raise RemoteRequestError("INVALID_MAX_REPAIR_ROUNDS") from exc
        if action.get("reuse_candidate") is not None:
            if approved_reuse is None:
                raise RemoteRequestError("REUSE_REGISTRY_REQUIRED")
            from .reuse import approve_reuse
            approve_reuse(data, action, approved_reuse)
        for path in action["allowed_paths"]:
            safe_path(path)
        if action["verification_profile"] not in registry:
            raise RemoteRequestError("VERIFICATION_PROFILE_UNREGISTERED")
        deps = action["depends_on"]
        if action["action_id"] in deps or any(d not in known for d in deps):
            raise RemoteRequestError("DEPENDENCY_UNKNOWN_OR_SELF")
        graph[action["action_id"]] = deps
    seen, visiting = set(), set()
    def dfs(node: str) -> None:
        if node in visiting:
            raise RemoteRequestError("DEPENDENCY_CYCLE")
        if node in seen:
            return
        visiting.add(node)
        for parent in graph[node]:
            dfs(parent)
        visiting.remove(node)
        seen.add(node)
    for node in graph:
        dfs(node)
    return data

def approved_verification_plan(action: dict, registry: dict[str, list[str]]) -> dict:
    """Until a companion registration lands, unknown MT-01 scope uses full pytest.

    Select an existing registry entry verbatim; no action controls generated argv.
    RemoteActionBatch v1 and its fingerprints remain unchanged.
    """
    requested = action["verification_profile"]
    if requested not in registry:
        raise RemoteRequestError("VERIFICATION_PROFILE_UNREGISTERED")
    # Honor an explicitly requested full-suite profile, including the
    # registered serial variant. A narrow profile alone cannot prove all
    # affected ACs until MT-01 sees the executor's changed-path closure.
    # Narrow registered groups can still be selected by MT-01 when proven.
    def is_full_suite(argv: list[str]) -> bool:
        return (argv[:3] in (["python", "-m", "pytest"], ["py", "-m", "pytest"]) and
                argv[3:] in (["tests", "-q"], ["tests", "-q", "-n", "12"]))

    explicit = registry[requested]
    explicit_full = is_full_suite(explicit)
    broad = registry.get("project-tests", [])
    broad_valid = is_full_suite(broad)
    selected = explicit if explicit_full else broad if broad_valid else explicit
    proven = explicit_full or broad_valid
    effective_profile = requested if explicit_full or not broad_valid else "project-tests"
    return {"state": "EXECUTE" if proven else "UNPROVEN", "requested_profile": requested,
            "profile": effective_profile, "argv": list(selected),
            "minimum_test": proven, "registered_only": True,
            "coverage": "FULL_REGISTERED_SUITE" if proven else "BROAD_PROFILE_UNAVAILABLE",
            "workers_requested": registered_workers(selected) if proven else 12,
            "registered_workers": registered_workers(selected) if proven else None,
            "reason": ("EXPLICIT_REGISTERED_FULL_SUITE" if explicit_full
                       else "MT01_SELECTOR_REGISTRATION_REQUIRES_COMPANION_PR"),
            "aggregate_requires_fresh_execution": bool(action.get("depends_on"))}


def check_source(repo: Path, source_sha: str, *, git_executable: str | None = None) -> None:
    if not HEX_SHA.fullmatch(source_sha):
        raise RemoteRequestError("INVALID_SOURCE_SHA")
    proc = subprocess.run(git_argv(repo, "cat-file", "-t", source_sha, executable=git_executable),
                          capture_output=True, text=True, check=False)
    if proc.returncode or proc.stdout.strip() != "commit":
        raise RemoteRequestError("SOURCE_COMMIT_UNAVAILABLE")

def parse_issue_body(body: str) -> dict:
    text = body.strip()
    if text.startswith("~~~json") and text.endswith("~~~"):
        text = text[7:-3].strip()
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RemoteRequestError("ISSUE_MUST_CONTAIN_JSON_BATCH") from exc
    if not isinstance(doc, dict):
        raise RemoteRequestError("BATCH_MUST_BE_OBJECT")
    return doc
