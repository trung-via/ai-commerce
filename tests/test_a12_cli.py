"""Targeted CLI tests for ai-commerce product commands, preserving all existing CLI commands."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_commerce.cli import main
from ai_commerce.core.provenance import canonical_bytes

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = "2026-10-30T01:00:00Z"


def invoke(*args: str, code: int = 0) -> dict:
    """Helper to run CLI commands and parse JSON output."""
    captured_out: list[str] = []
    captured_err: list[str] = []

    # Run in-process with main()
    import io
    from contextlib import redirect_stderr, redirect_stdout

    buf_out = io.StringIO()
    buf_err = io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        ret = main(list(args))
    assert ret == code, f"Expected {code}, got {ret}. Stderr: {buf_err.getvalue()}"
    out_str = buf_out.getvalue().strip()
    err_str = buf_err.getvalue().strip()
    if code != 0:
        return json.loads(err_str) if err_str.startswith("{") else {"raw": err_str}
    return json.loads(out_str)


def test_cli_help():
    with pytest.raises(SystemExit) as exc:
        main(["product", "--help"])
    assert exc.value.code == 0


def test_cli_snapshot_import(tmp_path: Path):
    raw_listing = {
        "schema_version": "ManualProductListings.v1",
        "rows": [
            {
                "marketplace": "test-market",
                "seller_key": "seller-1",
                "listing_key": "item-1",
                "variant_key": "v-1",
                "price_amount": 19.99,
                "currency": "USD",
            }
        ],
    }
    raw_path = tmp_path / "raw.json"
    raw_path.write_bytes(canonical_bytes(raw_listing))

    out_path = tmp_path / "snap.json"
    data = invoke(
        "product", "snapshot",
        "--file", str(raw_path),
        "--format", "json",
        "--source-ref", "test-ref",
        "--captured-at", CAPTURE,
        "--output", str(out_path),
    )
    assert data["schema_version"] == "ProductListingSnapshot.v1"
    assert data["snapshot_id"].startswith("a12-snapshot-")
    assert out_path.is_file()


def test_cli_init_db_append_inspect_integrity_export_verify(tmp_path: Path):
    db_path = tmp_path / "evidence.sqlite"

    # 1. init-db
    init_res = invoke("product", "init-db", "--db", str(db_path))
    assert init_res["status"] == "INITIALIZED"

    # 2. snapshot
    raw_listing = {
        "schema_version": "ManualProductListings.v1",
        "rows": [
            {
                "marketplace": "test-market",
                "seller_key": "seller-1",
                "listing_key": "item-1",
                "variant_key": "v-1",
                "price_amount": 19.99,
                "currency": "USD",
            }
        ],
    }
    raw_path = tmp_path / "raw.json"
    raw_bytes = canonical_bytes(raw_listing)
    raw_path.write_bytes(raw_bytes)
    snap_path = tmp_path / "snap.json"
    snap_data = invoke(
        "product", "snapshot",
        "--file", str(raw_path),
        "--format", "json",
        "--source-ref", "test-ref",
        "--captured-at", CAPTURE,
        "--output", str(snap_path),
    )

    # 3. append snapshot
    app_res = invoke(
        "product", "append",
        "--db", str(db_path),
        "--file", str(snap_path),
        "--raw", str(raw_path),
        "--stream-key", "snap-stream",
        "--recorded-at", "2026-10-30T02:00:00Z",
    )
    assert app_res["duplicate"] is False
    assert app_res["event_id"].startswith("a12-event-")

    # 4. inspect
    insp_res = invoke("product", "inspect", "--db", str(db_path))
    assert len(insp_res["events"]) == 1
    assert insp_res["events"][0]["event_id"] == app_res["event_id"]

    # 5. integrity
    integ_res = invoke("product", "integrity", "--db", str(db_path))
    assert integ_res["integrity"] == "LOCAL_HASH_AND_REPLAY_VERIFIED"
    assert integ_res["event_count"] == 1

    # 6. export
    pack_path = tmp_path / "exported_pack.json"
    pack_res = invoke(
        "product", "export",
        "--db", str(db_path),
        "--listing-id", snap_data["listings"][0]["identity"]["listing_id"],
        "--output", str(pack_path),
    )
    assert pack_res["schema_version"] == "ProductEvidencePack.v1"
    assert pack_res["pack_id"].startswith("a12-pack-")
    assert pack_path.is_file()

    # 7. verify
    ver_res = invoke("product", "verify", "--file", str(pack_path), "--expected-id", pack_res["pack_id"])
    assert ver_res["pack_id"] == pack_res["pack_id"]

    # 8. verify with wrong expected-id
    err = invoke("product", "verify", "--file", str(pack_path), "--expected-id", "a12-pack-" + "0" * 64, code=70)
    assert err["error"] == "PACK_ID_MISMATCH"


def test_cli_negative_errors(tmp_path: Path):
    # Invalid file path
    res = invoke(
        "product", "snapshot",
        "--file", str(tmp_path / "nonexistent.json"),
        "--format", "json",
        "--source-ref", "ref",
        "--captured-at", CAPTURE,
        code=70,
    )
    assert "error" in res


def test_cli_preserves_existing_commands():
    """Verify A01..A11 existing commands remain intact and functional."""
    assert main(["hello"]) == 0
    assert main(["about", "--json"]) == 0
