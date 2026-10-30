"""D5.4 synthetic completion handoff; no network, browser, grants or activation."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
from threading import Event
from types import SimpleNamespace
import urllib.error

import pytest

from ai_commerce.remote import local_completion_handoff as handoff
from ai_commerce.remote import _private_state, local_wake_entry
from ai_commerce.remote.completion_event import build_completion_event
from ai_commerce.remote.local_run_registration import RegistrationBlocked
from ai_commerce.remote.validation import fingerprint
from ai_commerce.remote.wake_adapter import WakeReceipt
from ai_commerce.remote.signed_origin_consent import registry_sha256
from test_ef02_d5_run_registration import GitHub, ROOT, NOW, REPO, WORKFLOW, zipped

EXPIRY = "2026-10-30T00:21:00Z"


@pytest.fixture(autouse=True)
def no_real_transports(monkeypatch):
    from ai_commerce.remote import github_artifact_authority, cdp_doorbell
    monkeypatch.setattr(github_artifact_authority, "_fetch",
                        lambda *a, **k: pytest.fail("real network forbidden"))
    monkeypatch.setattr(cdp_doorbell._PlaywrightInventory, "pages",
                        lambda *a, **k: pytest.fail("real browser forbidden"))
    monkeypatch.delenv("AI_COMMERCE_LOCAL_HANDOFF_ENABLED", raising=False)
    monkeypatch.delenv("AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED", raising=False)


def provision(path):
    """Provision only a new synthetic test directory, never a host directory."""
    path.mkdir(mode=0o700)
    if os.name == "nt":
        sid = handoff._windows_process_sid()
        result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                                 "*" + sid + ":(OI)(CI)F"], capture_output=True)
        assert result.returncode == 0, "test directory ACL provisioning failed"
    return path


def write_private(path, data):
    path.write_text(json.dumps(data) if isinstance(data, dict) else data, encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)


class Clock:
    def __init__(self):
        self.wall, self.mono, self.sleeps = NOW, 0, []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.wall += seconds
        self.mono += seconds


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source = GitHub()
    private = provision(tmp_path / "operator-private")
    store = handoff.HandoffStore(private / "handoff.json", ROOT)
    clock = Clock()
    # Exercise real D3.1 comparisons and shared GET budgeting with synthetic GETs.
    monkeypatch.setattr(handoff.GitHubRestArtifactAuthority, "_read_json",
                        staticmethod(lambda u, t, **kw: source.json(u, t)))
    monkeypatch.setattr(handoff.GitHubRestArtifactAuthority, "_read_bytes",
                        staticmethod(lambda u, t, **kw: source.archive(u, t)))
    monkeypatch.setattr(handoff, "_on_windows", lambda: True)
    env = {"AI_COMMERCE_LOCAL_HANDOFF_ENABLED": "true",
           "AI_COMMERCE_LOCAL_HANDOFF_STATE": str(store.state.path),
           "GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_NAME": "issues",
           "GITHUB_WORKFLOW_REF": REPO + "/.github/workflows/ef02-batch.yml@refs/heads/main",
           "GITHUB_RUN_ID": "120", "GITHUB_RUN_ATTEMPT": "1"}
    return SimpleNamespace(source=source, store=store, clock=clock, env=env, private=private)


def enroll(s, **options):
    return handoff.enroll(s.store, intent=s.source.intent(), run_id=120,
                         expires_at=EXPIRY, opt_in=True, wall_clock=lambda: s.clock.wall, **options)


def emit(s, **options):
    s.clock.wall = NOW + 2
    values = dict(batch=s.source.request, event=s.source.events[120],
                  report=s.source.reports[120], repository_root=ROOT, issuer_login="trung-via",
                  completed_at=NOW + 1, environ=s.env, wall_clock=lambda: s.clock.wall)
    handoff._emit_worker_signal(**(values | options))


def listen(s, **options):
    values = dict(max_polls=3, interval_seconds=1, max_seconds=30,
                  reader_factory=lambda budget: s.source.reader(read_budget=budget),
                  wall_clock=lambda: s.clock.wall, clock=lambda: s.clock.mono, sleep=s.clock.sleep)
    return handoff.listen(s.store, **(values | options))


def read(s):
    return json.loads(s.store.state.path.read_bytes())


def delivery(s, monkeypatch):
    principal = handoff._private_identity(s.private)
    monkeypatch.setattr(handoff, "_interactive_sid", lambda: principal)
    monkeypatch.setattr(handoff, "_secret_inputs", lambda: ("fixture-read-token", b"11" * 32))
    options = {k: s.private / (k + ".json") for k in ("registry", "idempotency", "grants")}
    for key in ("registry", "grants"):
        write_private(options[key], {})
    return options | {"caller_id": "fixture-operator"}


def test_worker_opt_out_never_touches_private_state(monkeypatch):
    monkeypatch.delenv("AI_COMMERCE_LOCAL_HANDOFF_ENABLED", raising=False)
    monkeypatch.setattr(handoff, "_emit_worker_signal", lambda **kw: pytest.fail("opt-out touched state"))
    handoff.start_worker_signal(batch={}, event={}, report={}, repository_root=Path.cwd(),
                                issuer_login="trung-via", completed_at=1)


def test_new_module_has_no_automatic_activation(monkeypatch):
    monkeypatch.delenv("AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED", raising=False)
    assert handoff._result("UNPROVEN", "NO_SIGNAL")["chat_delivery_proven"] is False
    assert handoff._result("UNPROVEN", "NO_SIGNAL")["brain_continuation_proven"] is False
    assert handoff._result("UNPROVEN", "NO_SIGNAL")["live_wake_status"] == "LIVE_WAKE_NOT_PROVEN"


@pytest.mark.parametrize("opt_in", [False, "true", 1, None])
def test_enrollment_requires_literal_opt_in(setup, opt_in):
    s = setup
    with pytest.raises(handoff.HandoffBlocked, match="LITERAL"):
        handoff.enroll(s.store, intent=s.source.intent(), run_id=120, expires_at=EXPIRY,
                       opt_in=opt_in, wall_clock=lambda: NOW)
    assert not s.store.state.path.exists() and not s.source.calls


def test_no_enrollment_or_hint_means_no_gets_no_sender(setup):
    s = setup
    forbidden = lambda *a, **kw: pytest.fail("activity without signal")
    assert listen(s, reader_factory=forbidden, sender=forbidden)["error_code"] == "NO_ENROLLMENT"
    enroll(s)
    assert listen(s, reader_factory=forbidden, sender=forbidden)["error_code"] == "HANDOFF_NOT_READY"
    assert not s.source.calls and s.clock.sleeps == [1, 1]
    assert read(s)["phase"] == "ARMED"


def test_enrollment_is_private_bounded_idempotent_and_cannot_be_extended(setup):
    s = setup
    record = enroll(s)
    before = s.store.state.path.read_bytes()
    s.clock.wall += 1
    assert enroll(s) == record
    assert s.store.state.path.read_bytes() == before
    for key, value in [("run_id", 121), ("expires_at", "2026-10-30T00:22:00Z"),
                       ("mode", "invalid")]:
        kwargs = dict(intent=s.source.intent(), run_id=120, expires_at=EXPIRY,
                      opt_in=True, wall_clock=lambda: s.clock.wall) | {key: value}
        with pytest.raises(handoff.HandoffBlocked):
            handoff.enroll(s.store, **kwargs)
    raw = before.decode()
    assert len(before) <= 16384
    assert all(x not in raw for x in ("fixture-read-token", "fixture instruction",
                                     "chatgpt.com", "signature", "grants.json"))


@pytest.mark.parametrize("field,value", [
    ("run_id", True), ("run_id", 0), ("run_id", "120"),
    ("expires_at", "2026-10-30T00:11:00Z"),
    ("expires_at", "2026-10-30T02:11:01Z"), ("expires_at", "not-utc"),
])
def test_bad_enrollment_selectors_or_windows_are_rejected(setup, field, value):
    s = setup
    kwargs = dict(intent=s.source.intent(), run_id=120, expires_at=EXPIRY,
                  opt_in=True, wall_clock=lambda: NOW) | {field: value}
    with pytest.raises(ValueError):
        handoff.enroll(s.store, **kwargs)
    assert not s.store.state.path.exists()


@pytest.mark.parametrize("location", ["source", "checkout", "relative", "missing_parent", "lock_suffix"])
def test_private_location_fail_closed(setup, tmp_path, location):
    s = setup
    checkout = provision(tmp_path / "checkout")
    choices = {"source": (ROOT / "never-handoff.json", checkout),
               "checkout": (checkout / "never-handoff.json", checkout),
               "relative": (Path("relative.json"), ROOT),
               "missing_parent": (tmp_path / "absent" / "handoff.json", ROOT),
               "lock_suffix": (s.private / "handoff.json.lock", ROOT)}
    with pytest.raises((ValueError, OSError)):
        handoff.HandoffStore(*choices[location])


def test_private_acl_checks_owner_groups_and_unrecognized_aces():
    sid = "S-1-5-21-123"
    handoff._check_windows_acl(sid, [("allow", sid), ("allow", "S-1-5-18"),
                                    ("allow", "S-1-5-32-544")], sid)
    for owner, aces in [(sid, []), ("S-1-5-21-999", [("allow", sid)]),
                        (sid, [("allow", sid), ("allow", "S-1-1-0")]),
                        (sid, [("allow", sid), ("object_allow", sid)]),
                        (sid, [("allow", "S-1-5-18")])]:
        with pytest.raises(handoff.HandoffBlocked, match="PERMISSIONS"):
            handoff._check_windows_acl(owner, aces, sid)


def test_native_private_directory_is_owned_and_restrictive(setup):
    s = setup
    assert handoff._private_identity(s.private) == s.store._permissions()
    if os.name == "nt":
        owner, aces = handoff._windows_acl(s.private)
        assert owner == handoff._windows_process_sid()
        assert all(kind != "allow" or sid in {owner, "S-1-5-18", "S-1-5-32-544", "S-1-3-4"}
                   for kind, sid in aces)


def test_permission_or_identity_changes_fail_before_network(setup, monkeypatch):
    s = setup
    enroll(s)
    emit(s)
    original = handoff._private_identity
    monkeypatch.setattr(handoff, "_private_identity",
                        lambda path: "uid:999999" if path == s.private else original(path))
    with pytest.raises(handoff.HandoffBlocked, match="OWNER"):
        listen(s)
    assert not s.source.calls


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits; native Windows DACL tested separately")
def test_posix_broad_permissions_fail_closed(setup):
    s = setup
    s.private.chmod(0o755)
    with pytest.raises(handoff.HandoffBlocked, match="PERMISSIONS"):
        enroll(s)
    s.private.chmod(0o700)
    enroll(s)
    s.store.state.path.chmod(0o644)
    with pytest.raises(handoff.HandoffBlocked, match="PERMISSIONS"):
        listen(s)


@pytest.mark.parametrize("target", ["state", "lock", "directory"])
def test_symlink_and_reparse_paths_are_rejected(setup, tmp_path, target):
    s = setup
    other = provision(tmp_path / "other")
    destination = other / "data.json"
    write_private(destination, {})
    link = (s.store.state.path if target == "state" else
            s.private / "handoff.json.lock" if target == "lock" else tmp_path / "directory-link")
    try:
        link.symlink_to(other if target == "directory" else destination, target_is_directory=target == "directory")
    except OSError:
        pytest.skip("OS does not permit native symlink creation")
    with pytest.raises(ValueError):
        if target == "directory":
            handoff.HandoffStore(link / "handoff.json", ROOT)
        else:
            enroll(s)
    assert json.loads(destination.read_bytes()) == {}


def test_reparse_attribute_is_rejected_even_without_symlink_privilege(setup, monkeypatch):
    s = setup
    actual = s.private.lstat()
    real = handoff._linked
    monkeypatch.setattr(handoff, "_linked", lambda info: info is actual or real(info))
    class FakePath:
        def lstat(self):
            return actual
    with pytest.raises(handoff.HandoffBlocked, match="UNSAFE"):
        handoff._private_identity(FakePath())


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(version=True), lambda r: r.update(run_id=True),
    lambda r: r["intent"].update(request_fingerprint="b" * 64),
    lambda r: r.update(extra="arbitrary"), lambda r: r.update(enrolled_at=float("nan")),
    lambda r: r.update(chat_delivery_proven=True), lambda r: r.update(brain_continuation_proven=True),
    lambda r: r.update(phase="UNKNOWN"), lambda r: r.update(hint={}),
    lambda r: r.update(registration={}), lambda r: r.update(completed_at=NOW - 1),
])
def test_private_state_integrity_and_shape_are_fail_closed(setup, mutate):
    s = setup
    enroll(s)
    record = read(s)
    mutate(record)
    write_private(s.store.state.path, record)
    with pytest.raises(handoff.HandoffBlocked, match="STATE_INVALID"):
        listen(s)
    assert not s.source.calls


@pytest.mark.parametrize("raw", ['{}', '{"version":1,"version":1}', "not-json", "x" * 16385])
def test_empty_duplicate_malformed_or_oversize_private_state_is_not_enrollment(setup, raw):
    s = setup
    write_private(s.store.state.path, raw)
    with pytest.raises(ValueError):
        listen(s)
    assert not s.source.calls


def test_os_lock_contention_and_atomic_write_failure_leave_enrollment_unchanged(setup, monkeypatch):
    s = setup
    enroll(s)
    before = s.store.state.path.read_bytes()
    real_clock = _private_state.time.monotonic
    values = iter([0, 6])
    with s.store.state.locked():
        monkeypatch.setattr(_private_state, "time", SimpleNamespace(monotonic=lambda: next(values), sleep=lambda x: None))
        with pytest.raises(ValueError, match="BUSY"):
            emit(s)
    monkeypatch.setattr(_private_state, "time", SimpleNamespace(monotonic=real_clock, sleep=lambda x: None))
    def fail_replace(*a, **kw):
        raise OSError("synthetic interrupted replace")
    monkeypatch.setattr(_private_state.os, "replace", fail_replace)
    with pytest.raises(ValueError):
        emit(s)
    assert s.store.state.path.read_bytes() == before
    assert not list(s.private.glob("*.part")) and not s.source.calls


@pytest.mark.parametrize("flag", [None, "", "TRUE", "1", "false"])
def test_worker_literal_opt_in_is_required_even_with_valid_enrollment(setup, flag):
    s = setup
    enroll(s)
    if flag is None:
        s.env.pop("AI_COMMERCE_LOCAL_HANDOFF_ENABLED")
    else:
        s.env["AI_COMMERCE_LOCAL_HANDOFF_ENABLED"] = flag
    emit(s)
    assert read(s)["phase"] == "ARMED" and not s.source.calls


@pytest.mark.parametrize("change", [
    {"GITHUB_REPOSITORY": "other/repo"}, {"GITHUB_EVENT_NAME": "workflow_dispatch"},
    {"GITHUB_WORKFLOW_REF": "other/.github/workflows/ef02-batch.yml@refs/heads/main"},
    {"GITHUB_RUN_ID": "121"}, {"GITHUB_RUN_ATTEMPT": "2"},
    {"AI_COMMERCE_LOCAL_HANDOFF_STATE": ""}])
def test_worker_wrong_runtime_selectors_drop_hint(setup, change):
    s = setup
    enroll(s)
    s.env.update(change)
    emit(s)
    assert read(s)["phase"] == "ARMED" and not s.source.calls


@pytest.mark.parametrize("change", ["issuer", "request", "report", "event", "preconsent", "expired"])
def test_worker_mismatches_and_out_of_order_completion_never_signal(setup, change):
    s = setup
    enroll(s)
    options = {}
    if change == "issuer":
        options["issuer_login"] = "attacker"
    elif change == "request":
        options["batch"] = deepcopy(s.source.request) | {"authorization_ref": "modified"}
    elif change == "report":
        options["report"] = s.source.reports[120] | {"wake_event_id": "wrong"}
    elif change == "event":
        options["event"] = build_completion_event(s.source.reports[120], repository=REPO,
                                                   run_id=121, run_attempt=1, issue_number=123)
    elif change == "preconsent":
        options["completed_at"] = NOW
    else:
        options["wall_clock"] = lambda: NOW + 601
    emit(s, **options)
    assert read(s)["phase"] == "ARMED" and not s.source.calls


def test_worker_no_enrollment_does_not_create_one(setup):
    s = setup
    emit(s)
    assert not s.store.state.path.exists() and not s.source.calls


def test_worker_signal_contains_only_exact_safe_selectors_and_is_idempotent(setup):
    s = setup
    enroll(s)
    emit(s)
    record = read(s)
    assert record["phase"] == "SIGNALED"
    assert set(record["hint"]) == {"repository", "issue_number", "run_id", "run_attempt",
                                   "workflow_id", "batch_id", "source_sha", "event_id"}
    assert record["hint"]["event_id"] == s.source.events[120]["event_id"]
    before = s.store.state.path.read_bytes()
    emit(s)
    assert s.store.state.path.read_bytes() == before and not s.source.calls


def test_worker_busy_lock_never_waits_and_signal_failure_is_swallowed(setup, monkeypatch):
    s = setup
    enroll(s)
    for k, v in s.env.items():
        monkeypatch.setenv(k, v)
    s.clock.wall = NOW + 2
    with s.store.state.locked():
        monkeypatch.setattr(_private_state, "time", SimpleNamespace(
            monotonic=lambda: 0, sleep=lambda x: pytest.fail("worker waited for a lock")))
        handoff.start_worker_signal(batch=s.source.request, event=s.source.events[120],
            report=s.source.reports[120], repository_root=ROOT,
            issuer_login="trung-via", completed_at=NOW + 1)
    assert read(s)["phase"] == "ARMED" and not s.source.calls
    monkeypatch.setattr(handoff, "_emit_worker_signal", lambda **kw: (_ for _ in ()).throw(OSError("private error")))
    handoff.start_worker_signal(batch={}, event={}, report={}, repository_root=ROOT,
                                issuer_login="trung-via", completed_at=NOW)


def test_after_worker_before_artifact_race_and_independent_get_authentication(setup):
    s = setup
    enroll(s)
    emit(s)
    s.source.runs[120]["status"] = "in_progress"
    result_artifact = s.source.artifacts[120].pop()
    def delayed_upload(seconds):
        s.clock.sleep(seconds)
        if len(s.clock.sleeps) == 1:
            s.source.runs[120]["status"] = "completed"
        else:
            s.source.artifacts[120].append(result_artifact)
    result = listen(s, sleep=delayed_upload)
    assert result["status"] == "CANDIDATE_READY_NOT_SENT"
    assert result["registration_status"] == "REGISTERED"
    assert s.clock.sleeps == [1, 1] and read(s)["phase"] == "OBSERVED"
    # D5.3 downloads the immutable request; D3.1 separately re-downloads results.
    assert sum("/artifacts/1201/zip" in u for u in s.source.calls) >= 1
    assert sum("/artifacts/1202/zip" in u for u in s.source.calls) == 2
    assert not any("/runs?" in u or "/runs/121" in u for u in s.source.calls)


@pytest.mark.parametrize("seconds", [-1, 6, float("nan"), float("inf"), True, "0"])
def test_invalid_lock_budgets_cannot_create_or_hold_private_state(setup, seconds):
    s = setup
    with pytest.raises(ValueError, match="LOCK_BUDGET_INVALID"):
        with s.store.state.locked(wait_seconds=seconds):
            pytest.fail("invalid lock budget acquired state")
    assert not s.store.state.path.exists()


def test_listener_can_start_before_hint_without_network(setup):
    s = setup
    enroll(s)
    def local_arrival(seconds):
        assert not s.source.calls
        s.clock.sleep(seconds)
        emit(s)
    assert listen(s, sleep=local_arrival)["status"] == "CANDIDATE_READY_NOT_SENT"
    assert s.clock.sleeps == [1]


@pytest.mark.parametrize("change", [
    {"run_id": 121}, {"run_id": True}, {"run_attempt": 2}, {"workflow_id": WORKFLOW + 1},
    {"issue_number": 124}, {"repository": "other/repo"}, {"source_sha": "b" * 40},
    {"event_id": "garbage"}, {"extra": "payload"}])
def test_wrong_or_ambiguous_hint_is_denied_before_gets(setup, change):
    s = setup
    enroll(s)
    emit(s)
    record = read(s)
    record["hint"].update(change)
    write_private(s.store.state.path, record)
    with pytest.raises(ValueError):
        listen(s)
    assert not s.source.calls


def test_bogus_well_formed_event_hash_is_not_an_authenticated_wake(setup):
    s = setup
    enroll(s)
    emit(s)
    record = read(s)
    record["hint"]["event_id"] = "ef02-completion-v1:" + "b" * 64
    write_private(s.store.state.path, record)
    with pytest.raises(handoff.HandoffBlocked, match="HINT_MISMATCH"):
        listen(s)
    assert read(s)["phase"] == "SIGNALED"


@pytest.mark.parametrize("kind", ["edited_issue", "wrong_snapshot", "missing_snapshot", "bad_digest",
                                  "wrong_run", "duplicate_results", "wrong_actions", "wrong_issuer"])
def test_invalid_immutable_request_run_or_result_never_hands_off(setup, kind):
    s = setup
    enroll(s)
    emit(s)
    if kind == "edited_issue":
        s.source.issue["body"] = "{}"
    elif kind == "wrong_snapshot":
        request = deepcopy(s.source.request) | {"authorization_ref": "altered"}
        s.source.put_archive(120, 1201, "ef02-request-123", zipped({"request.json": request}))
    elif kind == "missing_snapshot":
        s.source.artifacts[120] = s.source.artifacts[120][1:]
    elif kind == "bad_digest":
        s.source.artifacts[120][1]["digest"] = "sha256:" + "b" * 64
    elif kind == "wrong_run":
        s.source.runs[120]["run_attempt"] = 2
    elif kind == "wrong_issuer":
        s.source.issue["user"]["login"] = "attacker"
    elif kind == "duplicate_results":
        s.source.artifacts[120].append(s.source.artifacts[120][1] | {"id": 1300})
    else:
        report = deepcopy(s.source.reports[120])
        report["actions"][0]["attempt_id"] = "wrong-attempt"
        event = build_completion_event(report, repository=REPO, run_id=120, run_attempt=1, issue_number=123)
        report["wake_event_id"] = event["event_id"]
        s.source.put_archive(120, 1202, "ef02-results-120-1",
                            zipped({s.source.request["batch_id"] + "/completion_event.json": event,
                                    s.source.request["batch_id"] + "/batch_result.json": report}))
    if kind == "missing_snapshot":
        assert listen(s)["status"] == "UNPROVEN"
    else:
        with pytest.raises(ValueError):
            listen(s, sender=lambda **kw: pytest.fail("invalid evidence sent"))
    assert read(s)["phase"] == "SIGNALED"


def test_independent_artifact_authority_denial_is_fatal(setup, monkeypatch):
    s = setup
    enroll(s)
    emit(s)
    monkeypatch.setattr(handoff.GitHubRestArtifactAuthority, "verify_artifact", lambda *a, **kw: False)
    with pytest.raises(handoff.HandoffBlocked, match="PROVENANCE_UNVERIFIED"):
        listen(s)
    assert read(s)["phase"] == "SIGNALED"


@pytest.mark.parametrize("kind", ["polls", "reads", "deadline", "consent", "stale_request_window"])
def test_unready_and_stale_sessions_are_bounded(setup, kind):
    s = setup
    enroll(s)
    emit(s)
    s.source.runs[120]["status"] = "in_progress"
    kwargs = {}
    if kind == "reads":
        kwargs["max_reads"] = 1
    elif kind == "deadline":
        kwargs["max_seconds"] = 1
    elif kind == "consent":
        s.clock.wall = NOW + 601
    elif kind == "stale_request_window":
        s.clock.wall = NOW + 8 * 86400
    if kind == "polls":
        assert listen(s, **kwargs)["error_code"] == "HANDOFF_NOT_READY"
        assert s.clock.sleeps == [1, 1]
    else:
        with pytest.raises(ValueError):
            listen(s, **kwargs)
        assert len(s.clock.sleeps) <= 1
    assert read(s)["phase"] == "SIGNALED"


def test_only_transient_gets_retry_and_late_artifact_restart_stays_exact(setup):
    s = setup
    enroll(s)
    emit(s)
    original = s.source.json
    first = [True]
    def transient(url, token):
        if first[0]:
            first[0] = False
            raise urllib.error.HTTPError(url, 503, "synthetic", {}, None)
        return original(url, token)
    s.source.json = transient
    results = s.source.artifacts[120].pop()
    assert listen(s, max_polls=1)["status"] == "UNPROVEN"
    assert s.clock.sleeps == [1]
    s.source.artifacts[120].append(results)
    s.source.add_run(121)
    assert listen(s)["status"] == "CANDIDATE_READY_NOT_SENT"
    assert not any("/runs/121" in u or "/runs?" in u for u in s.source.calls)


def test_permanent_get_denial_does_not_retry(setup):
    s = setup
    enroll(s)
    emit(s)
    calls = []
    def forbidden(url, token):
        calls.append(url)
        raise urllib.error.HTTPError(url, 403, "synthetic", {}, None)
    s.source.json = forbidden
    with pytest.raises(RegistrationBlocked):
        listen(s)
    assert len(calls) == 1 and not s.clock.sleeps


def test_observation_duplicate_signal_and_process_restart_have_no_second_activity(setup):
    s = setup
    enroll(s)
    emit(s)
    first = listen(s)
    assert first["status"] == "CANDIDATE_READY_NOT_SENT"
    before = s.store.state.path.read_bytes()
    calls = len(s.source.calls)
    s.store = handoff.HandoffStore(s.store.state.path, ROOT)
    emit(s)
    assert listen(s)["error_code"] == "HANDOFF_ALREADY_CONSUMED"
    enroll(s)
    assert s.store.state.path.read_bytes() == before and len(s.source.calls) == calls


def test_delivery_cannot_escalate_observe_enrollment(setup, monkeypatch):
    s = setup
    options = delivery(s, monkeypatch)
    enroll(s)
    emit(s)
    with pytest.raises(handoff.HandoffBlocked, match="NOT_ENROLLED"):
        listen(s, deliver=True, delivery=options)
    assert not s.source.calls and read(s)["phase"] == "SIGNALED"


def test_deliver_requires_interactive_gate_again_at_listener(setup, monkeypatch):
    s = setup
    options = delivery(s, monkeypatch)
    enroll(s, mode="deliver")
    emit(s)
    def denied():
        raise handoff.HandoffBlocked("LOCAL_SEND_GATE_CLOSED")
    monkeypatch.setattr(handoff, "_interactive_sid", denied)
    with pytest.raises(handoff.HandoffBlocked, match="GATE_CLOSED"):
        listen(s, deliver=True, delivery=options)
    assert not s.source.calls


@pytest.mark.parametrize("flag", [None, "false", "TRUE", "1"])
def test_delivery_enrollment_requires_preexisting_literal_environment_gate(setup, monkeypatch, flag):
    s = setup
    if flag is not None:
        monkeypatch.setenv("AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED", flag)
    with pytest.raises(handoff.HandoffBlocked, match="SEND_GATE_CLOSED"):
        enroll(s, mode="deliver")
    assert not s.store.state.path.exists() and not s.source.calls


def test_consent_expiring_during_authentication_prevents_any_delivery(setup, monkeypatch):
    s = setup
    options = delivery(s, monkeypatch)
    enroll(s, mode="deliver")
    emit(s)
    def late(*args, **kwargs):
        s.clock.wall = NOW + 601
        return True
    monkeypatch.setattr(handoff.GitHubRestArtifactAuthority, "verify_artifact", late)
    with pytest.raises(handoff.HandoffBlocked, match="WINDOW_CLOSED"):
        listen(s, deliver=True, delivery=options, sender=lambda **kw: pytest.fail("expired consent sent"))
    assert read(s)["phase"] == "SIGNALED"


@pytest.mark.parametrize("alias", ["handoff", "handoff_lock", "grants", "registry_lock"])
def test_delivery_private_paths_must_not_alias_any_store_or_lock(setup, monkeypatch, alias):
    s = setup
    options = delivery(s, monkeypatch)
    enroll(s, mode="deliver")
    emit(s)
    options["idempotency"] = {
        "handoff": s.store.state.path, "handoff_lock": s.store.state.path.with_suffix(".json.lock"),
        "grants": options["grants"], "registry_lock": options["registry"].with_suffix(".json.lock"),
    }[alias]
    with pytest.raises(ValueError):
        listen(s, deliver=True, delivery=options)
    assert not s.source.calls


@pytest.mark.parametrize("failure", ["unknown", "exception", "crash"])
def test_unknown_is_durable_before_send_and_never_replayed(setup, monkeypatch, failure):
    s = setup
    options = delivery(s, monkeypatch)
    enroll(s, mode="deliver")
    emit(s)
    calls = []
    def sender(**kw):
        calls.append(kw)
        assert read(s)["phase"] == "UNKNOWN"
        if failure == "exception":
            raise OSError("private failure")
        if failure == "crash":
            raise KeyboardInterrupt
        return WakeReceipt(event_id=kw["event"]["event_id"], route_id="route", generation=1,
                           delivery_status="UNKNOWN", error_code="UI_USER_TURN_OBSERVED")
    if failure == "crash":
        with pytest.raises(KeyboardInterrupt):
            listen(s, deliver=True, delivery=options, sender=sender)
    else:
        result = listen(s, deliver=True, delivery=options, sender=sender)
        assert result["status"] == "UNKNOWN" and result["chat_delivery_proven"] is False
    network_calls = len(s.source.calls)
    s.store = handoff.HandoffStore(s.store.state.path, ROOT)
    emit(s)
    enroll(s, mode="deliver")
    assert listen(s, deliver=True, delivery=options, sender=sender)["status"] == "UNKNOWN"
    assert len(calls) == 1 and len(s.source.calls) == network_calls
    assert read(s)["live_wake_status"] == "LIVE_WAKE_NOT_PROVEN"


def test_atomic_unknown_persistence_failure_never_invokes_sender(setup, monkeypatch):
    s = setup
    options = delivery(s, monkeypatch)
    enroll(s, mode="deliver")
    emit(s)
    def fail(*a, **kw):
        raise OSError("synthetic write failure")
    monkeypatch.setattr(_private_state.os, "replace", fail)
    with pytest.raises(ValueError):
        listen(s, deliver=True, delivery=options, sender=lambda **kw: pytest.fail("sent before durable intent"))
    assert read(s)["phase"] == "SIGNALED"


def test_concurrent_listeners_make_at_most_one_delivery_attempt(setup, monkeypatch):
    s = setup
    options = delivery(s, monkeypatch)
    enroll(s, mode="deliver")
    emit(s)
    entered, release = Event(), Event()
    calls = []
    def sender(**kw):
        calls.append(kw)
        entered.set()
        assert release.wait(3)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(listen, s, deliver=True, delivery=options, sender=sender)
        assert entered.wait(3)
        second = pool.submit(listen, s, deliver=True, delivery=options, sender=sender)
        release.set()
        assert first.result()["status"] == second.result()["status"] == "UNKNOWN"
    assert len(calls) == 1


def test_cli_opt_out_errors_are_redacted_and_default_listen_is_observe(setup, monkeypatch, capsys):
    s = setup
    base = ["--state", str(s.store.state.path), "--checkout", str(ROOT)]
    assert handoff.main(["enroll", *base]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "BLOCKED" and str(s.private) not in json.dumps(output)
    enroll(s)
    emit(s)
    observed = []
    def fake_listen(store, **kw):
        observed.append(kw)
        return handoff._result("CANDIDATE_READY_NOT_SENT", "OBSERVED", registered=True)
    monkeypatch.setattr(handoff, "listen", fake_listen)
    assert handoff.main(["listen", *base]) == 0
    assert observed[0]["deliver"] is False
    assert handoff.main(["listen", *base, "--run-id", "121"]) == 2


def test_worker_final_boundary_and_signal_failure_preserve_result(setup, monkeypatch, tmp_path, capsys):
    from ai_commerce.remote import __main__ as worker_cli
    s = setup
    root = tmp_path / "result"
    root.mkdir()
    monkeypatch.setattr(worker_cli, "verify_registry", lambda p: {})
    monkeypatch.setattr(worker_cli, "load_approved_candidates", lambda p: {})
    monkeypatch.setattr(worker_cli, "validate_batch", lambda x, **kw: s.source.request)
    monkeypatch.setattr(worker_cli, "check_source", lambda *a, **kw: None)
    monkeypatch.setattr(worker_cli, "GitHubTransport", lambda *a: SimpleNamespace(comment=lambda *a: None))
    monkeypatch.setattr(worker_cli, "LocalBatchWorker",
                        lambda **kw: SimpleNamespace(root=root, execute_action=lambda *a: None))
    monkeypatch.setattr(worker_cli, "run_schedule", lambda *a, **kw: deepcopy(s.source.reports[120]))
    for key, value in s.env.items():
        if key.startswith("GITHUB_"):
            monkeypatch.setenv(key, value)
    request = tmp_path / "request.json"
    request.write_text("{}")
    calls = []
    def failing_hint(**kw):
        assert json.loads((root / "completion_event.json").read_bytes()) == kw["event"]
        assert json.loads((root / "batch_result.json").read_bytes()) == kw["report"]
        calls.append(kw)
        raise OSError("PRIVATE_HINT_FAILURE")
    monkeypatch.setattr(worker_cli, "start_worker_signal", failing_hint)
    args = ["run", "--request-file", str(request), "--checkout", str(tmp_path),
            "--issuer-login", "trung-via", "--owner-login", "trung-via", "--issue-number", "123"]
    assert worker_cli.main(args) == 1  # Fixture batch has a final failed action.
    output = capsys.readouterr()
    assert len(calls) == 1 and "PRIVATE_HINT_FAILURE" not in output.out + output.err
    report = json.loads((root / "batch_result.json").read_bytes())
    assert report["feedback_status"] == "GITHUB_ISSUE_COMMENT_PUBLISHED"
    assert report["status"] == "PARTIAL_OR_FAILED"
    def failed_event(*a, **kw):
        raise ValueError("synthetic invalid event")
    monkeypatch.setattr(worker_cli, "build_completion_event", failed_event)
    assert worker_cli.main(args) == 1
    assert len(calls) == 1  # No hint when event emission failed.


@pytest.mark.parametrize("gate", ["valid", "missing_resolve", "missing_deliver", "wrong_sid",
                                 "expired", "bad_signature", "stale_registry", "unsafe_cdp"])
def test_real_d31_d32_d2_d4_composition_uses_two_grants_and_holds_unknown(setup, monkeypatch, gate):
    from ai_commerce.remote.cdp_doorbell import LocalChromeDoorbellAdapter
    from test_ef02_cdp_doorbell import FakePage, FakeInventory, URL
    s = setup
    options = delivery(s, monkeypatch)
    origin = local_wake_entry.SignedOriginConsentAuthority
    monkeypatch.setattr(local_wake_entry, "SignedOriginConsentAuthority",
                        lambda **kw: origin(**kw, clock=lambda: s.clock.wall))
    principal = handoff._private_identity(s.private)
    key = b"11" * 32
    event = s.source.events[120]
    scope = {k: event[k] for k in ("repository", "batch_id", "source_sha", "issue_number")}
    registry = {"version": 1, "routes": [
        {"route_id": "route-A", "generation": 1, "chat_url": URL,
         "cdp_endpoint": "http://127.0.0.1:9222", "consent": True}],
        "claims": [scope | {"route_id": "route-A", "generation": 1}]}
    def signed(operation, claim):
        grant = {"kind": "AI_COMMERCE_SIGNED_ORIGIN_CONSENT", "version": 1,
                 "caller_id": options["caller_id"], "principal": principal,
                 "operation": operation, "scope": claim, "registry_sha256": registry_sha256(registry),
                 "valid_until": 2000000000, "human_consent": True}
        if gate == "wrong_sid":
            grant["principal"] = "different-principal"
        if gate == "expired":
            grant["valid_until"] = 1
        if gate == "stale_registry":
            grant["registry_sha256"] = "b" * 64
        raw = json.dumps(grant, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        grant["signature"] = hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()
        if gate == "bad_signature":
            grant["signature"] = "0" * 64
        return grant
    grants = [signed("resolve_claim_route", scope), signed("deliver_wake", {"event": event})]
    if gate == "missing_resolve":
        grants = [grants[1], grants[1]]
    if gate == "missing_deliver":
        grants = [grants[0], grants[0]]
    write_private(options["registry"], registry)
    write_private(options["grants"], {"version": 1, "grants": grants})
    page = FakePage(gate="DRAFT_PRESENT" if gate == "unsafe_cdp" else "READY")
    inventory = FakeInventory(page)
    monkeypatch.setattr(local_wake_entry, "LocalChromeDoorbellAdapter",
                        lambda **kw: LocalChromeDoorbellAdapter(allow_submit=True, inventory=inventory))
    enroll(s, mode="deliver")
    emit(s)
    outcome = listen(s, deliver=True, delivery=options)
    assert outcome["status"] == "UNKNOWN"
    assert outcome["chat_delivery_proven"] is outcome["brain_continuation_proven"] is False
    assert page.counts["click"] == (1 if gate == "valid" else 0)
    if gate == "valid":
        assert options["idempotency"].exists()
        assert "UNKNOWN" in options["idempotency"].read_text()
    before = len(s.source.calls)
    assert listen(s, deliver=True, delivery=options)["error_code"] == "HANDOFF_ALREADY_CONSUMED"
    assert len(s.source.calls) == before
