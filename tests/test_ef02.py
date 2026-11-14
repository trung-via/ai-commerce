"""EF02 scope tests: validation, real parallel child processes, dependencies and claim races."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ai_commerce.remote.validation import (
    RemoteRequestError, validate_batch, verify_registry, safe_path, fingerprint, approved_acceptance_plan
)
from ai_commerce.remote.scheduler import run_schedule
from ai_commerce.remote.transport import GitHubTransport, RemoteTransportError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts" / "RemoteActionBatch.v1.schema.json"
REGISTRY = ROOT / ".github" / "ef02" / "verify-profiles.json"


def action(name, depends=(), resources=()):
    return {
        "action_id": name, "attempt_id": name + "-001",
        "executor": "codex" if name != "b" else "antigravity",
        "model": "gpt-6.1-sol" if name != "b" else "gemini-3.8-flash",
        "effort": "max" if name != "b" else "medium",
        "instruction": "Make one scoped source change",
        "allowed_paths": ["src/ai_commerce/feature_" + name],
        "verification_profile": "project-tests", "depends_on": list(depends),
        "resources": list(resources)
    }


def batch(actions):
    return {
        "kind": "AI_COMMERCE_REMOTE_ACTION_BATCH", "version": 1,
        "batch_id": "sample-001", "source_sha": "a" * 40,
        "actor": "HUMAN", "authorization_ref": "user-directive-001",
        "max_parallel": 2, "actions": actions,
    }


def validate(packet, issuer="trung-via", owner="trung-via", brain=()):
    return validate_batch(packet, schema=SCHEMA, registry=verify_registry(REGISTRY),
                          issuer_login=issuer, owner_login=owner, brain_issuers=brain)


def test_schema_and_authentication_are_not_just_actor_strings():
    assert validate(batch([action("a")]))["batch_id"] == "sample-001"
    with pytest.raises(RemoteRequestError, match="ISSUER_NOT_AUTHORIZED"):
        validate(batch([action("a")]), issuer="someone-else")
    p = batch([action("a")])
    p["actor"] = "BRAIN"
    assert validate(p, issuer="ai-commerce-bot", brain=("ai-commerce-bot",))
    with pytest.raises(RemoteRequestError, match="ISSUER_NOT_AUTHORIZED"):
        validate(p, issuer="trung-via")


def test_reject_duplicate_cycle_unknown_dependency_and_bad_path():
    cases = [
        [action("a"), action("a")],
        [action("a", depends=("b",)), action("b", depends=("a",))],
        [action("a", depends=("missing",))],
    ]
    for acts in cases:
        with pytest.raises(RemoteRequestError):
            validate(batch(acts))
    for value in ("../secret", "src\\secret", "/etc/passwd", ".github/workflows/run.yml",
                  "C:/Windows/System32", "./src"):
        with pytest.raises(RemoteRequestError):
            safe_path(value)
    p = batch([action("a")])
    p["actions"][0]["verification_profile"] = "shell"
    with pytest.raises(RemoteRequestError, match="VERIFICATION_PROFILE_UNREGISTERED"):
        validate(p)


def test_component_scope_accepts_nested_paths_not_unrelated_siblings():
    from ai_commerce.engineering.runner import _safe_scope
    a = action("a")
    a["allowed_paths"] = ["src/ai_commerce/engineering", "src/ai_commerce/remote",
                          "tests/test_ef01.py"]
    packet = batch([a])
    assert validate(packet) == packet
    assert _safe_scope(["src/ai_commerce/engineering/new_helper.py",
                        "src/ai_commerce/remote/scheduler.py",
                        "tests/test_ef01.py"], a["allowed_paths"])
    for name in ("src/ai_commerce/engineering_extra/z.py", "tests/test_ef02.py",
                 ".github/workflows/ef02-batch.yml", "src/ai_commerce/unrelated.py"):
        assert not _safe_scope([name], a["allowed_paths"])
    for name in ("../bad", ".git/config", ".local/cache", ".github/workflows/main.yml"):
        with pytest.raises(RemoteRequestError):
            safe_path(name)


def test_fingerprint_deterministic_and_model_change_is_distinct():
    p = batch([action("a")])
    assert fingerprint(p) == fingerprint(copy.deepcopy(p))
    alt = copy.deepcopy(p)
    alt["actions"][0]["model"] = "other-model"
    assert fingerprint(p) != fingerprint(alt)


def test_acceptance_declaration_is_v1_compatible_authorized_and_fingerprinted():
    a = action("a")
    a["instruction"] += '\nMT01_ACCEPTANCE_JSON: ["A12.product-evidence", "EF01.repair"]'
    packet = batch([a])
    assert validate(packet) == packet
    acceptance = approved_acceptance_plan(a)
    assert acceptance["ids"] == ["A12.product-evidence", "EF01.repair"]
    assert not acceptance["broad_required"]
    assert "tests/test_a12_claims.py" in acceptance["consumer_map"]["A12.product-evidence"]["tests"]
    changed = copy.deepcopy(packet)
    changed["actions"][0]["instruction"] += ' '
    assert fingerprint(packet) != fingerprint(changed)
    with pytest.raises(RemoteRequestError, match="ISSUER_NOT_AUTHORIZED"):
        validate(packet, issuer="untrusted-executor")


PERSONAL_SOURCE = "src/ai_commerce/hostops_v3/personal.py"
PERSONAL_TEST = "tests/test_hostops_personal.py"
PERSONAL_AC = "HOSTOPS.PERSONAL.chat-approved-public-read-owner-comment-new-scratch-write"
PERSONAL_MANIFEST = "work_items/mt01-components/hostops-personal.json"
A12_SOURCE = "src/ai_commerce/product/identity.py"
A14_SOURCE = "src/ai_commerce/affiliate_attribution/evidence.py"
WAKE_SOURCE = "src/ai_commerce/remote/completion_event.py"


def scope_action(name, paths, acs, depends=()):
    item = action(name, depends=depends)
    item["allowed_paths"] = list(paths)
    if acs is not None:
        item["instruction"] += "\nMT01_ACCEPTANCE_JSON:" + json.dumps(acs)
    return item


def make_scope_repo(tmp_path, monkeypatch, *, contents=None):
    """Offline immutable Git responses; no git init/commit or executor calls.

    Use representative real reviewed static owners and a real persisted owner
    manifest, exercising the production loader rather than a caller owner map.
    """
    from ai_commerce.engineering.minimum_test import approved_groups
    from ai_commerce.remote import validation

    groups = tuple(g for g in approved_groups()
                   if g.name in {"a12", "a14", "ef02-wake", "hostops-v3", "ef02"})
    monkeypatch.setattr(validation, "approved_groups", lambda: groups)
    files = {p: b"# pinned fixture\n" for g in groups for p in
             (*g.paths, *g.tests, *g.consumers, *g.contracts)}
    files.update({PERSONAL_MANIFEST: (ROOT / PERSONAL_MANIFEST).read_bytes(),
                  PERSONAL_SOURCE: b"# personal\n", PERSONAL_TEST: b"# test personal\n",
                  "contracts/RemoteActionBatch.v1.schema.json": SCHEMA.read_bytes(),
                  ".github/ef02/verify-profiles.json": REGISTRY.read_bytes(),
                  ".github/ef02/approved-candidates.v1.json":
                      (ROOT / ".github/ef02/approved-candidates.v1.json").read_bytes()})
    files.update(contents or {})
    (tmp_path / ".git").mkdir()
    for path, raw in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    objects = {hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
                           ).hexdigest().encode("ascii"): raw for raw in files.values()}
    entries = b"".join(b"100644 blob " + hashlib.sha1(
        b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest().encode("ascii")
        + b"\t" + path.encode("utf-8") + b"\0" for path, raw in sorted(files.items()))
    calls = []

    def git_read(argv, **kwargs):
        calls.append(argv)
        # argv[0] may be an explicitly selected Git executable.
        args = argv[3:] if argv[1:2] == ["-C"] else argv[1:]
        if args == ["rev-parse", "HEAD"]:
            output = "a" * 40 + "\n"
        elif args == ["cat-file", "-t", "a" * 40]:
            output = "commit\n"
        elif args == ["ls-tree", "-r", "--name-only", "-z", "a" * 40]:
            output = b"\0".join(p.encode("utf-8") for p in sorted(files)) + b"\0"
        elif args == ["ls-tree", "-r", "-z", "a" * 40]:
            output = entries
        elif args[:1] == ["show"] and args[1].startswith("a" * 40 + ":"):
            output = files[args[1].split(":", 1)[1]]
        elif args == ["cat-file", "--batch"]:
            output = b""
            for oid in kwargs["input"].splitlines():
                raw = objects[oid]
                output += oid + b" blob " + str(len(raw)).encode("ascii") + b"\n" + raw + b"\n"
        else:
            pytest.fail("Unexpected effect in scope preflight: " + repr(args))
        return subprocess.CompletedProcess(argv, 0, output, "" if isinstance(output, str) else b"")

    monkeypatch.setattr(subprocess, "run", git_read)
    return files, calls


@pytest.mark.parametrize("paths,acs,compatible", [
    ([PERSONAL_SOURCE, PERSONAL_TEST], ["EF02.wake-boundaries"], False),
    ([PERSONAL_SOURCE, PERSONAL_TEST], [PERSONAL_AC], True),
    ([PERSONAL_SOURCE, WAKE_SOURCE], [PERSONAL_AC, "EF02.wake-boundaries"], True),
    ([A14_SOURCE], ["A12.product-evidence"], False),
    ([A14_SOURCE], ["A14.L04.offline-attribution-evidence"], True),
])
def test_pre_executor_scope_uses_pinned_real_owner_mapping(tmp_path, monkeypatch, paths, acs, compatible):
    from ai_commerce.remote.validation import validate_acceptance_scope
    make_scope_repo(tmp_path, monkeypatch)
    packet = batch([scope_action("a", paths, acs)])
    original = copy.deepcopy(packet)
    if compatible:
        result = validate_acceptance_scope(packet, repo=tmp_path)
        assert result["status"] == "PRE_EXECUTOR_COMPATIBLE"
        assert result["next_gate"] == "POST_EDIT_MT01_REQUIRED"
    else:
        with pytest.raises(RemoteRequestError, match="^AC_SCOPE_MISMATCH_PRE_EXECUTOR$"):
            validate_acceptance_scope(packet, repo=tmp_path)
    assert packet == original and fingerprint(packet) == fingerprint(original)


@pytest.mark.parametrize("paths,acs", [
    (["src/ai_commerce/new_component.py"], ["EF02.wake-boundaries"]),
    (["work_items/mt01-components/new-component.json"], ["EF02.wake-boundaries"]),
    ([PERSONAL_MANIFEST], ["EF02.wake-boundaries"]),
    (["src/ai_commerce/hostops_v3"], ["EF02.wake-boundaries"]),
    ([PERSONAL_SOURCE], None),
    ([PERSONAL_SOURCE], ["UNKNOWN.new-owner"]),
    ([PERSONAL_SOURCE], [PERSONAL_AC, "UNKNOWN.new-owner"]),
])
def test_pre_executor_uncertain_scope_defers_without_guessing(tmp_path, monkeypatch, paths, acs):
    from ai_commerce.remote.validation import validate_acceptance_scope
    make_scope_repo(tmp_path, monkeypatch)
    result = validate_acceptance_scope(batch([scope_action("a", paths, acs)]), repo=tmp_path)
    assert result["status"] == "PRE_EXECUTOR_UNDETERMINED"
    assert result["next_gate"] == "DEFER_TO_POST_EDIT_MT01"


def test_pre_executor_missing_instruction_defers_but_schema_still_rejects(tmp_path, monkeypatch):
    from jsonschema import ValidationError
    from ai_commerce.remote.validation import validate_acceptance_scope
    packet = batch([scope_action("a", [PERSONAL_SOURCE], None)])
    del packet["actions"][0]["instruction"]
    def forbidden(*args, **kwargs):
        pytest.fail("Undeclared acceptance must not inspect a repository")
    monkeypatch.setattr(subprocess, "run", forbidden)
    result = validate_acceptance_scope(packet, repo=tmp_path)
    assert result["status"] == "PRE_EXECUTOR_UNDETERMINED"
    assert result["actions"][0]["reason"] == "AC_DECLARATION_UNDETERMINED"
    with pytest.raises(ValidationError):
        validate(packet)


@pytest.mark.parametrize("acs", [[PERSONAL_AC], ["EF02.wake-boundaries"]])
def test_pre_executor_reads_raw_python_blobs_without_archive_attributes(tmp_path, monkeypatch, acs):
    from ai_commerce.remote.validation import validate_acceptance_scope
    files, _ = make_scope_repo(tmp_path, monkeypatch, contents={
        PERSONAL_SOURCE: b"# pinned CRLF source\r\n",
        "src/ai_commerce/hostops_v3/README.md": b"unrelated text\r\n",
    })
    original_run = subprocess.run
    reads = []
    def run(argv, **kwargs):
        if argv[-2:] == ["cat-file", "--batch"]:
            reads.append(kwargs["input"].splitlines())
        return original_run(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", run)
    packet = batch([scope_action("a", [PERSONAL_SOURCE, PERSONAL_TEST], acs)])
    if acs == [PERSONAL_AC]:
        result = validate_acceptance_scope(packet, repo=tmp_path)
        assert result["status"] == "PRE_EXECUTOR_COMPATIBLE"
    else:
        with pytest.raises(RemoteRequestError, match="^AC_SCOPE_MISMATCH_PRE_EXECUTOR$"):
            validate_acceptance_scope(packet, repo=tmp_path)
    expected = [hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
                             ).hexdigest().encode("ascii") for path, raw in sorted(files.items())
                if path.endswith(".py") and path.startswith(("src/ai_commerce/", "tests/"))]
    assert reads == [expected]


def test_pre_executor_import_consumer_and_dag_fanin_are_real_scope(tmp_path, monkeypatch):
    from ai_commerce.remote.validation import validate_acceptance_scope
    make_scope_repo(tmp_path, monkeypatch, contents={
        A14_SOURCE: b"from ai_commerce.product import identity\n"})
    # A12 source affects its A14 consumer without an A14 edit.
    packet = batch([scope_action("a", [A12_SOURCE], ["A14.L04.offline-attribution-evidence"])])
    assert validate_acceptance_scope(packet, repo=tmp_path)["status"] == "PRE_EXECUTOR_COMPATIBLE"
    packet = batch([
        scope_action("a", [WAKE_SOURCE], ["EF02.wake-boundaries"]),
        scope_action("b", [PERSONAL_SOURCE], ["EF02.wake-boundaries"], depends=("a",)),
    ])
    assert validate_acceptance_scope(packet, repo=tmp_path)["status"] == "PRE_EXECUTOR_COMPATIBLE"
    packet["actions"][1]["depends_on"] = []
    with pytest.raises(RemoteRequestError, match="AC_SCOPE_MISMATCH_PRE_EXECUTOR"):
        validate_acceptance_scope(packet, repo=tmp_path)


def test_pre_executor_uses_pinned_consumers_not_forged_checkout_imports(tmp_path, monkeypatch):
    from ai_commerce.remote.validation import validate_acceptance_scope
    make_scope_repo(tmp_path, monkeypatch)
    (tmp_path / WAKE_SOURCE).write_text("from ai_commerce.hostops_v3 import personal\n", encoding="utf-8")
    packet = batch([scope_action("a", [PERSONAL_SOURCE], ["EF02.wake-boundaries"])])
    with pytest.raises(RemoteRequestError, match="AC_SCOPE_MISMATCH_PRE_EXECUTOR"):
        validate_acceptance_scope(packet, repo=tmp_path)


@pytest.mark.parametrize("problem", ["dynamic-import", "unknown-source", "manifest-drift",
                                     "unknown-consumer", "forged-owner", "snapshot-empty",
                                     "snapshot-content", "snapshot-header", "snapshot-trailing",
                                     "git-unavailable"])
def test_pre_executor_unproven_graph_never_claims_compatibility(tmp_path, monkeypatch, problem):
    from ai_commerce.remote.validation import validate_acceptance_scope
    source = PERSONAL_SOURCE
    contents = {}
    if problem == "dynamic-import":
        contents[source] = b"import importlib\nimportlib.import_module(name)\n"
    elif problem == "unknown-source":
        source = "src/ai_commerce/unowned.py"
        contents[source] = b"# committed but not registered\n"
    elif problem == "unknown-consumer":
        contents["src/ai_commerce/unowned.py"] = b"from ai_commerce.hostops_v3 import personal\n"
    elif problem == "forged-owner":
        manifest = json.loads((ROOT / PERSONAL_MANIFEST).read_text(encoding="utf-8"))
        manifest["acs"] = ["EF02.wake-boundaries"]  # Cannot re-own an existing AC.
        contents[PERSONAL_MANIFEST] = json.dumps(manifest).encode("utf-8")
    _, calls = make_scope_repo(tmp_path, monkeypatch, contents=contents)
    if problem == "manifest-drift":
        (tmp_path / PERSONAL_MANIFEST).write_text("{}", encoding="utf-8")
    if problem.startswith("snapshot-") or problem == "git-unavailable":
        original_run = subprocess.run
        def run(argv, **kwargs):
            if argv[-2:] == ["cat-file", "--batch"]:
                if problem == "git-unavailable":
                    return subprocess.CompletedProcess(argv, 1, b"", b"SECRET Git error")
                output = original_run(argv, **kwargs).stdout
                if problem == "snapshot-empty":
                    output = b""
                elif problem == "snapshot-content":
                    header, raw = output.split(b"\n", 1)
                    output = header + b"\n" + bytes([raw[0] ^ 1]) + raw[1:]
                elif problem == "snapshot-header":
                    output = output.replace(b" blob ", b" tree ", 1)
                else:
                    output += b"unexpected trailing object\n"
                return subprocess.CompletedProcess(argv, 0, output, b"")
            return original_run(argv, **kwargs)
        monkeypatch.setattr(subprocess, "run", run)
    result = validate_acceptance_scope(batch([
        scope_action("a", [source], ["EF02.wake-boundaries"])]), repo=tmp_path)
    assert result["status"] == "PRE_EXECUTOR_UNDETERMINED"
    assert result["next_gate"] == "DEFER_TO_POST_EDIT_MT01"
    if problem.startswith("snapshot-") or problem == "git-unavailable":
        assert result["actions"][0]["reason"] == "PINNED_BASELINE_UNPROVEN"
    assert "SECRET" not in json.dumps(result)
    assert calls


@pytest.mark.parametrize("command", ["validate", "run"])
@pytest.mark.parametrize("full", [False, True])
def test_cli_scope_mismatch_blocks_entire_batch_before_any_claim_or_model(
        tmp_path, monkeypatch, capsys, command, full):
    from ai_commerce.remote import __main__ as cli
    _, calls = make_scope_repo(tmp_path, monkeypatch)
    packet = batch([
        scope_action("a", [WAKE_SOURCE], ["EF02.wake-boundaries"]),
        scope_action("b", [PERSONAL_SOURCE, PERSONAL_TEST], ["EF02.wake-boundaries"]),
    ])
    if full:
        packet["actions"][1]["verification_intent"] = "HUMAN_FULL"
    else:
        packet["actions"][1]["verification_profile"] = "mt01-ef02-wake-tests"
    request = tmp_path / "request.json"
    request.write_text(json.dumps(packet), encoding="utf-8")
    effects = []
    def forbidden(*args, **kwargs):
        effects.append(True)
        raise AssertionError("No claim, worker, provider or schedule permitted")
    for name in ("GitHubTransport", "LocalBatchWorker", "run_schedule"):
        monkeypatch.setattr(cli, name, forbidden)
    assert cli.main([command, "--request-file", str(request), "--checkout", str(tmp_path),
                     "--issuer-login", "owner", "--owner-login", "owner"]) == 2
    assert effects == []
    assert "AC_SCOPE_MISMATCH_PRE_EXECUTOR" in capsys.readouterr().err
    source_check = next(i for i, argv in enumerate(calls) if "cat-file" in argv)
    baseline_check = next(i for i, argv in enumerate(calls) if "ls-tree" in argv)
    assert source_check < baseline_check


def test_cli_scope_gate_preserves_authentication_replay_and_source_checks(tmp_path, monkeypatch, capsys):
    from ai_commerce.remote import __main__ as cli
    _, calls = make_scope_repo(tmp_path, monkeypatch)
    request = tmp_path / "request.json"
    packet = batch([scope_action("a", [PERSONAL_SOURCE], [PERSONAL_AC])])
    request.write_text(json.dumps(packet), encoding="utf-8")
    argv = ["validate", "--request-file", str(request), "--checkout", str(tmp_path),
            "--issuer-login", "owner", "--owner-login", "owner"]
    assert cli.main(argv[:-3] + ["attacker", "--owner-login", "owner"]) == 2
    assert "ISSUER_NOT_AUTHORIZED" in capsys.readouterr().err
    assert calls == []
    packet["actions"][0]["reuse_candidate"] = {"candidate_sha": "b" * 40, "verified_run_id": 123}
    request.write_text(json.dumps(packet), encoding="utf-8")
    assert cli.main(argv) == 2
    assert "REUSE_" in capsys.readouterr().err
    assert calls == []
    del packet["actions"][0]["reuse_candidate"]
    request.write_text(json.dumps(packet), encoding="utf-8")
    def unavailable(*args, **kwargs):
        raise RemoteRequestError("SOURCE_COMMIT_UNAVAILABLE")
    monkeypatch.setattr(cli, "check_source", unavailable)
    assert cli.main(argv) == 2
    assert "SOURCE_COMMIT_UNAVAILABLE" in capsys.readouterr().err
    assert calls == []


@pytest.mark.parametrize("declaration", [
    "", "MT01_ACCEPTANCE_JSON: []", "MT01_ACCEPTANCE_JSON: not-json",
    'MT01_ACCEPTANCE_JSON: ["A12.product-evidence"]\nMT01_ACCEPTANCE_JSON: ["EF01.repair"]',
    'MT01_ACCEPTANCE_JSON: ["A12.product-evidence", "A12.product-evidence"]',
    'MT01_ACCEPTANCE_JSON: ["--verify-json", "arbitrary-command"]',
    'MT01_ACCEPTANCE_JSON: {"argv": ["python", "-c", "arbitrary"]}',
    'MT01_ACCEPTANCE_JSON: ["AC-UNKNOWN"]',
])
def test_missing_ambiguous_or_unknown_acceptance_requires_broad_verification(declaration):
    a = action("a")
    a["instruction"] += "\n" + declaration
    acceptance = approved_acceptance_plan(a)
    assert acceptance["broad_required"] and acceptance["unknown_ids"]


@pytest.mark.parametrize("key,value", [("acceptance_ids", ["A12.product-evidence"]),
                                     ("verify_argv", ["python", "-c", "arbitrary"])])
def test_mt01_does_not_relax_v1_additional_properties(key, value):
    from jsonschema import ValidationError
    a = action("a")
    a[key] = value
    with pytest.raises(ValidationError):
        validate(batch([a]))


def test_two_real_child_processes_overlap_in_one_batch():
    running = batch([action("a"), action("b")])
    def work(a):
        start = time.monotonic()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.8)"])
        child.wait(timeout=20)
        return {"status": "CANDIDATE_READY", "process_id": child.pid,
                "child_started": start, "child_ended": time.monotonic()}
    result = run_schedule(running, work, max_workers=2)
    assert result["status"] == "COMPLETE"
    assert result["scheduler_max_active"] == 2
    one, two = result["actions"]
    assert one["process_id"] != two["process_id"]
    assert max(one["child_started"], two["child_started"]) < min(one["child_ended"], two["child_ended"])


def test_five_actions_dag_wait_and_aggregate():
    p = batch([action("a"), action("b"), action("c", ("a", "b")),
               action("d", ("c",)), action("e")])
    completed = []
    def work(a):
        completed.append(a["action_id"])
        return {"status": "CANDIDATE_READY"}
    out = run_schedule(p, work, max_workers=2)
    assert out["total"] == 5 and out["succeeded"] == 5
    assert completed.index("c") > completed.index("a")
    assert completed.index("c") > completed.index("b")
    assert completed.index("d") > completed.index("c")


def test_failure_blocks_dependent_but_not_independent_sibling():
    p = batch([action("a"), action("b", ("a",)), action("c")])
    out = run_schedule(p, lambda a: {"status": "VERIFICATION_FAILED"} if a["action_id"] == "a"
                       else {"status": "CANDIDATE_READY"})
    states = {a["action_id"]: a["status"] for a in out["actions"]}
    assert states == {"a": "VERIFICATION_FAILED", "b": "BLOCKED_DEPENDENCY",
                      "c": "CANDIDATE_READY"}
    assert out["status"] == "PARTIAL_OR_FAILED"


def test_named_shared_resource_never_overlaps():
    p = batch([action("a", resources=("gpu",)), action("b", resources=("gpu",))])
    out = run_schedule(p, lambda a: (time.sleep(0.12), {"status": "CANDIDATE_READY"})[1],
                       max_workers=2)
    assert out["status"] == "COMPLETE"
    assert out["scheduler_max_active"] == 1


class FakeGitHub(GitHubTransport):
    def __init__(self):
        super().__init__("trung-via/ai-commerce", "fake")
        self.refs = {}
        self.commits = {}
        self.lock = threading.Lock()

    def api(self, method, path, data=None):
        with self.lock:
            if method == "GET" and path.startswith("/git/commits/"):
                sha = path.rsplit("/", 1)[1]
                return self.commits.get(sha, {"tree": {"sha": "b" * 40}})
            if method == "POST" and path == "/git/commits":
                sha = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:40]
                self.commits[sha] = {"message": data["message"], "tree": {"sha": data["tree"]}}
                return {"sha": sha}
            if method == "POST" and path == "/git/refs":
                ref = data["ref"]
                if ref in self.refs:
                    raise RemoteTransportError("GITHUB_HTTP_422")
                self.refs[ref] = data["sha"]
                return {"ref": ref}
            if method == "GET" and path.startswith("/git/ref/"):
                ref = path[len("/git/ref/"):]
                return {"object": {"sha": self.refs["refs/" + ref]}}
        raise AssertionError((method, path))


def test_ten_simultaneous_claims_only_one_can_start_executor():
    github = FakeGitHub()
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: github.claim(source_sha="a"*40,
                         attempt_id="abc-001", fingerprint="1"*64), range(10)))
    assert results.count("CLAIMED") == 1
    assert results.count("DUPLICATE_CLAIM_NO_REEXECUTION") == 9
    assert github.claim(source_sha="a"*40, attempt_id="abc-001",
                        fingerprint="2"*64) == "ATTEMPT_ID_CONFLICT"


class FakeIntegrationGitHub(GitHubTransport):
    """API-level fake for disjoint/fan-in blob tree composition."""

    def __init__(self, conflict=False):
        super().__init__("trung-via/ai-commerce", "fake")
        self.created = []
        base = [{"path": "src/existing.py", "mode": "100644", "type": "blob", "sha": "1"*40}]
        fa = [{"path": "src/existing.py", "mode": "100644", "type": "blob", "sha": "1"*40},
              {"path": "src/alpha.py", "mode": "100644", "type": "blob", "sha": "2"*40}]
        fb = [{"path": "src/existing.py", "mode": "100644", "type": "blob", "sha": "1"*40},
              {"path": "src/beta.py", "mode": "100644", "type": "blob", "sha": "3"*40}]
        if conflict:
            fb[-1]["path"] = "src/alpha.py"
            fb[-1]["sha"] = "4"*40
        self.trees = {"base-tree": base, "alpha-tree": fa, "beta-tree": fb}
        self.commits = {"a"*40: "base-tree", "b"*40: "alpha-tree",
                        "c"*40: "beta-tree"}

    def api(self, method, path, data=None):
        if method == "GET" and path.startswith("/git/commits/"):
            return {"tree": {"sha": self.commits[path.split("/")[-1]]},
                    "message": "EF02 candidate attempt fixture"}
        if method == "GET" and path.startswith("/git/trees/"):
            key = path.split("/")[-1].split("?")[0]
            return {"tree": self.trees[key], "truncated": False}
        if method == "POST" and path == "/git/trees":
            self.created = data["tree"]
            return {"sha": "integrated-tree"}
        if method == "POST" and path == "/git/commits":
            assert data["parents"] == ["a"*40]
            return {"sha": "d"*40}
        if method == "POST" and path == "/git/refs":
            assert data["ref"].startswith("refs/heads/ef02/integrated/")
            return {"ref": data["ref"]}
        raise AssertionError((method, path, data))


def test_dependency_fanin_composes_distinct_candidate_file_trees():
    github = FakeIntegrationGitHub()
    outcome = github.integrated_source(source_sha="a"*40, attempt_id="combine-001",
                                       dependencies=[{"candidate_sha": "b"*40, "status": "CANDIDATE_READY"},
                                                     {"candidate_sha": "c"*40, "status": "CANDIDATE_READY"}])
    assert outcome["effective_sha"] == "d"*40
    assert outcome["integration_ref"].startswith("refs/heads/ef02/integrated/")
    assert {e["path"] for e in github.created} == {"src/alpha.py", "src/beta.py"}


def test_dependency_fanin_fails_on_file_collision_without_silent_merge():
    github = FakeIntegrationGitHub(conflict=True)
    with pytest.raises(RemoteTransportError, match="INTEGRATION_CONFLICT"):
        github.integrated_source(source_sha="a"*40, attempt_id="combine-002",
                                 dependencies=[{"candidate_sha": "b"*40, "status": "CANDIDATE_READY"},
                                               {"candidate_sha": "c"*40, "status": "CANDIDATE_READY"}])


def test_scheduler_passes_pinned_predecessor_results_to_worker():
    p = batch([action("a"), action("b", ("a",))])
    seen = {}
    def work(a):
        seen[a["action_id"]] = a.get("resolved_dependencies", [])
        return {"status": "CANDIDATE_READY", "candidate_sha": a["action_id"] * 40}
    assert run_schedule(p, work)["status"] == "COMPLETE"
    assert seen["a"] == []
    assert len(seen["b"]) == 1
    assert seen["b"][0]["candidate_sha"] == "a" * 40


def test_remote_schema_limits_parallel_request_not_fixed_slot_count():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["properties"]["max_parallel"]["maximum"] == 128
    assert schema["properties"]["max_parallel"]["default"] == 2

def test_ef02_mxc_only_for_windows_codex_service():
    from ai_commerce.remote.worker import _remote_codex_sandbox_args
    assert _remote_codex_sandbox_args("codex", windows=True) == ["--codex-windows-sandbox", "mxc"]
    assert _remote_codex_sandbox_args("codex", windows=False) == []
    assert _remote_codex_sandbox_args("antigravity", windows=True) == []
    assert _remote_codex_sandbox_args("antigravity", windows=False) == []

def test_codex_capacity_failure_reports_distinct_code(tmp_path):
    from ai_commerce.remote.worker import _classify_failed_executor
    evidence = tmp_path / "ef01"
    evidence.mkdir()
    (evidence / "executor.stdout.log").write_text(
        '{"type":"turn.failed","error":{"message":"Selected model is at capacity. Please try a different model."}}',
        encoding="utf-8")
    report = {"status": "EXECUTOR_FAILED", "error_code": "EXECUTOR_NONZERO"}
    assert _classify_failed_executor(report, evidence) == "MODEL_AT_CAPACITY"


def test_capacity_classifier_does_not_confuse_quota_and_unsupported(tmp_path):
    from ai_commerce.remote.worker import _classify_failed_executor
    evidence = tmp_path / "ef01"
    evidence.mkdir()
    log = evidence / "executor.stderr.log"
    report = {"status": "EXECUTOR_FAILED"}
    log.write_text("429 too many requests", encoding="utf-8")
    assert _classify_failed_executor(report, evidence) == "QUOTA_EXHAUSTED"
    log.write_text("model is not supported", encoding="utf-8")
    assert _classify_failed_executor(report, evidence) == "MODEL_UNSUPPORTED"
    log.write_text("command crashed for an unrelated reason", encoding="utf-8")
    assert _classify_failed_executor(report, evidence) == "EXECUTOR_FAILED"

def test_approved_reuse_needs_exact_pinned_profile_and_source():
    from ai_commerce.remote.reuse import approve_reuse
    entry = dict(action_id="a", attempt_id="old-1", executor="codex", model="m",
                 effort="max", depends_on=[], allowed_paths=["tests/one.py"],
                 verification_profile="project-tests", source_sha="a"*40,
                 candidate_sha="b"*40, parent_sha="a"*40, tree_sha="c"*40,
                 verified_run_id=5, verification_status="BUILD_VERIFIED_FOR_SCOPE",
                 verify_exit_code=0, scope_valid=True)
    approved = {"version": 1, "approved_source_sha": "a"*40, "candidates": {"old-1": entry}}
    action = {k: entry[k] for k in ("action_id","attempt_id","executor","model","effort",
                                   "depends_on","allowed_paths","verification_profile")}
    action["reuse_candidate"] = {"candidate_sha": "b"*40, "verified_run_id": 5}
    assert approve_reuse({"source_sha": "a"*40}, action, approved) == entry
    tampered = copy.deepcopy(action)
    tampered["effort"] = "medium"
    with pytest.raises(RemoteRequestError, match="REUSE_PROFILE_OR_SCOPE_MISMATCH"):
        approve_reuse({"source_sha": "a"*40}, tampered, approved)
    tampered = copy.deepcopy(action)
    tampered["reuse_candidate"]["candidate_sha"] = "d"*40
    with pytest.raises(RemoteRequestError, match="REUSE_CANDIDATE_NOT_APPROVED"):
        approve_reuse({"source_sha": "a"*40}, tampered, approved)


def test_reuse_candidate_readonly_path_no_claim_no_model(tmp_path):
    from ai_commerce.remote.worker import LocalBatchWorker
    class StubGithub:
        calls = 0
        def verify_reused_candidate(self, entry):
            self.calls += 1
        def claim(self, **kwargs):
            raise AssertionError("must not claim or invoke provider")
    worker = object.__new__(LocalBatchWorker)
    worker.root = tmp_path
    worker.checkout = tmp_path
    worker.github = StubGithub()
    entry = dict(action_id="a", attempt_id="old-1", executor="codex", model="m",
                 effort="max", depends_on=[], allowed_paths=["tests/one.py"],
                 verification_profile="project-tests", source_sha="a"*40,
                 candidate_sha="b"*40, candidate_ref="refs/heads/ef02/candidate/key",
                 parent_sha="a"*40, tree_sha="c"*40,
                 verified_run_id=5, verification_status="BUILD_VERIFIED_FOR_SCOPE",
                 verify_exit_code=0, scope_valid=True)
    worker.approved_reuse = {"version": 1, "approved_source_sha": "a"*40,
                             "candidates": {"old-1": entry}}
    worker.batch = {"source_sha":"a"*40,"batch_id":"reuse-smoke"}
    act = {k: entry[k] for k in ("action_id","attempt_id","executor","model","effort",
                                "depends_on","allowed_paths","verification_profile")}
    act.update(instruction="No provider calls", reuse_candidate={"candidate_sha":"b"*40, "verified_run_id":5})
    result = worker.execute_action(act)
    assert result["status"] == "UNPROVEN"
    assert result["error_code"] == "LEGACY_REUSE_MT01_EVIDENCE_REQUIRED"
    assert not result["reused_verified"] and not result["executor_invoked"]
    assert worker.github.calls == 1


def test_git_ref_reuse_verification_detects_mismatch():
    class FakeReuseTransport(GitHubTransport):
        def __init__(self):
            super().__init__("trung-via/ai-commerce","fake")
        def api(self, method, path, data=None):
            if "/candidate/" in path:
                return {"object":{"sha":"f"*40}}
            raise AssertionError("bad ref should fail before other reads")
    github=FakeReuseTransport()
    attempt="old-1"
    entry={"attempt_id":attempt,"candidate_sha":"b"*40,
           "candidate_ref":"refs/heads/ef02/candidate/"+github.attempt_key(attempt)}
    with pytest.raises(RemoteTransportError,match="REUSE_REF_SHA_CHANGED"):
        github.verify_reused_candidate(entry)


def test_remote_pre_spawn_ref_is_create_only_and_chained_to_exact_claim():
    fp = "f" * 64
    source = "a" * 40
    claim_sha = "b" * 40
    tree_sha = "c" * 40

    class CheckpointTransport(GitHubTransport):
        def __init__(self):
            super().__init__("trung-via/ai-commerce", "fixture-token")
            self.writes = []
        def api(self, method, path, data=None):
            if method == "GET" and path.startswith("/git/ref/heads/ef02/claims/"):
                return {"object": {"sha": claim_sha}}
            if method == "GET" and path == "/git/commits/" + claim_sha:
                return {"message": "EF02 CLAIM\n\nfingerprint=" + fp +
                        "\nattempt_id=checkpoint-001",
                        "tree": {"sha": tree_sha}, "parents": [{"sha": source}]}
            if method == "POST" and path == "/git/commits":
                self.writes.append((path, data))
                return {"sha": "d" * 40}
            if method == "POST" and path == "/git/refs":
                self.writes.append((path, data))
                return {}
            raise AssertionError("unexpected operation: " + method + " " + path)

    t = CheckpointTransport()
    ref = t.pre_spawn_checkpoint(source_sha=source,
                                 attempt_id="checkpoint-001", fingerprint=fp)
    assert ref == "refs/heads/ef02/pre-spawn/" + t.attempt_key("checkpoint-001")
    assert len(t.writes) == 2
    commit = t.writes[0][1]
    assert commit["tree"] == tree_sha
    assert commit["parents"] == [claim_sha]
    assert commit["message"] == "EF02 PRESPAWN\n\nfingerprint=" + fp + "\nattempt_id=checkpoint-001"
    assert t.writes[1][1] == {"ref": ref, "sha": "d" * 40}


def test_remote_pre_spawn_ref_rejects_wrong_claim_parent_before_any_write():
    class BadClaim(GitHubTransport):
        def __init__(self):
            super().__init__("trung-via/ai-commerce", "fixture-token")
        def api(self, method, path, data=None):
            assert method == "GET", "must not create any commit/ref"
            if "/git/ref/" in path:
                return {"object": {"sha": "b" * 40}}
            return {"message": "EF02 CLAIM\n\nfingerprint=" + "f" * 64 +
                    "\nattempt_id=bad-claim",
                    "tree": {"sha": "c" * 40},
                    "parents": [{"sha": "d" * 40}]}
    with pytest.raises(RemoteTransportError, match="PRESPAWN_CLAIM_IDENTITY_MISMATCH"):
        BadClaim().pre_spawn_checkpoint(source_sha="a" * 40,
                                        attempt_id="bad-claim", fingerprint="f" * 64)


def test_spawned_remote_checkpoint_links_pre_spawn_and_pid():
    source = "a" * 40
    fp = "f" * 64
    attempt = "spawned-pid-001"
    claim_sha = "b" * 40
    pre_sha = "c" * 40
    tree_sha = "d" * 40

    class StageTransport(GitHubTransport):
        def __init__(self):
            super().__init__("trung-via/ai-commerce", "fixture")
            self.writes = []
        def api(self, method, path, data=None):
            if method == "GET" and "/git/ref/heads/ef02/claims/" in path:
                return {"object": {"sha": claim_sha}}
            if method == "GET" and "/git/ref/heads/ef02/pre-spawn/" in path:
                return {"object": {"sha": pre_sha}}
            if method == "GET" and path == "/git/commits/" + claim_sha:
                return {"message": "EF02 CLAIM\n\nfingerprint=" + fp +
                        "\nattempt_id=" + attempt, "tree": {"sha": tree_sha},
                        "parents": [{"sha": source}]}
            if method == "GET" and path == "/git/commits/" + pre_sha:
                return {"message": "EF02 PRESPAWN\n\nfingerprint=" + fp +
                        "\nattempt_id=" + attempt, "tree": {"sha": tree_sha},
                        "parents": [{"sha": claim_sha}]}
            self.writes.append((method, path, data))
            if method == "POST" and path == "/git/commits":
                return {"sha": "e" * 40}
            if method == "POST" and path == "/git/refs":
                return {"ref": data["ref"]}
            raise AssertionError((method, path, data))

    github = StageTransport()
    ref = github.spawned_checkpoint(source_sha=source, attempt_id=attempt,
                                    fingerprint=fp, child_pid=4312)
    assert ref == "refs/heads/ef02/spawned/" + github.attempt_key(attempt)
    assert len(github.writes) == 2
    assert github.writes[0][2] == {
        "message": "EF02 SPAWNED\n\nfingerprint=" + fp +
                   "\nattempt_id=" + attempt + "\nchild_pid=4312",
        "tree": tree_sha, "parents": [pre_sha]}
    assert github.writes[1][2] == {"ref": ref, "sha": "e" * 40}


def test_spawned_checkpoint_rejects_untrusted_parent_without_writes():
    class Fake(GitHubTransport):
        def __init__(self):
            super().__init__("trung-via/ai-commerce", "fixture")
            self.writes = 0
        def api(self, method, path, data=None):
            if method == "POST":
                self.writes += 1
                raise AssertionError("unexpected write")
            if "/git/ref/heads/ef02/claims/" in path:
                return {"object": {"sha": "b" * 40}}
            if path == "/git/commits/" + "b" * 40:
                return {"message": "EF02 CLAIM\n\nfingerprint=" + "f" * 64 +
                        "\nattempt_id=incorrect",
                        "parents": [{"sha": "a" * 40}], "tree": {"sha": "c" * 40}}
            raise AssertionError(path)
    fake = Fake()
    with pytest.raises(RemoteTransportError, match="SPAWNED_CLAIM_IDENTITY_MISMATCH"):
        fake.spawned_checkpoint(source_sha="a" * 40, attempt_id="expected",
                                fingerprint="f" * 64, child_pid=2345)
    assert fake.writes == 0
    with pytest.raises(RemoteTransportError, match="SPAWNED_PID_INVALID"):
        fake.spawned_checkpoint(source_sha="a" * 40, attempt_id="expected",
                                fingerprint="f" * 64, child_pid=0)


def test_ef02_batch_ingress_is_not_a_legacy_m0r_probe():
    """Keep Issue ingress via trusted router and manual dispatch, without M0-R probe."""
    workflow = (ROOT / ".github" / "workflows" / "ef02-batch.yml").read_text(encoding="utf-8")
    router = (ROOT / ".github" / "workflows" / "issue-ingress.yml").read_text(encoding="utf-8")
    assert "types: [opened, labeled]" in router
    assert "startsWith(github.event.issue.title, '[EF02]')" in router
    assert "uses: ./.github/workflows/ef02-batch.yml" in router
    assert "workflow_call:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "startsWith(github.event.issue.title, '[EF02]')" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "needs.validate.result == 'success'" in workflow
    assert "m0r_pre_executor_cancel" not in workflow
    assert "M0-R pre-executor cancellation probe" not in workflow



def test_authenticated_human_full_is_distinct_from_default_profile(monkeypatch):
    from ai_commerce.remote.validation import approved_verification_plan
    registry = verify_registry(REGISTRY)
    packet = batch([action("a")])
    packet["actions"][0]["verification_intent"] = "HUMAN_FULL"
    assert validate(packet) == packet
    assert fingerprint(packet) != fingerprint(batch([action("a")]))
    monkeypatch.setenv("MT01_V21_ENABLED", "1")
    human = approved_verification_plan(packet["actions"][0], registry)
    assert human["human_full_suite_authorized"] is True
    assert human["argv"] == registry["project-tests"]
    assert human["reason"] == "HUMAN_AUTHENTICATED_FULL_SUITE"
    with pytest.raises(RemoteRequestError, match="ISSUER_NOT_AUTHORIZED"):
        validate(packet, issuer="untrusted")
    brain = copy.deepcopy(packet)
    brain["actor"] = "BRAIN"
    with pytest.raises(RemoteRequestError, match="HUMAN_FULL_REQUIRES_OWNER"):
        validate(brain, issuer="brain-bot", brain=("brain-bot",))
    narrow = copy.deepcopy(packet)
    narrow["actions"][0]["verification_profile"] = "ef02-tests"
    with pytest.raises(RemoteRequestError, match="HUMAN_FULL_REQUIRES_OWNER"):
        validate(narrow)
    default_plan = approved_verification_plan(action("a"), registry)
    assert default_plan["human_full_suite_authorized"] is False
    assert default_plan["coverage"] == "V21_REQUIRES_POST_EXECUTOR_IMPACT_PROOF"
    serial = action("a")
    serial["verification_profile"] = "project-tests-serial"
    assert approved_verification_plan(serial, registry)["human_full_suite_authorized"] is False


def test_ef02_independent_receipt_accepts_correct_profile_and_rejects_alias(tmp_path):
    """Exact artifact hashes + registered profile identity; not a mocked PASS."""
    from ai_commerce.remote.worker import _verified_receipt_evidence
    from ai_commerce.engineering.minimum_test import Evidence, file_hashes
    workspace = tmp_path / "worktree"
    root = tmp_path / "evidence"
    workspace.mkdir()
    root.mkdir()
    (workspace / "impl.py").write_text("verified", encoding="utf-8")
    source = "a" * 40
    command = ["python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"]
    profiles = {
        "mt01-component-onboarding": command,
        "mt01-mt01-v21-tests": command,
        "unrelated-alias": ["python", "-m", "pytest", "tests/test_ef02.py", "-q", "-n", "12"],
    }
    binding = {
        "base_sha": source, "aggregate_sources": [],
        "workspace": str(workspace.resolve()),
        "candidate_files": {"impl.py": file_hashes(workspace)["impl.py"]},
        "registered_profile": "mt01-mt01-v21-tests",
        "argv": command, "environment_proven": True,
    }
    refs = {}
    hashes = {}

    def store(key, filename, data):
        (root / filename).write_bytes(data)
        refs[key] = filename
        hashes[key] = hashlib.sha256(data).hexdigest()

    store("tracked_diff", "tracked.patch", b"diff --git a/impl.py b/impl.py\n")
    store("source_after_verify", "source.after.json", b'{"clean": true}')
    store("verify_stdout", "verify.stdout.log", b"1 passed\n")
    store("provider_return_observed", "provider-return.json", b'{"observed": true}')
    passed = b"1 passed in 0.01s\nMT01_XDIST_WORKERS_COLLECTED=12\n"
    store("mt01_0_stdout", "mt01.stdout.log", passed)
    proof = Evidence.from_success(binding, exit_code=0, stdout=passed,
                                  artifacts={"stdout": refs["mt01_0_stdout"]},
                                  evidence_root=root)
    store("mt01_0_evidence", "mt01.evidence.json", proof.payload.encode("utf-8"))
    report = {
        "verify_completed": True, "provider_return_determinate": True,
        "evidence_files": refs, "evidence_sha256": hashes,
        "tracked_diff_sha256": hashes["tracked_diff"],
        "minimum_test_enabled": True,
        "changed_files": ["impl.py"],
        "minimum_test": {"groups": [
            {"group": "mt01-v21", "binding": binding,
             "evidence_sha256": proof.sha256},
        ]},
    }
    args = dict(root=root, workspace=workspace, source_sha=source,
                sources=[], profiles=profiles, allowed_paths=["impl.py"])
    assert _verified_receipt_evidence(report, **args) is True
    import copy
    for forged_name in ("mt01-component-onboarding", "unrelated-alias", "unknown-profile"):
        bad = copy.deepcopy(report)
        bad["minimum_test"]["groups"][0]["binding"]["registered_profile"] = forged_name
        diagnostics = []
        assert _verified_receipt_evidence(bad, **args, diagnostics=diagnostics) is False
        assert diagnostics == ["MT01_GROUP_0_REGISTERED_BINDING_REJECTED"]
    tampered = copy.deepcopy(report)
    tampered["evidence_sha256"]["tracked_diff"] = "0" * 64
    assert _verified_receipt_evidence(tampered, **args) is False
    for bad_stdout in (
        b"1 passed in 0.01s\n",
        b"1 passed in 0.01s\nMT01_XDIST_WORKERS_COLLECTED=4\n",
        b"1 passed in 0.01s\\nMT01_XDIST_WORKERS_COLLECTED=12\\n",
    ):
        bad_xdist = copy.deepcopy(report)
        (root / "mt01.stdout.log").write_bytes(bad_stdout)
        bad_xdist["evidence_sha256"]["mt01_0_stdout"] = hashlib.sha256(bad_stdout).hexdigest()
        bad_proof = Evidence.from_success(
            binding, exit_code=0, stdout=bad_stdout,
            artifacts={"stdout": refs["mt01_0_stdout"]}, evidence_root=root)
        (root / "mt01.evidence.json").write_bytes(bad_proof.payload.encode("utf-8"))
        bad_xdist["evidence_sha256"]["mt01_0_evidence"] = bad_proof.sha256
        bad_xdist["minimum_test"]["groups"][0]["evidence_sha256"] = bad_proof.sha256
        diagnostics = []
        assert _verified_receipt_evidence(bad_xdist, **args, diagnostics=diagnostics) is False
        assert diagnostics == ["MT01_GROUP_0_XDIST_UNPROVEN"]

# Minimal immutable retirement security regression; no WorkOwner runtime.
import pytest

from ai_commerce.remote.validation import (
    RemoteRequestError, reject_retired_wp_identifiers,
)
from ai_commerce.remote.worker import LocalBatchWorker


@pytest.mark.parametrize("key,value", [
    ("batch_id", "EPWO-REPLAY"),
    ("action_id", "WP01-a"),
    ("attempt_id", "WP06-a"),
    ("action_id", "wP03-mixed"),
])
def test_retired_machine_identifier_denied(key, value):
    batch = {"batch_id": "fresh-nonwp", "actions": [{
        "action_id": "safe", "attempt_id": "safe-attempt",
    }]}
    if key == "batch_id":
        batch["batch_id"] = value
    else:
        batch["actions"][0][key] = value
    with pytest.raises(RemoteRequestError, match="RETIRED_WP_NOT_DISPATCHABLE"):
        reject_retired_wp_identifiers(batch)


def test_unrelated_nonwp_prose_is_not_a_program_identifier():
    reject_retired_wp_identifiers({
        "batch_id": "safe-batch",
        "description": "Historical EPWO audit must remain read only",
        "actions": [{"action_id": "fresh", "attempt_id": "fresh-1"}],
    })


def test_direct_worker_no_claim_no_provider_on_retired_id(tmp_path):
    worker = LocalBatchWorker.__new__(LocalBatchWorker)
    worker.root = tmp_path
    worker.batch = {"batch_id": "safe-batch", "source_sha": "a" * 40}
    action = {
        "action_id": "WP01-replay", "attempt_id": "old-attempt",
        "executor": "codex", "model": "gpt-6.1-sol", "effort": "xhigh",
        "depends_on": [],
    }
    outcome = worker.execute_action(action)
    assert outcome["status"] == "INVALID_ACTION"
    assert outcome["error_code"] == "RETIRED_WP_NOT_DISPATCHABLE"
    assert outcome["executor_invoked"] is False

def test_new_worktree_path_is_short_stable_and_unique(tmp_path):
    """Windows EF02 must never derive a tool cwd from a long action_id."""
    from ai_commerce.remote.worker import _new_worktree_path
    import subprocess
    checkout = tmp_path / "runner" / "checkout"
    checkout.mkdir(parents=True)
    first = _new_worktree_path(checkout)
    second = _new_worktree_path(checkout)
    assert first != second
    assert first.parent == checkout.parent / "_ef02_worktrees"
    assert not first.exists() and not second.exists()
    assert first.name.startswith("wt-")
    # A real Git worktree at this location remains available to rev-parse.
    subprocess.run(["git", "-C", str(checkout), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.email", "fixture@example.invalid"], check=True)
    (checkout / "README.md").write_text("base\\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(checkout), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "base"], check=True)
    subprocess.run(["git", "-C", str(checkout), "worktree", "add", "--detach", str(first), "HEAD"],
                   check=True, capture_output=True)
    head = subprocess.run(["git", "-C", str(first), "rev-parse", "HEAD"],
                          check=True, text=True, capture_output=True).stdout.strip()
    assert len(head) == 40
    assert (first / ".git").is_file()


def test_agy_stream_p0b_opt_in_is_owner_only_and_does_not_affect_regular_ef02():
    """Only an owner-created opened P0-B Issue can enable opt-in profiling.

    This checks the protected workflow's exact source contract, not a claimed
    native/provider execution. Regular and workflow_dispatch batches stay off.
    """
    workflow = (ROOT / ".github/workflows/ef02-batch.yml").read_text(encoding="utf-8")
    marker = "AI_COMMERCE_EF01_AGY_STREAM_PROFILE:"
    assert workflow.count(marker) == 1
    execute = workflow.split("- name: Execute bounded concurrent EF01 jobs", 1)[1]
    assert marker in execute
    expression = next(line.strip() for line in execute.splitlines()
                      if line.strip().startswith(marker))
    assert "github.event_name == 'issues'" in expression
    assert "github.event.action == 'opened'" in expression
    assert "github.repository == 'trung-via/ai-commerce'" in expression
    assert "github.actor == github.repository_owner" in expression
    assert "github.event.issue.user.login == github.repository_owner" in expression
    assert "startsWith(github.event.issue.title, '[EF02][P0B-AGY-STREAM-SMOKE] ')" in expression
    assert "&& '1' || '0' }}" in expression
    assert "vars.EF02_AGY_STREAM_PROFILE" not in workflow
    assert "dangerously-skip-permissions" not in workflow


def test_ef02_verified_receipt_accepts_committed_manifest_and_rejects_xdist_or_hash_tamper(tmp_path):
    """EF02 independently verifies committed manifest bindings and -n 12 evidence."""
    import copy
    import subprocess
    import sys
    from ai_commerce.engineering.impact_v21 import registered_action_plan
    from ai_commerce.engineering.minimum_test import (
        Evidence, TestGroup, digest, file_hashes,
    )
    from ai_commerce.remote.worker import _verified_receipt_evidence

    workspace = tmp_path / "worktree"
    ev_root = tmp_path / "evidence"
    workspace.mkdir()
    ev_root.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=workspace, check=True,
            capture_output=True, text=True).stdout.strip()

    def write(rel, content):
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")

    git("init", "-q")
    git("config", "user.name", "MT01")
    git("config", "user.email", "mt01@example.invalid")
    git("config", "core.autocrlf", "false")
    src = "src/ai_commerce/alpha/mod.py"
    test_file = "tests/test_alpha.py"
    write(src, "def val():\n    return 1\n")
    write(test_file, "def test_alpha():\n    assert True\n")
    manifest = {
        "schema_version": "MT01ComponentOwner.v1", "component": "alpha",
        "sources": [src], "tests": [test_file], "acs": ["ALPHA.offline"],
        "consumers": [], "contracts": [],
    }
    write("work_items/mt01-components/alpha.json", json.dumps(manifest) + "\n")
    git("add", ".")
    git("commit", "-qm", "baseline")
    base_sha = git("rev-parse", "HEAD")
    base_files = file_hashes(workspace)

    # Candidate edits only the owned source (manifest remains unchanged baseline).
    write(src, "def val():\n    return 2\n")
    profiles = {
        "project-tests": ["python", "-m", "pytest", "tests", "-q", "-n", "12"],
        "mt01-component-onboarding": [
            "python", "-m", "pytest", "tests/test_mt01_v21.py", "-q", "-n", "12"],
    }
    plan = registered_action_plan(
        repo=workspace, base_sha=base_sha, base_files=base_files,
        candidate_files=file_hashes(workspace), changed_paths=[src],
        required_acs=("ALPHA.offline",), fallback_argv=profiles["project-tests"],
        groups=(), registered_profiles=profiles, allowed_paths=[src])
    assert plan["selected_groups"] == ["alpha"]
    binding = copy.deepcopy(plan["groups"][0]["binding"])
    # Anchor python provenance deterministically for offline receipt creation.
    binding["executable_sha256"] = digest(Path(sys.executable).read_bytes())
    binding["environment_proven"] = True

    refs, hashes = {}, {}

    def store(key, filename, data):
        (ev_root / filename).write_bytes(data)
        refs[key] = filename
        hashes[key] = hashlib.sha256(data).hexdigest()

    store("tracked_diff", "tracked.patch", b"diff --git a/x b/x\n")
    store("source_after_verify", "source.after.json", b'{"clean": true}')
    store("verify_stdout", "verify.stdout.log", b"1 passed\n")
    store("provider_return_observed", "provider-return.json", b'{"observed": true}')
    passed = b"1 passed in 0.01s\nMT01_XDIST_WORKERS_COLLECTED=12\n"
    store("mt01_0_stdout", "mt01.stdout.log", passed)
    proof = Evidence.from_success(
        binding, exit_code=0, stdout=passed,
        artifacts={"stdout": refs["mt01_0_stdout"]}, evidence_root=ev_root)
    store("mt01_0_evidence", "mt01.evidence.json", proof.payload.encode("utf-8"))

    report = {
        "verify_completed": True, "provider_return_determinate": True,
        "evidence_files": refs, "evidence_sha256": hashes,
        "tracked_diff_sha256": hashes["tracked_diff"],
        "minimum_test_enabled": True, "changed_files": [src],
        "minimum_test": {"groups": [
            {"group": "alpha", "binding": binding, "evidence_sha256": proof.sha256},
        ]},
    }
    args = dict(root=ev_root, workspace=workspace, source_sha=base_sha,
                sources=[], profiles=profiles, allowed_paths=[src])
    assert _verified_receipt_evidence(report, **args) is True

    # Missing -n 12 worker collection marker -> rejected.
    bad_xdist = copy.deepcopy(report)
    bad_stdout = b"1 passed in 0.01s\nMT01_XDIST_WORKERS_COLLECTED=4\n"
    (ev_root / "mt01.stdout.log").write_bytes(bad_stdout)
    bad_xdist["evidence_sha256"]["mt01_0_stdout"] = hashlib.sha256(bad_stdout).hexdigest()
    bad_proof = Evidence.from_success(
        binding, exit_code=0, stdout=bad_stdout,
        artifacts={"stdout": refs["mt01_0_stdout"]}, evidence_root=ev_root)
    (ev_root / "mt01.evidence.json").write_bytes(bad_proof.payload.encode("utf-8"))
    bad_xdist["evidence_sha256"]["mt01_0_evidence"] = bad_proof.sha256
    bad_xdist["minimum_test"]["groups"][0]["evidence_sha256"] = bad_proof.sha256
    diagnostics = []
    assert _verified_receipt_evidence(bad_xdist, **args, diagnostics=diagnostics) is False
    assert diagnostics == ["MT01_GROUP_0_XDIST_UNPROVEN"]

    # Out-of-scope allowed_paths -> rejected.
    diagnostics = []
    assert _verified_receipt_evidence(
        bad_xdist, **{**args, "allowed_paths": ["src/ai_commerce/other"]},
        diagnostics=diagnostics) is False
    assert diagnostics == ["MT01_GROUP_0_REGISTERED_BINDING_REJECTED"]


def test_temporary_human_full_verification_budget_is_bounded_and_opt_in():
    from ai_commerce.remote.worker import _temporary_verification_budget
    from ai_commerce.engineering.runner import DEFAULT_ACTION_TIMEOUT_SECONDS

    # Normal AUTO/owner-first actions are NOT granted the long-test budget.
    assert _temporary_verification_budget(human_full_suite_authorized=False) == (
        None, DEFAULT_ACTION_TIMEOUT_SECONDS, 7500,
    )
    # An already-authorized HUMAN_FULL profile receives 180m of pytest inside
    # a finite 300m action and a 305m EF02 child-process watchdog.
    verify, action, child = _temporary_verification_budget(human_full_suite_authorized=True)
    assert (verify, action, child) == (10800, 18000, 18300)
    assert verify < action < child < 360 * 60
