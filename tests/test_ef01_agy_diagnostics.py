"""Offline provider fixtures; these are not live Antigravity permission evidence."""
import copy
import hashlib
import json
import math
import os
import shlex
import shutil
import stat
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_commerce.engineering import runner
from ai_commerce.engineering.profile import ExecutorSelection
from ai_commerce.engineering import adapters


@pytest.mark.skipif(os.name != "nt", reason="native Windows Git loose-object diagnostic")
def test_native_git_loose_object_rmtree_winerror5(tmp_path, record_property):
    """Real Git/NTFS discriminator; no provider, locks, ACL or Defender inference."""
    from test_ef01 import _repo
    tree = tmp_path / "owned-git-repro"
    tree.mkdir()
    repo = _repo(tree)
    child = subprocess.Popen(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _ = child.communicate(timeout=10)
    assert child.returncode == 0 and len(stdout.strip()) == 40
    with pytest.raises(PermissionError) as raised:
        shutil.rmtree(tree)
    failure = raised.value
    target = Path(failure.filename)
    assert tree in target.parents and ".git" in target.parts and "objects" in target.parts
    info = target.lstat()
    diagnostic = {"winerror": failure.winerror, "readonly": bool(info.st_file_attributes & 1),
                  "reparse": bool(info.st_file_attributes & 0x400), "mode": info.st_mode,
                  "links": info.st_nlink, "file_identity": [info.st_dev, info.st_ino],
                  "worker": os.environ.get("PYTEST_XDIST_WORKER"), "pid": os.getpid(),
                  "git_child_pid": child.pid, "git_child_exit": child.returncode,
                  "classification": "READONLY_GIT_LOOSE_OBJECT_REPRODUCED"}
    assert diagnostic["winerror"] == 5 and diagnostic["readonly"] and not diagnostic["reparse"]
    record_property("native_scratch_discriminator", json.dumps(diagnostic, sort_keys=True))
    print(json.dumps(diagnostic, sort_keys=True))
    identity, parent_identity = tree.stat(), tmp_path.stat()
    result = adapters.agy_remove_owned_tree(tree, tmp_path,
        (identity.st_dev, identity.st_ino), (parent_identity.st_dev, parent_identity.st_ino))
    assert result["status"] == "CLEAN" and result["readonly_files_cleared"] >= 1
    assert not tree.exists()


def input_fixture(tmp_path, feedback=True):
    repo, mirror = tmp_path / "source", tmp_path / "input"
    repo.mkdir()
    mirror.mkdir()
    expected = {"instruction.txt": 'Sửa mã nguồn "Việt" \\ 🚀'.encode()}
    if feedback:
        expected["repair_feedback.json"] = b'{"trusted_host_feedback":true}\n'
    for name, data in expected.items():
        (mirror / name).write_bytes(data)
    baseline = adapters.agy_input_manifest(repo, mirror, expected)
    assert baseline["status"] == "INTACT", baseline
    return repo, mirror, expected, baseline


def test_benign_sidecar_keeps_contract_hash_and_is_never_read(tmp_path, monkeypatch):
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    (mirror / "pytest-session.log").write_bytes(b"not an instruction")
    # The old guard rejects exactly this reproduction before comparing bytes.
    with pytest.raises(ValueError, match="AGY_EVIDENCE_DIR_NOT_PREPARED"):
        adapters.agy_input_files(repo, mirror)
    original = adapters.agy_read_regular
    def read_contract_only(path, limit):
        assert path.name in expected
        return original(path, limit)
    monkeypatch.setattr(adapters, "agy_read_regular", read_contract_only)
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "INTACT"
    contract = next(f for f in result["files"] if f["name"] == "instruction.txt")
    assert contract["sha256"] == hashlib.sha256(expected["instruction.txt"]).hexdigest()
    sidecar = next(f for f in result["files"] if f["role"] == "AUXILIARY")
    assert sidecar["sha256"] is None and sidecar["hash_status"] == "AUXILIARY_CONTENT_NOT_READ"


@pytest.mark.parametrize("name", ["instruction.txt", "repair_feedback.json"])
@pytest.mark.parametrize("change,reason", [("modify", "MODIFIED"), ("delete", "DELETED"), ("replace", "REPLACED")])
def test_contract_tampering_has_exact_reason(tmp_path, name, change, reason):
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    target = mirror / name
    if change == "modify":
        target.write_bytes(b"tampered")
    elif change == "delete":
        target.unlink()
    else:
        # Keep the old inode alive to make identity replacement deterministic.
        target.rename(tmp_path / "old-contract")
        target.write_bytes(expected[name])
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD"
    assert result["reason"] == "AGY_INPUT_CONTRACT_" + reason


@pytest.mark.parametrize("name", ["settings.json", "AGENTS.md", "metadata.json", "pytest.ini",
                                  "pytest-session.exe", "pytest-session.py", "scratch-session.cmd",
                                  "cache-agent.log.json", "repair_feedback.json"])
def test_unknown_config_and_executable_names_hold(tmp_path, name):
    repo, mirror, expected, baseline = input_fixture(tmp_path, feedback=False)
    (mirror / name).write_bytes(b"opaque")
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD"
    assert result["reason"] in ("AGY_INPUT_UNEXPECTED_AUXILIARY", "AGY_INPUT_EXECUTABLE")


@pytest.mark.parametrize("target", ["instruction.txt", "pytest-session.log"])
def test_hardlinks_hold_without_opening_external_bytes(tmp_path, target):
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    outside = tmp_path / "external"
    outside.write_bytes(b"private outside")
    (mirror / target).unlink(missing_ok=True)
    try:
        os.link(outside, mirror / target)
    except PermissionError:
        pytest.skip("native hardlink creation denied; no bypass")
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD" and result["reason"] == "AGY_INPUT_HARDLINK"


def test_reported_hardlink_metadata_holds_even_when_native_creation_is_unavailable(tmp_path, monkeypatch):
    """Simulated stat metadata, explicitly not native hardlink proof."""
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    original = Path.lstat
    target = mirror / "instruction.txt"
    def linked_stat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size,
                                   st_file_attributes=getattr(info, "st_file_attributes", 0), st_nlink=2)
        return info
    monkeypatch.setattr(Path, "lstat", linked_stat)
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD" and result["reason"] == "AGY_INPUT_HARDLINK"


@pytest.mark.parametrize("target", ["instruction.txt", "pytest-session.log"])
def test_reparse_entries_hold_and_cleanup_never_follows_target(tmp_path, target):
    repo, _, _, _ = input_fixture(tmp_path)
    from test_ef01 import _directory_alias
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_bytes(b"keep")
    with runner._agy_input_mirror(repo, "task", None) as mirror:
        expected = adapters.agy_input_files(repo, mirror)
        baseline = adapters.agy_input_manifest(repo, mirror, expected)
        (mirror / target).unlink(missing_ok=True)
        try:
            _directory_alias(outside, mirror / target)
        except PermissionError:
            pytest.skip("native reparse creation denied; no bypass")
        result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
        assert result["status"] == "HOLD" and result["reason"] == "AGY_INPUT_NON_REGULAR"
    assert (outside / "sentinel.txt").read_bytes() == b"keep"


@pytest.mark.skipif(os.name != "nt", reason="native NTFS ADS check")
@pytest.mark.parametrize("target", ["instruction.txt", "pytest-session.log", "."])
def test_ntfs_alternate_streams_hold(tmp_path, target):
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    if target == "pytest-session.log":
        (mirror / target).write_bytes(b"opaque")
    stream_path = str(mirror if target == "." else mirror / target) + ":hidden"
    with open(stream_path, "wb") as stream_file:
        stream_file.write(b"hidden payload")
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD" and result["reason"] == "AGY_INPUT_NAMED_STREAM"


@pytest.mark.parametrize("kind", ["directory", "count", "size", "root-swap", "traversal"])
def test_input_bounds_and_root_replacement_hold(tmp_path, kind):
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    if kind == "directory":
        (mirror / "pytest-dir.log").mkdir()
    elif kind == "count":
        for index in range(17):
            (mirror / f"pytest-{index}.log").write_bytes(b"")
    elif kind == "size":
        with (mirror / "cache-large.tmp").open("wb") as file:
            file.truncate(1024 * 1024 + 1)
    elif kind == "root-swap":
        mirror.rename(tmp_path / "old-input")
        mirror.mkdir()
        for name, data in expected.items():
            (mirror / name).write_bytes(data)
    else:
        mirror = mirror / ".." / "input"
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD"


def test_contract_race_is_observed_before_read(tmp_path, monkeypatch):
    repo, mirror, expected, baseline = input_fixture(tmp_path)
    original = adapters.os.open
    def racing_open(path, flags, *args, **kwargs):
        if Path(path).name == "instruction.txt":
            (mirror / "instruction.txt").write_bytes(b"raced")
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(adapters.os, "open", racing_open)
    result = adapters.agy_input_manifest(repo, mirror, expected, baseline)
    assert result["status"] == "HOLD" and result["reason"] == "AGY_INPUT_RACE"


def test_cleanup_refuses_to_delete_replacement_directory(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(tmp_path))
    with pytest.raises(ValueError, match="AGY_INPUT_CLEANUP_UNSAFE"):
        with runner._agy_input_mirror(repo, "task", None) as mirror:
            mirror.rename(tmp_path / "original-input")
            mirror.mkdir()
            (mirror / "unrelated.txt").write_bytes(b"keep")
    assert (mirror / "unrelated.txt").read_bytes() == b"keep"
    assert (tmp_path / "original-input/instruction.txt").read_bytes() == b"task"


def test_scratch_is_a_distinct_explicit_root_and_preserves_model(tmp_path):
    repo, mirror, _, _ = input_fixture(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    selection = ExecutorSelection("antigravity", "gemini-3.8-flash", "high")
    argv, stdin = adapters.command_for(selection, repo, "task", antigravity_evidence_dir=mirror,
                                       antigravity_scratch_dir=scratch)
    assert stdin is None
    assert [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--add-dir"] == list(map(str, (repo, mirror, scratch)))
    assert argv[argv.index("--model") + 1] == selection.model
    assert argv[argv.index("--effort") + 1] == "high"
    for alias in (repo, mirror, mirror / ".." / "scratch"):
        with pytest.raises(ValueError):
            adapters.command_for(selection, repo, "task", antigravity_evidence_dir=mirror,
                                 antigravity_scratch_dir=alias)


def test_agy_cache_environment_is_source_owned_and_child_only(tmp_path):
    repo, mirror, _, _ = input_fixture(tmp_path)
    scratch = tmp_path / "scratch with spaces"
    scratch.mkdir()
    inherited = {"PYTEST_ADDOPTS": f'-p cacheprovider -o cache_dir="{mirror}"',
                 "PYTHONDONTWRITEBYTECODE": "0", "PYTHONPYCACHEPREFIX": str(repo),
                 "XDG_CACHE_HOME": str(mirror), "TEMP": str(mirror),
                 "AI_COMMERCE_PYTEST_SCRATCH": str(mirror),
                 "AI_COMMERCE_PYTEST_REPO": str(mirror),
                 "PYTEST_PLUGINS": "ai_commerce.engineering.profile,provider_own_plugin",
                 "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
                 "GIT_CONFIG_VALUE_0": "unchanged guard", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    before = inherited.copy()
    child = runner._agy_child_env(inherited, scratch, repo=repo)
    assert inherited == before
    assert child["PYTEST_PLUGINS"] == "provider_own_plugin"
    assert shlex.split(child["PYTEST_ADDOPTS"]) == ["-p", "no:cacheprovider",
        "-p", "no:ai_commerce.engineering.profile",
        "--basetemp=" + str(scratch / "pytest-basetemp")]
    assert child["PYTHONDONTWRITEBYTECODE"] == "1"
    assert child["PYTHONPYCACHEPREFIX"] == str(scratch / "pycache")
    assert child["XDG_CACHE_HOME"] == str(scratch / "user-cache")
    assert child["AI_COMMERCE_PYTEST_SCRATCH"] == str(scratch)
    assert child["AI_COMMERCE_PYTEST_REPO"] == str(repo)
    assert all(child[key] == str(scratch) for key in ("TEMP", "TMP", "TMPDIR"))
    assert all(child[key] == value for key, value in inherited.items()
               if key.startswith("GIT_") or key == "PYTEST_DISABLE_PLUGIN_AUTOLOAD")


def test_owned_cache_policy_overrides_case_variants_in_actual_child(tmp_path):
    source, mirror, expected, _ = input_fixture(tmp_path)
    scratch = tmp_path / "private-scratch"
    scratch.mkdir()
    hostile = dict(os.environ)
    hostile.update({"UV_LINK_MODE": "hardlink", "uv_link_mode": "hardlink",
                    "UV_CACHE_DIR": str(mirror), "uv_cache_dir": str(source),
                    "PIP_CACHE_DIR": str(source), "pip_cache_dir": str(mirror),
                    "npm_config_cache": str(source), "NPM_CONFIG_CACHE": str(mirror),
                    "temp": str(source)})
    before = dict(hostile)
    child = runner._agy_child_env(hostile, scratch, repo=source)
    assert hostile == before
    assert len({key.upper() for key in child}) == len(child)
    canary = subprocess.run([sys.executable, "-I", "-B", "-c",
        "import json,os; print(json.dumps({k:os.environ[k] for k in "
        "('UV_LINK_MODE','UV_CACHE_DIR','PIP_CACHE_DIR','npm_config_cache','TEMP')}))"],
        cwd=scratch, env=child, capture_output=True, check=True, timeout=15)
    observed = json.loads(canary.stdout)
    assert observed.pop("UV_LINK_MODE") == "copy"
    assert observed.pop("TEMP") == str(scratch)
    assert all(Path(value).parent == scratch for value in observed.values())
    assert {p.name: p.read_bytes() for p in mirror.iterdir()} == expected
    assert list(source.iterdir()) == []


def test_only_fresh_provider_scratch_enables_internal_link_proof(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    seen = []
    def cleanup(root, parent, identity, parent_identity, **kwargs):
        assert (root.stat().st_dev, root.stat().st_ino) == identity
        seen.append(kwargs)
        root.rmdir()
        return {"status": "CLEAN", "reason": None}
    monkeypatch.setattr(runner, "agy_remove_owned_tree", cleanup)
    for prefix in ("agy_scratch-", "agy_input-", "unrecognized-"):
        with runner._agy_temp_tree(source, prefix):
            pass
    assert seen == [{"scratch_internal_links": True}, {}, {}]


@pytest.mark.parametrize("name", [
    r"\outside\leaf", r"\owned-other\leaf", r"\Owned\leaf", r"\\server\owned\leaf",
    r"\owned\..\outside", r"\owned\.\leaf", r"\owned\file:stream",
    "\\owned\\leaf ", "\\owned\\leaf.", "\\owned\\", r"\owned\a/b", r"\owned\\leaf",
])
def test_modeled_hardlink_names_reject_foreign_and_inexact_paths(name):
    """Pure name validation, not native hardlink evidence."""
    with pytest.raises(ValueError):
        adapters._agy_link_relative(name, r"\owned")


def test_modeled_hardlink_names_preserve_exact_case_and_components():
    assert adapters._agy_link_relative(r"\owned\Branch\Leaf", r"\owned") == ("Branch", "Leaf")


@pytest.mark.parametrize("fault", ["zero-volume", "zero-id", "query-failed"])
def test_modeled_full_file_id_rejects_unproven_identity(fault):
    class Query:
        def GetFileInformationByHandleEx(self, handle, kind, value, size):
            assert kind == 18 and size == 24
            value._obj.volume = 0 if fault == "zero-volume" else 1
            value._obj.identifier[15] = 0 if fault == "zero-id" else 1
            return fault != "query-failed"
    with pytest.raises(ValueError, match="AGY_SCRATCH_ENTRY_UNSAFE"):
        adapters._AgyScratchLinks.file_id(Query(), 1)


def test_modeled_full_file_id_keeps_high_volume_and_file_bits():
    class Query:
        def GetFileInformationByHandleEx(self, handle, kind, value, size):
            value._obj.volume = (1 << 63) + 7
            value._obj.identifier[0] = 1
            value._obj.identifier[15] = handle
            return True
    first = adapters._AgyScratchLinks.file_id(Query(), 1)
    second = adapters._AgyScratchLinks.file_id(Query(), 2)
    assert first[0] == (1 << 63) + 7 and first != second
    assert first[1][15] == 1 and len(first[1]) == 16


@pytest.mark.skipif(os.name != "nt", reason="Windows enumeration API error model")
@pytest.mark.parametrize("fault", ["access", "more-data", "short", "duplicate", "overflow"])
def test_modeled_hardlink_enumeration_never_accepts_partial_or_duplicate_names(fault):
    """API faults are modeled explicitly; no native hardlink claim."""
    import ctypes
    closed = []
    class Query:
        def FindFirstFileNameW(self, path, flags, size, buffer):
            buffer.value = r"\owned\a"
            return ctypes.c_void_p(-1).value if fault == "access" else 42
        def FindNextFileNameW(self, handle, size, buffer):
            if fault in ("duplicate", "overflow"):
                buffer.value = r"\owned\a" if fault == "duplicate" else r"\owned\b"
                return True
            ctypes.set_last_error(234 if fault == "more-data" else 38)
            return False
        def FindClose(self, handle):
            closed.append(handle)
    scope = adapters._AgyScratchLinks(None, None, lambda: None)
    with pytest.raises(ValueError):
        scope.names(Query(), "modeled", 1 if fault == "overflow" else 2)
    assert closed == ([] if fault == "access" else [42])


def _required_native_link(source, alias):
    try:
        os.link(source, alias)
    except OSError as exc:
        native_error = getattr(exc, "winerror", None)
    else:
        return
    # Raise outside the handler: exception chaining would print private paths.
    pytest.fail("SANDBOX_NATIVE_LINK_UNAVAILABLE / UNPROVEN_MODEL_SANDBOX "
                "when executed in a model sandbox; independent HOST must HOLD "
                f"if unavailable there (winerror={native_error}). No native proof.",
                pytrace=False)


def test_native_link_denial_diagnostic_excludes_private_paths(tmp_path, monkeypatch):
    private = tmp_path / "private-sentinel"
    def denied(*args):
        raise PermissionError(13, "access denied", str(private))
    monkeypatch.setattr(os, "link", denied)
    with pytest.raises(pytest.fail.Exception) as raised:
        _required_native_link(private, tmp_path / "alias")
    assert "UNPROVEN_MODEL_SANDBOX" in str(raised.value)
    assert str(private) not in str(raised.value)
    assert raised.value.__context__ is None


def _native_outside_state(path):
    """Metadata and security descriptor without reading/changing file data."""
    import ctypes
    from ctypes import wintypes as w
    get_security = ctypes.WinDLL("advapi32", use_last_error=True).GetFileSecurityW
    get_security.argtypes = [w.LPCWSTR, w.DWORD, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)]
    get_security.restype = w.BOOL
    size = w.DWORD()
    get_security(str(path), 7, None, 0, ctypes.byref(size))
    assert size.value
    buffer = ctypes.create_string_buffer(size.value)
    assert get_security(str(path), 7, buffer, len(buffer), ctypes.byref(size))
    info = path.lstat()
    return (info.st_dev, info.st_ino, info.st_nlink, info.st_mode, info.st_size,
            info.st_mtime_ns, getattr(info, "st_birthtime_ns", info.st_ctime_ns),
            info.st_file_attributes, buffer.raw)


def _scratch_link_cleanup(tree, *, timeout_seconds=adapters.AGY_CLEANUP_DEFAULT_SECONDS):
    info, parent = tree.lstat(), tree.parent.lstat()
    return adapters.agy_remove_owned_tree(tree, tree.parent, (info.st_dev, info.st_ino),
        (parent.st_dev, parent.st_ino), timeout_seconds=timeout_seconds, scratch_internal_links=True)


@pytest.mark.skipif(os.name != "nt", reason="native containment APIs on an ordinary single-link file")
def test_native_scratch_single_link_containment_api_roundtrip(tmp_path):
    """Exercise real enumeration/volume/ID/pins; not multi-link native AC."""
    tree = tmp_path / "fresh-private-scratch"
    tree.mkdir()
    branch = tree / "branch"
    branch.mkdir()
    leaf = branch / "single-link"
    leaf.write_bytes(b"ordinary")
    root_info, info = tree.lstat(), leaf.lstat()
    checks = []
    scope = adapters._AgyScratchLinks(tree, (root_info.st_dev, root_info.st_ino),
                                      lambda: checks.append(True))
    handle = adapters._AgyCleanupHandle(leaf, info, link_scope=scope)
    try:
        with scope.prove(handle, info):
            handle.delete(info, {"readonly_files_cleared": 0})
    finally:
        handle.close()
    assert checks and not leaf.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows pytest symlink proof")
def test_native_scoped_pytest_current_link_clean_without_following_target(tmp_path):
    tree = tmp_path / "fresh-private-scratch"
    pytest_dir = tree / "pytest-basetemp"
    pytest_dir.mkdir(parents=True)
    target = pytest_dir / "test_example_0"
    target.mkdir()
    (target / "data").write_bytes(b"owned")
    link = pytest_dir / "test_example_current"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("native directory symlink creation denied")
    assert link.is_symlink()
    result = _scratch_link_cleanup(tree)
    assert result["status"] == "CLEAN", result
    assert result["reparse_links_removed"] == 1
    assert not tree.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows foreign pytest-style symlink denial")
@pytest.mark.parametrize("scenario", ["outside", "bad-name", "junction"])
def test_native_scoped_pytest_current_link_rejects_unproven_target(tmp_path, scenario):
    from test_ef01 import _directory_alias
    tree = tmp_path / "fresh-private-scratch"
    pytest_dir = tree / "pytest-basetemp"
    pytest_dir.mkdir(parents=True)
    outside = tmp_path / "protected-external"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"never alter")
    internal = pytest_dir / "test_example_0"
    internal.mkdir()
    alias = pytest_dir / ("unexpected_alias" if scenario == "bad-name" else "test_example_current")
    try:
        if scenario == "junction":
            _directory_alias(outside, alias)
        else:
            alias.symlink_to(outside if scenario == "outside" else internal,
                             target_is_directory=True)
    except OSError:
        pytest.skip("native link privilege not available")
    result = _scratch_link_cleanup(tree)
    assert result["status"] == "HOLD", result
    assert result["first_unsafe_entry"]["subtype"] == "REPARSE_UNPROVEN", result
    assert alias.is_symlink() or adapters.is_link(alias)
    assert sentinel.read_bytes() == b"never alter"
    assert (pytest_dir / "test_example_0").exists()




@pytest.mark.skipif(os.name != "nt", reason="native scratch lifecycle without hardlink creation")
def test_native_scratch_single_link_cleanup_retains_readonly_behavior(tmp_path):
    """Single-link compatibility only; not a substitute for hardlink AC."""
    tree = tmp_path / "fresh-private-scratch"
    tree.mkdir()
    branch = tree / "branch"
    branch.mkdir()
    leaf = branch / "readonly"
    leaf.write_bytes(b"ordinary")
    os.chmod(leaf, stat.S_IREAD)
    result = _scratch_link_cleanup(tree)
    assert result["status"] == "CLEAN" and not tree.exists(), result
    assert result["readonly_files_cleared"] == 1
    assert result["internal_hardlinks_removed"] == 0


@pytest.mark.skipif(os.name != "nt", reason="native scratch ADS containment")
@pytest.mark.parametrize("target", ["root", "leaf"])
def test_native_scratch_named_stream_holds_before_deletion(tmp_path, target):
    tree = tmp_path / "fresh-private-scratch"
    tree.mkdir()
    leaf = tree / "leaf"
    leaf.write_bytes(b"owned")
    path = tree if target == "root" else leaf
    Path(str(path) + ":private-stream").write_bytes(b"must not disappear")
    before = _native_outside_state(leaf)
    result = _scratch_link_cleanup(tree)
    assert result["status"] == "HOLD" and result["reason"] == "AGY_INPUT_NAMED_STREAM", result
    assert result["measurements"]["deleted_entries"] == 0
    assert _native_outside_state(leaf) == before
    assert Path(str(path) + ":private-stream").read_bytes() == b"must not disappear"


def test_modeled_scoped_reparse_holds_before_cleanup_mutation(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows reparse metadata model")
    tree = tmp_path / "fresh-private-scratch"
    tree.mkdir()
    leaf = tree / "leaf"
    leaf.write_bytes(b"owned")
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == leaf:
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_mode=info.st_mode,
                st_nlink=info.st_nlink, st_file_attributes=info.st_file_attributes | 0x400)
        return info
    monkeypatch.setattr(Path, "lstat", reparse)
    result = _scratch_link_cleanup(tree)
    # Injected lstat claims a reparse attribute that the pinned native handle
    # proves absent: that is a metadata CHANGED mismatch, not an unproven
    # actual symlink. Both dispositions remain fail-closed before unlink.
    assert result["status"] == "HOLD", result
    assert result["reason"] == "AGY_INPUT_CLEANUP_UNSAFE", result
    assert result["first_unsafe_entry"]["subtype"] == "REPARSE_CHANGED", result
    assert result["measurements"]["deleted_entries"] == 0 and leaf.read_bytes() == b"owned"


@pytest.mark.skipif(os.name != "nt", reason="independent HOST NTFS hardlink acceptance")
@pytest.mark.parametrize("entries", [700, 6500])
def test_native_owned_scratch_internal_hardlinks_clean(tmp_path, entries, record_property):
    source, mirror, expected, baseline = input_fixture(tmp_path)
    sentinel = source / "source-sentinel"
    sentinel.write_bytes(b"source byte identity")
    tree = tmp_path / "fresh-private-scratch"
    tree.mkdir()
    branches = [tree / f"branch-{i}" for i in range(4)]
    for branch in branches:
        branch.mkdir()
    leaf = branches[0] / "native-owned"
    leaf.write_bytes(b"internal group")
    for branch in branches[1:3]:
        _required_native_link(leaf, branch / "native-alias")
    assert leaf.stat().st_nlink == 3
    os.chmod(leaf, stat.S_IREAD)
    # Exactly entries names including root/directories/native link names.
    for index in range(entries - 8):
        (branches[3] / f"leaf-{index:05d}").write_bytes(b"private")
    before = _native_outside_state(sentinel)
    # This is native containment/count acceptance, not a host throughput bound.
    # At 12 workers the 6500-entry fixture can exhaust the 30-second default
    # while still making progress. Use the API's supported finite budget for
    # that case; the smaller fixture and negative timeout tests keep defaults.
    budget = 120.0 if entries == 6500 else adapters.AGY_CLEANUP_DEFAULT_SECONDS
    result = _scratch_link_cleanup(tree, timeout_seconds=budget)
    record_property("native_internal_hardlink_receipt", json.dumps(result, sort_keys=True))
    assert result["status"] == "CLEAN", result
    assert not tree.exists() and result["internal_hardlinks_removed"] == 2
    assert result["measurements"]["deleted_entries"] == entries
    assert result["measurements"]["entries_visited"] == entries
    assert result["measurements"]["budget_seconds"] == budget
    assert 0 <= result["measurements"]["elapsed_seconds"] <= budget
    assert _native_outside_state(sentinel) == before
    assert sentinel.read_bytes() == b"source byte identity"
    assert adapters.agy_input_manifest(source, mirror, expected, baseline)["status"] == "INTACT"


@pytest.mark.skipif(os.name != "nt", reason="independent HOST external alias negative AC")
@pytest.mark.parametrize("outside", ["readonly-git", "input", "same-sid"])
def test_native_owned_scratch_external_alias_holds_without_metadata_change(tmp_path, outside):
    source, mirror, expected, baseline = input_fixture(tmp_path)
    tree = tmp_path / "fresh-private-scratch"
    tree.mkdir()
    if outside == "readonly-git":
        sentinel = source / ".git" / "objects" / "sentinel"
        sentinel.parent.mkdir(parents=True)
    else:
        sentinel = (mirror if outside == "input" else tmp_path) / "outside-sentinel"
    sentinel.write_bytes(b"outside byte identity")
    _required_native_link(sentinel, tree / "alias")
    os.chmod(sentinel, stat.S_IREAD)
    before = _native_outside_state(sentinel)
    result = _scratch_link_cleanup(tree)
    assert result["status"] == "HOLD" and result["reason"] == "AGY_SCRATCH_ENTRY_UNSAFE", result
    assert result["readonly_files_cleared"] == result["measurements"]["deleted_entries"] == 0
    assert _native_outside_state(sentinel) == before
    assert sentinel.read_bytes() == b"outside byte identity" and (tree / "alias").exists()
    assert {name: (mirror / name).read_bytes() for name in expected} == expected


@pytest.mark.skipif(os.name != "nt", reason="independent HOST hardlink proof fault AC")
@pytest.mark.parametrize("fault", ["enum-access", "volume", "zero-id", "high-id", "alias-name", "late-enum"])
def test_native_owned_scratch_proof_fault_holds_before_deletion(tmp_path, monkeypatch, fault):
    tree = tmp_path / "fresh-private-scratch"
    tree.mkdir()
    leaf = tree / "a"
    leaf.write_bytes(b"owned untouched")
    _required_native_link(leaf, tree / "b")
    os.chmod(leaf, stat.S_IREAD)
    before = _native_outside_state(leaf)
    kernel = adapters._agy_kernel()
    class Fault:
        names = 0
        ids = None
        def __getattr__(self, name):
            return getattr(kernel, name)
        def FindFirstFileNameW(self, *args):
            import ctypes
            self.names += 1
            if fault == "enum-access" or (fault == "late-enum" and self.names >= 3):
                return ctypes.c_void_p(-1).value
            return kernel.FindFirstFileNameW(*args)
        def GetFileInformationByHandleEx(self, handle, kind, value, size):
            success = kernel.GetFileInformationByHandleEx(handle, kind, value, size)
            if success and kind == 18:
                if fault == "volume":
                    value._obj.volume = 0
                if fault == "zero-id":
                    for i in range(16):
                        value._obj.identifier[i] = 0
                if fault == "high-id":
                    if self.ids is None:
                        self.ids = {}
                    self.ids.setdefault(handle, len(self.ids) + 1)
                    value._obj.identifier[15] ^= self.ids[handle]
            return success
        def GetFinalPathNameByHandleW(self, handle, buffer, size, flags):
            length = kernel.GetFinalPathNameByHandleW(handle, buffer, size, flags)
            if fault == "alias-name" and length and buffer.value.endswith("\\b"):
                buffer.value = buffer.value[:-1] + "B"
            return length
    monkeypatch.setattr(adapters, "_agy_kernel", Fault)
    result = _scratch_link_cleanup(tree)
    assert result["status"] == "HOLD", result
    assert result["measurements"]["deleted_entries"] == result["readonly_files_cleared"] == 0
    assert _native_outside_state(leaf) == before and (tree / "b").exists()


@pytest.mark.skipif(os.name != "nt", reason="independent HOST external alias race AC")
def test_native_owned_scratch_late_external_alias_holds(tmp_path, monkeypatch):
    tree = tmp_path / "fresh-private-scratch"
    tree.mkdir()
    leaf, external = tree / "a", tmp_path / "outside-race"
    leaf.write_bytes(b"unchanged")
    _required_native_link(leaf, tree / "b")
    before = []
    original = adapters._AgyCleanupHandle.delete
    def race(handle, info, report):
        if handle.path == leaf:
            _required_native_link(leaf, external)
            before.append(_native_outside_state(external))
        return original(handle, info, report)
    monkeypatch.setattr(adapters._AgyCleanupHandle, "delete", race)
    result = _scratch_link_cleanup(tree)
    assert before and result["status"] == "HOLD", result
    assert _native_outside_state(external) == before[0]
    assert external.read_bytes() == b"unchanged" and leaf.exists()


@pytest.mark.skipif(os.name != "nt", reason="independent HOST INPUT remains single-link")
def test_native_input_cleanup_still_holds_provably_internal_aliases(tmp_path):
    tree = tmp_path / "input"
    tree.mkdir()
    (tree / "a").write_bytes(b"INPUT must remain single-link")
    _required_native_link(tree / "a", tree / "b")
    result = owned_cleanup(tree)
    assert result["status"] == "HOLD" and (tree / "a").exists() and (tree / "b").exists()


def _native_scratch_inventory(root):
    """Observe pytest artifacts before teardown, without following any alias.

    Hosted pytest can create `current` symlinks when its Windows token permits
    them. Record that possibility instead of changing cleanup or assuming it
    explains a historical HOLD. Match entry hashes to first_unsafe_entry.
    """
    report = {"status": "COMPLETE", "entries": 0, "reparse_entries": 0,
              "hardlink_entries": 0, "readonly_entries": 0, "special_entries": []}
    def visit(path, depth):
        if depth > 128 or report["entries"] >= 100_000:
            raise ValueError("INVENTORY_LIMIT")
        info = path.lstat()
        report["entries"] += 1
        attributes = getattr(info, "st_file_attributes", 0)
        reparse = bool(attributes & 0x400) or stat.S_ISLNK(info.st_mode)
        hardlink = stat.S_ISREG(info.st_mode) and info.st_nlink > 1
        report["reparse_entries"] += int(reparse)
        report["hardlink_entries"] += int(hardlink)
        report["readonly_entries"] += int(bool(attributes & 1))
        if reparse or hardlink or attributes & 1:
            report["special_entries"].append({
                "relative_entry_sha256": hashlib.sha256(path.relative_to(root).as_posix().encode()).hexdigest(),
                "reparse": reparse, "links": info.st_nlink, "readonly": bool(attributes & 1)})
        if stat.S_ISDIR(info.st_mode) and not reparse:
            with os.scandir(path) as entries:
                for entry in entries:
                    visit(Path(entry.path), depth + 1)
    try:
        visit(root, 0)
    except (OSError, ValueError) as exc:
        report.update(status="UNPROVEN", winerror=getattr(exc, "winerror", None))
    return report


def _native_cache_diagnostic(result, scenario, inventory=None, aliases=None):
    """Keep failure receipts useful without disclosing provider/source paths."""
    teardown = result.get("scratch_teardown", {})
    lifecycle = result.get("process_lifecycle", {})
    return {"scenario": scenario, "status": result.get("status"),
            "error_code": result.get("error_code"), "scratch_before_teardown": inventory,
            "pytest_current_aliases": aliases,
            "lifecycle": {key: lifecycle.get(key) for key in
                ("status", "reason", "authority", "tree_empty", "forced_termination",
                 "containment_proven", "native_error", "gate_identity", "at_return",
                 "after_grace", "after_reconcile")},
            "teardown_status": teardown.get("status"),
            "roots": [{key: root.get(key) for key in
                ("role", "status", "reason", "platform", "attempts", "first_unsafe_entry",
                 "measurements", "diagnostics", "internal_hardlinks_removed", "hardlink_proof_checks")}
                for root in teardown.get("roots", [])]}


def _native_cache_attempt(tmp_path, monkeypatch, scenario, provider_exit=0, record_property=None,
                          *, expected_scratch_status="CLEAN"):
    """Real Windows provider ingress/process tree, with explicitly modeled Git.

    Python is the diagnostic provider, not a live AGY/model permission proof.
    The production invocation supplies cwd, env, mirror and scratch; neither
    the provider nor its pytest/xdist descendants receive a replacement env.
    """
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "probe_module.py").write_bytes(b"VALUE = 611\n")
    sentinel = (repo / "probe_module.py").read_bytes()
    def snapshot(*_args, **_kwargs):
        changed = repo / "changed.txt"
        hashes = {changed.name: hashlib.sha256(changed.read_bytes()).hexdigest()} if changed.exists() else {}
        assert (repo / "probe_module.py").read_bytes() == sentinel
        assert sorted(p.name for p in repo.iterdir()) == sorted(["probe_module.py", *hashes])
        return {"git_head_after": "a" * 40, "git_dir": str(repo / ".git"),
                "changed_files": list(hashes), "candidate_file_sha256": hashes,
                "tracked_diff_sha256": hashlib.sha256(json.dumps(hashes).encode()).hexdigest(),
                "scope_valid": True}, b"modeled Git diff; not a native Git receipt"
    monkeypatch.setattr(runner, "_snapshot", snapshot)
    monkeypatch.setattr(runner, "_agy_source_binding", lambda r, s, *a: {
        "source_root": str(r), "source_sha": s["git_head_after"], "git_dir": s["git_dir"]})
    # A caller must not be able to redirect the provider's default cache back
    # into input. No parent Git guard or plugin policy is removed here.
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p cacheprovider -o cache_dir=.pytest_cache")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "0")
    # Reproduce registered verification's runtime plugin inheritance even
    # when this regression is run directly, without an outer EF01 verifier.
    verifier_scratch = tmp_path / "verifier-scratch"
    verifier_scratch.mkdir()
    plugins = [p for p in os.environ.get("PYTEST_PLUGINS", "").split(",") if p]
    if "ai_commerce.engineering.profile" not in plugins:
        plugins.append("ai_commerce.engineering.profile")
    monkeypatch.setenv("PYTEST_PLUGINS", ",".join(plugins))
    monkeypatch.setenv("AI_COMMERCE_PYTEST_SCRATCH", str(verifier_scratch))
    monkeypatch.setenv("AI_COMMERCE_PYTEST_REPO", str(repo))
    roots, observations, inventories = [], [], []
    prompt = 'Trusted infrastructure probe "Việt" \\ 🚀'
    code = r'''
import hashlib, json, os, shlex, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
source, mirror, scratch = [Path(args[i+1]) for i, a in enumerate(args[:-1]) if a == '--add-dir']
assert Path.cwd() == source
assert hashlib.sha256((mirror / 'instruction.txt').read_bytes()).hexdigest() == EXPECTED_SHA
assert 'ai_commerce.engineering.profile' not in os.environ.get('PYTEST_PLUGINS', '').split(',')
assert shlex.split(os.environ['PYTEST_ADDOPTS']) == ['-p', 'no:cacheprovider',
    '-p', 'no:ai_commerce.engineering.profile',
    '--basetemp=' + str(scratch / 'pytest-basetemp')]
assert os.environ['PYTHONDONTWRITEBYTECODE'] == '1'
assert all(os.environ[k] == str(scratch) for k in ('TEMP', 'TMP', 'TMPDIR'))
assert os.environ['AI_COMMERCE_PYTEST_SCRATCH'] == str(scratch)
assert os.environ['AI_COMMERCE_PYTEST_REPO'] == str(source)
assert os.environ['UV_LINK_MODE'] == 'copy'
assert all(Path(os.environ[k]).parent == scratch for k in ('UV_CACHE_DIR', 'PIP_CACHE_DIR', 'npm_config_cache'))
sys.path.insert(0, str(source))
import probe_module
assert probe_module.VALUE == 611
if SCENARIO in ('pytest', 'xdist'):
    # Observe pytest's real syscall, preserving its arguments and exception.
    # _force_symlink deliberately swallows creation failures on Windows.
    (scratch / 'conftest.py').write_text("""import os
from pathlib import Path

def pytest_configure(config):
    config._native_current_attempts = []
    original = Path.symlink_to
    base = Path(os.environ['TEMP']) / 'pytest-basetemp'
    def observe(path, target, target_is_directory=False):
        if base not in path.parents or not path.name.endswith('current'):
            return original(path, target, target_is_directory=target_is_directory)
        proof = {'alias': path.relative_to(base).as_posix(),
                 'target': Path(target).relative_to(base).as_posix()}
        config._native_current_attempts.append(proof)
        try:
            result = original(path, target, target_is_directory=target_is_directory)
        except OSError as exc:
            proof.update(created=False, errno=exc.errno, winerror=exc.winerror)
            raise
        else:
            proof.update(created=True, errno=None, winerror=None)
            return result
    Path.symlink_to = observe
""", encoding='utf-8')
    test = scratch / 'test_native_cache.py'
    test.write_text("import json, os\nfrom pathlib import Path\nimport pytest\n"
        "@pytest.mark.parametrize('value', [1, 2])\n"
        "def test_native_cache(value, tmp_path, request):\n"
        "    assert not request.config.pluginmanager.hasplugin('cacheprovider')\n"
        "    assert not request.config.pluginmanager.hasplugin('ai_commerce.engineering.profile')\n"
        "    scratch = Path(os.environ['TEMP'])\n"
        "    assert scratch in tmp_path.parents\n"
        "    assert (scratch / 'pytest-basetemp') in tmp_path.parents\n"
        "    relative = tmp_path.relative_to(scratch / 'pytest-basetemp').as_posix()\n"
        "    attempts = [p for p in request.config._native_current_attempts if p['target'] == relative]\n"
        "    assert len(attempts) == 1\n"
        "    (scratch / ('worker-' + str(value) + '.json')).write_text(json.dumps({"
        "'pid': os.getpid(), 'worker': os.environ.get('PYTEST_XDIST_WORKER'), "
        "'cwd': str(Path.cwd()), 'addopts': os.environ['PYTEST_ADDOPTS'], "
        "'pytest_current_alias': attempts[0]}))\n", encoding='utf-8')
    argv = [sys.executable, '-m', 'pytest', str(test), '--rootdir', str(mirror),
            '-o', 'cache_dir=' + str(mirror / '.pytest_cache'), '-q']
    if SCENARIO == 'xdist':
        argv += ['-n', '2']
    child = subprocess.run(argv, cwd=mirror, capture_output=True, timeout=40)
    assert child.returncode == 0, (child.stdout + child.stderr).decode('utf-8', 'replace')
    workers = [json.loads(p.read_text()) for p in sorted(scratch.glob('worker-*.json'))]
    assert len(workers) == 2 and all(w['cwd'] == str(mirror) for w in workers)
    assert all(shlex.split(w['addopts']) == ['-p', 'no:cacheprovider',
        '-p', 'no:ai_commerce.engineering.profile',
        '--basetemp=' + str(scratch / 'pytest-basetemp')] for w in workers)
    assert not list(source.rglob('__pycache__'))
    assert not list(scratch.rglob('__pycache__'))
    assert not (mirror / '.pytest_cache').exists()
    assert not list(mirror.rglob('__pycache__'))
    for worker in workers:
        worker['cwd_is_input'] = worker.pop('cwd') == str(mirror)
        proof = worker['pytest_current_alias']
        alias = scratch / 'pytest-basetemp' / proof['alias']
        target = scratch / 'pytest-basetemp' / proof['target']
        assert alias.parent == target.parent and target.is_dir() and not target.is_symlink()
        assert alias.name.endswith('current')
        suffix = target.name[len(alias.name[:-len('current')]):]
        assert suffix.isdecimal()
        try:
            info = alias.lstat()
        except FileNotFoundError:
            proof['entry_state'] = 'ABSENT'
        else:
            proof.update(entry_state='PRESENT', reparse_tag=info.st_reparse_tag,
                         reparse=bool(info.st_file_attributes & 0x400),
                         symlink=alias.is_symlink(), target_matches=os.readlink(alias) == str(target))
        proof['relative_entry_sha256'] = hashlib.sha256(
            alias.relative_to(scratch).as_posix().encode()).hexdigest()
        del proof['alias'], proof['target']
    (scratch / 'native.receipt.json').write_text(json.dumps({'pytest_exit': child.returncode,
        'workers': workers, 'owned_cache_canary': True}))
elif SCENARIO == 'directory':
    (mirror / '.pytest_cache').mkdir()
elif SCENARIO == 'junction':
    child = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(mirror / '.pytest_cache'), str(scratch)], capture_output=True)
    assert child.returncode == 0
elif SCENARIO == 'ads':
    with open(str(mirror / 'instruction.txt') + ':hidden', 'wb') as stream:
        stream.write(b'hostile stream')
elif SCENARIO == 'collision':
    (mirror / 'INSTRUCTION.TXT').write_bytes(b'hostile case collision')
elif SCENARIO == 'replacement':
    target = mirror / 'instruction.txt'
    data = target.read_bytes()
    target.rename(scratch / 'old-instruction.txt')
    target.write_bytes(data)
elif SCENARIO != 'nonzero':
    raise AssertionError(SCENARIO)
(source / 'changed.txt').write_bytes(b'allowed source edit')
print(json.dumps({'status': 'SUCCESS'}))
sys.exit(PROVIDER_EXIT)
'''.replace("EXPECTED_SHA", repr(hashlib.sha256(prompt.encode()).hexdigest()))
    code = code.replace("SCENARIO", repr(scenario)).replace("PROVIDER_EXIT", str(provider_exit))
    def command(selection, source, instruction, **kwargs):
        argv, stdin = adapters.command_for(selection, source, instruction,
            antigravity_evidence_dir=kwargs.get("antigravity_evidence_dir"),
            antigravity_scratch_dir=kwargs.get("antigravity_scratch_dir"))
        if kwargs.get("antigravity_evidence_dir") is not None:
            roots.extend([source, kwargs["antigravity_evidence_dir"], kwargs["antigravity_scratch_dir"]])
            # Keep the diagnostic fixture below the production command limit;
            # its script and audit conftest belong to this owned scratch root.
            script = kwargs["antigravity_scratch_dir"] / "native-provider.py"
            script.write_text(code, encoding="utf-8")
            return [sys.executable, str(script), *argv[1:]], stdin
        return [sys.executable, "-c", code, *argv[1:]], stdin
    monkeypatch.setattr(runner, "command_for", command)
    native_run = runner._run_agy_owned
    def observe(command, **kwargs):
        assert kwargs["cwd"] == repo and shlex.split(kwargs["env"]["PYTEST_ADDOPTS"]) == [
            "-p", "no:cacheprovider", "-p", "no:ai_commerce.engineering.profile",
            "--basetemp=" + str(roots[2] / "pytest-basetemp")]
        assert "ai_commerce.engineering.profile" not in kwargs["env"]["PYTEST_PLUGINS"].split(",")
        result = native_run(command, **kwargs)
        inventories.append(_native_scratch_inventory(roots[2]))
        receipt = roots[2] / "native.receipt.json"
        observations.append(json.loads(receipt.read_text()) if receipt.exists() else {})
        return result
    monkeypatch.setattr(runner, "_run_agy_owned", observe)
    marker = tmp_path / "verifier-ran"
    result = runner.execute_attempt(repo=repo, output_dir=tmp_path / "evidence",
        work_id="input-cache-owner", attempt_id="fresh-cache-" + scenario,
        selection=ExecutorSelection("antigravity", "gemini-3.8-flash", "high"),
        instruction=prompt, allowed_prefixes=["changed.txt"], max_repair_rounds=2,
        verify_argv=[sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_bytes(b'verified')"])
    aliases = [w["pytest_current_alias"] for w in observations[0].get("workers", [])] if observations else []
    diagnostic = json.dumps(_native_cache_diagnostic(
        result, scenario, inventories[0] if inventories else None, aliases), sort_keys=True)
    print("NATIVE_CACHE_DIAGNOSTIC=" + diagnostic)
    if record_property is not None:
        record_property("native_cache_diagnostic", diagnostic)
    assert len(observations) == result["invocation_count"] == 1, diagnostic
    assert result["process_lifecycle"]["status"] == "RECONCILED"
    assert result["process_lifecycle"]["tree_empty"] and not result["process_lifecycle"]["forced_termination"]
    assert result["scratch_teardown"]["status"] == expected_scratch_status, diagnostic
    if scenario in {"pytest", "xdist"}:
        scratch_root = next(x for x in result["scratch_teardown"]["roots"] if x["role"] == "SCRATCH")
        assert inventories[0]["status"] == "COMPLETE", diagnostic
        assert len(aliases) == len({p["relative_entry_sha256"] for p in aliases}) == 2, diagnostic
        if all(p["created"] for p in aliases):
            assert all(p["entry_state"] == "PRESENT" and p["reparse"] and p["symlink"]
                       and p["reparse_tag"] == 0xA000000C and p["target_matches"] for p in aliases), diagnostic
            assert inventories[0]["reparse_entries"] == 2, diagnostic
            assert {p["relative_entry_sha256"] for p in inventories[0]["special_entries"] if p["reparse"]} == {
                p["relative_entry_sha256"] for p in aliases}, diagnostic
            if expected_scratch_status == "CLEAN":
                assert scratch_root["reparse_links_removed"] == 2, diagnostic
        else:
            # Zero links is acceptable only with two independent native
            # creation denials AND no directory entry, including dangling links.
            assert all(p["created"] is False and p["winerror"] in {5, 50, 1314}
                       and p["entry_state"] == "ABSENT" for p in aliases), diagnostic
            assert inventories[0]["reparse_entries"] == scratch_root["reparse_links_removed"] == 0, diagnostic
    assert {r["role"] for r in result["scratch_teardown"]["roots"]} == {"INPUT", "SCRATCH"}
    assert all(r["status"] == (expected_scratch_status if r["role"] == "SCRATCH" else "CLEAN")
               for r in result["scratch_teardown"]["roots"]), diagnostic
    assert not roots[1].exists()
    assert roots[2].exists() is (expected_scratch_status == "HOLD")
    assert (repo / "changed.txt").is_file(), (result["status"],
        (tmp_path / "evidence" / result["evidence_files"]["executor_stderr"]).read_text())
    assert (repo / "changed.txt").read_bytes() == b"allowed source edit"
    assert (repo / "probe_module.py").read_bytes() == sentinel
    assert not list(verifier_scratch.iterdir())
    assert result["model"] == "gemini-3.8-flash" and result["reasoning_effort"] == "high"
    assert result["replay_authorized"] is False and "candidate_ref" not in result
    return result, marker, observations[0]


@pytest.mark.skipif(os.name != "nt", reason="native Windows provider/pytest/xdist ingress")
@pytest.mark.parametrize("scenario", ["pytest", "xdist"])
def test_native_provider_pytest_in_input_keeps_manifest_intact(tmp_path, monkeypatch, record_property, scenario):
    result, marker, receipt = _native_cache_attempt(tmp_path, monkeypatch, scenario, record_property=record_property)
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE" and marker.exists()
    assert result["verify_completed"] and result["verify_exit_code"] == 0
    evidence = tmp_path / "evidence"
    manifests = [json.loads((evidence / result["evidence_files"]["agy_input_" + phase]).read_text())
                 for phase in ("before", "after_executor", "after_verify")]
    assert manifests[0] == manifests[1] == manifests[2]
    assert manifests[0]["status"] == "INTACT"
    assert receipt["owned_cache_canary"] and all(w["cwd_is_input"] for w in receipt["workers"])
    assert [f["name"] for f in manifests[0]["files"]] == ["instruction.txt"]
    if scenario == "xdist":
        assert len({w["pid"] for w in receipt["workers"]}) == 2
        assert {w["worker"] for w in receipt["workers"]} == {"gw0", "gw1"}
    record_property("native_input_cache_receipt", json.dumps({
        "fixture": "NATIVE_PROCESS_MODELED_GIT_NOT_LIVE_AGY", "scenario": scenario,
        "input_manifest": manifests[0], "pytest": receipt,
        "lifecycle": result["process_lifecycle"], "teardown": result["scratch_teardown"]}, sort_keys=True))


@pytest.mark.skipif(os.name != "nt", reason="native Windows sharing denial diagnostic")
@pytest.mark.parametrize("scenario", ["pytest", "xdist"])
def test_native_nested_cache_hold_emits_sanitized_root_proof(tmp_path, monkeypatch, record_property, scenario):
    """A real retained handle distinguishes teardown HOLD from a live child.

    This deliberately introduces a sharing violation; it does not claim the
    historical hosted failure had that cause. Positive scenarios stay CLEAN.
    """
    import ctypes
    native_run = runner._run_agy_owned
    kernel = adapters._agy_kernel()
    pins, roots, properties = [], [], {}
    def retain_handle(command, **kwargs):
        result = native_run(command, **kwargs)
        scratch = Path(kwargs["env"]["TEMP"])
        handle = kernel.CreateFileW(str(scratch / "native.receipt.json"),
                                   0x80000000, 1, None, 3, 0x02200000, None)
        assert handle != ctypes.c_void_p(-1).value
        pins.append(handle)
        roots.append(scratch)
        return result
    monkeypatch.setattr(runner, "_run_agy_owned", retain_handle)
    try:
        result, marker, _ = _native_cache_attempt(tmp_path, monkeypatch, scenario,
            record_property=properties.__setitem__, expected_scratch_status="HOLD")
        assert len(pins) == len(roots) == 1
        assert result["status"] == "UNPROVEN" and marker.exists()
        assert result["verify_completed"] and result["verify_exit_code"] == 0
        diagnostic = json.loads(properties["native_cache_diagnostic"])
        record_property("native_cache_diagnostic", properties["native_cache_diagnostic"])
        assert str(tmp_path) not in json.dumps(diagnostic)
        assert all(str(root) not in json.dumps(diagnostic) for root in roots)
        assert diagnostic["lifecycle"]["status"] == "RECONCILED"
        assert diagnostic["lifecycle"]["tree_empty"]
        assert not diagnostic["lifecycle"]["forced_termination"]
        scratch = next(root for root in diagnostic["roots"] if root["role"] == "SCRATCH")
        assert scratch["status"] == "HOLD"
        assert scratch["reason"] == "AGY_SCRATCH_CLEANUP_SHARING_VIOLATION"
        assert scratch["diagnostics"][-1]["winerror"] == 32
        assert scratch["measurements"]["entries_visited"] > 0
        assert diagnostic["scratch_before_teardown"]["status"] == "COMPLETE"
        evidence = tmp_path / "evidence"
        manifests = [json.loads((evidence / result["evidence_files"]["agy_input_" + phase]).read_text())
                     for phase in ("before", "after_executor", "after_verify")]
        assert manifests[0] == manifests[1] == manifests[2]
        assert manifests[0]["status"] == "INTACT"
    finally:
        for handle in pins:
            assert kernel.CloseHandle(handle)
        for root in roots:
            recovery = _scratch_link_cleanup(root)
            record_property("native_cache_recovery", json.dumps(recovery, sort_keys=True))
            assert recovery["status"] == "CLEAN" and not root.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows hostile input and nonzero ingress")
@pytest.mark.parametrize("scenario,reason", [
    ("directory", "AGY_INPUT_NON_REGULAR"), ("junction", "AGY_INPUT_NON_REGULAR"),
    ("ads", "AGY_INPUT_NAMED_STREAM"), ("collision", "AGY_INPUT_CONTRACT_MODIFIED"),
    ("replacement", "AGY_INPUT_CONTRACT_REPLACED"), ("nonzero", None)])
@pytest.mark.parametrize("provider_exit", [0, 1])
def test_native_hostile_input_or_nonzero_never_verifies_or_replays(tmp_path, monkeypatch, scenario, reason, provider_exit):
    if scenario == "nonzero" and provider_exit == 0:
        # Cover a second ordinary nonzero code despite SUCCESS in stdout.
        provider_exit = 7
    result, marker, _ = _native_cache_attempt(tmp_path, monkeypatch, scenario, provider_exit)
    assert not marker.exists() and result["verify_exit_code"] is None and not result["verify_completed"]
    assert "quarantine_manifest" in result["evidence_files"]
    if reason:
        assert result["status"] == "SOURCE_DRIFT" and result["error_code"] == "AGY_INPUT_INTEGRITY_FAILED"
        assert result["input_integrity_reason"] == reason
    else:
        assert result["status"] == "EXECUTOR_FAILED" and result["error_code"] == "EXECUTOR_NONZERO"


def stream(*events):
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode()


def test_known_target_is_classified_without_disclosing_it(tmp_path):
    repo = tmp_path / "repo"
    private = "credential-do-not-print"
    data = stream({"status": "SUCCESS", "denied_actions": [
        {"action": "read_file", "arguments": {"path": str(repo / "src" / private)}},
        {"action": "read_file", "path": str(tmp_path / private)},
        {"action": "read_file", "parameters": {"file_path": str(tmp_path / "input" / private)}},
        {"action": "read_file"},
    ]})
    diagnostic = runner._headless_denial_diagnostics(
        data, "antigravity", repo=repo, agy_input_dir=tmp_path / "input")
    assert [d["target_status"] for d in diagnostic] == [
        "DENIED_TARGET_KNOWN", "DENIED_TARGET_KNOWN", "DENIED_TARGET_KNOWN",
        "DENIED_TARGET_UNKNOWN"]
    assert [d["target_scope"] for d in diagnostic] == ["WORKTREE", "EXTERNAL", "AGY_INPUT", "UNKNOWN"]
    assert private not in json.dumps(diagnostic)
    assert str(tmp_path) not in json.dumps(diagnostic)


def test_no_inference_from_text_or_nested_tool_payload_and_bounded_output(tmp_path):
    nested = stream({"type": "item.completed", "item": {"result": {
        "denied_actions": [{"action": "read_file", "path": "secret"}]}}})
    assert runner._headless_denied_actions(nested, "antigravity") == []
    data = stream({"denied_actions": [{"action": "read_file",
        "message": "permission denied reading C:/private/token"}] * 100})
    result = runner._headless_denial_diagnostics(data, "antigravity", repo=tmp_path)
    assert 0 < len(result) <= 32
    assert all(d["target_status"] == "DENIED_TARGET_UNKNOWN" for d in result)
    assert "private" not in json.dumps(result)
    assert runner._headless_denied_actions(data, "codex") == []


def test_lexical_target_scope_never_confuses_sibling_or_relative_paths(tmp_path):
    repo = tmp_path / "repo"
    data = stream({"denied_actions": [
        {"action": "read_file", "path": str(tmp_path / "repo-other" / "file.py")},
        {"action": "read_file", "path": "src/file.py"},
        {"action": "read_file", "path": str(repo / ".." / "private")}]})
    assert [d["target_scope"] for d in runner._headless_denial_diagnostics(
        data, "antigravity", repo=repo)] == ["EXTERNAL", "RELATIVE", "EXTERNAL"]


@pytest.mark.parametrize("denied", [[{}], ["read_file"], [{"action": "secret\nTOKEN=value"}]])
def test_any_explicit_denial_fails_closed_and_never_echoes_unknown_tool(denied):
    assert runner._headless_denied_actions(stream({"denied_actions": denied}), "antigravity") == ["UNKNOWN_TOOL"]


@pytest.mark.parametrize("denied", ["read_file", {"action": "read_file"}])
def test_nonempty_malformed_denial_container_still_fails_closed(denied):
    assert runner._headless_denied_actions(stream({"status": "SUCCESS", "denied_actions": denied}),
        "antigravity") == ["UNKNOWN_TOOL"]


def test_private_structured_target_is_distinct_from_ordinary_source(tmp_path):
    result = runner._headless_denial_diagnostics(stream({"denied_actions": [
        {"action": "read_file", "path": str(tmp_path / ".local" / "authoritative.json")}
    ]}), "antigravity", repo=tmp_path)
    assert result[0]["target_scope"] == "PRIVATE_PATH"
    assert "authoritative.json" not in json.dumps(result)


def test_registered_roots_under_private_ancestors_are_still_exact_roots(tmp_path):
    repo = tmp_path / ".codex" / "worktree"
    agy_input = tmp_path / ".local" / "attempt" / "agy_input"
    data = stream({"denied_actions": [
        {"action": "read_file", "path": str(repo / "src" / "file.py")},
        {"action": "read_file", "path": str(agy_input / "instruction.txt")},
        {"action": "read_file", "path": str(repo / ".local" / "private.json")} ]})
    assert [d["target_scope"] for d in runner._headless_denial_diagnostics(
        data, "antigravity", repo=repo, agy_input_dir=agy_input)] == ["WORKTREE", "AGY_INPUT", "PRIVATE_PATH"]


def run_fixture(tmp_path, monkeypatch, events, *, exit_code=0, verify_code=0, lifecycle_result=None):
    from test_ef01 import _fixture_agy_process
    def process_fixture(command, *, lifecycle, **kwargs):
        result = _fixture_agy_process(command, lifecycle=lifecycle, **kwargs)
        lifecycle.update(lifecycle_result or {})
        return result
    monkeypatch.setattr(runner, "_run_agy_owned", process_fixture)
    repo = tmp_path / "workspace"
    repo.mkdir()
    calls = []
    def snapshot(*args, **kwargs):
        changed = (repo / "changed.txt").exists()
        hashes = {"changed.txt": hashlib.sha256((repo / "changed.txt").read_bytes()).hexdigest()} if changed else {}
        return {"git_head_after": "a" * 40, "git_dir": str(repo / ".git"),
                "changed_files": list(hashes), "candidate_file_sha256": hashes,
                "tracked_diff_sha256": hashlib.sha256(json.dumps(hashes).encode()).hexdigest(),
                "scope_valid": True}, b"fixture diff"
    monkeypatch.setattr(runner, "_snapshot", snapshot)
    monkeypatch.setattr(runner, "_agy_source_binding", lambda r, s, *a: {
        "source_root": str(r), "source_sha": s["git_head_after"], "git_dir": s["git_dir"]})
    monkeypatch.setattr(runner, "command_for", lambda *a, **k: (["fixture-provider"], None))
    original_run = subprocess.run
    def execute(argv, **kwargs):
        if argv[0] == "fixture-provider":
            calls.append("provider")
            (repo / "changed.txt").write_text("candidate", encoding="utf-8")
            return subprocess.CompletedProcess(argv, exit_code, stream(*events).decode(), "")
        if list(argv[1:3]) == ["-m", "pytest"]:
            calls.append("verify")
            return subprocess.CompletedProcess(argv, verify_code,
                "MT01_XDIST_WORKERS_COLLECTED=12\n" + ("1 failed\n" if verify_code else "1 passed\n"), "")
        return original_run(argv, **kwargs)
    monkeypatch.setattr(runner.subprocess, "run", execute)
    result = runner.execute_attempt(repo=repo, output_dir=tmp_path / "evidence",
        work_id="fixture", attempt_id="fixture-attempt", instruction="create changed.txt",
        selection=ExecutorSelection("antigravity", "fixture-model", "high"),
        verify_argv=[sys.executable, "-m", "pytest", "tests", "-q"],
        allowed_prefixes=["changed.txt"], max_repair_rounds=2)
    return result, calls


@pytest.mark.parametrize("exit_code", [0, 1])
def test_success_or_nonzero_with_denial_never_verifies_or_replays(tmp_path, monkeypatch, exit_code):
    result, calls = run_fixture(tmp_path, monkeypatch, [{"status": "SUCCESS",
        "denied_actions": [{"action": "read_file"}], "error": {"message": "quota exceeded"}}],
        exit_code=exit_code)
    assert result["status"] == "EXECUTOR_PERMISSION_DENIED"
    assert result["error_code"] == "HEADLESS_TOOL_PERMISSION_DENIED"
    assert result["verify_exit_code"] is None and calls == ["provider"]
    assert result["replay_authorized"] is False
    assert result["failure_provenance"]["phase"] == "HEADLESS_TOOL_DENIED"
    assert result["failure_provenance"]["tool"] == "read_file"
    assert result["denied_action_diagnostics"][0]["target_status"] == "DENIED_TARGET_UNKNOWN"
    assert result["git_delivery"] == "LOCAL_DIFF_ONLY_NO_COMMIT_NO_PUSH"
    assert "candidate_ref" not in result


@pytest.mark.parametrize("message,phase", [("quota exceeded", "PROVIDER_QUOTA"),
    ("Selected model is at capacity", "PROVIDER_CAPACITY"), ("unrecognized failure", "UNKNOWN_EFFECTS")])
def test_nonzero_provider_failure_keeps_specific_phase(tmp_path, monkeypatch, message, phase):
    result, calls = run_fixture(tmp_path, monkeypatch, [{"type": "turn.failed",
        "error": {"message": message}}], exit_code=1)
    assert calls == ["provider"] and result["verify_exit_code"] is None
    assert result["failure_provenance"]["phase"] == phase
    assert result["replay_authorized"] is False


def test_provider_internal_pytest_is_not_registered_verification(tmp_path, monkeypatch):
    result, calls = run_fixture(tmp_path, monkeypatch, [
        {"type": "item.completed", "item": {"type": "command_execution",
            "command": "python -m pytest tests -q", "exit_code": 1,
            "aggregated_output": "SECRET traceback"}},
        {"type": "turn.failed", "error": {"message": "quota exceeded"}}], exit_code=1)
    assert calls == ["provider"] and result["verify_exit_code"] is None
    assert result["provider_test_observations"] == [{"phase": "AGENT_INTERNAL_PYTEST",
        "tool": "pytest", "status": "FAILED", "exit_code": 1,
        "authority": "PROVIDER_REPORTED_NOT_EF01_VERIFY"}]
    assert "SECRET" not in json.dumps(result["provider_test_observations"])
    assert result["failure_provenance"]["phase"] == "PROVIDER_QUOTA"


def test_provider_comment_cannot_claim_internal_pytest_execution():
    assert runner._provider_test_observations(stream({"type": "item.completed", "item": {
        "type": "agent_message", "text": "I ran python -m pytest: 1 failed"}})) == []


@pytest.mark.parametrize("quote", ["'", "\u2018", "\u2019", "\u201b", "\u02bc", "\uff07"])
def test_usage_limit_only_in_failed_provider_fields(quote):
    message = f"You{quote}ve hit your usage limit. Try again later."
    failure = stream({"type": "error", "message": message},
                     {"type": "turn.failed", "error": {"message": message}})
    assert runner._provider_problem(failure) == ("QUOTA_EXHAUSTED", "QUOTA_EXHAUSTED")
    for kind in ("command_execution", "tool_result", "agent_message"):
        success = stream({"type": "item.completed", "item": {
            "type": kind, "status": "completed", "exit_code": 0,
            "aggregated_output": message, "error": {"message": message}}},
            {"type": "turn.completed"})
        assert runner._provider_problem(success) is None


@pytest.mark.parametrize("quote", ["'", "\u2019"])
def test_codex_usage_limit_stops_same_attempt_without_verify(tmp_path, monkeypatch, quote):
    from test_ef01_repair import fixture, _provider, VERIFY
    f = fixture.__wrapped__(tmp_path, monkeypatch)
    selection, _, _ = _provider(f)
    calls = []
    raw = stream({"type": "error", "message": f"You{quote}ve hit your usage limit"},
                 {"type": "turn.failed", "error": {"message": f"You{quote}ve hit your usage limit"}})
    actual_run = subprocess.run
    def fail(argv, **kwargs):
        if argv[0] == "git":
            return actual_run(argv, **kwargs)
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, raw.decode(), "")
    monkeypatch.setattr(runner.subprocess, "run", fail)
    result = runner.execute_attempt(repo=f.repo, output_dir=f.root / "evidence",
        work_id="quota", attempt_id="fresh-quota", selection=selection,
        instruction="scoped source change", verify_argv=VERIFY, max_repair_rounds=2)
    assert result["status"] == result["error_code"] == "QUOTA_EXHAUSTED"
    assert len(calls) == result["invocation_count"] == 1
    assert result["verify_exit_code"] is None and result["repair_rounds_used"] == 0
    assert result["replay_authorized"] is False
    assert (f.root / "evidence/executor.stdout.log").read_bytes() == raw
    assert (f.root / "evidence/source.before.json").is_file()
    assert (f.root / "evidence/source.after_executor.json").is_file()


def test_agy_repair_uses_mirrored_feedback_without_requiring_external_logs(tmp_path, monkeypatch):
    from test_ef01_repair import fixture, _run
    # Reuse the existing no-Git fixture and real local Python subprocesses.
    f = fixture.__wrapped__(tmp_path, monkeypatch)
    result, calls, _ = _run(f, executor="antigravity")
    assert result["status"] == "BUILD_VERIFIED_FOR_SCOPE"
    prompt = calls[1]["prompt"]
    assert "External archived artifacts are OPTIONAL" in prompt
    assert "Read the full feedback, source snapshot" not in prompt
    assert "agy_input" in prompt and "repair_feedback.json" in prompt


def test_fingerprint_excludes_attempt_and_raw_diagnostics_but_binds_precondition():
    invocation = {"status": "EXECUTOR_PERMISSION_DENIED", "error_code": "HEADLESS_TOOL_PERMISSION_DENIED",
        "executor": "antigravity", "model": "fixture", "reasoning_effort": "high",
        "source_base_sha": "a" * 40, "attempt_id": "attempt-1", "denied_actions": ["read_file"],
        "candidate_file_sha256": {"file.py": "b" * 64}, "verify_argv": ["python", "-m", "pytest"],
        "diagnostic": "SECRET C:/private/token", "instruction_sha256": "c" * 64}
    first = runner._failure_provenance(invocation, precondition_instruction_sha256="d" * 64)
    other = copy.deepcopy(invocation)
    other.update(attempt_id="attempt-2", diagnostic="another secret", instruction_sha256="e" * 64)
    second = runner._failure_provenance(other, precondition_instruction_sha256="d" * 64)
    assert first["fingerprint"] == second["fingerprint"]
    assert first["attempt_id"] != second["attempt_id"]
    assert "SECRET" not in json.dumps(first) and "private" not in json.dumps(first)
    other["candidate_file_sha256"]["file.py"] = "f" * 64
    assert first["fingerprint"] != runner._failure_provenance(other,
        precondition_instruction_sha256="d" * 64)["fingerprint"]
    other["attempt_id"] = "TOKEN=secret\nC:/private/key"
    assert runner._failure_provenance(other, precondition_instruction_sha256="d" * 64)["attempt_id"] == "UNPROVEN"


def test_unchanged_failure_precondition_blocks_next_model_call(tmp_path, monkeypatch):
    # Drive the existing repair loop with unchanged, determinate verifier evidence.
    from test_ef01_repair import _snapshot_fake, _files
    repo = tmp_path / "workspace"
    repo.mkdir()
    baseline = _files(repo)
    monkeypatch.setattr(runner, "_snapshot", lambda r, s, d: _snapshot_fake(r, baseline, {"scope": ["impl.py"]}))
    monkeypatch.setattr(runner, "command_for", lambda *a, **k: (["fixture-provider"], None))
    calls = []
    def failed_round(**kwargs):
        inv = kwargs["invocation"]
        calls.append(inv["repair_round"])
        inv.update(status="VERIFICATION_FAILED", error_code="TARGETED_VERIFY_NONZERO_OR_UNAVAILABLE",
            executor_invoked=True, executor_exit_code=0, verify_exit_code=1, verify_completed=True,
            verification_origin="REGISTERED_EF01", repair_eligible=True,
            candidate_file_sha256={"impl.py": "b" * 64},
            final_snapshot=kwargs["expected"])
        for key in ("verify_stdout", "verify_stderr", "tracked_diff", "source_after_verify"):
            inv["evidence_files"][key] = "fixture-" + key
            inv["evidence_sha256"][key] = "c" * 64
    monkeypatch.setattr(runner, "_run_invocation", failed_round)
    result = runner.execute_attempt(repo=repo, output_dir=tmp_path / "evidence",
        work_id="fixture", attempt_id="unchanged", selection=ExecutorSelection("codex", "fixture", "high"),
        instruction="scoped change", allowed_prefixes=["impl.py"],
        verify_argv=[sys.executable, "-m", "pytest", "tests", "-q", "-n", "12"])
    assert calls == [0, 1]
    assert result["stop_reason"] == "UNCHANGED_FAILURE_PRECONDITION"
    assert result["failure_provenance"]["phase"] == "EF01_REGISTERED_VERIFY"

@pytest.mark.parametrize("exit_code", [128, 130, -9])
def test_interrupted_provider_exit_preserves_unknown_effects_despite_quota(tmp_path, monkeypatch, exit_code):
    result, calls = run_fixture(tmp_path, monkeypatch,
        [{"type": "turn.failed", "error": {"message": "quota exceeded"}}],
        exit_code=exit_code)
    assert result["status"] == "EXECUTION_UNKNOWN"
    assert result["error_code"] == "EXECUTOR_INTERRUPTED"
    assert result["failure_provenance"]["phase"] == "UNKNOWN_EFFECTS"
    assert result["verify_exit_code"] is None
    assert result["replay_authorized"] is False
    assert calls == ["provider"]


def owned_cleanup(path):
    info, parent = path.lstat(), path.parent.lstat()
    return adapters.agy_remove_owned_tree(path, path.parent,
        (info.st_dev, info.st_ino), (parent.st_dev, parent.st_ino))


@pytest.mark.skipif(os.name != "nt", reason="native Windows cleanup handle metadata")
@pytest.mark.parametrize("kind", ["file", "readonly", "directory"])
def test_native_cleanup_handle_accepts_ordinary_metadata(tmp_path, kind):
    target = tmp_path / kind
    if kind == "directory":
        target.mkdir()
    else:
        target.write_bytes(b"owned")
        if kind == "readonly":
            os.chmod(target, stat.S_IREAD)
    expected = target.lstat()
    handle = adapters._AgyCleanupHandle(target, expected)
    try:
        report = {"readonly_files_cleared": 0}
        handle.delete(target.lstat(), report)
    finally:
        handle.close()
    assert not target.exists()
    assert report["readonly_files_cleared"] == (kind == "readonly")


@pytest.mark.skipif(os.name != "nt", reason="native Windows handle versus path metadata")
@pytest.mark.parametrize("field,value", [
    ("st_nlink", 2), ("st_mode", stat.S_IFDIR | 0o700),
    ("st_file_attributes", 0x400), ("st_ino", 0), ("st_dev", 0),
])
def test_native_cleanup_handle_rejects_mismatched_safety_metadata(tmp_path, field, value):
    target = tmp_path / "owned"
    target.write_bytes(b"keep")
    info = target.lstat()
    expected = SimpleNamespace(**{name: getattr(info, name) for name in (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_file_attributes", "st_reparse_tag")})
    setattr(expected, field, value)
    with pytest.raises(ValueError, match="AGY_INPUT_CLEANUP_UNSAFE"):
        handle = adapters._AgyCleanupHandle(target, expected)
        handle.close()
    assert target.read_bytes() == b"keep"


@pytest.mark.skipif(os.name != "nt", reason="native Windows directory attribute differential")
def test_native_cleanup_ignores_non_safety_directory_attribute(tmp_path):
    target = tmp_path / "directory"
    target.mkdir()
    info = target.lstat()
    # Observed native differential: 0x10000000 can disappear on opening a
    # newly created directory. It is not a type, readonly or reparse bit.
    expected = SimpleNamespace(**{name: getattr(info, name) for name in (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_file_attributes", "st_reparse_tag")})
    expected.st_file_attributes ^= 0x10000000
    handle = adapters._AgyCleanupHandle(target, expected)
    handle.close()
    assert target.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="native handle query with labeled metadata faults")
@pytest.mark.parametrize("phase", ["open", "delete"])
@pytest.mark.parametrize("fault,subtype", [
    ("hardlink", "HARDLINK"), ("nonregular", "FILE_TYPE_CHANGED"),
    ("readonly", "READONLY_CHANGED"), ("reparse", "REPARSE_CHANGED"),
    ("query-failed", "HANDLE_METADATA_UNPROVEN"),
])
def test_native_cleanup_handle_metadata_fault_holds_before_mutation(tmp_path, monkeypatch, phase, fault, subtype):
    tree = tmp_path / "owned"
    tree.mkdir()
    target = tree / "private-leaf"
    target.write_bytes(b"keep")
    kernel = adapters._agy_kernel()
    class FaultQuery:
        """Real handles and queries; only the explicitly named metadata is modeled."""
        handle = None
        queries = 0
        def __getattr__(self, name):
            return getattr(kernel, name)
        def CreateFileW(self, path, *args):
            handle = kernel.CreateFileW(path, *args)
            if path == str(target):
                self.handle = handle
            return handle
        def GetFileInformationByHandleEx(self, handle, kind, value, size):
            success = kernel.GetFileInformationByHandleEx(handle, kind, value, size)
            if handle != self.handle:
                return success
            if kind == 9:
                self.queries += 1
            if success and self.queries >= (1 if phase == "open" else 2):
                if fault == "query-failed":
                    return False
                if kind == 1 and fault == "hardlink":
                    value._obj.links = 2
                if kind == 1 and fault == "nonregular":
                    value._obj.directory = True
                if kind == 9 and fault == "readonly":
                    value._obj.attributes |= 1
                if kind == 9 and fault == "reparse":
                    value._obj.attributes |= 0x400
            return success
    proxy = FaultQuery()
    monkeypatch.setattr(adapters, "_agy_kernel", lambda: proxy)
    result = owned_cleanup(tree)
    assert result["status"] == "HOLD" and target.read_bytes() == b"keep"
    assert result["first_unsafe_entry"] == {
        "subtype": subtype, "relative_entry_sha256": hashlib.sha256(target.name.encode()).hexdigest()}
    assert result["readonly_files_cleared"] == result["measurements"]["deleted_entries"] == 0
    assert "private-leaf" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)


@pytest.mark.skipif(os.name != "nt", reason="native file with unused reparse tag field modeled")
def test_native_cleanup_ignores_reparse_tag_when_attribute_is_absent(tmp_path, monkeypatch):
    tree = tmp_path / "owned"
    tree.mkdir()
    (tree / "ordinary").write_bytes(b"owned")
    kernel = adapters._agy_kernel()
    class UnusedTag:
        def __getattr__(self, name):
            return getattr(kernel, name)
        def GetFileInformationByHandleEx(self, handle, kind, value, size):
            success = kernel.GetFileInformationByHandleEx(handle, kind, value, size)
            if success and kind == 9 and not value._obj.attributes & 0x400:
                # Windows defines this member as unused without the reparse bit.
                value._obj.tag = 0xDEADBEEF
            return success
    monkeypatch.setattr(adapters, "_agy_kernel", UnusedTag)
    result = owned_cleanup(tree)
    assert result["status"] == "CLEAN" and not tree.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows hardlink race")
@pytest.mark.parametrize("phase", ["open", "delete"])
def test_native_cleanup_hardlink_race_holds_external_sentinel(tmp_path, monkeypatch, phase):
    tree = tmp_path / "owned"
    tree.mkdir()
    target, external = tree / "private-leaf", tmp_path / "external-sentinel"
    target.write_bytes(b"keep")
    original = getattr(adapters._AgyCleanupHandle, "__init__" if phase == "open" else "delete")
    def link():
        try:
            os.link(target, external)
        except OSError as exc:
            pytest.skip(f"native hardlink creation unavailable: {getattr(exc, 'winerror', None)}")
    if phase == "open":
        def racing(handle, path, expected, **kwargs):
            if path == target:
                link()
            original(handle, path, expected, **kwargs)
        monkeypatch.setattr(adapters._AgyCleanupHandle, "__init__", racing)
    else:
        def racing(handle, info, report):
            if stat.S_ISREG(info.st_mode):
                link()
            original(handle, info, report)
        monkeypatch.setattr(adapters._AgyCleanupHandle, "delete", racing)
    result = owned_cleanup(tree)
    assert external.read_bytes() == target.read_bytes() == b"keep"
    assert result["status"] == "HOLD"
    assert result["first_unsafe_entry"] == {
        "subtype": "HARDLINK", "relative_entry_sha256": hashlib.sha256(b"private-leaf").hexdigest()}
    assert "private-leaf" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
    assert result["readonly_files_cleared"] == 0


@pytest.mark.parametrize("kind", ["hardlink", "nonregular"])
def test_cleanup_6000_leaves_then_unsafe_entry_is_sanitized(tmp_path, monkeypatch, kind):
    tree, external = tmp_path / "owned", tmp_path / "external-sentinel"
    tree.mkdir()
    external.write_bytes(b"keep")
    for index in range(6000):
        (tree / f"leaf-{index:04d}").write_bytes(b"x")
    unsafe = tree / "private-last-entry"
    unsafe.write_bytes(b"opaque")
    original_lstat, original_scandir = Path.lstat, os.scandir
    def lstat(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == unsafe:
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                st_mode=stat.S_IFCHR if kind == "nonregular" else info.st_mode,
                st_nlink=2 if kind == "hardlink" else info.st_nlink,
                st_file_attributes=getattr(info, "st_file_attributes", 0))
        return info
    class LastUnsafe:
        def __enter__(self):
            self.entries = original_scandir(tree)
            return self
        def __exit__(self, *args):
            self.entries.close()
        def __iter__(self):
            last = None
            for entry in self.entries:
                if entry.name == unsafe.name:
                    last = entry
                else:
                    yield entry
            assert last is not None
            yield last
    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(adapters.os, "scandir", lambda path: LastUnsafe() if Path(path) == tree else original_scandir(path))
    # This models unsafe metadata after 6000 real deletions, not host throughput.
    # Advance only the adapter's clock so a slow Windows runner still reaches
    # that metadata. Keep pytest's clock and the separate deadline tests real.
    ticks = 0
    def monotonic():
        nonlocal ticks
        ticks += 1
        return ticks / 1000
    monkeypatch.setattr(adapters, "time", SimpleNamespace(
        monotonic=monotonic, sleep=adapters.time.sleep))
    result = owned_cleanup(tree)
    assert result["status"] == "HOLD" and result["reason"] == "AGY_SCRATCH_ENTRY_UNSAFE", result
    assert result["measurements"]["budget_seconds"] == adapters.AGY_CLEANUP_DEFAULT_SECONDS
    assert 0 < result["measurements"]["elapsed_seconds"] < result["measurements"]["budget_seconds"]
    assert result["measurements"]["deleted_entries"] == 6000
    assert result["measurements"]["entries_visited"] == 6002
    assert result["first_unsafe_entry"] == {
        "subtype": "HARDLINK" if kind == "hardlink" else "NON_REGULAR",
        "relative_entry_sha256": hashlib.sha256(unsafe.name.encode()).hexdigest()}
    assert external.read_bytes() == b"keep" and unsafe.read_bytes() == b"opaque"
    assert "private-last-entry" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)


@pytest.mark.skipif(os.name != "nt", reason="native Windows sharing semantics")
@pytest.mark.parametrize("release", [True, False])
def test_native_held_open_file_cleanup_is_bounded_and_does_not_change_acl(tmp_path, release):
    """CreateFile without FILE_SHARE_DELETE discriminates an actual open handle."""
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [w.HANDLE], w.BOOL
    tree = tmp_path / "owned"
    tree.mkdir()
    target = tree / "held.txt"
    target.write_bytes(b"locked")
    handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    timer = None
    if release:
        timer = threading.Timer(0.12, lambda: kernel.CloseHandle(handle))
        timer.start()
    try:
        result = owned_cleanup(tree)
        assert result["attempts"] > 1 and result["readonly_files_cleared"] == 0
        assert result["diagnostics"][0]["winerror"] == 32
        assert result["status"] == ("CLEAN" if release else "HOLD")
        if not release:
            assert result["attempts"] == 5 and target.read_bytes() == b"locked"
    finally:
        if timer:
            timer.join(timeout=2)
        else:
            kernel.CloseHandle(handle)
        if tree.exists():
            assert owned_cleanup(tree)["status"] == "CLEAN"


@pytest.mark.skipif(os.name != "nt", reason="fault injection around native Windows handles")
@pytest.mark.parametrize("permanent", [False, True])
def test_mock_transient_scanner_style_vs_permanent_access_denied(tmp_path, monkeypatch, permanent):
    """WinError5 injection fixture; this is explicitly NOT Defender evidence."""
    tree = tmp_path / "owned"
    tree.mkdir()
    (tree / "credential-do-not-log.txt").write_bytes(b"opaque")
    original = adapters._AgyCleanupHandle.delete
    failures = []
    def denied(handle, info, report):
        if permanent or len(failures) < 2:
            failures.append(1)
            error = PermissionError(13, "SECRET path must not escape into diagnostics")
            error.winerror = 5
            raise error
        return original(handle, info, report)
    with monkeypatch.context() as patch:
        patch.setattr(adapters._AgyCleanupHandle, "delete", denied)
        result = owned_cleanup(tree)
    assert result["attempts"] == (5 if permanent else 3)
    assert result["status"] == ("HOLD" if permanent else "CLEAN")
    assert all(d["classification"] == "ACCESS_DENIED_UNATTRIBUTED" for d in result["diagnostics"])
    assert "SECRET" not in json.dumps(result) and "credential" not in json.dumps(result)
    if permanent:
        assert tree.exists() and owned_cleanup(tree)["status"] == "CLEAN"


def test_multiple_scratch_roots_do_not_cross_delete_or_reuse_identity(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(tmp_path))
    first, second = [], []
    with runner._agy_temp_tree(repo, "agy_scratch-", cleanup_receipts=second) as two:
        (two / "sentinel").write_bytes(b"second")
        with runner._agy_temp_tree(repo, "agy_scratch-", cleanup_receipts=first) as one:
            (one / "sentinel").write_bytes(b"first")
        assert not one.exists() and (two / "sentinel").read_bytes() == b"second"
    assert not two.exists() and first[0]["status"] == second[0]["status"] == "CLEAN"
    assert first[0]["root_token_sha256"] != second[0]["root_token_sha256"]


def test_nested_reparse_cleanup_never_touches_external_readonly_file(tmp_path):
    from test_ef01 import _directory_alias
    import stat
    tree, outside = tmp_path / "owned", tmp_path / "outside"
    (tree / "nested").mkdir(parents=True)
    outside.mkdir()
    target = outside / "sentinel"
    target.write_bytes(b"keep")
    if os.name == "nt":
        os.chmod(target, stat.S_IREAD)  # exact fixture file, not an arbitrary parent
    link = tree / "nested/escape"
    try:
        _directory_alias(outside, link)
    except PermissionError:
        pytest.skip("native directory reparse creation denied; no bypass")
    before = target.lstat()
    result = owned_cleanup(tree)
    assert target.read_bytes() == b"keep"
    assert getattr(before, "st_file_attributes", 0) == getattr(target.lstat(), "st_file_attributes", 0)
    assert result["status"] in ("CLEAN", "HOLD")
    if result["status"] == "CLEAN":
        assert result["reparse_links_removed"] == 1
    else:
        # A filesystem that cannot attest/delete the link handle must retain
        # it and HOLD, never traverse its target as a cleanup workaround.
        assert adapters.is_link(link)


def test_root_reparse_replacement_is_preserved_and_never_followed(tmp_path, monkeypatch):
    from test_ef01 import _directory_alias
    repo, outside = tmp_path / "source", tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"keep")
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(tmp_path))
    creation_denied = False
    try:
        with pytest.raises(runner._AgyScratchCleanupError):
            with runner._agy_temp_tree(repo, "agy_scratch-") as root:
                root.rename(tmp_path / "original-root")
                try:
                    _directory_alias(outside, root)
                except PermissionError:
                    # Teardown's identity HOLD can mask this creation failure.
                    # Do not count an ordinary residual directory as a junction.
                    creation_denied = True
        if creation_denied:
            pytest.skip("native root reparse creation denied; no bypass")
        assert adapters.is_link(root)
        assert root.exists() and (outside / "sentinel").read_bytes() == b"keep"
    finally:
        if 'root' in locals() and adapters.is_link(root):
            if root.is_symlink():
                root.unlink()
            else:
                root.rmdir()


@pytest.mark.parametrize("component", ["..", "file:stream"])
def test_cleanup_path_indirection_is_rejected_before_deletion(tmp_path, component):
    tree = tmp_path / "owned"
    tree.mkdir()
    (tree / "sentinel").write_bytes(b"keep")
    info, parent = tree.stat(), tmp_path.stat()
    result = adapters.agy_remove_owned_tree(tree / component, tmp_path,
        (info.st_dev, info.st_ino), (parent.st_dev, parent.st_ino))
    assert result["status"] == "HOLD" and (tree / "sentinel").read_bytes() == b"keep"


@pytest.mark.skipif(os.name != "nt", reason="native Windows Job Object proof")
@pytest.mark.parametrize("mode", ["normal", "lingering", "abrupt", "timeout"])
def test_native_job_proves_children_empty_or_holds_before_verification(tmp_path, mode):
    lifecycle = {}
    code = "import subprocess,sys,os,time,json; "
    if mode in ("lingering", "abrupt", "timeout"):
        code += ("subprocess.Popen([sys.executable,'-I','-S','-c','import time; time.sleep(30)'],"
                 "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); ")
    code += "print(json.dumps({'status':'SUCCESS'}),flush=True); "
    if mode == "abrupt":
        code += "os._exit(137)"
    elif mode == "timeout":
        code += "time.sleep(30)"
    else:
        # Pending-task prose alone must not override native process accounting.
        code += "print('root agent idle; waiting for 1 background task(s) to finish',file=sys.stderr)"
    args = dict(lifecycle=lifecycle, cwd=tmp_path, env=os.environ.copy(), input=None,
                timeout=0.3 if mode == "timeout" else 10)
    if mode == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            runner._run_agy_owned([sys.executable, "-I", "-S", "-c", code], **args)
    else:
        result = runner._run_agy_owned([sys.executable, "-I", "-S", "-c", code], **args)
        assert result.returncode == (137 if mode == "abrupt" else 0)
    assert lifecycle["authority"] == "WINDOWS_JOB_OBJECT" and lifecycle["containment_proven"]
    assert lifecycle["tree_empty"] is True
    if mode == "normal":
        assert lifecycle["status"] == "RECONCILED" and not lifecycle["forced_termination"]
        assert lifecycle["after_grace"]["active"] == lifecycle["after_grace"]["pid_count"] == 0
    else:
        assert lifecycle["status"] == "HOLD" and lifecycle["forced_termination"]
        assert lifecycle["after_reconcile"]["active"] == lifecycle["after_reconcile"]["pid_count"] == 0


def test_tracking_unavailable_never_launches_provider(tmp_path, monkeypatch):
    def unavailable():
        raise PermissionError(13, "SECRET unavailable Windows job API")
    monkeypatch.setattr(runner, "_WindowsAgyJob", unavailable)
    lifecycle = {}
    marker = tmp_path / "provider-ran"
    with pytest.raises(runner._AgyProcessUnproven, match="AGY_PROCESS_TRACKING_UNAVAILABLE"):
        runner._run_agy_owned([sys.executable, "-c", f"open({str(marker)!r},'w').close()"],
            lifecycle=lifecycle, cwd=tmp_path, env=os.environ.copy(), input=None, timeout=3)
    assert not marker.exists() and lifecycle["status"] == "HOLD"
    assert "SECRET" not in json.dumps(lifecycle)


@pytest.mark.parametrize("tree_empty", [False, True])
def test_fake_success_cannot_replace_process_proof(tmp_path, monkeypatch, tree_empty):
    result, calls = run_fixture(tmp_path, monkeypatch, [{"status": "SUCCESS"}],
        lifecycle_result={"status": "HOLD", "tree_empty": tree_empty,
                          "reason": "AGY_PROCESS_RECONCILIATION_UNPROVEN"})
    assert calls == ["provider"] and result["verify_exit_code"] is None
    assert result["status"] == "UNPROVEN" and result["replay_authorized"] is False
    assert "quarantine_manifest" in result["evidence_files"]
    assert result["scratch_teardown"]["status"] == ("CLEAN" if tree_empty else "HOLD")


@pytest.mark.parametrize("verify_code", [0, 1])
def test_cleanup_hold_preserves_separate_build_result_and_never_replays(tmp_path, monkeypatch, verify_code):
    original = runner.agy_remove_owned_tree
    def unproven(*args, **kwargs):
        result = original(*args, **kwargs)
        # Simulated cleanup uncertainty, independently labeled as a fixture.
        result.update(status="HOLD", reason="AGY_SCRATCH_CLEANUP_UNPROVEN", fixture=True)
        return result
    monkeypatch.setattr(runner, "agy_remove_owned_tree", unproven)
    result, calls = run_fixture(tmp_path, monkeypatch, [{"status": "SUCCESS"}], verify_code=verify_code)
    assert calls == ["provider", "verify"] and result["verify_completed"]
    assert result["verify_exit_code"] == verify_code and result["scratch_teardown"]["status"] == "HOLD"
    assert result["source_build_status"] == ("VERIFICATION_FAILED" if verify_code else "BUILD_VERIFIED_FOR_SCOPE")
    assert result["status"] == ("VERIFICATION_FAILED" if verify_code else "UNPROVEN")
    assert not result["invocations"][0]["repair_eligible"] and result["invocation_count"] == 1
    assert "quarantine_manifest" in result["evidence_files"]


def test_clean_teardown_does_not_bless_subsequent_source_change(tmp_path, monkeypatch):
    original = runner.agy_remove_owned_tree
    def late_change(*args, **kwargs):
        result = original(*args, **kwargs)
        (tmp_path / "workspace/changed.txt").write_bytes(b"late mutation")
        return result
    monkeypatch.setattr(runner, "agy_remove_owned_tree", late_change)
    result, calls = run_fixture(tmp_path, monkeypatch, [{"status": "SUCCESS"}])
    assert calls == ["provider", "verify"] and result["verify_exit_code"] == 0
    assert result["status"] == "SOURCE_DRIFT" and result["error_code"] == "AGY_SOURCE_CHANGED_AFTER_TEARDOWN"
    assert result["scratch_teardown"]["status"] == "CLEAN" and "quarantine_manifest" in result["evidence_files"]


@pytest.mark.skipif(os.name != "nt", reason="native Git, Job Object and readonly teardown integration")
@pytest.mark.parametrize("lingering", [False, True])
def test_native_agy_fixture_end_to_end_readonly_git_and_lingering_writer(tmp_path, monkeypatch, lingering):
    from test_ef01 import _repo
    repo = _repo(tmp_path)
    marker = tmp_path / "verifier-ran"
    # The real Git fixture was created above under the inherited guard. Copy
    # its exact readonly object bytes/attributes into disposable provider TEMP;
    # do not bypass a Git/MSYS hook environment failure in a provider child.
    code = ("import os,sys,subprocess,json,shutil; from pathlib import Path; "
            "shutil.copytree(Path.cwd(),Path(os.environ['TEMP'])/'fixture'); "
            "Path('changed.txt').write_bytes(b'verified'); ")
    if lingering:
        child = "import time; from pathlib import Path; time.sleep(30); Path('changed.txt').write_bytes(b'late')"
        code += (f"subprocess.Popen([sys.executable,'-I','-S','-c',{child!r}],"
                 "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); ")
    code += "print(json.dumps({'status':'SUCCESS'}))"
    monkeypatch.setattr(runner, "command_for", lambda *a, **k: ([sys.executable, "-c", code], None))
    result = runner.execute_attempt(repo=repo, output_dir=tmp_path / "evidence",
        work_id="scratch-native", attempt_id="fresh-rootfix-native",
        selection=ExecutorSelection("antigravity", "gemini-4-argon-medium", "medium"),
        instruction="fixture source edit only", allowed_prefixes=["changed.txt"], max_repair_rounds=0,
        verify_argv=[sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_bytes(b'verified')"])
    assert result["process_lifecycle"]["authority"] == "WINDOWS_JOB_OBJECT"
    assert result["process_lifecycle"]["tree_empty"]
    assert result["scratch_teardown"]["status"] == "CLEAN"
    assert any(r["readonly_files_cleared"] for r in result["scratch_teardown"]["roots"])
    assert marker.exists() is (not lingering)
    assert result["status"] == ("UNPROVEN" if lingering else "BUILD_VERIFIED_FOR_SCOPE")
    assert result["model"] == "gemini-4-argon-medium" and result["reasoning_effort"] == "medium"
    if lingering:
        assert result["error_code"] == "AGY_DESCENDANTS_STILL_ACTIVE"
        assert result["verify_exit_code"] is None and "quarantine_manifest" in result["evidence_files"]
    else:
        after_verify = result["evidence_sha256"]["source_after_verify"]
        assert after_verify == result["evidence_sha256"]["source_after_teardown"]
        assert result["verify_exit_code"] == 0


def _measured_tree(tmp_path, files=12, depth=2):
    tree = tmp_path / "measured"
    leaf = tree
    for level in range(depth):
        leaf = leaf / f"d{level}"
    leaf.mkdir(parents=True)
    for index in range(files):
        (leaf / f"f{index}.txt").write_bytes(b"x")
    identity, parent = tree.stat(), tmp_path.stat()
    return tree, (identity.st_dev, identity.st_ino), (parent.st_dev, parent.st_ino)


def test_cleanup_reports_sanitized_measurements(tmp_path):
    tree, identity, parent = _measured_tree(tmp_path)
    result = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent)
    measured = result["measurements"]
    assert result["status"] == "CLEAN" and not tree.exists()
    assert measured["entries_visited"] == 12 + 2 + 1
    assert measured["deleted_entries"] == 15 and measured["max_depth"] == 3
    assert 0 <= measured["elapsed_seconds"] <= measured["budget_seconds"]
    assert measured["entry_limit"] == adapters.AGY_CLEANUP_MAX_ENTRIES
    assert measured["depth_limit"] == adapters.AGY_CLEANUP_MAX_DEPTH
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("zero_timeout", [0, 0.0])
def test_cleanup_zero_budget_holds_immediately(tmp_path, zero_timeout):
    tree, identity, parent = _measured_tree(tmp_path)
    result = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent, timeout_seconds=zero_timeout)
    assert result["status"] == "HOLD"
    assert result["reason"] == "AGY_SCRATCH_CLEANUP_TIME_EXCEEDED"
    assert result["measurements"]["budget_seconds"] == 0.0
    assert tree.exists()


def test_cleanup_elapsed_budget_holds_before_mutation(tmp_path, monkeypatch):
    tree, identity, parent = _measured_tree(tmp_path)
    # A positive default budget must still expire before touching the tree.
    moments = iter((0.0, adapters.AGY_CLEANUP_DEFAULT_SECONDS + 1,
                    adapters.AGY_CLEANUP_DEFAULT_SECONDS + 2))
    monkeypatch.setattr(adapters, "time", SimpleNamespace(
        monotonic=lambda: next(moments), sleep=adapters.time.sleep))
    result = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent)
    assert result["status"] == "HOLD"
    assert result["reason"] == "AGY_SCRATCH_CLEANUP_TIME_EXCEEDED"
    assert result["measurements"]["budget_seconds"] == adapters.AGY_CLEANUP_DEFAULT_SECONDS
    assert result["measurements"]["elapsed_seconds"] > result["measurements"]["budget_seconds"]
    assert result["measurements"]["entries_visited"] == 1
    assert result["measurements"]["deleted_entries"] == 0
    assert result["first_unsafe_entry"] is None
    assert len(list((tree / "d0" / "d1").iterdir())) == 12
    assert (tree / "d0" / "d1" / "f0.txt").read_bytes() == b"x"


@pytest.mark.parametrize("invalid_timeout", [
    float("nan"),
    float("inf"),
    float("-inf"),
    True,
    False,
    -1.0,
    -0.001,
    -1,
    -(10**400),
    -(2**4096),
    adapters.AGY_CLEANUP_MAX_SECONDS + 0.001,
    adapters.AGY_CLEANUP_MAX_SECONDS + 1,
    10**9,
    2**1024,
    10**400,
    2**4096,
    "30",
    None,
    [],
    {},
])
def test_cleanup_rejects_nonfinite_and_invalid_timeout(tmp_path, invalid_timeout):
    tree, identity, parent = _measured_tree(tmp_path)
    result = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent, timeout_seconds=invalid_timeout)
    assert result["status"] == "HOLD"
    assert result["reason"] == "AGY_SCRATCH_CLEANUP_TIMEOUT_INVALID"
    assert tree.exists()
    assert (tree / "d0" / "d1" / "f0.txt").is_file()
    assert result["attempts"] == 0
    assert result["readonly_files_cleared"] == 0
    assert result["reparse_links_removed"] == 0
    assert result["measurements"]["deleted_entries"] == 0
    assert result["measurements"]["entries_visited"] == 0


@pytest.mark.parametrize("valid_timeout", [
    15.0,
    adapters.AGY_CLEANUP_MAX_SECONDS,
    int(adapters.AGY_CLEANUP_MAX_SECONDS),
])
def test_cleanup_valid_custom_budget(tmp_path, valid_timeout):
    tree, identity, parent = _measured_tree(tmp_path)
    result = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent, timeout_seconds=valid_timeout)
    assert result["status"] == "CLEAN" and not tree.exists()
    assert result["measurements"]["budget_seconds"] == float(valid_timeout)


def test_cleanup_entry_and_depth_limits_are_separate_reasons(tmp_path, monkeypatch):
    tree, identity, parent = _measured_tree(tmp_path)
    monkeypatch.setattr(adapters, "AGY_CLEANUP_MAX_ENTRIES", 5)
    entries = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent)
    assert entries["status"] == "HOLD"
    assert entries["reason"] == "AGY_SCRATCH_CLEANUP_ENTRY_LIMIT_EXCEEDED"
    assert tree.exists()
    monkeypatch.setattr(adapters, "AGY_CLEANUP_MAX_ENTRIES", 100_000)
    monkeypatch.setattr(adapters, "AGY_CLEANUP_MAX_DEPTH", 1)
    depth = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent)
    assert depth["status"] == "HOLD"
    assert depth["reason"] == "AGY_SCRATCH_CLEANUP_DEPTH_LIMIT_EXCEEDED"
    assert tree.exists()


def test_cleanup_residual_present_holds(tmp_path, monkeypatch):
    tree, identity, parent = _measured_tree(tmp_path)
    if os.name == "nt":
        monkeypatch.setattr(adapters._AgyCleanupHandle, "delete", lambda self, info, report: None)
    else:
        monkeypatch.setattr(Path, "rmdir", lambda self: None)
        monkeypatch.setattr(Path, "unlink", lambda self: None)
    result = adapters.agy_remove_owned_tree(tree, tmp_path, identity, parent)
    assert result["status"] == "HOLD"
    assert result["reason"] == "AGY_SCRATCH_RESIDUAL_PRESENT"
    assert tree.exists()


@pytest.mark.skipif(os.name != "nt", reason="kernel32 DLL is Windows-only")
def test_agy_kernel_thread_safety(monkeypatch):
    monkeypatch.setattr(adapters, "_AGY_KERNEL", None)
    results = []
    def worker():
        results.append(adapters._agy_kernel())
    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 16
    first = results[0]
    assert all(r is first for r in results)
    assert first is adapters._AGY_KERNEL


@pytest.mark.skipif(os.name != "nt", reason="native Windows performance and Git loose objects")
def test_native_medium_large_scratch_scope_proof(tmp_path):
    """Host-owned targeted fixture with >=1,000 ordinary scratch leaves,

    moderate nested tree, multiple read-only Git loose-object-like files,
    and observed elapsed/entries/depth/final root ABSENT.
    """
    tree = tmp_path / "large_scratch"
    tree.mkdir()
    total_files = 0
    readonly_count = 0
    for d_idx in range(10):
        branch = tree / f"dir_{d_idx:02d}" / "sub"
        branch.mkdir(parents=True)
        for f_idx in range(100):
            file_path = branch / f"leaf_{f_idx:03d}.tmp"
            file_path.write_bytes(b"disposable payload")
            total_files += 1
            if f_idx < 2:  # 2 per dir * 10 dirs = 20 read-only files
                os.chmod(file_path, stat.S_IREAD)
                readonly_count += 1
    assert total_files == 1000
    assert readonly_count == 20

    identity = tree.stat()
    parent_identity = tmp_path.stat()
    result = adapters.agy_remove_owned_tree(
        tree, tmp_path,
        (identity.st_dev, identity.st_ino),
        (parent_identity.st_dev, parent_identity.st_ino)
    )

    assert result["status"] == "CLEAN"
    assert result["reason"] is None
    assert not tree.exists()
    assert result["readonly_files_cleared"] == 20
    measurements = result["measurements"]
    assert measurements["entries_visited"] == 1021
    assert measurements["deleted_entries"] == 1021
    assert measurements["max_depth"] == 3
    assert measurements["elapsed_seconds"] >= 0.0
    assert measurements["elapsed_seconds"] <= measurements["budget_seconds"]
    assert str(tmp_path) not in json.dumps(result)
