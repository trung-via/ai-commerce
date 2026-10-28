"""GitHub REST evidence channel. No AIOS tokens, no auto merge to main."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class RemoteTransportError(RuntimeError):
    pass


class GitHubTransport:
    def __init__(self, repo: str, token: str):
        if not token or "/" not in repo or repo.count("/") != 1:
            raise RemoteTransportError("GITHUB_TRANSPORT_UNAVAILABLE")
        self.repo, self.token = repo, token
        self.prefix = "https://api.github.com/repos/" + repo

    def api(self, method: str, path: str, data: dict | None = None) -> dict:
        url = self.prefix + path
        raw = json.dumps(data, sort_keys=True).encode("utf-8") if data is not None else None
        req = Request(url, data=raw, method=method,
                      headers={"Authorization": "Bearer " + self.token,
                               "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28",
                               "Content-Type": "application/json",
                               "User-Agent": "ai-commerce-ef02"})
        try:
            with urlopen(req, timeout=35) as stream:
                return json.loads(stream.read().decode("utf-8")) if method != "DELETE" else {}
        except HTTPError as exc:
            raise RemoteTransportError("GITHUB_HTTP_" + str(exc.code)) from exc
        except (URLError, TimeoutError) as exc:
            raise RemoteTransportError("GITHUB_TRANSPORT_UNKNOWN") from exc

    def attempt_key(self, attempt_id: str) -> str:
        return hashlib.sha256((self.repo + ":" + attempt_id).encode("utf-8")).hexdigest()[:32]

    def _create_ref(self, ref: str, sha: str) -> None:
        self.api("POST", "/git/refs", {"ref": "refs/heads/" + ref, "sha": sha})

    def claim(self, *, source_sha: str, attempt_id: str, fingerprint: str) -> str:
        """First atomic ref creation wins. An abandoned claim NEVER authorizes rerun."""
        key = self.attempt_key(attempt_id)
        ref = "ef02/claims/" + key
        commit = self.api("GET", "/git/commits/" + source_sha)
        message = "EF02 CLAIM\n\nfingerprint=" + fingerprint + "\nattempt_id=" + attempt_id
        claim_obj = self.api("POST", "/git/commits",
                             {"message": message, "tree": commit["tree"]["sha"],
                              "parents": [source_sha]})
        try:
            self._create_ref(ref, claim_obj["sha"])
            return "CLAIMED"
        except RemoteTransportError as exc:
            if str(exc) != "GITHUB_HTTP_422":
                raise
        current = self.api("GET", "/git/ref/heads/" + ref)
        other = self.api("GET", "/git/commits/" + current["object"]["sha"])
        if other.get("message") == message:
            return "DUPLICATE_CLAIM_NO_REEXECUTION"
        return "ATTEMPT_ID_CONFLICT"

    def candidate(self, *, source_sha: str, attempt_id: str,
                  workspace: Path, paths: list[str]) -> dict:
        """Create-only GitHub commit/ref using source tree and scoped local bytes."""
        if not paths:
            raise RemoteTransportError("EMPTY_CANDIDATE")
        base = self.api("GET", "/git/commits/" + source_sha)
        tree_items = []
        for relative in paths:
            path = workspace / relative
            if path.is_symlink():
                raise RemoteTransportError("CANDIDATE_SYMLINK_UNSUPPORTED")
            if not path.exists():
                tree_items.append({"path": relative, "mode": "100644", "type": "blob", "sha": None})
                continue
            if not path.is_file():
                raise RemoteTransportError("NON_FILE_CANDIDATE")
            content = base64.b64encode(path.read_bytes()).decode("ascii")
            blob = self.api("POST", "/git/blobs", {"content": content, "encoding": "base64"})
            mode = "100755" if os.name != "nt" and path.stat().st_mode & 0o111 else "100644"
            tree_items.append({"path": relative, "mode": mode, "type": "blob", "sha": blob["sha"]})
        remote_tree = self.api("POST", "/git/trees",
                               {"base_tree": base["tree"]["sha"], "tree": tree_items})
        commit = self.api("POST", "/git/commits",
                          {"message": "EF02 candidate attempt " + attempt_id,
                           "tree": remote_tree["sha"], "parents": [source_sha]})
        name = "ef02/candidate/" + self.attempt_key(attempt_id)
        self._create_ref(name, commit["sha"])
        return {"candidate_sha": commit["sha"], "candidate_ref": "refs/heads/" + name}

    def comment(self, issue_number: int, text: str) -> None:
        if issue_number <= 0:
            return
        self.api("POST", "/issues/" + str(issue_number) + "/comments", {"body": text})
