"""W1.4 Phase 2: bind private GitHub REST identity to a separate broker SSHSIG.

Pure, offline verification. No GitHub token, network, worker, Windows effect,
ledger write, self-host runner admission or provider credentials. Input REST
objects must be obtained by an independently authenticated broker outside the
public Actions runner Windows SID; this module CANNOT attest its custody/TLS.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import auth, w1_bridge
from .contract import CONTROL_REPOSITORY, canonical, deny, digest, sha, shape

REPO_URL = "https://api.github.com/repos/" + CONTROL_REPOSITORY
REPO_ID = 1443294096
OWNER_ID = 301941572
OWNER_LOGIN = "trung-via"
BROKER_SCHEMA = "hostops.personal.w14_origin_attestation.v1"
BROKER_ENVELOPE_SCHEMA = "hostops.personal.w14_signed_origin.v1"
BROKER_NAMESPACE = "hostops-w14-github-origin@ai-commerce"
BROKER_PRINCIPAL = "hostops-github-broker"
MAX_BROKER_TTL_SECONDS = 120


def _positive(value):
    return type(value) is int and 0 < value <= 2**53 - 1


def normalize_private_issue_rest(repo, issue, *, issue_number):
    """Validate pinned REST metadata; NOT proof of who supplied the JSON.

    Do not use user-controlled URLs or issue-selected paths. A caller/broker
    must have obtained these two REST objects from the real authenticated
    GitHub API at the exact fixed endpoints, without redirects.
    """
    if (type(repo) is not dict or type(issue) is not dict or
            not _positive(issue_number) or issue_number > 2147483647):
        deny("W14_GITHUB_METADATA_DENIED")
    owner, author = repo.get("owner"), issue.get("user")
    if (type(owner) is not dict or type(author) is not dict or
            repo.get("full_name") != CONTROL_REPOSITORY or
            repo.get("id") != REPO_ID or type(repo.get("id")) is not int or
            repo.get("url") != REPO_URL or repo.get("private") is not True or
            owner.get("login") != OWNER_LOGIN or
            type(owner.get("id")) is not int or owner.get("id") != OWNER_ID or
            issue.get("url") != REPO_URL + "/issues/" + str(issue_number) or
            issue.get("repository_url") != REPO_URL or
            type(issue.get("number")) is not int or
            issue.get("number") != issue_number or
            not _positive(issue.get("id")) or
            author.get("login") != OWNER_LOGIN or
            type(author.get("id")) is not int or author.get("id") != OWNER_ID or
            issue.get("state") != "open" or "pull_request" in issue):
        deny("W14_GITHUB_METADATA_DENIED")
    node = issue.get("node_id")
    updated = issue.get("updated_at")
    if (type(node) is not str or
            re.fullmatch(r"[A-Za-z0-9_-]{8,128}", node) is None or
            type(updated) is not str or
            re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", updated) is None):
        deny("W14_GITHUB_METADATA_DENIED")
    body = issue.get("body")
    if type(body) is not str:
        deny("W14_GITHUB_BODY_DENIED")
    try:
        raw = body.encode("ascii")
    except UnicodeError:
        deny("W14_GITHUB_BODY_DENIED")
    if not 0 < len(raw) <= w1_bridge.W14_BODY_MAX:
        deny("W14_GITHUB_BODY_DENIED")
    snapshot = {
        "schema": w1_bridge.W14_ISSUE_SCHEMA,
        "repository": CONTROL_REPOSITORY,
        "issue_number": issue_number,
        "issue_state": "open",
        "author_login": OWNER_LOGIN,
        "body": body,
    }
    return {
        "schema": "hostops.personal.w14_github_rest_candidate.v1",
        "snapshot": snapshot,
        "repository_id": REPO_ID,
        "owner_id": OWNER_ID,
        "issue_id": issue["id"],
        "issue_node_id": node,
        "issue_updated_at": updated,
        "body_sha256": digest(raw),
    }


def make_broker_statement(candidate, *, source_sha, observed_at, expires_at):
    """Canonical unsigned broker statement, NOT a signer and NOT authorization."""
    shape(candidate, "schema snapshot repository_id owner_id issue_id issue_node_id issue_updated_at body_sha256")
    if candidate["schema"] != "hostops.personal.w14_github_rest_candidate.v1":
        deny("W14_ORIGIN_CANDIDATE_DENIED")
    sha(source_sha, 40)
    if (type(observed_at) is not int or type(expires_at) is not int or
            not 1 <= expires_at - observed_at <= MAX_BROKER_TTL_SECONDS):
        deny("W14_BROKER_TTL_DENIED")
    # Strict candidate consistency; a claimed body hash is never trusted.
    snapshot = candidate["snapshot"]
    shape(snapshot, "schema repository issue_number issue_state author_login body")
    if (snapshot["schema"] != w1_bridge.W14_ISSUE_SCHEMA or
            snapshot["repository"] != CONTROL_REPOSITORY or
            snapshot["issue_state"] != "open" or
            snapshot["author_login"] != OWNER_LOGIN or
            not _positive(snapshot["issue_number"]) or
            candidate["repository_id"] != REPO_ID or
            candidate["owner_id"] != OWNER_ID or
            not _positive(candidate["issue_id"]) or
            type(candidate["issue_node_id"]) is not str or
            re.fullmatch(r"[A-Za-z0-9_-]{8,128}", candidate["issue_node_id"]) is None or
            type(candidate["issue_updated_at"]) is not str or
            not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
                             candidate["issue_updated_at"])):
        deny("W14_ORIGIN_CANDIDATE_DENIED")
    body = snapshot["body"]
    if type(body) is not str:
        deny("W14_ORIGIN_CANDIDATE_DENIED")
    try:
        body_raw = body.encode("ascii")
    except UnicodeError:
        deny("W14_ORIGIN_CANDIDATE_DENIED")
    if (not 0 < len(body_raw) <= w1_bridge.W14_BODY_MAX or
            digest(body_raw) != candidate["body_sha256"]):
        deny("W14_ORIGIN_BODY_CHANGED")
    return {
        "schema": BROKER_SCHEMA,
        "repository": CONTROL_REPOSITORY,
        "repository_id": REPO_ID,
        "owner_id": OWNER_ID,
        "issue_number": snapshot["issue_number"],
        "issue_id": candidate["issue_id"],
        "issue_node_id": candidate["issue_node_id"],
        "issue_updated_at": candidate["issue_updated_at"],
        "body_sha256": candidate["body_sha256"],
        "source_sha": source_sha,
        "observed_at": observed_at,
        "expires_at": expires_at,
    }


def verify_broker_signed_issue(candidate, envelope, *, expected_source_sha,
                               human_allowed_signers, expected_human_signers_sha256,
                               broker_allowed_signers, expected_broker_signers_sha256,
                               ssh_keygen, expected_verifier_sha256, now=None):
    """Verify independent broker claim and Human signature, with no host effect.

    A valid broker signature DOES NOT prove the broker has independently
    isolated GitHub credentials or directly read GitHub via authenticated TLS.
    Those are real deployment/effect boundaries, not boolean self-claims.
    """
    shape(envelope, "schema statement broker_signature_b64")
    if envelope["schema"] != BROKER_ENVELOPE_SCHEMA:
        deny("W14_BROKER_ENVELOPE_DENIED")
    statement = envelope["statement"]
    shape(statement, ("schema repository repository_id owner_id issue_number issue_id "
                      "issue_node_id issue_updated_at body_sha256 source_sha observed_at expires_at"))
    if statement["schema"] != BROKER_SCHEMA:
        deny("W14_BROKER_ENVELOPE_DENIED")
    if now is None:
        now = int(time.time())
    if type(now) is not int or now < 0 or now > 2**53 - 1:
        deny("W14_BROKER_TTL_DENIED")
    if statement["source_sha"] != expected_source_sha:
        deny("W14_BROKER_SOURCE_MISMATCH")
    actual = make_broker_statement(
        candidate, source_sha=expected_source_sha,
        observed_at=statement["observed_at"], expires_at=statement["expires_at"])
    if statement != actual:
        deny("W14_BROKER_BINDING_DENIED")
    if not statement["observed_at"] <= now < statement["expires_at"]:
        deny("W14_BROKER_TTL_DENIED")
    sha(expected_broker_signers_sha256)
    sha(expected_human_signers_sha256)
    sha(expected_verifier_sha256)
    broker = Path(broker_allowed_signers)
    human = Path(human_allowed_signers)
    exe = Path(ssh_keygen)
    if (not all(p.is_absolute() and p.is_file() and not p.is_symlink()
                for p in (broker, human, exe))):
        deny("W14_BROKER_POLICY_UNTRUSTED")
    try:
        for path in (broker, human, exe):
            for part in (path, *path.parents):
                if getattr(part.lstat(), "st_file_attributes", 0) & 0x400:
                    deny("W14_BROKER_POLICY_UNTRUSTED")
        broker_bytes = broker.read_bytes()
        human_bytes = human.read_bytes()
        if (len(broker_bytes) > 4096 or len(human_bytes) > 8192 or
                digest(broker_bytes) != expected_broker_signers_sha256 or
                digest(human_bytes) != expected_human_signers_sha256 or
                digest(exe.read_bytes()) != expected_verifier_sha256):
            deny("W14_BROKER_POLICY_UNTRUSTED")
        broker_lines = [line.split() for line in broker_bytes.decode("ascii").splitlines()
                        if line.strip() and not line.lstrip().startswith("#")]
        human_lines = [line.split() for line in human_bytes.decode("ascii").splitlines()
                       if line.strip() and not line.lstrip().startswith("#")]
    except (OSError, UnicodeError):
        deny("W14_BROKER_POLICY_UNTRUSTED")
    if (len(broker_lines) != 1 or len(human_lines) != 1 or
            len(broker_lines[0]) not in (3, 4) or
            len(human_lines[0]) not in (3, 4) or
            broker_lines[0][0] != BROKER_PRINCIPAL or
            broker_lines[0][1] != "ssh-ed25519" or
            human_lines[0][0] != "hostops-human" or
            human_lines[0][1] != "ssh-ed25519" or
            broker_lines[0][2] == human_lines[0][2]):
        deny("W14_BROKER_NOT_INDEPENDENT")
    auth.verify_signature(
        statement, envelope["broker_signature_b64"], broker,
        BROKER_PRINCIPAL, BROKER_NAMESPACE, exe)
    human_receipt = w1_bridge.inspect_private_issue_snapshot(
        candidate["snapshot"], issue_number=statement["issue_number"],
        expected_source_sha=expected_source_sha,
        allowed_signers=human,
        expected_signers_sha256=expected_human_signers_sha256,
        ssh_keygen=exe,
        expected_verifier_sha256=expected_verifier_sha256, now=now)
    return {
        "schema": "hostops.personal.w14_broker_bound_ingress.v1",
        "status": "BROKER_AND_HUMAN_SIGNATURES_VERIFIED_NO_EFFECT",
        "request_id": human_receipt["request_id"],
        "issue_number": statement["issue_number"],
        "issue_id": statement["issue_id"],
        "repository_id": REPO_ID,
        "source_sha": expected_source_sha,
        "body_sha256": statement["body_sha256"],
        "human_signature_verified": True,
        "broker_signature_verified": True,
        "broker_credential_custody_proven": False,
        "github_origin_verified": False,
        "anti_rollback_proven": False,
        "dispatch_authorized": False,
        "remote_write_enabled": False,
        "independent_host_receipt": False,
    }
