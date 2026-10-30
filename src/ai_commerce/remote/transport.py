"""GitHub REST evidence channel. No AIOS tokens, no auto merge to main."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_commerce.engineering.git_byte_consistency import (GitByteConsistencyError, assert_candidate_blob_portable)


class RemoteTransportError(RuntimeError):
    pass


class CandidateIntegrityError(RemoteTransportError):
    """Workspace bytes do not match the verified EF01 manifest."""


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

    def pre_spawn_checkpoint(self, *, source_sha: str, attempt_id: str,
                             fingerprint: str) -> str:
        """Write-once remote boundary; lack of confirmation forbids EF01 spawn.

        This is NOT proof that an EF01 child or provider was ever invoked.
        A transport timeout can leave a ref behind: the old claim still
        prevents any automatic re-execution with the same attempt_id.
        """
        key = self.attempt_key(attempt_id)
        claim = self.api("GET", "/git/ref/heads/ef02/claims/" + key)
        claim_sha = claim.get("object", {}).get("sha")
        if not isinstance(claim_sha, str) or len(claim_sha) != 40:
            raise RemoteTransportError("PRESPAWN_CLAIM_REF_INVALID")
        parent = self.api("GET", "/git/commits/" + claim_sha)
        claim_message = ("EF02 CLAIM\n\nfingerprint=" + fingerprint +
                         "\nattempt_id=" + attempt_id)
        if (parent.get("message") != claim_message or
                [p.get("sha") for p in parent.get("parents", [])] != [source_sha]):
            raise RemoteTransportError("PRESPAWN_CLAIM_IDENTITY_MISMATCH")
        message = ("EF02 PRESPAWN\n\nfingerprint=" + fingerprint +
                   "\nattempt_id=" + attempt_id)
        checkpoint = self.api("POST", "/git/commits",
                              {"message": message, "tree": parent["tree"]["sha"],
                               "parents": [claim_sha]})
        ref = "ef02/pre-spawn/" + key
        self._create_ref(ref, checkpoint["sha"])
        return "refs/heads/" + ref


    def spawned_checkpoint(self, *, source_sha: str, attempt_id: str,
                           fingerprint: str, child_pid: int) -> str:
        """One-time remote observation of EF01 child PID, not provider proof.

        Bind this checkpoint to the existing immutable pre-spawn and claim
        ancestry. Failure to confirm does not imply that the child never ran.
        """
        if type(child_pid) is not int or child_pid <= 0:
            raise RemoteTransportError("SPAWNED_PID_INVALID")
        key = self.attempt_key(attempt_id)
        claim = self.api("GET", "/git/ref/heads/ef02/claims/" + key)
        claim_sha = claim.get("object", {}).get("sha")
        initial = self.api("GET", "/git/commits/" + claim_sha)
        expected_claim = ("EF02 CLAIM\n\nfingerprint=" + fingerprint +
                          "\nattempt_id=" + attempt_id)
        if (initial.get("message") != expected_claim or
                [p.get("sha") for p in initial.get("parents", [])] != [source_sha]):
            raise RemoteTransportError("SPAWNED_CLAIM_IDENTITY_MISMATCH")
        pre = self.api("GET", "/git/ref/heads/ef02/pre-spawn/" + key)
        pre_sha = pre.get("object", {}).get("sha")
        stage = self.api("GET", "/git/commits/" + pre_sha)
        expected_pre = ("EF02 PRESPAWN\n\nfingerprint=" + fingerprint +
                        "\nattempt_id=" + attempt_id)
        if (stage.get("message") != expected_pre or
                [p.get("sha") for p in stage.get("parents", [])] != [claim_sha] or
                stage.get("tree", {}).get("sha") != initial.get("tree", {}).get("sha")):
            raise RemoteTransportError("SPAWNED_PRESPAWN_IDENTITY_MISMATCH")
        message = ("EF02 SPAWNED\n\nfingerprint=" + fingerprint +
                   "\nattempt_id=" + attempt_id + "\nchild_pid=" + str(child_pid))
        marked = self.api("POST", "/git/commits",
                          {"message": message, "tree": stage["tree"]["sha"],
                           "parents": [pre_sha]})
        ref = "ef02/spawned/" + key
        self._create_ref(ref, marked["sha"])
        return "refs/heads/" + ref

    def verify_reused_candidate(self, approved: dict) -> None:
        """Verify original immutable commit, candidate ref and prior distributed claim."""
        attempt_id = approved["attempt_id"]
        key = self.attempt_key(attempt_id)
        if approved["candidate_ref"] != "refs/heads/ef02/candidate/" + key:
            raise RemoteTransportError("REUSE_REF_NAME_MISMATCH")
        ref = self.api("GET", "/git/ref/heads/ef02/candidate/" + key)
        if ref.get("object", {}).get("sha") != approved["candidate_sha"]:
            raise RemoteTransportError("REUSE_REF_SHA_CHANGED")
        candidate = self.api("GET", "/git/commits/" + approved["candidate_sha"])
        if (candidate.get("message") != "EF02 candidate attempt " + attempt_id
            or candidate.get("tree", {}).get("sha") != approved["tree_sha"]
            or [p["sha"] for p in candidate.get("parents", [])] != [approved["parent_sha"]]):
            raise RemoteTransportError("REUSE_COMMIT_MISMATCH")
        claim = self.api("GET", "/git/ref/heads/ef02/claims/" + key)
        initial = self.api("GET", "/git/commits/" + claim["object"]["sha"])
        msg = initial.get("message", "")
        if (not msg.startswith("EF02 CLAIM\n\nfingerprint=")
            or not msg.endswith("\nattempt_id=" + attempt_id)
            or [p["sha"] for p in initial.get("parents", [])] != [approved["source_sha"]]):
            raise RemoteTransportError("REUSE_CLAIM_MISMATCH")

    def candidate(self, *, source_sha: str, attempt_id: str,
                  workspace: Path, paths: list[str],
                  expected_hashes: dict[str, str | None]) -> dict:
        """Publish precisely the EF01-verified bytes, including deletions.

        Validate the entire manifest and snapshot every file before any GitHub
        blob/ref write. A changed workspace must never become a candidate.
        """
        if not paths:
            raise RemoteTransportError("EMPTY_CANDIDATE")
        if (not isinstance(expected_hashes, dict) or len(set(paths)) != len(paths)
                or set(expected_hashes) != set(paths)):
            raise CandidateIntegrityError("CANDIDATE_HASH_MANIFEST_MISMATCH")

        prepared = []
        for relative in paths:
            path = workspace / relative
            if path.is_symlink():
                raise RemoteTransportError("CANDIDATE_SYMLINK_UNSUPPORTED")
            if not path.exists():
                if expected_hashes[relative] is not None:
                    raise CandidateIntegrityError("CANDIDATE_DELETION_HASH_MISMATCH:" + relative)
                prepared.append((relative, None, None))
                continue
            if not path.is_file():
                raise RemoteTransportError("NON_FILE_CANDIDATE")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected_hashes[relative]:
                raise CandidateIntegrityError("CANDIDATE_BYTES_MISMATCH:" + relative)
            mode = "100755" if os.name != "nt" and path.stat().st_mode & 0o111 else "100644"
            try:
                assert_candidate_blob_portable(workspace, relative, content)
            except GitByteConsistencyError as exc:
                raise CandidateIntegrityError(str(exc)) from exc
            prepared.append((relative, content, mode))

        base = self.api("GET", "/git/commits/" + source_sha)
        tree_items = []
        for relative, content, mode in prepared:
            if content is None:
                tree_items.append({"path": relative, "mode": "100644", "type": "blob", "sha": None})
                continue
            encoded = base64.b64encode(content).decode("ascii")
            blob = self.api("POST", "/git/blobs", {"content": encoded, "encoding": "base64"})
            tree_items.append({"path": relative, "mode": mode, "type": "blob", "sha": blob["sha"]})
        remote_tree = self.api("POST", "/git/trees",
                               {"base_tree": base["tree"]["sha"], "tree": tree_items})
        commit = self.api("POST", "/git/commits",
                          {"message": "EF02 candidate attempt " + attempt_id,
                           "tree": remote_tree["sha"], "parents": [source_sha]})
        name = "ef02/candidate/" + self.attempt_key(attempt_id)
        self._create_ref(name, commit["sha"])
        return {"candidate_sha": commit["sha"], "candidate_ref": "refs/heads/" + name}

    def integrated_source(self, *, source_sha: str, attempt_id: str,
                          dependencies: list[dict]) -> dict:
        """Combine pinned dependency candidate trees (never merge to main).

        Distinct edits to the same path create an explicit integration conflict.
        Transitive changes with identical blobs merge deterministically.
        """
        if not dependencies:
            return {"effective_sha": source_sha, "integration_ref": None}
        base = self.api("GET", "/git/commits/" + source_sha)
        def entries(tree_sha: str) -> dict:
            payload = self.api("GET", "/git/trees/" + tree_sha + "?recursive=1")
            if payload.get("truncated"):
                raise RemoteTransportError("INTEGRATION_TREE_TRUNCATED")
            return {e["path"]: (e["mode"], e["type"], e["sha"])
                    for e in payload.get("tree", [])
                    if e["type"] in ("blob", "commit")}
        original = entries(base["tree"]["sha"])
        overlay: dict[str, tuple | None] = {}
        for dependency in dependencies:
            candidate_sha = dependency.get("candidate_sha")
            if not candidate_sha:
                raise RemoteTransportError("DEPENDENCY_CANDIDATE_MISSING")
            candidate = self.api("GET", "/git/commits/" + candidate_sha)
            modified = entries(candidate["tree"]["sha"])
            for path in sorted(set(original) | set(modified)):
                old = original.get(path)
                new = modified.get(path)
                if old == new:
                    continue
                if path in overlay and overlay[path] != new:
                    raise RemoteTransportError("INTEGRATION_CONFLICT:" + path)
                overlay[path] = new
        if not overlay:
            return {"effective_sha": source_sha, "integration_ref": None}
        changes = []
        for path, item in sorted(overlay.items()):
            if item is None:
                changes.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            else:
                mode, kind, sha = item
                changes.append({"path": path, "mode": mode, "type": kind, "sha": sha})
        target = self.api("POST", "/git/trees",
                          {"base_tree": base["tree"]["sha"], "tree": changes})
        commit = self.api("POST", "/git/commits",
                          {"message": "EF02 dependency integration for " + attempt_id,
                           "tree": target["sha"], "parents": [source_sha]})
        ref = "ef02/integrated/" + self.attempt_key(attempt_id)
        self._create_ref(ref, commit["sha"])
        return {"effective_sha": commit["sha"], "integration_ref": "refs/heads/" + ref}

    def comment(self, issue_number: int, text: str) -> None:
        if issue_number <= 0:
            return
        self.api("POST", "/issues/" + str(issue_number) + "/comments", {"body": text})
