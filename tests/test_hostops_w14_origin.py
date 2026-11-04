"""W1.4 Phase 2 broker provenance binding; OpenSSH test keys only."""
import copy
from pathlib import Path

import pytest

from ai_commerce.hostops_v3 import auth, contract as c, w1_bridge, w14_origin as origin
from test_hostops_v3 import h, keys
from test_hostops_w14_ingress import _personal_w1_signed_fixture, _w14_private_snapshot


def _case(h):
    proposal, human_kwargs = _personal_w1_signed_fixture(h)
    snap = _w14_private_snapshot(proposal, human_kwargs)
    repo = {
        "id": origin.REPO_ID,
        "full_name": c.CONTROL_REPOSITORY,
        "url": origin.REPO_URL,
        "private": True,
        "owner": {"login": origin.OWNER_LOGIN, "id": origin.OWNER_ID},
    }
    issue = {
        "id": 6197463901,
        "node_id": "I_kwDOVgbrkM8AAAABcWXLXQ",
        "number": 123,
        "url": origin.REPO_URL + "/issues/123",
        "repository_url": origin.REPO_URL,
        "user": {"login": origin.OWNER_LOGIN, "id": origin.OWNER_ID},
        "state": "open",
        "updated_at": "2026-11-04T07:21:57Z",
        "body": snap["body"],
    }
    candidate = origin.normalize_private_issue_rest(repo, issue, issue_number=123)
    assert candidate["snapshot"] == snap
    broker_policy = h.policy / "w14-broker-signers"
    broker_policy.write_text(
        origin.BROKER_PRINCIPAL + " " + (h.keys / "issuer.pub").read_text(),
        encoding="utf-8",
    )
    statement = origin.make_broker_statement(
        candidate, source_sha=human_kwargs["expected_source_sha"],
        observed_at=proposal["issued_at"], expires_at=proposal["issued_at"] + 120,
    )
    signed = {
        "schema": origin.BROKER_ENVELOPE_SCHEMA,
        "statement": statement,
        "broker_signature_b64": h.sign(statement, "issuer", origin.BROKER_NAMESPACE),
    }
    kwargs = {
        "expected_source_sha": human_kwargs["expected_source_sha"],
        "human_allowed_signers": human_kwargs["allowed_signers"],
        "expected_human_signers_sha256": human_kwargs["expected_signers_sha256"],
        "broker_allowed_signers": broker_policy,
        "expected_broker_signers_sha256": c.digest(broker_policy.read_bytes()),
        "ssh_keygen": human_kwargs["ssh_keygen"],
        "expected_verifier_sha256": human_kwargs["expected_verifier_sha256"],
        "now": human_kwargs["now"],
    }
    return proposal, repo, issue, candidate, signed, kwargs


def test_w14_origin_real_dual_signature_binds_repository_issue_source_and_body(h):
    p, repo, issue, candidate, signed, args = _case(h)
    original_files = set(x.name for x in h.data.iterdir())
    result = origin.verify_broker_signed_issue(candidate, signed, **args)
    assert result == {
        "schema": "hostops.personal.w14_broker_bound_ingress.v1",
        "status": "BROKER_AND_HUMAN_SIGNATURES_VERIFIED_NO_EFFECT",
        "request_id": p["request_id"],
        "issue_number": p["issue_number"],
        "issue_id": issue["id"],
        "repository_id": origin.REPO_ID,
        "source_sha": p["source_sha"],
        "body_sha256": c.digest(issue["body"].encode("ascii")),
        "human_signature_verified": True,
        "broker_signature_verified": True,
        "broker_credential_custody_proven": False,
        "github_origin_verified": False,
        "anti_rollback_proven": False,
        "dispatch_authorized": False,
        "remote_write_enabled": False,
        "independent_host_receipt": False,
    }
    assert not hasattr(origin, "dispatch")
    assert not hasattr(origin, "create")
    assert set(x.name for x in h.data.iterdir()) == original_files
    assert not (h.data / p["parameters"]["filename"]).exists()


@pytest.mark.parametrize("where,key,value", [
    ("repo", "private", False),
    ("repo", "id", True),
    ("repo", "id", 1),
    ("repo", "url", "https://malicious.invalid/api"),
    ("repo", "full_name", "trung-via/ai-commerce"),
    ("repo_owner", "id", 13),
    ("issue", "number", True),
    ("issue", "number", 124),
    ("issue", "id", 0),
    ("issue", "url", "https://api.github.com/repos/trung-via/other/issues/123"),
    ("issue", "repository_url", "https://api.github.com/repos/trung-via/other"),
    ("issue", "state", "closed"),
    ("issue", "node_id", "bad"),
    ("issue", "updated_at", "future"),
    ("author", "login", "attacker"),
    ("author", "id", 1),
    ("issue", "body", "not signed yet 😀"),
    ("issue", "body", ""),
])
def test_w14_origin_rejects_spoofed_rest_claims(h, where, key, value):
    _, repo, issue, _, _, _ = _case(h)
    repo, issue = copy.deepcopy(repo), copy.deepcopy(issue)
    parent = {"repo": repo, "repo_owner": repo["owner"],
              "issue": issue, "author": issue["user"]}[where]
    parent[key] = value
    with pytest.raises(c.Denied):
        origin.normalize_private_issue_rest(repo, issue, issue_number=123)


def test_w14_origin_rejects_pull_request_as_issue_and_claimed_api_override(h):
    _, repo, issue, candidate, _, _ = _case(h)
    with pytest.raises(c.Denied):
        origin.normalize_private_issue_rest(
            repo, {**issue, "pull_request": {}}, issue_number=123)
    with pytest.raises(c.Denied):
        origin.make_broker_statement(
            {**candidate, "github_origin_verified": True},
            source_sha="b" * 40, observed_at=1800000000, expires_at=1800000050)


def test_w14_origin_broker_signature_rejects_tampered_body_issue_source_and_time(h):
    _, _, _, candidate, signed, kw = _case(h)
    with pytest.raises(c.Denied):
        origin.verify_broker_signed_issue(
            {**candidate, "body_sha256": "f" * 64}, signed, **kw)
    for k, bad in (("issue_id", 999), ("issue_number", 999),
                   ("source_sha", "f" * 40), ("body_sha256", "f" * 64),
                   ("repository_id", 999), ("issue_updated_at", "2025-01-01T00:00:00Z")):
        statement = {**signed["statement"], k: bad}
        with pytest.raises(c.Denied):
            origin.verify_broker_signed_issue(
                candidate, {**signed, "statement": statement}, **kw)
    with pytest.raises(c.Denied, match="W14_BROKER_TTL_DENIED"):
        origin.verify_broker_signed_issue(
            candidate, signed, **{**kw, "now": signed["statement"]["expires_at"]})
    with pytest.raises(c.Denied, match="W14_BROKER_TTL_DENIED"):
        origin.make_broker_statement(
            candidate, source_sha="b" * 40, observed_at=1800000000, expires_at=1800000121)


def test_w14_origin_rejects_wrong_signature_role_policy_and_namespace(h):
    _, _, _, candidate, signed, kw = _case(h)
    s = signed["statement"]
    for sig in (
        h.sign(s, "human", origin.BROKER_NAMESPACE),
        h.sign(s, "issuer", auth.PERSONAL_W1_NAMESPACE),
        "not-a-valid-sshsig",
    ):
        with pytest.raises(c.Denied):
            origin.verify_broker_signed_issue(
                candidate, {**signed, "broker_signature_b64": sig}, **kw)
    with pytest.raises(c.Denied, match="W14_BROKER_POLICY_UNTRUSTED"):
        origin.verify_broker_signed_issue(
            candidate, signed, **{**kw, "expected_broker_signers_sha256": "f" * 64})
    same_key = h.policy / "same-as-human"
    same_key.write_text(
        origin.BROKER_PRINCIPAL + " " + (h.keys / "human.pub").read_text(),
        encoding="utf-8",
    )
    with pytest.raises(c.Denied, match="W14_BROKER_NOT_INDEPENDENT"):
        origin.verify_broker_signed_issue(
            candidate, signed, **{
                **kw,
                "broker_allowed_signers": same_key,
                "expected_broker_signers_sha256": c.digest(same_key.read_bytes()),
            })


def test_w14_origin_rejects_valid_broker_but_invalid_human_signature(h):
    proposal, repo, issue, _, _, kw = _case(h)
    modified = {
        **proposal,
        "parameters": {**proposal["parameters"], "filename": "another.txt"}
    }
    forged = w1_bridge.W14_BODY_MARKER + c.canonical(modified).decode("ascii")
    candidate = origin.normalize_private_issue_rest(
        repo, {**issue, "body": forged}, issue_number=123)
    statement = origin.make_broker_statement(
        candidate, source_sha=kw["expected_source_sha"],
        observed_at=proposal["issued_at"], expires_at=proposal["issued_at"] + 90)
    signed = {"schema": origin.BROKER_ENVELOPE_SCHEMA, "statement": statement,
              "broker_signature_b64": h.sign(statement, "issuer", origin.BROKER_NAMESPACE)}
    with pytest.raises(c.Denied, match="SIGNATURE_INVALID"):
        origin.verify_broker_signed_issue(candidate, signed, **kw)
    assert not (h.data / "another.txt").exists()
