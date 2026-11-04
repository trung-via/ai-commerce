"""W1.4 signed private Issue source-only ingress tests; no host effects."""
from __future__ import annotations

import base64
import pytest
from pathlib import Path

from ai_commerce.hostops_v3 import auth, transport, contract as c
from test_hostops_v3 import h


def _personal_w1_signed_fixture(h, *, now=1800000000):
    content = "W1 signed source contract, no file effects.\\n"
    unsigned = {
        "schema": auth.PERSONAL_W1_SCHEMA,
        "request_id": "w1-" + "a" * 32,
        "issue_number": 123,
        "source_repository": c.SOURCE_REPOSITORY,
        "control_repository": c.CONTROL_REPOSITORY,
        "source_sha": "b" * 40,
        "operation": "create_new_utf8",
        "parameters": {
            "root_id": "human-scratch-v1",
            "filename": "signed-proof.txt",
            "content_utf8": content,
            "content_sha256": c.digest(content.encode("utf-8")),
            "content_bytes": len(content.encode("utf-8")),
        },
        "issued_at": now,
        "expires_at": now + 300,
    }
    signed = {**unsigned, "approval_signature_b64": h.sign(
        unsigned, "human", auth.PERSONAL_W1_NAMESPACE)}
    signers = h.policy / "human-signers"
    return signed, {
        "issue_number": 123,
        "expected_source_sha": "b" * 40,
        "allowed_signers": signers,
        "expected_signers_sha256": c.digest(signers.read_bytes()),
        "ssh_keygen": Path(h.config["ssh_keygen"]),
        "expected_verifier_sha256": h.config["ssh_keygen_sha256"],
        "now": now + 30,
    }


# W1.4 private Issue ingress remains an OFFLINE source gate: no token or worker.
def _w14_private_snapshot(proposal, kw):
    from ai_commerce.hostops_v3 import w1_bridge
    return {
        "schema": w1_bridge.W14_ISSUE_SCHEMA,
        "repository": c.CONTROL_REPOSITORY,
        "issue_number": kw["issue_number"],
        "issue_state": "open",
        "author_login": "trung-via",
        "body": w1_bridge.encode_signed_issue_body(
            proposal, issue_number=kw["issue_number"],
            expected_source_sha=kw["expected_source_sha"], now=kw["now"]),
    }


def test_w14_signed_private_issue_inspection_checks_real_ssh_sig_but_cannot_dispatch(h):
    from ai_commerce.hostops_v3 import w1_bridge
    proposal, verified = _personal_w1_signed_fixture(h)
    snap = _w14_private_snapshot(proposal, verified)
    existing = {p.name for p in h.data.iterdir()}
    r = w1_bridge.inspect_private_issue_snapshot(snap, **verified)
    assert r["status"] == "SIGNED_ISSUE_CONTENT_VERIFIED_NO_EFFECT"
    assert r["request_id"] == proposal["request_id"]
    assert r["issue_number"] == proposal["issue_number"]
    assert r["source_sha"] == proposal["source_sha"]
    assert r["human_signature_verified"] is True
    assert r["proposal_sha256"] == auth.personal_w1_verify_offline(
        proposal, **verified)["proposal_sha256"]
    assert r["snapshot_body_sha256"] == c.digest(snap["body"].encode("ascii"))
    for gate in ("github_origin_verified", "credential_custody_proven",
                 "replay_prevented", "dispatch_authorized",
                 "remote_write_enabled", "independent_host_receipt"):
        assert r[gate] is False
    assert set(r) == {
        "schema", "status", "request_id", "issue_number", "source_sha",
        "proposal_sha256", "snapshot_body_sha256",
        "human_signature_verified", "github_origin_verified",
        "credential_custody_proven", "replay_prevented",
        "dispatch_authorized", "remote_write_enabled",
        "independent_host_receipt"
    }
    assert {p.name for p in h.data.iterdir()} == existing
    assert not hasattr(w1_bridge, "dispatch")
    assert not hasattr(transport, "w14_remote_write")
    assert not (h.data / proposal["parameters"]["filename"]).exists()


@pytest.mark.parametrize("field,value", [
    ("repository", c.SOURCE_REPOSITORY),
    ("issue_number", 124),
    ("issue_number", True),
    ("issue_state", "closed"),
    ("author_login", "attacker"),
    ("schema", "hostops.v4.issue"),
    ("body", "script=write-something"),
    ("body", ""),
    ("body", "unexpected unicode marker"),
])
def test_w14_issue_claims_fail_closed_before_disk_or_credentials(h, field, value):
    from ai_commerce.hostops_v3 import w1_bridge
    proposal, verified = _personal_w1_signed_fixture(h)
    before = _w14_private_snapshot(proposal, verified)
    with pytest.raises(c.Denied):
        w1_bridge.inspect_private_issue_snapshot(
            {**before, field: value}, **verified)
    assert not (h.data / proposal["parameters"]["filename"]).exists()


def test_w14_signed_body_tamper_noncanonical_expiry_and_extra_metadata_denied(h):
    from ai_commerce.hostops_v3 import w1_bridge
    proposal, verified = _personal_w1_signed_fixture(h)
    snap = _w14_private_snapshot(proposal, verified)
    raw = snap["body"]
    bad = [
        raw + " ",
        raw.replace('"issue_number":123', '"issue_number":124'),
        raw.replace('"filename":"signed-proof.txt"',
                    '"filename":"fake.txt"'),
        raw.replace(proposal["approval_signature_b64"][:16], "AAAAAAAAAAAAAAAA"),
        "~~~json\n" + raw + "~~~",
        raw.replace(',"schema":', ',"EXTRA":true,"schema":'),
    ]
    for body in bad:
        with pytest.raises(c.Denied):
            w1_bridge.inspect_private_issue_snapshot(
                {**snap, "body": body}, **verified)
    with pytest.raises(c.Denied):
        w1_bridge.inspect_private_issue_snapshot(
            {**snap, "extra": "trusted_origin=true"}, **verified)
    with pytest.raises(c.Denied, match="W1_SOURCE_MISMATCH"):
        w1_bridge.inspect_private_issue_snapshot(
            snap, **{**verified, "expected_source_sha": "f" * 40})
    with pytest.raises(c.Denied, match="W1_REQUEST_EXPIRED"):
        w1_bridge.inspect_private_issue_snapshot(
            snap, **{**verified, "now": proposal["expires_at"] + 10})
    assert not (h.data / proposal["parameters"]["filename"]).exists()
