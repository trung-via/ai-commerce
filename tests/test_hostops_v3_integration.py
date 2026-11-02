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
    assert '  issues:' in text
    assert '    types: [opened]' in text
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
    assert out["error_code"] == "V4_OPERATION_NOT_ENABLED"
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
