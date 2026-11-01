"""D5 turn CLI: private ledger + registered route, never browser Send."""
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_commerce.remote import plugin_turn_cli as cli
from ai_commerce.remote.plugin_turn_handoff import (
    TurnHandoffError, record_send_intent,
)
from ai_commerce.remote.plugin_turn_store import TurnSessionStore
from test_ef02_d5_local_handoff import provision


@pytest.fixture
def config(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    private = provision(tmp_path / "private")
    state = private / "turn.json"
    registry = private / "route.json"
    args = ["--checkout", str(checkout), "--state-file", str(state)]
    return SimpleNamespace(checkout=checkout, private=private,
                           state=state, registry=registry, args=args)


def initial(c):
    store = TurnSessionStore(path=c.state, repository_root=c.checkout)
    store.create(handoff_id="d5-turn-" + "a" * 32,
                 issue_number=181, source_sha="b" * 40)
    return store


def test_default_preview_has_no_cdp_or_send(capsys, config, monkeypatch):
    c = config
    initial(c)
    monkeypatch.setattr(cli, "ExplicitTurnChipStager", lambda **kw:
                        pytest.fail("CDP must not initialize"))
    monkeypatch.setattr(cli, "load_registered_probe_route", lambda **kw:
                        pytest.fail("route must not be queried"))
    assert cli.main(c.args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "code": "PREVIEW_ONLY", "host_selection_proven": False,
        "role": "BRAIN", "sent": False, "turn_index": 0}


def test_create_ledger_requires_separate_consent(config, monkeypatch, capsys):
    c = config
    monkeypatch.setattr("builtins.input", lambda token: "NO")
    base = c.args + ["--mode", "create-ledger", "--issue-number", "181",
                     "--source-sha", "b" * 40]
    assert cli.main(base) == 2
    assert not c.state.exists()
    monkeypatch.setattr("builtins.input", lambda token: "CREATE-D5-PRIVATE-LEDGER")
    assert cli.main(base) == 0
    record = json.loads(c.state.read_text())
    assert record["phase"] == "READY"
    assert record["handoff_id"].startswith("d5-turn-")
    assert cli.main(base) == 2  # Cannot silently overwrite the ledger.


def test_missing_state_does_not_stage(config, capsys):
    assert cli.main(config.args) == 2
    assert "TURN_SESSION_NOT_FOUND" in capsys.readouterr().out


@pytest.mark.parametrize("kwargs,code", [
    (["--mode", "type-only"], "ROUTE_ARGS_REQUIRED"),
    (["--mode", "create-ledger"], "LEDGER_INIT_ARGS_REQUIRED"),
])
def test_rejects_missing_mode_args(config, capsys, kwargs, code):
    assert cli.main(config.args + kwargs) == 2
    assert json.loads(capsys.readouterr().out)["code"] == code


def test_rejects_consent_typo_before_typing(config, monkeypatch, capsys):
    c = config
    initial(c)
    monkeypatch.setattr(cli, "load_registered_probe_route",
                        lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr("builtins.input", lambda text: "TPYE-ONLY-D5-BRAIN-0")
    monkeypatch.setattr(cli, "ExplicitTurnChipStager", lambda **kwargs:
                        pytest.fail("No CDP after typo"))
    args = c.args + ["--mode", "type-only", "--registry", str(c.registry),
                     "--route-id", "enrolled"]
    assert cli.main(args) == 2
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["code"] == "USER_DECLINED" and not result["sent"]


def test_type_only_one_expected_role_and_false_host_proof(config, monkeypatch, capsys):
    c = config
    initial(c)
    calls = []
    monkeypatch.setattr(cli, "load_registered_probe_route",
                        lambda **kwargs: SimpleNamespace(route_id="enrolled"))
    monkeypatch.setattr("builtins.input", lambda text: "TYPE-ONLY-D5-BRAIN-0")

    class FakeStager:
        def __init__(self, **kwargs):
            calls.append(("created", kwargs))

        def stage(self, binding, session, **kwargs):
            calls.append(("stage", session["turn_index"], kwargs))
            return SimpleNamespace(code="INPUT_STAGED_NO_SEND", phase="COMPLETE",
                                   brain="STRUCTURED_CANDIDATE", engineering="ABSENT")

    monkeypatch.setattr(cli, "ExplicitTurnChipStager", FakeStager)
    args = c.args + ["--mode", "type-only", "--registry", str(c.registry),
                     "--route-id", "enrolled"]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert (result["code"], result["role"], result["sent"],
            result["host_selection_proven"]) == (
                "INPUT_STAGED_NO_SEND", "BRAIN", False, False)
    assert calls[0] == ("created", {"enabled": True})
    assert calls[1] == ("stage", 0, {"operator_approved": True})


def test_unknown_pending_does_not_prompt_or_open_route(config, monkeypatch, capsys):
    c = config
    store = initial(c)
    store.record_intent(operator_approved=True)
    monkeypatch.setattr(cli, "load_registered_probe_route",
                        lambda **kwargs: pytest.fail("route access during unknown"))
    args = c.args + ["--mode", "type-only", "--registry", str(c.registry),
                     "--route-id", "enrolled"]
    assert cli.main(args) == 2
    assert "TURN_NOT_READY" in capsys.readouterr().out


def test_private_route_failure_does_not_prompt(config, monkeypatch, capsys):
    c = config
    initial(c)
    def bad(**kwargs):
        raise cli.LocalProbeConfigError("PROBE_ROUTE_NOT_CONSENTED")
    monkeypatch.setattr(cli, "load_registered_probe_route", bad)
    monkeypatch.setattr("builtins.input", lambda token:
                        pytest.fail("must not ask for typing after revoked consent"))
    args = c.args + ["--mode", "type-only", "--registry", str(c.registry),
                     "--route-id", "enrolled"]
    assert cli.main(args) == 2
    assert "PROBE_ROUTE_NOT_CONSENTED" in capsys.readouterr().out


def test_unexpected_exception_redacts_private_paths(config, monkeypatch, capsys):
    c = config
    initial(c)
    monkeypatch.setattr(cli, "load_registered_probe_route",
                        lambda **kwargs: (_ for _ in ()).throw(
                            RuntimeError("PRIVATE_PATH_OR_SESSION_COOKIE")))
    args = c.args + ["--mode", "type-only", "--registry", str(c.registry),
                     "--route-id", "enrolled"]
    assert cli.main(args) == 2
    out = capsys.readouterr().out
    assert "D5_LOCAL_UNAVAILABLE" in out
    assert "PRIVATE_PATH" not in out
    assert "SESSION_COOKIE" not in out
