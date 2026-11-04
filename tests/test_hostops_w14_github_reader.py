"""W1.4 HTTPS GitHub Issue reader: source-only, no creds or host effects."""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from ai_commerce.hostops_v3 import contract as c, w1_bridge, w14_origin, w1_github_reader as reader
from test_hostops_v3 import h, keys
from test_hostops_w14_ingress import _personal_w1_signed_fixture

TOKEN = "ghs_" + "X" * 40
BASE = "https://api.github.com/repos/trung-via/ai-commerce-hostops-control"
HTML = "https://github.com/trung-via/ai-commerce-hostops-control"


def payloads(proposal, verify):
    body = w1_bridge.encode_signed_issue_body(
        proposal, issue_number=verify["issue_number"],
        expected_source_sha=verify["expected_source_sha"], now=verify["now"])
    repo = {
        "full_name": c.CONTROL_REPOSITORY, "private": True,
        "html_url": HTML, "url": BASE, "id": w14_origin.REPO_ID,
        "owner": {"login": "trung-via", "id": w14_origin.OWNER_ID},
    }
    n = verify["issue_number"]
    issue = {
        "number": n, "id": 900, "repository_url": BASE,
        "url": BASE + "/issues/" + str(n),
        "html_url": HTML + "/issues/" + str(n),
        "state": "open", "user": {"login": "trung-via", "id": w14_origin.OWNER_ID},
        "node_id": "I_0123456789",
        "updated_at": "2026-11-04T00:00:00Z",
        "body": body,
    }
    return repo, issue


class FakeResponse:
    def __init__(self, url, body, *, status=200, final_url=None, media="application/json"):
        self.url = url
        self.body = body if type(body) is bytes else json.dumps(body).encode("utf-8")
        self.status = status
        self.final_url = final_url or url
        self.headers = {"Content-Type": media}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def getcode(self):
        return self.status

    def geturl(self):
        return self.final_url

    def read(self, limit):
        return self.body[:limit]


class FakeOpener:
    def __init__(self, response_map):
        self.response_map = response_map
        self.seen = []

    def open(self, req, *, timeout):
        self.seen.append((req, timeout))
        item = self.response_map[req.full_url]
        if isinstance(item, Exception):
            raise item
        return item


def simulated(monkeypatch, repo, issue):
    opener = FakeOpener({
        BASE: FakeResponse(BASE, repo),
        BASE + "/issues/123": FakeResponse(BASE + "/issues/123", issue),
    })
    monkeypatch.setattr(reader, "_build_https_opener", lambda: opener)
    return opener


def test_w14_real_signed_rest_reader_never_enables_remote_write(h, monkeypatch):
    proposal, verify = _personal_w1_signed_fixture(h)
    repo, issue = payloads(proposal, verify)
    fake = simulated(monkeypatch, repo, issue)
    existing = {p.name for p in h.data.iterdir()}

    result = reader.inspect_github_issue_readonly(TOKEN, **verify)
    assert result["status"] == "GITHUB_ISSUE_SIGNATURE_CHECKED_NO_EFFECT"
    assert result["request_id"] == proposal["request_id"]
    assert result["source_sha"] == verify["expected_source_sha"]
    assert (result["repo_id"], result["issue_id"]) == (w14_origin.REPO_ID, 900)
    assert result["human_signature_verified"] is True
    for field in ("github_origin_independently_attested",
                  "external_token_custody_proven", "replay_prevented",
                  "host_effect_authorized", "remote_write_enabled",
                  "independent_host_receipt"):
        assert result[field] is False
    assert set(result) == {
        "schema", "status", "request_id", "issue_number", "source_sha",
        "repo_id", "issue_id", "snapshot_sha256", "proposal_sha256",
        "human_signature_verified", "github_origin_independently_attested",
        "external_token_custody_proven", "replay_prevented",
        "host_effect_authorized", "remote_write_enabled",
        "independent_host_receipt",
    }
    assert len(fake.seen) == 2
    assert [req.full_url for req, _ in fake.seen] == [
        BASE, BASE + "/issues/123"]
    for req, timeout in fake.seen:
        assert req.get_method() == "GET"
        assert timeout == 10
        assert req.get_header("Authorization") == "Bearer " + TOKEN
        assert req.get_header("Cache-control") == "no-store"
        assert req.get_header("X-github-api-version") == "2022-11-28"
    assert TOKEN not in repr(result)
    assert {p.name for p in h.data.iterdir()} == existing
    assert not (h.data / proposal["parameters"]["filename"]).exists()


@pytest.mark.parametrize("bad", [
    ({"private": False}, {}),
    ({"full_name": c.SOURCE_REPOSITORY}, {}),
    ({"html_url": "https://fake.github.invalid"}, {}),
    ({"id": True}, {}),
    ({"id": w14_origin.REPO_ID + 1}, {}),
    ({"owner": {"login": "attacker", "id": 42}}, {}),
    ({"owner": {"login": "trung-via", "id": 0}}, {}),
])
def test_w14_repository_must_be_private_and_exact(h, monkeypatch, bad):
    proposal, verify = _personal_w1_signed_fixture(h)
    repo, issue = payloads(proposal, verify)
    repo.update(bad[0])
    opener = simulated(monkeypatch, repo, issue)
    with pytest.raises(c.Denied, match="W14_GITHUB_REPOSITORY_DENIED|W14_GITHUB_METADATA_DENIED"):
        reader.read_private_issue_snapshot(TOKEN, issue_number=123)
    assert len(opener.seen) == 1


@pytest.mark.parametrize("field,value", [
    ("number", 124),
    ("number", True),
    ("repository_url", "https://fake.invalid"),
    ("html_url", HTML + "/pull/123"),
    ("url", BASE + "/issues/124"),
    ("id", 0),
    ("state", "closed"),
    ("user", {"login": "attacker", "id": 42}),
    ("user", {"login": "trung-via", "id": 99}),
    ("body", ""),
    ("node_id", "invalid"),
    ("updated_at", "1900-01-01"),
])
def test_w14_issue_metadata_identity_mismatch_denied(h, monkeypatch, field, value):
    proposal, verify = _personal_w1_signed_fixture(h)
    repo, issue = payloads(proposal, verify)
    issue[field] = value
    opener = simulated(monkeypatch, repo, issue)
    with pytest.raises(c.Denied, match="W14_GITHUB_ISSUE_DENIED|W14_ISSUE_BODY_DENIED|W14_GITHUB_METADATA_DENIED"):
        reader.read_private_issue_snapshot(TOKEN, issue_number=123)
    assert len(opener.seen) == 2


def test_w14_pr_payload_not_accepted_as_issue(h, monkeypatch):
    proposal, verify = _personal_w1_signed_fixture(h)
    repo, issue = payloads(proposal, verify)
    issue["pull_request"] = {"url": BASE + "/pulls/123"}
    simulated(monkeypatch, repo, issue)
    with pytest.raises(c.Denied, match="W14_GITHUB_ISSUE_DENIED"):
        reader.read_private_issue_snapshot(TOKEN, issue_number=123)


@pytest.mark.parametrize("credential", ["", "short", "ghs_secret\nInjected: x", None, 42, "x" * 1025])
def test_w14_invalid_credential_denied_before_network(monkeypatch, credential):
    monkeypatch.setattr(reader, "_build_https_opener", lambda: pytest.fail("should not build opener"))
    with pytest.raises(c.Denied, match="W14_CREDENTIAL_UNAVAILABLE"):
        reader.read_private_issue_snapshot(credential, issue_number=123)


@pytest.mark.parametrize("issue_number", [True, 0, -1, 2147483648, "123"])
def test_w14_issue_id_denied_before_any_transport(monkeypatch, issue_number):
    monkeypatch.setattr(reader, "_build_https_opener", lambda: pytest.fail("should not build opener"))
    with pytest.raises(c.Denied, match="W14_ISSUE_NUMBER_DENIED"):
        reader.read_private_issue_snapshot(TOKEN, issue_number=issue_number)


def test_w14_http_redirect_and_unauthorized_do_not_disclose_token(h, monkeypatch):
    proposal, verify = _personal_w1_signed_fixture(h)
    repo, issue = payloads(proposal, verify)
    opener = simulated(monkeypatch, repo, issue)
    opener.response_map[BASE] = FakeResponse(BASE, repo, status=301, final_url="https://elsewhere.invalid")
    with pytest.raises(c.Denied, match="W14_GITHUB_UNTRUSTED_RESPONSE") as err:
        reader.read_private_issue_snapshot(TOKEN, issue_number=123)
    assert TOKEN not in str(err.value)

    opener.response_map[BASE] = urllib.error.HTTPError(
        BASE, 403, TOKEN, {}, None)
    with pytest.raises(c.Denied, match="W14_GITHUB_API_UNAVAILABLE") as err2:
        reader.read_private_issue_snapshot(TOKEN, issue_number=123)
    assert TOKEN not in str(err2.value)


@pytest.mark.parametrize("code,media", [
    (304, "application/json"),
    (200, "text/html"),
    (302, "application/json"),
])
def test_w14_non200_or_nonjson_rejected(h, monkeypatch, code, media):
    proposal, verify = _personal_w1_signed_fixture(h)
    repo, issue = payloads(proposal, verify)
    opener = simulated(monkeypatch, repo, issue)
    opener.response_map[BASE] = FakeResponse(BASE, repo, status=code, media=media)
    with pytest.raises(c.Denied, match="W14_GITHUB_UNTRUSTED_RESPONSE"):
        reader.read_private_issue_snapshot(TOKEN, issue_number=123)


def test_w14_oversize_duplicate_json_and_tampered_signed_body_fail(h, monkeypatch):
    proposal, verify = _personal_w1_signed_fixture(h)
    repo, issue = payloads(proposal, verify)
    opener = simulated(monkeypatch, repo, issue)

    opener.response_map[BASE] = FakeResponse(BASE, b"x" * 65537)
    with pytest.raises(c.Denied, match="W14_GITHUB_RESPONSE_LIMIT"):
        reader.read_private_issue_snapshot(TOKEN, issue_number=123)

    opener.response_map[BASE] = FakeResponse(
        BASE, b'{"full_name":"trung-via/ai-commerce-hostops-control","full_name":"spoof"}')
    with pytest.raises(c.Denied, match="DUPLICATE_KEY"):
        reader.read_private_issue_snapshot(TOKEN, issue_number=123)

    opener.response_map[BASE] = FakeResponse(BASE, repo)
    issue["body"] = issue["body"].replace('"filename":"signed-proof.txt"', '"filename":"hijack.txt"')
    opener.response_map[BASE + "/issues/123"] = FakeResponse(BASE + "/issues/123", issue)
    with pytest.raises(c.Denied):
        reader.inspect_github_issue_readonly(TOKEN, **verify)
    assert not (h.data / "hijack.txt").exists()


def test_w14_expiry_and_source_sha_fail_when_github_fields_valid(h, monkeypatch):
    proposal, verify = _personal_w1_signed_fixture(h)
    repo, issue = payloads(proposal, verify)
    simulated(monkeypatch, repo, issue)
    with pytest.raises(c.Denied, match="W1_REQUEST_EXPIRED"):
        reader.inspect_github_issue_readonly(
            TOKEN, **{**verify, "now": proposal["expires_at"] + 10})
    with pytest.raises(c.Denied, match="W1_SOURCE_MISMATCH"):
        reader.inspect_github_issue_readonly(
            TOKEN, **{**verify, "expected_source_sha": "f" * 40})


def test_w14_https_builder_has_no_inherited_proxy(monkeypatch):
    # urllib.build_opener intentionally omits an EMPTY ProxyHandler from
    # opener.handlers; validate the explicit construction input, not a
    # handler instance which is absent by design on Python 3.11/3.14.
    builder = reader.urllib.request.build_opener
    supplied = []

    def inspect_builder(*handlers):
        supplied.extend(handlers)
        return builder(*handlers)

    monkeypatch.setattr(reader.urllib.request, "build_opener", inspect_builder)
    opener = reader._build_https_opener()
    proxies = [h for h in supplied
               if isinstance(h, reader.urllib.request.ProxyHandler)]
    assert len(proxies) == 1
    assert proxies[0].proxies == {}
    # A proxy handler should not survive in the configured runtime opener.
    assert not any(isinstance(h, reader.urllib.request.ProxyHandler)
                   for h in opener.handlers)
    assert any(isinstance(h, reader._DenyRedirect)
               for h in opener.handlers)
