"""A04 v1.1 safety and compatibility; real codec checks are dependency-gated.

IO-only tests explicitly stub decoding and make no playback/codec-validity claim.
The embedded V1 receipt/object were captured from the unmodified local baseline.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import struct
import subprocess
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes, timestamp_utc
from ai_commerce.media import (
    Cost, FakeAsyncMediaProvider, GenerationEvent, GenerationJobRequest,
    MediaDependencyError, MediaInput, MediaProvenance,
    MediaProviderError, MediaRequest, MediaValidationError, MockMediaProvider, hash_file, validate_media_file,
)
from ai_commerce.media import files
from ai_commerce.operations import (
    ArtifactCollisionError, ArtifactCorruptError, ArtifactMissingError, ArtifactRegistry,
    GenerationJobRunner, JobCollisionError, LocalMediaRunner,
    ManifestValidationError, RegistryIOError, RetryUnsafeError, UnsafePathError,
)


@pytest.fixture
def registry(tmp_path):
    return ArtifactRegistry(tmp_path / "registry")


@pytest.fixture
def image_file(tmp_path):
    content = MockMediaProvider().produce(MediaInput("mock", None, "image/png", None)).content
    path = tmp_path / "fixture.png"
    path.write_bytes(content)
    return path


@pytest.fixture
def fake(image_file):
    return FakeAsyncMediaProvider(image_file)


@pytest.fixture
def intent(fake):
    return GenerationJobRequest(fake.capabilities.name, fake.capabilities.model,
                                "prompt:fixture", sha256_bytes(b"source"),
                                prompt_sha256=sha256_bytes(b"prompt"), prompt_version="1")


def snapshot(path):
    digest = hash_file(path)[0]
    return {"snapshot_id": "snapshot:" + digest, "source_ref": str(path),
            "source_kind": "LOCAL_MEDIA_FILE", "captured_at": timestamp_utc(),
            "sha256": digest, "verification_status": "SOURCE_OBSERVED", "rights_status": "UNKNOWN"}


def store_file(registry, path, mime="image/png"):
    return registry.register_file(path, mime, provider="local-file-test", snapshot=snapshot(path),
                                  mode="import", origin="LOCAL_IMPORT")


def no_staging(registry):
    assert not list(registry.safe_path(".staging").iterdir())


def box(name, data=b""):
    return struct.pack(">I4s", len(data) + 8, name) + data


def mp4_shell(path, size=1024):
    # Deliberately not playable; only for structural/dependency and IO tests.
    with path.open("wb") as stream:
        stream.write(box(b"ftyp", b"isom\0\0\0\0isom") + box(b"moov"))
        stream.write(struct.pack(">I4s", size + 8, b"mdat"))
        remaining = size
        while remaining:
            count = min(remaining, files.CHUNK_BYTES)
            stream.write(b"\0" * count)
            remaining -= count


def test_new_formats_require_explicit_file_import_without_reading_legacy_bytes(tmp_path, registry, monkeypatch):
    source = tmp_path / "movie.mp4"
    mp4_shell(source)
    def forbidden(*args, **kwargs):
        raise AssertionError("V1 must reject an MP4 request before opening/reading it")
    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(MediaValidationError, match="import_file"):
        LocalMediaRunner(registry).run(MediaRequest(source_path=source, mime_type="video/mp4"))


def test_file_import_hash_reload_and_source_removal(registry, image_file):
    runner = LocalMediaRunner(registry)
    original = image_file.read_bytes()
    manifest = runner.import_file(MediaRequest(source_path=image_file, mime_type="image/png"), job_id="file")
    assert manifest.state == "SUCCEEDED"
    artifact = manifest.result["artifact"]
    assert artifact["sha256"] == sha256_bytes(original)
    assert artifact["size_bytes"] == len(original)
    assert artifact["generation_performed"] is manifest.result["generation_performed"] is False
    assert artifact["rights_status"] == artifact["source_generation"] == "UNKNOWN"
    assert registry.verify_file(artifact).read_bytes() == original
    saved = registry.read(runner.manifest_ref("file"))
    image_file.unlink()
    reopened = LocalMediaRunner(ArtifactRegistry(registry.root)).load("file")
    assert reopened.to_dict() == manifest.to_dict()
    assert registry.read(runner.manifest_ref("file")) == saved
    no_staging(registry)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_file_reload_refuses_changed_object(registry, image_file, damage):
    runner = LocalMediaRunner(registry)
    manifest = runner.import_file(MediaRequest(source_path=image_file, mime_type="image/png"))
    path = registry.safe_path(manifest.result["artifact"]["path"])
    if damage == "missing":
        path.unlink()
        error = ArtifactMissingError
    else:
        path.write_bytes(b"corrupt")
        error = ArtifactCorruptError
    with pytest.raises(error):
        runner.load(manifest.job_id)


def test_source_is_captured_before_it_is_removed(registry, image_file):
    expected = hash_file(image_file)
    with registry.capture_file(image_file) as captured:
        image_file.unlink()
        assert hash_file(captured.path) == expected
    no_staging(registry)


@pytest.mark.parametrize("mime,data", [
    ("image/jpeg", b"\xff\xd8" + b"junk" * 4),
    ("image/webp", b"RIFF\xff\xff\xff\xffWEBPjunk"),
    ("video/mp4", box(b"ftyp") + struct.pack(">I4s", 100, b"mdat")),
    ("video/mp4", b"not mp4"),
    ("video/mp4", box(b"ftyp") + box(b"moov")),
    ("video/mp4", b"\0\0\0\1mdat\0"),
])
def test_structurally_corrupt_formats_are_rejected_without_decoder(tmp_path, registry, mime, data):
    source = tmp_path / "invalid"
    source.write_bytes(data)
    with pytest.raises(MediaValidationError):
        LocalMediaRunner(registry).import_file(MediaRequest(source_path=source, mime_type=mime))
    assert not registry.exists("jobs")
    assert not registry.exists("objects")
    no_staging(registry)


@pytest.mark.parametrize("mime", ["image/jpeg", "image/webp"])
def test_missing_image_decoder_never_means_valid_media(tmp_path, registry, monkeypatch, mime):
    source = tmp_path / "claimed-image"
    source.write_bytes((b"\xff\xd8" + b"?" * 20 + b"\xff\xd9") if mime == "image/jpeg"
                       else b"RIFF" + struct.pack("<I", 12) + b"WEBP" + b"?" * 8)
    def unavailable():
        raise MediaDependencyError("Pillow decoder unavailable (test)")
    monkeypatch.setattr(files, "_pillow", unavailable)
    with pytest.raises(MediaDependencyError, match="Pillow") as exc:
        LocalMediaRunner(registry).import_file(MediaRequest(source_path=source, mime_type=mime))
    assert exc.value.code == "MEDIA_DEPENDENCY_UNAVAILABLE"
    assert not registry.exists("jobs") and not registry.exists("objects")
    no_staging(registry)


def test_missing_ffprobe_or_ffmpeg_is_a_dependency_error(tmp_path, registry, monkeypatch):
    source = tmp_path / "claimed.mp4"
    mp4_shell(source)
    monkeypatch.setattr(files.shutil, "which", lambda _: None)
    with pytest.raises(MediaDependencyError, match="ffprobe and FFmpeg"):
        LocalMediaRunner(registry).import_file(MediaRequest(source_path=source, mime_type="video/mp4"))
    assert not registry.exists("jobs") and not registry.exists("objects")
    no_staging(registry)


@pytest.fixture(params=["JPEG", "WEBP"])
def real_image(request, tmp_path):
    try:
        Image, _, features = files._pillow()
        if request.param == "WEBP" and not features.check("webp"):
            pytest.skip("Pillow lacks libwebp")
    except MediaDependencyError as exc:
        pytest.skip(str(exc))
    path = tmp_path / ("actual." + request.param.lower())
    Image.new("RGB", (16, 16), (43, 90, 170)).save(path, format=request.param)
    return path, {"JPEG": "image/jpeg", "WEBP": "image/webp"}[request.param]


def test_real_images_decode_hash_and_reopen(registry, real_image):
    source, mime = real_image
    expected = hash_file(source)
    manifest = LocalMediaRunner(registry).import_file(MediaRequest(source_path=source, mime_type=mime))
    assert manifest.state == "SUCCEEDED"
    artifact = manifest.result["artifact"]
    assert (artifact["sha256"], artifact["size_bytes"]) == expected
    assert validate_media_file(registry.verify_file(artifact), mime).method == "PILLOW_DECODE"
    source.unlink()
    assert LocalMediaRunner(ArtifactRegistry(registry.root)).load(manifest.job_id).to_dict() == manifest.to_dict()


@pytest.mark.parametrize("damage", ["truncated", "corrupt", "mismatched"])
def test_real_images_invalid_payloads_fail_before_custody(registry, real_image, damage):
    source, mime = real_image
    data = source.read_bytes()
    if damage == "truncated":
        source.write_bytes(data[:-1])
    elif damage == "mismatched":
        mime = "image/webp" if mime == "image/jpeg" else "image/jpeg"
    else:
        broken = data[:len(data) // 2] + data[-2:]
        if mime == "image/webp":
            broken = broken[:4] + struct.pack("<I", len(broken) - 8) + broken[8:]
        source.write_bytes(broken)
    with pytest.raises(MediaValidationError):
        LocalMediaRunner(registry).import_file(MediaRequest(source_path=source, mime_type=mime))
    assert not registry.exists("objects")
    no_staging(registry)


@pytest.fixture
def real_mp4(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("Real MP4 checks require FFmpeg and ffprobe on PATH")
    path = tmp_path / "actual.mp4"
    done = subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=16x16:r=2",
                           "-t", "1", "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(path)],
                          capture_output=True, timeout=30)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return path


def test_real_mp4_decoder_hash_and_reload(registry, real_mp4):
    expected = hash_file(real_mp4)
    manifest = LocalMediaRunner(registry).import_file(MediaRequest(source_path=real_mp4, mime_type="video/mp4"))
    assert manifest.state == "SUCCEEDED"
    artifact = manifest.result["artifact"]
    assert (artifact["sha256"], artifact["size_bytes"]) == expected
    assert validate_media_file(registry.verify_file(artifact), "video/mp4").method == "FFPROBE_AND_FFMPEG_DECODE"
    real_mp4.unlink()
    assert LocalMediaRunner(ArtifactRegistry(registry.root)).load(manifest.job_id).to_dict() == manifest.to_dict()


@pytest.mark.parametrize("damage", ["truncated", "corrupt", "mismatched"])
def test_real_mp4_rejects_invalid_frames_or_wrong_mime(registry, real_mp4, damage):
    data = bytearray(real_mp4.read_bytes())
    if damage == "truncated":
        real_mp4.write_bytes(data[:-32])
    elif damage == "corrupt":
        mdat = data.index(b"mdat") + 4
        data[mdat:mdat + 64] = b"\xff" * 64
        real_mp4.write_bytes(data)
    else:
        real_mp4.write_bytes(MockMediaProvider().produce(MediaInput("mock", None, None, None)).content)
    with pytest.raises(MediaValidationError):
        LocalMediaRunner(registry).import_file(MediaRequest(source_path=real_mp4, mime_type="video/mp4"))
    assert not registry.exists("objects")


def test_large_mp4_custody_has_bounded_io_with_decoder_stub_only(registry, tmp_path, monkeypatch):
    source = tmp_path / "large.mp4"
    mp4_shell(source, 20 * files.CHUNK_BYTES + 31)
    expected = hash_file(source)
    checks = []
    monkeypatch.setattr(files, "_decode_mp4", lambda path: checks.append(path))
    def forbidden_read_bytes(*args, **kwargs):
        raise AssertionError("MP4 may not be materialized by read_bytes")
    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    actual_copy = files.copy_and_hash
    class Bounded:
        def __init__(self, stream):
            self.stream = stream
        def read(self, amount=-1):
            assert 0 < amount <= files.CHUNK_BYTES
            return self.stream.read(amount)
    def bounded_copy(source_stream, target=None):
        return actual_copy(Bounded(source_stream), target)
    import ai_commerce.operations.registry as registry_module
    monkeypatch.setattr(registry_module, "copy_and_hash", bounded_copy)
    monkeypatch.setattr(files, "copy_and_hash", bounded_copy)
    runner = LocalMediaRunner(registry)
    manifest = runner.import_file(MediaRequest(source_path=source, mime_type="video/mp4"))
    assert manifest.state == "SUCCEEDED"
    artifact = manifest.result["artifact"]
    assert (artifact["sha256"], artifact["size_bytes"]) == expected
    assert hash_file(registry.verify_file(artifact)) == expected
    source.unlink()
    assert runner.load(manifest.job_id).to_dict() == manifest.to_dict()
    with pytest.raises(MediaValidationError, match="file-only"):
        registry.verify(artifact)
    assert len(checks) >= 4  # IO evidence only; not a codec proof.
    no_staging(registry)


def test_copy_short_write_is_not_committed():
    class Short(io.BytesIO):
        def write(self, content):
            return len(content) - 1
    with pytest.raises(OSError, match="Short write"):
        files.copy_and_hash(io.BytesIO(b"input"), Short())


def test_interrupted_immutable_write_never_exposes_partial_target(registry, monkeypatch):
    import ai_commerce.operations.registry as module
    def interrupted(source, target):
        target.write(b"partial")
        raise OSError("disk interrupted")
    monkeypatch.setattr(module, "copy_and_hash", interrupted)
    with pytest.raises(RegistryIOError, match="disk interrupted"):
        registry.put_immutable("objects/new", b"complete")
    assert not registry.exists("objects/new")
    no_staging(registry)


def test_failed_atomic_commit_leaves_existing_artifact_and_cleans_temp(registry, image_file, monkeypatch):
    artifact = store_file(registry, image_file)
    original = registry.read(artifact["path"])
    def failed(*args, **kwargs):
        raise OSError("commit refused")
    monkeypatch.setattr(registry, "_commit", failed)
    with pytest.raises(RegistryIOError, match="commit refused"):
        store_file(registry, image_file)
    assert registry.read(artifact["path"]) == original
    no_staging(registry)


def test_file_registration_deduplicates_without_overwriting_corrupt_existing_output(registry, image_file):
    artifact = store_file(registry, image_file)
    assert store_file(registry, image_file) == artifact
    registry.safe_path(artifact["path"]).write_bytes(b"existing corruption")
    with pytest.raises(ArtifactCollisionError):
        store_file(registry, image_file)
    assert registry.read(artifact["path"]) == b"existing corruption"
    no_staging(registry)


@pytest.mark.parametrize("relative", ["../escape", "objects/../escape", "/absolute", "objects/file:ads",
                                    "objects/NUL", "objects//file", "objects/a."])
def test_streaming_operations_keep_root_traversal_guards(registry, relative):
    with pytest.raises(UnsafePathError):
        registry.put_immutable(relative, b"data")


def test_source_hardlink_is_rejected(registry, image_file, tmp_path):
    link = tmp_path / "hardlink.png"
    try:
        os.link(image_file, link)
    except OSError as exc:
        pytest.skip(f"Host cannot create hardlinks: {exc}")
    with pytest.raises(MediaValidationError, match="unlinked regular"):
        with registry.capture_file(link):
            pytest.fail("Linked source must not be captured")
    no_staging(registry)


def test_replaced_registry_root_cannot_publish_file(registry, image_file):
    registry.root.rename(registry.root.with_name("moved-registry"))
    registry.root.mkdir()
    with pytest.raises(UnsafePathError):
        store_file(registry, image_file)


def test_staging_junction_rejected_without_host_link_privilege(registry, image_file, monkeypatch):
    from types import SimpleNamespace
    import stat
    actual = Path.lstat
    target = registry.root / ".staging"
    def lstat(path, *args, **kwargs):
        if path == target:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_file_attributes=0x400)
        return actual(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(UnsafePathError, match="Linked/reparse"):
        store_file(registry, image_file)


def test_v2_request_roundtrip_and_immutable_identity(intent):
    assert GenerationJobRequest.from_dict(intent.to_dict()) == intent
    assert replace(intent, model="different").digest != intent.digest
    with pytest.raises(FrozenInstanceError):
        intent.model = "mutated"
    with pytest.raises(ValueError):
        GenerationJobRequest.from_dict({**intent.to_dict(), "job_version": 1})
    with pytest.raises(ValueError):
        replace(intent, rights_status="GRANTED")
    with pytest.raises(ValueError):
        replace(intent, prompt_sha256=None)


def test_async_lifecycle_custody_and_unknown_rights_cost(registry, fake, intent):
    runner = GenerationJobRunner(registry, fake)
    granted_intent = replace(intent, rights_status="GRANTED", rights_ref="human-supplied:claim")
    first = runner.submit(granted_intent, job_id="async")
    assert first.state == "SUBMITTED" and first.external_request_id.startswith("fake:")
    assert first.side_effects == "CONFIRMED" and first.cost == Cost()
    assert runner.submit(granted_intent, job_id="async") == first and fake.submit_calls == 1
    with pytest.raises(RetryUnsafeError):
        runner.fetch("async")
    fake.emit(first.external_request_id, "RUNNING")
    assert runner.observe("async").state == "RUNNING"
    fake.emit(first.external_request_id, "SUCCEEDED")
    succeeded = runner.observe("async")
    assert succeeded.terminal and succeeded.retry_safety == "NO_RETRY"
    assert succeeded.cost.status == "UNKNOWN" and succeeded.cost.amount is None
    result = runner.fetch("async")
    assert result["receipt_version"] == 2
    assert result["artifact"]["rights_status"] == "UNKNOWN"
    assert result["artifact"]["generation_performed"] is False
    assert result["provenance"]["classification"] == "SYNTHETIC_FIXTURE"
    assert result["provenance"]["generation_performed"] is False
    saved = registry.read("generation-jobs/async/output.receipt.json")
    recreated = GenerationJobRunner(ArtifactRegistry(registry.root), fake)
    assert recreated.load("async") == succeeded
    assert recreated.fetch("async") == result and fake.fetch_calls == 1
    assert registry.read("generation-jobs/async/output.receipt.json") == saved


def test_duplicate_and_out_of_order_events_are_idempotent(registry, fake, intent):
    runner = GenerationJobRunner(registry, fake)
    first = runner.submit(intent, job_id="ordering")
    running = fake.emit(first.external_request_id, "RUNNING", sequence=1)
    success = fake.emit(first.external_request_id, "SUCCEEDED", sequence=2, cost=Cost("CHARGED", 0.25))
    fake.deliver(success, running, success)
    observed = runner.observe("ordering")
    assert observed.state == "SUCCEEDED" and observed.cost == Cost("CHARGED", 0.25)
    assert len(observed.event_ids) == 3
    fake.deliver(running, success)
    assert runner.observe("ordering") == observed
    fake.emit(first.external_request_id, "RUNNING", sequence=3)  # Invalid late regression ignored.
    assert runner.observe("ordering").state == "SUCCEEDED"
    assert fake.submit_calls == 1


def test_conflicting_terminal_events_need_reconciliation(registry, fake, intent):
    runner = GenerationJobRunner(registry, fake)
    submitted = runner.submit(intent, job_id="conflict")
    fake.emit(submitted.external_request_id, "SUCCEEDED")
    assert runner.observe("conflict").state == "SUCCEEDED"
    fake.emit(submitted.external_request_id, "FAILED")
    uncertain = runner.observe("conflict")
    assert uncertain.state == "UNKNOWN" and uncertain.retry_safety == "RECONCILE_REQUIRED"
    with pytest.raises(RetryUnsafeError):
        runner.fetch("conflict")
    assert runner.reconcile("conflict").state == "FAILED"
    assert fake.submit_calls == 1


def test_duplicate_event_id_with_different_content_is_rejected(registry, fake, intent):
    runner = GenerationJobRunner(registry, fake)
    submitted = runner.submit(intent, job_id="event-collision")
    event = fake.emit(submitted.external_request_id, "RUNNING")
    runner.observe("event-collision")
    fake.deliver(replace(event, cost=Cost("ESTIMATED", 1)))
    with pytest.raises(ManifestValidationError, match="Duplicate event ID"):
        runner.observe("event-collision")
    assert runner.load("event-collision").state == "RUNNING" and fake.submit_calls == 1


@pytest.mark.parametrize("wrong", ["request", "external"])
def test_observation_cannot_change_request_binding(registry, fake, intent, wrong):
    runner = GenerationJobRunner(registry, fake)
    first = runner.submit(intent, job_id="binding")
    event = fake.emit(first.external_request_id, "RUNNING")
    runner.observe("binding")
    bad = replace(event, event_id="bad-id", request_digest="0" * 64) if wrong == "request" else replace(
        event, event_id="bad-id", external_request_id="different-external")
    fake.observe = lambda _: (bad,)
    with pytest.raises(ManifestValidationError):
        runner.observe("binding")
    with pytest.raises(JobCollisionError):
        runner.submit(replace(intent, source_ref="different"), job_id="binding")
    assert fake.submit_calls == 1


def test_interrupted_submission_is_reconciled_without_double_submit(registry, image_file):
    class Interrupted(FakeAsyncMediaProvider):
        def submit(self, request, *, client_token):
            super().submit(request, client_token=client_token)
            raise KeyboardInterrupt("after external acceptance")
    fake = Interrupted(image_file)
    intent = GenerationJobRequest(fake.capabilities.name, fake.capabilities.model, "source:local", "1" * 64)
    runner = GenerationJobRunner(registry, fake)
    with pytest.raises(KeyboardInterrupt):
        runner.submit(intent, job_id="interrupted")
    reopened = GenerationJobRunner(ArtifactRegistry(registry.root), fake)
    assert reopened.submit(intent, job_id="interrupted").state == "UNKNOWN"
    assert fake.submit_calls == 1
    with pytest.raises(RetryUnsafeError):
        reopened.observe("interrupted")
    recovered = reopened.reconcile("interrupted")
    assert recovered.state == "SUBMITTED" and recovered.external_request_id is not None
    assert fake.submit_calls == 1 and fake.reconcile_calls == 1


def test_timeout_after_acceptance_preserves_unknown_cost_and_does_not_retry(registry, image_file):
    class Timeout(FakeAsyncMediaProvider):
        def submit(self, request, *, client_token):
            super().submit(request, client_token=client_token)
            raise TimeoutError("response lost after acceptance")
    fake = Timeout(image_file)
    intent = GenerationJobRequest(fake.capabilities.name, fake.capabilities.model, "source:local", "2" * 64)
    runner = GenerationJobRunner(registry, fake)
    unknown = runner.submit(intent, job_id="timeout")
    assert unknown.state == "UNKNOWN" and unknown.cost == Cost()
    assert unknown.side_effects == "UNKNOWN" and unknown.external_request_id is None
    assert runner.submit(intent, job_id="timeout") == unknown and fake.submit_calls == 1
    assert runner.reconcile("timeout").state == "SUBMITTED"
    assert fake.submit_calls == 1


def test_observe_timeout_requires_explicit_reconciliation(registry, fake, intent):
    runner = GenerationJobRunner(registry, fake)
    runner.submit(intent, job_id="observe-timeout")
    original = fake.observe
    fake.observe = lambda _: (_ for _ in ()).throw(TimeoutError("unobserved state"))
    unknown = runner.observe("observe-timeout")
    assert unknown.state == "UNKNOWN" and unknown.side_effects == "CONFIRMED"
    fake.observe = original
    assert runner.observe("observe-timeout").state == "UNKNOWN"
    assert runner.reconcile("observe-timeout").state == "SUBMITTED"
    assert fake.submit_calls == 1


def test_unknown_observed_cost_does_not_erase_known_cost(registry, fake, intent):
    runner = GenerationJobRunner(registry, fake)
    first = runner.submit(intent, job_id="cost")
    fake.emit(first.external_request_id, "RUNNING", cost=Cost("ESTIMATED", 0.5))
    assert runner.observe("cost").cost == Cost("ESTIMATED", 0.5)
    fake.emit(first.external_request_id, "SUCCEEDED", cost=Cost())
    status = runner.observe("cost")
    assert status.cost == Cost("ESTIMATED", 0.5) and status.state == "SUCCEEDED"
    assert runner.reconcile("cost").cost == Cost("ESTIMATED", 0.5)
    with pytest.raises(ValueError):
        Cost("UNKNOWN", 0)


@pytest.mark.parametrize("kind", ["network", "spend", "not-fixture"])
def test_paid_or_live_backends_are_disabled(registry, fake, kind):
    caps = fake.capabilities
    changes = {"network_required": True} if kind == "network" else (
        {"external_spend_possible": True} if kind == "spend" else {"synthetic_fixture": False})
    class Forbidden:
        capabilities = replace(caps, **changes)
    with pytest.raises(MediaValidationError, match="offline synthetic"):
        GenerationJobRunner(registry, Forbidden())
    assert fake.submit_calls == 0


def test_fetch_replay_detects_corruption_without_provider_call(registry, fake, intent):
    runner = GenerationJobRunner(registry, fake)
    first = runner.submit(intent, job_id="output")
    fake.emit(first.external_request_id, "SUCCEEDED")
    runner.observe("output")
    output = runner.fetch("output")
    registry.safe_path(output["artifact"]["path"]).write_bytes(b"corrupt")
    with pytest.raises(ArtifactCorruptError):
        runner.fetch("output")
    assert fake.submit_calls == fake.fetch_calls == 1


def test_fake_cannot_fetch_substituted_bytes(registry, fake, intent):
    runner = GenerationJobRunner(registry, fake)
    first = runner.submit(intent, job_id="substitution")
    fake.emit(first.external_request_id, "SUCCEEDED")
    runner.observe("substitution")
    fake.fixture_path.write_bytes(b"substituted")
    with pytest.raises(MediaValidationError, match="observed digest"):
        runner.fetch("substitution")
    assert not registry.exists("generation-jobs/substitution/output.receipt.json")


def test_generation_receipt_tampering_fails_closed(registry, fake, intent):
    runner = GenerationJobRunner(registry, fake)
    runner.submit(intent, job_id="tamper")
    ref = "generation-jobs/tamper/observations/00000000.json"
    record = json.loads(registry.read(ref))
    record["events"][0]["state"] = "SUCCEEDED"
    registry.safe_path(ref).write_bytes(canonical_bytes(record))
    with pytest.raises(ManifestValidationError, match="checksum"):
        runner.load("tamper")


def test_provenance_requires_bound_completion_not_labels(intent):
    digest = "3" * 64
    event = GenerationEvent("e", "request-id", intent.digest, 1, "SUCCEEDED", "CONFIRMED",
                            output_sha256=digest, output_kind="MODEL_GENERATED")
    assert MediaProvenance.generation(intent, event, observed_sha256=digest,
                                     synthetic_fixture=False).classification == "MODEL_GENERATED"
    assert MediaProvenance.generation(intent, event, observed_sha256=digest,
                                     synthetic_fixture=True).classification == "UNKNOWN"
    with pytest.raises(ValueError, match="matching observed"):
        MediaProvenance.generation(intent, event, observed_sha256="4" * 64, synthetic_fixture=False)
    with pytest.raises(ValueError):
        replace(event, state="RUNNING")
    assert MediaProvenance().classification == "UNKNOWN"
    render = MediaProvenance.derived_render(observed_sha256=digest, receipt_output_sha256=digest,
                input_sha256s=("4" * 64,), renderer_ref="local:renderer-v1", receipt_ref="observed:render")
    assert render.classification == "DERIVED_RENDER" and render.original_generation == "UNKNOWN"
    with pytest.raises(ValueError):
        MediaProvenance.derived_render(observed_sha256=digest, receipt_output_sha256="0" * 64,
                input_sha256s=("4" * 64,), renderer_ref="local:renderer", receipt_ref="observed:render")


# Exact V1 manifest, binding, and object from the pre-edit baseline run.
LEGACY = json.loads(r'''{"manifest":{"attempt":1,"cost":{"amount":null,"status":"NOT_APPLICABLE"},"error":null,"job_id":"media-ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b","manifest_version":1,"record_sha256":"f18989829a690a45067d57b142b395ed6056edee230d5bccc6b3b4874bfb43d5","refs":["snapshot:ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b","objects/884216073bb51af03174db57102bb3e74b484d3c4c4e661dac522e48470097b2"],"request":{"input":null,"mime_type":"image/png","mode":"mock","provider":{"external_spend_possible":false,"mime_types":["image/png"],"modes":["mock"],"name":"local-media-mock","network_required":false,"origin":"MOCK"},"rights_ref":null,"rights_status":"UNKNOWN"},"request_digest":"ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b","result":{"artifact":{"artifact_id":"artifact:884216073bb51af03174db57102bb3e74b484d3c4c4e661dac522e48470097b2","generation_performed":false,"kind":"image","mime_type":"image/png","mode":"mock","origin":"MOCK","path":"objects/884216073bb51af03174db57102bb3e74b484d3c4c4e661dac522e48470097b2","provider":"local-media-mock","rights_ref":null,"rights_status":"UNKNOWN","sha256":"884216073bb51af03174db57102bb3e74b484d3c4c4e661dac522e48470097b2","size_bytes":135,"source_generation":"SYNTHETIC_FIXTURE","source_ref":"request:ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b","source_snapshot_ids":["snapshot:ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b"]},"attempt":1,"cost_amount":null,"cost_status":"NOT_APPLICABLE","error_code":null,"error_detail":null,"evidence":[{"evidence_id":"source:ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b","observed_at":"2026-10-28T20:16:15+00:00","rights_status":"UNKNOWN","sha256":"ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b","source_kind":"MEDIA_REQUEST","source_ref":"request:ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b","verification_status":"SOURCE_OBSERVED"}],"evidence_refs":["source:ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b"],"generation_performed":false,"job_id":"media-ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b","mode":"mock","origin":"MOCK","provider":"local-media-mock","publication_status":"NOT_ATTEMPTED","source_snapshot":{"captured_at":"2026-10-28T20:16:15+00:00","rights_status":"UNKNOWN","sha256":"ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b","snapshot_id":"snapshot:ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b","source_kind":"MEDIA_REQUEST","source_ref":"request:ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b","verification_status":"SOURCE_OBSERVED"},"status":"SUCCEEDED"},"retry_safety":"NO_RETRY","stage":"COMPLETE","state":"SUCCEEDED","trace":[{"stage":"PREPARE","state":"READY"},{"stage":"PROVIDER","state":"RUNNING"},{"stage":"REGISTER","state":"RUNNING"},{"stage":"COMPLETE","state":"SUCCEEDED"}]},"binding":{"job_id":"media-ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b","request":{"input":null,"mime_type":"image/png","mode":"mock","provider":{"external_spend_possible":false,"mime_types":["image/png"],"modes":["mock"],"name":"local-media-mock","network_required":false,"origin":"MOCK"},"rights_ref":null,"rights_status":"UNKNOWN"},"request_digest":"ca5bca88154dad8e88865e4f22a37dfa69bab0b45556686e4f2f2b309ed80c2b"},"object":"89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000003574455874436f6d6d656e7400414920436f6d6d65726365204d4f434b20666978747572653b206e6f206d6f64656c2067656e65726174696f6e133fa64f0000000d49444154789c63f8cff0ff3f0006fe02fe0c7589de0000000049454e44ae426082"}''')


def test_exact_stored_v1_mock_receipt_replays_without_migration(registry):
    old = LEGACY["manifest"]
    job = old["job_id"]
    manifest_ref = LocalMediaRunner.manifest_ref(job)
    manifest_bytes = canonical_bytes(old) + b"\n"
    registry.put_immutable(manifest_ref, manifest_bytes)
    registry.put_immutable(f"jobs/{job}/request.json", canonical_bytes(LEGACY["binding"]))
    artifact = old["result"]["artifact"]
    object_bytes = bytes.fromhex(LEGACY["object"])
    registry.put_immutable(artifact["path"], object_bytes)
    runner = LocalMediaRunner(registry)
    assert runner.load(job).to_dict() == old
    assert runner.run(MediaRequest(mode="mock")).to_dict() == old
    assert registry.read(manifest_ref) == manifest_bytes
    assert registry.verify(artifact) == object_bytes
    assert old["result"]["generation_performed"] is False


@pytest.mark.parametrize("brands", [b"qt  ", b"avif", b"heic"])
def test_mov_and_still_image_brands_do_not_masquerade_as_mp4(tmp_path, brands):
    path = tmp_path / "mislabeled.mp4"
    path.write_bytes(box(b"ftyp", brands + b"\0\0\0\0" + brands) + box(b"moov") + box(b"mdat", b"data"))
    with pytest.raises(MediaValidationError, match="brands"):
        validate_media_file(path, "video/mp4")


@pytest.mark.parametrize("outcome", ["denied", "not-found", "dll-failure", "timeout"])
def test_tool_execution_errors_are_honest_dependencies_or_validation(tmp_path, monkeypatch, outcome):
    def run(*args, **kwargs):
        if outcome == "denied":
            raise PermissionError("not executable")
        if outcome == "not-found":
            raise FileNotFoundError("missing")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("verifier", 60)
        return subprocess.CompletedProcess(args[0], 0xC0000142)
    monkeypatch.setattr(files.subprocess, "run", run)
    error = MediaValidationError if outcome == "timeout" else MediaDependencyError
    with pytest.raises(error):
        files._tool(["local-verifier", "input"], tmp_path)


def test_pillow_import_error_is_explicit_dependency_error(monkeypatch):
    import builtins
    actual = builtins.__import__
    def importing(name, *args, **kwargs):
        if name == "PIL":
            raise ImportError("native decoder missing")
        return actual(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", importing)
    with pytest.raises(MediaDependencyError, match="working Pillow"):
        files._pillow()


def test_ffprobe_metadata_without_frames_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "container.mp4"
    mp4_shell(path)
    monkeypatch.setattr(files.shutil, "which", lambda tool: tool)
    monkeypatch.setattr(files, "_tool", lambda *args: canonical_bytes(
        {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"}, "streams": [
            {"codec_type": "video", "width": 16, "height": 16, "nb_read_frames": "0"}]}))
    with pytest.raises(MediaValidationError, match="ffprobe video evidence"):
        validate_media_file(path, "video/mp4")


@pytest.mark.parametrize("certainty,cost,state", [
    ("NONE", Cost("NOT_APPLICABLE"), "FAILED"),
    ("UNKNOWN", Cost("CHARGED", 1.25), "UNKNOWN"),
])
def test_typed_error_preserves_cost_and_effect_certainty(registry, image_file, certainty, cost, state):
    class Refusal(FakeAsyncMediaProvider):
        def submit(self, request, *, client_token):
            self.submit_calls += 1
            raise MediaProviderError("REFUSAL", "observed refusal", side_effects=certainty, cost=cost)
    fake = Refusal(image_file)
    request = GenerationJobRequest(fake.capabilities.name, fake.capabilities.model, "source:local", "5" * 64)
    runner = GenerationJobRunner(registry, fake)
    status = runner.submit(request, job_id="refusal")
    assert status.state == state and status.side_effects == certainty and status.cost == cost
    assert runner.submit(request, job_id="refusal") == status
    assert fake.submit_calls == 1


def test_reverse_order_conflicting_terminals_are_unknown_until_reconciled(registry, fake, intent):
    runner = GenerationJobRunner(registry, fake)
    first = runner.submit(intent, job_id="reverse")
    success = fake.emit(first.external_request_id, "SUCCEEDED", sequence=2)
    fake._pending[first.external_request_id].clear()
    fake.deliver(success)
    assert runner.observe("reverse").state == "SUCCEEDED"
    stale_failure = replace(success, event_id="stale-failure", sequence=1, state="FAILED",
                            output_sha256=None, output_kind="UNKNOWN")
    fake.deliver(stale_failure)
    assert runner.observe("reverse").state == "UNKNOWN"
    assert runner.reconcile("reverse").state == "SUCCEEDED"
    fake.deliver(stale_failure, success)  # Already reconciled duplicate evidence cannot regress.
    assert runner.observe("reverse").state == "SUCCEEDED"
    assert fake.submit_calls == 1


def test_reconciliation_cannot_regress_terminal_to_running(registry, fake, intent):
    runner = GenerationJobRunner(registry, fake)
    first = runner.submit(intent, job_id="no-regression")
    fake.emit(first.external_request_id, "SUCCEEDED")
    assert runner.observe("no-regression").state == "SUCCEEDED"
    fake.emit(first.external_request_id, "RUNNING", sequence=10)
    runner.observe("no-regression")
    assert runner.reconcile("no-regression").state == "UNKNOWN"
    assert runner.load("no-regression").retry_safety == "RECONCILE_REQUIRED"


def test_malformed_submit_receipt_remains_unknown_without_replay(registry, fake, intent):
    fake.submit = lambda *args, **kwargs: None
    runner = GenerationJobRunner(registry, fake)
    status = runner.submit(intent, job_id="malformed")
    assert status.state == "UNKNOWN" and status.cost == Cost()
    assert runner.submit(intent, job_id="malformed") == status
    assert runner.reconcile("malformed").state == "UNKNOWN"


def test_generation_journal_gap_is_not_silently_ignored(registry, fake, intent):
    runner = GenerationJobRunner(registry, fake)
    first = runner.submit(intent, job_id="gapped")
    fake.emit(first.external_request_id, "RUNNING")
    runner.observe("gapped")
    registry.safe_path("generation-jobs/gapped/observations/00000000.json").unlink()
    with pytest.raises(ManifestValidationError, match="gapped"):
        runner.load("gapped")


def test_file_registration_does_not_promote_rights_without_reference(registry, image_file):
    source = {**snapshot(image_file), "rights_status": "GRANTED"}
    with pytest.raises(MediaValidationError, match="rights evidence"):
        registry.register_file(image_file, "image/png", provider="local-file", snapshot=source,
                               mode="import", origin="LOCAL_IMPORT")
    artifact = store_file(registry, image_file)
    provenance = MediaProvenance.custody(artifact, observed_sha256=artifact["sha256"])
    assert provenance.classification == "LOCAL_IMPORT" and provenance.original_generation == "UNKNOWN"
    assert artifact["rights_status"] == "UNKNOWN"


def test_provenance_label_alone_cannot_assert_model_generation():
    with pytest.raises(ValueError):
        MediaProvenance("MODEL_GENERATED")
    with pytest.raises(ValueError, match="observed evidence"):
        MediaProvenance("MODEL_GENERATED", "MODEL_GENERATED", True)


def test_source_hardlink_guard_without_native_link_privilege(registry, image_file, monkeypatch):
    from types import SimpleNamespace
    actual = Path.lstat
    def lstat(path, *args, **kwargs):
        if path == image_file:
            return SimpleNamespace(st_mode=actual(path).st_mode, st_nlink=2, st_file_attributes=0)
        return actual(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(MediaValidationError, match="unlinked regular"):
        with registry.capture_file(image_file):
            pytest.fail("Hardlinked input was accepted")
    no_staging(registry)
