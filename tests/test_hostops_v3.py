"""#225 category-derived 48-row matrix. Fixture files only; NO live host enablement.

The linked Issue omits the original row descriptions. The manifest records that
gap rather than pretending these derived descriptions are the unavailable text.
Real OpenSSH signatures and native fixture I/O exercise the complete worker.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_commerce.hostops_v3 import auth, broker, cli, contract as c, filesystem as fs, security, transport
from ai_commerce.hostops_v3.journal import Journal
from ai_commerce.hostops_v3.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
NOW = 1800000000


class Crash(BaseException):
    pass


def fixture_powershell_pin():
    """Pin readable fixture bytes, never attest production signed PowerShell.

    Store App Execution Aliases can launch but raise EINVAL on read_bytes().
    The existing resolver prefers a readable real pwsh in PATH. Recheck its
    complete bytes; only this test harness may fall back to readable Python.
    Tests needing native PowerShell select and execute it independently.
    """
    candidate = security.prefer_readable_pwsh_path()
    if candidate:
        executable = Path(candidate)
        try:
            raw = executable.read_bytes()
        except OSError:
            pass
        else:
            if raw:
                return str(executable), c.digest(raw)
    executable = Path(sys.executable)
    raw = executable.read_bytes()  # A broken fallback is a fixture error.
    assert raw, "readable nonempty fixture executable required"
    return str(executable), c.digest(raw)


def test_fixture_powershell_pin_prefers_readable_pwsh(tmp_path, monkeypatch):
    executable = tmp_path / "pwsh.exe"
    executable.write_bytes(b"readable test fixture")
    monkeypatch.setattr(security, "prefer_readable_pwsh_path", lambda: str(executable))
    assert fixture_powershell_pin() == (str(executable), c.digest(executable.read_bytes()))


@pytest.mark.parametrize("error_number", [errno.EINVAL, errno.EACCES])
def test_fixture_powershell_pin_unreadable_alias(tmp_path, monkeypatch, error_number):
    alias = tmp_path / "WindowsApps" / "pwsh.EXE"
    original_read = Path.read_bytes
    expected = (sys.executable, c.digest(original_read(Path(sys.executable))))
    path_before = os.environ.get("PATH")
    monkeypatch.setattr(security, "prefer_readable_pwsh_path", lambda: str(alias))

    def read_bytes(path):
        if path == alias:
            raise OSError(error_number, "simulated unreadable App Execution Alias")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    assert fixture_powershell_pin() == expected
    assert os.environ.get("PATH") == path_before


def test_fixture_powershell_pin_missing_pwsh_and_broken_fallback(monkeypatch):
    monkeypatch.setattr(security, "prefer_readable_pwsh_path", lambda: None)
    assert fixture_powershell_pin() == (sys.executable, c.digest(Path(sys.executable).read_bytes()))
    monkeypatch.setattr(Path, "read_bytes", lambda _: (_ for _ in ()).throw(OSError(errno.EINVAL, "unreadable")))
    with pytest.raises(OSError):
        fixture_powershell_pin()


@pytest.fixture(scope="session")
def keys(tmp_path_factory):
    root = tmp_path_factory.mktemp("h225-independent-test-keys")
    exe = shutil.which("ssh-keygen")
    assert exe, "OpenSSH required for cryptographic tests (must not silently skip)"
    for key in ("human", "issuer"):
        p = subprocess.run([exe, "-q", "-t", "ed25519", "-N", "", "-f", str(root / key)], capture_output=True)
        assert p.returncode == 0, "test key generation unavailable"
    return root, Path(exe)


class Harness:
    def __init__(self, root, keys):
        self.root, (self.keys, exe), self.serial = root, keys, 0
        self.data, self.state, self.policy, self.release = [root / k for k in ("data", "state", "policy", "release")]
        for p in (self.data, self.state, self.policy, self.release):
            p.mkdir()
        pwsh, pwsh_sha256 = fixture_powershell_pin()
        self.config = {"schema": "hostops.config.v3", "enabled": True, "host_id": "test-host-001",
            "policy_dir": str(self.policy), "state_dir": str(self.state), "release_dir": str(self.release),
            "transport_state_dir": str(self.state),
            "ssh_keygen": str(exe), "ssh_keygen_sha256": c.digest(exe.read_bytes()),
            "pwsh": pwsh, "pwsh_sha256": pwsh_sha256,
            "broker_identity": "broker-identity", "transport_identity": "transport-identity",
            "untrusted_identities": ["ci-identity"], "trusted_owners": ["installer-identity"],
            "endpoint": str(self.state / "socket")}
        self.manifest = {"schema": "hostops.release.v3", "release_id": "a" * 64,
                         "source_sha": "846110081b0cae3b490e36e142cdea945754df6a",
                         "workflow_sha256": "b" * 64, "files": {}}
        (self.policy / "human-signers").write_text("hostops-human " + (self.keys / "human.pub").read_text())
        (self.policy / "issuer-signers").write_text("issuer-001 " + (self.keys / "issuer.pub").read_text())
        self.revoke()
        self.authority = auth.Authority(self.config, self.manifest, clock=lambda: NOW)
        self.journal = Journal(self.state / "journal.sqlite")
        self.worker = Worker(self.config, self.authority, self.journal)

    def revoke(self, *, leases=(), requests=(), epoch=0):
        (self.policy / "revocations.json").write_bytes(c.canonical({"schema": "hostops.revocations.v3",
            "epoch": epoch, "lease_ids": list(leases), "request_ids": list(requests)}))

    def sign(self, body, role, namespace):
        self.serial += 1
        path = self.root / ("signature-input-" + str(self.serial))
        path.write_bytes(c.canonical(body))
        result = subprocess.run([self.config["ssh_keygen"], "-Y", "sign", "-n", namespace,
            "-f", str(self.keys / role), str(path)], capture_output=True)
        assert result.returncode == 0, "test signing unavailable"
        return base64.b64encode(path.with_suffix(path.suffix + ".sig").read_bytes()).decode("ascii")

    def make(self, op="stat", p=None, *, updates=None, lease_updates=None, exact=False):
        self.serial += 1
        r = {"schema": "hostops.request.v3", "request_id": "request-" + str(self.serial).zfill(5),
             "repository": c.REPOSITORY, "issuer": "issuer-001", "host_id": "test-host-001",
             "release_id": self.manifest["release_id"], "source_sha": self.manifest["source_sha"],
             "created_at": NOW - 1, "expires_at": NOW + 120, "timeout_seconds": 5,
             "operation": op, "parameters": p if p is not None else {"path": str(self.data)}, "preview": False}
        r.update(updates or {})
        lease = {"schema": "hostops.lease.v3", "lease_id": "human-lease-001", "issuer": "issuer-001",
            "host_id": "test-host-001", "release_id": self.manifest["release_id"], "roots": [str(self.data)],
            "operations": sorted(c.OPS), "not_before": NOW - 10, "expires_at": NOW + 1000,
            "max_bytes": c.MAX_FILE, "max_entries": 256, "allow_unc": False}
        lease.update(lease_updates or {})
        return self.seal(r, lease, exact=exact)

    def seal(self, request, lease, *, exact=False):
        return {"schema": "hostops.envelope.v3", "request": request, "lease": lease,
            "issuer_signature_b64": self.sign(request, "issuer", "hostops-v3-request"),
            "lease_signature_b64": self.sign(lease, "human", "hostops-v3-lease"),
            "action_signature_b64": self.sign({"request": request, "lease_sha256": c.digest(c.canonical(lease))},
                "human", "hostops-v3-exact-action") if exact else ""}

    def execute(self, e, **kwargs):
        return self.worker.execute(c.canonical(e), **kwargs)

    def file(self, name="ordinary.txt", content=b"original"):
        p = self.data / name
        p.write_bytes(content)
        return p

    def write(self, op="create", *, path=None, data=b"new", old=b"original", **params):
        p = {"path": str(path or self.data / "new.txt"), "data_b64": base64.b64encode(data).decode(), "data_sha256": c.digest(data)}
        if op != "create":
            p["expected_sha256"] = c.digest(old)
        p.update(params)
        return self.make(op, p)


@pytest.fixture
def h(tmp_path, keys):
    value = Harness(tmp_path, keys)
    yield value
    value.journal.close()


def result(response):
    return response["receipt"]


def prototype_adapter(h, *, fault=lambda _: None):
    """Fixture-only adapter tests do not establish broker CAS acceptance."""
    e = h.make()
    return fs.Filesystem(e["lease"], h.state, [h.policy, h.state, h.release],
                         lambda: h.authority.checkpoint(e["request"], e["lease"]), fault=fault)


def assert_conditional_blocked(h, e):
    out = result(h.execute(e))
    assert out["status"] == "BLOCKED"
    assert out["error_code"] == "CONDITIONAL_WRITE_ATOMICITY_UNPROVEN"


def test_h225_01_strict_canonical_request(h):
    e = h.make()
    assert c.envelope(c.canonical(e))[1].fingerprint == c.digest(c.canonical(e["request"]))
    assert c.decode(b'{"b":2,"a":1}') == c.decode(b'{ "a":1, "b":2 }')
    for raw in (b'{"x":1,"x":2}', b'{"x":1.1}', b'{"x":NaN}', b'[]', b'x' * (c.MAX_WIRE + 1)):
        with pytest.raises(c.Denied):
            c.envelope(raw)
    for key, bad in (("timeout_seconds", True), ("operation", "shell"), ("preview", 1)):
        r = {**e["request"], key: bad}
        with pytest.raises(c.Denied):
            c.Request.parse(r)
    with pytest.raises(c.Denied):
        c.Request.parse({**e["request"], "unreviewed": "field"})


def test_h225_02_eligible_volume_enumeration(h):
    r = result(h.execute(h.make("volumes", {})))
    assert r["status"] == "OBSERVED" and r["result"]["volume_count"] == 1
    assert str(h.data) not in json.dumps(r)
    missing = str(h.data / "offline-volume")
    r = result(h.execute(h.make("volumes", {}, lease_updates={"roots": [missing]})))
    assert r["status"] == "UNAVAILABLE" and "volume_count" not in r["result"]


def test_h225_03_metadata_and_local_directory_selection(h):
    p = h.file("private-filename.txt")
    out = result(h.execute(h.make("list", {"path": str(h.data), "limit": 10})))
    assert out["result"]["count"] == 1 and "private-filename" not in json.dumps(out)
    sid = out["result"]["selection_id"]
    assert b"private-filename" in (h.state / "selections" / (sid + ".bin")).read_bytes()
    s = result(h.execute(h.make("stat", {"path": str(p)})))
    assert s["result"]["bytes"] == len(b"original")


def test_h225_04_unicode_scoped_find_and_limits(h):
    h.file("tiếng-Việt.txt")
    h.file("unrelated.txt")
    out = result(h.execute(h.make("find", {"path": str(h.data), "needle": "VIỆT", "limit": 2})))
    assert out["result"]["count"] == 1 and "Việt" not in json.dumps(out)
    out = result(h.execute(h.make("find", {"path": str(h.root), "needle": "txt", "limit": 2})))
    assert out["error_code"] == "PATH_OUTSIDE_LEASE"


def test_h225_05_large_binary_chunks_and_full_digest(h):
    data = bytes(range(256)) * 1025
    p = h.file("large.bin", data)
    reconstructed = bytearray()
    for offset in range(0, len(data), c.MAX_CHUNK):
        out = result(h.execute(h.make("read", {"path": str(p), "offset": offset, "length": c.MAX_CHUNK,
                                                   "expected_sha256": c.digest(data)})))
        assert out["status"] == "OBSERVED" and out["result"]["sha256"] == c.digest(data)
        chunk = (h.state / "selections" / (out["result"]["selection_id"] + ".bin")).read_bytes()
        assert c.digest(chunk) == out["result"]["chunk_sha256"]
        reconstructed.extend(chunk)
    assert bytes(reconstructed) == data


def test_h225_06_encoding_preserved_as_bytes(h):
    content = "Xin chào\n世界".encode("utf-16-le")
    p = h.file("Unicode-世界.txt", content)
    out = result(h.execute(h.make("read", {"path": str(p), "offset": 0, "length": 65536,
                                         "expected_sha256": c.digest(content)})))
    assert out["result"]["bytes"] == len(content) and out["result"]["chunk_sha256"] == c.digest(content)


def test_h225_07_binary_read_requires_separate_exact_disclosure(h):
    data = b"\x00private-content\xff"
    p = h.file("binary.bin", data)
    selected = result(h.execute(h.make("read", {"path": str(p), "offset": 0, "length": 100, "expected_sha256": c.digest(data)})))
    params = {"selection_id": selected["result"]["selection_id"], "content_sha256": c.digest(data),
              "offset": 0, "length": len(data), "classification": "sensitive", "recipient": "github:trung-via/ai-commerce/issues/999"}
    assert result(h.execute(h.make("disclose", params)))["status"] == "BLOCKED"
    e = h.make("disclose", params, exact=True)
    out = h.execute(e)
    assert out["receipt"]["status"] == "DISCLOSED" and base64.b64decode(out["disclosure_b64"]) == data
    assert "disclosure_b64" not in h.execute(e)  # No cached confidential-byte re-export.


def test_h225_08_sensitive_stores_and_read_export_boundary(h):
    for part in (".ssh", ".aws", ".git", ".github", "secrets", ".env", "Cookies"):
        out = result(h.execute(h.make("hash", {"path": str(h.data / part / "private.txt")})))
        assert out["error_code"] == "PROTECTED_PATH"
    out = h.execute(h.make("stat", {"path": str(h.data)}))
    assert set(out) == {"receipt"}


def test_h225_09_create_outside_old_root_with_hash(h):
    e = h.write(data=b"fixture outside old approved root")
    out = result(h.execute(e))
    assert out["status"] == "APPLIED" and out["result"]["sha256"] == c.digest((h.data / "new.txt").read_bytes())
    other = h.write(data=b"overwrite")
    assert result(h.execute(other))["error_code"] == "ALREADY_EXISTS"


def test_h225_10_atomic_replace_hash_precondition(h):
    p = h.file()
    e = h.write("replace", path=p, data=b"replacement")
    assert_conditional_blocked(h, e)
    assert p.read_bytes() == b"original"
    adapter = prototype_adapter(h)
    adapter.mutate("replace", e["request"]["parameters"])
    assert p.read_bytes() == b"replacement"
    with pytest.raises(c.Denied, match="PRECONDITION_FAILED"):
        adapter.mutate("replace", h.write("replace", path=p, data=b"bad")["request"]["parameters"])
    assert p.read_bytes() == b"replacement"


def test_h225_11_append_hash_precondition(h):
    p = h.file()
    e = h.write("append", path=p, data=b"+suffix")
    assert_conditional_blocked(h, e)
    assert p.read_bytes() == b"original"
    adapter = prototype_adapter(h)
    out = adapter.mutate("append", e["request"]["parameters"])
    assert out["sha256"] == c.digest(b"original+suffix") and p.read_bytes() == b"original+suffix"
    with pytest.raises(c.Denied, match="PRECONDITION_FAILED"):
        adapter.mutate("append", h.write("append", path=p)["request"]["parameters"])


def test_h225_12_typed_patch_and_preview(h):
    p = h.file()
    e = h.write("patch", path=p, data=b"PATCH", offset=1, remove_bytes=2)
    preview = h.seal({**e["request"], "preview": True}, e["lease"])
    assert result(h.execute(preview))["status"] == "PREVIEW" and p.read_bytes() == b"original"
    e["request"]["request_id"] = "patch-after-preview"
    assert_conditional_blocked(h, h.seal(e["request"], e["lease"]))
    assert p.read_bytes() == b"original"
    prototype_adapter(h).mutate("patch", e["request"]["parameters"])
    assert p.read_bytes() == b"oPATCHginal"


def test_h225_13_traversal_ads_device_normalization(h):
    for path in (str(h.data / ".." / "escape.txt"), "relative.txt", str(h.data / "con.txt"),
                 str(h.data / "x.txt:stream"), str(h.data / "trailing. "), "\\\\?\\C:\\escape"):
        out = result(h.execute(h.write(path=Path(path))))
        assert out["status"] == "BLOCKED"
    assert not (h.root / "escape.txt").exists()


def test_h225_14_actual_acl_denial_is_not_bypassed(h, monkeypatch):
    p = h.file()
    original = fs.PinnedPath.open
    def denied(self, *args, **kwargs):
        if self.path == p:
            raise PermissionError(errno.EACCES, "simulated OS ACL denial")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(fs.PinnedPath, "open", denied)
    out = result(h.execute(h.make("hash", {"path": str(p)})))
    assert out["status"] == "UNAVAILABLE" and p.read_bytes() == b"original"


def test_h225_15_missing_parent_is_unavailable_not_created(h):
    out = result(h.execute(h.write(path=h.data / "missing" / "new.txt")))
    assert out["status"] == "UNAVAILABLE" and not (h.data / "missing").exists()


def test_h225_16_unc_is_explicit_and_offline_is_unavailable(h, monkeypatch):
    with pytest.raises(c.Denied, match="UNC_DENIED"):
        fs.canonical_path("//offline/share/file.txt", ["//offline/share"], False)
    if os.name == "nt":
        path = fs.canonical_path("\\\\offline\\share\\file.txt", ["\\\\offline\\share"], True)
        assert str(path).startswith("\\\\offline")
    monkeypatch.setattr(fs.Filesystem, "metadata", lambda *_: (_ for _ in ()).throw(OSError(errno.ENETDOWN, "offline UNC")))
    assert result(h.execute(h.make("volumes", {})))["status"] == "UNAVAILABLE"


def test_h225_17_reparse_and_hardlink_escape(h, monkeypatch, record_property):
    p = h.file()
    hard = h.data / "hardlink.txt"
    try:
        os.link(p, hard)
    except PermissionError:
        record_property("native_hardlinks", "OS_DENIED; link-count fault injection used")
        original, inode = os.fstat, p.stat().st_ino
        def alias(fd):
            s = original(fd)
            return SimpleNamespace(st_mode=s.st_mode, st_nlink=2) if s.st_ino == inode else s
        with monkeypatch.context() as scoped:
            scoped.setattr(os, "fstat", alias)
            out = result(h.execute(h.make("hash", {"path": str(p)})))
    else:
        out = result(h.execute(h.make("hash", {"path": str(hard)})))
    assert out["status"] == "BLOCKED"
    with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0o100600, st_file_attributes=0x400)):
        with pytest.raises(c.Denied, match="REPARSE_DENIED"):
            fs.no_link(p)
    if os.name != "nt":
        link = h.data / "link"
        link.symlink_to(h.root, target_is_directory=True)
        assert result(h.execute(h.write(path=link / "escape.txt")))["status"] == "UNAVAILABLE"


def test_h225_18_toctou_target_change_keeps_external_data(h):
    p = h.file()
    def race(point):
        if point == "before_replace_check":
            p.write_bytes(b"concurrent external edit")
    e = h.write("replace", path=p)
    assert_conditional_blocked(h, e)
    adapter = prototype_adapter(h, fault=race)
    with pytest.raises(c.Denied, match="PRECONDITION_FAILED"):
        adapter.mutate("replace", e["request"]["parameters"])
    assert adapter.started
    assert p.read_bytes() == b"concurrent external edit" and not list(h.data.glob(".hostops-stage-*"))


def test_h225_19_copy_no_clobber_and_hash(h):
    source = h.file()
    dest = h.data / "copy.txt"
    params = {"path": str(source), "destination": str(dest), "expected_sha256": c.digest(b"original")}
    assert result(h.execute(h.make("copy", params)))["status"] == "APPLIED"
    assert source.read_bytes() == dest.read_bytes() == b"original"
    assert result(h.execute(h.make("copy", params)))["error_code"] == "ALREADY_EXISTS"


def test_h225_20_same_volume_move_and_rename(h):
    for op in ("move", "rename"):
        source = h.file(op + ".txt")
        dest = h.data / (op + "-dest.txt")
        e = h.make(op, {"path": str(source), "destination": str(dest), "expected_sha256": c.digest(b"original")})
        assert_conditional_blocked(h, e)
        assert source.exists() and not dest.exists()
        prototype_adapter(h).mutate(op, e["request"]["parameters"])
        assert not source.exists() and dest.read_bytes() == b"original"


def test_h225_21_cross_volume_move_is_explicit(h, monkeypatch):
    monkeypatch.setattr(fs.Filesystem, "cross_volume", staticmethod(lambda *_: True))
    source, dest = h.file(), h.data / "other-volume.txt"
    e = h.make("move", {"path": str(source), "destination": str(dest), "expected_sha256": c.digest(b"original")})
    assert_conditional_blocked(h, e)
    assert source.exists() and not dest.exists()
    out = prototype_adapter(h).mutate("move", e["request"]["parameters"])
    assert out["cross_volume"] is True
    assert not source.exists() and dest.read_bytes() == b"original"


def test_h225_22_cross_volume_failure_requires_reconciliation(h, monkeypatch):
    monkeypatch.setattr(fs.Filesystem, "cross_volume", staticmethod(lambda *_: True))
    source, dest = h.file(), h.data / "other-volume.txt"
    def fault(point):
        if point == "before_source_remove":
            raise OSError(errno.EACCES, "source removal denied")
    e = h.make("move", {"path": str(source), "destination": str(dest), "expected_sha256": c.digest(b"original")})
    assert_conditional_blocked(h, e)
    assert source.exists() and not dest.exists()
    # Simulate an unresolved intent left by the pre-block prototype release.
    legacy = h.make("move", e["request"]["parameters"])
    h.journal.reserve(c.Request.parse(legacy["request"]), 0)
    with pytest.raises(OSError):
        prototype_adapter(h, fault=fault).mutate("move", e["request"]["parameters"])
    assert source.read_bytes() == dest.read_bytes() == b"original"
    assert result(h.execute(legacy))["status"] == "UNKNOWN"
    assert result(h.execute(legacy, reconcile=True))["status"] == "UNKNOWN"
    assert source.exists() and dest.exists()


def test_h225_23_quarantine_exact_approval_preview_and_no_fake_rollback(h):
    source = h.file()
    p = {"path": str(source), "expected_sha256": c.digest(b"original"), "preview_sha256": "0" * 64}
    preview = h.make("quarantine", p, updates={"preview": True})
    proof = result(h.execute(preview))["result"]["preview_sha256"]
    p = {**p, "preview_sha256": proof}
    assert result(h.execute(h.make("quarantine", p)))["status"] == "BLOCKED" and source.exists()
    assert_conditional_blocked(h, h.make("quarantine", p, exact=True))
    assert source.exists()
    prototype_adapter(h).mutate("quarantine", p)
    assert not source.exists()
    quarantined = list(h.data.glob(".hostops-quarantine-*"))
    assert len(quarantined) == 1 and quarantined[0].read_bytes() == b"original"


def test_h225_24_recursive_delete_acl_registry_custom_shell_denied(h):
    for op in ("delete", "recursive_delete", "set_acl", "registry", "service", "command", "elevate"):
        out = result(h.execute(h.make(op, {"command": "arbitrary"})))
        assert out["status"] == "BLOCKED"
    assert not list(h.data.iterdir())


def test_h225_25_protected_paths_include_installed_control_plane(h):
    for p in (h.policy / "new.txt", h.state / "new.txt", h.release / "new.txt", h.data / ".github" / "workflow.yml"):
        e = h.write(path=p)
        e["lease"]["roots"] = [str(h.root)]
        e = h.seal(e["request"], e["lease"])
        assert result(h.execute(e))["error_code"] == "PROTECTED_PATH"
        assert not p.exists()


def test_h225_26_disk_full_during_write_is_unknown(h, monkeypatch):
    original = fs.PinnedPath.open
    class FullDisk:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *_):
            self.stream.close()
        def write(self, data):
            self.stream.write(data[:1])
            self.stream.flush()
            raise OSError(errno.ENOSPC, "simulated disk full")
    def open_full(self, *args, **kwargs):
        stream = original(self, *args, **kwargs)
        return FullDisk(stream) if kwargs.get("create") else stream
    monkeypatch.setattr(fs.PinnedPath, "open", open_full)
    e = h.write(data=b"abcdef")
    assert result(h.execute(e))["status"] == "UNKNOWN"
    assert (h.data / "new.txt").read_bytes() == b"a"
    assert result(h.execute(e))["status"] == "UNKNOWN"


def test_h225_27_crash_after_durable_intent_never_reexecutes(h):
    e = h.write()
    h.worker.fault = lambda point: (_ for _ in ()).throw(Crash()) if point == "after_intent" else None
    with pytest.raises(Crash):
        h.execute(e)
    h.journal.close()
    h.journal = Journal(h.state / "journal.sqlite")
    h.worker = Worker(h.config, h.authority, h.journal)
    assert result(h.execute(e))["status"] == "UNKNOWN" and not (h.data / "new.txt").exists()
    assert result(h.execute(e, reconcile=True))["status"] == "UNAVAILABLE"
    assert not (h.data / "new.txt").exists()


def test_h225_28_crash_after_effect_reconciles_observation_only(h):
    e = h.write()
    h.worker.fault = lambda point: (_ for _ in ()).throw(Crash()) if point == "after_effect" else None
    with pytest.raises(Crash):
        h.execute(e)
    h.worker.fault = lambda _: None
    assert (h.data / "new.txt").read_bytes() == b"new"
    out = result(h.execute(e, reconcile=True))
    assert out["status"] == "UNKNOWN" and out["result"]["reconciled"] is True
    assert out["result"]["sha256"] == c.digest(b"new")


def test_h225_29_cancel_kill_switch_mid_operation(h):
    p = h.data / "new.txt"
    def kill(point):
        if point == "after_destination":
            (h.policy / "KILL").touch()
    h.worker.fault = kill
    e = h.write(path=p)
    out = result(h.execute(e))
    assert out["status"] == "PARTIAL" and out["error_code"] == "KILL_SWITCH"
    assert p.read_bytes() == b"new"  # No invented rollback after the kill.
    assert result(h.execute(e))["error_code"] == "KILL_SWITCH"


def test_h225_30_report_failure_does_not_repeat_write(h, monkeypatch):
    e = h.write(data=b"once")
    monkeypatch.setattr(transport, "provenance", lambda *_: c.canonical(e))
    api = SimpleNamespace(post=lambda *_: c.deny("GITHUB_UNAVAILABLE"))
    send = lambda _config, raw: h.worker.execute(raw)
    with pytest.raises(c.Denied, match="GITHUB_UNAVAILABLE"):
        transport.poll_once(api, h.config, h.manifest, 999, 123, send=send)
    assert (h.data / "new.txt").read_bytes() == b"once"
    assert len(list(h.state.glob("receipt-*.json"))) == 1
    assert result(h.execute(e))["status"] == "APPLIED"
    assert (h.data / "new.txt").read_bytes() == b"once"


def test_h225_31_semantic_replay_and_id_conflict(h):
    p = h.data / "new.txt"
    e = h.write(path=p, data=b"once")
    first = h.execute(e)
    pretty = json.dumps(e, indent=4).encode()
    assert h.worker.execute(pretty) == first and p.read_bytes() == b"once"
    changed = {**e["request"], "parameters": {**e["request"]["parameters"], "data_b64": "eA==", "data_sha256": c.digest(b"x")}}
    assert result(h.execute(h.seal(changed, e["lease"])))["error_code"] == "REQUEST_ID_CONFLICT"


def test_h225_32_concurrent_writes_are_serialized(h):
    p = h.data / "new.txt"
    first, second = h.write(path=p, data=b"A"), h.write(path=p, data=b"B")
    ready, effect = threading.Barrier(2), threading.Event()
    def run(e, slow):
        journal = Journal(h.state / "journal.sqlite")
        ready.wait(timeout=5)
        def fault(point):
            if slow and point == "before_effect":
                effect.set()
                time.sleep(0.35)
        worker = Worker(h.config, h.authority, journal, fault=fault)
        try:
            if not slow:
                assert effect.wait(5)
            return result(worker.execute(c.canonical(e)))
        finally:
            journal.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(run, first, True), pool.submit(run, second, False)
        values = [a.result(10), b.result(10)]
    assert sorted(v["status"] for v in values) == ["APPLIED", "BUSY"]
    assert p.read_bytes() == b"A"


def test_h225_33_signed_payload_tamper_has_no_effect(h):
    e = h.write()
    e["request"]["parameters"]["path"] = str(h.data / "tampered.txt")
    assert result(h.execute(e))["error_code"] == "SIGNATURE_INVALID"
    assert not list(h.data.iterdir())


def test_h225_34_release_pin_independent_of_main_sha(h):
    assert result(h.execute(h.make()))["status"] == "OBSERVED"
    out = result(h.execute(h.make(updates={"source_sha": "0" * 40})))
    assert out["error_code"] == "SOURCE_UNVERIFIED"
    out = result(h.execute(h.make(updates={"release_id": "0" * 64})))
    assert out["error_code"] == "LEASE_IDENTITY_MISMATCH"


def test_h225_35_authenticated_issuer_not_github_owner_claim(h):
    e = h.write()
    e["request"]["issuer"] = "trung-via"
    assert result(h.execute(e))["error_code"] == "SIGNATURE_INVALID"
    e = h.make(lease_updates={"issuer": "other-issuer"})
    assert result(h.execute(e))["error_code"] == "LEASE_IDENTITY_MISMATCH"


def test_h225_36_human_signature_independence_and_invalid_lease(h):
    e = h.write()
    e["lease_signature_b64"] = e["issuer_signature_b64"]
    assert result(h.execute(e))["status"] == "BLOCKED" and not list(h.data.iterdir())
    (h.policy / "human-signers").write_text("hostops-human " + (h.keys / "issuer.pub").read_text())
    with pytest.raises(c.Denied, match="HUMAN_KEY_NOT_INDEPENDENT"):
        auth.Authority(h.config, h.manifest)


def test_h225_37_ipc_token_wrong_missing_rotated(h):
    class Fake(broker.Channel):
        def receive(self):
            return c.canonical({"nonce": "a" * 64, "mac": broker.mac(b"x" * 32, b"server", bytes.fromhex("a" * 64))})
        def send(self, _):
            pytest.fail("wrong server token must fail before request transmission")
    with pytest.raises(c.Denied, match="IPC_AUTH_FAILED"):
        broker.client_exchange(Fake(), b"y" * 32, b"private request")
    with pytest.raises(FileNotFoundError):
        transport.submit(h.config, c.canonical(h.make()))
    (h.policy / "ipc.key").write_bytes(b"short")
    with pytest.raises(c.Denied, match="IPC_KEY_INVALID"):
        transport.submit(h.config, c.canonical(h.make()))


def test_h225_38_offline_reboot_is_unavailable(h, monkeypatch):
    (h.policy / "ipc.key").write_bytes(b"k" * 32)
    monkeypatch.setattr(transport, "connect", lambda *_: c.deny("BROKER_UNAVAILABLE"))
    with pytest.raises(c.Denied, match="BROKER_UNAVAILABLE"):
        transport.submit(h.config, c.canonical(h.write()))
    assert not list(h.data.iterdir())


def test_h225_39_busy_journal_has_no_side_effect(h):
    other = sqlite3.connect(h.state / "journal.sqlite", isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        out = result(h.execute(h.write()))
        assert out["status"] == "BUSY" and not list(h.data.iterdir())
    finally:
        other.rollback()
        other.close()


def test_h225_40_github_outage_never_reaches_broker(h):
    api = SimpleNamespace(get=lambda *_: c.deny("GITHUB_UNAVAILABLE"))
    with pytest.raises(c.Denied, match="GITHUB_UNAVAILABLE"):
        transport.poll_once(api, h.config, h.manifest, 999, 123, send=lambda *_: pytest.fail("no provenance"))


def test_h225_41_unverified_release_is_rejected(h):
    manifest = {"schema": "hostops.release.v3", "source_sha": h.manifest["source_sha"],
                "workflow_sha256": "a" * 64, "files": {"module.py": "0" * 64}}
    manifest["release_id"] = c.digest(c.canonical(manifest))
    (h.release / "release.json").write_bytes(c.canonical(manifest))
    (h.release / "module.py").write_bytes(b"unverified source")
    with pytest.raises(c.Denied, match="SOURCE_UNVERIFIED"):
        auth.verify_release(h.config)


def test_h225_42_noninteractive_fixed_digest_powershell(h, monkeypatch):
    script = h.release / "scripts" / "HostOps-V3-TokenInventory.ps1"
    script.parent.mkdir()
    script.write_bytes((ROOT / "scripts" / script.name).read_bytes())
    value = c.digest(script.read_bytes())
    h.manifest["files"]["scripts/" + script.name] = value
    # Patch only the fixed script invocation; leave real SSH verification intact.
    original, calls = subprocess.run, []
    def run(argv, **kwargs):
        if "-File" in argv:
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=0, stdout=b'{"pwsh_major":7,"non_elevated":true}', stderr=b"private ignored")
        return original(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", run)
    out = result(h.execute(h.make("powershell", {"script_id": "token_inventory_v1", "script_sha256": value})))
    assert out["status"] == "OBSERVED" and len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [h.config["pwsh"], "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)]
    assert "shell" not in kwargs and "PATH" not in kwargs["env"]


def test_h225_43_multiple_users_and_ci_identity_are_denied(h, monkeypatch):
    config_path = h.root / "config.json"
    config_path.write_bytes(c.canonical({**h.config, "broker_identity": "ci-identity"}))
    with pytest.raises(c.Denied, match="IDENTITIES_NOT_ISOLATED"):
        security.load_config(config_path)
    if os.name == "nt":
        monkeypatch.setattr(security, "windows_identity", lambda *_: ("other-user", False))
        with pytest.raises(c.Denied, match="BROKER_IDENTITY_MISMATCH"):
            security.admit(h.config, config_path)
    else:
        monkeypatch.setattr(os, "geteuid", lambda: 1001)
        with pytest.raises(c.Denied, match="BROKER_IDENTITY_MISMATCH"):
            security.admit(h.config, config_path)


def test_h225_44_admin_uac_is_never_requested(h, monkeypatch):
    if os.name == "nt":
        monkeypatch.setattr(security, "windows_identity", lambda *_: ("broker-identity", True))
    else:
        monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(c.Denied, match="ELEVATION_DENIED"):
        security.admit(h.config, h.root / "config.json")
    for name in ("broker.py", "worker.py", "transport.py"):
        source = (ROOT / "src/ai_commerce/hostops_v3" / name).read_text()
        assert "runas" not in source.lower() and "-Command" not in source.replace("# Fixed argv only. No request paths/expressions, -Command or execution-policy bypass.", "")


def test_h225_45_command_injection_is_data_or_rejected(h):
    name = "literal;$(no-shell).txt"
    out = result(h.execute(h.write(path=h.data / name)))
    assert out["status"] == "APPLIED" and (h.data / name).read_bytes() == b"new"
    e = h.make("powershell", {"script_id": "token_inventory_v1;whoami", "script_sha256": "0" * 64})
    assert result(h.execute(e))["status"] == "BLOCKED"
    e = h.make("powershell", {"script_id": "token_inventory_v1", "script_sha256": "0" * 64, "command": "whoami"})
    assert result(h.execute(e))["status"] == "BLOCKED"


def test_h225_46_private_content_never_enters_receipts_or_journal(h):
    private = b"unique-confidential-fixture-9d721"
    p = h.file(content=private)
    out = h.execute(h.make("read", {"path": str(p), "offset": 0, "length": 100, "expected_sha256": c.digest(private)}))
    assert private not in c.canonical(out) and private.decode() not in transport.report(out)
    h.journal.db.execute("PRAGMA wal_checkpoint(FULL)")
    for file in h.state.glob("journal.sqlite*"):
        assert private not in file.read_bytes()
    bad = {**out["receipt"], "result": {"contents": private.decode()}}
    with pytest.raises(c.Denied, match="PRIVATE_RECEIPT_FIELD"):
        transport.report({"receipt": bad})


def test_h225_47_revoke_expiry_kill_and_revocation_rollback(h):
    e = h.write()
    h.revoke(leases=[e["lease"]["lease_id"]], epoch=1)
    assert result(h.execute(e))["error_code"] == "REVOKED"
    h.revoke(epoch=2)
    expired = h.make(updates={"created_at": NOW - 20, "expires_at": NOW - 1})
    assert result(h.execute(expired))["error_code"] == "EXPIRED_OR_FUTURE"
    assert result(h.execute(h.make()))["status"] == "OBSERVED"
    h.revoke(epoch=1)
    assert result(h.execute(h.write()))["error_code"] == "REVOCATION_ROLLBACK"
    (h.policy / "KILL").touch()
    assert result(h.execute(h.write()))["error_code"] == "KILL_SWITCH"
    assert not list(h.data.iterdir())


def test_h225_48_hash_mismatch_before_and_after_effect(h, monkeypatch):
    e = h.write()
    e["request"]["parameters"]["data_sha256"] = "0" * 64
    assert result(h.execute(e))["error_code"] == "PAYLOAD_HASH_MISMATCH"
    assert not list(h.data.iterdir())
    p = h.file()
    out = result(h.execute(h.make("copy", {"path": str(p), "destination": str(h.data / "bad-copy.txt"),
                                          "expected_sha256": c.digest(b"wrong")})))
    assert out["error_code"] == "PRECONDITION_FAILED" and p.read_bytes() == b"original"
    target = h.data / "new.txt"
    def corrupt(point):
        if point == "after_destination":
            target.write_bytes(b"external corruption")
    h.worker.fault = corrupt
    out = result(h.execute(h.write(path=target)))
    assert out["status"] == "PARTIAL" and out["error_code"] == "POSTCONDITION_FAILED"


def test_workowner_effect_ledger_lifecycle_and_conflict_rejection(tmp_path):
    from ai_commerce.hostops_v3.journal import OfflineWorkOwnerEffectLedger
    from ai_commerce.hostops_v3.contract import Denied

    # Invalid path rejection
    with pytest.raises(Denied, match="EFFECT_LEDGER_PATH_DENIED"):
        OfflineWorkOwnerEffectLedger(tmp_path / "relative.txt")

    db_path = tmp_path / "effect_ledger.sqlite3"
    with OfflineWorkOwnerEffectLedger(db_path) as ledger:
        base_req = {
            "request_id": "1" * 64, "replay_id": "2" * 64, "nonce": "3" * 64,
            "repo": "trung-via/ai-commerce", "action_id": "effect-test",
            "root_issue": 587, "current_issue": 595, "attempt_id": "attempt-001",
            "source_sha": "a" * 40,
            "work_owner": {
                "root_commit_sha": "b" * 40, "root_digest": "c" * 64,
                "journal_tip_sha": "d" * 40, "journal_digest": "e" * 64,
                "work_key": "f" * 64, "fingerprint": "1" * 64,
                "fence": "2" * 64, "generation": 2
            },
            "provider_intent": {"commit_sha": "3" * 40, "digest": "4" * 64, "round": 0}
        }
        res1 = ledger.record_effect_intent(base_req)
        assert res1["status"] == "INTENT_RESERVED_NO_EFFECT"
        assert res1["execution_allowed"] is False
        assert ledger.unreconciled_intents_count() == 1

        # Duplicate exact request returns HOLD_REPLAY_UNKNOWN (at-most-once)
        res_dup = ledger.record_effect_intent(base_req)
        assert res_dup["status"] == "HOLD_REPLAY_UNKNOWN"

        # Conflict on same request_id with different nonce raises Denied
        conflicting = dict(base_req)
        conflicting["nonce"] = "9" * 64
        with pytest.raises(Denied, match="EFFECT_REQUEST_ID_CONFLICT"):
            ledger.record_effect_intent(conflicting)

        # Generation rollback on same (repo, attempt_id) raises Denied
        gen_rollback = dict(base_req)
        gen_rollback["request_id"] = "4" * 64
        gen_rollback["replay_id"] = "5" * 64
        gen_rollback["nonce"] = "6" * 64
        gen_rollback["work_owner"] = dict(base_req["work_owner"])
        gen_rollback["work_owner"]["generation"] = 1
        with pytest.raises(Denied, match="EFFECT_GENERATION_ROLLBACK"):
            ledger.record_effect_intent(gen_rollback)

        # Source drift on same attempt raises Denied
        src_drift = dict(base_req)
        src_drift["request_id"] = "7" * 64
        src_drift["replay_id"] = "8" * 64
        src_drift["nonce"] = "a" * 64
        src_drift["source_sha"] = "0" * 40
        with pytest.raises(Denied, match="EFFECT_SOURCE_DRIFT"):
            ledger.record_effect_intent(src_drift)


def test_owner_witness_isolation_and_safe_path(tmp_path):
    from ai_commerce.hostops_v3.security import validate_owner_witness_isolation, assert_safe_reparse_and_scratch
    from ai_commerce.hostops_v3.contract import Denied

    # Same principal denied
    with pytest.raises(Denied, match="SAME_PRINCIPAL_SIGNER_DENIED"):
        validate_owner_witness_isolation("1" * 64, "1" * 64)

    # Isolated principals allowed
    validate_owner_witness_isolation("1" * 64, "2" * 64)

    # Safe path validation
    safe_file = tmp_path / "valid.txt"
    safe_file.write_text("content")
    assert_safe_reparse_and_scratch(safe_file)

    with pytest.raises(Denied, match="UNSAFE_PATH_NOT_ABSOLUTE"):
        assert_safe_reparse_and_scratch("relative_file.txt")

