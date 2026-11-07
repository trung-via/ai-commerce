"""GitHub REST evidence channel. No AIOS tokens, no auto merge to main."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
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


class WorkOwner:
    """WorkOwner.v2: create-only fenced generation transfer, bounded hash-linked journal.

    Identity derives a bounded stable WorkId from the independently authenticated
    repository Issue/WorkItem root. Authentic cross-issue repair lineage is verified
    via predecessor Issue JSON, corresponding GitHub claims, and checkpoints.
    Monotonic generation 2+ transfers occur only upon independently verified prior
    terminal journal/candidate. Concurrent successor races resolve via atomic,
    create-only ref creation. Stale owners are fenced out before each EF01 provider
    call and candidate publication.

    Transitions: OWNED -> CLAIMED -> SPAWN_INTENT -> CHILD_OBSERVED (optional)
    -> PROVIDER_INTENT -> ROUND_VERIFIED [-> PROVIDER_INTENT ...]
    -> RESULT_VERIFIED -> PUBLISH_INTENT -> PUBLISHED. UNKNOWN is terminal
    from every nonterminal state. Intent means effect MAY have occurred.
    Only hashes/normalized IDs enter public commit messages; never logs/input.
    """
    LIMIT = 32
    MARKER = "EF02 WorkOwner.v1\n\n"
    EDGES = {
        "OWNED": {"CLAIMED"}, "CLAIMED": {"SPAWN_INTENT"},
        "SPAWN_INTENT": {"CHILD_OBSERVED", "PROVIDER_INTENT"},
        "CHILD_OBSERVED": {"PROVIDER_INTENT", "ROUND_VERIFIED", "RESULT_VERIFIED"},
        "PROVIDER_INTENT": {"CHILD_OBSERVED", "ROUND_VERIFIED"},
        "ROUND_VERIFIED": {"PROVIDER_INTENT", "CHILD_OBSERVED", "RESULT_VERIFIED"},
        "RESULT_VERIFIED": {"PUBLISH_INTENT"}, "PUBLISH_INTENT": {"PUBLISHED"},
        "PUBLISHED": set(), "UNKNOWN": set(),
    }

    @staticmethod
    def digest(value):
        return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

    @classmethod
    def _lookup_attempt_issue(cls, github, target_attempt_id):
        """Bounded unambiguous owner-authenticated attempt ID lookup through trusted Issue data or durable index."""
        key = github.attempt_key(target_attempt_id)
        index_sha = None
        for ns in ("ef02/index/", "ef02/indices/"):
            try:
                ref_obj = github.api("GET", f"/git/ref/heads/{ns}{key}")
            except RemoteTransportError as exc:
                if str(exc) != "GITHUB_HTTP_404":
                    raise
                continue
            expected_ref = f"refs/heads/{ns}{key}"
            if not isinstance(ref_obj, dict) or ref_obj.get("ref", expected_ref) != expected_ref:
                raise RemoteTransportError("WORK_LINEAGE_INDEX_REF_INVALID")
            sha = ref_obj.get("object", {}).get("sha") if isinstance(ref_obj.get("object"), dict) else None
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise RemoteTransportError("WORK_LINEAGE_INDEX_REF_INVALID")
            index_sha = sha
            break

        if index_sha:
            index_commit = github.api("GET", f"/git/commits/{index_sha}")
            if not isinstance(index_commit, dict):
                raise RemoteTransportError("WORK_LINEAGE_INDEX_COMMIT_INVALID")
            parsed = GitHubTransport._parse_index_message(index_commit.get("message", ""))
            if not parsed:
                raise RemoteTransportError("WORK_LINEAGE_INDEX_COMMIT_INVALID")
            if parsed.get("attempt_id") != target_attempt_id:
                raise RemoteTransportError("WORK_LINEAGE_INDEX_COLLISION")

            src_sha = parsed.get("source_sha")
            if not isinstance(src_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", src_sha):
                raise RemoteTransportError("WORK_LINEAGE_INDEX_COMMIT_INVALID")
            if [p.get("sha") for p in index_commit.get("parents", []) if isinstance(p, dict)] != [src_sha]:
                raise RemoteTransportError("WORK_LINEAGE_INDEX_COLLISION")

            num = parsed.get("issue")
            if type(num) is not int or not 1 <= num <= 2**53:
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID")

            try:
                issue = github.api("GET", f"/issues/{num}")
            except RemoteTransportError as exc:
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID") from exc

            if not isinstance(issue, dict) or issue.get("number") != num or "pull_request" in issue:
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID")

            expected_issuer = parsed.get("issuer")
            actual_issuer = (issue.get("user") or {}).get("login", "") if isinstance(issue.get("user"), dict) else ""
            if (not isinstance(expected_issuer, str) or not GitHubTransport._valid_issuer_login(expected_issuer) or
                    not isinstance(actual_issuer, str) or not GitHubTransport._valid_issuer_login(actual_issuer) or
                    actual_issuer.casefold() != expected_issuer.casefold()):
                raise RemoteTransportError("WORK_LINEAGE_INDEX_ISSUER_MISMATCH")

            body = issue.get("body")
            if not isinstance(body, str) or len(body.encode("utf-8")) > 262144:
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID")

            try:
                packet = json.loads(body)
            except (TypeError, ValueError) as exc:
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID") from exc

            if not isinstance(packet, dict) or packet.get("source_sha") != src_sha:
                raise RemoteTransportError("WORK_LINEAGE_INDEX_COLLISION")

            matched = next((a for a in packet.get("actions", []) if isinstance(a, dict) and a.get("attempt_id") == target_attempt_id), None)
            if matched is None:
                raise RemoteTransportError("WORK_LINEAGE_INDEX_COLLISION")

            sub = {k: v for k, v in matched.items() if k != "resolved_dependencies"}
            calc_fp = hashlib.sha256(json.dumps({
                "batch_id": packet["batch_id"],
                "source_sha": packet["source_sha"],
                "action": sub
            }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()

            if not parsed.get("fingerprint") or calc_fp != parsed.get("fingerprint"):
                raise RemoteTransportError("WORK_LINEAGE_INDEX_COLLISION")

            return num, packet, matched

        matches = []
        page = 1
        page_limit = 10
        seen_numbers = set()
        while page <= page_limit:
            try:
                issues = github.api("GET", f"/issues?state=all&per_page=100&page={page}")
            except RemoteTransportError as exc:
                if str(exc) == "GITHUB_HTTP_404":
                    break
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID") from exc

            if not isinstance(issues, list):
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID")

            if not issues:
                break

            new_issues = [i for i in issues if isinstance(i, dict) and i.get("number") not in seen_numbers]
            if not new_issues and page > 1:
                break

            for issue in issues:
                if not isinstance(issue, dict) or "pull_request" in issue:
                    continue
                num = issue.get("number")
                if type(num) is not int or not 1 <= num <= 2**53:
                    continue
                if num in seen_numbers:
                    continue
                seen_numbers.add(num)
                body = issue.get("body")
                if not isinstance(body, str):
                    try:
                        full_issue = github.api("GET", f"/issues/{num}")
                        body = full_issue.get("body")
                    except RemoteTransportError:
                        continue
                if not isinstance(body, str) or len(body.encode("utf-8")) > 262144:
                    continue
                try:
                    packet = json.loads(body)
                except (TypeError, ValueError):
                    continue
                if not isinstance(packet, dict):
                    continue
                for a in packet.get("actions", []):
                    if isinstance(a, dict) and a.get("attempt_id") == target_attempt_id:
                        matches.append((num, packet, a))

            if len(issues) < 100:
                break
            page += 1
        else:
            raise RemoteTransportError("WORK_LINEAGE_PAGE_OVERFLOW")

        if len(matches) == 0:
            raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ATTEMPT_NOT_FOUND")
        if len(matches) > 1:
            raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ATTEMPT_AMBIGUOUS")
        return matches[0]

    @classmethod
    def _authenticate_lineage(cls, github, current_issue, batch, action):
        repair_of = action.get("repair_of")
        if not repair_of:
            return None, current_issue, action["action_id"], 1, []

        visited_issues = {current_issue}
        visited_attempts = {action["attempt_id"]}
        lineage = []
        root_issue = current_issue
        root_action_id = action["action_id"]

        current_repair = repair_of
        while current_repair:
            if len(visited_issues) > 16:
                raise RemoteTransportError("WORK_LINEAGE_DEPTH_EXCEEDED")

            cursor_issue_num = None
            expected_attempt = None
            pred_packet = None
            matched_action = None

            if isinstance(current_repair, int):
                cursor_issue_num = current_repair
            elif isinstance(current_repair, dict):
                num = current_repair.get("issue") or current_repair.get("issue_number")
                cursor_issue_num = int(num) if num is not None else None
                expected_attempt = current_repair.get("attempt_id")
            elif isinstance(current_repair, str):
                val = current_repair.strip()
                m = re.match(r"^#(\d+)(?::([A-Za-z0-9_-]+))?$", val)
                if m:
                    cursor_issue_num = int(m.group(1))
                    expected_attempt = m.group(2)
                else:
                    expected_attempt = val
                    cursor_issue_num, pred_packet, matched_action = cls._lookup_attempt_issue(github, expected_attempt)
            else:
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID")

            if cursor_issue_num in visited_issues:
                raise RemoteTransportError("WORK_LINEAGE_CYCLE_DETECTED")
            visited_issues.add(cursor_issue_num)

            if type(cursor_issue_num) is not int or not 1 <= cursor_issue_num <= 2**53:
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID")

            if pred_packet is None:
                try:
                    pred_issue = github.api("GET", f"/issues/{cursor_issue_num}")
                except RemoteTransportError as exc:
                    raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID") from exc
                body = pred_issue.get("body")
                if (pred_issue.get("number") != cursor_issue_num or "pull_request" in pred_issue or
                        not isinstance(body, str) or len(body.encode("utf-8")) > 262144):
                    raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID")
                try:
                    pred_packet = json.loads(body)
                except (TypeError, ValueError) as exc:
                    raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ISSUE_INVALID") from exc

                pred_actions = pred_packet.get("actions", [])
                if expected_attempt:
                    matched_action = next((a for a in pred_actions if a.get("attempt_id") == expected_attempt), None)
                else:
                    matched_action = next((a for a in pred_actions if a.get("action_id") == action["action_id"]), None)
                    if matched_action is None and len(pred_actions) == 1:
                        matched_action = pred_actions[0]
                if matched_action is None:
                    raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_ACTION_NOT_FOUND")

            pred_attempt_id = matched_action["attempt_id"]
            if pred_attempt_id in visited_attempts:
                raise RemoteTransportError("WORK_LINEAGE_CYCLE_DETECTED")
            visited_attempts.add(pred_attempt_id)

            pred_submitted = {k: v for k, v in matched_action.items() if k != "resolved_dependencies"}
            pred_fp = hashlib.sha256(json.dumps({
                "batch_id": pred_packet["batch_id"],
                "source_sha": pred_packet["source_sha"],
                "action": pred_submitted
            }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()

            claim_key = github.attempt_key(pred_attempt_id)
            try:
                claim_ref = github.api("GET", f"/git/ref/heads/ef02/claims/{claim_key}")
            except RemoteTransportError as exc:
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_CLAIM_MISSING") from exc
            claim_sha = claim_ref.get("object", {}).get("sha")
            claim_commit = github.api("GET", f"/git/commits/{claim_sha}")
            expected_msg = f"EF02 CLAIM\n\nfingerprint={pred_fp}\nattempt_id={pred_attempt_id}"
            if claim_commit.get("message") != expected_msg:
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_CLAIM_MISMATCH")
            parents = [p.get("sha") for p in claim_commit.get("parents", [])]
            if parents != [pred_packet["source_sha"]]:
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_CLAIM_MISMATCH")

            cand_sha = None
            try:
                c_ref = github.api("GET", f"/git/ref/heads/ef02/candidate/{claim_key}")
                cand_sha = c_ref.get("object", {}).get("sha")
            except RemoteTransportError as exc:
                if str(exc) != "GITHUB_HTTP_404":
                    raise

            if cand_sha:
                cand_commit = github.api("GET", f"/git/commits/{cand_sha}")
                try:
                    proof = commit_evidence(cand_commit.get("message", ""))
                except ValueError as exc:
                    raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_CANDIDATE_MISMATCH") from exc
                if proof is None:
                    raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_CANDIDATE_MISMATCH")
                if (proof.get("attempt_id") != pred_attempt_id or
                        proof.get("source_sha") != pred_packet["source_sha"] or
                        proof.get("action_id") != matched_action["action_id"] or
                        proof.get("fingerprint") != pred_fp or
                        [p.get("sha") for p in cand_commit.get("parents", [])] != [proof.get("effective_source_sha")]):
                    raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_CANDIDATE_MISMATCH")

            lineage.append({
                "issue": cursor_issue_num,
                "action_id": matched_action["action_id"],
                "attempt_id": pred_attempt_id,
                "candidate_sha": cand_sha,
                "source_sha": pred_packet["source_sha"],
                "fingerprint": pred_fp,
                "batch_id": pred_packet["batch_id"]
            })
            root_issue = cursor_issue_num
            root_action_id = matched_action["action_id"]

            current_repair = matched_action.get("repair_of")

        generation = len(lineage) + 1
        return repair_of, root_issue, root_action_id, generation, lineage

    @classmethod
    def identity(cls, github, issue_number, batch, action):
        if type(issue_number) is not int or not 1 <= issue_number <= 2**53:
            raise RemoteTransportError("WORK_IDENTITY_UNKNOWN_TRUSTED_ISSUE_REQUIRED")
        issue = github.api("GET", "/issues/" + str(issue_number))
        body = issue.get("body")
        if (issue.get("number") != issue_number or "pull_request" in issue or
                not isinstance(body, str) or len(body.encode("utf-8")) > 262144):
            raise RemoteTransportError("WORK_INGRESS_MISMATCH")
        user_obj = issue.get("user")
        issue_author = user_obj.get("login", "") if isinstance(user_obj, dict) else ""
        if not isinstance(issue_author, str) or not GitHubTransport._valid_issuer_login(issue_author):
            raise RemoteTransportError("WORK_INDEX_ISSUE_CREATOR_MISSING")
        try:
            packet = json.loads(body)
        except (TypeError, ValueError) as exc:
            raise RemoteTransportError("WORK_INGRESS_MISMATCH") from exc
        # Compare the entire validated request, not instruction prose or an
        # executor-provided root ID. resolved_dependencies is scheduler-only.
        if packet != batch or not any(a == action for a in packet.get("actions", [])):
            raise RemoteTransportError("WORK_INGRESS_MISMATCH")

        repair_of, root_issue, root_action_id, generation, lineage = cls._authenticate_lineage(
            github, issue_number, batch, action
        )

        base_id = {"repo": github.repo, "issue": root_issue, "action_id": root_action_id}
        work_key = cls.digest(base_id)

        fp = hashlib.sha256(json.dumps({"batch_id": batch["batch_id"],
            "source_sha": batch["source_sha"], "action": action}, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()

        return {
            **base_id,
            "work_key": work_key,
            "root_issue": root_issue,
            "root_action_id": root_action_id,
            "current_issue": issue_number,
            "issuer_login": issue_author,
            "generation": generation,
            "repair_of": repair_of,
            "lineage": lineage,
            "source_sha": batch["source_sha"],
            "batch_id": batch["batch_id"],
            "attempt_id": action["attempt_id"],
            "fingerprint": fp,
        }

    @classmethod
    def index_ingress(cls, github, issue_number: int, batch: dict, action: dict, issuer: str | None = None) -> str:
        if type(issue_number) is not int or not 1 <= issue_number <= 2**53:
            raise RemoteTransportError("WORK_IDENTITY_UNKNOWN_TRUSTED_ISSUE_REQUIRED")
        fp = hashlib.sha256(json.dumps({
            "batch_id": batch["batch_id"],
            "source_sha": batch["source_sha"],
            "action": {k: v for k, v in action.items() if k != "resolved_dependencies"}
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
        return github.index_attempt(
            issue_number=issue_number,
            source_sha=batch["source_sha"],
            attempt_id=action["attempt_id"],
            fingerprint=fp,
            issuer=issuer
        )

    def __init__(self, github, identity, fence):
        self.github, self.identity, self.fence = github, dict(identity), fence
        root_issue = identity.get("root_issue", identity["issue"])
        root_action_id = identity.get("root_action_id", identity["action_id"])
        self.base_identity = {"repo": github.repo, "issue": root_issue,
                              "action_id": root_action_id}
        self.generation = identity.get("generation", 1)
        self.prefix = "ef02/workowner/" + identity["work_key"] + "/"
        self.lock = threading.RLock()
        self.tip = None
        self.uncertain = False

    @classmethod
    def acquire(cls, github, identity):
        owner = cls(github, identity, secrets.token_hex(32))
        chain = owner.read()
        target_gen = identity.get("generation", 1)

        if target_gen == 1:
            if chain:
                raise RemoteTransportError("WORK_OWNER_EXISTS_RECONCILE_ONLY")
            for namespace in ("claims", "pre-spawn", "spawned", "candidate"):
                try:
                    github.api("GET", "/git/ref/heads/ef02/" + namespace + "/" +
                               github.attempt_key(identity["attempt_id"]))
                except RemoteTransportError as exc:
                    if str(exc) != "GITHUB_HTTP_404":
                        raise
                else:
                    raise RemoteTransportError("WORK_LEGACY_BOUNDARY_RECONCILE_ONLY")
            owner.generation = 1
            owner._create("OWNED", [], {"attempt_id": identity["attempt_id"],
                                        "issue": identity.get("current_issue", identity["issue"])})
            return owner
        else:
            if not chain:
                raise RemoteTransportError("WORK_LINEAGE_PREDECESSOR_JOURNAL_MISSING")
            prior = chain[-1]
            if prior["generation"] >= target_gen:
                raise RemoteTransportError("WORK_OWNER_TRANSFER_CONFLICT")
            # UNKNOWN_FAIL_CLOSED: Strictly DENY UNKNOWN predecessor provider/publication/reownership
            if prior["stage"] != "PUBLISHED":
                raise RemoteTransportError("WORK_OWNER_PREDECESSOR_UNRESOLVED")
            if target_gen != prior["generation"] + 1:
                raise RemoteTransportError("WORK_OWNER_STALE_OR_SKIPPED_GENERATION")

            receipt = prior.get("data", {}).get("receipt")
            cand_digest = prior.get("data", {}).get("candidate")
            if not receipt or not cand_digest:
                raise RemoteTransportError("WORK_TERMINAL_RECEIPT_MISMATCH")
            pred_attempt = prior.get("data", {}).get("attempt_id")
            if not pred_attempt and identity.get("lineage"):
                pred_attempt = identity["lineage"][0].get("attempt_id")
            if pred_attempt:
                cand_ref = github.api("GET", f"/git/ref/heads/ef02/candidate/{github.attempt_key(pred_attempt)}")
                cand_sha = cand_ref.get("object", {}).get("sha")
                if cls.digest(cand_sha) != cand_digest:
                    raise RemoteTransportError("WORK_TERMINAL_RECEIPT_MISMATCH")
                cand_commit = github.api("GET", f"/git/commits/{cand_sha}")
                try:
                    proof = commit_evidence(cand_commit.get("message", ""))
                except ValueError as exc:
                    raise RemoteTransportError("WORK_TERMINAL_RECEIPT_MISMATCH") from exc
                if (proof is None or evidence_digest(proof) != receipt or
                        proof.get("attempt_id") != pred_attempt or
                        [p.get("sha") for p in cand_commit.get("parents", [])] != [proof.get("effective_source_sha")]):
                    raise RemoteTransportError("WORK_TERMINAL_RECEIPT_MISMATCH")

            owner.generation = target_gen
            data = {
                "attempt_id": identity["attempt_id"],
                "issue": identity.get("current_issue", identity["issue"]),
                "repair_of": str(identity.get("repair_of")),
                "predecessor_tip": prior["digest"]
            }
            try:
                owner._create("OWNED", chain, data)
            except RemoteTransportError as exc:
                if "422" in str(exc) or "ALREADY_EXISTS" in str(exc):
                    raise RemoteTransportError("WORK_OWNER_TRANSFER_CONFLICT") from exc
                raise
            return owner

    def read(self):
        deadline = time.monotonic() + 60
        def get(path):
            if time.monotonic() >= deadline:
                raise RemoteTransportError("WORK_RECONCILE_READ_BUDGET_EXHAUSTED")
            return self.github.api("GET", path)

        refs = get("/git/matching-refs/heads/" + self.prefix)
        if not isinstance(refs, list):
            refs = []

        legacy_refs = []
        legacy_pred = None
        # V1->V2 bridge: discover legacy predecessor journal when self.prefix has no refs or starts after 000
        first_ref = sorted(refs, key=lambda r: r.get("ref", ""))[0].get("ref", "") if refs else ""
        if (not refs or not first_ref.endswith("000")) and self.identity.get("lineage"):
            for pred in self.identity["lineage"]:
                p_base = {"repo": self.github.repo, "issue": pred["issue"], "action_id": pred["action_id"]}
                p_key = self.digest(p_base)
                p_prefix = "ef02/workowner/" + p_key + "/"
                if p_prefix != self.prefix:
                    l_refs = get("/git/matching-refs/heads/" + p_prefix)
                    if isinstance(l_refs, list) and l_refs:
                        legacy_refs = sorted(l_refs, key=lambda r: r.get("ref", ""))
                        legacy_pred = pred
                        break

        all_refs = legacy_refs + sorted(refs, key=lambda r: r.get("ref", ""))
        if len(all_refs) > self.LIMIT:
            raise RemoteTransportError("WORK_JOURNAL_BOUND_INVALID")

        refs = all_refs
        chain = []
        tree = None
        for index, ref in enumerate(refs):
            ref_name = ref.get("ref", "")
            if not ref_name.endswith(f"{index:03d}"):
                raise RemoteTransportError("WORK_JOURNAL_ORDER_INVALID")
            sha = ref.get("object", {}).get("sha")
            if not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{40}", sha):
                raise RemoteTransportError("WORK_JOURNAL_REF_INVALID")
            commit = get("/git/commits/" + sha)
            message = commit.get("message", "")
            if not isinstance(message, str) or len(message) > 4096 or not message.startswith(self.MARKER):
                raise RemoteTransportError("WORK_JOURNAL_RECORD_INVALID")
            try:
                record = json.loads(message[len(self.MARKER):])
                digest = record.pop("digest")
            except (TypeError, ValueError, KeyError, AttributeError) as exc:
                raise RemoteTransportError("WORK_JOURNAL_RECORD_INVALID") from exc

            prior = chain[-1] if chain else None

            # Verify digest matches canonical bytes
            if digest != self.digest(record):
                if legacy_pred or record.get("version") == 1:
                    raise RemoteTransportError("LEGACY_MIGRATION_UNPROVEN")
                raise RemoteTransportError("WORK_JOURNAL_INTEGRITY_MISMATCH")

            rec_id = record.get("identity")
            is_legacy = False
            matched_pred = None
            if rec_id == self.base_identity:
                is_legacy = False
            else:
                candidate_preds = (self.identity.get("lineage") or []) + [
                    {"repo": self.github.repo, "issue": self.identity.get("current_issue", self.identity.get("issue")),
                     "action_id": self.identity.get("action_id"), "attempt_id": self.identity.get("attempt_id"),
                     "source_sha": self.identity.get("source_sha"), "fingerprint": self.identity.get("fingerprint")}
                ]
                if isinstance(rec_id, dict):
                    for p in candidate_preds:
                        p_work_key = self.digest({"repo": self.github.repo, "issue": p["issue"], "action_id": p["action_id"]})
                        if (rec_id.get("repo") == self.github.repo and
                                rec_id.get("issue") == p["issue"] and
                                rec_id.get("action_id") == p["action_id"] and
                                rec_id.get("attempt_id") == p["attempt_id"] and
                                rec_id.get("source_sha") == p["source_sha"] and
                                rec_id.get("fingerprint") == p["fingerprint"] and
                                rec_id.get("work_key") == p_work_key):
                            matched_pred = p
                            break
                if matched_pred is not None and record.get("version") == 1 and record.get("generation") == 1:
                    is_legacy = True
                else:
                    if legacy_pred or record.get("version") == 1:
                        raise RemoteTransportError("LEGACY_MIGRATION_UNPROVEN")
                    raise RemoteTransportError("WORK_JOURNAL_INTEGRITY_MISMATCH")

            # A successor's source is not the immutable genesis parent. Lineage
            # was authenticated by identity(); retain that root source binding.
            genesis_source = (self.identity["lineage"][-1]["source_sha"]
                              if self.identity.get("lineage") else self.identity["source_sha"])
            parent = prior["sha"] if prior else (matched_pred["source_sha"] if is_legacy else genesis_source)

            if (set(record) != {"kind", "version", "identity", "generation", "fence",
                    "sequence", "previous", "stage", "data"} or
                    record["kind"] != "WorkOwner" or type(record["version"]) is not int or record["version"] not in (1, 2) or
                    type(record["generation"]) is not int or record["generation"] < 1 or
                    type(record["sequence"]) is not int or record["sequence"] != index or
                    record["previous"] != (prior["digest"] if prior else None) or
                    not re.fullmatch("[0-9a-f]{64}", str(record["fence"])) or
                    [p.get("sha") for p in commit.get("parents", [])] != [parent]):
                if legacy_pred or is_legacy or record.get("version") == 1:
                    raise RemoteTransportError("LEGACY_MIGRATION_UNPROVEN")
                raise RemoteTransportError("WORK_JOURNAL_INTEGRITY_MISMATCH")

            if is_legacy and record["stage"] == "PUBLISHED":
                receipt = record.get("data", {}).get("receipt")
                cand_digest = record.get("data", {}).get("candidate")
                if not receipt or not cand_digest:
                    raise RemoteTransportError("LEGACY_MIGRATION_UNPROVEN")
                try:
                    c_ref = self.github.api("GET", f"/git/ref/heads/ef02/candidate/{self.github.attempt_key(matched_pred['attempt_id'])}")
                    c_sha = c_ref.get("object", {}).get("sha")
                    if self.digest(c_sha) != cand_digest:
                        raise RemoteTransportError("LEGACY_MIGRATION_UNPROVEN")
                    cand_c = self.github.api("GET", f"/git/commits/{c_sha}")
                    try:
                        proof = commit_evidence(cand_c.get("message", ""))
                    except ValueError as exc:
                        raise RemoteTransportError("LEGACY_MIGRATION_UNPROVEN") from exc
                    if (proof is None or evidence_digest(proof) != receipt or
                            proof.get("attempt_id") != matched_pred["attempt_id"] or
                            [p.get("sha") for p in cand_c.get("parents", [])] != [proof.get("effective_source_sha")]):
                        raise RemoteTransportError("LEGACY_MIGRATION_UNPROVEN")
                except RemoteTransportError as exc:
                    raise RemoteTransportError("LEGACY_MIGRATION_UNPROVEN") from exc

            if index == 0:
                if record["generation"] != 1:
                    raise RemoteTransportError("WORK_JOURNAL_INTEGRITY_MISMATCH")
            else:
                if record["generation"] == prior["generation"]:
                    if record["fence"] != prior["fence"]:
                        raise RemoteTransportError("WORK_JOURNAL_INTEGRITY_MISMATCH")
                elif record["generation"] == prior["generation"] + 1:
                    if prior["stage"] != "PUBLISHED":
                        raise RemoteTransportError("WORK_JOURNAL_INTEGRITY_MISMATCH")
                    if record["stage"] != "OWNED":
                        raise RemoteTransportError("WORK_JOURNAL_INTEGRITY_MISMATCH")
                    if record["fence"] == prior["fence"]:
                        raise RemoteTransportError("WORK_JOURNAL_INTEGRITY_MISMATCH")
                else:
                    raise RemoteTransportError("WORK_JOURNAL_INTEGRITY_MISMATCH")

            current_tree = commit.get("tree", {}).get("sha")
            if tree is None:
                tree = current_tree
            if not tree or current_tree != tree:
                if legacy_pred or is_legacy:
                    raise RemoteTransportError("LEGACY_MIGRATION_UNPROVEN")
                raise RemoteTransportError("WORK_JOURNAL_TREE_MISMATCH")

            self._transition(record["stage"], chain, record["data"])
            chain.append({**record, "digest": digest, "sha": sha})
        return chain

    @classmethod
    def _transition(cls, stage, chain, data):
        previous = chain[-1]["stage"] if chain else None
        is_gen_transfer = bool(chain and stage == "OWNED" and previous == "PUBLISHED")
        if (stage not in cls.EDGES or (not chain and stage != "OWNED") or
                (chain and not is_gen_transfer and stage not in cls.EDGES[previous] and not (
                    stage == "UNKNOWN" and previous not in ("PUBLISHED", "UNKNOWN")))):
            raise RemoteTransportError("WORK_JOURNAL_TRANSITION_INVALID")
        allowed_data = {"round", "pid", "receipt", "candidate", "attempt_id", "issue", "repair_of", "predecessor_tip"}
        if (not isinstance(data, dict) or set(data) - allowed_data or
                any(not re.fullmatch("[0-9a-f]{64}", str(data[k]))
                    for k in ("receipt", "candidate", "predecessor_tip") if k in data) or
                ("pid" in data and (type(data["pid"]) is not int or not 0 < data["pid"] < 2**32)) or
                ("round" in data and type(data["round"]) is not int)):
            raise RemoteTransportError("WORK_JOURNAL_DATA_INVALID")
        current_gen = (chain[-1]["generation"] + 1) if is_gen_transfer else (chain[-1]["generation"] if chain else 1)
        gen_chain = [r for r in chain if r.get("generation") == current_gen]
        intents = [r for r in gen_chain if r["stage"] == "PROVIDER_INTENT"]
        verified = [r for r in gen_chain if r["stage"] == "ROUND_VERIFIED"]
        if stage in ("PROVIDER_INTENT", "ROUND_VERIFIED"):
            expected = len(intents) if stage == "PROVIDER_INTENT" else len(verified)
            if type(data.get("round")) is not int or data["round"] != expected or not 0 <= expected <= 5:
                raise RemoteTransportError("WORK_PROVIDER_DUPLICATE_OR_MISMATCH")
            if stage == "PROVIDER_INTENT" and len(intents) != len(verified):
                raise RemoteTransportError("WORK_PROVIDER_OUTCOME_UNKNOWN")
            if stage == "ROUND_VERIFIED" and (len(intents) != len(verified) + 1 or "receipt" not in data):
                raise RemoteTransportError("WORK_PROVIDER_RECEIPT_MISMATCH")
        if stage in ("RESULT_VERIFIED", "PUBLISH_INTENT", "PUBLISHED") and "receipt" not in data:
            raise RemoteTransportError("WORK_VERIFIED_RECEIPT_REQUIRED")
        if stage == "PUBLISHED" and "candidate" not in data:
            raise RemoteTransportError("WORK_CANDIDATE_RECEIPT_REQUIRED")
        if stage in ("PUBLISH_INTENT", "PUBLISHED") and data.get("receipt") != chain[-1]["data"].get("receipt"):
            raise RemoteTransportError("WORK_VERIFIED_RECEIPT_MISMATCH")
        if stage == "RESULT_VERIFIED" and (not verified or len(intents) != len(verified)):
            raise RemoteTransportError("WORK_PROVIDER_OUTCOME_UNKNOWN")
        if stage == "CHILD_OBSERVED" and ("pid" not in data or any(r["stage"] == stage for r in gen_chain)):
            raise RemoteTransportError("WORK_CHILD_DUPLICATE_OR_MISMATCH")

    def assert_current(self):
        if self.uncertain:
            raise RemoteTransportError("WORK_OWNER_STALE_FENCE")
        chain = self.read()
        if self.uncertain or not chain:
            raise RemoteTransportError("WORK_OWNER_STALE_FENCE")
        gen_records = [r for r in chain if r["generation"] == self.generation]
        if (not gen_records or
                gen_records[0]["fence"] != self.digest(self.fence) or
                any(r["fence"] != self.digest(self.fence) for r in gen_records) or
                (self.tip is not None and chain[-1]["digest"] != self.tip) or
                chain[-1]["generation"] > self.generation or
                chain[-1]["stage"] in ("UNKNOWN", "PUBLISHED")):
            raise RemoteTransportError("WORK_OWNER_STALE_FENCE")
        return chain

    def _create(self, stage, chain, data):
        if len(chain) >= self.LIMIT:
            raise RemoteTransportError("WORK_JOURNAL_BOUND_INVALID")
        self._transition(stage, chain, data)
        record = {"kind": "WorkOwner", "version": 1, "identity": self.base_identity,
                  "generation": self.generation, "fence": self.digest(self.fence), "sequence": len(chain),
                  "previous": chain[-1]["digest"] if chain else None, "stage": stage, "data": data}
        record["digest"] = self.digest(record)
        tree_sha = None
        if chain:
            first_commit = self.github.api("GET", "/git/commits/" + chain[0]["sha"])
            tree_sha = first_commit.get("tree", {}).get("sha")
        if not tree_sha:
            source = self.github.api("GET", "/git/commits/" + self.identity["source_sha"])
            tree_sha = source["tree"]["sha"]
        commit = self.github.api("POST", "/git/commits", {
            "message": self.MARKER + canonical(record), "tree": tree_sha,
            "parents": [chain[-1]["sha"] if chain else self.identity["source_sha"]]})
        # No retry for 422, timeout or transport failure. A ref may exist.
        self.github._create_ref(self.prefix + f"{len(chain):03d}", commit["sha"])
        confirmed = self.read()
        if not confirmed or confirmed[-1]["sha"] != commit["sha"]:
            raise RemoteTransportError("WORK_JOURNAL_WRITE_UNKNOWN")
        self.tip = record["digest"]
        return record["digest"]

    def append(self, stage, **data):
        with self.lock:
            try:
                return self._create(stage, self.assert_current(), data)
            except Exception:
                self.uncertain = True
                raise

    def reconcile(self):
        try:
            chain = self.read()
        except RemoteTransportError as exc:
            if "LEGACY_MIGRATION_UNPROVEN" in str(exc):
                return {
                    "status": "HOLD_UNKNOWN_EFFECT",
                    "replay_authorized": False,
                    "publication_authorized": False,
                    "generation": self.generation,
                    "proves_current_mt01_pass": False,
                    "stage": "ABSENT",
                    "lineage_gap": "LEGACY_MIGRATION_UNPROVEN",
                }
            raise

        current_gen = chain[-1]["generation"] if chain else 1
        current_stage = chain[-1]["stage"] if chain else "ABSENT"

        lineage_gap = None
        if not chain:
            lineage_gap = "WORK_OWNER_JOURNAL_ABSENT"
        elif current_stage == "UNKNOWN":
            lineage_gap = "PREDECESSOR_EXECUTION_UNKNOWN"
        elif self.identity.get("lineage") is None and current_gen == 1:
            lineage_gap = "V1_HAS_NO_TRUSTED_CROSS_ISSUE_REPAIR_OR_TRANSFER"

        result = {
            "status": "HOLD_UNKNOWN_EFFECT" if current_stage != "ABSENT" else "ABSENT",
            "replay_authorized": False,
            "publication_authorized": False,
            "generation": current_gen,
            "proves_current_mt01_pass": False,
            "stage": current_stage,
            "lineage_gap": lineage_gap,
        }
        if chain and current_stage in ("PUBLISH_INTENT", "PUBLISHED"):
            cand_attempt_id = chain[-1].get("data", {}).get("attempt_id") or self.identity["attempt_id"]
            try:
                ref = self.github.api("GET", "/git/ref/heads/ef02/candidate/" +
                                      self.github.attempt_key(cand_attempt_id))
            except RemoteTransportError as exc:
                if str(exc) == "GITHUB_HTTP_404":
                    return result
                raise
            sha = ref.get("object", {}).get("sha")
            candidate = self.github.api("GET", "/git/commits/" + str(sha))
            proof = commit_evidence(candidate.get("message", ""))
            receipt = chain[-1]["data"]
            if (proof is None or ("candidate" in receipt and self.digest(sha) != receipt["candidate"]) or
                    evidence_digest(proof) != receipt["receipt"] or
                    proof["source_sha"] != self.identity["source_sha"] or
                    proof["attempt_id"] != cand_attempt_id or
                    proof["action_id"] not in (self.identity["action_id"], self.identity.get("root_action_id")) or
                    [p.get("sha") for p in candidate.get("parents", [])] != [proof["effective_source_sha"]]):
                raise RemoteTransportError("WORK_TERMINAL_RECEIPT_MISMATCH")
            result.update(status="RECONCILED_VERIFIED_TERMINAL", candidate_sha=sha)
        return result


class _EffectDenied(Exception):
    pass


def _effect_deny(code):
    raise _EffectDenied(code)


def _effect_canonical(value):
    # Same closed canonical ASCII JSON wire format as HostOps offline signatures,
    # without a runtime dependency on that independently registered owner.
    def walk(v):
        if v is None or type(v) in (str, bool):
            return
        if type(v) is int and abs(v) <= 2**53 - 1:
            return
        if type(v) is list:
            for item in v:
                walk(item)
            return
        if type(v) is dict and all(type(k) is str for k in v):
            for item in v.values():
                walk(item)
            return
        _effect_deny("NON_CANONICAL_TYPE")
    walk(value)
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def _effect_decode(raw, *, limit=32768):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        _effect_deny("WIRE_LIMIT")
    def pairs(items):
        value = {}
        for k, v in items:
            if k in value:
                _effect_deny("DUPLICATE_KEY")
            value[k] = v
        return value
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    _effect_canonical(value)
    return value


def _effect_digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _effect_shape(value, keys):
    if type(value) is not dict or set(value) != set(keys.split()):
        _effect_deny("BAD_SHAPE")


def _effect_sha(value, size=64):
    if type(value) is not str or not re.fullmatch("[0-9a-f]{" + str(size) + "}", value):
        _effect_deny("BAD_HASH")


def _effect_integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        _effect_deny("BAD_INTEGER")


def _effect_verify_signature(body, signature, signers, identity, namespace, executable):
    # Fixed OpenSSH offline verification only. No Issue-selected arguments,
    # shell, network, private keys, raw output or native HostOps operation.
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
        if not 20 <= len(signature_bytes) <= 12288:
            _effect_deny("SIGNATURE_INVALID")
        with tempfile.TemporaryDirectory(prefix="effect-signature-public-") as folder:
            sig = Path(folder) / "attestation.sig"
            sig.write_bytes(signature_bytes)
            process = subprocess.run([str(executable), "-Y", "verify", "-f", str(signers),
                "-I", identity, "-n", namespace, "-s", str(sig)], input=_effect_canonical(body),
                capture_output=True, timeout=5, check=False,
                env={k: v for k, v in os.environ.items()
                     if k.casefold() in {"systemroot", "windir", "temp", "tmp", "programdata"}})
        if process.returncode != 0:
            _effect_deny("SIGNATURE_INVALID")
    except (ValueError, OSError, subprocess.TimeoutExpired):
        _effect_deny("SIGNATURE_UNAVAILABLE")


# E1 is an offline source contract, separate from W1 and the HostOps catalogue.
# No production enrollment is adopted. Only a separately reviewed policy may
# populate this exact canonical public-enrollment allowlist; Issue data cannot.
WORK_OWNER_EFFECT_SCHEMA = "WorkOwnerEffectWitness.v1"
WORK_OWNER_EFFECT_NAMESPACE = "workowner-effect-witness-v1@ai-commerce"
WORK_OWNER_EFFECT_NATIVE_GATE = "HOLD_PENDING_ADOPTED_NATIVE_OWNER_POLICY"
_WORK_OWNER_EFFECT_ENROLLMENTS: tuple[bytes, ...] = ()


class WorkOwnerEffectGate(str, Enum):
    NO_AUTHENTICATED_OWNER_WITNESS = "NO_AUTHENTICATED_OWNER_WITNESS"
    BAD_SIGNATURE = "BAD_SIGNATURE"
    REPLAY_OR_STALE = "REPLAY_OR_STALE"
    SOURCE_BOUNDARY_CONTRADICTION = "SOURCE_BOUNDARY_CONTRADICTION"
    EFFECT_DETERMINATE_AUTHENTICATED = "EFFECT_DETERMINATE_AUTHENTICATED"


@dataclass(frozen=True)
class WorkOwnerEffectEnrollment:
    """PUBLIC, out-of-band policy input, never enrollment by an Issue.

    Hashes identify the exact external principal, public key and supported
    verifier. They do not prove custody: the production allowlist is empty.
    A same-SID runner or a fixture key cannot become a native owner signer.
    """
    identity: str
    public_key: str
    verifier_sha256: str
    signer_principal_sha256: str
    executor_principal_sha256: str
    custody: str
    purpose: str

    def encoded(self):
        return _effect_canonical(asdict(self))


@dataclass(frozen=True)
class WorkOwnerEffectReadContext:
    """Immutable acquisition snapshot supplied outside Issue/receipt claims.

    Future native integration must authenticate both this post-effect snapshot
    and enrollment independently. Caller clocks and caller-populated sets are
    insufficient for native replay prevention. No default clock/ledger exists.
    All digest pins must come from independently acquired immutable evidence.
    """
    attestation_sha256: str
    artifact_sha256: str
    verification_sha256: str
    post_effect_sha256: str
    replay_ledger_sha256: str
    clock_id: str
    monotonic_ns: int
    unix_time: int
    seen_nonces: frozenset[str]
    seen_replay_ids: frozenset[str]


@dataclass(frozen=True)
class WorkOwnerEffectResult:
    gate: WorkOwnerEffectGate
    scope: str = "UNENROLLED"
    evidence_hashes: tuple[tuple[str, str], ...] = ()

    def payload(self):
        return {"schema": "WorkOwnerEffectSourceGate.v1", "gate": self.gate.value,
                "scope": self.scope, "native_owner_policy_gate": WORK_OWNER_EFFECT_NATIVE_GATE,
                "work_owner_transition_authorized": False, "replay_authorized": False,
                "transfer_authorized": False, "publication_authorized": False,
                "execution_allowed": False, "credential_custody_proven": False,
                "replay_ledger_proven": False,
                "evidence_hashes": dict(self.evidence_hashes)}


def work_owner_effect_binding(binding):
    """Closed public source identity. Returns a detached canonical snapshot."""
    _effect_shape(binding, "repo root_issue current_issue action_id attempt_id source_sha work_owner refs provider_intent")
    if binding["repo"] != "trung-via/ai-commerce":
        _effect_deny("EFFECT_BINDING_INVALID")
    for name in ("root_issue", "current_issue"):
        _effect_integer(binding[name], 1, 2147483647)
    for name in ("action_id", "attempt_id"):
        if type(binding[name]) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", binding[name]):
            _effect_deny("EFFECT_BINDING_INVALID")
    _effect_sha(binding["source_sha"], 40)
    owner = binding["work_owner"]
    _effect_shape(owner, "root_commit_sha root_digest journal_tip_sha journal_digest work_key fingerprint fence generation")
    for name in ("root_commit_sha", "journal_tip_sha"):
        _effect_sha(owner[name], 40)
    for name in ("root_digest", "journal_digest", "work_key", "fingerprint", "fence"):
        _effect_sha(owner[name])
    _effect_integer(owner["generation"], 1, 32)
    _effect_shape(binding["refs"], "claim pre_spawn spawned")
    key = hashlib.sha256((binding["repo"] + ":" + binding["attempt_id"]).encode()).hexdigest()[:32]
    for name, namespace in (("claim", "claims"), ("pre_spawn", "pre-spawn"), ("spawned", "spawned")):
        ref = binding["refs"][name]
        _effect_shape(ref, "ref sha")
        if ref["ref"] != f"refs/heads/ef02/{namespace}/{key}":
            _effect_deny("EFFECT_BINDING_INVALID")
        _effect_sha(ref["sha"], 40)
    intent = binding["provider_intent"]
    _effect_shape(intent, "commit_sha digest round")
    _effect_sha(intent["commit_sha"], 40)
    _effect_sha(intent["digest"])
    _effect_integer(intent["round"], 0, 31)
    return _effect_decode(_effect_canonical(binding), limit=16384)


def _work_owner_effect_verifier_snapshot(executable, expected):
    """Bounded read of an out-of-band pinned verifier; no Issue-selected path."""
    exe = Path(executable)
    if not exe.is_absolute() or not exe.is_file():
        _effect_deny("EFFECT_VERIFIER_UNTRUSTED")
    for part in (exe, *exe.parents):
        if part.is_symlink() or getattr(part.lstat(), "st_file_attributes", 0) & 0x400:
            _effect_deny("EFFECT_VERIFIER_UNTRUSTED")
    before = exe.stat()
    with exe.open("rb") as stream:
        raw = stream.read(32 * 1024 * 1024 + 1)
    after = exe.stat()
    if (not raw or len(raw) > 32 * 1024 * 1024 or _effect_digest(raw) != expected or
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        _effect_deny("EFFECT_VERIFIER_UNTRUSTED")
    return after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns


def work_owner_effect_witness_verify_offline(raw, *, expected_binding, enrollment=None,
        context=None, ssh_keygen=None, source_contract_fixture=False):
    """Read-only E1 validation. No network, enrollment, clock read or ledger write.

    Wire bytes are canonical ASCII JSON with one trailing LF (contract.canonical).
    The external OpenSSH Ed25519 signature covers the complete payload in the
    E1 namespace; only the detached signature is excluded. The full envelope's
    SHA256 must equal an independently acquired immutable attestation pin.
    Fixture mode is explicit and ALWAYS labeled SOURCE_CONTRACT_FIXTURE. Even
    future enrolled source validation grants no native effect or owner transition.
    """
    gate = WorkOwnerEffectGate
    scope = "SOURCE_CONTRACT_FIXTURE" if source_contract_fixture is True else "UNENROLLED"
    def result(value):
        return WorkOwnerEffectResult(value, scope)
    try:
        if type(source_contract_fixture) is not bool or type(enrollment) is not WorkOwnerEffectEnrollment:
            return result(gate.NO_AUTHENTICATED_OWNER_WITNESS)
        enrolled = enrollment.encoded()
        if source_contract_fixture:
            if enrollment.purpose != "SOURCE_CONTRACT_FIXTURE":
                return result(gate.NO_AUTHENTICATED_OWNER_WITNESS)
        elif (enrollment.purpose != "AUTHENTICATED_EXTERNAL_OWNER" or
                enrolled not in _WORK_OWNER_EFFECT_ENROLLMENTS):
            return result(gate.NO_AUTHENTICATED_OWNER_WITNESS)
        if (enrollment.custody != "EXTERNAL_ISOLATED_SIGNER" or
                enrollment.signer_principal_sha256 == enrollment.executor_principal_sha256 or
                type(enrollment.identity) is not str or
                not re.fullmatch(r"effect-owner-[a-z0-9-]{1,48}", enrollment.identity)):
            return result(gate.NO_AUTHENTICATED_OWNER_WITNESS)
        for value in (enrollment.verifier_sha256, enrollment.signer_principal_sha256,
                      enrollment.executor_principal_sha256):
            _effect_sha(value)
        # One exact, option-free Ed25519 key. Never a URL, wildcard or W1 signer.
        if type(enrollment.public_key) is not str or not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]{68}", enrollment.public_key):
            return result(gate.NO_AUTHENTICATED_OWNER_WITNESS)
        key = base64.b64decode(enrollment.public_key.split()[1], validate=True)
        if len(key) != 51 or key[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20":
            return result(gate.NO_AUTHENTICATED_OWNER_WITNESS)
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip("=")
        if type(raw) is not bytes or not 1 <= len(raw) <= 32768:
            return result(gate.SOURCE_BOUNDARY_CONTRADICTION)
        envelope = _effect_decode(raw, limit=32768)
        _effect_shape(envelope, "payload signature_b64")
        if _effect_canonical(envelope) != raw:
            return result(gate.SOURCE_BOUNDARY_CONTRADICTION)
        p = envelope["payload"]
        _effect_shape(p, "schema namespace binding signer provider verification cleanup publication timestamp replay")
        if (p["schema"] != WORK_OWNER_EFFECT_SCHEMA or p["namespace"] != WORK_OWNER_EFFECT_NAMESPACE or
                work_owner_effect_binding(p["binding"]) != work_owner_effect_binding(expected_binding)):
            return result(gate.SOURCE_BOUNDARY_CONTRADICTION)
        _effect_shape(p["signer"], "identity fingerprint enrollment_sha256")
        if p["signer"] != {"identity": enrollment.identity, "fingerprint": fingerprint,
                           "enrollment_sha256": _effect_digest(enrolled)}:
            return result(gate.NO_AUTHENTICATED_OWNER_WITNESS)
        sig = envelope["signature_b64"]
        if type(sig) is not str or not 40 <= len(sig) <= 16384:
            return result(gate.BAD_SIGNATURE)
        try:
            original = _work_owner_effect_verifier_snapshot(ssh_keygen, enrollment.verifier_sha256)
            with tempfile.TemporaryDirectory(prefix="effect-witness-public-") as folder:
                allowed = Path(folder) / "allowed-signers"
                allowed.write_text(enrollment.identity + " " + enrollment.public_key + "\n", encoding="ascii")
                _effect_verify_signature(p, sig, allowed, enrollment.identity, WORK_OWNER_EFFECT_NAMESPACE, Path(ssh_keygen))
            if _work_owner_effect_verifier_snapshot(ssh_keygen, enrollment.verifier_sha256) != original:
                return result(gate.NO_AUTHENTICATED_OWNER_WITNESS)
        except (_EffectDenied, OSError, ValueError, TypeError):
            return result(gate.BAD_SIGNATURE)
        if type(context) is not WorkOwnerEffectReadContext:
            return result(gate.NO_AUTHENTICATED_OWNER_WITNESS)
        for value in (context.attestation_sha256, context.artifact_sha256, context.verification_sha256,
                      context.post_effect_sha256, context.replay_ledger_sha256, context.clock_id):
            _effect_sha(value)
        if _effect_digest(raw) != context.attestation_sha256:
            return result(gate.SOURCE_BOUNDARY_CONTRADICTION)
        t, replay = p["timestamp"], p["replay"]
        _effect_shape(t, "clock_id monotonic_ns issued_at expires_at")
        _effect_shape(replay, "nonce replay_id ledger_sha256")
        for value in (replay["nonce"], replay["replay_id"], replay["ledger_sha256"], t["clock_id"]):
            _effect_sha(value)
        for value in (t["monotonic_ns"], t["issued_at"], t["expires_at"], context.monotonic_ns, context.unix_time):
            _effect_integer(value, 1, 2**53 - 1)
        if (type(context.seen_nonces) is not frozenset or type(context.seen_replay_ids) is not frozenset or
                len(context.seen_nonces) > 4096 or len(context.seen_replay_ids) > 4096):
            return result(gate.REPLAY_OR_STALE)
        for value in context.seen_nonces | context.seen_replay_ids:
            _effect_sha(value)
        if (t["clock_id"] != context.clock_id or not 1 <= t["expires_at"] - t["issued_at"] <= 600 or
                not t["issued_at"] <= context.unix_time <= t["expires_at"] or
                not 0 <= context.monotonic_ns - t["monotonic_ns"] <= 600_000_000_000 or
                replay["ledger_sha256"] != context.replay_ledger_sha256 or
                replay["nonce"] in context.seen_nonces or replay["replay_id"] in context.seen_replay_ids):
            return result(gate.REPLAY_OR_STALE)
        provider, verification, cleanup, publication = (p[n] for n in
            ("provider", "verification", "cleanup", "publication"))
        _effect_shape(provider, "intent_digest return_determinate outcome return_sha256")
        _effect_shape(verification, "completed completed_monotonic_ns exit_code groups passed skipped failed xdist_workers receipt_sha256 artifact_sha256")
        _effect_shape(cleanup, "completed observed_monotonic_ns scratch_dirty process_tree_empty verified_tree_sha256 post_verify_tree_sha256 receipt_sha256")
        _effect_shape(publication, "state candidate_sha candidate_at_acquisition fence acquisition_monotonic_ns monotonic_ns acquisition_sha256 commit_sha256 post_effect_sha256 intent_only absence_basis")
        for value in (provider["return_sha256"], verification["receipt_sha256"], verification["artifact_sha256"],
                cleanup["receipt_sha256"], cleanup["verified_tree_sha256"], cleanup["post_verify_tree_sha256"],
                publication["acquisition_sha256"], publication["commit_sha256"], publication["post_effect_sha256"]):
            _effect_sha(value)
        for name in ("groups", "passed", "skipped", "failed", "xdist_workers"):
            _effect_integer(verification[name], 0, 1000000)
        # One externally witnessed clock domain, ordered acquisition -> genuine
        # verification -> post-verification cleanup -> committed boundary.
        _effect_integer(publication["acquisition_monotonic_ns"], 1, t["monotonic_ns"])
        _effect_integer(verification["completed_monotonic_ns"], publication["acquisition_monotonic_ns"], t["monotonic_ns"])
        _effect_integer(cleanup["observed_monotonic_ns"], verification["completed_monotonic_ns"], t["monotonic_ns"])
        _effect_integer(publication["monotonic_ns"], cleanup["observed_monotonic_ns"], t["monotonic_ns"])
        if (provider["intent_digest"] != p["binding"]["provider_intent"]["digest"] or
                provider["return_determinate"] is not True or provider["outcome"] != "COMPLETED" or
                verification["completed"] is not True or type(verification["exit_code"]) is not int or
                verification["exit_code"] != 0 or verification["failed"] != 0 or
                verification["groups"] < 1 or verification["passed"] < 1 or
                verification["receipt_sha256"] != context.verification_sha256 or
                verification["artifact_sha256"] != context.artifact_sha256 or
                cleanup["completed"] is not True or cleanup["scratch_dirty"] is not False or
                cleanup["process_tree_empty"] is not True or
                cleanup["verified_tree_sha256"] != cleanup["post_verify_tree_sha256"] or
                publication["fence"] != p["binding"]["work_owner"]["fence"] or
                publication["candidate_at_acquisition"] is not None or
                publication["post_effect_sha256"] != context.post_effect_sha256 or
                publication["intent_only"] is not False):
            return result(gate.SOURCE_BOUNDARY_CONTRADICTION)
        if publication["state"] == "ABSENT_AT_COMMIT_BOUNDARY":
            if publication["candidate_sha"] is not None or publication["absence_basis"] != "FENCED_OWNER_COMMIT_BOUNDARY":
                return result(gate.SOURCE_BOUNDARY_CONTRADICTION)
        elif publication["state"] == "COMMITTED_CANDIDATE":
            _effect_sha(publication["candidate_sha"], 40)
            if publication["absence_basis"] != "NOT_ABSENT":
                return result(gate.SOURCE_BOUNDARY_CONTRADICTION)
        else:
            return result(gate.SOURCE_BOUNDARY_CONTRADICTION)
        return WorkOwnerEffectResult(gate.EFFECT_DETERMINATE_AUTHENTICATED, scope, tuple(sorted({
            "attestation_sha256": _effect_digest(raw), "payload_sha256": _effect_digest(_effect_canonical(p)),
            "enrollment_sha256": _effect_digest(enrolled), "artifact_sha256": context.artifact_sha256,
            "verification_sha256": context.verification_sha256, "post_effect_sha256": context.post_effect_sha256,
            "replay_ledger_sha256": context.replay_ledger_sha256}.items())))
    except Exception:
        # No exception text, signer bytes, paths, Issue prose or signature leaks.
        return result(gate.SOURCE_BOUNDARY_CONTRADICTION)


class AuditGate(str, Enum):
    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"


class AuditProviderStatus(str, Enum):
    UNOBSERVED = "UNOBSERVED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"


@dataclass(frozen=True)
class WorkOwnerAuditDiagnostics:
    """Untrusted local hints can only add a HOLD, never establish authority."""
    provider_status: AuditProviderStatus = AuditProviderStatus.UNOBSERVED
    scratch_dirty: bool | None = None
    cleanup_completed: bool | None = None
    ef01_report_sha256: str | None = None


@dataclass(frozen=True)
class WorkOwnerAuditProvenance:
    ref: str
    sha: str


@dataclass(frozen=True)
class WorkOwnerAuditReport:
    source_gate: AuditGate
    code: str
    stage: str
    generation: int | None
    candidate_sha: str | None
    provenance: tuple[WorkOwnerAuditProvenance, ...]
    reads: int
    provider_status: AuditProviderStatus
    effect_witness: WorkOwnerEffectResult | None = None

    def payload(self) -> dict:
        # Source authentication does not authenticate native effects, cleanup,
        # an EF01 signer, or a new owner. The E1 contract is read-only as well.
        result = {"kind": "WorkOwnerReadOnlyAudit.v1", "status": "HOLD_UNKNOWN_EFFECT",
                "gate": (AuditGate.CONTRADICTED if self.source_gate is AuditGate.CONTRADICTED
                         else AuditGate.UNKNOWN).value,
                "source_gate": self.source_gate.value, "code": self.code,
                "stage": self.stage, "generation": self.generation,
                "candidate_sha": self.candidate_sha,
                "provenance": [{"ref": p.ref, "sha": p.sha} for p in self.provenance],
                "reads": self.reads, "provider_status": self.provider_status.value,
                "owner_witness_gate": AuditGate.UNKNOWN.value,
                "writable_contract": "INACTIVE", "replay_authorized": False,
                "publication_authorized": False, "proves_current_mt01_pass": False}
        if self.effect_witness is not None:
            result["effect_witness"] = self.effect_witness.payload()
        return result


class _AuditUnknown(Exception):
    pass


class _AuditContradicted(Exception):
    pass


class _WorkOwnerAuditReads:
    """GET-only capability with a cumulative budget, including strict readers.

    Mutable Issue/ref observations are re-read before a successful report. This
    detects observed races, not atomic absence or a future publication boundary.
    Immutable commits are cached; missing refs are also checked again. No token,
    request body, exception detail, or Issue prose enters the report.
    """
    MAX_READS = 256
    MAX_BYTES = 4 * 1024 * 1024

    def __init__(self, github, budget):
        if type(budget) is not int or not 1 <= budget <= self.MAX_READS:
            raise _AuditUnknown("READ_BUDGET_INVALID")
        self.repo = github.repo
        if not isinstance(self.repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo):
            raise _AuditUnknown("REPOSITORY_INVALID")
        self._github, self.budget = github, budget
        self.reads, self.bytes = 0, 0
        self.deadline = time.monotonic() + 60
        self.commits, self.mutable = {}, {}
        self.provenance = set()

    def attempt_key(self, attempt_id):
        return GitHubTransport.attempt_key(self, attempt_id)

    @staticmethod
    def sha(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None

    def _ref(self, value, expected):
        if (not isinstance(value, dict) or value.get("ref") != expected or
                not isinstance(value.get("object"), dict) or
                not self.sha(value["object"].get("sha")) or
                value["object"].get("type", "commit") != "commit"):
            raise _AuditContradicted("REF_IDENTITY_MISMATCH")
        self.provenance.add(WorkOwnerAuditProvenance(expected, value["object"]["sha"]))

    def api(self, method, path, data=None):
        if method != "GET" or data is not None:
            raise _AuditContradicted("READ_ONLY_BOUNDARY")
        if not isinstance(path, str) or not re.fullmatch(
                r"/issues(?:/[1-9][0-9]*|\?state=all&per_page=100&page=[1-9][0-9]*)|"
                r"/git/commits/[0-9a-f]{40}|"
                r"/git/(?:ref|matching-refs)/heads/ef02/[a-z-]+/[0-9a-f]{32,64}(?:/[0-9]{3})?/?", path):
            raise _AuditContradicted("READ_ROUTE_INVALID")
        if path in self.commits:
            return json.loads(self.commits[path])
        if self.reads >= self.budget or time.monotonic() >= self.deadline:
            raise _AuditUnknown("READ_BUDGET_EXHAUSTED")
        self.reads += 1
        try:
            value = self._github.api("GET", path)
        except RemoteTransportError as exc:
            if exc.http_status == 404 or str(exc) == "GITHUB_HTTP_404":
                self.mutable[path] = None
            raise
        if isinstance(value, list) and len(value) > (WorkOwner.LIMIT if "/git/" in path else 100):
            raise _AuditContradicted("RESPONSE_BOUND_INVALID")
        encoded = canonical(value)
        self.bytes += len(encoded.encode("utf-8"))
        if len(encoded) > 262144 or self.bytes > self.MAX_BYTES:
            raise _AuditUnknown("READ_SIZE_BUDGET_EXHAUSTED")
        if path.startswith("/git/ref/"):
            self._ref(value, "refs/" + path.removeprefix("/git/ref/"))
        elif path.startswith("/git/matching-refs/"):
            prefix = "refs/" + path.removeprefix("/git/matching-refs/")
            if not isinstance(value, list):
                raise _AuditContradicted("JOURNAL_REFS_INVALID")
            names = []
            for ref in value:
                name = ref.get("ref") if isinstance(ref, dict) else None
                if not isinstance(name, str) or not re.fullmatch(re.escape(prefix) + r"[0-9]{3}", name):
                    raise _AuditContradicted("JOURNAL_REF_NAMESPACE_MISMATCH")
                self._ref(ref, name)
                names.append(name)
            if len(names) != len(set(names)):
                raise _AuditContradicted("JOURNAL_REF_DUPLICATE")
        elif path.startswith("/git/commits/"):
            sha = path.rsplit("/", 1)[1]
            if (not isinstance(value, dict) or value.get("sha", sha) != sha or
                    not isinstance(value.get("tree"), dict) or not self.sha(value["tree"].get("sha")) or
                    not isinstance(value.get("parents"), list) or
                    any(not isinstance(p, dict) or not self.sha(p.get("sha")) for p in value["parents"])):
                raise _AuditContradicted("COMMIT_IDENTITY_INVALID")
            self.commits[path] = encoded
        if path not in self.commits:
            previous = self.mutable.get(path, encoded)
            if previous != encoded:
                raise _AuditUnknown("CONCURRENT_SOURCE_CHANGE")
            self.mutable[path] = encoded
        return value

    def optional_ref(self, namespace, key):
        try:
            return self.api("GET", f"/git/ref/heads/ef02/{namespace}/{key}")
        except RemoteTransportError as exc:
            if exc.http_status == 404 or str(exc) == "GITHUB_HTTP_404":
                return None
            raise

    def confirm(self):
        for path, original in tuple(self.mutable.items()):
            try:
                current = canonical(self.api("GET", path))
            except RemoteTransportError as exc:
                if exc.http_status != 404 and str(exc) != "GITHUB_HTTP_404":
                    raise
                current = None
            if current != original:
                raise _AuditUnknown("CONCURRENT_SOURCE_CHANGE")


class WorkOwnerAudit:
    """Bounded source forensics, never reconciliation or a terminal transfer.

    trusted_issuer comes from the owner's validated ingress configuration, not
    the Issue or model. A VERIFIED source gate identifies an authenticated
    PUBLISHED Git candidate only. Native receipt/signer/owner witness remains
    UNKNOWN. Any future writable contract is INACTIVE and requires independent
    owner authentication at the actual publication boundary. This class cannot
    acquire, append, transfer, replay, or publish a WorkOwner.
    """
    @classmethod
    def run(cls, github, *, issue_number, batch, action, trusted_issuer,
            read_budget=192, diagnostics=None, effect_witness=None, witness_enrollment=None,
            witness_context=None, witness_verifier=None, source_contract_fixture=False) -> WorkOwnerAuditReport:
        reads = None
        stage, generation, candidate_sha = "UNOBSERVED", None, None
        provider = AuditProviderStatus.UNOBSERVED
        witness = None
        if effect_witness is not None:
            witness = WorkOwnerEffectResult(WorkOwnerEffectGate.SOURCE_BOUNDARY_CONTRADICTION)
        try:
            diagnostics = diagnostics or WorkOwnerAuditDiagnostics()
            if (not isinstance(diagnostics, WorkOwnerAuditDiagnostics) or
                    not isinstance(diagnostics.provider_status, AuditProviderStatus) or
                    any(v is not None and type(v) is not bool for v in
                        (diagnostics.scratch_dirty, diagnostics.cleanup_completed)) or
                    (diagnostics.ef01_report_sha256 is not None and not re.fullmatch(
                        r"[0-9a-f]{64}", diagnostics.ef01_report_sha256))):
                raise _AuditUnknown("DIAGNOSTIC_HINT_INVALID")
            provider = diagnostics.provider_status
            if not GitHubTransport._valid_issuer_login(trusted_issuer):
                raise _AuditUnknown("TRUSTED_ISSUER_REQUIRED")
            reads = _WorkOwnerAuditReads(github, read_budget)
            identity = WorkOwner.identity(reads, issue_number, batch, action)
            contexts = []
            for pred in reversed(identity["lineage"]):
                packet, submitted = cls._ingress(reads, pred["issue"], pred["attempt_id"], trusted_issuer)
                contexts.append((pred["issue"], packet, submitted, pred["fingerprint"]))
            packet, submitted = cls._ingress(reads, issue_number, action["attempt_id"], trusted_issuer)
            contexts.append((issue_number, packet, submitted, identity["fingerprint"]))
            # Retain the existing strict transition reader; no permissive second
            # journal decoder or downgrade around LEGACY_MIGRATION_UNPROVEN.
            owner = WorkOwner(reads, identity, "")
            chain = owner.read()
            if not chain:
                raise _AuditUnknown("JOURNAL_UNOBSERVED")
            stage, generation = chain[-1]["stage"], chain[-1]["generation"]
            if generation > identity["generation"]:
                raise _AuditContradicted("STALE_GENERATION")
            if generation != identity["generation"]:
                raise _AuditUnknown("GENERATION_NOT_OBSERVED")
            root_source = reads.api("GET", "/git/commits/" + contexts[0][1]["source_sha"])
            first = reads.api("GET", "/git/commits/" + chain[0]["sha"])
            if first["tree"] != root_source["tree"]:
                raise _AuditContradicted("GENESIS_TREE_MISMATCH")
            for gen, (number, packet, submitted, fp) in enumerate(contexts, 1):
                records = [r for r in chain if r["generation"] == gen]
                if not records:
                    raise _AuditUnknown("GENERATION_NOT_OBSERVED")
                calculated = hashlib.sha256(json.dumps({"batch_id": packet["batch_id"],
                    "source_sha": packet["source_sha"], "action": submitted}, sort_keys=True,
                    separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
                if calculated != fp:
                    raise _AuditContradicted("ACTION_FINGERPRINT_MISMATCH")
                genesis = records[0]
                if gen > 1 and (genesis["data"].get("predecessor_tip") != chain[genesis["sequence"] - 1]["digest"] or
                        genesis["data"].get("repair_of") != str(submitted.get("repair_of"))):
                    raise _AuditContradicted("GENERATION_TRANSFER_MISMATCH")
                if genesis["identity"] == owner.base_identity:
                    if (genesis["data"].get("attempt_id") != submitted["attempt_id"] or
                            genesis["data"].get("issue") != number):
                        raise _AuditContradicted("GENERATION_INGRESS_MISMATCH")
                else:
                    legacy_identity = {"repo": reads.repo, "issue": number, "action_id": submitted["action_id"],
                        "work_key": WorkOwner.digest({"repo": reads.repo, "issue": number,
                                                       "action_id": submitted["action_id"]}),
                        "source_sha": packet["source_sha"], "batch_id": packet["batch_id"],
                        "attempt_id": submitted["attempt_id"], "fingerprint": fp}
                    if any(r["identity"] != legacy_identity for r in records):
                        raise _AuditContradicted("LEGACY_INGRESS_MISMATCH")
                # Legacy full identities were already authenticated by read().
                cls._boundaries(reads, number, packet, submitted, fp, trusted_issuer, records)
                intents = [r for r in records if r["stage"] == "PROVIDER_INTENT"]
                completed = [r for r in records if r["stage"] == "ROUND_VERIFIED"]
                if len(intents) != len(completed):
                    raise _AuditUnknown("PROVIDER_OUTCOME_UNKNOWN")
                terminal = records[-1]
                candidate = cls._candidate(reads, packet, submitted, fp, terminal,
                    diagnostics if gen == generation else WorkOwnerAuditDiagnostics())
                if gen == generation:
                    candidate_sha = candidate
            reads.confirm()
            if diagnostics.cleanup_completed is False:
                raise _AuditUnknown("CLEANUP_UNCONFIRMED")
            if diagnostics.scratch_dirty is True or provider is AuditProviderStatus.QUOTA_EXHAUSTED:
                raise _AuditUnknown("LOCAL_EFFECT_UNCONFIRMED")
            gate, code = AuditGate.VERIFIED, "PUBLISHED_SOURCE_AUTHENTICATED_OWNER_WITNESS_MISSING"
            if effect_witness is not None:
                binding = cls._effect_binding(reads, identity, chain)
                witness = work_owner_effect_witness_verify_offline(effect_witness,
                    expected_binding=binding, enrollment=witness_enrollment, context=witness_context,
                    ssh_keygen=witness_verifier, source_contract_fixture=source_contract_fixture)
                if witness.gate is WorkOwnerEffectGate.EFFECT_DETERMINATE_AUTHENTICATED:
                    # Join only to the SAME already-authenticated committed candidate.
                    # A signed absence/staged-intent receipt cannot describe it.
                    p = json.loads(effect_witness)["payload"]
                    proof = commit_evidence(reads.api("GET", "/git/commits/" + candidate_sha)["message"])
                    if (p["publication"]["state"] != "COMMITTED_CANDIDATE" or
                            p["publication"]["candidate_sha"] != candidate_sha or
                            p["verification"]["receipt_sha256"] != proof["verification"]["report_sha256"]):
                        witness = WorkOwnerEffectResult(WorkOwnerEffectGate.SOURCE_BOUNDARY_CONTRADICTION)
                # Includes races during offline signature verification and all
                # extra witness reads in the original cumulative budget.
                reads.confirm()
        except _AuditContradicted as exc:
            gate, code = AuditGate.CONTRADICTED, str(exc)
        except _AuditUnknown as exc:
            gate, code = AuditGate.UNKNOWN, str(exc)
        except RemoteTransportError as exc:
            # Never persist arbitrary transport text, even from a fake reader.
            if (exc.http_status is not None or str(exc).startswith("GITHUB_") or
                    str(exc) == "WORK_RECONCILE_READ_BUDGET_EXHAUSTED"):
                gate, code = AuditGate.UNKNOWN, "REMOTE_READ_UNCONFIRMED"
            else:
                gate, code = AuditGate.CONTRADICTED, "STRICT_WORKOWNER_REJECTED"
        except Exception:
            gate, code = AuditGate.UNKNOWN, "MALFORMED_OR_UNAVAILABLE_SOURCE"
        if gate is not AuditGate.VERIFIED and witness is not None:
            witness = WorkOwnerEffectResult(WorkOwnerEffectGate.SOURCE_BOUNDARY_CONTRADICTION)
        return WorkOwnerAuditReport(gate, code, stage, generation, candidate_sha,
            tuple(sorted(reads.provenance, key=lambda p: (p.ref, p.sha))) if reads else (),
            reads.reads if reads else 0, provider, witness)

    @staticmethod
    def _effect_binding(reads, identity, chain):
        """Use strict-reader objects only, never expected values from a witness."""
        current = [r for r in chain if r["generation"] == identity["generation"]]
        intents = [r for r in current if r["stage"] == "PROVIDER_INTENT"]
        if not intents:
            raise _AuditUnknown("PROVIDER_INTENT_UNOBSERVED")
        intent, tip, root = intents[-1], chain[-1], chain[0]
        key = reads.attempt_key(identity["attempt_id"])
        provenance = {p.ref: p.sha for p in reads.provenance}
        refs = {}
        for name, namespace in (("claim", "claims"), ("pre_spawn", "pre-spawn"), ("spawned", "spawned")):
            ref = f"refs/heads/ef02/{namespace}/{key}"
            if ref not in provenance:
                raise _AuditUnknown("BOUNDARY_WITNESS_UNOBSERVED")
            refs[name] = {"ref": ref, "sha": provenance[ref]}
        return work_owner_effect_binding({"repo": reads.repo, "root_issue": identity["root_issue"],
            "current_issue": identity["current_issue"], "action_id": identity["action_id"],
            "attempt_id": identity["attempt_id"], "source_sha": identity["source_sha"],
            "work_owner": {"root_commit_sha": root["sha"], "root_digest": root["digest"],
                "journal_tip_sha": tip["sha"], "journal_digest": tip["digest"],
                "work_key": identity["work_key"], "fingerprint": identity["fingerprint"],
                "fence": tip["fence"], "generation": tip["generation"]}, "refs": refs,
            "provider_intent": {"commit_sha": intent["sha"], "digest": intent["digest"],
                                "round": intent["data"]["round"]}})

    @staticmethod
    def _ingress(reads, number, attempt_id, issuer):
        issue = reads.api("GET", f"/issues/{number}")
        if (not isinstance(issue, dict) or issue.get("number") != number or "pull_request" in issue or
                not isinstance(issue.get("user"), dict) or
                not isinstance(issue["user"].get("login"), str) or
                issue["user"]["login"].casefold() != issuer.casefold()):
            raise _AuditContradicted("ISSUE_ISSUER_MISMATCH")
        packet = json.loads(issue["body"])
        actions = [a for a in packet["actions"] if a.get("attempt_id") == attempt_id]
        if len(actions) != 1 or "resolved_dependencies" in actions[0]:
            raise _AuditContradicted("ATTEMPT_IDENTITY_AMBIGUOUS")
        return packet, actions[0]

    @staticmethod
    def _boundaries(reads, number, packet, action, fp, issuer, records):
        key = reads.attempt_key(action["attempt_id"])
        source = reads.api("GET", "/git/commits/" + packet["source_sha"])
        index = reads.optional_ref("index", key)
        if index is None:
            index = reads.optional_ref("indices", key)
        if index is None:
            raise _AuditUnknown("INDEX_UNOBSERVED")
        index_commit = reads.api("GET", "/git/commits/" + index["object"]["sha"])
        GitHubTransport._verify_index_commit_object(reads._github, index_commit,
            attempt_id=action["attempt_id"], issue_number=number,
            source_sha=packet["source_sha"], fingerprint=fp, expected_issuer=issuer)
        if index_commit["tree"] != source["tree"]:
            raise _AuditContradicted("INDEX_TREE_MISMATCH")
        stages = {r["stage"] for r in records}
        if "PUBLISHED" in stages and "CHILD_OBSERVED" not in stages:
            raise _AuditUnknown("CHILD_WITNESS_UNOBSERVED")
        parent = packet["source_sha"]
        for namespace, marker, needed in (("claims", "CLAIM", "CLAIMED" in stages),
                ("pre-spawn", "PRESPAWN", "SPAWN_INTENT" in stages),
                ("spawned", "SPAWNED", "CHILD_OBSERVED" in stages)):
            ref = reads.optional_ref(namespace, key)
            if ref is None:
                if needed:
                    raise _AuditUnknown("BOUNDARY_WITNESS_UNOBSERVED")
                continue
            commit = reads.api("GET", "/git/commits/" + ref["object"]["sha"])
            message = f"EF02 {marker}\n\nfingerprint={fp}\nattempt_id={action['attempt_id']}"
            if namespace == "spawned":
                observed = [r for r in records if r["stage"] == "CHILD_OBSERVED"]
                if len(observed) != 1:
                    raise _AuditUnknown("CHILD_WITNESS_UNOBSERVED")
                message += "\nchild_pid=" + str(observed[0]["data"]["pid"])
            if (commit.get("message") != message or
                    [p["sha"] for p in commit["parents"]] != [parent] or
                    commit["tree"] != source["tree"]):
                raise _AuditContradicted("BOUNDARY_IDENTITY_MISMATCH")
            parent = ref["object"]["sha"]

    @staticmethod
    def _candidate(reads, packet, action, fp, terminal, diagnostics):
        ref = reads.optional_ref("candidate", reads.attempt_key(action["attempt_id"]))
        if ref is None:
            raise _AuditUnknown("CANDIDATE_UNOBSERVED_NOT_EFFECT_ABSENCE")
        sha = ref["object"]["sha"]
        commit = reads.api("GET", "/git/commits/" + sha)
        try:
            proof = commit_evidence(commit.get("message", ""))
        except ValueError:
            raise _AuditContradicted("CANDIDATE_EVIDENCE_INVALID") from None
        if proof is None:
            raise _AuditUnknown("TERMINAL_EVIDENCE_UNOBSERVED")
        if (proof["source_sha"] != packet["source_sha"] or proof["fingerprint"] != fp or
                proof["attempt_id"] != action["attempt_id"] or proof["action_id"] != action["action_id"] or
                proof["allowed_paths"] != action["allowed_paths"] or
                proof["verification"]["profile"] != action["verification_profile"] or
                any(proof["provenance"][k] != action[k] for k in ("executor", "model", "effort")) or
                [p["sha"] for p in commit["parents"]] != [proof["effective_source_sha"]]):
            raise _AuditContradicted("CANDIDATE_IDENTITY_MISMATCH")
        if (diagnostics.ef01_report_sha256 is not None and
                proof["verification"]["report_sha256"] != diagnostics.ef01_report_sha256):
            raise _AuditContradicted("EF01_RECEIPT_CONTRADICTED")
        if proof["provenance"]["dependency_candidates"] or action.get("depends_on"):
            raise _AuditUnknown("DEPENDENCY_SOURCE_WITNESS_UNOBSERVED")
        if proof["effective_source_sha"] != packet["source_sha"]:
            raise _AuditContradicted("CANDIDATE_PARENT_MISMATCH")
        if terminal["stage"] != "PUBLISHED":
            raise _AuditUnknown("PUBLISHED_TERMINAL_UNOBSERVED")
        if terminal["data"].get("attempt_id", action["attempt_id"]) != action["attempt_id"]:
            raise _AuditContradicted("TERMINAL_ATTEMPT_MISMATCH")
        if (terminal["data"].get("candidate") != WorkOwner.digest(sha) or
                terminal["data"].get("receipt") != evidence_digest(proof)):
            raise _AuditContradicted("TERMINAL_RECEIPT_MISMATCH")
        return sha


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
        except (KeyError, TypeError, ValueError, OSError, HTTPException, AssertionError):
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
        except (KeyError, TypeError, ValueError, OSError, HTTPException, AssertionError):
            raise RemoteTransportError("", publication=PublicationDiagnostic(stage)) from None

    def attempt_key(self, attempt_id: str) -> str:
        return hashlib.sha256((self.repo + ":" + attempt_id).encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _valid_issuer_login(value: str | None) -> bool:
        if not isinstance(value, str):
            return False
        s = value.strip()
        if not s or s != value or s.upper() in ("HUMAN", "BRAIN"):
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}(?:\[bot\])?", s))

    @staticmethod
    def _parse_index_message(msg: str) -> dict | None:
        if not isinstance(msg, str):
            return None
        parts = msg.strip().split("\n\n")
        if not parts or not (parts[0].startswith("EF02 INDEX") or parts[0].startswith("EF02-INDEX")):
            return None
        res = {}
        for part in parts[1:]:
            part_str = part.strip()
            if part_str.startswith("{") and part_str.endswith("}"):
                try:
                    data = json.loads(part_str)
                    if isinstance(data, dict):
                        res.update(data)
                except Exception:
                    pass
            for line in part_str.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if k in ("issue", "issue_number"):
                        try:
                            v = int(v)
                        except ValueError:
                            pass
                    res[k] = v
        if "attempt_id" in res and ("issue" in res or "issue_number" in res):
            if "issue" not in res and "issue_number" in res:
                res["issue"] = res["issue_number"]
            return res
        return None

    def _verify_index_commit_object(self, commit_obj: dict, *, attempt_id: str, issue_number: int,
                                    source_sha: str, fingerprint: str, expected_issuer: str) -> None:
        if not isinstance(commit_obj, dict):
            raise RemoteTransportError("WORK_LINEAGE_INDEX_COMMIT_INVALID")
        parsed = self._parse_index_message(commit_obj.get("message", ""))
        if not parsed:
            raise RemoteTransportError("WORK_LINEAGE_INDEX_COMMIT_INVALID")
        rec_issuer = parsed.get("issuer")
        if not isinstance(rec_issuer, str) or not self._valid_issuer_login(rec_issuer):
            raise RemoteTransportError("WORK_LINEAGE_INDEX_ISSUER_MISMATCH")
        if rec_issuer.casefold() != expected_issuer.casefold():
            raise RemoteTransportError("WORK_LINEAGE_INDEX_ISSUER_MISMATCH")
        if (parsed.get("attempt_id") != attempt_id or
                parsed.get("issue") != issue_number or
                parsed.get("source_sha") != source_sha or
                parsed.get("fingerprint") != fingerprint or
                [p.get("sha") for p in commit_obj.get("parents", []) if isinstance(p, dict)] != [source_sha]):
            raise RemoteTransportError("WORK_LINEAGE_INDEX_COLLISION")

    def index_attempt(self, *, issue_number: int, source_sha: str, attempt_id: str,
                      fingerprint: str, issuer: str | None = None) -> str:
        """Create-only SHA-authenticated attempt->issue index ref with read-back verification.

        Binds attempt_id to issue_number, source_sha, fingerprint, and independently
        verified GitHub Issue creator login. Fails closed on any mismatch, collision,
        or unverified write.
        """
        if type(issue_number) is not int or not 1 <= issue_number <= 2**53:
            raise RemoteTransportError("WORK_IDENTITY_UNKNOWN_TRUSTED_ISSUE_REQUIRED")
        if (not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha) or
                not isinstance(attempt_id, str) or not attempt_id or
                not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)):
            raise RemoteTransportError("WORK_LINEAGE_INDEX_COMMIT_INVALID")
        if issuer is not None and not self._valid_issuer_login(issuer):
            raise RemoteTransportError("WORK_INDEX_ISSUER_INVALID")

        issue_obj = self.api("GET", f"/issues/{issue_number}")
        if not isinstance(issue_obj, dict) or issue_obj.get("number") != issue_number or "pull_request" in issue_obj:
            raise RemoteTransportError("WORK_INGRESS_MISMATCH")
        user_obj = issue_obj.get("user")
        actual_issuer = user_obj.get("login", "") if isinstance(user_obj, dict) else ""
        if not isinstance(actual_issuer, str) or not self._valid_issuer_login(actual_issuer):
            raise RemoteTransportError("WORK_INDEX_ISSUE_CREATOR_MISSING")
        if issuer is not None and actual_issuer.casefold() != issuer.casefold():
            raise RemoteTransportError("WORK_LINEAGE_INDEX_ISSUER_MISMATCH")

        resolved_issuer = actual_issuer
        key = self.attempt_key(attempt_id)
        ref_name = "ef02/index/" + key
        full_ref = "refs/heads/" + ref_name

        existing = None
        try:
            existing = self.api("GET", "/git/ref/heads/" + ref_name)
        except RemoteTransportError as exc:
            if str(exc) != "GITHUB_HTTP_404":
                raise

        if existing is not None:
            if not isinstance(existing, dict) or existing.get("ref", full_ref) != full_ref:
                raise RemoteTransportError("WORK_LINEAGE_INDEX_REF_INVALID")
            existing_sha = existing.get("object", {}).get("sha") if isinstance(existing.get("object"), dict) else None
            if not isinstance(existing_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", existing_sha):
                raise RemoteTransportError("WORK_LINEAGE_INDEX_REF_INVALID")
            existing_commit = self.api("GET", "/git/commits/" + existing_sha)
            self._verify_index_commit_object(
                existing_commit, attempt_id=attempt_id, issue_number=issue_number,
                source_sha=source_sha, fingerprint=fingerprint, expected_issuer=resolved_issuer
            )
            return full_ref

        source_commit = self.api("GET", "/git/commits/" + source_sha)
        tree_sha = source_commit.get("tree", {}).get("sha") if isinstance(source_commit, dict) and isinstance(source_commit.get("tree"), dict) else None
        if not isinstance(tree_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", tree_sha):
            raise RemoteTransportError("WORK_LINEAGE_INDEX_COMMIT_INVALID")
        payload = {
            "attempt_id": attempt_id,
            "issue": issue_number,
            "source_sha": source_sha,
            "fingerprint": fingerprint,
            "issuer": resolved_issuer,
        }
        msg = (f"EF02 INDEX\n\nattempt_id={attempt_id}\nissue={issue_number}\nsource_sha={source_sha}"
               f"\nfingerprint={fingerprint}\nissuer={resolved_issuer}\n\n" + canonical(payload))
        commit_obj = self.api("POST", "/git/commits", {
            "message": msg,
            "tree": tree_sha,
            "parents": [source_sha]
        })
        created_sha = commit_obj.get("sha") if isinstance(commit_obj, dict) else None
        if not isinstance(created_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", created_sha):
            raise RemoteTransportError("WORK_LINEAGE_INDEX_COMMIT_INVALID")
        try:
            self._create_ref(ref_name, created_sha)
        except RemoteTransportError as exc:
            if "422" not in str(exc) and "ALREADY_EXISTS" not in str(exc):
                raise
        # Always perform strict read-back verification of ref and commit object.
        confirmed = self.api("GET", "/git/ref/heads/" + ref_name)
        if not isinstance(confirmed, dict) or confirmed.get("ref", full_ref) != full_ref:
            raise RemoteTransportError("WORK_LINEAGE_INDEX_REF_INVALID")
        confirmed_sha = confirmed.get("object", {}).get("sha") if isinstance(confirmed.get("object"), dict) else None
        if not isinstance(confirmed_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", confirmed_sha):
            raise RemoteTransportError("WORK_LINEAGE_INDEX_REF_INVALID")
        confirmed_commit = self.api("GET", "/git/commits/" + confirmed_sha)
        self._verify_index_commit_object(
            confirmed_commit, attempt_id=attempt_id, issue_number=issue_number,
            source_sha=source_sha, fingerprint=fingerprint, expected_issuer=resolved_issuer
        )
        return full_ref

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
                  expected_hashes: dict[str, str | None], evidence: dict | None = None,
                  owner: WorkOwner | None = None) -> dict:
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
        self._candidate_owner_fence(owner, proof)
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
        self._candidate_owner_fence(owner, proof)
        self._publication_api(PublicationStage.CANDIDATE_REF_CREATE,
                              {"ref": expected_ref, "sha": expected_sha},
                              expected_ref=expected_ref, expected_sha=expected_sha)
        return {"candidate_sha": commit["sha"], "candidate_ref": expected_ref,
                "status": proof["status"], "review_required": proof["review_required"],
                "candidate_evidence": proof, "candidate_evidence_sha256": evidence_digest(proof)}

    def _candidate_owner_fence(self, owner, proof):
        if owner is None:
            return
        if owner.github is not self:
            raise CandidateIntegrityError("WORK_OWNER_PUBLICATION_FENCE_REQUIRED")
        chain = owner.assert_current()
        identity = owner.identity
        if (chain[-1]["stage"] != "PUBLISH_INTENT" or
                chain[-1]["data"].get("receipt") != evidence_digest(proof) or
                identity["repo"] != self.repo or
                any(identity[k] != proof[k] for k in ("source_sha", "attempt_id", "action_id", "fingerprint"))):
            raise CandidateIntegrityError("WORK_OWNER_PUBLICATION_FENCE_MISMATCH")

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
