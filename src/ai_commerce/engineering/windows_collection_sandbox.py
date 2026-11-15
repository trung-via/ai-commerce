"""P1-B2c Windows *inert fixture* provider. This does not authorize imports.

OS proof is obtained only by run_inert_probe on an ephemeral GitHub-hosted
Windows job. A fresh, signature-verified GitHub OIDC token (including its
runner_environment claim), REST job metadata and exact-head source contents
are required BEFORE any native call, compiler, profile or scratch creation.
Environment flags alone cannot admit a runner. Missing OIDC permissions,
compiler, metadata, native capability, or skipped tests are an UNPROVEN gap.

The existing AUTO owner command selects test_windows_native_inert_provider
in test_minimum_test.py. Hosted CI must opt in with PROFILE_SHA256, grant
id-token:write and contents/actions:read, and retain that test's JUnit receipt.
This source change cannot grant those workflow permissions. Independent
exact-head hosted execution and Brain review are still required. A local
receipt is never acceptance or permission to run candidate pytest imports.
"""
from __future__ import annotations

import base64
import ctypes as C
from ctypes import wintypes as W
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


SOURCE_PATHS = (
    "src/ai_commerce/engineering/windows_collection_sandbox.py",
    "src/ai_commerce/engineering/minimum_test.py",
    "tests/test_minimum_test.py",
)
_PROFILE = dict(version=1, kind="INERT_NATIVE_ONLY", capabilities=[],
                lifecycle_seconds=55, work_seconds=38, output_bytes=4096,
                artifact_bytes=4 * 1024 * 1024, job_limit_flags=0x2000,
                faults=["none", "timeout", "assignment"], source_paths=SOURCE_PATHS)
PROFILE_SHA256 = _sha(_json(_PROFILE))
# The native fixture uses absolute paths and Win32 APIs, and needs no PATH,
# user profile, Python configuration, auth variables or inherited handles.
_ENVIRONMENT = {"SystemRoot": r"C:\Windows"}  # Compiler's minimal inert environment.
# The AppContainer process API requires its authenticated host profile location.
# Bind the *kind* of dynamic input in this source-owned, path-free digest; never
# inherit the entire runner environment or expose LOCALAPPDATA in receipts.
_APP_CONTAINER_ENV_TEMPLATE = {"LOCALAPPDATA": "WIN32_VERIFIED_LOCALAPPDATA",
                               "SystemRoot": r"C:\Windows",
                               "TEMP": "WIN32_PINNED_SCRATCH_OS_REDIRECT", "TMP": "WIN32_PINNED_SCRATCH_OS_REDIRECT"}
ENVIRONMENT_SHA256 = _sha(_json(_APP_CONTAINER_ENV_TEMPLATE))
OPT_IN_VARIABLE = "AI_COMMERCE_WINDOWS_NATIVE_PROOF"
_AUDIENCE = "urn:ai-commerce:mt01:inert-windows:"


class Unproven(RuntimeError):
    """Only source-owned codes, never paths, tokens or raw native messages."""

    def __init__(self, code, native_error=None):
        super().__init__(code)
        self.code = code
        self.native_error = native_error


class _Deadline:
    def __init__(self):
        self.start = time.monotonic()

    def remaining(self, *, cleanup=False, maximum=3):
        limit = 54 if cleanup else _PROFILE["work_seconds"]
        left = limit - (time.monotonic() - self.start)
        if left <= 0:
            raise Unproven("NATIVE_LIFECYCLE_DEADLINE")
        return min(left, maximum)


def source_digest(repository):
    """Offline identity helper; it neither executes source nor proves its origin."""
    root = Path(os.path.abspath(repository))
    manifest = {}
    for relative in SOURCE_PATHS:
        path = root / relative
        for component in (path, *path.parents):
            info = component.lstat()
            if component.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise Unproven("NATIVE_REPARSE_SOURCE_REJECTED")
        with path.open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise Unproven("NATIVE_SOURCE_SIZE_LIMIT")
        manifest[relative] = _sha(data)
    return _sha(_json(manifest))


def _validate_binding(candidate_sha, source_sha256, profile_sha256, environment_sha256, fault):
    if not isinstance(candidate_sha, str) or not re.fullmatch("[0-9a-f]{40}", candidate_sha):
        raise Unproven("NATIVE_EXACT_HEAD_REQUIRED")
    if not isinstance(source_sha256, str) or not re.fullmatch("[0-9a-f]{64}", source_sha256):
        raise Unproven("NATIVE_SOURCE_BINDING_REQUIRED")
    if profile_sha256 != PROFILE_SHA256:
        raise Unproven("NATIVE_PROFILE_MISMATCH")
    if environment_sha256 != ENVIRONMENT_SHA256:
        raise Unproven("NATIVE_ENVIRONMENT_MISMATCH")
    if fault not in _PROFILE["faults"]:
        raise Unproven("NATIVE_FAULT_NOT_REGISTERED")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Unproven("HOSTED_METADATA_REDIRECT_REJECTED")


def _http_json(url, deadline, *, bearer=None):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment
            or not (host == "api.github.com" or host == "token.actions.githubusercontent.com"
                    or host.endswith(".actions.githubusercontent.com"))):
        raise Unproven("HOSTED_METADATA_ORIGIN_REJECTED")
    headers = {"Accept": "application/json", "User-Agent": "MT01-inert-native-proof"}
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.build_opener(_NoRedirect).open(
                request, timeout=deadline.remaining()) as response:
            chunks, length = [], 0
            while True:
                deadline.remaining()
                chunk = response.read1(min(65536, 1024 * 1024 + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk); length += len(chunk)
                if length > 1024 * 1024:
                    raise Unproven("HOSTED_METADATA_OVERSIZED")
            data = b"".join(chunks)
        if len(data) > 1024 * 1024:
            raise Unproven("HOSTED_METADATA_OVERSIZED")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (OSError, ValueError, urllib.error.URLError):
        raise Unproven("HOSTED_METADATA_UNAVAILABLE") from None


def _b64(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise Unproven("HOSTED_OIDC_MALFORMED")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _verify_oidc(token, jwks, *, audience, candidate_sha, repository, run_id, attempt, now):
    """RS256/PKCS#1 v1.5 verification against HTTPS issuer keys, no decode-only trust.

    The full encoded RSA block is compared, including padding and DigestInfo.
    No token-supplied key URL or algorithm is accepted.
    """
    try:
        if not isinstance(token, str) or len(token) > 24000:
            raise ValueError
        header_part, claims_part, signature_part = token.split(".")
        header, claims = json.loads(_b64(header_part)), json.loads(_b64(claims_part))
        if (header.get("alg") != "RS256" or header.get("typ") != "JWT"
                or set(header) - {"alg", "typ", "kid", "x5t"}):
            raise ValueError
        keys = [k for k in jwks["keys"] if k.get("kid") == header.get("kid")
                and k.get("kty") == "RSA" and k.get("use") == "sig"
                and k.get("alg", "RS256") == "RS256"]
        if len(keys) != 1:
            raise ValueError
        modulus = int.from_bytes(_b64(keys[0]["n"]), "big")
        exponent = int.from_bytes(_b64(keys[0]["e"]), "big")
        if not 2048 <= modulus.bit_length() <= 8192 or exponent != 65537:
            raise ValueError
        signature = _b64(signature_part)
        size = (modulus.bit_length() + 7) // 8
        number = int.from_bytes(signature, "big")
        if len(signature) != size or number >= modulus:
            raise ValueError
        hashed = hashlib.sha256((header_part + "." + claims_part).encode("ascii")).digest()
        tail = bytes.fromhex("3031300d060960864801650304020105000420") + hashed
        expected = b"\x00\x01" + b"\xff" * (size - len(tail) - 3) + b"\x00" + tail
        actual = pow(number, exponent, modulus).to_bytes(size, "big")
        if not hmac.compare_digest(actual, expected):
            raise ValueError
        if any(type(claims.get(k)) is not int for k in ("iat", "nbf", "exp")):
            raise ValueError
        if not (now - 120 <= claims["iat"] <= now + 10
                and claims["nbf"] <= now + 10 < claims["exp"] <= now + 600):
            raise ValueError
        required = dict(iss="https://token.actions.githubusercontent.com", aud=audience,
                        runner_environment="github-hosted", sha=candidate_sha,
                        repository=repository, run_id=run_id, run_attempt=attempt)
        if any(claims.get(k) != v for k, v in required.items()):
            raise ValueError
        workflow = claims.get("workflow_ref", "")
        if not workflow.startswith(repository + "/.github/workflows/") or "@refs/" not in workflow:
            raise ValueError
        return claims
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise Unproven("HOSTED_OIDC_VERIFICATION_FAILED") from None



def _verified_hosted_job_metadata(run, jobs, *, repo, run_id, attempt,
                                  runner_name, github_ref, head_ref):
    """Match GitHub's authenticated PR-head job SHA, not the PR merge checkout SHA.

    The signed OIDC sha remains the exact checked-out synthetic merge commit.
    GitHub Actions job/run head_sha is the immutable PR head commit instead.
    These distinct identities are linked only by the authenticated run's PR
    event snapshot, with same-repository origin and a single main-base PR.
    """
    fail = "HOSTED_WINDOWS_JOB_METADATA_UNPROVEN"
    match = re.fullmatch(r"refs/pull/([1-9][0-9]*)/merge", github_ref or "")
    if not match or not isinstance(run, dict) or not isinstance(jobs, dict):
        raise Unproven(fail)
    origin = run.get("repository")
    head_origin = run.get("head_repository")
    pr_list = run.get("pull_requests")
    if (not isinstance(origin, dict) or not isinstance(head_origin, dict)
            or origin.get("full_name") != repo
            or head_origin.get("id") != origin.get("id")
            or type(run.get("id")) is not int or run["id"] != int(run_id)
            or type(run.get("run_attempt")) is not int or run["run_attempt"] != int(attempt)
            or run.get("event") != "pull_request"
            or run.get("status") != "in_progress"
            or not isinstance(pr_list, list) or len(pr_list) != 1
            or not isinstance(pr_list[0], dict)):
        raise Unproven(fail)
    pr = pr_list[0]
    head, base = pr.get("head"), pr.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise Unproven(fail)
    expected_head = run.get("head_sha")
    if (not isinstance(expected_head, str)
            or not re.fullmatch(r"[0-9a-f]{40}", expected_head)
            or pr.get("number") != int(match.group(1))
            or head.get("sha") != expected_head
            or not isinstance(head_ref, str) or not head_ref
            or head.get("ref") != head_ref
            or run.get("head_branch") != head_ref
            or not isinstance(head.get("repo"), dict)
            or head["repo"].get("id") != origin["id"]
            or not isinstance(base.get("repo"), dict)
            or base["repo"].get("id") != origin["id"]
            or base.get("ref") != "main"):
        raise Unproven(fail)
    all_jobs = jobs.get("jobs")
    if (type(jobs.get("total_count")) is not int
            or not 0 < jobs["total_count"] <= 100
            or not isinstance(all_jobs, list)
            or len(all_jobs) != jobs["total_count"]):
        raise Unproven(fail)
    matches = [j for j in all_jobs if isinstance(j, dict)
               and j.get("runner_name") == runner_name
               and j.get("status") == "in_progress"
               and j.get("head_sha") == expected_head
               and j.get("run_id") == int(run_id)
               and j.get("runner_group_name") == "GitHub Actions"
               and isinstance(j.get("labels"), list)
               and set(j["labels"]) & {"windows-2022", "windows-2025", "windows-latest"}
               and "self-hosted" not in j["labels"]]
    if len(matches) != 1 or type(matches[0].get("id")) is not int:
        raise Unproven(fail)
    return matches[0]["id"], expected_head


def _hosted_identity(repository, candidate_sha, deadline):
    """Authenticate before native effects, even if RUNNER_ENVIRONMENT was forged."""
    env = os.environ
    repo = env.get("GITHUB_REPOSITORY", "")
    run_id, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "")
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
            or not re.fullmatch(r"[1-9][0-9]{0,5}", attempt)
            or env.get("GITHUB_SHA") != candidate_sha
            or not env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN") or not env.get("GITHUB_TOKEN")):
        raise Unproven("HOSTED_SIGNED_PROVENANCE_REQUIRED")
    audience = _AUDIENCE + uuid.uuid4().hex
    url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    query = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)
    query = [(k, v) for k, v in query if k != "audience"] + [("audience", audience)]
    parts = urllib.parse.urlsplit(url)
    if not (parts.hostname or "").endswith(".actions.githubusercontent.com"):
        raise Unproven("HOSTED_OIDC_REQUEST_ORIGIN_REJECTED")
    url = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
    token = _http_json(url, deadline, bearer=env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]).get("value")
    jwks = _http_json("https://token.actions.githubusercontent.com/.well-known/jwks", deadline)
    claims = _verify_oidc(token, jwks, audience=audience, candidate_sha=candidate_sha,
                          repository=repo, run_id=run_id, attempt=attempt, now=time.time())
    api = "https://api.github.com/repos/" + repo
    jobs = _http_json(api + f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100",
                      deadline, bearer=env["GITHUB_TOKEN"])
    run = _http_json(api + f"/actions/runs/{run_id}", deadline, bearer=env["GITHUB_TOKEN"])
    job_id, job_head_sha = _verified_hosted_job_metadata(
        run, jobs, repo=repo, run_id=run_id, attempt=attempt,
        runner_name=env.get("RUNNER_NAME"), github_ref=env.get("GITHUB_REF"),
        head_ref=env.get("GITHUB_HEAD_REF"))
    # Compare exact GitHub source blobs as well as caller's local source digest.
    for relative in SOURCE_PATHS:
        data = (Path(repository) / relative).read_bytes()
        item = _http_json(api + "/contents/" + relative + "?ref=" + candidate_sha,
                          deadline, bearer=env["GITHUB_TOKEN"])
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if item.get("type") != "file" or item.get("sha") != blob:
            raise Unproven("HOSTED_EXACT_HEAD_SOURCE_MISMATCH")
    return dict(repository=repo, candidate_sha=candidate_sha, run_id=int(run_id),
                run_attempt=int(attempt), job_id=job_id, pr_head_sha=job_head_sha,
                runner_environment=claims["runner_environment"],
                workflow_sha256=_sha(claims["workflow_ref"].encode()))


# A fixed, native, /MT executable: no Python, shell, user code or external endpoint.
# Its only writes are a 64-byte report and a child process inside the same Job.
_FIXTURE_C = r'''
#define _WIN32_WINNT 0x0602
#include <winsock2.h>
#include <windows.h>
#include <sddl.h>
#include <stdlib.h>
#include <wchar.h>
static DWORD file_error(const wchar_t *p, DWORD access) {
    HANDLE h = CreateFileW(p, access, FILE_SHARE_READ|FILE_SHARE_WRITE,
                          NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return GetLastError();
    CloseHandle(h); return 0;
}
int wmain(int argc, wchar_t **argv) {
    if (argc == 2 && wcscmp(argv[1], L"grandchild") == 0) { Sleep(INFINITE); return 0; }
    if (argc != 10 || wcscmp(argv[1], L"probe") != 0) return 81;
    DWORD r[16] = {0}; r[0] = 0x4d543031;
    HANDLE token = NULL; DWORD length = 0, ac = 0;
    BYTE buffer[4096];
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return 82;
    if (!GetTokenInformation(token, TokenIsAppContainer, &ac, sizeof(ac), &length)) return 83;
    r[1] = ac;
    if (!GetTokenInformation(token, TokenCapabilities, buffer, sizeof(buffer), &length)) return 84;
    r[2] = ((TOKEN_GROUPS*)buffer)->GroupCount;
    if (!GetTokenInformation(token, TokenPrivileges, buffer, sizeof(buffer), &length)) return 85;
    TOKEN_PRIVILEGES *privs = (TOKEN_PRIVILEGES*)buffer;
    LUID traverse; if (!LookupPrivilegeValueW(NULL, SE_CHANGE_NOTIFY_NAME, &traverse)) return 86;
    for (DWORD i=0; i<privs->PrivilegeCount; i++) {
        LUID_AND_ATTRIBUTES a = privs->Privileges[i];
        if ((a.Attributes & SE_PRIVILEGE_ENABLED) &&
            (a.Luid.LowPart != traverse.LowPart || a.Luid.HighPart != traverse.HighPart)) r[3]++;
    }
    if (!GetTokenInformation(token, TokenAppContainerSid, buffer, sizeof(buffer), &length)) return 96;
    LPWSTR sid_string = NULL;
    if (!ConvertSidToStringSidW(((TOKEN_APPCONTAINER_INFORMATION*)buffer)->TokenAppContainer, &sid_string)) return 97;
    r[13] = wcscmp(sid_string, argv[8]) == 0; LocalFree(sid_string);
    CloseHandle(token);
    // Windows redirects LOCALAPPDATA to the AppContainer profile, and both
    // TEMP/TMP to its Temp subdirectory. argv[9] is the independently verified
    // GetAppContainerFolderPath from the parent, never a caller override.
    // Reject every other value, duplicate key or unexpected inherited entry.
    wchar_t expected_temp[32768];
    if (!argv[9][0] || wcslen(argv[9]) > 32756 ||
        swprintf_s(expected_temp, 32768, L"%s\\Temp", argv[9]) < 0) return 98;
    LPWCH env = GetEnvironmentStringsW();
    r[12] = 0;
    if (env) {
        DWORD count = 0, matches = 0, drives = 0, profile = 0, other = 0;
        for (LPWCH item = env; *item; item += wcslen(item) + 1) {
            if (++count > 32) { other = 15; break; }
            if (wcsncmp(item, L"LOCALAPPDATA=", 13) == 0) {
                if ((matches & 1) || wcscmp(item + 13, argv[9]) != 0) other++;
                else matches |= 1;
            } else if (wcscmp(item, L"SystemRoot=C:\\Windows") == 0 && !(matches & 2)) {
                matches |= 2;
            } else if (wcsncmp(item, L"TEMP=", 5) == 0) {
                const wchar_t *value = item + 5;
                if ((profile & 2) || wcscmp(value, expected_temp) != 0) other++;
                else profile |= 2;
            } else if (wcsncmp(item, L"TMP=", 4) == 0) {
                const wchar_t *value = item + 4;
                if ((profile & 4) || wcscmp(value, expected_temp) != 0) other++;
                else profile |= 4;
            } else if (item[0] == L'=' && ((item[1] >= L'A' && item[1] <= L'Z')
                        || (item[1] >= L'a' && item[1] <= L'z'))
                        && item[2] == L':' && item[3] == L'=') {
                drives++;
            } else {
                other++;
            }
        }
        r[14] = matches | (count == 4 ? 4 : 0);
        r[15] = (count > 15 ? 15 : count)
              | ((drives > 15 ? 15 : drives) << 4)
              | ((profile & 15) << 8)
              | ((other > 15 ? 15 : other) << 12);
        r[12] = count == 4 && matches == 3 && profile == 6 && drives == 0 && other == 0;
    }
    if (env) FreeEnvironmentStringsW(env);
    r[4] = file_error(argv[2], GENERIC_READ);
    r[5] = file_error(argv[3], GENERIC_WRITE);
    r[6] = file_error(argv[4], GENERIC_READ);
    r[7] = file_error(argv[3], GENERIC_READ);
    r[8] = file_error(argv[5], GENERIC_READ);
    WSADATA wsa; if (WSAStartup(MAKEWORD(2,2), &wsa)) return 87;
    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) return 88;
    SOCKADDR_IN target = {0}; target.sin_family = AF_INET;
    target.sin_addr.s_addr = htonl(INADDR_LOOPBACK); target.sin_port = htons((u_short)_wtoi(argv[6]));
    r[9] = connect(s, (SOCKADDR*)&target, sizeof(target)) == SOCKET_ERROR ? WSAGetLastError() : 0;
    closesocket(s); WSACleanup();
    wchar_t exe[32768], cmd[32768];
    if (!GetModuleFileNameW(NULL, exe, 32768)) return 89;
    if (swprintf_s(cmd, 32768, L"\"%s\" grandchild", exe) < 0) return 90;
    STARTUPINFOW si = {0}; si.cb = sizeof(si); PROCESS_INFORMATION pi = {0};
    if (!CreateProcessW(exe, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) return 91;
    r[10] = pi.dwProcessId; CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    r[11] = GetCurrentProcessId();
    wchar_t report_tmp[32768];
    if (swprintf_s(report_tmp, 32768, L"%s.tmp", argv[7]) < 0) return 92;
    HANDLE report = CreateFileW(report_tmp, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (report == INVALID_HANDLE_VALUE) return 92;
    DWORD written = 0;
    if (!WriteFile(report, r, sizeof(r), &written, NULL) || written != sizeof(r)) return 93;
    if (!FlushFileBuffers(report)) return 94;
    CloseHandle(report);
    if (!MoveFileExW(report_tmp, argv[7], MOVEFILE_WRITE_THROUGH)) return 95;
    Sleep(INFINITE); return 0;
}
'''


class _IO_COUNTERS(C.Structure):
    _fields_ = [(n, C.c_ulonglong) for n in ("read_ops", "write_ops", "other_ops", "read", "write", "other")]


class _BASIC_LIMIT(C.Structure):
    _fields_ = [("process_time", C.c_longlong), ("job_time", C.c_longlong), ("flags", W.DWORD),
                ("min_working", C.c_size_t), ("max_working", C.c_size_t), ("active", W.DWORD),
                ("affinity", C.c_size_t), ("priority", W.DWORD), ("scheduling", W.DWORD)]


class _EXTENDED_LIMIT(C.Structure):
    _fields_ = [("basic", _BASIC_LIMIT), ("io", _IO_COUNTERS), ("process_memory", C.c_size_t),
                ("job_memory", C.c_size_t), ("peak_process", C.c_size_t), ("peak_job", C.c_size_t)]


class _STARTUP(C.Structure):
    _fields_ = [("cb", W.DWORD), ("reserved", W.LPWSTR), ("desktop", W.LPWSTR), ("title", W.LPWSTR),
                *[(n, W.DWORD) for n in ("x", "y", "width", "height", "chars_x", "chars_y", "fill", "flags")],
                ("show", W.WORD), ("reserved_size", W.WORD), ("reserved_bytes", C.c_void_p),
                ("stdin", W.HANDLE), ("stdout", W.HANDLE), ("stderr", W.HANDLE)]


class _STARTUP_EX(C.Structure):
    _fields_ = [("startup", _STARTUP), ("attributes", C.c_void_p)]


class _PROCESS(C.Structure):
    _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE), ("pid", W.DWORD), ("tid", W.DWORD)]


class _CAPABILITIES(C.Structure):
    _fields_ = [("sid", C.c_void_p), ("capabilities", C.c_void_p),
                ("count", W.DWORD), ("reserved", W.DWORD)]


class _Win32:
    """Private fixed-fixture launcher. Never accepts candidate code or shell text."""
    def __init__(self, deadline):
        self.deadline = deadline
        self.k = C.WinDLL("kernel32.dll", winmode=0x800, use_last_error=True)
        self.a = C.WinDLL("advapi32.dll", winmode=0x800, use_last_error=True)
        self.u = C.WinDLL("userenv.dll", winmode=0x800, use_last_error=True)
        signatures = {
            "CloseHandle": ([W.HANDLE], W.BOOL), "LocalFree": ([C.c_void_p], C.c_void_p),
            "CreateFileW": ([W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
            "GetFileInformationByHandleEx": ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
            "GetFinalPathNameByHandleW": ([W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD], W.DWORD),
            "CreateJobObjectW": ([C.c_void_p, W.LPCWSTR], W.HANDLE),
            "SetInformationJobObject": ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
            "QueryInformationJobObject": ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.c_void_p], W.BOOL),
            "AssignProcessToJobObject": ([W.HANDLE, W.HANDLE], W.BOOL),
            "IsProcessInJob": ([W.HANDLE, W.HANDLE, C.POINTER(W.BOOL)], W.BOOL),
            "ResumeThread": ([W.HANDLE], W.DWORD),
            "TerminateProcess": ([W.HANDLE, W.UINT], W.BOOL),
            "TerminateJobObject": ([W.HANDLE, W.UINT], W.BOOL),
            "WaitForSingleObject": ([W.HANDLE, W.DWORD], W.DWORD),
            "OpenProcess": ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            "GetExitCodeProcess": ([W.HANDLE, C.POINTER(W.DWORD)], W.BOOL),
            "InitializeProcThreadAttributeList": ([C.c_void_p, W.DWORD, W.DWORD, C.POINTER(C.c_size_t)], W.BOOL),
            "UpdateProcThreadAttribute": ([C.c_void_p, W.DWORD, C.c_size_t, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p], W.BOOL),
            "DeleteProcThreadAttributeList": ([C.c_void_p], None),
            "CreateProcessW": ([W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD,
                                C.c_void_p, W.LPCWSTR, C.POINTER(_STARTUP_EX), C.POINTER(_PROCESS)], W.BOOL),
            "DeviceIoControl": ([W.HANDLE, W.DWORD, C.c_void_p, W.DWORD, C.c_void_p,
                                 W.DWORD, C.POINTER(W.DWORD), C.c_void_p], W.BOOL),
            "GetWindowsDirectoryW": ([W.LPWSTR, W.UINT], W.UINT),
            "MoveFileExW": ([W.LPCWSTR, W.LPCWSTR, W.DWORD], W.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, result
        for name, args, result in (
            ("ConvertStringSecurityDescriptorToSecurityDescriptorW", [W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.c_void_p], W.BOOL),
            ("SetFileSecurityW", [W.LPCWSTR, W.DWORD, C.c_void_p], W.BOOL),
            ("ConvertSidToStringSidW", [C.c_void_p, C.POINTER(W.LPWSTR)], W.BOOL),
            ("OpenProcessToken", [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)], W.BOOL),
            ("GetTokenInformation", [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)], W.BOOL),
            ("FreeSid", [C.c_void_p], C.c_void_p),
        ):
            f = getattr(self.a, name); f.argtypes, f.restype = args, result
        self.k.GetCurrentProcess.restype = W.HANDLE
        for name, args in (
            ("CreateAppContainerProfile", [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, C.c_void_p, W.DWORD, C.POINTER(C.c_void_p)]),
            ("DeriveAppContainerSidFromAppContainerName", [W.LPCWSTR, C.POINTER(C.c_void_p)]),
            ("DeleteAppContainerProfile", [W.LPCWSTR]),
            ("GetAppContainerFolderPath", [W.LPCWSTR, C.POINTER(W.LPWSTR)]),
        ):
            f = getattr(self.u, name); f.argtypes, f.restype = args, C.c_long
        self.ole = C.WinDLL("ole32.dll", winmode=0x800)
        self.ole.CoTaskMemFree.argtypes = [C.c_void_p]

    def check(self, value, code):
        if not value:
            raise Unproven(code, C.get_last_error())
        return value

    def close(self, handle):
        if handle:
            self.check(self.k.CloseHandle(handle), "NATIVE_HANDLE_CLOSE_FAILED")

    def sid_text(self, sid):
        text = W.LPWSTR()
        self.check(self.a.ConvertSidToStringSidW(sid, C.byref(text)), "NATIVE_SID_STRING_FAILED")
        try:
            return text.value
        finally:
            self.k.LocalFree(C.cast(text, C.c_void_p))

    def owner(self):
        token = W.HANDLE(); size = W.DWORD()
        self.check(self.a.OpenProcessToken(self.k.GetCurrentProcess(), 8, C.byref(token)), "NATIVE_TOKEN_QUERY_FAILED")
        try:
            self.a.GetTokenInformation(token, 1, None, 0, C.byref(size))
            if not 0 < size.value <= 4096:
                raise Unproven("NATIVE_TOKEN_SIZE_INVALID")
            buffer = C.create_string_buffer(size.value)
            self.check(self.a.GetTokenInformation(token, 1, buffer, size, C.byref(size)), "NATIVE_TOKEN_QUERY_FAILED")
            return self.sid_text(C.cast(buffer, C.POINTER(C.c_void_p))[0])
        finally:
            self.close(token)

    def acl(self, path, owner, sid=None, rights="GRGX", inherit="", low=False):
        # Protected DACL: no inherited ALL APPLICATION PACKAGES/broad grants.
        sddl = f"D:P(A;OICI;FA;;;{owner})(A;OICI;FA;;;SY)"
        if sid:
            sddl += f"(A;{inherit};{rights};;;{sid})"
        if low:
            sddl += "S:(ML;OICI;NW;;;LW)"
        descriptor = C.c_void_p()
        self.check(self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, C.byref(descriptor), None), "NATIVE_ACL_BUILD_FAILED")
        try:
            self.check(self.a.SetFileSecurityW(str(path), 0x80000004 | (0x10 if low else 0),
                descriptor), "NATIVE_ACL_SET_FAILED")
        finally:
            self.k.LocalFree(descriptor)

    def lock(self, path, *, directory=False, reparse=False, writable=False):
        flags = 0x00200000 | (0x02000000 if directory else 0)  # OPEN_REPARSE_POINT/BACKUP_SEMANTICS
        handle = self.k.CreateFileW(str(path), 0x80000000 | (0x40000000 if writable else 0),
                                    3 if directory else 1, None, 3, flags, None)
        if handle == C.c_void_p(-1).value:
            raise Unproven("NATIVE_PATH_LOCK_FAILED", C.get_last_error())
        try:
            attributes = (W.DWORD * 2)()
            self.check(self.k.GetFileInformationByHandleEx(handle, 9, attributes, C.sizeof(attributes)), "NATIVE_PATH_INFO_FAILED")
            if bool(attributes[0] & 0x400) != reparse:
                raise Unproven("NATIVE_REPARSE_PATH_REJECTED")
            final = C.create_unicode_buffer(32768)
            length = self.k.GetFinalPathNameByHandleW(handle, final, len(final), 0)
            if not 0 < length < len(final) or final.value.removeprefix("\\\\?\\").casefold() != str(path).casefold():
                raise Unproven("NATIVE_PATH_IDENTITY_MISMATCH")
            return handle
        except BaseException:
            self.close(handle)
            raise

    def lock_chain(self, path):
        handles = []
        try:
            for part in reversed((path, *path.parents)):
                handles.append(self.lock(part, directory=True))
            return handles
        except BaseException:
            for handle in reversed(handles):
                self.close(handle)
            raise

    def junction(self, path, target):
        path.mkdir()
        substitute = ("\\??\\" + str(target)).encode("utf-16le")
        printable = str(target).encode("utf-16le")
        names = substitute + b"\0\0" + printable + b"\0\0"
        body = struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(printable)) + names
        data = struct.pack("<IHH", 0xA0000003, len(body), 0) + body
        buffer = C.create_string_buffer(data)
        handle = self.lock(path, directory=True, writable=True)
        try:
            written = W.DWORD()
            self.check(self.k.DeviceIoControl(handle, 0x000900A4, buffer, len(data), None, 0,
                                             C.byref(written), None), "NATIVE_JUNCTION_CREATE_FAILED")
        finally:
            self.close(handle)

    def access_error(self, path, access):
        handle = self.k.CreateFileW(str(path), access, 3, None, 3, 0, None)
        if handle == C.c_void_p(-1).value:
            return C.get_last_error()
        self.close(handle)
        return 0

    def launch(self, exe, args, cwd, environment, *, sid=None, fault="none"):
        self.deadline.remaining()
        job = self.k.CreateJobObjectW(None, None)
        self.check(job, "NATIVE_JOB_CREATE_FAILED")
        process, startup = _PROCESS(), _STARTUP_EX()
        attributes = None
        events = []
        try:
            limits = _EXTENDED_LIMIT(); limits.basic.flags = 0x2000
            self.check(self.k.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)), "NATIVE_JOB_LIMIT_FAILED")
            observed = _EXTENDED_LIMIT()
            self.check(self.k.QueryInformationJobObject(job, 9, C.byref(observed), C.sizeof(observed), None), "NATIVE_JOB_QUERY_FAILED")
            if observed.basic.flags != 0x2000:
                raise Unproven("NATIVE_JOB_BREAKAWAY_REJECTED")
            startup.startup.cb = C.sizeof(startup) if sid else C.sizeof(startup.startup)
            caps = _CAPABILITIES(sid, None, 0, 0)
            if sid:
                size = C.c_size_t()
                self.k.InitializeProcThreadAttributeList(None, 1, 0, C.byref(size))
                if not 0 < size.value <= 65536:
                    raise Unproven("NATIVE_ATTRIBUTE_SIZE_INVALID")
                attributes = C.create_string_buffer(size.value)
                self.check(self.k.InitializeProcThreadAttributeList(attributes, 1, 0, C.byref(size)), "NATIVE_ATTRIBUTE_INIT_FAILED")
                startup.attributes = C.cast(attributes, C.c_void_p)
                self.check(self.k.UpdateProcThreadAttribute(attributes, 0, 0x00020009,
                    C.byref(caps), C.sizeof(caps), None, None), "NATIVE_CAPABILITIES_FAILED")
            # Explicit application, generated argv, fresh environment, no handles/shell.
            command = C.create_unicode_buffer(subprocess.list2cmdline([str(exe), *map(str, args)]))
            block = C.create_unicode_buffer("\0".join(k + "=" + v for k, v in sorted(environment.items())) + "\0\0")
            flags = 0x4 | 0x400 | 0x08000000 | (0x80000 if sid else 0)
            self.check(self.k.CreateProcessW(str(exe), command, None, None, False, flags,
                block, str(cwd), C.byref(startup), C.byref(process)), "NATIVE_CREATE_PROCESS_FAILED")
            events.append("CREATE_SUSPENDED")
            # The negative test calls the real API with an invalid Job handle.
            self.check(self.k.AssignProcessToJobObject(None if fault == "assignment" else job,
                process.process), "NATIVE_ASSIGN_JOB_FAILED")
            events.append("ASSIGN_JOB")
            inside = W.BOOL()
            self.check(self.k.IsProcessInJob(process.process, job, C.byref(inside)), "NATIVE_CONFIRM_JOB_FAILED")
            if not inside.value:
                raise Unproven("NATIVE_JOB_MEMBERSHIP_FAILED")
            events.append("CONFIRM_JOB")
            if self.k.ResumeThread(process.thread) != 1:
                raise Unproven("NATIVE_RESUME_FAILED", C.get_last_error())
            events.append("RESUME")
            self.close(process.thread); process.thread = None
            return job, process.process, process.pid, events
        except BaseException as exc:
            cleanup_failed = None
            if process.process:
                # This includes assignment failure: never resume a jobless child.
                try:
                    self.check(self.k.TerminateProcess(process.process, 94), "NATIVE_SUSPENDED_KILL_FAILED")
                except Unproven as error:
                    cleanup_failed = error
            for handle in (job, process.thread):
                try:
                    self.close(handle)
                except Unproven as error:
                    cleanup_failed = error
            if process.process:
                try:
                    self.wait(process.process)
                except Unproven as error:
                    cleanup_failed = error
                finally:
                    try:
                        self.close(process.process)
                    except Unproven as error:
                        cleanup_failed = error
            if cleanup_failed:
                error = Unproven("NATIVE_LAUNCH_CLEANUP_UNPROVEN", cleanup_failed.native_error)
                error.events = events
                raise error from None
            if isinstance(exc, Unproven):
                exc.events = events
            raise
        finally:
            if attributes is not None and startup.attributes:
                self.k.DeleteProcThreadAttributeList(attributes)

    def wait(self, handle):
        milliseconds = max(1, int(self.deadline.remaining(cleanup=True, maximum=2) * 1000))
        if self.k.WaitForSingleObject(handle, milliseconds) != 0:
            raise Unproven("NATIVE_PROCESS_TERMINATION_UNPROVEN", C.get_last_error())

    def members(self, job):
        # The fixed fixture can have only two processes; extra members fail closed.
        data = C.create_string_buffer(8 + C.sizeof(C.c_size_t) * 32)
        self.check(self.k.QueryInformationJobObject(job, 3, data, len(data), None), "NATIVE_JOB_MEMBERS_QUERY_FAILED")
        assigned, present = struct.unpack_from("<II", data)
        if assigned != present or present > 32:
            raise Unproven("NATIVE_JOB_MEMBERS_OVERSIZED")
        return list((C.c_size_t * present).from_buffer(data, 8))

    def stop_job(self, job):
        self.check(self.k.TerminateJobObject(job, 98), "NATIVE_JOB_TERMINATION_FAILED")
        while self.members(job):
            self.deadline.remaining(cleanup=True, maximum=2)
            time.sleep(0.01)

    def container_token(self, process, expected_sid):
        token = W.HANDLE(); size = W.DWORD(); ac = W.DWORD()
        self.check(self.a.OpenProcessToken(process, 8, C.byref(token)), "NATIVE_DESCENDANT_TOKEN_FAILED")
        try:
            self.check(self.a.GetTokenInformation(token, 29, C.byref(ac), C.sizeof(ac), C.byref(size)),
                       "NATIVE_DESCENDANT_TOKEN_FAILED")
            buffer = C.create_string_buffer(4096)
            self.check(self.a.GetTokenInformation(token, 30, buffer, len(buffer), C.byref(size)),
                       "NATIVE_DESCENDANT_TOKEN_FAILED")
            count = C.cast(buffer, C.POINTER(W.DWORD))[0]
            self.check(self.a.GetTokenInformation(token, 31, buffer, len(buffer), C.byref(size)),
                       "NATIVE_DESCENDANT_TOKEN_FAILED")
            sid = C.cast(buffer, C.POINTER(C.c_void_p))[0]
            if ac.value != 1 or count != 0 or self.sid_text(sid) != expected_sid:
                raise Unproven("NATIVE_DESCENDANT_CAPABILITIES_UNPROVEN")
        finally:
            self.close(token)


def _compiler(root):
    """Use only the hosted image's fixed VS/SDK installation, no PATH lookup."""
    # GitHub windows-latest switched from VS 2022 to VS 2026 (18/Enterprise).
    # Source-owned, exact trusted-install allowlist; never search PATH or accept
    # an issue/env-supplied compiler. Unknown or ambiguous image layouts HOLD.
    allowed_vs = (
        Path(r"C:\Program Files\Microsoft Visual Studio\18\Enterprise\VC\Tools\MSVC"),
        Path(r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Tools\MSVC"),
    )
    kits = Path(r"C:\Program Files (x86)\Windows Kits\10")
    found = [sorted(vs.glob("*/bin/Hostx64/x64/cl.exe")) for vs in allowed_vs]
    active = [paths for paths in found if paths]
    if len(active) != 1:
        raise Unproven("HOSTED_NATIVE_COMPILER_UNAVAILABLE")
    tools = active[0]
    sdks = sorted((kits / "Include").glob("10.*"))
    if not tools or not sdks:
        raise Unproven("HOSTED_NATIVE_COMPILER_UNAVAILABLE")
    exe, sdk = tools[-1], sdks[-1]
    vc = exe.parents[3]
    include = [vc / "include", *[sdk / p for p in ("ucrt", "shared", "um")]]
    libraries = [vc / "lib/x64", *[kits / "Lib" / sdk.name / p / "x64" for p in ("ucrt", "um")]]
    if not all(p.is_dir() for p in (*include, *libraries)):
        raise Unproven("HOSTED_NATIVE_SDK_UNAVAILABLE")
    env = dict(_ENVIRONMENT, INCLUDE=";".join(map(str, include)), LIB=";".join(map(str, libraries)),
               TEMP=str(root), TMP=str(root))
    args = ["/nologo", "/MT", "/O1", "/W4", "/DUNICODE", "/D_UNICODE", str(root / "fixture.c"),
            "/Fe:" + str(root / "fixture.exe"), "/Fo:" + str(root / "fixture.obj"),
            "/link", "/INCREMENTAL:NO", "advapi32.lib", "ws2_32.lib"]
    return exe, args, env


def _compile(native, root):
    (root / "fixture.c").write_text(_FIXTURE_C, encoding="ascii")
    exe, args, env = _compiler(root)
    job = process = None
    try:
        job, process, _, _ = native.launch(exe, args, root, env)
        # No stdout/stderr inheritance: compiler paths/diagnostics stay private.
        milliseconds = int(native.deadline.remaining(maximum=15) * 1000)
        if native.k.WaitForSingleObject(process, milliseconds) != 0:
            raise Unproven("HOSTED_NATIVE_COMPILER_TIMEOUT")
        code = W.DWORD()
        native.check(native.k.GetExitCodeProcess(process, C.byref(code)), "HOSTED_NATIVE_COMPILER_EXIT_UNAVAILABLE")
        if code.value != 0 or not (root / "fixture.exe").is_file():
            raise Unproven("HOSTED_NATIVE_COMPILATION_FAILED", code.value)
        return dict(compiler_sha256=_sha(exe.read_bytes()),
                    compiler_environment_sha256=_sha(_json(env)),
                    fixture_sha256=_sha((root / "fixture.exe").read_bytes()))
    finally:
        try:
            if job:
                try:
                    native.stop_job(job)  # confirm no compiler descendants
                finally:
                    native.close(job)
        finally:
            if process:
                try:
                    native.wait(process)
                finally:
                    native.close(process)


def _artifact_size(root):
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            continue
        if path.is_file():
            total += path.stat().st_size
    if total > _PROFILE["artifact_bytes"]:
        raise Unproven("NATIVE_ARTIFACT_LIMIT_EXCEEDED")
    return total


def _decode_fixture_report(raw, pid):
    """Native codes are necessary, never sufficient without launch/provenance."""
    if len(raw) != 64:
        raise Unproven("NATIVE_FIXTURE_OUTPUT_INVALID")
    values = struct.unpack("<16I", raw)
    # Keep all original fail-closed requirements, but identify which *fixed*
    # native predicate failed without disclosing paths, SIDs or raw report data.
    checks = (
        (values[0] == 0x4d543031, "NATIVE_FIXTURE_MAGIC_UNPROVEN"),
        (values[1] == 1, "NATIVE_APPCONTAINER_TOKEN_UNPROVEN"),
        (values[2] == 0, "NATIVE_TOKEN_CAPABILITIES_UNPROVEN"),
        (values[3] == 0, "NATIVE_TOKEN_PRIVILEGES_UNPROVEN"),
        (values[11] == pid, "NATIVE_CHILD_PID_UNPROVEN"),
        (values[12] == 1, "NATIVE_CHILD_ENVIRONMENT_UNPROVEN"),
        (values[13] == 1, "NATIVE_CHILD_SID_UNPROVEN"),
    )
    for allowed, reason in checks:
        if not allowed:
            raise Unproven(reason, ((values[15] & 0xffff) << 3) | (values[14] & 7) if reason == "NATIVE_CHILD_ENVIRONMENT_UNPROVEN" else None)
    if values[4:10] != (5, 5, 5, 0, 2, 10013):
        # Six fixed native result slots, no paths or raw environment values.
        # Lower six bits identify divergent slots; following four-bit nibbles
        # classify only known Win32/WSA result codes (15 means unrecognized).
        codebook = {0: 1, 2: 2, 3: 3, 5: 4, 32: 5, 65: 6, 10013: 7, 10061: 8}
        expected = (5, 5, 5, 0, 2, 10013)
        mismatches = 0
        classes = 0
        for index, (actual, required) in enumerate(zip(values[4:10], expected)):
            if actual != required:
                mismatches |= 1 << index
                classes |= codebook.get(actual, 15) << (4 * index)
        detail = mismatches | (classes << 6)
        if mismatches & (1 << 5):
            # A WSA error code is a bounded public integer, not a network
            # address or payload. Expose 0..65534 exactly; clamp anything
            # else to the reserved 65535 category, so one run distinguishes
            # OS error families without logging any socket/path secrets.
            detail |= (min(values[9], 65535) + 1) << 30
        raise Unproven("NATIVE_ACCESS_DENIAL_UNPROVEN", detail)
    return values



def _verified_host_localappdata(profile_folder, reported_localappdata):
    """Bind the necessary Win32 profile variable to an OS-reported folder.

    Windows GetAppContainerFolderPath yields
    <LOCALAPPDATA>/Packages/<AppContainer>/AC, not the package root itself.
    Require this exact OS-documented structure, then match the host's value.
    Never include the value in the sanitized receipt.
    """
    folder = Path(profile_folder)
    if (folder.name.casefold() != "ac"
            or folder.parent.parent.name.casefold() != "packages"
            or not isinstance(reported_localappdata, str)
            or not reported_localappdata
            or os.path.normcase(os.path.abspath(reported_localappdata))
               != os.path.normcase(str(folder.parents[2]))):
        raise Unproven("NATIVE_HOST_LOCALAPPDATA_UNPROVEN")
    return str(folder.parents[2])


def run_inert_probe(*, enabled=False, repository=None, candidate_sha=None, source_sha256=None,
                    profile_sha256=None, environment_sha256=None, fault="none"):
    """Launch ONLY the embedded inert fixture and always return a sanitized receipt.

    No argv, executable, token/capability, source selection or environment
    override is accepted. Normal and deliberate-timeout cases both close the
    last Job handle and wait on real child/grandchild handles. Assignment fault
    kills the still-suspended child. Cleanup failure can never report OBSERVED.
    """
    deadline = _Deadline()
    result = dict(kind="MT01_WINDOWS_INERT_NATIVE_PROBE", status="UNPROVEN",
                  native_isolation="UNPROVEN", trusted_windows_proof=False, acceptance_pass=False,
                  candidate_imports_authorized=False, fault=fault if fault in _PROFILE["faults"] else None,
                  profile_sha256=PROFILE_SHA256, source_sha256=None,
                  environment_sha256=ENVIRONMENT_SHA256, identity=None, events=[], denials={},
                  positive_controls={},
                  cleanup_complete=False, orphan_count=None, timed_out=False, reason_codes=[],
                  native_error=None, elapsed_seconds=0, output_bytes=0, artifact_bytes=0,
                  profile_deleted=False, child_terminated=False, grandchild_terminated=False)
    result["reparse_guard"] = None
    native = root = listener = None
    handles, source_handles = [], []
    sid = C.c_void_p(); derived = C.c_void_p()
    profile_name = profile_folder = None
    job = child = grandchild = None
    observed = False
    try:
        if enabled is not True or os.environ.get(OPT_IN_VARIABLE) != PROFILE_SHA256:
            raise Unproven("HOSTED_NATIVE_SOURCE_OPT_IN_REQUIRED")
        _validate_binding(candidate_sha, source_sha256, profile_sha256, environment_sha256, fault)
        if _sha(_json(_PROFILE)) != profile_sha256 or _sha(_json(_APP_CONTAINER_ENV_TEMPLATE)) != environment_sha256:
            raise Unproven("NATIVE_PROFILE_OR_ENVIRONMENT_DRIFT")
        if os.name != "nt" or C.sizeof(C.c_void_p) != 8:
            raise Unproven("HOSTED_WINDOWS_X64_REQUIRED")
        if source_digest(repository) != source_sha256:
            raise Unproven("NATIVE_SOURCE_DIGEST_MISMATCH")
        result["identity"] = _hosted_identity(repository, candidate_sha, deadline)
        # No native/profile/filesystem mutation has occurred before this point.
        native = _Win32(deadline)
        system = C.create_unicode_buffer(32768)
        if native.k.GetWindowsDirectoryW(system, len(system)) != len(_ENVIRONMENT["SystemRoot"]) or system.value != _ENVIRONMENT["SystemRoot"]:
            raise Unproven("NATIVE_SYSTEM_ENVIRONMENT_MISMATCH")
        repository = Path(os.path.abspath(repository))
        handles.extend(native.lock_chain(repository))
        for relative in SOURCE_PATHS:
            path = repository / relative
            handles.extend(native.lock_chain(path.parent))
            source_handles.append(native.lock(path))
        if source_digest(repository) != source_sha256:
            raise Unproven("NATIVE_SOURCE_DIGEST_MISMATCH")
        result["source_sha256"] = source_sha256
        scratch_parent = Path(os.path.abspath(os.environ.get("RUNNER_TEMP", "")))
        if not os.environ.get("RUNNER_TEMP") or not scratch_parent.is_dir():
            raise Unproven("HOSTED_DISPOSABLE_SCRATCH_REQUIRED")
        handles.extend(native.lock_chain(scratch_parent))
        root = scratch_parent / ("mt01-inert-" + uuid.uuid4().hex)
        root.mkdir()
        owner = native.owner()
        native.acl(root, owner)
        deadline.remaining()
        result.update(_compile(native, root))
        result["artifact_bytes"] = _artifact_size(root)
        profile_name = "mt01.inert." + uuid.uuid4().hex
        deadline.remaining()
        hr = native.u.CreateAppContainerProfile(profile_name, profile_name, "MT01 disposable inert proof", None, 0, C.byref(sid))
        result["create_profile_hresult"] = hr & 0xffffffff
        if hr != 0:
            # Never derive/reuse an existing profile as a privilege fallback.
            profile_name = None
            raise Unproven("NATIVE_APPCONTAINER_CREATE_FAILED", hr & 0xffffffff)
        hr = native.u.DeriveAppContainerSidFromAppContainerName(profile_name, C.byref(derived))
        result["derive_profile_hresult"] = hr & 0xffffffff
        if hr != 0 or native.sid_text(sid) != native.sid_text(derived):
            raise Unproven("NATIVE_APPCONTAINER_SID_MISMATCH", hr & 0xffffffff)
        sid_string = native.sid_text(sid)
        folder = W.LPWSTR()
        hr = native.u.GetAppContainerFolderPath(sid_string, C.byref(folder))
        if hr != 0:
            raise Unproven("NATIVE_PROFILE_FOLDER_UNAVAILABLE", hr & 0xffffffff)
        try:
            profile_folder = Path(folder.value)
        finally:
            native.ole.CoTaskMemFree(C.cast(folder, C.c_void_p))
        host_localappdata = _verified_host_localappdata(
            profile_folder, os.environ.get("LOCALAPPDATA"))
        native.acl(root, owner, sid_string)
        exe = root / "fixture.exe"
        native.acl(exe, owner, sid_string)
        snapshot, private, scratch = (root / p for p in ("source", "private", "scratch"))
        for path in (snapshot, private, scratch):
            path.mkdir()
        native.acl(snapshot, owner, sid_string, inherit="OICI")
        native.acl(private, owner)
        native.acl(scratch, owner, sid_string, rights="FA", inherit="OICI", low=True)
        # Pass only four source-owned inputs. Windows will redirect TEMP/TMP
        # and LOCALAPPDATA into the OS-created disposable AppContainer profile.
        # The native child verifies exact OS profile paths before acceptance.
        runtime_environment = dict(_ENVIRONMENT, LOCALAPPDATA=host_localappdata,
                                   TEMP=str(scratch), TMP=str(scratch))
        result["runtime_environment_sha256"] = _sha(_json(runtime_environment))
        source = snapshot / "fixture.c"; sentinel = private / "sentinel.txt"
        source.write_bytes(_FIXTURE_C.encode("ascii")); sentinel.write_bytes(b"INERT_SENTINEL")
        native.acl(source, owner, sid_string, rights="GR")
        native.acl(sentinel, owner)
        source_hash = _sha(source.read_bytes())
        native.junction(snapshot / "escape", private)
        escape = snapshot / "escape" / "sentinel.txt"
        if sentinel.read_bytes() != b"INERT_SENTINEL" or escape.read_bytes() != b"INERT_SENTINEL":
            raise Unproven("NATIVE_FILE_POSITIVE_CONTROL_FAILED")
        controls = dict(outside_source_read=native.access_error(sentinel, 0x80000000),
                        readonly_source_write=native.access_error(source, 0x40000000),
                        junction_escape_read=native.access_error(escape, 0x80000000))
        if any(controls.values()):
            raise Unproven("NATIVE_FILE_POSITIVE_CONTROL_FAILED", next(v for v in controls.values() if v))
        result["positive_controls"].update(controls)
        # Prove the source WRITE is possible for the unsandboxed control.
        with source.open("r+b") as control:
            control.write(_FIXTURE_C.encode("ascii"))
        handles.extend(native.lock_chain(snapshot))
        handles.extend(native.lock_chain(private))
        handles.extend(native.lock_chain(scratch))
        handles.append(native.lock(source))
        handles.append(native.lock(sentinel))
        handles.append(native.lock(snapshot / "escape", directory=True, reparse=True))
        handles.append(native.lock(exe))
        try:
            unexpected = native.lock_chain(snapshot / "escape")
        except Unproven as exc:
            if exc.code != "NATIVE_REPARSE_PATH_REJECTED":
                raise
        else:
            for handle in reversed(unexpected):
                native.close(handle)
            raise Unproven("NATIVE_REPARSE_GUARD_FAILED")
        if native.k.MoveFileExW(str(snapshot / "escape"), str(snapshot / "replaced"), 0):
            raise Unproven("NATIVE_REPARSE_REPLACEMENT_NOT_BLOCKED")
        replacement_error = C.get_last_error()
        if replacement_error != 32:  # sharing violation from the retained handle
            raise Unproven("NATIVE_REPARSE_REPLACEMENT_UNPROVEN", replacement_error)
        result["reparse_guard"] = dict(handle_rejected=True, replacement_win32_error=replacement_error)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0)); listener.listen(4); listener.settimeout(deadline.remaining())
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=deadline.remaining()):
            connection, _ = listener.accept(); connection.close()
        result["positive_controls"]["loopback_connect"] = 0
        report = scratch / "report.bin"
        args = ["probe", sentinel, source, escape, snapshot / "missing.txt", str(port),
                report, sid_string, profile_folder]
        deadline.remaining()
        try:
            job, child, pid, events = native.launch(exe, args, scratch, runtime_environment, sid=sid, fault=fault)
            result["events"] = events
        except Unproven as exc:
            result["events"] = getattr(exc, "events", [])
            if fault == "assignment" and exc.code == "NATIVE_ASSIGN_JOB_FAILED":
                result["child_terminated"] = True
            raise
        while not report.exists() or report.stat().st_size != 64:
            deadline.remaining()
            if native.k.WaitForSingleObject(child, 0) != 0x102:
                code = W.DWORD(); native.k.GetExitCodeProcess(child, C.byref(code))
                raise Unproven("NATIVE_FIXTURE_EXITED_EARLY", code.value)
            time.sleep(0.01)
        with report.open("rb") as stream:
            raw = stream.read(_PROFILE["output_bytes"] + 1)
        result["output_bytes"] = len(raw)
        values = _decode_fixture_report(raw, pid)
        grandchild = native.k.OpenProcess(0x100000 | 0x1000, False, values[10])
        native.check(grandchild, "NATIVE_DESCENDANT_HANDLE_FAILED")
        inside = W.BOOL()
        native.check(native.k.IsProcessInJob(grandchild, job, C.byref(inside)), "NATIVE_DESCENDANT_JOB_QUERY_FAILED")
        if not inside.value or set(native.members(job)) != {pid, values[10]}:
            raise Unproven("NATIVE_DESCENDANT_JOB_UNPROVEN")
        native.container_token(grandchild, sid_string)
        if native.k.WaitForSingleObject(grandchild, 0) != 0x102:
            raise Unproven("NATIVE_DESCENDANT_NOT_LIVE")
        result["events"].append("CHILD_AND_DESCENDANT_READY")
        result["denials"] = dict(outside_source_read=values[4], readonly_source_write=values[5],
                                 junction_escape_read=values[6], source_read_control=values[7],
                                 missing_file_control=values[8], loopback_connect=values[9])
        if (_sha(source.read_bytes()) != source_hash or source_digest(repository) != source_sha256
                or _sha(exe.read_bytes()) != result["fixture_sha256"]):
            raise Unproven("NATIVE_SOURCE_MUTATED")
        if _sha(_json(_APP_CONTAINER_ENV_TEMPLATE)) != environment_sha256 or _sha(_json(_PROFILE)) != profile_sha256:
            raise Unproven("NATIVE_PROFILE_OR_ENVIRONMENT_DRIFT")
        result["artifact_bytes"] = _artifact_size(root)
        if fault == "timeout":
            # An actual expired wait, while both native processes remain live.
            if native.k.WaitForSingleObject(child, 20) != 0x102:
                raise Unproven("NATIVE_TIMEOUT_CONTROL_FAILED")
            result["timed_out"] = True
        observed = True
    except Unproven as exc:
        result["reason_codes"].append(exc.code)
        result["native_error"] = exc.native_error
    except OSError as exc:
        result["reason_codes"].append("NATIVE_OS_OPERATION_FAILED")
        result["native_error"] = getattr(exc, "winerror", None) or exc.errno
    except (ValueError, TypeError, KeyError, AttributeError, C.ArgumentError):
        result["reason_codes"].append("NATIVE_CAPABILITY_OR_INPUT_UNAVAILABLE")
    finally:
        if native:
            cleanup_errors = []
            # First close the last Job handle. No breakaway/inherited Job handle
            # exists. Wait on retained process handles, not reused process IDs.
            try:
                if job:
                    try:
                        if not observed:
                            native.stop_job(job)
                            result["events"].append("FAILURE_JOB_EMPTY")
                    finally:
                        native.close(job); job = None
                        result["events"].append("CLOSE_LAST_JOB_HANDLE")
                if child:
                    native.wait(child); result["child_terminated"] = True
                    result["events"].append("WAIT_CHILD_SIGNALED")
                if grandchild:
                    native.wait(grandchild); result["grandchild_terminated"] = True
                    result["events"].append("WAIT_DESCENDANT_SIGNALED")
            except Unproven as exc:
                cleanup_errors.append(exc.code)
            # Each resource has its own teardown so one failure cannot prevent
            # the remaining handles/profile/scratch from being cleaned up.
            for handle in (grandchild, child, *reversed(source_handles), *reversed(handles)):
                try:
                    native.close(handle)
                except Unproven as exc:
                    cleanup_errors.append(exc.code)
            if profile_name:
                try:
                    hr = native.u.DeleteAppContainerProfile(profile_name)
                    result["delete_profile_hresult"] = hr & 0xffffffff
                    result["profile_deleted"] = hr == 0 and profile_folder is not None and not profile_folder.exists()
                    if not result["profile_deleted"]:
                        cleanup_errors.append("NATIVE_PROFILE_DELETE_UNPROVEN")
                except OSError:
                    cleanup_errors.append("NATIVE_PROFILE_DELETE_UNPROVEN")
            for value in (derived, sid):
                if value.value:
                    if native.a.FreeSid(value):
                        cleanup_errors.append("NATIVE_SID_RELEASE_FAILED")
            if root:
                try:
                    # Remove the generated junction itself before recursive
                    # scratch deletion. Never follow its target during cleanup.
                    junction = root / "source" / "escape"
                    if junction.exists():
                        junction.rmdir()
                    shutil.rmtree(root)
                    if root.exists():
                        raise OSError
                except OSError:
                    cleanup_errors.append("NATIVE_SCRATCH_DELETE_UNPROVEN")
            if listener:
                listener.close()
            result["reason_codes"].extend(cleanup_errors)
            result["cleanup_complete"] = not cleanup_errors
            if result["cleanup_complete"] and result["child_terminated"] and (
                    result["grandchild_terminated"] or fault == "assignment"):
                result["orphan_count"] = 0
                result["events"].append("CLEANUP_COMPLETE")
        result["elapsed_seconds"] = round(time.monotonic() - deadline.start, 6)
        if result["elapsed_seconds"] > 55:
            result["reason_codes"].append("NATIVE_LIFECYCLE_DEADLINE")
            result["cleanup_complete"] = False
    if observed and result["cleanup_complete"] and result["orphan_count"] == 0 and not result["reason_codes"]:
        result.update(status="OBSERVED", native_isolation="INERT_FIXTURE_OBSERVED")
    elif (fault == "assignment" and result["cleanup_complete"] and result["orphan_count"] == 0
          and result["reason_codes"] == ["NATIVE_ASSIGN_JOB_FAILED"]):
        result["status"] = "NEGATIVE_OBSERVED"
    return result
