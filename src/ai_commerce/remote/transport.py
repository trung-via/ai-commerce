"""GitHub REST evidence channel. No AIOS tokens, no auto merge to main."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
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


class RemoteTransportError(RuntimeError):
    pass


class CandidateIntegrityError(RemoteTransportError):
    """Workspace bytes do not match the verified EF01 manifest."""


class WorkOwner:
    """WorkOwner.v1: create-only generation 1, bounded hash-linked journal.

    Identity is repository + independently read issue number + action ID. v1
    ingress has no authenticated cross-issue repair lineage or transfer grant:
    existing owners are HOLD, including terminal owners. There is no lease
    expiry, generation takeover, ref update, rollback or automatic replay.

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
    def identity(cls, github, issue_number, batch, action):
        if type(issue_number) is not int or not 1 <= issue_number <= 2**53:
            raise RemoteTransportError("WORK_IDENTITY_UNKNOWN_TRUSTED_ISSUE_REQUIRED")
        issue = github.api("GET", "/issues/" + str(issue_number))
        body = issue.get("body")
        if (issue.get("number") != issue_number or "pull_request" in issue or
                not isinstance(body, str) or len(body.encode("utf-8")) > 262144):
            raise RemoteTransportError("WORK_INGRESS_MISMATCH")
        try:
            packet = json.loads(body)
        except (TypeError, ValueError) as exc:
            raise RemoteTransportError("WORK_INGRESS_MISMATCH") from exc
        # Compare the entire validated request, not instruction prose or an
        # executor-provided root ID. resolved_dependencies is scheduler-only.
        if packet != batch or not any(a == action for a in packet.get("actions", [])):
            raise RemoteTransportError("WORK_INGRESS_MISMATCH")
        identity = {"repo": github.repo, "issue": issue_number,
                    "action_id": action["action_id"]}
        return {**identity, "work_key": cls.digest(identity),
                "source_sha": batch["source_sha"], "batch_id": batch["batch_id"],
                "attempt_id": action["attempt_id"],
                "fingerprint": hashlib.sha256(json.dumps({"batch_id": batch["batch_id"],
                    "source_sha": batch["source_sha"], "action": action}, sort_keys=True,
                    separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()}

    def __init__(self, github, identity, fence):
        self.github, self.identity, self.fence = github, dict(identity), fence
        self.prefix = "ef02/workowner/" + identity["work_key"] + "/"
        self.lock = threading.RLock()
        self.tip = None
        self.uncertain = False

    @classmethod
    def acquire(cls, github, identity):
        owner = cls(github, identity, secrets.token_hex(32))
        # Read failures are UNKNOWN, never absence. Never adopt an existing
        # journal after restart, even if it has the same request fingerprint.
        if owner.read():
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
        owner._create("OWNED", [], {})
        return owner

    def read(self):
        deadline = time.monotonic() + 60
        def get(path):
            if time.monotonic() >= deadline:
                raise RemoteTransportError("WORK_RECONCILE_READ_BUDGET_EXHAUSTED")
            return self.github.api("GET", path)
        refs = get("/git/matching-refs/heads/" + self.prefix)
        if not isinstance(refs, list) or len(refs) > self.LIMIT:
            raise RemoteTransportError("WORK_JOURNAL_BOUND_INVALID")
        refs = sorted(refs, key=lambda ref: ref.get("ref", ""))
        chain = []
        tree = None
        for index, ref in enumerate(refs):
            if ref.get("ref") != "refs/heads/" + self.prefix + f"{index:03d}":
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
            parent = prior["sha"] if prior else self.identity["source_sha"]
            if (set(record) != {"kind", "version", "identity", "generation", "fence",
                    "sequence", "previous", "stage", "data"} or
                    record["kind"] != "WorkOwner" or type(record["version"]) is not int or record["version"] != 1 or
                    type(record["generation"]) is not int or record["generation"] != 1 or
                    record["identity"] != self.identity or type(record["sequence"]) is not int or record["sequence"] != index or
                    record["previous"] != (prior["digest"] if prior else None) or
                    digest != self.digest(record) or
                    not re.fullmatch("[0-9a-f]{64}", str(record["fence"])) or
                    (prior and record["fence"] != prior["fence"]) or
                    [p.get("sha") for p in commit.get("parents", [])] != [parent]):
                raise RemoteTransportError("WORK_JOURNAL_INTEGRITY_MISMATCH")
            current_tree = commit.get("tree", {}).get("sha")
            if tree is None:
                source = get("/git/commits/" + self.identity["source_sha"])
                tree = source.get("tree", {}).get("sha")
            if not tree or current_tree != tree:
                raise RemoteTransportError("WORK_JOURNAL_TREE_MISMATCH")
            self._transition(record["stage"], chain, record["data"])
            chain.append({**record, "digest": digest, "sha": sha})
        return chain

    @classmethod
    def _transition(cls, stage, chain, data):
        previous = chain[-1]["stage"] if chain else None
        if (stage not in cls.EDGES or (not chain and stage != "OWNED") or
                (chain and stage not in cls.EDGES[previous] and not (
                    stage == "UNKNOWN" and previous not in ("PUBLISHED", "UNKNOWN")))):
            raise RemoteTransportError("WORK_JOURNAL_TRANSITION_INVALID")
        if (not isinstance(data, dict) or set(data) - {"round", "pid", "receipt", "candidate"} or
                any(not re.fullmatch("[0-9a-f]{64}", str(data[k]))
                    for k in ("receipt", "candidate") if k in data) or
                ("pid" in data and (type(data["pid"]) is not int or not 0 < data["pid"] < 2**32))):
            raise RemoteTransportError("WORK_JOURNAL_DATA_INVALID")
        intents = [r for r in chain if r["stage"] == "PROVIDER_INTENT"]
        verified = [r for r in chain if r["stage"] == "ROUND_VERIFIED"]
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
        if stage == "CHILD_OBSERVED" and ("pid" not in data or any(r["stage"] == stage for r in chain)):
            raise RemoteTransportError("WORK_CHILD_DUPLICATE_OR_MISMATCH")

    def assert_current(self):
        if self.uncertain:
            raise RemoteTransportError("WORK_OWNER_STALE_FENCE")
        chain = self.read()
        if (self.uncertain or not chain or chain[0]["fence"] != self.digest(self.fence) or
                (self.tip is not None and chain[-1]["digest"] != self.tip) or
                chain[-1]["stage"] in ("UNKNOWN", "PUBLISHED")):
            raise RemoteTransportError("WORK_OWNER_STALE_FENCE")
        return chain

    def _create(self, stage, chain, data):
        if len(chain) >= self.LIMIT:
            raise RemoteTransportError("WORK_JOURNAL_BOUND_INVALID")
        self._transition(stage, chain, data)
        record = {"kind": "WorkOwner", "version": 1, "identity": self.identity,
                  "generation": 1, "fence": self.digest(self.fence), "sequence": len(chain),
                  "previous": chain[-1]["digest"] if chain else None, "stage": stage, "data": data}
        record["digest"] = self.digest(record)
        source = self.github.api("GET", "/git/commits/" + self.identity["source_sha"])
        commit = self.github.api("POST", "/git/commits", {
            "message": self.MARKER + canonical(record), "tree": source["tree"]["sha"],
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
        chain = self.read()
        result = {"status": "HOLD_UNKNOWN_EFFECT", "replay_authorized": False,
                  "publication_authorized": False, "generation": 1,
                  "proves_current_mt01_pass": False,
                  "stage": chain[-1]["stage"] if chain else "ABSENT",
                  "lineage_gap": "V1_HAS_NO_TRUSTED_CROSS_ISSUE_REPAIR_OR_TRANSFER"}
        if chain and chain[-1]["stage"] in ("PUBLISH_INTENT", "PUBLISHED"):
            try:
                ref = self.github.api("GET", "/git/ref/heads/ef02/candidate/" +
                                      self.github.attempt_key(self.identity["attempt_id"]))
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
                    proof["attempt_id"] != self.identity["attempt_id"] or
                    proof["action_id"] != self.identity["action_id"] or
                    proof["fingerprint"] != self.identity["fingerprint"] or
                    [p.get("sha") for p in candidate.get("parents", [])] != [proof["effective_source_sha"]]):
                raise RemoteTransportError("WORK_TERMINAL_RECEIPT_MISMATCH")
            result.update(status="RECONCILED_VERIFIED_TERMINAL", candidate_sha=sha)
        return result


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
                          {"message": "EF02 candidate attempt " + attempt_id + EVIDENCE_MARKER + canonical(proof),
                           "tree": remote_tree["sha"], "parents": [source_sha]})
        name = "ef02/candidate/" + self.attempt_key(attempt_id)
        self._candidate_owner_fence(owner, proof)
        self._create_ref(name, commit["sha"])
        return {"candidate_sha": commit["sha"], "candidate_ref": "refs/heads/" + name,
                "status": proof["status"], "review_required": proof["review_required"],
                "candidate_evidence": proof, "candidate_evidence_sha256": evidence_digest(proof)}

    def _candidate_owner_fence(self, owner, proof):
        if owner is None or owner.github is not self:
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
