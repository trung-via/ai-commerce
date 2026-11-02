"""One-time D5 readiness enrollment; synthetic no Chrome/Send tests."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from ai_commerce.remote.wake_readiness_bootstrap import (
    provision, SetupBlocked, _same_loopback_9333,
)


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "AI-Commerce"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    checkout = tmp_path / "checkout"
    checkout.mkdir(mode=0o700)
    c = root / "chrome.json"
    ledger = root / "ledger.json"
    reg = root / "registry.json"
    for path in (c, ledger, reg):
        path.write_text("{}")
        path.chmod(0o600)

    class Store:
        phase = "READY"
        source_sha = "a" * 40
        def __init__(self, *, path, repository_root):
            assert path == ledger and repository_root == checkout
        def read(self):
            return {"phase": self.phase, "source_sha": self.source_sha}

    params = {
        "checkout": checkout,
        "chrome_config": c,
        "state_file": ledger,
        "registry": reg,
        "route_id": "d5-route",
        "local_app_data": tmp_path,
        "source_reader": lambda: "a" * 40,
        "chrome_loader": lambda config, source, *, local_app_data:
            SimpleNamespace(port=9333, endpoint="http://127.0.0.1:9333"),
        "route_loader": lambda *, registry_path, checkout, route_id:
            SimpleNamespace(cdp_endpoint="http://127.0.0.1:9333"),
        "store_cls": Store,
    }
    return params, root, Store


def test_one_time_creates_private_manifest_without_browser_or_send(fixture):
    params, root, _ = fixture
    response = provision(**params)
    assert response == {"status": "CONFIGURED", "wake_sent": False,
                        "browser_launched": False}
    file = root / "wake-readiness.json"
    data = json.loads(file.read_text())
    assert data["route_id"] == "d5-route"
    assert data["checkout"] == str(params["checkout"])
    assert data["version"] == 1
    assert file.stat().st_mode & 0o077 == 0


def test_idempotent_identical_manifest_and_reject_changed_parameters(fixture):
    params, root, _ = fixture
    provision(**params)
    assert provision(**params)["status"] == "ALREADY_CONFIGURED"
    changed = dict(params, route_id="other-route")
    with pytest.raises(SetupBlocked, match="MANIFEST_ALREADY_ENROLLED_DIFFERENT"):
        provision(**changed)
    assert json.loads((root / "wake-readiness.json").read_text())["route_id"] == "d5-route"


@pytest.mark.parametrize("phase,code", [
    ("SUBMISSION_UNKNOWN", "UNKNOWN_REQUIRES_RECONCILIATION"),
    ("COMPLETE_UI_ONLY", "FRESH_READY_LEDGER_REQUIRED"),
])
def test_unsafe_ledger_blocks_before_writing(fixture, phase, code):
    params, root, store = fixture
    store.phase = phase
    with pytest.raises(SetupBlocked, match=code):
        provision(**params)
    assert not (root / "wake-readiness.json").exists()


def test_stale_source_sha_blocks_private_setup(fixture):
    params, root, _ = fixture
    params["source_reader"] = lambda: "b" * 40
    with pytest.raises(SetupBlocked, match="SOURCE_SHA_CHANGED"):
        provision(**params)
    assert not (root / "wake-readiness.json").exists()


@pytest.mark.parametrize("endpoint", [
    "http://127.0.0.1:9222",
    "http://0.0.0.0:9333",
    "http://localhost:9333",
    "http://[::1]:9333",
    "http://127.0.0.1:9333/x",
    "http://127.0.0.1:9333/?token=x",
])
def test_only_exact_ipv4_loopback_route_accepted(fixture, endpoint):
    params, root, _ = fixture
    params["route_loader"] = lambda **kwargs: SimpleNamespace(cdp_endpoint=endpoint)
    with pytest.raises(SetupBlocked, match="ENROLLED_ROUTE_NOT_9333"):
        provision(**params)
    assert not (root / "wake-readiness.json").exists()


def test_missing_private_root_is_not_created_implicitly(tmp_path, fixture):
    params, root, _ = fixture
    root.rmdir() if not list(root.iterdir()) else None
    alt = tmp_path / "unprovisioned-appdata"
    alt.mkdir()
    params["local_app_data"] = alt
    with pytest.raises(SetupBlocked, match="PRIVATE_ROOT_NOT_PROVISIONED"):
        provision(**params)


def test_reject_private_inputs_inside_checkout(fixture):
    params, root, _ = fixture
    fake = params["checkout"] / "bad-registry.json"
    fake.write_text("{}")
    fake.chmod(0o600)
    params["registry"] = fake
    with pytest.raises(SetupBlocked, match="PRIVATE_INPUT_INSIDE_REPO"):
        provision(**params)
    assert not (root / "wake-readiness.json").exists()


def test_no_protocol_wildcards():
    assert _same_loopback_9333("http://127.0.0.1:9333")
    assert not _same_loopback_9333("http://0.0.0.0:9333")
