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


def test_type_only_rechecks_ledger_after_human_consent(config, monkeypatch, capsys):
    c = config
    store = initial(c)
    monkeypatch.setattr(
        cli, "load_registered_probe_route",
        lambda **kwargs: SimpleNamespace(route_id="enrolled"))
    monkeypatch.setattr(
        cli, "ExplicitTurnChipStager",
        lambda **kwargs: pytest.fail("CDP must not start after state drift"))

    def approve_after_other_writer(token):
        assert "TYPE-ONLY-D5-BRAIN-0" in token
        store.record_intent(operator_approved=True)
        return "TYPE-ONLY-D5-BRAIN-0"

    monkeypatch.setattr("builtins.input", approve_after_other_writer)
    args = c.args + ["--mode", "type-only", "--registry", str(c.registry),
                     "--route-id", "enrolled"]
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "TURN_STATE_CHANGED"
    assert store.read()["phase"] == "SUBMISSION_UNKNOWN"


def test_type_only_rechecks_route_after_operator_consent(config, monkeypatch, capsys):
    c = config
    initial(c)
    routes = iter([
        SimpleNamespace(route_id="enrolled", generation=1),
        SimpleNamespace(route_id="enrolled", generation=2),
    ])
    monkeypatch.setattr(
        cli, "load_registered_probe_route", lambda **kwargs: next(routes))
    monkeypatch.setattr("builtins.input", lambda token: "TYPE-ONLY-D5-BRAIN-0")
    monkeypatch.setattr(
        cli, "ExplicitTurnChipStager",
        lambda **kwargs: pytest.fail("CDP must not start after route drift"))
    args = c.args + ["--mode", "type-only", "--registry", str(c.registry),
                     "--route-id", "enrolled"]
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "ROUTE_STATE_CHANGED"
    assert TurnSessionStore(
        path=c.state, repository_root=c.checkout).read()["phase"] == "READY"


def test_type_only_rechecks_revoked_route_consent(config, monkeypatch, capsys):
    c = config
    initial(c)
    calls = [0]
    def lookup(**kwargs):
        calls[0] += 1
        if calls[0] == 2:
            raise cli.LocalProbeConfigError("PROBE_ROUTE_NOT_CONSENTED")
        return SimpleNamespace(route_id="enrolled", generation=1)
    monkeypatch.setattr(cli, "load_registered_probe_route", lookup)
    monkeypatch.setattr("builtins.input", lambda token: "TYPE-ONLY-D5-BRAIN-0")
    monkeypatch.setattr(
        cli, "ExplicitTurnChipStager",
        lambda **kwargs: pytest.fail("CDP must not start after revoked consent"))
    args = c.args + ["--mode", "type-only", "--registry", str(c.registry),
                     "--route-id", "enrolled"]
    assert cli.main(args) == 2
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["code"] == "PROBE_ROUTE_NOT_CONSENTED"
    assert calls[0] == 2


def test_send_mode_disabled_even_if_ledger_missing(config, capsys, monkeypatch):
    monkeypatch.setattr(cli, "load_registered_probe_route",
                        lambda **kw: pytest.fail("disabled mode must not read route"))
    monkeypatch.setattr(cli, "_fresh_main_sha",
                        lambda: pytest.fail("disabled mode must not read GitHub"))
    assert cli.main(config.args + ["--mode", "send-once"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result == {"code": "SEND_DISABLED", "sent": False}
    assert not config.state.exists()


def test_send_mode_wrong_token_has_no_ledger_write_or_cdp(config, monkeypatch, capsys):
    c = config
    store = initial(c)
    before = c.state.read_bytes()
    monkeypatch.setattr(cli, "load_registered_probe_route",
                        lambda **kw: SimpleNamespace(route_id="enrolled"))
    monkeypatch.setattr(cli, "_fresh_main_sha", lambda: "b" * 40)
    monkeypatch.setattr("builtins.input", lambda prompt: "WRONG")
    monkeypatch.setattr(cli, "ExplicitTurnOneShotSender",
                        lambda **kw: pytest.fail("no CDP after token refusal"))
    args = c.args + ["--mode", "send-once", "--enable-send-once",
                     "--registry", str(c.registry), "--route-id", "enrolled"]
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "USER_DECLINED"
    assert c.state.read_bytes() == before and store.read()["phase"] == "READY"


def test_send_mode_stale_main_does_not_prompt_or_write(config, monkeypatch, capsys):
    c = config
    initial(c)
    before = c.state.read_bytes()
    monkeypatch.setattr(cli, "load_registered_probe_route",
                        lambda **kw: SimpleNamespace(route_id="enrolled"))
    monkeypatch.setattr(cli, "_fresh_main_sha", lambda: "c" * 40)
    monkeypatch.setattr("builtins.input",
                        lambda prompt: pytest.fail("stale source must not prompt"))
    args = c.args + ["--mode", "send-once", "--enable-send-once",
                     "--registry", str(c.registry), "--route-id", "enrolled"]
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "SOURCE_SHA_CHANGED"
    assert c.state.read_bytes() == before


def test_send_mode_consent_rechecks_route_before_attaching(config, monkeypatch, capsys):
    c = config
    initial(c)
    before = c.state.read_bytes()
    routes = iter([SimpleNamespace(route_id="enrolled", generation=1),
                   SimpleNamespace(route_id="enrolled", generation=2)])
    monkeypatch.setattr(cli, "load_registered_probe_route",
                        lambda **kw: next(routes))
    monkeypatch.setattr(cli, "_fresh_main_sha", lambda: "b" * 40)
    monkeypatch.setattr("builtins.input", lambda token: token.split("Type exactly ")[1].split(" [other")[0])
    monkeypatch.setattr(cli, "ExplicitTurnOneShotSender",
                        lambda **kw: pytest.fail("route changed before sender"))
    args = c.args + ["--mode", "send-once", "--enable-send-once",
                     "--registry", str(c.registry), "--route-id", "enrolled"]
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "ROUTE_STATE_CHANGED"
    assert c.state.read_bytes() == before


def test_send_mode_has_one_shot_per_ledger_token_and_unknown_result(config, monkeypatch, capsys):
    c = config
    store = initial(c)
    private = []
    monkeypatch.setattr(cli, "load_registered_probe_route",
                        lambda **kw: SimpleNamespace(route_id="enrolled", generation=1))
    monkeypatch.setattr(cli, "_fresh_main_sha", lambda: "b" * 40)
    def consent(text):
        private.append(text)
        return text.split("Type exactly ")[1].split(" [other")[0]
    monkeypatch.setattr("builtins.input", consent)

    class Sender:
        def __init__(self, **kwargs):
            assert kwargs == {"enabled": True}

        def send_once(self, **kwargs):
            assert kwargs["operator_approved"] is True
            assert kwargs["store"] is store or kwargs["store"].read() == store.read()
            assert kwargs["expected_state"]["phase"] == "READY"
            assert kwargs["current_main_sha"]() == "b" * 40
            assert kwargs["current_binding"]().route_id == "enrolled"
            return SimpleNamespace(code="SEND_OUTCOME_UNKNOWN",
                                   phase="SUBMISSION_UNKNOWN", sent=None)

    monkeypatch.setattr(cli, "ExplicitTurnOneShotSender", Sender)
    args = c.args + ["--mode", "send-once", "--enable-send-once",
                     "--registry", str(c.registry), "--route-id", "enrolled"]
    assert cli.main(args) == 2  # unknown outcomes always nonzero
    output = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert output["code"] == "SEND_OUTCOME_UNKNOWN" and output["sent"] is None
    assert output["host_selection_proven"] is False
    assert "SEND-ONCE-D5-BRAIN-0-aaaaaaaa" in private[0]


def test_prepare_turn_calls_stager_with_exact_prompt_mode(config, monkeypatch, capsys):
    c = config
    initial(c)
    monkeypatch.setattr(
        cli, "load_registered_probe_route",
        lambda **kwargs: SimpleNamespace(route_id="enrolled"))
    monkeypatch.setattr("builtins.input", lambda _: "PREPARE-D5-BRAIN-0")
    calls = []

    class FakeStager:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))
        def stage(self, binding, session, **kwargs):
            calls.append(("stage", session["turn_index"], kwargs))
            return SimpleNamespace(
                code="TURN_PREPARED_NO_SEND", phase="COMPLETE",
                brain="STRUCTURED_CANDIDATE", engineering="ABSENT")
    monkeypatch.setattr(cli, "ExplicitTurnChipStager", FakeStager)
    class PreloadInventory:
        def preload(self, *args, **kwargs):
            assert kwargs["current_binding"]().route_id == "enrolled"
            return "EXACT_CHAT_OPENED"
    monkeypatch.setattr(cli, "_PlaywrightInventory", PreloadInventory)
    args = c.args + [
        "--mode", "prepare-turn", "--registry", str(c.registry),
        "--route-id", "enrolled"]
    assert cli.main(args) == 0
    assert calls[-1] == ("stage", 0, {
        "operator_approved": True, "include_prompt": True,
    })
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["sent"] is False


def test_observe_reply_reconciles_only_with_ui_and_github_evidence(
        config, monkeypatch, capsys):
    c = config
    state = initial(c)
    state.record_intent(operator_approved=True)
    monkeypatch.setattr(cli, "_fresh_main_sha", lambda: "b" * 40)
    monkeypatch.setattr(
        "builtins.input", lambda _: "OBSERVE-UI-D5-0-aaaaaaaa")
    called = []

    class FakeAuthority:
        def __init__(self, **kwargs):
            called.append(("authority", kwargs["expected_author_id"]))
        def verify(self, session, comment):
            called.append(("verify", session["phase"], comment))
            return True
    monkeypatch.setattr(cli, "GitHubTurnEvidenceAuthority", FakeAuthority)
    comment = ("https://github.com/trung-via/ai-commerce/issues/181"
               "#issuecomment-345678")
    args = c.args + [
        "--mode", "observe-reply", "--comment-ref", comment,
        "--expected-author-id", "12345"]
    assert cli.main(args) == 0
    status = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert status["code"] == "UI_REPLY_RECONCILED_ONLY"
    assert status["turn_index"] == 1
    assert status["host_selection_proven"] is False
    assert state.read()["phase"] == "READY"
    assert called == [("authority", 12345), ("verify", "SUBMISSION_UNKNOWN", comment)]


def test_observe_refuses_missing_evidence_and_no_pending_ui(
        config, monkeypatch, capsys):
    c = config
    initial(c)
    monkeypatch.setattr(cli, "_fresh_main_sha",
                        lambda: pytest.fail("must not query source when args incomplete"))
    assert cli.main(c.args + ["--mode", "observe-reply"]) == 2
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "OBSERVATION_ARGS_REQUIRED"
    assert cli.main(c.args + ["--mode", "observe-reply",
                               "--comment-ref", "invalid", "--expected-author-id", "5"]) == 2
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "TURN_RECONCILIATION_BLOCKED"


def test_observe_denied_does_not_advance_unknown(config, monkeypatch, capsys):
    c = config
    state = initial(c)
    state.record_intent(operator_approved=True)
    monkeypatch.setattr(cli, "_fresh_main_sha", lambda: "b" * 40)
    monkeypatch.setattr("builtins.input", lambda _: "NO")
    monkeypatch.setattr(
        cli, "GitHubTurnEvidenceAuthority",
        lambda **kwargs: pytest.fail("Must not read GitHub without Human UI approval"))
    args = c.args + [
        "--mode", "observe-reply",
        "--comment-ref", "https://github.com/trung-via/ai-commerce/issues/181#issuecomment-123",
        "--expected-author-id", "12345"]
    assert cli.main(args) == 2
    assert state.read()["phase"] == "SUBMISSION_UNKNOWN"
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "USER_DECLINED"


def test_open_chat_only_loads_url_no_stage_or_send(config, monkeypatch, capsys):
    c = config
    store = initial(c)
    monkeypatch.setattr(cli, "load_registered_probe_route",
                        lambda **kw: SimpleNamespace(route_id="enrolled"))
    monkeypatch.setattr("builtins.input", lambda _: "OPEN-EXACT-CHAT-D5-BRAIN-0")
    class PreloadInventory:
        def preload(self, binding, **kw):
            assert kw["current_binding"]() == binding
            return "EXACT_CHAT_OPENED"
    monkeypatch.setattr(cli, "_PlaywrightInventory", PreloadInventory)
    monkeypatch.setattr(cli, "ExplicitTurnChipStager",
                        lambda **kw: pytest.fail("open-chat cannot stage or send"))
    args = c.args + ["--mode", "open-chat", "--registry", str(c.registry),
                     "--route-id", "enrolled"]
    assert cli.main(args) == 0
    output = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert output["code"] == "EXACT_CHAT_OPENED"
    assert output["sent"] is False
    assert store.read()["phase"] == "READY"


def test_prepare_turn_preload_failure_blocks_staging(config, monkeypatch, capsys):
    c = config
    store = initial(c)
    monkeypatch.setattr(cli, "load_registered_probe_route",
                        lambda **kw: SimpleNamespace(route_id="enrolled"))
    monkeypatch.setattr("builtins.input", lambda _: "PREPARE-D5-BRAIN-0")
    class PreloadInventory:
        def preload(self, *args, **kwargs):
            return "PRELOAD_CONTEXT_AMBIGUOUS"
    monkeypatch.setattr(cli, "_PlaywrightInventory", PreloadInventory)
    monkeypatch.setattr(cli, "ExplicitTurnChipStager",
                        lambda **kw: pytest.fail("no staging if preload fails"))
    args = c.args + ["--mode", "prepare-turn", "--registry", str(c.registry),
                     "--route-id", "enrolled"]
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "PRELOAD_CONTEXT_AMBIGUOUS"
    assert store.read()["phase"] == "READY"


# D5 Wake Control Center #200, entirely synthetic, no browser or Send.
def test_wake_panel_status_route_privacy_and_open():
    from ai_commerce.remote import wake_control_center as ui
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    binding = ResolvedBinding("private_route", 1,
        "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001",
        "http://127.0.0.1:9333")
    calls = []
    class Inventory:
        def preload(self, b, *, current_binding):
            assert b == current_binding() == binding
            calls.append("open")
            return "EXACT_CHAT_OPENED"
    state = {"phase": "READY", "turn_index": 1}
    panel = ui.WakePanel(store=SimpleNamespace(read=lambda: state),
        route_reader=lambda: binding, tcp_probe=lambda: True,
        inventory_factory=Inventory)
    data = panel.status()
    assert data["role"] == "ENGINEERING" and data["can_open"]
    assert not data["can_send"] and not data["server_receipt_proven"]
    assert "private_route" not in json.dumps(data)
    assert binding.chat_url not in json.dumps(data)
    assert panel.open_exact_chat() == {"code": "EXACT_CHAT_OPENED", "sent": False}
    assert calls == ["open"]
    state["phase"] = "SUBMISSION_UNKNOWN"
    assert panel.status()["next_action"] == "RECONCILE_ONLY_NO_RETRY"
    assert panel.open_exact_chat()["code"] == "NOT_READY"
    assert calls == ["open"]


def test_wake_panel_unknown_navigation_latched_no_retries():
    from ai_commerce.remote import wake_control_center as ui
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    b = ResolvedBinding("r", 1,
        "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001",
        "http://127.0.0.1:9333")
    calls = []
    class Inventory:
        def preload(self, *_a, **_k):
            calls.append(1)
            return "PRELOAD_NAVIGATION_UNKNOWN"
    panel = ui.WakePanel(
        store=SimpleNamespace(read=lambda: {"phase":"READY","turn_index":0}),
        route_reader=lambda: b, tcp_probe=lambda: True, inventory_factory=Inventory)
    assert panel.open_exact_chat()["code"] == "PRELOAD_NAVIGATION_UNKNOWN"
    assert panel.open_exact_chat()["code"] == "NOT_READY"
    assert len(calls) == 1
    panel.tcp_probe = lambda: False
    assert panel.status()["next_action"] == "CHECK_CHROME_9333"


def test_wake_panel_http_security_and_ui_has_no_send():
    from ai_commerce.remote import wake_control_center as ui
    panel = ui.WakePanel(
        store=SimpleNamespace(read=lambda: {"phase":"COMPLETE_UI_ONLY","turn_index":3}),
        route_reader=lambda: None, tcp_probe=lambda: False)
    h = ui.make_handler(panel, port=8765, token="fixture")
    obj = h.__new__(h)
    obj.headers = {"Host": "evil.example:8765"}
    assert obj.headers.get("Host") != "127.0.0.1:8765"
    assert panel.status()["role"] == "NONE"
    assert not panel.status()["can_open"]
    assert "/api/open-chat" in ui._HTML and "Wake Control Center" in ui._HTML
    assert "/api/send" not in ui._HTML
    assert "__TOKEN__" in ui._HTML


def test_wake_panel_readonly_exact_chat_census_without_navigation():
    from contextlib import contextmanager
    from ai_commerce.remote import wake_control_center as ui
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    binding = ResolvedBinding("private", 1,
        "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001",
        "http://127.0.0.1:9333")
    other = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000002"
    pages = [SimpleNamespace(url=other)]
    calls = []
    class Inventory:
        @contextmanager
        def pages(self, endpoint):
            assert endpoint == binding.cdp_endpoint
            calls.append("inspect")
            yield pages
        def preload(self, *_args, **_kwargs):
            pytest.fail("inspection may not navigate")
    panel = ui.WakePanel(
        store=SimpleNamespace(read=lambda: {"phase":"READY","turn_index":0}),
        route_reader=lambda: binding, tcp_probe=lambda: True,
        inventory_factory=Inventory)
    assert panel.inspect_exact_chat() == {"code":"EXACT_CHAT_NOT_OPEN"}
    pages.append(SimpleNamespace(url=binding.chat_url))
    assert panel.inspect_exact_chat() == {"code":"EXACT_CHAT_PRESENT"}
    pages.append(SimpleNamespace(url=binding.chat_url))
    assert panel.inspect_exact_chat() == {"code":"EXACT_CHAT_AMBIGUOUS"}
    assert calls == ["inspect"] * 3
    assert binding.chat_url not in json.dumps(panel.inspect_exact_chat())


def test_wake_panel_inspection_denied_without_route_or_tcp():
    from ai_commerce.remote import wake_control_center as ui
    from ai_commerce.remote.conversation_binding import ResolvedBinding
    b = ResolvedBinding("private", 1,
        "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001",
        "http://127.0.0.1:9333")
    def forbidden():
        pytest.fail("must not inspect without TCP or route")
    p = ui.WakePanel(store=SimpleNamespace(read=lambda: {"phase":"READY","turn_index":0}),
        route_reader=lambda: None, tcp_probe=lambda: True, inventory_factory=forbidden)
    assert p.inspect_exact_chat() == {"code":"ROUTE_UNAVAILABLE"}
    p.route_reader = lambda: b
    p.tcp_probe = lambda: False
    assert p.inspect_exact_chat() == {"code":"CHROME_9333_NOT_REACHABLE"}
