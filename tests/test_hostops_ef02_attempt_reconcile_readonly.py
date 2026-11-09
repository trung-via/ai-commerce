"""Contract/negative tests for the one-shot HostOps EF02 #698 READ-only probe."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from ai_commerce.hostops_v3 import ef02_attempt_reconcile_readonly as probe


class EF02Attempt698ReadOnlyTests(unittest.TestCase):
    def test_exact_issue_attempt_and_no_replay_or_writes(self):
        with patch.object(probe, "_probe_pid", return_value="PID_ABSENT"), patch.object(
            probe, "_probe_worktree",
            return_value=("WORKTREE_ABSENT", "SOURCE_NOT_INSPECTED", "RECEIPT_NOT_INSPECTED")
        ):
            result = probe.observe()
        self.assertEqual(result["issue_number"], 698)
        self.assertEqual(result["operation"], "EF02_ATTEMPT_RECONCILE_READONLY")
        self.assertEqual(result["status"], "HOLD_UNKNOWN_EFFECT")
        self.assertFalse(result["write_attempted"])
        self.assertFalse(result["replay_authorized"])
        self.assertFalse(result["provider_outcome_proven"])
        self.assertEqual(result["process"], "PID_ABSENT")
        self.assertEqual(result["source"], "SOURCE_NOT_INSPECTED")
        self.assertEqual(len(json.dumps(result)), len(json.dumps(json.loads(json.dumps(result)))))
        self.assertEqual(
            set(result),
            {"schema", "issue_number", "operation", "source_sha", "attempt_id", "status",
             "process", "worktree", "source", "local_receipt", "replay_authorized",
             "write_attempted", "provider_outcome_proven"},
        )

    def test_pid_reuse_and_unknown_do_not_auto_authorize(self):
        states = ("PID_MATCHED_ACTIVE", "PID_REUSED_OR_UNRELATED", "PROCESS_READ_UNAVAILABLE")
        for state in states:
            with self.subTest(state=state), patch.object(probe, "_probe_pid", return_value=state), patch.object(
                probe, "_probe_worktree",
                return_value=("WORKTREE_SINGLE", "SOURCE_BYTES_PRESENT_UNVERIFIED",
                              "LOCAL_RESULT_PRESENT_UNVERIFIED")
            ):
                result = probe.observe()
                self.assertEqual(result["status"], "HOLD_UNKNOWN_EFFECT")
                self.assertFalse(result["replay_authorized"])
                self.assertFalse(result["provider_outcome_proven"])

    @unittest.skipUnless(os.name == "nt", "actual fixed Windows probe requires Windows")
    def test_fixed_native_pid_lookup_and_strict_identity(self):
        command = ("python -m ai_commerce.engineering --attempt-id " + probe.ATTEMPT
                   + " --executor antigravity --model gemini-3.8-flash")
        def fake(data):
            return type("Child", (), {"returncode": 0, "stdout": json.dumps(data).encode()})()
        with patch.object(probe.subprocess, "run", return_value=fake({"present": False})) as run:
            self.assertEqual(probe._probe_pid(), "PID_ABSENT")
            args = run.call_args.args[0]
            self.assertEqual(args[:3], ["powershell.exe", "-NoProfile", "-NonInteractive"])
            self.assertIn("ProcessId=14636", args[4])
            self.assertIs(run.call_args.kwargs["shell"], False)
        good = {"present": True, "name": "python.exe",
                "created": "2026-11-09T06:26:05+00:00", "command": command}
        with patch.object(probe.subprocess, "run", return_value=fake(good)):
            self.assertEqual(probe._probe_pid(), "PID_MATCHED_ACTIVE")
        for change in (
            {"created": "2026-11-09T08:26:05+00:00"}, {"command": "python -m other"},
            {"name": "cmd.exe"}, {"command": command.replace(probe.ATTEMPT, "wrong-attempt")}
        ):
            with self.subTest(change=change), patch.object(
                probe.subprocess, "run", return_value=fake({**good, **change})
            ):
                self.assertEqual(probe._probe_pid(), "PID_REUSED_OR_UNRELATED")

    @unittest.skipUnless(os.name == "nt", "native Windows path checks required")
    def test_readonly_source_inspection_does_not_promote_scratch_to_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = base / ("ai-commerce-ef02-" + probe.ACTION + "-one")
            path = root / "src" / "ai_commerce" / "creative_quality"
            path.mkdir(parents=True)
            file = path / "behavioral_learning_fresh_replay.py"
            file.write_bytes(b"UNVERIFIED")
            before = file.read_bytes()
            with patch.object(probe.tempfile, "gettempdir", return_value=folder):
                self.assertEqual(probe._probe_worktree(),
                    ("WORKTREE_SINGLE", "SOURCE_BYTES_PRESENT_UNVERIFIED",
                     "NO_TERMINAL_RECEIPT_OBSERVED"))
            self.assertEqual(file.read_bytes(), before)
            with patch.object(probe.tempfile, "gettempdir", return_value=folder), patch.object(
                probe, "_probe_pid", return_value="PID_ABSENT"
            ):
                observed = probe.observe()
                self.assertFalse(observed["replay_authorized"])
                self.assertFalse(observed["provider_outcome_proven"])

    @unittest.skipUnless(os.name == "nt", "native Windows reparse negative required")
    def test_symlink_or_junction_is_never_read_as_source(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            real = base / "real"
            real.mkdir()
            link = base / ("ai-commerce-ef02-" + probe.ACTION + "-link")
            try:
                link.symlink_to(real, target_is_directory=True)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 1314:
                    self.skipTest("NATIVE_SYMLINK_CREATION_UNPROVEN: WinError 1314")
                raise
            with patch.object(probe.tempfile, "gettempdir", return_value=folder):
                state = probe._probe_worktree()
            self.assertEqual(state[0], "WORKTREE_REPARSE_OR_HARDLINK")

    def test_workflow_gate_requires_owner_exact_698_and_sanitizer(self):
        workflow = (Path(__file__).resolve().parents[1]
                    / ".github" / "workflows" / "issue-ingress.yml").read_text(encoding="utf-8")
        self.assertIn("[HOSTOPS EF02 RECONCILE] #698", workflow)
        self.assertIn("ai-commerce.hostops.ef02-attempt-reconcile.v1", workflow)
        self.assertIn("EF02_ATTEMPT_RECONCILE_READONLY", workflow)
        self.assertIn("hostops-ef02-attempt-698-readonly", workflow)
        self.assertIn("replay_authorized", workflow)
        self.assertIn("NO_R3_DISPATCH", workflow)


if __name__ == "__main__":
    unittest.main()
