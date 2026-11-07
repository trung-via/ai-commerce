"""Native Windows B0.4 -> exact A06 media -> real Q4; no provider proof.

Every accepted case starts fresh at this shipped Python-tree version. The
existing BQ04b fixture consumers run independently, never as PULSE media.
"""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

import pytest

from ai_commerce.creative_quality import pulse_studio_offline_bridge as bridge
from ai_commerce.creative_quality import inspection_evidence as t1, t2_sampling as t2
from ai_commerce.production_runtime import attended_worker as w, handoff, operator
from ai_commerce.production_runtime.contracts import canonical_bytes, seal_contract
from ai_commerce.engineering import impact_v21, minimum_test
from test_b0_4_operator import case, cli, setup, media, PRIVATE, audit, count

ROOT = Path(__file__).resolve().parents[1]
WI = "work_items/BQ-05A_PULSE_STUDIO_OFFLINE_QA_001.json"
MANIFEST = "work_items/mt01-components/bq-05a.json"


def snapshot(c):
    # Include all native files, original stills, sealed directive, exports and
    # audit anchors. Do not include test hook audit output, which is observational.
    paths = [c["directive"]]
    for key in ("operator", "private", "export", "assets"):
        paths.extend(p for p in c[key].rglob("*") if p.is_file())
    return {str(p): p.read_bytes() for p in paths}


def restore_test_fixture(saved):
    """Restore only disposable test bytes; never a retained production journey."""
    for name, raw in saved.items():
        path = Path(name)
        if not path.exists() or path.read_bytes() != raw:
            path.write_bytes(raw)


@pytest.fixture
def journey(case):
    media()
    setup(case)
    assert cli(case, "run")["state"] == "WAIT_BRAIN"
    cli(case, "handoff")
    return case


def inspect(c, **kwargs):
    return bridge.inspect_wait_brain(str(c["session"]), **kwargs)


def hold(c, **kwargs):
    before = snapshot(c)
    result = inspect(c, **kwargs)
    value = result.to_dict()
    assert value["status"] == "FAIL_CLOSED_HOLD", value
    assert value["q5"]["executed"] is False and value["q5"]["editor"] == "NONE"
    assert value["flags"] == bridge.FLAGS
    assert snapshot(c) == before
    assert PRIVATE.encode() not in result.raw and str(c["root"]).encode() not in result.raw
    return result


def test_wait_brain_actual_a06_q4_closed_deterministic_readonly(journey, record_property):
    c = journey
    before = snapshot(c)
    # A separate audited Python process opens the actual retained native output.
    script = "from ai_commerce.creative_quality.pulse_studio_offline_bridge import inspect_wait_brain; import sys; sys.stdout.buffer.write(inspect_wait_brain(sys.argv[1]).raw)"
    proc = subprocess.run([sys.executable, "-c", script, str(c["session"])], cwd=c["root"],
                          env=c["env"], capture_output=True, timeout=180)
    assert proc.returncode == 0 and not proc.stderr
    result = bridge.BQ05aStudioQAEvidence(proc.stdout)
    value = result.to_dict()
    assert value["status"] == "OFFLINE_TECHNICAL_REVIEW_ONLY", value
    assert value["schema_version"] == "BQ05aStudioQAEvidence.v1" and set(value) == bridge.TOP
    body = {k: v for k, v in value.items() if k != "record_sha256"}
    assert value["record_sha256"] == sha256(canonical_bytes(body)).hexdigest()
    session, _, root, directive, intent, _ = operator.load(str(c["session"]))
    receipt, packet, parsed = handoff.verified(root, directive, intent)
    movie = w._render_path(root, intent) / "video.mp4"
    digest = sha256(movie.read_bytes()).hexdigest()
    assert receipt.raw == (c["export"] / handoff.RECEIPT).read_bytes()
    assert packet == (c["export"] / handoff.PACKET).read_bytes()
    assert value["binding"]["artifact_sha256"] == parsed["local_artifact"]["artifact_sha256"] == digest
    assert value["binding"]["packet_file_sha256"] == sha256(packet).hexdigest()
    assert value["binding"]["journal_checkpoint"] == parsed["journal_checkpoint"]
    assert value["binding"]["runtime_sha256"] == w._runtime_hash() == session["intent"]["runtime_sha256"]
    assert value["binding"]["pulse_source_sha"] == w.SOURCE_SHA
    assert value["source_sha"] == "c868b989382763b03f5040390a8adaf99c25e064"
    assert value["provenance"] == {"evidence_type": "SYNTHETIC_FIXTURE", "origin": "LOCAL_IMPORT",
        "confidence": "CURRENT_BYTES_TECHNICAL_ONLY", "provider_attested": False}
    q4 = value["q4"]
    assert q4["status"] == "OBSERVED" and q4["full_decode"] is True
    assert q4["source_asset_sha256"] == q4["result_asset_sha256"] == digest
    assert q4["facts"]["video_streams"] == [{"index": 0, "width": 64, "height": 96, "fps": "12", "decoded_frames": 6}]
    assert q4["facts"]["duration_seconds"] == "1/2" and q4["facts"]["audio_present"] is False
    assert q4["black_diagnostic"]["status"] == "OBSERVED"
    assert q4["t2_status"] == "OBSERVED" and len(q4["frames"]) == 2
    assert q4["requested_frame_times"] == ["0", "1/4"] and q4["audio_status"] == "NOT_APPLICABLE"
    assert q4["review_status"] in {"HOLD", "UNKNOWN", "REVISE"}
    assert value["q5"]["choice"] in {"HOLD", "KEEP_ORIGINAL", "RETURN_TO_BRAIN"}
    assert value["limits"]["T3"] == value["limits"]["human_calibration"] == "NOT_RUN"
    assert value["flags"] == bridge.FLAGS
    assert inspect(c).raw == result.raw
    assert bridge.recheck_sidecar(result.raw, str(c["session"])).raw == result.raw
    assert before == snapshot(c)
    assert count(c, "render_call") == count(c, "native_run_call") == 1
    events = audit(c)
    assert any(r.get("media_process") == "ffprobe" for r in events)
    assert any(r.get("media_process") == "ffmpeg" and r.get("decode") for r in events)
    assert not any(r.get("denied_network") or r.get("denied_process") for r in events)
    for private in (PRIVATE, str(c["root"]), "actor_ref", "account_ref", "freeform", "fixture_token"):
        assert private.encode() not in result.raw
    record_property("a06_current_artifact_sha256", digest)
    record_property("sidecar_sha256", result.digest)
    record_property("actual_windows_q4_t1_t2", True)
    record_property("retained_journey_bytes_unchanged", True)


def test_optional_t2_never_invents_observations(journey):
    result = inspect(journey, sample=False).to_dict()
    assert result["status"] == "OFFLINE_TECHNICAL_REVIEW_ONLY", result
    assert result["q4"]["t2_status"] == "NOT_RUN" and result["q4"]["frames"] == []


@pytest.mark.parametrize("context", [{"synthetic_fixture_profile": "bq04b-white-slate-4fps/v1"},
    {"fixture_token": "copied-bearer"}, {"receipt": {"state": "WAIT_BRAIN"}},
    {"provider_attested": True}, {"rights": "GRANTED"}, {"brain_approved": True},
    {"unknown_provider_effect": True}, {}])
def test_untrusted_context_never_upgrades_source(case, context, monkeypatch):
    monkeypatch.setattr(bridge, "_observe", lambda *a: pytest.fail("caller context reached native reads"))
    hold(case, context=context)


@pytest.mark.parametrize("effect", ["PUBLISH", "SPEND", "REPAIR", "START", "UNKNOWN", None])
def test_effects_without_native_grants_fail_before_media(case, effect, monkeypatch):
    monkeypatch.setattr(bridge, "_observe", lambda *a: pytest.fail("effect reached native reads"))
    hold(case, effect=effect)


def test_fabricated_receipt_or_prestart_session_is_not_native(case):
    media()
    setup(case)
    hold(case)
    result = bridge.inspect_wait_brain({"state": "WAIT_BRAIN", "receipt": "fake"})
    assert result.to_dict()["status"] == "FAIL_CLOSED_HOLD"
    fake = case["root"] / "fake.json"
    fake.write_bytes(canonical_bytes({"state": "WAIT_BRAIN"}))
    assert bridge.inspect_wait_brain(str(fake)).to_dict()["status"] == "FAIL_CLOSED_HOLD"


def _tamper_native_asset(c, damage):
    movie = next(c["private"].glob("e01-*/video.mp4"))
    if damage == "mutated-video":
        movie.write_bytes(movie.read_bytes() + b"stale-sha")
    elif damage == "corrupt-video":
        movie.write_bytes(b"not an mp4")
    elif damage == "foreign-video":
        # Different genuine FFmpeg video, with the same filename.
        subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "color=red:s=64x96:r=12:d=0.5", "-threads", "1", "-pix_fmt", "yuv420p", "-y", str(movie)],
            check=True, capture_output=True, timeout=30)
    elif damage == "different-native-asset":
        owner = w.service.inspect(movie.parent)
        custody = movie.parent / "custody"
        manifest = json.loads((custody / owner["render"]["manifest_ref"]).read_bytes())
        render = custody / manifest["output"]["ref"]
        # All owner videos are independently hash-bound; damaging either one
        # must prevent use of the otherwise valid delivered video.
        assert render != movie and render.is_file()
        assert sha256(render.read_bytes()).hexdigest() == manifest["output"]["sha256"]
        render.write_bytes(render.read_bytes() + b"different-native-render")
    elif damage in {"receipt", "packet"}:
        path = c["export"] / (handoff.RECEIPT if damage == "receipt" else handoff.PACKET)
        path.write_bytes(path.read_bytes() + b" ")
    elif damage == "checkpoint":
        path = sorted(c["private"].glob("checkpoint-*.json"))[-1]
        value = json.loads(path.read_bytes())
        value["sha256"] = "0" * 64
        path.write_bytes(canonical_bytes(value))
    elif damage.startswith("journal-"):
        with sqlite3.connect(c["private"] / "journal.sqlite") as db:
            db.execute("DROP TRIGGER immutable_records_update")
            if damage == "journal-edit":
                db.execute("UPDATE records SET payload='{}' WHERE position=2")
            else:
                db.execute("UPDATE records SET position=1000 WHERE position=2")
    elif damage == "admission":
        (c["operator"] / "run-admission.json").unlink()
    elif damage == "audit":
        path = c["operator"] / "audit.jsonl"
        path.write_bytes(path.read_bytes().splitlines()[0] + b"\n")
    else:
        (movie.parent / "e01-manifest.json").unlink()


def test_current_asset_and_native_provenance_tampering_holds(journey):
    saved = snapshot(journey)
    for damage in ("mutated-video", "corrupt-video", "foreign-video", "different-native-asset",
        "receipt", "packet", "checkpoint", "journal-edit", "journal-reorder", "admission", "audit", "missing-native"):
        restore_test_fixture(saved)
        _tamper_native_asset(journey, damage)
        hold(journey)
    restore_test_fixture(saved)
    assert snapshot(journey) == saved


def test_self_resealed_session_pin_mismatch_is_not_authenticated(journey):
    c = journey
    saved = c["session"].read_bytes()
    for field, value in (("journey_id", "journey:" + "8" * 32), ("directive_version", 2),
                         ("source_sha", "0" * 40), ("runtime_sha256", "0" * 64)):
        session = json.loads(saved)
        session["intent"][field] = value
        session["session_sha256"] = sha256(canonical_bytes({k: v for k, v in session.items() if k != "session_sha256"})).hexdigest()
        c["session"].write_bytes(canonical_bytes(session))
        hold(c)
    c["session"].write_bytes(saved)


def test_resealed_directive_digest_drift_holds(journey):
    c = journey
    c["body"]["creative"]["freeform"] = "Different private intent"
    c["directive"].write_bytes(seal_contract("CreativeDirective", c["body"]).raw)
    hold(c)


@pytest.mark.parametrize("alias", ["ads", "parent-traversal", "device", "short-name", "worktree"])
def test_path_alias_and_worktree_escape_holds(case, alias):
    c = case
    path = str(c["session"])
    if alias == "ads":
        path += ":extra"
    elif alias == "parent-traversal":
        path = str(c["operator"]) + "\\..\\operator\\SESSION.json"
    elif alias == "device":
        path = "\\\\?\\" + path
    elif alias == "short-name":
        path = path.replace("operator", "OPERAT~1")
    else:
        path = str(ROOT / "work_items" / Path(WI).name)
    assert bridge.inspect_wait_brain(path).to_dict()["status"] == "FAIL_CLOSED_HOLD"


def test_native_windows_hardlink_rejected_before_owner_or_media(journey, monkeypatch):
    c = journey
    movie = next(c["private"].glob("e01-*/video.mp4"))
    linked = c["root"] / "hardlink.mp4"
    os.link(movie, linked)
    assert movie.stat().st_nlink == 2
    monkeypatch.setattr(handoff, "verified", lambda *a: pytest.fail("hardlink reached owner verification"))
    hold(c)


def test_native_windows_junction_rejected_before_owner_or_media(case, monkeypatch):
    c = case
    link = c["root"] / "junction"
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(c["operator"])],
                          capture_output=True, timeout=15)
    assert proc.returncode == 0, "NATIVE_JUNCTION_ENVIRONMENT_HOLD"
    try:
        assert link.lstat().st_file_attributes & 0x400
        monkeypatch.setattr(operator, "load", lambda *a: pytest.fail("junction reached owner load"))
        result = bridge.inspect_wait_brain(str(link / "SESSION.json"))
        assert result.to_dict()["status"] == "FAIL_CLOSED_HOLD"
    finally:
        os.rmdir(link)


def test_symlink_tag_and_unknown_reparse_metadata_rejected(journey, monkeypatch):
    c = journey
    movie = next(c["private"].glob("e01-*/video.mp4"))
    real = Path.lstat
    class Tagged:
        def __init__(self, info):
            self.info = info
        def __getattr__(self, key):
            return 0xA000000C if key == "st_reparse_tag" else getattr(self.info, key)
    monkeypatch.setattr(Path, "lstat", lambda p, *a, **k: Tagged(real(p, *a, **k)) if p == movie else real(p, *a, **k))
    monkeypatch.setattr(handoff, "verified", lambda *a: pytest.fail("tagged link reached media"))
    hold(c)


def test_locked_source_denies_real_mutation_and_rename(journey, monkeypatch):
    c = journey
    movie = next(c["private"].glob("e01-*/video.mp4"))
    original = t1.inspect_local_t1
    attempts = []
    def checked(*args, **kwargs):
        with pytest.raises(OSError):
            with movie.open("ab") as stream:
                stream.write(b"mutation")
        with pytest.raises(OSError):
            movie.rename(movie.with_suffix(".moved"))
        attempts.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(t1, "inspect_local_t1", checked)
    before = snapshot(c)
    assert inspect(c).to_dict()["status"] == "OFFLINE_TECHNICAL_REVIEW_ONLY"
    assert attempts and before == snapshot(c)


def test_final_lock_exit_failure_cannot_return_success(journey, monkeypatch):
    original = t2._snapshot_lock
    @contextmanager
    def failing(path):
        with original(path) as stream:
            yield stream
        raise OSError("PRIVATE credential must not escape")
    monkeypatch.setattr(t2, "_snapshot_lock", failing)
    hold(journey)


def test_actual_tool_start_failure_never_promotes(journey, monkeypatch):
    original = subprocess.Popen
    for tool in ("ffprobe", "ffmpeg"):
        def failed(argv, *args, **kwargs):
            if tool in Path(argv[0]).stem.lower():
                raise OSError("PRIVATE failed tool path")
            return original(argv, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, "Popen", failed)
            hold(journey)


def test_sidecar_closed_and_hashes_never_authenticate_forgery(journey):
    current = inspect(journey)
    assert current.to_dict()["status"] == "OFFLINE_TECHNICAL_REVIEW_ONLY"
    for field in ("extra", "digest", "provenance", "nested", "quality"):
        value = current.to_dict()
        if field == "extra":
            value["provider_approval"] = True
        elif field == "digest":
            value["binding"]["artifact_sha256"] = "0" * 64
        elif field == "provenance":
            value["provenance"]["evidence_type"] = "PROVIDER_ATTESTED"
        elif field == "nested":
            value["q4"]["facts"]["private_path"] = "private"
        else:
            value["status"] = "QA_PASSED"
        value["record_sha256"] = sha256(canonical_bytes({k: v for k, v in value.items() if k != "record_sha256"})).hexdigest()
        with pytest.raises(bridge.BridgeHold):
            bridge.recheck_sidecar(canonical_bytes(value), str(journey["session"]))
    # A well-shaped, self-resealed technical number still needs local replay.
    value = current.to_dict()
    value["q4"]["size_bytes"] += 1
    value["record_sha256"] = sha256(canonical_bytes({k: v for k, v in value.items() if k != "record_sha256"})).hexdigest()
    with pytest.raises(bridge.BridgeHold, match="CURRENT_NATIVE_REPLAY"):
        bridge.recheck_sidecar(canonical_bytes(value), str(journey["session"]))


def test_stale_sidecar_cannot_authenticate_replaced_video(journey):
    original = inspect(journey)
    assert original.to_dict()["status"] == "OFFLINE_TECHNICAL_REVIEW_ONLY"
    movie = next(journey["private"].glob("e01-*/video.mp4"))
    movie.write_bytes(movie.read_bytes() + b"later replacement")
    with pytest.raises(bridge.BridgeHold, match="CURRENT_NATIVE_REPLAY"):
        bridge.recheck_sidecar(original.raw, str(journey["session"]))


def test_human_local_rights_never_become_live_grants_and_revocation_holds(case):
    media()
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
    case["directive"].write_bytes(seal_contract("CreativeDirective", case["body"]).raw)
    setup(case)
    cli(case, "run")
    cli(case, "handoff")
    result = inspect(case).to_dict()
    assert result["status"] == "OFFLINE_TECHNICAL_REVIEW_ONLY", result
    assert result["provenance"]["evidence_type"] == "HUMAN_SUPPLIED" and "RIGHTS_UNKNOWN" in result["flags"]
    hold(case, effect="PUBLISH")
    right = next(case["assets"].glob("*.json"))
    right.unlink()
    hold(case)


def test_rehashed_provider_attributed_receipt_is_not_owner_proof(journey):
    path = journey["export"] / handoff.RECEIPT
    value = json.loads(path.read_bytes())
    value["provenance"]["evidence_type"] = "PROVIDER_ATTESTED"
    body = {k: v for k, v in value.items() if k != "receipt_sha256"}
    value["receipt_sha256"] = sha256(canonical_bytes(body)).hexdigest()
    path.write_bytes(canonical_bytes(value))
    hold(journey)


def test_mt01_registered_onboarding_and_real_consumer_closure():
    work = json.loads((ROOT / WI).read_text())
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text())
    files = minimum_test.file_hashes(ROOT)
    base = {p: d for p, d in files.items() if p not in work["add_only_paths"]}
    plan = impact_v21.registered_action_plan(repo=ROOT, base_sha=work["source_sha"],
        base_files=base, candidate_files=files, changed_paths=work["allowed_paths"],
        required_acs=work["acceptance"], fallback_argv=profiles["mt01-component-onboarding"],
        groups=minimum_test.approved_groups(), registered_profiles=profiles, allowed_paths=work["allowed_paths"])
    assert not plan["fallback"] and not plan["impact_v21"]["missing_mapping"], plan
    assert {"a06", "b0-3", "b0-4", "bq-02a", "bq-02b", "bq-04a", "bq-04b", "bq-05a"} <= set(plan["selected_groups"])
    new = next(g for g in plan["groups"] if g["group"] == "bq-05a")
    assert new["binding"]["registered_profile"] == "mt01-component-onboarding"
    assert new["binding"]["argv"] == ["python", "-m", "pytest", "tests/test_bq_05a_pulse_studio_offline_bridge.py", "-q", "-n", "12"]
    manifest = json.loads((ROOT / MANIFEST).read_text())
    assert manifest["sources"] == work["add_only_paths"][:1]
    assert manifest["tests"] == work["add_only_paths"][1:2]
    assert manifest["acs"] == work["acceptance"]
