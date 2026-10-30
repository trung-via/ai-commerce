"""EF02-D5: operator-invoked Windows entrypoint for one exact-chat CDP doorbell.

No GitHub event watcher, schedule, auto-send, profile launch or secret issuance.
Read-only preflight is the default; real Send needs an explicit command AND an
operator-owned environment gate plus independently signed current-state grants.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sys
from pathlib import Path
from ctypes import wintypes

from .completion_event import CompletionEventError, validate_completion_event
from .conversation_binding import _decode_unique
from ._private_state import PrivateJsonState, PrivateStateError
from .cdp_doorbell import LocalChromeDoorbellAdapter
from .github_artifact_authority import GitHubRestArtifactAuthority
from .signed_origin_consent import SignedOriginConsentAuthority
from .trusted_origin import LocalCallerAttestation
from .wake_adapter import GitHubArtifactProvenance, deliver_exact_chat_wake

_REPOSITORY = "trung-via/ai-commerce"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_MAX_REPORT = 512 * 1024
_MAX_GRANTS = 128 * 1024


class LocalWakeConfigError(ValueError):
    """Fixed diagnostic codes only; never leak credentials or route URLs."""


def _read_json_file(path: Path, *, limit: int) -> dict:
    """Read bounded untrusted JSON; duplicate-key rejection is mandatory."""
    try:
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or path.stat().st_size > limit):
            raise LocalWakeConfigError("INPUT_FILE_INVALID")
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise LocalWakeConfigError("INPUT_FILE_INVALID")
        value = _decode_unique(raw.decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise LocalWakeConfigError("INPUT_SHAPE_INVALID")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise LocalWakeConfigError("INPUT_FILE_INVALID") from None


def load_evidence(event_file: Path, result_file: Path) -> tuple[dict, dict]:
    event = _read_json_file(event_file, limit=_MAX_REPORT)
    report = _read_json_file(result_file, limit=_MAX_REPORT)
    try:
        event = validate_completion_event(event)
    except (CompletionEventError, TypeError, ValueError, KeyError):
        raise LocalWakeConfigError("EVENT_INVALID") from None
    if (report.get("kind") != "AI_COMMERCE_REMOTE_BATCH_RESULT" or
            report.get("version") != 1 or
            report.get("batch_id") != event["batch_id"] or
            report.get("source_sha") != event["source_sha"] or
            report.get("wake_event_id") != event["event_id"] or
            report.get("wake_event_status") != "RECORDED_UNBOUND" or
            type(event.get("issue_number")) is not int or event["issue_number"] <= 0):
        raise LocalWakeConfigError("REPORT_EVENT_MISMATCH")
    return event, report


def _load_grants(path: Path, repository_root: Path) -> list[dict]:
    """Private operator manifest is never read from the working Git tree."""
    if not path.is_file():
        raise LocalWakeConfigError("GRANTS_UNAVAILABLE")
    try:
        private = PrivateJsonState(path, repository_root, label="GRANTS",
                                   max_bytes=_MAX_GRANTS)
        with private.locked() as transaction:
            data = transaction.read({"version": 0, "grants": []})
        if (not isinstance(data, dict) or set(data) != {"version", "grants"} or
                type(data["version"]) is not int or data["version"] != 1 or
                not isinstance(data["grants"], list) or
                not 2 <= len(data["grants"]) <= 128 or
                any(not isinstance(g, dict) for g in data["grants"])):
            raise LocalWakeConfigError("GRANTS_INVALID")
        return data["grants"]
    except (PrivateStateError, OSError, ValueError, RecursionError):
        raise LocalWakeConfigError("GRANTS_UNAVAILABLE") from None


def _windows_process_sid() -> str:
    """Read the current process token SID, not username/environment/Issue text."""
    if os.name != "nt":
        raise LocalWakeConfigError("WINDOWS_INTERACTIVE_REQUIRED")
    try:
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.argtypes = []
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.HANDLE)]
        adv.OpenProcessToken.restype = wintypes.BOOL
        adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD)]
        adv.GetTokenInformation.restype = wintypes.BOOL
        adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.c_wchar_p)]
        adv.ConvertSidToStringSidW.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        token = wintypes.HANDLE()
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008,
                                    ctypes.byref(token)):
            raise LocalWakeConfigError("PROCESS_IDENTITY_UNAVAILABLE")
        try:
            needed = wintypes.DWORD(0)
            adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            if not 8 <= needed.value <= 4096:
                raise LocalWakeConfigError("PROCESS_IDENTITY_UNAVAILABLE")
            data = ctypes.create_string_buffer(needed.value)
            if not adv.GetTokenInformation(token, 1, data, needed.value,
                                           ctypes.byref(needed)):
                raise LocalWakeConfigError("PROCESS_IDENTITY_UNAVAILABLE")
            pointer = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
            if not pointer:
                raise LocalWakeConfigError("PROCESS_IDENTITY_UNAVAILABLE")
            sid_text = ctypes.c_wchar_p()
            if not adv.ConvertSidToStringSidW(ctypes.c_void_p(pointer),
                                               ctypes.byref(sid_text)):
                raise LocalWakeConfigError("PROCESS_IDENTITY_UNAVAILABLE")
            try:
                sid = sid_text.value
                if not sid or not re.fullmatch(r"S-1-(?:\d+-){1,14}\d+", sid):
                    raise LocalWakeConfigError("PROCESS_IDENTITY_UNAVAILABLE")
                return sid
            finally:
                kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        finally:
            kernel.CloseHandle(token)
    except LocalWakeConfigError:
        raise
    except Exception:
        raise LocalWakeConfigError("PROCESS_IDENTITY_UNAVAILABLE") from None


def send_one(*, event: dict, report: dict, workflow_id: int,
             caller_id: str, grants_path: Path, registry_path: Path,
             idempotency_path: Path, repository_root: Path,
             token_provider, secret_provider, principal_provider,
             adapter=None):
    """Wire D3.1/D3.2 into D2/D4 without granting new authority.

    Caller must be a trusted local entrypoint. Event and batch bytes are
    untrusted until the D3.1 authenticated GitHub verification succeeds.
    """
    if (type(workflow_id) is not int or workflow_id <= 0 or
            not isinstance(caller_id, str) or not _ID.fullmatch(caller_id)):
        raise LocalWakeConfigError("LOCAL_CONFIG_INVALID")
    try:
        grants = _load_grants(grants_path, repository_root)
        origin = SignedOriginConsentAuthority(
            grants=grants, secret_provider=secret_provider,
            principal_provider=principal_provider)
        artifact = GitHubRestArtifactAuthority(
            repository=_REPOSITORY, workflow_id=workflow_id,
            token_provider=token_provider)
        provenance = GitHubArtifactProvenance(
            repository=_REPOSITORY, run_id=event["run_id"],
            run_attempt=event["run_attempt"],
            artifact_name=f"ef02-results-{event['run_id']}-{event['run_attempt']}",
            workflow_run_status="completed", attestation_verified=True,
        )
        hint = LocalCallerAttestation(
            caller_id=caller_id, is_local=True, attestation_verified=True)
        return deliver_exact_chat_wake(
            event=event, batch_result=report, provenance=provenance,
            origin_attestation=hint, registry_path=registry_path,
            idempotency_path=idempotency_path, repository_root=repository_root,
            origin_authority=origin, artifact_authority=artifact,
            adapter=adapter if adapter is not None else LocalChromeDoorbellAdapter(allow_submit=True),
        )
    except LocalWakeConfigError:
        raise
    except Exception:
        raise LocalWakeConfigError("WAKE_EXECUTION_FAILED") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_commerce.remote.local_wake_entry")
    parser.add_argument("command", choices=["check", "send"])
    parser.add_argument("--event-file", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, default=Path("."))
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--idempotency", type=Path)
    parser.add_argument("--grants", type=Path)
    parser.add_argument("--workflow-id", type=int)
    parser.add_argument("--caller-id")
    args = parser.parse_args(argv)
    try:
        checkout = args.checkout.resolve(strict=True)
        if not checkout.is_dir():
            raise LocalWakeConfigError("CHECKOUT_INVALID")
        event, report = load_evidence(args.event_file, args.result_file)
        if event["repository"] != _REPOSITORY:
            raise LocalWakeConfigError("REPOSITORY_MISMATCH")
        if args.command == "check":
            print(json.dumps({"status": "PRECHECK_ONLY", "event_id": event["event_id"],
                              "delivery_verified": False, "browser_accessed": False}))
            return 0
        if os.name != "nt" or os.environ.get("AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED") != "true":
            raise LocalWakeConfigError("SEND_GATE_CLOSED")
        if (args.registry is None or args.idempotency is None or
                args.grants is None or args.workflow_id is None or
                args.caller_id is None):
            raise LocalWakeConfigError("SEND_CONFIG_MISSING")
        # Do not invoke Chrome unless both operator-held secrets are present.
        token = os.environ.get("AI_COMMERCE_GITHUB_READ_TOKEN", "")
        secret_hex = os.environ.get("AI_COMMERCE_WAKE_HMAC_KEY_HEX", "")
        if (not token or len(token) > 4096 or any(c in token for c in "\r\n\0") or
                len(secret_hex) < 64 or len(secret_hex) > 8192):
            raise LocalWakeConfigError("TRUSTED_CREDENTIALS_UNAVAILABLE")
        try:
            key = bytes.fromhex(secret_hex)
        except ValueError:
            raise LocalWakeConfigError("TRUSTED_CREDENTIALS_UNAVAILABLE") from None
        if len(key) < 32:
            raise LocalWakeConfigError("TRUSTED_CREDENTIALS_UNAVAILABLE")
        sid = _windows_process_sid()
        result = send_one(
            event=event, report=report, workflow_id=args.workflow_id,
            caller_id=args.caller_id, grants_path=args.grants,
            registry_path=args.registry, idempotency_path=args.idempotency,
            repository_root=checkout, token_provider=lambda: token,
            secret_provider=lambda: key, principal_provider=lambda: sid)
        print(json.dumps({
            "status": result.delivery_status,
            "error_code": result.error_code, "replayed": result.replayed,
            "chat_delivery_proven": False, "brain_continuation_proven": False,
        }))
        # UNKNOWN may have clicked Send and MUST NOT be blindly retried.
        return 0 if result.error_code == "UI_USER_TURN_OBSERVED" else 3
    except (LocalWakeConfigError, OSError, ValueError, TypeError):
        print("EF02_WAKE_BLOCKED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
