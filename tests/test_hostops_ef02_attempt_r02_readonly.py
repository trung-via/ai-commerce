"""R0.2 #698 bounded forensic read tests: no model/replay/write authority."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from ai_commerce.hostops_v3 import ef02_attempt_r02_readonly as r
from ai_commerce.hostops_v3 import ef02_attempt_reconcile_readonly as prior


class EF02ForensicR02Tests(unittest.TestCase):
    def test_closed_contract_and_fixed_six_files(self):
        self.assertEqual(len(r.PATHS), 6)
        self.assertEqual(len(set(r.PATHS)), 6)
        self.assertEqual(r.PATHS[-1], "docs/roadmap/ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md")
        self.assertEqual(r.OPERATION, "EF02_698_FORENSIC_READONLY_R02")
        self.assertEqual(r.EVIDENCE_ROOT, prior._EVIDENCE_ACTION_ROOT)
        self.assertFalse(r.observe()["replay_authorized"])
        self.assertFalse(r.observe()["provider_outcome_proven"])
        self.assertFalse(r.observe()["write_attempted"])

    def test_absent_evidence_root_distinguished(self):
        with tempfile.TemporaryDirectory() as directory:
            fixed = Path(directory) / "missing-batch" / "missing-action"
            with patch.object(r, "EVIDENCE_ROOT", fixed):
                root, files = r._probe_evidence_root()
                self.assertEqual(root, "BATCH_ABSENT")
                self.assertEqual(files, ["ROOT_ABSENT"] * 3)
                fixed.parent.mkdir()
                root, files = r._probe_evidence_root()
                self.assertEqual(root, "ACTION_ABSENT")
                self.assertEqual(files, ["ROOT_ABSENT"] * 3)

    def test_fixed_boundary_does_not_promote_synthetic_return(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "batch" / "action"
            (root / "ef01").mkdir(parents=True)
            files = [root / p for p in r.BOUNDARY_FILES]
            for path, stage in zip(files, ("BEFORE_EXECUTOR_SUBPROCESS",
                                         "EXECUTOR_SUBPROCESS_RETURNED", None)):
                document = {"attempt_id": prior.ATTEMPT}
                if stage:
                    document["stage"] = stage
                else:
                    document["work_id"] = prior.BATCH + ":" + prior.ACTION
                path.write_text(json.dumps(document), encoding="utf-8")
            with patch.object(r, "EVIDENCE_ROOT", root), patch.object(
                r, "_read_scope", return_value=("SCOPE_PRESENT_UNVERIFIED", 6, 0, 1, "a" * 64)
            ):
                result = r.observe()
                self.assertEqual(result["evidence_root"], "ACTION_PRESENT")
                self.assertEqual(result["provider_return"], "PRESENT_UNVERIFIED")
                self.assertEqual(result["terminal"], "PRESENT_UNVERIFIED")
                self.assertEqual(result["status"], "HOLD_UNKNOWN_EFFECT")
                self.assertFalse(result["replay_authorized"])
                files[1].write_text(json.dumps({"attempt_id": "forged",
                                                "stage": "EXECUTOR_SUBPROCESS_RETURNED"}),
                                    encoding="utf-8")
                self.assertEqual(r._probe_evidence_root()[1][1], "UNSAFE")
                files[2].write_text("{broken", encoding="utf-8")
                self.assertEqual(r._probe_evidence_root()[1][2], "READ_UNKNOWN")

    def test_exact_six_scope_presence_and_roadmap_change_is_not_source_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "isolated"
            root.mkdir()
            for rel in r.PATHS:
                file = root / rel
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(("R2: " + rel).encode())
            with patch.object(r, "_get_isolated_worktree",
                              return_value=("WORKTREE_SINGLE", root)), patch.object(
                r.prior, "_safe_chain", return_value=True
            ), patch.object(r, "_git_blob", return_value=b"ORIGINAL"):
                status, seen, missing, changed, digest = r._read_scope()
                self.assertEqual(status, "SCOPE_PRESENT_UNVERIFIED")
                self.assertEqual((seen, missing, changed), (6, 0, 1))
                self.assertEqual(len(digest), 64)
                self.assertFalse(r.observe()["replay_authorized"])
                (root / r.PATHS[0]).unlink()
                status, seen, missing, changed, digest = r._read_scope()
                self.assertEqual(status, "SCOPE_PARTIAL_UNVERIFIED")
                self.assertEqual((seen, missing), (5, 1))
                self.assertEqual(len(digest), 64)

    def test_dangling_source_symlink_never_means_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "isolated"
            root.mkdir()
            with patch.object(r, "_get_isolated_worktree",
                              return_value=("WORKTREE_SINGLE", root)), patch.object(
                Path, "is_symlink", return_value=True
            ):
                self.assertEqual(r._read_scope()[0], "SOURCE_UNSAFE")

    @unittest.skipUnless(os.name == "nt", "real reparse check requires Windows")
    def test_symlink_scope_path_is_unsafe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "isolated"
            root.mkdir()
            outside = Path(directory) / "outside"
            outside.write_text("not an authorized file", encoding="utf-8")
            target = root / r.PATHS[0]
            target.parent.mkdir(parents=True)
            try:
                target.symlink_to(outside)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 1314:
                    self.skipTest("NATIVE_SYMLINK_CREATION_UNPROVEN: WinError 1314")
                raise
            with patch.object(r, "_get_isolated_worktree",
                              return_value=("WORKTREE_SINGLE", root)):
                self.assertEqual(r._read_scope()[0], "SOURCE_UNSAFE")

    def test_no_external_scope_command_or_provider_calls(self):
        self.assertRaises(ValueError, r._git_blob, Path("."), "../../secret")
        with patch.object(r, "_probe_evidence_root",
                          return_value=("ACTION_ABSENT", ["ROOT_ABSENT"] * 3)), patch.object(
            r, "_read_scope", return_value=("WORKTREE_ABSENT", 0, 0, 0, None)
        ):
            result = r.observe()
            self.assertEqual(result["issue_number"], 698)
            self.assertEqual(result["attempt_id"], prior.ATTEMPT)
            self.assertFalse(result["provider_outcome_proven"])
            self.assertFalse(result["replay_authorized"])
            self.assertFalse(result["write_attempted"])
            self.assertIsNone(result["scope_manifest_digest"])
            self.assertEqual(set(result), {
                "schema", "operation", "issue_number", "source_sha", "attempt_id",
                "status", "evidence_root", "boundary", "provider_return", "terminal",
                "source_state", "scope_seen", "scope_missing", "roadmap_diff_unverified",
                "scope_manifest_digest", "replay_authorized", "provider_outcome_proven",
                "write_attempted"
            })

    def test_protected_workflow_bound_to_only_r02_owner_event(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
                    / "issue-ingress.yml").read_text(encoding="utf-8")
        for text in ("[HOSTOPS EF02 FORENSIC] #698 R0.2 read-only",
                     "ai-commerce.hostops.ef02-698-r02.v1",
                     "EF02_698_FORENSIC_READONLY_R02",
                     "ef02_attempt_r02_readonly",
                     "HOLD_UNKNOWN_EFFECT",
                     "NO_R3_DISPATCH"):
            self.assertIn(text, workflow)


if __name__ == "__main__":
    unittest.main()
