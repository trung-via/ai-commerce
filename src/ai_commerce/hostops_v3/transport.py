"""Outbound GitHub adapter and stable workflow/CLI boundary. No host CI runner.

The installed transport account only polls HTTPS and talks to the local broker.
It never downloads/execut es source, scripts, Actions artifacts or Issue commands.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
import urllib.error
import urllib.request

from .broker import client_exchange, connect
from .contract import (MAX_WIRE, REPOSITORY, Denied, blob, canonical, decode, deny,
                       digest, envelope, public_receipt, receipt, sha, shape,
                       dual_repo_envelope, public_receipt_v4, receipt_v4)

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
                 re.fullmatch(re.escape(prefix) + r"/issues/[1-9][0-9]*/comments\?per_page=100", path) is not None or
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



READ_ONLY_V4 = frozenset({"volumes", "stat", "list", "find", "hash", "read"})


def validation_v4(raw):
    """Structure-only V4 envelope validation; never authenticates or executes."""
    e, r = dual_repo_envelope(raw)
    if r.value["operation"] not in READ_ONLY_V4:
        deny("OPERATION_NOT_ENABLED")
    return {"schema": "hostops.transport.v4", "status": "STRUCTURE_ONLY",
            "request_sha256": r.fingerprint, "envelope_sha256": digest(canonical(e)),
            "source_repository": r.value["source_repository"],
            "control_repository": r.value["control_repository"],
            "release_id": r.value["release_id"], "source_sha": r.value["source_sha"],
            "operation": r.value["operation"]}


def submit_v4(config, raw, *, reconcile=False):
    """V4 HMAC client; v3 CLI cannot invoke this function."""
    if config.get("schema") == "hostops.config.v4.personal":
        deny("PERSONAL_MODE_NOT_ENROLLED")
    envelope_data, request = dual_repo_envelope(raw)
    if request.value["operation"] not in READ_ONLY_V4:
        deny("OPERATION_NOT_ENABLED")
    if not config.get("enabled") or (Path(config["policy_dir"]) / "KILL").exists():
        deny("KILL_SWITCH")
    if (config.get("schema") != "hostops.config.v4" or
            config.get("source_repository") != SOURCE_REPOSITORY or
            config.get("control_repository") != CONTROL_REPOSITORY):
        deny("BAD_CONFIG")
    payload = canonical({"schema": "hostops.ipc.v4",
                         "mode": "reconcile" if reconcile else "execute",
                         "envelope": envelope_data})
    key = (Path(config["policy_dir"]) / "ipc.key").read_bytes()
    if len(key) != 32:
        deny("IPC_KEY_INVALID")
    channel = connect(config["endpoint"])
    try:
        response = client_exchange(channel, key, payload,
                                   verify_receipt=public_receipt_v4)
        if (set(response) != {"receipt"} or
                response["receipt"]["request_sha256"] != request.fingerprint or
                response["receipt"]["release_id"] != request.value["release_id"] or
                response["receipt"]["source_sha"] != request.value["source_sha"]):
            deny("IPC_RESPONSE_MISMATCH")
        return response
    except (Denied, OSError):
        return {"receipt": receipt_v4(request, "UNKNOWN", "RECONCILIATION_REQUIRED")}
    finally:
        channel.close()


def report_v4(response):
    """Closed sanitized projection, never includes file data or raw errors."""
    if type(response) is not dict or set(response) != {"receipt"}:
        deny("UNSOLICITED_DISCLOSURE")
    observed = public_receipt_v4(response["receipt"])
    return "HOSTOPS_V4_RECEIPT\n" + canonical(observed).decode("ascii")


def poll_once_v4(api, config, release, issue_number, run_id, *, send=submit_v4):
    """Single Private Issue, no daemon/CLI activation in this stage."""
    if config.get("schema") == "hostops.config.v4.personal":
        deny("PERSONAL_MODE_NOT_ENROLLED")
    if not config.get("enabled") or (Path(config["policy_dir"]) / "KILL").exists():
        deny("KILL_SWITCH")
    if (release.get("schema") != "hostops.release.v4" or
            release.get("source_repository") != SOURCE_REPOSITORY or
            release.get("control_repository") != CONTROL_REPOSITORY):
        deny("RELEASE_REPOSITORY_DENIED")
    raw = control_provenance(api, issue_number, run_id,
                             release["workflow_sha256"], release["source_sha"])
    _, request = dual_repo_envelope(raw)
    if request.value["operation"] not in READ_ONLY_V4:
        deny("OPERATION_NOT_ENABLED")
    if (request.value["release_id"] != release["release_id"] or
            request.value["host_id"] != config.get("host_id")):
        deny("SOURCE_UNVERIFIED")
    response = send(config, raw)
    if type(response) is not dict or set(response) != {"receipt"}:
        deny("UNSOLICITED_DISCLOSURE")
    observed = public_receipt_v4(response["receipt"])
    if (observed["request_sha256"] != request.fingerprint or
            observed["release_id"] != request.value["release_id"] or
            observed["source_sha"] != request.value["source_sha"]):
        deny("IPC_RESPONSE_MISMATCH")
    outbox = Path(config["transport_state_dir"]) / ("receipt-v4-" + request.fingerprint + ".json")
    with outbox.open("wb") as f:
        f.write(canonical(observed))
        f.flush()
        import os
        os.fsync(f.fileno())
    api.post("/repos/" + CONTROL_REPOSITORY + "/issues/" + str(issue_number) +
             "/comments", {"body": report_v4(response)})
    return observed


def poll_inbox_v4(api, config, release, *, send=submit_v4):
    """One bounded V4 private control scan; never repeats reported runs."""
    if config.get("schema") == "hostops.config.v4.personal":
        deny("PERSONAL_MODE_NOT_ENROLLED")
    if not config.get("enabled") or (Path(config["policy_dir"]) / "KILL").exists():
        deny("KILL_SWITCH")
    if (release.get("schema") != "hostops.release.v4" or
            release.get("source_repository") != SOURCE_REPOSITORY or
            release.get("control_repository") != CONTROL_REPOSITORY):
        deny("RELEASE_REPOSITORY_DENIED")
    prefix = "/repos/" + CONTROL_REPOSITORY
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
                marker = Path(config["transport_state_dir"]) / ("reported-v4-" + str(run["id"]) + ".json")
                if marker.exists():
                    break
                value = poll_once_v4(api, config, release, number, run["id"], send=send)
                marker.write_bytes(canonical(value))
                count += 1
                break
    return count



# Human-attended remote read: ChatGPT may create a private Issue, but no Issue
# can execute on this machine without a local interactive approval. No daemon.
PERSONAL_R1_SCHEMA = "hostops.personal.remote_read.v1"
PERSONAL_R1_RESULT_SCHEMA = "hostops.personal.remote_read_receipt.v1"
PERSONAL_R1_TITLE = "[HOSTOPS-R1] local_os_inventory"
PERSONAL_R1_MARKER = "HOSTOPS_R1_RESULT\n"


def personal_remote_prepare(api, issue_number, expected_source_sha, *, now=None):
    """Retrieve one owner-authored, short-lived private Issue, without effects.

    A GitHub Issue, even if owner-authored, is NOT independent Human approval.
    Live presence and exact-request approval are checked separately in CLI.
    """
    if type(issue_number) is not int or not 1 <= issue_number <= 2147483647:
        deny("BAD_GITHUB_ID")
    sha(expected_source_sha, 40)
    if now is None:
        now = int(time.time())
    if type(now) is not int or now < 1600000000:
        deny("BAD_TIME")
    prefix = "/repos/" + CONTROL_REPOSITORY
    repository = api.get(prefix)
    if (type(repository) is not dict or repository.get("private") is not True or
            repository.get("full_name") != CONTROL_REPOSITORY):
        deny("PRIVATE_CONTROL_UNVERIFIED")
    issue = api.get(prefix + "/issues/" + str(issue_number))
    if (type(issue) is not dict or issue.get("number") != issue_number or
            issue.get("state") != "open" or issue.get("pull_request") is not None or
            issue.get("title") != PERSONAL_R1_TITLE or
            type(issue.get("user")) is not dict or
            issue["user"].get("login") != "trung-via"):
        deny("OWNER_ISSUE_UNVERIFIED")
    raw_body = issue.get("body")
    if type(raw_body) is not str:
        deny("BAD_REMOTE_REQUEST")
    try:
        raw = raw_body.encode("utf-8")
    except UnicodeError:
        deny("BAD_REMOTE_REQUEST")
    request = decode(raw, limit=4096)
    shape(request, "schema request_id source_repository control_repository source_sha operation parameters issued_at expires_at")
    if (request["schema"] != PERSONAL_R1_SCHEMA or
            request["source_repository"] != SOURCE_REPOSITORY or
            request["control_repository"] != CONTROL_REPOSITORY or
            request["operation"] != "local_os_inventory" or
            type(request["parameters"]) is not dict or request["parameters"] or
            request["source_sha"] != expected_source_sha or
            type(request["request_id"]) is not str or
            re.fullmatch(r"r1-[a-f0-9]{24,40}", request["request_id"]) is None):
        deny("REMOTE_REQUEST_DENIED")
    issued = request["issued_at"]
    expires = request["expires_at"]
    if (type(issued) is not int or type(expires) is not int or
            not 1 <= expires - issued <= 600 or not issued <= now <= expires or
            now - issued > 600):
        deny("REMOTE_REQUEST_EXPIRED")
    # An Issue can have unrelated comments (e.g. the hosted-only queue
    # receipt), but any prior R1 outcome requires Human reconciliation.
    comments = api.get(prefix + "/issues/" + str(issue_number) + "/comments?per_page=100")
    if type(comments) is not list or len(comments) >= 100:
        deny("REMOTE_REPLAY_UNVERIFIED")
    for comment in comments:
        if type(comment) is not dict or type(comment.get("body")) is not str:
            deny("REMOTE_REPLAY_UNVERIFIED")
        if comment["body"].startswith((PERSONAL_R1_MARKER, "HOSTOPS_R1_UNKNOWN\n")):
            deny("REMOTE_RESULT_ALREADY_PRESENT")
    return request


def personal_supervised_scan(api):
    """Bounded private Issues discovery only; never execute an Issue body.

    A fixed-capability session still re-fetches and authenticates every
    candidate through personal_remote_prepare before requiring local consent.
    """
    prefix = "/repos/" + CONTROL_REPOSITORY
    repo = api.get(prefix)
    if (type(repo) is not dict or repo.get("private") is not True or
            repo.get("full_name") != CONTROL_REPOSITORY):
        deny("PRIVATE_CONTROL_UNVERIFIED")
    issues = api.get(prefix + "/issues?state=open&per_page=100")
    if type(issues) is not list or len(issues) >= 100:
        deny("REMOTE_QUEUE_UNBOUNDED")
    matches = []
    for issue in issues:
        if type(issue) is not dict:
            deny("REMOTE_QUEUE_UNVERIFIED")
        if issue.get("title") != PERSONAL_R1_TITLE:
            continue
        number = issue.get("number")
        if (type(number) is not int or not 1 <= number <= 2147483647 or
                issue.get("state") != "open" or issue.get("pull_request") is not None or
                type(issue.get("user")) is not dict or
                issue["user"].get("login") != "trung-via"):
            deny("REMOTE_QUEUE_UNVERIFIED")
        if number in matches:
            deny("REMOTE_QUEUE_UNVERIFIED")
        matches.append(number)
    if len(matches) > 10:
        deny("REMOTE_QUEUE_UNBOUNDED")
    return sorted(matches)


def personal_remote_publish(api, issue_number, request, inventory):
    """Post one fixed sanitized receipt, never a user-selected content blob."""
    shape(request, "schema request_id source_repository control_repository source_sha operation parameters issued_at expires_at")
    shape(inventory, "architecture logical_cpu_count")
    if (inventory["architecture"] not in ("X64", "OTHER") or
            type(inventory["logical_cpu_count"]) is not int or
            not 0 <= inventory["logical_cpu_count"] <= 512):
        deny("REMOTE_INVENTORY_INVALID")
    receipt = {
        "schema": PERSONAL_R1_RESULT_SCHEMA,
        "request_id": request["request_id"],
        "issue_number": issue_number,
        "source_sha": request["source_sha"],
        "operation": "local_os_inventory",
        "status": "OBSERVED_HUMAN_ATTENDED",
        "account_mode": "PERSONAL_SINGLE_USER",
        "result": {
            "architecture": inventory["architecture"],
            "logical_cpu_count": inventory["logical_cpu_count"],
            "current_sid_matched": True,
            "non_elevated": True,
            "console_confirmation_received": True,
            "broker_started": False,
            "hostops_file_write_requested": False,
            "ready_to_enable": False
        }
    }
    message = PERSONAL_R1_MARKER + canonical(receipt).decode("ascii")
    prefix = "/repos/" + CONTROL_REPOSITORY + "/issues/" + str(issue_number)
    try:
        response = api.post(prefix + "/comments", {"body": message})
    except Denied:
        # Posting may have reached GitHub. NEVER resend on an ambiguous result.
        deny("REMOTE_POST_UNKNOWN")
    if (type(response) is not dict or type(response.get("id")) is not int or
            response["id"] <= 0 or response.get("body") != message):
        deny("REMOTE_POST_UNKNOWN")
    return receipt
