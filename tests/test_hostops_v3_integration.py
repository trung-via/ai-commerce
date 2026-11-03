"""Transport, immutable installed-release, native local IPC, and schema integration."""
from __future__ import annotations

import base64
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import jsonschema
import pytest

from ai_commerce.hostops_v3 import auth, broker, cli, contract as c, security, transport
from ai_commerce.hostops_v3.journal import Journal
from ai_commerce.hostops_v3.worker import Worker
from test_hostops_v3 import ROOT, NOW, h, keys, result


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_versioned_schemas_agree_with_runtime_and_no_unknown_fields(h):
    schemas = load_script("hostops_v3_schemas").schemas()
    for name, schema in schemas.items():
        assert json.loads((ROOT / "contracts" / ("hostops-v3." + name + ".schema.json")).read_text()) == schema
        jsonschema.Draft202012Validator.check_schema(schema)
    e = h.make()
    jsonschema.validate(e["request"], schemas["request"])
    jsonschema.validate(e["lease"], schemas["lease"])
    jsonschema.validate(result(h.execute(e)), schemas["receipt"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**e["request"], "shell": "arbitrary"}, schemas["request"])


def test_cli_structure_validation_and_sanitized_report(h, capsys):
    request, output = h.root / "request.json", h.root / "validation.json"
    request.write_bytes(c.canonical(h.make()))
    assert cli.main(["validate", "--request-file", str(request), "--output", str(output)]) == 0
    assert c.decode(output.read_bytes())["status"] == "STRUCTURE_ONLY"
    request.write_bytes(b'{"private-content":"must-not-be-echoed"}')
    assert cli.main(["validate", "--request-file", str(request), "--output", str(output)]) == 2
    assert "must-not-be-echoed" not in str(capsys.readouterr())


def test_installed_signed_release_real_process_and_tamper_rejected(h):
    release = load_script("hostops_v3_release")
    for name in release.FILES:
        target = h.release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    manifest = release.build(h.release, h.manifest["source_sha"], "b" * 64)
    body = {k: v for k, v in manifest.items() if k != "release_id"}
    (h.release / "release.json").write_bytes(c.canonical(manifest))
    (h.release / "release.sig.b64").write_text(h.sign(body, "human", "hostops-v3-release"))
    config = h.root / "config.json"
    config.write_bytes(c.canonical(h.config))
    # The copied fixture is not an installed service or laptop policy change.
    code = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
            "from ai_commerce.hostops_v3.auth import verify_release;"
            "verify_release(json.load(open(sys.argv[2])));print('PIN_VERIFIED')")
    argv = [sys.executable, "-I", "-B", "-c", code, str(h.release / "src"), str(config)]
    ok = subprocess.run(argv, capture_output=True, timeout=15)
    assert ok.returncode == 0, ok.stderr.decode(errors="replace")
    (h.release / "src/ai_commerce/hostops_v3/worker.py").write_text("raise RuntimeError('tampered')")
    bad = subprocess.run(argv, capture_output=True, timeout=15)
    assert bad.returncode != 0 and b"SOURCE_UNVERIFIED" in bad.stderr


class FakeAPI:
    def __init__(self, e, workflow=b"reviewed workflow", source="f" * 40):
        self.e, self.workflow, self.source, self.posts = e, workflow, source, []
        self.repo = {"private": True, "full_name": c.REPOSITORY}
        self.run = {"event": "issues", "status": "completed", "conclusion": "success", "path": transport.WORKFLOW_PATH,
                    "display_title": "HostOpsV3 Issue #999", "head_repository": {"full_name": c.REPOSITORY}, "head_sha": source}
        self.issue = {"user": {"login": "trung-via"}, "title": "[HOSTOPS3] fixture", "body": c.canonical(e).decode()}

    def get(self, path):
        if path.endswith(c.REPOSITORY):
            return self.repo
        if "/issues/999" in path:
            return self.issue
        if "/actions/runs/123" in path:
            return self.run
        if "/contents/" in path:
            assert path.endswith("?ref=" + self.source)
            return {"content": base64.b64encode(self.workflow).decode()}
        raise AssertionError(path)

    def post(self, path, data):
        self.posts.append((path, data))
        return {"id": 1}


def test_simulated_github_to_broker_to_receipt_with_main_drift(h):
    e = h.write(data=b"fixture bridge")
    api = FakeAPI(e)
    manifest = {**h.manifest, "workflow_sha256": c.digest(api.workflow)}
    # Product main ffffff differs from signed installed code source, intentionally.
    value = transport.poll_once(api, h.config, manifest, 999, 123, send=lambda _, raw: h.worker.execute(raw))
    assert value["status"] == "APPLIED" and (h.data / "new.txt").read_bytes() == b"fixture bridge"
    assert len(api.posts) == 1 and "fixture bridge" not in api.posts[0][1]["body"]
    # Lost API response/repeat does not cause another file effect.
    again = transport.poll_once(api, h.config, manifest, 999, 123, send=lambda _, raw: h.worker.execute(raw))
    assert again == value


@pytest.mark.parametrize("failure", ["public", "issuer", "fork", "workflow", "digest", "event", "failed"])
def test_github_provenance_denials_before_broker(h, failure):
    api = FakeAPI(h.make())
    expected = c.digest(api.workflow)
    if failure == "public": api.repo["private"] = False
    if failure == "issuer": api.issue["user"]["login"] = "attacker"
    if failure == "fork": api.run["head_repository"]["full_name"] = "attacker/fork"
    if failure == "workflow": api.run["path"] = ".github/workflows/ef02-batch.yml"
    if failure == "digest": expected = "0" * 64
    if failure == "event": api.run["event"] = "pull_request"
    if failure == "failed": api.run["conclusion"] = "failure"
    with pytest.raises(c.Denied):
        transport.poll_once(api, h.config, {**h.manifest, "workflow_sha256": expected}, 999, 123,
                            send=lambda *_: pytest.fail("unverified provenance reached broker"))


def test_explicit_signed_github_disclosure_to_exact_issue(h):
    data = b"Human-approved fixture content"
    p = h.file(content=data)
    selected = result(h.execute(h.make("read", {"path": str(p), "offset": 0, "length": 100,
                                               "expected_sha256": c.digest(data)})))["result"]["selection_id"]
    params = {"selection_id": selected, "content_sha256": c.digest(data), "offset": 0, "length": len(data),
              "classification": "sensitive", "recipient": "github:trung-via/ai-commerce/issues/999"}
    e = h.make("disclose", params, exact=True)
    api = FakeAPI(e)
    out = transport.poll_once(api, h.config, {**h.manifest, "workflow_sha256": c.digest(api.workflow)}, 999, 123,
                              send=lambda _, raw: h.worker.execute(raw))
    assert out["status"] == "DISCLOSED" and base64.b64encode(data).decode() in api.posts[0][1]["body"]
    assert data not in next(h.state.glob("receipt-*.json")).read_bytes()
    api = FakeAPI(h.make("disclose", {**params, "recipient": "github:elsewhere"}, exact=True))
    with pytest.raises(c.Denied, match="DISCLOSURE_RECIPIENT_DENIED"):
        transport.poll_once(api, h.config, {**h.manifest, "workflow_sha256": c.digest(api.workflow)}, 999, 123)


def test_windows_pipe_nowait_requires_real_connection(monkeypatch):
    """A successful first nonblocking ConnectNamedPipe is NOT an attached peer."""
    from types import SimpleNamespace

    class Connect:
        def __init__(self):
            self.responses = [(True, 0), (False, 536), (False, 535)]
            self.last_error = None
            self.argtypes = None
            self.restype = None

        def __call__(self, _handle, _overlapped):
            ready, self.last_error = self.responses.pop(0)
            return ready

    connect = Connect()
    channel = SimpleNamespace(kernel=SimpleNamespace(ConnectNamedPipe=connect), handle=123)
    monkeypatch.setattr(broker.ctypes, "get_last_error", lambda: connect.last_error, raising=False)
    monkeypatch.setattr(broker.time, "sleep", lambda _: None)
    assert broker.windows_wait_connected(channel, timeout=5) is True
    assert not connect.responses  # Must consume both LISTENING and CONNECTED states.


def test_windows_pipe_nowait_fail_closed_on_unexpected_error(monkeypatch):
    """Do not interpret broken/disconnected pipes as an authorized client."""
    from types import SimpleNamespace

    class Connect:
        argtypes = None
        restype = None

        def __call__(self, _handle, _overlapped):
            return False

    channel = SimpleNamespace(kernel=SimpleNamespace(ConnectNamedPipe=Connect()), handle=123)
    monkeypatch.setattr(broker.ctypes, "get_last_error", lambda: 232, raising=False)
    with pytest.raises(c.Denied, match="PIPE_UNAVAILABLE"):
        broker.windows_wait_connected(channel, timeout=5)


def test_native_local_ipc_mutual_hmac_and_bounds(tmp_path, record_property):
    key, errors = os.urandom(32), []
    expected = c.receipt(None, "OBSERVED", result={"count": 1})
    config = None
    if os.name == "nt":
        identity, elevated = security.windows_identity()
        endpoint = "\\\\.\\pipe\\LOCAL\\AICommerceHostOpsV3-test-" + os.urandom(8).hex()
        config = {"endpoint": endpoint, "broker_identity": identity, "transport_identity": identity, "trusted_owners": []}
        server = broker.windows_listener(config)
    else:
        endpoint = str(tmp_path / "native.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(endpoint)
        listener.listen(1)
    stop = threading.Event()
    def serve():
        try:
            if os.name == "nt":
                if not broker.windows_wait_connected(server, stop=stop.is_set, timeout=5):
                    return
                broker.windows_peer(server, config)
                channel = server
            else:
                connection, _ = listener.accept()
                channel = broker.UnixChannel(connection)
            try:
                broker.server_exchange(channel, key, lambda raw: {"receipt": expected} if raw == b"fixture" else c.deny("BAD_SHAPE"))
            finally:
                channel.close()
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    fallback = False
    try:
        client = broker.connect(endpoint)
    except c.Denied as exc:
        # Some hosted AppContainer policies deny even same-SID named pipes.
        assert os.name == "nt" and str(exc) == "BROKER_UNAVAILABLE" and getattr(exc.__cause__, "winerror", None) == 5
        stop.set()
        thread.join(6)
        server.close()
        assert not thread.is_alive()
        record_property("native_ipc", "OS_DENIED_WINERROR_5; mock HMAC only")
        fallback = True
    else:
        if os.name == "nt" and elevated:
            # GitHub Windows hosted runner may run elevated. The production
            # peer gate MUST refuse it. Prove the actual OS denial before
            # exercising HMAC over an isolated in-memory byte stream.
            thread.join(6)
            client.close()
            assert not thread.is_alive(), "ELEVATED_PEER_DENIAL_THREAD_STUCK"
            assert len(errors) == 1 and isinstance(errors[0], c.Denied)
            assert str(errors[0]) == "IPC_PEER_DENIED"
            server.close()
            record_property("native_ipc", "OS_DENIED_ELEVATED_PEER; mock HMAC only")
            fallback = True

    if fallback:
        import queue
        class Memory(broker.Channel):
            def __init__(self, incoming, outgoing):
                self.incoming, self.outgoing = incoming, outgoing
                self.pending = b""
            def read(self, size):
                if not self.pending:
                    self.pending = self.incoming.get(timeout=5)
                out, self.pending = self.pending[:size], self.pending[size:]
                return out
            def write(self, data):
                self.outgoing.put(data)
            def close(self):
                pass
        incoming, outgoing = queue.Queue(), queue.Queue()
        client, local_server = Memory(incoming, outgoing), Memory(outgoing, incoming)
        errors.clear()
        def memory_serve():
            try:
                broker.server_exchange(local_server, key, lambda _: {"receipt": expected})
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=memory_serve, daemon=True)
        thread.start()
    try:
        try:
            assert broker.client_exchange(client, key, b"fixture") == {"receipt": expected}
        except c.Denied:
            # An early server-side Denied used to be masked by 45s client
            # IPC_TIMEOUT. Report only sanitized codes, never raw payloads.
            thread.join(0.1)
            if errors:
                server_error = errors[0]
                label = str(server_error) if isinstance(server_error, c.Denied) else type(server_error).__name__
                pytest.fail("IPC_SERVER_FAILED: " + label, pytrace=False)
            raise
    finally:
        client.close()
    thread.join(6)
    assert not thread.is_alive() and not errors
    if os.name != "nt":
        listener.close()
    class Oversize(broker.Channel):
        def read_exact(self, _):
            import struct
            return struct.pack("!I", broker.IPC_LIMIT + 1)
    with pytest.raises(c.Denied, match="IPC_FRAME_LIMIT"):
        Oversize().receive()


def test_request_timeout_and_selection_scope(h):
    p = h.file()
    ticks = iter([0, 100])
    h.worker.clock = lambda: next(ticks)
    assert result(h.execute(h.make("hash", {"path": str(p)})))["error_code"] == "TIMEOUT"
    # No deadline or expiry extension is inferred from a GitHub timeout.
    assert not list(h.state.glob("selections/*"))


def test_reconciliation_mode_cannot_start_an_effect(h):
    e = h.write()
    assert result(h.execute(e, reconcile=True))["status"] == "UNKNOWN"
    assert not (h.data / "new.txt").exists()
    message = c.canonical({"schema": "hostops.ipc.v3", "mode": "reconcile", "envelope": e})
    mode, raw = broker.ipc_request(message)
    assert mode == "reconcile" and c.envelope(raw)[1].value == e["request"]


def test_manifest_has_exact_48_unique_existing_test_names():
    import test_hostops_v3 as matrix
    manifest = json.loads((ROOT / "docs/engineering/hostops_v3_evidence/scenarios.json").read_text())
    assert [row["id"] for row in manifest["scenarios"]] == [f"H225-{n:02}" for n in range(1, 49)]
    assert len({row["testcase"] for row in manifest["scenarios"]}) == 48
    for row in manifest["scenarios"]:
        assert callable(getattr(matrix, row["testcase"].split("::")[-1]))
    assert manifest["original_row_text_available"] is False



def test_hostops_v3_hosted_queue_is_digest_only_without_host_execution():
    """Enforce hosted-only issue queue, no laptop executor or payload echo."""
    p = ROOT / ".github" / "workflows" / "hostops-v3.yml"
    text = p.read_text(encoding="utf-8")
    assert text.endswith(chr(10))
    assert 'name: AI Commerce HostOps v3 signed broker queue' in text
    assert 'run-name: "HostOpsV3 Issue #' in text
    # Issue events are now routed by one shared top-level workflow.
    ingress = (ROOT / ".github" / "workflows" / "issue-ingress.yml").read_text(encoding="utf-8")
    assert '  workflow_call:' in text
    assert '  issues:' not in text
    assert '    types: [opened, labeled]' in ingress
    assert 'uses: ./.github/workflows/hostops-v3.yml' in ingress
    assert "github.event.action == 'opened'" in ingress
    assert '  contents: read' in text
    assert '    runs-on: ubuntu-latest' in text
    assert 'HOSTOPS_V3_QUEUED_UNVERIFIED' in text
    assert 'envelope_sha256=' in text
    assert 'github.event.repository.private == true' in text
    assert 'github.event.issue.user.login == github.repository_owner' in text
    for forbidden in ('self-hosted', 'actions/checkout', 'secrets.', 'ssh-keygen -Y',
                      'github.event.issue.body', 'powershell', 'pwsh', 'broker --config',
                      'hostops_v3.py', 'curl', 'wget'):
        assert forbidden not in text


def _v4_fixture(h):
    """Synthetic signed-field shape; NOT a valid v4 broker signature."""
    old = h.make()
    r = old["request"]
    r.pop("repository")
    r.update(schema="hostops.request.v4",
             source_repository=c.SOURCE_REPOSITORY,
             control_repository=c.CONTROL_REPOSITORY)
    old["schema"] = "hostops.envelope.v4"
    old["lease"].update(schema="hostops.lease.v4",
                        source_repository=c.SOURCE_REPOSITORY,
                        control_repository=c.CONTROL_REPOSITORY)
    return old


class _V4API:
    def __init__(self, e, source_sha):
        self.workflow = b"private-only-hosted-queue-pinned"
        self.source_sha = source_sha
        self.control_sha = "e" * 40
        self.repo = {"private": True, "full_name": c.CONTROL_REPOSITORY}
        self.issue = {"number": 999, "title": "[HOSTOPS3] fixture",
                      "user": {"login": "trung-via"},
                      "body": c.canonical(e).decode("ascii")}
        self.run = {"event": "issues", "status": "completed", "conclusion": "success",
                    "path": transport.WORKFLOW_PATH, "display_title": "HostOpsV3 Issue #999",
                    "head_repository": {"full_name": c.CONTROL_REPOSITORY},
                    "head_sha": self.control_sha}
    def get(self, path):
        prefix = "/repos/" + c.CONTROL_REPOSITORY
        if path == prefix:
            return self.repo
        if path == prefix + "/issues/999":
            return self.issue
        if path == prefix + "/actions/runs/123":
            return self.run
        if path == prefix + "/contents/" + transport.WORKFLOW_PATH + "?ref=" + self.control_sha:
            return {"content": base64.b64encode(self.workflow).decode("ascii")}
        raise AssertionError(path)


def test_dual_repo_v4_structure_is_not_v3_broker_authorization(h):
    e = _v4_fixture(h)
    raw = c.canonical(e)
    frozen, request = c.dual_repo_envelope(raw)
    assert request.value["source_repository"] == c.SOURCE_REPOSITORY
    assert request.value["control_repository"] == c.CONTROL_REPOSITORY
    assert request.fingerprint == c.digest(c.canonical(e["request"]))
    assert frozen["schema"] == "hostops.envelope.v4"
    with pytest.raises(c.Denied, match="BAD_VERSION"):
        c.envelope(raw)  # v3 broker remains fail-closed
    with pytest.raises(c.Denied, match="BAD_VERSION"):
        c.dual_repo_envelope(c.canonical(h.make()))


@pytest.mark.parametrize("mutation", [
    "public_control", "wrong_repo", "wrong_issuer", "wrong_issue",
    "fork", "wrong_workflow", "failed_run", "wrong_event",
    "bad_digest", "wrong_source", "wrong_request_repo",
    "wrong_lease_repo", "different_release",
])
def test_dual_repo_v4_control_provenance_fail_closed(h, mutation):
    e = _v4_fixture(h)
    source_sha = e["request"]["source_sha"]
    api = _V4API(e, source_sha)
    digest = c.digest(api.workflow)
    expected_source = source_sha
    if mutation == "public_control": api.repo["private"] = False
    if mutation == "wrong_repo": api.repo["full_name"] = c.SOURCE_REPOSITORY
    if mutation == "wrong_issuer": api.issue["user"]["login"] = "attacker"
    if mutation == "wrong_issue": api.issue["number"] = 1234
    if mutation == "fork": api.run["head_repository"]["full_name"] = c.SOURCE_REPOSITORY
    if mutation == "wrong_workflow": api.run["path"] = ".github/workflows/unsafe.yml"
    if mutation == "failed_run": api.run["conclusion"] = "failure"
    if mutation == "wrong_event": api.run["event"] = "workflow_dispatch"
    if mutation == "bad_digest": digest = "0" * 64
    if mutation == "wrong_source": expected_source = "0" * 40
    if mutation == "wrong_request_repo":
        e["request"]["control_repository"] = c.SOURCE_REPOSITORY
        api.issue["body"] = c.canonical(e).decode("ascii")
    if mutation == "wrong_lease_repo":
        e["lease"]["control_repository"] = c.SOURCE_REPOSITORY
        api.issue["body"] = c.canonical(e).decode("ascii")
    if mutation == "different_release":
        e["lease"]["release_id"] = "0" * 64
        api.issue["body"] = c.canonical(e).decode("ascii")
    with pytest.raises(c.Denied):
        transport.control_provenance(api, 999, 123, digest, expected_source)


def test_dual_repo_v4_private_control_proof_offline_only(h):
    e = _v4_fixture(h)
    api = _V4API(e, e["request"]["source_sha"])
    result = transport.control_provenance(
        api, 999, 123, c.digest(api.workflow), e["request"]["source_sha"])
    assert c.dual_repo_envelope(result)[1].value == e["request"]
    assert api.repo["private"] is True
    # This parser never verifies Human Ed25519 signatures or submits to broker.
    assert not (h.data / "new.txt").exists()


def test_dual_repo_v4_invalid_json_and_api_routing_fail_closed(h):
    e = _v4_fixture(h)
    raw = c.canonical(e)
    with pytest.raises(c.Denied, match="DUPLICATE_KEY"):
        c.dual_repo_envelope(raw.replace(b'"schema":"hostops.envelope.v4"', b'"schema":"hostops.envelope.v4","schema":"hostops.envelope.v4"'))
    with pytest.raises(c.Denied):
        c.dual_repo_envelope(raw.replace(b'"control_repository":"trung-via/ai-commerce-hostops-control"',
                                         b'"control_repository":"attacker/public"'))
    client = transport.ControlGitHub("synthetic-not-used")
    with pytest.raises(c.Denied, match="TRANSPORT_ROUTE_DENIED"):
        client.get("/repos/" + c.SOURCE_REPOSITORY)
    with pytest.raises(c.Denied, match="TRANSPORT_ROUTE_DENIED"):
        client.post("/repos/attacker/not-allowed/issues/1/comments", {"body": "never"})
    for bypass in (
        "/repos/" + c.CONTROL_REPOSITORY + "/../ai-commerce/issues/1",
        "/repos/" + c.CONTROL_REPOSITORY + "/%2e%2e/ai-commerce",
        "/repos/" + c.CONTROL_REPOSITORY + "/issues/1?redirect=/other",
        "/repos/" + c.CONTROL_REPOSITORY + "/actions/workflows/unsafe.yml/runs",
        "https://api.github.com/repos/" + c.CONTROL_REPOSITORY,
    ):
        with pytest.raises(c.Denied, match="TRANSPORT_ROUTE_DENIED"):
            client.get(bypass)
    with pytest.raises(c.Denied, match="TRANSPORT_ROUTE_DENIED"):
        client.post("/repos/" + c.CONTROL_REPOSITORY + "/issues/999",
                    {"body": "never"})




def _v4_signed_fixture(h, *, request_mutation=None, lease_mutation=None,
                       signature_namespaces=None, exact=False):
    """Independent OpenSSH fixture keys never leave pytest tmp_path."""
    rel = load_script("hostops_v3_release")
    for name in rel.FILES:
        target = h.release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    release = rel.build_v4(h.release, h.manifest["source_sha"], "b" * 64)
    (h.release / "release.json").write_bytes(c.canonical(release))
    unsigned = {k: v for k, v in release.items() if k != "release_id"}
    (h.release / "release.sig.b64").write_text(
        h.sign(unsigned, "human", "hostops-v4-release"), encoding="ascii")
    e = _v4_fixture(h)
    e["request"]["release_id"] = release["release_id"]
    e["lease"]["release_id"] = release["release_id"]
    if request_mutation:
        e["request"].update(request_mutation)
    if lease_mutation:
        e["lease"].update(lease_mutation)
    namespaces = signature_namespaces or {}
    e["issuer_signature_b64"] = h.sign(
        e["request"], "issuer", namespaces.get("request", "hostops-v4-request"))
    e["lease_signature_b64"] = h.sign(
        e["lease"], "human", namespaces.get("lease", "hostops-v4-lease"))
    e["action_signature_b64"] = (
        h.sign({"request": e["request"], "lease_sha256": c.digest(c.canonical(e["lease"]))},
               "human", namespaces.get("action", "hostops-v4-exact-action"))
        if exact else "")
    return release, e


def test_v4_cryptographic_signed_release_request_and_lease_offline(h):
    release, e = _v4_signed_fixture(h)
    authority = auth.AuthorityV4(h.config, release, clock=lambda: NOW)
    request, lease = authority.authenticate_v4(c.canonical(e))
    assert request.value == e["request"]
    assert lease == e["lease"]
    assert release["source_repository"] == c.SOURCE_REPOSITORY
    assert release["control_repository"] == c.CONTROL_REPOSITORY
    with pytest.raises(c.Denied):
        c.envelope(c.canonical(e))  # v3 broker rejects v4 without downgrade
    assert not (h.data / "new.txt").exists()


@pytest.mark.parametrize("mutation", [
    "v3_request_signature", "v3_lease_signature", "tampered_request",
    "wrong_source", "wrong_release", "wrong_lease_repo", "wrong_lease_operation",
    "expired_lease", "revoked_lease", "kill_switch", "disabled_config",
    "missing_exact_write", "v3_exact_signature", "valid_exact_write",
])
def test_v4_cryptographic_fail_closed_and_exact_human_write(h, mutation):
    request = {"operation": "create",
               "parameters": {"path": str(h.data / "new.txt"),
                              "data_b64": base64.b64encode(b"fixture").decode(),
                              "data_sha256": c.digest(b"fixture")}}
    exact = mutation in {"v3_exact_signature", "valid_exact_write"}
    ns = {"request": "hostops-v3-request"} if mutation == "v3_request_signature" else (
        {"lease": "hostops-v3-lease"} if mutation == "v3_lease_signature" else (
        {"action": "hostops-v3-exact-action"} if mutation == "v3_exact_signature" else {}))
    release, e = _v4_signed_fixture(h, request_mutation=request,
                                     signature_namespaces=ns, exact=exact)
    if mutation == "tampered_request":
        e["request"]["timeout_seconds"] = 6
    if mutation == "wrong_source":
        e["request"]["source_sha"] = "0" * 40
        e["issuer_signature_b64"] = h.sign(e["request"], "issuer", "hostops-v4-request")
    if mutation == "wrong_release":
        e["request"]["release_id"] = "0" * 64
        e["issuer_signature_b64"] = h.sign(e["request"], "issuer", "hostops-v4-request")
    if mutation == "wrong_lease_repo":
        e["lease"]["control_repository"] = c.SOURCE_REPOSITORY
        e["lease_signature_b64"] = h.sign(e["lease"], "human", "hostops-v4-lease")
    if mutation == "wrong_lease_operation":
        e["lease"]["operations"] = ["read"]
        e["lease_signature_b64"] = h.sign(e["lease"], "human", "hostops-v4-lease")
    if mutation == "expired_lease":
        e["lease"]["expires_at"] = NOW - 1
        e["lease_signature_b64"] = h.sign(e["lease"], "human", "hostops-v4-lease")
    if mutation == "revoked_lease":
        h.revoke(leases=[e["lease"]["lease_id"]])
    if mutation == "kill_switch":
        (h.policy / "KILL").touch()
    if mutation == "disabled_config":
        h.config["enabled"] = False
    authority = auth.AuthorityV4(h.config, release, clock=lambda: NOW)
    if mutation == "valid_exact_write":
        r, l = authority.authenticate_v4(c.canonical(e))
        assert r.value["operation"] == "create"
        assert l == e["lease"]
    else:
        with pytest.raises(c.Denied):
            authority.authenticate_v4(c.canonical(e))
    assert not (h.data / "new.txt").exists()  # auth only, no write effects


@pytest.mark.parametrize("mutation", [
    "public_repo", "wrong_control", "wrong_source", "wrong_workflow",
    "bad_signature", "modified_release_file", "unexpected_file_path",
])
def test_v4_cryptographic_release_mutation_rejected(h, mutation):
    release, _ = _v4_signed_fixture(h)
    if mutation == "public_repo":
        release["control_repository"] = c.SOURCE_REPOSITORY
    if mutation == "wrong_control":
        release["control_repository"] = "attacker/elsewhere"
    if mutation == "wrong_source":
        release["source_sha"] = "0" * 40
    if mutation == "wrong_workflow":
        release["workflow_sha256"] = "0" * 64
    if mutation == "bad_signature":
        (h.release / "release.sig.b64").write_text("not-base64")
    if mutation == "modified_release_file":
        p = h.release / "src/ai_commerce/hostops_v3/worker.py"
        p.write_text("raise RuntimeError('tampered')")
    if mutation == "unexpected_file_path":
        release["files"]["../escape"] = "0" * 64
    # Even re-hashing a modified manifest cannot manufacture Human approval.
    if mutation not in {"bad_signature", "modified_release_file"}:
        unsigned = {k: v for k, v in release.items() if k != "release_id"}
        release["release_id"] = c.digest(c.canonical(unsigned))
    with pytest.raises(c.Denied):
        auth.AuthorityV4(h.config, release, clock=lambda: NOW)


def test_v4_cryptographic_release_loaded_only_from_pinned_install_process(h):
    release, _ = _v4_signed_fixture(h)
    config = h.root / "config-v4.json"
    config.write_bytes(c.canonical(h.config))
    code = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
            "from ai_commerce.hostops_v3.auth import verify_release_v4;"
            "verify_release_v4(json.load(open(sys.argv[2])));print('PIN_V4_VERIFIED')")
    argv = [sys.executable, "-I", "-B", "-c", code,
            str(h.release / "src"), str(config)]
    good = subprocess.run(argv, capture_output=True, timeout=15)
    assert good.returncode == 0, good.stderr.decode(errors="replace")
    assert b"PIN_V4_VERIFIED" in good.stdout
    p = h.release / "src/ai_commerce/hostops_v3/worker.py"
    p.write_text("raise RuntimeError('tampered')")
    bad = subprocess.run(argv, capture_output=True, timeout=15)
    assert bad.returncode != 0
    assert b"SOURCE_UNVERIFIED" in bad.stderr



def test_v4_signed_read_only_worker_replay_and_versioned_receipt(h):
    from ai_commerce.hostops_v3.worker import WorkerV4
    release, e = _v4_signed_fixture(h)
    worker = WorkerV4(h.config, auth.AuthorityV4(h.config, release, clock=lambda: NOW), h.journal)
    a = worker.execute(c.canonical(e))["receipt"]
    assert a["schema"] == "hostops.receipt.v4"
    assert a["status"] == "OBSERVED"
    assert a["source_repository"] == c.SOURCE_REPOSITORY
    assert a["control_repository"] == c.CONTROL_REPOSITORY
    assert c.public_receipt_v4(a) == a
    with pytest.raises(c.Denied, match="BAD_SHAPE"):
        c.public_receipt(a)
    assert worker.execute(c.canonical(e))["receipt"] == a
    ids = {row[0] for row in h.journal.db.execute("SELECT id FROM requests")}
    assert ids == {"v4:" + e["request"]["request_id"]}
    assert not (h.data / "new.txt").exists()


def test_v4_signed_read_only_worker_lost_response_stays_unknown(h):
    from ai_commerce.hostops_v3.worker import WorkerV4
    release, e = _v4_signed_fixture(h)
    class Crash(BaseException):
        pass
    def fault(point):
        if point == "after_effect":
            raise Crash()
    worker = WorkerV4(h.config, auth.AuthorityV4(h.config, release, clock=lambda: NOW),
                      h.journal, fault=fault)
    with pytest.raises(Crash):
        worker.execute(c.canonical(e))
    recovered = WorkerV4(h.config, auth.AuthorityV4(h.config, release, clock=lambda: NOW), h.journal)
    r = recovered.execute(c.canonical(e))["receipt"]
    assert r["status"] == "UNKNOWN"
    assert r["error_code"] == "RECONCILIATION_REQUIRED"
    assert r["schema"] == "hostops.receipt.v4"


@pytest.mark.parametrize("op", ["create", "replace", "patch", "append", "copy",
                                "move", "rename", "quarantine", "powershell", "disclose"])
def test_v4_signed_read_only_worker_rejects_effects_before_journal(h, op):
    from ai_commerce.hostops_v3.worker import WorkerV4
    path = str(h.data / "new.txt")
    destination = str(h.data / "other.txt")
    payload = base64.b64encode(b"fixture").decode("ascii")
    digest = c.digest(b"fixture")
    old = c.digest(b"original")
    params = {
        "create": {"path": path, "data_b64": payload, "data_sha256": digest},
        "replace": {"path": path, "data_b64": payload, "data_sha256": digest,
                    "expected_sha256": old},
        "patch": {"path": path, "data_b64": payload, "data_sha256": digest,
                  "expected_sha256": old, "offset": 0, "remove_bytes": 1},
        "append": {"path": path, "data_b64": payload, "data_sha256": digest,
                   "expected_sha256": old},
        "copy": {"path": path, "destination": destination, "expected_sha256": old},
        "move": {"path": path, "destination": destination, "expected_sha256": old},
        "rename": {"path": path, "destination": destination, "expected_sha256": old},
        "quarantine": {"path": path, "expected_sha256": old,
                       "preview_sha256": c.digest(b"preview")},
        "powershell": {"script_id": "token_inventory_v1",
                       "script_sha256": c.digest(b"script")},
        "disclose": {"selection_id": c.digest(b"selection"),
                     "content_sha256": digest, "offset": 0, "length": 1,
                     "classification": "ordinary", "recipient": "github:private"},
    }
    release, e = _v4_signed_fixture(h,
        request_mutation={"operation": op, "parameters": params[op]}, exact=True)
    # Prove the request and its Human/issuer signatures are all valid first.
    authority = auth.AuthorityV4(h.config, release, clock=lambda: NOW)
    authenticated, lease = authority.authenticate_v4(c.canonical(e))
    assert authenticated.value["operation"] == op and op in lease["operations"]
    worker = WorkerV4(h.config, authority, h.journal)
    out = worker.execute(c.canonical(e))["receipt"]
    assert out["schema"] == "hostops.receipt.v4"
    assert out["status"] == "BLOCKED"
    assert out["error_code"] == "OPERATION_NOT_ENABLED"
    assert h.journal.db.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
    assert not (h.data / "new.txt").exists()


def test_v4_ipc_version_separation_and_no_v3_dispatch(h):
    _, e = _v4_signed_fixture(h)
    payload = c.canonical({"schema": "hostops.ipc.v4", "mode": "execute", "envelope": e})
    mode, raw = broker.ipc_request_v4(payload)
    assert mode == "execute" and raw == c.canonical(e)
    with pytest.raises(c.Denied, match="BAD_IPC_MODE"):
        broker.ipc_request(payload)
    with pytest.raises(c.Denied, match="BAD_IPC_MODE"):
        broker.ipc_request_v4(c.canonical({"schema": "hostops.ipc.v3", "mode": "execute",
                                          "envelope": e}))


def test_v4_and_v3_share_journal_without_request_id_confusion(h):
    from ai_commerce.hostops_v3.worker import WorkerV4
    old = h.make()
    assert h.execute(old)["receipt"]["status"] == "OBSERVED"
    release, e = _v4_signed_fixture(h)
    e["request"]["request_id"] = old["request"]["request_id"]
    e["issuer_signature_b64"] = h.sign(e["request"], "issuer", "hostops-v4-request")
    r = WorkerV4(h.config, auth.AuthorityV4(h.config, release, clock=lambda: NOW),
                 h.journal).execute(c.canonical(e))["receipt"]
    assert r["status"] == "OBSERVED"
    ids = {x[0] for x in h.journal.db.execute("SELECT id FROM requests")}
    assert ids == {old["request"]["request_id"], "v4:" + old["request"]["request_id"]}



def test_v4_private_poll_posts_sanitized_receipt_only(h):
    release, e = _v4_signed_fixture(h)
    api = _V4API(e, e["request"]["source_sha"])
    release["workflow_sha256"] = c.digest(api.workflow)
    posted = []
    api.post = lambda path, data: posted.append((path, data))
    def send(_config, raw):
        return {"receipt": c.receipt_v4(c.dual_repo_envelope(raw)[1],
                "OBSERVED", result={"count": 1})}
    r = transport.poll_once_v4(api, h.config, release, 999, 123, send=send)
    assert r["status"] == "OBSERVED" and len(posted) == 1
    assert posted[0][0] == "/repos/" + c.CONTROL_REPOSITORY + "/issues/999/comments"
    assert set(posted[0][1]) == {"body"}
    assert "HOSTOPS_V4_RECEIPT" in posted[0][1]["body"]
    assert e["issuer_signature_b64"] not in posted[0][1]["body"]


def test_v4_private_poll_rejects_disclosure_or_spoofed_hash(h):
    release, e = _v4_signed_fixture(h)
    api = _V4API(e, e["request"]["source_sha"])
    release["workflow_sha256"] = c.digest(api.workflow)
    api.post = lambda *_args: pytest.fail("MUST_NOT_POST")
    def leak(_config, raw):
        return {"receipt": c.receipt_v4(c.dual_repo_envelope(raw)[1],
                "OBSERVED"), "disclosure_b64": "ZGF0YQ=="}
    with pytest.raises(c.Denied, match="UNSOLICITED_DISCLOSURE"):
        transport.poll_once_v4(api, h.config, release, 999, 123, send=leak)
    def fake(_config, raw):
        r = c.receipt_v4(c.dual_repo_envelope(raw)[1], "OBSERVED")
        r["request_sha256"] = "0" * 64
        return {"receipt": r}
    with pytest.raises(c.Denied, match="IPC_RESPONSE_MISMATCH"):
        transport.poll_once_v4(api, h.config, release, 999, 123, send=fake)


def test_v4_client_rejects_write_before_connect(h, monkeypatch):
    _, e = _v4_signed_fixture(h, request_mutation={
        "operation": "create", "parameters": {"path": str(h.data / "no.txt"),
        "data_b64": base64.b64encode(b"ok").decode("ascii"),
        "data_sha256": c.digest(b"ok")}}, exact=True)
    def bad(_endpoint):
        pytest.fail("MUST_NOT_CONNECT")
    monkeypatch.setattr(transport, "connect", bad)
    with pytest.raises(c.Denied, match="OPERATION_NOT_ENABLED"):
        transport.submit_v4(h.config, c.canonical(e))


def _v4_config(h, **overrides):
    return {
        **h.config,
        "schema": "hostops.config.v4",
        "source_repository": c.SOURCE_REPOSITORY,
        "control_repository": c.CONTROL_REPOSITORY,
        **overrides,
    }


def test_v4_child_signed_read_and_release_denial_offline(h, monkeypatch):
    release, e = _v4_signed_fixture(h)
    cfg = _v4_config(h)
    packet = c.canonical({"schema": "hostops.ipc.v4", "mode": "execute", "envelope": e})
    class Pipe:
        def __init__(self): self.data = None
        def send_bytes(self, value): self.data = value
        def close(self): pass
    monkeypatch.setattr(broker, "load_config_v4", lambda _: cfg)
    monkeypatch.setattr(broker, "admit_v4", lambda *_: None)
    monkeypatch.setattr(broker, "verify_release_v4",
                        lambda _: (release, auth.AuthorityV4(cfg, release, clock=lambda: NOW)))
    pipe = Pipe()
    broker.child_execute_v4("offline", packet, pipe)
    response = c.decode(pipe.data)
    assert response["receipt"]["status"] == "OBSERVED"
    assert response["receipt"]["schema"] == "hostops.receipt.v4"
    monkeypatch.setattr(broker, "verify_release_v4", lambda _: c.deny("SOURCE_UNVERIFIED"))
    pipe = Pipe()
    broker.child_execute_v4("offline", packet, pipe)
    response = c.decode(pipe.data)
    assert response["receipt"]["status"] == "UNAVAILABLE"
    assert response["receipt"]["error_code"] == "BROKER_UNAVAILABLE"


def test_v4_hmac_receipt_gate_rejects_v3_even_if_message_mac_valid(h):
    import queue
    key = os.urandom(32)
    _, e = _v4_signed_fixture(h)
    _, req = c.dual_repo_envelope(c.canonical(e))
    payload = c.canonical({"schema": "hostops.ipc.v4", "mode": "execute", "envelope": e})
    class Memory(broker.Channel):
        def __init__(self, incoming, outgoing):
            self.incoming, self.outgoing, self.pending = incoming, outgoing, b""
        def read(self, n):
            if not self.pending:
                self.pending = self.incoming.get(timeout=5)
            value, self.pending = self.pending[:n], self.pending[n:]
            return value
        def write(self, value): self.outgoing.put(value)
        def close(self): pass
    def exchange(reply):
        a, b = queue.Queue(), queue.Queue()
        client, server = Memory(a, b), Memory(b, a)
        errors = []
        def serve():
            try: broker.server_exchange(server, key, lambda got: reply)
            except BaseException as exc: errors.append(exc)
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        try:
            return broker.client_exchange(client, key, payload,
                                          verify_receipt=c.public_receipt_v4)
        finally:
            t.join(6)
    good = {"receipt": c.receipt_v4(req, "OBSERVED", result={"count": 1})}
    assert exchange(good) == good
    with pytest.raises(c.Denied):
        exchange({"receipt": c.receipt(req, "OBSERVED")})



def test_hostops_v4_windows_install_plan_is_never_an_installer():
    """No program mode may create users, ACLs, tasks, keys or a live broker."""
    script = ROOT / "scripts" / "HostOps-V4-InstallPlan.ps1"
    content = script.read_text(encoding="utf-8")
    assert "READ_ONLY_PLAN_NO_CHANGES" in content
    assert "ready_to_enable = $false" in content
    assert "native_two_account_ipc_verified = $false" in content
    assert "human_release_signature_verified = $false" in content
    assert "private_control_github_verified = $false" in content
    assert "INSTALL_DISABLED_USE_EXPLICIT_PLAN" in content
    for banned in ("New-LocalUser", "Set-Acl", "icacls /grant", "Register-ScheduledTask",
                   "New-ScheduledTask", "ssh-keygen -Y sign", "Start-Process -Verb RunAs",
                   "Invoke-Expression", "Invoke-WebRequest", "Set-Content", "Bypass"):
        assert banned.casefold() not in content.casefold()
    example = c.decode((ROOT / "config/hostops-v3/config-v4.example.json").read_bytes())
    assert example["schema"] == "hostops.config.v4.personal"
    assert example["account_mode"] == "PERSONAL_SINGLE_USER"
    assert example["enabled"] is False
    assert example["source_repository"] == c.SOURCE_REPOSITORY
    assert example["control_repository"] == c.CONTROL_REPOSITORY
    assert example["broker_identity"] == example["transport_identity"]
    assert example["broker_identity"] == security.PERSONAL_SID_PLACEHOLDER
    assert all(v == "0" * 64 for v in
               (example["ssh_keygen_sha256"], example["pwsh_sha256"]))


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell local PLAN")
def test_hostops_v4_windows_install_plan_actual_no_writes(tmp_path):
    """Execute PLAN on Windows via safe pwsh 7 without -ExecutionPolicy Bypass."""
    import hashlib
    powershell = security.prefer_readable_pwsh_path() or shutil.which("powershell.exe")
    assert powershell, "Windows runner missing PowerShell"
    script = ROOT / "scripts/HostOps-V4-InstallPlan.ps1"
    config = ROOT / "config/hostops-v3/config-v4.example.json"
    before = hashlib.sha256(config.read_bytes()).hexdigest()
    args = [powershell, "-NoLogo", "-NoProfile", "-NonInteractive"]
    assert "Bypass" not in args
    a = subprocess.run([*args, "-File", str(script), "-Plan",
                        "-ConfigPath", str(config)], capture_output=True, text=True, timeout=30)
    assert a.returncode == 0, a.stderr
    result = json.loads(a.stdout)
    assert result["schema"] == "hostops.v4.windows_install_plan.v1"
    assert result["stage"] == "READ_ONLY_PLAN_NO_CHANGES"
    assert result["config_present"] and result["config_schema_valid"]
    assert result["config_disabled"] and result["source_public_control_private_declared"]
    assert not result["distinct_non_admin_sid_configured"]
    assert not result["untrusted_identities_isolated"]
    assert not result["pinned_binary_hashes_configured"]
    assert not result["native_acl_verified"]
    assert not result["native_two_account_ipc_verified"]
    assert not result["human_release_signature_verified"]
    assert not result["private_control_github_verified"]
    assert not result["ready_to_enable"]
    if Path(powershell).stem.lower() == "pwsh":
        assert result["powershell_major"] >= 7
        assert result["powershell_edition"] == "Core"
        assert result["pwsh_7_preferred_runtime"] is True
    assert hashlib.sha256(config.read_bytes()).hexdigest() == before
    forbidden = ("broker_identity", "transport_identity", "C:\\", "S-1-", "TRUNG")
    assert all(value not in a.stdout for value in forbidden)
    no_plan = subprocess.run([*args, "-File", str(script),
                              "-ConfigPath", str(config)], capture_output=True, text=True, timeout=30)
    assert no_plan.returncode != 0
    extra_arg = subprocess.run([*args, "-File", str(script), "-Plan", "-Apply",
                                "-ConfigPath", str(config)], capture_output=True, text=True, timeout=30)
    assert extra_arg.returncode != 0
    assert hashlib.sha256(config.read_bytes()).hexdigest() == before


@pytest.mark.skipif(os.name != "nt", reason="Windows-only native SID and DACL inventory")
def test_hostops_v4_native_readonly_opt_in_fails_closed_without_installed_accounts():
    """Real Windows cmdlet invocation cannot turn sample PLAN into live proof."""
    import hashlib
    powershell = security.prefer_readable_pwsh_path() or shutil.which("powershell.exe")
    assert powershell
    script = ROOT / "scripts/HostOps-V4-InstallPlan.ps1"
    config = ROOT / "config/hostops-v3/config-v4.example.json"
    before = hashlib.sha256(config.read_bytes()).hexdigest()
    opts = [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)]
    outcome = subprocess.run([*opts, "-Plan", "-NativeReadOnly",
                              "-ConfigPath", str(config)],
                             capture_output=True, text=True, timeout=30)
    assert outcome.returncode == 0, outcome.stderr
    payload = json.loads(outcome.stdout)
    assert payload["schema"] == "hostops.v4.windows_install_plan.v1"
    assert payload["stage"] == "READ_ONLY_PLAN_NO_CHANGES"
    assert payload["native_readonly_requested"] is True
    assert payload["native_local_sid_query_complete"] is False
    assert payload["native_broker_local_account_found"] is False
    assert payload["native_transport_local_account_found"] is False
    assert payload["native_acl_root_query_complete"] is False
    assert payload["native_acl_root_policy_satisfied"] is False
    assert payload["native_acl_roots_inspected"] == 0
    assert payload["native_acl_verified"] is False
    assert payload["native_two_account_ipc_verified"] is False
    assert payload["human_release_signature_verified"] is False
    assert payload["private_control_github_verified"] is False
    assert payload["ready_to_enable"] is False
    assert payload["account_mode"] == "PERSONAL_SINGLE_USER"
    assert payload["single_user_mode_configured"] is True
    # Hosted Windows can run elevated: fail closed instead of claiming a
    # non-admin current-user SID. The personal mode never bypasses elevation.
    assert payload["single_user_sid_observed"] is payload["caller_non_elevated"]
    assert payload["native_single_user_identity_inspected"] is payload["single_user_sid_observed"]
    assert payload["single_user_current_sid_match"] is False
    assert payload["single_user_runtime_enrolled"] is False
    assert "SINGLE_USER_CURRENT_SID_UNPROVEN" in payload["block_reasons"]
    assert "PERSONAL_RUNTIME_NOT_ENROLLED" in payload["block_reasons"]
    # The probe only emits bounded aggregate booleans, never real identifiers.
    assert all(secret not in outcome.stdout for secret in
               ("broker_identity", "transport_identity", "S-1-", "C:\\\\", "TRUNG", "token"))
    missing_plan = subprocess.run([*opts, "-NativeReadOnly",
                                   "-ConfigPath", str(config)],
                                  capture_output=True, text=True, timeout=30)
    assert missing_plan.returncode != 0
    assert hashlib.sha256(config.read_bytes()).hexdigest() == before


def test_hostops_v4_native_readonly_scope_never_claims_live_or_install():
    script = (ROOT / "scripts/HostOps-V4-InstallPlan.ps1").read_text(encoding="utf-8")
    assert "[switch]$NativeReadOnly" in script
    assert "Get-CimInstance -ClassName Win32_UserAccount" in script
    assert "Get-Acl -LiteralPath" in script
    assert "-OperationTimeoutSec 3" in script
    assert "native_acl_verified = $false" in script
    assert "native_two_account_ipc_verified = $false" in script
    assert "single_user_runtime_enrolled = $false" in script
    assert "ready_to_enable = $false" in script
    assert "NATIVE_READONLY_PROOF_INCOMPLETE" in script
    assert "NativeReadOnly" in script
    forbidden = ("New-LocalUser", "Set-Acl", "Start-Service",
                 "Register-ScheduledTask", "Invoke-Expression",
                 "Start-Process -Verb RunAs", "Set-Content", "WriteAllBytes")
    assert all(term.casefold() not in script.casefold() for term in forbidden)


def test_v4_security_import_preserves_path_in_fresh_process():
    """V3 importing shared security.py must never alter process command lookup."""
    code = ("import os;"
            "before=os.environ.get('PATH');"
            "from ai_commerce.hostops_v3 import security;"
            "assert os.environ.get('PATH')==before")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows pwsh alias resolution")
def test_v4_pwsh_resolver_does_not_reorder_path(tmp_path, monkeypatch):
    """Unreadable WindowsApps-style stub must not trigger global PATH mutation."""
    alias_dir = tmp_path / "alias"
    real_dir = tmp_path / "readable"
    alias_dir.mkdir()
    real_dir.mkdir()
    stub = alias_dir / "pwsh.exe"
    binary = real_dir / "pwsh.exe"
    stub.write_bytes(b"alias")
    binary.write_bytes(b"real")
    path_value = str(alias_dir) + os.pathsep + str(real_dir)
    monkeypatch.setenv("PATH", path_value)
    monkeypatch.setattr(security.shutil, "which", lambda _: str(stub))
    original_open = Path.open

    def reject_stub(self, *args, **kwargs):
        if self == stub:
            raise PermissionError("simulated inaccessible AppExecLink")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_stub)
    assert security.prefer_readable_pwsh_path() == str(binary)
    assert os.environ["PATH"] == path_value


def test_v4_admission_scan_has_hard_size_depth_and_time_bounds(tmp_path):
    root = tmp_path / "transport"
    root.mkdir()
    assert security._bounded_install_paths(root) == [root]
    for index in range(8):
        (root / f"entry-{index}").write_text("fixture", encoding="utf-8")
    with pytest.raises(c.Denied, match="INSTALL_SCAN_LIMIT"):
        security._bounded_install_paths(root, max_entries=4)
    with pytest.raises(c.Denied, match="INSTALL_SCAN_LIMIT"):
        security._bounded_install_paths(root, max_seconds=0)

    deep = tmp_path / "deep"
    deep.mkdir()
    child = deep
    for _ in range(4):
        child = child / "child"
        child.mkdir()
    with pytest.raises(c.Denied, match="INSTALL_SCAN_LIMIT"):
        security._bounded_install_paths(deep, max_depth=2)
    try:
        (root / "linked").symlink_to(deep, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass  # Windows without symlink entitlement; size/depth still tested.
    else:
        with pytest.raises(c.Denied, match="INSTALL_REPARSE_DENIED"):
            security._bounded_install_paths(root)


def test_v4_disabled_windows_template_offline_cross_platform_parse():
    """Hosted POSIX CI may inspect a Windows PLAN template, never activate it."""
    template = ROOT / "config/hostops-v3/config-v4.example.json"
    cfg = security.load_config_v4(template)
    assert cfg["enabled"] is False
    if os.name != "nt":
        # Non-native paths must still be rejected for active broker configs.
        with pytest.raises(c.Denied, match="PERSONAL_MODE_NOT_ENROLLED"):
            security._validate_v4_config_dict({**cfg, "enabled": True})


def test_v4_personal_single_user_contract_is_explicit_disabled_and_non_downgradable(tmp_path):
    """#293: one account is a distinct PLAN-only contract, never a broker grant."""
    template = ROOT / "config/hostops-v3/config-v4.example.json"
    config = security.load_config_v4(template)
    assert config["schema"] == security.PERSONAL_V4_SCHEMA
    assert config["account_mode"] == security.PERSONAL_V4_MODE
    assert config["broker_identity"] == config["transport_identity"]
    assert config["enabled"] is False
    assert config["broker_identity"] == security.PERSONAL_SID_PLACEHOLDER
    with pytest.raises(c.Denied):
        security.load_config(template)  # legacy v3 loader must reject personal
    for mutation in (
        {"enabled": True},
        {"account_mode": "ISOLATED_ACCOUNTS"},
        {"broker_identity": "S-1-5-18", "transport_identity": "S-1-5-18"},
        {"broker_identity": "S-1-5-21-100", "transport_identity": "S-1-5-21-200"},
        {"broker_identity": "ci-identity", "transport_identity": "ci-identity"},
        {"trusted_owners": [security.PERSONAL_SID_PLACEHOLDER]},
        {"untrusted_identities": [security.PERSONAL_SID_PLACEHOLDER]},
        {"schema": "hostops.config.v4"},  # account_mode is forbidden in legacy
        {"source_repository": c.CONTROL_REPOSITORY},
        {"control_repository": c.SOURCE_REPOSITORY},
        {"policy_dir": "//other-host/secrets"},
        {"endpoint": "\\\\remote-host\\pipe\\unsafe"},
        {"unexpected_key": True},
    ):
        with pytest.raises(c.Denied):
            security._validate_v4_config_dict({**config, **mutation})
    # A real current-looking SID can be used in a *disabled* personal PLAN;
    # configuring the correct SID alone cannot enable a broker.
    valid_sid = "S-1-5-21-12345-67890-13579-1001"
    local = {**config, "broker_identity": valid_sid, "transport_identity": valid_sid}
    assert security._validate_v4_config_dict(local) == local
    with pytest.raises(c.Denied, match="PERSONAL_MODE_NOT_ENROLLED"):
        security._validate_v4_config_dict({**local, "enabled": True})
    with pytest.raises(c.Denied, match="PERSONAL_MODE_NOT_ENROLLED"):
        security.admit_v4(local, template)
    with pytest.raises(c.Denied, match="PERSONAL_MODE_NOT_ENROLLED"):
        broker.serve_v4(template, once=True)
    with pytest.raises(c.Denied, match="PERSONAL_MODE_NOT_ENROLLED"):
        transport.submit_v4(local, b"not-a-valid-envelope")
    with pytest.raises(c.Denied, match="PERSONAL_MODE_NOT_ENROLLED"):
        transport.poll_once_v4(None, local, {}, 1, 1)
    with pytest.raises(c.Denied, match="PERSONAL_MODE_NOT_ENROLLED"):
        transport.poll_inbox_v4(None, local, {})


def test_personal_local_inspect_needs_local_console_matching_sid_and_consent(
        tmp_path, monkeypatch, capsys):
    """#293: real one-shot fixed local inventory, no remote transport/privilege."""
    import builtins
    import platform
    import sys as system_sys

    sid = "S-1-5-21-12345-67890-13579-1001"
    config = security.load_config_v4(ROOT / "config/hostops-v3/config-v4.example.json")
    config["broker_identity"] = sid
    config["transport_identity"] = sid
    config["policy_dir"] = str(tmp_path / "policy")
    config_file = tmp_path / "personal.json"
    config_file.write_bytes(c.canonical(config))
    assert security.load_config_v4(config_file) == config
    options = ["personal-local-inspect", "--config", str(config_file)]
    original_os = cli.os
    monkeypatch.setattr(cli, "os",
                        SimpleNamespace(name="nt", cpu_count=lambda: 8))
    monkeypatch.setattr(cli, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True),
        stdout=SimpleNamespace(isatty=lambda: True),
        stderr=system_sys.stderr))
    monkeypatch.setattr(cli, "windows_identity", lambda: (sid, False))
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(builtins, "input", lambda: "LOCAL READ ONLY")
    called = []
    monkeypatch.setattr(cli, "serve_v4", lambda *_, **__: called.append("broker"))
    monkeypatch.setattr(cli, "submit_v4", lambda *_, **__: called.append("transport"))
    before = config_file.read_bytes()
    assert cli.main_v4(options) == 0
    output = capsys.readouterr()
    assert "HOSTOPS_PERSONAL_LOCAL_READ" in output.out
    receipt = c.decode(output.out.splitlines()[-1].encode("ascii"), limit=4096)
    assert receipt == {
        "schema": "hostops.personal.local_inventory.v1",
        "account_mode": security.PERSONAL_V4_MODE,
        "operation": "local_os_inventory", "windows": True,
        "current_sid_matched": True, "non_elevated": True,
        "console_confirmation_received": True,
        "architecture": "X64", "logical_cpu_count": 8,
        "github_contacted": False, "broker_started": False,
        "private_keys_used": False, "filesystem_written": False,
        "ready_to_enable": False}
    assert sid not in output.out + output.err
    assert not called and config_file.read_bytes() == before

    # Decline consent, wrong SID, elevated/System SID, missing terminal,
    # KILL marker, unsafe output path and forbidden network/credential args
    # must never cause a broker launch or create an output file.
    cases = [
        ("declined", {"confirm": "no"}),
        ("wrong-sid", {"sid": "S-1-5-21-7-8-9-10"}),
        ("elevated", {"elevated": True}),
        ("system", {"sid": "S-1-5-18"}),
        ("no-tty", {"tty": False}),
        ("no-windows", {"osname": "posix"}),
        ("output", {"flags": ["--output", str(tmp_path / "must-not-exist")]}),
        ("token", {"flags": ["--token-file", str(tmp_path / "token.txt")]}),
        ("request", {"flags": ["--request-file", str(tmp_path / "request.json")]}),
    ]
    for _, mutation in cases:
        monkeypatch.setattr(cli, "os", SimpleNamespace(
            name=mutation.get("osname", "nt"), cpu_count=lambda: 8))
        tty = mutation.get("tty", True)
        monkeypatch.setattr(cli, "sys", SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: tty),
            stdout=SimpleNamespace(isatty=lambda: tty),
            stderr=system_sys.stderr))
        monkeypatch.setattr(cli, "windows_identity", lambda m=mutation: (
            m.get("sid", sid), m.get("elevated", False)))
        monkeypatch.setattr(builtins, "input", lambda m=mutation: m.get("confirm", "LOCAL READ ONLY"))
        assert cli.main_v4([*options, *mutation.get("flags", [])]) == 2
        assert not (tmp_path / "must-not-exist").exists()
        assert not called
    capsys.readouterr()
    config["enabled"] = True
    config_file.write_bytes(c.canonical(config))
    assert cli.main_v4(options) == 2
    config["enabled"] = False
    config["schema"] = "hostops.config.v4"
    config.pop("account_mode")
    config_file.write_bytes(c.canonical(config))
    assert cli.main_v4(options) == 2
    config["schema"] = security.PERSONAL_V4_SCHEMA
    config["account_mode"] = security.PERSONAL_V4_MODE
    config_file.write_bytes(c.canonical(config))
    policy = tmp_path / "policy"
    policy.mkdir()
    (policy / "KILL").touch()
    assert cli.main_v4(options) == 2
    assert not called


def test_personal_local_inspect_rejects_nonlocal_config(tmp_path, monkeypatch, capsys):
    """A CLI-exposed local inventory never reads a symlinked config."""
    import sys as system_sys
    source = ROOT / "config/hostops-v3/config-v4.example.json"
    linked = tmp_path / "linked.json"
    try:
        linked.symlink_to(source)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    monkeypatch.setattr(cli, "os", SimpleNamespace(name="nt", cpu_count=lambda: 4))
    monkeypatch.setattr(cli, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True),
        stdout=SimpleNamespace(isatty=lambda: True),
        stderr=system_sys.stderr))
    assert cli.main_v4(["personal-local-inspect", "--config", str(linked)]) == 2
    assert "HOSTOPS_V4_BLOCKED" in capsys.readouterr().err


def test_v4_legacy_two_account_contract_rejects_equal_sid(h):
    cfg = _v4_config(h)
    assert cfg["schema"] == "hostops.config.v4"
    assert "account_mode" not in cfg
    with pytest.raises(c.Denied, match="IDENTITIES_NOT_ISOLATED"):
        security._validate_v4_config_dict(
            {**cfg, "transport_identity": cfg["broker_identity"]})


def test_v4_load_config_and_admit_v4_isolation_and_fail_closed(h, monkeypatch):
    v3_path = h.root / "config-v3.json"
    v3_path.write_bytes(c.canonical(h.config))
    with pytest.raises(c.Denied):
        security.load_config_v4(v3_path)
    cfg = _v4_config(h)
    v4_path = h.root / "config-v4.json"
    v4_path.write_bytes(c.canonical(cfg))
    assert security.load_config_v4(v4_path) == cfg
    with pytest.raises(c.Denied):
        security.load_config(v4_path)  # v3 loader rejects v4 config
    for bad in (
        {"source_repository": c.CONTROL_REPOSITORY},
        {"control_repository": c.SOURCE_REPOSITORY},
        {"broker_identity": cfg["transport_identity"]},
        {"broker_identity": "ci-identity"},
        {"transport_identity": "installer-identity"},
        {"policy_dir": "\\\\remote\\share\\policy"},
        {"policy_dir": "//remote/share/policy"},
    ):
        p = h.root / "bad-v4.json"
        p.write_bytes(c.canonical({**cfg, **bad}))
        with pytest.raises(c.Denied):
            security.load_config_v4(p)
    example_cfg = security.load_config_v4(ROOT / "config/hostops-v3/config-v4.example.json")
    assert example_cfg["enabled"] is False
    with pytest.raises(c.Denied, match="PERSONAL_MODE_NOT_ENROLLED"):
        security.admit_v4(example_cfg, v4_path)
    with pytest.raises(c.Denied, match="VERIFIER_UNVERIFIED"):
        security.admit_v4({**cfg, "ssh_keygen_sha256": "0" * 64}, v4_path)
    with pytest.raises(c.Denied, match="ELEVATION_DENIED"):
        security.admit_v4({**cfg, "broker_identity": "S-1-5-18"}, v4_path)
    with pytest.raises(c.Denied, match="IDENTITIES_NOT_ISOLATED"):
        security.admit_v4({**cfg, "broker_identity": "REPLACE_WITH_BROKER"}, v4_path)
    (h.policy / "ipc.key").write_bytes(b"k" * 32)
    if os.name == "nt":
        monkeypatch.setattr(security, "windows_identity", lambda *_: ("broker-identity", False))
        monkeypatch.setattr(security, "windows_acl", lambda *_, **__: None)
    else:
        # Match the actual fixture owner; a broker SID/UID must never also be
        # listed as a separate trusted owner in V4's stricter identity contract.
        fixture_owner = h.policy.stat().st_uid
        monkeypatch.setattr(os, "geteuid", lambda: fixture_owner)
        cfg = {**cfg, "broker_identity": str(fixture_owner)}
        v4_path.write_bytes(c.canonical(cfg))
        for item in (v4_path, h.policy, h.release, h.state, *(h.policy.rglob("*")), *(h.state.rglob("*"))):
            os.chmod(item, 0o700 if item.is_dir() else 0o600)
    security.admit_v4(cfg, v4_path)
    (h.policy / "KILL").touch()
    with pytest.raises(c.Denied, match="KILL_SWITCH"):
        security.admit_v4(cfg, v4_path)


def test_v4_serve_v4_handler_and_transport_inbox_staging(h, monkeypatch):
    release, e = _v4_signed_fixture(h)
    cfg = _v4_config(h)
    v4_path = h.root / "config-v4.json"
    v4_path.write_bytes(c.canonical(cfg))
    _, req = c.dual_repo_envelope(c.canonical(e))
    good_reply = {"receipt": c.receipt_v4(req, "OBSERVED", result={"count": 1})}
    handler = broker._verified_v4_exchange_handler(v4_path, execute=lambda _: good_reply)
    packet = c.canonical({"schema": "hostops.ipc.v4", "mode": "execute", "envelope": e})
    assert handler(packet) == good_reply
    with pytest.raises(c.Denied, match="BAD_IPC_MODE"):
        handler(c.canonical({"schema": "hostops.ipc.v3", "mode": "execute", "envelope": h.make()}))
    _, write_e = _v4_signed_fixture(h, request_mutation={
        "operation": "create", "parameters": {"path": str(h.data / "no.txt"),
        "data_b64": base64.b64encode(b"ok").decode("ascii"),
        "data_sha256": c.digest(b"ok")}}, exact=True)
    with pytest.raises(c.Denied, match="OPERATION_NOT_ENABLED"):
        handler(c.canonical({"schema": "hostops.ipc.v4", "mode": "execute", "envelope": write_e}))
    bad_leak = broker._verified_v4_exchange_handler(
        v4_path, execute=lambda _: {**good_reply, "disclosure_b64": "ZGF0YQ=="})
    with pytest.raises(c.Denied, match="UNSOLICITED_DISCLOSURE"):
        bad_leak(packet)
    api = _V4API(e, e["request"]["source_sha"])
    release["workflow_sha256"] = c.digest(api.workflow)
    posted = []
    api.post = lambda path, data: posted.append((path, data))
    orig_get = api.get
    def inbox_get(path):
        prefix = "/repos/" + c.CONTROL_REPOSITORY
        if path == prefix + "/issues?state=open&per_page=100":
            return [api.issue]
        if path == prefix + "/actions/workflows/hostops-v3.yml/runs?event=issues&per_page=100":
            return {"workflow_runs": [{**api.run, "id": 123}]}
        return orig_get(path)
    api.get = inbox_get
    assert transport.poll_inbox_v4(api, cfg, release, send=lambda _, __: good_reply) == 1
    assert transport.poll_inbox_v4(api, cfg, release, send=lambda _, __: pytest.fail("duplicate")) == 0
    assert (h.state / f"receipt-v4-{req.fingerprint}.json").is_file()


def test_v4_cli_commands_isolated_from_v3_and_sanitized(h, monkeypatch, capsys):
    release, e = _v4_signed_fixture(h)
    cfg = _v4_config(h)
    cfg_path = h.root / "config-v4.json"
    cfg_path.write_bytes(c.canonical(cfg))
    req_path = h.root / "request-v4.json"
    req_path.write_bytes(c.canonical(e))
    out_path = h.root / "out.json"
    assert cli.main(["validate", "--request-file", str(req_path), "--output", str(out_path)]) == 2
    assert c.decode(out_path.read_bytes())["schema"] == "hostops.receipt.v3"
    assert cli.main_v4(["validate-v4", "--request-file", str(req_path), "--output", str(out_path)]) == 0
    validated = c.decode(out_path.read_bytes())
    assert validated["schema"] == "hostops.transport.v4" and validated["status"] == "STRUCTURE_ONLY"
    v3_req_path = h.root / "request-v3.json"
    v3_req_path.write_bytes(c.canonical(h.make()))
    assert cli.main_v4(["validate-v4", "--request-file", str(v3_req_path), "--output", str(out_path)]) == 2
    assert c.decode(out_path.read_bytes())["schema"] == "hostops.receipt.v4"
    _, req = c.dual_repo_envelope(c.canonical(e))
    rec_path = h.root / "receipt-v4.json"
    rec_path.write_bytes(c.canonical(c.receipt_v4(req, "OBSERVED", result={"count": 1})))
    assert cli.main_v4(["report-v4", "--receipt-file", str(rec_path), "--output", str(out_path)]) == 0
    assert b"HOSTOPS_V4_RECEIPT" in out_path.read_bytes()
    monkeypatch.setattr(cli, "verify_release_v4", lambda _: (release, None))
    if os.name == "nt":
        monkeypatch.setattr(cli, "windows_identity", lambda *_: ("transport-identity", False))
    else:
        monkeypatch.setattr(os, "geteuid", lambda: 2000)
        cfg["transport_identity"] = "2000"
        cfg_path.write_bytes(c.canonical(cfg))
    monkeypatch.setattr(cli, "submit_v4",
                        lambda *_, **__: {"receipt": c.receipt_v4(req, "OBSERVED", result={"count": 1})})
    assert cli.main_v4(["submit-v4", "--config", str(cfg_path), "--request-file", str(req_path),
                        "--output", str(out_path)]) == 0
    assert c.decode(out_path.read_bytes())["schema"] == "hostops.receipt.v4"
    assert cli.main_v4(["submit-v4", "--config", str(cfg_path), "--request-file", str(req_path),
                        "--disclosure-output", str(h.root / "disclose.bin"), "--output", str(out_path)]) == 2
    served = []
    monkeypatch.setattr(cli, "serve_v4", lambda path, once=False: served.append((path, once)))
    assert cli.main_v4(["broker-v4", "--config", str(cfg_path)]) == 0
    assert served == [(str(cfg_path), True)]
    captured = capsys.readouterr()
    assert "transport-identity" not in captured.out + captured.err

