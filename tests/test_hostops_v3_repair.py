"""Repair regressions use test-owned data; no installation or host activation."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import threading

import pytest

from ai_commerce.hostops_v3 import contract as c, filesystem as fs
from ai_commerce.hostops_v3.journal import Journal
from ai_commerce.hostops_v3.worker import Worker
from test_hostops_v3 import ROOT, h, keys, prototype_adapter, result


@pytest.mark.parametrize("operation", sorted(c.BLOCKED_CONDITIONAL_WRITES))
def test_conditional_mutation_is_blocked_before_adapter_even_with_signed_lease(h, monkeypatch, operation):
    source = h.file()
    if operation in ("replace", "append", "patch"):
        extra = {"offset": 0, "remove_bytes": 1} if operation == "patch" else {}
        e = h.write(operation, path=source, **extra)
    else:
        params = {"path": str(source), "expected_sha256": c.digest(b"original")}
        if operation == "quarantine":
            params["preview_sha256"] = "0" * 64
            preview = result(h.execute(h.make(operation, params, updates={"preview": True})))
            params["preview_sha256"] = preview["result"]["preview_sha256"]
        else:
            params["destination"] = str(h.data / "destination.txt")
        e = h.make(operation, params, exact=operation == "quarantine")
    monkeypatch.setattr(fs.Filesystem, "mutate", lambda *_: pytest.fail("blocked effect reached adapter"))
    first = h.execute(e)
    assert result(first)["status"] == "BLOCKED"
    assert result(first)["error_code"] == "CONDITIONAL_WRITE_ATOMICITY_UNPROVEN"
    assert h.execute(e) == first
    assert source.read_bytes() == b"original"
    assert [p.name for p in h.data.iterdir()] == [source.name]


def test_prototype_final_hash_to_replace_gap_is_not_atomic_cas(h, monkeypatch):
    """Deterministically reproduce the lost-update interval; broker must block it."""
    source = h.file()
    e = h.write("replace", path=source, data=b"prototype replacement")
    blocked = result(h.execute(e))
    assert blocked["error_code"] == "CONDITIONAL_WRITE_ATOMICITY_UNPROVEN"
    adapter = prototype_adapter(h)
    original, calls = adapter.assert_hash, []

    def external_writer_after_final_check(raw, expected):
        value = original(raw, expected)
        calls.append(raw)
        if len(calls) == 2:
            source.write_bytes(b"external writer after final hash")
        return value

    monkeypatch.setattr(adapter, "assert_hash", external_writer_after_final_check)
    adapter.mutate("replace", e["request"]["parameters"])
    assert len(calls) == 2
    assert source.read_bytes() == b"prototype replacement"
    # A passing prototype postcondition has overwritten another writer. This is
    # evidence for BLOCKED acceptance, never proof of safe conditional writes.


def test_concurrent_conditional_requests_leave_source_unchanged(h):
    source = h.file()
    envelopes = [h.write("append", path=source, data=suffix) for suffix in (b"A", b"B")]
    ready = threading.Barrier(2)

    def execute(e):
        journal = Journal(h.state / "journal.sqlite")
        try:
            ready.wait(timeout=5)
            return result(Worker(h.config, h.authority, journal).execute(c.canonical(e)))
        finally:
            journal.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(execute, envelopes))
    assert all(out["status"] in ("BLOCKED", "BUSY") for out in outcomes)
    assert any(out["error_code"] == "CONDITIONAL_WRITE_ATOMICITY_UNPROVEN" for out in outcomes)
    assert source.read_bytes() == b"original"


def test_broad_verification_helper_refuses_restricted_child(h, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("hostops_v3_verify_repair", ROOT / "scripts/hostops_v3_verify.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    monkeypatch.setenv("CODEX_SANDBOX_NETWORK_DISABLED", "1")
    monkeypatch.setattr(helper.subprocess, "run", lambda *_args, **_kw: pytest.fail("suite started in sandbox"))
    assert helper.main() == 2
    assert capsys.readouterr().err.strip() == "PARENT_EF01_VERIFICATION_REQUIRED"


def test_stable_manifest_does_not_reuse_prior_run_results():
    manifest = json.loads((ROOT / "docs/engineering/hostops_v3_evidence/scenarios.json").read_bytes())
    assert manifest["source_sha"] == "e267e0f2438fdb2ed8d0ec13b9357d547c2100e6"
    assert manifest["original_plan_parity"] == "BLOCKED_ORIGINAL_ROW_TEXT_UNAVAILABLE"
    assert manifest["exact_original_scenarios_validated"] == 0
    assert all("windows_result" not in row and "result_log" not in row for row in manifest["scenarios"])
    blocked = {row["id"] for row in manifest["scenarios"] if row["acceptance"] == "BLOCKED_ATOMICITY"}
    assert {"H225-10", "H225-11", "H225-12", "H225-18", "H225-20", "H225-21", "H225-22", "H225-23"} <= blocked
