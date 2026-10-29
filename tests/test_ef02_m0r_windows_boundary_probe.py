"""No-model M0-R Windows GitHub issue gate regressions."""
import importlib.util
from pathlib import Path
import pytest

def _probe():
    path = Path(__file__).resolve().parents[1] / ".github/scripts/ef02-m0r-windows-boundary-probe.py"
    spec = importlib.util.spec_from_file_location("m0r_probe_gate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _fixture():
    mod = _probe()
    env = {"GITHUB_REPOSITORY":"trung-via/ai-commerce", "GITHUB_REPOSITORY_OWNER":"trung-via",
           "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_ACTOR": "trung-via", "GITHUB_REF": "refs/heads/main", "RUNNER_OS":"Windows",
           "GITHUB_SHA":"a"*40, "GITHUB_RUN_ID":"12345"}
    event = {"ref": "refs/heads/main", "sender": {"login": "trung-via"},
             "repository": {"full_name": "trung-via/ai-commerce"}}
    return mod, env, event

def test_owner_windows_immutable_sha_accepted():
    mod, env, event = _fixture()
    assert mod.validate_context(env, event) == ("trung-via/ai-commerce", "a"*40, "12345")

@pytest.mark.parametrize("key,bad", [("GITHUB_EVENT_NAME","issues"),
    ("RUNNER_OS","Linux"), ("GITHUB_SHA","BAD"), ("GITHUB_RUN_ID","x"),
    ("GITHUB_REPOSITORY_OWNER","other")])
def test_unauthorized_context_rejected(key,bad):
    mod, env, event = _fixture()
    env[key] = bad
    with pytest.raises(ValueError,match="M0R_WINDOWS_PROBE_GUARD_REJECTED"):
        mod.validate_context(env,event)

def test_wrong_issue_or_actor_rejected():
    mod, env, event = _fixture()
    event["sender"]["login"]="not-owner"
    with pytest.raises(ValueError,match="M0R_WINDOWS_PROBE_GUARD_REJECTED"):
        mod.validate_context(env,event)
    event["sender"]["login"]="trung-via"
    event["ref"]="[EF02] other"
    with pytest.raises(ValueError,match="M0R_WINDOWS_PROBE_GUARD_REJECTED"):
        mod.validate_context(env,event)
