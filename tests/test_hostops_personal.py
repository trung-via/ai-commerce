"""Portable source tests; live effect requires existing AI Commerce Windows runner."""
import asyncio
import base64
import builtins
import importlib.util
import copy
import ctypes
from contextlib import ExitStack, contextmanager, nullcontext, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
from io import BytesIO
from itertools import count
import json
from pathlib import Path, PureWindowsPath
import re
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, mock_open, patch
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from ai_commerce.remote import chrome_acquisition as acquisition

MODULE = Path(__file__).resolve().parents[1] / "src" / "ai_commerce" / "hostops_v3" / "personal.py"
TEST_PWSH = r"C:\Program Files\PowerShell\7.7\pwsh.exe"
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


class A10HostReadinessTests(unittest.TestCase):
    OP = "a10_codex_native_readiness"
    SCHEMA = "A10CodexHostReadiness.v1"
    SHA = "a" * 40
    ROOT_HELP = b"""Codex CLI

Usage: codex [OPTIONS] [PROMPT]
       codex [OPTIONS] <COMMAND> [ARGS]

Commands:
  exec  Run Codex non-interactively

Options:
  -c, --config <key=value>
          Override a configuration value
      --disable <FEATURE>
          Disable a feature
  -h, --help
"""
    EXEC_HELP = b"""Run Codex non-interactively

Usage: codex exec [OPTIONS] [PROMPT] [COMMAND]

Options:
  -c, --config <key=value>
          Private example: --ignore-user-config user@example.test
      --disable <FEATURE>
          Disable a feature
  -m, --model <MODEL>
  -s, --sandbox <SANDBOX_MODE>
      --json
      --output-schema <FILE>
      --skip-git-repo-check
  -h, --help
"""

    def _protected_env(self):
        return {
            "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "issues", "GITHUB_REPOSITORY": hostops.REPO,
            "GITHUB_REPOSITORY_OWNER": hostops.OWNER, "GITHUB_ACTOR": hostops.OWNER,
            "GITHUB_TRIGGERING_ACTOR": hostops.OWNER, "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKFLOW_REF": hostops.REPO + "/.github/workflows/issue-ingress.yml@refs/heads/main",
            "RUNNER_ENVIRONMENT": "self-hosted", "RUNNER_NAME": "AI-COMMERCE-HOSTOPS-01",
            "GITHUB_SHA": self.SHA, "GITHUB_WORKFLOW_SHA": self.SHA,
            "HOSTOPS_A10_READINESS_DISPATCH": self.SCHEMA,
        }

    def _main(self, value, env, mode="read", *, os_name="nt"):
        raw = value if isinstance(value, str) else json.dumps(value)
        with (patch.object(hostops, "os", SimpleNamespace(name=os_name, environ={
                  **env, "GITHUB_EVENT_PATH": "fake-event.json"})),
              patch.object(hostops.sys, "argv", ["personal", "--mode", mode, "--output", "fake-result.json"]),
              patch.object(hostops, "open", mock_open(read_data=raw), create=True),
              patch.object(hostops.Path, "mkdir"), patch.object(hostops.Path, "write_text") as write,
              patch.object(hostops.subprocess, "Popen", side_effect=AssertionError("offline")) as spawn,
              patch.object(hostops.subprocess, "run", side_effect=AssertionError("offline")) as run):
            code = hostops.main()
        spawn.assert_not_called()
        run.assert_not_called()
        return code, json.loads(write.call_args.args[0])

    def test_malformed_owner_and_parameter_checks_precede_host_collection(self):
        wrong_sender = event("read", self.OP, {})
        wrong_sender["sender"]["login"] = "other"
        wrong_owner = event("read", self.OP, {})
        wrong_owner["issue"]["user"]["login"] = "other"
        bad_body = event("read", self.OP, {})
        bad_body["issue"]["body"] = '{"parameters":{},"parameters":{}}'
        cases = ["{", bad_body, wrong_owner, wrong_sender, event("read", self.OP, [])]
        cases.extend(event("read", self.OP, {key: "private"}) for key in (
            "executable", "path", "command", "model", "effort", "home", "env", "url", "args", "shell", "output",
        ))
        with (patch.object(hostops, "_a10_collect_r7_internal_evidence") as collect_r7,
              patch.object(hostops, "_a10_collect_readiness") as collect_legacy):
            for value in cases:
                with self.subTest(value=value):
                    code, result = self._main(value, self._protected_env())
                    self.assertEqual((code, result["status"]), (2, "BLOCKED"))
                    self.assertNotIn("a10_readiness", result)
            code, result = self._main(event("write", self.OP, {}), self._protected_env(), mode="write")
            self.assertEqual(result["code"], "WRITE_SCOPE_DENIED")
        collect_r7.assert_not_called()
        collect_legacy.assert_not_called()

    def test_guard_and_direct_perform_keep_source_only(self):
        value = event("read", self.OP, {})
        with (patch.object(hostops, "_a10_collect_r7_internal_evidence") as collect_r7,
              patch.object(hostops, "_a10_collect_readiness") as collect_legacy):
            result = hostops.perform(hostops.parse_event(value, "read"))
            self.assertEqual(result["a10_readiness"]["state"], "SOURCE_PRESENT")
            self.assertIsNone(result["a10_readiness"]["public_observation_sha256"])
            for key, update in (
                ("HOSTOPS_A10_READINESS_DISPATCH", ""), ("GITHUB_WORKFLOW_SHA", "b" * 40),
                ("GITHUB_REF", "refs/heads/feature"), ("GITHUB_RUN_ATTEMPT", "2"),
                ("RUNNER_NAME", "AI-COMMERCE-EF02-02"), ("RUNNER_ENVIRONMENT", "github-hosted"),
                ("PYTEST_CURRENT_TEST", "offline"),
                ("AI_COMMERCE_GIT_GUARD_ACTIVE", "1"),
            ):
                with self.subTest(key=key):
                    code, record = self._main(value, {**self._protected_env(), key: update})
                    self.assertEqual((code, record["code"]), (2, "A10_PROTECTED_WORKFLOW_REQUIRED"))
                    self.assertEqual(record["a10_readiness"]["state"], "SOURCE_PRESENT")
            code, record = self._main(value, self._protected_env(), os_name="posix")
            self.assertEqual((code, record["code"]), (2, "WINDOWS_ONLY"))
        collect_r7.assert_not_called()
        collect_legacy.assert_not_called()
        self.assertNotIn("a10_readiness", hostops.PERSONAL_PUBLIC_FIELDS)
        self.assertEqual(len(hostops.STANDING_READ_CATEGORIES), 16)

    def test_discovery_addresses_only_bounded_local_native_candidates(self):
        app_data = r"C:\Users\operator\AppData\Roaming"
        candidates = hostops._a10_install_candidates({
            "PATH": r'.;\\server\share;\\?\C:\private;C:\x\..\private;C:\private:stream;'
                    r'C:\Tools;"c:\TOOLS";C:\Users\operator\AppData\Local\Microsoft\WindowsApps',
            "APPDATA": app_data,
        })
        self.assertEqual(len(candidates), 14)
        self.assertEqual(candidates[:2], (PureWindowsPath("C:/Tools/codex.exe"), PureWindowsPath(
            "C:/Users/operator/AppData/Local/Microsoft/WindowsApps/codex.exe"
        )))
        self.assertIn(PureWindowsPath(
            "C:/Users/operator/AppData/Roaming/npm/node_modules/@openai/codex-win32-x64/"
            "vendor/x86_64-pc-windows-msvc/codex/codex.exe"
        ), candidates)
        for path in ("C:/Tools;" * 64 + "C:/Omitted", "C:/Tools;C:/" + "x" * 16384):
            with self.subTest(length=len(path)):
                bounded = hostops._a10_install_candidates({"PATH": path, "APPDATA": app_data})
                self.assertEqual(bounded, (candidates[0], *candidates[2:]))

    @staticmethod
    def _binary(path, raw=b"fake executable bytes", *, size=None):
        executable = MagicMock(spec=Path)
        executable.__str__.return_value = str(path)
        executable.name = path.name
        executable.stat.return_value = SimpleNamespace(
            st_mode=stat.S_IFREG, st_size=len(raw) if size is None else size,
        )
        executable.is_file.return_value = True
        executable.open.return_value = BytesIO(raw)
        return executable

    def test_rejected_and_unreadable_candidates_do_not_block_later_native_binary(self):
        candidates = tuple(map(PureWindowsPath, (
            "C:/early/codex.exe", "C:/valid/codex.exe", "C:/later/codex.exe",
        )))
        for early_is_native in (False, True):
            early, valid = [self._binary(path) for path in candidates[:2]]
            early.open.side_effect = PermissionError("private path")
            with (self.subTest(early_is_native=early_is_native),
                  patch.object(hostops, "Path", side_effect=[
                      Mock(resolve=Mock(return_value=executable)) for executable in (early, valid)
                  ]) as paths,
                  patch.object(hostops, "_a10_is_native_executable", side_effect=[early_is_native, True])):
                self.assertEqual(hostops._a10_find_executable(candidates), (
                    "OBSERVED", valid, hashlib.sha256(b"fake executable bytes").hexdigest(),
                ))
            self.assertEqual(paths.call_args_list, [call(candidate) for candidate in candidates[:2]])
            self.assertEqual(early.open.call_count, int(early_is_native))
            valid.open.assert_called_once_with("rb")

    def test_absent_inaccessible_oversized_and_growing_candidates_stay_categorical(self):
        from ai_commerce.opportunity.core import models

        candidates = tuple(map(PureWindowsPath, ("C:/one/codex.exe", "C:/two/codex.exe")))
        oversized = self._binary(candidates[0], size=513)
        growing = self._binary(candidates[1], raw=b"x" * 513, size=512)
        outside_scope = self._binary(PureWindowsPath("C:/profile/.codex/auth.json"))
        for outcome, expected, classification in (
            (FileNotFoundError, "NOT_FOUND_IN_SCOPE", []),
            (PermissionError, "UNSAFE", []),
            (outside_scope, "UNSAFE", []),
            (oversized, "UNSAFE", []),
            (growing, "UNSAFE", [call(growing)]),
        ):
            resolved = Mock(side_effect=[FileNotFoundError, outcome])
            with (self.subTest(outcome=outcome),
                  patch.object(hostops, "_a10_install_candidates", return_value=candidates),
                  patch.object(hostops, "Path", return_value=Mock(resolve=resolved)),
                  patch.object(hostops, "_a10_is_native_executable", return_value=True) as native,
                  patch.object(models, "AI_MAX_BYTES", 1),
                  patch.object(hostops, "_a10_isolated_cli") as launch):
                self.assertEqual(hostops._a10_collect_readiness(), {"binary": expected})
            self.assertEqual(native.call_args_list, classification)
            launch.assert_not_called()
        outside_scope.open.assert_not_called()
        oversized.open.assert_not_called()
        growing.open.assert_called_once_with("rb")

    def test_cache_attribute_pin_checks_fixed_drive_reparse_and_link_count(self):
        candidate = PureWindowsPath("C:/.codex/models_cache.json")
        directory = (stat.FILE_ATTRIBUTE_DIRECTORY, 1)
        reparse = (stat.FILE_ATTRIBUTE_DIRECTORY | stat.FILE_ATTRIBUTE_REPARSE_POINT, 1)
        for drive, metadata, expected_opens in (
            (4, [], 0),
            (3, [directory, reparse], 2),
            (3, [directory, directory, (0, 2)], 3),
        ):
            def file_information(handle, pointer):
                pointer.contents.attributes, pointer.contents.links = metadata[handle - 1]
                return True

            kernel = Mock()
            kernel.GetDriveTypeW.return_value = drive
            kernel.CreateFileW.side_effect = [1, 2, 3]
            kernel.GetFileInformationByHandle.side_effect = file_information
            with (self.subTest(drive=drive, expected_opens=expected_opens),
                  patch.object(ctypes, "WinDLL", return_value=kernel, create=True),
                  self.assertRaises(OSError), hostops._a10_pin_local_path(candidate)):
                self.fail("unsafe cache path was pinned")
            self.assertEqual(kernel.CreateFileW.call_count, expected_opens)
            self.assertEqual(kernel.CloseHandle.call_count, expected_opens)
            for opened in kernel.CreateFileW.call_args_list:
                self.assertEqual(opened.args[2], 1)  # FILE_SHARE_READ

    def test_filesystem_alias_is_hashed_without_strict_drive_or_ancestor_checks(self):
        raw = b"fake executable bytes"
        digest = hashlib.sha256(raw).hexdigest()
        alias = PureWindowsPath("C:/Tools/alias/codex.exe")
        destination = PureWindowsPath("D:/Codex/codex.exe")
        executable = self._binary(destination, raw)
        profile = PureWindowsPath("C:/Users/operator")
        resolve = Mock(return_value=executable)
        with (patch.object(hostops, "os", SimpleNamespace(environ={"USERPROFILE": str(profile)})),
              patch.object(hostops, "_a10_install_candidates", return_value=(alias, destination)),
              patch.object(hostops, "Path", return_value=Mock(resolve=resolve)) as paths,
              patch.object(hostops, "_a10_is_native_executable", return_value=True) as native,
              patch.object(hostops, "_a10_pin_local_path", side_effect=OSError) as pin,
              patch.object(hostops, "_a10_isolated_cli", return_value=nullcontext(
                  Mock(return_value=self._output(b""))
              )) as launch):
            observed = hostops._a10_collect_readiness()
        self.assertEqual(observed, {
            "binary": "OBSERVED", "observed_binary_sha256": digest,
            "catalog": "UNKNOWN",
        })
        paths.assert_called_once_with(alias)
        resolve.assert_called_once_with(strict=True)
        native.assert_called_once_with(executable)
        executable.open.assert_called_once_with("rb")
        pin.assert_called_once_with(profile / ".codex/models_cache.json")
        launch.assert_called_once_with(executable, digest)

    @staticmethod
    def _output(stdout, *, returncode=0, stderr=b""):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_version_root_and_exec_help_have_distinct_observation_surfaces(self):
        version = self._output(b"codex-cli 0.164.0\r\n")
        root_help = self._output(self.ROOT_HELP)
        exec_help = self._output(self.EXEC_HELP)
        actual = {}
        run = Mock(side_effect=[version, root_help, exec_help])
        hostops._a10_observe_cli(run, actual)
        self.assertEqual(run.call_args_list, [call(("--version",)), call(("--help",)), call(("exec", "--help"))])
        self.assertEqual(actual, {
            "version": "MATCHES_SOURCE_VERSION", "root_help": "USAGE_OBSERVED", "root_flags": "DECLARED",
            "exec_help": "USAGE_OBSERVED", "exec_flags": "NOT_DECLARED",
            "config_override_flags": "NOT_DECLARED", "feature_disable_flag": "DECLARED",
        })
        self.assertNotIn("user@example.test", json.dumps(actual))
        absent_from_exec = self.EXEC_HELP.replace(b"      --disable <FEATURE>", b"          --disable <FEATURE>")
        actual = {}
        hostops._a10_observe_cli(Mock(side_effect=[version, root_help, self._output(absent_from_exec)]), actual)
        self.assertEqual(actual["feature_disable_flag"], "NOT_DECLARED")
        actual = {}
        hostops._a10_observe_cli(Mock(side_effect=[version, exec_help, root_help]), actual)
        self.assertEqual(actual, {"version": "MATCHES_SOURCE_VERSION"})
        actual = {}
        hostops._a10_observe_cli(Mock(side_effect=[
            version, subprocess.TimeoutExpired("root help", 5), exec_help,
        ]), actual)
        self.assertNotIn("root_help", actual)
        self.assertEqual(actual["exec_help"], "USAGE_OBSERVED")

    def test_invalid_version_output_is_discarded_without_launching_help(self):
        for response in (
            subprocess.TimeoutExpired("private command", 5), self._output(b"\xffsecret"),
            self._output(b"codex-cli private version"),
            self._output(b"codex-cli 0.1.0", returncode=3),
            self._output(b"codex-cli 0.1.0", stderr=b"private error"),
            self._output(b"codex-cli 0.1.0", stderr=b"warning: diagnostic\r\nprivate error"),
            self._output(b"codex-cli 0.1.0", stderr=b"warning: " + b"x" * hostops._A10_PROBE_MAX_BYTES),
            self._output(b"codex-cli private version", stderr=b"warning: diagnostic"),
            self._output(b"x" * (hostops._A10_PROBE_MAX_BYTES + 1)),
        ):
            with self.subTest(response=type(response)):
                actual = {}
                run = Mock(side_effect=[response])
                hostops._a10_observe_cli(run, actual)
                self.assertEqual(actual, {})
                run.assert_called_once_with(("--version",))
    def test_protected_owner_read_launches_fixed_metadata_without_sha_or_temp_pin(self):
        from ai_commerce.opportunity.core import models

        raw = b"observed fixture"
        digest = hashlib.sha256(raw).hexdigest()
        executable = self._binary(PureWindowsPath("D:/Codex/codex.exe"), raw)
        executable.resolve.return_value = executable
        executable.open.side_effect = lambda _: BytesIO(raw)
        temp_home = r"C:\Temp\a10-isolated"
        options = {"input": b"", "cwd": temp_home, "codex_home": temp_home, "shell": False,
                   "timeout": 5, "max_bytes": 32768}
        expected_calls = [call([str(executable), *args], **options) for args in (
            ("--version",), ("--help",), ("exec", "--help"),
        )]
        exec_help = self.EXEC_HELP.replace(
            b"      --json", b"      --ignore-user-config\n      --strict-config\n      --json"
        )
        for version, version_code in (
            ("0.164.0", "MATCHES_SOURCE_VERSION"),
            ("0.170.0", "DIFFERS_FROM_SOURCE_VERSION"),
        ):
            stderr = (
                b"2026-11-21T00:00:00Z WARN codex_cli: private warning detail\r\n"
                b"warning: other private warning detail\r\n"
                if version_code == "DIFFERS_FROM_SOURCE_VERSION" else b""
            )
            executable.reset_mock()
            with (self.subTest(version=version),
                  patch.object(hostops, "_a10_find_executable", return_value=("OBSERVED", executable, digest)),
                  patch.object(hostops, "_a10_is_native_executable", return_value=True) as native,
                  patch.object(hostops, "_a10_pin_local_path") as pin,
                  patch.object(hostops.tempfile, "TemporaryDirectory", return_value=nullcontext(temp_home)) as temp,
                  patch.object(models, "_bounded_native_codex", side_effect=[
                      self._output(f"codex-cli {version}".encode(), stderr=stderr),
                      self._output(self.ROOT_HELP, stderr=stderr), self._output(exec_help, stderr=stderr),
                  ]) as runner):
                code, result = self._main(event("read", self.OP, {}), self._protected_env())
                temp.assert_called_once_with(prefix="a10-readiness-")
                with hostops._a10_isolated_cli(executable, digest) as probe:
                    for args in (("exec",), ("exec", "--help", "prompt"), ("login", "status"), ("features", "list")):
                        with self.assertRaisesRegex(hostops.Denied, "A10_PROBE_DENIED"):
                            probe(args)
                self.assertEqual(runner.call_args_list, expected_calls)
                self.assertEqual(executable.open.call_args_list, [call("rb")] * 4)
                self.assertEqual(native.call_args_list, [call(executable)] * 4)
                pin.assert_not_called()
                self.assertEqual((code, result["status"], result["code"]), (2, "HOLD", "HOST_UNVERIFIED"))
                readiness = result["a10_readiness"]
                self.assertEqual(readiness["state"], "HOST_UNVERIFIED")
                self.assertEqual(readiness["version"], version_code)
                self.assertEqual(readiness["observed_binary_sha256"], digest)
                self.assertEqual(readiness["root_help"], "USAGE_OBSERVED")
                self.assertEqual(readiness["root_flags"], "DECLARED")
                self.assertEqual(readiness["exec_help"], "USAGE_OBSERVED")
                self.assertEqual(readiness["exec_flags"], "DECLARED")
                self.assertEqual(readiness["config_override_flags"], "DECLARED")
                self.assertEqual(readiness["feature_disable_flag"], "DECLARED")
                for field in ("system_config", "account_config", "disabled_features", "read_only_policy"):
                    self.assertEqual(readiness[field], "UNKNOWN")
                self.assertFalse(readiness["native_inference_executed"])
                public = json.dumps(result)
                for private in (str(executable), temp_home, "user@example.test", "Codex CLI", version,
                                "private warning detail"):
                    self.assertNotIn(json.dumps(private)[1:-1], public)

    def test_missing_replaced_or_mutated_binary_stops_probes_and_preserves_passive_hash(self):
        from ai_commerce.opportunity.core import models

        raw = b"discovered binary"
        digest = hashlib.sha256(raw).hexdigest()
        executable = self._binary(PureWindowsPath("C:/Tools/codex.exe"))
        alternative = self._binary(PureWindowsPath("C:/Other/alternative.exe"))
        version = self._output(b"codex-cli 0.164.0")
        for case, resolved, native, contents, version_observed in (
            ("missing", FileNotFoundError(), True, (), False),
            ("redirected", alternative, True, (), False),
            ("non-native", executable, False, (), False),
            ("changed", executable, True, (b"changed",), False),
            ("changed after version", executable, True, (raw, b"changed"), True),
        ):
            with (self.subTest(case=case),
                  patch.object(hostops, "os", SimpleNamespace(environ={})),
                  patch.object(hostops, "_a10_find_executable", return_value=("OBSERVED", executable, digest)),
                  patch.object(hostops, "_a10_is_native_executable", return_value=native),
                  patch.object(hostops.tempfile, "TemporaryDirectory", return_value=nullcontext("fake-temp")),
                  patch.object(executable, "resolve", side_effect=[resolved] * 2),
                  patch.object(executable, "open", side_effect=[BytesIO(data) for data in contents]),
                  patch.object(models, "_bounded_native_codex", return_value=version) as launch):
                self.assertEqual(hostops._a10_collect_readiness(), {
                    "binary": "OBSERVED", "observed_binary_sha256": digest, "catalog": "UNKNOWN",
                    **({"version": "MATCHES_SOURCE_VERSION"} if version_observed else {}),
                })
                self.assertEqual([invocation.args[0][1:] for invocation in launch.call_args_list],
                                 [["--version"]] if version_observed else [])

    def test_public_receipt_is_closed_and_digest_binds_observed_binary_issue_and_source(self):
        from ai_commerce.opportunity.core import models

        binary_sha = hashlib.sha256(b"observed executable").hexdigest()
        observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        evidence = models.make_codex_effective_config_evidence(
            source_sha=self.SHA,
            observed_at=observed_at,
            binary={"state": "OBSERVED", "sha256": binary_sha, "version_status": "UNKNOWN",
                    "version": None, "help_sha256": None},
            cli_declarations=dict.fromkeys(
                ("root_help", "root_flags", "exec_help", "exec_flags",
                 "config_override_flags", "feature_disable_flag"),
                "UNKNOWN",
            ),
            system_config={"status": "UNKNOWN", "basis": "NOT_CHECKED", "known_paths_checked": 0},
            account_config={"status": "UNKNOWN", "basis": "REMOTE_ACCOUNT_POLICY_UNPROVABLE_OFFLINE"},
            windows_boundary=True,
        )
        with (patch.object(hostops, "_a10_collect_r7_internal_evidence", return_value=evidence) as collect,
              patch.object(hostops, "_a10_collect_readiness") as legacy_collect):
            code, result = self._main(event("read", self.OP, {}), self._protected_env())
        collect.assert_called_once_with(self.SHA)
        legacy_collect.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(result, {
            "status": "HOLD", "code": "HOST_UNVERIFIED", "operation": self.OP,
            "issue_number": 17, "source_sha": self.SHA, "approval_channel": "CHATGPT_TO_AI_COMMERCE_ISSUE",
            "remote_shell_enabled": False, "a10_readiness": result["a10_readiness"],
        })
        readiness = result["a10_readiness"]
        digest = readiness.pop("public_observation_sha256")
        self.assertEqual(readiness, {
            "schema_version": self.SCHEMA, "state": "HOST_UNVERIFIED", "binary": "OBSERVED",
            "observed_binary_sha256": binary_sha, "native_inference_executed": False,
            **dict.fromkeys(("version", "root_help", "exec_help", "root_flags", "exec_flags",
                             "config_override_flags", "feature_disable_flag", "catalog", "system_config",
                             "account_config", "disabled_features", "read_only_policy"), "UNKNOWN"),
        })
        canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("ascii")
        self.assertEqual(digest, hashlib.sha256(canonical).hexdigest())
        observed = {"binary": "OBSERVED", "observed_binary_sha256": binary_sha, "account_config": "secret",
                    "stdout": "private", "host_verification_sha256": "b" * 64}
        stripped = hostops._a10_readiness_receipt(17, self.SHA, observations=observed)
        self.assertEqual(stripped["a10_readiness"]["public_observation_sha256"], digest)
        self.assertEqual(stripped["a10_readiness"]["account_config"], "UNKNOWN")
        for issue, sha, bytes_sha in ((18, self.SHA, binary_sha), (17, "b" * 40, binary_sha),
                                      (17, self.SHA, "c" * 64)):
            changed = hostops._a10_readiness_receipt(
                issue, sha, observations={**observed, "observed_binary_sha256": bytes_sha}
            )
            self.assertNotEqual(changed["a10_readiness"]["public_observation_sha256"], digest)
        hold = hostops._a10_readiness_receipt(
            17, self.SHA, observations={**observed, "version": "DIFFERS_FROM_SOURCE_VERSION"}
        )
        self.assertEqual((hold["status"], hold["code"]), ("HOLD", "HOST_UNVERIFIED"))

    def test_r7_system_config_probe_metadata_only_and_fails_closed(self):
        with patch.object(hostops, "_a10_pin_local_path", side_effect=FileNotFoundError) as pin:
            result = hostops._a10_system_config_probe({"ProgramData": r"C:\ProgramData"})
        self.assertEqual(result, {
            "status": "UNKNOWN", "basis": "KNOWN_SYSTEM_PATHS_ABSENT_NOT_EXHAUSTIVE", "known_paths_checked": 3,
        })
        self.assertEqual(pin.call_args_list, [
            call(PureWindowsPath(r"C:\ProgramData\OpenAI\Codex\config.toml")),
            call(PureWindowsPath(r"C:\ProgramData\OpenAI\Codex\managed_config.toml")),
            call(PureWindowsPath(r"C:\ProgramData\OpenAI\Codex\requirements.toml")),
        ])

        with (patch.object(hostops, "_a10_pin_local_path",
                           side_effect=[FileNotFoundError, FileNotFoundError, nullcontext(Path("unused"))]),
              patch.object(Path, "read_bytes", side_effect=AssertionError("must not read policy bytes"))):
            conflict = hostops._a10_system_config_probe({"ProgramData": r"C:\ProgramData"})
        self.assertEqual(conflict, {
            "status": "CONFLICTING_POLICY_PRESENT", "basis": "KNOWN_SYSTEM_POLICY_FILE_PRESENT",
            "known_paths_checked": 3,
        })

        with patch.object(hostops, "_a10_pin_local_path", side_effect=[FileNotFoundError, OSError("reparse")]):
            unsafe = hostops._a10_system_config_probe({"ProgramData": r"C:\ProgramData"})
        self.assertEqual(unsafe, {"status": "UNSAFE", "basis": "SYSTEM_PATH_UNSAFE", "known_paths_checked": 2})

        for bad in ({}, {"ProgramData": r"\\server\share"}, {"ProgramData": "relative"},
                    {"ProgramData": r"C:\ProgramData:stream"}, {"ProgramData": r"C:\CON"}):
            with self.subTest(bad=bad), patch.object(hostops, "_a10_pin_local_path") as pin:
                self.assertEqual(hostops._a10_system_config_probe(bad), {
                    "status": "UNKNOWN", "basis": "NOT_CHECKED", "known_paths_checked": 0,
                })
                pin.assert_not_called()

    def test_r7_internal_evidence_dynamic_version_drift_conflicts_and_public_downgrade(self):
        from ai_commerce.opportunity.core import models

        raw = b"observed r7 binary"
        binary_sha = hashlib.sha256(raw).hexdigest()
        full_exec_help = self.EXEC_HELP.replace(
            b"      --json", b"      --ignore-user-config\n      --strict-config\n      --json"
        )
        as_of = "2026-11-21T08:00:00Z"

        with self.assertRaisesRegex(hostops.Denied, "PROTECTED_HOSTOPS_ONLY"):
            hostops._a10_collect_r7_internal_evidence("not-a-sha", as_of=as_of)

        for version in ("0.164.0", "0.170.0", "2.3.4"):
            executable = self._binary(PureWindowsPath("D:/Codex/codex.exe"), raw)
            executable.resolve.return_value = executable
            executable.open.side_effect = lambda _: BytesIO(raw)
            with (self.subTest(version=version),
                  patch.object(hostops, "os", SimpleNamespace(name="nt", environ={"ProgramData": r"C:\ProgramData"})),
                  patch.object(hostops, "_a10_find_executable", return_value=("OBSERVED", executable, binary_sha)),
                  patch.object(hostops, "_a10_is_native_executable", return_value=True),
                  patch.object(hostops, "_a10_pin_local_path", side_effect=FileNotFoundError),
                  patch.object(hostops.tempfile, "TemporaryDirectory", return_value=nullcontext("fake-temp")),
                  patch.object(models, "_bounded_native_codex", side_effect=[
                      self._output(f"codex-cli {version}".encode()),
                      self._output(self.ROOT_HELP),
                      self._output(full_exec_help),
                  ])):
                ev = hostops._a10_collect_r7_internal_evidence(self.SHA, as_of=as_of)
            self.assertEqual(ev["readiness_verdict"], "HOLD_UNVERIFIED")
            self.assertEqual(ev["binary"], {
                "state": "OBSERVED", "sha256": binary_sha, "version_status": "STABLE_SEMVER",
                "version": version, "help_sha256": hashlib.sha256(full_exec_help).hexdigest(),
            })
            self.assertFalse(ev["native_inference_executed"])
            self.assertEqual(ev["layers"]["system_config"]["status"], "UNKNOWN")
            self.assertEqual(ev["layers"]["account_config"]["status"], "UNKNOWN")
            self.assertEqual(ev["layers"]["disabled_features"], {
                "status": "UNKNOWN", "basis": "HELP_DECLARATION_NOT_RUNTIME_PROOF",
            })
            self.assertEqual(ev["layers"]["read_only_policy"], {
                "status": "UNKNOWN", "basis": "HELP_DECLARATION_NOT_OS_ENFORCEMENT",
            })
            models.verify_codex_effective_config_binding(
                ev, source_sha=self.SHA, as_of="2026-11-21T08:10:00Z",
                binary_sha256=binary_sha, version=version, help_sha256=hashlib.sha256(full_exec_help).hexdigest(),
            )
            with self.assertRaisesRegex(models.SignalValidationError, "HOLD_DEPENDENCY_NATIVE_CODEX_EFFECTIVE_CONFIG_UNVERIFIED"):
                models.require_codex_effective_config_proven(ev)

            projected = hostops._a10_project_public_observations(ev, catalog="DEFAULT_CACHE_PRESENT")
            receipt = hostops._a10_readiness_receipt(17, self.SHA, observations=projected)
            self.assertEqual(receipt["a10_readiness"]["state"], "HOST_UNVERIFIED")
            self.assertEqual(receipt["a10_readiness"]["version"],
                             "MATCHES_SOURCE_VERSION" if version == "0.164.0" else "DIFFERS_FROM_SOURCE_VERSION")
            for layer in ("system_config", "account_config", "disabled_features", "read_only_policy"):
                self.assertEqual(receipt["a10_readiness"][layer], "UNKNOWN")
            self.assertNotIn(version, json.dumps(receipt))

        # Prerelease, missing flags, conflicting system policy, post-probe binary drift, and non-Windows boundary
        for case in ("prerelease", "missing-flags", "system-conflict", "post-probe-drift", "non-windows"):
            executable = self._binary(PureWindowsPath("D:/Codex/codex.exe"), raw)
            executable.resolve.return_value = executable
            if case == "post-probe-drift":
                executable.open.side_effect = [BytesIO(raw), BytesIO(raw), BytesIO(raw), BytesIO(b"swapped-binary")]
            else:
                executable.open.side_effect = lambda _: BytesIO(raw)
            ver_out = b"codex-cli 0.170.0-rc.1" if case == "prerelease" else b"codex-cli 0.170.0"
            help_out = self.EXEC_HELP if case == "missing-flags" else full_exec_help
            pin_effect = nullcontext(Path("unused")) if case == "system-conflict" else FileNotFoundError
            os_name = "posix" if case == "non-windows" else "nt"
            with (self.subTest(case=case),
                  patch.object(hostops, "os", SimpleNamespace(name=os_name, environ={"ProgramData": r"C:\ProgramData"})),
                  patch.object(hostops, "_a10_find_executable", return_value=("OBSERVED", executable, binary_sha)),
                  patch.object(hostops, "_a10_is_native_executable", return_value=True),
                  patch.object(hostops, "_a10_pin_local_path", side_effect=[pin_effect, FileNotFoundError, FileNotFoundError]),
                  patch.object(hostops.tempfile, "TemporaryDirectory", return_value=nullcontext("fake-temp")),
                  patch.object(models, "_bounded_native_codex", side_effect=[
                      self._output(ver_out), self._output(self.ROOT_HELP), self._output(help_out),
                  ])):
                ev = hostops._a10_collect_r7_internal_evidence(self.SHA, as_of=as_of)
            if case == "prerelease":
                self.assertEqual(ev["binary"]["version_status"], "PRERELEASE_UNSUPPORTED")
                self.assertIsNone(ev["binary"]["version"])
                self.assertEqual(ev["readiness_verdict"], "HOLD_UNSUPPORTED")
            elif case == "missing-flags":
                self.assertEqual(ev["cli_declarations"]["exec_flags"], "NOT_DECLARED")
                self.assertEqual(ev["layers"]["read_only_policy"], {
                    "status": "NOT_DECLARED", "basis": "SANDBOX_OR_CONFIG_FLAGS_NOT_DECLARED",
                })
                self.assertEqual(ev["readiness_verdict"], "HOLD_UNSUPPORTED")
            elif case == "system-conflict":
                self.assertEqual(ev["layers"]["system_config"]["status"], "CONFLICTING_POLICY_PRESENT")
                self.assertEqual(ev["readiness_verdict"], "HOLD_CONFLICT")
            elif case == "post-probe-drift":
                self.assertEqual(ev["binary"]["state"], "UNSAFE")
                self.assertIsNone(ev["binary"]["sha256"])
                self.assertTrue(all(v == "UNKNOWN" for v in ev["cli_declarations"].values()))
                self.assertEqual(ev["readiness_verdict"], "HOLD_CONFLICT")
            else:
                self.assertEqual(ev["layers"]["read_only_policy"], {
                    "status": "UNSAFE", "basis": "NON_WINDOWS_BOUNDARY",
                })
                self.assertEqual(ev["readiness_verdict"], "HOLD_CONFLICT")

    def test_r7_protected_a10_sanitizer_compatibility_and_negative_rejection(self):
        """Execute actual .github/workflows/issue-ingress.yml A10 JS sanitizer offline via Node."""
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
        script = "\n".join(line[12:] if line.startswith("            ") else line
                           for line in workflow.split("          script: |\n", 1)[1].splitlines())
        encode = lambda x: json.dumps(x, ensure_ascii=True, separators=(",", ":"))

        def rehash_public(rec):
            copy = copy_mod.deepcopy(rec)
            copy["a10_readiness"].pop("public_observation_sha256", None)
            digest = hashlib.sha256(json.dumps(copy, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
            copy["a10_readiness"]["public_observation_sha256"] = digest
            return copy

        copy_mod = copy
        sha = "f" * 40
        bin_sha = "b" * 64
        r1251_obs = {
            "binary": "OBSERVED", "observed_binary_sha256": bin_sha,
            "version": "DIFFERS_FROM_SOURCE_VERSION", "root_help": "USAGE_OBSERVED",
            "root_flags": "DECLARED", "exec_help": "USAGE_OBSERVED", "exec_flags": "DECLARED",
            "config_override_flags": "DECLARED", "feature_disable_flag": "DECLARED",
            "catalog": "DEFAULT_CACHE_PRESENT",
        }
        r1251 = hostops._a10_readiness_receipt(17, sha, observations=r1251_obs)
        matched = hostops._a10_readiness_receipt(17, sha, observations={**r1251_obs, "version": "MATCHES_SOURCE_VERSION"})
        absent = hostops._a10_readiness_receipt(17, sha, observations={"binary": "NOT_FOUND_IN_SCOPE"})
        source_only = hostops._a10_readiness_receipt(17, sha)
        base_ctx = {
            "eventName": "issues",
            "repo": {"owner": "trung-via", "repo": "ai-commerce"},
            "issue": {"number": 17},
            "sha": sha,
            "payload": {
                "action": "opened",
                "sender": {"login": "trung-via"},
                "issue": {
                    "user": {"login": "trung-via"},
                    "title": "[HOSTOPS READ] A10-O2b R7a readiness",
                    "body": encode({"schema": hostops.SCHEMA, "operation": self.OP, "parameters": {}}),
                },
            },
        }
        cases = [
            {"name": "r1251_compat", "body": encode(r1251), "ctx": base_ctx,
             "status": "HOLD", "code": "HOST_UNVERIFIED", "expected": r1251},
            {"name": "matches_version", "body": encode(matched), "ctx": base_ctx,
             "status": "HOLD", "code": "HOST_UNVERIFIED", "expected": matched},
            {"name": "binary_absent", "body": encode(absent), "ctx": base_ctx,
             "status": "HOLD", "code": "HOST_UNVERIFIED", "expected": absent},
            {"name": "source_present", "body": encode(source_only), "ctx": base_ctx,
             "status": "BLOCKED", "code": "A10_PROTECTED_WORKFLOW_REQUIRED", "expected": source_only},
        ]
        # Negative cases: forged positive enums, HOST_READINESS_VERIFIED, inference=True, wrong owner, extra fields
        for layer in ("system_config", "account_config", "disabled_features", "read_only_policy"):
            forged = copy.deepcopy(r1251)
            forged["a10_readiness"][layer] = "VERIFIED"
            cases.append({"name": f"forged_{layer}", "body": encode(rehash_public(forged)), "ctx": base_ctx,
                          "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})
        forged_state = copy.deepcopy(r1251)
        forged_state["code"] = "HOST_READINESS_VERIFIED"
        forged_state["a10_readiness"]["state"] = "HOST_READINESS_VERIFIED"
        cases.append({"name": "forged_verified_state", "body": encode(rehash_public(forged_state)), "ctx": base_ctx,
                      "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})
        forged_inf = copy.deepcopy(r1251)
        forged_inf["a10_readiness"]["native_inference_executed"] = True
        cases.append({"name": "forged_inference", "body": encode(rehash_public(forged_inf)), "ctx": base_ctx,
                      "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})
        leaked_internal = {**r1251, "a10_effective_config_evidence": {"secret": "PRIVATE_SECRET"}}
        cases.append({"name": "leaked_internal_field", "body": encode(leaked_internal), "ctx": base_ctx,
                      "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})
        tampered_digest = copy.deepcopy(r1251)
        tampered_digest["a10_readiness"]["public_observation_sha256"] = "0" * 64
        cases.append({"name": "tampered_digest", "body": encode(tampered_digest), "ctx": base_ctx,
                      "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})
        wrong_sha = copy.deepcopy(r1251)
        wrong_sha["source_sha"] = "0" * 40
        cases.append({"name": "wrong_source_sha", "body": encode(rehash_public(wrong_sha)), "ctx": base_ctx,
                      "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})
        bad_cli = copy.deepcopy(r1251)
        bad_cli["a10_readiness"]["exec_help"] = "UNKNOWN"
        cases.append({"name": "inconsistent_cli", "body": encode(rehash_public(bad_cli)), "ctx": base_ctx,
                      "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})
        wrong_sender_ctx = copy.deepcopy(base_ctx)
        wrong_sender_ctx["payload"]["sender"]["login"] = "attacker"
        cases.append({"name": "wrong_sender", "body": encode(r1251), "ctx": wrong_sender_ctx,
                      "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})
        injected_param_ctx = copy.deepcopy(base_ctx)
        injected_param_ctx["payload"]["issue"]["body"] = encode(
            {"schema": hostops.SCHEMA, "operation": self.OP, "parameters": {"path": "PRIVATE_SECRET"}}
        )
        cases.append({"name": "injected_param", "body": encode(r1251), "ctx": injected_param_ctx,
                      "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})

        harness = r"""
const fs = require('fs'), path = require('path'), vm = require('vm');
const cases = JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const script = fs.readFileSync(process.argv[2],'utf8');
(async () => {
  for (const item of cases) {
    fs.writeFileSync(path.join(process.env.RUNNER_TEMP,'personal-hostops-result.json'),item.body,'utf8');
    const comments = [];
    const github = {rest:{issues:{createComment:async args => { comments.push(args.body); }}}};
    await vm.runInNewContext('(async () => {\n'+script+'\n})()',
      {require,process,context:item.ctx,github,Buffer});
    if (comments.length !== 1 || !comments[0].startsWith('AI_COMMERCE_PERSONAL_HOSTOPS_RESULT_V1\n'))
      throw new Error(item.name+': missing result');
    const result = JSON.parse(comments[0].split('\n').slice(1).join('\n'));
    if (result.status !== item.status || result.code !== item.code)
      throw new Error(item.name+': unexpected '+JSON.stringify(result));
    if (item.expected) require('assert').deepStrictEqual(result,item.expected,item.name+': changed receipt');
    if (JSON.stringify(result).includes('PRIVATE_SECRET'))
      throw new Error(item.name+': private data leak');
  }
  process.stdout.write('A10_VERIFIED_CASES='+cases.length);
})().catch(error => { console.error(error); process.exitCode=1; });
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "cases.json").write_text(encode(cases), encoding="utf-8")
            (path / "workflow.js").write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [node, "-e", harness, str(path / "cases.json"), str(path / "workflow.js")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30, env={**os.environ, "RUNNER_TEMP": str(path)}, check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr[-3000:])
        self.assertIn("A10_VERIFIED_CASES=" + str(len(cases)), completed.stdout)

    def _emit_r7c_public_receipt(
        self,
        *,
        sha=None,
        version="0.170.0",
        exec_help=None,
        pin_effects=None,
        binary_state="OBSERVED",
        post_probe_drift=False,
        program_data=r"C:\ProgramData",
        user_profile=None,
    ):
        from ai_commerce.opportunity.core import models

        target_sha = self.SHA if sha is None else sha
        raw = b"observed r7c binary"
        binary_sha = hashlib.sha256(raw).hexdigest()
        full_exec_help = self.EXEC_HELP.replace(
            b"      --json", b"      --ignore-user-config\n      --strict-config\n      --json"
        )
        chosen_exec_help = full_exec_help if exec_help is None else exec_help
        env = {**self._protected_env(), "GITHUB_SHA": target_sha, "GITHUB_WORKFLOW_SHA": target_sha}
        if program_data is not None:
            env["ProgramData"] = program_data
        if user_profile is not None:
            env["USERPROFILE"] = user_profile
        executable = self._binary(PureWindowsPath("D:/Codex/codex.exe"), raw)
        executable.resolve.return_value = executable
        if post_probe_drift:
            executable.open.side_effect = [BytesIO(raw), BytesIO(raw), BytesIO(raw), BytesIO(b"drifted-binary")]
        else:
            executable.open.side_effect = lambda _: BytesIO(raw)
        find_result = (
            ("OBSERVED", executable, binary_sha) if binary_state == "OBSERVED"
            else (binary_state, None, None)
        )
        pin_side_effect = FileNotFoundError if pin_effects is None else pin_effects
        with (patch.object(hostops, "_a10_find_executable", return_value=find_result),
              patch.object(hostops, "_a10_is_native_executable", return_value=True),
              patch.object(hostops, "_a10_pin_local_path", side_effect=pin_side_effect),
              patch.object(hostops.tempfile, "TemporaryDirectory", return_value=nullcontext(r"C:\Temp\a10-r7c")),
              patch.object(Path, "read_bytes", side_effect=AssertionError("must not read private files")),
              patch.object(Path, "read_text", side_effect=AssertionError("must not read private files")),
              patch.object(hostops, "_a10_collect_readiness") as legacy_collect,
              patch.object(models, "_bounded_native_codex", side_effect=[
                  self._output(f"codex-cli {version}".encode()),
                  self._output(self.ROOT_HELP),
                  self._output(chosen_exec_help),
              ]) as runner):
            code, receipt = self._main(event("read", self.OP, {}), env)
        legacy_collect.assert_not_called()
        if binary_state == "OBSERVED":
            self.assertEqual([c.args[0][1:] for c in runner.call_args_list],
                             [["--version"], ["--help"], ["exec", "--help"]])
        else:
            runner.assert_not_called()
        return code, receipt, binary_sha

    def test_r7c_check_codes_match_protected_r7b_negative_only_vocabulary(self):
        self.assertEqual(hostops._A10_CHECK_CODES["system_config"], ("CONFLICTING_POLICY_PRESENT", "UNSAFE"))
        self.assertEqual(hostops._A10_CHECK_CODES["account_config"],
                         ("MISSING_PRIVATE_CONFIG", "CONFLICTING_PRIVATE_CONFIG", "UNSAFE"))
        self.assertEqual(hostops._A10_CHECK_CODES["disabled_features"], ("NOT_DECLARED", "UNSAFE"))
        self.assertEqual(hostops._A10_CHECK_CODES["read_only_policy"], ("NOT_DECLARED", "UNSAFE"))
        all_codes = {c for codes in hostops._A10_CHECK_CODES.values() for c in codes}
        self.assertNotIn("VERIFIED", all_codes)
        self.assertNotIn("HOST_READINESS_VERIFIED", all_codes)

    def test_r7c_end_to_end_negative_observations_and_no_private_read_or_inference(self):
        full_exec_help = self.EXEC_HELP.replace(
            b"      --json", b"      --ignore-user-config\n      --strict-config\n      --json"
        )
        missing_disable_help = full_exec_help.replace(
            b"      --disable <FEATURE>", b"          --disable <FEATURE>"
        )

        # 1. System config present (metadata-only, no file read, no path leaked)
        code, rec, bin_sha = self._emit_r7c_public_receipt(
            pin_effects=[FileNotFoundError, nullcontext(Path("unused"))],
        )
        self.assertEqual((code, rec["status"], rec["code"]), (2, "HOLD", "HOST_UNVERIFIED"))
        row = rec["a10_readiness"]
        self.assertEqual(row["system_config"], "CONFLICTING_POLICY_PRESENT")
        self.assertEqual(row["account_config"], "UNKNOWN")
        self.assertEqual(row["disabled_features"], "UNKNOWN")
        self.assertEqual(row["read_only_policy"], "UNKNOWN")
        self.assertEqual(row["observed_binary_sha256"], bin_sha)
        self.assertFalse(row["native_inference_executed"])
        self.assertNotIn("ProgramData", json.dumps(rec))

        # 2. System config unsafe (reparse/access failure)
        code, rec, _ = self._emit_r7c_public_receipt(pin_effects=[OSError("reparse")])
        self.assertEqual((code, rec["status"], rec["code"]), (2, "HOLD", "HOST_UNVERIFIED"))
        self.assertEqual(rec["a10_readiness"]["system_config"], "UNSAFE")
        self.assertEqual(rec["a10_readiness"]["account_config"], "UNKNOWN")

        # 3. Missing config override flags -> read_only_policy NOT_DECLARED, disabled_features UNKNOWN
        code, rec, _ = self._emit_r7c_public_receipt(exec_help=self.EXEC_HELP)
        row = rec["a10_readiness"]
        self.assertEqual(row["exec_flags"], "NOT_DECLARED")
        self.assertEqual(row["config_override_flags"], "NOT_DECLARED")
        self.assertEqual(row["feature_disable_flag"], "DECLARED")
        self.assertEqual(row["read_only_policy"], "NOT_DECLARED")
        self.assertEqual(row["disabled_features"], "UNKNOWN")

        # 4. Missing --disable flag -> both disabled_features and read_only_policy NOT_DECLARED
        code, rec, _ = self._emit_r7c_public_receipt(exec_help=missing_disable_help)
        row = rec["a10_readiness"]
        self.assertEqual(row["feature_disable_flag"], "NOT_DECLARED")
        self.assertEqual(row["exec_flags"], "NOT_DECLARED")
        self.assertEqual(row["disabled_features"], "NOT_DECLARED")
        self.assertEqual(row["read_only_policy"], "NOT_DECLARED")

        # 5. Post-probe binary drift -> UNSAFE binary/disabled_features/read_only_policy, CLI UNKNOWN
        code, rec, _ = self._emit_r7c_public_receipt(post_probe_drift=True)
        row = rec["a10_readiness"]
        self.assertEqual(row["binary"], "UNSAFE")
        self.assertIsNone(row["observed_binary_sha256"])
        self.assertEqual(row["disabled_features"], "UNSAFE")
        self.assertEqual(row["read_only_policy"], "UNSAFE")
        for field in ("version", "root_help", "exec_help", "root_flags", "exec_flags",
                      "config_override_flags", "feature_disable_flag", "catalog", "account_config"):
            self.assertEqual(row[field], "UNKNOWN")

        # 6. Discovery-time unsafe binary + system conflict
        code, rec, _ = self._emit_r7c_public_receipt(
            binary_state="UNSAFE", pin_effects=[nullcontext(Path("unused"))],
        )
        row = rec["a10_readiness"]
        self.assertEqual(row["binary"], "UNSAFE")
        self.assertIsNone(row["observed_binary_sha256"])
        self.assertEqual(row["system_config"], "CONFLICTING_POLICY_PRESENT")
        self.assertEqual(row["disabled_features"], "UNSAFE")
        self.assertEqual(row["read_only_policy"], "UNSAFE")

        # 7. #1251 baseline with default cache present, all flags declared, all 4 layers UNKNOWN
        code, rec, _ = self._emit_r7c_public_receipt(
            user_profile=r"C:\Users\operator",
            pin_effects=[FileNotFoundError, FileNotFoundError, FileNotFoundError, nullcontext(Path("unused"))],
        )
        row = rec["a10_readiness"]
        self.assertEqual(row["catalog"], "DEFAULT_CACHE_PRESENT")
        for layer in ("system_config", "account_config", "disabled_features", "read_only_policy"):
            self.assertEqual(row[layer], "UNKNOWN")
        self.assertNotIn("operator", json.dumps(rec))

    def test_r7c_binding_tampering_downgrades_to_unknown_hold_without_leaks(self):
        from ai_commerce.opportunity.core import models

        bin_sha = "b" * 64
        help_sha = "c" * 64
        now = datetime.now(timezone.utc)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        stale_iso = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        future_iso = (now + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

        def make_ev(*, source_sha=self.SHA, observed_at=now_iso, windows_boundary=True,
                    account_config=None):
            return models.make_codex_effective_config_evidence(
                source_sha=source_sha,
                observed_at=observed_at,
                binary={"state": "OBSERVED", "sha256": bin_sha, "version_status": "STABLE_SEMVER",
                        "version": "0.170.0", "help_sha256": help_sha},
                cli_declarations={
                    "root_help": "USAGE_OBSERVED", "root_flags": "DECLARED",
                    "exec_help": "USAGE_OBSERVED", "exec_flags": "DECLARED",
                    "config_override_flags": "DECLARED", "feature_disable_flag": "DECLARED",
                },
                system_config={"status": "CONFLICTING_POLICY_PRESENT",
                               "basis": "KNOWN_SYSTEM_POLICY_FILE_PRESENT", "known_paths_checked": 1},
                account_config=account_config or {
                    "status": "UNKNOWN", "basis": "REMOTE_ACCOUNT_POLICY_UNPROVABLE_OFFLINE",
                },
                windows_boundary=windows_boundary,
            )

        valid_ev = make_ev()
        # Direct binding checks fail closed on stale, future, source, policy, binary, version, help, digest drift
        for name, ev, kwargs, expected_code in (
            ("stale", make_ev(observed_at=stale_iso), {"source_sha": self.SHA, "as_of": now_iso}, "CODEX_EVIDENCE_STALE"),
            ("future", make_ev(observed_at=future_iso), {"source_sha": self.SHA, "as_of": now_iso}, "CODEX_EVIDENCE_FUTURE"),
            ("source_drift", make_ev(source_sha="e" * 40), {"source_sha": self.SHA, "as_of": now_iso}, "CODEX_EVIDENCE_SOURCE_MISMATCH"),
            ("policy_drift", valid_ev, {"source_sha": self.SHA, "as_of": now_iso, "policy_sha256": "0" * 64}, "CODEX_EVIDENCE_POLICY_MISMATCH"),
            ("binary_drift", valid_ev, {"source_sha": self.SHA, "as_of": now_iso, "binary_sha256": "0" * 64}, "CODEX_EVIDENCE_BINARY_MISMATCH"),
            ("version_drift", valid_ev, {"source_sha": self.SHA, "as_of": now_iso, "version": "0.164.0"}, "CODEX_EVIDENCE_VERSION_MISMATCH"),
            ("help_drift", valid_ev, {"source_sha": self.SHA, "as_of": now_iso, "help_sha256": "0" * 64}, "CODEX_EVIDENCE_HELP_MISMATCH"),
            ("grant_drift", valid_ev, {"source_sha": self.SHA, "as_of": now_iso, "host_verification_sha256": "0" * 64}, "CODEX_EVIDENCE_GRANT_MISMATCH"),
        ):
            with self.subTest(binding=name), self.assertRaisesRegex(models.SignalValidationError, expected_code):
                hostops._a10_project_public_observations(ev, **kwargs)

        # Non-Windows read_only_policy UNSAFE with observed binary downgrades to UNKNOWN (R7b requires binary=UNSAFE)
        non_win = make_ev(windows_boundary=False)
        projected_non_win = hostops._a10_project_public_observations(non_win, source_sha=self.SHA, as_of=now_iso)
        self.assertNotIn("read_only_policy", projected_non_win)

        # Dedicated-home negative account_config projects only for exact deterministic bases
        for acct_status, acct_basis in (
            ("MISSING_PRIVATE_CONFIG", "DEDICATED_HOME_AUTH_ABSENT"),
            ("CONFLICTING_PRIVATE_CONFIG", "DEDICATED_HOME_FORBIDDEN_CONFIG_PRESENT"),
            ("UNSAFE", "DEDICATED_HOME_UNSAFE"),
        ):
            ev_acct = make_ev(account_config={"status": acct_status, "basis": acct_basis})
            proj = hostops._a10_project_public_observations(ev_acct, source_sha=self.SHA, as_of=now_iso)
            self.assertEqual(proj["account_config"], acct_status)

        # Protected READ downgrades any tampered/stale/malformed/exception evidence to all-UNKNOWN HOLD
        tampered_policy = copy.deepcopy(valid_ev)
        tampered_policy["policy_sha256"] = "0" * 64
        tampered_digest = copy.deepcopy(valid_ev)
        tampered_digest["evidence_sha256"] = "0" * 64
        forged_verified = copy.deepcopy(valid_ev)
        forged_verified["layers"]["system_config"] = {"status": "VERIFIED", "basis": "KNOWN_SYSTEM_POLICY_FILE_PRESENT", "known_paths_checked": 1}
        forged_inf = copy.deepcopy(valid_ev)
        forged_inf["native_inference_executed"] = True
        contradictory = copy.deepcopy(valid_ev)
        contradictory["layers"]["disabled_features"] = {"status": "NOT_DECLARED", "basis": "DISABLE_FLAG_NOT_DECLARED"}

        bad_returns = [
            ("stale", make_ev(observed_at=stale_iso)),
            ("future", make_ev(observed_at=future_iso)),
            ("wrong_source", make_ev(source_sha="e" * 40)),
            ("tampered_policy", tampered_policy),
            ("tampered_digest", tampered_digest),
            ("forged_verified", forged_verified),
            ("forged_inference", forged_inf),
            ("contradictory_layer", contradictory),
            ("malformed_none", None),
            ("malformed_private", {"private_bytes": "PRIVATE_SECRET_BYTES"}),
            ("collector_exception", RuntimeError("PRIVATE_STACK_SECRET")),
        ]
        for name, bad in bad_returns:
            with self.subTest(tamper=name):
                mock_kwargs = {"side_effect": bad} if isinstance(bad, Exception) else {"return_value": bad}
                with patch.object(hostops, "_a10_collect_r7_internal_evidence", **mock_kwargs):
                    code, rec = self._main(event("read", self.OP, {}), self._protected_env())
                self.assertEqual((code, rec["status"], rec["code"]), (2, "HOLD", "HOST_UNVERIFIED"))
                row = rec["a10_readiness"]
                self.assertIsNone(row["observed_binary_sha256"])
                self.assertFalse(row["native_inference_executed"])
                for field in hostops._A10_CHECK_CODES:
                    self.assertEqual(row[field], "UNKNOWN")
                serialized = json.dumps(rec)
                self.assertNotIn("PRIVATE_SECRET_BYTES", serialized)
                self.assertNotIn("PRIVATE_STACK_SECRET", serialized)

        # Defense-in-depth receipt normalization strips contradictory negative claims
        contradictory_obs = {
            "binary": "OBSERVED", "observed_binary_sha256": bin_sha,
            "exec_help": "USAGE_OBSERVED", "exec_flags": "DECLARED",
            "config_override_flags": "DECLARED", "feature_disable_flag": "DECLARED",
            "disabled_features": "NOT_DECLARED", "read_only_policy": "NOT_DECLARED",
            "system_config": "VERIFIED", "account_config": "VERIFIED",
        }
        rec = hostops._a10_readiness_receipt(17, self.SHA, observations=contradictory_obs)
        for field in ("disabled_features", "read_only_policy", "system_config", "account_config"):
            self.assertEqual(rec["a10_readiness"][field], "UNKNOWN")
        unsafe_obs = {
            "binary": "OBSERVED", "observed_binary_sha256": bin_sha,
            "disabled_features": "UNSAFE", "read_only_policy": "UNSAFE",
        }
        rec2 = hostops._a10_readiness_receipt(17, self.SHA, observations=unsafe_obs)
        self.assertEqual(rec2["a10_readiness"]["disabled_features"], "UNKNOWN")
        self.assertEqual(rec2["a10_readiness"]["read_only_policy"], "UNKNOWN")

    def test_r7c_emitted_python_receipts_pass_actual_protected_r7b_js_sanitizer(self):
        """Run newly emitted R7c Python receipts through actual .github/workflows/issue-ingress.yml JS in Node."""
        import os
        import shutil
        from ai_commerce.opportunity.core import models

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
        script = "\n".join(line[12:] if line.startswith("            ") else line
                           for line in workflow.split("          script: |\n", 1)[1].splitlines())
        encode = lambda x: json.dumps(x, ensure_ascii=True, separators=(",", ":"))

        def rehash_public(rec):
            cloned = copy.deepcopy(rec)
            cloned["a10_readiness"].pop("public_observation_sha256", None)
            digest = hashlib.sha256(
                json.dumps(cloned, sort_keys=True, separators=(",", ":")).encode("ascii")
            ).hexdigest()
            cloned["a10_readiness"]["public_observation_sha256"] = digest
            return cloned

        sha = "f" * 40
        full_exec_help = self.EXEC_HELP.replace(
            b"      --json", b"      --ignore-user-config\n      --strict-config\n      --json"
        )
        missing_disable_help = full_exec_help.replace(
            b"      --disable <FEATURE>", b"          --disable <FEATURE>"
        )
        _, rec_sys_conflict, _ = self._emit_r7c_public_receipt(
            sha=sha, pin_effects=[nullcontext(Path("unused"))],
        )
        _, rec_sys_unsafe, _ = self._emit_r7c_public_receipt(
            sha=sha, pin_effects=[OSError("reparse")],
        )
        _, rec_ro_not_declared, _ = self._emit_r7c_public_receipt(
            sha=sha, exec_help=self.EXEC_HELP,
        )
        _, rec_disable_not_declared, _ = self._emit_r7c_public_receipt(
            sha=sha, exec_help=missing_disable_help,
        )
        _, rec_unsafe_binary, _ = self._emit_r7c_public_receipt(
            sha=sha, post_probe_drift=True,
        )
        _, rec_baseline_1251, _ = self._emit_r7c_public_receipt(
            sha=sha, user_profile=r"C:\Users\operator",
            pin_effects=[FileNotFoundError, FileNotFoundError, FileNotFoundError, nullcontext(Path("unused"))],
        )
        stale_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        stale_ev = models.make_codex_effective_config_evidence(
            source_sha=sha, observed_at=stale_iso,
            binary={"state": "OBSERVED", "sha256": "b" * 64, "version_status": "STABLE_SEMVER",
                    "version": "0.170.0", "help_sha256": "c" * 64},
            cli_declarations={
                "root_help": "USAGE_OBSERVED", "root_flags": "DECLARED",
                "exec_help": "USAGE_OBSERVED", "exec_flags": "DECLARED",
                "config_override_flags": "DECLARED", "feature_disable_flag": "DECLARED",
            },
            system_config={"status": "CONFLICTING_POLICY_PRESENT",
                           "basis": "KNOWN_SYSTEM_POLICY_FILE_PRESENT", "known_paths_checked": 1},
            account_config={"status": "UNKNOWN", "basis": "REMOTE_ACCOUNT_POLICY_UNPROVABLE_OFFLINE"},
            windows_boundary=True,
        )
        with patch.object(hostops, "_a10_collect_r7_internal_evidence", return_value=stale_ev):
            _, rec_stale_downgraded = self._main(
                event("read", self.OP, {}),
                {**self._protected_env(), "GITHUB_SHA": sha, "GITHUB_WORKFLOW_SHA": sha},
            )

        base_ctx = {
            "eventName": "issues",
            "repo": {"owner": "trung-via", "repo": "ai-commerce"},
            "issue": {"number": 17},
            "sha": sha,
            "payload": {
                "action": "opened",
                "sender": {"login": "trung-via"},
                "issue": {
                    "user": {"login": "trung-via"},
                    "title": "[HOSTOPS READ] A10-O2b R7c readiness",
                    "body": encode({"schema": hostops.SCHEMA, "operation": self.OP, "parameters": {}}),
                },
            },
        }
        cases = [
            {"name": "r7c_system_conflict", "body": encode(rec_sys_conflict), "ctx": base_ctx,
             "status": "HOLD", "code": "HOST_UNVERIFIED", "expected": rec_sys_conflict},
            {"name": "r7c_system_unsafe", "body": encode(rec_sys_unsafe), "ctx": base_ctx,
             "status": "HOLD", "code": "HOST_UNVERIFIED", "expected": rec_sys_unsafe},
            {"name": "r7c_ro_not_declared", "body": encode(rec_ro_not_declared), "ctx": base_ctx,
             "status": "HOLD", "code": "HOST_UNVERIFIED", "expected": rec_ro_not_declared},
            {"name": "r7c_disable_not_declared", "body": encode(rec_disable_not_declared), "ctx": base_ctx,
             "status": "HOLD", "code": "HOST_UNVERIFIED", "expected": rec_disable_not_declared},
            {"name": "r7c_unsafe_binary", "body": encode(rec_unsafe_binary), "ctx": base_ctx,
             "status": "HOLD", "code": "HOST_UNVERIFIED", "expected": rec_unsafe_binary},
            {"name": "r7c_baseline_1251", "body": encode(rec_baseline_1251), "ctx": base_ctx,
             "status": "HOLD", "code": "HOST_UNVERIFIED", "expected": rec_baseline_1251},
            {"name": "r7c_stale_downgraded", "body": encode(rec_stale_downgraded), "ctx": base_ctx,
             "status": "HOLD", "code": "HOST_UNVERIFIED", "expected": rec_stale_downgraded},
        ]
        # Negative tampered receipts must be BLOCKED by actual protected R7b JS sanitizer
        for name, mutate in (
            ("contradictory_disabled_not_declared", lambda r: r["a10_readiness"].__setitem__("disabled_features", "NOT_DECLARED")),
            ("contradictory_ro_not_declared", lambda r: r["a10_readiness"].__setitem__("read_only_policy", "NOT_DECLARED")),
            ("contradictory_disabled_unsafe", lambda r: r["a10_readiness"].__setitem__("disabled_features", "UNSAFE")),
            ("contradictory_ro_unsafe", lambda r: r["a10_readiness"].__setitem__("read_only_policy", "UNSAFE")),
            ("forged_system_verified", lambda r: r["a10_readiness"].__setitem__("system_config", "VERIFIED")),
            ("forged_inference", lambda r: r["a10_readiness"].__setitem__("native_inference_executed", True)),
        ):
            bad = copy.deepcopy(rec_baseline_1251)
            mutate(bad)
            cases.append({"name": name, "body": encode(rehash_public(bad)), "ctx": base_ctx,
                          "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})
        bad_unsafe_cli = copy.deepcopy(rec_unsafe_binary)
        bad_unsafe_cli["a10_readiness"]["version"] = "DIFFERS_FROM_SOURCE_VERSION"
        cases.append({"name": "unsafe_binary_with_version", "body": encode(rehash_public(bad_unsafe_cli)),
                      "ctx": base_ctx, "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})
        wrong_owner_ctx = copy.deepcopy(base_ctx)
        wrong_owner_ctx["payload"]["issue"]["user"]["login"] = "attacker"
        cases.append({"name": "wrong_issue_owner", "body": encode(rec_sys_conflict), "ctx": wrong_owner_ctx,
                      "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})
        private_leak = {**rec_sys_conflict, "private_dump": "PRIVATE_SECRET"}
        cases.append({"name": "private_bytes_leak", "body": encode(private_leak), "ctx": base_ctx,
                      "status": "BLOCKED", "code": "INVALID_PUBLIC_A10_READINESS_RECEIPT"})

        harness = r"""
const fs = require('fs'), path = require('path'), vm = require('vm');
const cases = JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const script = fs.readFileSync(process.argv[2],'utf8');
(async () => {
  for (const item of cases) {
    fs.writeFileSync(path.join(process.env.RUNNER_TEMP,'personal-hostops-result.json'),item.body,'utf8');
    const comments = [];
    const github = {rest:{issues:{createComment:async args => { comments.push(args.body); }}}};
    await vm.runInNewContext('(async () => {\n'+script+'\n})()',
      {require,process,context:item.ctx,github,Buffer});
    if (comments.length !== 1 || !comments[0].startsWith('AI_COMMERCE_PERSONAL_HOSTOPS_RESULT_V1\n'))
      throw new Error(item.name+': missing result');
    const result = JSON.parse(comments[0].split('\n').slice(1).join('\n'));
    if (result.status !== item.status || result.code !== item.code)
      throw new Error(item.name+': unexpected '+JSON.stringify(result));
    if (item.expected) require('assert').deepStrictEqual(result,item.expected,item.name+': changed receipt');
    if (JSON.stringify(result).includes('PRIVATE_SECRET'))
      throw new Error(item.name+': private data leak');
  }
  process.stdout.write('R7C_VERIFIED_CASES='+cases.length);
})().catch(error => { console.error(error); process.exitCode=1; });
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "cases.json").write_text(encode(cases), encoding="utf-8")
            (path / "workflow.js").write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [node, "-e", harness, str(path / "cases.json"), str(path / "workflow.js")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30, env={**os.environ, "RUNNER_TEMP": str(path)}, check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr[-3000:])
        self.assertIn("R7C_VERIFIED_CASES=" + str(len(cases)), completed.stdout)


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
        ) as fake, patch.object(acquisition, "_powershell", return_value=TEST_PWSH):
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
                         [TEST_PWSH, "-NoProfile", "-NonInteractive", "-Command"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
        ) as fake, patch.object(acquisition, "_powershell", return_value=TEST_PWSH):
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
        ) as native, patch.object(hostops, "build_opener") as opener, patch.object(
            acquisition, "_powershell", return_value=TEST_PWSH
        ):
            native.return_value.returncode = 0
            native.return_value.stdout = json.dumps(record)
            answer = hostops.perform(req)
            opener.assert_not_called()
            self.assertEqual(native.call_args.args[0][:4],
                             [TEST_PWSH, "-NoProfile", "-NonInteractive", "-Command"])
            self.assertEqual(native.call_args.kwargs["timeout"], 18)
            self.assertEqual(native.call_args.kwargs["creationflags"],
                             getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
        ) as native, patch.object(hostops, "build_opener") as opener, patch.object(
            acquisition, "_powershell", return_value=TEST_PWSH
        ):
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
        ) as native, patch.object(hostops, "build_opener") as opener, patch.object(
            acquisition, "_powershell", return_value=TEST_PWSH
        ):
            native.return_value.returncode = 0
            for obj in invalid:
                native.return_value.stdout = json.dumps(obj)
                with self.assertRaises(hostops.Denied):
                    hostops.perform(req)
            opener.assert_not_called()
            native.return_value.returncode = 1
            with self.assertRaisesRegex(hostops.Denied, "CDP_NATIVE_READ_FAILED"):
                hostops.perform(req)

    def test_fixed_native_reads_deny_when_powershell_is_unavailable(self):
        cases = (
            ("runner_runtime_inventory", "RUNNER_RUNTIME_READ_UNAVAILABLE"),
            ("cdp_9333_status", "CDP_NATIVE_READ_UNAVAILABLE"),
        )
        with patch.object(hostops.os, "name", "nt"), patch.object(
            acquisition, "_powershell", side_effect=acquisition.AcquisitionBlocked("PRIVATE_BINARY")
        ), patch.object(subprocess, "run") as native, patch.object(hostops, "build_opener") as opener:
            for operation, code in cases:
                with self.subTest(operation=operation), self.assertRaisesRegex(hostops.Denied, f"^{code}$"):
                    hostops.perform(hostops.parse_event(event("read", operation, {}), "read"))
            native.assert_not_called()
            opener.assert_not_called()

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
                policy.unlink()
                self.assertEqual(self._defaults_original()["state"], "INVALID")
        self.native.assert_not_called()

    def test_strict_receipt_rejects_extra_raw_quota_identity_and_bad_types(self):
        valid = self.collect()
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
        supervisor_request = encode({"schema": hostops.SCHEMA, "operation": "deploy_wakehub_supervisor",
                                     "parameters": {}})
        for code, status in (
            ("SUPERVISOR_STARTED_HEALTH_OBSERVED", "OBSERVED"),
            ("SUPERVISOR_REUSED_HEALTH_OBSERVED", "OBSERVED"),
            ("SUPERVISOR_START_UNKNOWN", "HOLD"),
            ("LOCAL_CONFIG_REQUIRED", "HOLD"),
            ("PRIVATE_CONFIG_PATH", "HOLD"),
        ):
            native = hostops._supervisor_receipt(code, 17, "f" * 40)
            expected = {
                "status": status, "code": "SUPERVISOR_RUNTIME_UNAVAILABLE" if code.startswith("PRIVATE") else code,
                "operation": "deploy_wakehub_supervisor", "issue_number": 17, "source_sha": "f" * 40,
                "approval_channel": "OWNER_GITHUB_COMMENT", "remote_shell_enabled": False,
                "arbitrary_shell_enabled": False, "exact_chat_verified": False, "wake_sent": False,
            }
            self.assertEqual(native, expected)
            local_evidence = {"chrome_health": "PRIVATE", "effect_state": "PRIVATE", "pid": 123,
                              "config": {"private_token": "PRIVATE"}, "source_digest": "PRIVATE"}
            cases.append({"name": code, "body": encode({**native, **local_evidence}),
                          "request": supervisor_request, "expected": expected})
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


class ProtectedWakeHubSupervisorTests(unittest.TestCase):
    def setUp(self):
        from ai_commerce.wakehub_runtime import runner

        self.runtime = runner
        self.stack = self.enterContext(ExitStack())
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.config = self.root / "existing-config.json"
        self.config.touch()
        self.executable = self.root / "python.exe"
        self.executable.touch()
        (self.root / "Lib" / "site-packages").mkdir(parents=True)
        self.op = "deploy_wakehub_supervisor"
        self.sha, self.source, self.config_pin, self.desktop = "a" * 40, "b" * 64, "c" * 64, "d" * 64
        self.env = {
            "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "issue_comment", "GITHUB_REPOSITORY": hostops.REPO,
            "GITHUB_REPOSITORY_OWNER": hostops.OWNER, "GITHUB_ACTOR": hostops.OWNER,
            "GITHUB_TRIGGERING_ACTOR": hostops.OWNER, "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKFLOW_REF": hostops.REPO + "/.github/workflows/issue-ingress.yml@refs/heads/main",
            "RUNNER_ENVIRONMENT": "self-hosted", "RUNNER_NAME": "AI-COMMERCE-HOSTOPS-01", "GITHUB_SHA": self.sha,
            "GITHUB_WORKSPACE": str(MODULE.parents[3]), "GITHUB_EVENT_PATH": str(self.root / "event.json"),
            "AI_COMMERCE_WAKEHUB_CONFIG": str(self.config),
        }
        self.approval = event("write", self.op, {})
        self.owner = (self.root, 7, "S-1-5-21-101-202-303-1001")
        self.values = {}
        self.child = SimpleNamespace(pid=123, poll=Mock(return_value=None))
        clock = count()
        self.mock(hostops, "time", new=SimpleNamespace(monotonic=lambda: next(clock), sleep=Mock()))
        self.mock(hostops, "os", new=SimpleNamespace(name="nt", environ=self.env))
        self.mock(hostops.sys, "_base_executable", new=str(self.executable))
        self.mock(hostops.sys, "prefix", new=str(self.root))
        self.mock(hostops, "ROOTS", new={"workspace": self.root})
        self.mock(hostops, "_persistent_clean_source", return_value=self.source)
        self.mock(acquisition, "_native_operator", return_value=self.owner)
        self.mock(runner, "_desktop_logon", return_value=self.desktop)
        self.config_read = self.mock(runner, "_observe_only_config", return_value=(None, self.config_pin))
        self.active = self.mock(runner, "_supervisor_active", return_value=False)
        self.mock(runner, "_supervisor_deployment_value", side_effect=self.values.get)
        self.mock(runner, "_store_supervisor_deployment_value", side_effect=self.values.__setitem__)
        self.live_process = self.mock(runner, "_supervisor_process", return_value=456)
        self.mock(runner, "monotonic_ns", return_value=100_000_000_000)
        self.mock(hostops.subprocess, "DETACHED_PROCESS", new=0x8, create=True)
        self.spawn = self.mock(hostops.subprocess, "Popen", return_value=self.child)

    def mock(self, obj, name, **kwargs):
        return self.stack.enter_context(patch.object(obj, name, **kwargs))

    def dispatch(self):
        Path(self.env["GITHUB_EVENT_PATH"]).write_text(json.dumps(self.approval), encoding="utf-8")
        output = self.root / "result.json"
        argv = ["personal", "--mode", "write", "--output", str(output)]
        with patch.object(hostops.sys, "argv", argv):
            exit_code = hostops.main()
        receipt = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 0 if receipt["status"] == "OBSERVED" else 2)
        self.assertLessEqual(receipt.keys(), hostops.PERSONAL_PUBLIC_FIELDS)
        for private in ("PRIVATE", str(self.config), self.source, self.config_pin, self.desktop):
            self.assertNotIn(private, json.dumps(receipt))
        return receipt

    def publish_health(self, **overrides):
        self.active.return_value = True
        self.values["Health"] = json.dumps({
            "source_sha": self.sha, "source_digest": self.source, "config_digest": self.config_pin,
            "desktop": self.desktop, "nonce": "e" * 32, "pid": 123, "created": 456,
            "observed_ns": 99_000_000_000, "health": "CHROME_ABSENT", **overrides,
        })

    def test_authorization_and_parameters_are_fixed(self):
        with self.assertRaisesRegex(hostops.Denied, "READ_OPERATION_DENIED"):
            hostops.parse_event(event("read", self.op, {}), "read")
        with self.assertRaisesRegex(hostops.Denied, "INVALID_PARAMETERS"):
            hostops.parse_event(event("write", self.op, {"config": "PRIVATE"}), "write")
        with self.assertRaisesRegex(hostops.Denied, "PROTECTED_HOSTOPS_ONLY"):
            hostops.perform(hostops.parse_event(self.approval, "write"))
        self.approval["comment"]["body"] = "APPROVE WRITE #18"
        self.assertEqual(self.dispatch()["code"], "EXACT_WRITE_APPROVAL_REQUIRED")
        self.approval = event("write", self.op, {})
        self.env["AI_COMMERCE_GIT_GUARD_TEST"] = "1"
        self.assertEqual(self.dispatch()["code"], "PROTECTED_HOSTOPS_ONLY")
        del self.env["AI_COMMERCE_GIT_GUARD_TEST"]
        self.env["GITHUB_RUN_ATTEMPT"] = "2"
        self.assertEqual(self.dispatch()["code"], "PROTECTED_HOSTOPS_ONLY")
        self.config_read.assert_not_called()
        self.spawn.assert_not_called()
        self.assertEqual(self.values, {})

    def test_missing_changed_or_unavailable_config_holds_before_launch(self):
        self.env.pop("AI_COMMERCE_WAKEHUB_CONFIG")
        self.assertEqual(self.dispatch()["code"], "LOCAL_CONFIG_REQUIRED")
        self.env["AI_COMMERCE_WAKEHUB_CONFIG"] = str(self.config)
        self.config_read.side_effect = self.runtime.RuntimeHold("LOCAL_CONFIG_OBSERVE_ONLY_REQUIRED")
        self.assertEqual(self.dispatch()["code"], "LOCAL_CONFIG_OBSERVE_ONLY_REQUIRED")
        self.config_read.side_effect = [(None, self.config_pin), (None, "f" * 64)]
        self.assertEqual(self.dispatch()["code"], "LOCAL_CONFIG_CHANGED")
        self.config_read.side_effect = None
        self.active.side_effect = OSError("PRIVATE probe unavailable")
        self.assertEqual(self.dispatch()["code"], "SUPERVISOR_RUNTIME_UNAVAILABLE")
        self.spawn.assert_not_called()
        self.assertEqual(self.values, {})

    def test_reuse_needs_matching_live_source_owner_and_fresh_health(self):
        self.publish_health()
        receipt = self.dispatch()
        self.assertEqual((receipt["status"], receipt["code"], receipt["source_sha"]),
                         ("OBSERVED", "SUPERVISOR_REUSED_HEALTH_OBSERVED", self.sha))
        self.live_process.assert_called_with(123, self.executable, self.owner)
        for bad in ({"desktop": "f" * 64}, {"source_sha": "f" * 40}, {"source_digest": "f" * 64},
                    {"observed_ns": 0}, {"created": 789}, {"health": "PRIVATE"}):
            with self.subTest(bad=bad):
                self.publish_health(**bad)
                self.assertEqual(self.dispatch()["code"], "SUPERVISOR_ACTIVE_UNVERIFIED")
        self.values.pop("Health")
        self.assertEqual(self.dispatch()["code"], "SUPERVISOR_ACTIVE_UNVERIFIED")
        self.spawn.assert_not_called()

    def test_start_requires_same_child_and_rechecked_health(self):
        def launch(argv, **kwargs):
            self.assertEqual(argv[-4:-1], [self.source, self.config_pin, self.desktop])
            self.assertEqual(self.values["StartPending"], argv[-1])
            self.assertEqual(kwargs["cwd"], self.root)
            self.publish_health(nonce=argv[-1], health="CHROME_OWNED")
            return self.child

        self.spawn.side_effect = launch
        receipt = self.dispatch()
        self.assertEqual((receipt["status"], receipt["code"], receipt["source_sha"]),
                         ("OBSERVED", "SUPERVISOR_STARTED_HEALTH_OBSERVED", self.sha))
        self.assertIsNone(self.values["StartPending"])
        self.spawn.assert_called_once()

    def test_missing_or_ambiguous_child_health_keeps_unknown_and_never_retries(self):
        for publish in (False, True):
            with self.subTest(ambiguous=publish):
                self.values.clear()
                self.active.return_value = False
                self.spawn.reset_mock()

                def launch(argv, **kwargs):
                    if publish:
                        self.publish_health(nonce=argv[-1], pid=999)
                    return self.child

                self.spawn.side_effect = launch
                self.assertEqual(self.dispatch()["code"], "SUPERVISOR_START_UNKNOWN")
                pending = self.values["StartPending"]
                self.assertRegex(pending, r"^[a-f0-9]{32}$")
                self.active.return_value = False
                self.assertEqual(self.dispatch()["code"], "SUPERVISOR_START_UNKNOWN")
                self.assertEqual(self.values["StartPending"], pending)
                self.spawn.assert_called_once()


class PersonalClean9444AutostartTests(unittest.TestCase):
    def setUp(self):
        from ai_commerce.remote import chrome_acquisition, local_completion_handoff, local_wake_entry

        self.acq = chrome_acquisition
        self.handoff = local_completion_handoff
        self.wake = local_wake_entry
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.local = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.sid = "S-1-5-21-101-202-303-1001"
        self.op = "install_clean_9444_autostart"
        self.event = event("write", self.op, {})
        self.sha = "a" * 40
        self.env = {
            "CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_REPOSITORY": hostops.REPO, "GITHUB_REPOSITORY_OWNER": hostops.OWNER,
            "GITHUB_ACTOR": hostops.OWNER, "GITHUB_TRIGGERING_ACTOR": hostops.OWNER,
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": hostops.REPO + "/.github/workflows/issue-ingress.yml@refs/heads/main",
            "RUNNER_ENVIRONMENT": "self-hosted", "RUNNER_NAME": "AI-COMMERCE-HOSTOPS-01",
            "GITHUB_SHA": self.sha, "GITHUB_WORKFLOW_SHA": self.sha,
            "GITHUB_WORKSPACE": str(self.local / "runner" / "_work"),
            "GITHUB_EVENT_PATH": str(self.local / "event.json"),
        }
        self.mock(hostops, "os", new=SimpleNamespace(name="nt", environ=self.env, walk=hostops.os.walk))
        self.session = self.mock(self.acq, "_interactive_session", return_value=7)
        self.mock(self.wake, "_windows_process_sid", return_value=self.sid)
        self.mock(self.acq, "_native_local_app_data", return_value=self.local)
        self.real_source = hostops._persistent_clean_source
        self.source = self.mock(hostops, "_persistent_clean_source", return_value=self.local / "stable.py")
        self.real_task = self.acq._clean_task
        self.real_target = self.acq._clean_target
        self.target = self.acq._ChromeTarget(self.local / "chrome.exe", self.local / "WakeHub-9444-Clean",
                                            "Default", "http://127.0.0.1:9444", 9444)
        self.owner = (self.local, 7, self.sid)
        self.candidates = (self.target.executable,)
        self.target_read = self.mock(self.acq, "_clean_target", return_value=(self.target, self.candidates))
        self.inventory = self.mock(self.acq, "_clean_inventory", return_value=("ABSENT", None))
        self.mock(self.acq, "_verify_google_chrome")
        self.mock(self.acq, "_clean_require_unlocked")
        self.launch_pending = self.mock(self.acq, "_clean_launch_pending", return_value=False)
        self.pending = set()
        self.mock(self.acq, "_clean_pending", side_effect=self.pending.__contains__)
        self.record = self.mock(self.acq, "_clean_record_pending", side_effect=self.record_pending)
        self.task = self.mock(self.acq, "_clean_task", return_value="ALREADY_ENROLLED")
        self.reconcile = self.mock(
            self.acq,
            "_clean_reconcile_read",
            return_value={"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED"},
        )
        self.probe = self.mock(self.acq, "_probe_cdp")
        self.spawn = self.mock(self.acq.subprocess, "Popen", side_effect=AssertionError("direct spawn"))
        self.native_run = self.mock(self.acq.subprocess, "run", side_effect=AssertionError("native subprocess"))
        self.slot = False
        self.mock(self.acq, "_clean_supervisor_slot", new=self.supervisor_slot)

    def mock(self, obj, name, **kwargs):
        return self.stack.enter_context(patch.object(obj, name, **kwargs))

    @contextmanager
    def supervisor_slot(self):
        self.assertFalse(self.slot)
        self.slot = True
        try:
            yield
        finally:
            self.slot = False

    def record_pending(self, name, pending):
        (self.pending.add if pending else self.pending.remove)(name)

    def invoke(self, payload=None, *, mode="write"):
        event_file = Path(self.env["GITHUB_EVENT_PATH"])
        event_file.write_text(json.dumps(payload or self.event), encoding="utf-8")
        output = self.local / "receipt.json"
        with patch.object(hostops.sys, "argv", ["personal", "--mode", mode, "--output", str(output)]):
            code = hostops.main()
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0 if result["status"] == "OBSERVED" else 2)
        self.assertLessEqual(result.keys(), hostops.PERSONAL_PUBLIC_FIELDS)
        for private in (str(self.local), self.sid, "CommandLine", "RunEx", "PRIVATE"):
            self.assertNotIn(private, json.dumps(result))
        self.spawn.assert_not_called()
        self.native_run.assert_not_called()
        return result

    def invoke_probe(self, payload=None):
        with patch.dict(self.env, GITHUB_EVENT_NAME="issues"):
            return self.invoke(payload or event("read", "clean_9444_runtime_probe", {}), mode="read")

    def invoke_forensic(self, payload=None):
        with patch.dict(self.env, GITHUB_EVENT_NAME="issues"):
            return self.invoke(payload or event("read", "clean_9444_1270_forensic_read", {}), mode="read")

    def test_forensic_read_requires_exact_protected_owner_request(self):
        op = "clean_9444_1270_forensic_read"
        request = hostops.parse_event(event("read", op, {}), "read")
        with self.assertRaisesRegex(hostops.Denied, "OPERATION_DENIED"):
            hostops.perform(request)
        self.assertEqual(self.invoke(event("write", op, {}))["code"], "WRITE_SCOPE_DENIED")
        payload = event("read", op, {})
        payload["sender"]["login"] = "another-account"
        self.assertEqual(self.invoke_forensic(payload)["code"], "NON_OWNER_EVENT")
        for params in (None, [], "{}"):
            with self.subTest(params=params):
                self.assertEqual(self.invoke_forensic(event("read", op, params))["code"], "INVALID_REQUEST")
        for key in ("path", "registry", "command", "task", "sid", "sddl", "executable", "url", "wildcard"):
            with self.subTest(key=key):
                payload = event("read", op, {key: "PRIVATE"})
                self.assertEqual(self.invoke_forensic(payload)["code"], "INVALID_PARAMETERS")
        for key, value, expected in (
            ("GITHUB_ACTOR", "another-account", "WINDOWS_OWNER_CONTEXT_UNVERIFIED"),
            ("GITHUB_WORKFLOW_SHA", "b" * 40, "SOURCE_CUSTODY_UNVERIFIED"),
        ):
            with self.subTest(key=key), patch.dict(self.env, {key: value}):
                receipt = self.invoke_forensic()
                self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", expected))
                self.assertNotIn("source_sha", receipt)
        self.session.assert_not_called()
        self.source.assert_not_called()
        self.reconcile.assert_not_called()

    def test_forensic_read_checks_session_source_and_target_before_access(self):
        self.session.side_effect = (7, 8)
        self.assertEqual(self.invoke_forensic()["code"], "WINDOWS_OWNER_CONTEXT_UNVERIFIED")
        self.session.side_effect = None
        self.source.side_effect = hostops.Denied("LOCAL_MAIN_SYNC_REQUIRED")
        self.assertEqual(self.invoke_forensic()["code"], "SOURCE_CUSTODY_UNVERIFIED")
        self.target_read.assert_not_called()
        self.source.side_effect = None
        self.target_read.side_effect = self.real_target
        profile_parent = self.local / "AI-Commerce"
        lstat = type(profile_parent).lstat

        def junction(path):
            self.assertNotIn(profile_parent, path.parents)
            if path == profile_parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return lstat(path)

        with patch.object(type(profile_parent), "lstat", junction):
            self.assertEqual(self.invoke_forensic()["code"], "PROFILE_UNAVAILABLE")
        self.reconcile.assert_not_called()

    def test_forensic_read_projects_backup_separately_from_history_and_runtime(self):
        for raw, expected, status in (
            (("DEMAND_READY", "COMPLETE", "ENABLED_ATTESTED", "VALID_BUT_UNBOUND"),
             "VALID_BUT_UNBOUND+DEMAND_READY+D5_ENABLED+JOURNAL_COMPLETE", "OBSERVED"),
            (("DEMAND_READY", "FRESH", "UNVERIFIED_EXE", "JOURNAL_ABSENT"),
             "JOURNAL_ABSENT+DEMAND_READY+D5_UNVERIFIED_EXE+JOURNAL_FRESH", "HOLD"),
            (("CONFLICT_BASE_ACL", "COMPLETE", "ENABLED_ATTESTED", "VALID_BUT_UNBOUND"),
             "VALID_BUT_UNBOUND+TASK_CONFLICT_ACL+D5_ENABLED+JOURNAL_MISMATCH", "HOLD"),
            (("SD_UNVERIFIED", "COMPLETE", "ENABLED_ATTESTED", "VALID_BUT_UNBOUND"),
             "VALID_BUT_UNBOUND+TASK_SD_UNVERIFIED+D5_ENABLED+JOURNAL_MISMATCH", "HOLD"),
            (("DEMAND_READY", "COMPLETE", "UNVERIFIED_EXE_PATH", "VALID_BUT_UNBOUND"),
             "VALID_BUT_UNBOUND+DEMAND_READY+D5_UNVERIFIED_EXE_PATH+JOURNAL_COMPLETE", "HOLD"),
            (("MULTIPLE_CANDIDATES", "ROLLBACK_ONLY", "ENABLED_ATTESTED", "VALID_BUT_UNBOUND"),
             "VALID_BUT_UNBOUND+TASK_MULTIPLE+D5_ENABLED+ROLLBACK_ONLY", "HOLD"),
            (("FOREIGN_CANDIDATE", "MISMATCH", "ABSENT", "NO_TRUSTED_1270_CUSTODY"),
             "NO_TRUSTED_1270_CUSTODY+TASK_FOREIGN+D5_ABSENT+JOURNAL_MISMATCH", "HOLD"),
            (("DEMAND_READY", "COMPLETE", "ENABLED_ATTESTED", "PRIVATE <Task />"),
             "NO_TRUSTED_1270_CUSTODY+TASK_UNAVAILABLE+D5_UNVERIFIED+JOURNAL_UNVERIFIED", "HOLD"),
        ):
            with self.subTest(raw=raw):
                self.reconcile.reset_mock()
                self.reconcile.return_value = dict(zip(("task", "journal", "d5", "backup"), raw))
                receipt = self.invoke_forensic()
                self.assertEqual(receipt["status"], status)
                self.assertEqual(receipt["code"], f"UNKNOWN_EFFECT+{expected}+PENDING_CLEAR")
                self.assertRegex(receipt["code"], r"^[A-Z0-9_]+(?:\+[A-Z0-9_]+){5}$")
                self.assertEqual(receipt["source_sha"], self.sha)
                self.assertEqual(receipt["issue_number"], 17)
                self.assertFalse(receipt["wake_sent"])
                self.assertFalse(receipt["remote_shell_enabled"])
                self.reconcile.assert_called_once_with(self.owner, self.target, self.candidates, forensic=True)
        self.task.assert_not_called()
        self.record.assert_not_called()
        self.probe.assert_not_called()

    def test_forensic_read_requires_stable_process_identity_for_observed(self):
        self.reconcile.return_value = {
            "task": "DEMAND_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED", "backup": "VALID_BUT_UNBOUND",
        }
        identity = (17, self.target.executable, "created", "PRIVATE")
        for second, status, observation in (
            (("OWNED", identity), "OBSERVED", "PENDING_CLEAR"),
            (("OWNED", (18, *identity[1:])), "HOLD", "PROCESS_IDENTITY_UNVERIFIED"),
            (self.acq._Clean9444Hold("BROWSER_OWNERSHIP_UNPROVEN"), "HOLD", "PROCESS_IDENTITY_UNVERIFIED"),
        ):
            with self.subTest(status=status, second=second):
                self.inventory.reset_mock()
                self.inventory.side_effect = (("OWNED", identity), second)
                receipt = self.invoke_forensic()
                self.assertEqual(receipt["status"], status)
                self.assertEqual(receipt["code"], (
                    "UNKNOWN_EFFECT+VALID_BUT_UNBOUND+DEMAND_READY+D5_ENABLED+JOURNAL_COMPLETE+" + observation
                ))
                self.assertEqual(receipt["source_sha"], self.sha)
                self.assertEqual(self.inventory.call_args_list, [
                    call(self.target, self.candidates, self.owner[1], self.owner[2]),
                ] * 2)
        self.record.assert_not_called()
        self.task.assert_not_called()
        self.probe.assert_not_called()

    def test_forensic_read_holds_when_pending_markers_or_owner_change(self):
        self.reconcile.return_value = {
            "task": "DEMAND_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED", "backup": "VALID_BUT_UNBOUND",
        }
        self.pending.add("AutostartPending")
        receipt = self.invoke_forensic()
        self.assertEqual(receipt["status"], "HOLD")
        self.assertTrue(receipt["code"].endswith("+AUTOSTART_PENDING"))
        self.launch_pending.side_effect = (False, True)
        receipt = self.invoke_forensic()
        self.assertEqual(receipt["status"], "HOLD")
        self.assertEqual(receipt["code"], (
            "UNKNOWN_EFFECT+NO_TRUSTED_1270_CUSTODY+TASK_UNAVAILABLE+D5_UNVERIFIED"
            "+JOURNAL_UNVERIFIED+PENDING_STATE_CHANGED"
        ))
        self.launch_pending.side_effect = None
        self.session.side_effect = (7, 7, 8)
        self.assertEqual(self.invoke_forensic()["code"], "WINDOWS_OWNER_CONTEXT_UNVERIFIED")
        self.assertEqual(self.pending, {"AutostartPending"})
        self.record.assert_not_called()
        self.task.assert_not_called()
        self.probe.assert_not_called()

    def test_runtime_probe_rejects_unapproved_events_and_nonempty_parameters(self):
        op = "clean_9444_runtime_probe"
        request = hostops.parse_event(event("read", op, {}), "read")
        with self.assertRaisesRegex(hostops.Denied, "OPERATION_DENIED"):
            hostops.perform(request)
        self.assertEqual(self.invoke(event("write", op, {}))["code"], "WRITE_SCOPE_DENIED")
        payload = event("read", op, {})
        payload["sender"]["login"] = "another-account"
        self.assertEqual(self.invoke_probe(payload)["code"], "NON_OWNER_EVENT")
        for params in (None, [], {"cdp_port": 9333}, {"cdp_port": 9444}, {"command": "PRIVATE"}):
            with self.subTest(params=params):
                self.assertEqual(self.invoke_probe(event("read", op, params))["status"], "BLOCKED")
        self.session.assert_not_called()
        self.task.assert_not_called()

    def test_runtime_probe_checks_runner_actor_and_sha_before_native_access(self):
        for name, value, code in (
            ("RUNNER_NAME", "AI-COMMERCE-EF02-01", "WINDOWS_OWNER_CONTEXT_UNVERIFIED"),
            ("GITHUB_ACTOR", "another-account", "WINDOWS_OWNER_CONTEXT_UNVERIFIED"),
            ("GITHUB_RUN_ATTEMPT", "2", "WINDOWS_OWNER_CONTEXT_UNVERIFIED"),
            ("AI_COMMERCE_GIT_GUARD_ACTIVE", "1", "WINDOWS_OWNER_CONTEXT_UNVERIFIED"),
            ("GITHUB_SHA", "PRIVATE", "SOURCE_CUSTODY_UNVERIFIED"),
            ("GITHUB_WORKFLOW_SHA", "b" * 40, "SOURCE_CUSTODY_UNVERIFIED"),
        ):
            with self.subTest(name=name), patch.dict(self.env, {name: value}):
                receipt = self.invoke_probe()
                self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", code))
                self.assertNotIn("source_sha", receipt)
        with patch.object(hostops, "PERSONAL_PUBLIC_FIELDS", hostops.PERSONAL_PUBLIC_FIELDS - {"source_sha"}):
            self.assertEqual(self.invoke_probe()["code"], "PUBLIC_TRANSPORT_GAP")
        self.session.assert_not_called()
        self.source.assert_not_called()
        self.reconcile.assert_not_called()
        self.task.assert_not_called()

    def test_runtime_probe_allows_blank_workflow_sha_without_weakening_a10_or_source_custody(self):
        root, workflow, source, peer, git = self._clean_source_fixture()
        self.source.side_effect = self.real_source
        with patch.dict(hostops.ROOTS, workspace=root), \
                patch.dict(self.env, GITHUB_WORKFLOW_SHA=""), \
                patch.object(hostops, "__file__", workflow / "src/ai_commerce/hostops_v3/personal.py"), \
                patch.object(self.acq, "__file__", peer), \
                patch.object(hostops, "_git", side_effect=lambda *args: git[args]):
            receipt = self.invoke_probe()
            self.assertEqual(receipt, {
                "status": "OBSERVED",
                "code": "DEMAND_READY_JOURNAL_COMPLETE_D5_ENABLED",
                "operation": "clean_9444_runtime_probe",
                "cdp_port": 9444,
                "source_sha": self.sha,
                "exact_chat_verified": False,
                "wake_sent": False,
                "arbitrary_shell_enabled": False,
                "remote_shell_enabled": False,
            })
            # A10-O2b still rejects blank GITHUB_WORKFLOW_SHA.
            with self.assertRaisesRegex(hostops.Denied, "^PROTECTED_HOSTOPS_ONLY$"):
                hostops._protected_clean_workflow_sha()
            a10 = hostops._protected_a10_readiness(17)
            self.assertEqual((a10["status"], a10["code"]), ("BLOCKED", "A10_PROTECTED_WORKFLOW_REQUIRED"))
        self.reconcile.assert_called_once_with(self.owner, self.target, self.candidates)
        self.task.assert_not_called()
        self.record.assert_not_called()

    def test_runtime_probe_reuses_source_custody_and_only_reads_task(self):
        root, workflow, source, peer, git = self._clean_source_fixture()
        self.source.side_effect = self.real_source
        with patch.dict(hostops.ROOTS, workspace=root), \
                patch.object(hostops, "__file__", workflow / "src/ai_commerce/hostops_v3/personal.py"), \
                patch.object(self.acq, "__file__", peer), \
                patch.object(hostops, "_git", side_effect=lambda *args: git[args]):
            for args, value in (
                (("rev-parse", "HEAD", "refs/remotes/origin/main"), "b" * 40 + "\n" + self.sha),
                (("status", "--porcelain=v1", "--untracked-files=all"), " M src/PRIVATE.py"),
                (("status", "--porcelain=v1", "--untracked-files=all"), "?? src/PRIVATE.py"),
            ):
                with patch.dict(git, {args: value}):
                    receipt = self.invoke_probe()
                    self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", "SOURCE_CUSTODY_UNVERIFIED"))
            with patch.object(hostops, "_is_reparse", side_effect=lambda path: path == source):
                receipt = self.invoke_probe()
                self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", "SOURCE_CUSTODY_UNVERIFIED"))
            self.reconcile.assert_not_called()
            self.task.assert_not_called()

            self.assertEqual(self.invoke_probe(), {
                "status": "OBSERVED",
                "code": "DEMAND_READY_JOURNAL_COMPLETE_D5_ENABLED",
                "operation": "clean_9444_runtime_probe",
                "cdp_port": 9444,
                "source_sha": self.sha,
                "exact_chat_verified": False,
                "wake_sent": False,
                "arbitrary_shell_enabled": False,
                "remote_shell_enabled": False,
            })
        self.reconcile.assert_called_once_with(self.owner, self.target, self.candidates)
        self.task.assert_not_called()
        self.inventory.assert_not_called()
        self.record.assert_not_called()
        self.probe.assert_not_called()

    def test_runtime_probe_distinguishes_demand_legacy_journal_d5_and_holds_on_ambiguity(self):
        self.assertFalse(any(code.endswith("_D5_ABSENT") for code in hostops._CLEAN_RUNTIME_PROBE_OBSERVED_CODES))
        self.assertFalse(any(code.endswith("_D5_UNVERIFIED") for code in hostops._CLEAN_RUNTIME_PROBE_OBSERVED_CODES))
        self.session.return_value = 0
        receipt = self.invoke_probe()
        self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", "WINDOWS_OWNER_CONTEXT_UNVERIFIED"))
        self.source.assert_not_called()
        self.reconcile.assert_not_called()
        self.session.return_value = 7

        cases = (
            ({"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED"},
             "OBSERVED", "DEMAND_READY_JOURNAL_COMPLETE_D5_ENABLED"),
            ({"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "DISABLED_ATTESTED"},
             "OBSERVED", "DEMAND_READY_JOURNAL_COMPLETE_D5_DISABLED"),
            ({"task": "DEMAND_READY", "journal": "FRESH", "d5": "ENABLED_ATTESTED"},
             "OBSERVED", "DEMAND_READY_JOURNAL_FRESH_D5_ENABLED"),
            ({"task": "DEMAND_READY", "journal": "FRESH", "d5": "DISABLED_ATTESTED"},
             "OBSERVED", "DEMAND_READY_JOURNAL_FRESH_D5_DISABLED"),
            ({"task": "LEGACY_PT1M_READY", "journal": "FRESH", "d5": "ENABLED_ATTESTED"},
             "OBSERVED", "LEGACY_PT1M_READY_JOURNAL_FRESH_D5_ENABLED"),
            ({"task": "LEGACY_PT1M_READY", "journal": "FRESH", "d5": "DISABLED_ATTESTED"},
             "OBSERVED", "LEGACY_PT1M_READY_JOURNAL_FRESH_D5_DISABLED"),
            # D5_ABSENT must HOLD while retaining explicit factual visibility.
            ({"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "ABSENT"},
             "HOLD", "DEMAND_READY_JOURNAL_COMPLETE_D5_ABSENT"),
            ({"task": "DEMAND_READY", "journal": "FRESH", "d5": "ABSENT"},
             "HOLD", "DEMAND_READY_JOURNAL_FRESH_D5_ABSENT"),
            ({"task": "LEGACY_PT1M_READY", "journal": "FRESH", "d5": "ABSENT"},
             "HOLD", "LEGACY_PT1M_READY_JOURNAL_FRESH_D5_ABSENT"),
            # Partial/ambiguous/mismatched/unverified journal and unverified D5 must HOLD.
            ({"task": "DEMAND_READY", "journal": "ROLLBACK_ONLY", "d5": "ENABLED_ATTESTED"},
             "HOLD", "DEMAND_READY_ROLLBACK_ONLY_D5_ENABLED"),
            ({"task": "DEMAND_READY", "journal": "COMPLETE_ONLY", "d5": "ENABLED_ATTESTED"},
             "HOLD", "DEMAND_READY_COMPLETE_ONLY_D5_ENABLED"),
            ({"task": "DEMAND_READY", "journal": "UNVERIFIED", "d5": "ENABLED_ATTESTED"},
             "HOLD", "DEMAND_READY_JOURNAL_UNVERIFIED_D5_ENABLED"),
            ({"task": "DEMAND_READY", "journal": "MISMATCH", "d5": "ENABLED_ATTESTED"},
             "HOLD", "DEMAND_READY_JOURNAL_MISMATCH_D5_ENABLED"),
            ({"task": "LEGACY_PT1M_READY", "journal": "ROLLBACK_ONLY", "d5": "ENABLED_ATTESTED"},
             "HOLD", "LEGACY_PT1M_READY_ROLLBACK_ONLY_D5_ENABLED"),
            ({"task": "LEGACY_PT1M_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED"},
             "HOLD", "LEGACY_PT1M_READY_JOURNAL_MISMATCH_D5_ENABLED"),
            ({"task": "ABSENT", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED"},
             "HOLD", "TASK_ABSENT_JOURNAL_MISMATCH_D5_ENABLED"),
            ({"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "UNVERIFIED"},
             "HOLD", "DEMAND_READY_JOURNAL_COMPLETE_D5_UNVERIFIED"),
            ({"task": "LEGACY_PT1M_READY", "journal": "FRESH", "d5": "UNVERIFIED"},
             "HOLD", "LEGACY_PT1M_READY_JOURNAL_FRESH_D5_UNVERIFIED"),
            # Absent/disabled/not-ready/runtime-bad/multiple/conflict/unavailable must HOLD.
            ({"task": "DEMAND_DISABLED", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED"},
             "HOLD", "DEMAND_DISABLED_JOURNAL_COMPLETE_D5_ENABLED"),
            ({"task": "DEMAND_NOT_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED"},
             "HOLD", "DEMAND_NOT_READY_JOURNAL_COMPLETE_D5_ENABLED"),
            ({"task": "DEMAND_RUNTIME_BAD", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED"},
             "HOLD", "DEMAND_RUNTIME_BAD_JOURNAL_COMPLETE_D5_ENABLED"),
            ({"task": "LEGACY_PT1M_DISABLED", "journal": "FRESH", "d5": "ENABLED_ATTESTED"},
             "HOLD", "LEGACY_PT1M_DISABLED_JOURNAL_FRESH_D5_ENABLED"),
            ({"task": "LEGACY_PT1M_NOT_READY", "journal": "FRESH", "d5": "ENABLED_ATTESTED"},
             "HOLD", "LEGACY_PT1M_NOT_READY_JOURNAL_FRESH_D5_ENABLED"),
            ({"task": "ABSENT", "journal": "FRESH", "d5": "ENABLED_ATTESTED"},
             "HOLD", "TASK_ABSENT_JOURNAL_FRESH_D5_ENABLED"),
            ({"task": "MULTIPLE_CANDIDATES", "journal": "FRESH", "d5": "ENABLED_ATTESTED"},
             "HOLD", "TASK_MULTIPLE_JOURNAL_FRESH_D5_ENABLED"),
            ({"task": "CONFLICT", "journal": "FRESH", "d5": "ENABLED_ATTESTED"},
             "HOLD", "TASK_CONFLICT_JOURNAL_FRESH_D5_ENABLED"),
            ({"task": "UNAVAILABLE", "journal": "UNVERIFIED", "d5": "UNVERIFIED"},
             "HOLD", "TASK_UNAVAILABLE_JOURNAL_UNVERIFIED_D5_UNVERIFIED"),
            ({"error": "WINDOWS_OWNER_CONTEXT_UNVERIFIED"},
             "HOLD", "WINDOWS_OWNER_CONTEXT_UNVERIFIED"),
            ({"task": "PRIVATE", "journal": "FRESH", "d5": "ABSENT"},
             "HOLD", "UNKNOWN"),
            ("PRIVATE", "HOLD", "UNKNOWN"),
        )
        for reply, expected_status, expected_code in cases:
            with self.subTest(reply=reply):
                self.reconcile.reset_mock()
                self.reconcile.return_value = reply
                receipt = self.invoke_probe()
                self.assertEqual((receipt["status"], receipt["code"]), (expected_status, expected_code))
                self.assertIn(receipt["code"], hostops._CLEAN_RUNTIME_PROBE_CODES)
                self.assertRegex(receipt["code"], r"^[A-Z0-9_]{2,96}$")
                self.reconcile.assert_called_once_with(self.owner, self.target, self.candidates)

        self.reconcile.return_value = {"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED"}
        self.session.side_effect = (7, 7, 8)
        receipt = self.invoke_probe()
        self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", "WINDOWS_OWNER_CONTEXT_UNVERIFIED"))
        self.task.assert_not_called()
        self.record.assert_not_called()
        self.inventory.assert_not_called()
        self.probe.assert_not_called()

    def test_runtime_probe_distinguishes_conflict_and_d5_unverified_leaf_reasons(self):
        self.assertEqual(set(hostops._CLEAN_RECONCILE_TASK_MAP), set(self.acq._CLEAN_RECONCILE_TASK_VALUES))
        self.assertEqual(set(hostops._CLEAN_RECONCILE_TASK_MAP.values()), set(hostops._CLEAN_RUNTIME_TASK_CODES))
        self.assertEqual(set(hostops._CLEAN_RECONCILE_JOURNAL_MAP), set(self.acq._CLEAN_RECONCILE_JOURNAL_VALUES))
        self.assertEqual(set(hostops._CLEAN_RECONCILE_JOURNAL_MAP.values()), set(hostops._CLEAN_RUNTIME_JOURNAL_CODES))
        self.assertEqual(set(hostops._CLEAN_RECONCILE_D5_MAP), set(self.acq._CLEAN_RECONCILE_D5_VALUES))
        self.assertEqual(set(hostops._CLEAN_RECONCILE_D5_MAP.values()), set(hostops._CLEAN_RUNTIME_D5_CODES))
        for code in hostops._CLEAN_RUNTIME_PROBE_CODES:
            self.assertRegex(code, r"^[A-Z0-9_]{2,96}$")
        for forbidden in ("TASK_CONFLICT", "TASK_FOREIGN", "D5_ABSENT", "D5_UNVERIFIED"):
            self.assertFalse(any(forbidden in code for code in hostops._CLEAN_RUNTIME_PROBE_OBSERVED_CODES))

        expected_keys = {
            "status", "code", "operation", "cdp_port", "source_sha",
            "exact_chat_verified", "wake_sent", "arbitrary_shell_enabled", "remote_shell_enabled",
        }
        cases = (
            (set(), {"task": "CONFLICT_LEGACY_ARGS", "journal": "FRESH", "d5": "UNVERIFIED_EXE"},
             "TASK_CONFLICT_PT1M_ARGS_JOURNAL_FRESH_D5_UNVERIFIED_EXE"),
            ({"AutostartPending"}, {"task": "CONFLICT_LEGACY_ARGS", "journal": "FRESH", "d5": "UNVERIFIED_EXE"},
             "AUTOSTART_PENDING_TASK_CONFLICT_PT1M_ARGS_JOURNAL_FRESH_D5_UNVERIFIED_EXE"),
            ({"AutostartPending"}, {"task": "CONFLICT_DEMAND_PWSH_PATH", "journal": "COMPLETE", "d5": "UNVERIFIED_SCRIPT"},
             "AUTOSTART_PENDING_TASK_CONFLICT_DEMAND_PWSH_PATH_JOURNAL_MISMATCH_D5_UNVERIFIED_SCRIPT"),
            (set(), {"task": "CONFLICT_BASE_OWNER", "journal": "FRESH", "d5": "UNVERIFIED_ACL"},
             "TASK_CONFLICT_OWNER_JOURNAL_FRESH_D5_UNVERIFIED_ACL"),
            (set(), {"task": "CONFLICT_BASE_SETTINGS", "journal": "ROLLBACK_ONLY", "d5": "UNVERIFIED_CUSTODY"},
             "TASK_CONFLICT_SETTINGS_ROLLBACK_ONLY_D5_UNVERIFIED_CUSTODY"),
            (set(), {"task": "CONFLICT_LEGACY_PATH", "journal": "FRESH", "d5": "UNVERIFIED_PRINCIPAL"},
             "TASK_CONFLICT_PT1M_PATH_JOURNAL_FRESH_D5_UNVERIFIED_PRINCIPAL"),
            (set(), {"task": "CONFLICT_LEGACY_WORKDIR", "journal": "FRESH", "d5": "UNVERIFIED_ACTION"},
             "TASK_CONFLICT_PT1M_WORKDIR_JOURNAL_FRESH_D5_UNVERIFIED_ACTION"),
            (set(), {"task": "FOREIGN_CANDIDATE", "journal": "FRESH", "d5": "UNVERIFIED_MOVED"},
             "TASK_FOREIGN_JOURNAL_FRESH_D5_UNVERIFIED_MOVED"),
            (set(), {"task": "CONFLICT_TRIGGER_COUNT", "journal": "FRESH", "d5": "UNVERIFIED_MULTIPLE"},
             "TASK_CONFLICT_TRIGGERS_JOURNAL_FRESH_D5_UNVERIFIED_MULTIPLE"),
            (set(), {"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "UNVERIFIED_TRIGGER"},
             "DEMAND_READY_JOURNAL_COMPLETE_D5_UNVERIFIED_TRIGGER"),
            (set(), {"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "UNVERIFIED_STATE"},
             "DEMAND_READY_JOURNAL_COMPLETE_D5_UNVERIFIED_STATE"),
            (set(), {"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "UNVERIFIED_BASE"},
             "DEMAND_READY_JOURNAL_COMPLETE_D5_UNVERIFIED_BASE"),
        )
        for pending_set, reply, expected_code in cases:
            with self.subTest(pending=sorted(pending_set), reply=reply):
                self.pending.clear()
                self.pending.update(pending_set)
                self.launch_pending.return_value = False
                self.reconcile.reset_mock()
                self.reconcile.return_value = reply
                receipt = self.invoke_probe()
                self.assertEqual(set(receipt), expected_keys)
                self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", expected_code))
                self.assertIn(receipt["code"], hostops._CLEAN_RUNTIME_PROBE_CODES)
                self.assertRegex(receipt["code"], r"^[A-Z0-9_]{2,96}$")
                self.assertEqual(self.pending, pending_set)
                self.reconcile.assert_called_once_with(self.owner, self.target, self.candidates)
        self.pending.clear()
        self.task.assert_not_called()
        self.record.assert_not_called()
        self.inventory.assert_not_called()
        self.probe.assert_not_called()

    def test_runtime_probe_separates_pending_markers_and_reconciled_states_read_only(self):
        expected_keys = {
            "status", "code", "operation", "cdp_port", "source_sha",
            "exact_chat_verified", "wake_sent", "arbitrary_shell_enabled", "remote_shell_enabled",
        }
        reconcile_cases = (
            ({"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED"},
             None, "DEMAND_READY_JOURNAL_COMPLETE_D5_ENABLED"),
            ({"task": "LEGACY_PT1M_READY", "journal": "ROLLBACK_ONLY", "d5": "DISABLED_ATTESTED"},
             None, "LEGACY_PT1M_READY_ROLLBACK_ONLY_D5_DISABLED"),
            ({"task": "ABSENT", "journal": "FRESH", "d5": "ABSENT"},
             None, "TASK_ABSENT_JOURNAL_FRESH_D5_ABSENT"),
            ({"task": "CONFLICT", "journal": "COMPLETE_ONLY", "d5": "UNVERIFIED"},
             None, "TASK_CONFLICT_COMPLETE_ONLY_D5_UNVERIFIED"),
            ("PRIVATE task reply", None, "TASK_UNAVAILABLE_JOURNAL_UNVERIFIED_D5_UNVERIFIED"),
            (None, OSError("PRIVATE Schedule.Service error"), "TASK_UNAVAILABLE_JOURNAL_UNVERIFIED_D5_UNVERIFIED"),
            (None, self.acq._Clean9444Hold("PRIVATE task hold"), "TASK_UNAVAILABLE_JOURNAL_UNVERIFIED_D5_UNVERIFIED"),
        )
        marker_cases = (
            ("autostart_only", {"AutostartPending"}, False, "AUTOSTART_PENDING"),
            ("launch_only", set(), True, "LAUNCH_PENDING"),
            ("both_markers", {"AutostartPending"}, True, "BOTH_PENDING"),
        )
        for label, autostart_set, launch_value, prefix in marker_cases:
            for reply, error, suffix in reconcile_cases:
                with self.subTest(marker=label, reply=reply, error=type(error).__name__ if error else None):
                    self.pending.clear()
                    self.pending.update(autostart_set)
                    self.launch_pending.return_value = launch_value
                    self.reconcile.reset_mock()
                    self.reconcile.side_effect = error
                    self.reconcile.return_value = reply
                    receipt = self.invoke_probe()
                    expected_code = f"{prefix}_{suffix}"
                    self.assertEqual(set(receipt), expected_keys)
                    self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", expected_code))
                    self.assertIn(receipt["code"], hostops._CLEAN_RUNTIME_PROBE_CODES)
                    self.assertRegex(receipt["code"], r"^[A-Z0-9_]{2,96}$")
                    self.assertEqual(receipt["cdp_port"], 9444)
                    self.assertEqual(receipt["source_sha"], self.sha)
                    self.assertFalse(receipt["exact_chat_verified"])
                    self.assertFalse(receipt["wake_sent"])
                    self.assertFalse(receipt["arbitrary_shell_enabled"])
                    self.assertFalse(receipt["remote_shell_enabled"])
                    self.reconcile.assert_called_once_with(self.owner, self.target, self.candidates)
                    # Read-only guarantee: markers stay intact and WRITE still HOLDs.
                    self.assertEqual(self.pending, autostart_set)
        self.pending.clear()
        self.pending.add("AutostartPending")
        self.launch_pending.return_value = False
        self.reconcile.side_effect = None
        self.task.return_value = "ALREADY_ENROLLED"
        self.assertEqual(self.invoke()["code"], "UNKNOWN_EFFECT")
        self.record.assert_not_called()
        self.inventory.assert_not_called()
        self.probe.assert_not_called()

    def test_runtime_probe_handles_marker_read_failures_target_errors_and_state_changes(self):
        # Marker read failure still observes task/journal/D5 read-only and HOLDs.
        for reply, expected in (
            ({"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED"},
             "MARKER_UNVERIFIED_DEMAND_READY_JOURNAL_COMPLETE_D5_ENABLED"),
            ({"task": "ABSENT", "journal": "FRESH", "d5": "ABSENT"},
             "MARKER_UNVERIFIED_TASK_ABSENT_JOURNAL_FRESH_D5_ABSENT"),
            ({"task": "CONFLICT", "journal": "ROLLBACK_ONLY", "d5": "UNVERIFIED"},
             "MARKER_UNVERIFIED_TASK_CONFLICT_ROLLBACK_ONLY_D5_UNVERIFIED"),
            ({"task": "UNAVAILABLE", "journal": "UNVERIFIED", "d5": "UNVERIFIED"},
             "MARKER_UNVERIFIED_TASK_UNAVAILABLE_JOURNAL_UNVERIFIED_D5_UNVERIFIED"),
        ):
            with self.subTest(reply=reply), patch.object(
                self.acq, "_clean_pending", side_effect=PermissionError("PRIVATE HKCU\\Software\\AI-Commerce")
            ):
                self.reconcile.reset_mock()
                self.reconcile.return_value = reply
                receipt = self.invoke_probe()
                self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", expected))
                self.assertIn(receipt["code"], hostops._CLEAN_RUNTIME_PROBE_CODES)
                self.assertNotIn("HKCU", json.dumps(receipt))
                self.reconcile.assert_called_once_with(self.owner, self.target, self.candidates)

        # Pending marker with target/profile unavailable skips task read and preserves marker visibility.
        self.pending.add("AutostartPending")
        for exc, expected in (
            (self.acq._Clean9444Hold("PROFILE_UNAVAILABLE"), "AUTOSTART_PENDING_PROFILE_UNAVAILABLE"),
            (self.acq._Clean9444Hold("CHROME_PATH_INVALID"), "AUTOSTART_PENDING_TARGET_UNAVAILABLE"),
            (OSError("PRIVATE profile path"), "AUTOSTART_PENDING_TARGET_UNAVAILABLE"),
        ):
            with self.subTest(expected=expected):
                self.reconcile.reset_mock()
                self.target_read.side_effect = exc
                receipt = self.invoke_probe()
                self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", expected))
                self.reconcile.assert_not_called()
        self.target_read.side_effect = None

        # Wrong session overrides pending markers before and after task read.
        self.reconcile.return_value = {"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED"}
        for session_seq in ((0,), (7, 8), (7, 7, 8)):
            with self.subTest(session_seq=session_seq):
                self.session.side_effect = session_seq
                receipt = self.invoke_probe()
                self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", "WINDOWS_OWNER_CONTEXT_UNVERIFIED"))
        self.session.side_effect = None
        self.session.return_value = 7
        self.reconcile.return_value = {"error": "WINDOWS_OWNER_CONTEXT_UNVERIFIED"}
        receipt = self.invoke_probe()
        self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", "WINDOWS_OWNER_CONTEXT_UNVERIFIED"))

        # Concurrent marker transition during probe HOLDs and never reports OBSERVED.
        self.pending.clear()
        self.reconcile.return_value = {"task": "DEMAND_READY", "journal": "COMPLETE", "d5": "ENABLED_ATTESTED"}
        for seq in ((False, True), (True, False)):
            with self.subTest(seq=seq), patch.object(self.acq, "_clean_pending", side_effect=seq):
                receipt = self.invoke_probe()
                self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", "PENDING_STATE_CHANGED"))
        self.task.assert_not_called()
        self.record.assert_not_called()
        self.inventory.assert_not_called()
        self.probe.assert_not_called()

    def test_runtime_probe_maps_native_exceptions_to_closed_sanitized_codes(self):
        private_detail = (
            f"PRIVATE {self.local} {self.sid} HKCU\\Software\\AI-Commerce\\WakeHub-9444-Clean "
            "https://chatgpt.com/c/secret-chat-id credential=secret-token --user-data-dir=C:\\secret"
        )
        cases = (
            (ImportError(private_detail), "PROBE_EXCEPTION_IMPORT"),
            (PermissionError(private_detail), "PROBE_EXCEPTION_PERMISSION"),
            (subprocess.TimeoutExpired(["powershell.exe", private_detail], 15), "PROBE_EXCEPTION_TIMEOUT"),
            (OSError(private_detail), "PROBE_EXCEPTION_OS"),
            (ValueError(private_detail), "PROBE_EXCEPTION_DATA"),
            (TypeError(private_detail), "PROBE_EXCEPTION_DATA"),
            (KeyError(private_detail), "PROBE_EXCEPTION_DATA"),
            (AttributeError(private_detail), "PROBE_EXCEPTION_DATA"),
            (RuntimeError(private_detail), "PROBE_EXCEPTION_RUNTIME"),
            (LookupError(private_detail), "PROBE_EXCEPTION_OTHER"),
        )
        for error, expected in cases:
            with self.subTest(error=type(error).__name__), \
                    patch.object(hostops, "_observe_clean_runtime", side_effect=error):
                receipt = self.invoke_probe()
                self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", expected))
                self.assertIn(receipt["code"], hostops._CLEAN_RUNTIME_PROBE_CODES)
                self.assertRegex(receipt["code"], r"^[A-Z0-9_]{2,96}$")
                raw = json.dumps(receipt)
                for secret in ("PRIVATE", "HKCU", "WakeHub", "chatgpt.com", "secret-chat-id",
                               "credential", "secret-token", "--user-data-dir", self.sid, str(self.local)):
                    self.assertNotIn(secret, raw)
        # Non-allowlisted return value from _observe_clean_runtime fails closed to HOLD/UNKNOWN.
        with patch.object(hostops, "_observe_clean_runtime", return_value=private_detail):
            receipt = self.invoke_probe()
            self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", "UNKNOWN"))
            self.assertNotIn("PRIVATE", json.dumps(receipt))
        self.task.assert_not_called()
        self.record.assert_not_called()
        self.inventory.assert_not_called()
        self.probe.assert_not_called()

    def test_runtime_probe_reports_unavailable_profile_before_task_read(self):
        for code, expected in (
            ("PROFILE_UNAVAILABLE", "PROFILE_UNAVAILABLE"),
            ("PRIVATE path and SID", "TARGET_READ_UNVERIFIED"),
        ):
            with self.subTest(code=code):
                self.target_read.side_effect = self.acq._Clean9444Hold(code)
                receipt = self.invoke_probe()
                self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", expected))
                self.target_read.assert_called_with(self.owner)
        self.reconcile.assert_not_called()
        self.task.assert_not_called()
        self.inventory.assert_not_called()
        self.record.assert_not_called()

    def test_exact_grant_and_parameters_required_before_any_native_call(self):
        read = event("read", self.op, {})
        with self.assertRaisesRegex(hostops.Denied, "READ_OPERATION_DENIED"):
            hostops.parse_event(read, "read")
        request = hostops.parse_event(self.event, "write")
        self.assertEqual(hostops.perform(request)["code"], "PROTECTED_HOSTOPS_ONLY")
        for payload, expected in (
            (event("write", self.op, {}, comment="APPROVE WRITE #17 "), "EXACT_WRITE_APPROVAL_REQUIRED"),
            (event("write", self.op, {"port": 9444}), "INVALID_PARAMETERS"),
            (event("write", self.op, None), "INVALID_REQUEST"),
        ):
            self.assertEqual(self.invoke(payload)["code"], expected)
        for section in ("issue", "comment", "sender"):
            payload = copy.deepcopy(self.event)
            user = payload[section] if section == "sender" else payload[section]["user"]
            user["login"] = "another-account"
            self.assertEqual(self.invoke(payload)["status"], "BLOCKED")
        self.session.assert_not_called()
        self.task.assert_not_called()

    def test_source_executor_and_unprotected_or_replayed_events_have_no_effect(self):
        for name, value in (("RUNNER_NAME", "AI-COMMERCE-EF02-01"), ("GITHUB_EVENT_NAME", "pull_request"),
                            ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_WORKFLOW_SHA", "b" * 40),
                            ("AI_COMMERCE_GIT_GUARD_ACTIVE", "1")):
            with patch.dict(self.env, {name: value}):
                self.assertEqual(self.invoke()["code"], "PROTECTED_HOSTOPS_ONLY")
        self.session.assert_not_called()
        self.task.assert_not_called()

    def test_session_owner_profile_and_custody_fail_before_task_access(self):
        for owner, name, result in (
            (self.acq, "_interactive_session", 0),
            (self.wake, "_windows_process_sid", "S-1-5-18"),
        ):
            with patch.object(owner, name, return_value=result):
                self.assertEqual(self.invoke()["code"], "ENROLLMENT_IDENTITY_UNPROVEN")
        for owner, name, error, expected in (
            (self.acq, "_interactive_session", self.acq.AcquisitionBlocked, "ELEVATED_CHROME_DISALLOWED"),
            (self.acq, "_interactive_session", self.acq.AcquisitionBlocked, "WINDOWS_INTERACTIVE_REQUIRED"),
            (hostops, "_persistent_clean_source", hostops.Denied, "LOCAL_MAIN_SYNC_REQUIRED"),
            (self.acq, "_clean_target", self.acq._Clean9444Hold, "PROFILE_UNAVAILABLE"),
            (self.acq, "_clean_inventory", self.acq._Clean9444Hold, "FOREIGN_PORT_OWNER"),
            (self.acq, "_clean_inventory", self.acq._Clean9444Hold, "BROWSER_OWNERSHIP_UNPROVEN"),
        ):
            with patch.object(owner, name, side_effect=error(expected)):
                self.assertEqual(self.invoke()["code"], expected)
        self.task.assert_not_called()
        self.record.assert_not_called()
        self.probe.assert_not_called()

    def test_pending_already_conflict_and_unavailable_are_no_op(self):
        for code, expected in (("ALREADY_ENROLLED", "INSTALLED_BUT_NOT_RUNNING"),
                               ("AUTOSTART_CONFLICT", "AUTOSTART_CONFLICT"),
                               ("AUTOSTART_RUNTIME_UNAVAILABLE", "AUTOSTART_RUNTIME_UNAVAILABLE"),
                               ("PRIVATE path and SID", "AUTOSTART_RUNTIME_UNAVAILABLE")):
            self.task.return_value = code
            self.task.reset_mock()
            self.assertEqual(self.invoke()["code"], expected)
            self.task.assert_called_once_with(self.owner, self.target, self.candidates, install=False)
        self.pending.add("AutostartPending")
        self.task.reset_mock()
        self.assertEqual(self.invoke()["code"], "UNKNOWN_EFFECT")
        self.task.assert_not_called()
        self.record.assert_not_called()

    def test_existing_browser_prevents_first_task_registration(self):
        self.task.return_value = "AUTOSTART_NOT_ENROLLED"
        self.inventory.return_value = ("OWNED", (5, self.target.executable, "created", "PRIVATE"))
        self.assertEqual(self.invoke()["code"], "PROFILE_IN_USE")
        self.task.assert_called_once_with(self.owner, self.target, self.candidates, install=False)
        self.record.assert_not_called()
        self.probe.assert_not_called()

    def test_create_then_independent_readback_leaves_chrome_idle(self):
        directory = self.local / "AI-Commerce" / self.acq._CLEAN_9444_PROFILE
        (directory / "Default").mkdir(parents=True)
        executable = self.local / "Google" / "Chrome" / "Application" / "chrome.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
        self.target = self.acq._ChromeTarget(executable.resolve(), directory, "Default", "http://127.0.0.1:9444", 9444)
        self.candidates = (self.target.executable,)
        self.mock(self.acq, "_native_known_folder", return_value=self.local / "Program Files")
        self.target_read.side_effect = self.real_target
        windows_acl = self.mock(self.handoff, "_windows_acl", return_value=("S-1-5-18", (("allow", self.sid),)))
        private_id = self.mock(self.handoff, "_private_identity", return_value="S-1-5-18")
        self.reconcile.return_value = {"task": "ABSENT", "journal": "FRESH", "d5": "ENABLED_ATTESTED"}
        receipt = self.invoke_probe()
        self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", "TASK_ABSENT_JOURNAL_FRESH_D5_ENABLED"))
        self.reconcile.assert_called_once_with(self.owner, self.target, self.candidates)
        self.task.assert_not_called()

        responses = iter(("AUTOSTART_NOT_ENROLLED", "ENROLLED", "ALREADY_ENROLLED"))

        def scheduler(owner, target, candidates, *, install, start=False):
            self.assertEqual((owner, target, candidates), (self.owner, self.target, self.candidates))
            self.assertFalse(start)
            self.assertEqual(self.slot, self.task.call_count != 1)
            return next(responses)

        self.task.side_effect = scheduler
        with patch.object(hostops.time, "sleep") as wait:
            receipt = self.invoke()
        wait.assert_not_called()
        self.assertEqual((receipt["status"], receipt["code"]), ("OBSERVED", "INSTALLED_BUT_NOT_RUNNING"))
        self.assertIs(receipt["devtools_protocol_ok"], False)
        for key in ("exact_chat_verified", "wake_sent", "arbitrary_shell_enabled"):
            self.assertIs(receipt[key], False)
        self.assertEqual(self.task.call_args_list, [
            call(self.owner, self.target, self.candidates, install=False),
            call(self.owner, self.target, self.candidates, install=True),
            call(self.owner, self.target, self.candidates, install=False),
        ])
        self.probe.assert_not_called()
        self.assertFalse(self.pending)
        windows_acl.assert_not_called()
        private_id.assert_not_called()

    def test_uncertain_registration_persists_and_never_replays(self):
        for responses in (("AUTOSTART_NOT_ENROLLED", "UNKNOWN_EFFECT"),
                          ("AUTOSTART_NOT_ENROLLED", "ENROLLED", "AUTOSTART_CONFLICT")):
            self.pending.clear()
            self.task.reset_mock()
            self.task.side_effect = responses
            self.assertEqual(self.invoke()["code"], "UNKNOWN_EFFECT")
            self.assertEqual(self.task.call_count, len(responses))
            self.assertEqual(self.pending, {"AutostartPending"})
            self.assertEqual(self.invoke()["code"], "UNKNOWN_EFFECT")
            self.assertEqual(self.task.call_count, len(responses))
        self.probe.assert_not_called()

    def test_migration_uses_only_the_protected_owner_write_and_leaves_browser_untouched(self):
        op = "migrate_clean_9444_demand_task"
        request = event("write", op, {})
        self.assertEqual(hostops.perform(hostops.parse_event(request, "write"))["code"],
                         "PROTECTED_HOSTOPS_ONLY")
        for payload, expected in (
            (event("write", op, {"command": "PRIVATE"}), "INVALID_PARAMETERS"),
            (event("write", op, {}, comment="APPROVE WRITE #17 "), "EXACT_WRITE_APPROVAL_REQUIRED"),
        ):
            self.assertEqual(self.invoke(payload)["code"], expected)
        with patch.dict(self.env, GITHUB_RUN_ATTEMPT="2"):
            self.assertEqual(self.invoke(request)["code"], "PROTECTED_HOSTOPS_ONLY")
        self.task.assert_not_called()

        def scheduler(owner, target, candidates, *, install, migrate):
            self.assertTrue(self.slot)
            self.assertEqual((owner, target, candidates), (self.owner, self.target, self.candidates))
            self.assertFalse(install)
            self.assertTrue(migrate)
            return "DEMAND_MIGRATED"

        self.task.side_effect = scheduler
        with patch.dict(self.env, GITHUB_WORKFLOW_SHA=""):
            receipt = self.invoke(request)
        self.assertEqual((receipt["operation"], receipt["code"], receipt["status"]),
                         (op, "DEMAND_MIGRATED", "OBSERVED"))
        self.assertNotIn("devtools_protocol_ok", receipt)
        self.task.assert_called_once()
        self.source.assert_called_once_with(self.acq, self.sha)
        self.inventory.assert_not_called()
        self.probe.assert_not_called()
        self.record.assert_not_called()

    def test_migration_projects_ambiguity_replay_and_unknown_effect_without_retry(self):
        request = event("write", "migrate_clean_9444_demand_task", {})
        for code in ("AUTOSTART_CONFLICT", "TASK_MIGRATION_AMBIGUOUS", "TASK_MIGRATION_NOT_READY",
                     "TASK_MIGRATION_REPLAY", "UNKNOWN_EFFECT", "PRIVATE native details"):
            with self.subTest(code=code):
                self.task.reset_mock()
                self.task.return_value = code
                receipt = self.invoke(request)
                self.assertEqual(receipt["status"], "HOLD")
                self.assertEqual(receipt["code"], "UNKNOWN_EFFECT" if code.startswith("PRIVATE") else code)
                self.task.assert_called_once_with(self.owner, self.target, self.candidates,
                                                  install=False, migrate=True)
        self.inventory.assert_not_called()
        self.probe.assert_not_called()

    def test_reset_write_requires_fixed_operation_and_exact_owner_comment(self):
        op = "reset_clean_9444_tasks"
        approved = event("write", op, {})
        request = hostops.parse_event(approved, "write")
        self.assertEqual(request, {"issue_number": 17, "operation": op, "parameters": {}})
        self.assertEqual(hostops.perform(request)["code"], "PROTECTED_HOSTOPS_ONLY")
        for payload, mode, expected in (
            (event("read", op, {}), "read", "READ_OPERATION_DENIED"),
            (event("write", "reset_clean_9444_tasks_other", {}), "write", "WRITE_SCOPE_DENIED"),
            (event("write", op, {"task": "arbitrary"}), "write", "INVALID_PARAMETERS"),
            (event("write", op, {}, comment="APPROVE WRITE #18"), "write", "EXACT_WRITE_APPROVAL_REQUIRED"),
            ({**approved, "action": "edited"}, "write", "WRITE_EVENT_DENIED"),
        ):
            with self.subTest(expected=expected), self.assertRaisesRegex(hostops.Denied, f"^{expected}$"):
                hostops.parse_event(payload, mode)
        for section, expected in (
            ("issue", "ISSUE_OWNER_OR_STATE_DENIED"),
            ("sender", "NON_OWNER_EVENT"),
            ("comment", "EXACT_WRITE_APPROVAL_REQUIRED"),
        ):
            payload = copy.deepcopy(approved)
            actor = payload[section] if section == "sender" else payload[section]["user"]
            actor["login"] = "another-account"
            with self.subTest(section=section), self.assertRaisesRegex(hostops.Denied, f"^{expected}$"):
                hostops.parse_event(payload, "write")
        self.task.assert_not_called()

    def test_reset_dispatches_once_and_projects_only_verified_reset_success(self):
        op = "reset_clean_9444_tasks"
        approved = event("write", op, {})
        with patch.object(hostops, "_install_protected_clean_autostart") as install:
            for reply, code, status in (
                ("TASK_RESET_VERIFIED", "TASK_RESET_VERIFIED", "OBSERVED"),
                ("TASK_RESET_ALREADY_READY", "TASK_RESET_ALREADY_READY", "OBSERVED"),
                ("AUTOSTART_CONFLICT", "AUTOSTART_CONFLICT", "HOLD"),
                ("TASK_RESET_NOT_READY", "TASK_RESET_NOT_READY", "HOLD"),
                ("TASK_RESET_DRIFT", "TASK_RESET_DRIFT", "HOLD"),
                ("TASK_RESET_D5_UNVERIFIED", "TASK_RESET_D5_UNVERIFIED", "HOLD"),
                ("PRIVATE result", "UNKNOWN_EFFECT", "HOLD"),
                ("DEMAND_MIGRATED", "UNKNOWN_EFFECT", "HOLD"),
                ("UNKNOWN_EFFECT", "UNKNOWN_EFFECT", "HOLD"),
            ):
                with self.subTest(reply=reply):
                    self.task.reset_mock()
                    self.task.return_value = reply
                    receipt = self.invoke(approved)
                    self.assertEqual((receipt["operation"], receipt["code"], receipt["status"]), (op, code, status))
                    self.assertEqual(receipt["cdp_port"], 9444)
                    self.assertEqual(receipt["approval_channel"], "OWNER_GITHUB_COMMENT")
                    self.assertEqual(receipt["source_sha"], self.sha)
                    self.assertFalse(receipt["wake_sent"])
                    self.task.assert_called_once_with(self.owner, self.target, self.candidates,
                                                      install=False, reset=True)
        install.assert_not_called()
        self.inventory.assert_not_called()
        self.record.assert_not_called()
        self.probe.assert_not_called()

    def _clean_source_fixture(self):
        root = self.local / "persistent"
        (root / ".git").mkdir(parents=True, exist_ok=True)
        workflow = Path(self.env["GITHUB_WORKSPACE"])
        source, peer = (checkout / hostops._ACQUISITION_SOURCE for checkout in (root, workflow))
        for path in (source, peer):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"same reviewed source")
        git = {
            ("rev-parse", "--show-toplevel"): str(root),
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("config", "--get", "remote.origin.url"): "https://github.com/trung-via/ai-commerce.git",
            ("rev-parse", "HEAD", "refs/remotes/origin/main"): f"{self.sha}\n{self.sha}\n",
            ("status", "--porcelain=v1", "--untracked-files=all"): "",
            ("ls-files", "-v", "-z", "--", "src"): "H " + hostops._ACQUISITION_SOURCE.as_posix() + "\0",
        }
        return root, workflow, source, peer, git

    def test_persistent_source_refuses_stale_dirty_reparse_and_byte_mismatch(self):
        root, workflow, source, peer, git = self._clean_source_fixture()
        real_source = self.real_source
        with patch.dict(hostops.ROOTS, workspace=root), \
                patch.object(hostops, "__file__", workflow / "src/ai_commerce/hostops_v3/personal.py"), \
                patch.object(self.acq, "__file__", peer), \
                patch.object(self.handoff, "_private_identity", side_effect=AssertionError("no public ACL check")), \
                patch.object(self.handoff, "_windows_acl", side_effect=AssertionError("no public ACL check")), \
                patch.object(hostops, "_git", side_effect=lambda *args: git[args]):
            self.assertEqual(real_source(self.acq, self.sha), source)
            # Benign setuptools editable metadata under src/ is allowed only with fixed non-code names.
            egg = root / "src" / "ai_commerce.egg-info"
            egg.mkdir()
            (egg / "PKG-INFO").write_bytes(b"Metadata-Version: 2.1\n")
            self.assertEqual(real_source(self.acq, self.sha), source)
            (egg / "evil.py").write_bytes(b"print('untracked')")
            with self.assertRaisesRegex(hostops.Denied, "^SOURCE_UNTRACKED_FILE$"):
                real_source(self.acq, self.sha)
            (egg / "evil.py").unlink()
            for args, bad, expected in (
                (("rev-parse", "--abbrev-ref", "HEAD"), "other", "LOCAL_MAIN_SYNC_REQUIRED"),
                (("config", "--get", "remote.origin.url"), "https://evil.invalid/repo.git", "LOCAL_MAIN_SYNC_REQUIRED"),
                (("rev-parse", "HEAD", "refs/remotes/origin/main"), "old\nold\n", "LOCAL_MAIN_SYNC_REQUIRED"),
                (("status", "--porcelain=v1", "--untracked-files=all"), " M README.md", "LOCAL_MAIN_SYNC_REQUIRED"),
                (("status", "--porcelain=v1", "--untracked-files=all"), "?? src/evil.py", "SOURCE_UNTRACKED_FILE"),
                (("ls-files", "-v", "-z", "--", "src"), "h " + hostops._ACQUISITION_SOURCE.as_posix() + "\0",
                 "SOURCE_INDEX_UNVERIFIED"),
            ):
                with patch.dict(git, {args: bad}), self.assertRaisesRegex(hostops.Denied, f"^{expected}$"):
                    real_source(self.acq, self.sha)
            untracked_py = source.with_name("untracked.py")
            untracked_py.write_bytes(b"untracked executable source")
            with self.assertRaisesRegex(hostops.Denied, "^SOURCE_UNTRACKED_FILE$"):
                real_source(self.acq, self.sha)
            untracked_py.unlink()
            with patch.object(hostops, "_is_reparse", side_effect=lambda path: path == source):
                with self.assertRaisesRegex(hostops.Denied, "^SOURCE_PATH_UNTRUSTED$"):
                    real_source(self.acq, self.sha)
            with patch.object(self.acq, "_no_links", side_effect=lambda path: path != root):
                with self.assertRaisesRegex(hostops.Denied, "^SOURCE_PATH_UNTRUSTED$"):
                    real_source(self.acq, self.sha)
            with patch.object(self.acq, "_no_links", side_effect=lambda path: path != peer):
                with self.assertRaisesRegex(hostops.Denied, "^SOURCE_PATH_UNTRUSTED$"):
                    real_source(self.acq, self.sha)
            with patch.object(hostops, "__file__", root / "src/ai_commerce/hostops_v3/personal.py"):
                with self.assertRaisesRegex(hostops.Denied, "^SOURCE_WORKFLOW_PATH_MISMATCH$"):
                    real_source(self.acq, self.sha)
            source.write_bytes(b"index-hidden edit")
            with self.assertRaisesRegex(hostops.Denied, "^SOURCE_BYTES_MISMATCH$"):
                real_source(self.acq, self.sha)

    def test_public_checkout_skips_windows_acl_while_private_handoff_remains_strict(self):
        # Unused public-checkout Windows ACL verifiers are removed.
        for removed in ("_windows_checkout_acl", "_check_public_checkout_acl", "_verify_checkout_acl"):
            self.assertFalse(hasattr(hostops, removed))
        self.assertNotIn("SOURCE_ACL_UNTRUSTED", hostops._CLEAN_PUBLIC_CODES)

        root, workflow, source, peer, git = self._clean_source_fixture()
        # Normal inherited read ACLs and formerly-rejected public-checkout ACL fixtures
        # (e.g. Authenticated Users modify, Everyone, foreign principal/owner, empty DACL).
        simulated_acls = (
            ("inherited_users_read", (self.sid, (("allow", self.sid), ("allow", "S-1-5-32-545")))),
            ("inherited_auth_users_modify", (self.sid, (("allow", self.sid), ("allow", "S-1-5-11")))),
            ("inherited_everyone", (self.sid, (("allow", self.sid), ("allow", "S-1-1-0")))),
            ("foreign_principal", (self.sid, (("allow", self.sid), ("allow", "S-1-5-21-999-1002")))),
            ("foreign_owner", ("S-1-5-21-999-1002", (("allow", self.sid),))),
            ("empty_dacl", (self.sid, ())),
        )
        for label, acl in simulated_acls:
            with self.subTest(label=label):
                with patch.dict(hostops.ROOTS, workspace=root), \
                        patch.object(hostops, "__file__", workflow / "src/ai_commerce/hostops_v3/personal.py"), \
                        patch.object(self.acq, "__file__", peer), \
                        patch.object(hostops, "_git", side_effect=lambda *args: git[args]), \
                        patch.object(self.handoff, "_windows_acl", return_value=acl) as windows_acl, \
                        patch.object(self.handoff, "_private_identity", return_value="FOREIGN") as private_id:
                    self.assertEqual(self.real_source(self.acq, self.sha), source)
                    windows_acl.assert_not_called()
                    private_id.assert_not_called()

                owner, private_aces = acl
                with self.assertRaisesRegex(self.handoff.HandoffBlocked, "HANDOFF_PERMISSIONS_INVALID"):
                    self.handoff._check_windows_acl(owner, private_aces, self.sid)

    def test_clean_checkout_proceeds_to_task_without_source_acl_verifier(self):
        root, workflow, source, peer, git = self._clean_source_fixture()
        normal_acl = (self.sid, (("allow", self.sid), ("allow", "S-1-5-32-545")))
        formerly_rejected_acl = (self.sid, (("allow", self.sid), ("allow", "S-1-5-11"), ("allow", "S-1-1-0")))
        self.source.side_effect = self.real_source
        with patch.dict(hostops.ROOTS, workspace=root), \
                patch.object(hostops, "__file__", workflow / "src/ai_commerce/hostops_v3/personal.py"), \
                patch.object(self.acq, "__file__", peer), \
                patch.object(hostops, "_git", side_effect=lambda *args: git[args]):
            for label, acl in (("normal_inherited_acl", normal_acl),
                               ("formerly_rejected_public_acl", formerly_rejected_acl)):
                with self.subTest(label=label), \
                        patch.object(self.handoff, "_windows_acl", return_value=acl) as windows_acl, \
                        patch.object(self.handoff, "_private_identity",
                                     side_effect=AssertionError("public checkout must not invoke ACL verifier")):
                    self.task.reset_mock()
                    receipt = self.invoke()
                    self.assertEqual((receipt["status"], receipt["code"]), ("OBSERVED", "INSTALLED_BUT_NOT_RUNNING"))
                    self.task.assert_called_once_with(self.owner, self.target, self.candidates, install=False)
                    windows_acl.assert_not_called()
            for expected in (
                "LOCAL_MAIN_SYNC_REQUIRED", "SOURCE_WORKFLOW_PATH_MISMATCH", "SOURCE_PATH_UNTRUSTED",
                "SOURCE_INDEX_UNVERIFIED", "SOURCE_UNTRACKED_FILE", "SOURCE_BYTES_MISMATCH",
            ):
                self.assertIn(expected, hostops._CLEAN_PUBLIC_CODES)
                self.assertEqual(hostops._clean_autostart_receipt(expected)["code"], expected)

    def _clean_source_case(self, label, files):
        base = self.local / f"case_{label}"
        root = base / "persistent"
        workflow = base / "workflow"
        (root / ".git").mkdir(parents=True, exist_ok=True)
        source = root / hostops._ACQUISITION_SOURCE
        peer = workflow / hostops._ACQUISITION_SOURCE
        all_files = {hostops._ACQUISITION_SOURCE.as_posix(): (b"same reviewed source\n", b"same reviewed source\n")}
        all_files.update(files)
        tracked_entries = []
        for rel, (worktree_bytes, pinned_bytes) in all_files.items():
            work_path = root / rel
            pin_path = workflow / rel
            work_path.parent.mkdir(parents=True, exist_ok=True)
            pin_path.parent.mkdir(parents=True, exist_ok=True)
            work_path.write_bytes(worktree_bytes)
            pin_path.write_bytes(pinned_bytes)
            tracked_entries.append("H " + rel)
        git = {
            ("rev-parse", "--show-toplevel"): str(root),
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("config", "--get", "remote.origin.url"): "https://github.com/trung-via/ai-commerce.git",
            ("rev-parse", "HEAD", "refs/remotes/origin/main"): f"{self.sha}\n{self.sha}\n",
            ("status", "--porcelain=v1", "--untracked-files=all"): "",
            ("ls-files", "-v", "-z", "--", "src"): "\0".join(tracked_entries) + "\0",
        }
        return root, workflow, source, peer, git

    def test_supervisor_source_pin_hashes_exact_bytes_of_verified_checkout(self):
        runtime = "src/ai_commerce/wakehub_runtime/runner.py"
        raw = b"reviewed_runtime = True\r\n"
        root, workflow, source, peer, git = self._clean_source_case(
            "supervisor_pin", {runtime: (raw, raw.replace(b"\r\n", b"\n"))})
        paths = (source, root / runtime)
        manifest = sorted([path.relative_to(root / "src").as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()]
                          for path in paths)
        expected = hashlib.sha256(json.dumps(manifest, separators=(",", ":")).encode("ascii")).hexdigest()
        with patch.dict(hostops.ROOTS, workspace=root), patch.dict(self.env, GITHUB_WORKSPACE=str(workflow)), \
                patch.object(hostops, "__file__", workflow / "src/ai_commerce/hostops_v3/personal.py"), \
                patch.object(self.acq, "__file__", peer), patch.object(hostops, "_git", side_effect=lambda *a: git[a]):
            self.assertEqual(self.real_source(self.acq, self.sha, pin_runtime=True), expected)
            (root / runtime).write_bytes(b"reviewed_runtime = False\r\n")
            with self.assertRaisesRegex(hostops.Denied, "SOURCE_BYTES_MISMATCH"):
                self.real_source(self.acq, self.sha, pin_runtime=True)

    def test_persistent_source_allows_crlf_equivalence_for_recognized_text_suffixes_and_reaches_runtime(self):
        # Exact-byte fast path passes for both text and binary tracked files without normalization.
        exact_bin = b"\x89PNG\r\n\x1a\n\x00\xffexact"
        root, workflow, source, peer, git = self._clean_source_case("exact_fast_path", {
            "src/ai_commerce/__init__.py": (b'"""pkg"""\n__all__ = []\n', b'"""pkg"""\n__all__ = []\n'),
            "src/ai_commerce/asset.bin": (exact_bin, exact_bin),
        })
        with patch.dict(hostops.ROOTS, workspace=root), \
                patch.dict(self.env, GITHUB_WORKSPACE=str(workflow)), \
                patch.object(hostops, "__file__", workflow / "src/ai_commerce/hostops_v3/personal.py"), \
                patch.object(self.acq, "__file__", peer), \
                patch.object(hostops, "_git", side_effect=lambda *args: git[args]), \
                patch.object(hostops, "_normalize_crlf_text",
                             side_effect=AssertionError("exact-byte fast path must not normalize")):
            self.assertEqual(self.real_source(self.acq, self.sha), source)

        # Observed HostOps #1126 LF-vs-CRLF checkout equivalence across reviewed text suffixes.
        suffix_cases = (
            ("py_init_crlf", "src/ai_commerce/__init__.py",
             b'"""AI Commerce package."""\r\n\r\n__version__ = "0.1.0"\r\n',
             b'"""AI Commerce package."""\n\n__version__ = "0.1.0"\n'),
            ("py_reverse_lf", "src/ai_commerce/module.py",
             b"a = 1\nb = 2\n", b"a = 1\r\nb = 2\r\n"),
            ("py_mixed_pinned_pure_crlf", "src/ai_commerce/product/core.py",
             b"a = 1\r\nb = 2\r\nc = 3\r\n", b"a = 1\r\nb = 2\nc = 3\n"),
            ("json_crlf", "src/ai_commerce/contracts/sample.json",
             b'{\r\n  "ok": true\r\n}\r\n', b'{\n  "ok": true\n}\n'),
            ("md_crlf", "src/ai_commerce/README.md",
             b"# Title\r\n\r\nBody\r\n", b"# Title\n\nBody\n"),
            ("txt_crlf", "src/ai_commerce/verification/notes.txt",
             b"line 1\r\nline 2\r\n", b"line 1\nline 2\n"),
            ("patch_crlf", "src/ai_commerce/repair/fix.patch",
             b"--- a/x.py\r\n+++ b/x.py\r\n", b"--- a/x.py\n+++ b/x.py\n"),
            ("yaml_crlf", "src/ai_commerce/config.yaml",
             b"key: value\r\n", b"key: value\n"),
            ("toml_crlf", "src/ai_commerce/config.toml",
             b'key = "value"\r\n', b'key = "value"\n'),
        )
        for label, rel, work_bytes, pin_bytes in suffix_cases:
            with self.subTest(label=label, rel=rel):
                root, workflow, source, peer, git = self._clean_source_case(label, {
                    rel: (work_bytes, pin_bytes),
                })
                with patch.dict(hostops.ROOTS, workspace=root), \
                        patch.dict(self.env, GITHUB_WORKSPACE=str(workflow)), \
                        patch.object(hostops, "__file__", workflow / "src/ai_commerce/hostops_v3/personal.py"), \
                        patch.object(self.acq, "__file__", peer), \
                        patch.object(hostops, "_git", side_effect=lambda *args: git[args]):
                    self.assertEqual(self.real_source(self.acq, self.sha), source)

        # End-to-end offline invoke with CRLF persistent .py/.json/.md/.txt/.patch reaches the fixed task.
        init_lf = b'"""AI Commerce runtime package."""\n\n# Canonical LF source for HostOps custody check.\n__all__ = []\n'
        init_crlf = init_lf.replace(b"\n", b"\r\n")
        acq_lf = b"def monitor():\n    return 9444\n"
        acq_crlf = acq_lf.replace(b"\n", b"\r\n")
        root, workflow, source, peer, git = self._clean_source_case("e2e_crlf", {
            hostops._ACQUISITION_SOURCE.as_posix(): (acq_crlf, acq_lf),
            "src/ai_commerce/__init__.py": (init_crlf, init_lf),
            "src/ai_commerce/contracts/sample.json": (b'{\r\n  "k": 1\r\n}\r\n', b'{\n  "k": 1\n}\n'),
            "src/ai_commerce/README.md": (b"# Docs\r\n", b"# Docs\n"),
            "src/ai_commerce/notes.txt": (b"one\r\ntwo\r\n", b"one\ntwo\n"),
            "src/ai_commerce/fix.patch": (b"@@ -1 +1 @@\r\n", b"@@ -1 +1 @@\n"),
        })
        self.source.side_effect = self.real_source
        with patch.dict(hostops.ROOTS, workspace=root), \
                patch.dict(self.env, GITHUB_WORKSPACE=str(workflow)), \
                patch.object(hostops, "__file__", workflow / "src/ai_commerce/hostops_v3/personal.py"), \
                patch.object(self.acq, "__file__", peer), \
                patch.object(hostops, "_git", side_effect=lambda *args: git[args]):
            self.task.reset_mock()
            receipt = self.invoke()
            self.assertEqual((receipt["status"], receipt["code"]), ("OBSERVED", "INSTALLED_BUT_NOT_RUNNING"))
            self.task.assert_called_once_with(self.owner, self.target, self.candidates, install=False)

            # Profile access and task identity are checked after source custody.
            self.task.reset_mock()
            with patch.object(self.acq, "_clean_target",
                              side_effect=self.acq._Clean9444Hold("PROFILE_UNAVAILABLE")):
                held = self.invoke()
            self.assertEqual((held["status"], held["code"]), ("HOLD", "PROFILE_UNAVAILABLE"))
            self.task.assert_not_called()

            self.task.return_value = "AUTOSTART_CONFLICT"
            self.task.reset_mock()
            conflict = self.invoke()
            self.assertEqual((conflict["status"], conflict["code"]), ("HOLD", "AUTOSTART_CONFLICT"))
            self.task.assert_called_once_with(self.owner, self.target, self.candidates, install=False)

    def test_persistent_source_rejects_content_binary_rogue_newline_and_custody_violations(self):
        base_lf = b"alpha = 1\nbeta = 2\n"
        negative_byte_cases = (
            ("content_edit", "src/ai_commerce/__init__.py", b"alpha = 1\r\nbeta = 3\r\n", base_lf),
            ("extra_char", "src/ai_commerce/__init__.py", b"alpha = 1 \r\nbeta = 2\r\n", base_lf),
            ("missing_char", "src/ai_commerce/__init__.py", b"alpha = 1\r\nbeta = \r\n", base_lf),
            ("missing_trailing_nl", "src/ai_commerce/__init__.py", b"alpha = 1\r\nbeta = 2", base_lf),
            ("extra_trailing_nl", "src/ai_commerce/__init__.py", b"alpha = 1\r\nbeta = 2\r\n\r\n", base_lf),
            ("bom_added", "src/ai_commerce/__init__.py", b"\xef\xbb\xbfalpha = 1\r\nbeta = 2\r\n", base_lf),
            ("lone_cr", "src/ai_commerce/__init__.py", b"alpha = 1\rbeta = 2\r", base_lf),
            ("trailing_lone_cr", "src/ai_commerce/__init__.py", b"alpha = 1\r\nbeta = 2\r", base_lf),
            ("double_cr_lf", "src/ai_commerce/__init__.py", b"alpha = 1\r\r\nbeta = 2\r\n", base_lf),
            ("lfcr_reversed", "src/ai_commerce/__init__.py", b"alpha = 1\n\rbeta = 2\n\r", base_lf),
            ("mixed_worktree_crlf_lf", "src/ai_commerce/__init__.py", b"alpha = 1\r\nbeta = 2\n", base_lf),
            ("unicode_nel", "src/ai_commerce/__init__.py", b"alpha = 1\xc2\x85beta = 2\xc2\x85", base_lf),
            ("unicode_ls", "src/ai_commerce/__init__.py", b"alpha = 1\xe2\x80\xa8beta = 2\xe2\x80\xa8", base_lf),
            ("nul_in_py", "src/ai_commerce/__init__.py", b"alpha\x00\r\nbeta\r\n", b"alpha\x00\nbeta\n"),
            ("invalid_utf8_in_py", "src/ai_commerce/__init__.py", b"\xff\r\nbeta\r\n", b"\xff\nbeta\n"),
            ("binary_crlf_bin", "src/ai_commerce/blob.bin", b"line1\r\nline2\r\n", b"line1\nline2\n"),
            ("binary_crlf_png", "src/ai_commerce/img.png", b"\x89PNG\r\n\x1a\n", b"\x89PNG\n\x1a\n"),
            ("binary_edit_png", "src/ai_commerce/img.png", b"\x89PNG\r\n\x1a\n\x01", b"\x89PNG\r\n\x1a\n\x00"),
            ("disallowed_ps1_crlf", "src/ai_commerce/helper.ps1", b"Write-Host 1\r\n", b"Write-Host 1\n"),
            ("disallowed_noext_crlf", "src/ai_commerce/BLOB", b"line1\r\n", b"line1\n"),
        )
        self.source.side_effect = self.real_source
        for label, rel, work_bytes, pin_bytes in negative_byte_cases:
            with self.subTest(label=label, rel=rel):
                root, workflow, _source, peer, git = self._clean_source_case(label, {
                    rel: (work_bytes, pin_bytes),
                })
                with patch.dict(hostops.ROOTS, workspace=root), \
                        patch.dict(self.env, GITHUB_WORKSPACE=str(workflow)), \
                        patch.object(hostops, "__file__", workflow / "src/ai_commerce/hostops_v3/personal.py"), \
                        patch.object(self.acq, "__file__", peer), \
                        patch.object(hostops, "_git", side_effect=lambda *args: git[args]):
                    self.task.reset_mock()
                    with self.assertRaisesRegex(hostops.Denied, "^SOURCE_BYTES_MISMATCH$"):
                        self.real_source(self.acq, self.sha)
                    receipt = self.invoke()
                    self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", "SOURCE_BYTES_MISMATCH"))
                    self.task.assert_not_called()

        # Custody violations still determinately HOLD even when tracked .py has valid CRLF equivalence.
        root, workflow, source, peer, git = self._clean_source_case("custody_with_crlf", {
            "src/ai_commerce/__init__.py": (b"alpha = 1\r\nbeta = 2\r\n", base_lf),
        })
        with patch.dict(hostops.ROOTS, workspace=root), \
                patch.dict(self.env, GITHUB_WORKSPACE=str(workflow)), \
                patch.object(hostops, "__file__", workflow / "src/ai_commerce/hostops_v3/personal.py"), \
                patch.object(self.acq, "__file__", peer), \
                patch.object(hostops, "_git", side_effect=lambda *args: git[args]):
            for args, bad, expected in (
                (("rev-parse", "--abbrev-ref", "HEAD"), "feature", "LOCAL_MAIN_SYNC_REQUIRED"),
                (("config", "--get", "remote.origin.url"), "https://evil.invalid/repo.git", "LOCAL_MAIN_SYNC_REQUIRED"),
                (("rev-parse", "HEAD", "refs/remotes/origin/main"), "b" * 40 + "\n" + "b" * 40 + "\n",
                 "LOCAL_MAIN_SYNC_REQUIRED"),
                (("status", "--porcelain=v1", "--untracked-files=all"), " M src/ai_commerce/__init__.py",
                 "LOCAL_MAIN_SYNC_REQUIRED"),
                (("status", "--porcelain=v1", "--untracked-files=all"), "?? src/evil.py", "SOURCE_UNTRACKED_FILE"),
                (("ls-files", "-v", "-z", "--", "src"),
                 "h " + hostops._ACQUISITION_SOURCE.as_posix() + "\0H src/ai_commerce/__init__.py\0",
                 "SOURCE_INDEX_UNVERIFIED"),
            ):
                with self.subTest(git_args=args, expected=expected), patch.dict(git, {args: bad}):
                    self.task.reset_mock()
                    receipt = self.invoke()
                    self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", expected))
                    self.task.assert_not_called()

            untracked = root / "src" / "ai_commerce" / "extra.py"
            untracked.write_bytes(b"untracked\r\n")
            try:
                self.task.reset_mock()
                receipt = self.invoke()
                self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", "SOURCE_UNTRACKED_FILE"))
                self.task.assert_not_called()
            finally:
                untracked.unlink()

            with patch.object(hostops, "_is_reparse", side_effect=lambda p: p == root / "src/ai_commerce/__init__.py"):
                self.task.reset_mock()
                receipt = self.invoke()
                self.assertEqual((receipt["status"], receipt["code"]), ("HOLD", "SOURCE_PATH_UNTRUSTED"))
                self.task.assert_not_called()

    def test_start_requests_only_the_exact_demand_task_in_owner_session(self):
        powershell = self.local / "PowerShell" / "7" / "pwsh.exe"
        with patch.object(self.acq, "_powershell", return_value=powershell):
            self.native_run.side_effect = None
            self.native_run.return_value = SimpleNamespace(returncode=0, stdout=b"START_REQUESTED")
            self.assertEqual(self.real_task(self.owner, self.target, self.candidates,
                                            install=False, start=True), "START_REQUESTED")
        task_call = self.native_run.call_args
        script, env = task_call.args[0][-1], task_call.kwargs["env"]
        self.assertEqual(env["AI_COMMERCE_TASK_POWERSHELL"], str(powershell))
        self.assertEqual(Path(env["AI_COMMERCE_TASK_GUI"]).name, "pythonw.exe")
        self.assertEqual(env["AI_COMMERCE_TASK_SESSION"], "7")
        self.assertEqual(env["AI_COMMERCE_TASK_START_SESSION"], "7")
        self.assertEqual(env["AI_COMMERCE_TASK_INSTALL"], "0")
        self.assertIn("-Command", env["AI_COMMERCE_TASK_ARGUMENTS"])
        self.assertIn(".RunEx($null, 12, $session, $sid)", script)
        self.assertIn(".RegisterTaskDefinition($name, $expected, 34, $sid, $null, 3, $sddl)", script)
        self.assertNotIn("Triggers.Create", script)
        self.assertNotIn("PT1M", script)
        self.assertNotRegex(script, r"Stop-Process|\.Stop\(|DeleteTask|TerminateProcess")


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

    def test_fixed_clean_profile_uses_ordinary_access_without_legacy_config(self):
        self.config.unlink()
        # Resolve the known-folder root so it matches the canonical fixture paths.
        self.local = self.local.resolve(strict=True)
        self.local_identity.return_value = self.local
        self.private_identity.return_value = "S-1-5-18"
        self.root = self.local / "AI-Commerce"
        clean = self.root / "WakeHub-9444-Clean"
        (clean / "Default").mkdir(parents=True)
        # Windows realpaths may differ from the spelling used to create a fixture.
        # CDP identity compares the native target against exact canonical paths.
        clean = clean.resolve(strict=True)
        binary = self.local / "Google" / "Chrome" / "Application" / "chrome.exe"
        binary.parent.mkdir(parents=True)
        binary.touch()
        binary = binary.resolve(strict=True)
        self.proc.update(ExecutablePath=str(binary), CommandLine=json.dumps([
            str(binary), "--user-data-dir=" + str(clean), "--profile-directory=Default",
            "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=9444",
        ]))
        with patch.object(self.acquisition, "_native_known_folder", return_value=self.local / "Program Files"), \
                patch.object(self.acquisition, "load_environment") as legacy:
            context = hostops._cdp_9444_private_context(
                self.acquisition, json.loads(self.proc["CommandLine"]))
            self.assertIsInstance(context[0], tuple)
            state, identity = self.acquisition._clean_inventory(
                *context[0], self.session, self.sid, inventory=self.inventory)
            self.assertEqual(state, "OWNED")
            self.assertEqual(identity[0], self.proc["ProcessId"])
            receipt = self.observe()
            self.assertEqual(receipt["code"], "CDP_PROTOCOL_HEALTHY")
            self.assert_public(receipt)
            self.private_identity.assert_not_called()
            legacy.assert_not_called()
            self.assertEqual(self.protocol.call_args.args[0].user_data_dir, clean)
            self.protocol.reset_mock()
            self.proc["SessionId"] += 1
            self.assertEqual(self.observe()["code"], "UNTRUSTED_OWNER")
            self.protocol.assert_not_called()

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


class HostOpsExecV1CaptureTests(unittest.IsolatedAsyncioTestCase):
    """Drive subprocess callbacks and cleanup without launching a process."""

    ARGV = ("test-powershell", "-Command", "-")
    SCRIPT = b"private script"

    async def asyncSetUp(self):
        self.loop = asyncio.get_running_loop()
        self.calls = Mock()
        self.stdin = self.calls.stdin
        self.transport = self.calls.transport
        self.transport.get_pid.return_value = 42
        self.transport.get_pipe_transport.return_value = self.stdin
        self.transport.get_returncode.return_value = 23
        self.transport.close.side_effect = lambda: self.loop.call_soon(self.capture.connection_lost, None)
        self.calls.has_live_processes.return_value = False

        @contextmanager
        def job():
            try:
                yield self.calls.attach, self.calls.has_live_processes
            finally:
                self.calls.close_job()

        async def spawn(factory, *argv, **options):
            self.assertEqual(argv, self.ARGV)
            for name in ("stdin", "stdout", "stderr"):
                self.assertEqual(options[name], subprocess.PIPE)
            self.capture = factory()
            return self.transport, self.capture

        self.enterContext(patch.object(hostops, "_exec_v1_windows_job", job))
        self.enterContext(patch.object(self.loop, "subprocess_exec", spawn))
        self.enterContext(patch.object(hostops.subprocess, "Popen", side_effect=AssertionError("no native process")))

    async def test_streams_drain_past_combined_cap_with_bounded_retention(self):
        limit = 128
        stderr_chunk = b"E" * 4096
        stdout_prefix = b"small stdout"

        def produce():
            self.capture.pipe_data_received(2, stderr_chunk)
            self.assertEqual(len(self.capture.stderr), limit)
            self.capture.pipe_data_received(1, stdout_prefix)
            for _ in range(2000):
                self.capture.pipe_data_received(2, stderr_chunk)
                self.assertEqual(len(self.capture.stdout) + len(self.capture.stderr), limit)
            self.capture.connection_lost(None)

        self.stdin.close.side_effect = lambda: self.loop.call_soon(produce)
        result = await hostops._capture_hostops_exec_v1(self.ARGV, self.SCRIPT, 5, limit)
        self.assertEqual(result, hostops.HostOpsExecV1ProcessResult(
            23, stdout_prefix, b"E" * (limit - len(stdout_prefix)), True,
        ))
        self.assertLess(self.calls.mock_calls.index(call.attach(42)),
                        self.calls.mock_calls.index(call.stdin.write(self.SCRIPT)))
        self.calls.close_job.assert_called_once_with()
        self.transport.close.assert_called_once_with()
        self.stdin.abort.assert_not_called()

    async def test_exact_cap_and_stdout_overflow_after_stderr(self):
        for stdout, expected_stderr, truncated in ((b"aaa", b"bb", False), (b"aaaaaa", b"", True)):
            with self.subTest(stdout=stdout):
                def produce():
                    self.capture.pipe_data_received(2, b"bb")
                    self.capture.pipe_data_received(1, stdout)
                    self.capture.connection_lost(None)

                self.stdin.close.side_effect = produce
                result = await hostops._capture_hostops_exec_v1(self.ARGV, self.SCRIPT, 5, 5)
                self.assertEqual(result, hostops.HostOpsExecV1ProcessResult(
                    23, stdout[:5], expected_stderr, truncated,
                ))

    async def test_timeout_closes_job_and_reaps_transport_with_open_pipes(self):
        timeout = asyncio.timeout(None)

        def produce_without_eof():
            self.capture.pipe_data_received(2, b"busy child" * 20)
            timeout.reschedule(self.loop.time())

        self.stdin.close.side_effect = produce_without_eof
        with patch.object(hostops.asyncio, "timeout_at", return_value=timeout):
            with self.assertRaises(subprocess.TimeoutExpired) as expired:
                await hostops._capture_hostops_exec_v1(self.ARGV, self.SCRIPT, 5, 128)
        self.assertEqual(expired.exception.timeout, 5)
        self.assertEqual(self.calls.mock_calls[-3:], [
            call.close_job(), call.stdin.abort(), call.transport.close(),
        ])
        self.assertTrue(self.capture.closed.is_set())
        self.transport.get_returncode.assert_not_called()

    async def test_closed_pipes_with_live_child_still_hit_deadline(self):
        timeout = asyncio.timeout(None)
        self.stdin.close.side_effect = lambda: self.capture.connection_lost(None)

        def child_stays_alive():
            timeout.reschedule(self.loop.time())
            return True

        self.calls.has_live_processes.side_effect = child_stays_alive
        with patch.object(hostops.asyncio, "timeout_at", return_value=timeout):
            with self.assertRaises(subprocess.TimeoutExpired):
                await hostops._capture_hostops_exec_v1(self.ARGV, self.SCRIPT, 5, 128)
        self.assertEqual(self.calls.mock_calls[-3:], [
            call.transport.close(), call.has_live_processes(), call.close_job(),
        ])
        self.stdin.abort.assert_not_called()

    async def test_failed_job_attachment_closes_transport_without_writing(self):
        self.calls.attach.side_effect = OSError("job assignment failed")
        with self.assertRaisesRegex(OSError, "job assignment failed"):
            await hostops._capture_hostops_exec_v1(self.ARGV, self.SCRIPT, 5, 128)
        self.stdin.write.assert_not_called()
        self.assertEqual(self.calls.mock_calls[-3:], [
            call.close_job(), call.stdin.abort(), call.transport.close(),
        ])
        self.assertTrue(self.capture.closed.is_set())

    async def test_pipe_error_cannot_report_completion(self):
        def broken_pipe():
            self.capture.pipe_connection_lost(2, OSError("pipe read failed"))
            self.capture.connection_lost(None)

        self.stdin.close.side_effect = broken_pipe
        with self.assertRaisesRegex(OSError, "HOSTOPS_EXEC_PIPE_FAILED"):
            await hostops._capture_hostops_exec_v1(self.ARGV, self.SCRIPT, 5, 128)
        self.transport.get_returncode.assert_not_called()
        self.transport.close.assert_called_once_with()


class HostOpsExecV1Tests(unittest.TestCase):
    """Offline source-only tests for HOSTOPS_EXEC_V1; never invokes real PowerShell."""

    NOW = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    SHA = "503e6085a0151c3de3af940d570a623f7210a81e"
    SID = "S-1-5-21-1000000001-2000000002-3000000003-1001"
    KEY = b"owner-private-hmac-key-0123456789abcdef"
    NATIVE_KEY = bytes(range(1, 33))

    def _runtime(self, **overrides):
        base = {
            "os_name": "nt",
            "runner_name": hostops.HOSTOPS_EXEC_V1_RUNNER_NAME,
            "session_id": 1,
            "elevated": False,
            "owner_sid": self.SID,
            "env": {"RUNNER_NAME": hostops.HOSTOPS_EXEC_V1_RUNNER_NAME},
        }
        base.update(overrides)
        return hostops.HostOpsExecV1RuntimeContext(**base)

    def _protected_actions_env(self, **overrides):
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "issues",
            "GITHUB_REPOSITORY": hostops.REPO,
            "GITHUB_REPOSITORY_OWNER": hostops.OWNER,
            "GITHUB_ACTOR": hostops.OWNER,
            "GITHUB_TRIGGERING_ACTOR": hostops.OWNER,
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKFLOW_REF": hostops.HOSTOPS_EXEC_V1_WORKFLOW_REF,
            "GITHUB_SHA": self.SHA,
            "GITHUB_WORKFLOW_SHA": self.SHA,
            "RUNNER_ENVIRONMENT": "self-hosted",
            "RUNNER_NAME": hostops.HOSTOPS_EXEC_V1_RUNNER_NAME,
            "HOSTOPS_EXEC_V1_PROTECTED_DISPATCH": "ENABLED",
        }
        env.update(overrides)
        return env

    def _enroll(self, state_root: Path):
        return hostops.enroll_hostops_exec_v1(
            enrollment_id="enroll-owner-01",
            source_sha=self.SHA,
            state_root=state_root,
            auth_key=self.KEY,
            runtime=self._runtime(),
            now=self.NOW,
        )

    def _issue_event(self, body, *, private=False, action="opened", state="open",
                     repo_name=hostops.REPO, repo_owner=hostops.OWNER,
                     sender=hostops.OWNER, issue_user=hostops.OWNER,
                     title=None, number=1201, extra_issue=None):
        issue = {
            "number": number,
            "state": state,
            "title": (hostops.HOSTOPS_EXEC_V1_TITLE + "routine task") if title is None else title,
            "user": {"login": issue_user},
            "body": body if type(body) is str else json.dumps(body),
        }
        if extra_issue:
            issue.update(extra_issue)
        return {
            "action": action,
            "repository": {
                "full_name": repo_name,
                "private": private,
                "owner": {"login": repo_owner},
            },
            "sender": {"login": sender},
            "issue": issue,
        }

    def _store_payload(self, enrollment, script_utf8: str) -> str:
        raw = script_utf8.encode("utf-8")
        digest = hostops._exec_v1_sha256(raw)
        (enrollment.payload_root / f"{digest}.ps1").write_bytes(raw)
        return digest

    def test_varied_benign_powershell_commands_run_after_enrollment_without_source_changes(self):
        self.assertIs(hostops.HOSTOPS_EXEC_V1_ENABLED_BY_DEFAULT, False)
        self.assertIs(hostops.HOSTOPS_EXEC_V1_DENYLIST_IS_SECURITY_BOUNDARY, False)
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "hostops-exec-state"
            enrollment, enroll_receipt = self._enroll(state_root)
            self.assertEqual((enroll_receipt["status"], enroll_receipt["code"]), ("ENROLLED", "ENROLLMENT_RECORDED"))
            self.assertTrue(enroll_receipt["enrolled"])
            self.assertFalse(enroll_receipt["executed"])
            self.assertFalse(enroll_receipt["denylist_security_claim"])
            self.assertNotIn(str(state_root), json.dumps(enroll_receipt))
            self.assertNotIn(self.KEY.decode("ascii"), json.dumps(enroll_receipt))

            loaded = hostops.load_hostops_exec_v1_enrollment(
                state_root / hostops.HOSTOPS_EXEC_V1_ENROLLMENT_FILENAME
            )
            self.assertEqual(loaded, enrollment)

            executed_scripts = []
            def fake_runner(script_utf8, timeout_seconds, max_output_bytes):
                executed_scripts.append((script_utf8, timeout_seconds, max_output_bytes))
                rc = 0 if "exit 3" not in script_utf8 else 3
                return hostops.HostOpsExecV1ProcessResult(
                    exit_code=rc,
                    stdout=f"OK:{script_utf8}".encode("utf-8"),
                    stderr=b"",
                )

            broker = hostops.HostOpsExecV1Broker(
                enrollment=loaded,
                runtime_inspector=self._runtime,
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=fake_runner,
                clock=lambda: self.NOW,
            )

            commands = [
                ("job-date-0001", "1" * 32, "PRIVATE_INLINE_UTF8", "Get-Date -Format o", 0, "EXECUTION_COMPLETED"),
                ("job-task-0002", "2" * 32, "PRIVATE_DIGEST", "Get-ScheduledTask -TaskName 'AI-Commerce-*'", 0, "EXECUTION_COMPLETED"),
                ("job-proc-0003", "3" * 32, "PRIVATE_DIGEST", "Get-Process chrome -ErrorAction SilentlyContinue", 0, "EXECUTION_COMPLETED"),
                ("job-exit-0004", "4" * 32, "PRIVATE_INLINE_UTF8", "Write-Output 'diag'; exit 3", 3, "EXECUTION_NONZERO_EXIT"),
            ]
            for job_id, nonce, mode, script, expected_rc, expected_code in commands:
                if mode == "PRIVATE_DIGEST":
                    digest = self._store_payload(enrollment, script)
                    req = hostops.build_hostops_exec_v1_request(
                        job_id=job_id, nonce=nonce, payload_mode=mode, script_sha256=digest, timeout_seconds=15
                    )
                else:
                    req = hostops.build_hostops_exec_v1_request(
                        job_id=job_id, nonce=nonce, payload_mode=mode, script_utf8=script, timeout_seconds=15
                    )
                token = hostops.sign_hostops_exec_v1_request(req, self.KEY)
                plan_receipt = broker.plan(req, auth_token=token)
                self.assertEqual((plan_receipt["status"], plan_receipt["code"]), ("PLANNED", "READY_TO_EXECUTE"))
                exec_receipt = broker.execute(req, auth_token=token)
                self.assertEqual((exec_receipt["status"], exec_receipt["code"]), ("EXECUTED", expected_code))
                self.assertTrue(exec_receipt["executed"])
                self.assertTrue(exec_receipt["readback_verified"])
                self.assertFalse(exec_receipt["cancelled"])
                self.assertEqual(exec_receipt["exit_code"], expected_rc)
                self.assertFalse(exec_receipt["public_script_echoed"])
                self.assertFalse(exec_receipt["public_output_echoed"])
                self.assertFalse(exec_receipt["denylist_security_claim"])
                self.assertNotIn(script, json.dumps(exec_receipt))

            self.assertEqual([item[0] for item in executed_scripts], [c[3] for c in commands])
            journal_bytes = enrollment.journal_path.read_bytes()
            for _, _, _, script, _, _ in commands:
                self.assertNotIn(script.encode("utf-8"), journal_bytes)

    def test_disabled_by_default_and_missing_or_revoked_enrollment_truthful_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "state"
            self.assertIsNone(
                hostops.load_hostops_exec_v1_enrollment(state_root / hostops.HOSTOPS_EXEC_V1_ENROLLMENT_FILENAME)
            )
            runner = Mock(side_effect=AssertionError("runner must not be called"))
            req = hostops.build_hostops_exec_v1_request(
                job_id="job-unenrolled-1", nonce="a" * 32, payload_mode="PRIVATE_DIGEST", script_sha256="b" * 64
            )
            unenrolled = hostops.HostOpsExecV1Broker(
                enrollment=None,
                runtime_inspector=self._runtime,
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=runner,
                clock=lambda: self.NOW,
            )
            token = hostops.sign_hostops_exec_v1_request(req, self.KEY)
            for receipt in (unenrolled.plan(req, auth_token=token), unenrolled.execute(req, auth_token=token)):
                self.assertEqual((receipt["status"], receipt["code"]), ("DISABLED", "BLOCKED_NOT_ENROLLED"))
                self.assertFalse(receipt["enrolled"])
                self.assertFalse(receipt["executed"])

            enrollment, _ = self._enroll(state_root)
            with self.assertRaisesRegex(hostops.Denied, "ALREADY_ENROLLED_NO_OVERWRITE"):
                self._enroll(state_root)

            disabled_enrollment = enrollment._replace(enabled=False)
            revoked_enrollment = enrollment._replace(revoked=True)
            for enr, expected_code in (
                (disabled_enrollment, "ENROLLMENT_DISABLED"),
                (revoked_enrollment, "ENROLLMENT_REVOKED"),
            ):
                broker = hostops.HostOpsExecV1Broker(
                    enrollment=enr,
                    runtime_inspector=self._runtime,
                    authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                    runner=runner,
                    clock=lambda: self.NOW,
                )
                receipt = broker.execute(req, auth_token=token)
                self.assertEqual((receipt["status"], receipt["code"]), ("DISABLED", expected_code))

            # Enrolled but missing local private payload must fail closed, never fall back.
            enrolled_broker = hostops.HostOpsExecV1Broker(
                enrollment=enrollment,
                runtime_inspector=self._runtime,
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=runner,
                clock=lambda: self.NOW,
            )
            missing_payload = enrolled_broker.execute(req, public_issue=True, issue_number=1201, auth_token=token)
            self.assertEqual((missing_payload["status"], missing_payload["code"]), ("REJECTED", "PRIVATE_PAYLOAD_UNAVAILABLE"))
            runner.assert_not_called()

    def test_owner_vs_foreign_actor_and_event_spoof_rejected(self):
        digest = hostops._exec_v1_sha256(b"Get-Date")
        valid_req = hostops.build_hostops_exec_v1_request(
            job_id="job-event-0001", nonce="c" * 32, payload_mode="PRIVATE_DIGEST", script_sha256=digest
        )
        ok_event = self._issue_event(valid_req, private=False)
        parsed = hostops.parse_hostops_exec_v1_event(ok_event)
        self.assertEqual(parsed["issue_number"], 1201)
        self.assertTrue(parsed["public_issue"])

        # Raw script in a public repository Issue is strictly forbidden.
        inline_req = hostops.build_hostops_exec_v1_request(
            job_id="job-inline-0001", nonce="d" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
        )
        with self.assertRaisesRegex(hostops.Denied, "PUBLIC_ISSUE_RAW_SCRIPT_DENIED"):
            hostops.parse_hostops_exec_v1_event(self._issue_event(inline_req, private=False))

        bad_events = [
            (self._issue_event(valid_req, sender="attacker"), "EVENT_OWNER_OR_REPO_DENIED"),
            (self._issue_event(valid_req, issue_user="attacker"), "EVENT_OWNER_OR_REPO_DENIED"),
            (self._issue_event(valid_req, repo_name="attacker/ai-commerce"), "EVENT_OWNER_OR_REPO_DENIED"),
            (self._issue_event(valid_req, repo_owner="attacker"), "EVENT_OWNER_OR_REPO_DENIED"),
            (self._issue_event(valid_req, action="edited"), "EVENT_SHAPE_OR_STATE_DENIED"),
            (self._issue_event(valid_req, state="closed"), "EVENT_SHAPE_OR_STATE_DENIED"),
            (self._issue_event(valid_req, title="[HOSTOPS WRITE] spoof"), "EVENT_SHAPE_OR_STATE_DENIED"),
            (self._issue_event(valid_req, extra_issue={"pull_request": {}}), "EVENT_SHAPE_OR_STATE_DENIED"),
            (self._issue_event({**valid_req, "command": "Get-Date"}), "REQUEST_INVALID"),
            (self._issue_event({**valid_req, "path": "payload.ps1"}), "REQUEST_INVALID"),
            (self._issue_event({**valid_req, "args": []}), "REQUEST_INVALID"),
            (self._issue_event('{"schema":"HOSTOPS_EXEC_V1","schema":"HOSTOPS_EXEC_V1"}'), "DUPLICATE_JSON_KEY"),
        ]
        for ev, expected_code in bad_events:
            with self.subTest(expected_code=expected_code):
                with self.assertRaisesRegex(hostops.Denied, expected_code):
                    hostops.parse_hostops_exec_v1_event(ev)

        with tempfile.TemporaryDirectory() as tmp:
            enrollment, _ = self._enroll(Path(tmp) / "state")
            self._store_payload(enrollment, "Get-Date")
            runner = Mock(side_effect=AssertionError("runner must not be called"))
            # Missing authenticator or wrong token fails closed.
            for authenticator, token in (
                (None, hostops.sign_hostops_exec_v1_request(valid_req, self.KEY)),
                (hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY), "0" * 64),
                (hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY), None),
                (hostops.make_hostops_exec_v1_hmac_authenticator(b"wrong-key-" + b"x" * 32),
                 hostops.sign_hostops_exec_v1_request(valid_req, self.KEY)),
            ):
                broker = hostops.HostOpsExecV1Broker(
                    enrollment=enrollment,
                    runtime_inspector=self._runtime,
                    authenticator=authenticator,
                    runner=runner,
                    clock=lambda: self.NOW,
                )
                receipt = broker.handle_event(ok_event, action="execute", auth_token=token)
                self.assertEqual((receipt["status"], receipt["code"]), ("REJECTED", "AUTHENTICATION_UNPROVEN"))
            runner.assert_not_called()

    def test_replay_duplicate_job_and_digest_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "state"
            enrollment, _ = self._enroll(state_root)
            calls = []
            def fake_runner(script_utf8, timeout_seconds, max_output_bytes):
                calls.append(script_utf8)
                return hostops.HostOpsExecV1ProcessResult(0, b"ok", b"")

            broker = hostops.HostOpsExecV1Broker(
                enrollment=enrollment,
                runtime_inspector=self._runtime,
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=fake_runner,
                clock=lambda: self.NOW,
            )
            req1 = hostops.build_hostops_exec_v1_request(
                job_id="job-unique-001", nonce="1" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
            )
            tok1 = hostops.sign_hostops_exec_v1_request(req1, self.KEY)
            self.assertEqual(broker.execute(req1, auth_token=tok1)["status"], "EXECUTED")
            self.assertEqual(len(calls), 1)

            # Duplicate job_id with fresh nonce rejected.
            dup_job = hostops.build_hostops_exec_v1_request(
                job_id="job-unique-001", nonce="2" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
            )
            dup_receipt = broker.execute(dup_job, auth_token=hostops.sign_hostops_exec_v1_request(dup_job, self.KEY))
            self.assertEqual((dup_receipt["status"], dup_receipt["code"]), ("REJECTED", "DUPLICATE_JOB_ID"))

            # Replayed nonce with new job_id rejected.
            replay_nonce = hostops.build_hostops_exec_v1_request(
                job_id="job-unique-002", nonce="1" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
            )
            rep_receipt = broker.execute(
                replay_nonce, auth_token=hostops.sign_hostops_exec_v1_request(replay_nonce, self.KEY)
            )
            self.assertEqual((rep_receipt["status"], rep_receipt["code"]), ("REJECTED", "REPLAY_DENIED"))

            # Tampered field breaks request_digest.
            tampered = {**req1, "timeout_seconds": 60}
            tampered_receipt = broker.execute(tampered, auth_token=tok1)
            self.assertEqual((tampered_receipt["status"], tampered_receipt["code"]), ("REJECTED", "REQUEST_DIGEST_MISMATCH"))

            # Mismatched enrollment pin on existing journal rejected.
            other_enrollment = enrollment._replace(enrollment_id="enroll-other-02")
            other_broker = hostops.HostOpsExecV1Broker(
                enrollment=other_enrollment,
                runtime_inspector=self._runtime,
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=fake_runner,
                clock=lambda: self.NOW,
            )
            fresh_req = hostops.build_hostops_exec_v1_request(
                job_id="job-unique-003", nonce="3" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
            )
            pin_receipt = other_broker.execute(
                fresh_req, auth_token=hostops.sign_hostops_exec_v1_request(fresh_req, self.KEY)
            )
            self.assertEqual((pin_receipt["status"], pin_receipt["code"]), ("REJECTED", "ENROLLMENT_PIN_MISMATCH"))
            self.assertEqual(len(calls), 1)

    def test_wrong_session_elevated_non_windows_and_ef02_runner_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "state"
            # Enrollment itself refuses CI, EF02, Session 0, elevation, and service SIDs.
            for bad_runtime, expected_code in (
                (self._runtime(os_name="posix"), "WINDOWS_ONLY"),
                (self._runtime(runner_name="AI-COMMERCE-EF02-01"), "WRONG_RUNNER_FORBIDDEN"),
                (self._runtime(runner_name="AI-COMMERCE-EF02-02"), "WRONG_RUNNER_FORBIDDEN"),
                (self._runtime(session_id=0), "WRONG_SESSION_OR_ELEVATED"),
                (self._runtime(elevated=True), "WRONG_SESSION_OR_ELEVATED"),
                (self._runtime(owner_sid="S-1-5-18"), "OWNER_SID_MISMATCH"),
                (self._runtime(env={"GITHUB_ACTIONS": "true", "RUNNER_NAME": hostops.HOSTOPS_EXEC_V1_RUNNER_NAME}),
                 "SOURCE_CI_EXECUTION_FORBIDDEN"),
                (self._runtime(env={"AI_COMMERCE_GIT_GUARD_ACTIVE": "1", "RUNNER_NAME": hostops.HOSTOPS_EXEC_V1_RUNNER_NAME}),
                 "SOURCE_CI_EXECUTION_FORBIDDEN"),
            ):
                with self.subTest(enroll_code=expected_code):
                    with self.assertRaisesRegex(hostops.Denied, expected_code):
                        hostops.enroll_hostops_exec_v1(
                            enrollment_id="enroll-bad-01",
                            source_sha=self.SHA,
                            state_root=state_root,
                            auth_key=self.KEY,
                            runtime=bad_runtime,
                            now=self.NOW,
                        )

            enrollment, _ = self._enroll(state_root)
            req = hostops.build_hostops_exec_v1_request(
                job_id="job-session-01", nonce="a" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
            )
            token = hostops.sign_hostops_exec_v1_request(req, self.KEY)
            runner = Mock(side_effect=AssertionError("runner must not execute"))
            for bad_runtime, expected_code in (
                (self._runtime(os_name="posix"), "WINDOWS_ONLY"),
                (self._runtime(runner_name="AI-COMMERCE-EF02-02"), "WRONG_RUNNER_FORBIDDEN"),
                (self._runtime(session_id=0), "WRONG_SESSION_OR_ELEVATED"),
                (self._runtime(elevated=True), "WRONG_SESSION_OR_ELEVATED"),
                (self._runtime(owner_sid="S-1-5-21-9-9-9-1001"), "OWNER_SID_MISMATCH"),
                (self._runtime(env={"PYTEST_CURRENT_TEST": "tests/test_hostops_personal.py"}),
                 "SOURCE_CI_EXECUTION_FORBIDDEN"),
            ):
                broker = hostops.HostOpsExecV1Broker(
                    enrollment=enrollment,
                    runtime_inspector=lambda rt=bad_runtime: rt,
                    authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                    runner=runner,
                    clock=lambda: self.NOW,
                )
                receipt = broker.execute(req, auth_token=token)
                self.assertEqual((receipt["status"], receipt["code"]), ("REJECTED", expected_code))
            runner.assert_not_called()

    def test_malformed_and_oversized_payloads_rejected_and_output_bounded(self):
        with self.assertRaisesRegex(hostops.Denied, "PAYLOAD_OVERSIZED_OR_INVALID"):
            hostops.build_hostops_exec_v1_request(
                job_id="job-oversize-1", nonce="a" * 32, payload_mode="PRIVATE_INLINE_UTF8",
                script_utf8="A" * (hostops.HOSTOPS_EXEC_V1_MAX_SCRIPT_BYTES + 1),
            )
        with self.assertRaisesRegex(hostops.Denied, "PAYLOAD_OVERSIZED_OR_INVALID"):
            hostops.build_hostops_exec_v1_request(
                job_id="job-nulbyte-1", nonce="a" * 32, payload_mode="PRIVATE_INLINE_UTF8",
                script_utf8="Get-Date\x00",
            )
        with self.assertRaisesRegex(hostops.Denied, "PAYLOAD_DIGEST_MISMATCH"):
            hostops.build_hostops_exec_v1_request(
                job_id="job-digest-01", nonce="a" * 32, payload_mode="PRIVATE_INLINE_UTF8",
                script_utf8="Get-Date", script_sha256="0" * 64,
            )
        for bad_timeout in (0, -5, 121, True):
            with self.assertRaisesRegex(hostops.Denied, "REQUEST_INVALID"):
                hostops.build_hostops_exec_v1_request(
                    job_id="job-timeout-1", nonce="a" * 32, payload_mode="PRIVATE_DIGEST",
                    script_sha256="a" * 64, timeout_seconds=bad_timeout,
                )
        with self.assertRaisesRegex(hostops.Denied, "AUTHORITY_TIER_DENIED"):
            hostops.build_hostops_exec_v1_request(
                job_id="job-tier2-001", nonce="a" * 32, payload_mode="PRIVATE_DIGEST",
                script_sha256="a" * 64, authority_tier=hostops.HOSTOPS_EXEC_V1_AUTHORITY_TIER_EXTERNAL,
            )
        with self.assertRaisesRegex(hostops.Denied, "WRONG_SESSION_OR_ELEVATED"):
            hostops.build_hostops_exec_v1_request(
                job_id="job-sess0-001", nonce="a" * 32, payload_mode="PRIVATE_DIGEST",
                script_sha256="a" * 64, intended_session="SESSION_0",
            )
        with self.assertRaisesRegex(hostops.Denied, "PAYLOAD_OVERSIZED_OR_INVALID"):
            hostops.parse_hostops_exec_v1_request(b"{" + b" " * (hostops.HOSTOPS_EXEC_V1_MAX_EVENT_BYTES + 5) + b"}")

        # Verify bounded output truncation on oversized stdout.
        with tempfile.TemporaryDirectory() as tmp:
            enrollment, _ = self._enroll(Path(tmp) / "state")
            huge_stdout = b"S" * (hostops.HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES + 1024)
            broker = hostops.HostOpsExecV1Broker(
                enrollment=enrollment,
                runtime_inspector=self._runtime,
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=lambda *_: hostops.HostOpsExecV1ProcessResult(0, huge_stdout, b"stderr-data"),
                clock=lambda: self.NOW,
            )
            req = hostops.build_hostops_exec_v1_request(
                job_id="job-bounded-01", nonce="b" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
            )
            receipt = broker.execute(req, auth_token=hostops.sign_hostops_exec_v1_request(req, self.KEY))
            self.assertEqual(receipt["status"], "EXECUTED")
            self.assertEqual(receipt["output_bytes"], hostops.HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES)
            self.assertTrue(receipt["output_truncated"])

    def test_runner_truncation_metadata_survives_in_closed_code_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            enrollment, _ = self._enroll(Path(tmp) / "state")
            stdout = b"private stdout"
            stderr = b"E" * (hostops.HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES - len(stdout))
            runner = Mock(return_value=hostops.HostOpsExecV1ProcessResult(23, stdout, stderr, True))
            broker = hostops.HostOpsExecV1Broker(
                enrollment=enrollment,
                runtime_inspector=self._runtime,
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=runner,
                clock=lambda: self.NOW,
            )
            req = hostops.build_hostops_exec_v1_request(
                job_id="job-runner-cap-01", nonce="b" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
            )
            receipt = broker.execute(req, auth_token=hostops.sign_hostops_exec_v1_request(req, self.KEY))
            self.assertEqual((receipt["status"], receipt["code"], receipt["exit_code"]),
                             ("EXECUTED", "EXECUTION_NONZERO_EXIT", 23))
            self.assertEqual(receipt["output_bytes"], hostops.HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES)
            self.assertTrue(receipt["output_truncated"])
            self.assertEqual(receipt["stdout_sha256"], hashlib.sha256(stdout).hexdigest())
            self.assertEqual(receipt["stderr_sha256"], hashlib.sha256(stderr).hexdigest())
            self.assertNotIn(stdout.decode(), json.dumps(receipt))

    def test_malformed_capture_metadata_holds_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            enrollment, _ = self._enroll(Path(tmp) / "state")
            runner = Mock(return_value=hostops.HostOpsExecV1ProcessResult(0, b"private", b"", 1))
            broker = hostops.HostOpsExecV1Broker(
                enrollment=enrollment,
                runtime_inspector=self._runtime,
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=runner,
                clock=lambda: self.NOW,
            )
            req = hostops.build_hostops_exec_v1_request(
                job_id="job-malformed-01", nonce="b" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
            )
            token = hostops.sign_hostops_exec_v1_request(req, self.KEY)
            receipt = broker.execute(req, auth_token=token)
            self.assertEqual((receipt["status"], receipt["code"]),
                             ("UNKNOWN_EFFECT", "READBACK_UNVERIFIED_UNKNOWN_EFFECT"))
            for field in ("exit_code", "stdout_sha256", "stderr_sha256"):
                self.assertIsNone(receipt[field])
            self.assertEqual(broker.execute(req, auth_token=token)["code"], "UNKNOWN_EFFECT_HOLD")
            runner.assert_called_once()

    def test_unknown_effect_cancellation_readback_distinction_and_no_auto_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Case 1: Pre-spawn cancellation -> REJECTED (cancelled=True), no runner call, no UNKNOWN_EFFECT poison.
            state1 = Path(tmp) / "state1"
            enr1, _ = self._enroll(state1)
            runner1 = Mock(side_effect=subprocess.TimeoutExpired(["powershell.exe"], 5))
            broker1 = hostops.HostOpsExecV1Broker(
                enrollment=enr1,
                runtime_inspector=self._runtime,
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=runner1,
                clock=lambda: self.NOW,
            )
            req_pre = hostops.build_hostops_exec_v1_request(
                job_id="job-cancel-pre", nonce="1" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
            )
            pre_receipt = broker1.execute(
                req_pre, auth_token=hostops.sign_hostops_exec_v1_request(req_pre, self.KEY), cancel_before_spawn=True
            )
            self.assertEqual((pre_receipt["status"], pre_receipt["code"]), ("REJECTED", "CANCELLED_BEFORE_EXECUTION"))
            self.assertTrue(pre_receipt["cancelled"])
            self.assertFalse(pre_receipt["executed"])
            self.assertFalse(pre_receipt["readback_verified"])
            runner1.assert_not_called()

            # Case 2: Post-spawn timeout -> UNKNOWN_EFFECT (cancelled=True, readback_verified=False).
            req_to = hostops.build_hostops_exec_v1_request(
                job_id="job-timeout-02", nonce="2" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Start-Sleep 99"
            )
            to_receipt = broker1.execute(req_to, auth_token=hostops.sign_hostops_exec_v1_request(req_to, self.KEY))
            self.assertEqual(
                (to_receipt["status"], to_receipt["code"]),
                ("UNKNOWN_EFFECT", "EXECUTION_CANCELLED_OR_TIMED_OUT_UNKNOWN_EFFECT"),
            )
            self.assertTrue(to_receipt["cancelled"])
            self.assertFalse(to_receipt["readback_verified"])
            self.assertEqual(runner1.call_count, 1)

            # Subsequent job must NOT auto-replay or proceed past UNKNOWN_EFFECT.
            req_after = hostops.build_hostops_exec_v1_request(
                job_id="job-after-unk1", nonce="3" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
            )
            hold_receipt = broker1.execute(req_after, auth_token=hostops.sign_hostops_exec_v1_request(req_after, self.KEY))
            self.assertEqual((hold_receipt["status"], hold_receipt["code"]), ("UNKNOWN_EFFECT", "UNKNOWN_EFFECT_HOLD"))
            self.assertEqual(runner1.call_count, 1)

            # Case 3: Post-execution readback failure -> UNKNOWN_EFFECT (cancelled=False, readback_verified=False).
            state2 = Path(tmp) / "state2"
            enr2, _ = self._enroll(state2)
            runner2 = Mock(return_value=hostops.HostOpsExecV1ProcessResult(0, b"done", b""))
            broker2 = hostops.HostOpsExecV1Broker(
                enrollment=enr2,
                runtime_inspector=self._runtime,
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=runner2,
                readback_verifier=lambda _: False,
                clock=lambda: self.NOW,
            )
            req_rb = hostops.build_hostops_exec_v1_request(
                job_id="job-readback-1", nonce="4" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
            )
            rb_receipt = broker2.execute(req_rb, auth_token=hostops.sign_hostops_exec_v1_request(req_rb, self.KEY))
            self.assertEqual((rb_receipt["status"], rb_receipt["code"]), ("UNKNOWN_EFFECT", "READBACK_UNVERIFIED_UNKNOWN_EFFECT"))
            self.assertFalse(rb_receipt["cancelled"])
            self.assertFalse(rb_receipt["readback_verified"])
            # Next job blocked by UNKNOWN_EFFECT_HOLD.
            req_rb2 = hostops.build_hostops_exec_v1_request(
                job_id="job-readback-2", nonce="5" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8="Get-Date"
            )
            hold2 = broker2.execute(req_rb2, auth_token=hostops.sign_hostops_exec_v1_request(req_rb2, self.KEY))
            self.assertEqual((hold2["status"], hold2["code"]), ("UNKNOWN_EFFECT", "UNKNOWN_EFFECT_HOLD"))
            self.assertEqual(runner2.call_count, 1)

    def test_no_raw_secret_log_profile_websocket_or_trace_leakage(self):
        secrets_list = [
            "SECRET_PS_SCRIPT_BODY",
            "SECRET_STDOUT_CREDENTIAL",
            "SECRET_STDERR_STACKTRACE",
            "ws://127.0.0.1:9444/devtools/browser/PRIVATE_WS_ID",
            "C:\\Users\\Owner\\AppData\\Local\\Google\\Chrome\\User Data",
            self.KEY.decode("ascii"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "private-hostops-state"
            enrollment, enroll_receipt = self._enroll(state_root)
            script = f"# {secrets_list[0]} {secrets_list[3]} {secrets_list[4]}"
            broker = hostops.HostOpsExecV1Broker(
                enrollment=enrollment,
                runtime_inspector=self._runtime,
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=lambda *_: hostops.HostOpsExecV1ProcessResult(
                    0,
                    (secrets_list[1] + " " + secrets_list[3]).encode("utf-8"),
                    (secrets_list[2] + " " + secrets_list[4]).encode("utf-8"),
                ),
                clock=lambda: self.NOW,
            )
            req = hostops.build_hostops_exec_v1_request(
                job_id="job-secret-001", nonce="e" * 32, payload_mode="PRIVATE_INLINE_UTF8", script_utf8=script
            )
            exec_receipt = broker.execute(req, auth_token=hostops.sign_hostops_exec_v1_request(req, self.KEY))
            # Also test exception path does not leak traceback/exception string.
            state_err = Path(tmp) / "private-hostops-err"
            enr_err, _ = self._enroll(state_err)
            err_broker = hostops.HostOpsExecV1Broker(
                enrollment=enr_err,
                runtime_inspector=self._runtime,
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=Mock(side_effect=RuntimeError(" ".join(secrets_list))),
                clock=lambda: self.NOW,
            )
            err_receipt = err_broker.execute(req, auth_token=hostops.sign_hostops_exec_v1_request(req, self.KEY))
            for receipt in (enroll_receipt, exec_receipt, err_receipt):
                wire = json.dumps(receipt)
                for marker in secrets_list:
                    self.assertNotIn(marker, wire)
                self.assertNotIn(str(state_root), wire)
                self.assertNotIn(self.SID, wire)
            journal_bytes = enrollment.journal_path.read_bytes()
            for marker in secrets_list:
                self.assertNotIn(marker.encode("utf-8"), journal_bytes)

            # Validator rejects tampered receipts claiming denylist security or echoing script/output.
            for bad_patch in (
                {"denylist_security_claim": True},
                {"public_script_echoed": True},
                {"public_output_echoed": True},
                {"elevated_token": True},
                {"stdout": "raw"},
                {"script_utf8": "raw"},
            ):
                with self.assertRaisesRegex(hostops.Denied, "REQUEST_INVALID"):
                    hostops.validate_hostops_exec_v1_receipt({**exec_receipt, **bad_patch})

    def test_old_read_write_backward_compatibility_and_dispatch_isolation(self):
        # Legacy READ and WRITE parsing remains unchanged.
        self.assertEqual(hostops.parse_event(event("read", "inventory", {}), "read")["operation"], "inventory")
        self.assertEqual(
            hostops.parse_event(event("read", "standing_read_diagnostics", {}), "read")["operation"],
            "standing_read_diagnostics",
        )
        self.assertEqual(
            hostops.parse_event(
                event("write", "create_text", {
                    "root_id": "scratch", "relative_path": "hostops-personal-r7.txt", "content_utf8": "ok"
                }),
                "write",
            )["operation"],
            "create_text",
        )
        # Legacy parse_event rejects HOSTOPS_EXEC_V1 events/operations in both read and write modes.
        for mode in ("read", "write"):
            with self.assertRaises(hostops.Denied):
                hostops.parse_event(event(mode, "HOSTOPS_EXEC_V1", {}), mode)
        # Legacy main() CLI does not accept exec mode.
        with patch.object(hostops.sys, "argv", ["personal.py", "--mode", "exec", "--output", "out.json"]):
            with self.assertRaises(SystemExit) as exited:
                hostops.main()
            self.assertNotEqual(exited.exception.code, 0)
        # Any separate Exec job must keep its dispatch out of the legacy job.
        workflow = (MODULE.parents[3] / ".github/workflows/issue-ingress.yml").read_text(encoding="utf-8")
        legacy_job = re.search(r"(?ms)^  hostops-personal:\n(.*?)(?=^  [a-z0-9-]+:\n|\Z)", workflow).group(1)
        for marker in ("[HOSTOPS EXEC]", "HOSTOPS_EXEC_V1", "--hostops-exec-v1", "hostops_exec_v1_cli"):
            self.assertNotIn(marker, legacy_job)

    def test_exec_v1_selects_verified_powershell_with_utf8_after_runtime_gates(self):
        env = {"RUNNER_NAME": hostops.HOSTOPS_EXEC_V1_RUNNER_NAME, "GITHUB_TOKEN": "PRIVATE"}
        result = hostops.HostOpsExecV1ProcessResult(0, b"", b"", False)
        script = "Write-Output 'Việt'"
        with patch.object(hostops, "os", SimpleNamespace(name="nt", environ=env)), patch.object(
            hostops, "_exec_v1_source_ci", return_value=False
        ), patch.object(hostops, "_default_hostops_exec_v1_runtime") as runtime, patch.object(
            hostops, "_verify_hostops_exec_v1_runtime"
        ) as verify, patch.object(acquisition, "_powershell", return_value=TEST_PWSH) as resolve, patch.object(
            hostops.asyncio, "Runner", create=True
        ) as runner, patch.object(hostops.asyncio, "ProactorEventLoop", create=True), patch.object(
            hostops, "_capture_hostops_exec_v1", new=Mock(return_value=result)
        ) as capture:
            runner.return_value.__enter__.return_value.run.return_value = result
            self.assertIs(hostops._default_hostops_exec_v1_runner(script, 5, 1024), result)
            verify.assert_called_once_with(runtime.return_value, require_protected_workflow=True)
            argv, stdin, timeout, limit = capture.call_args.args
            self.assertEqual(argv[:4], (TEST_PWSH, "-NoProfile", "-NonInteractive", "-Command"))
            self.assertIn("[Console]::InputEncoding", argv[4])
            self.assertIn("[Console]::OutputEncoding", argv[4])
            self.assertEqual((stdin, timeout, limit), (script.encode("utf-8"), 5, 1024))
            self.assertEqual(capture.call_args.kwargs["child_env"], {})
            resolve.side_effect = acquisition.AcquisitionBlocked("PRIVATE_BINARY")
            runner.reset_mock()
            with self.assertRaisesRegex(hostops.Denied, "^PROTECTED_HOSTOPS_ONLY$"):
                hostops._default_hostops_exec_v1_runner(script, 5, 1024)
            runner.assert_not_called()
            capture.assert_called_once()

    def test_source_ci_no_native_effect_and_future_cli_interface_contract(self):
        # Exercise each guard without modifying the executor's real environment.
        with patch.object(hostops.asyncio, "Runner", side_effect=AssertionError("no native event loop")) as native:
            for bad_env, code in (
                ({"AI_COMMERCE_GIT_GUARD_ENABLED": "1", "RUNNER_NAME": hostops.HOSTOPS_EXEC_V1_RUNNER_NAME},
                 "SOURCE_CI_EXECUTION_FORBIDDEN"),
                ({"PYTEST_CURRENT_TEST": "x", "RUNNER_NAME": hostops.HOSTOPS_EXEC_V1_RUNNER_NAME},
                 "SOURCE_CI_EXECUTION_FORBIDDEN"),
                ({"RUNNER_NAME": "AI-COMMERCE-EF02-02"}, "SOURCE_CI_EXECUTION_FORBIDDEN"),
                ({"GITHUB_ACTIONS": "true", "RUNNER_NAME": hostops.HOSTOPS_EXEC_V1_RUNNER_NAME},
                 "PROTECTED_WORKFLOW_DISPATCH_DISABLED"),
            ):
                with patch.object(hostops, "os", SimpleNamespace(name="nt", environ=bad_env)):
                    with patch.object(hostops, "sys", SimpleNamespace(modules={})):
                        with self.assertRaisesRegex(hostops.Denied, code):
                            hostops._default_hostops_exec_v1_runner("Get-Date", 5, 1024)
            native.assert_not_called()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_root = root / "state"
            enrollment, _ = self._enroll(state_root)
            digest = self._store_payload(enrollment, "Get-Date -Format o")
            req = hostops.build_hostops_exec_v1_request(
                job_id="job-cli-00001", nonce="f" * 32, payload_mode="PRIVATE_DIGEST", script_sha256=digest
            )
            token = hostops.sign_hostops_exec_v1_request(req, self.KEY)
            event_file = root / "event.json"
            event_file.write_text(json.dumps(self._issue_event(req, private=False)), encoding="utf-8")
            out_file = root / "receipt.json"

            # Source CI is refused before output paths, enrollment or the event are touched.
            with redirect_stdout(io.StringIO()) as stdout:
                rc = hostops.hostops_exec_v1_cli(
                    ["--action", "execute", "--event", str(event_file), "--output", str(out_file)],
                    env={"RUNNER_NAME": "AI-COMMERCE-EF02-02"},
                )
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(stdout.getvalue())["code"], "SOURCE_CI_EXECUTION_FORBIDDEN")
            self.assertFalse(out_file.exists())

            # GitHub Actions without protected dispatch flag fails closed with DISABLED.
            no_dispatch_env = self._protected_actions_env(HOSTOPS_EXEC_V1_PROTECTED_DISPATCH="OFF")
            blocked_broker = hostops.HostOpsExecV1Broker(
                enrollment=enrollment,
                runtime_inspector=lambda: self._runtime(env=no_dispatch_env),
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=Mock(side_effect=AssertionError("must not execute")),
                clock=lambda: self.NOW,
                require_protected_workflow=True,
            )
            rc = hostops.hostops_exec_v1_cli(
                ["--action", "execute", "--event", str(event_file), "--output", str(out_file)],
                env={**no_dispatch_env, "HOSTOPS_EXEC_V1_AUTH_TOKEN": token},
                broker=blocked_broker,
            )
            self.assertEqual(rc, 2)
            blocked_receipt = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertEqual(
                (blocked_receipt["status"], blocked_receipt["code"]),
                ("DISABLED", "PROTECTED_WORKFLOW_DISPATCH_DISABLED"),
            )

            # Future protected workflow env + enrolled broker + valid HMAC plans and executes cleanly.
            dispatch_env = self._protected_actions_env(HOSTOPS_EXEC_V1_AUTH_TOKEN=token)
            source = self.enterContext(patch.object(hostops, "_exec_v1_verify_source"))
            runner = Mock(return_value=hostops.HostOpsExecV1ProcessResult(0, b"2026-11-20T10:00:00Z", b""))
            ready_broker = hostops.HostOpsExecV1Broker(
                enrollment=enrollment,
                runtime_inspector=lambda: self._runtime(env=dispatch_env),
                authenticator=hostops.make_hostops_exec_v1_hmac_authenticator(self.KEY),
                runner=runner,
                clock=lambda: self.NOW,
                require_protected_workflow=True,
            )
            rc_plan = hostops.hostops_exec_v1_cli(
                ["--action", "plan", "--event", str(event_file), "--output", str(out_file)],
                env=dispatch_env,
                broker=ready_broker,
            )
            self.assertEqual(rc_plan, 0)
            self.assertEqual(json.loads(out_file.read_text(encoding="utf-8"))["status"], "PLANNED")
            runner.assert_not_called()

            rc_exec = hostops.hostops_exec_v1_cli(
                ["--action", "execute", "--event", str(event_file), "--output", str(out_file)],
                env=dispatch_env,
                broker=ready_broker,
            )
            self.assertEqual(rc_exec, 0)
            done = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertEqual((done["status"], done["code"]), ("EXECUTED", "EXECUTION_COMPLETED"))
            self.assertEqual(done["issue_number"], 1201)
            self.assertEqual(source.call_args_list, [call(self.SHA, enrollment_sha=self.SHA)] * 2)
            runner.assert_called_once_with("Get-Date -Format o", 30, hostops.HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES)

    def _exec_cli_receipt(self, argv, *, via_main=False, **kwargs):
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            if via_main:
                with patch.object(hostops.sys, "argv", ["personal.py", "--hostops-exec-v1", *argv]):
                    code = hostops.main()
            else:
                code = hostops.hostops_exec_v1_cli(argv, **kwargs)
        self.assertEqual(stderr.getvalue(), "")
        receipt = hostops.validate_hostops_exec_v1_receipt(json.loads(stdout.getvalue()))
        self.assertEqual(stdout.getvalue(), hostops._exec_v1_canonical(receipt).decode("ascii") + "\n")
        return code, receipt

    def _attended_exec_cli(self, action, extra=(), data=None):
        output = io.StringIO()
        stdin = SimpleNamespace(isatty=lambda: data is None, buffer=io.BytesIO(data or b""))
        with (redirect_stdout(output), patch.object(hostops.sys, "stdin", stdin),
              patch.object(output, "isatty", return_value=True)):
            rc = hostops.hostops_exec_v1_cli(["--action", action, *extra], env={})
        return rc, hostops.validate_hostops_exec_v1_receipt(json.loads(output.getvalue()))

    def _enroll_legacy_native(self, root, state):
        enrollment, _ = hostops.enroll_hostops_exec_v1(
            enrollment_id="a" * 32, source_sha=self.SHA, state_root=root, auth_key=self.NATIVE_KEY, runtime=state,
        )
        (root / hostops._EXEC_V1_KEY_FILENAME).write_bytes(self.NATIVE_KEY)
        journal = hostops.HostOpsExecV1Journal(enrollment.journal_path)
        with journal._session() as db:
            db.execute("INSERT INTO enrollment_pin VALUES (1,?,?,?)",
                       (enrollment.enrollment_id, enrollment.owner_sid, self.SHA))
        return enrollment, journal

    def test_native_runtime_disregards_spoofed_token_and_runner_environment(self):
        env = self._protected_actions_env(USER_SID=self.SID, SESSIONNAME="Console", HOSTOPS_ELEVATED="false")
        sid = self.SID
        with (patch.object(hostops, "os", SimpleNamespace(name="nt", environ=env)),
              patch.object(hostops.sys, "version_info", (3, 14)),
              patch.object(hostops, "_exec_v1_source_ci", return_value=False),
              patch.object(hostops, "_exec_v1_interactive_token", return_value=(4, False, sid, sid)) as token,
              patch.object(hostops, "_exec_v1_runner_lineage", return_value=hostops.HOSTOPS_EXEC_V1_RUNNER_NAME) as lineage):
            ctx = hostops._default_hostops_exec_v1_runtime(env)
            hostops._verify_hostops_exec_v1_runtime(ctx, expected_owner_sid=sid, require_protected_workflow=True)
            lineage.assert_called_once_with(4, sid)

            for facts, code in (
                ((0, False, sid, sid), "WRONG_SESSION_OR_ELEVATED"),
                ((4, True, sid, sid), "WRONG_SESSION_OR_ELEVATED"),
                ((4, False, sid, sid + "9"), "OWNER_SID_MISMATCH"),
                ((4, False, "", ""), "OWNER_SID_MISMATCH"),
            ):
                with self.subTest(facts=facts), self.assertRaisesRegex(hostops.Denied, code):
                    token.return_value = facts
                    hostops._default_hostops_exec_v1_runtime(env)
            token.side_effect = OSError("native token required")
            with self.assertRaisesRegex(hostops.Denied, "PROTECTED_HOSTOPS_ONLY"):
                hostops._default_hostops_exec_v1_runtime(env)
            token.side_effect = None
            token.return_value = (4, False, sid, sid)
            lineage.return_value = "OTHER"
            with self.assertRaisesRegex(hostops.Denied, "WRONG_RUNNER_FORBIDDEN"):
                hostops._verify_hostops_exec_v1_runtime(hostops._default_hostops_exec_v1_runtime(env),
                                                       require_protected_workflow=True)
            with self.assertRaisesRegex(hostops.Denied, "SOURCE_CI_EXECUTION_FORBIDDEN"):
                hostops._default_hostops_exec_v1_runtime({})

    def test_native_enrollment_staging_dispatch_and_revocation(self):
        from ai_commerce.remote import local_completion_handoff as private_acl

        key = self.NATIVE_KEY
        script = "Write-Output 'private-é'\r\n".encode("utf-8")
        later_sha = "b" * 40
        rotated_sha = "c" * 40
        active_head = self.SHA
        env = self._protected_actions_env(GITHUB_SHA=later_sha, GITHUB_WORKFLOW_SHA=later_sha)
        approved = b"# enrolled source\n"
        rotated = b"# reviewed upgrade\n"
        revisions = {self.SHA: approved, later_sha: approved, rotated_sha: rotated}

        def read_source(git_dir, sha, *, head=False):
            self.assertEqual(git_dir, workspace / ".git")
            selected = active_head if head else sha
            if (sha is not None and selected != sha) or selected not in revisions:
                hostops.deny("SOURCE_REVISION_MISMATCH")
            return selected, revisions[selected]

        state = self._runtime(env={})
        with (tempfile.TemporaryDirectory() as tmp,
              patch.object(hostops, "_exec_v1_source_ci", return_value=False),
              patch.object(hostops, "_default_hostops_exec_v1_runtime", return_value=state) as runtime,
              patch.object(hostops, "_exec_v1_pinned_source", side_effect=read_source) as source,
              patch.object(hostops, "_exec_v1_https_read", side_effect=OSError(11001, "DNS offline")) as network,
              patch.object(hostops, "_exec_v1_check_oidc") as oidc,
              patch.object(hostops, "_default_hostops_exec_v1_runner", return_value=hostops.HostOpsExecV1ProcessResult(
                  0, b"private output", b"")) as runner,
              patch.object(hostops.secrets, "token_bytes", return_value=key) as random_key,
              patch.object(hostops.secrets, "token_hex", return_value="a" * 32) as random_enrollment_id,
              patch.object(private_acl, "_windows_acl", return_value=(self.SID, (("allow", self.SID),))) as acl):
            root = Path(tmp) / "private"
            workspace = Path(tmp) / "workspace"
            (workspace / ".git/objects").mkdir(parents=True)
            module = workspace / hostops._EXEC_V1_SOURCE_PATH
            module.parent.mkdir(parents=True)
            module.write_bytes(approved.replace(b"\n", b"\r\n"))
            with (patch.object(hostops, "_exec_v1_native_state_root", return_value=root),
                  patch.object(hostops, "__file__", str(module)),
                  patch.object(hostops.sys, "stdin", SimpleNamespace(isatty=lambda: True))):
                # Enrollment is only available when stdout is also attended.
                rc, result = self._exec_cli_receipt(["--action", "enroll", "--source-sha", self.SHA], env={})
                self.assertEqual((rc, result["code"]), (2, "ATTENDED_OWNER_REQUIRED"))
                self.assertFalse(root.exists())

                attended = self._attended_exec_cli
                rc, receipt = attended("enroll", ["--source-sha", self.SHA])
                self.assertEqual((rc, receipt["code"], receipt["readback_verified"]), (0, "ENROLLMENT_RECORDED", True))
                self.assertNotIn(str(root), json.dumps(receipt))
                random_key.assert_called_once_with(32)
                key_path = root / hostops._EXEC_V1_KEY_FILENAME
                record = root / hostops.HOSTOPS_EXEC_V1_ENROLLMENT_FILENAME
                self.assertEqual(key_path.read_bytes(), key)
                original_record = record.read_bytes()
                self.assertNotIn(key.hex().encode("ascii"), original_record)
                self.assertEqual(attended("enroll", ["--source-sha", self.SHA])[1]["code"], "ALREADY_ENROLLED_NO_OVERWRITE")
                random_key.assert_called_once_with(32)
                random_enrollment_id.assert_called_once_with(16)
                journal = hostops.HostOpsExecV1Journal(root / hostops.HOSTOPS_EXEC_V1_JOURNAL_FILENAME, existing=True)
                enrollment = hostops.load_hostops_exec_v1_enrollment(record)
                first_approval = self.SHA, hashlib.sha256(approved).hexdigest()
                self.assertEqual(journal.source_approval(enrollment, key), first_approval)
                self.assertEqual(attended("rotate-source", ["--source-sha", self.SHA])[1]["code"],
                                 "SOURCE_ALREADY_APPROVED")
                with journal._session() as db:
                    self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (1,))

                self.assertEqual(attended("stage", data=b"\xff")[1]["code"], "PAYLOAD_OVERSIZED_OR_INVALID")
                rc, staged = attended("stage", data=script)
                digest = hashlib.sha256(script).hexdigest()
                self.assertEqual((rc, staged["code"], staged["script_sha256"]), (0, "PAYLOAD_STAGED", digest))
                self.assertNotIn("private-", json.dumps(staged))
                self.assertEqual(attended("stage", data=script)[1]["code"], "PAYLOAD_ALREADY_STAGED")

                request = hostops.build_hostops_exec_v1_request(job_id="job-native-001", nonce="f" * 32, script_sha256=digest)
                event_file = Path(tmp) / "event.json"
                event_file.write_text(json.dumps(self._issue_event(request)), encoding="utf-8")
                env["GITHUB_EVENT_PATH"] = str(event_file)
                active_head = later_sha
                del revisions[self.SHA]
                runtime.return_value = self._runtime(env=env)
                with patch.object(hostops, "_exec_v1_event_path", return_value=event_file) as checked_path:
                    source.reset_mock()
                    rc, plan = self._exec_cli_receipt(["--action", "plan"], env=env)
                    self.assertEqual((rc, plan["code"], plan["source_sha"]), (0, "READY_TO_EXECUTE", later_sha))
                    runner.assert_not_called()
                    checked_path.assert_called_once_with(str(event_file), runtime.return_value)
                    oidc.assert_called_once_with(env, request["request_digest"], action="plan")
                    source.assert_called_once_with(workspace / ".git", later_sha, head=True)
                    self.assertIsNone(journal.record(request["job_id"]))
                    with patch.object(hostops, "_exec_v1_check_oidc",
                                      side_effect=hostops.Denied("PROTECTED_HOSTOPS_ONLY")):
                        rc, rejected = self._exec_cli_receipt(["--action", "execute"], env=env)
                        self.assertEqual((rc, rejected["code"]), (2, "PROTECTED_HOSTOPS_ONLY"))
                    self.assertIsNone(journal.record(request["job_id"]))
                    tampered = b"# changed source\n"
                    for action in ("plan", "execute"):
                        for committed in (approved, tampered, approved.replace(b"\n", b"\r\n")):
                            with self.subTest(action=action, committed=committed):
                                module.write_bytes(tampered)
                                if committed.endswith(b"\r\n"):
                                    module.write_bytes(approved)
                                with patch.dict(revisions, {later_sha: committed}):
                                    rc, rejected = self._exec_cli_receipt(["--action", action], env=env)
                                self.assertEqual((rc, rejected["code"]), (2, "SOURCE_REVISION_MISMATCH"))
                                self.assertIsNone(rejected["source_sha"])
                                self.assertIsNone(journal.record(request["job_id"]))
                    runner.assert_not_called()
                    module.write_bytes(approved)

                    payload = root / hostops.HOSTOPS_EXEC_V1_PAYLOAD_DIRNAME / (digest + ".ps1")
                    self.assertEqual(payload.read_bytes(), script)
                    payload.write_bytes(b"Different bytes")
                    rc, mismatch = self._exec_cli_receipt(["--action", "execute", "--event", str(event_file)], env=env)
                    self.assertEqual((rc, mismatch["code"]), (2, "PAYLOAD_DIGEST_MISMATCH"))
                    payload.write_bytes(script)
                    runner.assert_not_called()
                    rc, done = self._exec_cli_receipt(["--action", "execute"], env=env)
                    self.assertEqual((rc, done["code"], done["source_sha"]), (0, "EXECUTION_COMPLETED", later_sha))
                    self.assertNotIn("private output", json.dumps(done))
                    self.assertNotIn(str(root), json.dumps(done))
                    runner.assert_called_once_with(script.decode("utf-8"), 30, hostops.HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES)
                    self.assertEqual(self._exec_cli_receipt(["--action", "execute"], env=env)[1]["code"], "DUPLICATE_JOB_ID")
                    runner.assert_called_once()

                    original_job = journal.record(request["job_id"])
                    active_head = rotated_sha
                    module.write_bytes(rotated)
                    request_c = hostops.build_hostops_exec_v1_request(
                        job_id="job-native-002", nonce="e" * 32, script_sha256=digest,
                    )
                    event_file.write_text(json.dumps(self._issue_event(request_c)), encoding="utf-8")
                    env_c = {**env, "GITHUB_SHA": rotated_sha, "GITHUB_WORKFLOW_SHA": rotated_sha}
                    runtime.return_value = self._runtime(env=env_c)
                    for dispatch_env in (env, env_c):
                        runtime.return_value = self._runtime(env=dispatch_env)
                        self.assertEqual(self._exec_cli_receipt(["--action", "execute"], env=dispatch_env)[1]["code"],
                                         "SOURCE_REVISION_MISMATCH")
                    self.assertIsNone(journal.record(request_c["job_id"]))
                    runner.assert_called_once()

                    runtime.return_value = self._runtime(env={}, owner_sid=self.SID + "9")
                    rotation_args = ["--source-sha", rotated_sha]
                    self.assertNotEqual(attended("rotate-source", rotation_args)[0], 0)
                    self.assertEqual(journal.source_approval(enrollment, key), first_approval)
                    runtime.return_value = state
                    rc, no_tty = self._exec_cli_receipt(["--action", "rotate-source", *rotation_args], env={})
                    self.assertEqual((rc, no_tty["code"]), (2, "ATTENDED_OWNER_REQUIRED"))
                    self.assertEqual(attended("rotate-source", ["--source-sha", later_sha])[1]["code"],
                                     "SOURCE_REVISION_MISMATCH")
                    rc, approval = attended("rotate-source", rotation_args)
                    self.assertEqual((rc, approval["code"], approval["source_sha"], approval["readback_verified"]),
                                     (0, "SOURCE_ROTATION_RECORDED", rotated_sha, True))
                    self.assertEqual(attended("rotate-source", rotation_args)[1]["code"], "SOURCE_ALREADY_APPROVED")
                    self.assertEqual(journal.source_approval(enrollment, key), (rotated_sha, hashlib.sha256(rotated).hexdigest()))
                    self.assertEqual(journal.record(request["job_id"]), original_job)
                    self.assertEqual(key_path.read_bytes(), key)
                    self.assertEqual(record.read_bytes(), original_record)
                    stale_journal = hostops.HostOpsExecV1Journal(
                        enrollment.journal_path, existing=True, native_source=(key, first_approval),
                    )
                    with self.assertRaisesRegex(hostops.Denied, "SOURCE_REVISION_MISMATCH"):
                        stale_journal.reserve(request_c, enrollment, hostops._exec_v1_stamp(self.NOW))
                    self.assertIsNone(journal.record(request_c["job_id"]))

                    runtime.return_value = self._runtime(env=env_c)
                    replay = hostops.build_hostops_exec_v1_request(
                        job_id="job-native-003", nonce=request["nonce"], script_sha256=digest,
                    )
                    event_file.write_text(json.dumps(self._issue_event(replay)), encoding="utf-8")
                    self.assertEqual(self._exec_cli_receipt(["--action", "execute"], env=env_c)[1]["code"], "REPLAY_DENIED")
                    self.assertIsNone(journal.record(replay["job_id"]))
                    event_file.write_text(json.dumps(self._issue_event(request_c)), encoding="utf-8")
                    rc, planned = self._exec_cli_receipt(["--action", "plan"], env=env_c)
                    self.assertEqual((rc, planned["code"], planned["source_sha"]), (0, "READY_TO_EXECUTE", rotated_sha))
                    with patch.dict(revisions, clear=True):
                        self.assertEqual(self._exec_cli_receipt(["--action", "execute"], env=env_c)[1]["code"],
                                         "SOURCE_REVISION_MISMATCH")
                    self.assertIsNone(journal.record(request_c["job_id"]))
                    runner.assert_called_once()
                    rc, completed = self._exec_cli_receipt(["--action", "execute"], env=env_c)
                    self.assertEqual((rc, completed["code"], completed["source_sha"]),
                                     (0, "EXECUTION_COMPLETED", rotated_sha))
                    self.assertEqual(runner.call_count, 2)

                    runtime.return_value = state
                    self.assertEqual(attended("revoke")[0], 0)
                    self.assertEqual(attended("rotate-source", rotation_args)[1]["code"], "ENROLLMENT_REVOKED")
                    self.assertEqual(attended("unenroll")[1]["code"], "REQUEST_INVALID")
                    self.assertEqual(attended("enroll", ["--source-sha", later_sha])[1]["code"], "ALREADY_ENROLLED_NO_OVERWRITE")
                    self.assertEqual(key_path.read_bytes(), key)
                    self.assertEqual(record.read_bytes(), original_record)
                    self.assertEqual(journal.record(request["job_id"])["state"], "EXECUTED")
                    self.assertEqual((root / hostops._EXEC_V1_REVOKED_FILENAME).read_bytes(), b"revoked\n")
                    random_key.assert_called_once_with(32)
                    runtime.return_value = self._runtime(env=env_c)
                    acl.reset_mock()
                    self.assertEqual(self._exec_cli_receipt(["--action", "execute"], env=env_c)[1]["code"], "ENROLLMENT_REVOKED")
                    self.assertNotIn(call(key_path), acl.call_args_list)
                    (root / hostops._EXEC_V1_REVOKED_FILENAME).unlink()
                    self.assertEqual(self._exec_cli_receipt(["--action", "execute"], env=env_c)[1]["code"], "ENROLLMENT_REVOKED")
                    runtime.return_value = state
                    self.assertEqual(attended("rotate-source", rotation_args)[1]["code"], "ENROLLMENT_REVOKED")
                    self.assertEqual(runner.call_count, 2)
                    network.assert_not_called()

    def test_native_state_rejects_foreign_acl_and_key_tampering(self):
        from ai_commerce.remote import local_completion_handoff as private_acl

        state = self._runtime(env={})
        with (tempfile.TemporaryDirectory() as tmp,
              patch.object(hostops, "_exec_v1_verify_source", return_value="0" * 64),
              patch.object(private_acl, "_windows_acl", return_value=(self.SID, (("allow", self.SID),))) as acl,
              patch.object(hostops.secrets, "token_bytes", return_value=b"X" * 32)):
            root = Path(tmp) / "private"
            hostops._exec_v1_enroll_native(root, state, self.SHA)
            record = root / hostops.HOSTOPS_EXEC_V1_ENROLLMENT_FILENAME
            original = record.read_bytes()
            record.write_text(json.dumps({**json.loads(original), "enabled": False}), encoding="utf-8")
            with self.assertRaisesRegex(hostops.Denied, "ENROLLMENT_DISABLED"):
                hostops._exec_v1_load_native(root, state)
            record.write_bytes(original)
            key = root / hostops._EXEC_V1_KEY_FILENAME
            key.write_bytes(b"Y" * 32)
            with self.assertRaisesRegex(hostops.Denied, "AUTHENTICATION_UNPROVEN"):
                hostops._exec_v1_load_native(root, state)
            key.unlink()
            with self.assertRaisesRegex(hostops.Denied, "PRIVATE_STATE_UNSAFE"):
                hostops._exec_v1_load_native(root, state)
            key.write_bytes(b"X" * 32)
            acl.return_value = self.SID, (("allow", self.SID), ("allow", "S-1-1-0"))
            with self.assertRaisesRegex(hostops.Denied, "PRIVATE_STATE_UNSAFE"):
                hostops._exec_v1_load_native(root, state)
            denied_root = Path(tmp) / "foreign-acl"
            with self.assertRaisesRegex(hostops.Denied, "PRIVATE_STATE_UNSAFE"):
                hostops._exec_v1_enroll_native(denied_root, state, self.SHA)
            self.assertFalse((denied_root / hostops._EXEC_V1_KEY_FILENAME).exists())

    def test_legacy_shallow_checkout_requires_attended_migration(self):
        from ai_commerce.remote import local_completion_handoff as private_acl

        state = self._runtime(env={})
        later_sha = "b" * 40
        blob = b"# unchanged source\n"
        revisions = {self.SHA: blob, later_sha: blob}

        def read_source(git_dir, sha, *, head=False):
            if (head and sha != later_sha) or sha not in revisions:
                hostops.deny("SOURCE_REVISION_MISMATCH")
            return sha, revisions[sha]

        with (tempfile.TemporaryDirectory() as tmp,
              patch.object(hostops, "_exec_v1_source_ci", return_value=False),
              patch.object(hostops, "_default_hostops_exec_v1_runtime") as runtime,
              patch.object(hostops, "_exec_v1_pinned_source", side_effect=read_source) as source,
              patch.object(hostops, "_exec_v1_check_oidc"),
              patch.object(hostops, "_exec_v1_https_read", side_effect=AssertionError("network forbidden")) as network,
              patch.object(hostops, "_default_hostops_exec_v1_runner") as runner,
              patch.object(private_acl, "_windows_acl", return_value=(self.SID, (("allow", self.SID),)))):
            root = Path(tmp) / "private"
            enrollment, journal = self._enroll_legacy_native(root, state)
            record = root / hostops.HOSTOPS_EXEC_V1_ENROLLMENT_FILENAME
            original_record = record.read_bytes()
            digest = self._store_payload(enrollment, "Write-Output 'fixture'")
            previous = hostops.build_hostops_exec_v1_request(
                job_id="job-legacy-001", nonce="e" * 32, script_sha256=digest,
            )
            journal.reserve(previous, enrollment, hostops._exec_v1_stamp(self.NOW))
            journal.finalize(previous["job_id"], hostops.HostOpsExecV1Broker()._receipt(
                status="EXECUTED", code="EXECUTION_COMPLETED", request=previous, enrolled=True,
                executed=True, readback_verified=True, exit_code=0, stdout_sha256="a" * 64, stderr_sha256="b" * 64,
            ))
            history = journal.record(previous["job_id"])
            request = hostops.build_hostops_exec_v1_request(
                job_id="job-legacy-002", nonce="f" * 32, script_sha256=digest,
            )
            event = Path(tmp) / "event.json"
            event.write_text(json.dumps(self._issue_event(request)), encoding="utf-8")
            env = self._protected_actions_env(GITHUB_SHA=later_sha, GITHUB_WORKFLOW_SHA=later_sha,
                                              GITHUB_EVENT_PATH=str(event))
            module = Path(tmp) / "personal.py"
            module.write_bytes(blob)
            git_dir = Path(tmp) / ".git"
            with (patch.object(hostops, "_exec_v1_source_checkout", return_value=(module, git_dir)),
                  patch.object(hostops, "_exec_v1_native_state_root", return_value=root),
                  patch.object(hostops, "_exec_v1_event_path", return_value=event)):
                runtime.return_value = self._runtime(env=env)
                self.assertEqual(self._exec_cli_receipt(["--action", "plan"], env=env)[1]["code"],
                                 "READY_TO_EXECUTE")
                self.assertIsNone(journal.source_approval(enrollment, self.NATIVE_KEY))
                del revisions[self.SHA]
                for action in ("plan", "execute"):
                    rc, receipt = self._exec_cli_receipt(["--action", action], env=env)
                    self.assertEqual((rc, receipt["code"]), (2, "SOURCE_REVISION_MISMATCH"))
                self.assertIsNone(journal.record(request["job_id"]))
                self.assertIsNone(journal.source_approval(enrollment, self.NATIVE_KEY))

                runtime.return_value = state
                source.reset_mock()
                args = ["--source-sha", later_sha]
                rc, receipt = self._attended_exec_cli("rotate-source", args)
                self.assertEqual((rc, receipt["code"], receipt["readback_verified"]),
                                 (0, "SOURCE_ROTATION_RECORDED", True))
                source.assert_called_once_with(git_dir, later_sha, head=True)
                self.assertEqual(journal.source_approval(enrollment, self.NATIVE_KEY),
                                 (later_sha, hashlib.sha256(blob).hexdigest()))
                self.assertEqual(self._attended_exec_cli("rotate-source", args)[1]["code"], "SOURCE_ALREADY_APPROVED")
                self.assertEqual(record.read_bytes(), original_record)
                self.assertEqual((root / hostops._EXEC_V1_KEY_FILENAME).read_bytes(), self.NATIVE_KEY)
                self.assertEqual(journal.record(previous["job_id"]), history)

                runtime.return_value = self._runtime(env=env)
                rc, receipt = self._exec_cli_receipt(["--action", "plan"], env=env)
                self.assertEqual((rc, receipt["code"], receipt["source_sha"]), (0, "READY_TO_EXECUTE", later_sha))
                event.write_text(json.dumps(self._issue_event(previous)), encoding="utf-8")
                self.assertEqual(self._exec_cli_receipt(["--action", "execute"], env=env)[1]["code"], "DUPLICATE_JOB_ID")
                runner.assert_not_called()
                network.assert_not_called()

    def test_new_enrollment_pin_rolls_back_with_interrupted_approval(self):
        from ai_commerce.remote import local_completion_handoff as private_acl

        append = hostops.HostOpsExecV1Journal._append_source_approval

        def interrupt(*args):
            append(*args)
            raise KeyboardInterrupt

        with (tempfile.TemporaryDirectory() as tmp,
              patch.object(private_acl, "_windows_acl", return_value=(self.SID, (("allow", self.SID),))),
              patch.object(hostops, "_exec_v1_verify_source", return_value="0" * 64),
              patch.object(hostops.HostOpsExecV1Journal, "_append_source_approval", new=interrupt)):
            root = Path(tmp) / "private"
            with self.assertRaises(KeyboardInterrupt):
                hostops._exec_v1_enroll_native(root, self._runtime(env={}), self.SHA)
            journal = hostops.HostOpsExecV1Journal(root / hostops.HOSTOPS_EXEC_V1_JOURNAL_FILENAME, existing=True)
            with journal._session() as db:
                self.assertFalse(db.execute("SELECT 1 FROM enrollment_pin").fetchall())
                self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (0,))
                self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='source_approvals'").fetchone())
            enrollment, key = hostops._exec_v1_load_native(root, self._runtime(env={}))
            with self.assertRaisesRegex(hostops.Denied, "ENROLLMENT_PIN_MISMATCH"):
                journal.source_approval(enrollment, key)

    def test_rotation_transaction_recovery_and_unknown_effect_hold(self):
        state = self._runtime(env={})
        c_sha, d_sha = "c" * 40, "d" * 40
        c_digest, d_digest = "1" * 64, "2" * 64
        with (tempfile.TemporaryDirectory() as tmp,
              patch.object(hostops, "_exec_v1_verify_source", return_value=c_digest) as verify,
              patch.object(hostops, "_exec_v1_legacy_source_digest") as legacy_digest):
            root = Path(tmp) / "private"
            enrollment, _ = self._enroll_legacy_native(root, state)
            key = self.NATIVE_KEY
            journal = hostops.HostOpsExecV1Journal(enrollment.journal_path, existing=True)
            session = journal._session

            @contextmanager
            def interrupt_before_commit():
                with session() as db:
                    yield db
                    raise KeyboardInterrupt

            with patch.object(journal, "_session", side_effect=interrupt_before_commit):
                with self.assertRaises(KeyboardInterrupt):
                    journal.rotate_source(enrollment, key, c_sha)
            self.assertIsNone(journal.source_approval(enrollment, key))
            with session() as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (0,))
                self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='source_approvals'").fetchone())

            with patch.object(journal, "source_approval", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    journal.rotate_source(enrollment, key, c_sha)
            self.assertEqual(journal.source_approval(enrollment, key), (c_sha, c_digest))
            self.assertEqual(journal.rotate_source(enrollment, key, c_sha), "SOURCE_ALREADY_APPROVED")

            request = hostops.build_hostops_exec_v1_request(job_id="job-hold-001", nonce="f" * 32, script_sha256="a" * 64)
            journal.reserve(request, enrollment, hostops._exec_v1_stamp(self.NOW))
            verify.reset_mock()
            verify.return_value = d_digest
            with self.assertRaisesRegex(hostops.Denied, "UNKNOWN_EFFECT_HOLD"):
                journal.rotate_source(enrollment, key, d_sha)
            journal.finalize(request["job_id"], hostops.HostOpsExecV1Broker()._error_receipt("UNKNOWN_EFFECT_HOLD"))
            prior = journal.record(request["job_id"])
            self.assertEqual(prior["state"], "UNKNOWN_EFFECT")
            with self.assertRaisesRegex(hostops.Denied, "UNKNOWN_EFFECT_HOLD"):
                journal.rotate_source(enrollment, key, d_sha)
            other = hostops.build_hostops_exec_v1_request(job_id="job-hold-002", nonce="e" * 32, script_sha256="a" * 64)
            with self.assertRaisesRegex(hostops.Denied, "UNKNOWN_EFFECT_HOLD"):
                journal.reserve(other, enrollment, hostops._exec_v1_stamp(self.NOW))
            self.assertIsNone(journal.record(other["job_id"]))
            self.assertEqual(journal.record(request["job_id"]), prior)
            self.assertEqual(journal.source_approval(enrollment, key), (c_sha, c_digest))
            verify.assert_not_called()
            legacy_digest.assert_not_called()

            with session() as db:
                db.execute("UPDATE source_approvals SET source_sha256=?", (d_digest,))
            with self.assertRaisesRegex(hostops.Denied, "JOURNAL_UNAVAILABLE"):
                journal.source_approval(enrollment, key)
            with session() as db:
                db.execute("DELETE FROM source_approvals")
            with self.assertRaisesRegex(hostops.Denied, "JOURNAL_UNAVAILABLE"):
                journal.rotate_source(enrollment, key, d_sha)
            self.assertEqual(journal.record(request["job_id"]), prior)

    def test_native_source_and_event_are_bound_to_the_fixed_runner_paths(self):
        from ai_commerce.hostops_v3 import security

        with tempfile.TemporaryDirectory() as tmp:
            runner = Path(tmp) / "runner"
            workspace = runner / "_work/ai-commerce/ai-commerce"
            (workspace / ".git/objects").mkdir(parents=True)
            module = workspace / hostops._EXEC_V1_SOURCE_PATH
            module.parent.mkdir(parents=True)
            module.write_bytes(b"pass\n")
            event_file = runner / "_work/_temp/_github_workflow/event.json"
            event_file.parent.mkdir(parents=True)
            event_file.write_bytes(b"{}")
            ctx = self._runtime(env=self._protected_actions_env(GITHUB_EVENT_PATH=str(event_file), GITHUB_WORKSPACE=str(workspace)))
            with (patch.object(hostops, "_EXEC_V1_RUNNER_ROOT", runner),
                  patch.object(hostops, "__file__", str(module)),
                  patch.object(security, "windows_acl") as acl,
                  patch.object(hostops, "_exec_v1_pinned_source", return_value=(self.SHA, b"pass\n")) as source):
                self.assertEqual(hostops._exec_v1_event_path(str(event_file), ctx), event_file)
                self.assertEqual(acl.call_count, 3)
                with self.assertRaisesRegex(hostops.Denied, "PROTECTED_HOSTOPS_ONLY"):
                    hostops._exec_v1_event_path(str(event_file.with_name("other.json")), ctx)
                hostops._exec_v1_verify_source(self.SHA)
                source.assert_called_once_with(workspace / ".git", self.SHA, head=True)
                source.return_value = self.SHA, b"raise RuntimeError\n"
                with self.assertRaisesRegex(hostops.Denied, "SOURCE_REVISION_MISMATCH"):
                    hostops._exec_v1_verify_source(self.SHA)

                real_lstat = type(event_file).lstat

                def marked_lstat(path):
                    if path == event_file.parent:
                        return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
                    return real_lstat(path)

                with patch.object(type(event_file), "lstat", marked_lstat):
                    with self.assertRaisesRegex(hostops.Denied, "PROTECTED_HOSTOPS_ONLY"):
                        hostops._exec_v1_event_path(str(event_file), ctx)

    def test_source_adapter_rehashes_commit_trees_and_blob_using_offline_cat_file(self):
        objects = {}

        def save(kind, raw):
            oid = hashlib.sha1(f"{kind} {len(raw)}\0".encode("ascii") + raw).hexdigest()
            objects[oid] = kind, raw
            return oid

        blob = b"# pinned first line\n# pinned second line\n"
        blob_oid = save("blob", blob)
        tree_oid = blob_oid
        for entry in reversed(hostops._EXEC_V1_SOURCE_PATH.parts):
            mode = b"100644" if entry == hostops._EXEC_V1_SOURCE_PATH.name else b"40000"
            tree_oid = save("tree", mode + b" " + entry.encode("ascii") + b"\0" + bytes.fromhex(tree_oid))
        enrollment_oid = save("commit", f"tree {tree_oid}\n\nEnrollment commit\n".encode("ascii"))
        commit_oid = save("commit", f"tree {tree_oid}\nparent {enrollment_oid}\n\nShallow HEAD\n".encode("ascii"))
        del objects[enrollment_oid]

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            git_dir = workspace / ".git"
            (git_dir / "objects").mkdir(parents=True)
            module = workspace / hostops._EXEC_V1_SOURCE_PATH
            module.parent.mkdir(parents=True)
            module.write_bytes(blob.replace(b"\n", b"\r\n"))

            def git(argv, **kwargs):
                self.assertEqual(argv[:5], ["git", "--no-replace-objects", "--no-lazy-fetch",
                                           f"--git-dir={git_dir}", "cat-file"])
                self.assertEqual(kwargs, {"cwd": workspace, "stdin": subprocess.DEVNULL,
                                          "capture_output": True, "check": True, "timeout": 5})
                operation, ref = argv[5:]
                if ref == "HEAD":
                    ref = commit_oid
                if ref not in objects:
                    raise subprocess.CalledProcessError(128, argv, stderr=b"missing object")
                kind, raw = objects[ref]
                if operation == "-s":
                    return subprocess.CompletedProcess(argv, 0, str(len(raw)).encode("ascii") + b"\n")
                self.assertEqual(operation, kind)
                return subprocess.CompletedProcess(argv, 0, raw)

            with (patch.object(hostops, "__file__", str(module)),
                  patch.object(hostops.subprocess, "run", side_effect=git) as adapter):
                fingerprint = hashlib.sha256(blob).hexdigest()

                def verify():
                    return hostops._exec_v1_verify_source(
                        commit_oid, enrollment_sha=enrollment_oid, approved_digest=fingerprint,
                    )

                self.assertEqual(verify(), fingerprint)
                self.assertTrue(all(invocation.args[0][-1] != enrollment_oid for invocation in adapter.call_args_list))
                with self.assertRaisesRegex(hostops.Denied, "SOURCE_REVISION_MISMATCH"):
                    hostops._exec_v1_verify_source(commit_oid, enrollment_sha=enrollment_oid)
                for oid in (commit_oid, tree_oid, blob_oid):
                    kind, raw = objects[oid]
                    corrupted = raw[:-1] + bytes([raw[-1] ^ 1])
                    with self.subTest(kind=kind), patch.dict(objects, {oid: (kind, corrupted)}):
                        with self.assertRaisesRegex(hostops.Denied, "SOURCE_REVISION_MISMATCH"):
                            verify()
                with self.assertRaisesRegex(hostops.Denied, "SOURCE_REVISION_MISMATCH"):
                    hostops._exec_v1_verify_source(self.SHA)
                del objects[blob_oid]
                with self.assertRaisesRegex(hostops.Denied, "SOURCE_REVISION_MISMATCH"):
                    verify()
                adapter.side_effect = subprocess.TimeoutExpired("git", 5)
                with self.assertRaisesRegex(hostops.Denied, "SOURCE_REVISION_MISMATCH"):
                    hostops._exec_v1_verify_source(commit_oid)

    def test_native_dispatch_rejects_malformed_or_public_raw_events_and_suppresses_errors(self):
        ctx = self._runtime(env=self._protected_actions_env())
        with (tempfile.TemporaryDirectory() as tmp,
              patch.object(hostops, "_exec_v1_source_ci", return_value=False),
              patch.object(hostops, "_default_hostops_exec_v1_runtime", return_value=ctx),
              patch.object(hostops, "_exec_v1_event_path") as checked_path,
              patch.object(hostops, "_exec_v1_native_state_root") as state,
              patch.object(hostops, "_exec_v1_check_oidc") as oidc):
            path = Path(tmp) / "event.json"
            checked_path.return_value = path
            inline = hostops.build_hostops_exec_v1_request(job_id="job-inline-001", nonce="e" * 32,
                                                          payload_mode="PRIVATE_INLINE_UTF8", script_utf8="private-body")
            for raw, code in (
                (b"{PRIVATE-ERROR", "EVENT_SHAPE_OR_STATE_DENIED"),
                (json.dumps(self._issue_event(inline)).encode(), "PUBLIC_ISSUE_RAW_SCRIPT_DENIED"),
                (json.dumps(self._issue_event(inline, sender="foreign")).encode(), "EVENT_OWNER_OR_REPO_DENIED"),
            ):
                path.write_bytes(raw)
                rc, receipt = self._exec_cli_receipt(["--action", "execute"], via_main=True)
                self.assertEqual((rc, receipt["code"]), (2, code))
                self.assertNotIn("private-body", json.dumps(receipt))
            oidc.assert_not_called()
            checked_path.side_effect = OSError("SECRET native credential and path")
            self.assertEqual(self._exec_cli_receipt(["--action", "execute"], via_main=True)[1]["code"], "PROTECTED_HOSTOPS_ONLY")
            self.assertEqual(self._exec_cli_receipt(["--secret-value"], via_main=True)[1]["code"], "REQUEST_INVALID")
            state.assert_not_called()

    def test_protected_job_checks_signed_claims_and_authority_before_loading_state(self):
        later_sha = "b" * 40
        env = self._protected_actions_env(
            GITHUB_SHA=later_sha,
            GITHUB_WORKFLOW_SHA=later_sha,
            ACTIONS_ID_TOKEN_REQUEST_URL="https://pipelinesghubeus10.actions.githubusercontent.com/scope/idtoken?api-version=2.0",
            ACTIONS_ID_TOKEN_REQUEST_TOKEN="fixture-oidc-job-token",
            GITHUB_RUN_ID="1234567",
        )
        now = int(self.NOW.timestamp())
        expected = {
            "iss": hostops._EXEC_V1_OIDC_ISSUER, "environment": "hostops-exec-v1",
            "repository": hostops.REPO, "repository_owner": hostops.OWNER, "repository_visibility": "public",
            "actor": hostops.OWNER, "event_name": "issues", "ref": "refs/heads/main", "ref_type": "branch",
            "ref_protected": "true", "workflow_ref": hostops.HOSTOPS_EXEC_V1_WORKFLOW_REF, "workflow_sha": later_sha,
            "sha": later_sha, "runner_environment": "self-hosted", "run_attempt": "1", "run_id": "1234567",
            "iat": now, "nbf": now, "exp": now + 300,
        }
        script = "Get-Date"
        request = hostops.build_hostops_exec_v1_request(
            job_id="job-oidc-001", nonce="f" * 32, script_sha256=hashlib.sha256(script.encode()).hexdigest(),
        )
        digest = request["request_digest"]
        ctx = self._runtime(env=env)
        args = SimpleNamespace(event=None, action="execute")

        def b64(value):
            return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

        def fake_read(url, _limit, *, bearer=None):
            if bearer is None:
                self.assertEqual(url, hostops._EXEC_V1_OIDC_ISSUER + "/.well-known/jwks")
                return b'{"keys":[{"kid":"test","kty":"RSA","alg":"RS256"}]}'
            self.assertEqual(bearer, env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"])
            audience = parse_qs(urlsplit(url).query)["audience"][0]
            self.assertEqual(audience, f"ai-commerce:hostops-exec-v1:{args.action}:{digest}:" + "b" * 32)
            header = b64(b'{"kid":"test","alg":"RS256"}')
            claims = b64(json.dumps({"aud": audience, **expected}).encode())
            return json.dumps({"value": f"{header}.{claims}.{b64(b'fixture-signature')}"}).encode()

        with (patch.object(hostops, "_exec_v1_https_read", side_effect=fake_read) as fetch,
              patch.object(hostops, "_exec_v1_verify_rs256") as verify,
              patch.object(hostops, "_exec_v1_event_path"),
              patch.object(hostops, "_exec_v1_read_event", return_value=self._issue_event(request)),
              patch.object(hostops, "_exec_v1_native_state_root") as state,
              patch.object(hostops.secrets, "token_hex", return_value="b" * 32),
              patch.object(hostops.time, "time", return_value=now)):
            hostops._exec_v1_check_oidc(env, digest, action="execute")
            verify.assert_called_once()
            for claim, denied in (("actor", "foreign"), ("repository_owner", "foreign"), ("event_name", "push"),
                                  ("ref", "refs/heads/topic"), ("sha", self.SHA), ("workflow_sha", self.SHA),
                                  ("workflow_ref", "other"), ("iss", "https://example.invalid"),
                                  ("ref_protected", "false"), ("ref_protected", None), ("environment", "other"),
                                  ("iat", now - 121), ("exp", now - 1), ("aud", "replay")):
                with self.subTest(claim=claim), patch.dict(expected, {claim: denied}):
                    with self.assertRaisesRegex(hostops.Denied, "PROTECTED_HOSTOPS_ONLY"):
                        hostops._exec_v1_protected_dispatch(args, ctx)
            verify.side_effect = hostops.Denied("PROTECTED_HOSTOPS_ONLY")
            with self.assertRaisesRegex(hostops.Denied, "PROTECTED_HOSTOPS_ONLY"):
                hostops._exec_v1_protected_dispatch(args, ctx)
            state.assert_not_called()
            fetch.reset_mock()
            for env_override in ({"ACTIONS_ID_TOKEN_REQUEST_TOKEN": ""}, {"GITHUB_WORKFLOW_SHA": self.SHA}):
                with self.subTest(env_override=env_override):
                    bad_ctx = ctx._replace(env={**env, **env_override})
                    with self.assertRaisesRegex(hostops.Denied, "PROTECTED_HOSTOPS_ONLY"):
                        hostops._exec_v1_protected_dispatch(args, bad_ctx)
            spoofed = {**env, "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.invalid/idtoken"}
            with self.assertRaisesRegex(hostops.Denied, "PROTECTED_HOSTOPS_ONLY"):
                hostops._exec_v1_check_oidc(spoofed, digest, action="execute")
            fetch.assert_not_called()
            fetch.side_effect = OSError(11001, "DNS unavailable")
            with (patch.object(hostops, "_exec_v1_source_ci", return_value=False),
                  patch.object(hostops, "_default_hostops_exec_v1_runtime", return_value=ctx)):
                rc, receipt = self._exec_cli_receipt(["--action", "execute"], env=env)
            self.assertEqual((rc, receipt["code"]), (2, "PROTECTED_HOSTOPS_ONLY"))
            self.assertFalse(receipt["executed"])
            state.assert_not_called()

            # Run the real dispatch and broker with offline OIDC and native boundaries mocked.
            verify.side_effect = None
            fetch.side_effect = fake_read
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "state"
                enrollment, journal = self._enroll_legacy_native(root, self._runtime())
                self._store_payload(enrollment, script)
                state.return_value = root

                def run(script_utf8, timeout, output_limit):
                    self.assertEqual((script_utf8, timeout, output_limit),
                                     (script, 30, hostops.HOSTOPS_EXEC_V1_MAX_OUTPUT_BYTES))
                    self.assertEqual(journal.record(request["job_id"])["state"], "INTENT")
                    return hostops.HostOpsExecV1ProcessResult(0, b"private output", b"")

                with (patch.object(hostops, "_exec_v1_load_native", return_value=(enrollment, self.NATIVE_KEY)),
                      patch.object(hostops, "_exec_v1_private_path"),
                      patch.object(hostops, "_exec_v1_verify_source") as source,
                      patch.object(hostops, "_default_hostops_exec_v1_runner", side_effect=run) as runner):
                    args.action = "plan"
                    plan = hostops._exec_v1_protected_dispatch(args, ctx)
                    self.assertEqual((plan["status"], plan["source_sha"]), ("PLANNED", later_sha))
                    source.assert_called_once_with(later_sha, enrollment_sha=self.SHA, approved_digest=None)
                    self.assertIsNone(journal.record(request["job_id"]))
                    runner.assert_not_called()
                    args.action = "execute"
                    result = hostops._exec_v1_protected_dispatch(args, ctx)
                    self.assertEqual((result["status"], result["source_sha"]), ("EXECUTED", later_sha))
                    self.assertEqual(result["issue_number"], 1201)
                    self.assertNotIn("private output", json.dumps(result))
                    self.assertEqual(source.call_count, 2)
                    runner.assert_called_once()

    def test_native_cli_ci_guard_precedes_all_private_and_process_effects(self):
        with (patch.object(hostops, "_default_hostops_exec_v1_runtime") as runtime,
              patch.object(hostops, "_exec_v1_native_state_root") as state,
              patch.object(hostops, "_exec_v1_check_oidc") as oidc,
              patch.object(hostops, "_exec_v1_verify_source") as source,
              patch.object(hostops, "_default_hostops_exec_v1_runner") as runner):
            for env in ({"GITHUB_ACTIONS": "true"}, {"PYTEST_CURRENT_TEST": "offline"},
                        {"AI_COMMERCE_GIT_GUARD_ACTIVE": "1"}, {"CI": "true"}):
                for action in ("enroll", "rotate-source"):
                    with self.subTest(env=env, action=action):
                        rc, receipt = self._exec_cli_receipt(["--action", action, "--source-sha", self.SHA], env=env)
                        self.assertEqual((rc, receipt["code"]), (2, "SOURCE_CI_EXECUTION_FORBIDDEN"))
            env = self._protected_actions_env(AI_COMMERCE_GIT_GUARD_ACTIVE="1")
            for action in ("plan", "execute"):
                rc, receipt = self._exec_cli_receipt(["--action", action], env=env)
                self.assertEqual((rc, receipt["code"]), (2, "SOURCE_CI_EXECUTION_FORBIDDEN"))
            runtime.assert_not_called()
            state.assert_not_called()
            oidc.assert_not_called()
            source.assert_not_called()
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
