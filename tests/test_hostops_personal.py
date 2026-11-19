"""Portable source tests; live effect requires existing AI Commerce Windows runner."""
import builtins
import importlib.util
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path, PureWindowsPath
import re
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "src" / "ai_commerce" / "hostops_v3" / "personal.py"
spec = importlib.util.spec_from_file_location("hostops_personal", MODULE)
hostops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hostops)


def event(mode, operation, parameters, *, comment=None):
    number = 17
    result = {
        "repository": {
            "full_name": hostops.REPO,
            "private": False,
            "owner": {"login": hostops.OWNER},
        },
        "issue": {
            "number": number,
            "state": "open",
            "title": (hostops.READ_TITLE if mode == "read"
                      else hostops.WRITE_TITLE) + "test",
            "user": {"login": hostops.OWNER},
            "body": json.dumps({
                "schema": hostops.SCHEMA,
                "operation": operation,
                "parameters": parameters,
            }),
        },
        "sender": {"login": hostops.OWNER},
        "action": "opened" if mode == "read" else "created",
    }
    if mode == "write":
        result["comment"] = {
            "body": (comment if comment is not None else "APPROVE WRITE #17"),
            "user": {"login": hostops.OWNER},
        }
    return result


class PersonalHostOpsContractTests(unittest.TestCase):
    @staticmethod
    def _standing_snapshot():
        return {
            "status": "OBSERVED", "operation": "standing_read_diagnostics", "windows": True,
            "diagnostics": [{"category": category, "code": hostops._STANDING_CATEGORY_CODES[category][0]}
                            for category in hostops.STANDING_READ_CATEGORIES],
            "arbitrary_shell_enabled": False, "exact_chat_verified": False, "wake_sent": False,
        }

    def test_standing_read_uses_standing_consent_and_owner_event(self):
        value = event("read", "standing_read_diagnostics", {})
        self.assertNotIn("comment", value)
        self.assertEqual(hostops.parse_event(value, "read")["operation"], "standing_read_diagnostics")
        for location in ("sender", "issue"):
            bad = event("read", "standing_read_diagnostics", {})
            (bad["issue"]["user"] if location == "issue" else bad["sender"])["login"] = "other"
            with self.subTest(location=location), self.assertRaises(hostops.Denied):
                hostops.parse_event(bad, "read")
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(event("write", "standing_read_diagnostics", {}), "write")

    def test_standing_read_rejects_all_issue_selectors(self):
        for params in ({"path": "C:/private"}, {"port": 9222}, {"command": "Get-Process"},
                       {"category": "chrome_profile"}, {"pid": 1}, {"root_id": "scratch"}):
            with self.subTest(params=params), self.assertRaisesRegex(hostops.Denied, "INVALID_PARAMETERS"):
                hostops.parse_event(event("read", "standing_read_diagnostics", params), "read")

    def test_standing_read_uses_independently_approved_public_contract(self):
        request = hostops.parse_event(event("read", "standing_read_diagnostics", {}), "read")
        with patch.object(hostops, "_standing_read_snapshot", return_value=self._standing_snapshot()) as collect:
            result = hostops.perform(request)
            collect.assert_called_once_with()
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["operation"], "standing_read_diagnostics")
        self.assertEqual(len(result["diagnostics"]), 16)
        self.assertIn("diagnostics", hostops.PERSONAL_PUBLIC_FIELDS)

    def test_standing_read_public_fields_match_protected_workflow(self):
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        fields = re.search(r"const publicFields = \[(.*?)\];", workflow).group(1)
        self.assertEqual(hostops.PERSONAL_PUBLIC_FIELDS, set(re.findall(r"'([^']+)'", fields)) | {"runners"})

    def test_standing_read_protected_workflow_catalog_matches_python_contract(self):
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        match = re.search(r"const standingCatalog = (\[[^\n]+\]);", workflow)
        self.assertIsNotNone(match, "Missing independently reviewed, exact ordered sanitizer table")
        self.assertEqual(json.loads(match.group(1)),
                         [[category, list(codes)]
                          for category, codes in hostops._STANDING_CATEGORY_CODES.items()])
        self.assertIn("INVALID_PUBLIC_DIAGNOSTIC_RECEIPT", workflow)
        self.assertIn("rawNativeText.trim() === JSON.stringify(record)", workflow)
        self.assertIn("record.issue_number === context.issue.number", workflow)
        self.assertIn("record.source_sha === context.sha", workflow)
        self.assertIn("delete sanitized.diagnostics", workflow)

    def test_standing_read_protected_sanitizer_runtime_positive_and_negative(self):
        """Execute the actual GitHub workflow JS with Node, no GitHub or host effects."""
        import copy
        import os
        import shutil
        import subprocess
        node = shutil.which("node") or shutil.which("node.exe")
        if not node and os.name == "nt" and os.environ.get("RUNNER_TEMP"):
            runner_root = Path(os.environ["RUNNER_TEMP"]).parents[1]
            for version in ("node24", "node20"):
                candidate = runner_root / "externals" / version / "bin" / "node.exe"
                if candidate.is_file():
                    node = str(candidate)
                    break
        if not node:
            self.skipTest("Node CLI unavailable: hosted Windows JS sanitizer verification required")
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        # Keep this historical Personal sanitizer harness scoped to its
        # actual job: the new D5 carrier has a different independent JS receipt.
        workflow = workflow.split("  hostops-d5-carrier:", 1)[0]
        self.assertEqual(workflow.count("          script: |"), 1)
        script = "\n".join(line[12:] if line.startswith("            ") else line
                           for line in workflow.split("          script: |\n", 1)[1].splitlines())
        original = {**self._standing_snapshot(),
                    "issue_number": 17,
                    "approval_channel": "CHATGPT_TO_AI_COMMERCE_ISSUE",
                    "remote_shell_enabled": False,
                    "source_sha": "f" * 40}
        encode = lambda x: json.dumps(x, ensure_ascii=True, separators=(",", ":"))
        valid = encode(original)
        cases = [{"name": "valid", "body": valid, "status": "OBSERVED", "code": None}]
        for key, val in (
            ("extra_public_field", {**original, "content_utf8": "PRIVATE_SECRET"}),
            ("private_executor_receipt", {**original, "executors": [{"account": "PRIVATE_SECRET"}]}),
            ("private_bridge_snapshot", {**original, "snapshot": {"executors": [{"account": "PRIVATE_SECRET"}]}}),
            ("wrong_source_sha", {**original, "source_sha": "0" * 40}),
            ("wrong_issue", {**original, "issue_number": 99}),
            ("wrong_approval", {**original, "approval_channel": "OTHER"}),
            ("wrong_flag", {**original, "wake_sent": True}),
            ("wrong_windows", {**original, "windows": 1}),
            ("short_rows", {**original, "diagnostics": original["diagnostics"][:-1]}),
            ("reversed_rows", {**original, "diagnostics": list(reversed(original["diagnostics"]))}),
            ("raw_row", {**original, "diagnostics": [
                {**original["diagnostics"][0], "private_path": "PRIVATE_SECRET"},
                *original["diagnostics"][1:]]}),
            ("foreign_code", {**original, "diagnostics": [
                {"category": original["diagnostics"][0]["category"], "code": "PRIVATE_SECRET"},
                *original["diagnostics"][1:]]}),
        ):
            cases.append({"name": key, "body": encode(val), "status": "BLOCKED",
                          "code": "INVALID_PUBLIC_DIAGNOSTIC_RECEIPT"})
        duplicate_raw = valid.replace('"status":"OBSERVED"',
                                      '"status":"OBSERVED","status":"OBSERVED"', 1)
        cases.append({"name": "duplicate_json_key", "body": duplicate_raw, "status": "BLOCKED",
                      "code": "INVALID_PUBLIC_DIAGNOSTIC_RECEIPT"})
        cases.append({"name": "oversize", "body": valid[:-1] + (',"padding":"' + "x" * 8192 + '"}'),
                      "status": "BLOCKED", "code": "INVALID_PUBLIC_DIAGNOSTIC_RECEIPT"})
        cases.append({"name": "legacy_no_nested_leak",
                      "body": encode({"status": "OBSERVED", "operation": "inventory",
                                     "diagnostics": [{"private_path": "PRIVATE_SECRET"}]}),
                      "status": "OBSERVED", "code": None, "no_diagnostics": True})
        # Exercise the existing protected transport with an independent oracle
        # for every new topology stage code; no new public field is required.
        for code in sorted(PersonalCdp9444TopologyTests.NATIVE_FAILURE_CODES):
            topology = {
                "status": "UNKNOWN", "code": code, "operation": "cdp_9444_topology",
                "cdp_port": 9444, "listener_count": None, "loopback_count": None,
                "unexpected_binding_count": None, "chrome_owned_loopback_count": None,
                "same_session_chrome_count": None, "devtools_protocol_ok": None,
                "infrastructure_ready": False, "exact_chat_verified": False,
                "wake_sent": False, "arbitrary_shell_enabled": False,
                "issue_number": 17, "approval_channel": "CHATGPT_TO_AI_COMMERCE_ISSUE",
                "remote_shell_enabled": False, "source_sha": "f" * 40,
            }
            self.assertLessEqual(len(encode(topology).encode("utf-8")), 8192)
            cases.append({"name": code, "body": encode(topology), "status": "UNKNOWN", "code": code,
                          "no_diagnostics": True, "expected": topology})
            cases.append({"name": code + "_no_nested_leak",
                          "body": encode({**topology, "raw_native": {"path": "PRIVATE_SECRET"},
                                          "diagnostics": [{"message": "PRIVATE_SECRET"}]}),
                          "status": "UNKNOWN", "code": code,
                          "no_diagnostics": True, "expected": topology})
        harness = r"""
const fs = require('fs'), path = require('path'), vm = require('vm');
const cases = JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const script = fs.readFileSync(process.argv[2],'utf8');
(async () => {
  for (const item of cases) {
    fs.writeFileSync(path.join(process.env.RUNNER_TEMP,'personal-hostops-result.json'),item.body,'utf8');
    const comments = [];
    const context = {repo:{owner:'trung-via',repo:'ai-commerce'},issue:{number:17},sha:'f'.repeat(40)};
    const github = {rest:{issues:{createComment:async args => { comments.push(args.body); }}}};
    await vm.runInNewContext('(async () => {\n'+script+'\n})()',
      {require,process,context,github,Buffer});
    if (comments.length !== 1 || !comments[0].startsWith('AI_COMMERCE_PERSONAL_HOSTOPS_RESULT_V1\n'))
      throw new Error(item.name+': missing result');
    const result = JSON.parse(comments[0].split('\n').slice(1).join('\n'));
    if (result.status !== item.status ||
        (item.code && result.code !== item.code))
      throw new Error(item.name+': unexpected '+JSON.stringify(result));
    if (item.status === 'OBSERVED' && !item.no_diagnostics &&
        (!Array.isArray(result.diagnostics) || result.diagnostics.length !== 16))
      throw new Error(item.name+': diagnostics missing');
    if (item.no_diagnostics && Object.prototype.hasOwnProperty.call(result,'diagnostics'))
      throw new Error(item.name+': leaked legacy diagnostics');
    if (item.expected) require('assert').deepStrictEqual(result,item.expected,item.name+': changed receipt');
    if (JSON.stringify(result).includes('PRIVATE_SECRET'))
      throw new Error(item.name+': private data leak');
  }
  process.stdout.write('VERIFIED_CASES='+cases.length);
})().catch(error => { console.error(error); process.exitCode=1; });
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "cases.json").write_text(encode(cases), encoding="utf-8")
            (path / "workflow.js").write_text(script, encoding="utf-8")
            env = {**os.environ, "RUNNER_TEMP": str(path)}
            completed = subprocess.run(
                [node, "-e", harness, str(path / "cases.json"), str(path / "workflow.js")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30, env=env, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr[-3000:])
        self.assertIn("VERIFIED_CASES=" + str(len(cases)), completed.stdout)

    def test_standing_read_snapshot_schema_is_fixed_bounded_and_copied(self):
        snapshot = self._standing_snapshot()
        result = hostops._validate_standing_snapshot(snapshot)
        self.assertEqual(len(result["diagnostics"]), 16)
        self.assertLessEqual(len(json.dumps(result).encode()), 8192)
        self.assertEqual(result, snapshot)
        self.assertIsNot(result["diagnostics"], snapshot["diagnostics"])
        self.assertIsNot(result["diagnostics"][0], snapshot["diagnostics"][0])

    def test_standing_read_schema_rejects_raw_fields_and_wrong_types(self):
        base = self._standing_snapshot()
        invalid = [None, [], {**base, "raw": "private"}, {**base, "windows": 1},
                   {**base, "wake_sent": 0}, {**base, "exact_chat_verified": True},
                   {**base, "arbitrary_shell_enabled": True}, {**base, "status": "READY"},
                   {**base, "operation": "read_text"}, {**base, "diagnostics": {}},
                   {**base, "content_utf8": "private"}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(hostops.Denied, "INVALID_PUBLIC_DIAGNOSTIC_RECEIPT"):
                hostops._validate_standing_snapshot(value)

    def test_standing_read_schema_rejects_missing_duplicate_reordered_and_extra_rows(self):
        base = self._standing_snapshot()
        rows = base["diagnostics"]
        for invalid in (rows[:-1], rows + [rows[0]], [rows[0]] * 16, list(reversed(rows))):
            with self.subTest(rows=invalid), self.assertRaises(hostops.Denied):
                hostops._validate_standing_snapshot({**base, "diagnostics": invalid})

    def test_standing_read_schema_rejects_unknown_and_cross_category_codes(self):
        base = self._standing_snapshot()
        for row in (None, {"category": "chrome_profile", "code": "private-path"},
                    {"category": "chrome_profile", "code": "RUNNING"},
                    {"category": "chrome_profile", "code": True},
                    {"category": "chrome_profile", "code": "PRIVATE_READ_CHANNEL_REQUIRED", "path": "private"},
                    {"category": ["chrome_profile"], "code": "PRIVATE_READ_CHANNEL_REQUIRED"}):
            with self.subTest(row=row), self.assertRaises(hostops.Denied):
                hostops._validate_standing_snapshot({**base, "diagnostics": [row, *base["diagnostics"][1:]]})

    def test_fixed_private_metadata_without_content_or_manifest_following(self):
        # No manifest bytes are parsed and its chrome_config field is never
        # followed: the three reviewed literal Chrome config names are the
        # only candidates. No raw names/path/contents enter the public result.
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp)
            root = local / "AI-Commerce"
            root.mkdir()
            with patch.dict(hostops.os.environ, {"LOCALAPPDATA": str(local)}):
                result = hostops._private_d5_metadata_codes()
                self.assertEqual(result["chrome_config"], "KNOWN_CANDIDATES_ABSENT")
                self.assertEqual(result["chrome_profile"], "UNVERIFIED")
                self.assertEqual(result["chrome_markers"], "UNVERIFIED")
                self.assertEqual(result["d5_metadata"], "MANIFEST_ABSENT")
                (root / "wake-readiness.json").write_text(
                    '{"chrome_config":"C:/DO-NOT-FOLLOW/SECRET","private":"SECRET_TOKEN"}',
                    encoding="utf-8")
                with patch.object(hostops.Path, "read_text",
                                  side_effect=AssertionError("private manifest bytes read")):
                    result = hostops._private_d5_metadata_codes()
                self.assertEqual(result["d5_metadata"], "MANIFEST_PRESENT")
                self.assertEqual(result["chrome_config"], "KNOWN_CANDIDATES_ABSENT")
                self.assertNotIn("SECRET", json.dumps(result))

    def test_private_d5_config_profile_markers_locally_reduced_to_enums(self):
        # Existing real Chrome validator is used, but neither the input config
        # nor the manifest, profile paths, and lock-marker bytes are published.
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp) / "Local"
            root = local / "AI-Commerce"
            config = root / "Chrome-Acquisition" / "config.json"
            config.parent.mkdir(parents=True)
            user_data = root / "Chrome-Doorbell"
            profile = user_data / "Default"
            profile.mkdir(parents=True)
            chrome = Path(temp) / "chrome.exe"
            chrome.write_bytes(b"fixture executable, not a signed Chrome proof")
            record = {
                "version": 1, "executable": str(chrome),
                "user_data_dir": str(user_data), "profile_directory": "Default",
                "cdp_endpoint": "http://127.0.0.1:9333",
                "allow_launch": False, "exclusive_user_data": True,
            }
            with patch.dict(hostops.os.environ, {"LOCALAPPDATA": str(local)}):
                config.write_text(json.dumps(record), encoding="utf-8")
                result = hostops._private_d5_metadata_codes()
                self.assertEqual(result["chrome_config"], "KNOWN_CONFIG_EXACT_9333")
                self.assertEqual(result["chrome_profile"], "DEDICATED_DIR_PRESENT")
                self.assertEqual(result["chrome_markers"], "LOCK_MARKERS_ABSENT")
                self.assertEqual(result["d5_metadata"], "MANIFEST_ABSENT")
                self.assertNotIn(str(local), json.dumps(result))
                (user_data / "SingletonLock").write_text("SECRET_MARKER_CONTENT", encoding="utf-8")
                result = hostops._private_d5_metadata_codes()
                self.assertEqual(result["chrome_markers"], "LOCK_MARKER_PRESENT")
                self.assertNotIn("SECRET_MARKER_CONTENT", json.dumps(result))
                record["cdp_endpoint"] = "http://127.0.0.1:9222"
                config.write_text(json.dumps(record), encoding="utf-8")
                self.assertEqual(hostops._private_d5_metadata_codes()["chrome_config"],
                                 "KNOWN_CONFIG_OTHER_PORT")
                (root / "chrome.json").write_text(json.dumps(record), encoding="utf-8")
                result = hostops._private_d5_metadata_codes()
                self.assertEqual(result["chrome_config"], "KNOWN_CANDIDATES_AMBIGUOUS")
                self.assertEqual(result["chrome_profile"], "UNVERIFIED")
                (root / "chrome.json").unlink()
                config.write_text('{"private_token":"VERY_SECRET"}', encoding="utf-8")
                result = hostops._private_d5_metadata_codes()
                self.assertEqual(result["chrome_config"], "KNOWN_CONFIG_NOT_VERIFIED")
                self.assertNotIn("VERY_SECRET", json.dumps(result))

    def test_private_metadata_missing_root_and_nonexistent_reparse_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp) / "Missing"
            with patch.dict(hostops.os.environ, {"LOCALAPPDATA": str(local)}):
                result = hostops._private_d5_metadata_codes()
                self.assertEqual(result["chrome_config"], "KNOWN_CANDIDATES_ABSENT")
                self.assertEqual(result["d5_metadata"], "MANIFEST_ABSENT")
            with patch.dict(hostops.os.environ, {"LOCALAPPDATA": "relative-do-not-accept"}):
                result = hostops._private_d5_metadata_codes()
                self.assertEqual(result["chrome_config"], "UNAVAILABLE")
            self.assertEqual(hostops._fixed_private_metadata_kind(local / "not-found.json"),
                             "ABSENT")

    def test_standing_read_internal_snapshot_never_reads_private_files_or_mutates(self):
        cdp = {"listener_count": 1, "loopback_count": 1, "unexpected_binding_count": 0,
               "chrome_owned_loopback_count": 1, "same_session_chrome_count": 1, "devtools_protocol_ok": True}
        runners = {"runners": [{"task_state": "Running", "listener_process_count": count} for count in (0, 1, 1)]}
        with patch.object(hostops.os, "name", "nt"), patch.object(hostops, "_private_d5_metadata_codes",
            return_value={"chrome_profile": "DEDICATED_DIR_PRESENT",
                          "chrome_config": "KNOWN_CONFIG_EXACT_9333",
                          "chrome_markers": "LOCK_MARKERS_ABSENT",
                          "d5_metadata": "MANIFEST_PRESENT"}), patch.object(
            hostops, "_cdp_9333_status", return_value=cdp), patch.object(
            hostops, "_runner_runtime_inventory", return_value=runners
        ), patch.object(hostops, "_git", side_effect=["a" * 40, " M private-filename\n"]) as git, patch.object(
            hostops.Path, "read_bytes", side_effect=AssertionError("no private bytes")
        ), patch.object(hostops.Path, "read_text", side_effect=AssertionError("no private text")), patch.object(
            hostops, "_git_write", side_effect=AssertionError("no Git write")
        ), patch.object(hostops.subprocess, "run", side_effect=AssertionError("fixed probes mocked")):
            result = hostops._standing_read_snapshot()
        codes = {row["category"]: row["code"] for row in result["diagnostics"]}
        self.assertEqual(codes["ef02_01_task"], "RUNNING")
        self.assertEqual(codes["ef02_01_listener"], "ABSENT")
        self.assertEqual(codes["ef02_02_listener"], "PRESENT")
        self.assertEqual(codes["cdp_protocol"], "RESPONDING")
        self.assertEqual(codes["repo_git"], "TRACKED_CHANGES")
        self.assertEqual(codes["chrome_profile"], "DEDICATED_DIR_PRESENT")
        self.assertEqual(codes["chrome_config"], "KNOWN_CONFIG_EXACT_9333")
        self.assertEqual(codes["chrome_markers"], "LOCK_MARKERS_ABSENT")
        self.assertEqual(codes["d5_metadata"], "MANIFEST_PRESENT")
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(git.call_args_list[0].args, ("rev-parse", "HEAD"))
        self.assertEqual(git.call_args_list[1].args, ("status", "--porcelain=v1", "--untracked-files=no"))
        self.assertFalse(result["exact_chat_verified"])
        self.assertFalse(result["wake_sent"])

    def test_standing_read_probe_errors_stay_enum_unavailable(self):
        with patch.object(hostops.os, "name", "nt"), patch.object(
            hostops, "_private_d5_metadata_codes", side_effect=ValueError("private error")
        ), patch.object(hostops, "_cdp_9333_status", side_effect=hostops.Denied("private exception")
        ), patch.object(hostops, "_runner_runtime_inventory", side_effect=OSError("private path")), patch.object(
            hostops, "_git", side_effect=hostops.Denied("private output")
        ):
            result = hostops._standing_read_snapshot()
        codes = {row["category"]: row["code"] for row in result["diagnostics"]}
        self.assertEqual(codes["cdp_protocol"], "UNAVAILABLE")
        self.assertEqual(codes["repo_git"], "UNAVAILABLE")
        self.assertEqual(codes["hostops_01_task"], "UNAVAILABLE")
        self.assertNotIn("private", json.dumps(result))

    def test_standing_read_requires_windows_before_any_probe(self):
        with patch.object(hostops.os, "name", "posix"), patch.object(hostops, "_cdp_9333_status") as cdp:
            with self.assertRaisesRegex(hostops.Denied, "STANDING_READ_WINDOWS_ONLY"):
                hostops._standing_read_snapshot()
            cdp.assert_not_called()

    def test_standing_read_python_version_is_numeric(self):
        for version, expected in ((('3', '9', '9'), 'UNSUPPORTED'), (('3', '14', '0'), 'SUPPORTED')):
            with self.subTest(version=version), patch.object(hostops.os, "name", "nt"), patch.object(
                hostops.platform, "python_version_tuple", return_value=version
            ), patch.object(hostops, "_private_d5_metadata_codes",
                return_value={"chrome_profile": "UNVERIFIED",
                              "chrome_config": "UNAVAILABLE",
                              "chrome_markers": "UNVERIFIED",
                              "d5_metadata": "UNAVAILABLE"}), patch.object(
                hostops, "_cdp_9333_status", side_effect=hostops.Denied("unavailable")), patch.object(
                hostops, "_runner_runtime_inventory", side_effect=hostops.Denied("unavailable")
            ), patch.object(hostops, "_git", side_effect=hostops.Denied("unavailable")):
                rows = hostops._standing_read_snapshot()["diagnostics"]
            self.assertEqual(next(row["code"] for row in rows if row["category"] == "python_runtime"), expected)

    def test_inventory_read_auto_from_owner_chat_issue(self):
        request = hostops.parse_event(event("read", "inventory", {}), "read")
        self.assertEqual(request["operation"], "inventory")
        output = hostops.perform(request)
        self.assertEqual(output["status"], "OBSERVED")

    def test_runner_runtime_inventory_is_owner_only_exact_read(self):
        req = hostops.parse_event(event("read", "runner_runtime_inventory", {}), "read")
        self.assertEqual(req["operation"], "runner_runtime_inventory")
        for mode, params in (("read", {"command": "Get-Process"}),
                             ("read", {"task_name": "ALL"}),
                             ("write", {})):
            with self.subTest(mode=mode, params=params):
                with self.assertRaises(hostops.Denied):
                    hostops.parse_event(event(mode, "runner_runtime_inventory", params), mode)
        owner = event("read", "runner_runtime_inventory", {})
        owner["sender"]["login"] = "other-user"
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(owner, "read")

    def test_runner_runtime_inventory_sanitized_native_response(self):
        names = ["AI-COMMERCE-EF02-01", "AI-COMMERCE-EF02-02",
                 "AI-COMMERCE-HOSTOPS-01"]
        rows = [
            {"name": name, "task_state": "Running", "task_interactive": True,
             "hidden_launcher_configured": True,
             "hidden_launcher_process_count": 1,
             "listener_process_count": 1, "listener_session_ids": [1]}
            for name in names
        ]
        payload = {"all_listener_count": 4, "unmatched_listener_count": 1,
                   "runners": rows}
        req = hostops.parse_event(event("read", "runner_runtime_inventory", {}), "read")
        with patch.object(hostops.os, "name", "nt"), patch.object(
            hostops.subprocess, "run"
        ) as fake:
            fake.return_value.returncode = 0
            fake.return_value.stdout = json.dumps(payload)
            result = hostops.perform(req)
            args, kwargs = fake.call_args
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["operation"], "runner_runtime_inventory")
        self.assertEqual(result["all_listener_count"], 4)
        self.assertEqual(result["unmatched_listener_count"], 1)
        self.assertEqual([x["name"] for x in result["runners"]], names)
        self.assertFalse(result["arbitrary_shell_enabled"])
        self.assertEqual(args[0][:4],
                         ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["timeout"], 25)
        self.assertEqual(kwargs["check"], False)
        self.assertNotIn("COMMANDLINE", json.dumps(result).upper())
        self.assertNotIn("EXECUTABLEPATH", json.dumps(result).upper())

    def test_runner_runtime_inventory_rejects_native_unbounded_or_wrong_identity(self):
        names = ["AI-COMMERCE-EF02-01", "AI-COMMERCE-EF02-02",
                 "AI-COMMERCE-HOSTOPS-01"]
        records = [
            {"name": name, "task_state": "Running", "task_interactive": True,
             "hidden_launcher_configured": True,
             "hidden_launcher_process_count": 1,
             "listener_process_count": 1, "listener_session_ids": [1]}
            for name in names
        ]
        request = hostops.parse_event(event("read", "runner_runtime_inventory", {}), "read")
        samples = [
            {"all_listener_count": 3, "unmatched_listener_count": 0,
             "runners": records[:-1]},
            {"all_listener_count": 3, "unmatched_listener_count": 0,
             "runners": [{**records[0], "command_line": "secret"}, *records[1:]]},
            {"all_listener_count": 3, "unmatched_listener_count": 0,
             "runners": [{**records[0], "name": "OTHER-RUNNER"}, *records[1:]]},
            {"all_listener_count": 2, "unmatched_listener_count": 0,
             "runners": records},
        ]
        with patch.object(hostops.os, "name", "nt"), patch.object(
            hostops.subprocess, "run"
        ) as fake:
            fake.return_value.returncode = 0
            for payload in samples:
                fake.return_value.stdout = json.dumps(payload)
                with self.assertRaises(hostops.Denied):
                    hostops.perform(request)
            fake.return_value.returncode = 1
            fake.return_value.stdout = ""
            with self.assertRaisesRegex(hostops.Denied, "RUNNER_RUNTIME_READ_FAILED"):
                hostops.perform(request)

    def test_cdp_9333_status_owner_only_bounded_read(self):
        request = hostops.parse_event(event("read", "cdp_9333_status", {}), "read")
        self.assertEqual(request["operation"], "cdp_9333_status")
        for params in ({"command": "Get-Process"},
                       {"port": 9222},
                       {"chat_url": "https://chatgpt.com/c/private"}):
            with self.subTest(params=params):
                with self.assertRaises(hostops.Denied):
                    hostops.parse_event(event("read", "cdp_9333_status", params), "read")
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(event("write", "cdp_9333_status", {}), "write")
        other = event("read", "cdp_9333_status", {})
        other["sender"]["login"] = "untrusted"
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(other, "read")

    def test_cdp_9333_status_absent_never_accesses_http(self):
        req = hostops.parse_event(event("read", "cdp_9333_status", {}), "read")
        record = {"listener_count": 0, "loopback_count": 0,
                  "unexpected_binding_count": 0,
                  "chrome_owned_loopback_count": 0,
                  "same_session_chrome_count": 0}
        with patch.object(hostops.os, "name", "nt"), patch.object(
            hostops.subprocess, "run"
        ) as native, patch.object(hostops, "build_opener") as opener:
            native.return_value.returncode = 0
            native.return_value.stdout = json.dumps(record)
            answer = hostops.perform(req)
            opener.assert_not_called()
            self.assertEqual(native.call_args.args[0][:4],
                             ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"])
            self.assertEqual(native.call_args.kwargs["timeout"], 18)
            self.assertNotIn("shell", native.call_args.kwargs)
        self.assertEqual(answer["status"], "OBSERVED")
        self.assertEqual(answer["cdp_port"], 9333)
        self.assertFalse(answer["infrastructure_ready"])
        self.assertFalse(answer["devtools_protocol_ok"])
        self.assertFalse(answer["wake_sent"])
        self.assertFalse(answer["exact_chat_verified"])

    def test_cdp_9333_status_exact_local_chrome_and_devtools(self):
        req = hostops.parse_event(event("read", "cdp_9333_status", {}), "read")
        record = {"listener_count": 1, "loopback_count": 1,
                  "unexpected_binding_count": 0,
                  "chrome_owned_loopback_count": 1,
                  "same_session_chrome_count": 1}
        with patch.object(hostops.os, "name", "nt"), patch.object(
            hostops.subprocess, "run"
        ) as native, patch.object(hostops, "build_opener") as opener:
            native.return_value.returncode = 0
            native.return_value.stdout = json.dumps(record)
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = json.dumps({
                "Browser": "Chrome/153.0.0.0", "Protocol-Version": "1.3",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/PRIVATE",
            }).encode()
            answer = hostops.perform(req)
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 2)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:9333/json/version")
        self.assertTrue(answer["infrastructure_ready"])
        self.assertTrue(answer["devtools_protocol_ok"])
        self.assertFalse(answer["wake_sent"])
        self.assertFalse(answer["exact_chat_verified"])
        self.assertNotIn("PRIVATE", json.dumps(answer))
        self.assertNotIn("webSocketDebuggerUrl", json.dumps(answer))

    def test_cdp_9333_status_rejects_bad_native_evidence(self):
        req = hostops.parse_event(event("read", "cdp_9333_status", {}), "read")
        base = {"listener_count": 1, "loopback_count": 1,
                "unexpected_binding_count": 0,
                "chrome_owned_loopback_count": 1,
                "same_session_chrome_count": 1}
        invalid = [
            {**base, "chrome_owned_loopback_count": 2},
            {**base, "same_session_chrome_count": 2},
            {**base, "unexpected_binding_count": 1},
            {**base, "listener_count": True},
            {**base, "CommandLine": "secret"},
        ]
        with patch.object(hostops.os, "name", "nt"), patch.object(
            hostops.subprocess, "run"
        ) as native, patch.object(hostops, "build_opener") as opener:
            native.return_value.returncode = 0
            for obj in invalid:
                native.return_value.stdout = json.dumps(obj)
                with self.assertRaises(hostops.Denied):
                    hostops.perform(req)
            opener.assert_not_called()
            native.return_value.returncode = 1
            with self.assertRaisesRegex(hostops.Denied, "CDP_NATIVE_READ_FAILED"):
                hostops.perform(req)

    def test_no_read_approval_comment_required(self):
        value = event("read", "inventory", {})
        self.assertNotIn("comment", value)
        self.assertEqual(hostops.parse_event(value, "read")["issue_number"], 17)

    def test_non_owner_and_public_fail_closed(self):
        value = event("read", "inventory", {})
        value["sender"]["login"] = "attacker"
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(value, "read")
        value = event("read", "inventory", {})
        value["repository"]["private"] = True
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(value, "read")

    def test_arbitrary_commands_and_write_on_read_are_denied(self):
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(event("read", "powershell", {"command": "dir"}), "read")
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(event("read", "read_text", {
                "root_id": "workspace", "relative_path": ".env"
            }), "read")
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(event("read", "create_text", {
                "root_id": "scratch", "relative_path": "hostops-personal-x.txt", "content_utf8": "x"
            }), "read")

    def test_write_requires_exact_owner_comment(self):
        params = {"root_id": "scratch", "relative_path": "hostops-personal-proof.txt", "content_utf8": "OK"}
        value = event("write", "create_text", params)
        self.assertEqual(hostops.parse_event(value, "write")["operation"], "create_text")
        for text in ("OK", "APPROVE WRITE #18", "APPROVE WRITE #17\n", "APPROVE WRITE #17 extra"):
            with self.subTest(text=text):
                with self.assertRaises(hostops.Denied):
                    hostops.parse_event(event("write", "create_text", params, comment=text), "write")
        value["comment"]["user"]["login"] = "attacker"
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(value, "write")

    def test_no_overwrite_delete_or_unsafe_path(self):
        for filename in ("../secret.txt", "/tmp/test.txt", "AUX.txt", "a\\b.txt",
                         "sub/file.txt", "hostops-personal-proof.py", "hostops-personal-test.txt:ads"):
            with self.subTest(filename=filename):
                with self.assertRaises(hostops.Denied):
                    hostops.parse_event(event("write", "create_text", {
                        "root_id": "scratch", "relative_path": filename, "content_utf8": "ok"
                    }), "write")
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(event("write", "delete", {"root_id": "scratch"}), "write")

    def test_duplicate_json_keys_and_oversized_data_denied(self):
        value = event("read", "inventory", {})
        value["issue"]["body"] = (
            '{"schema":"' + hostops.SCHEMA + '","operation":"inventory",'
            '"operation":"create_text","parameters":{}}'
        )
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(value, "read")
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(event("write", "create_text", {
                "root_id": "scratch", "relative_path": "hostops-personal-large.md",
                "content_utf8": "A" * (hostops.MAX_WRITE + 1),
            }), "write")

    def test_real_create_new_and_second_attempt_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = hostops.ROOTS["scratch"]
            hostops.ROOTS["scratch"] = Path(tmp)
            try:
                params = {"root_id": "scratch", "relative_path": "hostops-personal-probe.txt",
                          "content_utf8": "public safe proof"}
                request = hostops.parse_event(event("write", "create_text", params), "write")
                result = hostops.perform(request)
                self.assertEqual(result["status"], "OBSERVED")
                self.assertEqual((Path(tmp) / "hostops-personal-probe.txt").read_text(), "public safe proof")
                with self.assertRaisesRegex(hostops.Denied, "TARGET_ALREADY_EXISTS_NO_RETRY"):
                    hostops.perform(request)
            finally:
                hostops.ROOTS["scratch"] = old

    def test_bounded_workspace_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = hostops.ROOTS["workspace"]
            hostops.ROOTS["workspace"] = Path(tmp)
            try:
                (Path(tmp) / "docs").mkdir()
                (Path(tmp) / "docs" / "hello.md").write_text("hello world", encoding="utf-8")
                request = hostops.parse_event(event("read", "read_text", {
                    "root_id": "workspace", "relative_path": "docs/hello.md"
                }), "read")
                with patch.object(hostops.subprocess, "run") as git:
                    git.return_value.returncode = 0
                    result = hostops.perform(request)
                self.assertEqual(result["content_utf8"], "hello world")
                self.assertEqual(result["bytes"], 11)
            finally:
                hostops.ROOTS["workspace"] = old


    def test_extended_public_source_restrictions(self):
        for rel in (
            "src/ai_commerce/hostops_v3/personal.py",
            "tests/test_hostops_personal.py",
            ".github/workflows/issue-ingress.yml",
            "work_items/mt01-components/hostops-personal.json",
        ):
            with self.subTest(rel=rel):
                req = hostops.parse_event(event("read", "read_text", {
                    "root_id": "workspace", "relative_path": rel
                }), "read")
                self.assertEqual(req["operation"], "read_text")
        for rel in (".env", "secrets/data.json", ".github/private.env",
                    "src/ai_commerce/hidden.ps1", "../secrets.txt"):
            with self.subTest(rel=rel):
                with self.assertRaises(hostops.Denied):
                    hostops.parse_event(event("read", "read_text", {
                        "root_id": "workspace", "relative_path": rel
                    }), "read")

    def test_git_snapshot_does_not_return_untracked_paths(self):
        req = hostops.parse_event(event("read", "git_snapshot", {}), "read")
        with patch.object(hostops, "_git", side_effect=[
            "a" * 40 + "\n", "main\n", " M src/example.py\n"
        ]) as git:
            actual = hostops.perform(req)
        self.assertEqual(actual["git_head_sha"], "a" * 40)
        self.assertEqual(actual["tracked_changes"], 1)
        self.assertNotIn("src/example.py", json.dumps(actual))
        self.assertEqual(git.call_count, 3)
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(event("read", "git_snapshot", {"shell": "dir"}), "read")

    def test_tracked_paths_restricted_and_capped(self):
        req = hostops.parse_event(event("read", "tracked_paths", {
            "prefix": "src/ai_commerce/hostops_v3/"
        }), "read")
        with patch.object(hostops, "_git", return_value="\n".join(
                "src/ai_commerce/hostops_v3/f%03d.py" % i for i in range(65))):
            result = hostops.perform(req)
        self.assertEqual(result["total_tracked_in_scope"], 65)
        self.assertEqual(len(result["paths"]), 50)
        self.assertTrue(result["truncated"])
        for prefix in (".git/", "C:/", "secrets/", "src/ai_commerce/../", ".github/", "tests"):
            with self.subTest(prefix=prefix):
                with self.assertRaises(hostops.Denied):
                    hostops.parse_event(event("read", "tracked_paths", {
                        "prefix": prefix
                    }), "read")

    def test_digest_after_independent_read_and_workspace_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_scratch = hostops.ROOTS["scratch"]
            old_notes = hostops.ROOTS["workspace_notes"]
            hostops.ROOTS["scratch"] = Path(tmp)
            hostops.ROOTS["workspace_notes"] = Path(tmp)
            try:
                params = {"root_id": "workspace_notes", "relative_path": "hostops-personal-note.md",
                          "content_utf8": "approved notes"}
                write = hostops.parse_event(event("write", "create_text", params), "write")
                observed = hostops.perform(write)
                read = hostops.parse_event(event("read", "file_digest", {
                    "root_id": "workspace_notes", "relative_path": "hostops-personal-note.md"
                }), "read")
                receipt = hostops.perform(read)
                self.assertEqual(receipt["sha256"], observed["sha256"])
                self.assertEqual(receipt["bytes"], len("approved notes"))
                self.assertNotIn("content_utf8", receipt)
                self.assertFalse(observed["overwritten"])
                with self.assertRaisesRegex(hostops.Denied, "TARGET_ALREADY_EXISTS_NO_RETRY"):
                    hostops.perform(write)
            finally:
                hostops.ROOTS["scratch"] = old_scratch
                hostops.ROOTS["workspace_notes"] = old_notes
        for root in ("workspace", "private", "C:/"):
            with self.subTest(root=root):
                with self.assertRaises(hostops.Denied):
                    hostops.parse_event(event("read", "file_digest", {
                        "root_id": root, "relative_path": "hostops-personal-note.md"
                    }), "read")

    def test_public_dirty_worktree_file_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_root = hostops.ROOTS["workspace"]
            hostops.ROOTS["workspace"] = Path(tmp)
            try:
                (Path(tmp) / "docs").mkdir()
                (Path(tmp) / "docs" / "readme.md").write_text(
                    "maybe secret uncommitted edits", encoding="utf-8")
                req = hostops.parse_event(event("read", "read_text", {
                    "root_id": "workspace", "relative_path": "docs/readme.md"
                }), "read")
                with patch.object(hostops, "_git", side_effect=[
                    "", hostops.Denied("GIT_READ_FAILED")
                ]):
                    with self.assertRaises(hostops.Denied):
                        hostops.perform(req)
            finally:
                hostops.ROOTS["workspace"] = old_root


    def test_git_sync_requires_owner_approval_and_exact_pinned_commits(self):
        params = {"expected_local_sha": "a" * 40, "target_sha": "b" * 40}
        req = hostops.parse_event(event("write", "git_sync_main", params), "write")
        self.assertEqual(req["parameters"], params)
        for message in ("APPROVE WRITE #16", "APPROVE WRITE #17 ", "yes", ""):
            with self.subTest(message=message):
                with self.assertRaisesRegex(hostops.Denied, "EXACT_WRITE_APPROVAL_REQUIRED"):
                    hostops.parse_event(event("write", "git_sync_main", params,
                                            comment=message), "write")
        with self.assertRaisesRegex(hostops.Denied, "READ_OPERATION_DENIED"):
            hostops.parse_event(event("read", "git_sync_main", params), "read")
        for bad in (
            {"expected_local_sha": "a" * 39, "target_sha": "b" * 40},
            {"expected_local_sha": "a" * 40, "target_sha": "b" * 41},
            {"expected_local_sha": "a" * 40, "target_sha": "B" * 40},
            {"expected_local_sha": "a" * 40, "target_sha": "b" * 40, "cmd": "reset --hard"},
            {},
        ):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(hostops.Denied, "GIT_SYNC_PIN_REQUIRED"):
                    hostops.parse_event(event("write", "git_sync_main", bad), "write")
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(event("write", "git_reset", params), "write")

    def test_git_sync_never_fetches_on_wrong_branch_origin_or_dirty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_root = hostops.ROOTS["workspace"]
            hostops.ROOTS["workspace"] = root
            params = {"expected_local_sha": "a" * 40, "target_sha": "b" * 40}
            req = hostops.parse_event(event("write", "git_sync_main", params), "write")
            try:
                with patch.object(hostops, "_git", side_effect=[
                    str(root), "feature\n"
                ]), patch.object(hostops, "_git_write") as write:
                    with self.assertRaisesRegex(hostops.Denied, "GIT_SYNC_NOT_MAIN"):
                        hostops.perform(req)
                    write.assert_not_called()
                with patch.object(hostops, "_git", side_effect=[
                    str(root), "main\n", "https://untrusted.invalid/repo"
                ]), patch.object(hostops, "_git_write") as write:
                    with self.assertRaisesRegex(hostops.Denied, "GIT_SYNC_ORIGIN_DENIED"):
                        hostops.perform(req)
                    write.assert_not_called()
                with patch.object(hostops, "_git", side_effect=[
                    str(root), "main\n",
                    "https://github.com/trung-via/ai-commerce.git",
                    "a" * 40, "?? newfile.txt\n"
                ]), patch.object(hostops, "_git_write") as write:
                    with self.assertRaisesRegex(hostops.Denied, "GIT_SYNC_DIRTY_WORKTREE"):
                        hostops.perform(req)
                    write.assert_not_called()
                with patch.object(hostops, "_git", side_effect=[
                    str(root), "main\n",
                    "https://github.com/trung-via/ai-commerce.git",
                    "c" * 40
                ]), patch.object(hostops, "_git_write") as write:
                    with self.assertRaisesRegex(hostops.Denied, "GIT_SYNC_LOCAL_SHA_DRIFT"):
                        hostops.perform(req)
                    write.assert_not_called()
            finally:
                hostops.ROOTS["workspace"] = old_root

    @staticmethod
    def _run_git(*args):
        import subprocess
        cp = subprocess.run(["git", *map(str, args)], capture_output=True,
                            text=True, check=True, timeout=20)
        return cp.stdout.strip()

    def _new_local_git_fixture(self, root):
        bare = root / "origin.git"
        producer = root / "producer"
        local = root / "workspace"
        self._run_git("init", "--bare", "-b", "main", bare)
        self._run_git("clone", bare, producer)
        self._run_git("-C", producer, "config", "user.name", "HostOps test")
        self._run_git("-C", producer, "config", "user.email", "hostops-test@example.invalid")
        (producer / "README.md").write_text("v1\n", encoding="utf-8")
        self._run_git("-C", producer, "add", "README.md")
        self._run_git("-C", producer, "commit", "-m", "v1")
        self._run_git("-C", producer, "push", "origin", "main")
        self._run_git("clone", bare, local)
        local_before = self._run_git("-C", local, "rev-parse", "HEAD")
        (producer / "README.md").write_text("v2\n", encoding="utf-8")
        self._run_git("-C", producer, "commit", "-am", "v2")
        self._run_git("-C", producer, "push", "origin", "main")
        target = self._run_git("-C", producer, "rev-parse", "HEAD")
        return bare, producer, local, local_before, target

    def test_git_sync_real_offline_fast_forward_and_no_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare, _, local, before, target = self._new_local_git_fixture(Path(tmp))
            params = {"expected_local_sha": before, "target_sha": target}
            req = hostops.parse_event(event("write", "git_sync_main", params), "write")
            old_root = hostops.ROOTS["workspace"]
            hostops.ROOTS["workspace"] = local
            try:
                with patch.object(hostops, "TRUSTED_ORIGIN_URLS",
                                  frozenset([str(bare)])):
                    result = hostops.perform(req)
                    self.assertEqual(result["git_before_sha"], before)
                    self.assertEqual(result["git_after_sha"], target)
                    self.assertEqual(result["sync_result"], "FAST_FORWARDED")
                    self.assertTrue(result["fast_forward_only"])
                    self.assertEqual(
                        self._run_git("-C", local, "rev-parse", "HEAD"), target)
                    self.assertEqual((local / "README.md").read_text(), "v2\n")
                    with self.assertRaisesRegex(hostops.Denied, "GIT_SYNC_LOCAL_SHA_DRIFT"):
                        hostops.perform(req)
            finally:
                hostops.ROOTS["workspace"] = old_root

    def test_git_sync_target_drift_does_not_change_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare, _, local, before, target = self._new_local_git_fixture(Path(tmp))
            req = hostops.parse_event(event("write", "git_sync_main", {
                "expected_local_sha": before, "target_sha": "a" * 40
            }), "write")
            old = hostops.ROOTS["workspace"]
            hostops.ROOTS["workspace"] = local
            try:
                with patch.object(hostops, "TRUSTED_ORIGIN_URLS",
                                  frozenset([str(bare)])):
                    with self.assertRaisesRegex(hostops.Denied, "GIT_SYNC_TARGET_MOVED"):
                        hostops.perform(req)
                self.assertEqual(self._run_git("-C", local, "rev-parse", "HEAD"), before)
            finally:
                hostops.ROOTS["workspace"] = old

    def test_git_sync_real_divergence_is_denied_without_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare, _, local, _, target = self._new_local_git_fixture(Path(tmp))
            self._run_git("-C", local, "config", "user.name", "HostOps test")
            self._run_git("-C", local, "config", "user.email",
                          "hostops-test@example.invalid")
            (local / "local-note.txt").write_text("local commit\n", encoding="utf-8")
            self._run_git("-C", local, "add", "local-note.txt")
            self._run_git("-C", local, "commit", "-m", "local diverged")
            before = self._run_git("-C", local, "rev-parse", "HEAD")
            req = hostops.parse_event(event("write", "git_sync_main", {
                "expected_local_sha": before, "target_sha": target
            }), "write")
            old = hostops.ROOTS["workspace"]
            hostops.ROOTS["workspace"] = local
            try:
                with patch.object(hostops, "TRUSTED_ORIGIN_URLS",
                                  frozenset([str(bare)])):
                    with self.assertRaisesRegex(hostops.Denied, "GIT_SYNC_NON_FAST_FORWARD"):
                        hostops.perform(req)
                self.assertEqual(self._run_git("-C", local, "rev-parse", "HEAD"), before)
                self.assertEqual((local / "local-note.txt").read_text(), "local commit\n")
            finally:
                hostops.ROOTS["workspace"] = old




    def test_d5_carrier_is_only_signed_closed_ingress_and_off_by_default(self):
        """D5 workflow source has no unsigned, generic shell or automatic Wake route."""
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        assert workflow.count("  hostops-d5-carrier:") == 1
        d5 = workflow.split("  hostops-d5-carrier:", 1)[1]
        assert "vars.HOSTOPS_D5_PROTECTED_ENABLE == 'REVIEWED'" in d5
        assert "github.actor == github.repository_owner" in d5
        assert "github.event_name == 'issue_comment'" in d5
        assert "startsWith(github.event.issue.title, '[HOSTOPS D5] ')" in d5
        assert "runs-on: [self-hosted, Windows, ai-commerce-hostops]" in d5
        assert "persist-credentials: false" in d5
        assert "python -B -m ai_commerce.hostops_v3.d5_control --prepare-event" in d5
        assert "python -B -m ai_commerce.hostops_v3.d5_control --request" in d5
        assert "AI_COMMERCE_D5_RESULT_V1" in d5
        assert "wake_sent" in d5 and "signed.issue_number !== context.issue.number" in d5
        assert "START_OR_REUSE_D5_CHROME" in d5  # enum only, not a shell command
        assert "--serve" not in d5  # never start the private operator under Actions
        assert "script: |" in d5
        assert "Invoke-Expression" not in d5 and "iex " not in d5
        assert "permissions:\n      contents: read\n      issues: write" in d5

    def test_personal_d5_bootstrap_is_single_fixed_owner_only_chrome_operation(self):
        """Personal fallback remains Chrome-only, with no signed-bridge bypass for other operations."""
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        assert workflow.count("  hostops-d5-personal-bootstrap:") == 1
        boot = workflow.split("  hostops-d5-personal-bootstrap:", 1)[1]
        assert "github.repository == 'trung-via/ai-commerce'" in boot
        assert "github.actor == github.repository_owner" in boot
        assert "github.event_name == 'issues' && github.event.action == 'opened'" in boot
        assert "github.event.issue.user.login == github.repository_owner" in boot
        assert "startsWith(github.event.issue.title, '[HOSTOPS D5 BOOT] ')" in boot
        assert '{"schema":"ai-commerce.d5.personal.v1","operation":"START_CDP_9333","parameters":{}}' in boot
        assert '{"schema":"ai-commerce.d5.personal.v1","operation":"PROBE_CDP_9333","parameters":{}}' in boot
        assert "ai_commerce.remote.chrome_acquisition probe --config" in boot
        assert "if ($probeOnly)" in boot
        assert "CDP_9333_NOT_RUNNING" in boot
        assert "parsed.launched === false" in boot
        assert "runs-on: [self-hosted, Windows, ai-commerce-hostops]" in boot
        assert "persist-credentials: false" in boot
        assert "github.rest.issues.createComment" in boot
        assert "AI_COMMERCE_D5_PERSONAL_BOOT_RESULT_V1" in boot
        assert "source_sha:context.sha" in boot
        assert "cdp_verified=$verified" in boot
        assert "wake_sent=$false" in boot and "wake_sent !== false" in boot
        assert "CHROME_ACQUISITION_BLOCKED" in boot
        assert "ai_commerce.remote.chrome_acquisition launch --config" in boot
        assert "--checkout $root --opt-in" in boot
        assert "$root = $env:GITHUB_WORKSPACE" in boot
        assert "CONFIG_AMBIGUOUS_OR_ABSENT" in boot
        assert "SOURCE_DRIFT" in boot and "NO_INTERACTIVE_SESSION" in boot
        assert "Get-NetTCPConnection" not in boot  # delegated to source-owned native guard
        assert "Invoke-Expression" not in boot and "Start-Process" not in boot
        assert "Remove-Item" not in boot and "Stop-Process" not in boot
        assert "wake-readiness" not in boot and "SEND_WAKE" not in boot
        assert "Register-ScheduledTask" not in boot
        assert "contents: read" in boot and "issues: write" in boot
        assert "python -m pip install" not in boot and "curl " not in boot



    def test_d5_port_compare_is_bounded_loopback_only(self):
        """Bind-check 9333 versus 9444 without relaunch or removing any Chrome profile."""
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        assert workflow.count("  hostops-d5-compare-loopback:") == 1
        job = workflow.split("  hostops-d5-compare-loopback:", 1)[1].split(
            "  hostops-d5-isolated-9444-trial:", 1)[0]
        assert "github.actor == github.repository_owner" in job
        assert "github.event.issue.user.login == github.repository_owner" in job
        assert "github.event_name == 'issues' && github.event.action == 'opened'" in job
        assert "startsWith(github.event.issue.title, '[HOSTOPS D5 PORT TEST] ')" in job
        assert '{"schema":"ai-commerce.d5.port-test.v1","operation":"BIND_LOOPBACK_9333_9444","parameters":{}}' in job
        assert "runs-on: [self-hosted, Windows, ai-commerce-hostops]" in job
        assert "persist-credentials: false" in job
        assert "Test-ExactLoopbackBind 9333" in job
        assert "Test-ExactLoopbackBind 9444" in job
        assert "127.0.0.1" in job
        assert "ExclusiveAddressUse = $true" in job
        assert "$listener.Start(1)" in job
        assert "$listener.Stop()" in job
        assert "PORT_IN_USE" in job and "ACCESS_DENIED" in job
        assert "AI_COMMERCE_D5_PORT_COMPARE_V1" in job
        assert "value.source_sha !== context.sha" in job
        assert "chrome_touched:false,wake_sent:false" in job
        assert "Get-NetTCPConnection" not in job
        assert "Start-Process" not in job and "Stop-Process" not in job
        assert "Remove-Item" not in job and "Register-ScheduledTask" not in job
        assert "chrome_acquisition launch" not in job and "Invoke-Expression" not in job
        assert "SEND_WAKE" not in job



    def test_d5_isolated_9444_uses_new_profile_fixed_input_and_never_touches_old_chrome(self):
        """An independent empty 9444 user-data-dir must not reuse D5's occupied 9333 profile."""
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        assert workflow.count("  hostops-d5-isolated-9444-trial:") == 1
        job = workflow.split("  hostops-d5-isolated-9444-trial:", 1)[1].split(
            "  hostops-d5-9444-cdp-protocol:", 1)[0]
        assert "github.actor == github.repository_owner" in job
        assert "github.event.issue.user.login == github.repository_owner" in job
        assert "github.event_name == 'issues' && github.event.action == 'opened'" in job
        assert '{"schema":"ai-commerce.d5.isolated9444.v1","operation":"START_FRESH_9444","parameters":{}}' in job
        assert '{"schema":"ai-commerce.d5.isolated9444.v1","operation":"PROBE_FRESH_9444","parameters":{}}' in job
        assert "D5-Isolated-9444-Trial" in job
        assert "Join-Path $trial 'UserData'" in job
        assert "Join-Path $data 'Default'" in job
        assert "Join-Path $trial 'config.json'" in job
        assert "Test-Path -LiteralPath $trial" in job
        assert "TRIAL_ALREADY_EXISTS_HOLD" in job
        assert "ExclusiveAddressUse = $true" in job
        assert "127.0.0.1" in job and "9444" in job
        assert "Set-Acl -LiteralPath $profile -AclObject $dirAcl" in job
        assert "Set-Acl -LiteralPath $cfg -AclObject $configAcl" in job
        assert "[IO.FileMode]::CreateNew" in job
        assert "ai_commerce.remote.chrome_acquisition launch --config $cfg" in job
        assert "ai_commerce.remote.chrome_acquisition probe --config $cfg" in job
        assert "AI_COMMERCE_D5_ISOLATED_9444_V1" in job
        assert "r.source_sha !== context.sha" in job
        assert "r.wake_sent !== false" in job
        assert "lock_marker_present" in job and "profile_separate" in job
        assert "persist-credentials: false" in job
        assert "Stop-Process" not in job and "Remove-Item" not in job
        assert "Register-ScheduledTask" not in job and "Invoke-Expression" not in job
        assert "Copy-Item" not in job and "SEND_WAKE" not in job
        assert "User Data\\Default" not in job



    def test_d5_9444_http_only_checks_devtools_version_without_process_or_profile_access(self):
        """Bypass failing Windows CIM to prove local CDP protocol directly, read-only."""
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        assert workflow.count("  hostops-d5-9444-cdp-protocol:") == 1
        import re
        # Scope negative checks to this job, not unrelated later HostOps jobs.
        job = workflow.split("  hostops-d5-9444-cdp-protocol:", 1)[1]
        job = re.split(r"(?m)^  [a-z][a-z0-9-]*:\s*$", job, maxsplit=1)[0]
        assert "github.actor == github.repository_owner" in job
        assert "github.event.issue.user.login == github.repository_owner" in job
        assert "github.event_name == 'issues' && github.event.action == 'opened'" in job
        assert "PROBE_LOCAL_CDP_9444" in job
        assert "runs-on: [self-hosted, Windows, ai-commerce-hostops]" in job
        assert "persist-credentials: false" in job
        assert 'endpoint="http://127.0.0.1:9444", port=9444' in job
        assert "_probe_cdp(SimpleNamespace" in job
        assert "$script = @(" in job
        assert "$script = @'" not in job
        # Critical YAML regression: no unindented Python heredoc may escape the
        # PowerShell run: | block and disable the whole protected issue router.
        for line in workflow.splitlines():
            assert not line.startswith((
                "from types import", "from ai_commerce.", "except AcquisitionBlocked:",
                "try:", "'@",
            )), line
        assert "CDP_PROTOCOL_VERIFIED" in job
        assert "CDP_PROTOCOL_UNAVAILABLE" in job
        assert "AI_COMMERCE_D5_9444_HTTP_PROTOCOL_V1" in job
        assert "r.source_sha !== context.sha" in job
        assert "r.chrome_touched !== false || r.wake_sent !== false" in job
        assert "Get-NetTCPConnection" not in job
        assert "Get-CimInstance" not in job
        assert "Start-Process" not in job and "Stop-Process" not in job
        assert "Remove-Item" not in job and "Register-ScheduledTask" not in job
        assert "chrome_acquisition launch" not in job and "Invoke-Expression" not in job
        assert "SEND_WAKE" not in job and "Copy-Item" not in job



    def test_d5_9444_playwright_only_attaches_without_browser_or_chat_mutation(self):
        """Two connection-only Playwright clients preserve browser, profile, and user session."""
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        assert workflow.count("  hostops-d5-playwright-9444-read:") == 1
        job = workflow.split("  hostops-d5-playwright-9444-read:", 1)[1]
        assert "github.actor == github.repository_owner" in job
        assert "github.event.issue.user.login == github.repository_owner" in job
        assert "github.event_name == 'issues' && github.event.action == 'opened'" in job
        assert "ATTACH_READONLY_TWICE" in job
        assert '"schema":"ai-commerce.d5.playwright9444.v1"' in job
        assert "runs-on: [self-hosted, Windows, ai-commerce-hostops]" in job
        assert "persist-credentials: false" in job
        assert 'endpoint="http://127.0.0.1:9444", port=9444' in job
        assert "verify_playwright_attach(e)" in job
        assert "for i in range(2):" in job
        assert "PLAYWRIGHT_TWICE_VERIFIED" in job
        assert "PLAYWRIGHT_MODULE_UNAVAILABLE" in job
        assert "CDP_AFTER_ATTACH_UNAVAILABLE" in job
        assert "AI_COMMERCE_D5_PLAYWRIGHT_9444_READ_V1" in job
        assert "r.source_sha !== context.sha" in job
        assert "r.chat_verified !== false || r.browser_mutated !== false" in job
        assert "r.wake_sent !== false" in job
        assert "NO_INTERACTIVE_SESSION" in job and "ELEVATED" in job
        assert "$head -cne $env:GITHUB_SHA" in job
        assert "$script = @(" in job
        assert "browser.contexts" not in job.split("$script = @(", 1)[1].split(") -join", 1)[0]
        assert ".pages" not in job and ".cookies(" not in job
        assert "Get-CimInstance" not in job
        assert "Start-Process" not in job and "Stop-Process" not in job
        assert "Remove-Item" not in job and "Register-ScheduledTask" not in job
        assert "Invoke-Expression" not in job and "SEND_WAKE" not in job
        for line in workflow.splitlines():
            assert not line.startswith((
                "from ai_commerce.", "from types import", "except ImportError:",
                "try:", "for i in range(2):",
            )), line



class ExecutorHealthLocalTests(unittest.TestCase):
    """All CLI and trust results below are stubs, never live provider proof."""

    NOW = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.codex = str(Path(tempfile.gettempdir()) / "codex.exe")
        self.agy = str(Path(tempfile.gettempdir()) / "agy.exe")
        self.defaults = {"state": "VALIDATED", "preferred": {
            "executor": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"},
            "fallback": {"executor": "antigravity", "model": "gemini-3.8-flash", "effort": "high"},
            "recovery_probe_cooldown_seconds": 3600}
        for name, options in (
            ("platform", None), ("_executor_defaults", {"return_value": self.defaults}),
            ("_health_executable", {"side_effect": lambda executor: (
                "PRESENT", self.codex if executor == "codex" else self.agy)}),
        ):
            if name == "platform":
                patcher = patch.object(hostops.platform, "system", return_value="Windows")
            else:
                patcher = patch.object(hostops, name, **options)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            setattr(self, name + "_mock", mock)
        native_patcher = patch.object(hostops.subprocess, "run", side_effect=self.native_result)
        self.native = native_patcher.start()
        self.addCleanup(native_patcher.stop)

    def native_result(self, argv, **kwargs):
        if argv == [self.codex, "--version"]:
            return SimpleNamespace(returncode=0, stdout=b"codex-cli 0.1.2\n", stderr=b"")
        if argv == [self.codex, "login", "status"]:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"Logged in using ChatGPT\n")
        if argv == [self.agy, "--version"]:
            return SimpleNamespace(returncode=0, stdout=b"agy 1.2.3\n", stderr=b"")
        raise AssertionError("Unexpected provider argv")

    def collect(self, **kwargs):
        return hostops.collect_executor_health_local(now=self.NOW, **kwargs)

    def report(self, *, status="QUOTA_EXHAUSTED", age=30, executor="codex"):
        finished = (self.NOW - timedelta(seconds=age)).isoformat()
        return {"executor": executor, "model": "gpt-6.1-sol" if executor == "codex" else "gemini-3.8-flash",
                "reasoning_effort": "xhigh" if executor == "codex" else "high",
                "started_at": (self.NOW - timedelta(seconds=age + 10)).isoformat(), "status": status,
                "invocations": [{"status": status, "executor_invoked": True,
                    "executor_exit_code": 1 if status == "QUOTA_EXHAUSTED" else 0,
                    "executor_finished_at": finished, "provider_return_determinate": status != "QUOTA_EXHAUSTED"}]}

    def test_fixed_catalog_exact_argv_timeout_no_shell_or_input_and_no_inference(self):
        result = self.collect()
        self.assertEqual([call.args[0] for call in self.native.call_args_list], [
            [self.codex, "--version"], [self.codex, "login", "status"], [self.agy, "--version"]])
        for call in self.native.call_args_list:
            self.assertIs(call.kwargs["shell"], False)
            self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(call.kwargs["timeout"], 5)
            self.assertIs(call.kwargs["check"], False)
            self.assertIs(call.kwargs["capture_output"], True)
        self.assertEqual(result["executors"][0]["authentication"], "LOGIN_REPORTED")
        self.assertEqual(result["executors"][1]["login_probe"], "NOT_SUPPORTED")
        for row in result["executors"]:
            self.assertEqual(row["provider_identity"], "UNKNOWN")
            self.assertEqual(row["account"], "UNKNOWN")
            self.assertEqual(row["quota"], "UNKNOWN")
            self.assertIsNone(row["remaining"])
            self.assertIsNone(row["reset_at"])
        self.assertEqual(result["visibility"], "LOCAL_PRIVATE")
        self.assertEqual(result["capability"], "SOURCE_ONLY")
        self.assertEqual(result["context_identity"], "UNKNOWN")

    def test_absent_cli_and_non_windows_do_not_invoke_any_provider(self):
        self._health_executable_mock.return_value = ("ABSENT", None)
        self._health_executable_mock.side_effect = None
        result = self.collect()
        self.assertTrue(all(row["executable"] == "ABSENT" for row in result["executors"]))
        self.native.assert_not_called()
        self.platform_mock.return_value = "Linux"
        self._health_executable_mock.reset_mock()
        self._executor_defaults_mock.reset_mock()
        result = self.collect()
        self.assertIs(result["windows"], False)
        self.assertTrue(all(row["executable"] == "UNKNOWN" for row in result["executors"]))
        self.assertEqual(result["defaults"]["state"], "UNKNOWN")
        self.native.assert_not_called()
        self._health_executable_mock.assert_not_called()
        self._executor_defaults_mock.assert_not_called()

    def test_discovery_uses_only_fixed_path_names_and_rejects_shell_wrapper(self):
        # Exercise the real discovery helper hidden by the default test stub.
        with patch.object(hostops.shutil, "which", side_effect=[None, "private/codex.cmd"]) as which:
            state, executable = self._discovery_original("codex")
        self.assertEqual((state, executable), ("UNSUPPORTED_LAUNCHER", None))
        self.assertEqual([c.args for c in which.call_args_list], [("codex.exe",), ("codex.cmd",)])
        with patch.object(hostops.shutil, "which", return_value=self.codex), patch.object(
                hostops, "_fixed_private_metadata_kind", return_value="PRESENT"):
            self.assertEqual(self._discovery_original("codex"), ("PRESENT", self.codex))
        for value in ("relative/codex.exe", str(Path(self.codex).with_name("cmd.exe"))):
            with patch.object(hostops.shutil, "which", return_value=value):
                self.assertEqual(self._discovery_original("codex"), ("UNAVAILABLE", None))
        self.native.assert_not_called()

    _discovery_original = staticmethod(hostops._health_executable)
    _defaults_original = staticmethod(hostops._executor_defaults)

    def test_generic_probe_and_executable_are_denied_before_subprocess(self):
        for probe, exe in (("/usage", self.agy), ("codex_exec", self.codex),
                           ("codex_version", str(Path(self.codex).with_name("cmd.exe"))),
                           ("codex_login", "codex.exe")):
            with self.subTest(probe=probe), self.assertRaisesRegex(hostops.Denied, "PROBE_DENIED"):
                hostops._health_probe(probe, exe)
        self.native.assert_not_called()

    def test_raw_stdout_stderr_and_account_secrets_never_escape(self):
        for stdout, stderr in ((b"Logged in using ChatGPT\nprivate@example.invalid", b""),
                               (b"", b"Logged in using an API key - PRIVATE_SECRET"),
                               (b"Logged in using ChatGPT", b"PRIVATE_SECRET"),
                               (b"\xffPRIVATE_SECRET", b""), (b"x" * 8193, b""),
                               (b'{"quota_percent":99,"reset":"today"}', b"")):
            with self.subTest(length=len(stdout)):
                def native(argv, **kw):
                    return (SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)
                            if argv[1:] == ["login", "status"] else self.native_result(argv, **kw))
                self.native.side_effect = native
                result = self.collect()
                row = result["executors"][0]
                self.assertEqual(row["login_probe"], "MALFORMED")
                self.assertEqual(row["authentication"], "UNKNOWN")
                self.assertNotIn("PRIVATE_SECRET", json.dumps(result))
                self.assertNotIn("private@example", json.dumps(result))
                self.assertNotIn("quota_percent", json.dumps(result))

    def test_failed_version_never_attempts_login_or_reuses_unverified_version(self):
        for response in (SimpleNamespace(returncode=1, stdout=b"codex-cli 0.1.2", stderr=b""),
                         SimpleNamespace(returncode=0, stdout=b"PRIVATE_SECRET", stderr=b""),
                         SimpleNamespace(returncode=0, stdout=b"codex-cli 0.1.2-PRIVATE_SECRET", stderr=b""),
                         SimpleNamespace(returncode=False, stdout=b"codex-cli 0.1.2", stderr=b""),
                         SimpleNamespace(returncode=0, stdout="codex-cli 0.1.2", stderr=b"")):
            self.native.reset_mock()
            self.native.side_effect = lambda argv, **kw: response if argv[0] == self.codex else self.native_result(argv, **kw)
            row = self.collect()["executors"][0]
            self.assertEqual(row["version_state"], "MALFORMED")
            self.assertIsNone(row["version"])
            self.assertEqual(row["login_probe"], "UNKNOWN")
            self.assertEqual(self.native.call_count, 2)

    def test_timeout_and_oserror_suppress_private_exception_text(self):
        for error, expected in ((subprocess.TimeoutExpired(["PRIVATE_SECRET"], 5, output=b"PRIVATE_SECRET"), "TIMEOUT"),
                                (OSError("PRIVATE_SECRET"), "UNAVAILABLE")):
            self.native.side_effect = error
            result = self.collect()
            self.assertTrue(all(row["version_state"] == expected for row in result["executors"]))
            self.assertNotIn("PRIVATE_SECRET", json.dumps(result))

    def test_not_logged_in_is_authentication_evidence_never_quota_failure(self):
        self.native.side_effect = lambda argv, **kw: (
            SimpleNamespace(returncode=1, stdout=b"", stderr=b"Not logged in\n")
            if argv[1:] == ["login", "status"] else self.native_result(argv, **kw))
        row = self.collect()["executors"][0]
        self.assertEqual(row["authentication"], "NOT_LOGGED_IN")
        self.assertEqual(row["quota"], "UNKNOWN")
        self.assertEqual(row["last_ef01"]["outcome"], "UNKNOWN")

    def test_trusted_historical_quota_and_success_never_measure_current_quota(self):
        for status, expected in (("QUOTA_EXHAUSTED", "QUOTA_EXHAUSTED"), ("UNPROVEN", "DETERMINATE_RETURN"),
                                 ("BUILD_VERIFIED_FOR_SCOPE", "DETERMINATE_RETURN")):
            raw = json.dumps(self.report(status=status)).encode()
            with patch.object(hostops, "_health_executable", return_value=("ABSENT", None)), patch(
                    "builtins.open", side_effect=AssertionError("No evidence file discovery")):
                verifier = unittest.mock.Mock(return_value=True)
                result = self.collect(ef01_report=raw, verify_ef01=verifier)
            verifier.assert_called_once_with(raw)
            row = result["executors"][0]
            self.assertEqual(row["last_ef01"]["outcome"], expected)
            self.assertEqual(row["last_ef01"]["state"], "FRESH")
            self.assertEqual(row["quota"], "UNKNOWN")
            self.assertIsNone(row["remaining"])
            self.assertIsNone(row["reset_at"])
            self.assertEqual(result["executors"][1]["last_ef01"]["state"], "UNKNOWN")

    def test_stale_future_untrusted_and_missing_evidence(self):
        raw = json.dumps(self.report(age=3601)).encode()
        self.assertEqual(self.collect(ef01_report=raw)["executors"][0]["last_ef01"]["state"], "UNTRUSTED")
        self.assertEqual(self.collect(ef01_report=raw, verify_ef01=lambda _: False)["executors"][0]["last_ef01"]["state"], "UNTRUSTED")
        row = self.collect(ef01_report=raw, verify_ef01=lambda _: True)["executors"][0]
        self.assertEqual(row["last_ef01"]["state"], "STALE")
        self.assertEqual(row["quota"], "UNKNOWN")
        for age, expected in ((3600, "FRESH"), (-1, "INVALID")):
            row = self.collect(ef01_report=json.dumps(self.report(age=age)).encode(), verify_ef01=lambda _: True)["executors"][0]
            self.assertEqual(row["last_ef01"]["state"], expected)
        self.assertEqual(self.collect()["executors"][0]["last_ef01"]["state"], "UNKNOWN")

    def test_auth_capacity_unknown_and_inconsistent_ef01_are_not_quota(self):
        for status in ("AUTHENTICATION_FAILED", "EXECUTOR_PERMISSION_DENIED", "EXECUTION_UNKNOWN", "MODEL_AT_CAPACITY"):
            report = self.report(status=status)
            row = self.collect(ef01_report=json.dumps(report).encode(), verify_ef01=lambda _: True)["executors"][0]
            self.assertEqual(row["last_ef01"]["outcome"], "UNKNOWN")
        report = self.report(status="QUOTA_EXHAUSTED")
        report["invocations"][0].update(status="UNPROVEN", executor_exit_code=0, provider_return_determinate=True)
        self.assertEqual(self.collect(ef01_report=json.dumps(report).encode(), verify_ef01=lambda _: True)["executors"][0]["last_ef01"]["outcome"], "UNKNOWN")

    def test_malformed_ef01_and_trust_verifier_fail_closed(self):
        bad = [b"[]", b"{}", b"\xff", b'{"executor":"codex","executor":"codex"}', b"x" * 131073]
        for field, value in (("executor_exit_code", True), ("executor_invoked", 1),
                             ("executor_finished_at", "2026-11-07T07:00:00")):
            report = self.report()
            report["invocations"][0][field] = value
            bad.append(json.dumps(report).encode())
        for raw in bad:
            row = self.collect(ef01_report=raw, verify_ef01=lambda _: True)["executors"][0]
            self.assertEqual(row["last_ef01"]["state"], "INVALID")
        def failed_verifier(raw):
            raise ValueError("PRIVATE_SECRET")
        result = self.collect(ef01_report=json.dumps(self.report()).encode(), verify_ef01=failed_verifier)
        self.assertEqual(result["executors"][0]["last_ef01"]["state"], "INVALID")
        self.assertNotIn("PRIVATE_SECRET", json.dumps(result))

    def test_antigravity_report_does_not_establish_codex_or_account_identity(self):
        raw = json.dumps(self.report(executor="antigravity")).encode()
        result = self.collect(ef01_report=raw, verify_ef01=lambda _: True)
        self.assertEqual(result["executors"][0]["last_ef01"]["state"], "UNKNOWN")
        self.assertEqual(result["executors"][1]["last_ef01"]["outcome"], "QUOTA_EXHAUSTED")
        self.assertEqual(result["executors"][1]["account"], "UNKNOWN")

    def test_policy_validation_matches_existing_defaults_without_mutating_state(self):
        from ai_commerce.engineering.model_defaults import _policy
        actual = self._defaults_original()
        primary, backup, seconds = _policy(MODULE.parents[3])
        self.assertEqual(actual["preferred"], {"executor": primary.executor, "model": primary.model, "effort": primary.effort})
        self.assertEqual(actual["fallback"], {"executor": backup.executor, "model": backup.model, "effort": backup.effort})
        self.assertEqual(actual["recovery_probe_cooldown_seconds"], seconds)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = root / "config/engineering/model-defaults.v1.json"
            policy.parent.mkdir(parents=True)
            valid = {"version": 1, "hard_default": False, "preferred": self.defaults["preferred"],
                     "fallback": self.defaults["fallback"], "recovery_probe_cooldown_seconds": 3600}
            raws = ["[]", json.dumps({**valid, "version": True}), json.dumps({**valid, "hard_default": True}),
                    json.dumps({**valid, "recovery_probe_cooldown_seconds": True}),
                    json.dumps({**valid, "preferred": self.defaults["fallback"]}),
                    json.dumps({**valid, "extra": "PRIVATE_SECRET"}),
                    json.dumps(valid).replace('"version": 1', '"version": 1, "version": 1'), "x" * 8193]
            with patch.object(hostops, "__file__", str(root / "src/ai_commerce/hostops_v3/personal.py")):
                for raw in [json.dumps(valid), *raws]:
                    policy.write_text(raw, encoding="utf-8")
                    before = policy.read_bytes()
                    result = self._defaults_original()
                    self.assertEqual(result["state"], "VALIDATED" if raw == json.dumps(valid) else "INVALID")
                    self.assertEqual(policy.read_bytes(), before)
                    self.assertFalse((root / ".local").exists())
                swapped = {**valid, "preferred": valid["fallback"], "fallback": valid["preferred"]}
                policy.write_text(json.dumps(swapped), encoding="utf-8")
                self.assertEqual(self._defaults_original()["state"], "VALIDATED")
                policy.unlink()
                self.assertEqual(self._defaults_original()["state"], "INVALID")
        self.native.assert_not_called()

    def test_strict_receipt_rejects_extra_raw_quota_identity_and_bad_types(self):
        valid = self.collect()
        reversed_defaults = copy.deepcopy(valid)
        reversed_defaults["defaults"]["preferred"], reversed_defaults["defaults"]["fallback"] = (
            reversed_defaults["defaults"]["fallback"], reversed_defaults["defaults"]["preferred"])
        self.assertEqual(hostops.validate_executor_health_local_receipt(
            reversed_defaults)["defaults"]["preferred"]["executor"], "antigravity")
        invalid = [None, [], {**valid, "stdout": "PRIVATE_SECRET"}, {**valid, "schema_version": "v2"},
                   {**valid, "windows": 1}, {**valid, "observed_at": "today"}, {**valid, "context_identity": "PRIVATE_SECRET"},
                   {**valid, "executors": list(reversed(valid["executors"]))}]
        for field, value in (("remaining", 99), ("reset_at", "tomorrow"), ("account", "PRIVATE_SECRET"),
                             ("provider_identity", "PRIVATE_SECRET"),
                             ("stdout", "PRIVATE_SECRET"), ("version", "PRIVATE_SECRET"),
                             ("executable", True), ("quota", "AVAILABLE")):
            receipt = copy.deepcopy(valid)
            receipt["executors"][0][field] = value
            invalid.append(receipt)
        for receipt in invalid:
            with self.subTest(receipt_type=type(receipt)), self.assertRaisesRegex(hostops.Denied, "INVALID_EXECUTOR_HEALTH_LOCAL_RECEIPT"):
                hostops.validate_executor_health_local_receipt(receipt)
        self.assertEqual(hostops.validate_executor_health_local_receipt(valid), valid)
        self.assertIsNot(hostops.validate_executor_health_local_receipt(valid)["executors"][0], valid["executors"][0])

    def test_no_live_operation_or_public_field_and_standing_read_is_unchanged(self):
        before = tuple(hostops.STANDING_READ_CATEGORIES)
        with patch.object(hostops, "collect_executor_health_local", side_effect=AssertionError("No live routing")):
            for op in ("executor_health", "executor_health_local", "collect_executor_health_local"):
                with self.assertRaisesRegex(hostops.Denied, "READ_OPERATION_DENIED"):
                    hostops.parse_event(event("read", op, {}), "read")
            request = hostops.parse_event(event("read", "standing_read_diagnostics", {}), "read")
            with patch.object(hostops, "_standing_read_snapshot", return_value=PersonalHostOpsContractTests._standing_snapshot()):
                result = hostops.perform(request)
        self.assertEqual(len(result["diagnostics"]), 16)
        self.assertEqual(tuple(row["category"] for row in result["diagnostics"]), before)
        self.assertFalse({"executors", "defaults", "last_ef01", "provider_identity", "account", "remaining", "reset_at"} & hostops.PERSONAL_PUBLIC_FIELDS)
        self.native.assert_not_called()

    def test_naive_or_invalid_clock_rejected_before_native_reads(self):
        for value in (datetime(2026, 11, 7), "2026-11-07"):
            with self.assertRaisesRegex(hostops.Denied, "EXECUTOR_HEALTH_TIME_INVALID"):
                hostops.collect_executor_health_local(now=value)
        self.native.assert_not_called()


class ExecutorHealthPrivateBridgeTests(unittest.TestCase):
    """Host-owned tests only; all authorization, clock and native results are stubs."""

    NOW = ExecutorHealthLocalTests.NOW
    setUp = ExecutorHealthLocalTests.setUp
    native_result = ExecutorHealthLocalTests.native_result

    def request(self, **changes):
        return {"schema_version": "ExecutorHealthPrivateRequest.v1",
                "operation": "executor_health_snapshot", "visibility": "LOCAL_PRIVATE",
                "origin_id": "a" * 64, "chat_id": "b" * 64, "nonce": "c" * 32,
                "sequence": 1, "issued_at": "2026-11-07T08:00:00Z", "ttl_seconds": 60,
                "max_age_seconds": 60, "providers": ["codex", "antigravity"],
                "requested_fields": ["preferred", "fallback", "executors"], **changes}

    @staticmethod
    def wire(value):
        return json.dumps(value).encode("utf-8")

    def bridge(self, **changes):
        options = {"origin_id": "a" * 64, "chat_id": "b" * 64,
                   "authorize": lambda _: True, "clock": lambda: self.NOW, **changes}
        return hostops.ExecutorHealthPrivateBridge(**options)

    def assert_no_collection(self, raw, code, *, bridge=None):
        with patch.object(hostops, "collect_executor_health_local") as collector:
            with self.assertRaisesRegex(hostops.Denied, code):
                (bridge if bridge is not None else self.bridge()).read(raw)
        collector.assert_not_called()
        self.native.assert_not_called()
        self._health_executable_mock.assert_not_called()
        self._executor_defaults_mock.assert_not_called()

    def test_valid_request_reuses_single_local_collector_and_three_fixed_slots(self):
        raw = self.wire(self.request())
        verifier = unittest.mock.Mock(return_value=True)
        with patch.object(hostops, "collect_executor_health_local",
                          wraps=hostops.collect_executor_health_local) as collector:
            result = self.bridge(authorize=verifier).read(raw)
        collector.assert_called_once_with(now=self.NOW)
        expected = json.dumps(self.request(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        verifier.assert_called_once_with(expected)
        self.assertEqual(result["request_sha256"], hostops.hashlib.sha256(expected).hexdigest())
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["code"], "LOCAL_OBSERVATION_ONLY")
        self.assertEqual(result["visibility"], "LOCAL_PRIVATE")
        self.assertEqual(result["capability"], "SOURCE_ONLY_NOT_DEPLOYED")
        self.assertEqual(result["freshness"], "FRESH")
        self.assertEqual(result["responded_at"], "2026-11-07T08:00:00Z")
        self.assertEqual(result["valid_until"], "2026-11-07T08:01:00Z")
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["preferred"], "CODEX_GPT_6_1_SOL_XHIGH")
        self.assertEqual(snapshot["fallback"], "ANTIGRAVITY_GEMINI_3_8_FLASH_HIGH")
        self.assertEqual([row["executor"] for row in snapshot["executors"]], ["codex", "antigravity"])
        self.assertEqual([c.args[0] for c in self.native.call_args_list], [
            [self.codex, "--version"], [self.codex, "login", "status"], [self.agy, "--version"]])
        for call in self.native.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 5)
            self.assertIs(call.kwargs["shell"], False)
            self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
        for row in snapshot["executors"]:
            for field in ("provider_identity", "account", "quota"):
                self.assertEqual(row[field], "UNKNOWN")
            for field in ("remaining", "reset_at", "percentage"):
                self.assertIsNone(row[field])
        self.assertEqual(snapshot["context_identity"], "UNKNOWN")
        self.assertEqual(hostops.validate_executor_health_private_response(result), result)
        self.assertIsNot(hostops.validate_executor_health_private_response(result)["snapshot"], snapshot)

    def test_closed_request_types_versions_and_selector_injection_before_reads(self):
        invalid = [None, [], {}, {**self.request(), "extra": "PRIVATE_SECRET"}]
        for field, values in (
            ("schema_version", ["ExecutorHealthPrivateRequest.v2", None]),
            ("operation", ["powershell", "codex exec", "executor_health_snapshot;whoami"]),
            ("visibility", ["PUBLIC", "REMOTE_PRIVATE"]),
            ("providers", [["codex"], ["antigravity", "codex"], ["codex", "codex"],
                           ["codex", "unknown"], ["C:/private/codex.exe", "antigravity"], "codex"]),
            ("requested_fields", [["account"], ["quota"], ["executors", "preferred", "fallback"],
                                  ["preferred", "fallback", "executors", "stdout"], []]),
            ("origin_id", ["https://chat.invalid/current", "../private", "A" * 64, "a" * 63, None]),
            ("chat_id", ["$(Get-Content secret)", "b" * 65, True]),
            ("nonce", ["../cache", "c" * 31, "c" * 33, "c" * 31 + "\n", 1]),
            ("sequence", [True, False, 0, -1, 1.0, "1", 2**53]),
            ("ttl_seconds", [True, 0, 61, 1.0, "60"]),
            ("max_age_seconds", [True, 0, 61, 1.0, "60"]),
            ("issued_at", ["2026-11-07T08:00:00", "2026-11-07T08:00:00+00:00",
                           "2026-11-07T15:00:00+07:00", "2026-11-31T08:00:00Z", None]),
        ):
            invalid.extend(self.request(**{field: value}) for value in values)
        for key in self.request():
            value = self.request()
            del value[key]
            invalid.append(value)
        for value in invalid:
            with self.subTest(value=value):
                self.assert_no_collection(self.wire(value), "PRIVATE_HEALTH_REQUEST_INVALID")
        for key in ("path", "shell", "command", "executable", "ef01_report", "verify_ef01", "authenticated"):
            self.assert_no_collection(self.wire({**self.request(), key: "PRIVATE_SECRET"}),
                                      "PRIVATE_HEALTH_REQUEST_INVALID")

    def test_duplicate_keys_malformed_oversized_nonbytes_before_reads(self):
        raw = self.wire(self.request())
        duplicate = raw.replace(b'"sequence": 1', b'"sequence": 1, "sequence": 2')
        self.assert_no_collection(duplicate, "DUPLICATE_JSON_KEY")
        for raw in (b"", b"\xff", b"{", b"x" * 4097, b"[" * 2000, "{}", bytearray(b"{}")):
            self.assert_no_collection(raw, "PRIVATE_HEALTH_REQUEST_INVALID")

    def test_stale_future_and_exact_expiry_rejected_before_authorization_or_reads(self):
        verifier = unittest.mock.Mock(return_value=True)
        for issued in ("2026-11-07T08:00:01Z", "2026-11-07T07:59:00Z", "2026-11-07T07:58:59Z",
                       "9999-12-31T23:59:59Z"):
            self.assert_no_collection(self.wire(self.request(issued_at=issued)),
                                      "PRIVATE_HEALTH_REQUEST_TIME_DENIED", bridge=self.bridge(authorize=verifier))
        verifier.assert_not_called()

    def test_missing_trust_and_false_or_exception_verifier_never_collect(self):
        def failed(_):
            raise RuntimeError("PRIVATE_SECRET")
        options = ({"origin_id": None}, {"chat_id": None}, {"authorize": None},
                   {"authorize": lambda _: False}, {"authorize": lambda _: 1},
                   {"authorize": lambda _: {"authenticated": True}}, {"authorize": failed})
        with patch.object(hostops, "collect_executor_health_local") as collector:
            for changes in options:
                result = self.bridge(**changes).read(self.wire(self.request()))
                self.assertEqual(result["status"], "UNAVAILABLE")
                self.assertIn(result["code"], ("TRUST_UNAVAILABLE", "AUTHORIZATION_UNPROVEN"))
                self.assertEqual(result["freshness"], "NOT_READY")
                self.assertIsNone(result["snapshot"])
                self.assertNotIn("PRIVATE_SECRET", json.dumps(result))
            result = hostops.ExecutorHealthPrivateBridge(clock=lambda: self.NOW).read(self.wire(self.request()))
            self.assertEqual(result["code"], "TRUST_UNAVAILABLE")
        collector.assert_not_called()
        self.native.assert_not_called()

    def test_exact_origin_and_current_chat_binding_before_authorization(self):
        verifier = unittest.mock.Mock(return_value=True)
        for changes in ({"origin_id": "d" * 64}, {"chat_id": "d" * 64}):
            self.assert_no_collection(self.wire(self.request(**changes)), "PRIVATE_HEALTH_ORIGIN_DENIED",
                                      bridge=self.bridge(authorize=verifier))
        verifier.assert_not_called()
        for changes in ({"origin_id": "../private"}, {"chat_id": True}):
            with self.assertRaisesRegex(hostops.Denied, "PRIVATE_HEALTH_ORIGIN_INVALID"):
                self.bridge(**changes)

    def test_replay_sequence_nonce_and_timestamp_order_before_any_collector(self):
        bridge = self.bridge()
        accepted = self.request(issued_at="2026-11-07T07:59:59Z", sequence=10)
        bridge.read(self.wire(accepted))
        self.native.reset_mock()
        self._health_executable_mock.reset_mock()
        self._executor_defaults_mock.reset_mock()
        for changes in ({}, {"sequence": 11}, {"nonce": "d" * 32},
                        {"sequence": 9, "nonce": "d" * 32},
                        {"sequence": 11, "nonce": "d" * 32, "issued_at": "2026-11-07T07:59:58Z"}):
            self.assert_no_collection(self.wire({**accepted, **changes}), "PRIVATE_HEALTH_REPLAY_DENIED", bridge=bridge)
        result = bridge.read(self.wire(self.request(sequence=11, nonce="d" * 32)))
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(self.native.call_count, 3)

    def test_failed_collector_consumes_nonce_and_suppresses_private_errors(self):
        bridge = self.bridge()
        with patch.object(hostops, "collect_executor_health_local", side_effect=OSError("PRIVATE_SECRET")) as collector:
            result = bridge.read(self.wire(self.request()))
            self.assertEqual(result["code"], "SOURCE_UNAVAILABLE")
            self.assertIsNone(result["snapshot"])
            self.assertNotIn("PRIVATE_SECRET", json.dumps(result))
            with self.assertRaisesRegex(hostops.Denied, "PRIVATE_HEALTH_REPLAY_DENIED"):
                bridge.read(self.wire(self.request()))
            self.assertEqual(collector.call_count, 1)
        self.native.assert_not_called()

    def test_replay_capacity_never_evicts_and_busy_never_collects(self):
        bridge = self.bridge()
        with patch.object(hostops, "_PRIVATE_HEALTH_REPLAY_LIMIT", 1):
            bridge.read(self.wire(self.request()))
            self.native.reset_mock()
            with patch.object(hostops, "collect_executor_health_local") as collector:
                result = bridge.read(self.wire(self.request(sequence=2, nonce="d" * 32)))
                self.assertEqual(result["code"], "REPLAY_CAPACITY")
                with self.assertRaisesRegex(hostops.Denied, "PRIVATE_HEALTH_REPLAY_DENIED"):
                    bridge.read(self.wire(self.request()))
                collector.assert_not_called()
        self.assertTrue(bridge._lock.acquire(blocking=False))
        try:
            with patch.object(hostops, "collect_executor_health_local") as collector:
                result = bridge.read(self.wire(self.request(sequence=2, nonce="d" * 32)))
                self.assertEqual(result["code"], "BUSY")
                collector.assert_not_called()
        finally:
            bridge._lock.release()
        self.native.assert_not_called()

    def test_expiry_during_authorization_rejected_before_collector(self):
        for admitted in (self.NOW + timedelta(seconds=60), self.NOW - timedelta(seconds=1)):
            clock = unittest.mock.Mock(side_effect=[self.NOW, admitted])
            self.assert_no_collection(self.wire(self.request()), "PRIVATE_HEALTH_REQUEST_TIME_DENIED",
                                      bridge=self.bridge(clock=clock))

    def test_collector_completion_freshness_and_clock_rollback(self):
        for changes, completion, expected in (
            ({"max_age_seconds": 5}, 5, "SNAPSHOT_NOT_READY"),
            ({"ttl_seconds": 5}, 5, "SNAPSHOT_NOT_READY"),
            ({}, -1, "SNAPSHOT_NOT_READY"),
            ({"max_age_seconds": 5}, 4, "LOCAL_OBSERVATION_ONLY"),
        ):
            with self.subTest(changes=changes, completion=completion):
                clock = unittest.mock.Mock(side_effect=[self.NOW, self.NOW, self.NOW + timedelta(seconds=completion)])
                result = self.bridge(clock=clock).read(self.wire(self.request(**changes)))
                self.assertEqual(result["code"], expected)
                if expected == "SNAPSHOT_NOT_READY":
                    self.assertEqual(result["status"], "UNKNOWN")
                    self.assertIsNone(result["snapshot"])
        clock = unittest.mock.Mock(return_value=self.NOW)
        bridge = self.bridge(clock=clock)
        bridge.read(self.wire(self.request(issued_at="2026-11-07T07:59:59Z")))
        self.native.reset_mock()
        self._health_executable_mock.reset_mock()
        self._executor_defaults_mock.reset_mock()
        clock.return_value = self.NOW - timedelta(seconds=1)
        self.assert_no_collection(self.wire(self.request(issued_at="2026-11-07T07:59:59Z", sequence=2, nonce="d" * 32)),
                                  "PRIVATE_HEALTH_REPLAY_DENIED", bridge=bridge)

    def test_invalid_clocks_never_collect(self):
        for now in (datetime(2026, 11, 7), "2026-11-07", self.NOW.astimezone(timezone(timedelta(hours=7)))):
            self.assert_no_collection(self.wire(self.request()), "PRIVATE_HEALTH_TIME_INVALID",
                                      bridge=self.bridge(clock=lambda: now))

    def test_non_windows_absent_source_and_unrecognized_policy_are_typed(self):
        self.platform_mock.return_value = "Linux"
        result = self.bridge().read(self.wire(self.request()))
        self.assertEqual(result["code"], "SOURCE_UNAVAILABLE")
        self.assertIsNone(result["snapshot"])
        self.native.assert_not_called()
        self.platform_mock.return_value = "Windows"
        self.defaults["preferred"]["model"] = "private-model-name"
        self.defaults["fallback"]["effort"] = "private-effort"
        result = self.bridge().read(self.wire(self.request()))
        self.assertEqual(result["snapshot"]["preferred"], "UNKNOWN")
        self.assertEqual(result["snapshot"]["fallback"], "UNKNOWN")
        self.assertNotIn("private-model-name", json.dumps(result))
        self.assertNotIn("private-effort", json.dumps(result))

    def test_malformed_stale_future_and_private_collector_values_never_escape(self):
        local = hostops.collect_executor_health_local(now=self.NOW)
        invalid = [None, {**local, "stdout": "PRIVATE_SECRET"},
                   {**local, "observed_at": "2026-11-07T07:59:59Z"},
                   {**local, "observed_at": "2026-11-07T08:00:01Z"},
                   {**local, "observed_at": "9999-12-31T23:59:59Z"}]
        for field, value in (("account", "PRIVATE_SECRET"), ("remaining", 99), ("percentage", 99)):
            receipt = copy.deepcopy(local)
            receipt["executors"][0][field] = value
            invalid.append(receipt)
        for receipt in invalid:
            with patch.object(hostops, "collect_executor_health_local", return_value=receipt):
                result = self.bridge().read(self.wire(self.request()))
            self.assertIn(result["code"], ("SOURCE_UNAVAILABLE", "SNAPSHOT_NOT_READY"))
            self.assertIsNone(result["snapshot"])
            self.assertNotIn("PRIVATE_SECRET", json.dumps(result))

    def test_response_schema_rejects_fields_quota_identity_time_and_status_forgery(self):
        valid = self.bridge().read(self.wire(self.request()))
        invalid = [None, {}, {**valid, "stdout": "PRIVATE_SECRET"}]
        for field, value in (("schema_version", "v2"), ("capability", "DEPLOYED"),
                             ("visibility", "PUBLIC"), ("request_sha256", "bad"),
                             ("status", "READY"), ("code", "LIVE_QUOTA"), ("freshness", "NOT_READY"),
                             ("responded_at", "2026-11-07T07:59:59Z"),
                             ("responded_at", "2026-11-07T08:01:00Z"),
                             ("valid_until", "2026-11-07T08:01:01Z"), ("max_age_seconds", True)):
            invalid.append({**valid, field: value})
        for field, value in (("preferred", "PRIVATE_SECRET"), ("fallback", "codex"),
                             ("context_identity", "PRIVATE_SECRET"), ("windows", 1)):
            receipt = copy.deepcopy(valid)
            receipt["snapshot"][field] = value
            invalid.append(receipt)
        for field, value in (("percentage", 1), ("quota", "AVAILABLE"), ("remaining", 1),
                             ("reset_at", "tomorrow"), ("account", "PRIVATE_SECRET"),
                             ("provider_identity", "PRIVATE_SECRET"), ("stdout", "PRIVATE_SECRET")):
            receipt = copy.deepcopy(valid)
            receipt["snapshot"]["executors"][0][field] = value
            invalid.append(receipt)
        for receipt in invalid:
            with self.subTest(receipt=receipt), self.assertRaisesRegex(hostops.Denied, "PRIVATE_HEALTH_RESPONSE_INVALID"):
                hostops.validate_executor_health_private_response(receipt)
        unavailable = self.bridge(authorize=None).read(self.wire(self.request()))
        with self.assertRaisesRegex(hostops.Denied, "PRIVATE_HEALTH_RESPONSE_INVALID"):
            hostops.validate_executor_health_private_response({**unavailable, "snapshot": valid["snapshot"]})

    def test_public_issue_deny_path_and_both_public_contracts_stay_closed(self):
        from ai_commerce.hostops_v3 import contract
        categories = ("chrome_profile", "chrome_config", "chrome_markers", "cdp_9333_listener",
                      "chrome_process", "chrome_session", "cdp_protocol", "ef02_01_task", "ef02_01_listener",
                      "ef02_02_task", "ef02_02_listener", "hostops_01_task", "hostops_01_listener",
                      "repo_git", "python_runtime", "d5_metadata")
        self.assertEqual(tuple(hostops.STANDING_READ_CATEGORIES), categories)
        with patch.object(hostops, "collect_executor_health_local") as collector:
            for operation in ("executor_health_snapshot", "executor_health_private", "executor_health",
                              "ExecutorHealthPrivateBridge", "collect_executor_health_local"):
                with self.assertRaisesRegex(hostops.Denied, "READ_OPERATION_DENIED"):
                    hostops.parse_event(event("read", operation, {}), "read")
                with self.assertRaisesRegex(hostops.Denied, "OPERATION_DENIED"):
                    hostops.perform({"operation": operation, "parameters": {}})
            request = hostops.parse_event(event("read", "standing_read_diagnostics", {}), "read")
            with patch.object(hostops, "_standing_read_snapshot", return_value=PersonalHostOpsContractTests._standing_snapshot()):
                result = hostops.perform(request)
            self.assertEqual(tuple(row["category"] for row in result["diagnostics"]), categories)
        collector.assert_not_called()
        self.native.assert_not_called()
        private_fields = {"snapshot", "executors", "preferred", "fallback", "account", "quota", "remaining",
                          "reset_at", "percentage", "request_sha256", "nonce", "origin_id", "chat_id"}
        self.assertFalse(private_fields & hostops.PERSONAL_PUBLIC_FIELDS)
        self.assertFalse(private_fields & contract.PUBLIC_FIELDS)
        old = contract.receipt(None, "UNKNOWN", result={"snapshot": {"account": "PRIVATE_SECRET"}})
        with self.assertRaisesRegex(contract.Denied, "PRIVATE_RECEIPT_FIELD"):
            contract.public_receipt(old)

    def test_persisted_owner_consumer_and_registered_downstream_present(self):
        root = MODULE.parents[3]
        owner = json.loads((root / "work_items/mt01-components/hostops-personal.json").read_text(encoding="utf-8"))
        self.assertEqual(owner["sources"], ["src/ai_commerce/hostops_v3/personal.py"])
        self.assertEqual(owner["tests"], ["tests/test_hostops_personal.py"])
        self.assertEqual(owner["consumers"], ["src/ai_commerce/hostops_v3/contract.py"])
        self.assertTrue((root / owner["consumers"][0]).is_file())
        registry = (root / "src/ai_commerce/engineering/minimum_test.py").read_text(encoding="utf-8")
        self.assertIn('TestGroup("hostops-v3"', registry)
        self.assertIn('"tests/test_hostops_v3.py"', registry)
        profiles = json.loads((root / ".github/ef02/verify-profiles.json").read_text(encoding="utf-8"))
        self.assertIn("mt01-component-onboarding", profiles)
        self.assertEqual(profiles["mt01-hostops-v3-tests"][-2:], ["-n", "12"])


class PersonalCdp9444ConfigRegistryTests(unittest.TestCase):
    """Mocked Windows metadata fixtures for the source-only operation."""

    OPERATION = "cdp_9444_config_registry"
    SLOT_IDS = ("SLOT_01", "SLOT_02", "SLOT_03", "SLOT_04", "SLOT_05", "SLOT_06")
    STATES = ("ABSENT", "PRESENT_FILE", "PRESENT_NONFILE", "UNAVAILABLE_OR_REPARSE")

    def setUp(self):
        from ai_commerce.remote import chrome_acquisition

        self.acquisition = chrome_acquisition
        self.local = PureWindowsPath("C:/Users/PRIVATE_OWNER/AppData/Local")
        self.root = self.local / "AI-Commerce"
        self.candidates = tuple(self.root / name for name in chrome_acquisition._CONFIG_9444_RELATIVE_PATHS)
        self.metadata = {path: self.info(stat.S_IFDIR) for path in (self.root, self.local, *self.local.parents)}
        self.native = self.enterContext(patch.object(chrome_acquisition, "_native_local_app_data", return_value=self.local))
        self.enterContext(patch.object(hostops, "os", new=SimpleNamespace(name="nt")))
        self.probe = Mock(side_effect=self.lstat)
        self.enterContext(patch.object(PureWindowsPath, "lstat", lambda path: self.probe(path), create=True))

    @staticmethod
    def info(mode, attributes=0):
        return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)

    def lstat(self, path):
        value = self.metadata.get(path, FileNotFoundError(str(path)))
        if isinstance(value, OSError):
            raise value
        return value

    def observe(self):
        return hostops.perform(hostops.parse_event(event("read", self.OPERATION, {}), "read"))

    def assert_receipt(self, receipt, states, *, code="METADATA_ONLY"):
        self.assertEqual(receipt, {
            "status": "OBSERVED" if code == "METADATA_ONLY" else "UNKNOWN",
            "code": code, "operation": self.OPERATION, "cdp_port": 9444,
            "slots": [{"slot_id": slot, "status": state} for slot, state in zip(self.SLOT_IDS, states)],
            "counts": {state: states.count(state) for state in self.STATES},
            "no_shell_command_executed": True, "browser_attached": False, "chrome_launched": False,
            "config_written": False, "enrollment_performed": False, "send_performed": False, "grant_issued": False,
        })
        self.assertEqual(len(receipt["slots"]), 6)
        self.assertTrue(all(type(count) is int and 0 <= count <= 6 for count in receipt["counts"].values()))
        self.assertLess(len(json.dumps(receipt).encode("ascii")), 1024)

    def test_exact_parameterless_read_registration(self):
        self.assertEqual(hostops.parse_event(event("read", self.OPERATION, {}), "read"), {
            "issue_number": 17, "operation": self.OPERATION, "parameters": {},
        })
        for params in ({"port": 9444}, {"LOCALAPPDATA": str(self.local)}, {"command": "Get-Item"}):
            with self.subTest(params=params), self.assertRaisesRegex(hostops.Denied, "INVALID_PARAMETERS"):
                hostops.parse_event(event("read", self.OPERATION, params), "read")
            with self.assertRaisesRegex(hostops.Denied, "INVALID_PARAMETERS"):
                hostops.perform({"operation": self.OPERATION, "parameters": params})
        for params in (None, [], ""):
            with self.assertRaisesRegex(hostops.Denied, "INVALID_REQUEST"):
                hostops.parse_event(event("read", self.OPERATION, params), "read")
            with self.assertRaisesRegex(hostops.Denied, "INVALID_PARAMETERS"):
                hostops.perform({"operation": self.OPERATION, "parameters": params})
        for operation in (self.OPERATION.upper(), self.OPERATION + " "):
            with self.assertRaisesRegex(hostops.Denied, "READ_OPERATION_DENIED"):
                hostops.parse_event(event("read", operation, {}), "read")
        with self.assertRaisesRegex(hostops.Denied, "WRITE_SCOPE_DENIED"):
            hostops.parse_event(event("write", self.OPERATION, {}), "write")
        self.native.assert_not_called()

    def test_present_files_and_directory_stay_separate_with_no_effects(self):
        for index in (0, 1, 3):
            self.metadata[self.candidates[index].parent] = self.info(stat.S_IFDIR)
        self.metadata[self.candidates[0]] = self.info(stat.S_IFREG)
        self.metadata[self.candidates[3]] = self.info(stat.S_IFDIR)
        self.metadata[self.candidates[4]] = self.info(stat.S_IFREG)

        with (patch("sys.stdin", None), patch.object(builtins, "open") as open_file,
              patch.object(Path, "open") as path_open, patch.object(subprocess, "Popen") as popen,
              patch.object(self.acquisition, "load_environment") as load,
              patch.object(self.acquisition, "_interactive_session") as session,
              patch.object(self.acquisition, "_probe_cdp") as probe):
            result = self.observe()
            for effect in (open_file, path_open, popen, load, session, probe):
                effect.assert_not_called()
        self.native.assert_called_once_with()
        self.assert_receipt(result, ("PRESENT_FILE", "ABSENT", "ABSENT", "PRESENT_NONFILE", "PRESENT_FILE", "ABSENT"))
        self.assertEqual(result, self.observe())

    def test_unsafe_components_are_unavailable_and_their_children_are_unprobed(self):
        junction, symlink, nondirectory, unreadable, reparse, _ = self.candidates
        self.metadata[junction.parent] = self.info(stat.S_IFDIR, stat.FILE_ATTRIBUTE_REPARSE_POINT)
        self.metadata[symlink.parent] = self.info(stat.S_IFDIR)
        self.metadata[symlink] = self.info(stat.S_IFLNK)
        self.metadata[nondirectory.parent] = self.info(stat.S_IFREG)
        self.metadata[unreadable.parent] = self.info(stat.S_IFDIR)
        self.metadata[unreadable] = PermissionError("C:/PRIVATE_PATH credential=PRIVATE_TOKEN")
        self.metadata[reparse] = self.info(stat.S_IFREG, stat.FILE_ATTRIBUTE_REPARSE_POINT)

        self.assert_receipt(self.observe(), ("UNAVAILABLE_OR_REPARSE",) * 5 + ("ABSENT",),
                            code="CONFIG_CUSTODY_UNAVAILABLE")
        paths = [call.args[0] for call in self.probe.call_args_list]
        self.assertEqual(paths[:len(self.local.parts)], [*reversed(self.local.parents), self.local])
        self.assertNotIn(junction, paths)
        self.assertNotIn(nondirectory, paths)

    def test_missing_root_reports_six_absent_without_probing_slots(self):
        del self.metadata[self.root]
        self.assert_receipt(self.observe(), ("ABSENT",) * 6)
        self.assertEqual([call.args[0] for call in self.probe.call_args_list],
                         [*reversed(self.local.parents), self.local, self.root])

    def test_registry_drift_cannot_expand_the_metadata_probe(self):
        paths = self.acquisition._CONFIG_9444_RELATIVE_PATHS
        for registry in (paths + ("extra.json",), paths[:-1] + (paths[0].upper(),),
                         paths[:-1] + ("../private.json",), paths[:-1] + ("extra/subdir/config.json",)):
            with self.subTest(registry=registry), patch.object(self.acquisition, "_CONFIG_9444_RELATIVE_PATHS", registry):
                self.assert_receipt(self.observe(), ("UNAVAILABLE_OR_REPARSE",) * 6,
                                    code="REGISTRY_SOURCE_UNAVAILABLE")
        self.native.assert_not_called()
        self.probe.assert_not_called()

    def test_unavailable_known_folder_holds_without_falling_back(self):
        unavailable = ("UNAVAILABLE_OR_REPARSE",) * 6
        self.native.side_effect = self.acquisition._EnrollmentHold("PRIVATE_KNOWN_FOLDER_FAILURE")
        self.assert_receipt(self.observe(), unavailable, code="OWNER_KNOWN_FOLDER_UNAVAILABLE")
        self.probe.assert_not_called()
        self.native.side_effect = None
        for path in (None, PureWindowsPath("//PRIVATE_SERVER/share/Local")):
            with self.subTest(path=path):
                self.native.return_value = path
                self.assert_receipt(self.observe(), unavailable, code="OWNER_KNOWN_FOLDER_UNAVAILABLE")
        self.probe.assert_not_called()
        self.native.return_value = self.local
        self.metadata[self.local.parent] = PermissionError("PRIVATE_OWNER")
        self.assert_receipt(self.observe(), unavailable, code="OWNER_KNOWN_FOLDER_UNAVAILABLE")
        self.assertNotIn(self.local, [call.args[0] for call in self.probe.call_args_list])

    def test_public_issue_entrypoint_collects_only_metadata_after_protected_integration(self):
        args = SimpleNamespace(mode="read", output="receipt.json")
        payload = json.dumps(event("read", self.OPERATION, {}))
        with (patch.object(hostops, "os", new=SimpleNamespace(name="nt", environ={
                  "GITHUB_EVENT_PATH": "event.json", "GITHUB_SHA": "f" * 40})),
              patch.object(hostops.argparse.ArgumentParser, "parse_args", return_value=args),
              patch.object(builtins, "open", mock_open(read_data=payload)),
              patch.object(hostops, "Path") as output):
            self.assertEqual(hostops.main(), 0)
        record = json.loads(output.return_value.write_text.call_args.args[0])
        self.assertEqual(record["status"], "OBSERVED")
        self.assertEqual(record["code"], "METADATA_ONLY")
        self.assertEqual(record["issue_number"], 17)
        self.assertEqual(record["source_sha"], "f" * 40)
        self.assertEqual(record["approval_channel"], "CHATGPT_TO_AI_COMMERCE_ISSUE")
        self.assertFalse(record["remote_shell_enabled"])
        self.assertEqual(record["slots"], [
            {"slot_id": slot, "status": "ABSENT"} for slot in self.SLOT_IDS])
        self.native.assert_called_once_with()
        self.probe.assert_called()

    def test_protected_registry_transport_matches_closed_source_contract(self):
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        ids = re.search(r"const registrySlotIds = (\[[^\n]+\]);", workflow)
        states = re.search(r"const registryStates = (\[[^\n]+\]);", workflow)
        self.assertIsNotNone(ids)
        self.assertIsNotNone(states)
        self.assertEqual(tuple(re.findall(r"'([^']+)'", ids.group(1))), self.SLOT_IDS)
        self.assertEqual(tuple(re.findall(r"'([^']+)'", states.group(1))), self.STATES)
        self.assertIn("INVALID_PUBLIC_CDP_REGISTRY_RECEIPT", workflow)
        self.assertIn("rawNativeText.trim() === JSON.stringify(record)", workflow)
        self.assertIn("record.issue_number === context.issue.number", workflow)
        self.assertIn("record.source_sha === context.sha", workflow)
        self.assertIn("registryRequested || record.operation === 'cdp_9444_config_registry'", workflow)
        self.assertNotIn("PUBLIC_RECEIPT_INTEGRATION_PENDING", MODULE.read_text(encoding="utf-8"))

    def test_protected_registry_sanitizer_runtime_positive_and_negative(self):
        """Execute the actual protected JavaScript against synthetic receipts; no host effects."""
        import os
        import shutil
        node = shutil.which("node") or shutil.which("node.exe")
        if not node and os.name == "nt" and os.environ.get("RUNNER_TEMP"):
            runner_root = Path(os.environ["RUNNER_TEMP"]).parents[1]
            for version in ("node24", "node20"):
                candidate = runner_root / "externals" / version / "bin" / "node.exe"
                if candidate.is_file():
                    node = str(candidate)
                    break
        if not node:
            self.skipTest("Node CLI unavailable: hosted Windows JS sanitizer verification required")
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        workflow = workflow.split("  hostops-d5-carrier:", 1)[0]
        self.assertEqual(workflow.count("          script: |"), 1)
        script = "\n".join(line[12:] if line.startswith("            ") else line
                           for line in workflow.split("          script: |\n", 1)[1].splitlines())
        source = {**self.observe(), "issue_number": 17,
                  "approval_channel": "CHATGPT_TO_AI_COMMERCE_ISSUE",
                  "remote_shell_enabled": False, "source_sha": "f" * 40}
        encode = lambda value: json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        blocked = {"status": "BLOCKED", "operation": self.OPERATION,
                   "code": "INVALID_PUBLIC_CDP_REGISTRY_RECEIPT"}
        request = json.dumps({"schema": hostops.SCHEMA, "operation": self.OPERATION,
                              "parameters": {}}, separators=(",", ":"))
        cases = [{"name": "observed", "body": encode(source), "expected": source}]
        unknown = copy.deepcopy(source)
        unknown.update(status="UNKNOWN", code="CONFIG_CUSTODY_UNAVAILABLE")
        unknown["slots"][0]["status"] = "UNAVAILABLE_OR_REPARSE"
        unknown["counts"].update(ABSENT=5, UNAVAILABLE_OR_REPARSE=1)
        cases.append({"name": "partial_custody_unknown", "body": encode(unknown), "expected": unknown})
        all_unknown = copy.deepcopy(source)
        all_unknown.update(status="UNKNOWN", code="OWNER_KNOWN_FOLDER_UNAVAILABLE")
        for row in all_unknown["slots"]:
            row["status"] = "UNAVAILABLE_OR_REPARSE"
        all_unknown["counts"].update(ABSENT=0, UNAVAILABLE_OR_REPARSE=6)
        cases.append({"name": "known_folder_unknown", "body": encode(all_unknown),
                      "expected": all_unknown})
        source_unknown = {**all_unknown, "code": "REGISTRY_SOURCE_UNAVAILABLE"}
        cases.append({"name": "registry_source_unknown", "body": encode(source_unknown),
                      "expected": source_unknown})
        mutations = {
            "unexpected_private_path": {**source, "private_path": "PRIVATE_SECRET"},
            "nested_private_metadata": {**source, "config": {"token": "PRIVATE_SECRET"}},
            "wrong_source_sha": {**source, "source_sha": "0" * 40},
            "wrong_issue": {**source, "issue_number": 123},
            "wrong_approval": {**source, "approval_channel": "PRIVATE"},
            "wrong_port": {**source, "cdp_port": 9333},
            "wrong_no_shell": {**source, "no_shell_command_executed": False},
            "unsafe_browser_effect": {**source, "chrome_launched": True},
            "unsafe_send_effect": {**source, "send_performed": True},
            "unsafe_remote_shell": {**source, "remote_shell_enabled": True},
            "extra_slot": {**source, "slots": source["slots"] + [source["slots"][0]]},
            "missing_slot": {**source, "slots": source["slots"][:-1]},
            "reordered_slot": {**source, "slots": list(reversed(source["slots"]))},
            "duplicate_slot": {**source, "slots": [source["slots"][0]] * 6},
            "private_row": {**source, "slots": [
                {**source["slots"][0], "path": "PRIVATE_SECRET"}, *source["slots"][1:]]},
            "invalid_state": {**source, "slots": [
                {**source["slots"][0], "status": "PRIVATE_SECRET"}, *source["slots"][1:]]},
            "count_mismatch": {**source, "counts": {**source["counts"], "ABSENT": 5}},
            "count_extra": {**source, "counts": {**source["counts"], "PRIVATE": 0}},
            "count_type": {**source, "counts": {**source["counts"], "ABSENT": 6.5}},
            "unknown_code": {**source, "status": "UNKNOWN", "code": "PRIVATE_SECRET"},
            "false_observed": {**unknown, "status": "OBSERVED"},
            "false_unknown": {**source, "status": "UNKNOWN", "code": "CONFIG_CUSTODY_UNAVAILABLE"},
            "false_owner_unknown": {**unknown, "code": "OWNER_KNOWN_FOLDER_UNAVAILABLE"},
            "missing_field": {key: value for key, value in source.items() if key != "counts"},
        }
        cases.extend({"name": name, "body": encode(value), "expected": blocked}
                     for name, value in mutations.items())
        duplicate_raw = encode(source).replace('"status":"OBSERVED"',
                                               '"status":"OBSERVED","status":"OBSERVED"', 1)
        cases.extend([
            {"name": "duplicate_raw_json_key", "body": duplicate_raw, "expected": blocked},
            {"name": "malformed_json", "body": "{", "expected": blocked},
            {"name": "missing_receipt", "body": None, "expected": blocked},
            {"name": "oversize_receipt", "body": encode({**source, "padding": "P" * 8192}),
             "expected": blocked},
            {"name": "wrong_issue_body", "body": encode(source), "expected": blocked,
             "request": encode({"schema": hostops.SCHEMA, "operation": "inventory", "parameters": {}})},
        ])
        legacy = {"status": "OBSERVED", "operation": "inventory",
                  "slots": [{"path": "PRIVATE_SECRET"}], "counts": {"PRIVATE": 1}}
        cases.append({"name": "legacy_projection_isolation", "body": encode(legacy),
                      "request": encode({"schema": hostops.SCHEMA, "operation": "inventory", "parameters": {}}),
                      "expected": {"status": "OBSERVED", "operation": "inventory"}})
        harness = r"""
const fs = require('fs'), path = require('path'), vm = require('vm');
const cases = JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const script = fs.readFileSync(process.argv[2],'utf8');
const assert = require('assert');
(async () => {
  for (const item of cases) {
    const receiptPath = path.join(process.env.RUNNER_TEMP,'personal-hostops-result.json');
    if (item.body === null) {
      try { fs.unlinkSync(receiptPath); } catch (_) {}
    } else {
      fs.writeFileSync(receiptPath,item.body,'utf8');
    }
    const comments = [];
    const context = {repo:{owner:'trung-via',repo:'ai-commerce'},issue:{number:17},
      sha:'f'.repeat(40),payload:{issue:{body:item.request || process.env.REGISTRY_REQUEST}}};
    const github = {rest:{issues:{createComment:async args => { comments.push(args.body); }}}};
    await vm.runInNewContext('(async () => {\n'+script+'\n})()',
      {require,process,context,github,Buffer});
    assert.strictEqual(comments.length,1,item.name+': missing result');
    assert.ok(comments[0].startsWith('AI_COMMERCE_PERSONAL_HOSTOPS_RESULT_V1\n'),item.name);
    const actual = JSON.parse(comments[0].split('\n').slice(1).join('\n'));
    assert.deepStrictEqual(actual,item.expected,item.name+': invalid projection');
    assert.ok(!JSON.stringify(actual).includes('PRIVATE_SECRET'),item.name+': private leak');
  }
  process.stdout.write('VERIFIED_REGISTRY_CASES='+cases.length);
})().catch(error => { console.error(error); process.exitCode=1; });
"""
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "cases.json").write_text(encode(cases), encoding="utf-8")
            (directory / "workflow.js").write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [node, "-e", harness, str(directory / "cases.json"), str(directory / "workflow.js")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30, env={**os.environ, "RUNNER_TEMP": str(directory),
                                 "REGISTRY_REQUEST": request}, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr[-3000:])
        self.assertIn("VERIFIED_REGISTRY_CASES=" + str(len(cases)), completed.stdout)

    def test_existing_reads_keep_their_dispatch_and_standing_rows(self):
        standing = PersonalHostOpsContractTests._standing_snapshot()
        topology = {"operation": "cdp_9444_topology", "cdp_port": 9444}
        with (patch.object(hostops, "_standing_read_snapshot", return_value=standing) as standing_read,
              patch.object(hostops, "_cdp_9444_topology", return_value=topology) as topology_read):
            for expected in (standing, topology):
                request = hostops.parse_event(event("read", expected["operation"], {}), "read")
                self.assertEqual(hostops.perform(request), expected)
        self.assertEqual(len(standing["diagnostics"]), 16)
        self.assertNotIn("slots", hostops.PERSONAL_PUBLIC_FIELDS)
        standing_read.assert_called_once_with()
        topology_read.assert_called_once_with()
        self.native.assert_not_called()


class PersonalCdp9444TopologyTests(unittest.TestCase):
    """Offline fixtures only: no real CIM, token, signature, browser or sends."""

    NATIVE_FAILURE_CODES = frozenset({
        "NATIVE_IMPORT_FAILED", "NATIVE_QUERY_FAILED", "NATIVE_NORMALIZATION_FAILED",
        "NATIVE_RECHECK_QUERY_FAILED", "NATIVE_RECHECK_NORMALIZATION_FAILED", "NATIVE_RECHECK_FAILED",
    })
    PUBLIC_CODES = NATIVE_FAILURE_CODES | {
        "NO_LISTENER", "CLAIMANT_WITHOUT_LISTENER", "UNTRUSTED_BIND", "UNTRUSTED_OWNER",
        "OWNER_SESSION_UNATTESTED", "TOPOLOGY_CHANGED", "CDP_PROTOCOL_FAILED", "CDP_PROTOCOL_HEALTHY",
    }
    COUNT_FIELDS = ("listener_count", "loopback_count", "unexpected_binding_count",
                    "chrome_owned_loopback_count", "same_session_chrome_count")

    def setUp(self):
        from ai_commerce.remote import chrome_acquisition, local_completion_handoff, local_wake_entry
        self.acquisition = chrome_acquisition
        self.native_query = chrome_acquisition._query_windows
        self.strict_probe = chrome_acquisition._probe_cdp
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.local = Path(temp.name) / "PRIVATE_LOCAL"
        self.root = self.local / "AI-Commerce"
        self.config = self.root / "Chrome-Acquisition" / "config.json"
        self.config.parent.mkdir(parents=True)
        self.directory = self.root / "PRIVATE_PROFILE"
        (self.directory / "Default").mkdir(parents=True)
        self.executable = Path(temp.name) / "chrome.exe"
        self.executable.write_bytes(b"offline fixture, signature mocked")
        self.sid, self.session = "S-1-5-21-101-202-303-1001", 7
        self.config_value = {
            "version": 1, "executable": str(self.executable), "user_data_dir": str(self.directory),
            "profile_directory": "Default", "cdp_endpoint": "http://127.0.0.1:9444",
            "allow_launch": False, "exclusive_user_data": True,
        }
        self.config.write_text(json.dumps(self.config_value), encoding="utf-8")
        self.argv = [str(self.executable), "--user-data-dir=" + str(self.directory),
                     "--profile-directory=Default", "--remote-debugging-address=127.0.0.1",
                     "--remote-debugging-port=9444"]
        self.proc = {"ProcessId": 4100, "ExecutablePath": str(self.executable),
                     "CommandLine": json.dumps(self.argv), "SessionId": self.session, "OwnerSid": self.sid,
                     "CreationDate": "2026-11-15T08:00:00.0000000Z"}
        self.listener = {"LocalAddress": "127.0.0.1", "LocalPort": 9444, "OwningProcess": 4100}
        self.inventory = {"listeners": [self.listener], "processes": [self.proc]}

        def mocked(obj, name, **kwargs):
            pending = patch.object(obj, name, **kwargs)
            result = pending.start()
            self.addCleanup(pending.stop)
            return result

        # Patch this module's OS reference, leaving pathlib and other modules intact.
        mocked(hostops, "os", new=SimpleNamespace(name="nt"))
        self.native = mocked(chrome_acquisition, "_query_windows", return_value=self.inventory)
        mocked(chrome_acquisition, "_commandline_argv", side_effect=json.loads)
        self.local_identity = mocked(chrome_acquisition, "_native_local_app_data", return_value=self.local)
        self.session_identity = mocked(chrome_acquisition, "_interactive_session", return_value=self.session)
        self.token_identity = mocked(local_wake_entry, "_windows_process_sid", return_value=self.sid)
        self.private_identity = mocked(local_completion_handoff, "_private_identity", return_value=self.sid)
        self.signature = mocked(chrome_acquisition, "_verify_google_chrome")
        self.protocol = mocked(chrome_acquisition, "_probe_cdp")

    def observe(self, inventory=None):
        if inventory is not None:
            self.native.return_value = inventory
        request = hostops.parse_event(event("read", "cdp_9444_topology", {}), "read")
        return hostops.perform(request)

    def assert_public(self, result):
        self.assertEqual(set(result), {
            "status", "code", "operation", "cdp_port", *self.COUNT_FIELDS,
            "devtools_protocol_ok", "infrastructure_ready", "exact_chat_verified", "wake_sent",
            "arbitrary_shell_enabled",
        })
        self.assertLessEqual(set(result), hostops.PERSONAL_PUBLIC_FIELDS)
        self.assertIn(result["code"], self.PUBLIC_CODES)
        self.assertRegex(result["code"], r"^[A-Z_]{2,64}$")
        self.assertIn(result["status"], ("UNKNOWN", "OBSERVED"))
        self.assertEqual(result["operation"], "cdp_9444_topology")
        self.assertEqual(result["cdp_port"], 9444)
        for field in self.COUNT_FIELDS:
            self.assertTrue(result[field] is None or
                            type(result[field]) is int and 0 <= result[field] <= 16)
        self.assertEqual(result["infrastructure_ready"], result["code"] == "CDP_PROTOCOL_HEALTHY")
        self.assertFalse(result["exact_chat_verified"])
        self.assertFalse(result["wake_sent"])
        self.assertFalse(result["arbitrary_shell_enabled"])
        raw = json.dumps(result)
        self.assertLessEqual(len(raw.encode("utf-8")), 8192)
        for private in (self.sid, str(self.local), str(self.executable), "PRIVATE", "4100",
                        "CommandLine", "ProcessId", "OwnerSid", "SessionId", "webSocketDebuggerUrl",
                        "enrolled", "profile_directory", "S-1-", "ws://", "http://", "token", "argv"):
            self.assertNotIn(private, raw)

    def assert_native_unknown(self, result, code):
        self.assertEqual((result["status"], result["code"]), ("UNKNOWN", code))
        for field in (*self.COUNT_FIELDS, "devtools_protocol_ok"):
            self.assertIsNone(result[field])
        self.assertFalse(result["infrastructure_ready"])
        self.assert_public(result)

    def test_import_failure_is_independent_of_native_query_and_normalization(self):
        original_import = builtins.__import__
        for error in (ImportError("PRIVATE import"), OSError("PRIVATE dependency"),
                      RuntimeError("PRIVATE initialization")):
            def unavailable(name, globals=None, locals=None, fromlist=(), level=0):
                if name == "ai_commerce.remote" and "chrome_acquisition" in fromlist:
                    raise error
                return original_import(name, globals, locals, fromlist, level)

            with self.subTest(error=type(error).__name__), \
                    patch("builtins.__import__", side_effect=unavailable), \
                    patch.object(hostops, "_cdp_9444_inventory") as normalize:
                result = self.observe()
            self.assert_native_unknown(result, "NATIVE_IMPORT_FAILED")
            normalize.assert_not_called()
        self.native.assert_not_called()
        self.private_identity.assert_not_called()
        self.protocol.assert_not_called()

    def test_exact_owner_open_read_and_empty_parameters_only(self):
        value = event("read", "cdp_9444_topology", {})
        self.assertEqual(hostops.parse_event(value, "read")["operation"], "cdp_9444_topology")
        mutations = (
            lambda v: v["issue"]["user"].update(login="forged"),
            lambda v: v["sender"].update(login="forged"),
            lambda v: v["repository"]["owner"].update(login="forged"),
            lambda v: v["repository"].update(private=True),
            lambda v: v["issue"].update(title="[HOSTOPS WRITE] cdp_9444_topology"),
            lambda v: v["issue"].update(title="cdp_9444_topology"),
            lambda v: v["issue"].update(state="closed"),
            lambda v: v["issue"].update(pull_request={}),
            lambda v: v.update(action="edited"),
        )
        for mutate in mutations:
            bad = copy.deepcopy(value)
            mutate(bad)
            with self.subTest(event=bad), self.assertRaises(hostops.Denied):
                hostops.parse_event(bad, "read")
        for params in ({"port": 9444}, {"pid": 4100}, {"owner_sid": self.sid},
                       {"session_id": self.session}, {"config": str(self.config)},
                       {"command": "Get-Process"}, {"chat_url": "PRIVATE"}, [], None):
            with self.subTest(params=params), self.assertRaises(hostops.Denied):
                hostops.parse_event(event("read", "cdp_9444_topology", params), "read")
            with self.assertRaises(hostops.Denied):
                hostops.perform({"operation": "cdp_9444_topology", "parameters": params})
        for operation in ("CDP_9444_TOPOLOGY", "cdp_9444_topology ", "cdp_9444_launch", "cdp_9444_enroll"):
            with self.assertRaises(hostops.Denied):
                hostops.parse_event(event("read", operation, {}), "read")
        with self.assertRaises(hostops.Denied):
            hostops.parse_event(event("write", "cdp_9444_topology", {}), "write")
        self.native.assert_not_called()

    def test_absent_and_unrelated_chrome_are_observed_zero_without_http(self):
        unrelated = {**self.proc, "CommandLine": json.dumps([str(self.executable)])}
        for processes in ([], [unrelated]):
            result = self.observe({"listeners": [], "processes": processes})
            self.assertEqual((result["status"], result["code"]), ("OBSERVED", "NO_LISTENER"))
            for name in ("listener_count", "loopback_count", "unexpected_binding_count",
                         "chrome_owned_loopback_count", "same_session_chrome_count"):
                self.assertEqual(result[name], 0)
            self.assertIsNone(result["devtools_protocol_ok"])
            self.assert_public(result)
        self.protocol.assert_not_called()
        self.private_identity.assert_not_called()

    def test_commandline_9444_without_listener_is_a_claim_only(self):
        result = self.observe({"listeners": [], "processes": [self.proc]})
        self.assertEqual(result["code"], "CLAIMANT_WITHOUT_LISTENER")
        self.assertEqual(result["listener_count"], 0)
        self.assertFalse(result["infrastructure_ready"])
        self.protocol.assert_not_called()
        self.assert_public(result)

    def test_child_chrome_is_neither_claimant_nor_listener_owner(self):
        child = {**self.proc, "CommandLine": json.dumps(self.argv + ["--type=renderer"])}
        result = self.observe({"listeners": [], "processes": [child]})
        self.assertEqual(result["code"], "NO_LISTENER")
        result = self.observe({"listeners": [self.listener], "processes": [child]})
        self.assertEqual(result["code"], "UNTRUSTED_OWNER")
        # An ordinary child alongside the owning browser does not add a claimant.
        result = self.observe({"listeners": [self.listener], "processes": [self.proc, {**child, "ProcessId": 4101}]})
        self.assertEqual(result["code"], "CDP_PROTOCOL_HEALTHY")

    def test_unsafe_or_multiple_bindings_never_probe(self):
        for address in ("0.0.0.0", "::", "::1", "127.0.0.2", "localhost"):
            result = self.observe({"listeners": [{**self.listener, "LocalAddress": address}], "processes": [self.proc]})
            self.assertEqual((result["status"], result["code"]), ("OBSERVED", "UNTRUSTED_BIND"))
            self.assertEqual(result["unexpected_binding_count"], 1)
            self.assert_public(result)
        result = self.observe({"listeners": [self.listener, self.listener], "processes": [self.proc]})
        self.assertEqual(result["code"], "UNTRUSTED_BIND")
        self.protocol.assert_not_called()
        self.private_identity.assert_not_called()

    def test_listener_pid_and_competing_claimants_fail_closed(self):
        for processes in ([], [{**self.proc, "ProcessId": 4101}],
                          [self.proc, {**self.proc, "ProcessId": 4101}]):
            result = self.observe({"listeners": [self.listener], "processes": processes})
            self.assertEqual(result["code"], "UNTRUSTED_OWNER")
            self.assertFalse(result["infrastructure_ready"])
        self.protocol.assert_not_called()

    def test_wrong_native_owner_or_session_never_probes(self):
        for changes in ({"OwnerSid": "S-1-5-21-999-1001"}, {"SessionId": 0}, {"SessionId": 8}):
            result = self.observe({"listeners": [self.listener], "processes": [{**self.proc, **changes}]})
            self.assertEqual(result["code"], "UNTRUSTED_OWNER")
            self.assert_public(result)
        self.protocol.assert_not_called()
        self.signature.assert_not_called()

    def test_wrong_executable_profile_or_debug_switches_never_probe(self):
        variants = [self.argv + ["--user-data-dir=" + str(self.directory)],
                    self.argv + ["--remote-debugging-port=9444"],
                    [*self.argv[:2], "--profile-directory=Other", *self.argv[3:]],
                    [*self.argv[:3], "--remote-debugging-address=0.0.0.0", self.argv[4]],
                    [*self.argv[:4], "--remote-debugging-port=9333"],
                    [str(self.executable.parent / "other.exe"), *self.argv[1:]],
                    [self.argv[0], "--user-data-dir=" + str(self.root), *self.argv[2:]]]
        for argv in variants:
            result = self.observe({"listeners": [self.listener],
                                   "processes": [{**self.proc, "CommandLine": json.dumps(argv)}]})
            self.assertIn(result["code"], ("UNTRUSTED_OWNER", "NATIVE_NORMALIZATION_FAILED"))
            self.assertFalse(result["infrastructure_ready"])
        result = self.observe({"listeners": [self.listener],
                               "processes": [{**self.proc, "ExecutablePath": str(self.executable.parent / "other.exe")}]})
        self.assertEqual(result["code"], "UNTRUSTED_OWNER")
        self.protocol.assert_not_called()

    def test_cim_timeouts_json_errors_and_native_failures_are_unknown_not_zero(self):
        errors = [self.acquisition.AcquisitionBlocked("CIM PRIVATE"), OSError("PRIVATE"),
                  subprocess.TimeoutExpired("PRIVATE", 8), json.JSONDecodeError("PRIVATE", "x", 0),
                  UnicodeError("PRIVATE")]
        for error in errors:
            self.native.reset_mock()
            self.native.side_effect = error
            with patch.object(hostops, "_cdp_9444_inventory") as normalize:
                result = self.observe()
            self.assert_native_unknown(result, "NATIVE_QUERY_FAILED")
            self.native.assert_called_once_with(9444)
            normalize.assert_not_called()
        self.private_identity.assert_not_called()
        self.protocol.assert_not_called()

    def test_real_query_with_offline_subprocess_distinguishes_raw_failure_from_inventory(self):
        self.native.side_effect = self.native_query
        # Run the real query/parser with all process I/O replaced. A malformed
        # outer response fails in the query; returned row data fails normalization.
        cases = [
            (SimpleNamespace(returncode=1, stdout=b"PRIVATE CIM"), "NATIVE_QUERY_FAILED"),
            (OSError("PRIVATE process"), "NATIVE_QUERY_FAILED"),
            (subprocess.TimeoutExpired("PRIVATE", 8), "NATIVE_QUERY_FAILED"),
            (SimpleNamespace(returncode=0, stdout=b"PRIVATE invalid JSON"), "NATIVE_QUERY_FAILED"),
            (SimpleNamespace(returncode=0, stdout=b"\xff"), "NATIVE_QUERY_FAILED"),
            (SimpleNamespace(returncode=0, stdout=b"x" * 262145), "NATIVE_QUERY_FAILED"),
            (SimpleNamespace(returncode=0, stdout=b"{}"), "NATIVE_QUERY_FAILED"),
            (SimpleNamespace(returncode=0, stdout=b'{"listeners":null,"processes":[]}'),
             "NATIVE_NORMALIZATION_FAILED"),
            (SimpleNamespace(returncode=0, stdout=b'{"listeners":[],"processes":[]}'), "NO_LISTENER"),
        ]
        with patch.object(self.acquisition, "_powershell", return_value=Path("PRIVATE_POWERSHELL")), \
                patch.object(self.acquisition, "_child_environment", return_value={}):
            for response, code in cases:
                self.native.reset_mock()
                with patch.object(self.acquisition.subprocess, "run") as run:
                    if isinstance(response, Exception):
                        run.side_effect = response
                    else:
                        run.return_value = response
                    result = self.observe()
                if code == "NO_LISTENER":
                    self.assertEqual((result["status"], result["code"]), ("OBSERVED", code))
                    self.assertTrue(all(result[field] == 0 for field in self.COUNT_FIELDS))
                    self.assert_public(result)
                else:
                    self.assert_native_unknown(result, code)
                self.native.assert_called_once_with(9444)
                run.assert_called_once()
                self.assertEqual(run.call_args.kwargs["timeout"], 8)
                self.assertEqual(run.call_args.kwargs["env"]["AI_COMMERCE_ACQ_PROBE_PORT"], "9444")
        self.private_identity.assert_not_called()
        self.protocol.assert_not_called()

    def test_malformed_inventory_never_becomes_observed_absence(self):
        invalid = [None, {}, Mock(), {"listeners": Mock(), "processes": []},
                   {"listeners": None, "processes": []}, {"listeners": [], "processes": None},
                   {"listeners": {}, "processes": []}, {**self.inventory, "PRIVATE": "secret"},
                   {"listeners": [self.listener] * 17, "processes": []},
                   {"listeners": [], "processes": [self.proc] * 257}]
        for field, value in (("LocalPort", 9333), ("LocalPort", True), ("OwningProcess", 0),
                             ("OwningProcess", True), ("LocalAddress", None)):
            invalid.append({"listeners": [{**self.listener, field: value}], "processes": [self.proc]})
        for field, value in (("ProcessId", True), ("SessionId", True), ("CommandLine", None),
                             ("CommandLine", "not JSON fixture argv"), ("OwnerSid", None), ("OwnerSid", "PRIVATE"),
                             ("CreationDate", ""), ("CreationDate", "PRIVATE"), ("CreationDate", "2026-99-99T00:00:00Z")):
            invalid.append({"listeners": [], "processes": [{**self.proc, field: value}]})
        invalid.extend([{"listeners": [], "processes": [self.proc, self.proc]},
                        {"listeners": [], "processes": [{**self.proc, "PRIVATE": "raw"}]}])
        for value in invalid:
            self.native.reset_mock()
            self.native.return_value = value
            result = self.observe()
            self.assert_native_unknown(result, "NATIVE_NORMALIZATION_FAILED")
            self.native.assert_called_once_with(9444)
        self.private_identity.assert_not_called()
        self.protocol.assert_not_called()

    def test_normalizer_exceptions_are_distinct_from_raw_query_failure(self):
        for error in (ValueError("PRIVATE argv"), OSError("PRIVATE normalization"),
                      RuntimeError("PRIVATE " * 8192)):
            self.native.reset_mock()
            with patch.object(hostops, "_cdp_9444_inventory", side_effect=error) as normalize:
                result = self.observe()
            self.assert_native_unknown(result, "NATIVE_NORMALIZATION_FAILED")
            self.native.assert_called_once_with(9444)
            normalize.assert_called_once_with(self.acquisition, self.inventory)
        self.private_identity.assert_not_called()
        self.protocol.assert_not_called()

    def test_unavailable_or_service_identity_cannot_attest_owner(self):
        for mock, values in ((self.session_identity, (None, True, 0)),
                             (self.token_identity, (None, "", "forged", "S-1-5-18")),
                             (self.private_identity, (None, "S-1-5-21-999-1001"))):
            original = mock.return_value
            for value in values:
                mock.return_value = value
                result = self.observe()
                self.assertEqual((result["status"], result["code"]), ("UNKNOWN", "OWNER_SESSION_UNATTESTED"))
                self.assertEqual(result["listener_count"], 1)
                self.assertIsNone(result["same_session_chrome_count"])
                self.assert_public(result)
            mock.return_value = original
        self.private_identity.side_effect = OSError("PRIVATE ACL")
        with patch.object(self.acquisition, "load_environment") as load:
            self.assertEqual(self.observe()["code"], "OWNER_SESSION_UNATTESTED")
            load.assert_not_called()
        self.protocol.assert_not_called()

    def test_missing_ambiguous_or_wrong_config_is_not_enrollment(self):
        self.config.unlink()
        self.assertEqual(self.observe()["code"], "OWNER_SESSION_UNATTESTED")
        self.config.write_text(json.dumps(self.config_value), encoding="utf-8")
        second = self.root / "chrome.json"
        second.write_text(json.dumps(self.config_value), encoding="utf-8")
        self.assertEqual(self.observe()["code"], "OWNER_SESSION_UNATTESTED")
        second.unlink()
        for changes in ({"cdp_endpoint": "http://127.0.0.1:9333"}, {"profile_directory": "Missing"},
                        {"exclusive_user_data": False}, {"user_data_dir": str(self.local)},
                        {"executable": "PRIVATE"}):
            self.config.write_text(json.dumps({**self.config_value, **changes}), encoding="utf-8")
            result = self.observe()
            self.assertEqual((result["status"], result["code"]), ("UNKNOWN", "OWNER_SESSION_UNATTESTED"))
            self.assert_public(result)
        for raw in ('PRIVATE invalid JSON', '{"version":1,"version":1}'):
            self.config.write_text(raw, encoding="utf-8")
            self.assertEqual(self.observe()["code"], "OWNER_SESSION_UNATTESTED")
        self.protocol.assert_not_called()

    def test_unsigned_chrome_never_reaches_protocol_read(self):
        self.signature.side_effect = self.acquisition.AcquisitionBlocked("PRIVATE signature")
        result = self.observe()
        self.assertEqual(result["code"], "UNTRUSTED_OWNER")
        self.assertIsNone(result["chrome_owned_loopback_count"])
        self.protocol.assert_not_called()
        self.assert_public(result)

    def test_verified_protocol_is_not_chat_a_or_send_proof(self):
        result = self.observe()
        self.assertEqual((result["status"], result["code"]), ("OBSERVED", "CDP_PROTOCOL_HEALTHY"))
        self.assertTrue(result["devtools_protocol_ok"])
        self.assertTrue(result["infrastructure_ready"])
        self.assertEqual(result["chrome_owned_loopback_count"], 1)
        self.assertEqual(result["same_session_chrome_count"], 1)
        self.assertEqual(self.native.call_count, 2)
        self.assertTrue(all(call.args == (9444,) for call in self.native.call_args_list))
        self.signature.assert_called_once()
        self.protocol.assert_called_once_with(self.signature.call_args.args[0])
        self.assert_public(result)

    def test_real_strict_protocol_checks_browser_protocol_and_websocket_without_attaching(self):
        valid = {"Browser": "Chrome/153.0", "Protocol-Version": "1.3",
                 "webSocketDebuggerUrl": "ws://127.0.0.1:9444/devtools/browser/PRIVATE"}
        self.protocol.side_effect = self.strict_probe
        invalid = [{}, {**valid, "Browser": "Other/1"}, {**valid, "Protocol-Version": "PRIVATE"},
                   {**valid, "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/PRIVATE"},
                   {**valid, "webSocketDebuggerUrl": "ws://evil.invalid:9444/devtools/browser/PRIVATE"}]
        with patch.object(self.acquisition, "build_opener") as opener:
            reply = opener.return_value.open.return_value.__enter__.return_value
            reply.status = 200
            for value in [valid, *invalid]:
                reply.read.return_value = json.dumps(value).encode()
                result = self.observe()
                self.assertEqual(result["code"], "CDP_PROTOCOL_HEALTHY" if value == valid else "CDP_PROTOCOL_FAILED")
                self.assert_public(result)
            for body in (b"bad JSON PRIVATE", b"x" * 8193):
                reply.read.return_value = body
                self.assertEqual(self.observe()["status"], "UNKNOWN")
            self.assertEqual(opener.return_value.open.call_args.args[0].full_url,
                             "http://127.0.0.1:9444/json/version")
            self.assertEqual(opener.return_value.open.call_args.args[0].get_method(), "GET")
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 2)
            self.assertEqual(opener.call_args.args[0].proxies, {})
            self.assertIsInstance(opener.call_args.args[1], self.acquisition._NoRedirect)

    def test_protocol_failure_timeout_or_exception_is_unknown_without_retry(self):
        for error in (self.acquisition.AcquisitionBlocked("PRIVATE WS"), OSError("PRIVATE"),
                      subprocess.TimeoutExpired("PRIVATE", 2), ValueError("PRIVATE JSON")):
            self.protocol.reset_mock()
            self.protocol.side_effect = error
            result = self.observe()
            self.assertEqual((result["status"], result["code"]), ("UNKNOWN", "CDP_PROTOCOL_FAILED"))
            self.assertFalse(result["devtools_protocol_ok"])
            self.assertFalse(result["infrastructure_ready"])
            self.protocol.assert_called_once()
            self.assert_public(result)

    def test_observation_drift_prevents_http(self):
        same_profile = {**self.proc, "ProcessId": 4101, "CommandLine": json.dumps(self.argv[:3])}
        changed = [
            {"listeners": [], "processes": []},
            {"listeners": [self.listener], "processes": [{**self.proc, "CreationDate": "2026-11-15T09:00:00Z"}]},
            {"listeners": [self.listener], "processes": [self.proc, same_profile]},
        ]
        for case, current in enumerate(changed):
            self.native.reset_mock()
            self.native.side_effect = [self.inventory, current]
            with self.subTest(case=case), \
                    patch.object(self.acquisition, "evaluate", wraps=self.acquisition.evaluate) as evaluate:
                result = self.observe()
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(result["code"], "TOPOLOGY_CHANGED")
                self.assertFalse(result["infrastructure_ready"])
                self.assert_public(result)
                self.assertEqual(self.native.call_count, 2)
                evaluate.assert_called_once()
        self.protocol.assert_not_called()

    def test_recheck_query_failure_is_unknown_without_stale_counts_or_retry(self):
        for error in (self.acquisition.AcquisitionBlocked("PRIVATE CIM"), OSError("PRIVATE"),
                      subprocess.TimeoutExpired("PRIVATE", 8), json.JSONDecodeError("PRIVATE", "x", 0),
                      UnicodeError("PRIVATE")):
            self.native.reset_mock()
            self.native.side_effect = [self.inventory, error]
            result = self.observe()
            self.assert_native_unknown(result, "NATIVE_RECHECK_QUERY_FAILED")
            self.assertEqual([call.args for call in self.native.call_args_list], [(9444,), (9444,)])
        self.protocol.assert_not_called()

    def test_recheck_malformed_inventory_is_distinct_from_query_failure(self):
        invalid = [None, {}, Mock(), {"listeners": [], "processes": None},
                   {"listeners": [{**self.listener, "LocalPort": 9333}], "processes": [self.proc]},
                   {"listeners": [], "processes": [{**self.proc, "CommandLine": "PRIVATE invalid JSON"}]},
                   {"listeners": [], "processes": [self.proc, self.proc]}]
        for value in invalid:
            self.native.reset_mock()
            self.native.side_effect = [self.inventory, value]
            result = self.observe()
            self.assert_native_unknown(result, "NATIVE_RECHECK_NORMALIZATION_FAILED")
            self.assertEqual([call.args for call in self.native.call_args_list], [(9444,), (9444,)])
        self.protocol.assert_not_called()

    def test_recheck_normalizer_failure_does_not_reuse_first_observation(self):
        normalize = hostops._cdp_9444_inventory
        initial = normalize(self.acquisition, self.inventory)
        with patch.object(hostops, "_cdp_9444_inventory",
                          side_effect=[initial, RuntimeError("PRIVATE " * 8192)]):
            result = self.observe()
        self.assert_native_unknown(result, "NATIVE_RECHECK_NORMALIZATION_FAILED")
        self.assertEqual(self.native.call_count, 2)
        self.protocol.assert_not_called()

    def test_recheck_identity_or_evaluation_failure_is_stage_only(self):
        context = hostops._cdp_9444_private_context(self.acquisition)
        for target, name, initial in (
            (hostops, "_cdp_9444_private_context", context),
            (self.acquisition, "evaluate", ("OWNED", self.proc["ProcessId"])),
        ):
            for error in (OSError("PRIVATE SID/path/token"), self.acquisition.AcquisitionBlocked("PRIVATE")):
                self.native.reset_mock()
                with patch.object(target, name, side_effect=[initial, error]) as recheck:
                    result = self.observe()
                self.assert_native_unknown(result, "NATIVE_RECHECK_FAILED")
                self.assertEqual(recheck.call_count, 2)
                self.assertEqual(self.native.call_count, 2)
        self.protocol.assert_not_called()

    def test_recheck_session_or_ownership_drift_retains_topology_changed(self):
        with patch.object(self.acquisition, "_interactive_session", side_effect=[self.session, self.session + 1]):
            result = self.observe()
        self.assertEqual((result["status"], result["code"]), ("UNKNOWN", "TOPOLOGY_CHANGED"))
        self.assert_public(result)
        with patch.object(self.acquisition, "evaluate", side_effect=[("OWNED", 4100), ("ABSENT", None)]):
            result = self.observe()
        self.assertEqual((result["status"], result["code"]), ("UNKNOWN", "TOPOLOGY_CHANGED"))
        self.assert_public(result)
        self.protocol.assert_not_called()

    def test_source_public_field_tamper_and_non_windows_fail_before_reads(self):
        for field in ("code", "listener_count", "devtools_protocol_ok", "exact_chat_verified", "wake_sent"):
            with patch.object(hostops, "PERSONAL_PUBLIC_FIELDS", hostops.PERSONAL_PUBLIC_FIELDS - {field}):
                with self.assertRaisesRegex(hostops.Denied, "PUBLIC_TRANSPORT_GAP"):
                    self.observe()
        with patch.object(hostops, "os", SimpleNamespace(name="posix")):
            with self.assertRaisesRegex(hostops.Denied, "CDP_TOPOLOGY_WINDOWS_ONLY"):
                self.observe()
        self.native.assert_not_called()
        self.protocol.assert_not_called()

    def test_read_never_calls_native_writes_launch_enrollment_or_send(self):
        from ai_commerce.remote import local_wake_entry
        with patch.object(hostops.subprocess, "run") as run, patch.object(hostops.subprocess, "Popen") as launch, \
                patch.object(hostops, "_git_write") as git_write, patch.object(hostops, "_git") as git_read, \
                patch.object(hostops.Path, "write_text") as write_text, patch.object(hostops.Path, "write_bytes") as write_bytes, \
                patch.object(self.acquisition, "run") as acquire, \
                patch.object(self.acquisition, "enroll_existing_9444") as enroll, \
                patch.object(self.acquisition, "verify_playwright_attach") as attach, \
                patch.object(local_wake_entry, "send_one") as send:
            self.assertEqual(self.observe()["code"], "CDP_PROTOCOL_HEALTHY")
            for forbidden in (run, launch, git_write, git_read, write_text, write_bytes, acquire, enroll, attach, send):
                forbidden.assert_not_called()


if __name__ == "__main__":
    unittest.main()
