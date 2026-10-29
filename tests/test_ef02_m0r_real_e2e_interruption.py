"""Pure scope/identity tests; NO provider or GitHub API calls."""
import importlib.util
from pathlib import Path

import pytest
from ai_commerce.remote.transport import RemoteTransportError


def _probe():
    path = Path(__file__).resolve().parents[1] / ".github/scripts/ef02-m0r-real-e2e-interruption.py"
    spec = importlib.util.spec_from_file_location("m0r_real_e2e", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _context():
    mod = _probe()
    env = {
        "GITHUB_REPOSITORY": "trung-via/ai-commerce",
        "GITHUB_REPOSITORY_OWNER": "trung-via",
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_ACTOR": "trung-via", "GITHUB_REF": "refs/heads/main", "RUNNER_OS": "Windows",
        "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "12345",
    }
    event = {"ref": "refs/heads/main", "sender": {"login": "trung-via"},
             "repository": {"full_name": "trung-via/ai-commerce"}}
    return mod, env, event


def test_explicit_owner_windows_and_selected_model():
    m, env, event = _context()
    assert m.validate_context(env, event) == ("trung-via/ai-commerce", "a"*40, "12345")
    action, path, marker = m.action_for("12345")
    assert (action["executor"], action["model"], action["effort"]) == (
        "codex", "gpt-6.1-sol", "max")
    assert action["attempt_id"] == "m0r-e2e-codex61-max-12345"
    assert action["verification_profile"] == "ef02-tests"
    assert path == "tests/m0r_codex_e2e_12345.txt"
    assert marker in action["instruction"]
    assert "time.sleep(150)" in action["instruction"]
    assert len(action["allowed_paths"]) == 1
    assert action["depends_on"] == []


@pytest.mark.parametrize("field,bad", [
    ("GITHUB_EVENT_NAME", "issues"),
    ("RUNNER_OS", "Linux"),
    ("GITHUB_SHA", "bad"),
    ("GITHUB_RUN_ID", "not-number"),
    ("GITHUB_REPOSITORY_OWNER", "other"),
])
def test_wrong_context_rejected(field, bad):
    mod, env, event = _context()
    env[field] = bad
    with pytest.raises(ValueError, match="M0R_E2E_OWNER_WINDOWS_GUARD_REJECTED"):
        mod.validate_context(env, event)


def test_non_owner_issue_is_rejected():
    mod, env, event = _context()
    event["sender"]["login"] = "not-owner"
    with pytest.raises(ValueError, match="M0R_E2E_OWNER_WINDOWS_GUARD_REJECTED"):
        mod.validate_context(env, event)


def test_wrong_issue_title_is_rejected():
    mod, env, event = _context()
    event["ref"] = "[EF02] untrusted batch"
    with pytest.raises(ValueError, match="M0R_E2E_OWNER_WINDOWS_GUARD_REJECTED"):
        mod.validate_context(env, event)


class GitFixture:
    def __init__(self, bad=False):
        self.refs = dict(zip(("claims","pre-spawn","spawned"),("b"*40,"c"*40,"d"*40)))
        self.bad = bad

    def attempt_key(self, attempt):
        assert attempt == "attempt"
        return "key"

    def api(self, method, path):
        assert method == "GET"
        if path.endswith("/candidate/key"):
            raise RemoteTransportError("GITHUB_HTTP_404")
        for stage, sha in self.refs.items():
            if path.endswith("/" + stage + "/key"):
                return {"object": {"sha": sha}}
        sha = path.rsplit("/", 1)[-1]
        messages = {
            "b"*40: "EF02 CLAIM\n\nfingerprint=" + "f"*64 + "\nattempt_id=attempt",
            "c"*40: "EF02 PRESPAWN\n\nfingerprint=" + "f"*64 + "\nattempt_id=attempt",
            "d"*40: "EF02 SPAWNED\n\nfingerprint=" + "f"*64 + "\nattempt_id=attempt\nchild_pid=987",
        }
        parents = {"b"*40: "a"*40, "c"*40: "b"*40, "d"*40: "c"*40}
        if self.bad and sha == "d"*40:
            parents[sha] = "a"*40
        return {"message": messages[sha],
                "parents": [{"sha": parents[sha]}],
                "tree": {"sha":"e"*40}}


def test_remote_chain_verifies_not_provider_outcome():
    m = _probe()
    r = m.inspect_remote(GitFixture(), "a"*40, "attempt", "f"*64, 987)
    assert r["candidate_ref_present"] is False
    assert r["spawned_sha"] == "d"*40


def test_bad_remote_chain_rejected():
    m = _probe()
    with pytest.raises(RuntimeError,match="M0R_E2E_REMOTE_CHECKPOINT_LINEAGE_INVALID"):
        m.inspect_remote(GitFixture(bad=True), "a"*40, "attempt", "f"*64, 987)


def test_marker_requires_exact_bytes_in_unique_action_worktree(tmp_path, monkeypatch):
    m = _probe()
    monkeypatch.setattr(m.tempfile, "gettempdir", lambda: str(tmp_path))
    prefix = "ai-commerce-ef02-m0r-e2e-12345-"
    repo = tmp_path / (prefix + "unique")
    target = repo / "tests" / "m0r_codex_e2e_12345.txt"
    target.parent.mkdir(parents=True)
    target.write_text("wrong", encoding="utf-8")
    assert m.marker_worktree("m0r-e2e-12345",
                             "tests/m0r_codex_e2e_12345.txt", "expected") is None
    target.write_text("expected", encoding="utf-8")
    assert m.marker_worktree("m0r-e2e-12345",
                             "tests/m0r_codex_e2e_12345.txt", "expected") == repo



def test_live_codex_stream_only_recognizes_exact_model_command(tmp_path):
    m = _probe()
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join([
        '{"type":"turn.started"}',
        '{"type":"item.started","item":{"id":"item_bad","type":"command_execution","command":"python -V"}}',
        '{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"python -c \\"print(M0R_E2E_REAL_CODEX_COMMAND_12345)\\""}}',
    ])+"\n", encoding="utf-8")
    assert m.streamed_codex_command_event(
        events, "M0R_E2E_REAL_CODEX_COMMAND_UNRELATED") is None
    event = m.streamed_codex_command_event(
        events, "M0R_E2E_REAL_CODEX_COMMAND_12345")
    assert event["item_id"] == "item_1"
    assert event["event"] == "MODEL_EMITTED_COMMAND_STARTED_NOT_SHELL_SUCCESS"
