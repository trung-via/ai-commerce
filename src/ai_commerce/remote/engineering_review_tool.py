"""Issue #182: bounded, read-only GitHub evidence for an Engineering MCP tool.

No EF02 dispatch, browser access, ChatGPT conversation write, local state or
GitHub mutations. A tool call's trace ID is not a host invocation receipt.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

REPOSITORY = "trung-via/ai-commerce"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_CORRELATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_REQUIRED_CHECKS = ("tests (ubuntu-latest)", "tests (windows-latest)")
_MAX_BYTES = 1024 * 1024


class GitHubReadError(RuntimeError):
    """Safe, credential-free failure code for GitHub reads."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GitHubReadError("GITHUB_REDIRECT_BLOCKED")


def github_get(path: str, token: str) -> dict:
    """Read an allowlisted GitHub REST path only; never follows redirects."""
    if not token or "\n" in token or "\r" in token or len(token) > 4096:
        raise GitHubReadError("GITHUB_READ_AUTH_UNAVAILABLE")
    if not path.startswith("/repos/trung-via/ai-commerce/") or any(
        part in ("", ".", "..") for part in path.split("?")[0].split("/")[1:]
    ):
        raise GitHubReadError("GITHUB_PATH_REJECTED")
    req = Request(
        "https://api.github.com" + path,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "ai-commerce-engineering-review/1"},
        method="GET",
    )
    try:
        with build_opener(_NoRedirect()).open(req, timeout=10) as response:
            if response.status != 200:
                raise GitHubReadError("GITHUB_RESPONSE_INVALID")
            data = response.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            raise GitHubReadError("GITHUB_RESPONSE_TOO_LARGE")
        obj = json.loads(data)
        if not isinstance(obj, dict):
            raise GitHubReadError("GITHUB_RESPONSE_INVALID")
        return obj
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise GitHubReadError("GITHUB_READ_DENIED") from None
        if exc.code == 404:
            raise GitHubReadError("GITHUB_RESOURCE_NOT_FOUND") from None
        raise GitHubReadError("GITHUB_HTTP_FAILURE") from None
    except (URLError, OSError, TimeoutError):
        raise GitHubReadError("GITHUB_TRANSPORT_FAILURE") from None
    except (ValueError, UnicodeError):
        raise GitHubReadError("GITHUB_RESPONSE_INVALID") from None


def _sha(value: object) -> bool:
    return type(value) is str and _SHA.fullmatch(value) is not None


def _safe_utc(now: Callable[[], datetime]) -> str:
    clock = now()
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        raise ValueError("CLOCK_INVALID")
    return clock.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def engineering_review_readonly(
    repository: str, pr_number: int, correlation_id: str,
    expected_main_sha: str | None = None, *,
    get: Callable[[str, str], dict] = github_get,
    token_provider: Callable[[], str] = lambda: os.environ.get("AI_COMMERCE_GITHUB_READ_TOKEN", ""),
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    trace_factory: Callable[[], str] = lambda: uuid4().hex,
) -> dict:
    """Inspect an exact PR and main CI, with no mutation or invented receipt.

    Clients should treat verdict as a *GitHub scope review*, never ChatGPT
    server delivery proof. correlation_id is a read-only trace selector and
    repeats are safe; it is not an immutable dispatch or deduplication claim.
    """
    if repository != REPOSITORY:
        raise ValueError("REPOSITORY_NOT_ALLOWED")
    if type(pr_number) is not int or not 1 <= pr_number <= 9999999:
        raise ValueError("PR_NUMBER_INVALID")
    if type(correlation_id) is not str or not _CORRELATION.fullmatch(correlation_id):
        raise ValueError("CORRELATION_INVALID")
    if expected_main_sha is not None and not _sha(expected_main_sha):
        raise ValueError("EXPECTED_SHA_INVALID")
    timestamp = _safe_utc(now)
    trace_id = trace_factory()
    if type(trace_id) is not str or not re.fullmatch(r"[a-f0-9]{32}", trace_id):
        raise ValueError("TRACE_INVALID")
    base = f"/repos/{REPOSITORY}"
    token = token_provider()
    if type(token) is not str or not token or len(token) > 4096:
        return _blocked("GITHUB_READ_AUTH_UNAVAILABLE", pr_number, correlation_id,
                        trace_id, timestamp)
    try:
        branch = get(f"{base}/branches/main", token)
        pr = get(f"{base}/pulls/{pr_number}", token)
    except GitHubReadError as exc:
        return _blocked(str(exc), pr_number, correlation_id, trace_id, timestamp)
    except Exception:
        return _blocked("GITHUB_READ_FAILED", pr_number, correlation_id, trace_id, timestamp)
    if not isinstance(branch, dict) or not isinstance(pr, dict):
        return _blocked("GITHUB_RESPONSE_INVALID", pr_number, correlation_id, trace_id, timestamp)
    main_sha = (branch.get("commit") or {}).get("sha") if isinstance(branch.get("commit"), dict) else None
    pr_base = (pr.get("base") or {}).get("ref") if isinstance(pr.get("base"), dict) else None
    merge_sha = pr.get("merge_commit_sha")
    merged = pr.get("merged")
    if (not _sha(main_sha) or pr.get("number") != pr_number or
            pr_base != "main" or type(merged) is not bool or
            (merge_sha is not None and not _sha(merge_sha)) or
            pr.get("html_url") != f"https://github.com/{REPOSITORY}/pull/{pr_number}"):
        return _blocked("GITHUB_IDENTITY_INVALID", pr_number, correlation_id,
                        trace_id, timestamp)
    result = {
        "repository": REPOSITORY,
        "pr_number": pr_number,
        "correlation_id": correlation_id,
        "trace_id": trace_id,
        "observed_at": timestamp,
        "main_sha": main_sha,
        "merge_commit_sha": merge_sha,
        "merged": merged,
        "checks": [],
        "sources": {
            "pr": f"https://github.com/{REPOSITORY}/pull/{pr_number}",
            "main": f"https://github.com/{REPOSITORY}/commit/{main_sha}",
            "checks": f"https://github.com/{REPOSITORY}/commit/{main_sha}/checks",
        },
        "chat_delivery_proven": False,
        "native_plugin_handoff_proven": False,
    }
    if expected_main_sha is not None and main_sha != expected_main_sha:
        return result | {"verdict": "BLOCKED_STALE_MAIN"}
    if not merged:
        return result | {"verdict": "BLOCKED_PR_NOT_MERGED"}
    if merge_sha != main_sha:
        return result | {"verdict": "BLOCKED_MERGE_NOT_MAIN_HEAD"}
    try:
        data = get(f"{base}/commits/{main_sha}/check-runs?per_page=100", token)
    except GitHubReadError as exc:
        return result | {"verdict": "BLOCKED_" + str(exc)}
    except Exception:
        return result | {"verdict": "BLOCKED_GITHUB_READ_FAILED"}
    if not isinstance(data, dict) or type(data.get("total_count")) is not int or not isinstance(data.get("check_runs"), list):
        return result | {"verdict": "BLOCKED_CHECKS_INVALID"}
    checks = data["check_runs"]
    if data["total_count"] != len(checks) or len(checks) > 100:
        return result | {"verdict": "BLOCKED_CHECKS_INCOMPLETE"}
    output = []
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("name"), str) or not isinstance(check.get("status"), str) or not isinstance(check.get("conclusion"), (str, type(None))) or check.get("head_sha") != main_sha:
            return result | {"verdict": "BLOCKED_CHECKS_INVALID"}
        output.append({"name": check["name"], "status": check["status"], "conclusion": check["conclusion"]})
    result["checks"] = sorted(output, key=lambda c: c["name"])
    names = [c["name"] for c in output]
    if len(names) != len(set(names)):
        return result | {"verdict": "BLOCKED_CHECKS_AMBIGUOUS"}
    if not all(n in names for n in _REQUIRED_CHECKS):
        return result | {"verdict": "BLOCKED_CHECKS_MISSING_REQUIRED"}
    if any(c["status"] != "completed" for c in output):
        return result | {"verdict": "BLOCKED_CHECKS_PENDING"}
    if any(c["conclusion"] != "success" for c in output):
        return result | {"verdict": "BLOCKED_CHECKS_FAILED"}
    return result | {"verdict": "MERGED_MAIN_POSTMERGE_CI_PASS"}


def _blocked(code: str, number: int, correlation: str,
             trace_id: str, timestamp: str) -> dict:
    return {
        "repository": REPOSITORY,
        "pr_number": number,
        "correlation_id": correlation,
        "trace_id": trace_id,
        "observed_at": timestamp,
        "verdict": "BLOCKED_" + code.removeprefix("BLOCKED_"),
        "checks": [],
        "sources": {"pr": f"https://github.com/{REPOSITORY}/pull/{number}"},
        "chat_delivery_proven": False,
        "native_plugin_handoff_proven": False,
    }
