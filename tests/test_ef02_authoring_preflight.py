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
    assert result["status"] == "PREFLIGHT_HOLD" and result["content_check"] == "PASS"
    assert result["authorization"] == "UNPROVEN"
    assert result["acceptance_scope"]["status"] == "PRE_EXECUTOR_UNDETERMINED"
    assert result["acceptance_scope"]["actions"][0]["reason"] == "SOURCE_REPO_NOT_VERIFIED"
    assert result["source_check"] == "NOT_REQUESTED"
    assert result["dispatch_performed"] is False
    with pytest.raises(RemoteRequestError, match="ISSUER_NOT_AUTHORIZED"):
        validate_batch(batch([action("a")]), schema=SCHEMA, registry=verify_registry(REGISTRY),
            issuer_login="attacker", owner_login="owner")


def test_default_offline_check_does_not_start_any_child(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline content check must not start a child process")
    monkeypatch.setattr(preflight.subprocess, "run", forbidden)
    assert check(batch([action("a")]))["status"] == "PREFLIGHT_HOLD"


@pytest.mark.parametrize("length", [256, 257, 512, 513])
def test_authorization_ref_preflight_and_local_cli_share_safe_character_boundary(
        tmp_path, monkeypatch, capsys, length):
    packet = batch([action("a")])
    marker = "PRIVATE_AUTHORIZATION_"
    packet["authorization_ref"] = marker + "ế" * (length - len(marker))
    packet["actions"][0]["instruction"] = "PRIVATE_INSTRUCTION"
    original = copy.deepcopy(packet)
    def forbidden(*args, **kwargs):
        pytest.fail("Offline authoring must not spawn a child")
    monkeypatch.setattr(preflight.subprocess, "run", forbidden)
    # Isolate the field contract from the independent optional source/scope check.
    monkeypatch.setattr(preflight, "validate_acceptance_scope",
                        lambda *args, **kwargs: {"status": "PRE_EXECUTOR_COMPATIBLE"})
    result = check(packet)
    request = tmp_path / "request.json"
    request.write_text(json.dumps(packet), encoding="utf-8")
    status = preflight.main(["--request-file", str(request), "--checkout", str(ROOT)])
    output = capsys.readouterr()
    assert output.err == "" and json.loads(output.out) == result
    assert packet == original and check(packet) == result
    assert "PRIVATE" not in output.out and packet["authorization_ref"] not in output.out
    assert result["authorization"] == "UNPROVEN" and result["dispatch_performed"] is False
    if length <= 512:
        assert status == 0 and result["status"] == "PREFLIGHT_PASS"
        assert result["content_check"] == "PASS" and result["source_check"] == "NOT_REQUESTED"
    else:
        assert status == 2 and result["status"] == "PREFLIGHT_REJECTED"
        assert result["error_code"] == "AUTHORIZATION_REF_TOO_LONG"
        assert result["failure_provenance"]["error_code"] == "AUTHORIZATION_REF_TOO_LONG"
        assert result["failure_provenance"]["phase"] == "REQUEST_VALIDATION"
        assert result["failure_provenance"]["replay_authorized"] is False


@pytest.mark.parametrize("present,value", [
    (False, None), (True, ""), (True, None), (True, 512),
    (True, False), (True, []), (True, {}),
])
def test_authorization_ref_preflight_preserves_other_schema_rejections(
        tmp_path, monkeypatch, capsys, present, value):
    packet = batch([action("a")])
    packet["actions"][0]["instruction"] = "PRIVATE_INSTRUCTION"
    if present:
        packet["authorization_ref"] = value
    else:
        del packet["authorization_ref"]
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid schema must reject before any source or scope check")
    monkeypatch.setattr(preflight.subprocess, "run", forbidden)
    monkeypatch.setattr(preflight, "validate_acceptance_scope", forbidden)
    result = check(packet)
    assert result["status"] == "PREFLIGHT_REJECTED" and result["error_code"] == "REQUEST_SCHEMA_INVALID"
    assert result["authorization"] == "UNPROVEN" and result["dispatch_performed"] is False
    request = tmp_path / "request.json"
    request.write_text(json.dumps(packet), encoding="utf-8")
    assert preflight.main(["--request-file", str(request), "--checkout", str(ROOT)]) == 2
    output = capsys.readouterr()
    assert output.err == "" and json.loads(output.out) == result
    assert "PRIVATE" not in output.out


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
    assert check(packet, expected_files={"a": ["src/ai_commerce/feature_a/core.py"]})["content_check"] == "PASS"
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
    assert result["status"] == "PREFLIGHT_HOLD" and result["content_check"] == "PASS"
    assert result["authorization"] == "UNPROVEN" and result["reuse_check"] == "LOCAL_REGISTRY_MATCH_ONLY"
    entry["scope_valid"] = False
    assert check(packet, approved_reuse=registry)["error_code"] == "REUSE_UNAPPROVED"


def test_optional_source_check_is_exact_read_only_head(tmp_path, monkeypatch):
    seen, scopes = [], []
    def run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
    monkeypatch.setattr(preflight.subprocess, "run", run)
    def scope(packet, *, repo):
        scopes.append(repo)
        return {"status": "PRE_EXECUTOR_UNDETERMINED"}
    monkeypatch.setattr(preflight, "validate_acceptance_scope", scope)
    assert check(batch([action("a")]), source_repo=tmp_path)["source_check"] == "EXACT_HEAD_MATCH"
    packet = batch([action("a")])
    packet["source_sha"] = "b" * 40
    assert check(packet, source_repo=tmp_path)["error_code"] == "SOURCE_SHA_MISMATCH"
    assert all(argv[-2:] == ["rev-parse", "HEAD"] for argv in seen)
    assert scopes == [tmp_path]


def test_documented_cli_from_unrelated_cwd_and_sanitized_invalid_json(tmp_path):
    packet = tmp_path / "packet.json"
    packet.write_text(json.dumps(batch([action("a")])), encoding="utf-8")
    command = [sys.executable, str(ROOT / "scripts/EF02-Authoring-Preflight.py"),
        "--request-file", str(packet), "--checkout", str(ROOT)]
    valid = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert valid.returncode == 2, valid.stderr
    result = json.loads(valid.stdout)
    assert result["authorization"] == "UNPROVEN" and result["content_check"] == "PASS"
    assert result["status"] == "PREFLIGHT_HOLD"
    packet.write_text('{"SECRET": invalid}', encoding="utf-8")
    invalid = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert invalid.returncode == 2
    assert json.loads(invalid.stdout)["error_code"] == "LOCAL_INPUT_INVALID"
    assert "SECRET" not in invalid.stdout + invalid.stderr


@pytest.mark.parametrize("problem", ["missing", "malformed", "duplicate-ids", "historical-duplicate", "unknown", "owner"])
def test_verified_preflight_uses_real_scope_parser_and_sanitized_provenance(tmp_path, monkeypatch, problem):
    from test_ef02 import make_acceptance_repo, malformed_scope_action
    make_acceptance_repo(tmp_path, monkeypatch)
    packet = batch([malformed_scope_action(problem)])
    original = copy.deepcopy(packet)
    result = check(packet, source_repo=tmp_path)
    code = "AC_SCOPE_MISMATCH_PRE_EXECUTOR" if problem == "owner" else "AC_DECLARATION_UNPROVEN_PRE_EXECUTOR"
    assert result["status"] == "PREFLIGHT_REJECTED" and result["error_code"] == code
    assert result["authorization"] == "UNPROVEN" and result["dispatch_performed"] is False
    assert result["failure_provenance"]["source_sha"] == packet["source_sha"]
    assert result["failure_provenance"]["attempt_id"] == "b-001"
    assert result["failure_provenance"]["replay_authorized"] is False
    assert "PRIVATE_INSTRUCTIONS" not in json.dumps(result)
    assert result == check(packet, source_repo=tmp_path) and packet == original


def test_verified_preflight_compatibility_cannot_grant_owner_authority(tmp_path, monkeypatch):
    from test_ef02 import make_acceptance_repo, scoped_action, VALIDATION_SOURCE
    make_acceptance_repo(tmp_path, monkeypatch)
    packet = batch([scoped_action("a", [VALIDATION_SOURCE], ["EF02.action-and-fanin"])])
    result = check(packet, source_repo=tmp_path)
    assert result["status"] == "PREFLIGHT_PASS"
    assert result["acceptance_scope"]["status"] == "PRE_EXECUTOR_COMPATIBLE"
    assert result["source_check"] == "EXACT_HEAD_MATCH"
    assert result["authorization"] == "UNPROVEN" and result["dispatch_performed"] is False
    with pytest.raises(RemoteRequestError, match="ISSUER_NOT_AUTHORIZED_FOR_ACTOR"):
        validate_batch(packet, schema=SCHEMA, registry=verify_registry(REGISTRY),
                       issuer_login="attacker", owner_login="owner")


def test_content_only_preflight_never_infers_registered_owner_from_ac_or_expected_files(monkeypatch):
    from test_ef02 import scoped_action, VALIDATION_SOURCE
    def forbidden(*args, **kwargs):
        pytest.fail("Unverified authoring must remain offline")
    monkeypatch.setattr(preflight.subprocess, "run", forbidden)
    packet = batch([scoped_action("a", [VALIDATION_SOURCE], ["EF02.action-and-fanin"])])
    result = check(packet, expected_files={"a": [VALIDATION_SOURCE]})
    assert result["status"] == "PREFLIGHT_HOLD"
    assert result["authorization"] == "UNPROVEN" and result["content_check"] == "PASS"
    assert result["acceptance_scope"]["next_gate"] == "HOLD_NO_EXECUTOR"


@pytest.mark.parametrize("timeout", [False, True])
def test_unavailable_local_source_is_sanitized_hold(tmp_path, monkeypatch, timeout):
    def unavailable(argv, **kwargs):
        if timeout:
            raise subprocess.TimeoutExpired(argv, 30, stderr="PRIVATE_INSTRUCTIONS")
        return subprocess.CompletedProcess(argv, 1, "", "PRIVATE_INSTRUCTIONS")
    monkeypatch.setattr(preflight.subprocess, "run", unavailable)
    result = check(batch([action("a")]), source_repo=tmp_path)
    assert result["status"] == "PREFLIGHT_HOLD" and result["error_code"] == "SOURCE_HEAD_UNAVAILABLE"
    assert result["failure_provenance"]["status"] == "HOLD"
    assert result["authorization"] == "UNPROVEN" and result["dispatch_performed"] is False
    assert result["acceptance_scope"]["status"] == "PRE_EXECUTOR_UNDETERMINED"
    assert "PRIVATE_INSTRUCTIONS" not in json.dumps(result)


def test_witnessed_preflight_preserves_contract_status_and_authorization_boundary(tmp_path, monkeypatch):
    from test_ef02 import make_acceptance_repo, wakehub_scope_action
    make_acceptance_repo(tmp_path, monkeypatch)
    packet = batch([wakehub_scope_action(witnessed=True)])
    result = check(packet, source_repo=tmp_path)
    assert result["status"] == "PREFLIGHT_PASS"
    assert result["acceptance_scope"]["coverage_status"] == "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN"
    assert result["acceptance_scope"]["actions"][0]["status"] == "PRE_EXECUTOR_CONTRACTED_NOT_PROVEN"
    assert result["authorization"] == "UNPROVEN" and result["dispatch_performed"] is False
    with pytest.raises(RemoteRequestError, match="^ISSUER_NOT_AUTHORIZED_FOR_ACTOR$"):
        validate_batch(packet, schema=SCHEMA, registry=verify_registry(REGISTRY),
                       issuer_login="untrusted-executor", owner_login="owner")


def test_witnessed_packet_cannot_rebind_approved_source_pin_in_same_git_repo(tmp_path, monkeypatch):
    from test_ef02 import make_acceptance_repo, wakehub_scope_action
    _, calls = make_acceptance_repo(tmp_path, monkeypatch)
    packet = batch([wakehub_scope_action(witnessed=True)])
    assert check(packet, source_repo=tmp_path)["status"] == "PREFLIGHT_PASS"
    calls.clear()
    packet["source_sha"] = "b" * 40
    result = check(packet, source_repo=tmp_path)
    assert result["error_code"] == "SOURCE_SHA_MISMATCH"
    assert result["status"] == "PREFLIGHT_REJECTED"
    assert result["authorization"] == "UNPROVEN" and result["dispatch_performed"] is False
    assert calls == [["rev-parse", "HEAD"]]
    assert "PRIVATE_INSTRUCTIONS" not in json.dumps(result)


def test_real_issuer_gate_precedes_witness_scope_or_source_reads(tmp_path, monkeypatch, capsys):
    from test_ef02 import make_acceptance_repo, wakehub_scope_action
    from ai_commerce.remote import __main__ as cli
    _, calls = make_acceptance_repo(tmp_path, monkeypatch)
    request = tmp_path / "request.json"
    request.write_text(json.dumps(batch([wakehub_scope_action(witnessed=True)])), encoding="utf-8")
    assert cli.main(["validate", "--request-file", str(request), "--checkout", str(tmp_path),
                     "--issuer-login", "untrusted-executor", "--owner-login", "owner"]) == 2
    assert capsys.readouterr().err.strip() == "EF02_REQUEST_REJECTED: ISSUER_NOT_AUTHORIZED_FOR_ACTOR"
    assert calls == []


@pytest.mark.parametrize("witnessed,code", [
    (False, "AC_IMPACT_WITNESS_REQUIRED_PRE_EXECUTOR"),
    (True, "AC_IMPACT_WITNESS_MALFORMED_PRE_EXECUTOR"),
])
def test_optional_scope_preflight_reports_only_public_reason(tmp_path, monkeypatch, witnessed, code):
    from test_ef02 import make_acceptance_repo, wakehub_scope_action, WITNESS_MARKER
    make_acceptance_repo(tmp_path, monkeypatch)
    item = wakehub_scope_action()
    if witnessed:
        item["instruction"] += "\n" + WITNESS_MARKER + '{"PRIVATE_SECRET":invalid}'
    result = check(batch([item]), source_repo=tmp_path)
    assert result["status"] == "PREFLIGHT_REJECTED" and result["error_code"] == code
    assert result["authorization"] == "UNPROVEN" and result["dispatch_performed"] is False
    assert "PRIVATE" not in json.dumps(result)
