"""BQ-06b: fresh A09 evidence replay pinned to retained A11 history -> read-only Brain handoff.

No writer, provider, strategy engine or authority ingress. Receivers must replay
``recheck_fresh_replay`` against independently retained pins; a SHA is identity only.
A09 local replay never upgrades HUMAN_SUPPLIED or SYNTHETIC_FIXTURE into verified provider metrics.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

from jsonschema import Draft202012Validator

from ai_commerce.analytics.schemas import CONTEXT, VERIFICATION
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.learning import (
    LearningAdvisory,
    LearningRegistry,
    SnapshotPin,
    export_advisory,
    read_snapshot,
)
from ai_commerce.learning.core import MAX_DOCUMENT_BYTES, digest
from ai_commerce.creative_quality import (
    behavioral_learning_bridge as bridge,
    pulse_studio_offline_bridge as q4_owner,
    t2_sampling,
)

SOURCE_SHA = "76589c479a10ff6788f5739cb3952dcf06c37d3b"
SCHEMA = "BQ06bFreshEvidenceReview.v1"
MAX_OUTPUT = 512 * 1024
MAX_FILES, MAX_BYTES, MAX_EVENTS, MAX_OBSERVATIONS = 512, 64 * 1024 * 1024, 32, 512

REASONS = (
    "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID",
    "UNSUPPORTED_REQUEST",
    "WINDOWS_REQUIRED",
    "A17_EXISTING_PENDING_PROPOSAL_REQUIRED",
    "A17_OPT_IN_REQUIRED",
    "LATEST_EVIDENCE_INCOMPLETE",
    "COHORT_REQUIRED",
    "REPORT_DENOMINATOR_REQUIRED",
    "CURRENT_Q4_TECHNICAL_EVIDENCE_REQUIRED",
    "A11_SELECTED_HISTORY_REQUIRED",
    "A11_LEARNING_RETIRED",
    "CROSS_OWNER_ATOMICITY_UNPROVEN",
    "A09_PIN_REQUIRED",
    "A09_SNAPSHOT_MISMATCH",
    "A09_RAW_REPLAY_FAILED",
    "A09_LINEAGE_STALE_OR_CORRECTED",
    "A11_UNMATCHED_OBSERVATION_REF",
    "METRIC_DEFINITION_DRIFT",
    "DENOMINATOR_DRIFT",
    "COHORT_DRIFT",
    "WINDOW_DRIFT",
    "TIMEZONE_DRIFT",
    "ACCOUNT_DRIFT",
    "SOURCE_STATUS_DRIFT",
    "DUPLICATE_OBSERVATION_ID",
    "MISSING_ZERO_CONFLATION",
    "INDEPENDENT_ORIGIN_DRIFT",
    "CURRENT_OWNER_REPLAY_REQUIRED",
)

QUESTIONS = [
    "Which independently comparable observations could challenge this hypothesis?",
    "What exposure denominator and assignment confounders remain unresolved?",
    "What evidence would bind a creative to an audience observation?",
]
HYPOTHESES = ["A separately authorized comparable observation could test the retained A11 hypothesis."]
COUNTEREXAMPLES = ["Retained counterevidence and missing observations must remain in review."]
FLAGS = {
    "provenance": "LOCAL_SUPPLIED_OR_SYNTHETIC_ONLY",
    "quality": "NOT_APPROVED",
    "rights": "UNKNOWN",
    "not_authorized": [
        "BRAIN_DECISION",
        "HUMAN_ADOPTION",
        "STRATEGY_CHANGE",
        "BRIEF_WRITE",
        "PROVIDER_REQUEST",
        "SPEND",
        "PUBLICATION",
        "LIVE_PULSE",
    ],
}
LIMITS = {
    "inference": "ADVISORY_ONLY",
    "verification_scope": "LOCAL_A09_RAW_REPLAY_OR_ARCHIVED_HASH_ONLY",
    "provider_attested": False,
    "audience_popularity": "NOT_ESTABLISHED",
    "causal_lift": None,
    "roi": None,
    "hash_use": "IDENTITY_ONLY_NOT_SIGNATURE",
    "authority": "NONE",
    "prose": "OWNER_RETAINED_HASH_REFERENCES_ONLY",
    "anti_rollback": "REQUIRES_INDEPENDENTLY_RETAINED_CURRENT_PINS",
}


class FreshReplayHold(ValueError):
    """Finite public failures; native exception messages are never exported."""


def _need(ok: bool, reason: str = "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID") -> None:
    if not ok:
        raise FreshReplayHold(reason)


def _obj(properties: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def _array(item: dict, limit: int) -> dict:
    return {"type": "array", "items": item, "maxItems": limit}


def _nullable(item: dict) -> dict:
    return {"oneOf": [item, {"type": "null"}]}


SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
NUMBER = {"type": "number", "minimum": -1e100, "maximum": 1e100}

REPLAYED_OBSERVATION = _obj({
    "observation_id": {"type": "string", "pattern": "^obs-[a-f0-9]{64}$"},
    "observation_ref_sha256": SHA,
    "state": {"enum": ["AVAILABLE", "NOT_AVAILABLE"]},
    "value": _nullable(NUMBER),
    "source_kind": {"enum": ["SYNTHETIC_FIXTURE", "USER_AUTHORED_MANUAL", "USER_PROVIDED_PLATFORM_EXPORT"]},
    "provenance": {"enum": ["SYNTHETIC_FIXTURE", "HUMAN_SUPPLIED"]},
    "source_status": {"const": VERIFICATION},
    "metric_key_sha256": SHA,
    "metric_definition_sha256": SHA,
})

A09_REPLAY = _obj({
    "import_id": {"type": "string", "pattern": "^a09-[a-f0-9]{64}$"},
    "snapshot_id": {"type": "string", "pattern": "^a11-snapshot-[a-f0-9]{64}$"},
    "raw_sha256": SHA,
    "context_sha256": SHA,
    "observation_count": {"type": "integer", "minimum": 1, "maximum": 10000},
    "matched_observation_count": {"type": "integer", "minimum": 1, "maximum": MAX_OBSERVATIONS},
    "replayed_observations": _array(REPLAYED_OBSERVATION, MAX_OBSERVATIONS),
    "verification_scope": {"const": "LOCAL_A09_RAW_REPLAY_ONLY"},
    "provider_attested": {"const": False},
    "source_status": {"const": VERIFICATION},
})


def fresh_review_schema() -> dict:
    """Complete closed field schema for BQ06b fresh evidence review."""
    return deepcopy(_obj({
        "schema_version": {"const": SCHEMA},
        "source_sha": {"const": SOURCE_SHA},
        "status": {"enum": ["LOCAL_A09_RAW_REPLAY", "ARCHIVED_A11_ONLY", "INSUFFICIENT_EVIDENCE_HOLD"]},
        "replay_type": {"enum": ["LOCAL_A09_RAW_REPLAY", "ARCHIVED_A11_ONLY", "HOLD"]},
        "bq06a": _nullable(bridge.review_schema()),
        "a09_replay": _nullable(A09_REPLAY),
        "association": {"const": "UNBOUND"},
        "comparability": {"const": "NOT_COMPARABLE"},
        "missing_requirements": {**_array({"enum": list(REASONS)}, len(REASONS)), "uniqueItems": True},
        "questions": {"const": QUESTIONS},
        "testable_hypotheses": {"const": HYPOTHESES},
        "counterexamples": {"const": COUNTEREXAMPLES},
        "flags": {"const": FLAGS},
        "limits": {"const": LIMITS},
        "record_sha256": SHA,
    }))


@dataclass(frozen=True, slots=True)
class BQ06bFreshEvidenceReview:
    """Serialization only. Constructing caller JSON confers no authenticity."""

    raw: bytes

    def __post_init__(self):
        try:
            _need(type(self.raw) is bytes and 0 < len(self.raw) <= MAX_OUTPUT)
            value = json.loads(self.raw)
            _need(canonical_bytes(value) == self.raw)
            _need(Draft202012Validator(fresh_review_schema()).is_valid(value))
            body = {k: v for k, v in value.items() if k != "record_sha256"}
            _need(value["record_sha256"] == digest(body))
            _need((value["status"] == "INSUFFICIENT_EVIDENCE_HOLD") == bool(value["missing_requirements"]))
            _need((value["status"] == "INSUFFICIENT_EVIDENCE_HOLD") == (value["replay_type"] == "HOLD"))
            if value["status"] == "LOCAL_A09_RAW_REPLAY":
                _need(value["a09_replay"] is not None and value["bq06a"] is not None)
                _need(value["replay_type"] == "LOCAL_A09_RAW_REPLAY")
            elif value["status"] == "ARCHIVED_A11_ONLY":
                _need(value["a09_replay"] is None and value["bq06a"] is not None)
                _need(value["replay_type"] == "ARCHIVED_A11_ONLY")
            if value["a09_replay"] is not None:
                for obs in value["a09_replay"]["replayed_observations"]:
                    _need((obs["state"] == "AVAILABLE") == (obs["value"] is not None))
        except Exception:
            raise FreshReplayHold("REVIEW_INVALID") from None

    def to_dict(self) -> dict:
        return json.loads(self.raw)

    @property
    def digest(self) -> str:
        return sha256(self.raw).hexdigest()


def _seal(
    bq06a: dict | None = None,
    a09_replay: dict | None = None,
    missing: tuple[str, ...] | list[str] = (),
) -> BQ06bFreshEvidenceReview:
    missing_list = sorted(set(missing))
    if missing_list:
        status = "INSUFFICIENT_EVIDENCE_HOLD"
        rtype = "HOLD"
        a09_rep = None
    elif a09_replay is not None:
        status = "LOCAL_A09_RAW_REPLAY"
        rtype = "LOCAL_A09_RAW_REPLAY"
        a09_rep = a09_replay
    else:
        status = "ARCHIVED_A11_ONLY"
        rtype = "ARCHIVED_A11_ONLY"
        a09_rep = None

    body = {
        "schema_version": SCHEMA,
        "source_sha": SOURCE_SHA,
        "status": status,
        "replay_type": rtype,
        "bq06a": bq06a,
        "a09_replay": a09_rep,
        "association": "UNBOUND",
        "comparability": "NOT_COMPARABLE",
        "missing_requirements": missing_list,
        "questions": QUESTIONS,
        "testable_hypotheses": HYPOTHESES,
        "counterexamples": COUNTEREXAMPLES,
        "flags": FLAGS,
        "limits": LIMITS,
    }
    return BQ06bFreshEvidenceReview(canonical_bytes({**body, "record_sha256": digest(body)}))


@contextmanager
def _frozen(files=(), trees=()):
    """Native deny-write/delete handles cover cross-owner replay and final checks."""
    import msvcrt

    with ExitStack() as stack:
        directories, retained, memberships = set(), {}, {}
        total = 0

        def directory(path: Path):
            for parent in reversed((path, *path.parents)):
                if parent not in directories:
                    stack.enter_context(q4_owner._directory_lock(parent))
                    directories.add(parent)

        def file(value: str | Path):
            nonlocal total
            path = q4_owner._path(str(value))
            if path in retained:
                return
            directory(path.parent)
            before = q4_owner._metadata(path)
            _need(len(retained) + len(memberships) < MAX_FILES and 0 < before.st_size <= MAX_BYTES - total)
            stream = stack.enter_context(t2_sampling._snapshot_lock(path))
            q4_owner._handle_path(q4_owner._kernel(), msvcrt.get_osfhandle(stream.fileno()), path)
            opened = os.fstat(stream.fileno())
            _need(
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                == (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            )
            _need(opened.st_nlink == 1)
            raw = stream.read(MAX_BYTES - total + 1)
            _need(len(raw) == before.st_size)
            total += len(raw)
            retained[path] = (stream, raw, before)

        def tree(value: str | Path):
            path = q4_owner._path(str(value), directory=True)
            directory(path)
            children = []
            for child in path.iterdir():
                children.append(child)
                _need(len(children) + len(retained) + len(memberships) <= MAX_FILES)
            memberships[path] = tuple(sorted(children))
            for child in children:
                if stat.S_ISDIR(child.lstat().st_mode):
                    tree(str(child))
                else:
                    file(str(child))

        for value in files:
            if value is not None:
                file(value)
        for value in trees:
            if value is not None:
                tree(value)
        yield
        for path, (stream, raw, before) in retained.items():
            after = q4_owner._metadata(path)
            _need(
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                "CROSS_OWNER_ATOMICITY_UNPROVEN",
            )
            stream.seek(0)
            _need(stream.read(len(raw) + 1) == raw, "CROSS_OWNER_ATOMICITY_UNPROVEN")
        for path, children in memberships.items():
            _need(tuple(sorted(path.iterdir())) == children, "CROSS_OWNER_ATOMICITY_UNPROVEN")


def _no_sqlite_sidecars(path: str | Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        _need(not os.path.lexists(str(path) + suffix))
    with Path(path).open("rb") as stream:
        header = stream.read(100)
    _need(header[:16] == b"SQLite format 3\x00" and header[18:20] == b"\x01\x01")


def review_fresh_replay(
    *,
    expected_export_id: str,
    learning_key: str,
    expected_revision: int,
    expected_context: dict,
    expected_metric: dict,
    registry_path: str | None = None,
    archive_path: str | None = None,
    a17: dict | None = None,
    pulse_session: str | None = None,
    q4_sidecar: bytes | None = None,
    a09_ledger_path: str | None = None,
    a09_pin: SnapshotPin | dict | None = None,
    bq06a_review: bytes | bridge.BQ06aLearningBriefReview | dict | None = None,
    association: str = "UNBOUND",
    context: dict | None = None,
    effect: str = "REVIEW_ONLY",
    **extra_kwargs,
) -> BQ06bFreshEvidenceReview:
    """Read-only A09 evidence recheck pinned to A11 selected history -> Brain handoff.

    Accepts receiver-supplied exact A09 SnapshotPin for one import, pinned A11
    archive/registry, and verified BQ06a review semantics. Missing evidence or drift
    produces typed HOLD.
    """
    if context is not None or effect != "REVIEW_ONLY" or association != "UNBOUND" or extra_kwargs:
        return _seal(missing=["UNSUPPORTED_REQUEST"])
    if os.name != "nt":
        return _seal(missing=["WINDOWS_REQUIRED"])

    try:
        _need(type(expected_export_id) is str and re.fullmatch(r"a11-advisory-[a-f0-9]{64}", expected_export_id))
        _need(type(learning_key) is str and 1 <= len(learning_key) <= 128)
        _need(type(expected_revision) is int and expected_revision >= 1)
        _need(type(expected_context) is dict and set(expected_context) == set(CONTEXT))
        _need(type(expected_metric) is dict)
        _need((registry_path is None) != (archive_path is None))
        a11_source = registry_path if archive_path is None else archive_path
        _need(type(a11_source) is str)

        a09_requested = a09_ledger_path is not None or a09_pin is not None
        pin_obj: SnapshotPin | None = None
        if a09_requested:
            if a09_ledger_path is None or a09_pin is None:
                return _seal(missing=["A09_PIN_REQUIRED"])
            _need(type(a09_ledger_path) is str)
            try:
                if isinstance(a09_pin, dict):
                    _need(set(a09_pin) == {"import_id", "context_sha256", "raw_sha256", "observation_ids"})
                    obs_ids = tuple(a09_pin["observation_ids"])
                    _need(len(set(obs_ids)) == len(obs_ids), "DUPLICATE_OBSERVATION_ID")
                    pin_obj = SnapshotPin(
                        a09_pin["import_id"],
                        a09_pin["context_sha256"],
                        a09_pin["raw_sha256"],
                        obs_ids,
                    )
                elif isinstance(a09_pin, SnapshotPin):
                    pin_obj = a09_pin
                else:
                    return _seal(missing=["SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID"])
            except FreshReplayHold:
                raise
            except Exception:
                return _seal(missing=["SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID"])

        files_to_freeze = [a11_source]
        if a09_requested and a09_ledger_path is not None:
            files_to_freeze.append(a09_ledger_path)
        trees_to_freeze = [] if a17 is None else [a17.get("registry_root")]

        with _frozen(files=files_to_freeze, trees=trees_to_freeze):
            if registry_path is not None:
                _no_sqlite_sidecars(registry_path)
            if a09_requested and a09_ledger_path is not None:
                _no_sqlite_sidecars(a09_ledger_path)

            missing: list[str] = []
            bq06a_dict: dict | None = None

            try:
                if bq06a_review is not None:
                    raw_bq06a = (
                        bq06a_review
                        if isinstance(bq06a_review, bytes)
                        else (
                            bq06a_review.raw
                            if isinstance(bq06a_review, bridge.BQ06aLearningBriefReview)
                            else canonical_bytes(bq06a_review)
                        )
                    )
                    bq06a_res = bridge.recheck_review(
                        raw_bq06a,
                        expected_export_id=expected_export_id,
                        learning_key=learning_key,
                        expected_revision=expected_revision,
                        expected_context=expected_context,
                        expected_metric=expected_metric,
                        registry_path=registry_path,
                        archive_path=archive_path,
                        a17=a17,
                        pulse_session=pulse_session,
                        q4_sidecar=q4_sidecar,
                    )
                else:
                    bq06a_res = bridge.review_learning(
                        expected_export_id=expected_export_id,
                        learning_key=learning_key,
                        expected_revision=expected_revision,
                        expected_context=expected_context,
                        expected_metric=expected_metric,
                        registry_path=registry_path,
                        archive_path=archive_path,
                        a17=a17,
                        pulse_session=pulse_session,
                        q4_sidecar=q4_sidecar,
                    )
                bq06a_dict = bq06a_res.to_dict()
                if bq06a_dict["status"] != "EVIDENCE_REVIEW_FOR_BRAIN":
                    missing.extend(bq06a_dict["missing_requirements"])
            except bridge.LearningBriefHold as exc:
                if str(exc) == "CURRENT_OWNER_REPLAY_REQUIRED":
                    missing.append("CURRENT_OWNER_REPLAY_REQUIRED")
                else:
                    missing.append("SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")
            except Exception:
                missing.append("SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")

            if not a09_requested:
                if registry_path is not None:
                    _no_sqlite_sidecars(registry_path)
                return _seal(bq06a=bq06a_dict, a09_replay=None, missing=missing)

            # A09 replay requested: extract selected A11 history and replay snapshot
            if registry_path is not None:
                advisory = export_advisory(LearningRegistry(registry_path))
            else:
                size = Path(archive_path).stat().st_size
                _need(0 < size <= MAX_DOCUMENT_BYTES)
                advisory = LearningAdvisory.from_bytes(Path(archive_path).read_bytes(), expected_id=expected_export_id)
            advisory = LearningAdvisory.from_bytes(advisory.to_bytes(), expected_id=expected_export_id)

            archive_dict = advisory.to_dict()
            selected = [e for e in archive_dict["entries"] if e["entry"]["revision"]["learning_key"] == learning_key]
            if not selected or selected[-1]["entry"]["revision"]["revision"] != expected_revision:
                missing.append("A11_SELECTED_HISTORY_REQUIRED")
            elif selected[-1]["entry"]["record"]["status"] == "RETIRED":
                missing.append("A11_LEARNING_RETIRED")

            snapshot = None
            if pin_obj is not None and a09_ledger_path is not None:
                try:
                    snapshot = read_snapshot(a09_ledger_path, pin=pin_obj)
                except Exception:
                    missing.append("A09_SNAPSHOT_MISMATCH")

            a09_rep_dict: dict | None = None
            if snapshot is not None and selected:
                snap_dict = {o["observation_id"]: o for o in snapshot.to_dict()["observations"]}
                rechecked: list[dict] = []

                import_rec = snapshot.to_dict()["import"]
                lineage = import_rec.get("lineage", [])
                for step in lineage:
                    action = step.get("action", "") if isinstance(step, dict) else ""
                    if action in ("CORRECTED", "SUPERSEDED", "REPROCESSED"):
                        missing.append("A09_LINEAGE_STALE_OR_CORRECTED")

                first_creative = selected[0]["entry"]["hypothesis"]["creative_ref"]
                for ev_entry in selected:
                    hyp = ev_entry["entry"]["hypothesis"]
                    if hyp["creative_ref"] != first_creative:
                        missing.append("INDEPENDENT_ORIGIN_DRIFT")

                for ev_entry in selected:
                    entry = ev_entry["entry"]
                    facts = {digest(f["observation_ref"]): f for f in entry["observation_sources"]}

                    for ev in entry["evidence"]:
                        ev_ref = ev["observation_ref"]
                        ev_obs_id = ev_ref["observation_id"]

                        if ev_ref["import_id"] != pin_obj.import_id:
                            missing.append("A11_UNMATCHED_OBSERVATION_REF")
                            continue
                        if ev_obs_id not in snap_dict:
                            missing.append("A11_UNMATCHED_OBSERVATION_REF")
                            continue

                        snap_obs = snap_dict[ev_obs_id]
                        snap_ref = snapshot.observation_ref(ev_obs_id)

                        if snap_ref != ev_ref:
                            missing.append("A11_UNMATCHED_OBSERVATION_REF")

                        fact_key = digest(ev_ref)
                        if fact_key not in facts:
                            missing.append("A11_UNMATCHED_OBSERVATION_REF")
                            continue
                        fact = facts[fact_key]

                        if snap_obs["status"] != fact["status"] or snap_obs["value"] != fact["value"]:
                            missing.append("SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")

                        if snap_obs["status"] == "AVAILABLE" and snap_obs["value"] is None:
                            missing.append("MISSING_ZERO_CONFLATION")
                        if snap_obs["status"] == "NOT_AVAILABLE" and snap_obs["value"] is not None:
                            missing.append("MISSING_ZERO_CONFLATION")

                        if digest(snap_obs["metric"]) != digest(expected_metric):
                            missing.append("METRIC_DEFINITION_DRIFT")
                        if snap_obs["metric"].get("denominator") != expected_metric.get("denominator"):
                            missing.append("DENOMINATOR_DRIFT")
                        if snap_obs.get("cohort") != expected_context.get("cohort"):
                            missing.append("COHORT_DRIFT")
                        if snap_obs.get("measurement_window") != expected_context.get("measurement_window"):
                            missing.append("WINDOW_DRIFT")
                        if snap_obs.get("timezone") != expected_context.get("timezone"):
                            missing.append("TIMEZONE_DRIFT")

                        actual_acc_id = "a11-account-" + digest(snap_obs["account_ref"])
                        if expected_context.get("account_ref", {}).get("id") != actual_acc_id:
                            missing.append("ACCOUNT_DRIFT")

                        if snap_obs.get("source", {}).get("source_status") != VERIFICATION:
                            missing.append("SOURCE_STATUS_DRIFT")

                        if not any(r["observation_id"] == ev_obs_id for r in rechecked):
                            kind = snap_obs["source"]["source_kind"]
                            rechecked.append({
                                "observation_id": ev_obs_id,
                                "observation_ref_sha256": digest(snap_ref),
                                "state": snap_obs["status"],
                                "value": snap_obs["value"],
                                "source_kind": kind,
                                "provenance": "SYNTHETIC_FIXTURE" if kind == "SYNTHETIC_FIXTURE" else "HUMAN_SUPPLIED",
                                "source_status": VERIFICATION,
                                "metric_key_sha256": digest(snap_obs["metric"]["metric_key"]),
                                "metric_definition_sha256": digest(snap_obs["metric"]),
                            })

                if rechecked:
                    a09_rep_dict = {
                        "import_id": snapshot.import_id,
                        "snapshot_id": snapshot.snapshot_id,
                        "raw_sha256": snapshot.to_dict()["import"]["raw_sha256"],
                        "context_sha256": snapshot.to_dict()["import"]["context_sha256"],
                        "observation_count": len(snapshot.to_dict()["observations"]),
                        "matched_observation_count": len(rechecked),
                        "replayed_observations": rechecked,
                        "verification_scope": "LOCAL_A09_RAW_REPLAY_ONLY",
                        "provider_attested": False,
                        "source_status": VERIFICATION,
                    }

            if registry_path is not None:
                _no_sqlite_sidecars(registry_path)
            if a09_requested and a09_ledger_path is not None:
                _no_sqlite_sidecars(a09_ledger_path)

            return _seal(bq06a=bq06a_dict, a09_replay=a09_rep_dict, missing=missing)

    except FreshReplayHold as exc:
        reason = str(exc)
        if reason not in REASONS:
            reason = "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID"
        return _seal(missing=[reason])
    except Exception:
        return _seal(missing=["SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID"])


def recheck_fresh_replay(raw: bytes, **source_arguments) -> BQ06bFreshEvidenceReview:
    """Receiver replay. A well-formed, rehashed sidecar alone is insufficient."""
    supplied = BQ06bFreshEvidenceReview(raw)
    current = review_fresh_replay(**source_arguments)
    if current.raw != supplied.raw:
        raise FreshReplayHold("CURRENT_OWNER_REPLAY_REQUIRED")
    return current
