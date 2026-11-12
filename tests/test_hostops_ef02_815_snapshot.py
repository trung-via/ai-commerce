"""Bounded fixed-source snapshot recovery tests; no AGY or Git writes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from ai_commerce.hostops_v3 import ef02_815_snapshot as snap


def _setup(tmp_path, monkeypatch, *, out_of_scope=False):
    root = tmp_path / "old-worktree"
    root.mkdir()
    baseline = {}
    for path in snap.observed.SOURCE_PATHS:
        target = root.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("revision for " + path, encoding="utf-8")
        baseline[path] = b"original content"
    monkeypatch.setattr(snap.observed, "_source_worktree", lambda: root)
    changed = snap.observed.SOURCE_PATHS
    def git(worktree, command, relative=None):
        assert worktree == root
        if command == "head":
            return (snap.observed.SOURCE_SHA + "\n").encode()
        if command == "status":
            return (b"?? unapproved.txt\0" if out_of_scope else
                    "".join(" M " + p + "\0" for p in changed).encode())
        if command == "blob" and relative in baseline:
            return baseline[relative]
        return None
    monkeypatch.setattr(snap.observed, "_read_git", git)
    return root


def test_snapshot_contains_only_pinned_two_files_and_unverified_manifest(tmp_path, monkeypatch):
    root = _setup(tmp_path, monkeypatch)
    output = tmp_path / snap.ARTIFACT_NAME
    manifest = snap.snapshot(output)
    assert manifest["source_changed_count"] == 2
    assert manifest["source_sha"] == snap.observed.SOURCE_SHA
    assert manifest["replay_authorized"] is False
    assert manifest["provider_outcome_proven"] is False
    assert manifest["candidate_verified"] is False
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert names == {"manifest.json"} | {
            "source/" + p for p in snap.observed.SOURCE_PATHS}
        expected = json.loads(archive.read("manifest.json"))
        assert expected == manifest
        for p in snap.observed.SOURCE_PATHS:
            data = archive.read("source/" + p)
            assert data == root.joinpath(*p.split("/")).read_bytes()
            assert manifest["source_fingerprints"][p]["source_sha256"] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("bad_entry", [
    b"?? .env\0",
    b" M unrelated/file.py\0",
    b"R  src/ai_commerce/creative_quality/causality.py\0",
    b"?? ../escape\0",
    b"\xff\0",
])
def test_snapshot_rejects_unapproved_or_malformed_status(tmp_path, monkeypatch, bad_entry):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(snap.observed, "_read_git",
        lambda root, command, relative=None:
            (snap.observed.SOURCE_SHA + "\n").encode() if command == "head" else
            bad_entry if command == "status" else b"original content")
    with pytest.raises(snap.SnapshotDenied):
        snap.snapshot(tmp_path / snap.ARTIFACT_NAME)
    assert not (tmp_path / snap.ARTIFACT_NAME).exists()


def test_snapshot_rejects_source_baseline_mismatch(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(snap.observed, "_read_git",
        lambda root, command, relative=None: b"wrong" if command == "head" else None)
    with pytest.raises(snap.SnapshotDenied, match="SOURCE_BASELINE_UNVERIFIED"):
        snap.snapshot(tmp_path / snap.ARTIFACT_NAME)


def test_snapshot_rejects_symlinked_source(tmp_path, monkeypatch):
    root = _setup(tmp_path, monkeypatch)
    path = root.joinpath(*snap.observed.SOURCE_PATHS[0].split("/"))
    path.unlink()
    outside = tmp_path / "private.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("Windows does not permit creating symlinks in this fixture")
    with pytest.raises(snap.SnapshotDenied, match="SOURCE_UNSAFE"):
        snap.snapshot(tmp_path / snap.ARTIFACT_NAME)


def test_snapshot_rejects_arbitrary_output_path(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    with pytest.raises(SystemExit):
        snap.main(["--output", str(tmp_path / "arbitrary.zip")])
    assert not (tmp_path / "arbitrary.zip").exists()
    exact = tmp_path / snap.ARTIFACT_NAME
    assert snap.main(["--output", str(exact)]) == 0
    with pytest.raises(SystemExit):
        snap.main(["--output", str(exact)])
