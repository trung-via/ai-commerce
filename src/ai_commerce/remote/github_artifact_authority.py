"""EF02-D3: independently authenticate a final EF02 result through GitHub REST.

This supplies the previously missing *artifact* authority, not a ChatGPT
conversation writer. Operator-owned token provider and pinned repository/workflow
must be configured at a trusted composition root. Missing authorization fails
closed. Never place secrets, archives or private chat routes in GitHub Issues.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from urllib.parse import quote, urlsplit

from .completion_event import CompletionEventError, validate_completion_event
from .conversation_binding import BindingError, _decode_unique
from .github_read_retry import GitHubReadBudget
from .wake_adapter import GitHubArtifactProvenance

_API = "https://api.github.com"
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_MAX_ZIP = 16 * 1024 * 1024
_MAX_JSON = 512 * 1024
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "ai-commerce-ef02-artifact-authority",
            "X-GitHub-Api-Version": "2022-11-28"}


class _NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    """Follow HTTPS-only signed artifact redirects without forwarding GitHub credentials."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (target.scheme != "https" or not target.hostname or target.username or
                target.password or not req.get_method() == "GET"):
            raise urllib.error.HTTPError(newurl, code, "UNSAFE_ARTIFACT_REDIRECT", headers, fp)
        # Do NOT copy the original Request's Authorization (including on redirects
        # to non-GitHub hosts). A signed URL is sufficient for the artifact CDN.
        return urllib.request.Request(newurl, method="GET", headers={
            "User-Agent": _HEADERS["User-Agent"], "Accept": "application/zip",
        })


def _fetch(url: str, token: str, *, max_bytes: int, accept: str, timeout=15) -> bytes:
    if not url.startswith(_API + "/"):
        raise ValueError("GITHUB_API_URL_INVALID")
    headers = dict(_HEADERS)
    headers["Authorization"] = "Bearer " + token
    headers["Accept"] = accept
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoCredentialRedirect())
    with opener.open(urllib.request.Request(url, headers=headers, method="GET"), timeout=timeout) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("GITHUB_RESPONSE_TOO_LARGE")
    return data


def _json_from_archive(archive: zipfile.ZipFile, name: str) -> dict:
    infos = [info for info in archive.infolist() if info.filename == name]
    if len(infos) != 1 or infos[0].is_dir() or infos[0].file_size > _MAX_JSON:
        raise ValueError("ARTIFACT_FILE_INVALID")
    raw = archive.read(infos[0])
    if len(raw) > _MAX_JSON:
        raise ValueError("ARTIFACT_FILE_TOO_LARGE")
    obj = _decode_unique(raw.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("ARTIFACT_JSON_INVALID")
    return obj


class GitHubRestArtifactAuthority:
    """Real authenticated GitHub-backed ArtifactAuthority for D2.

    The operator-provided token provider is trusted local configuration and
    MUST return a token with read access to this specific repository and run.
    The pinned workflow ID prevents a different repository workflow from minting
    a lookalike artifact. This class performs no ChatGPT messaging.
    """

    def __init__(self, *, repository: str, workflow_id: int,
                 token_provider: Callable[[], str], read_budget=None):
        if (not isinstance(repository, str) or not _REPO.fullmatch(repository) or
                type(workflow_id) is not int or workflow_id <= 0 or
                not callable(token_provider)):
            raise ValueError("GITHUB_AUTHORITY_CONFIG_INVALID")
        self._repository = repository
        self._workflow_id = workflow_id
        self._token_provider = token_provider
        self._read_budget = read_budget or GitHubReadBudget()

    def _get(self, operation, url, token):
        # Parsing and identity validation happen outside the retry boundary.
        if operation in (GitHubRestArtifactAuthority._read_json,
                         GitHubRestArtifactAuthority._read_bytes):
            raw = operation
            operation = lambda u, t: raw(u, t, timeout=self._read_budget.timeout())
        return self._read_budget.read(operation, url, token)

    def verify_artifact(self, provenance: GitHubArtifactProvenance, *,
                        event: Mapping, batch_result: Mapping) -> bool:
        """Return literal True only for the exact authenticated run/archive bytes."""
        try:
            if type(provenance) is not GitHubArtifactProvenance:
                return False
            if provenance.repository != self._repository or event.get("repository") != self._repository:
                return False
            expected = validate_completion_event(event)
            if (type(provenance.run_id) is not int or provenance.run_id <= 0 or
                    type(provenance.run_attempt) is not int or provenance.run_attempt <= 0 or
                    provenance.run_id != expected["run_id"] or
                    provenance.run_attempt != expected["run_attempt"] or
                    provenance.artifact_name !=
                    f"ef02-results-{provenance.run_id}-{provenance.run_attempt}"):
                return False
            token = self._token_provider()
            if (not isinstance(token, str) or not token or len(token) > 4096 or
                    any(ch in token for ch in "\r\n\0")):
                return False

            root = _API + "/repos/" + "/".join(quote(x, safe="") for x in self._repository.split("/"))
            run = self._get(self._read_json, root + f"/actions/runs/{provenance.run_id}", token)
            if (type(run.get("id")) is not int or run["id"] != provenance.run_id or
                    type(run.get("run_attempt")) is not int or
                    run["run_attempt"] != provenance.run_attempt or
                    run.get("workflow_id") != self._workflow_id or
                    run.get("status") != "completed" or
                    run.get("event") != "issues" or
                    not isinstance(run.get("repository"), dict) or
                    run["repository"].get("full_name") != self._repository):
                return False

            listing = self._get(self._read_json,
                root + f"/actions/runs/{provenance.run_id}/artifacts?per_page=100", token)
            arts = listing.get("artifacts")
            if (not isinstance(arts, list) or type(listing.get("total_count")) is not int or
                    not 0 <= listing["total_count"] <= 100 or len(arts) != listing["total_count"]):
                # Do not silently pick one matching entry from a paginated list.
                return False
            matches = [a for a in arts if isinstance(a, dict) and
                       a.get("name") == provenance.artifact_name]
            if (len(matches) != 1 or type(matches[0].get("id")) is not int or
                    matches[0]["id"] <= 0 or matches[0].get("expired") is not False):
                return False
            item = matches[0]
            if (isinstance(item.get("workflow_run"), dict) and
                    item["workflow_run"].get("id") != provenance.run_id):
                return False
            archive_bytes = self._get(self._read_bytes,
                root + f"/actions/artifacts/{item['id']}/zip", token)
            published_digest = item.get("digest")
            actual_digest = "sha256:" + hashlib.sha256(archive_bytes).hexdigest()
            if (published_digest is not None and published_digest != actual_digest) or (
                    provenance.artifact_digest is not None and
                    provenance.artifact_digest != actual_digest):
                return False

            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                prefix = expected["batch_id"] + "/"
                embedded_event = _json_from_archive(archive, prefix + "completion_event.json")
                embedded_report = _json_from_archive(archive, prefix + "batch_result.json")
            # Independent bytes must match *exactly* the values validated by D2.
            return embedded_event == dict(expected) and embedded_report == dict(batch_result)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError,
                urllib.error.URLError, zipfile.BadZipFile, RecursionError,
                BindingError, CompletionEventError, json.JSONDecodeError):
            return False
        except Exception:
            # Never leak bearer tokens or network error payloads to an EF02 event.
            return False

    @staticmethod
    def _read_json(url: str, token: str, *, timeout=15) -> dict:
        result = _decode_unique(_fetch(url, token, max_bytes=_MAX_JSON,
                                       accept=_HEADERS["Accept"], timeout=timeout).decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("GITHUB_RESPONSE_INVALID")
        return result

    @staticmethod
    def _read_bytes(url: str, token: str, *, timeout=15) -> bytes:
        return _fetch(url, token, max_bytes=_MAX_ZIP, accept="application/zip", timeout=timeout)
