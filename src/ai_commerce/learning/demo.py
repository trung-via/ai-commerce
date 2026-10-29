"""Deterministic synthetic A11 E2E; creates only a new demonstration directory."""
from __future__ import annotations

from pathlib import Path

from ai_commerce.analytics import AnalyticsLedger
from ai_commerce.analytics.files import local_path
from ai_commerce.analytics.schemas import VERIFICATION
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from .core import LearningError, require, seal_contract
from .export import LearningAdvisory, export_advisory, write_advisory
from .reader import SnapshotPin, read_snapshot
from .registry import LearningRegistry, hypothesis_context
from .reporting import build_performance_report, compare_reports


def _source(directory, name, values, *, cohort="synthetic-cohort", window=None, denominator=None):
    plan = {
        "schema_version": "MeasurementPlan.v1", "plan_id": "synthetic-" + name,
        "campaign_ref": "synthetic-campaign", "primary_metric": "plays", "denominator": denominator,
        "metric_definition_version": "synthetic-1", "platform": "TIKTOK", "surface": "VIDEO",
        "account_ref": {"namespace": "internal_alias", "id": "synthetic-account"},
        "measurement_window": window or ["2026-10-03T00:00:00+07:00", "2026-10-05T00:00:00+07:00"],
        "timezone": "UTC+07:00", "cohort": cohort, "source_mode": "MANUAL_IMPORT",
        "attribution_tier": "NOT_AVAILABLE", "missingness_rule": "Missing is null, never zero",
        "source_status": VERIFICATION, "metric_definitions": [{
            "schema_version": "MetricDefinition.v1", "metric_key": "plays", "provider_name": "Synthetic plays",
            "definition_version": "synthetic-1", "platform": "TIKTOK", "unit": "count" if denominator is None else "ratio",
            "definition": "Synthetic count for an offline example", "denominator": denominator, "cohort": cohort,
            "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE", "source_status": VERIFICATION,
        }],
    }
    raw = canonical_bytes({"schema_version": "ManualAnalyticsRows.v1", "rows": [
        {"provider_post_id": "synthetic-post-" + str(index), "public_url": None, "metric_key": "plays",
         "provider_name": "Synthetic plays", "definition_version": "synthetic-1",
         "status": "NOT_AVAILABLE" if value is None else "AVAILABLE", "value": value}
        for index, value in enumerate(values)]})
    source = directory / (name + "-rows.json")
    source.write_bytes(raw)
    # Explicit demo bootstrap of a fresh synthetic A09 ledger, before any A11 reads.
    ledger = AnalyticsLedger(directory / (name + "-a09.sqlite"))
    imported = ledger.import_file(source, plan=plan, profile="tiktok.manual.v1", file_format="json",
                                  source_kind="SYNTHETIC_FIXTURE")
    record = imported["import"]
    pin = SnapshotPin(record["import_id"], record["context_sha256"], record["raw_sha256"],
                      tuple(item["observation_id"] for item in imported["observations"]))
    snapshot = read_snapshot(ledger.path, pin=pin)
    for suffix, value in (("plan", plan), ("pin", pin.to_dict()), ("snapshot", snapshot.to_dict())):
        (directory / (name + "-" + suffix + ".json")).write_bytes(canonical_bytes(value))
    return snapshot, Path(ledger.path)


def _packet(snapshot, report, *, previous=None):
    hypothesis = seal_contract("LearningHypothesis", {
        "schema_version": "LearningHypothesis.v1", "source_status": VERIFICATION,
        **hypothesis_context(snapshot), "statement": "This synthetic opener may be associated with a positive count",
        "creative_ref": {"creative_id": "synthetic-opener", "version": "1", "content_sha256": "a" * 64},
        "expected_outcome": "An available count above zero", "uncertainty": "No controlled assignment",
        "confounders": [], "inference": "HYPOTHESIS_ONLY",
    })
    number = 1 if previous is None else previous["revision"]["revision"] + 1
    evidence = []
    for index, (role, state) in enumerate([("SUPPORTING", "AVAILABLE"), ("COUNTEREVIDENCE", "AVAILABLE"),
                                          ("INCONCLUSIVE", "NOT_AVAILABLE")][:number]):
        observation = snapshot.to_dict()["observations"][index]
        evidence.append(seal_contract("LearningEvidence", {
            "schema_version": "LearningEvidence.v1", "source_status": VERIFICATION,
            "hypothesis_ref": {"hypothesis_id": hypothesis["hypothesis_id"]},
            "observation_ref": snapshot.observation_ref(observation["observation_id"]),
            "report_ref": report.report_ref(), "role": role, "evidence_state": state,
            "observed_outcome": "Synthetic observation; zero and missing are distinct",
            "confounders": [], "limitations": ["No causal attribution or provider verification"],
            "inference": "DESCRIPTIVE_ONLY",
        }))
    record = seal_contract("LearningRecord", {
        "schema_version": "LearningRecord.v1", "source_status": VERIFICATION, "learning_key": "synthetic-opener",
        "hypothesis_ref": {"hypothesis_id": hypothesis["hypothesis_id"]},
        "evidence_refs": [{"evidence_id": item["evidence_id"]} for item in evidence if item["role"] != "COUNTEREVIDENCE"],
        "counterevidence_refs": [{"evidence_id": item["evidence_id"]} for item in evidence if item["role"] == "COUNTEREVIDENCE"],
        "status": ["SUPPORTED", "MIXED", "INCONCLUSIVE"][number - 1],
        "uncertainty": "Synthetic, sparse and uncontrolled; creative choices remain Human decisions",
        "confounders": [{"code": "UNCONTROLLED", "description": "Uncontrolled synthetic example",
                         "status": "DECLARED", "evidence_refs": []}], "inference": "ADVISORY_ONLY",
    })
    revision = seal_contract("LearningRevision", {
        "schema_version": "LearningRevision.v1", "source_status": VERIFICATION, "learning_key": "synthetic-opener",
        "revision": number, "previous_revision_ref": None if previous is None else {
            "revision_id": previous["revision"]["revision_id"], "revision": number - 1},
        "record_ref": {"record_id": record["record_id"]}, "operation": "CREATE" if number == 1 else "UPDATE",
        "reason": "Human authored synthetic example", "actor_ref": "synthetic-human",
        "recorded_at": "2026-10-30T0" + str(number) + ":00:00Z",
    })
    return {"hypothesis": hypothesis, "evidence": evidence, "record": record, "revision": revision}


def run_demo(output) -> dict:
    directory = local_path(output)
    require(not directory.exists(), "DEMO_OUTPUT_ALREADY_EXISTS")
    directory.mkdir()
    snapshot, upstream = _source(directory, "observations", [12, 0, None])
    before = sha256_bytes(upstream.read_bytes())
    report = build_performance_report(snapshot)
    (directory / "observations-report.json").write_bytes(report.to_bytes())
    registry = LearningRegistry(directory / "learning.sqlite")
    previous = None
    for number in range(1, 4):
        packet = _packet(snapshot, report, previous=previous)
        (directory / ("revision-" + str(number) + ".json")).write_bytes(canonical_bytes(packet))
        previous = registry.append(**packet, snapshots=[snapshot], reports=[report]).to_dict()
    advisory = export_advisory(registry)
    write_advisory(directory / "advisory.json", advisory)
    reloaded = LearningAdvisory.from_bytes((directory / "advisory.json").read_bytes(), expected_id=advisory.export_id)
    require(reloaded.to_bytes() == advisory.to_bytes(), "DEMO_RELOAD_MISMATCH")
    require(sha256_bytes(upstream.read_bytes()) == before, "DEMO_A09_CHANGED")
    zero, _ = _source(directory, "zero", [0], window=["2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"])
    missing, _ = _source(directory, "missing", [None])
    other, _ = _source(directory, "other-cohort", [99], cohort="another-synthetic-cohort")
    zero_report, missing_report = build_performance_report(zero), build_performance_report(missing)
    insufficient = build_performance_report(snapshot, denominator_value=0)
    incompatible = compare_reports(report, build_performance_report(other))
    relative = compare_reports(zero_report, report)
    for name, value in (("zero-report", zero_report), ("missing-report", missing_report),
                        ("insufficient-report", insufficient), ("incompatible-comparison", incompatible),
                        ("zero-baseline-comparison", relative)):
        (directory / (name + ".json")).write_bytes(value.to_bytes())
    tampered = advisory.to_dict()
    tampered["entries"][-1]["entry"]["record"]["uncertainty"] = "Modified archive"
    try:
        LearningAdvisory(tampered)
    except LearningError as exc:
        tamper_code = exc.code
    else:
        raise LearningError("DEMO_TAMPER_ACCEPTED")
    summary = {"schema_version": "A11SyntheticDemo.v1", "source_kind": "SYNTHETIC_FIXTURE",
        "source_status": VERIFICATION, "snapshot_id": snapshot.snapshot_id, "report_id": report.report_id,
        "export_id": advisory.export_id, "event_count": 3,
        "history_statuses": [entry.to_dict()["record"]["status"] for entry in registry.inspect()],
        "states": {"zero": zero_report.measurement, "missing": missing_report.measurement,
                   "insufficient": insufficient.measurement, "incompatible": incompatible.status,
                   "zero_baseline_relative": relative.to_dict()["relative_change"]},
        "a09_unchanged_after_bootstrap": True, "reload_identical": True, "tamper_rejected": tamper_code,
        "inference": "ADVISORY_ONLY"}
    (directory / "summary.json").write_bytes(canonical_bytes(summary))
    return summary
