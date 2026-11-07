"""R13 contracts: actual synthetic media, native aliases, metadata injection and trusted oracle.

Mandatory hardlink/junction creation failures are typed environment failures,
never skips. Symlink-tag injection tests the real verifier without pretending
to instantiate a privileged OS symlink. optional_native_symlink_probe is a
separate, explicitly unaccepted capability probe, not a mandatory pytest case.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import FrozenInstanceError
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_commerce.affiliate_release import inspection as a13
from ai_commerce.creative_quality import critic, inspection_evidence as t1, quality_review as qr
from ai_commerce.creative_quality import multimodal_review as mm, t2_sampling as t2
from ai_commerce.creative_quality import repair_policy as policy, repair_preservation as preservation
from ai_commerce.creative_quality import repair_reinspection as reinspection
from ai_commerce.creative_quality import repair_offline_adapter as adapter
from ai_commerce.creative_quality import repair_verified_evidence as evidence
from ai_commerce.engineering import impact_v21, minimum_test
from test_bq_02a_critic_t1 import make_mp4, make_plan

ROOT = Path(__file__).resolve().parents[1]
WI_PATH = "work_items/BQ-04B_OFFLINE_REPAIR_001.json"
MANIFEST_PATH = "work_items/mt01-components/bq-04b.json"


def preflight():
    plan = make_plan()
    return critic.preflight_critique(plan, plan_digest=plan.digest)


def measured_existing(root, asset):
    t1_record = t1.inspect_local_t1(root, [asset], diagnostics=a13.DiagnosticOptions(black=True))
    sampled = t2.sample_local_t2(t1_record,
        t2.SamplingRequest(asset.asset_ref, asset.expected_sha256, ("1/10", "3/10")),
        root=root, allow_local_probe=True)
    assert t1_record.to_dict()["status"] == "PASS"
    assert sampled.to_dict()["status"] == "OBSERVED"
    pre = preflight()
    review = qr.adversarial_review(pre, preflight_digest=pre.digest,
        technical_evidence=t1_record, technical_evidence_digest=t1_record.digest)
    return mm.multimodal_review(review, sampled, original_review_digest=review.digest, t2_digest=sampled.digest)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp("historical")
    first = measured_existing(root, make_mp4(root, name="before.mp4"))
    other = measured_existing(root, make_mp4(root, name="alternate.mp4", segmented=True))
    assert first.to_dict()["observed_asset_sha256"] != other.to_dict()["observed_asset_sha256"]
    return root, first, other


def parents(review, *, context=None):
    decision = policy.decide_repair(review, review_digest=review.digest,
        asset_sha256=review.to_dict()["observed_asset_sha256"], context=context)
    return decision, preservation.create_preservation_plan(decision, decision_digest=decision.digest)


def attempt_sources(decision, plan, **extra):
    return dict(decision=decision, plan=plan, decision_digest=decision.digest, plan_digest=plan.digest, **extra)


def bridge_sources(before, *, after=None):
    decision, plan = parents(before)
    sources = attempt_sources(decision, plan)
    attempt = adapter.assess_offline_attempt(**sources)
    request = reinspection.request_reinspection(plan, plan_digest=plan.digest)
    return dict(**sources, attempt=attempt, attempt_digest=attempt.digest, request=request,
        request_digest=request.digest, before=before, before_digest=before.digest,
        after=after, after_digest=None if after is None else after.digest)


@contextmanager
def executed(scratch):
    calls = []
    run = a13.BoundedToolRunner.run
    def observe(runner, argv, **kwargs):
        completed = run(runner, argv, **kwargs)
        if Path(runner.directory).is_relative_to(scratch):
            calls.append(dict(argv=list(argv), stdout_sha256=hashlib.sha256(completed.stdout).hexdigest(),
                stdout_bytes=len(completed.stdout), stderr_bytes=len(completed.stderr)))
        return completed
    with patch.object(a13.BoundedToolRunner, "run", observe), adapter.synthetic_fixture(scratch_root=scratch) as fixture:
        before = adapter.inspect_fixture(fixture, preflight=preflight())
        decision, plan = adapter.fixture_parents(fixture, before)
        sources = attempt_sources(decision, plan, operation=adapter.OPERATION, decision_context=adapter.CONTEXT)
        source_bytes = (fixture.root / "fixture.mp4").read_bytes()
        attempt = adapter.execute_offline_repair(**sources, fixture=fixture, root=fixture.root,
            input_path=fixture.root / "fixture.mp4", output_path=fixture.root / "repaired.mp4")
        output_bytes = (fixture.root / "repaired.mp4").read_bytes()
        marker = len(calls)
        after = adapter.inspect_fixture(fixture, preflight=preflight(), output=True)
        assert (fixture.root / "fixture.mp4").read_bytes() == source_bytes
        assert (fixture.root / "repaired.mp4").read_bytes() == output_bytes
        assert all("lavfi" not in call["argv"] for call in calls[marker:])
        request = reinspection.request_reinspection(plan, plan_digest=plan.digest)
        yield fixture, dict(**sources, attempt=attempt, attempt_digest=attempt.digest,
            request=request, request_digest=request.digest, before=before, before_digest=before.digest,
            after=after, after_digest=after.digest, fixture=fixture, target_oracle=adapter.ORACLE), calls


@pytest.fixture(scope="module")
def operation_bundle(tmp_path_factory):
    with executed(tmp_path_factory.mktemp("executed")) as bundle:
        yield bundle


def test_genuine_native_chain_cannot_authorize_fixture_edit(media):
    decision, plan = parents(media[1])
    path = media[0] / "before.mp4"
    original = path.read_bytes()
    result = adapter.assess_offline_attempt(**attempt_sources(decision, plan)).to_dict()
    assert decision.to_dict()["choice"] == "KEEP_ORIGINAL"
    assert decision.to_dict()["grounded_fault"] is None
    assert result["status"] == "HOLD" and result["evidence_domain"] == "SYNTHETIC"
    assert not result["output_created"] and result["output_asset_sha256"] is None
    assert path.read_bytes() == original


def test_attempt_immutable_closed_replay(media):
    sources = attempt_sources(*parents(media[1]))
    record = adapter.assess_offline_attempt(**sources)
    for replay in (adapter.BQ04bOfflineAttemptReceipt.from_dict(record.to_dict(), **sources),
                   adapter.BQ04bOfflineAttemptReceipt.from_json(record.to_json(), **sources)):
        assert replay.to_json() == record.to_json()
    with pytest.raises(FrozenInstanceError):
        record._sealed = "forged"


@pytest.mark.parametrize("key,value", [("status", "EXECUTED"), ("output_asset_sha256", "0"*64),
    ("output_created", True), ("quality_pass", True), ("release_authorization", "GO_LIVE"),
    ("spend_authorization", "UNLIMITED")])
def test_forged_receipt_rejected(media, key, value):
    sources = attempt_sources(*parents(media[1]))
    data = adapter.assess_offline_attempt(**sources).to_dict()
    data[key] = value
    with pytest.raises(critic.Error, match="replay mismatch"):
        adapter.BQ04bOfflineAttemptReceipt.from_dict(data, **sources)


@pytest.mark.parametrize("context", [{"rights_violation": True}, {"claim_mismatch": True},
    {"identity_mismatch": True}, {"unknown_provider_effect": True}])
def test_native_p0_unknown_effect_self_attestation_never_executes(media, context):
    decision, plan = parents(media[1], context=context)
    assert decision.to_dict()["choice"] == "HOLD"
    result = adapter.assess_offline_attempt(**attempt_sources(decision, plan, decision_context=context)).to_dict()
    assert result["status"] == "HOLD" and "BQ04A_DECISION_NOT_LOCAL_EDIT" in result["reason_codes"]


@pytest.mark.parametrize("operation", ["NONE", "OFFLINE_DETERMINISTIC_TRIM"])
def test_no_editor_claim_can_masquerade_as_executed(media, operation):
    sources = attempt_sources(*parents(media[1]), operation=operation)
    assert adapter.assess_offline_attempt(**sources).to_dict()["status"] == "HOLD"


def test_unsupported_operation_rejected(media):
    with pytest.raises(critic.Error, match="unsupported operation"):
        adapter.assess_offline_attempt(**attempt_sources(*parents(media[1]), operation="ARBITRARY_AI_INPAINT"))


@pytest.mark.parametrize("path", ["../escape.mp4", "before.mp4:ads_stream", "\\\\network\\share\\file.mp4",
    "/root/forbidden.mp4", "C:/outside.mp4"])
def test_adapter_has_no_path_or_sandbox_escape_ingress(media, path):
    with pytest.raises(adapter.UnsafePathError):
        adapter._validate_safe_path(media[0], path)


def test_invalid_native_sources_rejected(media):
    decision, plan = parents(media[1])
    wrong, _ = parents(media[2])
    with pytest.raises(critic.Error, match="digest"):
        adapter.assess_offline_attempt(decision, plan, decision_digest="bad_digest", plan_digest=plan.digest)
    with pytest.raises(adapter.ParentBindingError, match="decision/plan parent or original SHA mismatch"):
        adapter.assess_offline_attempt(**attempt_sources(wrong, plan))


@pytest.mark.parametrize("after_kind", [None, "same", "alternate"])
def test_independent_native_measurements_do_not_prove_target_or_preservation(media, after_kind):
    after = None if after_kind is None else media[1 if after_kind == "same" else 2]
    result = evidence.verify_technical_evidence(**bridge_sources(media[1], after=after)).to_dict()
    assert result["status"] == "IMPROVEMENT_UNPROVEN" and result["preservation_status"] == "PRESERVATION_UNPROVEN"
    assert not result["target_corrected"] and not result["technical_invariants_unregressed"]
    assert not result["quality_pass"] and result["release_authorization"] == "NONE"


@pytest.mark.parametrize("key,value", [("quality_pass", True), ("release_authorization", "GO_LIVE"),
    ("spend_authorization", "AUTHORIZED"), ("candidate_output_sha256", "f"*64),
    ("target_corrected", True), ("technical_invariants_unregressed", True)])
def test_false_technical_quality_and_authority_replay_rejected(media, key, value):
    sources = bridge_sources(media[1], after=media[2])
    data = evidence.verify_technical_evidence(**sources).to_dict()
    data[key] = value
    with pytest.raises(critic.Error, match="replay mismatch"):
        evidence.BQ04bTechnicalVerification.from_dict(data, **sources)


def test_no_effects_even_when_paths_links_or_source_bytes_change(media, tmp_path, monkeypatch):
    import builtins
    import socket
    def forbidden(*args, **kwargs):
        pytest.fail("pure bridge attempted I/O")
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(socket, "socket", forbidden)
        result = evidence.verify_technical_evidence(**bridge_sources(media[1], after=media[2])).to_dict()
    assert result["current_file_identity"] == "UNKNOWN" and not result["target_corrected"]
    assert not list(tmp_path.iterdir())


def test_no_claim_of_current_identity_after_actual_fixture_mutation(tmp_path):
    asset = make_mp4(tmp_path, name="before.mp4")
    before = measured_existing(tmp_path, asset)
    path = tmp_path / asset.asset_ref
    path.write_bytes(path.read_bytes() + b"changed-after-Q4")
    assert hashlib.sha256(path.read_bytes()).hexdigest() != asset.expected_sha256
    result = evidence.verify_technical_evidence(**bridge_sources(before, after=before)).to_dict()
    assert result["current_file_identity"] == "UNKNOWN" and not result["target_corrected"]


def test_creative_exemptions_remain_native_and_non_authorizing(media):
    decision, plan = parents(media[1])
    assert "NULL_PRODUCT_EXEMPTED" in decision.to_dict()["exemptions"]
    assert media[1].to_dict()["sampled_evidence"]["audio_status"] == "NOT_APPLICABLE"
    assert media[1].to_dict()["original_review"]["preflight"]["plan"]["visual_form"] == "ANIMATION"
    assert adapter.assess_offline_attempt(**attempt_sources(decision, plan)).to_dict()["status"] == "HOLD"


def test_real_media_local_deterministic_repair(operation_bundle, tmp_path):
    fixture, sources, calls = operation_bundle
    result = evidence.verify_technical_evidence(**sources).to_dict()
    assert result["status"] == "TECHNICAL_SUCCESS", result["reason_codes"]
    assert result["target_corrected"] and result["technical_invariants_unregressed"]
    assert result["current_file_identity"] == "CURRENT_FILE_BYTES_VERIFIED_DURING_REINSPECTION"
    assert result["candidate_output_sha256"] != result["original_asset_sha256"]
    assert result["measurements"]["target_before_maximum"] == [0, 0]
    assert result["measurements"]["target_after_minimum"] == [255, 255]
    assert not result["quality_pass"] and result["preservation_status"] == "PRESERVATION_UNPROVEN"
    assert result["product_identity_and_claims"] == result["story_and_semantics"] == "UNKNOWN"
    assert result["release_authorization"] == result["spend_authorization"] == "NONE"
    record = dict(kind="BQ04B_R13_ACTUAL_SYNTHETIC_TECHNICAL_ONLY", before=sources["before"].to_dict(),
        after=sources["after"].to_dict(), attempt=sources["attempt"].to_dict(), verification=result,
        actual_tool_calls=calls, native_symlink_effect="SYMLINK_NATIVE_EFFECT_UNPROVEN",
        original_mp4_base64=base64.b64encode((fixture.root / "fixture.mp4").read_bytes()).decode(),
        output_mp4_base64=base64.b64encode((fixture.root / "repaired.mp4").read_bytes()).decode(),
        test_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (tmp_path / "bq04b-real-measurements.json").write_text(json.dumps(record, sort_keys=True), encoding="utf-8")


def test_real_repair_rejects_alias_and_noop(tmp_path):
    with adapter.synthetic_fixture(scratch_root=tmp_path) as fixture:
        before = adapter.inspect_fixture(fixture, preflight=preflight())
        decision, plan = adapter.fixture_parents(fixture, before)
        assert decision.to_dict()["choice"] == "PROPOSE_LOCAL_DETERMINISTIC_EDIT"
        assert plan.to_dict()["status"] == "PRESERVATION_PLAN_ESTABLISHED"
        args = attempt_sources(decision, plan, operation=adapter.OPERATION, decision_context=adapter.CONTEXT)
        path = fixture.root / "fixture.mp4"
        original = path.read_bytes()
        with pytest.raises(critic.Error, match="cannot alias"):
            adapter.execute_offline_repair(**args, fixture=fixture, root=fixture.root, input_path=path, output_path=path)
        with pytest.raises(critic.Error, match="operation/scope/oracle/parent"):
            adapter.execute_offline_repair(**{**args, "operation": "NONE"}, fixture=fixture,
                root=fixture.root, input_path=path, output_path=fixture.root / "repaired.mp4")
        assert path.read_bytes() == original and not (fixture.root / "repaired.mp4").exists()


@pytest.mark.parametrize("key,value", [("status", "HOLD"), ("output_created", False),
    ("output_asset_sha256", None), ("output_asset_sha256", "f"*64), ("command_hash", "e"*64),
    ("operation", "NONE"), ("actual_bounded_location", "C:/alien"), ("attempt_id", "alien"),
    ("decision_digest", "d"*64), ("preservation_plan_digest", "d"*64), ("before_review_digest", "d"*64),
    ("input_bytes_verified_by_adapter", False), ("authority", {"publication": "GO_LIVE"}), ("unexpected", True)])
def test_executed_receipt_provenance_tamper(operation_bundle, key, value):
    _, args, _ = operation_bundle
    receipt = args["attempt"]
    sources = {key: args[key] for key in ("decision", "plan", "decision_digest", "plan_digest", "operation", "decision_context")}
    assert adapter.BQ04bOfflineAttemptReceipt.from_dict(receipt.to_dict(), witness=receipt, **sources) is receipt
    with pytest.raises(critic.Error, match="replay mismatch"):
        adapter.BQ04bOfflineAttemptReceipt.from_dict({**receipt.to_dict(), key: value}, witness=receipt, **sources)


def test_serialized_execution_is_not_a_witness(operation_bundle):
    _, args, _ = operation_bundle
    sources = {key: args[key] for key in ("decision", "plan", "decision_digest", "plan_digest", "operation", "decision_context")}
    for witness in (None, args["attempt"].to_dict()):
        with pytest.raises(critic.Error, match="native execution witness"):
            adapter.BQ04bOfflineAttemptReceipt.from_dict(args["attempt"].to_dict(), witness=witness, **sources)


@pytest.mark.parametrize("change", ["no_fixture", "no_oracle", "untrusted_oracle", "stale_after", "missing_after"])
def test_stale_recordings_and_missing_localized_oracle_hold(operation_bundle, change):
    _, sources, _ = operation_bundle
    args = dict(sources)
    if change == "no_fixture":
        args["fixture"] = None
    elif change == "no_oracle":
        args["target_oracle"] = None
    elif change == "untrusted_oracle":
        args["target_oracle"] = {"verified": True}
    elif change == "stale_after":
        args.update(after=args["before"], after_digest=args["before_digest"])
    else:
        args.update(after=None, after_digest=None)
    result = evidence.verify_technical_evidence(**args).to_dict()
    assert result["status"] == "IMPROVEMENT_UNPROVEN" and not result["target_corrected"]
    assert result["current_file_identity"] == "UNKNOWN"


def test_changed_actual_source_bytes_reject_edit(tmp_path):
    with adapter.synthetic_fixture(scratch_root=tmp_path) as fixture:
        before = adapter.inspect_fixture(fixture, preflight=preflight())
        sources = attempt_sources(*adapter.fixture_parents(fixture, before),
            operation=adapter.OPERATION, decision_context=adapter.CONTEXT)
        path = fixture.root / "fixture.mp4"
        path.write_bytes(path.read_bytes() + b"mutation")
        with pytest.raises(critic.Error, match="source bytes changed"):
            adapter.execute_offline_repair(**sources, fixture=fixture, root=fixture.root,
                input_path=path, output_path=fixture.root / "repaired.mp4")
        assert not (fixture.root / "repaired.mp4").exists()


@pytest.mark.parametrize("which", ["fixture.mp4", "repaired.mp4"])
def test_current_mutated_bytes_reinspection_hold(tmp_path, which):
    with executed(tmp_path) as (fixture, args, _):
        path = fixture.root / which
        path.write_bytes(path.read_bytes() + b"stale-receipt")
        result = evidence.verify_technical_evidence(**args).to_dict()
        assert result["status"] == "IMPROVEMENT_UNPROVEN" and result["current_file_identity"] == "UNKNOWN"
        assert any("SHA mismatch" in reason for reason in result["reason_codes"])


def _assert_link_rejected_before_reads(args, monkeypatch, expected):
    def forbidden(*args, **kwargs):
        pytest.fail("unsafe path reached media read/hash/tool/snapshot")
    for name in ("_hash", "_tools", "_runner", "_frames"):
        monkeypatch.setattr(adapter, name, forbidden)
    monkeypatch.setattr(a13.media_files, "open_regular", forbidden)
    monkeypatch.setattr(t2, "_snapshot_lock", forbidden)
    result = evidence.verify_technical_evidence(**args).to_dict()
    assert result["status"] == "IMPROVEMENT_UNPROVEN"
    assert result["path_rejection_code"] == expected
    assert result["current_file_identity"] == "UNKNOWN"
    assert not result["target_corrected"] and not result["technical_invariants_unregressed"]
    assert result["measurements"] is None and not result["quality_pass"]


def _create_mandatory_link(kind, link, target):
    try:
        if kind == "hardlink":
            os.link(target, link)
        else:
            link_path = str(Path(link).resolve())
            target_path = str(Path(target).resolve())
            completed = subprocess.run(
                f'cmd /d /s /c "mklink /J "{link_path}" "{target_path}""',
                capture_output=True, timeout=10, text=True
            )
            if completed.returncode:
                raise OSError(completed.stderr[:200] or completed.stdout[:200])
    except OSError as exc:
        pytest.fail("NATIVE_LINK_ENVIRONMENT_HOLD:" + kind + ":" + str(exc), pytrace=False)


@pytest.mark.parametrize("kind", ["hardlink", "junction"])
def test_current_link_alias_reinspection_hold(tmp_path, kind, monkeypatch, record_property):
    with executed(tmp_path) as (fixture, args, _):
        source = fixture.root / "fixture.mp4"
        output = fixture.root / "repaired.mp4"
        before_source, before_output = source.read_bytes(), output.read_bytes()
        saved = tmp_path / "saved.mp4"
        output.rename(saved)
        try:
            _create_mandatory_link(kind, output, saved if kind == "hardlink" else tmp_path)
            metadata = output.lstat()
            if kind == "hardlink":
                assert metadata.st_nlink == 2 and stat.S_ISREG(metadata.st_mode)
            else:
                assert metadata.st_file_attributes & 0x400
                assert metadata.st_reparse_tag == 0xA0000003
            record_property("native_alias", kind)
            record_property("observed_reparse_tag", getattr(metadata, "st_reparse_tag", 0))
            with monkeypatch.context() as guard:
                _assert_link_rejected_before_reads(args, guard,
                    "LINK_MULTIPLICITY" if kind == "hardlink" else "LINK_OR_REPARSE")
            assert source.read_bytes() == before_source and saved.read_bytes() == before_output
        finally:
            if os.path.lexists(output):
                if kind == "junction":
                    os.rmdir(output)  # Only this exact test-owned junction.
                else:
                    output.unlink()
            saved.rename(output)
        assert source.read_bytes() == before_source and output.read_bytes() == before_output


METADATA_CASES = [
    ("symlink", stat.S_IFREG, 0x400, 0xA000000C, 1),
    ("symlink_tag_only", stat.S_IFREG, 0, 0xA000000C, 1),
    ("junction", stat.S_IFDIR, 0x400, 0xA0000003, 1),
    ("unknown_tag", stat.S_IFREG, 0, 0x80001234, 1),
    ("attribute_only", stat.S_IFREG, 0x400, 0, 1),
    ("symlink_mode_only", stat.S_IFLNK, 0, 0, 1),
    ("hardlink", stat.S_IFREG, 0, 0, 2),
]


@pytest.mark.parametrize("kind,mode,attributes,tag,links", METADATA_CASES, ids=[row[0] for row in METADATA_CASES])
@pytest.mark.parametrize("location", ["source", "output", "ancestor"])
def test_current_symlink_tag_reinspection_hold(operation_bundle, monkeypatch, record_property,
                                               location, kind, mode, attributes, tag, links):
    fixture, args, _ = operation_bundle
    source, output = fixture.root / "fixture.mp4", fixture.root / "repaired.mp4"
    original_source, original_output = source.read_bytes(), output.read_bytes()
    path = source if location == "source" else output if location == "output" else fixture.root
    if location == "ancestor" and kind == "hardlink":
        # Directory link counts are not file hardlinks. Exercise a reparse tag
        # for this ancestor case; native hardlink proof is on regular files.
        tag = 0xA000000C
    lstat = Path.lstat
    def injected(candidate, *args, **kwargs):
        info = lstat(candidate, *args, **kwargs)
        if candidate != path:
            return info
        return SimpleNamespace(st_mode=mode, st_file_attributes=attributes, st_reparse_tag=tag, st_nlink=links)
    with monkeypatch.context() as guard:
        guard.setattr(Path, "lstat", injected)
        _assert_link_rejected_before_reads(args, guard,
            "LINK_MULTIPLICITY" if kind == "hardlink" and location != "ancestor" else "LINK_OR_REPARSE")
    assert source.read_bytes() == original_source and output.read_bytes() == original_output
    record_property("provenance", "INJECTED_METADATA_REAL_VERIFIER_NO_NATIVE_SYMLINK")
    record_property("symlink_native_effect", "SYMLINK_NATIVE_EFFECT_UNPROVEN")


@pytest.mark.parametrize("kind,mode,attributes,tag,links", METADATA_CASES, ids=[row[0] for row in METADATA_CASES])
def test_link_metadata_rejected_before_media_tools(tmp_path, monkeypatch, kind, mode, attributes, tag, links):
    path = tmp_path / "file.mp4"
    path.write_bytes(b"metadata negative; not an OS link")
    lstat = Path.lstat
    def injected(candidate, *args, **kwargs):
        return (SimpleNamespace(st_mode=mode, st_file_attributes=attributes, st_reparse_tag=tag, st_nlink=links)
                if candidate == path else lstat(candidate, *args, **kwargs))
    with monkeypatch.context() as guard:
        guard.setattr(Path, "lstat", injected)
        with pytest.raises(adapter.UnsafePathError) as error:
            adapter._validate_safe_path(tmp_path, path, must_exist=True)
        assert error.value.code == ("LINK_MULTIPLICITY" if kind == "hardlink" else "LINK_OR_REPARSE")
    assert path.read_bytes() == b"metadata negative; not an OS link"


def test_ordinary_metadata_control_runs_actual_reinspection(operation_bundle, monkeypatch):
    # A constant HOLD stub cannot satisfy this paired positive control.
    fixture, args, _ = operation_bundle
    lstat = Path.lstat
    observed = []
    def unchanged(path, *a, **kw):
        observed.append(path)
        return lstat(path, *a, **kw)
    monkeypatch.setattr(Path, "lstat", unchanged)
    result = evidence.verify_technical_evidence(**args).to_dict()
    assert result["status"] == "TECHNICAL_SUCCESS" and fixture.root / "repaired.mp4" in observed


def test_authority_before_path_effects(media, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("non-actionable plan touched path/tool")
    monkeypatch.setattr(adapter, "_validate_safe_path", forbidden)
    monkeypatch.setattr(adapter, "_tools", forbidden)
    with pytest.raises(critic.Error, match="not actionable"):
        adapter.execute_offline_repair(**attempt_sources(*parents(media[1])), input_path="ignored",
            output_path="ignored", root="ignored", operation=adapter.OPERATION)


def test_return_to_brain_remains_non_actionable(media):
    context = {"subjective_preference": True}
    decision, plan = parents(media[1], context=context)
    assert decision.to_dict()["choice"] == "RETURN_TO_BRAIN"
    assert reinspection.request_reinspection(plan, plan_digest=plan.digest).to_dict()["status"] == "RETURNED_TO_BRAIN_NON_ACTIONABLE"
    with pytest.raises(critic.Error, match="not actionable"):
        adapter.execute_offline_repair(**attempt_sources(decision, plan, decision_context=context),
            input_path="ignored", output_path="ignored", root="ignored", operation=adapter.OPERATION)


def test_external_media_cannot_acquire_fixture_capability(media, tmp_path):
    with pytest.raises(critic.Error, match="caller capabilities"):
        adapter.FixtureSession()
    with pytest.raises(critic.Error, match="outside source"):
        with adapter.synthetic_fixture(scratch_root=ROOT):
            pytest.fail("source accepted as scratch")
    with pytest.raises(critic.Error, match="live native"):
        adapter._session({"synthetic": True, "rights": True})


def test_missing_ffmpeg_creates_no_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter.shutil, "which", lambda name: None)
    with pytest.raises(a13.ToolFailure, match="MEDIA_TOOL_UNAVAILABLE"):
        with adapter.synthetic_fixture(scratch_root=tmp_path):
            pytest.fail("missing tool accepted")
    assert not list(tmp_path.iterdir())


def test_linked_scratch_rejected_before_creation(tmp_path, monkeypatch):
    actual = Path.lstat
    def injected(path, *args, **kwargs):
        if path == tmp_path:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=0xA000000C)
        return actual(path, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("linked scratch reached directory creation or tool lookup")
    with monkeypatch.context() as guard:
        guard.setattr(Path, "lstat", injected)
        guard.setattr(adapter, "_tools", forbidden)
        guard.setattr(adapter.tempfile, "TemporaryDirectory", forbidden)
        with pytest.raises(adapter.UnsafePathError, match="LINK_OR_REPARSE"):
            with adapter.synthetic_fixture(scratch_root=tmp_path):
                pytest.fail("linked scratch accepted")
    assert not list(tmp_path.iterdir())


def test_fixed_argv_has_no_shell_or_network(operation_bundle):
    calls = operation_bundle[2]
    edit = [call["argv"] for call in calls if any("drawbox=color=white" in arg for arg in call["argv"])]
    assert len(edit) == 1 and edit[0][-1] == "pipe:1"
    assert "-n" in edit[0] and "-protocol_whitelist" in edit[0]
    assert all(isinstance(call["argv"], list) for call in calls)
    assert all(not any(arg.startswith(("http:", "https:")) for arg in call["argv"]) for call in calls)


def test_toctou_source_and_output_locks_deny_mutation(operation_bundle):
    fixture = operation_bundle[0]
    for path in (fixture.root / "fixture.mp4", fixture.root / "repaired.mp4"):
        original = path.read_bytes()
        with adapter._paths_locked(fixture.root, path):
            with pytest.raises(OSError):
                path.write_bytes(b"racing mutation")
            with pytest.raises(OSError):
                path.rename(fixture.root / "raced.mp4")
        assert path.read_bytes() == original
    with adapter._paths_locked(fixture.root):
        with pytest.raises(OSError):
            fixture.root.rename(fixture.root.with_name(fixture.root.name + "-raced"))


def test_final_lock_check_failure_cannot_publish_success(operation_bundle, monkeypatch):
    actual = adapter._paths_locked
    @contextmanager
    def fail_final(root, *paths):
        with actual(root, *paths):
            yield
        raise adapter.UnsafePathError("FINAL_PATH_IDENTITY_UNPROVEN")
    monkeypatch.setattr(adapter, "_paths_locked", fail_final)
    result = evidence.verify_technical_evidence(**operation_bundle[1]).to_dict()
    assert result["status"] == "IMPROVEMENT_UNPROVEN" and result["measurements"] is None
    assert result["current_file_identity"] == "UNKNOWN" and not result["target_corrected"]


def test_no_retry_after_witness(operation_bundle):
    fixture, args, _ = operation_bundle
    sources = {k: args[k] for k in ("decision", "plan", "decision_digest", "plan_digest", "operation", "decision_context")}
    with pytest.raises(critic.Error, match="already witnessed"):
        adapter.execute_offline_repair(**sources, fixture=fixture, root=fixture.root,
            input_path=fixture.root / "fixture.mp4", output_path=fixture.root / "repaired.mp4")


def test_real_noop_output_and_failed_attempt_cannot_retry(tmp_path, monkeypatch):
    with adapter.synthetic_fixture(scratch_root=tmp_path) as fixture:
        before = adapter.inspect_fixture(fixture, preflight=preflight())
        original = (fixture.root / "fixture.mp4").read_bytes()
        sources = attempt_sources(*adapter.fixture_parents(fixture, before),
            operation=adapter.OPERATION, decision_context=adapter.CONTEXT)
        run = a13.BoundedToolRunner.run
        monkeypatch.setattr(a13.BoundedToolRunner, "run",
            lambda runner, argv, **kwargs: SimpleNamespace(stdout=original, stderr=b"")
            if any("drawbox" in a for a in argv) else run(runner, argv, **kwargs))
        with pytest.raises(critic.Error, match="no-op output"):
            adapter.execute_offline_repair(**sources, fixture=fixture, root=fixture.root,
                input_path=fixture.root / "fixture.mp4", output_path=fixture.root / "repaired.mp4")
        (fixture.root / "repaired.mp4").unlink()
        with pytest.raises(critic.Error, match="already witnessed or started"):
            adapter.execute_offline_repair(**sources, fixture=fixture, root=fixture.root,
                input_path=fixture.root / "fixture.mp4", output_path=fixture.root / "repaired.mp4")
        assert (fixture.root / "fixture.mp4").read_bytes() == original


def test_fake_private_q4_record_cannot_authorize_effects(tmp_path):
    with adapter.synthetic_fixture(scratch_root=tmp_path) as fixture:
        before = adapter.inspect_fixture(fixture, preflight=preflight())
        raw = deepcopy(before._sampled.to_dict())
        raw["frames"][0].update(defect=True, kind="FRAME_SIGNAL_DEFECT")
        sample = object.__new__(t2.BQ02bT2SelectiveEvidence)
        object.__setattr__(sample, "_source", before._sampled._source)
        object.__setattr__(sample, "_authenticated_json", critic._canonical(raw))
        critic._Record.__init__(sample, critic._canonical(raw))
        forged = mm.multimodal_review(before._review, sample,
            original_review_digest=before._review.digest, t2_digest=sample.digest)
        with pytest.raises(critic.Error, match="fixture-bound Q4 BEFORE"):
            adapter.fixture_parents(fixture, forged)
        assert not (fixture.root / "repaired.mp4").exists()


def test_color_changes_outside_target_are_rejected(tmp_path, monkeypatch):
    with adapter.synthetic_fixture(scratch_root=tmp_path) as fixture:
        before = adapter.inspect_fixture(fixture, preflight=preflight())
        run = a13.BoundedToolRunner.run
        def desaturate(runner, argv, **kwargs):
            argv = list(argv)
            if any("drawbox=color=white" in arg for arg in argv):
                argv[argv.index("-vf") + 1] += ",hue=s=0"
            return run(runner, argv, **kwargs)
        monkeypatch.setattr(a13.BoundedToolRunner, "run", desaturate)
        with pytest.raises(critic.Error, match="non-target preservation unproven"):
            adapter.execute_offline_repair(**attempt_sources(*adapter.fixture_parents(fixture, before),
                operation=adapter.OPERATION, decision_context=adapter.CONTEXT), fixture=fixture,
                root=fixture.root, input_path=fixture.root / "fixture.mp4", output_path=fixture.root / "repaired.mp4")


def test_witness_revoked_and_scratch_cleaned(tmp_path):
    with executed(tmp_path) as (fixture, args, _):
        sources = {k: args[k] for k in ("decision", "plan", "decision_digest", "plan_digest", "operation", "decision_context")}
        record, root = args["attempt"], fixture.root
    assert not root.exists() and not list(tmp_path.iterdir())
    with pytest.raises(critic.Error, match="live native"):
        adapter.BQ04bOfflineAttemptReceipt.from_dict(record.to_dict(), witness=record, **sources)


def test_negative_regression_forged_context_yields_no_proposed_edit(tmp_path):
    """Same genuine black frames with forged synthetic caller context yields NO proposed edit."""
    with adapter.synthetic_fixture(scratch_root=tmp_path) as fixture:
        before = adapter.inspect_fixture(fixture, preflight=preflight())
        forged_context = {"synthetic_fixture_profile": adapter.PROFILE}
        # Generic decide_repair called without live fixture capability
        decision = policy.decide_repair(before, review_digest=before.digest,
            asset_sha256=fixture.expected_sha256, context=forged_context)
        assert decision.to_dict()["choice"] == "KEEP_ORIGINAL"
        assert decision.to_dict()["status"] == "KEEP"
        assert "PROPOSE_LOCAL_DETERMINISTIC_EDIT" != decision.to_dict()["choice"]


def test_negative_regression_forged_token_or_stale_or_released_holds(tmp_path):
    """Forged fixture token/JSON, stale SHA, and released fixture must all HOLD."""
    with adapter.synthetic_fixture(scratch_root=tmp_path) as fixture:
        before = adapter.inspect_fixture(fixture, preflight=preflight())
        # 1. Forged token
        forged_token_ctx = {"fixture_token": "forged-token-xyz"}
        d1 = policy.decide_repair(before, review_digest=before.digest,
            asset_sha256=fixture.expected_sha256, context=forged_token_ctx)
        assert d1.to_dict()["choice"] == "HOLD"
        assert d1.to_dict()["status"] == "HELD"

        # 2. Forged fixture object
        d2 = policy.decide_repair(before, review_digest=before.digest,
            asset_sha256=fixture.expected_sha256, fixture=object())
        assert d2.to_dict()["choice"] == "HOLD"
        assert d2.to_dict()["status"] == "HELD"

        # 3. Tampered fixture SHA
        tampered_fixture = SimpleNamespace(expected_sha256="1" * 64, is_active=True, _released=False)
        d3 = policy.decide_repair(before, review_digest=before.digest,
            asset_sha256=fixture.expected_sha256, fixture=tampered_fixture)
        assert d3.to_dict()["choice"] == "HOLD"
        assert d3.to_dict()["status"] == "HELD"

    # 4. Released fixture (outside context manager)
    d4 = policy.decide_repair(before, review_digest=before.digest,
        asset_sha256=fixture.expected_sha256, fixture=fixture)
    assert d4.to_dict()["choice"] == "HOLD"
    assert d4.to_dict()["status"] == "HELD"


def optional_native_symlink_probe(scratch):
    """Capability evidence only. Denial is UNPROVEN, never a passing link test."""
    source, link = scratch / "probe-source.bin", scratch / "probe-symlink.bin"
    source.write_bytes(b"test-owned optional symlink probe")
    try:
        os.symlink(source, link)
        info = link.lstat()
        assert info.st_file_attributes & 0x400 and info.st_reparse_tag == 0xA000000C
        return dict(status="NATIVE_SYMLINK_CONSTRUCTED_PROBE_ONLY", tag=info.st_reparse_tag,
                    guard_acceptance="NOT_RUN_BY_CAPABILITY_PROBE")
    except OSError as error:
        return dict(status="SYMLINK_NATIVE_EFFECT_UNPROVEN", winerror=error.winerror, detail=str(error))
    finally:
        if os.path.lexists(link):
            link.unlink()
        assert source.read_bytes() == b"test-owned optional symlink probe"
        source.unlink()


def test_native_mt01_manifest_and_readonly_consumer_closure():
    work = json.loads((ROOT / WI_PATH).read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / MANIFEST_PATH).read_text(encoding="utf-8"))
    profiles = json.loads((ROOT / ".github/ef02/verify-profiles.json").read_text())
    files = minimum_test.file_hashes(ROOT)
    base = {path: digest for path, digest in files.items() if path not in work["add_only_paths"]}
    result = impact_v21.registered_action_plan(repo=ROOT, base_sha=work["source_sha"], base_files=base,
        candidate_files=files, changed_paths=work["add_only_paths"] + work["modified_paths"],
        required_acs=work["acceptance"], fallback_argv=profiles["mt01-component-onboarding"],
        groups=minimum_test.approved_groups(), registered_profiles=profiles, allowed_paths=work["allowed_paths"])
    assert result["selected_groups"] == ["a13", "bq-02a", "bq-02b", "bq-03b", "bq-04a", "bq-04b"], result
    assert not result["fallback"] and not result["impact_v21"]["missing_mapping"]
    assert manifest["sources"] == work["add_only_paths"][:2]
    assert manifest["tests"] == work["add_only_paths"][2:3] and manifest["acs"] == work["acceptance"]
