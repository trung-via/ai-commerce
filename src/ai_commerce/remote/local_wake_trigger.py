"""EF02-D5.2: bounded local GitHub exact-run completion trigger.

Source trigger, NOT a generic newest-run or newest-chat watcher. A trusted local
operator supplies the immutable run/attempt/issue/batch/source selectors. No
browser attachment or Wake is permitted by default. If explicitly requested,
D5.1 still independently verifies GitHub artifacts, signed consent and D2
idempotency before D4 can click Send.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from typing import Callable

from .completion_event import build_completion_event, validate_completion_event
from .github_artifact_authority import (
    GitHubRestArtifactAuthority, _API, _MAX_ZIP, _json_from_archive,
)
from .local_wake_entry import _windows_process_sid, send_one, LocalWakeConfigError
from .github_read_retry import GitHubReadBudget

_REPO = "trung-via/ai-commerce"
_BATCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class TriggerBlocked(ValueError):
    """Do not include remote payloads or bearer secrets in diagnostics."""


@dataclass(frozen=True)
class PinnedRun:
    run_id: int
    run_attempt: int
    workflow_id: int
    issue_number: int
    batch_id: str
    source_sha: str

    def __post_init__(self):
        if (any(type(getattr(self, k)) is not int or getattr(self, k) < 1
                for k in ("run_id", "run_attempt", "workflow_id", "issue_number")) or
                not isinstance(self.batch_id, str) or not _BATCH.fullmatch(self.batch_id) or
                not isinstance(self.source_sha, str) or not _SHA.fullmatch(self.source_sha)):
            raise TriggerBlocked("TARGET_INVALID")


class GitHubExactRunReader:
    """Read *candidate* bytes; D3.1 must independently verify before sending."""

    def __init__(self, *, token_provider: Callable[[], str],
                 read_json=None, read_archive=None, read_budget=None):
        self.token_provider = token_provider
        self.read_budget = read_budget or GitHubReadBudget()
        raw_json = read_json or (lambda u, t: GitHubRestArtifactAuthority._read_json(
            u, t, timeout=self.read_budget.timeout()))
        raw_archive = read_archive or (lambda u, t: GitHubRestArtifactAuthority._read_bytes(
            u, t, timeout=self.read_budget.timeout()))
        self.read_json = lambda u, t: self.read_budget.read(raw_json, u, t)
        self.read_archive = lambda u, t: self.read_budget.read(raw_archive, u, t)

    def credential(self):
        token = self.token_provider()
        if (not isinstance(token, str) or not token or len(token) > 4096 or
                any(ch in token for ch in "\r\n\0")):
            raise TriggerBlocked("GITHUB_CREDENTIAL_INVALID")
        return token

    def candidate(self, target: PinnedRun):
        try:
            token = self.credential()
            root = _API + "/repos/" + _REPO
            run = self.read_json(root + f"/actions/runs/{target.run_id}", token)
            if (not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] != target.run_id or
                    type(run.get("run_attempt")) is not int or run["run_attempt"] != target.run_attempt or
                    type(run.get("workflow_id")) is not int or run["workflow_id"] != target.workflow_id or
                    run.get("event") != "issues" or
                    not isinstance(run.get("repository"), dict) or
                    run["repository"].get("full_name") != _REPO):
                raise TriggerBlocked("RUN_IDENTITY_MISMATCH")
            if run.get("status") != "completed":
                if run.get("status") in ("queued", "in_progress", "waiting", "pending", "requested"):
                    return None
                raise TriggerBlocked("RUN_STATUS_INVALID")

            listing = self.read_json(
                root + f"/actions/runs/{target.run_id}/artifacts?per_page=100", token)
            arts = listing.get("artifacts") if isinstance(listing, dict) else None
            if (not isinstance(arts, list) or type(listing.get("total_count")) is not int
                    or not 0 <= listing["total_count"] <= 100 or len(arts) > 100):
                raise TriggerBlocked("ARTIFACT_LIST_INVALID")
            artifact_name = f"ef02-results-{target.run_id}-{target.run_attempt}"
            matches = [x for x in arts if isinstance(x, dict) and x.get("name") == artifact_name]
            if not matches and listing["total_count"] == len(arts):
                return None  # Completed runner may still be uploading its artifact.
            if (len(matches) != 1 or type(matches[0].get("id")) is not int or
                    matches[0]["id"] < 1 or matches[0].get("expired") is not False):
                raise TriggerBlocked("ARTIFACT_INVALID")
            if listing["total_count"] != len(arts):
                raise TriggerBlocked("ARTIFACT_LIST_INVALID")
            item = matches[0]
            if ("workflow_run" in item and (not isinstance(item["workflow_run"], dict) or
                    type(item["workflow_run"].get("id")) is not int or
                    item["workflow_run"]["id"] != target.run_id)):
                raise TriggerBlocked("ARTIFACT_RUN_MISMATCH")
            raw = self.read_archive(root + f"/actions/artifacts/{item['id']}/zip", token)
            if not isinstance(raw, bytes) or len(raw) > _MAX_ZIP:
                raise TriggerBlocked("ARTIFACT_ZIP_INVALID")
            digest = item.get("digest")
            if digest is not None and digest != "sha256:" + hashlib.sha256(raw).hexdigest():
                raise TriggerBlocked("ARTIFACT_DIGEST_INVALID")
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                event = _json_from_archive(archive, target.batch_id + "/completion_event.json")
                report = _json_from_archive(archive, target.batch_id + "/batch_result.json")
            event = validate_completion_event(event)
            if (build_completion_event(report, repository=_REPO, run_id=target.run_id,
                    run_attempt=target.run_attempt, issue_number=target.issue_number) != event or
                    event["repository"] != _REPO or event["run_id"] != target.run_id or
                    event["run_attempt"] != target.run_attempt or
                    event["issue_number"] != target.issue_number or
                    event["batch_id"] != target.batch_id or
                    event["source_sha"] != target.source_sha or
                    report.get("wake_event_id") != event["event_id"] or
                    report.get("wake_event_status") != "RECORDED_UNBOUND" or
                    str(report.get("github_workflow_run_id")) != str(target.run_id) or
                    report.get("github_issue_number") != target.issue_number):
                raise TriggerBlocked("EVIDENCE_AFFINITY_MISMATCH")
            return event, report
        except TriggerBlocked:
            raise
        except Exception:
            # Do not print remote responses, archive contents or tokens.
            raise TriggerBlocked("GITHUB_EVIDENCE_UNAVAILABLE") from None


def wait_for_exact_evidence(reader, target: PinnedRun, *,
                            max_polls: int = 20, interval_seconds: int = 30,
                            sleep: Callable[[float], None] = time.sleep):
    """At most N bounded read-only polls; never post a chat message here."""
    if (type(max_polls) is not int or not 1 <= max_polls <= 120 or
            type(interval_seconds) is not int or not 1 <= interval_seconds <= 600):
        raise TriggerBlocked("POLL_BUDGET_INVALID")
    for attempt in range(max_polls):
        result = reader.candidate(target)
        if result is not None:
            return result
        if attempt + 1 < max_polls:
            if hasattr(reader, "read_budget"):
                reader.read_budget.pause(interval_seconds)
            else:
                sleep(interval_seconds)
    raise TriggerBlocked("EVIDENCE_NOT_READY")


def _secret_inputs():
    token = os.environ.get("AI_COMMERCE_GITHUB_READ_TOKEN", "")
    key_hex = os.environ.get("AI_COMMERCE_WAKE_HMAC_KEY_HEX", "")
    if not token or len(token) > 4096 or any(x in token for x in "\r\n\0"):
        raise TriggerBlocked("GITHUB_CREDENTIAL_INVALID")
    if not key_hex or len(key_hex) % 2 or len(key_hex) < 64 or len(key_hex) > 8192:
        raise TriggerBlocked("WAKE_CREDENTIAL_INVALID")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError:
        raise TriggerBlocked("WAKE_CREDENTIAL_INVALID") from None
    if not 32 <= len(key) <= 4096:
        raise TriggerBlocked("WAKE_CREDENTIAL_INVALID")
    return token, key


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_commerce.remote.local_wake_trigger")
    parser.add_argument("command", nargs="?", default="observe", choices=["observe", "deliver"])
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--workflow-id", type=int, required=True)
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--max-polls", type=int, default=20)
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--checkout", type=str)
    parser.add_argument("--registry", type=str)
    parser.add_argument("--idempotency", type=str)
    parser.add_argument("--grants", type=str)
    parser.add_argument("--caller-id", type=str)
    parser.add_argument("--enroll", action="store_true")
    parser.add_argument("--repository", default=_REPO)
    parser.add_argument("--registration", type=str)
    parser.add_argument("--issuer-login", type=str)
    parser.add_argument("--actor", choices=["HUMAN", "BRAIN"])
    parser.add_argument("--request-fingerprint", type=str)
    parser.add_argument("--created-after", type=str)
    parser.add_argument("--created-before", type=str)
    parser.add_argument("--brain-issuer", action="append", default=[])
    parser.add_argument("--max-runs", type=int, default=50)
    parser.add_argument("--max-reads", type=int, default=200)
    parser.add_argument("--max-seconds", type=int, default=600)
    parser.add_argument("--read-attempts", type=int, default=3)
    args = parser.parse_args(argv)
    from pathlib import Path
    from .local_run_registration import (
        RegistrationBlocked, RegistrationIntent, RegistrationStore,
        IssueRunRegistrar, wait_for_registration,
    )
    try:
        if args.repository != _REPO:
            raise TriggerBlocked("REPOSITORY_MISMATCH")
        if args.enroll and not all((args.checkout, args.registration, args.issuer_login,
                                   args.actor, args.request_fingerprint,
                                   args.created_after, args.created_before)):
            raise TriggerBlocked("LOCAL_ENROLLMENT_REQUIRED")
        if not args.enroll and any((args.registration, args.issuer_login, args.actor,
                                   args.request_fingerprint, args.created_after,
                                   args.created_before, args.brain_issuer)):
            raise TriggerBlocked("LOCAL_ENROLLMENT_REQUIRED")
        if args.command == "deliver" and (
                os.name != "nt" or
                os.environ.get("AI_COMMERCE_LOCAL_WAKE_SEND_ENABLED") != "true" or
                not all((args.checkout, args.registry, args.idempotency,
                         args.grants, args.caller_id))):
            raise TriggerBlocked("LOCAL_SEND_GATE_CLOSED")
        token = os.environ.get("AI_COMMERCE_GITHUB_READ_TOKEN", "")
        if not token or len(token) > 4096 or any(x in token for x in "\r\n\0"):
            raise TriggerBlocked("GITHUB_CREDENTIAL_INVALID")
        if args.command == "deliver":
            token, key = _secret_inputs()
            principal = _windows_process_sid()
        budget = GitHubReadBudget(max_reads=args.max_reads, max_seconds=args.max_seconds,
                                  max_attempts=args.read_attempts)
        reader = GitHubExactRunReader(token_provider=lambda: token, read_budget=budget)
        if args.enroll:
            checkout = Path(args.checkout).resolve(strict=True)
            if not checkout.is_dir():
                raise TriggerBlocked("CHECKOUT_INVALID")
            intent = RegistrationIntent(args.repository, args.issue_number, args.batch_id,
                args.source_sha, args.workflow_id, args.run_attempt, args.issuer_login,
                args.actor, args.request_fingerprint, args.created_after, args.created_before)
            registrar = IssueRunRegistrar(reader=reader, intent=intent,
                store=RegistrationStore(Path(args.registration), checkout), repository_root=checkout,
                brain_issuers=tuple(args.brain_issuer), max_runs=args.max_runs)
            target, event, report = wait_for_registration(registrar, run_id=args.run_id,
                max_polls=args.max_polls, interval_seconds=args.interval_seconds)
        else:
            target = PinnedRun(args.run_id, args.run_attempt, args.workflow_id,
                               args.issue_number, args.batch_id, args.source_sha)
            event, report = wait_for_exact_evidence(
                reader, target, max_polls=args.max_polls,
                interval_seconds=args.interval_seconds)
        if args.command == "observe":
            print(json.dumps({"status": "CANDIDATE_READY_NOT_SENT",
                              "event_id": event["event_id"], "browser_accessed": False,
                              "registration_status": "REGISTERED" if args.enroll else "NOT_REQUESTED",
                              "chat_delivery_proven": False, "brain_continuation_proven": False}))
            return 0
        checkout = Path(args.checkout).resolve(strict=True)
        if not checkout.is_dir():
            raise TriggerBlocked("CHECKOUT_INVALID")
        receipt = send_one(
            event=event, report=report, workflow_id=target.workflow_id,
            caller_id=args.caller_id, grants_path=Path(args.grants),
            registry_path=Path(args.registry), idempotency_path=Path(args.idempotency),
            repository_root=checkout,
            token_provider=lambda: token, secret_provider=lambda: key,
            principal_provider=lambda: principal, read_budget=budget)
        print(json.dumps({"status": receipt.delivery_status,
                          "error_code": receipt.error_code, "replayed": receipt.replayed,
                          "chat_delivery_proven": False,
                          "brain_continuation_proven": False}))
        # Browser UI observation is NOT an authenticated server receipt.
        return 3
    except (TriggerBlocked, RegistrationBlocked, LocalWakeConfigError, OSError, ValueError, TypeError):
        print("EF02_LOCAL_TRIGGER_BLOCKED", file=sys.stderr)
        print(json.dumps({"status": "BLOCKED", "chat_delivery_proven": False,
                          "brain_continuation_proven": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
