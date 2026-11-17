"""EF02 validation: JSON Schema, real issuer authorization, DAG and path semantics."""
from __future__ import annotations
import hashlib
import os
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from jsonschema import Draft202012Validator
from ai_commerce.engineering.profile import ExecutorSelection
from ai_commerce.engineering.runner import validate_max_repair_rounds, DEFAULT_MAX_REPAIR_ROUNDS
from ai_commerce.engineering.git import git_argv
from ai_commerce.engineering.pytest_profile import registered_workers
from ai_commerce.engineering.minimum_test import approved_groups

IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
HEX_SHA = re.compile(r"^[0-9a-f]{40}$")
DIAGNOSTIC_MODE = "WINDOWS_B03_SERIAL_XDIST_V1"

class RemoteRequestError(ValueError):
    pass


def reject_executor_test_conflict(action: dict) -> None:
    """Finite capability admission plus a deliberately narrow legacy grammar.

    Prose is not a capability grant. Recognize clear executor imperatives, not
    every mention of tests, historical incidents, quotations or prohibitions.
    This is not a general natural-language classifier; the focused executor
    prohibition remains mandatory even for prose outside this grammar.
    """
    policy = action.get("executor_test_policy", "EF01_POST_MODEL_ONLY")
    if policy == "EXECUTOR_PYTEST_REQUIRED":
        raise RemoteRequestError("EXECUTOR_PYTEST_FORBIDDEN_PRESPAWN")
    if policy != "EF01_POST_MODEL_ONLY":
        raise RemoteRequestError("EXECUTOR_TEST_POLICY_INVALID")
    instruction = action.get("instruction", "")
    instruction = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*", "", instruction)
    # Preserve quoted command names, but exclude quoted examples of mandates.
    instruction = re.sub(
        r"(?<!\w)([\"'`])([^\n]*?)\1",
        lambda m: " " if re.search(r"\b(run|execute|invoke|must|shall)\b", m[2], re.I) else m[2],
        instruction)
    subject = r"(?:the\s+)?(?:coding\s+)?(?:executor|model|agent|you)(?:\s+(?:itself|yourself))?"
    verb = r"(?:run|execute|invoke)"
    command = r"(?:`|\"|')?(?:(?:python|py)(?:\.exe)?\s+-m\s+)?(?:pytest|pytest-xdist|xdist)\b"
    mandate = re.compile(
        rf"\b{subject}\s+(?:(?:must|shall|should|needs?\s+to|is\s+required\s+to|are\s+required\s+to)\s+)?"
        rf"(?:itself\s+)?(?:directly\s+)?{verb}\s+{command}", re.I)
    direct = re.compile(rf"^\s*(?:[-*]\s*)?(?:please\s+)?{verb}\s+{command}", re.I)
    for clause in re.split(r"[\n;]+|[.!?](?:\s|$)", instruction):
        if clause.lstrip().startswith(">"):
            continue
        match = mandate.search(clause) or direct.search(clause)
        if not match:
            continue
        prefix = clause[:match.start()]
        # Meta-requirements to reject such requests are not executor mandates.
        if re.search(r"\b(?:reject|deny|forbid|prohibit)\b[^;\n]*\b(?:requests?|mandates?|instructions?|demanding|requiring)\b", prefix, re.I):
            continue
        if re.search(r"\b(?:not|never|no)\s+(?:require|demand|instruct|ask|let|allow)\b[^;\n]*$", prefix, re.I):
            continue
        # Only standalone imperatives or a finite instructional preamble are
        # decisive. Unquoted descriptions of another request are not orders.
        if prefix.strip() and not re.fullmatch(
                r"\s*(?:[-*]\s*)?(?:(?:important|mandatory|requirement|task|verification|tests?)\s*:\s*|"
                r"(?:before (?:returning|delivery)|for verification|then)\s*[:,]?\s*)",
                prefix, re.I):
            continue
        raise RemoteRequestError("EXECUTOR_PYTEST_FORBIDDEN_PRESPAWN")


def is_diagnostic_batch(data: dict) -> bool:
    return any("diagnostic_mode" in a or a.get("delivery") == "DIAGNOSTIC_ONLY"
               for a in data["actions"])


def _validate_diagnostic_shape(data: dict) -> None:
    if data["actor"] != "HUMAN":
        raise RemoteRequestError("DIAGNOSTIC_OWNER_HUMAN_REQUIRED")
    if len(data["actions"]) != 1 or data.get("max_parallel", 1) != 1:
        raise RemoteRequestError("DIAGNOSTIC_SINGLE_ACTION_REQUIRED")
    action = data["actions"][0]
    if (action.get("diagnostic_mode") != DIAGNOSTIC_MODE or
            action.get("delivery") != "DIAGNOSTIC_ONLY" or
            set(action) - {"action_id", "attempt_id", "diagnostic_mode", "delivery", "depends_on", "resources"} or
            action.get("depends_on", []) != [] or action.get("resources", []) != []):
        raise RemoteRequestError("DIAGNOSTIC_SHAPE_INVALID")

# Human-retired WP01..WP06/EPWO must never be dispatched, even from old batch JSON.
# This narrow deny-only control is external to the restored pre-WP Engineering logic.
_RETIRED_PROGRAM_ID = re.compile(r"(?:EPWO|WP0[1-6])", re.IGNORECASE)

def reject_retired_wp_identifiers(batch: dict, action: dict | None = None) -> None:
    identifiers = [batch.get("batch_id")]
    actions = [action] if action is not None else batch.get("actions", [])
    for item in actions:
        if isinstance(item, dict):
            identifiers.extend((item.get("action_id"), item.get("attempt_id")))
    if any(isinstance(v, str) and _RETIRED_PROGRAM_ID.search(v) for v in identifiers):
        raise RemoteRequestError("RETIRED_WP_NOT_DISPATCHABLE")

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
    owner = bool(issuer_login and owner_login) and issuer_login.casefold() == owner_login.casefold()
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
    reject_retired_wp_identifiers(data)
    if is_diagnostic_batch(data):
        _validate_diagnostic_shape(data)
        return data
    ids = [a["action_id"] for a in data["actions"]]
    attempts = [a["attempt_id"] for a in data["actions"]]
    if len(set(ids)) != len(ids) or len(set(attempts)) != len(attempts):
        raise RemoteRequestError("DUPLICATE_ACTION_OR_ATTEMPT")
    known = set(ids)
    graph: dict[str, list[str]] = {}
    for action in data["actions"]:
        reject_executor_test_conflict(action)
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
        if action.get("verification_intent", "AUTO") == "HUMAN_FULL":
            if data["actor"] != "HUMAN" or action["verification_profile"] not in ("project-tests", "project-tests-serial"):
                raise RemoteRequestError("HUMAN_FULL_REQUIRES_OWNER_AND_REGISTERED_FULL_PROFILE")
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

def _declared_acceptance_ids(action: dict) -> list[str] | None:
    """Parse the machine declaration once; quoted historical markers count too."""
    prefix = "MT01_ACCEPTANCE_JSON:"
    instruction = action["instruction"]
    lines = [line.strip() for line in instruction.splitlines() if prefix in line]
    try:
        if instruction.count(prefix) != 1 or len(lines) != 1 or not lines[0].startswith(prefix):
            return None
        ids = json.loads(lines[0][len(prefix):])
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 128 or
                any(not isinstance(ac, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", ac) for ac in ids)
                or len(set(ids)) != len(ids)):
            return None
        return ids
    except (ValueError, TypeError):
        return None


def _declared_impact_witnesses(action: dict) -> dict[str, list[str]] | None:
    """Optional owner-impact prediction in the SAME signed instruction.

    Exactly one standalone line: MT01_IMPACT_WITNESSES_JSON: followed by
    canonical JSON (sorted keys and path lists, no whitespace/duplicate keys).
    This declares mandatory owner impact, never changed bytes or test evidence.
    """
    prefix = "MT01_IMPACT_WITNESSES_JSON:"
    instruction = action["instruction"]
    count = instruction.count(prefix)
    if not count:
        return None
    if count != 1:
        raise RemoteRequestError("AC_IMPACT_WITNESS_DUPLICATE_PRE_EXECUTOR")
    line = next(line for line in instruction.splitlines() if prefix in line)
    payload = line[len(prefix):]
    if not line.startswith(prefix) or len(payload) > 32768:
        raise RemoteRequestError("AC_IMPACT_WITNESS_MALFORMED_PRE_EXECUTOR")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("non-JSON constant")

    try:
        witnesses = json.loads(payload, object_pairs_hook=unique_object,
                               parse_constant=invalid_constant)
        if (not isinstance(witnesses, dict) or not 1 <= len(witnesses) <= 128
                or any(not isinstance(paths, list) or not 1 <= len(paths) <= 32
                       or any(not isinstance(p, str) or not 1 <= len(p) <= 512 for p in paths)
                       or paths != sorted(set(paths)) for paths in witnesses.values())
                or sum(map(len, witnesses.values())) > 256
                or len(payload.encode("utf-8")) > 32768):
            raise ValueError("invalid shape or limit")
        encoded = canonical(witnesses).decode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise RemoteRequestError("AC_IMPACT_WITNESS_MALFORMED_PRE_EXECUTOR") from exc
    if payload != encoded:
        raise RemoteRequestError("AC_IMPACT_WITNESS_NONCANONICAL_PRE_EXECUTOR")
    return witnesses


def approved_acceptance_plan(action: dict) -> dict:
    """Read a bounded declaration from the issuer's fingerprinted v1 instruction.

    No prose inference, new schema fields, executor file, or test argv is trusted.
    Absence/ambiguity is an unknown AC, forcing broad verification. Named IDs
    describe the reviewed test families; arbitrary issue ACs remain unknown.
    """
    ids = _declared_acceptance_ids(action)
    reason = "EXPLICIT_FINGERPRINTED_INSTRUCTION_ACCEPTANCE"
    if ids is None:
        ids = ["MT01.ACCEPTANCE_UNDECLARED"]
        reason = "AC_DECLARATION_MISSING_OR_INVALID_REQUIRES_BROAD_SCOPE"
    mapping = {ac: {"group": g.name, "tests": list(g.tests)} for g in approved_groups() for ac in g.acs}
    unknown = sorted(set(ids) - mapping.keys())
    return {"ids": sorted(ids), "unknown_ids": unknown,
            "consumer_map": {ac: mapping[ac] for ac in sorted(ids) if ac in mapping},
            "source": "ISSUER_FINGERPRINTED_V1_INSTRUCTION", "reason": reason,
            "broad_required": bool(unknown)}


class _PinnedTreePath:
    """Read-only Path view for the SAME MT-01 owner loader and impact planner.

    Only the root can be used as a subprocess cwd. File reads use verified Git
    blobs in memory, including owner manifests; no checkout or temp files exist.
    """
    def __init__(self, repo: Path, entries: dict, contents: dict, path: str = ""):
        self._repo, self._entries, self._contents, self._path = repo, entries, contents, path

    def _at(self, path: str):
        return _PinnedTreePath(self._repo, self._entries, self._contents, path)

    def __truediv__(self, path: str):
        return self._at(self._path + "/" + path if self._path else path)

    def __fspath__(self):
        if self._path:
            raise TypeError("PINNED_TREE_HAS_NO_DISK_FILES")
        return str(self._repo)

    def __eq__(self, other):
        return (isinstance(other, _PinnedTreePath) and self._entries is other._entries
                and self._path == other._path)

    def resolve(self):
        return self

    @property
    def parent(self):
        return self._at(self._path.rpartition("/")[0])

    @property
    def parents(self):
        result, path = [], self
        while path._path:
            path = path.parent
            result.append(path)
        return tuple(result)

    def relative_to(self, other):
        return PurePosixPath(self._path).relative_to(other._path or ".")

    def is_file(self):
        return self._entries.get(self._path, (None,))[0] in (b"100644", b"100755")

    def is_symlink(self):
        return self._entries.get(self._path, (None,))[0] == b"120000"

    def is_dir(self):
        prefix = self._path + "/" if self._path else ""
        return any(path.startswith(prefix) for path in self._entries)

    def exists(self):
        return self._path == ".git" or self.is_file() or self.is_dir()

    def rglob(self, pattern):
        prefix = self._path + "/" if self._path else ""
        return (self._at(path) for path in sorted(self._entries)
                if path.startswith(prefix) and PurePosixPath(path).match(pattern))

    def glob(self, pattern):
        return (path for path in self.rglob(pattern) if path.parent == self)

    def read_bytes(self):
        return self._contents[self._path]

    def read_text(self, encoding="utf-8", errors="strict"):
        return self.read_bytes().decode(encoding, errors)


def _pinned_source_tree(repo: Path, source_sha: str, *, git_executable: str | None):
    """Bound and authenticate the import/manifest snapshot without following links."""
    def git(*args, **kwargs):
        result = subprocess.run(git_argv(repo, *args, executable=git_executable),
                                capture_output=True, check=False, timeout=30, **kwargs)
        if result.returncode:
            raise ValueError("PINNED_BASELINE_UNPROVEN")
        return result.stdout

    inventory = git("ls-tree", "-r", "-l", "-z", source_sha)
    if len(inventory) > 16 * 1024 * 1024:
        raise ValueError("PINNED_BASELINE_LIMIT")
    entries = {}
    for entry in inventory.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, oid, size = metadata.split()
        path = name.decode("utf-8")
        if (not path or "\\" in path or ":" in path or path.startswith("/")
                or any(p in ("", ".", "..") for p in path.split("/"))
                or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or path in entries or not HEX_SHA.fullmatch(oid.decode("ascii"))):
            raise ValueError("PINNED_BASELINE_UNSAFE")
        entries[path] = (mode, kind, oid, int(size) if kind == b"blob" else 0)
    if not entries or len(entries) > 100_000:
        raise ValueError("PINNED_BASELINE_LIMIT")
    paths = sorted(p for p in entries if (
        p.startswith(("src/ai_commerce/", "tests/")) and p.endswith(".py")
        or p.startswith("work_items/mt01-components/")))
    if (any(entries[p][0] not in (b"100644", b"100755") or entries[p][1] != b"blob"
            or not 0 <= entries[p][3] <= 4 * 1024 * 1024 for p in paths)
            or sum(entries[p][3] for p in paths) > 64 * 1024 * 1024):
        raise ValueError("PINNED_BASELINE_UNSAFE_OR_LIMIT")
    snapshot = git("cat-file", "--batch", input=b"".join(entries[p][2] + b"\n" for p in paths))
    contents, offset = {}, 0
    for path in paths:
        end = snapshot.index(b"\n", offset)
        oid, kind, size = snapshot[offset:end].split()
        length, start = int(size), end + 1
        raw = snapshot[start:start + length]
        if (oid != entries[path][2] or kind != b"blob" or length != entries[path][3]
                or len(raw) != length or snapshot[start + length:start + length + 1] != b"\n"
                or hashlib.sha1(b"blob " + str(length).encode("ascii") + b"\0" + raw
                               ).hexdigest() != oid.decode("ascii")):
            raise ValueError("PINNED_BASELINE_BYTES_UNPROVEN")
        contents[path] = raw
        offset = start + length + 1
    if offset != len(snapshot):
        raise ValueError("PINNED_BASELINE_BYTES_UNPROVEN")
    return _PinnedTreePath(repo, entries, contents)


def validate_acceptance_scope(batch: dict, *, repo: Path | None,
                              git_executable: str | None = None) -> dict:
    """Owner-impact compatibility/contract AFTER issuer validation and pinning.

    Definitive declaration/owner contradictions reject the ENTIRE batch. New
    owners require the narrow onboarding contract below; directory scopes and
    incomplete graphs HOLD. Allowed paths are a maximum, not promised edits. An optional
    scope needs explicit signed witnesses unless EVERY admissible nonempty
    selection (including predecessor contributions) hits the required owners.
    Post-edit MT-01 must still prove the actual candidate diff and real tests.
    """
    from ai_commerce.engineering.impact_v21 import (
        load_baseline_component_owner_records, select_impact,
    )

    actions = {a["action_id"]: a for a in batch["actions"]}

    def deferred(key, reason):
        return {"action_id": key, "status": "PRE_EXECUTOR_UNDETERMINED", "reason": reason}

    def report(results):
        contracted = "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN"
        compatible = all(r["status"] in ("PRE_EXECUTOR_COMPATIBLE", contracted) for r in results)
        # Existing CLI/host consumers use status as the dispatch compatibility
        # gate. Keep that wire value; expose the provisional contract status on
        # each action and the batch coverage_status, never as coverage proof.
        return {"status": "PRE_EXECUTOR_COMPATIBLE" if compatible else "PRE_EXECUTOR_UNDETERMINED",
                "coverage_status": (contracted if compatible and any(r["status"] == contracted for r in results)
                                    else "PRE_EXECUTOR_COMPATIBLE" if compatible else "PRE_EXECUTOR_UNDETERMINED"),
                "source_sha": batch["source_sha"], "actions": results,
                "next_gate": "POST_EDIT_MT01_REQUIRED" if compatible else "HOLD_NO_EXECUTOR"}

    if repo is None:
        return report([deferred(key, "SOURCE_REPO_NOT_VERIFIED") for key in actions])
    scopes = {}

    def potential_paths(key):
        if key not in scopes:
            paths = set(actions[key]["allowed_paths"])
            for parent in actions[key]["depends_on"]:
                paths.update(potential_paths(parent))
            scopes[key] = paths
        return scopes[key]

    try:
        source_sha = batch["source_sha"]
        if not HEX_SHA.fullmatch(source_sha) or not (repo / ".git").exists():
            raise ValueError("PINNED_BASELINE_UNPROVEN")
        root = _pinned_source_tree(repo, source_sha, git_executable=git_executable)
        groups = approved_groups()
        records = load_baseline_component_owner_records(
            root=root, base_sha=source_sha, existing_groups=groups)
        groups = (*groups, *(record["group"] for record in records))
        known = {ac for group in groups for ac in group.acs}
        reviewed = {p for g in groups for p in (*g.paths, *g.tests, *g.consumers, *g.contracts)}
        acs_by_group = {g.name: set(g.acs) for g in groups}
        impacts = {}

        def impact_for(paths):
            key = tuple(sorted(paths))
            if key not in impacts:
                impacts[key] = select_impact(root=root, changed_paths=key, groups=groups,
                                            base_sha=source_sha, candidate_sha=source_sha)
            return impacts[key]

        def impact_acs(impact):
            return {ac for name in impact.selected_groups for ac in acs_by_group[name]}

        def proven_closure(paths, impact):
            return (set(paths) <= reviewed and impact.runnable and not impact.missing_mapping
                    and not (set(impact.affected_paths) - reviewed))

        results, minimum_acs = {}, {}

        def onboarding_scope(action):
            # Ingress contracts only a finite, fresh layout. There are no
            # candidate manifest bytes or executed consumers to prove yet.
            if (len(actions) != 1 or action["depends_on"]
                    or action.get("reuse_candidate") is not None):
                return "ONBOARD_SINGLE_NEW_ACTION_REQUIRED_PRE_EXECUTOR"
            if "MT01_IMPACT_WITNESSES_JSON:" in action["instruction"]:
                return "ONBOARD_BASELINE_IMPACT_FORBIDDEN_PRE_EXECUTOR"
            ids = _declared_acceptance_ids(action)
            if ids is None or len(ids) != 1:
                return "ONBOARD_AC_DECLARATION_INVALID_PRE_EXECUTOR"
            fence = None
            for line in action["instruction"].splitlines():
                stripped = line.strip()
                if stripped.startswith(("```", "~~~")):
                    marker = stripped[:3]
                    fence = None if fence == marker else marker if fence is None else fence
                if "MT01_ACCEPTANCE_JSON:" in line and fence is not None:
                    return "ONBOARD_AC_DECLARATION_INVALID_PRE_EXECUTOR"
            if set(ids) & known:
                return "ONBOARD_AC_ALREADY_OWNED_PRE_EXECUTOR"
            paths = action["allowed_paths"]
            if (not 3 <= len(paths) <= 81 or len(set(paths)) != len(paths)
                    or len({p.casefold() for p in paths}) != len(paths)):
                return "ONBOARD_EXACT_NEW_OWNER_LAYOUT_REQUIRED_PRE_EXECUTOR"
            reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
                        *(f"lpt{i}" for i in range(10))}
            for path in paths:
                try:
                    safe_path(path)
                except RemoteRequestError:
                    return "ONBOARD_UNSAFE_PATH_PRE_EXECUTOR"
                if (len(path) > 240 or any(ord(c) < 32 or ord(c) == 127 for c in path)
                        or any(part.split(".")[0].casefold() in reserved
                               for part in path.split("/"))):
                    return "ONBOARD_UNSAFE_PATH_PRE_EXECUTOR"
            manifests = [p for p in paths if p.startswith("work_items/mt01-components/")]
            if (len(manifests) != 1 or not re.fullmatch(
                    r"work_items/mt01-components/[a-z][a-z0-9_-]{1,63}\.json", manifests[0])):
                return "ONBOARD_EXACTLY_ONE_NEW_MANIFEST_REQUIRED_PRE_EXECUTOR"
            name = PurePosixPath(manifests[0]).stem
            sources = [p for p in paths if re.fullmatch(
                r"src/ai_commerce/[a-z][a-z0-9_]{0,63}/[A-Za-z_][A-Za-z0-9_]*\.py", p)]
            tests = [p for p in paths if re.fullmatch(r"tests/test_[A-Za-z0-9_]+\.py", p)]
            packages = {str(PurePosixPath(p).parent) for p in sources}
            if (not 1 <= len(sources) <= 40 or not 1 <= len(tests) <= 40
                    or len(packages) != 1 or len(paths) != 1 + len(sources) + len(tests)
                    or packages & {"src/ai_commerce/remote", "src/ai_commerce/engineering"}):
                return "ONBOARD_EXACT_NEW_OWNER_LAYOUT_REQUIRED_PRE_EXECUTOR"
            if name.casefold() in {g.name.casefold() for g in groups}:
                return "ONBOARD_BASELINE_OWNER_EXISTS_PRE_EXECUTOR"
            baseline = {p.relative_to(root).as_posix().casefold() for p in root.rglob("*")}
            package = next(iter(packages)).casefold()
            if (any(p == package or p.startswith(package + "/") for p in baseline)
                    or {p.casefold() for p in paths} & (baseline | {p.casefold() for p in reviewed})):
                return "ONBOARD_REOWN_OR_EXISTING_PATH_PRE_EXECUTOR"
            for path in paths:
                target = root / path
                # Check every prefix, including symlinks that do not appear
                # as files/directories in the immutable Path view, and their
                # Windows case aliases. Inventory entries are leaves, not dirs.
                while target != root:
                    if target.relative_to(root).as_posix().casefold() in baseline:
                        return "ONBOARD_UNSAFE_PATH_PRE_EXECUTOR"
                    target = target.parent
            return None

        def evaluate(key):
            if key in results:
                return
            action = actions[key]
            if action["verification_profile"] == "mt01-component-onboarding":
                minimum_acs[key] = set()  # A new AC never becomes baseline impact proof.
                reason = onboarding_scope(action)
                results[key] = (deferred(key, reason) if reason else {
                    "action_id": key, "status": "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN",
                    "onboarding_contract": {
                        "source_sha": source_sha, "request_fingerprint": fingerprint(batch),
                        "allowed_paths": list(action["allowed_paths"]),
                        "acceptance_ids": _declared_acceptance_ids(action), "coverage_proven": False,
                    },
                })
                return
            for parent in action["depends_on"]:
                evaluate(parent)
            minimum_acs[key] = set().union(*(minimum_acs[p] for p in action["depends_on"]))
            witnesses = _declared_impact_witnesses(action)
            ids = _declared_acceptance_ids(action)
            if witnesses is not None:
                if ids is None or set(ids) - known:
                    raise RemoteRequestError("AC_DECLARATION_UNPROVEN_PRE_EXECUTOR")
                if set(witnesses) - known:
                    raise RemoteRequestError("AC_IMPACT_WITNESS_UNKNOWN_AC_PRE_EXECUTOR")
                if set(witnesses) != set(ids):
                    raise RemoteRequestError("AC_IMPACT_WITNESS_AC_MISMATCH_PRE_EXECUTOR")
                for paths in witnesses.values():
                    for path in paths:
                        try:
                            safe_path(path)
                            if any(ord(c) < 32 or ord(c) == 127 for c in path):
                                raise RemoteRequestError("INVALID_PATH")
                        except RemoteRequestError as exc:
                            raise RemoteRequestError("AC_IMPACT_WITNESS_PATH_INVALID_PRE_EXECUTOR") from exc
                        # Predecessor maxima are NOT this issuer's mandatory
                        # edits for this action. Never widen the signed scope.
                        if path not in action["allowed_paths"]:
                            raise RemoteRequestError("AC_IMPACT_WITNESS_OUTSIDE_SCOPE_PRE_EXECUTOR")
                        if (not (root / path).is_file() or (root / path).is_symlink()
                                or path.startswith("work_items/mt01-components/")):
                            raise RemoteRequestError("AC_IMPACT_WITNESS_PATH_UNESTABLISHED_PRE_EXECUTOR")
            paths = potential_paths(key)
            if (not paths or any(not (root / p).is_file() for p in paths)
                    or any(p.startswith("work_items/mt01-components/") for p in paths)):
                results[key] = deferred(key, "ALLOWED_SCOPE_NOT_EXACT_ESTABLISHED_FILES")
                return
            impact = impact_for(paths)
            if not proven_closure(paths, impact):
                results[key] = deferred(key, "IMPACT_CLOSURE_UNPROVEN")
                return
            if ids is None or set(ids) - known:
                raise RemoteRequestError("AC_DECLARATION_UNPROVEN_PRE_EXECUTOR")
            if set(ids) - impact_acs(impact):
                raise RemoteRequestError("AC_SCOPE_MISMATCH_PRE_EXECUTOR")
            if witnesses is not None:
                for ac, witness_paths in witnesses.items():
                    for path in witness_paths:
                        single = impact_for([path])
                        if ac not in impact_acs(single):
                            raise RemoteRequestError("AC_IMPACT_WITNESS_OWNER_MISMATCH_PRE_EXECUTOR")
                        if not proven_closure([path], single):
                            raise RemoteRequestError("AC_IMPACT_WITNESS_CLOSURE_UNPROVEN_PRE_EXECUTOR")
                        minimum_acs[key].update(impact_acs(single))
            else:
                # Impact selection is monotone on this immutable graph: the
                # intersection of singleton selections proves all nonempty
                # subsets. Successful predecessors each contribute a nonempty
                # diff, so their independently established minima survive fan-in.
                inherent = None
                for path in action["allowed_paths"]:
                    single = impact_for([path])
                    if not proven_closure([path], single):
                        results[key] = deferred(key, "IMPACT_CLOSURE_UNPROVEN")
                        return
                    selected = impact_acs(single)
                    inherent = selected if inherent is None else inherent & selected
                    if set(ids) - (minimum_acs[key] | inherent):
                        raise RemoteRequestError("AC_IMPACT_WITNESS_REQUIRED_PRE_EXECUTOR")
                minimum_acs[key].update(inherent or ())
            contracted = witnesses is not None or any(
                results[p]["status"] == "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN" for p in action["depends_on"])
            results[key] = {"action_id": key,
                            "status": "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN" if contracted else "PRE_EXECUTOR_COMPATIBLE",
                            "selected_groups": list(impact.selected_groups)}
            if witnesses is not None:
                results[key]["impact_contract"] = {
                    "source_sha": source_sha, "request_fingerprint": fingerprint(batch),
                    "witnesses": witnesses, "coverage_proven": False,
                }

        for key in actions:
            evaluate(key)
        return report([results[key] for key in actions])
    except RemoteRequestError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, subprocess.SubprocessError):
        return report([deferred(key, "PINNED_BASELINE_UNPROVEN") for key in actions])


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
    human_full = action.get("verification_intent", "AUTO") == "HUMAN_FULL"
    if human_full and not explicit_full:
        raise RemoteRequestError("HUMAN_FULL_REQUIRES_REGISTERED_FULL_PROFILE")
    if os.environ.get("MT01_V21_ENABLED") == "1" and not human_full:
        # Preserve exactly the reviewed narrow registry command. The post-edit
        # impact planner must independently prove component+consumer closure.
        # A missing mapping returns UNPROVEN, never substitutes project-tests.
        return {"state": "EXECUTE", "requested_profile": requested,
                "profile": requested, "argv": list(explicit),
                "minimum_test": True, "registered_only": True,
                "coverage": "V21_REQUIRES_POST_EXECUTOR_IMPACT_PROOF",
                "workers_requested": registered_workers(explicit),
                "registered_workers": registered_workers(explicit),
                "reason": "MT01_V21_PRESERVE_EXACT_REGISTERED_PROFILE",
                "human_full_suite_authorized": False,
                "aggregate_requires_fresh_execution": bool(action.get("depends_on"))}

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
            "reason": ("HUMAN_AUTHENTICATED_FULL_SUITE" if human_full
                       else "EXPLICIT_REGISTERED_FULL_SUITE" if explicit_full
                       else "MT01_SELECTOR_REGISTRATION_REQUIRES_COMPANION_PR"),
            "human_full_suite_authorized": human_full,
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
