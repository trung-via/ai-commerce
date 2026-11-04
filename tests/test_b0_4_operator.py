"""Windows synthetic subprocess evidence. No live installation/identity claim.

Fault hooks live only in temporary sitecustomize, never runtime CLI/env switches.
Every ordinary CLI is a separate actual Python process. Missing FFmpeg fails;
native link creation denial is explicitly skipped/UNPROVEN.
"""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import zlib

import pytest

from ai_commerce.production_runtime import attended_worker as w
from ai_commerce.production_runtime import handoff, metrics, operator as op
from ai_commerce.production_runtime.contracts import canonical_bytes, draft_authorization, parse_contract, seal_contract
from ai_commerce.production_runtime.receipt import parse_packet

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "-m", "ai_commerce.production_runtime.operator_cli"]
PRIVATE = "B04_SYNTHETIC_PRIVATE_PROMPT_customer_đừng_xuất_TOKEN_fixture"
# A bounded invocation gets more than B0.3's historical 60 seconds under twelve
# workers, but less than the unchanged native 300-second lease. No retry loop.
CLI_SECONDS = 180
CUT_SECONDS = 170

SITE = r'''
import os,sys,json,socket,subprocess
from pathlib import Path
audit=Path(os.environ["B04_AUDIT"])
def record(value):
    with audit.open("a",encoding="utf-8") as f: f.write(json.dumps(value)+"\n")
def deny(*a,**k):
    record({"denied_network":True}); raise RuntimeError("TEST_NETWORK_DENIED")
socket.create_connection=deny
socket.getaddrinfo=deny
for n in ("connect","connect_ex","sendto"): setattr(socket.socket,n,deny)
allowed={str(Path(os.environ[n]).resolve()).lower() for n in ("B04_FFMPEG","B04_FFPROBE")}
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
    if name.startswith("socket.") and name!="socket.__new__": deny()
    if name=="subprocess.Popen":
        exe,argv,cwd,env=args
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
    record({"render_call":True}); return real(*a,**k)
w.service.run=counted
def pause(name):
    if name==os.environ.get("B04_CUT"):
        print("B04_CUT="+name,flush=True); sys.stdin.readline()
w._step=pause
original=w.run_once
def call(*a,**k):
    record({"native_run_call":True}); pause("before_call")
    if os.environ.get("B04_EXCEPTION"): raise ValueError(os.environ["B04_EXCEPTION"])
    return original(*a,**k)
w.run_once=call
record({"python_pid":os.getpid(),"audited":True})
'''


def png(red):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + bytes((red, 70, 130)) * 16) * 16)) + chunk(b"IEND", b""))


@pytest.fixture
def case(tmp_path):
    paths = {name: tmp_path / name for name in ("operator", "private", "export", "assets", "hooks")}
    for path in paths.values():
        path.mkdir()
    assets, mappings = [], []
    for index, red in enumerate((30, 220)):
        path = paths["assets"] / f"synthetic-{index}.png"
        path.write_bytes(png(red))
        digest = sha256(path.read_bytes()).hexdigest()
        ref = "asset:" + digest
        mappings.append(ref + "=" + str(path))
        assets.append({"ref": ref, "sha256": digest, "evidence_type": "SYNTHETIC_FIXTURE",
            "rights": {"status": "NOT_APPLICABLE", "ref": None, "sha256": None}})
    body = {"schema_version": "CreativeDirective.v1", "source_sha": w.SOURCE_SHA,
        "journey_id": "journey:" + "4" * 32, "directive_version": 1,
        "creative": {"freeform": PRIVATE, "product_ref": None},
        "scope": {"action_class": "EXPORT_DRAFT", "provider_ref": None, "account_ref": None,
            "surface": None, "asset_sha256": None}, "assets": assets,
        "action_classes": ["EXPORT_DRAFT"], "allowed_tools": [w.TOOL],
        "provenance": {"actor": "FIXTURE", "actor_ref": "fixture:" + "5" * 32,
            "source_ref": "local:" + "6" * 32, "source_sha": w.SOURCE_SHA, "evidence_type": "SYNTHETIC_FIXTURE"},
        "authorization": draft_authorization()}
    directive = tmp_path / "directive.json"
    directive.write_bytes(seal_contract("CreativeDirective", body).raw)
    audit = tmp_path / "process-audit.jsonl"
    (paths["hooks"] / "sitecustomize.py").write_text(SITE, encoding="utf-8")
    env = os.environ.copy()
    env.update(PYTHONPATH=os.pathsep.join((str(paths["hooks"]), str(ROOT / "src"))),
        B04_AUDIT=str(audit), B04_FFMPEG=shutil.which("ffmpeg") or "UNAVAILABLE",
        B04_FFPROBE=shutil.which("ffprobe") or "UNAVAILABLE")
    env.pop("B04_CUT", None)
    env.pop("B04_EXCEPTION", None)
    return {**paths, "root": tmp_path, "body": body, "directive": directive,
        "mappings": mappings, "rights": [], "env": env, "audit": audit,
        "session": paths["operator"] / op.SESSION_NAME}


def args(c, command, extra=()):
    if command == "setup":
        values = [command, "--operator-dir", str(c["operator"]), "--private-dir", str(c["private"]),
            "--export-dir", str(c["export"]), "--directive", str(c["directive"]),
            "--source-sha", w.SOURCE_SHA, "--asset-root", str(c["assets"])]
        for mapping in c["mappings"]:
            values += ["--asset", mapping]
        for right in c["rights"]:
            values += ["--rights", right]
    else:
        values = [command, "--session", str(c["session"])]
        if command in {"run", "reconcile"}:
            values += ["--human-attended"]
    return [*values, *extra]


def cli(c, command, extra=(), expected=0, *, env=None):
    result = subprocess.run([*CLI, *args(c, command, extra)], cwd=c["root"], env=env or c["env"],
        capture_output=True, text=True, encoding="utf-8", timeout=CLI_SECONDS)
    assert result.returncode == expected, (result.returncode, result.stdout, result.stderr)
    text = result.stdout + result.stderr
    assert PRIVATE not in text and str(c["root"]) not in text
    assert not result.stderr, "CLI unexpectedly emitted raw diagnostic text"
    return json.loads(result.stdout)


def setup(c):
    assert cli(c, "setup")["state"] == "SETUP"
    assert not any(c["private"].iterdir()) and not any(c["export"].iterdir())


def media():
    assert sys.platform == "win32", "UNPROVEN_WINDOWS_REQUIRED"
    assert shutil.which("ffmpeg") and shutil.which("ffprobe"), "UNPROVEN_MATERIAL_FFMPEG_REQUIRED"


def audit(c):
    return [json.loads(line) for line in c["audit"].read_text().splitlines()]


def count(c, name):
    return sum(bool(row.get(name)) for row in audit(c))


def journal_bytes(c):
    return (c["private"] / "journal.sqlite").read_bytes()


def native(c):
    session, _, root, directive, intent, inputs = op.load(str(c["session"]))
    return w._read(root, directive, intent)


def trail(c):
    session = w._json(c["session"])
    return metrics.read(c["operator"], session["session_sha256"])


def rewrite_directive(c):
    c["directive"].write_bytes(seal_contract("CreativeDirective", c["body"]).raw)


def stopped(c, cut):
    process = subprocess.Popen([*CLI, *args(c, "run")], cwd=c["root"],
        env={**c["env"], "B04_CUT": cut}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8")
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        line = pool.submit(process.stdout.readline).result(timeout=CUT_SECONDS)
        assert line.strip() == "B04_CUT=" + cut, line
    except BaseException:
        process.kill()
        process.communicate(timeout=10)
        raise
    finally:
        pool.shutdown(wait=False)
    return process


def kill(process):
    process.kill()
    process.communicate(timeout=10)
    assert process.returncode != 0


def test_windows_independent_cli_session_handoff_learning_metrics(case, record_property):
    media()
    setup(case)
    assert cli(case, "status")["state"] == "READY"
    assert cli(case, "run")["state"] == "WAIT_BRAIN"
    before = journal_bytes(case)
    _, history, result = native(case)
    assert len(result.event_sequence) == 4
    assert [r.data().get("kind", r.kind) for r in history.records if r.journey_id] == [
        "JOURNEY_CREATED", "START", "OUTPUT_READY", "QA_PASSED"]
    assert [r for r in history.records if r.journey_id][0].data()["product_ref"] is None
    assert cli(case, "status")["state"] == "WAIT_BRAIN"
    exported = cli(case, "handoff")
    saved = {p.name: p.read_bytes() for p in case["export"].iterdir()}
    assert set(saved) == {handoff.RECEIPT, handoff.PACKET, handoff.SUMMARY}
    assert cli(case, "handoff") == exported
    assert saved == {p.name: p.read_bytes() for p in case["export"].iterdir()}
    receipt = parse_contract("ProductJourneyReceipt", saved[handoff.RECEIPT]).to_dict()
    packet = parse_packet(saved[handoff.PACKET])
    summary = json.loads(saved[handoff.SUMMARY])
    assert receipt["brain_decision"] is None and receipt["authorization"] == draft_authorization()
    assert receipt["state"] == "WAIT_BRAIN" and receipt["effect"]["status"] == "NONE"
    assert summary["provenance"] == "SYNTHETIC_FIXTURE"
    assert summary["flags"] == ["SANITIZED", "NOT_AUTHENTICATED", "NOT_PUBLISHED", "NO_PROVIDER_EFFECT"]
    assert summary["receipt_file_sha256"] == sha256(saved[handoff.RECEIPT]).hexdigest()
    assert summary["companion_file_sha256"] == sha256(saved[handoff.PACKET]).hexdigest()
    assert packet["local_artifact"]["ffprobe_full_decode"] is True
    assert cli(case, "learning-template")["state"] == "WAIT_BRAIN"
    learning = (case["export"] / handoff.LEARNING).read_bytes()
    assert json.loads(learning)["observations"] is None
    assert json.loads(learning)["status"] == "INERT_UNFILLED_PROPOSAL"
    assert cli(case, "run")["state"] == "WAIT_BRAIN"  # inspection, no native run again
    observed = cli(case, "metrics")["metrics"]
    assert observed["incomplete_unknown"] == 0
    assert observed["coding_agent_calls"]["observed"] == 0
    assert observed["coding_agent_calls"]["outside_boundary"] == "UNKNOWN_NOT_INSTRUMENTED"
    assert observed["active_invocation_ns"] >= 0 and observed["elapsed_wall_ns"] >= 0
    assert journal_bytes(case) == before and count(case, "render_call") == count(case, "native_run_call") == 1
    rows = audit(case)
    assert len({r["python_pid"] for r in rows if "python_pid" in r}) == 9
    assert any(r.get("media_process") == "ffprobe" for r in rows)
    assert any(r.get("media_process") == "ffmpeg" and r["decode"] for r in rows)
    assert not any(r.get("denied_process") or r.get("denied_network") for r in rows)
    for raw in [*saved.values(), learning, (case["operator"] / "audit.jsonl").read_bytes()]:
        assert PRIVATE.encode() not in raw and str(case["root"]).encode() not in raw
        assert b"chat_ref" not in raw and b"receipt_ref" not in raw
    for key in ("artifact_sha256", "manifest_sha256", "tree_sha256"):
        record_property(key, packet["local_artifact"][key])
    for key, value in exported.items():
        if key.endswith("sha256"):
            record_property(key, value)
    record_property("independent_python_processes", 9)
    record_property("render_calls", 1)
    record_property("journal_sequence", 4)
    record_property("ffprobe_full_decode", True)
    record_property("network_git_coding_agent_calls_observed_in_boundary", 0)


@pytest.mark.parametrize("cut", ["before_call", "before_start", "after_start", "after_a06", "after_output", "after_wait"])
def test_actual_process_kill_preserves_attempt_and_never_rerenders(case, cut, record_property):
    media()
    setup(case)
    process = stopped(case, cut)
    kill(process)
    trail_before = trail(case)
    assert trail_before[-1]["phase"] == "ATTEMPT"
    before = journal_bytes(case) if cut != "before_call" else None
    # A missing terminal is permanently UNKNOWN; successful recovery is a new observation.
    snapshot = cli(case, "metrics", expected=20 if cut in {"before_start", "after_start", "after_a06", "after_output"} else 21)
    assert snapshot["metrics"]["incomplete_unknown"] == 1
    expected = 21 if cut == "before_call" else 20 if cut in {"before_start", "after_start", "after_a06", "after_output"} else 0
    state = cli(case, "run", expected=expected)
    assert state["state"] in {"HOLD", "UNKNOWN", "WAIT_BRAIN"}
    if before is not None:
        assert journal_bytes(case) == before
    if cut in {"after_a06", "after_output", "after_wait"}:
        _, history, _ = native(case)
        generation = history.checkpoint.generation
        assert cli(case, "reconcile", ["--stopped-generation", str(generation)])["state"] == "WAIT_BRAIN"
        cli(case, "handoff")
        assert len(native(case)[2].event_sequence) == 4
        observed = cli(case, "metrics", expected=21)["metrics"]
        assert observed["recovery_outcomes"]["PASS"] == 1
        assert observed["incomplete_unknown"] == 1
    else:
        assert not any(case["export"].iterdir())
    assert count(case, "native_run_call") == 1
    assert count(case, "render_call") == (1 if cut in {"after_a06", "after_output", "after_wait"} else 0)
    record_property("actual_kill_cut", cut)
    record_property("render_calls", count(case, "render_call"))
    record_property("terminated_process_exit", process.returncode)


def test_two_real_processes_hold_without_lease_deletion(case, record_property):
    media()
    setup(case)
    first = stopped(case, "after_start")
    try:
        before = journal_bytes(case)
        assert cli(case, "run", expected=20)["code"] == "LEASE_OR_PROCESS_HOLD"
        assert cli(case, "status", expected=20)["code"] == "LEASE_OR_PROCESS_HOLD"
        assert journal_bytes(case) == before
    finally:
        kill(first)
    assert count(case, "native_run_call") == 1 and count(case, "render_call") == 0
    record_property("actual_competing_python_processes", 2)
    record_property("journal_preserved", True)


@pytest.mark.parametrize("damage", ["missing", "partial", "tampered"])
def test_after_a06_incomplete_owner_package_holds_no_mutation(case, damage):
    media()
    setup(case)
    process = stopped(case, "after_a06")
    kill(process)
    movie = next(case["private"].glob("e01-*/video.mp4"))
    if damage == "missing":
        movie.unlink()
    elif damage == "partial":
        movie.write_bytes(movie.read_bytes()[:32])
    else:
        movie.write_bytes(movie.read_bytes() + b"tamper")
    before = journal_bytes(case)
    cli(case, "reconcile", ["--stopped-generation", "1"], expected=20)
    cli(case, "run", expected=20)
    assert before == journal_bytes(case) and count(case, "render_call") == 1


@pytest.mark.parametrize("damage", ["missing-receipt", "partial-receipt", "tampered-receipt", "missing-packet", "tampered-packet", "missing-summary", "forged-brain"])
def test_export_damage_holds_and_preserves_original(case, damage):
    media()
    setup(case)
    cli(case, "run")
    cli(case, "handoff")
    receipt = case["export"] / handoff.RECEIPT
    packet = case["export"] / handoff.PACKET
    if damage == "missing-receipt":
        receipt.unlink()
    elif damage == "partial-receipt":
        receipt.write_bytes(b"{")
    elif damage == "tampered-receipt":
        receipt.write_bytes(receipt.read_bytes() + b" ")
    elif damage == "missing-packet":
        packet.unlink()
    elif damage == "tampered-packet":
        value = json.loads(packet.read_bytes())
        value["brain_identity_authenticated"] = True
        packet.write_bytes(canonical_bytes(value))
    elif damage == "missing-summary":
        (case["export"] / handoff.SUMMARY).unlink()
    else:
        value = parse_contract("ProductJourneyReceipt", receipt.read_bytes()).to_dict()
        value.pop("receipt_sha256")
        value["brain_decision"] = {"ref": "brain:forged", "sha256": "a" * 64}
        receipt.write_bytes(seal_contract("ProductJourneyReceipt", value).raw)
    before = {p.name: p.read_bytes() for p in case["export"].iterdir()}
    journal = journal_bytes(case)
    assert cli(case, "handoff", expected=20)["code"] == "HANDOFF_HOLD"
    cli(case, "status", expected=20)
    assert before == {p.name: p.read_bytes() for p in case["export"].iterdir()}
    assert journal == journal_bytes(case) and count(case, "render_call") == 1


@pytest.mark.parametrize("damage", ["source", "directive", "asset", "checkpoint", "journal", "journal-missing", "journal-rollback", "intent", "lease"])
def test_stale_pins_and_native_history_hold_without_retry(case, damage):
    media()
    setup(case)
    cli(case, "run")
    if damage == "source":
        data = json.loads(case["session"].read_bytes())
        data["operator_baseline_sha"] = "0" * 40
        data.pop("session_sha256")
        data["session_sha256"] = sha256(canonical_bytes(data)).hexdigest()
        case["session"].write_bytes(canonical_bytes(data))
    elif damage == "directive":
        case["body"]["creative"]["freeform"] = "Changed sealed private directive"
        rewrite_directive(case)
    elif damage == "asset":
        next(case["assets"].glob("*.png")).write_bytes(png(99))
    elif damage == "checkpoint":
        next(case["private"].glob("checkpoint-*.json")).write_bytes(b"{}")
    elif damage == "journal":
        (case["private"] / "journal.sqlite").write_bytes(b"invalid sqlite private bytes")
    elif damage == "journal-missing":
        (case["private"] / "journal.sqlite").unlink()
    elif damage == "journal-rollback":
        # Valid *foreign* old native journal, with retained ahead checkpoints.
        other = case["root"] / "old.sqlite"
        w.Journal.create(str(other), journal_id=w._json(case["private"] / "intent.json")["journal_id"], source_sha=w.SOURCE_SHA)
        (case["private"] / "journal.sqlite").write_bytes(other.read_bytes())
    elif damage == "intent":
        (case["private"] / "intent.json").write_bytes(b"{}")
    else:
        before = journal_bytes(case)
        cli(case, "reconcile", ["--stopped-generation", "999"], expected=20)
        assert journal_bytes(case) == before
        return
    before = journal_bytes(case) if (case["private"] / "journal.sqlite").exists() else None
    cli(case, "run", expected=20)
    cli(case, "status", expected=20)
    assert count(case, "render_call") == 1
    assert ((case["private"] / "journal.sqlite").read_bytes() if before is not None else None) == before


@pytest.mark.parametrize("damage", ["rights-changed", "rights-expired", "rights-missing", "valid"])
def test_human_supplied_rights_revalidated_each_session_use(case, damage):
    for asset in case["body"]["assets"]:
        claim = {"schema_version": "LocalStillRights.v1", "asset_sha256": asset["sha256"],
            "source_sha": w.SOURCE_SHA, "scope": "OFFLINE_LOCAL_RENDER", "status": "GRANTED",
            "valid_from": 0, "expires_at": int(time.time()) + 3600}
        path = case["assets"] / (asset["sha256"] + ".json")
        raw = canonical_bytes(claim)
        path.write_bytes(raw)
        ref = "rights:" + sha256(raw).hexdigest()
        asset.update(evidence_type="HUMAN_SUPPLIED", rights={"status": "GRANTED", "ref": ref, "sha256": sha256(raw).hexdigest()})
        case["rights"].append(ref + "=" + str(path))
    case["body"]["provenance"].update(actor="HUMAN", evidence_type="HUMAN_SUPPLIED")
    rewrite_directive(case)
    setup(case)
    right = next(case["assets"].glob("*.json"))
    if damage == "rights-changed":
        right.write_bytes(right.read_bytes() + b" ")
    elif damage == "rights-expired":
        # A modified expired claim must also fail its retained exact-byte pin.
        claim = json.loads(right.read_bytes())
        claim["expires_at"] = 1
        right.write_bytes(canonical_bytes(claim))
    elif damage == "rights-missing":
        right.unlink()
    else:
        media()
        cli(case, "run")
        cli(case, "handoff")
        assert json.loads((case["export"] / handoff.SUMMARY).read_bytes())["provenance"] == "HUMAN_SUPPLIED"
        return
    cli(case, "status", expected=20)
    assert not any(case["private"].iterdir())


@pytest.mark.parametrize("mutation", ["expensive-provider", "unauthorized-adapter", "brain-decision", "publish", "product-id", "asset-ref"])
def test_unauthorized_directive_rejected_before_journal(case, mutation):
    if mutation == "expensive-provider":
        case["body"]["authorization"]["allow_spend"] = True
        # Structurally forbidden before a valid seal is possible.
        value = json.loads(case["directive"].read_bytes())
        value["authorization"]["allow_spend"] = True
        case["directive"].write_bytes(canonical_bytes(value))
    elif mutation == "unauthorized-adapter":
        case["body"]["allowed_tools"] = ["tool:expensive-provider"]
        rewrite_directive(case)
    elif mutation == "brain-decision":
        value = json.loads(case["directive"].read_bytes())
        value["brain_decision"] = {"verified": True}
        case["directive"].write_bytes(canonical_bytes(value))
    elif mutation == "publish":
        case["body"]["scope"]["action_class"] = "PUBLISH"
        case["body"]["action_classes"] = ["PUBLISH"]
        rewrite_directive(case)
    elif mutation == "asset-ref":
        ref = "asset:" + "f" * 64
        case["body"]["assets"][0]["ref"] = ref
        case["mappings"][0] = ref + "=" + case["mappings"][0].split("=", 1)[1]
        rewrite_directive(case)
    else:
        case["body"]["creative"]["product_ref"] = "customer:unredacted"
        rewrite_directive(case)
    cli(case, "setup", expected=20)
    assert not any(case["private"].iterdir()) and count(case, "render_call") == 0


@pytest.mark.parametrize("path", [r"\\server\share\fixture.png", "C:/Temp/../fixture.png", "C:/Temp/a.png:secret", "relative.png", "C:/Temp/NUL.png"])
def test_unc_ads_device_and_path_escape_hold(case, path):
    case["mappings"][0] = case["mappings"][0].split("=", 1)[0] + "=" + path
    cli(case, "setup", expected=20)
    assert not any(case["private"].iterdir())


def test_asset_outside_declared_root_hold(case):
    path = case["root"] / "outside.png"
    path.write_bytes(png(30))
    case["mappings"][0] = case["mappings"][0].split("=", 1)[0] + "=" + str(path)
    cli(case, "setup", expected=20)


@pytest.mark.parametrize("relation", ["operator-in-private", "export-equals-private", "private-in-repo"])
def test_private_directories_are_separate_and_outside_repo(case, relation):
    if relation == "operator-in-private":
        case["operator"] = case["private"] / "op"
        case["operator"].mkdir()
    elif relation == "export-equals-private":
        case["export"] = case["private"]
    else:
        case["private"] = ROOT / "tests"
    cli(case, "setup", expected=20)


def test_real_file_symlink_or_unproven(case, record_property):
    original = Path(case["mappings"][0].split("=", 1)[1])
    linked = case["assets"] / "linked.png"
    try:
        linked.symlink_to(original)
    except (OSError, NotImplementedError):
        pytest.skip("Host denied native symlink creation; UNPROVEN")
    case["mappings"][0] = case["mappings"][0].split("=", 1)[0] + "=" + str(linked)
    cli(case, "setup", expected=20)
    record_property("native_symlink_denied_by_operator", True)


def test_real_windows_junction_or_unproven(case, record_property):
    linked = case["root"] / "junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(case["assets"])],
        capture_output=True, timeout=15)
    if result.returncode or not linked.is_dir():
        pytest.skip("Host denied native junction creation; UNPROVEN")
    case["assets"] = linked
    cli(case, "setup", expected=20)
    record_property("native_junction_denied_by_operator", True)


def test_run_requires_real_flag_and_private_exceptions_are_redacted(case):
    setup(case)
    argv = args(case, "run")
    argv.remove("--human-attended")
    result = subprocess.run([*CLI, *argv], env=case["env"], cwd=case["root"], capture_output=True, text=True, timeout=CLI_SECONDS)
    assert result.returncode == 20 and json.loads(result.stdout)["code"] == "HUMAN_ATTENDED_REQUIRED"
    assert count(case, "native_run_call") == 0
    # Use a fresh session for the separate raw-exception projection test.
    assert cli(case, "run", expected=20)["code"] == "RUN_STOPPED_NO_RETRY"


def test_native_exception_is_bounded_and_attempt_never_retried(case):
    setup(case)
    cli(case, "run", expected=20, env={**case["env"], "B04_EXCEPTION": PRIVATE + str(case["root"])})
    assert cli(case, "run", expected=20)["code"] == "RUN_STOPPED_NO_RETRY"
    assert count(case, "native_run_call") == 1 and count(case, "render_call") == 0


@pytest.mark.parametrize("bad", ["brain-decision", "publish", "daemon", "run --provider", "status --asset"])
def test_no_unsupported_authority_provider_or_background_cli(case, bad):
    result = subprocess.run([*CLI, *bad.split(), "--session", str(case["session"]), "PRIVATE_ARG"],
        env=case["env"], cwd=case["root"], capture_output=True, text=True, timeout=CLI_SECONDS)
    assert result.returncode == 20
    assert json.loads(result.stdout)["code"] == "CLI_ARGUMENTS_INVALID"
    assert "PRIVATE_ARG" not in result.stdout + result.stderr


@pytest.mark.parametrize("damage", ["partial", "checksum", "rollback", "duplicate-attempt", "duplicate-terminal", "missing-head"])
def test_private_trail_corruption_is_hold_not_success(case, damage):
    setup(case)
    cli(case, "status")
    path = case["operator"] / "audit.jsonl"
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    if damage == "partial":
        path.write_bytes(path.read_bytes()[:-7])
    elif damage == "checksum":
        rows[0]["checksum"] = "0" * 64
        path.write_bytes(b"".join(canonical_bytes(r) + b"\n" for r in rows))
    elif damage == "rollback":
        path.write_bytes(canonical_bytes(rows[0]) + b"\n")
    elif damage == "missing-head":
        (case["operator"] / "audit-head-000002.json").unlink()
    else:
        row = deepcopy(rows[0 if damage == "duplicate-attempt" else 1])
        row.update(seq=3, event_id="f" * 32, prev_sha256=rows[-1]["checksum"])
        row.pop("checksum")
        row["checksum"] = sha256(canonical_bytes(row)).hexdigest()
        with path.open("ab") as stream:
            stream.write(canonical_bytes(row) + b"\n")
    before = path.read_bytes()
    assert cli(case, "metrics", expected=20)["state"] == "HOLD"
    assert path.read_bytes() == before and not any(case["private"].iterdir())


def test_duplicate_append_is_rejected_before_bytes_are_written(case):
    setup(case)
    cli(case, "status")
    rows = trail(case)
    session = w._json(case["session"])
    before = (case["operator"] / "audit.jsonl").read_bytes()
    with w._Lock(case["operator"]), pytest.raises(metrics.MetricsHold):
        metrics.append(case["operator"], session["session_sha256"], command="status",
            attempt_id=rows[0]["attempt_id"], phase="RESULT", outcome="PASS")
    assert before == (case["operator"] / "audit.jsonl").read_bytes()


def test_capacity_reserves_attempt_and_terminal_before_native_call(case, monkeypatch):
    setup(case)
    called = []
    monkeypatch.setattr(metrics, "MAX_RECORDS", 1)
    monkeypatch.setattr(w, "run_once", lambda **kwargs: called.append(True))
    result = op.invoke("run", session_path=str(case["session"]), human_attended=True)
    assert result["code"] == "TRAIL_HOLD" and not called
    assert (case["operator"] / "audit.jsonl").read_bytes() == b""
    assert not any(case["private"].iterdir())


def test_rehashed_session_is_not_authority_to_replace_original_paths(case):
    setup(case)
    data = json.loads(case["session"].read_bytes())
    original = Path(data["inputs"]["assets"][0].split("=", 1)[1])
    other = original.with_name("same-bytes-new-path.png")
    other.write_bytes(original.read_bytes())
    data["inputs"]["assets"][0] = data["inputs"]["assets"][0].split("=", 1)[0] + "=" + str(other)
    data.pop("session_sha256")
    data["session_sha256"] = sha256(canonical_bytes(data)).hexdigest()
    case["session"].write_bytes(canonical_bytes(data))
    assert cli(case, "status", expected=20)["code"] == "TRAIL_HOLD"
    assert not any(case["private"].iterdir())


def test_unobserved_external_b03_is_unknown_and_not_counted_pass(case, record_property):
    media()
    setup(case)
    # A real external B0.3 process after setup, outside B0.4 admission, must
    # remain unobserved even after repeated run/reconcile/handoff commands.
    session = w._json(case["session"])
    inputs = session["inputs"]
    external_args = ["run", "--human-attended", "--directive", inputs["directive_path"],
        "--source-sha", inputs["source_sha"], "--asset-root", inputs["asset_root"],
        "--private-dir", str(case["private"])]
    for mapping in inputs["assets"]:
        external_args += ["--asset", mapping]
    external = subprocess.run([sys.executable, "-m", "ai_commerce.production_runtime.attended_cli", *external_args],
        env=case["env"], cwd=case["root"], capture_output=True, text=True, timeout=CLI_SECONDS)
    assert external.returncode == 0 and json.loads(external.stdout)["state"] == "WAIT_BRAIN"
    before = journal_bytes(case)
    assert not (case["operator"] / "run-admission.json").exists()
    assert cli(case, "status", expected=21)["code"] == "UNOBSERVED_JOURNEY"
    for command in ("run", "run", "handoff", "reconcile"):
        assert cli(case, command, expected=21)["code"] == "UNOBSERVED_JOURNEY"
    value = cli(case, "metrics", expected=21)["metrics"]
    assert value["preexisting_or_external_journeys"] == "UNKNOWN"
    assert value["outcomes"]["PASS"] == 0
    assert count(case, "native_run_call") == count(case, "render_call") == 1
    assert journal_bytes(case) == before and not any(case["export"].iterdir())
    # A standalone local checksum/admission file cannot upgrade an inspection
    # attempt: its original hash-chained admission bit must also be present.
    attempts = [r for r in trail(case) if r["phase"] == "ATTEMPT" and r["command"] == "run"]
    assert attempts and not any(r["native_admission"] for r in attempts)
    (case["operator"] / "run-admission.json").write_bytes(canonical_bytes({
        "session_sha256": session["session_sha256"],
        "intent_sha256": sha256(canonical_bytes(session["intent"])).hexdigest(),
        "attempt_id": attempts[-1]["attempt_id"]}))
    cli(case, "status", expected=20)
    assert journal_bytes(case) == before and not any(case["export"].iterdir())
    record_property("actual_external_b03_state", "WAIT_BRAIN")
    record_property("b04_attributed_passes", 0)
    record_property("external_journal_preserved", True)


def test_corrupt_run_admission_holds_original_attempt(case):
    setup(case)
    process = stopped(case, "before_call")
    kill(process)
    marker = case["operator"] / "run-admission.json"
    assert marker.is_file()
    marker.write_bytes(b"{}")
    before = (case["operator"] / "audit.jsonl").read_bytes()
    cli(case, "status", expected=20)
    cli(case, "run", expected=20)
    assert (case["operator"] / "audit.jsonl").read_bytes() == before
    assert not any(case["private"].iterdir()) and count(case, "render_call") == 0


def test_monotonic_active_time_and_wall_clock_rollback_are_explicit(case):
    setup(case)
    session = w._json(case["session"])
    with w._Lock(case["operator"]):
        first = metrics.append(case["operator"], session["session_sha256"], command="reconcile",
            attempt_id="a" * 32, phase="ATTEMPT", wall_ns=20)
        metrics.append(case["operator"], session["session_sha256"], command="reconcile",
            attempt_id=first["attempt_id"], phase="RESULT", outcome="UNKNOWN", wall_ns=10,
            active_ns=3, wall_elapsed_ns=0, clock_drift=True,
            counters={"media_processes": 0, "network_attempts": 0, "other_process_attempts": 0})
    value = metrics.summarize(trail(case))
    assert value["elapsed_wall_ns"] == 0 and value["active_invocation_ns"] == 3
    assert value["clock_drift_observed"] is True and value["recovery_outcomes"]["UNKNOWN"] == 1


def test_real_audit_boundary_denies_network_git_and_coding_agents(case, record_property):
    # Independent Python proves production audit enforcement without launching
    # a network call, Git write or coding agent. Audit event denial occurs first.
    script = '''
import json,sys
from ai_commerce.production_runtime.metrics import Boundary,MetricsHold
out=[]
with Boundary() as boundary:
    for name,args in [("socket.connect",(None,("example.invalid",443))),
                      ("subprocess.Popen",("git",["git","status"],None,None)),
                      ("subprocess.Popen",("codex",["codex","exec"],None,None))]:
        try: sys.audit(name,*args)
        except MetricsHold: out.append("DENIED")
print(json.dumps({"outcomes":out,"counts":boundary.counts}))
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run([sys.executable, "-c", script], env=env, cwd=case["root"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["outcomes"] == ["DENIED"] * 3
    assert value["counts"] == {"network_attempts": 1, "other_process_attempts": 2, "media_processes": 0}
    record_property("audit_denied_boundaries", "network,git,coding-agent")


def test_finite_imports_and_single_native_run_site():
    expected = {"operator.py": set(), "operator_cli.py": {"argparse"},
        "handoff.py": set(), "metrics.py": {"os", "time", "uuid", "sys", "shutil", "subprocess"}}
    for filename, permitted in expected.items():
        tree = ast.parse((ROOT / "src/ai_commerce/production_runtime" / filename).read_text(encoding="utf-8"))
        loads = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__":
                assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
                loads.add(node.args[0].value)
            if isinstance(node, ast.Name):
                assert node.id not in {"VerificationContext", "AuthorityReceipt", "eval", "exec"}
        assert loads == permitted
    text = (ROOT / "src/ai_commerce/production_runtime/operator.py").read_text()
    assert text.count("w.run_once(") == 1 and text.count("w.resume_once(") == 1
    assert "service.run(" not in text
