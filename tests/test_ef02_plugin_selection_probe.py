"""Non-delivering D5 plugin-chip diagnostic tests; no actual browser/send."""
from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import contextmanager

import pytest

from ai_commerce.remote.conversation_binding import ResolvedBinding
from ai_commerce.remote.plugin_selection_probe import (
    ReadOnlyPluginSelectionProbe, _PLUGIN_PROBE_JS,
)

URL = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001"
WRONG = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000002"
BIND = ResolvedBinding(
    route_id="test-route", generation=1, chat_url=URL,
    cdp_endpoint="http://127.0.0.1:9222",
)


class FakePage:
    def __init__(self, *, url=URL, result=None):
        self.url = url
        self.result = result or {
            "code": "PROBE_COMPLETE", "brain": "STRUCTURED_CANDIDATE",
            "engineering": "STRUCTURED_CANDIDATE",
        }
        self.evaluations = 0

    def evaluate(self, script, args):
        self.evaluations += 1
        assert args["url"] == URL
        assert args["brain"] == "AI Commerce Brain"
        assert args["engineering"] == "AI Commerce Engineering Workflow"
        assert "button.click()" not in script
        assert "execCommand" not in script
        assert "fetch(" not in script
        assert "cookie" not in script
        return self.result


class FakeInventory:
    def __init__(self, *pages):
        self._pages = list(pages)
        self.attachments = []

    @contextmanager
    def pages(self, endpoint):
        assert endpoint == BIND.cdp_endpoint
        self.attachments.append(endpoint)
        yield self._pages


def test_probe_disabled_does_not_attach():
    inv = FakeInventory(FakePage())
    result = ReadOnlyPluginSelectionProbe(inventory=inv).inspect(BIND)
    assert result.code == "PROBE_DISABLED"
    assert not result.host_selection_proven
    assert inv.attachments == []


def test_probe_exact_chat_unique_and_only_returns_safe_states():
    page = FakePage()
    inv = FakeInventory(FakePage(url=WRONG), page)
    result = ReadOnlyPluginSelectionProbe(enabled=True, inventory=inv).inspect(BIND)
    assert (result.code, result.brain, result.engineering) == (
        "PROBE_COMPLETE", "STRUCTURED_CANDIDATE", "STRUCTURED_CANDIDATE")
    assert result.host_selection_proven is False
    assert page.evaluations == 1


@pytest.mark.parametrize("pages,code", [
    ([], "EXACT_CHAT_TAB_NOT_FOUND"),
    ([FakePage(url=WRONG)], "EXACT_CHAT_TAB_NOT_FOUND"),
    ([FakePage(), FakePage()], "AMBIGUOUS_CHAT_TARGETS"),
])
def test_probe_rejects_missing_or_ambiguous_exact_chat(pages, code):
    result = ReadOnlyPluginSelectionProbe(
        enabled=True, inventory=FakeInventory(*pages)).inspect(BIND)
    assert result.code == code
    assert not result.host_selection_proven


@pytest.mark.parametrize("reply", [
    {"code": "PROBE_COMPLETE", "brain": "SELECTED", "engineering": "ABSENT"},
    {"code": "PROBE_COMPLETE", "brain": "ABSENT", "engineering": "ABSENT",
     "transcript": "DO_NOT_RETURN"},
    {"code": "PROBE_COMPLETE", "brain": "ABSENT"},
    "raw user content",
])
def test_probe_rejects_untrusted_page_response(reply):
    inv = FakeInventory(FakePage(result=reply))
    result = ReadOnlyPluginSelectionProbe(enabled=True, inventory=inv).inspect(BIND)
    assert result.code == "SURFACE_UNPROVEN"
    assert not result.host_selection_proven


def test_probe_invalid_binding_fail_closed_without_browser():
    inv = FakeInventory(FakePage())
    probe = ReadOnlyPluginSelectionProbe(enabled=True, inventory=inv)
    assert probe.inspect(None).code == "BINDING_INVALID"
    bad = ResolvedBinding(
        route_id="r", generation=1, chat_url="https://evil.test/c/0123",
        cdp_endpoint="http://127.0.0.1:9222",
    )
    assert probe.inspect(bad).code == "BINDING_INVALID"
    assert inv.attachments == []


def test_real_js_distinguishes_structured_chip_from_plain_at_text():
    """Execute production JS on a bounded Node DOM fixture; no real Chrome."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js not installed")
    program = r"""
const fs=require('node:fs');
const v=JSON.parse(fs.readFileSync(0,'utf8'));
let url=v.url;
const elem=(tag,text,attrs={})=>({
 tagName:tag, textContent:text, children:[], parentElement:null,
 getAttribute:k=>Object.prototype.hasOwnProperty.call(attrs,k)?attrs[k]:null,
 getClientRects:()=>[1],
});
const form=elem('FORM',''), main=elem('MAIN','');
const editor=elem('DIV',''); editor.parentElement=form;
const brain=elem('SPAN','AI Commerce Brain',{'data-type':'mention','contenteditable':'false'});
const engineering=elem('SPAN','AI Commerce Engineering Workflow',{'data-type':'mention','contenteditable':'false'});
brain.parentElement=form; engineering.parentElement=form;
form.querySelectorAll=()=>v.scenario==='only_engineering'?[engineering]:
  v.scenario==='plain'?[elem('P','AI Commerce Brain')]:
  v.scenario==='duplicate'?[brain,brain,engineering]:[brain,engineering];
const document={
 querySelectorAll:sel=>{
  if(sel==='main') return [main];
  if(sel===v.args.composer) return [editor];
  if(sel===v.args.login || sel===v.args.work || sel===v.args.stop) return [];
  return [];
 }
};
const probe=(new Function('args','document','location','getComputedStyle',
  'return ('+v.js+')(args);'));
const run=mode=>probe(v.args,document,{href:mode==='wrong_url'?
 'https://chatgpt.com/c/00000000-0000-0000-0000-000000000002':url},
 ()=>({visibility:'visible'}));
function check(k,expected){
 v.scenario=k;
 const r=run(k);
 for(const [field,value] of Object.entries(expected))
  if(r[field]!==value) throw Error(k+':'+field+'='+r[field]+' expected '+value);
}
check('dual',{code:'PROBE_COMPLETE',brain:'STRUCTURED_CANDIDATE',
  engineering:'STRUCTURED_CANDIDATE'});
check('only_engineering',{code:'PROBE_COMPLETE',brain:'ABSENT',
  engineering:'STRUCTURED_CANDIDATE'});
check('plain',{code:'PROBE_COMPLETE',brain:'PLAIN_TEXT_ONLY',
  engineering:'ABSENT'});
check('duplicate',{code:'PROBE_COMPLETE',brain:'AMBIGUOUS',
  engineering:'STRUCTURED_CANDIDATE'});
check('wrong_url',{code:'TARGET_PAGE_CHANGED'});
"""
    args = {
        "url": URL,
        "composer": "main form [contenteditable]",
        "login": "#login", "work": "#work", "stop": "#stop",
        "brain": "AI Commerce Brain",
        "engineering": "AI Commerce Engineering Workflow",
    }
    out = subprocess.run([node, "-e", program],
                         input=json.dumps({"js": _PLUGIN_PROBE_JS, "args": args,
                                           "url": URL}),
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr[:1200]


def _write_private_route_registry(tmp_path, *, consent=True, extra_routes=None):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    private = tmp_path / "operator-private"
    private.mkdir()
    registry = private / "registry.json"
    routes = [{
        "route_id": "registered-test", "generation": 2,
        "chat_url": URL, "cdp_endpoint": "http://127.0.0.1:9222",
        "consent": consent,
    }]
    routes.extend(extra_routes or [])
    registry.write_text(json.dumps({
        "version": 1, "routes": routes, "claims": [],
    }), encoding="utf-8")
    return checkout, registry


def test_operator_probe_loader_reuses_registered_exact_route(tmp_path):
    from ai_commerce.remote.plugin_selection_probe_cli import load_registered_probe_route

    checkout, registry = _write_private_route_registry(tmp_path)
    binding = load_registered_probe_route(
        registry_path=registry, checkout=checkout, route_id="registered-test")
    assert binding == ResolvedBinding(
        route_id="registered-test", generation=2, chat_url=URL,
        cdp_endpoint="http://127.0.0.1:9222")


def test_operator_probe_loader_rejects_revoked_or_unknown_route(tmp_path):
    from ai_commerce.remote.plugin_selection_probe_cli import (
        LocalProbeConfigError, load_registered_probe_route,
    )

    checkout, registry = _write_private_route_registry(tmp_path, consent=False)
    with pytest.raises(LocalProbeConfigError, match="PROBE_ROUTE_NOT_CONSENTED"):
        load_registered_probe_route(
            registry_path=registry, checkout=checkout, route_id="registered-test")
    with pytest.raises(LocalProbeConfigError, match="PROBE_ROUTE_INVALID"):
        load_registered_probe_route(
            registry_path=registry, checkout=checkout, route_id="../bad")


def test_operator_probe_loader_rejects_ambiguous_chat_identity(tmp_path):
    from ai_commerce.remote.plugin_selection_probe_cli import (
        LocalProbeConfigError, load_registered_probe_route,
    )

    checkout, registry = _write_private_route_registry(tmp_path, extra_routes=[{
        "route_id": "duplicate-chat", "generation": 1,
        "chat_url": URL, "cdp_endpoint": "http://127.0.0.1:9222",
        "consent": True,
    }])
    with pytest.raises(LocalProbeConfigError, match="PROBE_REGISTRY_AMBIGUOUS"):
        load_registered_probe_route(
            registry_path=registry, checkout=checkout, route_id="registered-test")


def test_operator_probe_loader_rejects_repo_local_registry(tmp_path):
    from ai_commerce.remote.plugin_selection_probe_cli import (
        LocalProbeConfigError, load_registered_probe_route,
    )

    checkout, registry = _write_private_route_registry(tmp_path)
    inside = checkout / "registry.json"
    inside.write_bytes(registry.read_bytes())
    with pytest.raises(LocalProbeConfigError, match="PROBE_REGISTRY_INVALID"):
        load_registered_probe_route(
            registry_path=inside, checkout=checkout, route_id="registered-test")


def test_operator_probe_cli_requires_explicit_read_only_gate(tmp_path, capsys):
    from ai_commerce.remote.plugin_selection_probe_cli import main

    checkout, registry = _write_private_route_registry(tmp_path)
    result = main([
        "--registry", str(registry), "--checkout", str(checkout),
        "--route-id", "registered-test",
    ])
    assert result == 2
    output = json.loads(capsys.readouterr().out)
    assert output == {"code": "PROBE_DISABLED", "host_selection_proven": False}
