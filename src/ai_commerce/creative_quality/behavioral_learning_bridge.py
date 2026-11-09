"""BQ-06a: retained A11 history -> read-only, advisory Brain handoff.

No writer, provider, strategy engine or authority ingress. Receivers must replay
``recheck_review`` against independently retained pins; a SHA is identity only.
Owner prose stays in A11/A17: public projections contain hashes of that prose.
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
from ai_commerce.learning import LearningAdvisory, LearningRegistry, export_advisory
from ai_commerce.learning.core import MAX_DOCUMENT_BYTES, digest
from ai_commerce.creative_scaling.learning_bridge import consume_learning_advisory
from ai_commerce.creative_scaling.feedback import load_proposal
from ai_commerce.creative_scaling.variants import load_variant_batch
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.creative_quality import pulse_studio_offline_bridge as q4_owner
from ai_commerce.creative_quality import t2_sampling

SOURCE_SHA = "6777b13f9effbf242b2ebd963ee5f7580c485651"
SCHEMA = "BQ06aLearningBriefReview.v1"
MAX_OUTPUT = 512 * 1024
MAX_FILES, MAX_BYTES, MAX_EVENTS, MAX_OBSERVATIONS = 512, 64 * 1024 * 1024, 32, 512
REASONS = (
    "SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID", "UNSUPPORTED_REQUEST", "WINDOWS_REQUIRED",
    "A17_EXISTING_PENDING_PROPOSAL_REQUIRED", "A17_OPT_IN_REQUIRED",
    "LATEST_EVIDENCE_INCOMPLETE", "COHORT_REQUIRED", "REPORT_DENOMINATOR_REQUIRED",
    "CURRENT_Q4_TECHNICAL_EVIDENCE_REQUIRED", "A11_SELECTED_HISTORY_REQUIRED",
    "A09_CURRENT_REPLAY_NOT_SUPPORTED", "A11_LEARNING_RETIRED",
)
QUESTIONS = [
    "Which independently comparable observations could challenge this hypothesis?",
    "What exposure denominator and assignment confounders remain unresolved?",
    "What evidence would bind a creative to an audience observation?",
]
HYPOTHESES = ["A separately authorized comparable observation could test the retained A11 hypothesis."]
FLAGS = {"provenance": "LOCAL_SUPPLIED_OR_SYNTHETIC_ONLY", "quality": "NOT_APPROVED",
         "rights": "UNKNOWN", "not_authorized": ["BRAIN_DECISION", "HUMAN_ADOPTION", "STRATEGY_CHANGE",
         "BRIEF_WRITE", "PROVIDER_REQUEST", "SPEND", "PUBLICATION", "LIVE_PULSE"]}
LIMITS = {"inference": "ADVISORY_ONLY", "verification_scope": "ARCHIVED_LOCAL_HASH_CHAIN_ONLY",
          "current_a09_replay": "NOT_PERFORMED", "provider_attested": False,
          "audience_popularity": "NOT_ESTABLISHED", "causal_lift": None, "roi": None,
          "hash_use": "IDENTITY_ONLY_NOT_SIGNATURE", "authority": "NONE",
          "prose": "OWNER_RETAINED_HASH_REFERENCES_ONLY",
          "anti_rollback": "REQUIRES_INDEPENDENTLY_RETAINED_CURRENT_PINS"}


class LearningBriefHold(ValueError):
    """Finite public failures; native exception messages are never exported."""


def _need(ok):
    if not ok:
        raise LearningBriefHold("SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID")


def _obj(properties):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


def _array(item, limit):
    return {"type": "array", "items": item, "maxItems": limit}


def _nullable(item):
    return {"oneOf": [item, {"type": "null"}]}


SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
NUMBER = {"type": "number", "minimum": -1e100, "maximum": 1e100}
INTEGER = {"type": "integer", "minimum": 1, "maximum": 1000000}
REFS = _array(SHA, 200)
CONFOUNDER = _obj({"owner_value_sha256": SHA, "evidence_refs_sha256": SHA})
CONFOUNDERS = _array(CONFOUNDER, 200)
METRIC = _obj({"definition_sha256": SHA, "definition_version_sha256": SHA,
               "metric_key_sha256": SHA, "definition_text_sha256": SHA, "unit_sha256": SHA,
               "denominator_definition_sha256": _nullable(SHA), "cohort_sha256": _nullable(SHA),
               "source_status": {"const": VERIFICATION}})
PUBLIC_CONTEXT = _obj({"platform": {"enum": ["TIKTOK", "YOUTUBE", "FACEBOOK", "INSTAGRAM"]},
    "surface": {"enum": ["VIDEO", "PROFILE_VIDEO", "SHORT", "PAGE_REEL", "REEL"]},
    "account_alias": {"type": "string", "pattern": "^a11-account-[a-f0-9]{64}$"},
    "cohort_sha256": _nullable(SHA), "measurement_window": _array({"type": "string", "maxLength": 64}, 2),
    "timezone": {"type": "string", "maxLength": 128}})
MEASUREMENT = _obj({"status": {"enum": ["AVAILABLE", "NOT_AVAILABLE", "INSUFFICIENT_DATA"]},
    "value": _nullable(NUMBER), "numerator": _nullable(NUMBER), "denominator": _nullable(NUMBER),
    "unit_sha256": SHA, "reason_sha256": _nullable(SHA)})
OBSERVATION = _obj({"evidence_id": {"type": "string", "pattern": "^a11-evidence-[a-f0-9]{64}$"},
    "observation_ref_sha256": SHA, "source_fact_sha256": SHA,
    "role": {"enum": ["SUPPORTING", "COUNTEREVIDENCE", "INCONCLUSIVE"]},
    "state": {"enum": ["AVAILABLE", "NOT_AVAILABLE", "INSUFFICIENT_DATA"]},
    "source_state": {"enum": ["AVAILABLE", "NOT_AVAILABLE"]}, "source_value": _nullable(NUMBER),
    "value": _nullable(NUMBER), "report": _nullable(MEASUREMENT),
    "provenance": {"enum": ["SYNTHETIC_FIXTURE", "HUMAN_SUPPLIED"]},
    "source_status": {"const": VERIFICATION}, "source_kind": {"enum": ["SYNTHETIC_FIXTURE", "USER_AUTHORED_MANUAL", "USER_PROVIDED_PLATFORM_EXPORT"]},
    "metric": METRIC, "observed_outcome_sha256": SHA,
    "limitations_sha256": SHA, "confounders": CONFOUNDERS})
REVISION = _obj({"sequence": INTEGER, "event_hash": SHA, "entry_sha256": SHA,
    "revision": INTEGER, "revision_id": {"type": "string", "maxLength": 128},
    "previous_revision_sha256": _nullable(SHA), "creative_ref_sha256": SHA,
    "hypothesis_sha256": SHA, "uncertainty_sha256": SHA, "confounders": CONFOUNDERS,
    "evidence_refs": REFS, "counterevidence_refs": REFS,
    "observations": _array(OBSERVATION, MAX_OBSERVATIONS)})
ARCHIVE = _obj({"export_id": {"type": "string", "pattern": "^a11-advisory-[a-f0-9]{64}$"},
    "archive_sha256": SHA, "head_event_hash": SHA, "event_count": {"type": "integer", "minimum": 1, "maximum": MAX_EVENTS},
    "learning_key_sha256": SHA, "revision": INTEGER, "metric": METRIC, "context": PUBLIC_CONTEXT,
    "source_status": {"const": VERIFICATION}, "history": _array(REVISION, MAX_EVENTS)})
A17 = _obj({"proposal_sha256": SHA, "context_sha256": SHA, "source_sha256": SHA,
            "adoption": {"const": "PENDING"}, "generation_performed": {"const": False},
            "inference": {"const": "HYPOTHESIS_ONLY"}})
TECHNICAL = _obj({"sidecar_sha256": SHA, "artifact_sha256": SHA, "journal_checkpoint_sha256": SHA,
    "status": {"const": "CURRENT_BYTES_TECHNICAL_ONLY"}, "provider_attested": {"const": False},
    "evidence_type": {"enum": ["SYNTHETIC_FIXTURE", "HUMAN_SUPPLIED"]},
    "audience_metric": {"type": "null"}})


def review_schema():
    """Complete closed field schema, including every public nested projection."""
    return deepcopy(_obj({"schema_version": {"const": SCHEMA}, "source_sha": {"const": SOURCE_SHA},
        "status": {"enum": ["EVIDENCE_REVIEW_FOR_BRAIN", "INSUFFICIENT_EVIDENCE_HOLD"]},
        "archive": _nullable(ARCHIVE), "a17": _nullable(A17), "technical": _nullable(TECHNICAL),
        "association": {"const": "UNBOUND"}, "comparability": {"const": "NOT_COMPARABLE"},
        "missing_requirements": {**_array({"enum": list(REASONS)}, len(REASONS)), "uniqueItems": True},
        "questions": {"const": QUESTIONS}, "testable_hypotheses": {"const": HYPOTHESES},
        "counterexamples": {"const": ["Retained counterevidence and missing observations must remain in review."]},
        "flags": {"const": FLAGS}, "limits": {"const": LIMITS}, "record_sha256": SHA}))


@dataclass(frozen=True, slots=True)
class BQ06aLearningBriefReview:
    """Serialization only. Constructing caller JSON confers no authenticity."""

    raw: bytes

    def __post_init__(self):
        try:
            _need(type(self.raw) is bytes and 0 < len(self.raw) <= MAX_OUTPUT)
            value = json.loads(self.raw)
            _need(canonical_bytes(value) == self.raw)
            _need(Draft202012Validator(review_schema()).is_valid(value))
            body = {k: v for k, v in value.items() if k != "record_sha256"}
            _need(value["record_sha256"] == digest(body))
            _need((value["status"] == "EVIDENCE_REVIEW_FOR_BRAIN") == (not value["missing_requirements"]))
            if value["status"] == "EVIDENCE_REVIEW_FOR_BRAIN":
                _need(value["archive"] is not None and value["a17"] is not None)
            if value["archive"] is not None:
                for revision in value["archive"]["history"]:
                    for obs in revision["observations"]:
                        _need((obs["source_state"] == "AVAILABLE") == (obs["source_value"] is not None))
                        _need((obs["state"] == "AVAILABLE") == (obs["value"] is not None))
        except Exception:
            raise LearningBriefHold("REVIEW_INVALID") from None

    def to_dict(self):
        return json.loads(self.raw)

    @property
    def digest(self):
        return sha256(self.raw).hexdigest()


def _seal(archive=None, a17=None, technical=None, missing=()):
    body = {"schema_version": SCHEMA, "source_sha": SOURCE_SHA,
        "status": "INSUFFICIENT_EVIDENCE_HOLD" if missing else "EVIDENCE_REVIEW_FOR_BRAIN",
        "archive": archive, "a17": a17, "technical": technical,
        "association": "UNBOUND", "comparability": "NOT_COMPARABLE",
        "missing_requirements": sorted(set(missing)), "questions": QUESTIONS,
        "testable_hypotheses": HYPOTHESES,
        "counterexamples": ["Retained counterevidence and missing observations must remain in review."],
        "flags": FLAGS, "limits": LIMITS}
    return BQ06aLearningBriefReview(canonical_bytes({**body, "record_sha256": digest(body)}))


@contextmanager
def _frozen(files=(), trees=()):
    """Native deny-write/delete handles cover owner replay and final checks.

    Reuse BQ05a's strict Win32 spelling, ancestor locks and T2 file locks.
    Freeze every A17 file; bound enumeration before reading. SQLite sidecars
    are refused: this adapter requires a quiescent retained A11 archive.
    """
    import msvcrt
    with ExitStack() as stack:
        directories, retained, memberships = set(), {}, {}
        total = 0

        def directory(path):
            for parent in reversed((path, *path.parents)):
                if parent not in directories:
                    stack.enter_context(q4_owner._directory_lock(parent))
                    directories.add(parent)

        def file(value):
            nonlocal total
            path = q4_owner._path(value)
            if path in retained:
                return
            directory(path.parent)
            before = q4_owner._metadata(path)
            _need(len(retained) + len(memberships) < MAX_FILES and 0 < before.st_size <= MAX_BYTES - total)
            stream = stack.enter_context(t2_sampling._snapshot_lock(path))
            q4_owner._handle_path(q4_owner._kernel(), msvcrt.get_osfhandle(stream.fileno()), path)
            opened = os.fstat(stream.fileno())
            _need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                  == (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns))
            _need(opened.st_nlink == 1)
            raw = stream.read(MAX_BYTES - total + 1)
            _need(len(raw) == before.st_size)
            total += len(raw)
            retained[path] = (stream, raw, before)

        def tree(value):
            path = q4_owner._path(value, directory=True)
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
            file(value)
        for value in trees:
            tree(value)
        yield
        for path, (stream, raw, before) in retained.items():
            after = q4_owner._metadata(path)
            _need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                  == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
            stream.seek(0)
            _need(stream.read(len(raw) + 1) == raw)
        for path, children in memberships.items():
            _need(tuple(sorted(path.iterdir())) == children)


def _no_sqlite_sidecars(path):
    for suffix in ("-wal", "-shm", "-journal"):
        _need(not os.path.lexists(str(path) + suffix))
    # mode=ro may create WAL/SHM when the persistent header still declares WAL,
    # even after those files disappeared. Reject before the owner's SQLite open.
    # This bounded read occurs while the native file/ancestor locks are held.
    with Path(path).open("rb") as stream:
        header = stream.read(100)
    _need(header[:16] == b"SQLite format 3\x00" and header[18:20] == b"\x01\x01")


def _confounders(items):
    return [{"owner_value_sha256": digest(item), "evidence_refs_sha256": digest(item["evidence_refs"])}
            for item in items]


def _metric(value):
    return {"definition_sha256": digest(value), "definition_version_sha256": digest(value["definition_version"]),
        "metric_key_sha256": digest(value["metric_key"]), "definition_text_sha256": digest(value["definition"]),
        "unit_sha256": digest(value["unit"]), "denominator_definition_sha256": None if value["denominator"] is None
        else digest(value["denominator"]), "cohort_sha256": None if value["cohort"] is None else digest(value["cohort"]),
        "source_status": value["source_status"]}


def _project(advisory, key, revision, expected_context, expected_metric):
    archive = advisory.to_dict()
    _need(1 <= archive["event_count"] <= MAX_EVENTS)
    selected = [e for e in archive["entries"] if e["entry"]["revision"]["learning_key"] == key]
    _need(selected and type(revision) is int and selected[-1]["entry"]["revision"]["revision"] == revision)
    _need(type(expected_context) is dict and set(expected_context) == set(CONTEXT))
    _need(type(expected_metric) is dict)
    account = expected_context["account_ref"]
    _need(account["namespace"] == "internal_alias" and type(account["id"]) is str
          and re.fullmatch(r"a11-account-[a-f0-9]{64}", account["id"]) is not None)
    history, count, missing = [], 0, []
    for event in selected:
        entry = event["entry"]
        hyp, record, rev = (entry[k] for k in ("hypothesis", "record", "revision"))
        _need({k: hyp[k] for k in CONTEXT} == expected_context)
        # One source pin across the selected revision lineage. A different creative
        # requires a separate review, even when human-readable names happen to match.
        _need(hyp["creative_ref"] == selected[-1]["entry"]["hypothesis"]["creative_ref"])
        reports = {r["report_ref"]["report_id"]: r["body"] for r in entry["report_sources"]}
        for report in reports.values():
            _need(report["metric"] == expected_metric and {k: report[k] for k in CONTEXT} == expected_context)
        facts = {digest(f["observation_ref"]): f for f in entry["observation_sources"]}
        for fact in facts.values():
            _need(fact["metric"] == expected_metric and fact["context"] == expected_context)
        observations = []
        for evidence in entry["evidence"]:
            fact = facts[digest(evidence["observation_ref"])]
            report = None if evidence["report_ref"] is None else reports[evidence["report_ref"]["report_id"]]["measurement"]
            projected_report = None if report is None else {k: report[k] for k in ("status", "value", "numerator", "denominator")}
            if projected_report is not None:
                projected_report.update(unit_sha256=digest(report["unit"]), reason_sha256=None
                                        if report["reason"] is None else digest(report["reason"]))
            available = evidence["evidence_state"] == "AVAILABLE"
            value = (fact["value"] if report is None else report["value"]) if available else None
            kind = fact["source"]["source_kind"]
            observations.append({"evidence_id": evidence["evidence_id"],
                "observation_ref_sha256": digest(evidence["observation_ref"]), "source_fact_sha256": digest(fact),
                "role": evidence["role"], "state": evidence["evidence_state"], "source_state": fact["status"],
                "source_value": fact["value"], "value": value, "report": projected_report,
                "provenance": "SYNTHETIC_FIXTURE" if kind == "SYNTHETIC_FIXTURE" else "HUMAN_SUPPLIED",
                "source_status": fact["source"]["source_status"], "source_kind": kind, "metric": _metric(fact["metric"]),
                "observed_outcome_sha256": digest(evidence["observed_outcome"]),
                "limitations_sha256": digest(evidence["limitations"]), "confounders": _confounders(evidence["confounders"])})
            count += 1
            _need(count <= MAX_OBSERVATIONS)
        history.append({"sequence": event["sequence"], "event_hash": event["event_hash"], "entry_sha256": digest(entry),
            "revision": rev["revision"], "revision_id": rev["revision_id"],
            "previous_revision_sha256": None if rev["previous_revision_ref"] is None else digest(rev["previous_revision_ref"]),
            "creative_ref_sha256": digest(hyp["creative_ref"]), "hypothesis_sha256": digest(hyp),
            "uncertainty_sha256": digest({"hypothesis": hyp["uncertainty"], "record": record["uncertainty"]}),
            "confounders": _confounders(hyp["confounders"] + record["confounders"]),
            "evidence_refs": [digest(r) for r in record["evidence_refs"]],
            "counterevidence_refs": [digest(r) for r in record["counterevidence_refs"]], "observations": observations})
    latest = history[-1]["observations"]
    if not latest or any(o["state"] != "AVAILABLE" for o in latest):
        missing.append("LATEST_EVIDENCE_INCOMPLETE")
    if expected_context["cohort"] is None:
        missing.append("COHORT_REQUIRED")
    if expected_metric["denominator"] is not None and any(o["report"] is None
            or o["report"]["denominator"] is None for o in latest):
        missing.append("REPORT_DENOMINATOR_REQUIRED")
    if selected[-1]["entry"]["record"]["status"] == "RETIRED":
        missing.append("A11_LEARNING_RETIRED")
    public_context = {k: expected_context[k] for k in ("platform", "surface", "measurement_window", "timezone")}
    public_context.update(account_alias=account["id"], cohort_sha256=None if expected_context["cohort"] is None
                          else digest(expected_context["cohort"]))
    return {"export_id": advisory.export_id, "archive_sha256": sha256(advisory.to_bytes()).hexdigest(),
        "head_event_hash": archive["head_event_hash"], "event_count": archive["event_count"],
        "learning_key_sha256": digest(key), "revision": revision, "metric": _metric(expected_metric),
        "context": public_context, "source_status": archive["source_status"], "history": history}, missing


def _a17_review(options, advisory, key, revision, context, metric):
    _need(type(options) is dict and set(options) == {"registry_root", "batch_id", "source_id", "proposal_id",
                                                   "expected_proposal_sha256", "opt_in"})
    _need(options["opt_in"] is True)
    # The root already exists under native ancestor locks. Preserve every
    # owner constructor guard; its mkdir(exist_ok=True) cannot create a root
    # here. Subsequent native CompositionStore reads use the same owner API.
    root = q4_owner._path(options["registry_root"], directory=True)
    registry = ArtifactRegistry(root)
    batch = load_variant_batch(registry, options["batch_id"])
    learning = consume_learning_advisory(advisory, registry=registry, batch=batch,
        source_id=options["source_id"], expected_export_id=advisory.export_id, learning_key=key,
        expected_revision=revision, scope=batch["scope"], opt_in=True)
    _need({k: learning["context"]["scope"][k] for k in CONTEXT} == context and learning["context"]["metric"] == metric)
    proposal = load_proposal(registry, options["proposal_id"])
    _need(proposal["record_sha256"] == options["expected_proposal_sha256"])
    _need(proposal["source"] == learning["context"]["source"])
    _need(proposal["advisory"]["export_id"] == advisory.export_id and proposal["advisory"]["learning_key"] == key
          and proposal["advisory"]["revision"] == revision)
    _need(proposal["adoption"] == {"decision": "PENDING", "actor": None, "note": None})
    # A recorded r2 is an independently owned Human decision, outside this
    # pending handoff. Never report an already adopted proposal as pending.
    from ai_commerce.creative_scaling.feedback import proposal_ref
    _need(not registry.exists(proposal_ref(options["proposal_id"], 2)))
    return {"proposal_sha256": proposal["record_sha256"], "context_sha256": learning["sha256"],
        "source_sha256": proposal["source"]["sha256"], "adoption": "PENDING",
        "generation_performed": False, "inference": "HYPOTHESIS_ONLY"}


def review_learning(*, expected_export_id, learning_key, expected_revision, expected_context, expected_metric,
                    registry_path=None, archive_path=None, a17=None, pulse_session=None, q4_sidecar=None,
                    association="UNBOUND", context=None, effect="REVIEW_ONLY", a09_current=None):
    """Review genuine owners without mutation; missing contracts produce HOLD.

    Exactly one quiescent native A11 database or independently retained export
    is required. Expected export/revision/context/metric/proposal pins must come
    from the receiver's independent archive, never from untrusted input itself.
    No A09 current replay is implemented: requesting it explicitly holds.
    """
    if context is not None or effect != "REVIEW_ONLY" or association != "UNBOUND":
        return _seal(missing=["UNSUPPORTED_REQUEST"])
    if a09_current is not None:
        return _seal(missing=["A09_CURRENT_REPLAY_NOT_SUPPORTED"])
    if os.name != "nt":
        return _seal(missing=["WINDOWS_REQUIRED"])
    try:
        _need(type(expected_export_id) is str and re.fullmatch(r"a11-advisory-[a-f0-9]{64}", expected_export_id))
        _need(type(learning_key) is str and 1 <= len(learning_key) <= 128)
        _need((registry_path is None) != (archive_path is None))
        source = registry_path if archive_path is None else archive_path
        _need(type(source) is str)
        if a17 is not None:
            _need(type(a17) is dict and type(a17.get("registry_root")) is str)
        with _frozen(files=[source], trees=[] if a17 is None else [a17["registry_root"]]):
            if registry_path is not None:
                _no_sqlite_sidecars(registry_path)
                advisory = export_advisory(LearningRegistry(registry_path))
            else:
                size = Path(archive_path).stat().st_size
                _need(0 < size <= MAX_DOCUMENT_BYTES)
                advisory = LearningAdvisory.from_bytes(Path(archive_path).read_bytes(), expected_id=expected_export_id)
            advisory = LearningAdvisory.from_bytes(advisory.to_bytes(), expected_id=expected_export_id)
            archive, missing = _project(advisory, learning_key, expected_revision, expected_context, expected_metric)
            proposal = None
            if a17 is None:
                missing.append("A17_EXISTING_PENDING_PROPOSAL_REQUIRED")
            elif a17.get("opt_in") is not True:
                missing.append("A17_OPT_IN_REQUIRED")
            else:
                proposal = _a17_review(a17, advisory, learning_key, expected_revision, expected_context, expected_metric)
            technical = None
            if pulse_session is not None or q4_sidecar is not None:
                _need(type(pulse_session) is str)
                # Both creation and receiver replay invoke the native owner.
                observed = q4_owner.inspect_wait_brain(pulse_session) if q4_sidecar is None else q4_owner.recheck_sidecar(
                    q4_sidecar, pulse_session)
                value = observed.to_dict()
                if value["status"] != "OFFLINE_TECHNICAL_REVIEW_ONLY":
                    missing.append("CURRENT_Q4_TECHNICAL_EVIDENCE_REQUIRED")
                else:
                    technical = {"sidecar_sha256": observed.digest, "artifact_sha256": value["binding"]["artifact_sha256"],
                        "journal_checkpoint_sha256": digest(value["binding"]["journal_checkpoint"]),
                        "status": "CURRENT_BYTES_TECHNICAL_ONLY", "provider_attested": False,
                        "evidence_type": value["provenance"]["evidence_type"], "audience_metric": None}
            if registry_path is not None:
                _no_sqlite_sidecars(registry_path)
            result = _seal(archive, proposal, technical, missing)
        return result  # only after native handle and final byte checks complete
    except Exception:
        return _seal(missing=["SOURCE_PIN_OR_NATIVE_EVIDENCE_INVALID"])


def recheck_review(raw, **source_arguments):
    """Receiver replay. A well-formed, rehashed sidecar alone is insufficient."""
    supplied = BQ06aLearningBriefReview(raw)
    current = review_learning(**source_arguments)
    if current.raw != supplied.raw:
        raise LearningBriefHold("CURRENT_OWNER_REPLAY_REQUIRED")
    return current
