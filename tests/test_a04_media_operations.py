"""A04 custody, source identity, replay, and ambiguous provider outcome coverage."""

from __future__ import annotations

import copy
import json
import os
import struct
import subprocess
import zlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ai_commerce.contracts.schema import validate_record
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.media import (
    Cost, LocalImportAdapter, MediaCapabilities, MediaInput, MediaOutput,
    MediaProviderError, MediaProviderTimeout, MediaRequest, MediaValidationError,
)
from ai_commerce.operations import (
    ArtifactCollisionError, ArtifactCorruptError, ArtifactMissingError, ArtifactRegistry,
    AssetJobManifest, JobCollisionError, LocalMediaRunner, ManifestValidationError,
    RetryUnsafeError, RunnerBusyError, UnsafePathError,
)


def png(pixel: bytes = b"\xff\x00\x00") -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + pixel))
            + chunk(b"IEND", b""))


def avi() -> bytes:
    """A complete one-frame, 1x1, uncompressed BGR AVI fixture, with index."""
    def chunk(tag: bytes, body: bytes) -> bytes:
        return tag + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) & 1 else b"")
    main = struct.pack("<14I", 1000000, 4, 0, 0x10, 1, 0, 1, 4, 1, 1, 0, 0, 0, 0)
    stream = struct.pack("<4s4sIHH8I4h", b"vids", b"DIB ", 0, 0, 0,
                         0, 1, 1, 0, 1, 4, 0xffffffff, 0, 0, 0, 1, 1)
    bitmap = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 0, 0, 0, 0)
    header = chunk(b"LIST", b"hdrl" + chunk(b"avih", main)
                   + chunk(b"LIST", b"strl" + chunk(b"strh", stream) + chunk(b"strf", bitmap)))
    frames = chunk(b"LIST", b"movi" + chunk(b"00db", b"\x00\x00\xff\x00"))
    index = chunk(b"idx1", struct.pack("<4sIII", b"00db", 0x10, 4, 4))
    return chunk(b"RIFF", b"AVI " + header + frames + index)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "original.png"
    path.write_bytes(png())
    return path


@pytest.fixture
def runner(tmp_path: Path) -> LocalMediaRunner:
    return LocalMediaRunner(ArtifactRegistry(tmp_path / "registry"))


class CountingImport(LocalImportAdapter):
    calls = 0

    def produce(self, request: MediaInput) -> MediaOutput:
        self.calls += 1
        return super().produce(request)


def request(source: Path, **kwargs) -> MediaRequest:
    return MediaRequest(source_path=source, mime_type="image/png", **kwargs)


@pytest.mark.parametrize("data,mime,kind", [(png(), "image/png", "image"), (avi(), "video/x-msvideo", "video")])
def test_actual_file_import_is_traceable_and_ready_for_a05(tmp_path, runner, data, mime, kind):
    original = tmp_path / "original.media"
    original.write_bytes(data)
    manifest = runner.run(MediaRequest(source_path=original, mime_type=mime))
    result = manifest.result
    validate_record("JobResult", result)
    artifact = validate_record("MediaArtifact", result["artifact"])
    snapshot = validate_record("SourceSnapshot", result["source_snapshot"])
    assert manifest.state == result["status"] == "SUCCEEDED"
    assert artifact["kind"] == kind and artifact["mime_type"] == mime
    assert runner.registry.verify(artifact) == data
    assert artifact["sha256"] == snapshot["sha256"] == sha256_bytes(data)
    assert artifact["source_snapshot_ids"] == [snapshot["snapshot_id"]]
    assert snapshot["source_ref"] == artifact["source_ref"] == str(original.resolve())
    assert snapshot["source_kind"] == "LOCAL_MEDIA_FILE"
    assert artifact["origin"] == "LOCAL_IMPORT"
    assert artifact["source_generation"] == "UNKNOWN"
    assert artifact["generation_performed"] is False
    assert artifact["rights_status"] == snapshot["rights_status"] == "UNKNOWN"
    assert manifest.cost == Cost("NOT_APPLICABLE") and manifest.result["cost_amount"] is None
    assert manifest.result["publication_status"] == "NOT_ATTEMPTED"
    assert manifest.trace[-1] == ("COMPLETE", "SUCCEEDED")
    assert runner.load(manifest.job_id).to_dict() == manifest.to_dict()


def test_local_adapter_preserves_captured_bytes_even_if_source_changes(source, runner):
    class MutatingSource(LocalImportAdapter):
        def produce(self, captured):
            source.write_bytes(png(b"\x00\x00\xff"))
            return super().produce(captured)
    before = source.read_bytes()
    manifest = runner.run(request(source), provider=MutatingSource())
    assert source.read_bytes() != before
    assert runner.registry.verify(manifest.result["artifact"]) == before
    assert manifest.result["source_snapshot"]["sha256"] == sha256_bytes(before)


def test_source_changes_create_new_identity_and_preserve_previous_bytes(source, runner):
    first = runner.run(request(source))
    old = runner.registry.verify(first.result["artifact"])
    source.write_bytes(png(b"\x00\x00\xff"))
    second = runner.run(request(source))
    assert first.request_digest != second.request_digest and first.job_id != second.job_id
    assert first.result["artifact"]["sha256"] != second.result["artifact"]["sha256"]
    assert runner.registry.verify(first.result["artifact"]) == old


def test_input_identity_includes_true_location_even_for_identical_bytes(source, runner, tmp_path):
    other = tmp_path / "other.png"
    other.write_bytes(source.read_bytes())
    first, second = runner.run(request(source)), runner.run(request(other))
    assert first.request_digest != second.request_digest
    assert first.result["artifact"]["path"] == second.result["artifact"]["path"]
    assert first.result["artifact"]["source_ref"] != second.result["artifact"]["source_ref"]


def test_duplicate_job_replays_without_provider_call_or_overwriting(source, runner):
    provider = CountingImport()
    first = runner.run(request(source), job_id="same-job", provider=provider)
    ref = runner.manifest_ref(first.job_id)
    original = runner.registry.read(ref)
    second = runner.run(request(source), job_id="same-job", provider=provider)
    assert first.to_dict() == second.to_dict() and provider.calls == 1
    assert runner.registry.read(ref) == original


def test_job_collision_rejects_changed_input_and_rights(source, runner):
    first = runner.run(request(source), job_id="bound-job")
    with pytest.raises(JobCollisionError):
        runner.run(request(source, rights_status="GRANTED", rights_ref="license:explicit"), job_id=first.job_id)
    source.write_bytes(png(b"\x00\xff\x00"))
    with pytest.raises(JobCollisionError):
        runner.run(request(source), job_id=first.job_id)
    assert runner.load(first.job_id).to_dict() == first.to_dict()


def test_request_digest_and_manifest_are_immutable(source, runner):
    manifest = runner.run(request(source))
    with pytest.raises(FrozenInstanceError):
        manifest.request_digest = "0" * 64
    mutable_copy = manifest.request
    mutable_copy["input"]["source_ref"] = "elsewhere"
    result_copy = manifest.result
    result_copy["artifact"]["rights_status"] = "GRANTED"
    assert manifest.request["input"]["source_ref"] == str(source.resolve())
    assert manifest.result["artifact"]["rights_status"] == "UNKNOWN"
    assert manifest.request_digest == sha256_bytes(canonical_bytes(manifest.request))


def test_rights_are_never_inferred_and_grant_requires_reference(source, runner):
    with pytest.raises(MediaValidationError, match="GRANTED requires"):
        runner.run(request(source, rights_status="GRANTED"))
    granted = runner.run(request(source, rights_status="GRANTED", rights_ref="license:human-supplied"))
    assert granted.result["artifact"]["rights_ref"] == "license:human-supplied"
    assert granted.result["artifact"]["rights_status"] == "GRANTED"


def test_mock_is_an_explicit_synthetic_image_not_a02_or_model_generation(runner):
    from ai_commerce.providers.base import MockContentProvider
    assert MockContentProvider().capabilities.output_kinds == ("text",)
    manifest = runner.run(MediaRequest(mode="mock"))
    artifact = manifest.result["artifact"]
    assert manifest.request["provider"]["name"] == "local-media-mock"
    assert artifact["origin"] == "MOCK" and artifact["mode"] == "mock"
    assert artifact["source_generation"] == "SYNTHETIC_FIXTURE"
    assert artifact["generation_performed"] is False
    assert b"MOCK fixture; no model generation" in runner.registry.verify(artifact)
    assert runner.run(MediaRequest(mode="mock")).to_dict() == manifest.to_dict()


def test_dry_run_has_no_provider_invocation_or_media_output(source, runner):
    provider = CountingImport()
    manifest = runner.run(MediaRequest(mode="dry-run", source_path=source, mime_type="image/png"), provider=provider)
    assert provider.calls == 0
    assert manifest.state == "DRY_RUN" and manifest.result["status"] == "UNKNOWN"
    assert manifest.result["artifact"] is None and manifest.result["origin"] == "DRY_RUN"
    assert manifest.result["error_code"] == "DRY_RUN_NO_EXECUTION"
    assert not (runner.registry.root / "objects").exists()
    assert runner.load(manifest.job_id).to_dict() == manifest.to_dict()


@pytest.mark.parametrize("mime,data", [
    ("text/plain", png()), ("video/mp4", png()), ("image/png", b"not a PNG"),
    ("image/png", b""), ("image/png", png()[:-1]),
    ("image/png", png()[:40] + b"\x00" + png()[41:]), ("video/x-msvideo", b"RIFFnot video"),
])
def test_invalid_or_mismatched_mime_is_rejected_before_provider(tmp_path, runner, mime, data):
    path = tmp_path / "invalid.media"
    path.write_bytes(data)
    provider = CountingImport()
    with pytest.raises(MediaValidationError):
        runner.run(MediaRequest(source_path=path, mime_type=mime), provider=provider)
    assert provider.calls == 0


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_replay_detects_missing_or_corrupt_output_without_reexecuting(source, runner, damage):
    provider = CountingImport()
    manifest = runner.run(request(source), provider=provider)
    path = runner.registry.safe_path(manifest.result["artifact"]["path"])
    if damage == "missing":
        path.unlink()
        error = ArtifactMissingError
    else:
        path.write_bytes(b"tampered output")
        error = ArtifactCorruptError
    with pytest.raises(error):
        runner.run(request(source), provider=provider)
    assert provider.calls == 1


def test_registry_refuses_overwrite_on_content_address_collision(source, runner):
    manifest = runner.run(request(source))
    ref = manifest.result["artifact"]["path"]
    runner.registry.safe_path(ref).write_bytes(b"existing corrupt bytes")
    with pytest.raises(ArtifactCollisionError):
        runner.registry.put_immutable(ref, source.read_bytes())
    assert runner.registry.read(ref) == b"existing corrupt bytes"


@pytest.mark.parametrize("ref", [
    "../escape", "objects/../../escape", "/absolute", "C:/outside", "C:drive-relative",
    r"..\escape", r"\\server\share\file", "objects//file", "objects/./file",
    "objects/file:ads", "objects/NUL", "objects/file.", "",
])
def test_traversal_windows_paths_and_ambiguous_names_are_rejected(runner, ref):
    with pytest.raises(UnsafePathError):
        runner.registry.put_immutable(ref, b"no write")


def directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("Host does not permit directory symlinks")
        completed = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
                                   capture_output=True, text=True)
        if completed.returncode:
            pytest.skip("Host permits neither directory symlinks nor junctions")


def test_symlink_or_junction_cannot_escape_storage_root(tmp_path, runner):
    outside = tmp_path / "outside"
    outside.mkdir()
    directory_link(runner.registry.root / "objects", outside)
    with pytest.raises(UnsafePathError):
        runner.registry.put_immutable("objects/escape", b"forbidden")
    assert not (outside / "escape").exists()


def test_symlink_or_junction_root_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "registry-link"
    directory_link(link, outside)
    with pytest.raises(UnsafePathError):
        ArtifactRegistry(link)


def test_replaced_root_is_detected(source, runner):
    manifest = runner.run(request(source))
    runner.registry.root.rename(runner.registry.root.with_name("old-registry"))
    runner.registry.root.mkdir()
    with pytest.raises(UnsafePathError):
        runner.load(manifest.job_id)


def test_single_worker_lock_is_shared_between_runner_instances(source, runner):
    second = LocalMediaRunner(ArtifactRegistry(runner.registry.root))
    with runner.registry.worker():
        with pytest.raises(RunnerBusyError):
            second.run(request(source))
    assert second.run(request(source)).state == "SUCCEEDED"


@pytest.mark.parametrize("failure", [
    MediaProviderTimeout("timeout after invocation"), TimeoutError("unreconciled timeout"),
    RuntimeError("unexpected provider termination"),
    MediaProviderError("SIDE_EFFECTS_UNKNOWN", "Requires reconciliation"),
    ArtifactMissingError("Provider-internal operational failure"),
])
def test_timeout_and_unknown_side_effects_are_unknown_and_never_blindly_retried(source, runner, failure):
    class Ambiguous(CountingImport):
        def produce(self, captured):
            self.calls += 1
            raise failure
    provider = Ambiguous()
    first = runner.run(request(source), provider=provider, job_id="ambiguous")
    assert first.state == first.result["status"] == "UNKNOWN"
    assert first.result["artifact"] is None
    assert first.cost == Cost() and first.result["cost_amount"] is None
    assert first.retry_safety == "RECONCILE_REQUIRED" and first.error.last_state == "RUNNING"
    assert runner.run(request(source), provider=provider, job_id="ambiguous").to_dict() == first.to_dict()
    with pytest.raises(RetryUnsafeError):
        runner.run(request(source), provider=provider, job_id="ambiguous", attempt=2)
    assert provider.calls == 1


def test_interrupted_attempt_is_reconciled_as_unknown_without_reinvocation(source, runner):
    class Interrupted(CountingImport):
        def produce(self, captured):
            self.calls += 1
            raise KeyboardInterrupt()
    provider = Interrupted()
    with pytest.raises(KeyboardInterrupt):
        runner.run(request(source), provider=provider, job_id="interrupted")
    recovered = runner.run(request(source), provider=provider, job_id="interrupted")
    assert recovered.state == "UNKNOWN" and recovered.error.code == "PROVIDER_OUTCOME_UNKNOWN"
    assert recovered.retry_safety == "RECONCILE_REQUIRED" and provider.calls == 1


def test_definitive_failure_can_only_be_retried_by_an_explicit_new_attempt(source, runner):
    class Once(CountingImport):
        def produce(self, captured):
            self.calls += 1
            if self.calls == 1:
                raise MediaProviderError("LOCAL_REFUSAL", "No side effect occurred",
                                         side_effects="NONE", cost=Cost("NOT_APPLICABLE"))
            return LocalImportAdapter.produce(self, captured)
    provider = Once()
    first = runner.run(request(source), provider=provider, job_id="manual-retry")
    assert first.state == "FAILED" and first.retry_safety == "SAFE_LOCAL" and provider.calls == 1
    assert runner.run(request(source), provider=provider, job_id="manual-retry").to_dict() == first.to_dict()
    second = runner.run(request(source), provider=provider, job_id="manual-retry", attempt=2)
    assert second.state == "SUCCEEDED" and second.attempt == 2 and provider.calls == 2
    assert runner.load(first.job_id, 1).to_dict() == first.to_dict()


def test_unknown_cost_stays_null_even_with_successful_import(source, runner):
    class UnknownCost(LocalImportAdapter):
        def produce(self, captured):
            return MediaOutput(captured.content, captured.mime_type, Cost())
    manifest = runner.run(request(source), provider=UnknownCost())
    assert manifest.state == "SUCCEEDED"
    assert manifest.cost == Cost("UNKNOWN", None) and manifest.result["cost_amount"] is None
    with pytest.raises(ValueError):
        Cost("UNKNOWN", 0)
    with pytest.raises(ValueError):
        Cost("CHARGED", float("nan"))


def test_import_provider_cannot_substitute_different_source_bytes(source, runner):
    class Substitution(LocalImportAdapter):
        def produce(self, captured):
            return MediaOutput(png(b"\x00\xff\x00"), "image/png", Cost("NOT_APPLICABLE"))
    manifest = runner.run(request(source), provider=Substitution())
    assert manifest.state == "FAILED" and manifest.result["artifact"] is None
    assert "true source" in manifest.error.detail


@pytest.mark.parametrize("network,spend,origin", [(True, False, "LOCAL_IMPORT"), (False, True, "LOCAL_IMPORT"),
                                                (False, False, "MODEL_GENERATED")])
def test_network_spending_and_real_model_providers_are_outside_a04(source, runner, network, spend, origin):
    class Forbidden(CountingImport):
        @property
        def capabilities(self):
            return MediaCapabilities("forbidden", origin, ("import",), ("image/png",), network, spend)
    provider = Forbidden()
    with pytest.raises(MediaValidationError, match="offline A04 policy"):
        runner.run(request(source), provider=provider)
    assert provider.calls == 0


@pytest.mark.parametrize("field", ["job", "attempt", "digest", "request", "state", "cost", "source",
                                   "rights", "origin", "artifact_ref", "evidence", "retry"])
def test_manifest_replay_validates_semantic_links_even_if_checksum_is_recomputed(source, runner, field):
    manifest = runner.run(request(source))
    record = copy.deepcopy(manifest.to_dict())
    if field == "job":
        record["job_id"] = "another-job"
    elif field == "attempt":
        record["attempt"] = 2
    elif field == "digest":
        record["request_digest"] = "0" * 64
    elif field == "request":
        record["request"]["input"]["source_ref"] = "false-source"
    elif field == "state":
        record["state"] = "UNKNOWN"
    elif field == "cost":
        record["cost"] = {"status": "UNKNOWN", "amount": 0}
    elif field == "source":
        record["result"]["source_snapshot"]["source_kind"] = "LOCAL_JSON_FILE"
    elif field == "rights":
        record["result"]["artifact"]["rights_status"] = "GRANTED"
    elif field == "origin":
        record["result"]["artifact"]["origin"] = "MODEL_GENERATED"
    elif field == "artifact_ref":
        record["result"]["artifact"]["path"] = "../escape"
    elif field == "evidence":
        record["result"]["evidence"][0]["source_ref"] = "false-source"
    else:
        record["retry_safety"] = "SAFE_LOCAL"
    record.pop("record_sha256")
    record["record_sha256"] = sha256_bytes(canonical_bytes(record))
    runner.registry.safe_path(runner.manifest_ref(manifest.job_id)).write_bytes(canonical_bytes(record))
    with pytest.raises(ManifestValidationError):
        runner.load(manifest.job_id)


def test_manifest_checksum_and_duplicate_json_keys_are_validated(source, runner):
    manifest = runner.run(request(source))
    record = manifest.to_dict()
    record["result"]["source_snapshot"]["captured_at"] = "2020-01-01T00:00:00Z"
    with pytest.raises(ManifestValidationError, match="checksum"):
        AssetJobManifest.from_dict(record)
    path = runner.registry.safe_path(runner.manifest_ref(manifest.job_id))
    path.write_bytes(b'{"manifest_version":1,"manifest_version":1}')
    with pytest.raises(ManifestValidationError, match="Duplicate"):
        runner.load(manifest.job_id)


@pytest.mark.parametrize("link_kind", ["symlink", "reparse"])
def test_link_escape_rejection_branches_without_host_link_privilege(tmp_path, runner, monkeypatch, link_kind):
    """Deterministic branch coverage supplements the actual-host link tests."""
    import stat
    from types import SimpleNamespace

    outside = tmp_path / "outside"
    outside.mkdir()
    link = runner.registry.root / "objects"
    actual_lstat = Path.lstat

    def linked_lstat(path, *args, **kwargs):
        if path == link:
            return SimpleNamespace(
                st_mode=(stat.S_IFLNK if link_kind == "symlink" else stat.S_IFDIR) | 0o700,
                st_file_attributes=0 if link_kind == "symlink" else 0x400,
            )
        return actual_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", linked_lstat)
    with pytest.raises(UnsafePathError, match="Linked/reparse"):
        runner.registry.put_immutable("objects/escape", b"forbidden")
    assert not (outside / "escape").exists()


def test_a05_can_load_preserved_output_after_original_source_is_removed(source, runner):
    manifest = runner.run(request(source))
    source_ref = str(source.resolve())
    data = source.read_bytes()
    source.unlink()
    loaded = runner.load(manifest.job_id)
    assert loaded.result["source_snapshot"]["source_ref"] == source_ref
    assert runner.registry.verify(loaded.result["artifact"]) == data


def test_provider_identity_changes_cannot_reuse_a_bound_job(source, runner):
    runner.run(request(source), job_id="provider-bound")

    class DifferentIdentity(CountingImport):
        @property
        def capabilities(self):
            return MediaCapabilities("another-import-adapter", "LOCAL_IMPORT", ("import",), ("image/png",))

    provider = DifferentIdentity()
    with pytest.raises(JobCollisionError):
        runner.run(request(source), provider=provider, job_id="provider-bound")
    assert provider.calls == 0


def test_a02_text_provider_is_rejected_by_media_boundary(source, runner):
    from ai_commerce.providers.base import MockContentProvider
    with pytest.raises(MediaValidationError, match="A02 text"):
        runner.run(request(source), provider=MockContentProvider())


def test_no_retry_after_success_or_without_a_consecutive_previous_attempt(source, runner):
    first = runner.run(request(source), job_id="finished")
    with pytest.raises(RetryUnsafeError):
        runner.run(request(source), job_id=first.job_id, attempt=2)
    with pytest.raises(RetryUnsafeError):
        runner.run(request(source), job_id="not-started", attempt=2)


def test_unreconciled_worker_lock_is_never_automatically_cleared(source, runner):
    provider = CountingImport()
    runner.registry.put_immutable("worker.lock", b"unreconciled-old-worker")
    with pytest.raises(RunnerBusyError):
        runner.run(request(source), provider=provider)
    assert provider.calls == 0
    assert runner.registry.read("worker.lock") == b"unreconciled-old-worker"


def test_png_with_valid_chunk_crc_but_invalid_pixel_stream_is_rejected(source, runner):
    data = source.read_bytes()
    offset = data.index(b"IDAT")
    size = struct.unpack_from(">I", data, offset - 4)[0]
    bad = b"\x00" * size
    damaged = (data[:offset + 4] + bad
               + struct.pack(">I", zlib.crc32(b"IDAT" + bad) & 0xffffffff)
               + data[offset + 8 + size:])
    source.write_bytes(damaged)
    with pytest.raises(MediaValidationError):
        runner.run(request(source))


def test_output_validation_failure_does_not_erase_unknown_cost(source, runner):
    class WrongMime(LocalImportAdapter):
        def produce(self, captured):
            return MediaOutput(captured.content, "video/x-msvideo", Cost())
    manifest = runner.run(request(source), provider=WrongMime())
    assert manifest.state == "FAILED" and manifest.stage == "REGISTER"
    assert manifest.cost == Cost("UNKNOWN", None)
    assert manifest.result["artifact"] is None


def test_indexed_png_requires_a_valid_palette(source, runner):
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff)
    source.write_bytes(b"\x89PNG\r\n\x1a\n"
                       + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
                       + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b""))
    with pytest.raises(MediaValidationError):
        runner.run(request(source))

    valid = (b"\x89PNG\r\n\x1a\n"
             + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
             + chunk(b"PLTE", b"\xff\x00\x00")
             + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b""))
    source.write_bytes(valid)
    assert runner.run(request(source)).state == "SUCCEEDED"
