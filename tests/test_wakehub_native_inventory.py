"""Offline adversarial fixtures and mocked Windows reads, never host inventory.

The authority fixture represents separately pinned private evidence, not an
installed signer/enrollment or a claim about this runner's desktop/Chrome.
"""
from __future__ import annotations

import builtins
from dataclasses import FrozenInstanceError, asdict, replace
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

from ai_commerce.engineering.three_runner_topology import planned_topology
from ai_commerce.wakehub import native_inventory as inv


T = 1800000000.0
SID = "S-1-5-21-111-222-333-1001"
USER = r"fixture-host\private-fixture-owner"
PROFILE = r"C:\Private-Fixture\WakeHub-Dedicated"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ROOT = Path(__file__).resolve().parents[1]


def process(pid, executable):
    return inv.ProcessIdentity(pid, T - 60, executable, SID, 7)


@pytest.fixture
def evidence():
    runners = tuple((process(100 + i, s.root + r"\bin\Runner.Listener.exe"),)
                    for i, s in enumerate(planned_topology()))
    browser = inv.ChromeProcess(process(200, CHROME), (9444,), ("127.0.0.1",), (PROFILE,))
    identities = tuple(g[0] for g in runners) + (browser.identity,)
    snapshot = inv.PrivateSnapshot(inv.SOURCE_EPOCH, T - 1, USER, SID, 7,
        ("READY", "RUNNING", "READY"), runners, (browser,), identities, identities,
        (inv.SocketListener("127.0.0.1", 9444, 200),), True)
    enrollment = inv.PrivateEnrollment(inv.SOURCE_EPOCH, USER, SID, 7, CHROME,
        PROFILE, T - 5, T + 25, True, False, True, False, False)
    return snapshot, enrollment


def attest(snapshot, enrollment, verified_at=T):
    # Synthetic independently approved evidence; production must not just echo.
    return inv.PrivateAttestation(inv._private_digest(snapshot),
        inv._private_digest(enrollment), verified_at)


def normalize(snapshot, enrollment, attestation=None, now=T):
    return inv.normalize_private_inventory(snapshot, enrollment,
        attestation if attestation is not None else attest(snapshot, enrollment), now=now)


def change_browser(snapshot, **changes):
    browser = replace(snapshot.chrome_processes[0], **changes)
    return replace(snapshot, chrome_processes=(browser,))


def no_chrome(snapshot):
    identities = tuple(p for p in snapshot.initial_processes if p.pid != 200)
    return replace(snapshot, chrome_processes=(), listeners=(),
        initial_processes=identities, confirmed_processes=identities)


def test_fixed_tasks_ready_listener_and_consumer_compatibility(evidence):
    snapshot, enrollment = evidence
    result = normalize(snapshot, enrollment)
    assert inv.TASKS == tuple(s.task for s in planned_topology())
    assert result.task_states == ("READY", "RUNNING", "READY")
    assert result.runner_listeners == ("PRESENT",) * 3
    assert result.chrome_ownership == "OWNED_CDP_9444"
    assert result.inventory_status == "VERIFIED_READ"
    assert result.supervisor.code == "READY_FOR_ORIGIN_PREPARE"
    assert result.supervisor.listener_count == 3
    assert not result.supervisor.start_permitted
    assert not result.supervisor.wake_send_authorized
    assert not result.supervisor.effect_attempted


@pytest.mark.parametrize("state", sorted(inv._TASK_STATES))
def test_task_state_does_not_override_independent_listener(state, evidence):
    snapshot, enrollment = evidence
    result = normalize(replace(snapshot, task_states=(state,) * 3), enrollment)
    assert result.task_states == (state,) * 3
    assert result.runner_listeners == ("PRESENT",) * 3
    assert result.supervisor.code == "READY_FOR_ORIGIN_PREPARE"


@pytest.mark.parametrize(("field", "value"), [
    ("user_name", "forged-owner"), ("user_sid", "S-1-5-21-999-1001"),
    ("session_id", 8), ("source_epoch", "f" * 40),
    ("observed_at", T - 31), ("observed_at", T + 1), ("complete", False),
])
def test_snapshot_identity_epoch_and_freshness_not_self_asserted(field, value, evidence):
    snapshot, enrollment = evidence
    forged = replace(snapshot, **{field: value})
    # Even a digest-matching fixture cannot override enrollment/temporal checks.
    result = normalize(forged, enrollment)
    assert result.chrome_ownership == "UNVERIFIED"
    assert result.inventory_status == "HOLD_UNVERIFIED"


@pytest.mark.parametrize(("field", "value"), [
    ("pid", 999), ("created_at", T - 59), ("user_sid", "S-1-5-21-999-1001"),
    ("session_id", 8), ("executable", r"C:\Foreign\chrome.exe"),
])
def test_process_forgery_cannot_reuse_attestation_or_confirmation(field, value, evidence):
    snapshot, enrollment = evidence
    forged = change_browser(snapshot,
        identity=replace(snapshot.chrome_processes[0].identity, **{field: value}))
    assert normalize(forged, enrollment, attest(snapshot, enrollment)).chrome_ownership == "UNVERIFIED"
    assert normalize(forged, enrollment).chrome_ownership == "UNVERIFIED"


@pytest.mark.parametrize(("field", "value"), [
    ("ports", (9333,)), ("ports", (9222,)), ("ports", ()),
    ("ports", (9444, 9444)), ("addresses", ("0.0.0.0",)),
    ("addresses", ("::1",)), ("addresses", ("::",)),
    ("addresses", ("127.0.0.2",)), ("addresses", ()),
    ("profiles", (r"C:\Foreign\Profile",)), ("profiles", ()),
])
def test_chrome_requires_every_exact_identity_facet(field, value, evidence):
    snapshot, enrollment = evidence
    result = normalize(change_browser(snapshot, **{field: value}), enrollment)
    assert result.chrome_ownership == "UNVERIFIED"
    assert not result.supervisor.ready_for_origin_prepare


@pytest.mark.parametrize(("field", "value"), [
    ("address", "0.0.0.0"), ("address", "::1"),
    ("address", "::"), ("address", "192.168.1.1"),
    ("port", 9333), ("pid", 201),
])
def test_foreign_wrong_port_wildcard_and_ipv6_listeners_hold(field, value, evidence):
    snapshot, enrollment = evidence
    listener = replace(snapshot.listeners[0], **{field: value})
    result = normalize(replace(snapshot, listeners=(listener,)), enrollment)
    assert result.chrome_ownership == "UNVERIFIED"
    assert result.inventory_status == "HOLD_UNVERIFIED"


@pytest.mark.parametrize("kind", ["port-only", "no-listener", "duplicate-listener",
    "duplicate-browser", "duplicate-profile", "duplicate-confirmation", "pid-reused",
    "new-chrome-between-reads", "missing-confirmation", "duplicate-initial"])
def test_missing_duplicate_collision_and_process_races(kind, evidence):
    snapshot, enrollment = evidence
    if kind == "port-only":
        snapshot = replace(snapshot, chrome_processes=())
    elif kind == "no-listener":
        snapshot = replace(snapshot, listeners=())
    elif kind == "duplicate-listener":
        snapshot = replace(snapshot, listeners=snapshot.listeners * 2)
    elif kind == "duplicate-browser":
        snapshot = replace(snapshot, chrome_processes=snapshot.chrome_processes * 2)
    elif kind == "duplicate-profile":
        snapshot = change_browser(snapshot, profiles=(PROFILE, PROFILE))
    elif kind == "duplicate-confirmation":
        snapshot = replace(snapshot, confirmed_processes=snapshot.confirmed_processes * 2)
    elif kind == "pid-reused":
        p = replace(snapshot.confirmed_processes[-1], created_at=T - 0.5)
        snapshot = replace(snapshot, confirmed_processes=(*snapshot.confirmed_processes[:-1], p))
    elif kind == "new-chrome-between-reads":
        absent = no_chrome(snapshot)
        snapshot = replace(absent, confirmed_processes=snapshot.confirmed_processes)
    elif kind == "missing-confirmation":
        snapshot = replace(snapshot, confirmed_processes=snapshot.confirmed_processes[:-1])
    else:
        snapshot = replace(snapshot, initial_processes=snapshot.initial_processes * 2)
    result = normalize(snapshot, enrollment)
    assert result.chrome_ownership in ("UNVERIFIED", "PROFILE_CONFLICT")
    assert not result.supervisor.ready_for_origin_prepare


def test_profile_collision_has_distinct_public_state(evidence):
    snapshot, enrollment = evidence
    other = replace(snapshot.chrome_processes[0], identity=process(201, CHROME))
    snapshot = replace(snapshot, chrome_processes=(*snapshot.chrome_processes, other),
        initial_processes=(*snapshot.initial_processes, other.identity),
        confirmed_processes=(*snapshot.confirmed_processes, other.identity))
    result = normalize(snapshot, enrollment)
    assert result.chrome_ownership == "PROFILE_CONFLICT"
    assert result.supervisor.code == "HOLD_CHROME_OWNERSHIP"


def test_different_profile_or_other_port_never_substitutes_for_owned_9444(evidence):
    snapshot, enrollment = evidence
    other = inv.ChromeProcess(process(201, CHROME), (9333,), ("127.0.0.1",),
        (r"C:\Unrelated-Fixture\Profile",))
    snapshot = replace(snapshot, chrome_processes=(other,),
        initial_processes=(*snapshot.initial_processes[:-1], other.identity),
        confirmed_processes=(*snapshot.confirmed_processes[:-1], other.identity))
    assert normalize(snapshot, enrollment).chrome_ownership == "UNVERIFIED"


def test_human_stop_absence_and_unknown_effect_preserved(evidence):
    snapshot, enrollment = evidence
    absent = no_chrome(snapshot)
    pause = replace(enrollment, trusted_human_pause=True)
    result = normalize(absent, pause)
    assert result.chrome_ownership == "ABSENT_INDEPENDENTLY_VERIFIED"
    assert result.lifecycle == "EXPECTED_MANUAL_STOP"
    assert result.supervisor.code == "PAUSED_BY_HUMAN"
    assert result.supervisor.next_action == "NO_EFFECT_KEEP_STOPPED"
    assert normalize(absent, pause) == result
    assert pause.trusted_human_pause is True
    resumed = normalize(absent, enrollment)
    assert resumed.supervisor.code == "WAIT_PRIVATE_CHROME_START_GRANT"
    assert not resumed.supervisor.start_permitted
    unknown = normalize(absent, replace(pause, unknown_effect=True))
    assert unknown.inventory_status == "HOLD_RECONCILE"
    assert unknown.supervisor.code == "HOLD_UNKNOWN_EFFECT"
    assert unknown.supervisor.next_action == "RECONCILE_ONLY"
    assert unknown.lifecycle == "EXPECTED_MANUAL_STOP"


@pytest.mark.parametrize("index", [0, 1, 2])
def test_task_ready_and_independently_absent_process(index, evidence):
    snapshot, enrollment = evidence
    removed = snapshot.runner_processes[index][0]
    groups = list(snapshot.runner_processes)
    groups[index] = ()
    identities = tuple(p for p in snapshot.initial_processes if p != removed)
    snapshot = replace(snapshot, task_states=("READY",) * 3, runner_processes=tuple(groups),
        initial_processes=identities, confirmed_processes=identities)
    result = normalize(snapshot, enrollment)
    assert result.task_states[index] == "READY"
    assert result.runner_listeners[index] == "ABSENT"
    assert result.supervisor.listener_count == 2
    if index == 2:
        assert result.supervisor.code == "HOLD_EVENT_RECEIVER_UNAVAILABLE"
    else:
        assert result.supervisor.code == "READY_FOR_ORIGIN_PREPARE"


@pytest.mark.parametrize(("field", "value"), [
    ("source_epoch", "f" * 40), ("read_allowed", False), ("muted", True),
    ("interactive_session_proven", False), ("user_name", "forged-owner"),
    ("user_sid", "S-1-5-21-999-1001"), ("session_id", 8),
    ("chrome_executable", r"C:\Foreign\chrome.exe"),
    ("dedicated_profile", r"C:\Foreign\Profile"),
])
def test_enrollment_forgery_and_denial_cannot_promote_ownership(field, value, evidence):
    snapshot, enrollment = evidence
    forged = replace(enrollment, **{field: value})
    assert normalize(snapshot, forged, attest(snapshot, enrollment)).chrome_ownership == "UNVERIFIED"
    assert normalize(snapshot, forged).chrome_ownership == "UNVERIFIED"


@pytest.mark.parametrize("kind", ["none", "stale", "future", "old-snapshot", "old-enrollment", "expired"])
def test_missing_stale_and_mismatched_private_attestation(kind, evidence):
    snapshot, enrollment = evidence
    proof = attest(snapshot, enrollment)
    if kind == "none":
        proof = None
    elif kind == "stale":
        proof = replace(proof, verified_at=T - 40)
    elif kind == "future":
        proof = replace(proof, verified_at=T + 1)
    elif kind == "old-snapshot":
        proof = replace(proof, snapshot_sha256="0" * 64)
    elif kind == "old-enrollment":
        proof = replace(proof, enrollment_sha256="0" * 64)
    now = T + 26 if kind == "expired" else T
    result = inv.normalize_private_inventory(snapshot, enrollment, proof, now=now)
    assert result.chrome_ownership == "UNVERIFIED"
    assert result.inventory_status == "HOLD_UNVERIFIED"


def test_models_frozen_private_and_public_receipt_does_not_disclose(evidence):
    snapshot, enrollment = evidence
    proof = attest(snapshot, enrollment)
    result = normalize(snapshot, enrollment, proof)
    public = result.to_json()
    data = json.loads(public)
    for secret in (USER, SID, PROFILE, CHROME, "command_line", "ws://", "token", "cookie",
                   proof.snapshot_sha256, proof.enrollment_sha256, "private-route"):
        assert secret not in public
        assert secret not in repr(snapshot)
        assert secret not in repr(enrollment)
    assert set(data) == {"schema_version", "cdp_port", "task_states", "runner_listeners",
        "chrome_ownership", "lifecycle", "inventory_status", "supervisor_code", "receiver_expectation",
        "runner_evidence", "start_permitted", "wake_send_authorized", "effect_attempted"}
    assert all(data[k] is False for k in ("start_permitted", "wake_send_authorized", "effect_attempted"))
    for obj, field, value in ((snapshot, "complete", False), (enrollment, "read_allowed", True),
                              (proof, "snapshot_sha256", "0" * 64), (result, "lifecycle", "FORGED")):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field, value)


@pytest.mark.parametrize("field", ["task_states", "runner_listeners", "chrome_ownership", "lifecycle", "inventory_status"])
def test_public_serializer_rejects_free_text_instead_of_redacting(field, evidence):
    snapshot, enrollment = evidence
    result = normalize(snapshot, enrollment)
    value = ("private-secret",) * 3 if field in ("task_states", "runner_listeners") else "private-secret"
    with pytest.raises(ValueError, match="WAKEHUB_PRIVATE_INPUT_INVALID"):
        replace(result, **{field: value}).to_json()


def test_normalization_has_no_file_process_browser_task_or_network_calls(monkeypatch, evidence):
    snapshot, enrollment = evidence
    proof = attest(snapshot, enrollment)
    def forbidden(*args, **kwargs):
        raise AssertionError("pure normalization attempted an external operation")
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(subprocess, "run", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(os, "system", forbidden)
        guard.setattr(socket, "socket", forbidden)
        result = normalize(snapshot, enrollment, proof)
        assert result == normalize(snapshot, enrollment, proof)
        assert json.loads(result.to_json())["effect_attempted"] is False


class FixtureAuthority:
    def __init__(self, snapshot, enrollment):
        self.enrollment = enrollment
        self.proof = attest(snapshot, enrollment)
        self.verified = 0

    def authorize_read(self):
        return self.enrollment

    def verify_read(self, snapshot, enrollment):
        self.verified += 1
        return self.proof


class FixtureBackend:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = 0

    def read_fixed_inventory(self):
        self.calls += 1
        return self.snapshot


@pytest.fixture
def private_windows(monkeypatch):
    # Synthetic gate settings only; all native commands are mocked below.
    monkeypatch.setattr(inv.os, "name", "nt")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(inv.time, "time", lambda: T)


def test_injected_backend_and_independent_verifier_called_once(private_windows, evidence):
    snapshot, enrollment = evidence
    authority = FixtureAuthority(snapshot, enrollment)
    backend = FixtureBackend(snapshot)
    result = inv.collect_private_inventory(authority, backend=backend)
    assert result.chrome_ownership == "OWNED_CDP_9444"
    assert backend.calls == authority.verified == 1
    backend.snapshot = replace(snapshot, user_name="forged-owner")
    result = inv.collect_private_inventory(authority, backend=backend)
    assert result.chrome_ownership == "UNVERIFIED"
    assert backend.calls == 2


@pytest.mark.parametrize("gate", ["no-authority", "no-enrollment", "denied", "muted",
    "unproven-desktop", "unknown-effect", "expired", "non-windows", "ci"])
def test_private_collection_denies_before_read_and_never_retries(gate, private_windows, monkeypatch, evidence):
    snapshot, enrollment = evidence
    overrides = {"denied": {"read_allowed": False}, "muted": {"muted": True},
        "unproven-desktop": {"interactive_session_proven": False},
        "unknown-effect": {"unknown_effect": True},
        "expired": {"issued_at": T - 60, "expires_at": T - 30}}
    enrollment = replace(enrollment, **overrides.get(gate, {}))
    authority = FixtureAuthority(snapshot, enrollment)
    if gate == "no-authority":
        authority = None
    elif gate == "no-enrollment":
        authority.enrollment = None
    elif gate == "non-windows":
        monkeypatch.setattr(inv.os, "name", "posix")
    elif gate == "ci":
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
    backend = FixtureBackend(snapshot)
    result = inv.collect_private_inventory(authority, backend=backend)
    assert backend.calls == 0
    assert result.chrome_ownership == "UNVERIFIED"
    if gate == "unknown-effect":
        assert result.inventory_status == "HOLD_RECONCILE"


def test_exception_is_sanitized_and_read_not_replayed(private_windows, evidence):
    snapshot, enrollment = evidence
    authority = FixtureAuthority(snapshot, replace(enrollment, trusted_human_pause=True))
    class FailedBackend:
        calls = 0
        def read_fixed_inventory(self):
            self.calls += 1
            raise RuntimeError("private-route token cookie " + SID)
    backend = FailedBackend()
    result = inv.collect_private_inventory(authority, backend=backend)
    assert backend.calls == 1 and authority.verified == 0
    assert result.lifecycle == "EXPECTED_MANUAL_STOP"
    assert "token" not in result.to_json()
    assert SID not in result.to_json()


def raw_native(snapshot):
    processes = []
    for identity in snapshot.initial_processes:
        p = asdict(identity)
        p["command_line"] = '"' + identity.executable + '"'
        if identity.pid == 200:
            p["command_line"] += (' --remote-debugging-port=9444 --remote-debugging-address=127.0.0.1'
                ' --user-data-dir="' + PROFILE + '"')
        processes.append(p)
    return {"schema_version": "WakeHubNativeReadPrivate.v1", "source_epoch": snapshot.source_epoch,
        "observed_at": snapshot.observed_at, "user_name": snapshot.user_name,
        "user_sid": snapshot.user_sid, "session_id": snapshot.session_id,
        "task_states": list(snapshot.task_states), "processes": processes,
        "confirmed_processes": [asdict(p) for p in snapshot.confirmed_processes],
        "listeners": [asdict(p) for p in snapshot.listeners], "complete": True}


def raw_bytes(snapshot):
    return json.dumps(raw_native(snapshot)).encode()


def test_mocked_fixed_windows_query_bounded_no_shell(private_windows, monkeypatch, evidence):
    snapshot, enrollment = evidence
    calls = []
    def read(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=raw_bytes(snapshot), stderr=b"private stderr")
    monkeypatch.setattr(inv.subprocess, "run", read)
    authority = FixtureAuthority(snapshot, enrollment)
    result = inv.collect_private_inventory(authority)
    assert result.chrome_ownership == "OWNED_CDP_9444"
    assert len(calls) == 1
    command, options = calls[0]
    assert command == inv._WINDOWS_READ_COMMAND
    assert options == dict(shell=False, capture_output=True, timeout=10, check=False, creationflags=0x08000000)
    script = command[-1]
    for task in inv.TASKS:
        assert task in script
    assert "LocalPort = 9444 AND State = 2" in script
    assert "9333" not in script
    for forbidden in ("Start-", "Stop-", "Register-", "Set-Content", "Invoke-WebRequest",
                      "Invoke-RestMethod", "Send", "New-ScheduledTask"):
        assert forbidden not in script
    decoded = inv._decode_native(raw_bytes(snapshot))
    assert decoded == snapshot
    assert "command_line" not in asdict(decoded)


@pytest.mark.parametrize("gate", ["no-authority", "ci", "non-windows", "denied", "unproven", "unknown"])
def test_native_backend_direct_call_also_requires_private_authority(gate, private_windows, monkeypatch, evidence):
    snapshot, enrollment = evidence
    def forbidden(*args, **kwargs):
        pytest.fail("unentitled native subprocess called")
    monkeypatch.setattr(inv.subprocess, "run", forbidden)
    if gate == "ci":
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
    elif gate == "non-windows":
        monkeypatch.setattr(inv.os, "name", "posix")
    overrides = {"denied": {"read_allowed": False}, "unproven": {"interactive_session_proven": False},
        "unknown": {"unknown_effect": True}}
    authority = FixtureAuthority(snapshot, replace(enrollment, **overrides.get(gate, {})))
    with pytest.raises(ValueError, match="WAKEHUB_"):
        inv.WindowsReadBackend(None if gate == "no-authority" else authority).read_fixed_inventory()


@pytest.mark.parametrize("failure", ["timeout", "exit", "malformed", "oversize"])
def test_native_failure_sanitized_no_second_command(failure, private_windows, monkeypatch, evidence):
    snapshot, enrollment = evidence
    calls = []
    def read(*args, **kwargs):
        calls.append(1)
        if failure == "timeout":
            raise subprocess.TimeoutExpired("private-command", 10)
        return SimpleNamespace(returncode=1 if failure == "exit" else 0,
            stdout=b"x" * (inv._MAX_BYTES + 1) if failure == "oversize" else b"not-json",
            stderr=("private-route " + SID).encode())
    monkeypatch.setattr(inv.subprocess, "run", read)
    result = inv.collect_private_inventory(FixtureAuthority(snapshot, enrollment))
    assert result.inventory_status == "HOLD_UNVERIFIED"
    assert len(calls) == 1
    assert SID not in result.to_json()


@pytest.mark.parametrize("kind", ["unknown-root", "unknown-process", "unknown-listener", "unknown-confirmation",
    "wrong-schema", "wrong-task-count", "unknown-task", "bool-pid", "negative-pid", "nan-time",
    "oversize-processes", "oversize-text", "non-list", "not-complete-bool", "null-command",
    "duplicate-json-field", "nested-bad-json", "oversize-bytes"])
def test_native_wire_contract_rejects_malformed_unknown_and_oversize(kind, evidence):
    snapshot, enrollment = evidence
    data = raw_native(snapshot)
    if kind == "unknown-root":
        data["command"] = "arbitrary-selector"
    elif kind == "unknown-process":
        data["processes"][0]["token"] = "private"
    elif kind == "unknown-listener":
        data["listeners"][0]["url"] = "ws://private"
    elif kind == "unknown-confirmation":
        data["confirmed_processes"][0]["token"] = "private"
    elif kind == "wrong-schema":
        data["schema_version"] = "anything"
    elif kind == "wrong-task-count":
        data["task_states"] = ["READY"]
    elif kind == "unknown-task":
        data["task_states"][0] = "OFFLINE"
    elif kind == "bool-pid":
        data["processes"][0]["pid"] = True
    elif kind == "negative-pid":
        data["processes"][0]["pid"] = -1
    elif kind == "nan-time":
        data["observed_at"] = float("nan")
    elif kind == "oversize-processes":
        data["processes"] *= 17
    elif kind == "oversize-text":
        data["user_name"] = "x" * 257
    elif kind == "non-list":
        data["processes"] = {}
    elif kind == "not-complete-bool":
        data["complete"] = 1
    elif kind == "null-command":
        data["processes"][0]["command_line"] = None
    raw = json.dumps(data).encode()
    if kind == "duplicate-json-field":
        raw = b'{"complete":true,"complete":false}'
    elif kind == "nested-bad-json":
        raw = b"[" * 1200 + b"]" * 1200
    elif kind == "oversize-bytes":
        raw = b" " * (inv._MAX_BYTES + 1)
    with pytest.raises(ValueError, match="WAKEHUB_PRIVATE_INPUT_INVALID") as caught:
        inv._decode_native(raw)
    assert SID not in str(caught.value)


@pytest.mark.parametrize("arguments", [
    "--remote-debugging-port=9444 --remote-debugging-port=9444",
    "--remote-debugging-port 9444", "--remote-debugging-port=abc",
    "--remote-debugging-port=65536", "--remote-debugging-address=0.0.0.0",
    "--remote-debugging-address=::1", "--user-data-dir=C:\\Foreign",
    "--remote-debugging-port=9333",
])
def test_native_argument_facets_never_execute_or_promote(arguments, evidence):
    snapshot, enrollment = evidence
    data = raw_native(snapshot)
    data["processes"][-1]["command_line"] = '"' + CHROME + '" ' + arguments
    try:
        decoded = inv._decode_native(json.dumps(data).encode())
    except ValueError:
        return
    assert normalize(decoded, enrollment).chrome_ownership != "OWNED_CDP_9444"


def test_windows_command_parser_and_powershell_syntax_only():
    if os.name != "nt":
        pytest.skip("Windows parser check, not a host native observation")
    args = inv._windows_argv('"' + CHROME + '" --user-data-dir="' + PROFILE + '"')
    assert args == (CHROME, "--user-data-dir=" + PROFILE)
    # Parse the fixed script text only; never execute its native queries.
    check = "$e=$null;[Management.Automation.Language.Parser]::ParseInput(@'\n" + inv._WINDOWS_READ_SCRIPT + "\n'@,[ref]$null,[ref]$e)|Out-Null;if($e.Count){exit 1}"
    result = subprocess.run([inv._WINDOWS_READ_COMMAND[0], "-NoProfile", "-NonInteractive",
        "-Command", check], capture_output=True, timeout=15, check=False)
    assert result.returncode == 0


def test_owner_manifest_exact_data_only_scope():
    path = ROOT / "work_items/mt01-components/wakehub-native-inventory.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest == {
        "schema_version": "MT01ComponentOwner.v1", "component": "wakehub-native-inventory",
        "sources": ["src/ai_commerce/wakehub/__init__.py", "src/ai_commerce/wakehub/native_inventory.py"],
        "tests": ["tests/test_wakehub_native_inventory.py"],
        "acs": ["WAKEHUB.WH01B.native-inventory-readonly"],
        "consumers": ["src/ai_commerce/engineering/three_runner_topology.py"], "contracts": [],
    }
