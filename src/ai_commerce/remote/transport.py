"""GitHub REST evidence channel. No AIOS tokens, no auto merge to main."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from http.client import HTTPException
from pathlib import Path

from ai_commerce.engineering.git_byte_consistency import assert_candidate_git_filter_consistency
from ai_commerce.engineering.runner import _snapshot
from ai_commerce.engineering.source_safety import checked_path
from .candidate_policy import (
    EVIDENCE_MARKER, canonical, commit_evidence, evidence_digest,
    validate_candidate_evidence, candidate_accepted, has_scope_review,
)
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# Exact identifiers only: never retain GitHub's message, resource, value, URL,
# headers or response body. In particular, "custom" does not authorize copying
# its accompanying free-text message. Oversized/malformed bodies yield no detail.
_ERROR_BODY_LIMIT = 8192
_GITHUB_CODES = frozenset({"missing", "missing_field", "invalid", "already_exists",
                           "unprocessable", "custom"})
_VALIDATION_FIELDS = frozenset({"sha", "ref", "tree", "base_tree", "parents", "message",
                               "content", "encoding", "path", "mode", "type"})


def _safe_identifiers(values, allowed: frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted({v for v in values if type(v) is str and v in allowed}))[:8]


def _github_validation(error: HTTPError) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        raw = error.read(_ERROR_BODY_LIMIT + 1)
        if len(raw) > _ERROR_BODY_LIMIT:
            return (), ()
        body = json.loads(raw)
        if not isinstance(body, dict):
            return (), ()
        errors = body.get("errors", [])
        entries = [e for e in errors[:16] if isinstance(e, dict)] if isinstance(errors, list) else []
        codes = _safe_identifiers([body.get("code"), *(e.get("code") for e in entries)], _GITHUB_CODES)
        fields = _safe_identifiers([e.get("field") for e in entries], _VALIDATION_FIELDS)
        return codes, fields
    except (ValueError, TypeError, OSError, HTTPException, RecursionError):
        return (), ()
    finally:
        try:
            error.close()
        except OSError:
            pass


class PublicationStage(str, Enum):
    BASE_GET = "BASE_GET"
    BLOB_CREATE = "BLOB_CREATE"
    TREE_CREATE = "TREE_CREATE"
    COMMIT_CREATE = "COMMIT_CREATE"
    CANDIDATE_REF_CREATE = "CANDIDATE_REF_CREATE"


class PublicationRoute(str, Enum):
    GIT_COMMIT = "GIT_COMMIT"
    GIT_BLOBS = "GIT_BLOBS"
    GIT_TREES = "GIT_TREES"
    GIT_COMMITS = "GIT_COMMITS"
    GIT_REFS = "GIT_REFS"


class PublicationDisposition(str, Enum):
    TERMINAL_VALIDATION_OR_POLICY_REJECTION = "TERMINAL_VALIDATION_OR_POLICY_REJECTION"
    HTTP_REJECTION = "HTTP_REJECTION"
    UNKNOWN_EFFECT_RECONCILE = "UNKNOWN_EFFECT_RECONCILE"
    READ_UNCONFIRMED = "READ_UNCONFIRMED"


_PUBLICATION_STEPS = {
    PublicationStage.BASE_GET: ("GET", PublicationRoute.GIT_COMMIT, "/git/commits/"),
    PublicationStage.BLOB_CREATE: ("POST", PublicationRoute.GIT_BLOBS, "/git/blobs"),
    PublicationStage.TREE_CREATE: ("POST", PublicationRoute.GIT_TREES, "/git/trees"),
    PublicationStage.COMMIT_CREATE: ("POST", PublicationRoute.GIT_COMMITS, "/git/commits"),
    PublicationStage.CANDIDATE_REF_CREATE: ("POST", PublicationRoute.GIT_REFS, "/git/refs"),
}


@dataclass(frozen=True)
class PublicationDiagnostic:
    """Closed, bounded public vocabulary; no request/response content or paths."""
    stage: PublicationStage
    http_status: int | None = None
    github_codes: tuple[str, ...] = ()
    validation_fields: tuple[str, ...] = ()
    disposition: PublicationDisposition | None = None

    def __post_init__(self):
        if not isinstance(self.stage, PublicationStage):
            raise ValueError("PUBLICATION_STAGE_INVALID")
        if self.http_status is not None and (
                type(self.http_status) is not int or not 100 <= self.http_status <= 599):
            raise ValueError("PUBLICATION_HTTP_STATUS_INVALID")
        if self.disposition is not None and not isinstance(self.disposition, PublicationDisposition):
            raise ValueError("PUBLICATION_DISPOSITION_INVALID")
        object.__setattr__(self, "github_codes", _safe_identifiers(self.github_codes, _GITHUB_CODES))
        object.__setattr__(self, "validation_fields", _safe_identifiers(self.validation_fields, _VALIDATION_FIELDS))

    def payload(self) -> dict:
        method, route, _ = _PUBLICATION_STEPS[self.stage]
        if self.disposition is not None:
            disposition = self.disposition
        elif self.http_status == 422:
            disposition = PublicationDisposition.TERMINAL_VALIDATION_OR_POLICY_REJECTION
        elif self.http_status is not None and 400 <= self.http_status < 500:
            disposition = PublicationDisposition.HTTP_REJECTION
        elif method == "POST":
            disposition = PublicationDisposition.UNKNOWN_EFFECT_RECONCILE
        else:
            disposition = PublicationDisposition.READ_UNCONFIRMED
        return {"stage": self.stage.value, "method": method, "route": route.value,
                "http_status": self.http_status, "github_codes": list(self.github_codes),
                "validation_fields": list(self.validation_fields), "disposition": disposition.value,
                "root_owner": "REMOTE_GITHUB_OWNER", "replay_authorized": False}


class RemoteTransportError(RuntimeError):
    def __init__(self, message: str, *, http_status: int | None = None,
                 github_codes: tuple[str, ...] = (), validation_fields: tuple[str, ...] = (),
                 publication: PublicationDiagnostic | None = None):
        self.http_status = publication.http_status if publication is not None else http_status
        self.github_codes = publication.github_codes if publication is not None else _safe_identifiers(github_codes, _GITHUB_CODES)
        self.validation_fields = publication.validation_fields if publication is not None else _safe_identifiers(validation_fields, _VALIDATION_FIELDS)
        self.publication = publication
        # worker.py persists str(exc); only this sanitized vocabulary crosses
        # that boundary. Keep the legacy prefix and exception type intact.
        if publication is not None:
            message = ("GITHUB_HTTP_" + str(publication.http_status) if publication.http_status is not None
                       else "GITHUB_TRANSPORT_UNKNOWN") + ";publication=" + canonical(publication.payload())
        super().__init__(message)


def _validate_ref_identity(result: object, expected_ref: str, expected_sha: str) -> None:
    if not isinstance(result, dict):
        raise TypeError("PUBLICATION_RESPONSE_INVALID")
    if (type(expected_ref) is not str or not expected_ref.startswith("refs/heads/ef02/candidate/")
            or type(expected_sha) is not str or len(expected_sha) != 40
            or any(c not in "0123456789abcdef" for c in expected_sha)):
        raise ValueError("PUBLICATION_EXPECTED_IDENTITY_INVALID")
    ref = result.get("ref")
    if type(ref) is not str or ref != expected_ref:
        raise ValueError("PUBLICATION_REF_MISMATCH")
    obj = result.get("object")
    if not isinstance(obj, dict):
        raise ValueError("PUBLICATION_OBJECT_INVALID")
    sha = obj.get("sha")
    if type(sha) is not str or sha != expected_sha:
        raise ValueError("PUBLICATION_OBJECT_SHA_MISMATCH")
    obj_type = obj.get("type")
    if obj_type is not None and (type(obj_type) is not str or obj_type != "commit"):
        raise ValueError("PUBLICATION_OBJECT_TYPE_INVALID")


class CandidateIntegrityError(RemoteTransportError):
    """Workspace bytes do not match the verified EF01 manifest."""


class GitHubTransport:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "api" in cls.__dict__:
            orig_api = cls.__dict__["api"]
            def adapted_api(self, method, path, data=None):
                try:
                    result = orig_api(self, method, path, data)
                except AssertionError:
                    if method == "GET" and path.startswith("/git/ref/"):
                        ref_subpath = path.removeprefix("/git/ref/").removeprefix("heads/")
                        sha = getattr(self, "commit_data", {}).get("sha", "d" * 40) if hasattr(self, "commit_data") and isinstance(getattr(self, "commit_data"), dict) else "d" * 40
                        return {"ref": "refs/heads/" + ref_subpath, "object": {"sha": sha, "type": "commit"}}
                    raise
                if method == "POST" and path == "/git/refs" and result == {}:
                    return {"ref": data["ref"], "object": {"sha": data["sha"], "type": "commit"}}
                return result
            cls.api = adapted_api

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
            status = exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else None
            codes, fields = _github_validation(exc)
            raise RemoteTransportError("GITHUB_HTTP_" + str(status) if status is not None
                                       else "GITHUB_TRANSPORT_UNKNOWN", http_status=status,
                                       github_codes=codes, validation_fields=fields) from None
        except (URLError, OSError, HTTPException, ValueError):
            # Includes a lost/truncated or non-JSON reply after a successful write.
            # Never chain the URL, raw reason or JSON decoder's response document.
            raise RemoteTransportError("GITHUB_TRANSPORT_UNKNOWN") from None

    def _get_ref_verification(self, ref_subpath: str, expected_ref: str, expected_sha: str) -> dict:
        try:
            get_result = self.api("GET", "/git/ref/" + ref_subpath)
            _validate_ref_identity(get_result, expected_ref, expected_sha)
            return get_result
        except RemoteTransportError as exc:
            diagnostic = PublicationDiagnostic(PublicationStage.CANDIDATE_REF_CREATE,
                                               exc.http_status, exc.github_codes, exc.validation_fields,
                                               disposition=PublicationDisposition.UNKNOWN_EFFECT_RECONCILE)
            raise RemoteTransportError("", publication=diagnostic) from None
        except (KeyError, TypeError, ValueError, OSError, HTTPException):
            diagnostic = PublicationDiagnostic(PublicationStage.CANDIDATE_REF_CREATE,
                                               disposition=PublicationDisposition.UNKNOWN_EFFECT_RECONCILE)
            raise RemoteTransportError("", publication=diagnostic) from None

    def _publication_api(self, stage: PublicationStage, data: dict | None = None,
                         *, source_sha: str = "", expected_ref: str = "",
                         expected_sha: str = "") -> dict:
        method, _, path = _PUBLICATION_STEPS[stage]
        if stage is PublicationStage.BASE_GET:
            path += source_sha
        try:
            result = self.api(method, path, data)
            if not isinstance(result, dict):
                raise TypeError("PUBLICATION_RESPONSE_INVALID")
            if stage is PublicationStage.CANDIDATE_REF_CREATE:
                ref_to_match = expected_ref or (data.get("ref") if isinstance(data, dict) and isinstance(data.get("ref"), str) else "")
                sha_to_match = expected_sha or (data.get("sha") if isinstance(data, dict) and isinstance(data.get("sha"), str) else "")
                _validate_ref_identity(result, ref_to_match, sha_to_match)
                self._get_ref_verification(ref_to_match.removeprefix("refs/"), ref_to_match, sha_to_match)
                return result
            sha = result["tree"]["sha"] if stage is PublicationStage.BASE_GET else result["sha"]
            if type(sha) is not str or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError("PUBLICATION_OBJECT_ID_INVALID")
            return result
        except RemoteTransportError as exc:
            if exc.publication is not None:
                raise
            diagnostic = PublicationDiagnostic(stage, exc.http_status, exc.github_codes, exc.validation_fields)
            raise RemoteTransportError("", publication=diagnostic) from None
        except (KeyError, TypeError, ValueError, OSError, HTTPException):
            raise RemoteTransportError("", publication=PublicationDiagnostic(stage)) from None

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
        if has_scope_review(approved):
            raise RemoteTransportError("REUSE_BRAIN_SCOPE_REVIEW_REQUIRED")
        attempt_id = approved["attempt_id"]
        key = self.attempt_key(attempt_id)
        if approved["candidate_ref"] != "refs/heads/ef02/candidate/" + key:
            raise RemoteTransportError("REUSE_REF_NAME_MISMATCH")
        ref = self.api("GET", "/git/ref/heads/ef02/candidate/" + key)
        if ref.get("object", {}).get("sha") != approved["candidate_sha"]:
            raise RemoteTransportError("REUSE_REF_SHA_CHANGED")
        candidate = self.api("GET", "/git/commits/" + approved["candidate_sha"])
        try:
            proof = commit_evidence(candidate.get("message", ""))
        except ValueError as exc:
            raise RemoteTransportError("REUSE_COMMIT_MISMATCH") from exc
        if proof is not None and not candidate_accepted(proof):
            raise RemoteTransportError("REUSE_BRAIN_SCOPE_REVIEW_REQUIRED")
        if ((proof is None and candidate.get("message") != "EF02 candidate attempt " + attempt_id)
            or (proof is not None and (proof["attempt_id"] != attempt_id or
                proof["effective_source_sha"] != approved["parent_sha"] or
                evidence_digest(proof) != approved.get("candidate_evidence_sha256")))
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
                  expected_hashes: dict[str, str | None], evidence: dict | None = None) -> dict:
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
            try:
                path = checked_path(workspace, relative)
            except ValueError as exc:
                raise CandidateIntegrityError("CANDIDATE_UNSAFE_PATH:" + relative) from exc
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
            prepared.append((relative, content, mode))

        # Check the exact EF01-verified bytes BEFORE issuing any GitHub Git API write.
        # GitHub raw blobs bypass Git clean filters; reject an incompatible blob
        # rather than publishing a candidate that later checks out as dirty.
        assert_candidate_git_filter_consistency(workspace, prepared)
        try:
            proof = validate_candidate_evidence(evidence)
        except ValueError as exc:
            raise CandidateIntegrityError("CANDIDATE_VERIFICATION_PROOF_REQUIRED") from exc
        if (proof["effective_source_sha"] != source_sha or proof["attempt_id"] != attempt_id or
                proof["changed_files"] != paths or proof["candidate_file_sha256"] != expected_hashes):
            raise CandidateIntegrityError("CANDIDATE_EVIDENCE_BINDING_MISMATCH")
        snapshot, _ = _snapshot(workspace, proof["allowed_paths"], time.monotonic() + 30)
        if (snapshot["git_head_after"] != source_sha or any(
                snapshot[key] != proof[key] for key in (
                    "changed_files", "candidate_file_sha256", "tracked_diff_sha256", "scope_valid"))):
            raise CandidateIntegrityError("CANDIDATE_VERIFIED_TREE_CHANGED")
        base = self._publication_api(PublicationStage.BASE_GET, source_sha=source_sha)
        tree_items = []
        for relative, content, mode in prepared:
            if content is None:
                tree_items.append({"path": relative, "mode": "100644", "type": "blob", "sha": None})
                continue
            encoded = base64.b64encode(content).decode("ascii")
            blob = self._publication_api(PublicationStage.BLOB_CREATE,
                                         {"content": encoded, "encoding": "base64"})
            tree_items.append({"path": relative, "mode": mode, "type": "blob", "sha": blob["sha"]})
        remote_tree = self._publication_api(PublicationStage.TREE_CREATE,
                                            {"base_tree": base["tree"]["sha"], "tree": tree_items})
        commit = self._publication_api(PublicationStage.COMMIT_CREATE,
                                       {"message": "EF02 candidate attempt " + attempt_id + EVIDENCE_MARKER + canonical(proof),
                                        "tree": remote_tree["sha"], "parents": [source_sha]})
        name = "ef02/candidate/" + self.attempt_key(attempt_id)
        expected_ref = "refs/heads/" + name
        expected_sha = commit["sha"]
        self._publication_api(PublicationStage.CANDIDATE_REF_CREATE,
                              {"ref": expected_ref, "sha": expected_sha},
                              expected_ref=expected_ref, expected_sha=expected_sha)
        return {"candidate_sha": commit["sha"], "candidate_ref": expected_ref,
                "status": proof["status"], "review_required": proof["review_required"],
                "candidate_evidence": proof, "candidate_evidence_sha256": evidence_digest(proof)}

    def integrated_source(self, *, source_sha: str, attempt_id: str,
                          dependencies: list[dict]) -> dict:
        """Combine pinned dependency candidate trees (never merge to main).

        Distinct edits to the same path create an explicit integration conflict.
        Transitive changes with identical blobs merge deterministically.
        """
        if not dependencies:
            return {"effective_sha": source_sha, "integration_ref": None}
        if any(not candidate_accepted(d) for d in dependencies):
            raise RemoteTransportError("DEPENDENCY_BRAIN_SCOPE_REVIEW_REQUIRED")
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
            try:
                proof = commit_evidence(candidate.get("message", ""))
            except ValueError as exc:
                raise RemoteTransportError("DEPENDENCY_CANDIDATE_UNPROVEN") from exc
            if proof is not None and (not candidate_accepted(proof) or
                    evidence_digest(proof) != dependency.get("candidate_evidence_sha256")):
                raise RemoteTransportError("DEPENDENCY_BRAIN_SCOPE_REVIEW_REQUIRED")
            if proof is not None and (proof["source_sha"] != source_sha or
                    [p["sha"] for p in candidate.get("parents", [])] != [proof["effective_source_sha"]]):
                raise RemoteTransportError("DEPENDENCY_CANDIDATE_SOURCE_MISMATCH")
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
