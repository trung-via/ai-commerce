"""Portable source tests; live effect requires existing AI Commerce Windows runner."""
import importlib.util
import json
from pathlib import Path
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
    def test_inventory_read_auto_from_owner_chat_issue(self):
        request = hostops.parse_event(event("read", "inventory", {}), "read")
        self.assertEqual(request["operation"], "inventory")
        output = hostops.perform(request)
        self.assertEqual(output["status"], "OBSERVED")

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


if __name__ == "__main__":
    unittest.main()
