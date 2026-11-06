"""Deterministic offline fixtures: zero Human participants and no live intake."""

from copy import copy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import socket
import subprocess

import pytest

from ai_commerce.creative_quality import blind_trials as bt, rater_receipts as rr, calibration_stats as cs
from ai_commerce.creative_quality import study_protocol as sp, human_attestation as ha, study_readiness as sr
from ai_commerce.creative_quality import t3_assessment as t3, multimodal_review as mm
from test_bq_03a_blind_calibration import uninspected_bq02b
from test_bq_02a_critic_t1 import make_plan


def ref(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def plan(**kwargs):
    return bt.build_plan([ref(0), ref(1)], seed=ref("synthetic-seed"), **kwargs)


def refs(draft):
    task = draft.to_dict()["blinded_task_bindings"][0]
    return tuple({"kind": kind, "ref_id": "request_" + f"{index:032x}",
        "protocol_digest": draft.digest, "asset_sha256": task["expected_asset_sha256s"][0],
        "blinded_task_digest": task["blinded_task_digest"], "state": "UNVERIFIED"}
        for index, kind in enumerate(ha.KINDS))


def chain(p=None, **source):
    p = plan() if p is None else p
    d = sp.build_protocol(p, plan_digest=p.digest, **source)
    a = ha.request_attestation(d, draft_digest=d.digest, plan=p, plan_digest=p.digest, **source)
    r = sr.build_readiness(p, d, a, plan_digest=p.digest, draft_digest=d.digest,
                           attestation_digest=a.digest, **source)
    return p, d, a, r


def bindings(p, d, a=None, **source):
    return {"plan": p, "draft": d, "request": a, "plan_digest": p.digest,
            "draft_digest": d.digest, "attestation_digest": None if a is None else a.digest, **source}


@pytest.mark.parametrize("privacy", sp.PRIVACY_MODES, ids=["p0", "p1"])
@pytest.mark.parametrize("population", sp.POPULATIONS, ids=["g0", "g1"])
def test_preregistered_design_complete_and_pending(privacy, population):
    p = plan()
    d = sp.build_protocol(p, plan_digest=p.digest, privacy_mode=privacy, population=population)
    data = d.to_dict()
    assert data["source_plan_digest"] == p.digest
    assert data["expected_asset_sha256s"] == p.to_dict()["config"]["asset_sha256s"]
    assert data["sampling_power"]["target_participant_count"] is None
    assert data["sampling_power"]["approved_calculation_digest"] is None
    assert data["sampling_power"]["status"] == "PENDING_INDEPENDENT_CALCULATION_AND_APPROVAL"
    assert data["allocation"]["actual_assignment"].startswith("NOT_RUN")
    assert data["allocation"]["design"] == "BETWEEN_PERSON_COUNTERBALANCE_AB_BA"
    assert data["exposure_order"].index("PARTICIPANT_INFORMED_OPT_IN") < data["exposure_order"].index("ACTUAL_EXPOSURE")
    assert set(data["consent_checklist"]) == set(sp.CONSENT_CHECKLIST)
    assert data["rights_status"] == "REVIEW_REQUIRED_NO_GRANT"
    assert data["observed_human_count"] == 0
    assert sp.BQ03bStudyProtocolDraft.from_json(d.to_json(), plan=p, plan_digest=p.digest).to_bytes() == d.to_bytes()


@pytest.mark.parametrize("mode,audio,rubric", [
    ("ANIMATION", "SILENT", ("visual_continuity",)),
    ("FANTASY", "VOICE", ("audio_preference",)),
    ("LIVE_ACTION", "SILENT", ("physical_plausibility",)),
    ("VOICE_ONLY", "VOICE", ("audio_preference",)),
    ("STYLIZED", "MUSIC", ("overall_preference", "visual_continuity")),
    ("UNSPECIFIED", "UNSPECIFIED", ("overall_preference",))], ids=["m0", "m1", "m2", "m3", "m4", "m5"])
def test_rubric_applicability_and_public_preview(mode, audio, rubric):
    p, d, _, r = chain(plan(visual_mode=mode, audio_mode=audio, rubric=rubric))
    assert d.to_dict()["rubric"] == sorted(rubric)
    assert d.to_dict()["applicability"]["visual_mode"] == mode
    public = bt.canonical(d.public_preview(plan=p, plan_digest=p.digest))
    private = p.to_dict()
    forbidden = private["config"]["asset_sha256s"] + [private["config"]["seed"], private["seed_commitment"], p.digest, d.digest]
    forbidden += ["task_id", "synthetic_slot", "pair_id", "file_ref", "reference-only.mp4", "product_ref", "A/B", "B/A"]
    assert all(value not in public for value in forbidden)
    assert r.to_dict()["status"] == "NOT_READY_FOR_LIVE_STUDY"


def test_no_bound_source_has_explicit_hold():
    d = sp.build_protocol()
    a = ha.request_attestation(d, draft_digest=d.digest, plan=None, plan_digest=None)
    r = sr.build_readiness(None, d, a, plan_digest=None, draft_digest=d.digest, attestation_digest=a.digest)
    assert d.to_dict()["source_status"] == "NO_BOUND_SOURCE"
    assert "NO_BOUND_SOURCE" in r.to_dict()["reason_codes"]
    assert r.to_dict()["expected_asset_sha256s"] == []


@pytest.mark.parametrize("target", ["protocol", "attestation", "readiness"], ids=["d", "a", "r"])
def test_immutable_exact_canonical_replay_and_digest(target):
    p, d, a, r = chain()
    obj = {"protocol": d, "attestation": a, "readiness": r}[target]
    assert obj.digest == hashlib.sha256(obj.to_bytes()).hexdigest()
    with pytest.raises(FrozenInstanceError):
        obj._json = "{}"
    with pytest.raises(bt.Error):
        obj.verify_digest(ref("wrong"))
    data = obj.to_dict(); data["observed_human_count"] = 99
    assert obj.to_dict()["observed_human_count"] == 0
    if target == "protocol":
        rebuilt = sp.BQ03bStudyProtocolDraft.from_json(d.to_json(), plan=p, plan_digest=p.digest)
    elif target == "attestation":
        rebuilt = ha.BQ03bHumanAttestationRequest.from_json(a.to_json(), draft=d, draft_digest=d.digest, plan=p, plan_digest=p.digest)
    else:
        rebuilt = sr.BQ03bStudyReadiness.from_json(r.to_json(), **bindings(p, d, a))
    assert rebuilt.to_bytes() == obj.to_bytes()


@pytest.mark.parametrize("key", list(sp.build_protocol().to_dict()), ids=lambda x: "d" + str(list(sp.build_protocol().to_dict()).index(x)))
def test_every_protocol_field_rejects_forgery(key):
    p, d, _, _ = chain()
    data = d.to_dict(); data[key] = "FORGED"
    with pytest.raises(bt.Error):
        sp.BQ03bStudyProtocolDraft.from_dict(data, plan=p, plan_digest=p.digest)


_P, _D, _A, _R = chain()


@pytest.mark.parametrize("key", list(_A.to_dict()), ids=[f"a{i}" for i in range(len(_A.to_dict()))])
def test_every_attestation_field_rejects_forgery(key):
    data = _A.to_dict(); data[key] = "VERIFIED_HUMAN_OBSERVED"
    with pytest.raises(bt.Error):
        ha.BQ03bHumanAttestationRequest.from_dict(data, draft=_D, draft_digest=_D.digest, plan=_P, plan_digest=_P.digest)


@pytest.mark.parametrize("key", list(_R.to_dict()), ids=[f"r{i}" for i in range(len(_R.to_dict()))])
def test_every_readiness_field_rejects_forgery(key):
    data = _R.to_dict(); data[key] = "APPROVED"
    with pytest.raises(bt.Error):
        sr.BQ03bStudyReadiness.from_dict(data, **bindings(_P, _D, _A))


RAW_SUBMISSIONS = [True, False, "signed:human", {"consent": True}, {"allow_true": True},
    {"signer": "project-windows-sid"}, {"human_observed": 1}, {"rights": "VERIFIED"},
    {"participant_name": "Synthetic Person"}, {"email": "fixture@example.invalid"},
    {"bank": "0000000000"}, {"withdrawer": "claimed-person"}, {"signature": "ed25519:fake"},
    {"verification_time": "2099-01-01"}, {"uri": "https://invalid.test/private"},
    {"path": "C:/private/consent.json"}, {"response": "LEFT"}, {"token": "secret"},
    float("nan"), float("inf"), -float("inf"), "\ud800", "x" * 600_000, [None] * 5000,
    {"protocol": "BQ03b.v99"}, {"gate": "PASS"}, {"exposure_time": "yesterday"}]


@pytest.mark.parametrize("raw", RAW_SUBMISSIONS, ids=[f"raw{i}" for i in range(len(RAW_SUBMISSIONS))])
def test_all_raw_submissions_discarded_and_never_human(raw):
    a = ha.request_attestation(_D, draft_digest=_D.digest, plan=_P, plan_digest=_P.digest, submission=raw)
    data = a.to_dict()
    assert data["status"] == "HOLD_UNVERIFIED" and data["admission"] == "NOT_ADMITTED"
    assert data["observed_human_count"] == 0 and data["external_effect"] == "UNKNOWN"
    assert data["submission_handling"] == "REJECTED_WITHOUT_RETENTION"
    assert data["reference_metadata"] == []
    for private in ("fixture@example.invalid", "Synthetic Person", "0000000000", "ed25519:fake", "C:/private", "2099-01-01"):
        assert private not in a.to_json()
    r = sr.build_readiness(_P, _D, a, plan_digest=_P.digest, draft_digest=_D.digest, attestation_digest=a.digest)
    assert r.to_dict()["human_data"] == "AUTHENTIC_HUMAN_DATA_NOT_AVAILABLE"
    assert "UNKNOWN_EFFECT_NO_RETRY" in r.to_dict()["reason_codes"]


def test_cycles_and_hostile_objects_are_not_serialized_hashed_or_called(monkeypatch, caplog):
    class Hostile:
        def __repr__(self): raise AssertionError("raw repr called")
        def __str__(self): raise AssertionError("raw str called")
        def __bool__(self): raise AssertionError("raw bool called")
        def __call__(self): raise AssertionError("raw callback called")
    cycle = []; cycle.append(cycle)
    secret = "private-person@example.invalid"
    original = hashlib.sha256
    def checked_hash(value=b"", **kwargs):
        assert secret.encode() not in value
        return original(value, **kwargs)
    monkeypatch.setattr(hashlib, "sha256", checked_hash)
    for value in (Hostile(), cycle, {"email": secret}):
        a = ha.request_attestation(_D, draft_digest=_D.digest, plan=_P, plan_digest=_P.digest,
                                   submission=value, verification_callback=Hostile())
        assert a.to_dict()["admission"] == "NOT_ADMITTED"
        assert secret not in a.to_json()
    assert secret not in caplog.text


@pytest.mark.parametrize("key", ["allow_true", "verified", "consent", "rights", "verification_callback", "signer", "human_observed"], ids=[f"o{i}" for i in range(7)])
def test_authority_overrides_never_admit(key):
    called = []
    value = (lambda: called.append(1) or True) if key == "verification_callback" else True
    a = ha.request_attestation(_D, draft_digest=_D.digest, plan=_P, plan_digest=_P.digest, **{key: value})
    assert a.to_dict()["submission_handling"] == "REJECTED_WITHOUT_RETENTION" and not called


@pytest.mark.parametrize("state", ha.STATES, ids=[f"s{i}" for i in range(len(ha.STATES))])
@pytest.mark.parametrize("index", range(5), ids=["c", "x", "s", "w", "v"])
def test_each_required_receipt_state_remains_unverified(state, index):
    rows = list(refs(_D)); rows[index]["state"] = state
    a = ha.request_attestation(_D, draft_digest=_D.digest, plan=_P, plan_digest=_P.digest, references=tuple(rows))
    assert state + "_REFERENCE" in a.to_dict()["reason_codes"]
    assert a.to_dict()["consent"] == "NOT_ESTABLISHED" and a.to_dict()["rights"] == "UNKNOWN"
    assert ha.BQ03bHumanAttestationRequest.from_json(a.to_json(), draft=_D, draft_digest=_D.digest,
        plan=_P, plan_digest=_P.digest).digest == a.digest
    r = sr.build_readiness(**bindings(_P, _D, a)).to_dict()
    assert r["status"] == "NOT_READY_FOR_LIVE_STUDY"
    assert all(row["status"] == "PENDING_AUTHENTIC_EXTERNAL_PROOF" for row in r["checklist"])


BAD_METADATA = [("kind", "PARTICIPANT"), ("kind", True), ("ref_id", "person@example.invalid"),
    ("ref_id", "Alice"), ("ref_id", "https://invalid.test/consent"), ("ref_id", "C:/secret"),
    ("ref_id", "request_" + "a" * 33), ("ref_id", "request_" + "A" * 32),
    ("ref_id", "\ud800"), ("ref_id", "Tên người 🌏"), ("ref_id", "x" * 600000),
    ("state", "VERIFIED"), ("state", "HUMAN_OBSERVED"), ("state", True),
    ("protocol_digest", True), ("protocol_digest", "a" * 63), ("protocol_digest", "G" * 64),
    ("asset_sha256", "https://invalid.test/media"), ("asset_sha256", float("nan")),
    ("blinded_task_digest", None), ("extra", "PII"), ("signature", "signed-looking")]


@pytest.mark.parametrize("key,value", BAD_METADATA, ids=[f"b{i}" for i in range(len(BAD_METADATA))])
def test_invalid_reference_metadata_dropped_without_echo(key, value):
    rows = list(refs(_D)); rows[0][key] = value
    a = ha.request_attestation(_D, draft_digest=_D.digest, plan=_P, plan_digest=_P.digest, references=tuple(rows))
    assert a.to_dict()["reference_metadata"] == []
    assert "INVALID_REFERENCE_METADATA" in a.to_dict()["reason_codes"]
    assert ha.BQ03bHumanAttestationRequest.from_json(a.to_json(), draft=_D, draft_digest=_D.digest,
        plan=_P, plan_digest=_P.digest).digest == a.digest
    assert sr.build_readiness(**bindings(_P, _D, a)).to_dict()["decision"] == "HOLD_PENDING_AUTHENTIC_GRANTS"


@pytest.mark.parametrize("kind", ha.KINDS, ids=["mc", "mx", "ms", "mw", "mv"])
def test_each_missing_reference_explicit(kind):
    rows = tuple(row for row in refs(_D) if row["kind"] != kind)
    a = ha.request_attestation(_D, draft_digest=_D.digest, plan=_P, plan_digest=_P.digest, references=rows)
    assert "MISSING_" + kind + "_REFERENCE" in a.to_dict()["reason_codes"]


@pytest.mark.parametrize("mode,reason", [
    ("duplicate_kind", "DUPLICATE_REFERENCE"), ("duplicate_id", "DUPLICATE_REFERENCE"),
    ("wrong_protocol", "WRONG_PROTOCOL_REFERENCE"), ("wrong_asset", "ASSET_SHA_MISMATCH"),
    ("wrong_task", "BLINDED_TASK_DIGEST_MISMATCH")], ids=["dk", "di", "wp", "wa", "wt"])
def test_reference_linkage_failures(mode, reason):
    rows = list(refs(_D))
    if mode == "duplicate_kind": rows[1]["kind"] = rows[0]["kind"]
    elif mode == "duplicate_id": rows[1]["ref_id"] = rows[0]["ref_id"]
    else: rows[0][{"wrong_protocol": "protocol_digest", "wrong_asset": "asset_sha256", "wrong_task": "blinded_task_digest"}[mode]] = ref("wrong")
    a = ha.request_attestation(_D, draft_digest=_D.digest, plan=_P, plan_digest=_P.digest, references=tuple(rows))
    assert reason in a.to_dict()["reason_codes"]


def test_known_asset_must_also_match_exact_blinded_task():
    p = bt.build_plan([ref(0), ref(1), ref(2)], seed=ref("fixture"))
    d = sp.build_protocol(p, plan_digest=p.digest)
    rows = list(refs(d))
    pair_assets = d.to_dict()["blinded_task_bindings"][0]["expected_asset_sha256s"]
    rows[0]["asset_sha256"] = next(x for x in p.to_dict()["config"]["asset_sha256s"] if x not in pair_assets)
    a = ha.request_attestation(d, draft_digest=d.digest, plan=p, plan_digest=p.digest, references=tuple(rows))
    assert "TASK_ASSET_MISMATCH" in a.to_dict()["reason_codes"]


@pytest.mark.parametrize("value", [True, False, None, {}, [], "", "Tên người", "\ud800", "https://invalid.test", "C:/private", float("nan"), "x" * 17000], ids=[f"v{i}" for i in range(12)])
@pytest.mark.parametrize("option", ["privacy_mode", "population"], ids=["privacy", "population"])
def test_protocol_category_inputs_are_closed_non_pii_tokens(value, option):
    with pytest.raises(bt.Error):
        sp.build_protocol(_P, plan_digest=_P.digest, **{option: value})


@pytest.mark.parametrize("mode", ["list", "oversized", "missing_field", "cycle", "bool"], ids=["list", "large", "field", "cycle", "bool"])
def test_reference_container_bounds(mode):
    rows = refs(_D)
    if mode == "list": rows = list(rows)
    elif mode == "oversized": rows = rows + (rows[0],)
    elif mode == "missing_field":
        rows = list(rows); del rows[0]["state"]; rows = tuple(rows)
    elif mode == "cycle":
        cycle = []; cycle.append(cycle); rows = (cycle,)
    else: rows = True
    a = ha.request_attestation(_D, draft_digest=_D.digest, plan=_P, plan_digest=_P.digest, references=rows)
    assert "INVALID_REFERENCE_METADATA" in a.to_dict()["reason_codes"]
    assert a.to_dict()["reference_metadata"] == []


@pytest.mark.parametrize("bad", [None, True, {}, "signed", "a" * 63, "G" * 64, ref("stale")], ids=[f"p{i}" for i in range(7)])
@pytest.mark.parametrize("pin", ["plan_digest", "draft_digest", "attestation_digest"], ids=["bp", "bd", "ba"])
def test_missing_stale_and_invalid_exact_pins(bad, pin):
    args = bindings(_P, _D, _A); args[pin] = bad
    with pytest.raises(bt.Error): sr.build_readiness(**args)


@pytest.mark.parametrize("target", ["plan", "draft", "request"], ids=["np", "nd", "na"])
@pytest.mark.parametrize("bad", [True, {}, "signed", []], ids=["bool", "dict", "sig", "list"])
def test_detached_records_not_native_evidence(target, bad):
    args = bindings(_P, _D, _A); args[target] = bad
    with pytest.raises(bt.Error): sr.build_readiness(**args)


NESTED = [("sampling_power", "status", "APPROVED"), ("sampling_power", "target_participant_count", 48),
    ("sampling_power", "approved_calculation_digest", ref("fake")), ("allocation", "actual_assignment", "OBSERVED"),
    ("allocation", "blinded_ui", "DEPLOYED"), ("privacy", "identity_linkage", "LOCAL_NAMES"),
    ("response_policy", "missing", "ZERO"), ("response_policy", "withdrawal", "IGNORE"),
    ("applicability", "product_ref", "person@example.invalid")]


@pytest.mark.parametrize("section,key,value", NESTED, ids=[f"n{i}" for i in range(len(NESTED))])
def test_deep_protocol_authority_and_privacy_mutation(section, key, value):
    data = _D.to_dict(); data[section][key] = value
    with pytest.raises(bt.Error): sp.BQ03bStudyProtocolDraft.from_dict(data, plan=_P, plan_digest=_P.digest)


BAD_JSON = ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '[]', 'null', 'true',
            '{', '"\\ud800"', ' ' + _D.to_json(), json.dumps(_D.to_dict(), indent=2), '"' + 'x' * 600000 + '"']


@pytest.mark.parametrize("raw", BAD_JSON, ids=[f"j{i}" for i in range(len(BAD_JSON))])
def test_noncanonical_duplicate_oversized_nonfinite_json_rejected(raw):
    with pytest.raises(bt.Error): sp.BQ03bStudyProtocolDraft.from_json(raw, plan=_P, plan_digest=_P.digest)


def test_no_source_pin_without_source_and_missing_request_remains_hold():
    with pytest.raises(bt.Error): sp.build_protocol(None, plan_digest=_P.digest)
    r = sr.build_readiness(_P, _D, plan_digest=_P.digest, draft_digest=_D.digest)
    assert "ATTESTATION_REQUEST_MISSING" in r.to_dict()["reason_codes"]
    assert "SAMPLE_POWER_UNAPPROVED" in r.to_dict()["uncertainty"]
    assert "SELECTION_POSITION_ORDER_AND_RATER_BIAS_UNMEASURED" in r.to_dict()["uncertainty"]
    with pytest.raises(bt.Error):
        sr.build_readiness(_P, _D, plan_digest=_P.digest, draft_digest=_D.digest, attestation_digest=_A.digest)


def test_tampered_native_records_and_source_substitution_rejected():
    for obj, key in ((_P, "observed_human_count"), (_D, "observed_human_count"), (_A, "observed_human_count")):
        forged = copy(obj); data = forged.to_dict(); data[key] = 1
        object.__setattr__(forged, "_json", bt.canonical(data))
        args = bindings(_P, _D, _A)
        target, pin = ("plan", "plan_digest") if obj is _P else ("draft", "draft_digest") if obj is _D else ("request", "attestation_digest")
        args[target] = forged; args[pin] = forged.digest
        with pytest.raises(bt.Error): sr.build_readiness(**args)
    other = plan(reviewer_slots=2)
    with pytest.raises(bt.Error): sr.build_readiness(**{**bindings(_P, _D, _A), "plan": other, "plan_digest": other.digest})


def test_native_bq03a_bq02b_read_only_consumer_closure_and_t3_not_run(monkeypatch):
    original = make_plan()
    def denied(*args, **kwargs): pytest.fail("offline composition attempted media or external effect")
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "run", denied)
        guard.setattr(subprocess, "Popen", denied)
        guard.setattr(socket, "socket", denied)
        guard.setattr(Path, "open", denied)
        review = uninspected_bq02b(original)
        assert type(review) is mm.BQ02bMultimodalReview
        source = {"linked_review": review, "linked_review_digest": review.digest}
        p, d, a, ready = chain(plan(**source), **source)
        before = review.to_bytes()
        judgments = rr.synthetic_judgments(p, [], plan_digest=p.digest, **source)
        report = cs.synthetic_report(p, judgments, plan_digest=p.digest, judgment_digest=judgments.digest, **source)
        assessment = t3.T3AssessmentLedgerlessStatus.from_dict(review.to_dict()["t3_assessment"])
        assert assessment.to_dict()["status"] == "NOT_RUN"
        assert report.to_dict()["human_calibration"] == "NOT_RUN"
        assert ready.to_dict()["source_link"]["expected_asset_sha256"] == ref(0)
        assert ready.to_dict()["t3_status"] == "NOT_RUN"
        assert ready.to_dict()["q2_p0"] == "UNCHANGED_SOURCE_AUTHORITY"
        assert ready.to_dict()["q4_semantic"] == "UNKNOWN_PRESERVED"
        assert review.to_bytes() == before
        assert sr.BQ03bStudyReadiness.from_json(ready.to_json(), **bindings(p, d, a, **source)).digest == ready.digest
        preview = bt.canonical(d.public_preview(plan=p, plan_digest=p.digest, **source))
        assert "reference-only.mp4" not in preview and review.digest not in preview
    assert review.to_dict()["semantic_quality"] == "UNKNOWN"


def test_source_registration_is_data_only_and_precise():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "work_items/mt01-components/bq-03b.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "MT01ComponentOwner.v1"
    assert manifest["sources"] == [f"src/ai_commerce/creative_quality/{name}.py" for name in ("study_protocol", "human_attestation", "study_readiness")]
    assert manifest["tests"] == ["tests/test_bq_03b_human_study_readiness.py"]
    assert manifest["contracts"] == [] and len(manifest["acs"]) == 3
    assert {Path(p).stem for p in manifest["consumers"]} == {"blind_trials", "rater_receipts", "calibration_stats", "t3_assessment", "multimodal_review"}
