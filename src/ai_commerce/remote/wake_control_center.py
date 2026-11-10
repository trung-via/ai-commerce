"""D5 Wake Control Center: loopback-only operator UI, no Send capability."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import threading
from urllib.parse import urlsplit
from .cdp_doorbell import _PlaywrightInventory, _CHAT_A_READ_ONLY_SURFACE
from .conversation_binding import ResolvedBinding
from .plugin_selection_probe_cli import load_registered_probe_route
from .plugin_turn_store import TurnSessionStore
from .plugin_turn_handoff import preview_next_turn, TurnHandoffError
from .plugin_turn_stage_only import ExplicitTurnChipStager
from .plugin_turn_cli import _fresh_main_sha

_ROLES = ("BRAIN", "ENGINEERING", "BRAIN")
_GOOD = frozenset(("EXACT_CHAT_OPENED", "EXACT_CHAT_PRESENT"))
_UNCERTAIN = frozenset((
    "PRELOAD_NAVIGATION_UNKNOWN", "PRELOAD_NAVIGATION_UNVERIFIED",
    "PRELOAD_CDP_UNAVAILABLE", "PRELOAD_EXACT_CHAT_AMBIGUOUS",
    "PRELOAD_CONTEXT_AMBIGUOUS",
))


def _tcp_port(port: int) -> bool:
    # TCP availability is NOT proof of Chrome identity or a Send grant.
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _tcp_9333() -> bool:
    return _tcp_port(9333)


class WakePanel:
    def __init__(self, *, store, route_reader, tcp_probe=None,
                 inventory_factory=_PlaywrightInventory,
                 source_reader=_fresh_main_sha,
                 staging_factory=ExplicitTurnChipStager,
                 cdp_port: int = 9333):
        if type(cdp_port) is not int or cdp_port not in (9333, 9444):
            raise ValueError("CONTROL_CDP_PORT_INVALID")
        self.cdp_port = cdp_port
        self.store, self.route_reader = store, route_reader
        self.tcp_probe = tcp_probe if tcp_probe is not None else (
            lambda: _tcp_port(self.cdp_port))
        self.inventory_factory = inventory_factory
        self.source_reader, self.staging_factory = source_reader, staging_factory
        self._lock = threading.Lock()
        self._uncertain = False
        self._prepare_attempted = False

    def _state(self):
        try:
            s = self.store.read()
            if s["phase"] not in ("READY", "SUBMISSION_UNKNOWN", "COMPLETE_UI_ONLY"):
                return None
            if type(s["turn_index"]) is not int or not 0 <= s["turn_index"] <= 3:
                return None
            return s
        except Exception:
            return None

    def _route(self):
        try:
            b = self.route_reader()
            u = urlsplit(b.cdp_endpoint)
            if (type(b) is not ResolvedBinding or u.scheme != "http" or
                u.hostname not in ("127.0.0.1", "localhost") or
                u.port != self.cdp_port or
                (self.cdp_port == 9444 and
                 b.cdp_endpoint != "http://127.0.0.1:9444")):
                return None
            return b
        except Exception:
            return None

    def status(self):
        state = self._state()
        phase = state["phase"] if state else "UNAVAILABLE"
        idx = state["turn_index"] if state else -1
        route = self._route() is not None
        try:
            tcp = self.tcp_probe() is True
        except Exception:
            tcp = False
        enabled = (phase == "READY" and route and tcp
                   and not self._uncertain and not self._prepare_attempted)
        if phase == "SUBMISSION_UNKNOWN":
            next_action = "RECONCILE_ONLY_NO_RETRY"
        elif phase == "COMPLETE_UI_ONLY":
            next_action = "SESSION_FINISHED_UI_ONLY"
        elif phase != "READY":
            next_action = "PRIVATE_LEDGER_UNAVAILABLE"
        elif not route:
            next_action = "CHECK_ENROLLED_ROUTE"
        elif not tcp:
            next_action = "CHECK_CHROME_" + str(self.cdp_port)
        elif self._uncertain:
            next_action = "INSPECT_NAVIGATION_UNKNOWN"
        elif self._prepare_attempted:
            next_action = "INSPECT_PREPARED_DRAFT_MANUALLY"
        else:
            next_action = ("OPEN_EXACT_CHAT_NO_SEND" if self.cdp_port == 9444
                           else "OPEN_EXACT_CHAT_THEN_PREPARE")
        return {
            "phase": phase, "turn_index": idx,
            "role": _ROLES[idx] if 0 <= idx < 3 else "NONE",
            "route": "ENROLLED" if route else "UNAVAILABLE",
            "tcp": "REACHABLE_UNVERIFIED" if tcp else "NOT_REACHABLE",
            "next_action": next_action, "can_open": enabled,
            # WH-06 9444 UI never stages @plugin or sends; WH-08 remains disabled.
            "can_prepare": enabled and self.cdp_port == 9333, "can_send": False,
            "host_selection_proven": False,
            "server_receipt_proven": False,
        }

    def prepare_turn(self, consent: str) -> dict:
        """One explicitly consented @chip+prompt staging; never Send/retry.

        Serialized against the local Open Chat button. Caller must still use
        independent CLI consent for Send Once after visual inspection.
        """
        if self.cdp_port == 9444:
            return {"code": "WH07_NO_PLUGIN_PREPARE", "sent": False}
        with self._lock:
            if not self.status()["can_prepare"]:
                return {"code": "PREPARE_NOT_ELIGIBLE", "sent": False}
            snapshot, binding = self._state(), self._route()
            if snapshot is None or binding is None:
                return {"code": "PREPARE_STATE_CHANGED", "sent": False}
            try:
                preview = preview_next_turn(snapshot)
            except (TurnHandoffError, KeyError, TypeError):
                return {"code": "PREPARE_SESSION_INVALID", "sent": False}
            required = f"PREPARE-D5-{preview['role']}-{preview['turn_index']}"
            if (type(consent) is not str or len(consent) > 80 or
                    not secrets.compare_digest(consent, required)):
                return {"code": "PREPARE_CONSENT_REQUIRED", "sent": False}
            # Source check is a live authenticated local gh read, not a cache.
            try:
                if self.source_reader() != snapshot["source_sha"]:
                    return {"code": "PREPARE_SOURCE_CHANGED", "sent": False}
                if self._state() != snapshot or self._route() != binding:
                    return {"code": "PREPARE_STATE_CHANGED", "sent": False}
            except Exception:
                return {"code": "PREPARE_SOURCE_UNAVAILABLE", "sent": False}
            try:
                preloaded = self.inventory_factory().preload(
                    binding, current_binding=self.route_reader)
            except Exception:
                preloaded = "PRELOAD_CDP_UNAVAILABLE"
            if preloaded not in _GOOD:
                if preloaded in _UNCERTAIN:
                    self._uncertain = True
                return {"code": preloaded if preloaded.startswith("PRELOAD_")
                        else "PRELOAD_UNAVAILABLE", "sent": False}
            try:
                if (self._state() != snapshot or self._route() != binding or
                        self.source_reader() != snapshot["source_sha"]):
                    return {"code": "PREPARE_STATE_OR_SOURCE_CHANGED", "sent": False}
            except Exception:
                return {"code": "PREPARE_SOURCE_UNAVAILABLE", "sent": False}
            # Once staging might touch the composer, never attempt again in
            # this process. Failed or ambiguous staging needs Human cleanup.
            self._prepare_attempted = True
            try:
                result = self.staging_factory(enabled=True).stage(
                    binding, snapshot, operator_approved=True,
                    include_prompt=True)
                code = result.code
            except Exception:
                code = "PREPARE_UNKNOWN_CLEAR_MANUALLY"
            return {"code": code if type(code) is str and
                    len(code) <= 64 else "PREPARE_UNKNOWN_CLEAR_MANUALLY",
                    "sent": False, "host_selection_proven": False}

    def inspect_exact_chat(self):
        """On-demand read-only CDP tab census: no navigation, no Send."""
        binding = self._route()
        if binding is None:
            return {"code": "ROUTE_UNAVAILABLE"}
        try:
            if self.tcp_probe() is not True:
                return {"code": "CHROME_" + str(self.cdp_port) + "_NOT_REACHABLE"}
            with self.inventory_factory().pages(binding.cdp_endpoint) as pages:
                matches = [page for page in pages
                           if isinstance(page.url, str) and
                           page.url.rstrip("/") == binding.chat_url]
                if len(matches) > 1:
                    code = "EXACT_CHAT_AMBIGUOUS"
                elif len(matches) == 1:
                    if self.cdp_port == 9444:
                        # WH-06 never displays a URL-only match as ready.
                        # This is a candidate read, not account/route authority.
                        observed = matches[0].evaluate(_CHAT_A_READ_ONLY_SURFACE)
                        code = ("EXACT_CHAT_PRESENT" if observed == "CANDIDATE_READY"
                                else "DRAFT_PRESENT" if observed == "DRAFT_PRESENT"
                                else "SURFACE_UNPROVEN")
                    else:
                        code = "EXACT_CHAT_PRESENT"
                else:
                    code = "EXACT_CHAT_NOT_OPEN"
                return {"code": code}
        except Exception:
            return {"code": "CDP_INSPECTION_UNAVAILABLE"}

    def open_exact_chat(self):
        # Only entered by an independently authorized same-origin UI click.
        with self._lock:
            if not self.status()["can_open"]:
                return {"code": "NOT_READY", "sent": False}
            before, binding = self._state(), self._route()
            if before is None or before["phase"] != "READY" or binding is None:
                return {"code": "STATE_CHANGED", "sent": False}
            try:
                code = self.inventory_factory().preload(
                    binding, current_binding=self.route_reader)
            except Exception:
                code = "PRELOAD_CDP_UNAVAILABLE"
            if code in _UNCERTAIN:
                # No automatic retry, even if the new tab partially navigated.
                self._uncertain = True
            if code not in _GOOD and not code.startswith("PRELOAD_"):
                code = "PRELOAD_UNAVAILABLE"
            return {"code": code, "sent": False}


_HTML = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Commerce — Wake Control Center</title>
<style>
:root{color-scheme:dark;font:15px/1.5 system-ui,Segoe UI,Arial,sans-serif;
--bg:#0b1021;--card:#15213a;--line:#364a68;--ink:#f4f7ff;
--muted:#b4c1da;--blue:#9fc0ff;--mint:#66ddc1}
*{box-sizing:border-box}body{margin:0;color:var(--ink);background:
radial-gradient(ellipse at 15% 0,#2b4378,transparent 55%),
radial-gradient(ellipse at 90% 60%,#17363b,transparent 60%),var(--bg)}
main{max-width:1000px;margin:auto;padding:clamp(16px,4vw,48px)}
h1{font-size:clamp(26px,4vw,40px);margin:4px 0}h2{font-size:18px;margin:0 0 12px}
p{color:var(--muted)}.eyebrow{color:var(--blue);text-transform:uppercase;
font-size:12px;letter-spacing:.15em;font-weight:800}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-top:25px}
.card{background:linear-gradient(145deg,#203052ee,var(--card));border:1px solid
var(--line);border-radius:18px;padding:21px;min-width:0;box-shadow:0 12px 38px #0002}
.wide{grid-column:1/-1}.label{font-size:12px;color:var(--muted);
text-transform:uppercase;letter-spacing:.09em}.value{font-size:20px;
overflow-wrap:anywhere;font-weight:750;margin-top:12px}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.step{border:1px solid var(--line);border-radius:12px;padding:17px;color:var(--muted)}
.step.active{background:#2b4265;border-color:var(--blue);color:var(--ink)}
.step.done{border-color:#347d73}
.controls{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}
button{padding:12px 16px;border-radius:12px;background:var(--blue);color:#101b31;
border:none;font-weight:750;font:inherit;cursor:pointer}
button.secondary{background:#26354f;color:var(--ink);border:1px solid var(--line)}
button:disabled{cursor:not-allowed;opacity:.45}
button:focus-visible,input:focus-visible{outline:3px solid #ffd080;outline-offset:3px}
input{padding:11px;border-radius:12px;border:1px solid var(--line);
background:#0e1830;color:var(--ink);min-width:min(100%,300px);font:inherit}
label{color:var(--muted);font-size:13px}
#result{color:var(--mint);min-height:22px}
small{color:var(--muted)}footer{font-size:12px;color:var(--muted);margin-top:20px}
a{color:var(--blue)}
@media(max-width:650px){.grid,.steps{grid-template-columns:1fr}.wide{grid-column:1}}
</style></head><body><main>
<div class="eyebrow">AI Commerce / Local Operator</div>
<h1>Wake Control Center</h1>
<p>Chrome CDP 9333 · Mở đúng cuộc trò chuyện · Không tự gửi Wake</p>
<div class="grid">
<section class="card"><div class="label">Chrome 9333</div><div class="value" id="tcp">Đang đọc…</div>
<small>TCP không chứng minh danh tính Chrome.</small><br>
<small id="chat">Chưa kiểm tra tab</small></section>
<section class="card"><div class="label">Private Route</div><div class="value" id="route">Đang đọc…</div>
<small>Không hiển thị URL riêng tư.</small></section>
<section class="card"><div class="label">Ledger</div><div class="value" id="phase">Đang đọc…</div>
<small>UNKNOWN luôn chặn retry.</small></section>
<section class="card wide"><h2>Brain → Engineering → Brain</h2>
<div class="steps" id="steps">
<div class="step">01 · BRAIN</div><div class="step">02 · ENGINEERING</div>
<div class="step">03 · BRAIN</div></div></section>
<section class="card wide"><h2>Hành động được phép</h2>
<div class="value" id="next">Đang xác minh…</div>
<div class="controls"><button id="open" disabled>Mở chat chính xác</button>
<button id="inspect" class="secondary">Kiểm tra tab (chỉ đọc)</button>
<button id="refresh" class="secondary">Làm mới</button></div>
<div class="controls">
<label for="consent">Mã xác nhận riêng cho lượt Prepare (không Send):</label>
<input id="consent" maxlength="80" autocomplete="off" spellcheck="false"
aria-describedby="prepare-hint" placeholder="PREPARE-D5-ROLE-INDEX">
<button id="prepare" disabled>Prepare @ plugin + prompt (chưa Send)</button></div>
<p id="prepare-hint"><small>Mã yêu cầu: <code id="expected-consent">Chưa sẵn sàng</code>.
Nhập thủ công; chỉ thực hiện một lần mỗi phiên giao diện.</small></p>
<p id="result" role="status" aria-live="polite"></p>
<p><small>Chỉ mở tab đã đăng ký. Prepare @ plugin, Send Once và Observe Reply vẫn
thực hiện qua CLI với Human consent riêng. Nút này không gửi tin nhắn.</small></p>
</section></div>
<footer>Local-only 127.0.0.1 · Không xuất URL, cookie hoặc transcript.
<a href="https://github.com/trung-via/ai-commerce/issues/200"
rel="noopener noreferrer">Issue #200</a></footer></main>
<script>
const token=__TOKEN__;
const el=id=>document.getElementById(id);
const label={READY:"READY",SUBMISSION_UNKNOWN:"UNKNOWN · CẦN ĐỐI SOÁT",
COMPLETE_UI_ONLY:"COMPLETE · UI-only",UNAVAILABLE:"Không xác thực",
ENROLLED:"Đã đăng ký",NOT_REACHABLE:"Chưa kết nối",
REACHABLE_UNVERIFIED:"TCP 9333 sẵn sàng",
RECONCILE_ONLY_NO_RETRY:"Đối soát · Không gửi lại",
SESSION_FINISHED_UI_ONLY:"Phiên đã kết thúc",
PRIVATE_LEDGER_UNAVAILABLE:"Kiểm tra ledger",
CHECK_ENROLLED_ROUTE:"Kiểm tra route",
CHECK_CHROME_9333:"Kiểm tra Chrome 9333",
INSPECT_NAVIGATION_UNKNOWN:"Kiểm tra tab thủ công",
INSPECT_PREPARED_DRAFT_MANUALLY:"Đã Prepare/thử Prepare — kiểm tra bản nháp",
TURN_PREPARED_NO_SEND:"Đã chuẩn bị chip + prompt; chưa Send",
PREPARE_CONSENT_REQUIRED:"Nhập đúng mã xác nhận",
PREPARE_SOURCE_CHANGED:"Main SHA không còn khớp ledger",
PREPARE_SOURCE_UNAVAILABLE:"Không xác minh được SHA main qua GitHub",
PREPARE_STATE_OR_SOURCE_CHANGED:"Ledger, route hoặc source đã đổi",
PREPARE_NOT_ELIGIBLE:"Không đủ điều kiện Prepare",
PREPARE_UNKNOWN_CLEAR_MANUALLY:"Kết quả chưa rõ; kiểm tra bản nháp",
EXACT_CHAT_PRESENT:"Đã có tab đúng URL",EXACT_CHAT_NOT_OPEN:"Chưa mở tab đúng URL",
EXACT_CHAT_AMBIGUOUS:"Nhiều tab trùng — cần xử lý thủ công",
CHROME_9333_NOT_REACHABLE:"Chrome 9333 chưa kết nối",
ROUTE_UNAVAILABLE:"Chưa xác minh route",
CDP_INSPECTION_UNAVAILABLE:"Không thể kiểm tra CDP",
OPEN_EXACT_CHAT_THEN_PREPARE:"Mở đúng chat → Prepare @ plugin"};
function display(x){return label[x]||"Chưa rõ"}
async function refresh(){try{
const r=await fetch("/api/status",{cache:"no-store"});if(!r.ok)throw Error();
const s=await r.json();
el("tcp").textContent=display(s.tcp);el("route").textContent=display(s.route);
el("phase").textContent=display(s.phase);el("next").textContent=display(s.next_action);
el("open").disabled=!s.can_open;
el("prepare").disabled=!s.can_prepare;
el("expected-consent").textContent=s.can_prepare?
"PREPARE-D5-"+s.role+"-"+s.turn_index:"Chưa sẵn sàng";
[...el("steps").children].forEach((n,i)=>{
n.classList.toggle("active",s.turn_index===i&&s.phase==="READY");
n.classList.toggle("done",s.turn_index>i);});
}catch(e){el("next").textContent="Không đọc được trạng thái";el("open").disabled=true;}}
el("refresh").onclick=refresh;
el("prepare").onclick=async()=>{
const required=el("expected-consent").textContent;
const consent=el("consent").value;
if(consent!==required){el("result").textContent="Mã xác nhận không đúng";return;}
el("prepare").disabled=true;el("consent").value="";
el("result").textContent="Đang kiểm tra SHA main, route và bản nháp…";
try{
const r=await fetch("/api/prepare-turn",{method:"POST",
headers:{"X-Wake-Token":token,"Content-Type":"application/json"},
body:JSON.stringify({consent})});
const s=await r.json();
el("result").textContent=display(s.code)==="Chưa rõ"?
"Prepare: "+s.code:display(s.code);
}catch(e){el("result").textContent="Không rõ kết quả Prepare; kiểm tra bản nháp thủ công";}
await refresh();
};
el("inspect").onclick=async()=>{
el("inspect").disabled=true;
try{
const r=await fetch("/api/inspect-chat",{cache:"no-store"});
if(!r.ok)throw Error();
const s=await r.json();el("chat").textContent=display(s.code);
}catch(e){el("chat").textContent="Không thể xác minh tab";}
el("inspect").disabled=false;
};
el("open").onclick=async()=>{el("open").disabled=true;
el("result").textContent="Đang mở tab đã đăng ký…";
try{const r=await fetch("/api/open-chat",{method:"POST",
headers:{"X-Wake-Token":token,"Content-Type":"application/json"},body:""});
const s=await r.json();
el("result").textContent=s.code==="EXACT_CHAT_OPENED"?"Đã mở tab, chưa Send":
s.code==="EXACT_CHAT_PRESENT"?"Tab đã có, chưa Send":
"Không thể xác minh: "+s.code;}catch(e){
el("result").textContent="Không thể xác minh kết quả";}
await refresh();};
refresh();setInterval(refresh,7000);
</script></body></html>"""



_HTML_9444 = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WakeHub · CDP 9444</title>
<style>
:root{color-scheme:dark;font:16px/1.5 system-ui,Segoe UI,sans-serif;background:#101827;color:#eef3fb}
body{margin:0}main{max-width:780px;margin:0 auto;padding:clamp(20px,5vw,48px)}
section{background:#1c2940;border:1px solid #40516f;border-radius:16px;padding:18px;margin:16px 0}
h1{font-size:clamp(25px,5vw,38px)}.status{font-weight:700;overflow-wrap:anywhere}
small{color:#bbc9db}.controls{display:flex;flex-wrap:wrap;gap:10px}
button{border:1px solid #7895c0;border-radius:10px;background:#b9d4fb;color:#111e32;
padding:12px 18px;font:inherit;font-weight:bold;cursor:pointer}
button:disabled{opacity:.45;cursor:not-allowed}button:focus-visible{outline:3px solid #ffcf72;outline-offset:3px}
</style></head><body><main><small>AI Commerce · WakeHub WH-04/05/06</small>
<h1>Chrome CDP 9444</h1><p>Chỉ kiểm tra và mở chat đã đăng ký. Không gửi Wake hoặc gọi @plugin.</p>
<section><div>Chrome</div><div class="status" id="tcp">Đang kiểm tra…</div>
<small>TCP chưa chứng minh danh tính Chrome, tài khoản hoặc quyền Send.</small></section>
<section><div>Private route</div><div class="status" id="route">Đang kiểm tra…</div>
<small>Không hiển thị URL hoặc nội dung cuộc trò chuyện.</small></section>
<section><div>Ledger / Trạng thái</div><div class="status" id="phase">Đang kiểm tra…</div>
<div class="status" id="next"></div><small>UNKNOWN yêu cầu đối soát, không tự gửi lại.</small></section>
<section><div class="controls"><button id="inspect">Kiểm tra chat (chỉ đọc)</button>
<button id="open" disabled>Mở đúng chat</button><button id="refresh">Làm mới</button></div>
<p class="status" id="result" role="status" aria-live="polite"></p></section>
<small>WH-07 chỉ được gửi bởi bộ điều khiển private đã xác thực sự kiện, tuyến và quyền một lần.
Nút Send và Prepare @plugin cố ý không có trong giao diện này.</small></main>
<script>
const token=__TOKEN__,el=id=>document.getElementById(id);
const display=x=>typeof x==='string'&&x.length<90?x:'UNAVAILABLE';
async function refresh(){try{
const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error();
const x=await r.json();
el('tcp').textContent=display(x.tcp);
el('route').textContent=display(x.route);
el('phase').textContent=display(x.phase);
el('next').textContent=display(x.next_action);
el('open').disabled=x.can_open!==true;
}catch(e){el('open').disabled=true;el('next').textContent='Kiểm tra không khả dụng';}}
el('refresh').onclick=refresh;
el('inspect').onclick=async()=>{el('inspect').disabled=true;
try{const r=await fetch('/api/inspect-chat',{cache:'no-store'});
const x=await r.json();el('result').textContent=display(x.code);}
catch(e){el('result').textContent='Không đọc được chat';}
el('inspect').disabled=false;};
el('open').onclick=async()=>{el('open').disabled=true;
try{const r=await fetch('/api/open-chat',{method:'POST',
headers:{'X-Wake-Token':token,'Content-Type':'application/json'},body:''});
const x=await r.json();el('result').textContent=display(x.code);}
catch(e){el('result').textContent='Kết quả mở chat không rõ; không tự thử lại';}
await refresh();};
refresh();
</script></body></html>"""


def make_handler(panel: WakePanel, *, port: int, token: str):
    origin = "http://127.0.0.1:" + str(port)
    host = "127.0.0.1:" + str(port)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # No private route logs.

        def _send(self, code, data, content_type="application/json; charset=utf-8"):
            self.send_response(code)
            for k, v in (
                ("Content-Type", content_type),
                ("Content-Length", str(len(data))),
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
                ("X-Frame-Options", "DENY"),
                ("Referrer-Policy", "no-referrer"),
                ("Content-Security-Policy", "default-src 'none'; "
                 "style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                 "connect-src 'self'; frame-ancestors 'none'"),
            ):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, sort_keys=True).encode())

        def do_GET(self):
            if self.headers.get("Host") != host:
                return self._json(403, {"code": "HOST_REJECTED"})
            if self.path == "/":
                template = _HTML_9444 if panel.cdp_port == 9444 else _HTML
                html = template.replace("__TOKEN__", json.dumps(token))
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if self.path == "/api/status":
                return self._json(200, panel.status())
            if self.path == "/api/inspect-chat":
                return self._json(200, panel.inspect_exact_chat())
            return self._json(404, {"code": "NOT_FOUND"})

        def do_POST(self):
            if self.headers.get("Host") != host or self.headers.get("Origin") != origin:
                return self._json(403, {"code": "ORIGIN_REJECTED"})
            if not secrets.compare_digest(self.headers.get("X-Wake-Token", ""), token):
                return self._json(403, {"code": "TOKEN_REJECTED"})
            if self.path == "/api/open-chat":
                if self.headers.get("Content-Length") not in ("0", None):
                    return self._json(400, {"code": "OPERATION_NOT_ALLOWED"})
                return self._json(200, panel.open_exact_chat())
            if panel.cdp_port == 9444 or self.path != "/api/prepare-turn":
                return self._json(400, {"code": "OPERATION_NOT_ALLOWED"})
            if self.headers.get("Content-Type") != "application/json":
                return self._json(400, {"code": "INVALID_CONTENT_TYPE"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= 160:
                    raise ValueError
                request = json.loads(self.rfile.read(size))
                if (type(request) is not dict or
                        set(request) != {"consent"} or
                        type(request["consent"]) is not str):
                    raise ValueError
            except (ValueError, TypeError, UnicodeError, OSError):
                return self._json(400, {"code": "PREPARE_REQUEST_INVALID"})
            return self._json(200, panel.prepare_turn(request["consent"]))
    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(description="Human-clicked exact chat UI (no Send)")
    p.add_argument("--checkout", type=Path, required=True)
    p.add_argument("--state-file", type=Path, required=True)
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--route-id", required=True)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--cdp-port", type=int, choices=(9333, 9444), default=9333)
    a = p.parse_args(argv)
    if not 1024 <= a.port <= 65535:
        p.error("Invalid local port")
    root = a.checkout.resolve(strict=True)
    if not root.is_dir():
        p.error("Invalid checkout")
    panel = WakePanel(
        store=TurnSessionStore(path=a.state_file, repository_root=root),
        route_reader=lambda: load_registered_probe_route(
            registry_path=a.registry, checkout=root, route_id=a.route_id),
        cdp_port=a.cdp_port)
    server = ThreadingHTTPServer(("127.0.0.1", a.port),
        make_handler(panel, port=a.port, token=secrets.token_urlsafe(32)))
    print("WAKE_UI_LOCAL_ONLY http://127.0.0.1:" + str(a.port), flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
