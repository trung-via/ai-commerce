"""Operator-enrolled, finite Issue-to-run registration. No route or send authority.

REST run metadata cannot authenticate an Issue payload. Adoption requires the
workflow's immutable request archive AND its exact completion artifact/event.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import io
import re
from pathlib import Path
import time
from urllib.parse import urlencode
import zipfile

from ._private_state import PrivateJsonState, PrivateStateError
from .conversation_binding import _decode_unique
from .github_artifact_authority import _API, _MAX_JSON, _MAX_ZIP, _json_from_archive
from .validation import fingerprint, validate_batch, verify_registry

REPOSITORY = "trung-via/ai-commerce"
_ROOT = _API + "/repos/" + REPOSITORY
_CODE_ROOT = Path(__file__).resolve().parents[3]
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_EVENT = re.compile(r"ef02-completion-v1:[0-9a-f]{64}\Z")


class RegistrationBlocked(ValueError):
    """Fixed diagnostic codes; untrusted payloads must not enter logs."""


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise RegistrationBlocked("REGISTRATION_TIME_INVALID")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        raise RegistrationBlocked("REGISTRATION_TIME_INVALID") from None


@dataclass(frozen=True)
class RegistrationIntent:
    repository: str
    issue_number: int
    batch_id: str
    source_sha: str
    workflow_id: int
    run_attempt: int
    issuer_login: str
    actor: str
    request_fingerprint: str
    created_after: str
    created_before: str

    def __post_init__(self):
        if (self.repository != REPOSITORY or
                any(type(getattr(self, k)) is not int or getattr(self, k) <= 0
                    for k in ("issue_number", "workflow_id", "run_attempt")) or
                any(not isinstance(v, str) or not p.fullmatch(v) for v, p in (
                    (self.batch_id, _ID), (self.source_sha, _SHA),
                    (self.issuer_login, _LOGIN), (self.request_fingerprint, _HEX))) or
                self.actor not in ("HUMAN", "BRAIN")):
            raise RegistrationBlocked("REGISTRATION_INTENT_INVALID")
        if not 0 < _timestamp(self.created_before) - _timestamp(self.created_after) <= 86400:
            raise RegistrationBlocked("REGISTRATION_WINDOW_INVALID")

    def target(self, run_id):
        from .local_wake_trigger import PinnedRun
        return PinnedRun(run_id, self.run_attempt, self.workflow_id,
                         self.issue_number, self.batch_id, self.source_sha)


def _record_valid(record, intent):
    if (not isinstance(record, dict) or set(record) != {
            "version", "intent", "run_id", "event_id", "request_artifact_id", "request_digest"} or
            type(record["version"]) is not int or record["version"] != 1 or
            not isinstance(record["intent"], dict) or
            fingerprint(record["intent"]) != fingerprint(asdict(intent)) or
            any(type(record[k]) is not int or record[k] <= 0
                for k in ("run_id", "request_artifact_id")) or
            not isinstance(record["event_id"], str) or not _EVENT.fullmatch(record["event_id"]) or
            not isinstance(record["request_digest"], str) or not _HEX.fullmatch(record["request_digest"])):
        raise RegistrationBlocked("REGISTRATION_STATE_INVALID")
    return record


class RegistrationStore:
    def __init__(self, path: Path, repository_root: Path):
        try:
            # Neither the operational checkout nor this source tree may hold state.
            PrivateJsonState(path, _CODE_ROOT, label="REGISTRATION", max_bytes=8192)
            self.state = PrivateJsonState(path, repository_root, label="REGISTRATION", max_bytes=8192)
        except PrivateStateError:
            raise RegistrationBlocked("REGISTRATION_LOCATION_INVALID") from None

    def _read(self, transaction, intent):
        data = transaction.read({})
        if data == {} and not self.state.path.exists():
            return None
        return _record_valid(data, intent)

    def read(self, intent):
        try:
            with self.state.locked() as transaction:
                return self._read(transaction, intent)
        except PrivateStateError:
            raise RegistrationBlocked("REGISTRATION_STATE_UNAVAILABLE") from None

    def register(self, intent, record):
        _record_valid(record, intent)
        try:
            with self.state.locked() as transaction:
                current = self._read(transaction, intent)
                if current is not None:
                    if current != record:
                        raise RegistrationBlocked("REGISTRATION_CONFLICT")
                    return current  # Idempotent: never rewrite an identical record.
                transaction.write(record)
                return record
        except PrivateStateError:
            raise RegistrationBlocked("REGISTRATION_STATE_UNAVAILABLE") from None


class IssueRunRegistrar:
    def __init__(self, *, reader, intent: RegistrationIntent, store: RegistrationStore,
                 repository_root: Path, brain_issuers=(), max_runs=50, wall_clock=time.time):
        if type(max_runs) is not int or not 1 <= max_runs <= 100:
            raise RegistrationBlocked("REGISTRATION_LIST_BUDGET_INVALID")
        if (not isinstance(brain_issuers, tuple) or
                any(not isinstance(v, str) or not _LOGIN.fullmatch(v) for v in brain_issuers)):
            raise RegistrationBlocked("REGISTRATION_ISSUERS_INVALID")
        self.reader, self.intent, self.store = reader, intent, store
        self.repository_root = Path(repository_root)
        self.brain_issuers = brain_issuers
        self.max_runs, self.wall_clock = max_runs, wall_clock

    def _json(self, path):
        return self.reader.read_json(_ROOT + path, self.reader.credential())

    def _request(self, request):
        intent = self.intent
        if (not isinstance(request, dict) or fingerprint(request) != intent.request_fingerprint or
                request.get("actor") != intent.actor or request.get("batch_id") != intent.batch_id or
                request.get("source_sha") != intent.source_sha):
            raise RegistrationBlocked("REQUEST_IDENTITY_MISMATCH")
        try:
            validate_batch(request, schema=self.repository_root / "contracts/RemoteActionBatch.v1.schema.json",
                           registry=verify_registry(self.repository_root / ".github/ef02/verify-profiles.json"),
                           issuer_login=intent.issuer_login, owner_login="trung-via",
                           brain_issuers=self.brain_issuers)
        except Exception:
            raise RegistrationBlocked("REQUEST_AUTHORIZATION_INVALID") from None
        return request

    def _preflight(self):
        intent = self.intent
        now = self.wall_clock()
        # A finite, closed search window, at most seven days old.
        if not now - 7 * 86400 <= _timestamp(intent.created_after) < _timestamp(intent.created_before) <= now:
            raise RegistrationBlocked("REGISTRATION_WINDOW_STALE_OR_OPEN")
        repository = self._json("")
        if (not isinstance(repository, dict) or repository.get("full_name") != REPOSITORY or
                not isinstance(repository.get("owner"), dict) or
                repository["owner"].get("login") != "trung-via"):
            raise RegistrationBlocked("REPOSITORY_IDENTITY_MISMATCH")
        workflow = self._json(f"/actions/workflows/{intent.workflow_id}")
        if (not isinstance(workflow, dict) or type(workflow.get("id")) is not int or
                workflow["id"] != intent.workflow_id or workflow.get("state") != "active" or
                workflow.get("path") != ".github/workflows/ef02-batch.yml"):
            raise RegistrationBlocked("WORKFLOW_IDENTITY_MISMATCH")
        issue = self._json(f"/issues/{intent.issue_number}")
        if (not isinstance(issue, dict) or type(issue.get("number")) is not int or
                issue["number"] != intent.issue_number or "pull_request" in issue or
                issue.get("html_url") != f"https://github.com/{REPOSITORY}/issues/{intent.issue_number}" or
                not isinstance(issue.get("user"), dict) or
                issue["user"].get("login") != intent.issuer_login or
                not isinstance(issue.get("title"), str) or not issue["title"].startswith("[EF02]") or
                not isinstance(issue.get("body"), str) or
                len(issue["body"].encode("utf-8")) > _MAX_JSON):
            raise RegistrationBlocked("ISSUE_IDENTITY_MISMATCH")
        text = issue["body"].strip()
        if text.startswith("~~~json") and text.endswith("~~~"):
            text = text[7:-3].strip()
        self._request(_decode_unique(text))

    def _run(self, run_id):
        intent = self.intent
        run = self._json(f"/actions/runs/{run_id}")
        if (not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] != run_id or
                type(run.get("workflow_id")) is not int or run["workflow_id"] != intent.workflow_id or
                type(run.get("run_attempt")) is not int or run["run_attempt"] != intent.run_attempt or
                run.get("event") != "issues" or not isinstance(run.get("repository"), dict) or
                run["repository"].get("full_name") != REPOSITORY or
                not _timestamp(intent.created_after) <= _timestamp(run.get("created_at")) <= _timestamp(intent.created_before) or
                not isinstance(run.get("actor"), dict) or
                run["actor"].get("login") not in {"trung-via", intent.issuer_login} or
                ("triggering_actor" in run and (not isinstance(run["triggering_actor"], dict) or
                 run["triggering_actor"].get("login") not in {"trung-via", intent.issuer_login}))):
            raise RegistrationBlocked("RUN_IDENTITY_MISMATCH")
        if run.get("status") not in {"completed", "queued", "in_progress", "waiting", "pending", "requested"}:
            raise RegistrationBlocked("RUN_STATUS_INVALID")
        return run

    def _artifacts(self, run_id):
        listing = self._json(f"/actions/runs/{run_id}/artifacts?per_page=100")
        arts = listing.get("artifacts") if isinstance(listing, dict) else None
        if (not isinstance(arts, list) or type(listing.get("total_count")) is not int or
                not 0 <= listing["total_count"] <= 100 or listing["total_count"] != len(arts) or
                any(not isinstance(a, dict) or type(a.get("id")) is not int or a["id"] <= 0 or
                    not isinstance(a.get("name"), str) for a in arts) or
                len({a["id"] for a in arts}) != len(arts)):
            raise RegistrationBlocked("ARTIFACT_LIST_INVALID")
        for item in arts:
            if ("workflow_run" in item and (not isinstance(item["workflow_run"], dict) or
                    type(item["workflow_run"].get("id")) is not int or
                    item["workflow_run"]["id"] != run_id)):
                raise RegistrationBlocked("ARTIFACT_RUN_MISMATCH")
        return arts

    def _candidate(self, run_id, *, explicit):
        run = self._run(run_id)
        if run["status"] != "completed":
            return None  # Even an unfinished competitor prevents adoption.
        artifacts = self._artifacts(run_id)
        snapshots = [a for a in artifacts if re.fullmatch(r"ef02-request-[1-9][0-9]*", a["name"])]
        if not snapshots:
            return None  # Upload/validation may not have produced the snapshot yet.
        if len(snapshots) != 1:
            raise RegistrationBlocked("REQUEST_ARTIFACT_AMBIGUOUS")
        item = snapshots[0]
        if item["name"] != f"ef02-request-{self.intent.issue_number}":
            if explicit:
                raise RegistrationBlocked("REQUEST_ISSUE_MISMATCH")
            return False  # Authenticated workflow snapshot belongs to another Issue.
        if item.get("expired") is not False:
            raise RegistrationBlocked("REQUEST_ARTIFACT_INVALID")
        raw = self.reader.read_archive(_ROOT + f"/actions/artifacts/{item['id']}/zip", self.reader.credential())
        if not isinstance(raw, bytes) or len(raw) > _MAX_ZIP:
            raise RegistrationBlocked("REQUEST_ZIP_INVALID")
        digest = hashlib.sha256(raw).hexdigest()
        if item.get("digest") is not None and item["digest"] != "sha256:" + digest:
            raise RegistrationBlocked("REQUEST_DIGEST_MISMATCH")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            request = self._request(_json_from_archive(archive, "request.json"))
        result = self.reader.candidate(self.intent.target(run_id))
        if result is None:
            return None
        event, report = result
        if sorted((a["action_id"], a["attempt_id"]) for a in request["actions"]) != sorted(
                (a["action_id"], a["attempt_id"]) for a in event["actions"]):
            raise RegistrationBlocked("REQUEST_ACTIONS_MISMATCH")
        record = {"version": 1, "intent": asdict(self.intent), "run_id": run_id,
                  "event_id": event["event_id"], "request_artifact_id": item["id"],
                  "request_digest": digest}
        return record, event, report

    def resolve(self, *, run_id=None):
        """One snapshot per poll. Identity failures escape; they never restart discovery."""
        try:
            self._preflight()
            saved = self.store.read(self.intent)
            if run_id is not None and (type(run_id) is not int or run_id <= 0):
                raise RegistrationBlocked("RUN_SELECTOR_INVALID")
            if saved is not None:
                if run_id is not None and run_id != saved["run_id"]:
                    raise RegistrationBlocked("REGISTRATION_CONFLICT")
                run_id = saved["run_id"]
            if run_id is not None:
                selected = self._candidate(run_id, explicit=True)
            else:
                query = urlencode({"event": "issues", "created": self.intent.created_after + ".." +
                                   self.intent.created_before, "per_page": 100})
                listing = self._json(f"/actions/workflows/{self.intent.workflow_id}/runs?{query}")
                runs = listing.get("workflow_runs") if isinstance(listing, dict) else None
                if (not isinstance(runs, list) or type(listing.get("total_count")) is not int or
                        not 0 <= listing["total_count"] <= self.max_runs or len(runs) != listing["total_count"] or
                        any(not isinstance(r, dict) or type(r.get("id")) is not int or r["id"] <= 0 for r in runs) or
                        len({r["id"] for r in runs}) != len(runs)):
                    raise RegistrationBlocked("RUN_LIST_INCOMPLETE_OR_OVER_BUDGET")
                matches, pending = [], False
                for run in runs:
                    candidate = self._candidate(run["id"], explicit=False)
                    if candidate is None:
                        pending = True
                    elif candidate is not False:
                        matches.append(candidate)
                if len(matches) > 1:
                    raise RegistrationBlocked("MATCHING_RUNS_AMBIGUOUS")
                selected = matches[0] if len(matches) == 1 and not pending else None
            if selected is None:
                return None
            record, event, report = selected
            if saved is not None and saved != record:
                raise RegistrationBlocked("REGISTRATION_PROVENANCE_CHANGED")
            self.store.register(self.intent, record)
            return self.intent.target(record["run_id"]), event, report
        except RegistrationBlocked:
            raise
        except Exception:
            raise RegistrationBlocked("REGISTRATION_EVIDENCE_UNAVAILABLE") from None


def wait_for_registration(registrar, *, run_id=None, max_polls=20, interval_seconds=30):
    if (type(max_polls) is not int or not 1 <= max_polls <= 120 or
            type(interval_seconds) is not int or not 1 <= interval_seconds <= 600):
        raise RegistrationBlocked("REGISTRATION_POLL_BUDGET_INVALID")
    for index in range(max_polls):
        result = registrar.resolve(run_id=run_id)
        if result is not None:
            return result
        if index + 1 < max_polls:
            registrar.reader.read_budget.pause(interval_seconds)
    raise RegistrationBlocked("REGISTRATION_EVIDENCE_NOT_READY")
