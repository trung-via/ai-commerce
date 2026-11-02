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
from .cdp_doorbell import _PlaywrightInventory
from .conversation_binding import ResolvedBinding
from .plugin_selection_probe_cli import load_registered_probe_route
from .plugin_turn_store import TurnSessionStore

_ROLES = ("BRAIN", "ENGINEERING", "BRAIN")
_GOOD = frozenset(("EXACT_CHAT_OPENED", "EXACT_CHAT_PRESENT"))


def _tcp_9333() -> bool:
    # TCP availability is NOT proof the server is an authenticated Chrome.
    try:
        with socket.create_connection(("127.0.0.1", 9333), timeout=0.3):
            return True
    except OSError:
        return False


class WakePanel:
    def __init__(self, *, store, route_reader, tcp_probe=_tcp_9333,
                 inventory_factory=_PlaywrightInventory):
        self.store, self.route_reader = store, route_reader
        self.tcp_probe, self.inventory_factory = tcp_probe, inventory_factory
        self._lock = threading.Lock()
        self._uncertain = False

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
                u.hostname not in ("127.0.0.1", "localhost") or u.port != 9333):
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
        enabled = phase == "READY" and route and tcp and not self._uncertain
        if phase == "SUBMISSION_UNKNOWN":
            next_action = "RECONCILE_ONLY_NO_RETRY"
        elif phase == "COMPLETE_UI_ONLY":
            next_action = "SESSION_FINISHED_UI_ONLY"
        elif phase != "READY":
            next_action = "PRIVATE_LEDGER_UNAVAILABLE"
        elif not route:
            next_action = "CHECK_ENROLLED_ROUTE"
        elif not tcp:
            next_action = "CHECK_CHROME_9333"
        elif self._uncertain:
            next_action = "INSPECT_NAVIGATION_UNKNOWN"
        else:
            next_action = "OPEN_EXACT_CHAT_THEN_PREPARE"
        return {
            "phase": phase, "turn_index": idx,
            "role": _ROLES[idx] if 0 <= idx < 3 else "NONE",
            "route": "ENROLLED" if route else "UNAVAILABLE",
            "tcp": "REACHABLE_UNVERIFIED" if tcp else "NOT_REACHABLE",
            "next_action": next_action, "can_open": enabled,
            "can_send": False, "host_selection_proven": False,
            "server_receipt_proven": False,
        }

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
            if code in ("PRELOAD_NAVIGATION_UNKNOWN", "PRELOAD_CDP_UNAVAILABLE"):
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
button:focus-visible{outline:3px solid #ffd080;outline-offset:3px}
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
<small>TCP không chứng minh danh tính Chrome.</small></section>
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
<button id="refresh" class="secondary">Làm mới</button></div>
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
OPEN_EXACT_CHAT_THEN_PREPARE:"Mở đúng chat → Prepare @ plugin"};
function display(x){return label[x]||"Chưa rõ"}
async function refresh(){try{
const r=await fetch("/api/status",{cache:"no-store"});if(!r.ok)throw Error();
const s=await r.json();
el("tcp").textContent=display(s.tcp);el("route").textContent=display(s.route);
el("phase").textContent=display(s.phase);el("next").textContent=display(s.next_action);
el("open").disabled=!s.can_open;
[...el("steps").children].forEach((n,i)=>{
n.classList.toggle("active",s.turn_index===i&&s.phase==="READY");
n.classList.toggle("done",s.turn_index>i);});
}catch(e){el("next").textContent="Không đọc được trạng thái";el("open").disabled=true;}}
el("refresh").onclick=refresh;
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
                html = _HTML.replace("__TOKEN__", json.dumps(token))
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if self.path == "/api/status":
                return self._json(200, panel.status())
            return self._json(404, {"code": "NOT_FOUND"})

        def do_POST(self):
            if self.headers.get("Host") != host or self.headers.get("Origin") != origin:
                return self._json(403, {"code": "ORIGIN_REJECTED"})
            if not secrets.compare_digest(self.headers.get("X-Wake-Token", ""), token):
                return self._json(403, {"code": "TOKEN_REJECTED"})
            if self.path != "/api/open-chat" or self.headers.get("Content-Length") not in ("0", None):
                return self._json(400, {"code": "OPERATION_NOT_ALLOWED"})
            return self._json(200, panel.open_exact_chat())
    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(description="Human-clicked exact chat UI (no Send)")
    p.add_argument("--checkout", type=Path, required=True)
    p.add_argument("--state-file", type=Path, required=True)
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--route-id", required=True)
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args(argv)
    if not 1024 <= a.port <= 65535:
        p.error("Invalid local port")
    root = a.checkout.resolve(strict=True)
    if not root.is_dir():
        p.error("Invalid checkout")
    panel = WakePanel(
        store=TurnSessionStore(path=a.state_file, repository_root=root),
        route_reader=lambda: load_registered_probe_route(
            registry_path=a.registry, checkout=root, route_id=a.route_id))
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
