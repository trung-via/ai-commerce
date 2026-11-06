"""BQ04b policy fallback: real synthetic media, never synthetic observations.

Only fixture setup invokes fixed argv through the existing native A13 runner.
Product functions have no filesystem, process, network, or edit capability.
"""

from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from ai_commerce.affiliate_release import inspection as a13
from ai_commerce.creative_quality import critic, inspection_evidence as t1, quality_review as qr
from ai_commerce.creative_quality import multimodal_review as mm, t2_sampling as t2
from ai_commerce.creative_quality import repair_policy as policy, repair_preservation as preservation
from ai_commerce.creative_quality import repair_reinspection as reinspection
from ai_commerce.creative_quality import repair_offline_adapter as adapter
from ai_commerce.creative_quality import repair_verified_evidence as evidence
from ai_commerce.engineering import impact_v21, minimum_test
from test_bq_02a_critic_t1 import make_plan

ROOT = Path(__file__).resolve().parents[1]
WI_PATH = "work_items/BQ-04B_OFFLINE_REPAIR_001.json"
MANIFEST_PATH = "work_items/mt01-components/bq-04b.json"


def measured_review(root, name, color):
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg and shutil.which("ffprobe"), "HOLD: real native media tools required"
    runner = a13.BoundedToolRunner(root, timeout_seconds=20, max_output_bytes=65536)
    version = runner.run([ffmpeg, "-version"], strict_errors=False).stdout
    assert version.startswith(b"ffmpeg version "), "HOLD: FFmpeg identification failed"
    # Fixed synthetic fixture program: caller text never enters argv/filters.
    runner.run([ffmpeg, "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
        f"color=c={color}:s=48x32:r=10:d=0.5", "-t", "0.5", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-threads", "1", str(root / name)])
    sha = hashlib.sha256((root / name).read_bytes()).hexdigest()
    asset = a13.ReleaseAsset(name, sha)
    source = t1.inspect_local_t1(root, [asset], diagnostics=a13.DiagnosticOptions(black=True, silence=True))
    assert source.to_dict()["status"] == "PASS", "HOLD: genuine A13 inspection required"
    sample = t2.sample_local_t2(source, t2.SamplingRequest(name, sha, ("1/10", "3/10")),
        root=root, allow_local_probe=True)
    assert sample.to_dict()["status"] == "OBSERVED", sample.to_dict()["reason_codes"]
    plan = make_plan()
    pre = critic.preflight_critique(plan, plan_digest=plan.digest)
    original = qr.adversarial_review(pre, preflight_digest=pre.digest,
        technical_evidence=source, technical_evidence_digest=source.digest)
    review = mm.multimodal_review(original, sample, original_review_digest=original.digest,
        t2_digest=sample.digest)
    return review


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp("bq04b-synthetic")
    before = measured_review(root, "before.mp4", "black")
    alternate = measured_review(root, "alternate.mp4", "white")
    assert before.to_dict()["observed_asset_sha256"] != alternate.to_dict()["observed_asset_sha256"]
    return root, before, alternate


def parents(before, *, context=None):
    decision = policy.decide_repair(before, review_digest=before.digest,
        asset_sha256=before.to_dict()["observed_asset_sha256"], context=context)
    plan = preservation.create_preservation_plan(decision, decision_digest=decision.digest)
    return decision, plan


def attempt_sources(decision, plan, **extra):
    return dict(decision=decision, plan=plan, decision_digest=decision.digest,
                plan_digest=plan.digest, **extra)


def bridge_sources(before, *, after=None):
    decision, plan = parents(before)
    attempt = adapter.assess_offline_attempt(**attempt_sources(decision, plan))
    request = reinspection.request_reinspection(plan, plan_digest=plan.digest)
    return dict(**attempt_sources(decision, plan), attempt=attempt, attempt_digest=attempt.digest,
                request=request, request_digest=request.digest, before=before, before_digest=before.digest,
                after=after, after_digest=None if after is None else after.digest)


def test_genuine_native_chain_cannot_authorize_fixture_edit(media):
    root, before, _ = media
    original = (root / "before.mp4").read_bytes()
    d, p = parents(before)
    assert d.to_dict()["choice"] == "KEEP_ORIGINAL"
    assert d.to_dict()["grounded_fault"] is None
    row = before.to_dict()["measured_findings"]["frames"][0]
    assert row["minimum"] == row["maximum"] == 0 and "defect" not in row
    assert row["time"]["observed_source_frame_index"] is None
    receipt = adapter.assess_offline_attempt(**attempt_sources(d, p))
    value = receipt.to_dict()
    assert value["status"] == "HOLD" and value["evidence_domain"] == "SYNTHETIC"
    assert value["original_asset_sha256"] == hashlib.sha256(original).hexdigest()
    assert value["output_asset_sha256"] is value["actual_bounded_location"] is None
    assert value["output_created"] is value["input_bytes_verified_by_adapter"] is False
    assert adapter.OBSTACLE in value["reason_codes"]
    assert not adapter.SUPPORTED_OPERATIONS
    assert (root / "before.mp4").read_bytes() == original
    assert {p.name for p in root.iterdir()} == {"before.mp4", "alternate.mp4"}


def test_attempt_immutable_closed_replay(media):
    _, before, _ = media
    d, p = parents(before)
    args = attempt_sources(d, p)
    held = adapter.assess_offline_attempt(**args)
    assert adapter.BQ04bOfflineAttemptReceipt.from_json(held.to_json(), **args).digest == held.digest
    with pytest.raises(FrozenInstanceError):
        held._json = "forged"
    detached = held.to_dict()
    detached["reason_codes"].clear()
    assert held.to_dict()["reason_codes"]


@pytest.mark.parametrize("key,value", [
    ("schema_version", "BQ04bOfflineAttemptReceipt.v0"), ("output_asset_sha256", "1" * 64),
    ("original_asset_sha256", "0" * 64), ("output_created", True), ("status", "SUCCEEDED"),
    ("actual_bounded_location", "C:/Users/private.mp4"), ("evidence_domain", "OBSERVED_PRODUCTION"),
    ("quality_pass", True), ("release_authorization", "GO_PUBLISH"),
    ("spend_authorization", "GRANTED"), ("extra", "self-attested Q4 OBSERVED"),
    ("decision_digest", "2" * 64), ("preservation_plan_digest", "3" * 64),
    ("input_bytes_verified_by_adapter", True), ("operation_version", "IMPLEMENTED"),
])
def test_forged_receipt_rejected(media, key, value):
    _, before, _ = media
    d, p = parents(before)
    args = attempt_sources(d, p)
    raw = adapter.assess_offline_attempt(**args).to_dict()
    raw[key] = value
    with pytest.raises(critic.Error, match="replay mismatch"):
        adapter.BQ04bOfflineAttemptReceipt.from_dict(raw, **args)


@pytest.mark.parametrize("context", [
    {"rights_violation": True}, {"claim_mismatch": True}, {"identity_mismatch": True},
    {"unknown_provider_effect": True}, {"grounded_defect": {"kind": "black", "time": "0.1"}},
    {"ungrounded_defect": True}, {"plan_revision": True},
    {"script": "$(Remove-Item -Recurse C:/); publish now", "rights": True},
])
def test_native_p0_unknown_effect_self_attestation_never_executes(media, context):
    _, before, _ = media
    d, p = parents(before, context=context)
    value = adapter.assess_offline_attempt(**attempt_sources(d, p, decision_context=context)).to_dict()
    assert value["status"] == "HOLD" and value["output_asset_sha256"] is None
    assert value["external_effects"] == value["release_authorization"] == "NONE"


@pytest.mark.parametrize("operation", preservation.SUPPORTED_EDITORS)
def test_no_editor_claim_can_masquerade_as_executed(media, operation):
    _, before, _ = media
    d, p = parents(before)
    value = adapter.assess_offline_attempt(**attempt_sources(d, p), operation=operation).to_dict()
    assert value["status"] == "HOLD" and value["supported_operations"] == []
    assert "EXACT_TARGET_LOCALIZATION_UNPROVEN" in value["reason_codes"]
    assert "NO_PRESERVATION_TEST_ORACLE" in value["reason_codes"]


@pytest.mark.parametrize("operation", ["ffmpeg", "sh -c x", "UNKNOWN_EFFECT", "../edit", None, True])
def test_unsupported_operation_rejected(media, operation):
    _, before, _ = media
    d, p = parents(before)
    with pytest.raises(critic.Error, match="unsupported operation"):
        adapter.assess_offline_attempt(**attempt_sources(d, p), operation=operation)


@pytest.mark.parametrize("path", ["../outside.mp4", "C:/Users/private.mp4", "https://example/x", "link.mp4"])
def test_adapter_has_no_path_or_sandbox_escape_ingress(media, path):
    _, before, _ = media
    d, p = parents(before)
    with pytest.raises(TypeError):
        adapter.assess_offline_attempt(**attempt_sources(d, p), path=path)


@pytest.mark.parametrize("case", ["digest", "parent", "dictionary", "version", "invariant"])
def test_invalid_native_sources_rejected(media, case):
    _, before, _ = media
    d, p = parents(before)
    args = attempt_sources(d, p)
    if case == "digest":
        args["plan_digest"] = "0" * 64
    elif case == "parent":
        other, _ = parents(before, context={"rights_violation": True})
        args.update(decision=other, decision_digest=other.digest, decision_context={"rights_violation": True})
    elif case == "dictionary":
        args["decision"] = d.to_dict()
    else:
        raw = p.to_dict()
        if case == "version":
            raw["schema_version"] = "BQ04aPreservationPlan.v0"
        else:
            raw["non_target_invariants"]["source_provenance"] = False
        # Deliberately forged in-process native-shaped object, never a positive fixture.
        forged = object.__new__(preservation.BQ04aPreservationPlan)
        object.__setattr__(forged, "_decision", d)
        object.__setattr__(forged, "_json", critic._canonical(raw))
        args.update(plan=forged, plan_digest=forged.digest)
    with pytest.raises(critic.Error):
        adapter.assess_offline_attempt(**args)


@pytest.mark.parametrize("after_kind", ["missing", "same", "alternate"])
def test_independent_native_measurements_do_not_prove_target_or_preservation(media, after_kind):
    root, before, alternate = media
    after = {"missing": None, "same": before, "alternate": alternate}[after_kind]
    args = bridge_sources(before, after=after)
    result = evidence.verify_technical_evidence(**args)
    value = result.to_dict()
    assert value["status"] == "IMPROVEMENT_UNPROVEN"
    assert value["preservation_status"] == "PRESERVATION_UNPROVEN"
    assert value["target_corrected"] is value["all_invariants_unregressed"] is False
    assert value["candidate_output_sha256"] is None and value["current_file_identity"] == "UNKNOWN"
    assert value["evidence_domain"] == "SYNTHETIC"
    assert value["product_identity_and_claims"] == value["actor_voice_likeness_rights"] == "UNKNOWN"
    assert value["story_and_semantics"] == value["human_calibration"] == "UNKNOWN"
    assert value["brain_media_pass"] is value["quality_pass"] is False
    if after_kind == "alternate":
        assert "ALTERNATE_ASSET_HAS_NO_TARGET_OR_CANDIDATE_LINK" in value["reason_codes"]
        assert value["after_native_measurement_pin"] == hashlib.sha256((root / "alternate.mp4").read_bytes()).hexdigest()
    assert evidence.BQ04bTechnicalVerification.from_json(result.to_json(), **args).digest == result.digest
    closure = reinspection.evaluate_repair_closure(args["request"], request_digest=args["request"].digest,
        after_review=after, after_review_digest=None if after is None else after.digest,
        after_asset_sha256=None if after is None else after.to_dict()["observed_asset_sha256"])
    assert closure.to_dict()["target_corrected"] is False  # Existing policy remains authoritative.


@pytest.mark.parametrize("key,value", [
    ("target_corrected", True), ("technical_invariants_unregressed", True),
    ("all_invariants_unregressed", True), ("quality_pass", True), ("brain_media_pass", True),
    ("status", "PASS"), ("evidence_domain", "OBSERVED_PRODUCTION"),
    ("candidate_output_sha256", "2" * 64), ("current_file_identity", "VERIFIED"),
    ("release_authorization", "GO_PUBLISH"), ("actor_voice_likeness_rights", "VERIFIED"),
    ("extra", "signed-looking receipt"),
])
def test_false_technical_quality_and_authority_replay_rejected(media, key, value):
    _, before, alternate = media
    args = bridge_sources(before, after=alternate)
    raw = evidence.verify_technical_evidence(**args).to_dict()
    raw[key] = value
    with pytest.raises(critic.Error, match="replay mismatch"):
        evidence.BQ04bTechnicalVerification.from_dict(raw, **args)


@pytest.mark.parametrize("case", ["before_pin", "after_pin", "after_dict", "request_parent", "disabled_probes"])
def test_injected_q4_request_or_after_rejected(media, case):
    _, before, alternate = media
    args = bridge_sources(before, after=alternate)
    if case == "before_pin":
        args["before_digest"] = "0" * 64
    elif case == "after_pin":
        args["after_digest"] = "0" * 64
    elif case == "after_dict":
        args["after"] = alternate.to_dict()
    elif case == "request_parent":
        d, p = parents(alternate)
        req = reinspection.request_reinspection(p, plan_digest=p.digest)
        args.update(request=req, request_digest=req.digest)
    else:
        req = reinspection.request_reinspection(args["plan"], plan_digest=args["plan"].digest, expected_probes=[])
        args.update(request=req, request_digest=req.digest)
    with pytest.raises(critic.Error):
        evidence.verify_technical_evidence(**args)


def test_native_shaped_injected_localized_defect_is_rejected(media):
    _, before, _ = media
    raw = deepcopy(before._sampled.to_dict())
    raw["frames"][0]["defect"] = True
    sampled = object.__new__(t2.BQ02bT2SelectiveEvidence)
    object.__setattr__(sampled, "_source", before._sampled._source)
    object.__setattr__(sampled, "_authenticated_json", critic._canonical(raw))
    critic._Record.__init__(sampled, critic._canonical(raw))
    injected = mm.multimodal_review(before._review, sampled,
        original_review_digest=before._review.digest, t2_digest=sampled.digest)
    d, p = parents(injected)
    assert d.to_dict()["choice"] == "PROPOSE_LOCAL_DETERMINISTIC_EDIT"
    with pytest.raises(critic.Error, match="injected finding"):
        adapter.assess_offline_attempt(**attempt_sources(d, p))


def test_no_effects_even_when_paths_links_or_source_bytes_change(media, tmp_path, monkeypatch):
    import builtins
    import socket
    import subprocess
    root, before, alternate = media
    args = bridge_sources(before, after=alternate)
    original = (root / "before.mp4").read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("pure HOLD/evidence bridge attempted an external or filesystem effect")
    # Filesystem hazards cannot enter either product API; no file operation is attempted.
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(subprocess, "run", forbidden)
        guard.setattr(socket, "socket", forbidden)
        result = evidence.verify_technical_evidence(**args)
    assert result.to_dict()["current_file_identity"] == "UNKNOWN"
    assert (root / "before.mp4").read_bytes() == original
    assert list(tmp_path.iterdir()) == []


def test_no_claim_of_current_identity_after_actual_fixture_mutation(media, tmp_path):
    root, before, _ = media
    # Mutate a disposable copy only, never the inspected original. The pure
    # sidecar must explicitly decline fresh-byte identity in either case.
    copied = tmp_path / "copy.mp4"
    copied.write_bytes((root / "before.mp4").read_bytes() + b"changed")
    result = evidence.verify_technical_evidence(**bridge_sources(before, after=before)).to_dict()
    assert hashlib.sha256(copied.read_bytes()).hexdigest() != result["original_asset_sha256"]
    assert result["current_file_identity"] == "UNKNOWN" and result["target_corrected"] is False


def test_creative_exemptions_remain_native_and_non_authorizing(media):
    _, before, _ = media
    d, p = parents(before)
    assert "NULL_PRODUCT_EXEMPTED" in d.to_dict()["exemptions"]
    assert before._sampled.to_dict()["audio_status"] == "NOT_APPLICABLE"
    assert before.to_dict()["original_review"]["preflight"]["plan"]["visual_form"] == "ANIMATION"
    assert adapter.assess_offline_attempt(**attempt_sources(d, p)).to_dict()["status"] == "HOLD"


def owner_groups():
    # Read-only executor preview. Trusted EF02 must independently reconstruct
    # baseline owners from the pinned Git tree; these bytes cannot attest it.
    groups = list(minimum_test.approved_groups())
    for path in sorted((ROOT / "work_items/mt01-components").glob("*.json")):
        if path.name == "bq-04b.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        groups.append(minimum_test.TestGroup(data["component"], tuple(data["tests"]), tuple(data["acs"]),
            tuple(data["sources"]), tuple(data["consumers"]), (*data["contracts"], path.relative_to(ROOT).as_posix())))
    return tuple(groups)


def test_native_mt01_manifest_and_readonly_consumer_closure():
    wi = json.loads((ROOT / WI_PATH).read_text(encoding="utf-8"))
    data = json.loads((ROOT / MANIFEST_PATH).read_text(encoding="utf-8"))
    groups = owner_groups()
    base = tuple(p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")
                 if p.relative_to(ROOT).as_posix() not in wi["allowed_paths"])
    admitted = impact_v21.validate_component_onboarding_manifest(data, manifest_path=MANIFEST_PATH,
        changed_paths=wi["allowed_paths"], base_paths=base, existing_groups=groups,
        allowed_paths=wi["allowed_paths"], root=ROOT)
    new = minimum_test.TestGroup(admitted["name"], admitted["tests"], admitted["acs"],
        admitted["paths"], admitted["consumers"], (MANIFEST_PATH,))
    selection = impact_v21.select_impact(root=ROOT, groups=(*groups, new), changed_paths=wi["allowed_paths"])
    assert selection.runnable, selection.as_dict()
    assert set(selection.selected_groups) == {"a13", "bq-02a", "bq-02b", "bq-04a", "bq-04b"}
    assert "ONBOARD_CONSUMER_OWNER_CLOSURE" in selection.reason_codes
    assert wi["verification_profile"] == "mt01-component-onboarding"
    assert data["contracts"] == [] and len(wi["allowed_paths"]) == 7
