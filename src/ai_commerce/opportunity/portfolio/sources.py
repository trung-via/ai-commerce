"""Typed selectors over native owners, never another ledger or a writer.

Pins are caller-retained expectations, not attestations. Each evaluation reopens
available native custody. Archives without custody remain explicitly unverified.
Only native read APIs see local store locations; no path is accepted in JSON.
"""
from __future__ import annotations

from dataclasses import dataclass

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.affiliate_commerce import AffiliateCommerceRun
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.learning import LearningAdvisory, LearningRegistry, SnapshotPin, read_snapshot
from ai_commerce.learning.export import export_advisory
from ai_commerce.learning.reporting import (
    ComparisonResult, PerformanceReport, build_performance_report, compare_reports,
)
from ai_commerce.operations.cost_ledger import CostEntry, CostLedger
from ai_commerce.operations.registry import ArtifactRegistry
from ai_commerce.operations.errors import MediaOperationError
from ai_commerce.opportunity.audience import AudienceLedger
from ai_commerce.opportunity.proposals.planning import propose_opportunities, validate_proposal
from ai_commerce.opportunity.signals import SignalLedger
from ai_commerce.opportunity.trends import TrendRule
from ai_commerce.settlement import CommercialReview, SettlementLedger, commercial_review

from .schemas import MAX_SOURCES, PortfolioError, bounded, digest, parse, require


@dataclass(frozen=True, slots=True)
class ProposalSource:
    raw: bytes
    sha256: str
    trend_series: tuple[bytes, ...] = ()
    signal_ledger: SignalLedger | None = None
    audience_ledger: AudienceLedger | None = None
    trend_rule: TrendRule | None = None


@dataclass(frozen=True, slots=True)
class SnapshotSource:
    ledger: AnalyticsLedger
    pin: SnapshotPin


@dataclass(frozen=True, slots=True)
class ReportSource:
    report: PerformanceReport
    sha256: str
    snapshots: tuple[SnapshotSource, ...] = ()
    aggregation: str = "SUM"


@dataclass(frozen=True, slots=True)
class AdvisorySource:
    advisory: LearningAdvisory
    sha256: str
    registry: LearningRegistry | None = None


@dataclass(frozen=True, slots=True)
class ComparisonSource:
    comparison: ComparisonResult
    sha256: str


@dataclass(frozen=True, slots=True)
class CostSource:
    entry: CostEntry
    sha256: str
    ledger: CostLedger | None = None


@dataclass(frozen=True, slots=True)
class ReviewSource:
    review: CommercialReview
    sha256: str
    commerce_run: AffiliateCommerceRun | None = None
    settlement_ledger: SettlementLedger | None = None
    cost_ledger: CostLedger | None = None


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    proposals: tuple[ProposalSource, ...] = ()
    reports: tuple[ReportSource, ...] = ()
    advisories: tuple[AdvisorySource, ...] = ()
    comparisons: tuple[ComparisonSource, ...] = ()
    costs: tuple[CostSource, ...] = ()
    reviews: tuple[ReviewSource, ...] = ()


@dataclass(frozen=True, slots=True)
class _Resolved:
    kind: str
    body: dict
    source_id: str
    sha256: str
    custody: str
    data_kind: str
    refs: tuple[tuple[str, str], ...]
    plans: tuple[dict, ...] = ()


def _pin(raw, expected):
    value = parse(raw, native=True)
    require(type(expected) is str and len(expected) == 64
            and sha256_bytes(raw) == expected, "SOURCE_HASH_PIN_MISMATCH")
    return value


def _store(ledger, cls):
    require(type(ledger) is cls and type(ledger.registry) is ArtifactRegistry,
            "NATIVE_STORE_REQUIRED")


def _refs(items):
    values = tuple(sorted(set(items)))
    require(len(values) <= 128, "NATIVE_REFERENCE_LIMIT")
    return values


def _kind(values):
    kinds = set(values)
    if not kinds:
        return "UNVERIFIED"
    if kinds == {"SYNTHETIC_FIXTURE"}:
        return "SYNTHETIC_FIXTURE"
    if "SYNTHETIC_FIXTURE" in kinds:
        return "MIXED"
    return "USER_SUPPLIED" if kinds <= {"USER_SUPPLIED", "USER_AUTHORED_MANUAL", "USER_PROVIDED_PLATFORM_EXPORT"} else "UNVERIFIED"


def _proposal(source):
    body = _pin(source.raw, source.sha256)
    validate_proposal(body)
    require(type(source.trend_series) is tuple and len(source.trend_series) <= 16, "PROPOSAL_SERIES_LIMIT")
    for ledger, cls in ((source.signal_ledger, SignalLedger), (source.audience_ledger, AudienceLedger)):
        require(ledger is None or type(ledger) is cls, "NATIVE_PROPOSAL_LEDGER_REQUIRED")
    require(source.trend_rule is None or type(source.trend_rule) is TrendRule, "NATIVE_TREND_RULE_REQUIRED")
    can_replay = ((body["trend"] is None or bool(source.trend_series) and source.signal_ledger is not None)
                  and (not body["audience_insights"] or source.audience_ledger is not None))
    if can_replay:
        rebuilt = propose_opportunities(
            trend_series=[parse(raw, native=True) for raw in source.trend_series],
            audience_insight_ids=[row["insight_id"] for row in body["audience_insights"]],
            signal_ledger=source.signal_ledger, audience_ledger=source.audience_ledger,
            as_of=body["as_of"], manual_idea=body["manual_input"]["text"] if body["manual_input"] else None,
            creative_overrides=body["creative_overrides"], trend_rule=source.trend_rule)
        require(body in rebuilt, "PROPOSAL_REPLAY_MISMATCH")
    refs = [(row["signal_id"], row["signal_sha256"]) for row in body["signal_evidence"]]
    refs += [(row["raw_ref"], row["raw_sha256"]) for row in body["signal_evidence"]]
    refs += [(row["insight_id"], row["record_sha256"]) for row in body["audience_insights"]]
    kinds = [row["data_kind"] for row in body["audience_insights"]]
    if body["manual_input"] is not None:
        kinds.append("USER_SUPPLIED")
    if can_replay and source.signal_ledger is not None:
        for import_id in sorted({row["import_id"] for row in body["signal_evidence"]}):
            original = source.signal_ledger.get(import_id)["import"]
            require(all(digest(original) == row["import_sha256"] for row in body["signal_evidence"]
                        if row["import_id"] == import_id), "PROPOSAL_ORIGIN_CHANGED_DURING_READ")
            kinds.append(original["source_snapshot"]["source_kind"])
    return _Resolved("PROPOSAL", body, body["proposal_id"], source.sha256,
                     "NATIVE_REPLAY" if can_replay else "UNVERIFIED", _kind(kinds), _refs(refs))


def _report(source):
    require(type(source.report) is PerformanceReport, "NATIVE_REPORT_REQUIRED")
    raw = source.report.to_bytes()
    _pin(raw, source.sha256)
    body = PerformanceReport.from_bytes(raw).to_dict()
    require(type(source.snapshots) is tuple and len(source.snapshots) <= 8, "SNAPSHOT_LIMIT")
    require(source.aggregation in {"SUM", "MEAN", "COUNT"}, "REPORT_AGGREGATION_REQUIRED")
    snapshots, seen = [], set()
    for selection in source.snapshots:
        require(type(selection) is SnapshotSource and type(selection.ledger) is AnalyticsLedger
                and type(selection.pin) is SnapshotPin, "NATIVE_SNAPSHOT_SELECTOR_REQUIRED")
        snapshot = read_snapshot(selection.ledger.path, pin=selection.pin)
        require(snapshot.snapshot_id not in seen, "DUPLICATE_SNAPSHOT_SOURCE")
        seen.add(snapshot.snapshot_id)
        # Raw private post IDs/URLs are never exported. A URL in normalized
        # observations is not accepted as an M06 input, even though A09 permits it.
        bounded(snapshot.to_dict(), native=True)
        snapshots.append(snapshot)
    if snapshots:
        rebuilt = build_performance_report(snapshots, metric=body["metric"], aggregation=source.aggregation,
                    confounders=body["confounders"], caveats=body["caveats"], as_of=body.get("as_of"))
        require(rebuilt.to_bytes() == raw, "REPORT_REPLAY_MISMATCH")
    refs = [(snapshot.snapshot_id, sha256_bytes(snapshot.to_bytes())) for snapshot in snapshots]
    refs += [(row["observation_id"], row["observation_sha256"]) for row in body["evidence_refs"]]
    refs += [("sha256:" + row["raw_sha256"], row["raw_sha256"]) for row in body["snapshot_refs"]]
    return _Resolved("REPORT", body, body["report_id"], source.sha256,
        "NATIVE_REPLAY" if snapshots else "UNVERIFIED",
        _kind(s.to_dict()["import"]["source_kind"] for s in snapshots), _refs(refs),
        tuple(s.to_dict()["import"]["plan"] for s in snapshots))


def _advisory(source):
    require(type(source.advisory) is LearningAdvisory, "NATIVE_ADVISORY_REQUIRED")
    raw = source.advisory.to_bytes()
    _pin(raw, source.sha256)
    body = LearningAdvisory.from_bytes(raw).to_dict()
    if source.registry is not None:
        require(type(source.registry) is LearningRegistry, "NATIVE_LEARNING_REGISTRY_REQUIRED")
        require(export_advisory(source.registry).to_bytes() == raw, "ADVISORY_REPLAY_MISMATCH")
    refs, kinds = [], []
    for row in body["entries"]:
        entry = row["entry"]
        refs.append((entry["revision"]["revision_id"], digest(entry["revision"])))
        refs.append(("sha256:" + row["event_hash"], row["event_hash"]))
        kinds.extend(fact["source"]["source_kind"] for fact in entry["observation_sources"])
    return _Resolved("ADVISORY", body, body["export_id"], source.sha256,
        "NATIVE_REPLAY" if source.registry is not None else "ARCHIVE_VALIDATED", _kind(kinds), _refs(refs))


def _cost(source):
    require(type(source.entry) is CostEntry, "NATIVE_COST_ENTRY_REQUIRED")
    body = source.entry.to_record()
    _pin(canonical_bytes(body), source.sha256)
    CostEntry.from_record(body)
    if source.ledger is not None:
        _store(source.ledger, CostLedger)
        require(source.ledger.get(source.entry.entry_id).to_record() == body, "COST_REPLAY_MISMATCH")
    # Native A15 record checksum is provenance even for an estimate with no
    # receipt. It is the owner's payload checksum, never a provider attestation.
    refs = [(source.entry.entry_id, body["record_sha256"])]
    if source.entry.evidence_sha256:
        refs.append(("sha256:" + source.entry.evidence_sha256, source.entry.evidence_sha256))
    return _Resolved("COST", body, source.entry.entry_id, source.sha256,
        "NATIVE_REPLAY" if source.ledger is not None else "UNVERIFIED",
        _kind([source.entry.evidence_origin]), _refs(refs))


def _review(source):
    require(type(source.review) is CommercialReview, "NATIVE_REVIEW_REQUIRED")
    raw = source.review.to_bytes()
    _pin(raw, source.sha256)
    body = CommercialReview.from_bytes(raw).to_dict()  # mandatory embedded native replay
    handles = (source.commerce_run, source.settlement_ledger, source.cost_ledger)
    require(all(h is None for h in handles) or all(h is not None for h in handles), "REVIEW_CUSTODY_INCOMPLETE")
    if source.commerce_run is not None:
        require(type(source.commerce_run) is AffiliateCommerceRun, "NATIVE_COMMERCE_RUN_REQUIRED")
        _store(source.settlement_ledger, SettlementLedger)
        _store(source.cost_ledger, CostLedger)
        rebuilt = commercial_review(commerce_run=source.commerce_run,
            settlement_ledger=source.settlement_ledger, cost_ledger=source.cost_ledger,
            cost_selections=body["sources"]["cost_selections"])
        require(rebuilt.to_bytes() == raw, "REVIEW_REPLAY_MISMATCH")
    refs = [("sha256:" + body["sources"][key]["raw_sha256"], body["sources"][key]["raw_sha256"])
            for key in ("commerce_run", "settlement_history")]
    refs += [(row["entry_id"], digest(row)) for row in body["sources"]["cost_records"]]
    kinds = [row["source_kind"] for row in body["orders"]]
    kinds.extend(row["evidence_origin"] for row in body["sources"]["cost_records"])
    return _Resolved("REVIEW", body, body["review_id"], source.sha256,
        "NATIVE_REPLAY" if source.commerce_run is not None else "ARCHIVE_VALIDATED", _kind(kinds), _refs(refs))


def resolve(bundle: EvidenceBundle) -> tuple[_Resolved, ...]:
    """No callbacks, arbitrary dictionaries, discovery, cached PASS or side effects."""
    require(type(bundle) is EvidenceBundle, "TYPED_EVIDENCE_BUNDLE_REQUIRED")
    groups = ((bundle.proposals, ProposalSource, _proposal), (bundle.reports, ReportSource, _report),
              (bundle.advisories, AdvisorySource, _advisory), (bundle.costs, CostSource, _cost),
              (bundle.reviews, ReviewSource, _review))
    total = len(bundle.comparisons) if type(bundle.comparisons) is tuple else MAX_SOURCES + 1
    for items, cls, _ in groups:
        require(type(items) is tuple and len(items) <= MAX_SOURCES, "TYPED_SOURCE_TUPLE_REQUIRED")
        require(all(type(item) is cls for item in items), "TYPED_NATIVE_SOURCE_REQUIRED")
        total += len(items)
    require(total <= MAX_SOURCES, "SOURCE_BUNDLE_LIMIT")
    try:
        result = [reader(source) for items, _, reader in groups for source in items]
        reports = {row.source_id: row for row in result if row.kind == "REPORT"}
        for source in bundle.comparisons:
            require(type(source) is ComparisonSource and type(source.comparison) is ComparisonResult,
                    "NATIVE_COMPARISON_REQUIRED")
            raw = source.comparison.to_bytes()
            _pin(raw, source.sha256)
            body = ComparisonResult.from_bytes(raw).to_dict()
            left = reports.get(body["left_report_ref"]["report_id"])
            right = reports.get(body["right_report_ref"]["report_id"])
            if left and right:
                rebuilt = compare_reports(PerformanceReport(left.body), PerformanceReport(right.body))
                require(rebuilt.to_bytes() == raw, "COMPARISON_REPLAY_MISMATCH")
            custody = "NATIVE_REPLAY" if left and right and left.custody == right.custody == "NATIVE_REPLAY" else "UNVERIFIED"
            result.append(_Resolved("COMPARISON", body, body["comparison_id"], source.sha256,
                custody, _kind([left.data_kind, right.data_kind]) if left and right else "UNVERIFIED",
                _refs([(row.source_id, row.sha256) for row in (left, right) if row])))
        identities, hashes, report_observations = set(), set(), set()
        observations, charged_receipts = set(), set()
        for row in result:
            identity = row.source_id
            require(identity not in identities and row.sha256 not in hashes, "DUPLICATE_SOURCE")
            identities.add(identity)
            hashes.add(row.sha256)
            if row.kind == "REPORT":
                facts = {ref["observation_id"] for ref in row.body["evidence_refs"]}
                require(not facts & report_observations, "DUPLICATE_REPORT_OBSERVATION")
                report_observations.update(facts)
            if row.kind == "COST":
                body = row.body
                key = tuple(body[k] for k in ("job_id", "attempt", "intent_digest", "provider", "status", "evidence_ref", "evidence_sha256"))
                require(key not in observations, "DUPLICATE_COST_OBSERVATION")
                observations.add(key)
                if body["status"] == "CHARGED":
                    require(body["evidence_sha256"] not in charged_receipts, "DUPLICATE_CHARGE_RECEIPT")
                    charged_receipts.add(body["evidence_sha256"])
        # A16 embeds A15 assertions. They are references, never additional costs.
        selected = {row.source_id: row.body for row in result if row.kind == "COST"}
        for row in result:
            if row.kind == "REVIEW":
                for cost in row.body["sources"]["cost_records"]:
                    require(cost["entry_id"] not in selected or selected[cost["entry_id"]] == cost,
                            "COST_VERSION_CONFLICT")
        return tuple(sorted(result, key=lambda row: (row.kind, row.source_id)))
    except PortfolioError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, MediaOperationError):
        raise PortfolioError("NATIVE_SOURCE_VERIFICATION_FAILED") from None
