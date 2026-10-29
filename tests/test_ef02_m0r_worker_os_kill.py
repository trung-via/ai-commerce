"""Scope and remote ancestry checks for actual OS-kill M0-R no-model probe."""
import importlib.util
from pathlib import Path

import pytest
from ai_commerce.remote.transport import RemoteTransportError


def _module():
    path = (Path(__file__).resolve().parents[1] / ".github" / "scripts" /
            "ef02-m0r-worker-os-kill.py")
    spec = importlib.util.spec_from_file_location("m0r_worker_os_kill", path)
    m = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(m)
    return m


def _context():
    m = _module()
    env = {"GITHUB_REPOSITORY":"trung-via/ai-commerce",
           "GITHUB_REPOSITORY_OWNER":"trung-via", "GITHUB_SHA":"a"*40,
           "GITHUB_RUN_ID":"98765", "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_ACTOR": "trung-via", "GITHUB_REF": "refs/heads/main",
           "RUNNER_OS":"Windows"}
    event = {"ref": "refs/heads/main", "sender": {"login": "trung-via"},
             "repository": {"full_name": "trung-via/ai-commerce"}}
    return m, env, event


def test_owned_windows_issue_guard_accepts_only_exact_event():
    m, env, event = _context()
    assert m.validate_context(env, event) == (
        "trung-via/ai-commerce", "a"*40, "98765")
    a = m.action_for("98765")
    assert a["attempt_id"] == "m0r-os-worker-kill-98765"
    assert a["model"] == "NO_MODEL_INERT_FIXTURE"
    assert a["verification_profile"] == "m0r-inert"


@pytest.mark.parametrize("key,bad", [
    ("GITHUB_EVENT_NAME","issues"), ("RUNNER_OS","Linux"),
    ("GITHUB_RUN_ID","bad"), ("GITHUB_SHA","not-a-commit"),
    ("GITHUB_REPOSITORY_OWNER","someone-else")])
def test_guard_rejects_non_owner_non_windows_or_unpinned(key,bad):
    m, env, event = _context()
    env[key] = bad
    with pytest.raises(ValueError,match="M0R_WORKER_KILL_GUARD_REJECTED"):
        m.validate_context(env,event)


def test_guard_rejects_other_issue_and_issuer():
    m, env, event = _context()
    event["ref"]="[EF02] unrelated"
    with pytest.raises(ValueError,match="M0R_WORKER_KILL_GUARD_REJECTED"):
        m.validate_context(env,event)
    event["ref"] = "refs/heads/main"
    event["sender"]["login"]="other"
    with pytest.raises(ValueError,match="M0R_WORKER_KILL_GUARD_REJECTED"):
        m.validate_context(env,event)


class FakeGitHub:
    def __init__(self, attempt: str, source: str, fp: str, pid: int, bad=""):
        from ai_commerce.remote.transport import GitHubTransport
        self.attempt=attempt
        self.key=GitHubTransport("trung-via/ai-commerce","fixture").attempt_key(attempt)
        self.refs={k:(ch*40) for k,ch in zip(("claims","pre-spawn","spawned"),"bcd")}
        self.commits={
            "b"*40:{"message":"EF02 CLAIM\n\nfingerprint="+fp+
                    "\nattempt_id="+attempt,
                    "parents":[{"sha":source}],"tree":{"sha":"e"*40}},
            "c"*40:{"message":"EF02 PRESPAWN\n\nfingerprint="+fp+
                    "\nattempt_id="+attempt,
                    "parents":[{"sha":"b"*40}],"tree":{"sha":"e"*40}},
            "d"*40:{"message":"EF02 SPAWNED\n\nfingerprint="+fp+
                    "\nattempt_id="+attempt+"\nchild_pid="+str(pid),
                    "parents":[{"sha":"c"*40}],"tree":{"sha":"e"*40}}}
        if bad=="parent":
            self.commits["d"*40]["parents"]=[{"sha":"a"*40}]
        if bad=="pid":
            self.commits["d"*40]["message"] += "0"
        if bad=="tree":
            self.commits["d"*40]["tree"]={"sha":"f"*40}

    def attempt_key(self, attempt):
        assert attempt == self.attempt
        return self.key

    def api(self, method, path):
        assert method=="GET"
        if path.endswith("/candidate/"+self.key):
            raise RemoteTransportError("GITHUB_HTTP_404")
        for name,sha in self.refs.items():
            if path.endswith("/"+name+"/"+self.key):
                return {"object":{"sha":sha}}
        sha=path.rsplit("/",1)[-1]
        if sha in self.commits:
            return self.commits[sha]
        raise AssertionError(path)


def test_remote_ancestry_and_no_candidate_accepts_only_exact_lineage():
    m=_module()
    gh=FakeGitHub("attempt-1","a"*40,"f"*64,1234)
    r=m.inspect_remote(gh,"a"*40,"attempt-1","f"*64,1234)
    assert r["claim_sha"]=="b"*40
    assert r["pre_spawn_sha"]=="c"*40
    assert r["spawned_sha"]=="d"*40
    assert r["candidate_present"] is False


@pytest.mark.parametrize("bad",["parent","pid","tree"])
def test_remote_ancestry_rejects_forged_pid_checkpoint(bad):
    m=_module()
    gh=FakeGitHub("attempt-1","a"*40,"f"*64,1234,bad)
    with pytest.raises(RuntimeError,match="KILLED_WORKER_REMOTE_CHAIN_UNVERIFIED"):
        m.inspect_remote(gh,"a"*40,"attempt-1","f"*64,1234)
