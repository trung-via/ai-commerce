"""HostOps v2: strict issue contract, bounded host reads, scoped writes and SSH-signed Human approval."""
from __future__ import annotations
import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

REPO = "trung-via/ai-commerce"
SCHEMA = "ai-commerce-hostops-v2"
RESULT_SCHEMA = "ai-commerce-hostops-result-v2"
READ = frozenset({"host_inventory_read", "d5_deep_read", "d5_pid_map_read"})
SAFE_WRITE = frozenset({"scratch_marker_write"})
SENSITIVE = frozenset({"workspace_file_write"})
MAX_BYTES = 8192
MAX_STDOUT = 8192
SHA = re.compile(r"[0-9a-f]{40}\Z")
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,63}\Z")
NOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,99}\Z")
SAFE_PATH = re.compile(r"[a-zA-Z0-9_./-]{1,120}\Z")
DENIED_SEGMENTS = {".git", ".github", ".venv", ".ssh", "secrets", ".env", "node_modules", "__pycache__"}

class Denied(Exception):
    pass

def pairs_to_dict(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise Denied("DUPLICATE_KEY")
        out[key] = value
    return out

def clean_path(path):
    if type(path) is not str or not SAFE_PATH.fullmatch(path):
        raise Denied("BAD_RELATIVE_PATH")
    parts = path.split("/")
    if path.startswith("/") or "\\" in path or any(p in ("", ".", "..") or p.lower() in DENIED_SEGMENTS for p in parts):
        raise Denied("BAD_RELATIVE_PATH")
    if not path.endswith((".txt", ".json", ".md")):
        raise Denied("UNSUPPORTED_FILE_EXTENSION")
    return parts

def canonical_for_signature(req):
    unsigned = {k: v for k, v in req.items() if k != "approval_signature_b64"}
    return (json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")

def check_request(raw: bytes, *, repository, owner, issuer, source_sha, private):
    if repository != REPO or not private:
        raise Denied("PRIVATE_REPO_REQUIRED")
    if owner != "trung-via" or issuer != owner:
        raise Denied("OWNER_REQUIRED")
    if type(source_sha) is not str or not SHA.fullmatch(source_sha):
        raise Denied("BAD_SHA")
    if not 0 < len(raw) <= MAX_BYTES:
        raise Denied("BAD_INPUT_LENGTH")
    try:
        r = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs_to_dict)
    except (ValueError, UnicodeError) as e:
        raise Denied("BAD_JSON") from e
    if type(r) is not dict:
        raise Denied("BAD_SHAPE")
    required = {"schema", "request_id", "repository", "source_sha", "operation", "parameters"}
    operation = r.get("operation")
    sensitive = type(operation) is str and operation in SENSITIVE
    if set(r) != (required | ({"approval_signature_b64"} if sensitive else set())):
        raise Denied("BAD_SHAPE")
    if r["schema"] != SCHEMA or r["repository"] != REPO or r["source_sha"] != source_sha:
        raise Denied("IDENTITY_MISMATCH")
    if type(r["request_id"]) is not str or not IDENT.fullmatch(r["request_id"]):
        raise Denied("BAD_REQUEST_ID")
    if type(operation) is not str or operation not in READ | SAFE_WRITE | SENSITIVE:
        raise Denied("OPERATION_NOT_ALLOWED")
    params = r["parameters"]
    if type(params) is not dict:
        raise Denied("BAD_PARAMETERS")
    if operation in READ and params != {}:
        raise Denied("READ_PARAMETERS_NOT_ALLOWED")
    if operation in SAFE_WRITE:
        if set(params) != {"marker"} or type(params["marker"]) is not str or not NOTE.fullmatch(params["marker"]):
            raise Denied("BAD_MARKER")
    if sensitive:
        if set(params) != {"relative_path", "content"}:
            raise Denied("BAD_WRITE_SHAPE")
        clean_path(params["relative_path"])
        if type(params["content"]) is not str or not 0 < len(params["content"].encode("utf-8")) <= 2048:
            raise Denied("BAD_WRITE_CONTENT")
        signature = r["approval_signature_b64"]
        if type(signature) is not str or not 20 <= len(signature) <= 16384:
            raise Denied("HUMAN_SIGNATURE_REQUIRED")
        try:
            decoded = base64.b64decode(signature, validate=True)
        except (ValueError, binascii.Error) as e:
            raise Denied("BAD_SIGNATURE_ENCODING") from e
        if not decoded.startswith(b"-----BEGIN SSH SIGNATURE-----\n") or not decoded.rstrip().endswith(b"-----END SSH SIGNATURE-----"):
            raise Denied("BAD_SIGNATURE_FORMAT")
    return r

def verify_human(r, *, signers_file: Path):
    if r["operation"] not in SENSITIVE:
        return
    if not signers_file.is_file():
        raise Denied("HUMAN_KEY_NOT_ENROLLED")
    lines = [x.strip() for x in signers_file.read_text(encoding="utf-8").splitlines() if x.strip() and not x.lstrip().startswith("#")]
    if len(lines) != 1 or not lines[0].startswith("ai-commerce-human ssh-ed25519 "):
        raise Denied("HUMAN_KEY_NOT_ENROLLED")
    signature = base64.b64decode(r["approval_signature_b64"], validate=True)
    with tempfile.TemporaryDirectory(prefix="hostops-signature-") as folder:
        sigfile = Path(folder) / "approval.sig"
        with sigfile.open("xb") as fh:
            fh.write(signature)
        try:
            p = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(signers_file),
                                "-I", "ai-commerce-human", "-n", "hostops-v2", "-s", str(sigfile)],
                               input=canonical_for_signature(r), capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Denied("SIGNATURE_VERIFIER_UNAVAILABLE") from e
    if p.returncode != 0:
        raise Denied("HUMAN_SIGNATURE_INVALID")

def guarded_root(raw_root: str | None):
    if not raw_root:
        raise Denied("APPROVED_ROOT_NOT_CONFIGURED")
    p = Path(raw_root)
    if not p.is_absolute() or not p.is_dir() or p.is_symlink():
        raise Denied("APPROVED_ROOT_INVALID")
    return p.resolve(strict=True)

def write_no_clobber(root: Path, subparts: list[str], data: bytes):
    if not root.is_dir() or root.is_symlink():
        raise Denied("ROOT_NOT_SAFE")
    current = root
    for part in subparts[:-1]:
        current = current / part
        if current.is_symlink():
            raise Denied("SYMLINK_DENIED")
        if not current.exists():
            current.mkdir()
        if not current.is_dir() or current.is_symlink():
            raise Denied("NON_DIRECTORY_PATH")
    dest = current / subparts[-1]
    if dest.is_symlink():
        raise Denied("SYMLINK_DENIED")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(dest, flags, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except FileExistsError as e:
        raise Denied("ALREADY_EXISTS_NO_OVERWRITE") from e
    except OSError as e:
        raise Denied("WRITE_FAILED") from e
    return hashlib.sha256(data).hexdigest()

def validate_pid_map(d):
    """Strict public shape: PID, flags and ephemeral group only, never paths or command lines."""
    fields = {"operation", "session_id", "pwsh_major", "process_known", "tcp_known",
              "chrome_total", "chrome_truncated", "listener_truncated",
              "profile_identity_verified", "processes", "listeners"}
    if type(d) is not dict or set(d) != fields or d["operation"] != "d5_pid_map_read":
        raise Denied("DIAGNOSTIC_SHAPE_INVALID")
    for key in ("session_id", "pwsh_major", "chrome_total"):
        if type(d[key]) is not int or not 0 <= d[key] <= 10000:
            raise Denied("DIAGNOSTIC_VALUE_INVALID")
    for key in ("process_known", "tcp_known", "chrome_truncated", "listener_truncated"):
        if type(d[key]) is not bool:
            raise Denied("DIAGNOSTIC_VALUE_INVALID")
    if d["profile_identity_verified"] is not False:
        raise Denied("PROFILE_IDENTITY_NOT_AUTHENTICATED")
    procs, listeners = d["processes"], d["listeners"]
    if type(procs) is not list or len(procs) > 32 or type(listeners) is not list or len(listeners) > 16:
        raise Denied("DIAGNOSTIC_ARRAY_INVALID")
    if (not d["process_known"] and (procs or d["chrome_total"])) or (
        d["process_known"] and d["chrome_total"] < len(procs)
    ) or (not d["tcp_known"] and listeners):
        raise Denied("DIAGNOSTIC_INCONSISTENT")
    if d["process_known"] and d["chrome_truncated"] != (d["chrome_total"] > 32):
        raise Denied("DIAGNOSTIC_INCONSISTENT")
    seen = set()
    for p in procs:
        if type(p) is not dict or set(p) != {
            "pid", "session_id", "commandline_known", "debug_9333", "debug_9222",
            "profile_group", "profile_root_exists"
        }:
            raise Denied("DIAGNOSTIC_PROCESS_SHAPE_INVALID")
        if (type(p["pid"]) is not int or not 0 < p["pid"] <= 2**31 - 1 or
            type(p["session_id"]) is not int or not 0 <= p["session_id"] <= 10000 or
            type(p["commandline_known"]) is not bool or p["pid"] in seen):
            raise Denied("DIAGNOSTIC_PROCESS_VALUE_INVALID")
        seen.add(p["pid"])
        if any(p[k] is not None and type(p[k]) is not bool for k in (
            "debug_9333", "debug_9222", "profile_root_exists"
        )):
            raise Denied("DIAGNOSTIC_PROCESS_VALUE_INVALID")
        group = p["profile_group"]
        if group is not None and (
            type(group) is not str or re.fullmatch(r"p[0-9]{2}", group) is None
        ):
            raise Denied("DIAGNOSTIC_PROCESS_VALUE_INVALID")
        if (not p["commandline_known"] and (
            p["debug_9333"] is not None or p["debug_9222"] is not None or
            group is not None or p["profile_root_exists"] is not None
        )) or (group is None and p["profile_root_exists"] is not None):
            raise Denied("DIAGNOSTIC_INCONSISTENT")
    for item in listeners:
        if type(item) is not dict or set(item) != {"port", "owner_pid", "binding", "chrome_owner"}:
            raise Denied("DIAGNOSTIC_LISTENER_SHAPE_INVALID")
        if (type(item["port"]) is not int or item["port"] not in (9222, 9333) or
            type(item["owner_pid"]) is not int or not 0 <= item["owner_pid"] <= 2**31 - 1 or
            item["binding"] not in ("ipv4_loopback", "ipv6_loopback", "other") or
            type(item["chrome_owner"]) is not bool):
            raise Denied("DIAGNOSTIC_LISTENER_VALUE_INVALID")
    return d


def run(r, digest, *, workdir: Path, approved_root: str | None):
    op = r["operation"]
    if op in READ:
        script = Path(__file__).resolve().with_name(
            "HostOps-D5-PidMap.ps1" if op == "d5_pid_map_read" else "HostOps-V2-Diagnostics.ps1"
        )
        if not script.is_file():
            raise Denied("SCRIPT_NOT_FOUND")
        try:
            cmd = ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)]
            if op != "d5_pid_map_read":
                cmd.extend(["-Operation", op])
            p = subprocess.run(cmd,
                               capture_output=True, timeout=55, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Denied("DIAGNOSTIC_UNAVAILABLE_OR_TIMEOUT") from e
        if p.returncode != 0 or len(p.stdout) > MAX_STDOUT:
            raise Denied("DIAGNOSTIC_FAILED")
        try:
            d = json.loads(p.stdout.decode("utf-8"), object_pairs_hook=pairs_to_dict)
        except (UnicodeError, ValueError) as e:
            raise Denied("DIAGNOSTIC_OUTPUT_INVALID") from e
        if op == "d5_pid_map_read":
            validate_pid_map(d)
        else:
            required = {"operation", "session_id", "pwsh_major", "chrome_count", "runner_count", "cdp_9333_count", "cdp_9222_count", "tcp_known", "process_flags_known", "d5_debug_flag_count", "d5_profile_flag_count", "task_state"}
            if type(d) is not dict or set(d) != required or d["operation"] != op:
                raise Denied("DIAGNOSTIC_SHAPE_INVALID")
            for k in ("session_id", "pwsh_major", "chrome_count", "runner_count", "cdp_9333_count", "cdp_9222_count", "d5_debug_flag_count", "d5_profile_flag_count"):
                if type(d[k]) is not int or not 0 <= d[k] <= 10000:
                    raise Denied("DIAGNOSTIC_VALUE_INVALID")
            if type(d["tcp_known"]) is not bool or type(d["process_flags_known"]) is not bool or d["task_state"] not in ("Running", "Ready", "Disabled", "Other", "Unknown"):
                raise Denied("DIAGNOSTIC_VALUE_INVALID")
        result = {"status": "OBSERVED", "diagnostics": d}
    elif op in SAFE_WRITE:
        root = workdir / ".local" / "hostops-v2-scratch"
        root.mkdir(parents=True, exist_ok=True)
        content = (r["request_id"] + ":" + r["parameters"]["marker"] + "\n").encode()
        output_sha = write_no_clobber(root, [r["request_id"] + ".txt"], content)
        result = {"status": "WRITTEN", "sha256": output_sha, "scope": "job_scratch_only"}
    else:
        root = guarded_root(approved_root)
        relative = clean_path(r["parameters"]["relative_path"])
        output_sha = write_no_clobber(root, relative, r["parameters"]["content"].encode("utf-8"))
        result = {"status": "WRITTEN", "sha256": output_sha, "scope": "human_approved_workspace"}
    return {"schema": RESULT_SCHEMA, "request_id": r["request_id"], "operation": op,
            "source_sha": r["source_sha"], "request_sha256": digest, **result}

def main(argv=None):
    a = argparse.ArgumentParser()
    a.add_argument("mode", choices=("validate", "execute"))
    for arg in ("request-file", "repository", "owner", "issuer", "source-sha", "is-private", "output"):
        a.add_argument("--" + arg, required=True)
    a.add_argument("--approved-root", default="")
    args = a.parse_args(argv)
    dest = Path(args.output)
    try:
        raw = Path(args.request_file).read_bytes()
        r = check_request(raw, repository=args.repository, owner=args.owner, issuer=args.issuer,
                          source_sha=args.source_sha, private=args.is_private == "true")
        verify_human(r, signers_file=Path(__file__).resolve().parents[1] / "config" / "hostops" / "human-allowed-signers")
        digest = hashlib.sha256(raw).hexdigest()
        result = {"schema": RESULT_SCHEMA, "status": "VALIDATED", "operation": r["operation"], "request_id": r["request_id"], "request_sha256": digest, "source_sha": r["source_sha"]} if args.mode == "validate" else run(r, digest, workdir=Path.cwd(), approved_root=args.approved_root)
        status = 0
    except (Denied, OSError) as e:
        result = {"schema": RESULT_SCHEMA, "status": "BLOCKED", "error_code": str(e) if isinstance(e, Denied) else "REQUEST_UNREADABLE"}
        status = 2
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    print("HOSTOPS_V2_" + result["status"])
    return status

if __name__ == "__main__":
    sys.exit(main())
