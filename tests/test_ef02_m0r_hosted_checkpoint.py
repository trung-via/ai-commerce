"""No-provider regression for the GitHub-hosted M0-R pre-spawn proof."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / ".github" / "scripts" / "ef02-m0r-hosted-checkpoint-probe.py"
TITLE = "[M0R] Hosted remote pre-spawn checkpoint probe"
SOURCE = "a" * 40


def module():
    spec = importlib.util.spec_from_file_location("m0r_hosted_probe", PROBE)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def inputs():
    env = {"GITHUB_REPOSITORY": "trung-via/ai-commerce",
           "GITHUB_REPOSITORY_OWNER": "trung-via",
           "GITHUB_SHA": SOURCE, "GITHUB_RUN_ID": "123456",
           "GITHUB_EVENT_NAME": "issues", "GITHUB_TOKEN": "fixture-no-network"}
    event = {"action": "opened", "issue": {"title": TITLE,
             "user": {"login": "trung-via"}},
             "repository": {"full_name": "trung-via/ai-commerce"}}
    return env, event


def test_hosted_probe_uses_real_transport_contract_without_running_ef01():
    calls = []
    class Transport:
        def __init__(self, repo, token):
            assert repo == "trung-via/ai-commerce"
            assert token == "fixture-no-network"
        def attempt_key(self, attempt):
            assert attempt == "m0r-hosted-checkpoint-123456"
            return "f" * 32
        def claim(self, **kwargs):
            calls.append(("claim", kwargs))
            return "CLAIMED"
        def pre_spawn_checkpoint(self, **kwargs):
            calls.append(("pre_spawn", kwargs))
            return "refs/heads/ef02/pre-spawn/" + "f" * 32
    env, event = inputs()
    result = module().run_probe(env, event, Transport)
    assert [name for name, _ in calls] == ["claim", "pre_spawn"]
    assert calls[0][1] == calls[1][1]
    assert calls[0][1]["source_sha"] == SOURCE
    assert len(calls[0][1]["fingerprint"]) == 64
    assert result["status"] == "CHECKPOINT_CREATED_NOT_PROVIDER_PROOF"
    assert result["candidate_created"] is False
    assert result["model_invoked"] is False
    assert result["ef01_invoked"] is False
    assert result["windows_runner_invoked"] is False
    assert result["replay_authorized"] is False


@pytest.mark.parametrize("field,value", [
    ("GITHUB_EVENT_NAME", "workflow_dispatch"),
    ("GITHUB_SHA", "bad"),
    ("GITHUB_REPOSITORY_OWNER", "someone-else"),
    ("GITHUB_RUN_ID", "not-a-number"),
])
def test_invalid_event_environment_never_creates_ref(field, value):
    env, event = inputs()
    env[field] = value
    with pytest.raises(ValueError, match="M0R_HOSTED_PROBE_GUARD_REJECTED"):
        module().run_probe(env, event, lambda *_a: pytest.fail("no Git transport"))


def test_issue_not_created_by_owner_never_creates_ref():
    env, event = inputs()
    event["issue"]["user"]["login"] = "unknown"
    with pytest.raises(ValueError, match="M0R_HOSTED_PROBE_GUARD_REJECTED"):
        module().run_probe(env, event, lambda *_a: pytest.fail("no Git transport"))


def test_duplicate_claim_never_creates_stage_or_retries():
    class Duplicate:
        def __init__(self, *_a):
            pass
        def claim(self, **_kw):
            return "DUPLICATE_CLAIM_NO_REEXECUTION"
        def pre_spawn_checkpoint(self, **_kw):
            pytest.fail("stage must not be retried")
    env, event = inputs()
    with pytest.raises(RuntimeError, match="M0R_CLAIM_NOT_NEW_NO_REPLAY"):
        module().run_probe(env, event, Duplicate)


def test_hosted_probe_is_not_ef02_batch_or_windows_executor():
    text = (ROOT / ".github" / "workflows" /
            "ef02-m0r-hosted-checkpoint.yml").read_text(encoding="utf-8")
    main = (ROOT / ".github" / "workflows" /
            "ef02-batch.yml").read_text(encoding="utf-8")
    assert "github.event.issue.user.login == github.repository_owner" in text
    assert "github.event.action == 'opened'" in text
    assert "github.event.issue.title == '[M0R] Hosted remote pre-spawn checkpoint probe'" in text
    assert text.count("runs-on: ubuntu-latest") == 2
    assert "runs-on: [self-hosted" not in text
    assert "contents: write" in text and "contents: read" in text
    assert "python .github/scripts/ef02-m0r-hosted-checkpoint-probe.py" in text
    assert "reconcile.inspectAction(" in text
    assert "python -m ai_commerce.remote run" not in text
    assert "codex " not in text.lower()
    assert "antigravity " not in text.lower()
    assert "startsWith(github.event.issue.title, '[EF02]')" in main
