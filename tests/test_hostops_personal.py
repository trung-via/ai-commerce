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

    def test_standing_read_public_transport_returns_only_enum_snapshot(self):
        request = hostops.parse_event(event("read", "standing_read_diagnostics", {}), "read")
        with patch.object(hostops, "_standing_read_snapshot", return_value=self._standing_snapshot()) as collect:
            result = hostops.perform(request)
            collect.assert_called_once_with()
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(len(result["diagnostics"]), 16)
        self.assertIn("diagnostics", hostops.PERSONAL_PUBLIC_FIELDS)
        self.assertNotIn("content_utf8", result)

    def test_standing_read_public_sanitizer_vocab_and_fail_closed_contract(self):
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        marker = "if (record.operation === 'standing_read_diagnostics') {"
        self.assertEqual(workflow.count(marker), 1)
        section = workflow.split(marker, 1)[1].split("const expectedKeys =", 1)[0]
        rows = re.findall(r"^\s*\['([^']+)',\[(.*?)\]\],\s*$", section, flags=re.M)
        parsed = [(name, tuple(re.findall(r"'([^']+)'", codes))) for name, codes in rows]
        self.assertEqual(parsed, list(hostops._STANDING_CATEGORY_CODES.items()))
        self.assertIn("Buffer.byteLength(JSON.stringify(record),'utf8') <= 8192", workflow)
        self.assertIn("INVALID_PUBLIC_DIAGNOSTIC_RECEIPT", workflow)
        self.assertIn("record.source_sha === context.sha", workflow)
        self.assertIn("Object.keys(record).length === expectedKeys.length", workflow)
        self.assertIn("record.issue_number === context.issue.number", workflow)
        self.assertIn("sanitized.diagnostics = allowed.map(", workflow)

    def test_standing_read_public_javascript_rejects_malformed_and_private_data(self):
        # Execute the actual embedded github-script with a stub GitHub client
        # when a Node runtime is available. Static contract checks above always run.
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            return
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        source_lines = workflow.splitlines()
        start = source_lines.index("          script: |") + 1
        script_lines = []
        for line in source_lines[start:]:
            if line and not line.startswith("            "):
                break
            script_lines.append(line[12:] if line else "")
        script = "\n".join(script_lines) + "\n"
        harness = """
const fs=require('fs'), path=require('path');
const code=fs.readFileSync(process.argv[1],'utf8');
process.env.RUNNER_TEMP=process.argv[2];
const context={repo:{owner:'trung-via',repo:'ai-commerce'},issue:{number:17},sha:'a'.repeat(40)};
const posts=[];
const github={rest:{issues:{createComment:async x=>posts.push(x.body)}}};
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('github','context','require',code)(github,context,require)
  .then(()=>process.stdout.write(posts[0]||''))
  .catch(e=>{process.stderr.write(String(e));process.exitCode=1});
"""
        valid = {**self._standing_snapshot(),
                 "issue_number": 17, "approval_channel": "CHATGPT_TO_AI_COMMERCE_ISSUE",
                 "remote_shell_enabled": False, "source_sha": "a" * 40}
        cases = [
            (valid, "OBSERVED"),
            ({**valid, "content_utf8": "sensitive-private-data"}, "BLOCKED"),
            ({**valid, "diagnostics": valid["diagnostics"][:-1]}, "BLOCKED"),
            ({**valid, "diagnostics": [{**valid["diagnostics"][0], "path": "SECRET"},
                                      *valid["diagnostics"][1:]]}, "BLOCKED"),
            ({**valid, "issue_number": 18}, "BLOCKED"),
            ({**valid, "source_sha": "b" * 40}, "BLOCKED"),
            ({**valid, "operation": "git_snapshot", "diagnostics": [{"private": "secret"}]},
             "OBSERVED_NO_DIAGNOSTICS"),
            ({**valid, "diagnostics": [{"category": "chrome_profile", "code": "ACTIVE"},
                                      *valid["diagnostics"][1:]]}, "BLOCKED"),
        ]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            js = root / "sanitizer.js"
            js.write_text(script, encoding="utf-8")
            for index, (record, expected) in enumerate(cases):
                (root / "personal-hostops-result.json").write_text(json.dumps(record), encoding="utf-8")
                run = subprocess.run([node, "-e", harness, str(js), str(root)],
                                     capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", timeout=20, check=False)
                with self.subTest(index=index):
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertTrue(run.stdout.startswith("AI_COMMERCE_PERSONAL_HOSTOPS_RESULT_V1\n"))
                    response = json.loads(run.stdout.split("\n", 1)[1])
                    self.assertEqual(response["status"],
                                     "OBSERVED" if expected == "OBSERVED_NO_DIAGNOSTICS" else expected)
                    if expected == "OBSERVED":
                        self.assertEqual(response["diagnostics"], valid["diagnostics"])
                    elif expected == "OBSERVED_NO_DIAGNOSTICS":
                        self.assertNotIn("diagnostics", response)
                        self.assertNotIn("secret", run.stdout)
                    else:
                        self.assertEqual(response["code"], "INVALID_PUBLIC_DIAGNOSTIC_RECEIPT")
                        self.assertNotIn("diagnostics", response)
                        self.assertNotIn("content_utf8", response)
                        self.assertNotIn("sensitive-private-data", run.stdout)

    def test_standing_read_public_fields_match_protected_workflow(self):
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        fields = re.search(r"const publicFields = \[(.*?)\];", workflow).group(1)
        self.assertEqual(hostops.PERSONAL_PUBLIC_FIELDS, set(re.findall(r"'([^']+)'", fields)) | {"runners"})

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

    def test_standing_read_internal_snapshot_never_reads_private_files_or_mutates(self):
        cdp = {"listener_count": 1, "loopback_count": 1, "unexpected_binding_count": 0,
               "chrome_owned_loopback_count": 1, "same_session_chrome_count": 1, "devtools_protocol_ok": True}
        runners = {"runners": [{"task_state": "Running", "listener_process_count": count} for count in (0, 1, 1)]}
        with patch.object(hostops.os, "name", "nt"), patch.object(hostops, "_cdp_9333_status", return_value=cdp), patch.object(
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
        for name in ("chrome_profile", "chrome_config", "chrome_markers", "d5_metadata"):
            self.assertEqual(codes[name], "PRIVATE_READ_CHANNEL_REQUIRED")
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(git.call_args_list[0].args, ("rev-parse", "HEAD"))
        self.assertEqual(git.call_args_list[1].args, ("status", "--porcelain=v1", "--untracked-files=no"))
        self.assertFalse(result["exact_chat_verified"])
        self.assertFalse(result["wake_sent"])

    def test_standing_read_probe_errors_stay_enum_unavailable(self):
        with patch.object(hostops.os, "name", "nt"), patch.object(
            hostops, "_cdp_9333_status", side_effect=hostops.Denied("private exception")
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
            ), patch.object(hostops, "_cdp_9333_status", side_effect=hostops.Denied("unavailable")), patch.object(
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


if __name__ == "__main__":
    unittest.main()
