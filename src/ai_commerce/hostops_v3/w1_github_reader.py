"""W1.4 Phase 2 — fixed GitHub REST read-only adapter, never a HostOps dispatcher.

A future isolated broker may call this over authenticated HTTPS with a
short-lived installation token. A token in the *public self-hosted Windows SID*
is never secure: DO NOT wire this module into Actions, HostOps worker or CLI.

Even valid HTTPS response metadata is not independent broker/host attestation.
Returned observations are NO_EFFECT and cannot authorize file operations.
"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request

from . import w1_bridge
from .contract import CONTROL_REPOSITORY, canonical, decode, deny, digest

_API_BASE = "https://api.github.com/repos/" + CONTROL_REPOSITORY
_REPO_HTML = "https://github.com/" + CONTROL_REPOSITORY
_API_VERSION = "2022-11-28"
_MAX_REPO = 65536
_MAX_ISSUE = 131072


class _DenyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _build_https_opener():
    # Do not let inherited HTTP(S)_PROXY env redirect sensitive auth headers
    # through an unapproved proxy. TLS certificate validation remains enabled.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _DenyRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )


def _bounded_github_get(opener, url, token, *, limit):
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": "ai-commerce-hostops-w14-readonly",
            "Cache-Control": "no-store",
        },
    )
    try:
        with opener.open(request, timeout=10) as response:
            code = response.getcode()
            final_url = response.geturl()
            media = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
            if (code != 200 or final_url != url or
                    media not in {"application/json", "application/vnd.github+json"}):
                deny("W14_GITHUB_UNTRUSTED_RESPONSE")
            raw = response.read(limit + 1)
            if not 0 < len(raw) <= limit:
                deny("W14_GITHUB_RESPONSE_LIMIT")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            TimeoutError, ValueError, UnicodeError):
        # Never print original URL errors, token-bearing headers, traceback,
        # private Issue body or a decoded error payload.
        deny("W14_GITHUB_API_UNAVAILABLE")
    return decode(raw, limit=limit)


def read_private_issue_snapshot(token, *, issue_number):
    """GET exactly one private repository and one Issue using TLS and a token.

    No env-token lookup, retries, persistence, pagination, redirects, arbitrary
    URL, writes, broker claim, worker or ledger. The caller must source the token
    from an independently isolated short-lived broker, which is NOT provided.
    A test-injected opener is NOT real GitHub origin proof.
    """
    if (type(issue_number) is not int or not 1 <= issue_number <= 2147483647):
        deny("W14_ISSUE_NUMBER_DENIED")
    if (type(token) is not str or not 20 <= len(token) <= 1024 or
            not token.isascii() or not all(
                ("A" <= c <= "Z" or "a" <= c <= "z" or "0" <= c <= "9" or
                 c in "._-") for c in token)):
        deny("W14_CREDENTIAL_UNAVAILABLE")
    opener = _build_https_opener()
    repo = _bounded_github_get(opener, _API_BASE, token, limit=_MAX_REPO)
    owner = repo.get("owner") if type(repo) is dict else None
    if (type(repo) is not dict or
            repo.get("full_name") != CONTROL_REPOSITORY or
            repo.get("private") is not True or
            repo.get("html_url") != _REPO_HTML or
            type(repo.get("id")) is not int or repo["id"] <= 0 or
            type(owner) is not dict or
            owner.get("login") != w1_bridge.W14_EXPECTED_AUTHOR or
            type(owner.get("id")) is not int or owner["id"] <= 0):
        deny("W14_GITHUB_REPOSITORY_DENIED")

    url = _API_BASE + "/issues/" + str(issue_number)
    issue = _bounded_github_get(opener, url, token, limit=_MAX_ISSUE)
    user = issue.get("user") if type(issue) is dict else None
    if (type(issue) is not dict or
            type(issue.get("number")) is not int or
            issue["number"] != issue_number or
            type(issue.get("id")) is not int or issue["id"] <= 0 or
            issue.get("repository_url") != _API_BASE or
            issue.get("url") != url or
            issue.get("html_url") != _REPO_HTML + "/issues/" + str(issue_number) or
            issue.get("state") != "open" or
            "pull_request" in issue or
            type(user) is not dict or
            user.get("login") != w1_bridge.W14_EXPECTED_AUTHOR or
            type(user.get("id")) is not int or
            user["id"] != owner["id"] or
            type(issue.get("body")) is not str):
        deny("W14_GITHUB_ISSUE_DENIED")
    body = issue["body"]
    if len(body) > w1_bridge.W14_BODY_MAX or not body.isascii():
        deny("W14_ISSUE_BODY_DENIED")
    snapshot = {
        "schema": w1_bridge.W14_ISSUE_SCHEMA,
        "repository": CONTROL_REPOSITORY,
        "issue_number": issue_number,
        "issue_state": "open",
        "author_login": w1_bridge.W14_EXPECTED_AUTHOR,
        "body": body,
    }
    return {
        "schema": "hostops.personal.w14_api_read.v1",
        "status": "GITHUB_API_FIELDS_CHECKED_NO_EFFECT",
        "snapshot": snapshot,
        "repo_id": repo["id"],
        "issue_id": issue["id"],
        "snapshot_sha256": digest(canonical(snapshot)),
        "github_origin_independently_attested": False,
        "external_token_custody_proven": False,
        "anti_rollback_proven": False,
        "host_effect_authorized": False,
        "remote_write_enabled": False,
    }


def inspect_github_issue_readonly(token, *, issue_number, expected_source_sha,
                                  allowed_signers, expected_signers_sha256,
                                  ssh_keygen, expected_verifier_sha256, now=None):
    """Combine fixed REST read and OpenSSH verification; return only safe fields.

    The caller MUST still obtain independent authenticated origin, immutable
    Issue digest and anti-rollback before any separate HostOps authorization.
    """
    fetched = read_private_issue_snapshot(token, issue_number=issue_number)
    verified = w1_bridge.inspect_private_issue_snapshot(
        fetched["snapshot"], issue_number=issue_number,
        expected_source_sha=expected_source_sha,
        allowed_signers=allowed_signers,
        expected_signers_sha256=expected_signers_sha256,
        ssh_keygen=ssh_keygen,
        expected_verifier_sha256=expected_verifier_sha256, now=now)
    return {
        "schema": "hostops.personal.w14_github_readonly_result.v1",
        "status": "GITHUB_ISSUE_SIGNATURE_CHECKED_NO_EFFECT",
        "request_id": verified["request_id"],
        "issue_number": issue_number,
        "source_sha": verified["source_sha"],
        "repo_id": fetched["repo_id"],
        "issue_id": fetched["issue_id"],
        "snapshot_sha256": fetched["snapshot_sha256"],
        "proposal_sha256": verified["proposal_sha256"],
        "human_signature_verified": True,
        "github_origin_independently_attested": False,
        "external_token_custody_proven": False,
        "replay_prevented": False,
        "host_effect_authorized": False,
        "remote_write_enabled": False,
        "independent_host_receipt": False,
    }
