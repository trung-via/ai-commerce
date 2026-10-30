"""D3 authenticated GitHub artifact authority — no browser writes or live Wake."""
from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import urllib.request
import zipfile

import pytest

from ai_commerce.remote.completion_event import build_completion_event
from ai_commerce.remote.github_artifact_authority import (
    GitHubRestArtifactAuthority, _NoCredentialRedirect,
)
from ai_commerce.remote.wake_adapter import (
    GitHubArtifactProvenance, ProvenanceError, verify_provenance,
)

REPO = "trung-via/ai-commerce"


def evidence():
    report = {
        "kind": "AI_COMMERCE_REMOTE_BATCH_RESULT", "version": 1,
        "batch_id": "d3-batch", "source_sha": "a" * 40,
        "status": "COMPLETE", "total": 1, "succeeded": 1,
        "actions": [{"action_id": "D3", "attempt_id": "d3-01",
                     "status": "CANDIDATE_READY", "candidate_sha": "b" * 40}],
        "github_workflow_run_id": 42, "github_issue_number": 7,
    }
    event = build_completion_event(report, repository=REPO, run_id=42,
                                   run_attempt=1, issue_number=7)
    report.update(wake_event_id=event["event_id"], wake_event_status="RECORDED_UNBOUND")
    provenance = GitHubArtifactProvenance(
        repository=REPO, run_id=42, run_attempt=1,
        artifact_name="ef02-results-42-1", workflow_run_status="completed",
        attestation_verified=True,
    )
    return event, report, provenance


def archived(event, report, *, event_bytes=None, report_bytes=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(event["batch_id"] + "/completion_event.json",
                         json.dumps(event).encode() if event_bytes is None else event_bytes)
        archive.writestr(event["batch_id"] + "/batch_result.json",
                         json.dumps(report).encode() if report_bytes is None else report_bytes)
    return stream.getvalue()


def authorized(monkeypatch, data, *, run_overrides=None, listing_overrides=None):
    authority = GitHubRestArtifactAuthority(
        repository=REPO, workflow_id=123, token_provider=lambda: "test-token-ONLY")
    run = {
        "id": 42, "run_attempt": 1, "workflow_id": 123, "status": "completed",
        "event": "issues", "repository": {"full_name": REPO},
    }
    listing = {
        "total_count": 1,
        "artifacts": [{"id": 301, "name": "ef02-results-42-1", "expired": False,
                       "workflow_run": {"id": 42},
                       "digest": "sha256:" + hashlib.sha256(data).hexdigest()}],
    }
    run.update(run_overrides or {})
    listing.update(listing_overrides or {})

    def fake_json(url, token):
        assert token == "test-token-ONLY"
        assert url.startswith("https://api.github.com/repos/" + REPO + "/")
        return listing if "/artifacts?" in url else run

    def fake_zip(url, token):
        assert token == "test-token-ONLY"
        assert url == "https://api.github.com/repos/" + REPO + "/actions/artifacts/301/zip"
        return data

    monkeypatch.setattr(authority, "_read_json", fake_json)
    monkeypatch.setattr(authority, "_read_bytes", fake_zip)
    return authority


def test_real_authenticated_archive_content_is_accepted(monkeypatch):
    event, report, provenance = evidence()
    a = authorized(monkeypatch, archived(event, report))
    assert verify_provenance(event, report, provenance, authority=a) is None


@pytest.mark.parametrize("run_overrides", [
    {"id": 43}, {"run_attempt": 2}, {"workflow_id": 124},
    {"status": "in_progress"}, {"event": "workflow_dispatch"},
    {"repository": {"full_name": "other/repo"}},
])
def test_wrong_github_run_never_grants_artifact_authority(monkeypatch, run_overrides):
    event, report, provenance = evidence()
    a = authorized(monkeypatch, archived(event, report), run_overrides=run_overrides)
    with pytest.raises(ProvenanceError, match="PROVENANCE_UNVERIFIED"):
        verify_provenance(event, report, provenance, authority=a)


@pytest.mark.parametrize("listing_overrides", [
    {"total_count": 101},
    {"artifacts": []},
    {"artifacts": [
        {"id": 301, "name": "ef02-results-42-1", "expired": False},
        {"id": 302, "name": "ef02-results-42-1", "expired": False},
    ]},
    {"artifacts": [{"id": 301, "name": "ef02-results-42-1", "expired": True}]},
])
def test_wrong_github_artifact_never_grants_authority(monkeypatch, listing_overrides):
    event, report, provenance = evidence()
    a = authorized(monkeypatch, archived(event, report), listing_overrides=listing_overrides)
    with pytest.raises(ProvenanceError, match="PROVENANCE_UNVERIFIED"):
        verify_provenance(event, report, provenance, authority=a)


def test_independent_archive_must_match_untrusted_payload(monkeypatch):
    event, report, provenance = evidence()
    modified = dict(report)
    modified["succeeded"] = 0
    a = authorized(monkeypatch, archived(event, modified))
    assert not a.verify_artifact(provenance, event=event, batch_result=report)


def test_duplicate_json_keys_cannot_authenticate(monkeypatch):
    event, report, provenance = evidence()
    bad = b'{"kind":"x","kind":"y"}'
    a = authorized(monkeypatch, archived(event, report, report_bytes=bad))
    assert not a.verify_artifact(provenance, event=event, batch_result=report)


def test_missing_or_untrusted_credentials_fail_closed(monkeypatch):
    event, report, provenance = evidence()
    a = GitHubRestArtifactAuthority(
        repository=REPO, workflow_id=123, token_provider=lambda: "")
    monkeypatch.setattr(a, "_read_json", lambda *a: pytest.fail("network accessed without token"))
    assert not a.verify_artifact(provenance, event=event, batch_result=report)
    assert not GitHubRestArtifactAuthority(
        repository=REPO, workflow_id=123, token_provider=lambda: "secret\nother"
    ).verify_artifact(provenance, event=event, batch_result=report)


def test_provenance_digest_cannot_override_authenticated_archive(monkeypatch):
    event, report, provenance = evidence()
    a = authorized(monkeypatch, archived(event, report))
    from dataclasses import replace
    altered = replace(provenance, artifact_digest="sha256:" + "0" * 64)
    assert not a.verify_artifact(altered, event=event, batch_result=report)


def test_signed_redirect_never_forwards_credentials():
    redirect = _NoCredentialRedirect()
    original = urllib.request.Request(
        "https://api.github.com/repos/trung-via/ai-commerce/actions/artifacts/301/zip",
        headers={"Authorization": "Bearer test-token", "User-Agent": "test"}, method="GET")
    response = redirect.redirect_request(
        original, None, 302, "Moved", {}, "https://example.blob.core.windows.net/file?signed=yes")
    assert response.get_header("Authorization") is None
    assert response.full_url.startswith("https://")
    with pytest.raises(urllib.error.HTTPError):
        redirect.redirect_request(original, None, 302, "Moved", {}, "http://evil.example/artifact")


def test_caller_flag_without_authority_still_fails():
    event, report, provenance = evidence()
    with pytest.raises(ProvenanceError, match="PROVENANCE_UNVERIFIED"):
        verify_provenance(event, report, provenance)


def test_artifact_zip_get_uses_github_rest_api_accept_header(monkeypatch):
    """The zip endpoint returns a redirect only for the GitHub REST media type."""
    from ai_commerce.remote import github_artifact_authority as ga

    captured = []
    def fake_fetch(url, token, *, max_bytes, accept, timeout):
        captured.append((url, token, max_bytes, accept, timeout))
        return b"test-archive-bytes"

    monkeypatch.setattr(ga, "_fetch", fake_fetch)
    url = "https://api.github.com/repos/trung-via/ai-commerce/actions/artifacts/301/zip"
    assert ga.GitHubRestArtifactAuthority._read_bytes(
        url, "test-token-ONLY", timeout=7) == b"test-archive-bytes"
    assert captured == [
        (url, "test-token-ONLY", ga._MAX_ZIP, ga._HEADERS["Accept"], 7)
    ]
    assert captured[0][3] == "application/vnd.github+json"


def test_artifact_zip_get_uses_same_accept_as_json_endpoint(monkeypatch):
    """The artifact ZIP REST request and metadata REST request share API Accept."""
    from ai_commerce.remote import github_artifact_authority as ga

    values = []
    def fake_fetch(url, token, *, max_bytes, accept, timeout):
        values.append((max_bytes, accept))
        return b"{}"

    monkeypatch.setattr(ga, "_fetch", fake_fetch)
    ga.GitHubRestArtifactAuthority._read_json(
        "https://api.github.com/repos/trung-via/ai-commerce/actions/runs/42",
        "test-token-ONLY", timeout=3)
    ga.GitHubRestArtifactAuthority._read_bytes(
        "https://api.github.com/repos/trung-via/ai-commerce/actions/artifacts/301/zip",
        "test-token-ONLY", timeout=3)
    assert values == [
        (ga._MAX_JSON, ga._HEADERS["Accept"]),
        (ga._MAX_ZIP, ga._HEADERS["Accept"]),
    ]
