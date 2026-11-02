"""Safety regressions for bounded, model-free source recovery of EF02 #202."""
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch
import zipfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hostops_ef02_202_recovery.py"
spec = importlib.util.spec_from_file_location("hostops_ef02_202_recovery_test", SCRIPT)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def _fixture(tmp_path: Path):
    worktree = tmp_path / recovery.WORKTREE_BASENAME
    worktree.mkdir()
    data = {"src/ai_commerce/affiliate_attribution/evidence.py": b"offline only\n",
            "tests/test_a14_attribution.py": b"assert True\n"}
    for name, raw in data.items():
        path = worktree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    expected = {name: hashlib.sha256(raw).hexdigest() for name, raw in data.items()}
    return worktree, data, expected


def _run(tmp_path, expected):
    complete = subprocess.CompletedProcess([], 0, recovery.SOURCE_SHA.encode() + b"\n", b"")
    with patch.object(recovery.tempfile, "gettempdir", return_value=str(tmp_path)), \
         patch.object(recovery, "EXPECTED", expected), \
         patch.object(recovery.subprocess, "run", return_value=complete):
        return recovery.snapshot(tmp_path / "job")


def test_missing_worktree_is_observed_not_a_provider_retry(tmp_path):
    with patch.object(recovery.tempfile, "gettempdir", return_value=str(tmp_path)), \
         patch.object(recovery.subprocess, "run") as git:
        result = recovery.snapshot(tmp_path / "job")
    assert result == {
        "operation": recovery.OPERATION, "found": False, "file_count": 0,
        "archive_sha256": None, "source_sha_verified": False,
    }
    git.assert_not_called()


def test_exact_sha_bound_bytes_export_privately_without_touching_original(tmp_path):
    root, contents, expected = _fixture(tmp_path)
    before = {p: (root / p).read_bytes() for p in contents}
    result = _run(tmp_path, expected)
    assert result["found"] and result["source_sha_verified"]
    assert result["file_count"] == len(contents)
    output = tmp_path / "job/.local/hostops-v2-ef02-202/verified-source.zip"
    assert result["archive_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    with zipfile.ZipFile(output) as z:
        assert sorted(z.namelist()) == sorted(contents)
        assert all(z.read(name) == raw for name, raw in contents.items())
    assert before == {p: (root / p).read_bytes() for p in contents}


def test_wrong_bytes_are_denied_without_partial_archive(tmp_path):
    root, contents, expected = _fixture(tmp_path)
    (root / next(iter(contents))).write_bytes(b"tampered\n")
    with pytest.raises(recovery.RecoveryDenied, match="L04_SOURCE_HASH_MISMATCH"):
        _run(tmp_path, expected)
    assert not (tmp_path / "job/.local/hostops-v2-ef02-202/verified-source.zip").exists()


def test_symlinked_content_is_denied(tmp_path):
    root, contents, expected = _fixture(tmp_path)
    path = root / next(iter(contents))
    path.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"not trusted")
    try:
        path.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink requires platform privilege")
    with pytest.raises(recovery.RecoveryDenied, match="L04_SOURCE_SYMLINK_DENIED"):
        _run(tmp_path, expected)


def test_mismatched_git_source_is_denied(tmp_path):
    _root, _contents, expected = _fixture(tmp_path)
    wrong = subprocess.CompletedProcess([], 0, b"0" * 40 + b"\n", b"")
    with patch.object(recovery.tempfile, "gettempdir", return_value=str(tmp_path)), \
         patch.object(recovery, "EXPECTED", expected), \
         patch.object(recovery.subprocess, "run", return_value=wrong):
        with pytest.raises(recovery.RecoveryDenied, match="L04_SOURCE_SHA_MISMATCH"):
            recovery.snapshot(tmp_path / "job")


def test_scope_is_hardcoded_to_exact_one_worktree_and_known_paths(tmp_path):
    root, contents, expected = _fixture(tmp_path)
    root.rename(tmp_path / "ai-commerce-ef02-a14_l04-another")
    with patch.object(recovery.tempfile, "gettempdir", return_value=str(tmp_path)):
        result = recovery.snapshot(tmp_path / "job")
    assert result["found"] is False
    assert set(recovery.EXPECTED).issuperset({
        "src/ai_commerce/affiliate_attribution/verification/consumer-tests.txt",
        "tests/test_a14_attribution.py",
    })
