"""Optional, offline EF02 packet authoring checks against the real contract."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_ef02 import action, batch, ROOT, SCHEMA, REGISTRY
from ai_commerce.remote import authoring_preflight as preflight
from ai_commerce.remote.validation import RemoteRequestError, validate_batch, verify_registry


def check(packet, **kwargs):
    return preflight.preflight_batch(packet, schema=SCHEMA,
        registry=verify_registry(REGISTRY), **kwargs)


def test_clean_packet_is_structurally_valid_but_actor_authority_unproven():
    result = check(batch([action("a")]))
    assert result["status"] == "PREFLIGHT_PASS"
    assert result["authorization"] == "UNPROVEN"
    assert result["source_check"] == "NOT_REQUESTED"
    assert result["dispatch_performed"] is False
    with pytest.raises(RemoteRequestError, match="ISSUER_NOT_AUTHORIZED"):
        validate_batch(batch([action("a")]), schema=SCHEMA, registry=verify_registry(REGISTRY),
            issuer_login="attacker", owner_login="owner")


def test_default_offline_check_does_not_start_any_child(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline content check must not start a child process")
    monkeypatch.setattr(preflight.subprocess, "run", forbidden)
    assert check(batch([action("a")]))["status"] == "PREFLIGHT_PASS"


def test_143_duplicate_paths_is_schema_rejection_without_echoing_packet():
    packet = batch([action("a")])
    packet["actions"][0]["allowed_paths"] = ["tests/test_a13_link.py"] * 2
    packet["actions"][0]["instruction"] = "SECRET"
    result = check(packet)
    assert result["status"] == "PREFLIGHT_REJECTED"
    assert result["error_code"] == "DUPLICATE_ALLOWED_PATHS"
    assert result["failure_provenance"]["phase"] == "REQUEST_VALIDATION"
    assert result["failure_provenance"]["source_sha"] == packet["source_sha"]
    assert result["failure_provenance"]["attempt_id"] == "a-001"
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("path", ["../secret", ".github/workflows/run.yml", "src/x/../../bad",
    "src\\feature.py", "C:/private", "src/*.py", "src/x/.env", "src/x\nsecret"])
def test_unsafe_paths_rejected(path):
    packet = batch([action("a")])
    packet["actions"][0]["allowed_paths"] = [path]
    assert check(packet)["status"] == "PREFLIGHT_REJECTED"


def test_explicit_expected_files_use_segment_scope_and_remain_outside_packet():
    packet = batch([action("a")])
    original = copy.deepcopy(packet)
    assert check(packet, expected_files={"a": ["src/ai_commerce/feature_a/core.py"]})["status"] == "PREFLIGHT_PASS"
    assert check(packet, expected_files={"a": ["src/ai_commerce/feature_a.py"]})["error_code"] == "EXPECTED_PATH_OUTSIDE_SCOPE"
    assert check(packet, expected_files={"missing": ["src/x.py"]})["error_code"] == "EXPECTED_ACTION_UNKNOWN"
    assert check(packet, expected_files={"a": [".local/log"]})["status"] == "PREFLIGHT_REJECTED"
    assert packet == original


@pytest.mark.parametrize("problem,code", [("profile", "VERIFICATION_PROFILE_UNREGISTERED"),
    ("sha", "INVALID_SOURCE_SHA"), ("cycle", "DEPENDENCY_CYCLE"),
    ("dependency", "DEPENDENCY_UNKNOWN_OR_SELF")])
def test_existing_semantic_contracts(problem, code):
    packet = batch([action("a"), action("b")])
    if problem == "profile":
        packet["actions"][0]["verification_profile"] = "unknown"
    elif problem == "sha":
        packet["source_sha"] = "main"
    elif problem == "cycle":
        packet["actions"][0]["depends_on"] = ["b"]
        packet["actions"][1]["depends_on"] = ["a"]
    else:
        packet["actions"][0]["depends_on"] = ["absent"]
    assert check(packet)["error_code"] == code


def test_142_reuse_requires_actual_matching_registry_entry():
    packet = batch([action("a")])
    a = packet["actions"][0]
    a["reuse_candidate"] = {"candidate_sha": "b" * 40, "verified_run_id": 123}
    assert check(packet)["error_code"] == "REUSE_UNAPPROVED"
    assert check(packet, approved_reuse={"version": 1, "candidates": {}})["error_code"] == "REUSE_UNAPPROVED"
    entry = {**a, "source_sha": packet["source_sha"], "candidate_sha": "b" * 40,
        "verified_run_id": 123, "parent_sha": "a" * 40, "tree_sha": "c" * 40,
        "verification_status": "BUILD_VERIFIED_FOR_SCOPE", "verify_exit_code": 0, "scope_valid": True}
    registry = {"version": 1, "approved_source_sha": packet["source_sha"], "candidates": {a["attempt_id"]: entry}}
    result = check(packet, approved_reuse=registry)
    assert result["status"] == "PREFLIGHT_PASS" and result["authorization"] == "UNPROVEN"
    entry["scope_valid"] = False
    assert check(packet, approved_reuse=registry)["error_code"] == "REUSE_UNAPPROVED"


def test_optional_source_check_is_exact_read_only_head(tmp_path, monkeypatch):
    seen = []
    def run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
    monkeypatch.setattr(preflight.subprocess, "run", run)
    assert check(batch([action("a")]), source_repo=tmp_path)["source_check"] == "EXACT_HEAD_MATCH"
    packet = batch([action("a")])
    packet["source_sha"] = "b" * 40
    assert check(packet, source_repo=tmp_path)["error_code"] == "SOURCE_SHA_MISMATCH"
    assert all(argv[-2:] == ["rev-parse", "HEAD"] for argv in seen)


def test_documented_cli_from_unrelated_cwd_and_sanitized_invalid_json(tmp_path):
    packet = tmp_path / "packet.json"
    packet.write_text(json.dumps(batch([action("a")])), encoding="utf-8")
    command = [sys.executable, str(ROOT / "scripts/EF02-Authoring-Preflight.py"),
        "--request-file", str(packet), "--checkout", str(ROOT)]
    valid = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert valid.returncode == 0, valid.stderr
    assert json.loads(valid.stdout)["authorization"] == "UNPROVEN"
    packet.write_text('{"SECRET": invalid}', encoding="utf-8")
    invalid = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert invalid.returncode == 2
    assert json.loads(invalid.stdout)["error_code"] == "LOCAL_INPUT_INVALID"
    assert "SECRET" not in invalid.stdout + invalid.stderr
