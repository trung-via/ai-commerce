"""Standard-library safety tests: exact #202 source recovery without provider calls."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hostops_ef02_202_recovery.py"
spec = importlib.util.spec_from_file_location("hostops_ef02_202_recovery_test", SCRIPT)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def make_fixture(self, base):
        root = base / recovery.WORKTREE_BASENAME
        root.mkdir()
        contents = {
            "src/ai_commerce/affiliate_attribution/evidence.py": b"offline only\n",
            "tests/test_a14_attribution.py": b"assert True\n",
        }
        for relative, data in contents.items():
            p = root / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        hashes = {k: hashlib.sha256(v).hexdigest() for k, v in contents.items()}
        return root, contents, hashes

    def run_recovery(self, base, hashes):
        git = subprocess.CompletedProcess([], 0, recovery.SOURCE_SHA.encode() + b"\n", b"")
        with patch.object(recovery.tempfile, "gettempdir", return_value=str(base)), \
             patch.object(recovery, "EXPECTED", hashes), \
             patch.object(recovery.subprocess, "run", return_value=git):
            return recovery.snapshot(base / "job")

    def test_missing_source_is_not_silent_reexecution(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with patch.object(recovery.tempfile, "gettempdir", return_value=str(root)), \
                 patch.object(recovery.subprocess, "run") as git:
                result = recovery.snapshot(root / "job")
            self.assertEqual(result, {
                "operation": recovery.OPERATION, "found": False, "file_count": 0,
                "archive_sha256": None, "source_sha_verified": False,
            })
            git.assert_not_called()

    def test_exact_bytes_are_exported_without_mutating_original(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root, contents, hashes = self.make_fixture(base)
            result = self.run_recovery(base, hashes)
            self.assertTrue(result["found"] and result["source_sha_verified"])
            self.assertEqual(result["file_count"], len(contents))
            archive = base / "job/.local/hostops-v2-ef02-202/verified-source.zip"
            self.assertEqual(result["archive_sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(sorted(z.namelist()), sorted(contents))
                for relative, data in contents.items():
                    self.assertEqual(z.read(relative), data)
                    self.assertEqual((root / relative).read_bytes(), data)

    def test_divergent_bytes_fail_closed_without_snapshot(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root, contents, hashes = self.make_fixture(base)
            (root / next(iter(contents))).write_bytes(b"tampered\n")
            with self.assertRaisesRegex(recovery.RecoveryDenied, "L04_SOURCE_HASH_MISMATCH"):
                self.run_recovery(base, hashes)
            self.assertFalse((base / "job/.local/hostops-v2-ef02-202/verified-source.zip").exists())

    def test_symlinked_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root, contents, hashes = self.make_fixture(base)
            target = root / next(iter(contents))
            target.unlink()
            outside = base / "outside.txt"
            outside.write_bytes(b"unsafe")
            try:
                target.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaisesRegex(recovery.RecoveryDenied, "L04_SOURCE_SYMLINK_DENIED"):
                self.run_recovery(base, hashes)

    def test_wrong_source_head_fails_closed(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            _root, _contents, hashes = self.make_fixture(base)
            wrong = subprocess.CompletedProcess([], 0, b"0" * 40 + b"\n", b"")
            with patch.object(recovery.tempfile, "gettempdir", return_value=str(base)), \
                 patch.object(recovery, "EXPECTED", hashes), \
                 patch.object(recovery.subprocess, "run", return_value=wrong):
                with self.assertRaisesRegex(recovery.RecoveryDenied, "L04_SOURCE_SHA_MISMATCH"):
                    recovery.snapshot(base / "job")

    def test_only_one_exact_worktree_can_be_read(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root, _contents, _hashes = self.make_fixture(base)
            root.rename(base / "ai-commerce-ef02-a14_l04-other")
            with patch.object(recovery.tempfile, "gettempdir", return_value=str(base)):
                result = recovery.snapshot(base / "job")
            self.assertFalse(result["found"])
            self.assertEqual(len(recovery.EXPECTED), 12)
            self.assertIn("tests/test_a14_attribution.py", recovery.EXPECTED)



# E04 recovery is an independently pinned exact-#223 read, not a generic host file API.
E04_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hostops_ef02_223_recovery.py"
e04_spec = importlib.util.spec_from_file_location("hostops_ef02_223_recovery_test", E04_SCRIPT)
e04_recovery = importlib.util.module_from_spec(e04_spec)
e04_spec.loader.exec_module(e04_recovery)


class E04RecoveryTests(unittest.TestCase):
    def make_fixture(self, base):
        root = base / e04_recovery.WORKTREE_BASENAME
        root.mkdir()
        files = {
            "src/ai_commerce/affiliate_commerce/service.py": b"offline E04\n",
            "tests/test_a14_e04.py": b"def test_e04(): assert True\n",
        }
        for relative, content in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        expected = {relative: hashlib.sha256(content).hexdigest()
                    for relative, content in files.items()}
        return root, files, expected

    def test_missing_e04_worktree_does_not_reexecute_model(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.object(e04_recovery.tempfile, "gettempdir", return_value=str(base)), \
                 patch.object(e04_recovery.subprocess, "run") as call:
                result = e04_recovery.snapshot(base / "job")
            self.assertFalse(result["found"])
            self.assertIsNone(result["archive_sha256"])
            call.assert_not_called()

    def test_e04_source_hash_verified_export_and_original_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, files, expected = self.make_fixture(base)
            git = subprocess.CompletedProcess([], 0, e04_recovery.SOURCE_SHA.encode() + b"\n", b"")
            with patch.object(e04_recovery.tempfile, "gettempdir", return_value=str(base)), \
                 patch.object(e04_recovery, "EXPECTED", expected), \
                 patch.object(e04_recovery.subprocess, "run", return_value=git):
                result = e04_recovery.snapshot(base / "job")
            self.assertTrue(result["source_sha_verified"] and result["found"])
            self.assertEqual(result["file_count"], len(files))
            archive = base / "job/.local/hostops-v2-ef02-223/verified-source.zip"
            self.assertEqual(result["archive_sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(sorted(z.namelist()), sorted(files))
                for relative, content in files.items():
                    self.assertEqual(z.read(relative), content)
                    self.assertEqual((root / relative).read_bytes(), content)

    def test_e04_hash_tamper_is_blocked_before_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, files, expected = self.make_fixture(base)
            next(iter((root / p for p in files))).write_bytes(b"tampered\n")
            git = subprocess.CompletedProcess([], 0, e04_recovery.SOURCE_SHA.encode() + b"\n", b"")
            with patch.object(e04_recovery.tempfile, "gettempdir", return_value=str(base)), \
                 patch.object(e04_recovery, "EXPECTED", expected), \
                 patch.object(e04_recovery.subprocess, "run", return_value=git):
                with self.assertRaisesRegex(e04_recovery.RecoveryDenied, "E04_SOURCE_HASH_MISMATCH"):
                    e04_recovery.snapshot(base / "job")
            self.assertFalse((base / "job/.local/hostops-v2-ef02-223/verified-source.zip").exists())

    def test_e04_wrong_git_head_blocks_export(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            _, _, expected = self.make_fixture(base)
            wrong = subprocess.CompletedProcess([], 0, b"0"*40+b"\n", b"")
            with patch.object(e04_recovery.tempfile, "gettempdir", return_value=str(base)), \
                 patch.object(e04_recovery, "EXPECTED", expected), \
                 patch.object(e04_recovery.subprocess, "run", return_value=wrong):
                with self.assertRaisesRegex(e04_recovery.RecoveryDenied, "E04_SOURCE_SHA_MISMATCH"):
                    e04_recovery.snapshot(base / "job")

if __name__ == "__main__":
    unittest.main()
