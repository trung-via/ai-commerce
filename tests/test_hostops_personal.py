"""Portable source tests; live effect requires existing AI Commerce Windows runner."""
import importlib.util
import json
from pathlib import Path
import re
from unittest.mock import patch
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



if __name__ == "__main__":
    unittest.main()
