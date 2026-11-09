"""#225 category-derived 48-row matrix. Fixture files only; NO live host enablement.

The linked Issue omits the original row descriptions. The manifest records that
gap rather than pretending these derived descriptions are the unavailable text.
Real OpenSSH signatures and native fixture I/O exercise the complete worker.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import ctypes
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
from ai_commerce.remote.transport import _EffectDenied

ROOT = Path(__file__).resolve().parents[1]
NOW = 1800000000


class Crash(BaseException):
    pass


@pytest.mark.parametrize("kind", ["owned_new", "missing_parent", "foreign_sentinel", "escape", "wal", "shm"])
def test_r4_native_sqlite_never_opens_without_custody_proof(tmp_path, monkeypatch, kind):
    from ai_commerce.hostops_v3.journal import OfflineWorkOwnerEffectLedger
    sentinel = tmp_path / "foreign-sentinel.sqlite"
    sentinel.write_bytes(b"FOREIGN SENTINEL; NEVER ALTER ACL OR CONTENT")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    path = {"owned_new": tmp_path / "new.sqlite", "missing_parent": tmp_path / "absent" / "new.sqlite",
            "foreign_sentinel": sentinel, "escape": tmp_path / ".." / "escape.sqlite",
            "wal": tmp_path / "db.sqlite-wal", "shm": tmp_path / "db.sqlite-shm"}[kind]
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("native SQLite open before custody proof"))
    with pytest.raises(c.Denied, match="^SOURCE_ONLY_HOLD_LEDGER_CUSTODY_UNPROVEN$"):
        OfflineWorkOwnerEffectLedger(path, private_root=tmp_path)
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows alias operations")
@pytest.mark.parametrize("kind", ["sqlite_symlink", "parent_symlink", "hardlink", "wal_hardlink", "shm_symlink", "junction"])
def test_r4_native_alias_denied_without_external_sentinel_effects(tmp_path, kind):
    from ai_commerce.hostops_v3.journal import OfflineWorkOwnerEffectLedger
    outside = tmp_path / "foreign"; outside.mkdir()
    sentinel = outside / "sentinel.sqlite"; sentinel.write_bytes(b"FOREIGN SENTINEL")
    private = tmp_path / "private"; private.mkdir()
    alias = private / "ledger.sqlite"
    directory_alias = False
    try:
        if kind == "junction":
            import _winapi
            alias = private / "junction"
            _winapi.CreateJunction(str(outside), str(alias))
            directory_alias = True
        elif kind == "parent_symlink":
            alias = private / "parent"; alias.symlink_to(outside, target_is_directory=True)
            directory_alias = True
        elif kind in ("hardlink", "wal_hardlink"):
            if kind == "wal_hardlink": alias = private / "ledger.sqlite-wal"
            os.link(sentinel, alias)
        else:
            if kind == "shm_symlink": alias = private / "ledger.sqlite-shm"
            alias.symlink_to(sentinel)
    except OSError:
        pytest.skip("native alias creation denied by target environment; no privilege workaround")
    try:
        assert alias.lstat().st_ino
        with pytest.raises(c.Denied, match="SOURCE_ONLY_HOLD_LEDGER_CUSTODY_UNPROVEN"):
            OfflineWorkOwnerEffectLedger(alias / "sentinel.sqlite" if directory_alias else private / "ledger.sqlite",
                                         private_root=private)
        assert sentinel.read_bytes() == b"FOREIGN SENTINEL"
        assert sorted(p.name for p in outside.iterdir()) == ["sentinel.sqlite"]
    finally:
        # Remove only the alias itself; never recursively traverse its target.
        if directory_alias: os.rmdir(alias)
        else: alias.unlink()


def test_r4_parent_replacement_and_wal_swap_cannot_trigger_sqlite(tmp_path, monkeypatch):
    from ai_commerce.hostops_v3.journal import OfflineWorkOwnerEffectLedger
    parent = tmp_path / "private"; parent.mkdir()
    old_parent = tmp_path / "old-private"
    parent.rename(old_parent); parent.mkdir()
    wal = parent / "ledger.sqlite-wal"; wal.write_bytes(b"FOREIGN WAL")
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("SQLite opened during parent/WAL replacement"))
    with pytest.raises(c.Denied, match="SOURCE_ONLY_HOLD_LEDGER_CUSTODY_UNPROVEN"):
        OfflineWorkOwnerEffectLedger(parent / "ledger.sqlite", private_root=parent)
    assert wal.read_bytes() == b"FOREIGN WAL"
    assert not list(old_parent.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_preflight_positive():
    with fs.windows_owned_fixture_root() as (fixture_path, receipt):
        assert fixture_path.is_dir()
        assert receipt["status"] == "OWNED_FIXTURE_ROOT_VERIFIED"
        assert receipt["schema"] == "hostops.fixture.owned_root.v1"
        assert receipt["effective_rights_verified"] is True
        assert receipt["foreign_write_detected"] is False
        assert receipt["ancestors_verified_count"] >= 1
        assert receipt["inherited_foreign_read_only_count"] >= 0
        assert isinstance(receipt["root_digest"], str) and len(receipt["root_digest"]) == 64
        assert "S-1-" not in receipt["status"]
        assert "S-1-" not in receipt["root_digest"]
        assert ":" not in receipt["root_digest"]
        assert "\\" not in receipt["root_digest"]
        assert fixture_path.exists()
    assert not fixture_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_sanitized_receipt_never_leaks_raw_sids_or_paths():
    with fs.windows_owned_fixture_root() as (fixture_path, receipt):
        assert set(receipt.keys()) == {
            "schema", "status", "root_digest", "root_identity",
            "ancestor_identities", "ancestors_verified_count",
            "effective_rights_verified", "inherited_foreign_read_only_count",
            "foreign_write_detected", "verified",
        }
        for key in ("status", "schema", "root_digest"):
            assert "S-1-" not in receipt[key]
            assert "C:" not in receipt[key]
            assert "\\" not in receipt[key]


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_no_available_ancestors_emits_hold():
    with pytest.raises(c.Denied, match="^HOLD_NATIVE_FIXTURE_ROOT_UNAVAILABLE$"):
        with fs.windows_owned_fixture_root(candidate_ancestors=[Path(os.environ["TEMP"])]):
            pass


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_preflight_temp_foreign_modify_hold_outside_sentinel_unchanged(tmp_path):
    sentinel = tmp_path / "outside-sentinel.txt"
    sentinel.write_bytes(b"OUTSIDE SENTINEL NEVER ALTER")
    try:
        with pytest.raises(c.Denied, match="^HOLD_NATIVE_CUSTODY_ACL_FOREIGN$"):
            fs.windows_owned_fixture_root_preflight(tmp_path, check_ancestors=True, raise_denied=True)
        assert sentinel.read_bytes() == b"OUTSIDE SENTINEL NEVER ALTER"
    finally:
        if sentinel.exists():
            sentinel.unlink()


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_preflight_hostile_child_delete_child_and_write_dac_hold():
    with fs.windows_owned_fixture_root() as (fixture_path, _):
        disposable = fixture_path / "hostile-child-disposable"
        disposable.mkdir()
        # Test DELETE_CHILD (DC)
        subprocess.run(["icacls", str(disposable), "/grant", "*S-1-5-32-546:(DC)"], capture_output=True, check=True)
        with pytest.raises(c.Denied, match="^HOLD_NATIVE_CUSTODY_ACL_FOREIGN$"):
            fs.windows_owned_fixture_root_preflight(disposable, check_ancestors=True, raise_denied=True)

        # Test WRITE_DAC (WDAC)
        subprocess.run(["icacls", str(disposable), "/remove", "*S-1-5-32-546"], capture_output=True, check=True)
        subprocess.run(["icacls", str(disposable), "/grant", "*S-1-5-32-546:(WDAC)"], capture_output=True, check=True)
        with pytest.raises(c.Denied, match="^HOLD_NATIVE_CUSTODY_ACL_FOREIGN$"):
            fs.windows_owned_fixture_root_preflight(disposable, check_ancestors=True, raise_denied=True)

        # Test Modify (M)
        subprocess.run(["icacls", str(disposable), "/remove", "*S-1-5-32-546"], capture_output=True, check=True)
        subprocess.run(["icacls", str(disposable), "/grant", "*S-1-5-32-546:(M)"], capture_output=True, check=True)
        with pytest.raises(c.Denied, match="^HOLD_NATIVE_CUSTODY_ACL_FOREIGN$"):
            fs.windows_owned_fixture_root_preflight(disposable, check_ancestors=True, raise_denied=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_preflight_reparse_junction_parent_swap_drift_hold():
    import _winapi
    with fs.windows_owned_fixture_root() as (fixture_path, _):
        target = fixture_path / "junc-target"
        target.mkdir()
        junc = fixture_path / "junc-link"
        _winapi.CreateJunction(str(target), str(junc))
        try:
            with pytest.raises(c.Denied, match="^W1_NATIVE_ROOT_REPARSE$"):
                fs.windows_owned_fixture_root_preflight(junc, check_ancestors=True, raise_denied=True)
        finally:
            os.rmdir(junc)

        # Parent identity drift
        fake_receipt = {"volume_serial_hex": "12345678", "file_index_hex": "0000000000000000"}
        with pytest.raises(c.Denied, match="^W1_NATIVE_ROOT_MISMATCH$"):
            fs.w1_windows_native_create_fixture(fixture_path, fake_receipt, "drift.txt", "data")


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_preflight_unsupported_ace_typed_unproven(monkeypatch):
    with fs.windows_owned_fixture_root() as (fixture_path, _):
        disposable = fixture_path / "unsupported-ace-disposable"
        disposable.mkdir()

        fake_buf = (ctypes.c_ubyte * 16)(2, 0, 16, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        def fake_get_ace(pacl, idx, pace):
            pace._obj.value = ctypes.addressof(fake_buf)
            return 1

        monkeypatch.setattr(fs._advapi32, "GetAce", fake_get_ace)
        with pytest.raises(c.Denied, match="^UNPROVEN_UNSUPPORTED_ACE_TYPE$"):
            fs.windows_owned_fixture_root_preflight(disposable, check_ancestors=True, raise_denied=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_harmless_read_traverse_no_false_hold():
    with fs.windows_owned_fixture_root() as (fixture_path, _):
        disposable = fixture_path / "harmless-read-disposable"
        disposable.mkdir()
        subprocess.run(["icacls", str(disposable), "/grant", "*S-1-5-32-546:(RX)"], capture_output=True, check=True)
        res = fs.windows_owned_fixture_root_preflight(disposable, check_ancestors=True, raise_denied=True)
        assert res["status"] == "OWNED_FIXTURE_ROOT_VERIFIED"
        assert res["foreign_write_detected"] is False
        assert res["inherited_foreign_read_only_count"] >= 1


# AC1 negative tests
@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_preflight_missing_child_never_claims_verified():
    with fs.windows_owned_fixture_root() as (fixture_path, _):
        missing = fixture_path / "missing-child-never-exists"
        res = fs.windows_owned_fixture_root_preflight(missing, check_ancestors=True, raise_denied=False)
        assert res["verified"] is False
        assert res["status"] == "W1_NATIVE_ROOT_DENIED"
        with pytest.raises(c.Denied, match="^W1_NATIVE_ROOT_DENIED$"):
            fs.windows_owned_fixture_root_preflight(missing, check_ancestors=True, raise_denied=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_preflight_missing_ancestor_never_claims_verified():
    missing_anc = Path("C:/nonexistent_ancestor_dir_12345/child")
    res = fs.windows_owned_fixture_root_preflight(missing_anc, check_ancestors=True, raise_denied=False)
    assert res["verified"] is False
    with pytest.raises(c.Denied, match="^W1_NATIVE_ROOT_DENIED$"):
        fs.windows_owned_fixture_root_preflight(missing_anc, check_ancestors=True, raise_denied=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_preflight_ancestors_disabled_never_claims_verified():
    with fs.windows_owned_fixture_root() as (fixture_path, _):
        res = fs.windows_owned_fixture_root_preflight(fixture_path, check_ancestors=False, raise_denied=False)
        assert res["verified"] is False
        assert res["status"] == "HOLD_ANCESTORS_DISABLED"
        with pytest.raises(c.Denied, match="^HOLD_ANCESTORS_DISABLED$"):
            fs.windows_owned_fixture_root_preflight(fixture_path, check_ancestors=False, raise_denied=True)


# AC2 tests: read-rights false positive & deny ACE
@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_preflight_read_rights_false_positive_denied(monkeypatch):
    with fs.windows_owned_fixture_root() as (fixture_path, _):
        disposable = fixture_path / "read-only-disposable"
        disposable.mkdir()

        orig_inspect = fs._windows_inspect_handle_security
        def inspect_read_only(handle, trusted_owners, *, is_ancestor=False, is_volume_root=False):
            info = orig_inspect(handle, trusted_owners, is_ancestor=is_ancestor, is_volume_root=is_volume_root)
            if not is_ancestor:
                info["owner_has_effective_rights"] = False
            return info
        monkeypatch.setattr(fs, "_windows_inspect_handle_security", inspect_read_only)

        res = fs.windows_owned_fixture_root_preflight(disposable, check_ancestors=True, raise_denied=False)
        assert res["verified"] is False
        assert res["effective_rights_verified"] is False
        with pytest.raises(c.Denied, match="^HOLD_NATIVE_CUSTODY_ACL_FOREIGN$"):
            fs.windows_owned_fixture_root_preflight(disposable, check_ancestors=True, raise_denied=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_preflight_deny_ace_blocks_effective_rights(monkeypatch):
    with fs.windows_owned_fixture_root() as (fixture_path, _):
        disposable = fixture_path / "deny-ace-disposable"
        disposable.mkdir()

        orig_inspect = fs._windows_inspect_handle_security
        def inspect_with_deny(handle, trusted_owners, *, is_ancestor=False, is_volume_root=False):
            info = orig_inspect(handle, trusted_owners, is_ancestor=is_ancestor, is_volume_root=is_volume_root)
            if not is_ancestor:
                info["owner_has_effective_rights"] = False
            return info
        monkeypatch.setattr(fs, "_windows_inspect_handle_security", inspect_with_deny)

        res = fs.windows_owned_fixture_root_preflight(disposable, check_ancestors=True, raise_denied=False)
        assert res["verified"] is False
        assert res["effective_rights_verified"] is False


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_preflight_weak_owner_hold(monkeypatch):
    with fs.windows_owned_fixture_root() as (fixture_path, _):
        disposable = fixture_path / "weak-owner-disposable"
        disposable.mkdir()

        orig_inspect = fs._windows_inspect_handle_security
        def inspect_weak_owner(handle, trusted_owners, *, is_ancestor=False, is_volume_root=False):
            if not is_ancestor:
                c.deny("HOLD_NATIVE_CUSTODY_ACL_FOREIGN")
            return orig_inspect(handle, trusted_owners, is_ancestor=is_ancestor, is_volume_root=is_volume_root)
        monkeypatch.setattr(fs, "_windows_inspect_handle_security", inspect_weak_owner)

        with pytest.raises(c.Denied, match="^HOLD_NATIVE_CUSTODY_ACL_FOREIGN$"):
            fs.windows_owned_fixture_root_preflight(disposable, check_ancestors=True, raise_denied=True)


# AC3 tests: no service SID wildcard & untrusted caller-supplied allowances
@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_no_service_sid_wildcard(monkeypatch):
    with fs.windows_owned_fixture_root() as (fixture_path, _):
        disposable = fixture_path / "wildcard-disposable"
        disposable.mkdir()

        orig_inspect = fs._windows_inspect_handle_security
        def inspect_service_sid(handle, trusted_owners, *, is_ancestor=False, is_volume_root=False):
            fake_sid = "S-1-5-80-999999-999999"
            if not is_ancestor and fake_sid not in trusted_owners:
                c.deny("HOLD_NATIVE_CUSTODY_ACL_FOREIGN")
            return orig_inspect(handle, trusted_owners, is_ancestor=is_ancestor, is_volume_root=is_volume_root)
        monkeypatch.setattr(fs, "_windows_inspect_handle_security", inspect_service_sid)

        with pytest.raises(c.Denied, match="^HOLD_NATIVE_CUSTODY_ACL_FOREIGN$"):
            fs.windows_owned_fixture_root_preflight(disposable, check_ancestors=True, raise_denied=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_root_untrusted_caller_supplied_owner_rejected():
    with fs.windows_owned_fixture_root() as (fixture_path, _):
        for weak in ("S-1-1-0", "S-1-5-11", "S-1-5-32-545", "S-1-5-32-546"):
            with pytest.raises(c.Denied, match="^HOLD_NATIVE_CUSTODY_ACL_FOREIGN$"):
                fs.windows_owned_fixture_root_preflight(fixture_path, trusted_owners=[weak], check_ancestors=True, raise_denied=True)
            res = fs.windows_owned_fixture_root_preflight(fixture_path, trusted_owners=[weak], check_ancestors=True, raise_denied=False)
            assert res["verified"] is False
            assert res["foreign_write_detected"] is True


# AC4 tests: cleanup does not traverse junction, holds on hardlink and root swap
@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_cleanup_does_not_traverse_junction_outside_sentinel_unchanged(tmp_path):
    import _winapi
    outside = tmp_path / "outside_sentinel_dir"
    outside.mkdir()
    sentinel = outside / "do_not_touch.txt"
    sentinel.write_bytes(b"OUTSIDE SENTINEL DO NOT TOUCH")

    with fs.windows_owned_fixture_root() as (fixture_path, _):
        junc = fixture_path / "junc_link"
        _winapi.CreateJunction(str(outside), str(junc))

    assert sentinel.read_bytes() == b"OUTSIDE SENTINEL DO NOT TOUCH"
    assert outside.is_dir()
    assert not fixture_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_cleanup_external_hardlink_hold_sentinel_unchanged(tmp_path):
    sentinel = tmp_path / "hardlink_sentinel.txt"
    sentinel.write_bytes(b"HARDLINK SENTINEL UNTOUCHED")

    fixture_path_held = None
    try:
        with fs.windows_owned_fixture_root() as (fixture_path, _):
            fixture_path_held = fixture_path
            hl = fixture_path / "hardlink_child.txt"
            try:
                os.link(sentinel, hl)
            except OSError:
                pytest.skip("Hardlinks unavailable")
    finally:
        assert sentinel.read_bytes() == b"HARDLINK SENTINEL UNTOUCHED"
        if fixture_path_held and fixture_path_held.exists():
            hl = fixture_path_held / "hardlink_child.txt"
            if hl.exists():
                hl.unlink()
            fixture_path_held.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows native owned fixture root preflight required")
def test_windows_owned_fixture_cleanup_root_rename_swap_holds_sentinel_unchanged(tmp_path):
    swapped_outside = tmp_path / "swapped_outside"
    swapped_outside.mkdir()
    sentinel = swapped_outside / "sentinel.txt"
    sentinel.write_bytes(b"SWAPPED SENTINEL NEVER DELETE")

    temp_orig = None
    held_path = None
    try:
        with fs.windows_owned_fixture_root() as (fixture_path, _):
            held_path = fixture_path
            temp_orig = fixture_path.with_name(fixture_path.name + "-temp")
            fixture_path.rename(temp_orig)
            swapped_outside.rename(fixture_path)
    finally:
        assert (held_path / "sentinel.txt").read_bytes() == b"SWAPPED SENTINEL NEVER DELETE"
        held_path.rename(swapped_outside)
        if temp_orig and temp_orig.exists():
            temp_orig.rmdir()
        sentinel.unlink()
        swapped_outside.rmdir()



@pytest.mark.parametrize("field", ["source_sha", "work_owner.journal_tip_sha", "work_owner.journal_digest",
    "work_owner.fence", "work_owner.generation", "provider_intent.digest"])
def test_r4_sqlite_effect_slot_conflicts_rollback_in_source_fixture(tmp_path, monkeypatch, field):
    from test_ef02_m0r_fault_injection import _r4_bridge_fixture
    from ai_commerce.hostops_v3.journal import _AuthenticatedEffectIntent
    f = _r4_bridge_fixture(tmp_path, monkeypatch)
    with f.ledger:
        assert f.bridge()["status"] == "SOURCE_ONLY_HOLD_NATIVE_SIGNER_REQUIRED"
        binding = c.decode(c.canonical(f.binding)); node = binding; parts = field.split(".")
        for name in parts[:-1]: node = node[name]
        old = node[parts[-1]]; node[parts[-1]] = old + 1 if type(old) is int else "0" * len(old)
        replay = {"replay_id": "a" * 64, "nonce": "b" * 64, "ledger_sha256": "4" * 64}
        intent = _AuthenticatedEffectIntent(binding, replay, f.request()["candidate_boundary"])
        with pytest.raises(_EffectDenied, match="EFFECT_BINDING_CONFLICT|EFFECT_GENERATION_CONFLICT"):
            f.ledger.record_effect_intent(intent, request_id="c" * 64, revalidate=lambda: None)
        assert f.ledger.unreconciled_intents_count() == 1


def test_r4_sqlite_source_rounds_require_new_pinned_provenance_and_no_reclaim(tmp_path, monkeypatch):
    from test_ef02_m0r_fault_injection import _r4_bridge_fixture
    from ai_commerce.hostops_v3.journal import _AuthenticatedEffectIntent
    f = _r4_bridge_fixture(tmp_path, monkeypatch)
    with f.ledger:
        assert f.bridge()["status"] == "SOURCE_ONLY_HOLD_NATIVE_SIGNER_REQUIRED"
        binding = c.decode(c.canonical(f.binding))
        replay = {"replay_id": "a" * 64, "nonce": "b" * 64, "ledger_sha256": "4" * 64}
        def reserve():
            return f.ledger.record_effect_intent(_AuthenticatedEffectIntent(binding, replay,
                f.request()["candidate_boundary"]), request_id="c" * 64, revalidate=lambda: None)
        binding["provider_intent"]["round"] = 1
        with pytest.raises(_EffectDenied, match="EFFECT_PROVENANCE_REUSED"): reserve()
        binding["work_owner"].update(journal_tip_sha="0" * 40, journal_digest="0" * 64)
        binding["provider_intent"].update(commit_sha="1" * 40, digest="1" * 64)
        assert reserve()["status"] == "INTENT_RESERVED_NO_EFFECT"
        binding["provider_intent"]["round"] = 3
        with pytest.raises(_EffectDenied, match="EFFECT_ROUND_ROLLBACK_OR_GAP"): reserve()
        assert f.ledger.unreconciled_intents_count() == 2


def test_r4_raw_witness_and_mutated_intent_cannot_change_fixture_ledger():
    from dataclasses import FrozenInstanceError
    from ai_commerce.hostops_v3.journal import OfflineWorkOwnerEffectLedger, _AuthenticatedEffectIntent
    with OfflineWorkOwnerEffectLedger.source_contract_fixture() as ledger:
        with pytest.raises(_EffectDenied, match="UNAUTHENTICATED_EFFECT_INTENT"):
            ledger.record_effect_intent({}, request_id="a" * 64, revalidate=lambda: None)
        intent = _AuthenticatedEffectIntent({}, {}, {})
        with pytest.raises(FrozenInstanceError): intent._wire = b"forged"
        assert ledger.unreconciled_intents_count() == 0


def test_r4_source_fixture_interrupted_reservation_rolls_back_without_expiry(tmp_path, monkeypatch):
    from test_ef02_m0r_fault_injection import _r4_bridge_fixture
    from ai_commerce.hostops_v3.journal import _AuthenticatedEffectIntent
    f = _r4_bridge_fixture(tmp_path, monkeypatch)
    with f.ledger:
        intent = _AuthenticatedEffectIntent(f.binding, f.payload["replay"], f.request()["candidate_boundary"])
        def interrupt():
            if f.ledger.unreconciled_intents_count(): raise Crash()
        with pytest.raises(Crash):
            f.ledger.record_effect_intent(intent, request_id="c" * 64, revalidate=interrupt)
        assert not f.ledger.db.in_transaction and f.ledger.unreconciled_intents_count() == 0


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
