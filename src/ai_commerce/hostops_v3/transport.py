"""Outbound GitHub adapter and stable workflow/CLI boundary. No host CI runner.

The installed transport account only polls HTTPS and talks to the local broker.
It never downloads/execut es source, scripts, Actions artifacts or Issue commands.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import urllib.error
import urllib.request

from .broker import client_exchange, connect
from .contract import (MAX_WIRE, REPOSITORY, Denied, blob, canonical, decode, deny,
                       digest, envelope, public_receipt, receipt, sha)

WORKFLOW_PATH = ".github/workflows/hostops-v3.yml"


def validation(raw):
    e, r = envelope(raw)
    return {"schema": "hostops.transport.v3", "status": "STRUCTURE_ONLY",
            "request_sha256": r.fingerprint, "envelope_sha256": digest(canonical(e)),
            "release_id": r.value["release_id"], "source_sha": r.value["source_sha"],
            "operation": r.value["operation"]}


def submit(config, raw, *, reconcile=False):
    e, request = envelope(raw)  # Bounded, typed before connecting. Broker verifies all signatures.
    payload = canonical({"schema": "hostops.ipc.v3", "mode": "reconcile" if reconcile else "execute", "envelope": e})
    key = (Path(config["policy_dir"]) / "ipc.key").read_bytes()
    if len(key) != 32:
        deny("IPC_KEY_INVALID")
    channel = connect(config["endpoint"])
    try:
        response = client_exchange(channel, key, payload)
        if (response["receipt"]["request_sha256"] != request.fingerprint or
                response["receipt"]["release_id"] != request.value["release_id"]):
            deny("IPC_RESPONSE_MISMATCH")
        return response
    except (Denied, OSError):
        # Once a connection may have delivered a request, a lost response cannot
        # be interpreted as proof that the write did not happen.
        return {"receipt": receipt(request, "UNKNOWN", "RECONCILIATION_REQUIRED")}
    finally:
        channel.close()


def report(response):
    # Strict public projection: never render unexpected exception text or fields.
    value = public_receipt(response["receipt"])
    return "HOSTOPS_V3_RECEIPT\n```json\n" + canonical(value).decode("ascii").rstrip() + "\n```"


class GitHub:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, data=None):
        # Closed origin and repository. No URLs accepted from API/Issue responses.
        if not path.startswith("/repos/" + REPOSITORY + "/") and path != "/repos/" + REPOSITORY:
            deny("TRANSPORT_ROUTE_DENIED")
        req = urllib.request.Request("https://api.github.com" + path,
            data=canonical(data) if data is not None else None, method=method,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ai-commerce-hostops-v3"})
        # Reject redirects so an authentication header can never leave the origin.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                return decode(raw, 2 * 1024 * 1024)
        except (OSError, urllib.error.URLError):
            deny("GITHUB_UNAVAILABLE")

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, data):
        return self.request("POST", path, data)


def provenance(api, issue_number, run_id, workflow_sha256):
    if type(issue_number) is not int or type(run_id) is not int or issue_number < 1 or run_id < 1:
        deny("BAD_GITHUB_ID")
    prefix = "/repos/" + REPOSITORY
    repo = api.get(prefix)
    issue = api.get(prefix + "/issues/" + str(issue_number))
    run = api.get(prefix + "/actions/runs/" + str(run_id))
    if (repo.get("private") is not True or repo.get("full_name") != REPOSITORY or
            issue.get("user", {}).get("login") != "trung-via" or issue.get("pull_request") is not None or
            not str(issue.get("title", "")).startswith("[HOSTOPS3] ")):
        deny("GITHUB_IDENTITY_DENIED")
    if (run.get("event") != "issues" or run.get("status") != "completed" or run.get("conclusion") != "success" or
            run.get("path") != WORKFLOW_PATH or run.get("display_title") != "HostOpsV3 Issue #" + str(issue_number) or
            run.get("head_repository", {}).get("full_name") != REPOSITORY):
        deny("WORKFLOW_UNVERIFIED")
    source = sha(run.get("head_sha"), 40)
    file = api.get(prefix + "/contents/" + WORKFLOW_PATH + "?ref=" + source)
    try:
        content = base64.b64decode(file["content"])
    except (ValueError, KeyError, TypeError):
        deny("WORKFLOW_UNVERIFIED")
    if digest(content) != workflow_sha256:
        deny("WORKFLOW_DIGEST_MISMATCH")
    body = issue.get("body")
    if type(body) is not str:
        deny("BAD_JSON")
    raw = body.encode("utf-8")
    envelope(raw)
    return raw


def poll_once(api, config, release, issue_number, run_id, *, send=submit):
    raw = provenance(api, issue_number, run_id, release["workflow_sha256"])
    _, request = envelope(raw)
    r = request.value
    # Disclosure has a distinct exact Human signature, recipient and bounded chunk.
    recipient = "github:" + REPOSITORY + "/issues/" + str(issue_number)
    if r["operation"] == "disclose" and (r["parameters"]["recipient"] != recipient or r["parameters"]["length"] > 32768):
        deny("DISCLOSURE_RECIPIENT_DENIED")
    response = send(config, raw)
    body = report(response)
    if "disclosure_b64" in response:
        if r["operation"] != "disclose" or response["receipt"]["status"] != "DISCLOSED":
            deny("UNSOLICITED_DISCLOSURE")
        content = blob(response["disclosure_b64"])
        if (len(content) != response["receipt"]["result"]["bytes"] or
                digest(content) != response["receipt"]["result"]["chunk_sha256"]):
            deny("DISCLOSURE_HASH_MISMATCH")
        body += "\nHUMAN_SIGNED_DISCLOSURE_BASE64\n" + response["disclosure_b64"]
    # Only a sanitized receipt enters the outbox. Confidential bytes are never logged.
    outbox = Path(config["transport_state_dir"]) / ("receipt-" + request.fingerprint + ".json")
    with outbox.open("wb") as f:
        f.write(canonical(public_receipt(response["receipt"])))
        f.flush()
        import os
        os.fsync(f.fileno())
    api.post("/repos/" + REPOSITORY + "/issues/" + str(issue_number) + "/comments", {"body": body})
    return response["receipt"]


def poll_inbox(api, config, release):
    """One bounded scan. Repeating a request cannot repeat a filesystem effect."""
    prefix = "/repos/" + REPOSITORY
    issues = api.get(prefix + "/issues?state=open&per_page=100")
    runs = api.get(prefix + "/actions/workflows/hostops-v3.yml/runs?event=issues&per_page=100")
    if type(issues) is not list or type(runs.get("workflow_runs")) is not list:
        deny("GITHUB_UNAVAILABLE")
    count = 0
    for issue in issues[:100]:
        if not str(issue.get("title", "")).startswith("[HOSTOPS3] "):
            continue
        number = issue.get("number")
        for run in runs["workflow_runs"][:100]:
            if run.get("display_title") == "HostOpsV3 Issue #" + str(number) and run.get("conclusion") == "success":
                marker = Path(config["transport_state_dir"]) / ("reported-" + str(run["id"]) + ".json")
                if marker.exists():
                    break
                value = poll_once(api, config, release, number, run["id"])
                marker.write_bytes(canonical(value))
                count += 1
                break
    return count


# V4 control-plane migration is non-executing until a separately installed and
# Human-signed v4 broker is proved. Do not feed v4 bytes to v3 submit()/poll().
from .contract import CONTROL_REPOSITORY, SOURCE_REPOSITORY, dual_repo_envelope


class ControlGitHub(GitHub):
    """Closed HTTPS API client for the PRIVATE control repository only."""

    def request(self, method, path, data=None):
        # Exact GitHub REST path shapes: no user-defined URL, traversal, encoded
        # slash, query injection or requests to other endpoint families.
        import re
        prefix = "/repos/" + CONTROL_REPOSITORY
        literal = {prefix,
                   prefix + "/issues?state=open&per_page=100",
                   prefix + "/actions/workflows/hostops-v3.yml/runs?event=issues&per_page=100"}
        exact = (path in literal or
                 re.fullmatch(re.escape(prefix) + r"/issues/[1-9][0-9]*", path) is not None or
                 re.fullmatch(re.escape(prefix) + r"/actions/runs/[1-9][0-9]*", path) is not None or
                 re.fullmatch(re.escape(prefix) + r"/contents/\.github/workflows/hostops-v3\.yml\?ref=[0-9a-f]{40}", path) is not None)
        post_ok = re.fullmatch(re.escape(prefix) + r"/issues/[1-9][0-9]*/comments", path) is not None
        if not ((method == "GET" and exact) or (method == "POST" and post_ok and type(data) is dict and set(data) == {"body"})):
            deny("TRANSPORT_ROUTE_DENIED")
        # Base class controls HTTPS origin, redirects and bounded response.
        # It is fixed to the public v3 repository, so temporarily substitute
        # only the literal fixed control route in the path check by using a
        # dedicated (private) method rather than weakening v3 route checks.
        req = urllib.request.Request("https://api.github.com" + path,
            data=canonical(data) if data is not None else None, method=method,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ai-commerce-hostops-v4-control"})
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                return decode(raw, 2 * 1024 * 1024)
        except (OSError, urllib.error.URLError):
            deny("GITHUB_UNAVAILABLE")


def control_provenance(api, issue_number, run_id, workflow_sha256, expected_source_sha):
    """Private GitHub source proof. Returns v4 bytes, NEVER invokes the broker."""
    if type(issue_number) is not int or issue_number <= 0 or type(run_id) is not int or run_id <= 0:
        deny("BAD_GITHUB_ID")
    sha(workflow_sha256)
    sha(expected_source_sha, 40)
    prefix = "/repos/" + CONTROL_REPOSITORY
    repo = api.get(prefix)
    issue = api.get(prefix + "/issues/" + str(issue_number))
    run = api.get(prefix + "/actions/runs/" + str(run_id))
    if (repo.get("private") is not True or repo.get("full_name") != CONTROL_REPOSITORY or
            issue.get("number") != issue_number or issue.get("pull_request") is not None or
            issue.get("user", {}).get("login") != "trung-via" or
            not str(issue.get("title", "")).startswith("[HOSTOPS3] ")):
        deny("CONTROL_REPOSITORY_DENIED")
    if (run.get("event") != "issues" or run.get("status") != "completed" or
            run.get("conclusion") != "success" or run.get("path") != WORKFLOW_PATH or
            run.get("display_title") != "HostOpsV3 Issue #" + str(issue_number) or
            run.get("head_repository", {}).get("full_name") != CONTROL_REPOSITORY):
        deny("WORKFLOW_UNVERIFIED")
    control_sha = sha(run.get("head_sha"), 40)
    f = api.get(prefix + "/contents/" + WORKFLOW_PATH + "?ref=" + control_sha)
    try:
        content = base64.b64decode(f["content"], validate=True)
    except (ValueError, KeyError, TypeError):
        deny("WORKFLOW_UNVERIFIED")
    if digest(content) != workflow_sha256:
        deny("WORKFLOW_DIGEST_MISMATCH")
    if type(issue.get("body")) is not str:
        deny("BAD_JSON")
    raw = issue["body"].encode("utf-8")
    _, request = dual_repo_envelope(raw)
    if request.value["source_sha"] != expected_source_sha:
        deny("SOURCE_SHA_MISMATCH")
    return raw
