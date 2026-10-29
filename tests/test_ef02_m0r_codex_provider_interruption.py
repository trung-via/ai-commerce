"""Never invoke Codex or GitHub in these live-probe contract tests."""
import importlib.util
from pathlib import Path

import pytest


def _probe():
    path = (Path(__file__).resolve().parents[1] / ".github" / "scripts" /
            "ef02-m0r-codex-provider-interruption.py")
    spec = importlib.util.spec_from_file_location("m0r_codex_provider_testonly", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _context():
    mod = _probe()
    env = {"GITHUB_REPOSITORY": "trung-via/ai-commerce",
           "GITHUB_REPOSITORY_OWNER": "trung-via",
           "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_ACTOR": "trung-via", "GITHUB_REF": "refs/heads/main", "RUNNER_OS": "Windows",
           "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "98765"}
    event = {"ref": "refs/heads/main", "sender": {"login": "trung-via"},
             "repository": {"full_name": "trung-via/ai-commerce"}}
    return mod, env, event


def test_exact_owner_and_model_is_required():
    mod, env, event = _context()
    assert mod.validate_context(env, event) == ("trung-via/ai-commerce", "a"*40, "98765")
    assert (mod.EXECUTOR, mod.MODEL, mod.EFFORT) == ("codex", "gpt-6.1-sol", "max")


@pytest.mark.parametrize("field,value", [
    ("GITHUB_EVENT_NAME", "issues"),
    ("RUNNER_OS", "Linux"),
    ("GITHUB_SHA", "notsha"),
    ("GITHUB_RUN_ID", "-1"),
    ("GITHUB_REPOSITORY_OWNER", "attacker")])
def test_reject_untrusted_event_context(field, value):
    mod, env, event = _context()
    env[field] = value
    with pytest.raises(ValueError, match="M0R_PROVIDER_PROBE_GUARD_REJECTED"):
        mod.validate_context(env, event)


def test_reject_wrong_issue_title_or_author():
    mod, env, event = _context()
    event["ref"] = "[EF02] other"
    with pytest.raises(ValueError, match="M0R_PROVIDER_PROBE_GUARD_REJECTED"):
        mod.validate_context(env, event)
    event["ref"] = "refs/heads/main"
    event["sender"]["login"] = "attacker"
    with pytest.raises(ValueError, match="M0R_PROVIDER_PROBE_GUARD_REJECTED"):
        mod.validate_context(env, event)


@pytest.mark.parametrize("value,expected", [
    ({"type": "turn.started"}, False),
    ({"type": "thread.started"}, False),
    ({"type": "item.completed", "item": {"type": "command_execution", "command": "pwd"}}, False),
    ({"type": "item.started", "item": {"type": "agent_message", "text": "Hi"}}, False),
    ({"type": "item.started", "item": {"type": "command_execution", "command": ""}}, False),
    ({"type": "item.started", "item": {"type": "command_execution", "command": "python -V"}}, True),
    (None, False),
])
def test_only_model_emitted_command_start_is_provider_evidence(value, expected):
    assert _probe().provider_tool_event(value) is expected


def test_ref_integrity_rejects_wrong_parent_without_writes():
    mod = _probe()
    class Git:
        def __init__(self):
            self.refs = dict(zip(("claims", "pre-spawn", "spawned"), ("b"*40,"c"*40,"d"*40)))
        def attempt_key(self, attempt):
            return "one-key"
        def api(self, method, path):
            assert method == "GET"
            if "/candidate/" in path:
                from ai_commerce.remote.transport import RemoteTransportError
                raise RemoteTransportError("GITHUB_HTTP_404")
            for name, sha in self.refs.items():
                if path.endswith("/" + name + "/one-key"):
                    return {"object": {"sha": sha}}
            sha = path.rsplit("/",1)[-1]
            parent = {"b"*40: "a"*40, "c"*40: "b"*40,
                      "d"*40: "a"*40}[sha]
            message = {
                "b"*40: "EF02 CLAIM\n\nfingerprint=" + "f"*64 + "\nattempt_id=attempt",
                "c"*40: "EF02 PRESPAWN\n\nfingerprint=" + "f"*64 + "\nattempt_id=attempt",
                "d"*40: "EF02 SPAWNED\n\nfingerprint=" + "f"*64 + "\nattempt_id=attempt\nchild_pid=123"
            }[sha]
            return {"message": message, "parents": [{"sha":parent}],
                    "tree": {"sha": "e"*40}}
    with pytest.raises(RuntimeError, match="M0R_PROVIDER_REF_CHAIN_UNVERIFIED"):
        mod._read_ref_chain(Git(), "a"*40, "attempt", "f"*64, 123)
