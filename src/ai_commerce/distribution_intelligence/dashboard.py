"""L07 deterministic read-only projections of pinned native offline sources.

Selectors are typed projections, not new ledgers or provider adapters. Missing
sources, reported zero, pending orders and supplied settlements stay distinct.
This module performs no writes, including render/decoder scratch-file writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from ai_commerce.affiliate_attribution import AffiliateAttributionEvidence
from ai_commerce.affiliate_attribution.ledger import AttributionLedger
from ai_commerce.bridge import composition_to_render_plan
from ai_commerce.composition import CompositionStore
from ai_commerce.core.provenance import sha256_bytes
from ai_commerce.creative_scaling.feedback import load_proposal
from ai_commerce.creative_scaling.learning_bridge import consume_learning_advisory
from ai_commerce.creative_scaling.schemas import read_record as read_creative_record
from ai_commerce.creative_scaling.variants import load_variant_batch, source_pin
from ai_commerce.distribution.receipt import PublicationJournal
from ai_commerce.learning import LearningAdvisory, SnapshotPin, read_snapshot
from ai_commerce.media.files import hash_file
from ai_commerce.operations.cost_ledger import CostEntry, CostLedger
from ai_commerce.operations.registry import ArtifactRegistry
from ai_commerce.operations.retry_guard import RetryGuard
from ai_commerce.render.manifest import RenderManifest, manifest_ref, validate_binding
from ai_commerce.settlement.ledger import SettlementLedger, latest_records, validate_history

from .reconciliation import ReconciliationLedger, derive_states, inspect_sources
from .schemas import (DASHBOARD, IntelligenceError, bounded, detached, digest, parse,
    registry_bytes, require, scope_value, validate, within)


@dataclass(frozen=True)
class PublicationSelection:
    package_dir: object
    journal: PublicationJournal
    guard: RetryGuard
    publication_id: str
    intent_id: str
    attempt: int
    source_sha256: str
    observation_pins: tuple[dict, ...] = ()
    reconciliation_ledger: ReconciliationLedger | None = None
    reconciliation_id: str | None = None

    def arguments(self, scope):
        return {"package_dir": self.package_dir, "journal": self.journal, "guard": self.guard,
                "publication_id": self.publication_id, "intent_id": self.intent_id, "attempt": self.attempt,
                "scope": scope, "observation_pins": self.observation_pins}


@dataclass(frozen=True)
class CreativeSelection:
    registry: ArtifactRegistry
    batch_id: str
    batch_sha256: str
    source_id: str
    render_sha256: str | None = None


@dataclass(frozen=True)
class MetricSelection:
    definition_sha256: str
    stage: str


@dataclass(frozen=True)
class AnalyticsSelection:
    path: object
    pin: SnapshotPin
    metrics: tuple[MetricSelection, ...]


@dataclass(frozen=True)
class LearningSelection:
    advisory: LearningAdvisory
    export_id: str
    learning_key: str
    revision: int


@dataclass(frozen=True)
class FeedbackSelection:
    proposal_id: str
    revision: int
    record_sha256: str


@dataclass(frozen=True)
class AttributionSelection:
    ledger: AttributionLedger
    history_sha256: str
    evidence_id: str


@dataclass(frozen=True)
class SettlementSelection:
    ledger: SettlementLedger
    history_sha256: str


@dataclass(frozen=True)
class CostSelection:
    ledger: CostLedger
    # Exact A15 job/attempt/intent/provider identities; allocation is supplied.
    selections: tuple[dict, ...]


def _pin(component, ident, sha, provenance):
    return {"component": component, "id": ident, "sha256": sha, "provenance": provenance,
            "verification": "LOCAL_BYTES_ONLY_NOT_PROVIDER_VERIFIED"}


def _origin(kind):
    return "SYNTHETIC_FIXTURE" if kind == "SYNTHETIC_FIXTURE" else "USER_SUPPLIED"


def _context(scope, *, alias=False):
    value = {k: scope[k] for k in ("platform", "surface", "account_ref", "measurement_window", "timezone", "cohort")}
    if alias:
        require(scope["account_ref"] is not None, "LEARNING_ACCOUNT_REQUIRED")
        value["account_ref"] = {"namespace": "internal_alias", "id": "a11-account-" + digest(scope["account_ref"])}
    return value


def _creative(selection, scope, pins, limitations):
    require(type(selection) is CreativeSelection and type(selection.registry) is ArtifactRegistry, "CREATIVE_SELECTION_REQUIRED")
    batch = load_variant_batch(selection.registry, selection.batch_id)
    require(batch["record_sha256"] == selection.batch_sha256, "CREATIVE_BATCH_PIN_MISMATCH")
    pin, source = source_pin(batch, selection.source_id)
    require(pin["id"] == scope["creative_ref"] and pin["revision"] == scope["creative_revision"]
            and pin["sha256"] == scope["creative_sha256"] and batch["product_ref"] == scope["product_ref"],
            "CREATIVE_SCOPE_MISMATCH")
    if batch["scope"] is not None:
        require(batch["scope"] == {**_context(scope, alias=True), "market": scope["market"]}, "CREATIVE_CONTEXT_MISMATCH")
    else:
        limitations.add("CREATIVE_NATIVE_TARGET_SCOPE_UNAVAILABLE")
    pins.append(_pin("A17_1", batch["batch_id"], digest(batch), batch["provenance"]))
    value = {"created": "RECORDED", "batch_sha256": batch["record_sha256"], "creative_sha256": pin["sha256"],
        "mp4_generated": "NOT_AVAILABLE", "content_sha256": None, "generation_performed": False,
        "provenance": batch["provenance"], "feedback": None}
    render_ref = f"creative-scaling/renders/{selection.source_id[12:]}.json" if pin["kind"] == "VARIANT" else None
    exists = render_ref is not None and selection.registry.exists(render_ref)
    require(exists or selection.render_sha256 is None, "RENDER_SOURCE_MISSING")
    if exists:
        require(selection.render_sha256 is not None, "RENDER_PIN_REQUIRED")
        render = read_creative_record(selection.registry, render_ref)
        require(set(render) == {"schema_version", "source", "master_id", "composition", "job_id", "state", "manifest_ref",
            "manifest_sha256", "output", "full_decode_verified", "semantic_realization", "generation_performed",
            "publication_status", "record_sha256"}, "RENDER_FIELDS_INVALID")
        require(render["schema_version"] == "CreativeVariantRender.v1" and render["record_sha256"] == selection.render_sha256
                == digest({k: v for k, v in render.items() if k != "record_sha256"})
                and render["source"] == pin and render["master_id"] == batch["master"]["master_id"]
                and render["composition"] == source["composition"] and render["job_id"] == source["render"]["job_id"]
                and render["generation_performed"] is False and render["publication_status"] == "NOT_ATTEMPTED"
                and render["semantic_realization"] == "UNKNOWN", "RENDER_SOURCE_BINDING_MISMATCH")
        require(render["manifest_ref"] == manifest_ref(render["job_id"]), "RENDER_MANIFEST_REF_MISMATCH")
        receipt = RenderManifest.from_dict(parse(registry_bytes(selection.registry, render["manifest_ref"], limit=1024 * 1024)))
        native = receipt.to_dict()
        binding = parse(registry_bytes(selection.registry, f"render-jobs/{render['job_id']}/request.json", limit=1024 * 1024))
        validate_binding(binding)
        composition = CompositionStore(selection.registry.root).load(source["composition"]["id"], source["composition"]["revision"])
        plan = composition_to_render_plan(composition, composition_ref=source["composition"]["ref"])
        require(native["record_sha256"] == render["manifest_sha256"] and native["job_id"] == render["job_id"]
                and native["request_digest"] == binding["record_sha256"] and native["plan"] == binding["plan"] == plan.to_dict()
                and native["encoder"] == binding["encoder"] and native["created_at"] == binding["created_at"]
                and native["state"] == render["state"] and native["output"] == render["output"], "NATIVE_RENDER_MISMATCH")
        diag = native["diagnostics"]
        if diag is not None:
            raw = registry_bytes(selection.registry, diag["ref"], limit=1024 * 1024)
            require((sha256_bytes(raw), len(raw)) == (diag["sha256"], diag["size_bytes"]), "RENDER_DIAGNOSTIC_HASH_MISMATCH")
        if receipt.state == "SUCCEEDED":
            require(render["full_decode_verified"] is True, "RENDER_DECODE_RECEIPT_REQUIRED")
            for artifact in (receipt.output, receipt.thumbnail):
                path = selection.registry.safe_path(artifact["ref"])
                require(path.is_file() and path.stat().st_size <= 256 * 1024 * 1024
                        and hash_file(path) == (artifact["sha256"], artifact["size_bytes"]), "RENDER_ARTIFACT_HASH_MISMATCH")
            require(scope["content_sha256"] == receipt.output["sha256"], "CREATIVE_CONTENT_SHA_MISMATCH")
            value.update(mp4_generated="RECORDED_LOCAL", content_sha256=receipt.output["sha256"])
            limitations.add("MP4_HASH_REOPENED_DECODE_NOT_RERUN")
        else:
            require(render["full_decode_verified"] is False, "RENDER_FALSE_DECODE_CLAIM")
            value["mp4_generated"] = "UNKNOWN"
        pins.append(_pin("A17_1", render["job_id"], digest(render), batch["provenance"]))
    limitations.add("CREATIVE_SEMANTIC_REALIZATION_UNKNOWN")
    return value, batch


def _publication(selection, scope, pins, limitations):
    require(type(selection) is PublicationSelection, "PUBLICATION_SELECTION_REQUIRED")
    sources = inspect_sources(**selection.arguments(scope))
    require(sources["source_sha256"] == selection.source_sha256, "PUBLICATION_SOURCE_PIN_MISMATCH")
    require((selection.reconciliation_id is None) == (selection.reconciliation_ledger is None), "RECONCILIATION_SELECTION_INCOMPLETE")
    _, state, _ = derive_states(sources)
    if selection.reconciliation_id is not None:
        require(type(selection.reconciliation_ledger) is ReconciliationLedger, "RECONCILIATION_LEDGER_REQUIRED")
        record = selection.reconciliation_ledger.get(selection.reconciliation_id).to_dict()
        require(record["sources"] == sources, "STALE_RECONCILIATION_SNAPSHOT")
        state = record["new_state"]
    native = sources["publication"]
    pins.append(_pin("A08_A15", native["publication_id"], sources["source_sha256"], "UNVERIFIED"))
    for observation in sources["observations"]:
        pins.append(_pin("A15", observation["document"]["observation_id"], observation["pin"]["sha256"],
                         observation["document"]["evidence_origin"]))
    limitations.update({"A08_RECEIPTS_NOT_PROVIDER_ATTESTED", "A08_CHANNEL_AND_CREATIVE_ID_NOT_RECORDED",
                        "A15_GENERIC_RETRY_IS_NOT_POST_PERMISSION"})
    return {"reported_state": native["state"], "reported_visibility": native["reported_visibility"],
        "public_visibility": "UNKNOWN", "provider_post_id": native["provider_post_id"], "provenance": "USER_SUPPLIED",
        "reconciliation_status": state["status"], "decision": state["decision"]}


def _audience(selection, scope, publication, pins, limitations):
    require(type(selection) is AnalyticsSelection and type(selection.pin) is SnapshotPin
            and type(selection.metrics) is tuple and 1 <= len(selection.metrics) <= 100, "ANALYTICS_SELECTION_REQUIRED")
    roles = {}
    for metric in selection.metrics:
        require(type(metric) is MetricSelection and metric.stage in {"REACH", "ENGAGEMENT"}
                and metric.definition_sha256 not in roles, "METRIC_ROLE_AMBIGUOUS")
        roles[metric.definition_sha256] = metric.stage
    snapshot = read_snapshot(selection.path, pin=selection.pin)
    value = snapshot.to_dict()
    plan = value["import"]["plan"]
    require({k: plan[k] for k in _context(scope)} == _context(scope), "AUDIENCE_CONTEXT_MISMATCH")
    require(len(value["observations"]) <= 1024, "AUDIENCE_PROJECTION_LIMIT")
    require(set(roles) == {digest(m) for m in plan["metric_definitions"]}, "METRIC_DEFINITION_SELECTION_MISMATCH")
    rows = []
    for item in value["observations"]:
        if publication["provider_post_id"] is not None:
            require(item["provider_post_id"] == publication["provider_post_id"], "AUDIENCE_POST_ID_MISMATCH")
        metric_sha = digest(item["metric"])
        rows.append({"observation_id": item["observation_id"], "source_sha256": digest(item),
            "native_context": {k: item[k] for k in _context(scope)}, "provider_post_id": item["provider_post_id"],
            "stage": roles[metric_sha], "role_basis": "EXPLICIT_SELECTION_BY_DEFINITION_HASH",
            "metric": item["metric"], "metric_sha256": metric_sha, "status": item["status"], "value": item["value"],
            "denominator_value": None, "attribution_tier": plan["attribution_tier"],
            "provenance": _origin(item["source"]["source_kind"]),
            "limitations": ["A09_DENOMINATOR_VALUE_NOT_RECORDED", "A09_MARKET_PRODUCT_CREATIVE_NOT_RECORDED"]})
    pins.append(_pin("A09", snapshot.snapshot_id, sha256_bytes(snapshot.to_bytes()), _origin(value["import"]["source_kind"])))
    limitations.update({"A09_MARKET_PRODUCT_CREATIVE_NOT_RECORDED", "A09_METRICS_NOT_PROVIDER_VERIFIED"})
    return {"status": "RECORDED", "rows": rows}


def _learning(selection, creative, batch, scope, pins, limitations):
    require(type(selection) is LearningSelection and type(selection.advisory) is LearningAdvisory
            and creative is not None, "LEARNING_AND_CREATIVE_SELECTION_REQUIRED")
    result = consume_learning_advisory(selection.advisory, registry=creative.registry, batch=batch,
        source_id=creative.source_id, expected_export_id=selection.export_id, learning_key=selection.learning_key,
        expected_revision=selection.revision, scope={**_context(scope, alias=True), "market": scope["market"]}, opt_in=True)
    context = result["context"]
    require(len(context["history"]) <= 128, "LEARNING_HISTORY_LIMIT")
    history = []
    for entry in context["history"]:
        record, revision = entry["record"], entry["revision"]
        history.append({"revision": revision["revision"], "revision_id": revision["revision_id"],
            "entry_sha256": entry["entry_sha256"], "uncertainty": record["uncertainty"], "status": record["status"],
            "confounders": [{"code": c["code"], "description": c["description"], "status": c["status"],
                "source_sha256": digest(c)} for c in record["confounders"]],
            "counterevidence_ids": [r["evidence_id"] for r in record["counterevidence_refs"]]})
    observations = [{"evidence_id": o["evidence"]["evidence_id"], "revision": o["revision"], "role": o["evidence"]["role"],
        "status": o["status"], "value": o["value"], "denominator": o["denominator"], "provenance": o["provenance"],
        "observation_sha256": o["source_fact"]["observation_ref"]["observation_sha256"]} for o in context["observations"]]
    provenance = "SYNTHETIC_FIXTURE" if any(o["provenance"] == "SYNTHETIC_FIXTURE" for o in observations) else "USER_SUPPLIED"
    pins.append(_pin("A11", context["export_id"], sha256_bytes(selection.advisory.to_bytes()), provenance))
    limitations.update({"A11_ARCHIVED_FACTS_ONLY_NO_FRESH_A09_REPLAY", "A11_MARKET_NOT_RECORDED",
                        "A11_HYPOTHESIS_IS_NOT_CREATIVE_ATTRIBUTION"})
    return {"status": "ADVISORY_ONLY", "export_id": context["export_id"], "metric": context["metric"],
            "history": history, "observations": observations, "causal_lift": None}


def _feedback(selection, creative, batch, scope, pins):
    require(type(selection) is FeedbackSelection and creative is not None, "FEEDBACK_AND_CREATIVE_SELECTION_REQUIRED")
    value = load_proposal(creative.registry, selection.proposal_id, selection.revision)
    require(value["record_sha256"] == selection.record_sha256 and value["batch_sha256"] == batch["record_sha256"]
            and value["source"]["id"] == creative.source_id
            and value["scope"] == {**_context(scope, alias=True), "market": scope["market"]}, "FEEDBACK_SOURCE_PIN_MISMATCH")
    pins.append(_pin("E06", value["proposal_id"], digest(value), "UNVERIFIED"))
    return {"proposal_id": value["proposal_id"], "revision": value["revision"], "record_sha256": value["record_sha256"],
        "source_brief_sha256": value["source_brief"]["sha256"], "next_brief_sha256": value["next_brief"]["sha256"],
        "adoption_decision": value["adoption"]["decision"], "after_status": "UNKNOWN", "causal_lift": None}


def _commercial_context(native, scope, *, attribution=False):
    fields = ("provider_ref", "platform", "account_ref", "market", "surface", "measurement_window", "timezone")
    require(all(native[k] == scope[k] for k in fields), "COMMERCE_CONTEXT_MISMATCH")
    if attribution:
        require(native["cohort_ref"] == scope["cohort"] and native["app_ref"] ==
                (scope["app_ref"]["id"] if scope["app_ref"] else None), "ATTRIBUTION_CONTEXT_MISMATCH")
    else:
        require(native["app_ref"] == scope["app_ref"] and native["channel"] == scope["channel"]
                and native["product_id"] == scope["product_ref"] and native["listing_id"] == scope["listing_ref"]
                and native["variant_id"] == scope["variant_ref"] and native["link_ref"] == scope["link_ref"]
                and native["link_url_sha256"] == scope["link_url_sha256"]
                and native["creative_ref"] == scope["creative_ref"] and native["creative_sha256"] == scope["creative_sha256"],
                "SETTLEMENT_SCOPE_MISMATCH")


def _affiliate(selection, scope, pins, limitations):
    require(type(selection) is AttributionSelection and type(selection.ledger) is AttributionLedger, "ATTRIBUTION_SELECTION_REQUIRED")
    raw = selection.ledger.to_bytes()
    require(len(raw) <= 16 * 1024 * 1024 and sha256_bytes(raw) == selection.history_sha256, "ATTRIBUTION_HISTORY_PIN_MISMATCH")
    ledger = AttributionLedger.from_bytes(raw)
    active = {row.evidence_id: row for row in ledger.active()}
    require(selection.evidence_id in active, "STALE_ATTRIBUTION_SELECTION")
    evidence = AffiliateAttributionEvidence.from_bytes(active[selection.evidence_id].to_bytes(), expected_id=selection.evidence_id)
    value = evidence.to_dict()
    _commercial_context(value["scope"], scope, attribution=True)
    provenance = _origin(value["source"]["document"]["source_kind"])
    events = value["source"]["document"]["events"]
    maps = {e["link_ref"]: e for e in events if e["kind"] == "LINK_CREATIVE"}
    for event in events:
        if event.get("creative_ref") is not None:
            require(event["creative_ref"] == scope["creative_ref"] and event["creative_sha256"] == scope["creative_sha256"],
                    "ATTRIBUTION_CREATIVE_MISMATCH")
        if event.get("link_ref") is not None and scope["link_ref"] is not None:
            require(event["link_ref"] == scope["link_ref"], "ATTRIBUTION_LINK_MISMATCH")
    clicks = []
    for event in events:
        if event["kind"] != "CLICK_LINK":
            continue
        link = maps.get(event["link_ref"])
        clicks.append({"event_ref": event["event_ref"], "click_ref": event["click_ref"], "link_ref": event["link_ref"],
            "observed_at": event["observed_at"], "creative_ref": link["creative_ref"] if link else None,
            "creative_sha256": link["creative_sha256"] if link else None, "provenance": provenance})
    orders = [{"event_ref": r["event_ref"], "order_ref": r["order_ref"], "creative_ref": r["creative_ref"],
        "creative_sha256": r["creative_sha256"], "reported_count": r["reported_count"],
        "reported_order_status": r["reported_order_status"], "attribution_tier": r["tier"],
        "attribution_scope": r["attribution_scope"], "reason_code": r["reason_code"],
        "support_event_refs": r["support_event_refs"], "provenance": provenance} for r in value["results"]]
    pins.append(_pin("A14", evidence.evidence_id, sha256_bytes(evidence.to_bytes()), provenance))
    limitations.update({"A14_ORDER_AND_PROVIDER_REPORTS_ARE_SUPPLIED", "A14_PRODUCT_CHANNEL_NOT_RECORDED"})
    return {"status": "RECORDED", "clicks": clicks, "orders": orders, "actual_orders_verified": False}, value


def _settlement(selection, scope, attribution, pins, limitations):
    require(type(selection) is SettlementSelection and type(selection.ledger) is SettlementLedger, "SETTLEMENT_SELECTION_REQUIRED")
    raw = selection.ledger.to_bytes()
    require(len(raw) <= 40 * 1024 * 1024 and sha256_bytes(raw) == selection.history_sha256, "SETTLEMENT_HISTORY_PIN_MISMATCH")
    records = validate_history(raw)
    rows = []
    for value in latest_records(records):
        source = value["source"]["document"]
        _commercial_context(source["scope"], scope)
        if source["attribution"] is not None:
            require(attribution is not None and source["attribution"]["evidence_id"] == attribution["evidence_id"]
                    and value["attribution_snapshot"]["raw_sha256"] == sha256_bytes(bounded(attribution)), "SETTLEMENT_ATTRIBUTION_PIN_MISMATCH")
        rows.append({"evidence_id": value["evidence_id"], "order_ref": source["scope"]["order_ref"],
            "reported_status": source["reported_status"], "currency": source["scope"]["currency"],
            "attribution_tier": value["attribution_tier"], "commission_basis": source["commission_basis"],
            **{k: source["amounts"][k] for k in ("settled_commission", "credited_commission", "refund_gross", "reversed_commission")},
            "provenance": source["source_kind"]})
        pins.append(_pin("A16", value["evidence_id"], digest(value), source["source_kind"]))
    # Pin even an explicitly selected empty history; empty still isn't zero sales.
    pins.append(_pin("A16", "settlement-history", sha256_bytes(raw), "UNVERIFIED"))
    limitations.update({"A16_SUPPLIED_SETTLEMENT_NOT_PROVIDER_ATTESTED", "A16_COHORT_NOT_RECORDED"})
    return {"status": "RECORDED" if rows else "NOT_AVAILABLE", "rows": rows,
            "actual_settlement": "UNKNOWN", "settlement_verified": False}


def _cost_totals(rows):
    totals = {}
    with localcontext() as context:
        context.prec = 64
        for row in rows:
            if row["status"] not in {"ESTIMATED", "CHARGED"}:
                continue
            bucket = totals.setdefault(row["currency"], {"estimated": None, "supplied_charged": None, "synthetic_charged": None})
            key = "estimated" if row["status"] == "ESTIMATED" else (
                "synthetic_charged" if row["provenance"] == "SYNTHETIC_FIXTURE" else "supplied_charged")
            bucket[key] = (bucket[key] or Decimal(0)) + Decimal(row["amount"])
        return [{"currency": currency, **{k: None if v is None else format(v, "f") for k, v in bucket.items()},
                 "completeness": "SELECTED_ENTRIES_ONLY"} for currency, bucket in sorted(totals.items())]


def _costs(selection, scope, pins, limitations):
    require(type(selection) is CostSelection and type(selection.ledger) is CostLedger
            and type(selection.ledger.registry) is ArtifactRegistry and type(selection.selections) is tuple
            and len(selection.selections) <= 128, "COST_SELECTION_REQUIRED")
    rows, ids, observations, charge_hashes, charge_refs = [], set(), set(), set(), set()
    for selected in selection.selections:
        require(type(selected) is dict and set(selected) == {"entry_id", "job_id", "attempt", "intent_digest", "provider"}, "COST_SELECTION_FIELDS_INVALID")
        raw = registry_bytes(selection.ledger.registry, selection.ledger.ref(selected["entry_id"]), limit=65536)
        record = parse(raw, limit=65536)
        entry = CostEntry.from_record(record)
        require(bounded(entry.to_record()) == bounded(record) and all(getattr(entry, k) == v for k, v in selected.items()),
                "COST_SOURCE_IDENTITY_MISMATCH")
        require(type(entry.attempt) is int and entry.entry_id not in ids, "DUPLICATE_COST_ENTRY")
        ids.add(entry.entry_id)
        identity = (entry.job_id, entry.attempt, entry.intent_digest, entry.provider, entry.status,
                    entry.evidence_sha256)
        require(identity not in observations, "DUPLICATE_COST_OBSERVATION")
        observations.add(identity)
        if entry.evidence_ref is not None:
            proof = registry_bytes(selection.ledger.registry, entry.evidence_ref, limit=1024 * 1024)
            require(sha256_bytes(proof) == entry.evidence_sha256, "COST_EVIDENCE_HASH_MISMATCH")
        if entry.status == "CHARGED":
            require(entry.evidence_sha256 not in charge_hashes and (entry.provider, entry.evidence_ref) not in charge_refs,
                    "DUPLICATE_COST_RECEIPT")
            charge_hashes.add(entry.evidence_sha256)
            charge_refs.add((entry.provider, entry.evidence_ref))
        within(entry.observed_at, scope["measurement_window"])
        rows.append({"entry_id": entry.entry_id, "job_id": entry.job_id, "attempt": entry.attempt,
            "intent_digest": entry.intent_digest, "provider": entry.provider, "model": entry.model,
            "status": entry.status, "amount": entry.amount, "currency": entry.currency,
            "evidence_sha256": entry.evidence_sha256, "provenance": entry.evidence_origin,
            "observed_at": entry.observed_at, "allocation_basis": "USER_SUPPLIED_JOB_SELECTION"})
        pins.append(_pin("A15", entry.entry_id, sha256_bytes(raw), entry.evidence_origin))
    # Native duplicate checks also run; its sentinel-zero categories are unused.
    selection.ledger.summarize(tuple(sorted(ids)))
    limitations.add("A15_COST_ACCOUNT_CREATIVE_CHANNEL_NOT_RECORDED")
    if any(row["status"] in {"PENDING", "UNKNOWN"} for row in rows):
        limitations.add("SELECTED_COSTS_INCOMPLETE")
    return {"status": "RECORDED" if rows else "NOT_AVAILABLE", "rows": sorted(rows, key=lambda r: r["entry_id"]),
            "by_currency": _cost_totals(rows), "provider_charge_verified": False}


@dataclass(frozen=True, slots=True, init=False)
class DecisionDashboardSnapshot:
    """An archived projection. Rebuild from selectors to check current sources."""
    _raw: bytes

    @classmethod
    def from_bytes(cls, raw, *, expected_id=None):
        value = validate(DASHBOARD, parse(raw))
        scope_value(value["scope"])
        body = {k: v for k, v in value.items() if k not in {"record_sha256", "snapshot_id"}}
        sha = digest(body)
        require(value["record_sha256"] == sha and value["snapshot_id"] == "a17-dashboard-" + sha, "DASHBOARD_DIGEST_MISMATCH")
        require(expected_id is None or value["snapshot_id"] == expected_id, "DASHBOARD_PIN_MISMATCH")
        require(value["costs"]["by_currency"] == _cost_totals(value["costs"]["rows"]), "DASHBOARD_COST_TOTAL_MISMATCH")
        require(len({(p["component"], p["id"]) for p in value["source_pins"]}) == len(value["source_pins"]), "DUPLICATE_DASHBOARD_SOURCE")
        for row in value["audience"]["rows"]:
            require(row["metric_sha256"] == digest(row["metric"]), "DASHBOARD_METRIC_DIGEST_MISMATCH")
        result = object.__new__(cls)
        object.__setattr__(result, "_raw", bounded(value))
        return result

    def to_bytes(self):
        return self._raw

    def to_dict(self):
        return parse(self._raw)

    @property
    def snapshot_id(self):
        return self.to_dict()["snapshot_id"]


def build_dashboard(*, scope, creative=None, publication=None, analytics=None, learning=None,
                    feedback=None, attribution=None, settlement=None, costs=None):
    """Read selected native evidence only. No output directory or writer exists.

    Returned bytes may be explicitly retained by the caller with ArtifactRegistry.
    Scope is a requested filter, never evidence for a missing native source field.
    """
    scope = scope_value(scope)
    pins, limitations = [], {"OFFLINE_SOURCES_NOT_PROVIDER_ATTESTED", "SCOPE_IS_SELECTION_NOT_MISSING_FIELD_EVIDENCE"}
    body = {"schema_version": DASHBOARD, "scope": scope, "source_pins": pins,
        "creative": {"created": "UNKNOWN", "batch_sha256": None, "creative_sha256": None, "mp4_generated": "UNKNOWN",
                     "content_sha256": None, "generation_performed": False, "provenance": "UNVERIFIED", "feedback": None},
        "publication": {"reported_state": None, "reported_visibility": None, "public_visibility": "UNKNOWN",
            "provider_post_id": None, "provenance": "UNVERIFIED", "reconciliation_status": None, "decision": "RECONCILE_REQUIRED"},
        "audience": {"status": "NOT_AVAILABLE", "rows": []},
        "learning": {"status": "NOT_AVAILABLE", "export_id": None, "metric": None, "history": [], "observations": [], "causal_lift": None},
        "affiliate": {"status": "NOT_AVAILABLE", "clicks": [], "orders": [], "actual_orders_verified": False},
        "settlement": {"status": "NOT_AVAILABLE", "rows": [], "actual_settlement": "UNKNOWN", "settlement_verified": False},
        "costs": {"status": "NOT_AVAILABLE", "rows": [], "by_currency": [], "provider_charge_verified": False},
        "limitations": [], "realized_profit": None, "causal_uplift": None, "provider_live": "UNKNOWN",
        "read_only": True, "network_calls_performed": False}
    batch, attr = None, None
    try:
        if creative is not None:
            body["creative"], batch = _creative(creative, scope, pins, limitations)
        if publication is not None:
            body["publication"] = _publication(publication, scope, pins, limitations)
        if analytics is not None:
            body["audience"] = _audience(analytics, scope, body["publication"], pins, limitations)
        if learning is not None:
            body["learning"] = _learning(learning, creative, batch, scope, pins, limitations)
        if feedback is not None:
            body["creative"]["feedback"] = _feedback(feedback, creative, batch, scope, pins)
        if attribution is not None:
            body["affiliate"], attr = _affiliate(attribution, scope, pins, limitations)
        if settlement is not None:
            body["settlement"] = _settlement(settlement, scope, attr, pins, limitations)
        if costs is not None:
            body["costs"] = _costs(costs, scope, pins, limitations)
    except IntelligenceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        raise IntelligenceError("NATIVE_DASHBOARD_SOURCE_UNPROVEN") from exc
    for name, selection in (("creative", creative), ("publication", publication), ("audience", analytics),
                            ("learning", learning), ("affiliate", attribution), ("settlement", settlement), ("costs", costs)):
        if selection is None:
            limitations.add(name.upper() + "_SOURCE_NOT_AVAILABLE")
    body["source_pins"] = sorted(pins, key=lambda p: (p["component"], p["id"]))
    body["limitations"] = sorted(limitations)
    sha = digest(body)
    return DecisionDashboardSnapshot.from_bytes(bounded({**body, "record_sha256": sha, "snapshot_id": "a17-dashboard-" + sha}))


def verify_dashboard(snapshot, **selection):
    """Fresh native-source replay, separate from archived JSON/hash validation."""
    require(type(snapshot) is DecisionDashboardSnapshot, "NATIVE_DASHBOARD_REQUIRED")
    require(build_dashboard(**selection).to_bytes() == snapshot.to_bytes(), "DASHBOARD_SOURCE_REPLAY_MISMATCH")
    return snapshot
