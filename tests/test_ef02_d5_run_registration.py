"""D5.3 enrollment/registration integration with synthetic GitHub artifacts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
import zipfile

import pytest

from ai_commerce.remote import local_wake_trigger as trigger
from ai_commerce.remote import local_run_registration as registration
from ai_commerce.remote.completion_event import build_completion_event
from ai_commerce.remote.github_read_retry import GitHubReadBudget
from ai_commerce.remote.local_run_registration import (
    RegistrationIntent, RegistrationStore, RegistrationBlocked, IssueRunRegistrar,
    wait_for_registration,
)
from ai_commerce.remote.validation import fingerprint
from ai_commerce.remote.wake_adapter import WakeReceipt

ROOT = Path(__file__).resolve().parents[1]
REPO = "trung-via/ai-commerce"
SHA = "a" * 40
WORKFLOW = 399104102
AFTER = "2026-10-30T00:00:00Z"
BEFORE = "2026-10-30T00:10:00Z"
NOW = datetime(2026, 10, 30, 0, 11, tzinfo=timezone.utc).timestamp()
Reader = trigger.GitHubExactRunReader  # Stable factory even when CLI composition is patched.


@pytest.fixture(autouse=True)
def no_real_transport(monkeypatch):
    from ai_commerce.remote import github_artifact_authority
    monkeypatch.setattr(github_artifact_authority, "_fetch",
                        lambda *a, **k: pytest.fail("unexpected real network"))
    # The CLI imports this composition root at invocation time. Pin its wall
    # clock too, so a later CI date cannot expire our synthetic Issue window.
    monkeypatch.setattr(registration, "IssueRunRegistrar", lambda **options:
                        IssueRunRegistrar(**(options | {"wall_clock": lambda: NOW})))


def zipped(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path, json.dumps(data) if isinstance(data, dict) else data)
    return stream.getvalue()


class GitHub:
    def __init__(self):
        self.request = {
            "kind": "AI_COMMERCE_REMOTE_ACTION_BATCH", "version": 1,
            "batch_id": "registered-batch", "source_sha": SHA, "actor": "HUMAN",
            "authorization_ref": "human-fixture", "max_parallel": 1,
            "actions": [{"action_id": "D5", "attempt_id": "attempt-1", "executor": "codex",
                "model": "gpt-6.1-sol", "effort": "max", "instruction": "fixture instruction",
                "allowed_paths": ["src/ai_commerce/remote"], "verification_profile": "project-tests",
                "depends_on": []}],
        }
        self.issue = {"number": 123, "html_url": f"https://github.com/{REPO}/issues/123",
                      "title": "[EF02] fixture", "user": {"login": "trung-via"}}
        self.runs, self.artifacts, self.archives, self.events, self.reports = {}, {}, {}, {}, {}
        self.calls, self.overrides, self.list_override = [], {}, None
        self.add_run(120)

    def add_run(self, run_id, *, issue_number=123, status="completed", request=None):
        request = request or self.request
        self.runs[run_id] = {
            "id": run_id, "run_attempt": 1, "workflow_id": WORKFLOW, "event": "issues",
            "status": status, "repository": {"full_name": REPO},
            "created_at": "2026-10-30T00:05:00Z", "actor": {"login": "trung-via"},
            "triggering_actor": {"login": "trung-via"},
            "display_title": f"EF02 Issue #{issue_number}", "head_sha": "b" * 40,
        }
        report = {"kind": "AI_COMMERCE_REMOTE_BATCH_RESULT", "version": 1,
            "batch_id": request["batch_id"], "source_sha": request["source_sha"],
            "status": "PARTIAL_OR_FAILED", "total": 1, "succeeded": 0,
            "actions": [{"action_id": "D5", "attempt_id": "attempt-1", "status": "VERIFICATION_FAILED"}],
            "github_workflow_run_id": str(run_id), "github_issue_number": issue_number}
        event = build_completion_event(report, repository=REPO, run_id=run_id,
                                       run_attempt=1, issue_number=issue_number)
        report.update(wake_event_id=event["event_id"], wake_event_status="RECORDED_UNBOUND")
        self.events[run_id], self.reports[run_id] = event, report
        self.artifacts[run_id] = []
        self.put_archive(run_id, run_id * 10 + 1, f"ef02-request-{issue_number}",
                         zipped({"request.json": request}))
        self.put_archive(run_id, run_id * 10 + 2, f"ef02-results-{run_id}-1",
            zipped({request["batch_id"] + "/completion_event.json": event,
                    request["batch_id"] + "/batch_result.json": report}))

    def put_archive(self, run_id, artifact_id, name, raw):
        self.archives[artifact_id] = raw
        self.artifacts[run_id] = [a for a in self.artifacts[run_id] if a["id"] != artifact_id]
        self.artifacts[run_id].append({"id": artifact_id, "name": name, "expired": False,
            "digest": "sha256:" + hashlib.sha256(raw).hexdigest(), "workflow_run": {"id": run_id}})

    def json(self, url, token):
        assert token == "fixture-read-token"
        self.calls.append(url)
        path = urlsplit(url).path.removeprefix("/repos/" + REPO)
        if path in self.overrides:
            return deepcopy(self.overrides[path])
        if path == "":
            return {"full_name": REPO, "owner": {"login": "trung-via"}}
        if path == f"/actions/workflows/{WORKFLOW}":
            return {"id": WORKFLOW, "state": "active", "path": ".github/workflows/ef02-batch.yml"}
        if path == "/issues/123":
            return deepcopy(self.issue | {"body": self.issue.get("body", json.dumps(self.request))})
        if path == f"/actions/workflows/{WORKFLOW}/runs":
            return deepcopy(self.list_override if self.list_override is not None else
                            {"total_count": len(self.runs), "workflow_runs": [{"id": n} for n in self.runs]})
        if path.startswith("/actions/runs/"):
            run_id = int(path.split("/")[3])
            if path.endswith("/artifacts"):
                return {"total_count": len(self.artifacts[run_id]), "artifacts": deepcopy(self.artifacts[run_id])}
            return deepcopy(self.runs[run_id])
        pytest.fail("unmocked JSON path")

    def archive(self, url, token):
        assert token == "fixture-read-token"
        self.calls.append(url)
        return self.archives[int(urlsplit(url).path.split("/")[-2])]

    def reader(self, **options):
        return Reader(token_provider=lambda: "fixture-read-token", read_json=self.json,
                      read_archive=self.archive, **options)

    def intent(self, **options):
        return replace(RegistrationIntent(REPO, 123, self.request["batch_id"], SHA, WORKFLOW, 1,
            "trung-via", self.request["actor"], fingerprint(self.request), AFTER, BEFORE), **options)


def make_registrar(tmp_path, source=None, *, intent=None, budget=None, **options):
    source = source or GitHub()
    private = tmp_path / "private"
    private.mkdir(exist_ok=True)
    store = RegistrationStore(private / "registration.json", ROOT)
    registrar = IssueRunRegistrar(reader=source.reader(read_budget=budget), intent=intent or source.intent(),
        store=store, repository_root=ROOT, wall_clock=lambda: NOW, **options)
    return registrar, source, store


def test_authenticated_request_and_result_register_exact_run_and_minimal_private_state(tmp_path):
    reg, source, store = make_registrar(tmp_path)
    target, event, report = reg.resolve()
    assert target == source.intent().target(120)
    assert event == source.events[120] and report == source.reports[120]
    data = json.loads(store.state.path.read_bytes())
    assert data["intent"] == asdict(source.intent()) and data["run_id"] == 120
    assert data["event_id"] == event["event_id"]
    assert set(data) == {"version", "intent", "run_id", "event_id", "request_artifact_id", "request_digest"}
    raw = store.state.path.read_text()
    assert all(value not in raw for value in ["fixture-read-token", "fixture instruction", "chatgpt", "grants"])
    assert source.runs[120]["head_sha"] != SHA  # Workflow HEAD is not the immutable requested source.


def test_saved_registration_is_idempotent_and_never_scans_newer_runs(tmp_path):
    reg, source, store = make_registrar(tmp_path)
    first = reg.resolve()
    before = store.state.path.stat().st_mtime_ns
    source.add_run(121)
    source.calls.clear()
    assert reg.resolve() == first
    assert store.state.path.stat().st_mtime_ns == before
    assert not any("/runs?" in url for url in source.calls)
    assert not any("/runs/121" in url for url in source.calls)
    with pytest.raises(RegistrationBlocked, match="CONFLICT"):
        reg.resolve(run_id=121)


def test_ambiguous_matching_runs_never_choose_newest(tmp_path):
    source = GitHub()
    source.add_run(121)
    reg, _, store = make_registrar(tmp_path, source)
    with pytest.raises(RegistrationBlocked, match="MATCHING_RUNS_AMBIGUOUS"):
        reg.resolve()
    assert not store.state.path.exists()


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending", "requested"])
def test_unfinished_competitor_prevents_registration(tmp_path, status):
    source = GitHub()
    source.add_run(121, status=status)
    reg, _, store = make_registrar(tmp_path, source)
    assert reg.resolve() is None
    assert not store.state.path.exists()


def test_forged_display_title_cannot_substitute_for_issue_artifact(tmp_path):
    source = GitHub()
    source.runs.clear()
    source.add_run(121, issue_number=999)
    source.runs[121]["display_title"] = "EF02 Issue #123"
    reg, _, store = make_registrar(tmp_path, source)
    assert reg.resolve() is None
    assert not store.state.path.exists()
    with pytest.raises(RegistrationBlocked, match="REQUEST_ISSUE_MISMATCH"):
        reg.resolve(run_id=121)


def test_genuine_artifacts_work_even_with_misleading_display_name(tmp_path):
    reg, source, _ = make_registrar(tmp_path)
    source.runs[120]["display_title"] = "newest fake name"
    assert reg.resolve()[0].run_id == 120


@pytest.mark.parametrize("change", [{"run_attempt": 2}, {"run_attempt": True}, {"workflow_id": 1},
    {"id": 121}, {"repository": {"full_name": "other/repo"}}, {"event": "workflow_dispatch"},
    {"created_at": "2026-10-29T00:05:00Z"}, {"actor": {"login": "attacker"}},
    {"triggering_actor": {"login": "attacker"}}, {"status": "stopped"}])
def test_bad_run_identity_stops_without_rescanning(tmp_path, change):
    reg, source, store = make_registrar(tmp_path)
    source.runs[120].update(change)
    with pytest.raises(RegistrationBlocked):
        wait_for_registration(reg, max_polls=3)
    assert sum("/runs?" in url for url in source.calls) == 1
    assert not store.state.path.exists()


@pytest.mark.parametrize("change", [{"number": 124}, {"user": {"login": "attacker"}},
    {"html_url": "https://github.com/other/repo/issues/123"}, {"pull_request": {}},
    {"title": "ordinary issue"}, {"body": "{}"}, {"body": '{"actor":"HUMAN","actor":"BRAIN"}'}])
def test_issue_identity_and_edited_or_malformed_request_fail_closed(tmp_path, change):
    reg, source, store = make_registrar(tmp_path)
    source.issue.update(change)
    with pytest.raises(RegistrationBlocked):
        reg.resolve()
    assert not store.state.path.exists()
    assert not any("/runs?" in url for url in source.calls)


@pytest.mark.parametrize("field,value", [("batch_id", "other"), ("source_sha", "b" * 40),
    ("actor", "BRAIN"), ("authorization_ref", "changed")])
def test_immutable_request_snapshot_must_match_original_fingerprint(tmp_path, field, value):
    reg, source, store = make_registrar(tmp_path)
    request = deepcopy(source.request)
    request[field] = value
    source.put_archive(120, 1201, "ef02-request-123", zipped({"request.json": request}))
    with pytest.raises(RegistrationBlocked, match="REQUEST_IDENTITY_MISMATCH"):
        reg.resolve()
    assert not store.state.path.exists()


def test_brain_issuer_requires_operator_local_allowlist(tmp_path):
    source = GitHub()
    source.request["actor"] = "BRAIN"
    source.issue["user"]["login"] = "trusted-bot"
    source.add_run(120, request=source.request)
    intent = source.intent(issuer_login="trusted-bot")
    reg, _, store = make_registrar(tmp_path, source, intent=intent)
    with pytest.raises(RegistrationBlocked, match="AUTHORIZATION_INVALID"):
        reg.resolve()
    assert not store.state.path.exists()
    reg.brain_issuers = ("trusted-bot",)
    assert reg.resolve()[0].run_id == 120


def test_human_actor_cannot_be_enrolled_by_nonowner_even_if_locally_named(tmp_path):
    source = GitHub()
    source.issue["user"]["login"] = "attacker"
    reg, _, store = make_registrar(tmp_path, source, intent=source.intent(issuer_login="attacker"),
                                   brain_issuers=("attacker",))
    with pytest.raises(RegistrationBlocked, match="AUTHORIZATION_INVALID"):
        reg.resolve()
    assert not store.state.path.exists()


@pytest.mark.parametrize("listing", [{"total_count": 101, "workflow_runs": []},
    {"total_count": 2, "workflow_runs": [{"id": 120}]},
    {"total_count": 2, "workflow_runs": [{"id": 120}, {"id": 120}]},
    {"total_count": True, "workflow_runs": [{"id": 120}]},
    {"total_count": 1, "workflow_runs": [{"id": True}]}])
def test_bounded_snapshot_must_be_complete_and_unique(tmp_path, listing):
    reg, source, store = make_registrar(tmp_path)
    source.list_override = listing
    with pytest.raises(RegistrationBlocked, match="RUN_LIST_INCOMPLETE_OR_OVER_BUDGET"):
        reg.resolve()
    assert not store.state.path.exists()


def test_list_budget_and_window_bounds_are_enforced(tmp_path):
    reg, source, _ = make_registrar(tmp_path, max_runs=1)
    source.add_run(121)
    with pytest.raises(RegistrationBlocked, match="OVER_BUDGET"):
        reg.resolve()
    reg.wall_clock = lambda: NOW + 8 * 86400
    with pytest.raises(RegistrationBlocked, match="WINDOW_STALE_OR_OPEN"):
        reg.resolve()
    reg.wall_clock = lambda: NOW - 3600
    with pytest.raises(RegistrationBlocked, match="WINDOW_STALE_OR_OPEN"):
        reg.resolve()


@pytest.mark.parametrize("options", [{"repository": "other/repo"}, {"issue_number": True},
    {"run_attempt": 0}, {"workflow_id": 0}, {"source_sha": "latest"}, {"batch_id": "../unsafe"},
    {"request_fingerprint": "unknown"}, {"issuer_login": "bad/issuer"}, {"actor": "UNKNOWN"},
    {"created_before": AFTER}, {"created_after": "2026-10-20T00:00:00Z"}])
def test_missing_or_malformed_registration_is_rejected(options):
    with pytest.raises(RegistrationBlocked):
        GitHub().intent(**options)


@pytest.mark.parametrize("kind", ["expired", "duplicate", "run", "digest", "zip", "json", "duplicate_json"])
def test_request_artifact_tampering_never_persists(tmp_path, kind):
    reg, source, store = make_registrar(tmp_path)
    item = source.artifacts[120][0]
    if kind == "expired":
        item["expired"] = True
    elif kind == "duplicate":
        source.put_archive(120, 1203, item["name"], source.archives[1201])
    elif kind == "run":
        item["workflow_run"] = {"id": 121}
    elif kind == "digest":
        item["digest"] = "sha256:" + "0" * 64
    else:
        raw = b"invalid zip" if kind == "zip" else zipped({"request.json":
            b"not json" if kind == "json" else b'{"actor":"HUMAN","actor":"BRAIN"}'})
        source.put_archive(120, 1201, item["name"], raw)
    with pytest.raises(RegistrationBlocked):
        reg.resolve()
    assert not store.state.path.exists()


@pytest.mark.parametrize("change", [{"issue_number": 124}, {"source_sha": "b" * 40},
    {"batch_id": "other"}, {"run_attempt": 2}, {"run_id": 121}])
def test_completion_event_mismatch_is_not_a_discovery_hint(tmp_path, change):
    reg, source, store = make_registrar(tmp_path)
    event = source.events[120] | change
    source.put_archive(120, 1202, "ef02-results-120-1", zipped({
        "registered-batch/completion_event.json": event,
        "registered-batch/batch_result.json": source.reports[120]}))
    with pytest.raises(RegistrationBlocked):
        wait_for_registration(reg, max_polls=2)
    assert sum("/runs?" in url for url in source.calls) == 1
    assert not store.state.path.exists()


def test_report_action_attempts_must_match_original_request(tmp_path):
    reg, source, store = make_registrar(tmp_path)
    report = deepcopy(source.reports[120])
    report["actions"][0]["attempt_id"] = "other-attempt"
    event = build_completion_event(report, repository=REPO, run_id=120, run_attempt=1, issue_number=123)
    report["wake_event_id"] = event["event_id"]
    source.put_archive(120, 1202, "ef02-results-120-1", zipped({
        "registered-batch/completion_event.json": event, "registered-batch/batch_result.json": report}))
    with pytest.raises(RegistrationBlocked, match="REQUEST_ACTIONS_MISMATCH"):
        reg.resolve()
    assert not store.state.path.exists()


def test_delayed_result_upload_uses_finite_read_only_polls(tmp_path):
    source = GitHub()
    result_item = source.artifacts[120].pop()
    ticks, now = [], [0.0]
    def sleep(delay):
        ticks.append(delay)
        now[0] += delay
        source.artifacts[120].append(result_item)
    budget = GitHubReadBudget(clock=lambda: now[0], sleep=sleep)
    reg, _, _ = make_registrar(tmp_path, source, budget=budget)
    assert wait_for_registration(reg, max_polls=2, interval_seconds=1)[0].run_id == 120
    assert ticks == [1]


def test_unready_exhaustion_writes_no_registration_and_does_not_send(tmp_path):
    source = GitHub()
    source.runs[120]["status"] = "in_progress"
    ticks, now = [], [0.0]
    def sleep(delay):
        ticks.append(delay)
        now[0] += delay
    reg, _, store = make_registrar(tmp_path, source,
        budget=GitHubReadBudget(clock=lambda: now[0], sleep=sleep))
    with pytest.raises(RegistrationBlocked, match="EVIDENCE_NOT_READY"):
        wait_for_registration(reg, max_polls=2, interval_seconds=1)
    assert ticks == [1] and not store.state.path.exists()


@pytest.mark.parametrize("mutate", [lambda d: d.update(run_id=121), lambda d: d.update(extra="unsafe"),
    lambda d: d.update(version=True), lambda d: d.update(event_id="bad"),
    lambda d: d["intent"].update(run_attempt=True),
    lambda d: d["intent"].update(source_sha="b" * 40)])
def test_malformed_or_conflicting_private_record_cannot_be_adopted(tmp_path, mutate):
    reg, source, store = make_registrar(tmp_path)
    reg.resolve()
    record = json.loads(store.state.path.read_bytes())
    mutate(record)
    store.state.path.write_text(json.dumps(record))
    source.calls.clear()
    with pytest.raises(RegistrationBlocked):
        reg.resolve()
    assert not any("/runs?" in url for url in source.calls)


def test_private_registration_rejects_checkout_and_source_locations(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    with pytest.raises(RegistrationBlocked, match="LOCATION_INVALID"):
        RegistrationStore(checkout / "registration.json", checkout)
    with pytest.raises(RegistrationBlocked, match="LOCATION_INVALID"):
        RegistrationStore(ROOT / "registration.json", checkout)


def test_atomic_write_failure_leaves_no_partial_registration(tmp_path, monkeypatch):
    from ai_commerce.remote import _private_state
    reg, _, store = make_registrar(tmp_path)
    monkeypatch.setattr(_private_state.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("fixture")))
    with pytest.raises(RegistrationBlocked, match="STATE_UNAVAILABLE"):
        reg.resolve()
    assert not store.state.path.exists()
    assert not list(store.state.path.parent.glob("*.part"))


def test_concurrent_idempotent_registration_keeps_one_record(tmp_path):
    reg, source, store = make_registrar(tmp_path)
    reg.resolve()
    record = store.read(source.intent())
    before = store.state.path.stat().st_mtime_ns
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.register(source.intent(), record), range(2)))
    assert results == [record, record] and store.state.path.stat().st_mtime_ns == before


def cli_args(tmp_path, source, command=None):
    private = tmp_path / "private"
    private.mkdir(exist_ok=True)
    return ([] if command is None else [command]) + [
        "--enroll", "--checkout", str(ROOT), "--registration", str(private / "registration.json"),
        "--run-attempt", "1", "--workflow-id", str(WORKFLOW), "--issue-number", "123",
        "--batch-id", "registered-batch", "--source-sha", SHA,
        "--issuer-login", "trung-via", "--actor", "HUMAN",
        "--request-fingerprint", fingerprint(source.request),
        "--created-after", AFTER, "--created-before", BEFORE, "--max-polls", "1"]


def test_enrolled_cli_defaults_to_observe_with_no_browser_or_post(tmp_path, monkeypatch, capsys):
    source = GitHub()
    monkeypatch.setenv("AI_COMMERCE_GITHUB_READ_TOKEN", "fixture-read-token")
    monkeypatch.setattr(trigger, "GitHubExactRunReader", lambda **k: source.reader(read_budget=k["read_budget"]))
    monkeypatch.setattr(trigger, "send_one", lambda **k: pytest.fail("unexpected send"))
    assert trigger.main(cli_args(tmp_path, source)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "CANDIDATE_READY_NOT_SENT" and result["registration_status"] == "REGISTERED"
    assert result["browser_accessed"] is False and result["brain_continuation_proven"] is False


def test_deliver_gate_is_checked_before_enrollment_or_network(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED", raising=False)
    source = GitHub()
    monkeypatch.setattr(trigger, "GitHubExactRunReader", lambda **k: pytest.fail("unexpected read"))
    assert trigger.main(cli_args(tmp_path, source, "deliver")) == 2
    assert "EF02_LOCAL_TRIGGER_BLOCKED" in capsys.readouterr().err
    assert not (tmp_path / "private/registration.json").exists()


def test_explicit_deliver_hands_off_once_and_unknown_never_retries(tmp_path, monkeypatch, capsys):
    source, calls = GitHub(), []
    env = {"AI_COMMERCE_GITHUB_READ_TOKEN": "fixture-read-token",
           "AI_COMMERCE_WAKE_HMAC_KEY_HEX": "11" * 32, "AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED": "true"}
    monkeypatch.setattr(trigger, "os", SimpleNamespace(name="nt", environ=env))
    monkeypatch.setattr(trigger, "_windows_process_sid", lambda: "S-1-5-21-1")
    monkeypatch.setattr(trigger, "GitHubExactRunReader", lambda **k: source.reader(read_budget=k["read_budget"]))
    def send(**options):
        calls.append(options)
        return WakeReceipt(event_id=options["event"]["event_id"], route_id="fixture-route", generation=1,
                           delivery_status="UNKNOWN", error_code="UI_USER_TURN_OBSERVED")
    monkeypatch.setattr(trigger, "send_one", send)
    args = cli_args(tmp_path, source, "deliver") + ["--registry", str(tmp_path / "private/routes.json"),
        "--idempotency", str(tmp_path / "private/delivery.json"), "--grants", str(tmp_path / "private/grants.json"),
        "--caller-id", "local-operator"]
    assert trigger.main(args) == 3
    assert len(calls) == 1 and calls[0]["event"] == source.events[120]
    assert calls[0]["workflow_id"] == WORKFLOW and isinstance(calls[0]["read_budget"], GitHubReadBudget)
    assert calls[0]["principal_provider"]() == "S-1-5-21-1"
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "UNKNOWN" and result["chat_delivery_proven"] is False


def test_registration_identity_failure_never_hands_off_to_send(tmp_path, monkeypatch, capsys):
    source = GitHub()
    source.add_run(121)
    monkeypatch.setenv("AI_COMMERCE_GITHUB_READ_TOKEN", "fixture-read-token")
    monkeypatch.setattr(trigger, "GitHubExactRunReader", lambda **k: source.reader(read_budget=k["read_budget"]))
    monkeypatch.setattr(trigger, "send_one", lambda **k: pytest.fail("unexpected send"))
    assert trigger.main(cli_args(tmp_path, source)) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED"
    assert not (tmp_path / "private/registration.json").exists()
