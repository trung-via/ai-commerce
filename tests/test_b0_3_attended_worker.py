"""Synthetic Windows subprocess/FFmpeg proof, never a live PULSE installation.

All inputs, prompts, process fault hooks and audit logs live in pytest temp.
The fixture sitecustomize audits the real `python -m ...attended_cli` process;
it is not an in-process CLI substitute and adds no runtime fault switches.
"""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import zlib

import pytest

from ai_commerce.production_runtime import attended_worker as worker
from ai_commerce.production_runtime.contracts import canonical_bytes, draft_authorization, parse_contract, seal_contract
from ai_commerce.production_runtime.journal import EffectTarget, Journal, JournalHold, Lease
from ai_commerce.production_runtime.recovery import recover
from ai_commerce.production_runtime.receipt import ReceiptHold, parse_packet, put_new
from ai_commerce.production_runtime.state_machine import Event, EventKind

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "-m", "ai_commerce.production_runtime.attended_cli"]
PRIVATE_TEXT = "SYNTHETIC_PRIVATE_BRIEF_đừng_xuất_customer_fixture"


def png(red):
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + bytes((red, 20, 180)) * 16) * 16)) + chunk(b"IEND", b""))


SITE = '''
import os,sys,json,socket,subprocess
from pathlib import Path
audit=Path(os.environ["B03_TEST_AUDIT"])
def record(value):
    with audit.open("a",encoding="utf-8") as f: f.write(json.dumps(value)+"\\n")
def deny(*a,**k):
    record({"denied_network":True})
    raise RuntimeError("TEST_NETWORK_DENIED")
socket.create_connection=deny
socket.getaddrinfo=deny
for n in ("connect","connect_ex","sendto"): setattr(socket.socket,n,deny)
allowed={str(Path(os.environ[n]).resolve()).lower() for n in ("B03_FFMPEG","B03_FFPROBE")}
approved=[]
real_popen=subprocess.Popen
class CheckedPopen(real_popen):
    def __init__(self,argv,*args,**kwargs):
        if (not isinstance(argv,(tuple,list)) or not argv or kwargs.get("shell",False)
            or str(Path(argv[0]).resolve()).lower() not in allowed
            or (kwargs.get("executable") is not None and
                str(Path(kwargs["executable"]).resolve()).lower()!=str(Path(argv[0]).resolve()).lower())):
            record({"denied_process":True}); raise RuntimeError("TEST_PROCESS_DENIED")
        approved.append((subprocess.list2cmdline(argv),list(argv)))
        record({"media_process":Path(argv[0]).stem.lower(),"decode":"-xerror" in argv})
        super().__init__(argv,*args,**kwargs)
subprocess.Popen=CheckedPopen
def audit_event(name,args):
    if name.startswith("socket.") and name not in {"socket.__new__"}: deny()
    if name=="subprocess.Popen":
        exe,argv,cwd,env=args
        # CPython Windows converts an argv list to a command-line string before
        # its audit event. Accept only the exact prechecked conversion, with
        # shell=False and a pinned local executable; never parse arbitrary text.
        if not approved:
            record({"denied_process":True}); raise RuntimeError("TEST_PROCESS_DENIED")
        cmdline,original=approved.pop(0)
        if ((argv!=cmdline if isinstance(argv,str) else list(argv)!=original)
            or (exe is not None and str(Path(exe).resolve()).lower()!=str(Path(original[0]).resolve()).lower())):
            record({"denied_process":True}); raise RuntimeError("TEST_PROCESS_DENIED")
sys.addaudithook(audit_event)
from ai_commerce.production_runtime import attended_worker as w
real=w.service.run
def counted(*a,**k):
    record({"render_call":True,"pid":os.getpid()})
    return real(*a,**k)
w.service.run=counted
def cut(name):
    if name==os.environ.get("B03_TEST_CUT"):
        print("B03_CUT="+name,flush=True)
        sys.stdin.readline()
w._step=cut
record({"python_pid":os.getpid()})
'''


@pytest.fixture
def case(tmp_path):
    asset_dir, private, exports, hooks = (tmp_path / x for x in ("assets", "private", "export", "hooks"))
    for path in (asset_dir, private, exports, hooks):
        path.mkdir()
    assets, selections = [], []
    for index, red in enumerate((40, 200)):
        image = asset_dir / f"synthetic-{index}.png"
        image.write_bytes(png(red))
        digest = sha256(image.read_bytes()).hexdigest()
        ref = "asset:" + digest
        selections.append(ref + "=" + str(image))
        assets.append({"ref": ref, "sha256": digest, "evidence_type": "SYNTHETIC_FIXTURE",
                       "rights": {"status": "NOT_APPLICABLE", "ref": None, "sha256": None}})
    body = {"schema_version": "CreativeDirective.v1", "journey_id": "journey:" + "1" * 32,
        "source_sha": worker.SOURCE_SHA, "directive_version": 1,
        "creative": {"freeform": PRIVATE_TEXT, "product_ref": None},
        "scope": {"action_class": "EXPORT_DRAFT", "provider_ref": None,
                  "account_ref": None, "surface": None, "asset_sha256": None},
        "assets": assets, "action_classes": ["EXPORT_DRAFT"], "allowed_tools": [worker.TOOL],
        "provenance": {"actor": "FIXTURE", "actor_ref": "fixture:" + "2" * 32,
                       "evidence_type": "SYNTHETIC_FIXTURE", "source_ref": "local:" + "3" * 32,
                       "source_sha": worker.SOURCE_SHA}, "authorization": draft_authorization()}
    directive = tmp_path / "directive.json"
    directive.write_bytes(seal_contract("CreativeDirective", body).raw)
    (hooks / "sitecustomize.py").write_text(SITE, encoding="utf-8")
    audit = tmp_path / "audit.jsonl"
    env = os.environ.copy()
    env.update(PYTHONPATH=os.pathsep.join((str(hooks), str(ROOT / "src"))),
        B03_TEST_AUDIT=str(audit), B03_FFMPEG=shutil.which("ffmpeg") or "UNAVAILABLE",
        B03_FFPROBE=shutil.which("ffprobe") or "UNAVAILABLE")
    env.pop("B03_TEST_CUT", None)
    args = {"directive_path": str(directive), "assets": tuple(selections), "rights": (),
        "asset_root": str(asset_dir), "source_sha": worker.SOURCE_SHA, "human_attended": True}
    return {"root": tmp_path, "private": private, "exports": exports, "body": body,
            "args": args, "env": env, "audit": audit}


def arguments(case, command, extra=()):
    arg = case["args"]
    result = [command, "--human-attended", "--directive", arg["directive_path"],
        "--source-sha", arg["source_sha"], "--asset-root", arg["asset_root"],
        "--private-dir", str(case["private"])]
    for value in arg["assets"]:
        result += ["--asset", value]
    for value in arg["rights"]:
        result += ["--rights", value]
    if command == "export":
        result += ["--destination", str(case["exports"])]
    return [*result, *extra]


def cli(case, command, extra=(), expected=0):
    response = subprocess.run([*CLI, *arguments(case, command, extra)], env=case["env"],
        cwd=case["root"], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert response.returncode == expected, (response.returncode, response.stdout, response.stderr)
    assert PRIVATE_TEXT not in response.stdout + response.stderr
    assert str(case["root"]) not in response.stdout + response.stderr
    return response


def audit(case):
    return [json.loads(line) for line in case["audit"].read_text().splitlines()]


def render_count(case):
    return sum(row.get("render_call", False) for row in audit(case))


def journal(case):
    intent = json.loads((case["private"] / "intent.json").read_bytes())
    return Journal.open(str(case["private"] / "journal.sqlite"), journal_id=intent["journal_id"], source_sha=worker.SOURCE_SHA)


def rewrite(case, body=None):
    Path(case["args"]["directive_path"]).write_bytes(seal_contract("CreativeDirective", body or case["body"]).raw)


def require_media():
    # Missing material prerequisites are a failed proof, never a skipped PASS.
    assert sys.platform == "win32", "MATERIAL_UNPROVEN_WINDOWS_REQUIRED"
    assert shutil.which("ffmpeg") and shutil.which("ffprobe"), "MATERIAL_UNPROVEN_FFMPEG_REQUIRED"


def test_windows_selfhost_real_separate_process_mp4_reopen_and_identical_export(case, record_property):
    require_media()
    first = cli(case, "run")
    assert json.loads(first.stdout)["state"] == "WAIT_BRAIN"
    before = journal(case).inspect()
    assert len([r for r in before.records if r.journey_id]) == 4
    assert [r.data().get("kind", r.kind) for r in before.records if r.journey_id] == [
        "JOURNEY_CREATED", "START", "OUTPUT_READY", "QA_PASSED"]
    assert json.loads(cli(case, "inspect").stdout)["journal_sequence"] == 4
    cli(case, "resume")
    assert journal(case).inspect() == before
    first_export = json.loads(cli(case, "export").stdout)
    saved = {p.name: p.read_bytes() for p in case["exports"].iterdir()}
    second_export = json.loads(cli(case, "export").stdout)
    assert first_export == second_export
    assert saved == {p.name: p.read_bytes() for p in case["exports"].iterdir()}
    receipt = parse_contract("ProductJourneyReceipt", saved["ProductJourneyReceipt.v1.json"]).to_dict()
    packet = parse_packet(saved["attended-evidence.json"])
    assert receipt["brain_decision"] is None and receipt["effect"]["status"] == "NONE"
    assert receipt["state"] == "WAIT_BRAIN" and receipt["authorization"] == draft_authorization()
    assert all(p["evidence_type"] == "SYNTHETIC_FIXTURE" for p in receipt["owner_evidence_refs"])
    assert packet["local_artifact"]["ffprobe_full_decode"] is True
    assert packet["local_artifact"]["origin"] == "LOCAL_IMPORT"
    assert packet["provider_network_calls"] == packet["paid_generation_calls"] == packet["publish_calls"] == 0
    assert before.records[1].data()["product_ref"] is None
    assert render_count(case) == 1
    assert len({row["python_pid"] for row in audit(case) if "python_pid" in row}) == 5
    assert any(row.get("media_process") == "ffprobe" for row in audit(case))
    assert any(row.get("media_process") == "ffmpeg" and row["decode"] for row in audit(case))
    assert not any(row.get("denied_network") or row.get("denied_process") for row in audit(case))
    for raw in [*saved.values(), (case["private"] / "journal.sqlite").read_bytes(),
                (case["private"] / "intent.json").read_bytes()]:
        assert PRIVATE_TEXT.encode() not in raw and str(case["root"]).encode() not in raw
    for name in ("artifact_sha256", "manifest_sha256", "tree_sha256"):
        record_property(name, packet["local_artifact"][name])
    record_property("receipt_file_sha256", first_export["receipt_file_sha256"])
    record_property("packet_sha256", first_export["packet_sha256"])
    record_property("journal_sequence", 4)
    record_property("independent_python_processes", 5)
    record_property("ffprobe_full_decode", True)


def stopped(case, cut):
    env = {**case["env"], "B03_TEST_CUT": cut}
    process = subprocess.Popen([*CLI, *arguments(case, "run")], env=env, cwd=case["root"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        marker = pool.submit(process.stdout.readline).result(timeout=50)
        assert marker.strip() == "B03_CUT=" + cut, marker
    except BaseException:
        process.kill()
        process.communicate(timeout=5)
        raise
    finally:
        pool.shutdown(wait=False)
    return process


@pytest.mark.parametrize("cut", ["before_start", "after_start", "after_a06", "after_output", "after_wait"])
def test_real_process_kill_at_commit_cuts_never_rerenders(case, cut, record_property):
    require_media()
    process = stopped(case, cut)
    process.kill()
    process.communicate(timeout=5)
    before = journal(case).inspect()
    cli(case, "run", expected=20)
    if cut in {"before_start", "after_start"}:
        cli(case, "resume", extra=("--reconcile-stopped-generation", "1"), expected=20)
        assert journal(case).inspect() == before
        assert render_count(case) == 0
        assert not list(case["private"].glob("e01-*"))
    else:
        if cut != "after_wait":
            cli(case, "resume", expected=20)  # No implicit abandoned lease takeover.
            assert journal(case).inspect() == before
        cli(case, "resume", extra=("--reconcile-stopped-generation", "1"))
        assert render_count(case) == 1
        cli(case, "export")
        assert len([r for r in journal(case).inspect().records if r.journey_id]) == 4
        assert json.loads(cli(case, "inspect").stdout)["state"] == "WAIT_BRAIN"
    record_property("actual_kill_cut", cut)
    record_property("render_calls", render_count(case))
    record_property("terminated_process_exit", process.returncode)


def test_two_genuine_processes_conflict_while_first_has_lease(case):
    process = stopped(case, "before_start")
    try:
        before = journal(case).inspect()
        assert "PROCESS_LOCK_BUSY" in cli(case, "run", expected=20).stderr
        assert "PROCESS_LOCK_BUSY" in cli(case, "resume", expected=20).stderr
        assert journal(case).inspect() == before
    finally:
        process.kill()
        process.communicate(timeout=5)


@pytest.mark.parametrize("mutation", ["digest", "json", "tool", "extra-tool", "publish", "generate",
    "source", "provenance", "private-source-ref", "brain-identity", "authorization", "unselected-asset"])
def test_invalid_ingress_never_calls_a06_or_creates_journal(case, mutation):
    body = deepcopy(case["body"])
    path = Path(case["args"]["directive_path"])
    if mutation == "digest":
        value = json.loads(path.read_bytes())
        value["creative"]["freeform"] = "Changed"
        path.write_bytes(canonical_bytes(value))
    elif mutation == "json":
        path.write_bytes(b'{"duplicate":1,"duplicate":2}')
    elif mutation == "tool":
        body["allowed_tools"] = ["tool:arbitrary-executable"]
    elif mutation == "extra-tool":
        body["allowed_tools"].append("tool:online-provider")
    elif mutation in {"publish", "generate"}:
        body["scope"]["action_class"] = "PUBLISH" if mutation == "publish" else "GENERATE_MEDIA"
        body["action_classes"] = [body["scope"]["action_class"]]
    elif mutation == "source":
        body["source_sha"] = body["provenance"]["source_sha"] = "0" * 40
    elif mutation == "provenance":
        body["provenance"]["actor"] = "UNKNOWN"
    elif mutation == "private-source-ref":
        body["provenance"]["source_ref"] = "customer:PRIVATE_CUSTOMER_SECRET"
    elif mutation == "brain-identity":
        body["provenance"].update(actor="CHATGPT_BRAIN", evidence_type="HUMAN_SUPPLIED")
    elif mutation == "authorization":
        value = json.loads(path.read_bytes())
        value["authorization"]["allow_network"] = True
        path.write_bytes(canonical_bytes(value))
    elif mutation == "unselected-asset":
        case["args"]["assets"] = case["args"]["assets"][:1]
    if mutation not in {"digest", "json", "authorization"}:
        rewrite(case, body)
    cli(case, "run", expected=20)
    assert not list(case["private"].iterdir())
    assert render_count(case) == 0


@pytest.mark.parametrize("bad", ["relative.png", r"\\server\share\asset.png", r"\\?\C:\asset.png",
    r"\\.\NUL", "C:/NUL.png", "C:/COM1/x.png", "C:/a/../b.png", "C:/a.png:stream", "C:/trailing./a.png"])
def test_ambiguous_asset_paths_hold_without_effect(case, bad):
    ref = case["args"]["assets"][0].split("=", 1)[0]
    case["args"]["assets"] = (ref + "=" + bad, case["args"]["assets"][1])
    cli(case, "run", expected=20)
    assert render_count(case) == 0 and not list(case["private"].iterdir())


def test_asset_path_escape_denied(case):
    original = case["args"]["assets"]
    ref, image = original[0].split("=", 1)
    other = case["root"] / "escape.png"
    other.write_bytes(Path(image).read_bytes())
    case["args"]["assets"] = (ref + "=" + str(other), original[1])
    cli(case, "run", expected=20)
    assert render_count(case) == 0


def test_real_windows_junction_denied_when_host_permits(case):
    assert sys.platform == "win32", "MATERIAL_UNPROVEN_WINDOWS_REQUIRED"
    import _winapi
    linked = case["root"] / "junction"
    try:
        _winapi.CreateJunction(case["args"]["asset_root"], str(linked))
    except OSError:
        pytest.skip("UNPROVEN_NATIVE_JUNCTION: host denies creation; lexical escape and structural link guard tested separately")
    original = case["args"]["assets"]
    ref, image = original[0].split("=", 1)
    case["args"]["assets"] = (ref + "=" + str(linked / Path(image).name), original[1])
    cli(case, "run", expected=20)
    assert render_count(case) == 0


def test_actual_file_symlink_when_host_permits(case):
    ref, image = case["args"]["assets"][0].split("=", 1)
    link = Path(case["args"]["asset_root"]) / "linked.png"
    try:
        link.symlink_to(image)
    except OSError:
        pytest.skip("UNPROVEN_NATIVE_FILE_SYMLINK: host denies creation; structural link guard tested separately")
    case["args"]["assets"] = (ref + "=" + str(link), case["args"]["assets"][1])
    cli(case, "run", expected=20)
    assert render_count(case) == 0


def test_structural_symlink_and_reparse_attribute_guards(case, monkeypatch):
    image = Path(case["args"]["assets"][0].split("=", 1)[1])
    original = Path.is_symlink
    with monkeypatch.context() as patch:
        patch.setattr(Path, "is_symlink", lambda p: p == image or original(p))
        with pytest.raises(worker.WorkerHold, match="PATH_LINK_DENIED"):
            worker._local(str(image))
    # Covers the Python 3.11 fallback without asserting native link creation.
    from types import SimpleNamespace
    original_lstat = Path.lstat
    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", lambda p, **kw: SimpleNamespace(st_mode=original_lstat(p, **kw).st_mode,
            st_file_attributes=0x400) if p == image else original_lstat(p, **kw))
        patch.setattr(Path, "is_symlink", lambda p: False if p == image else original(p))
        patch.setattr(Path, "is_junction", lambda p: False, raising=False)
        with pytest.raises(worker.WorkerHold, match="PATH_REPARSE_DENIED"):
            worker._local(str(image))


@pytest.mark.parametrize("mutation", ["asset", "directive", "directive-file-bytes", "intent-source", "journal-corrupt", "journal-rollback", "output-missing", "output-partial", "output-corrupt", "output-foreign"])
def test_post_run_tamper_or_rollback_holds_without_rerender(case, mutation):
    require_media()
    cli(case, "run")
    render = next(case["private"].glob("e01-*"))
    if mutation == "asset":
        Path(case["args"]["assets"][0].split("=", 1)[1]).write_bytes(png(99))
    elif mutation == "directive":
        body = deepcopy(case["body"])
        body["creative"]["freeform"] = "Other directive"
        rewrite(case, body)
    elif mutation == "directive-file-bytes":
        path = Path(case["args"]["directive_path"])
        path.write_bytes(path.read_bytes() + b"\n")
    elif mutation == "intent-source":
        path = case["private"] / "intent.json"
        value = json.loads(path.read_bytes())
        value["runtime_sha256"] = "0" * 64
        path.write_bytes(canonical_bytes(value))
    elif mutation == "journal-corrupt":
        (case["private"] / "journal.sqlite").write_bytes(b"foreign journal")
    elif mutation == "journal-rollback":
        j = journal(case)
        # Exact old, internally valid database with independently retained tail anchors.
        other = case["root"] / "old.sqlite"
        Journal.create(str(other), journal_id=j.journal_id, source_sha=worker.SOURCE_SHA)
        shutil.copyfile(other, j.path)
    elif mutation == "output-missing":
        (render / "video.mp4").unlink()
    elif mutation == "output-partial":
        (render / "delivery.json").unlink()
    elif mutation == "output-corrupt":
        with (render / "video.mp4").open("ab") as stream:
            stream.write(b"corruption")
    else:
        request = render / "custody" / "e01" / "request.json"
        request.write_bytes(b"{}")
    before = (case["private"] / "journal.sqlite").read_bytes()
    cli(case, "resume", expected=20)
    cli(case, "export", expected=20)
    assert (case["private"] / "journal.sqlite").read_bytes() == before
    assert render_count(case) == 1


def test_stale_fence_and_exact_lease_reconciliation(case):
    process = stopped(case, "after_output")
    process.kill()
    process.communicate(timeout=5)
    j = journal(case)
    before = j.inspect()
    cli(case, "resume", extra=("--expected-generation", "999"), expected=20)
    cli(case, "resume", extra=("--reconcile-stopped-generation", "999"), expected=20)
    assert j.inspect() == before
    cli(case, "resume", extra=("--reconcile-stopped-generation", "1"))
    with pytest.raises(JournalHold):
        j.release(Lease(before.lease.owner, 1, before.lease.expires_at), at=int(time.time()), event_id="lease:stale")
    assert render_count(case) == 1


@pytest.mark.parametrize("damage", ["missing", "partial", "corrupt"])
def test_killed_after_a06_with_ambiguous_package_holds_without_mutation(case, damage):
    process = stopped(case, "after_a06")
    process.kill()
    process.communicate(timeout=5)
    render = next(case["private"].glob("e01-*"))
    if damage == "missing":
        (render / "video.mp4").unlink()
    elif damage == "partial":
        (render / "delivery.json").unlink()
    else:
        (render / "video.mp4").write_bytes(b"corrupt")
    before = journal(case).inspect()
    assert "PENDING_OFFLINE_EFFECT_RECONCILIATION" in cli(case, "resume",
        extra=("--reconcile-stopped-generation", "1"), expected=20).stderr
    assert journal(case).inspect() == before and render_count(case) == 1


def test_foreign_or_missing_journal_is_never_created_by_resume(case):
    cli(case, "resume", expected=20)
    assert not list(case["private"].iterdir())
    (case["private"] / "journal.sqlite").write_bytes(b"foreign")
    before = (case["private"] / "journal.sqlite").read_bytes()
    cli(case, "inspect", expected=20)
    cli(case, "run", expected=20)
    assert (case["private"] / "journal.sqlite").read_bytes() == before


def test_export_conflict_and_atomic_concurrent_byte_identical_publication(case):
    cli(case, "run")
    cli(case, "export")
    target = case["exports"] / "attended-evidence.json"
    target.write_bytes(b"divergent packet")
    before = (case["exports"] / "ProductJourneyReceipt.v1.json").read_bytes()
    cli(case, "export", expected=20)
    assert target.read_bytes() == b"divergent packet"
    assert (case["exports"] / "ProductJourneyReceipt.v1.json").read_bytes() == before
    atomic = case["exports"] / "atomic.json"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(put_new, atomic, b"identical") for _ in range(2)]
        for future in futures:
            future.result(timeout=5)
    with pytest.raises(ReceiptHold):
        put_new(atomic, b"different")
    assert atomic.read_bytes() == b"identical"


@pytest.mark.parametrize("mode", ["missing", "expired", "wrong-hash", "valid"])
def test_human_import_requires_matching_local_rights_with_freshness(case, mode):
    body = deepcopy(case["body"])
    body["provenance"].update(actor="HUMAN", actor_ref="human:" + "4" * 32, evidence_type="HUMAN_SUPPLIED")
    right_args = []
    for index, asset in enumerate(body["assets"]):
        asset["evidence_type"] = "HUMAN_SUPPLIED"
        claim = {"schema_version": "LocalStillRights.v1", "asset_sha256": asset["sha256"],
            "source_sha": worker.SOURCE_SHA, "scope": "OFFLINE_LOCAL_RENDER", "status": "GRANTED",
            "valid_from": 0, "expires_at": 1 if mode == "expired" else int(time.time()) + 300}
        raw = canonical_bytes(claim)
        path = Path(case["args"]["asset_root"]) / f"rights-{index}.json"
        path.write_bytes(raw)
        digest = sha256(raw).hexdigest()
        asset["rights"] = {"status": "GRANTED", "ref": "rights:" + digest,
                           "sha256": "0" * 64 if mode == "wrong-hash" else digest}
        right_args.append("rights:" + digest + "=" + str(path))
    case["args"]["rights"] = () if mode == "missing" else tuple(right_args)
    rewrite(case, body)
    cli(case, "run", expected=0 if mode == "valid" else 20)
    assert render_count(case) == (1 if mode == "valid" else 0)
    if mode == "valid":
        cli(case, "export")
        receipt = parse_contract("ProductJourneyReceipt", (case["exports"] / "ProductJourneyReceipt.v1.json").read_bytes()).to_dict()
        assert all(pin["evidence_type"] == "HUMAN_SUPPLIED" for pin in receipt["owner_evidence_refs"])


def test_no_brain_decision_command_or_arbitrary_tool_argv(case):
    for extra in [("--brain-decision", PRIVATE_TEXT), ("--tool", "powershell"), ("--command", "cmd.exe")]:
        cli(case, "run", extra=extra, expected=20)
    assert not list(case["private"].iterdir()) and render_count(case) == 0
    assert "CLI_ARGUMENTS_INVALID" in cli(case, "brain-accept", expected=20).stderr


def test_b02_reserved_publish_unknown_is_preserved_and_not_rekeyed(case):
    # Legacy B0.2 evidence stays UNKNOWN. B0.3 denies this directive at ingress.
    body = deepcopy(case["body"])
    body["scope"] = {"action_class": "PUBLISH", "provider_ref": "provider:fixture",
        "account_ref": "account:fixture", "surface": "surface:fixture", "asset_sha256": "a" * 64}
    body["action_classes"] = ["PUBLISH"]
    directive = seal_contract("CreativeDirective", body)
    path = case["private"] / "journal.sqlite"
    j = Journal.create(str(path), journal_id="fixture:unknown", source_sha=worker.SOURCE_SHA)
    lease = j.acquire(owner="fixture:owner", at=1, ttl=100, event_id="lease:one")
    j.create_journey(directive, directive_ref="directive:fixture", lease=lease, at=2, event_id="event:input")
    from ai_commerce.production_runtime.state_machine import EvidencePin
    pin = EvidencePin("MEDIA_CREATIVE", "fixture:qa", "c" * 64, "SYNTHETIC_FIXTURE")
    for seq, kind in enumerate((EventKind.START, EventKind.OUTPUT_READY, EventKind.QA_PASSED), 1):
        j.append_event(Event("event:" + str(seq), kind, body["journey_id"], worker.SOURCE_SHA,
            tool=worker.TOOL if seq == 1 else None, evidence=() if seq == 1 else (pin,)),
            directive=directive, lease=lease, at=3, expected_sequence=seq)
    target = EffectTarget("PUBLISH", "provider:fixture", "account:fixture", "surface:fixture", "a" * 64)
    j.reserve_intent(body["journey_id"], target, directive=directive, lease=lease,
                     at=4, event_id="event:reserve", expected_sequence=4)
    rewrite(case, body)
    before = path.read_bytes()
    cli(case, "resume", expected=20)
    assert path.read_bytes() == before and render_count(case) == 0
    assert recover(str(path), journal_id=j.journal_id, source_sha=worker.SOURCE_SHA,
                   journey_id=body["journey_id"], directive=directive).state == "UNKNOWN_EFFECT"


def test_finite_literal_dependencies_and_no_dynamic_dispatch_or_authority_context():
    expected = {"attended_worker.py": {"os", "pathlib", "time", "uuid", "msvcrt"},
                "attended_cli.py": {"argparse", "sys"}, "receipt.py": {"os", "pathlib", "tempfile"}}
    for name, permitted in expected.items():
        tree = ast.parse((ROOT / "src/ai_commerce/production_runtime" / name).read_text(encoding="utf-8"))
        actual = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__":
                assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
                actual.add(node.args[0].value)
            if isinstance(node, ast.Name):
                assert node.id not in {"eval", "exec", "compile", "AuthorityReceipt", "VerificationContext"}
            if isinstance(node, ast.ImportFrom) and node.level:
                assert node.module in {"e2e", "contracts", "journal", "recovery", "state_machine", "receipt", None}
        assert actual == permitted
    source = (ROOT / "src/ai_commerce/production_runtime/attended_worker.py").read_text(encoding="utf-8")
    assert source.count("service.run(") == 1
    assert "reserve_intent(" not in source and "BRAIN_ACCEPTED" not in source
