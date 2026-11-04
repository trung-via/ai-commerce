"""W1.4 private GitHub Issue ingress: signed packet inspection, NO dispatch.

This module is a *pure offline boundary* for a later independently
authenticated GitHub API reader. A supplied Issue snapshot is untrusted data,
even if its fields claim to originate from GitHub. Neither this module nor
a valid SSHSIG grants a remote file effect or stores runner credentials.
"""
from __future__ import annotations

from . import auth
from .contract import (CONTROL_REPOSITORY, Denied, canonical, decode, deny,
                       digest, shape, sha)

W14_ISSUE_SCHEMA = "hostops.personal.w14_issue_snapshot.v1"
W14_BODY_MARKER = "HOSTOPS_W1_SIGNED_ISSUE_V1\n"
W14_BODY_MAX = 16384
W14_EXPECTED_AUTHOR = "trung-via"


def encode_signed_issue_body(proposal, *, issue_number, expected_source_sha, now=None):
    """Encode already Human-signed proposal for a PRIVATE Issue.

    Structural validation cannot replace the cryptographic check at ingress.
    The payload contains a public signature, NEVER a signing key or token.
    """
    auth.personal_w1_signing_payload(
        proposal, issue_number=issue_number,
        expected_source_sha=expected_source_sha, now=now)
    return W14_BODY_MARKER + canonical(proposal).decode("ascii")


def inspect_private_issue_snapshot(snapshot, *, issue_number,
                                   expected_source_sha, allowed_signers,
                                   expected_signers_sha256, ssh_keygen,
                                   expected_verifier_sha256, now=None):
    """Check a claimed private Issue body and the independent Human SSHSIG.

    The *caller* must authenticate TLS, GitHub API endpoint, installation,
    owner, repository and immutable Issue identity using credentials held
    outside the public self-hosted runner SID. This offline function cannot
    attest those transport facts; it explicitly returns origin_verified=False.

    IMPORTANT: no execution, GitHub network call, ledger reservation, or
    remote dispatch. The signed packet is not an executable Issue command.
    """
    shape(snapshot, "schema repository issue_number issue_state author_login body")
    if (snapshot["schema"] != W14_ISSUE_SCHEMA or
            snapshot["repository"] != CONTROL_REPOSITORY or
            snapshot["issue_state"] != "open" or
            snapshot["author_login"] != W14_EXPECTED_AUTHOR or
            type(snapshot["issue_number"]) is not int or
            snapshot["issue_number"] != issue_number or
            type(issue_number) is not int or
            not 1 <= issue_number <= 2147483647):
        deny("W14_ISSUE_ORIGIN_CLAIM_DENIED")
    sha(expected_source_sha, 40)
    body = snapshot["body"]
    if type(body) is not str:
        deny("W14_ISSUE_BODY_DENIED")
    try:
        raw = body.encode("ascii")
    except UnicodeError:
        deny("W14_ISSUE_BODY_DENIED")
    marker = W14_BODY_MARKER.encode("ascii")
    if (len(raw) <= len(marker) or len(raw) > W14_BODY_MAX or
            not raw.startswith(marker)):
        deny("W14_ISSUE_BODY_DENIED")
    proposal_bytes = raw[len(marker):]
    proposal = decode(proposal_bytes, limit=W14_BODY_MAX)
    # Forbid whitespace/reordering ambiguity and appended text. The canonical
    # signed bytes are the *only* allowed Issue body payload.
    if proposal_bytes != canonical(proposal):
        deny("W14_ISSUE_BODY_NONCANONICAL")
    receipt = auth.personal_w1_verify_offline(
        proposal, issue_number=issue_number,
        expected_source_sha=expected_source_sha,
        allowed_signers=allowed_signers,
        expected_signers_sha256=expected_signers_sha256,
        ssh_keygen=ssh_keygen,
        expected_verifier_sha256=expected_verifier_sha256,
        now=now)
    return {
        "schema": "hostops.personal.w14_ingress_inspection.v1",
        "status": "SIGNED_ISSUE_CONTENT_VERIFIED_NO_EFFECT",
        "request_id": receipt["request_id"],
        "issue_number": issue_number,
        "source_sha": expected_source_sha,
        "proposal_sha256": receipt["proposal_sha256"],
        "snapshot_body_sha256": digest(raw),
        "human_signature_verified": True,
        "github_origin_verified": False,
        "credential_custody_proven": False,
        "replay_prevented": False,
        "dispatch_authorized": False,
        "remote_write_enabled": False,
        "independent_host_receipt": False,
    }
