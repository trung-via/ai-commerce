"""B3.4 read-only operational visibility from narrowly scoped native owners.

B0.4 owns locally audited operator invocations. A15 owns user-supplied costs.
There is no cross-owner transaction, real billing oracle or global model quota.
No provider, GitHub, ChatGPT or HostOps account is accessed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import re

from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.operations.cost_ledger import CostLedger
from ai_commerce.production_runtime import metrics

SCHEMA = "B3OperationalDashboard.v1"
ZERO = "0" * 64
HEX = re.compile(r"[a-f0-9]{64}\Z")
MAX_ENTRIES = 64
MAX_BYTES = 65536
LIMITS = {
    "authority": "READ_ONLY_NO_EXTERNAL_EFFECTS",
    "b04_scope": "LOCAL_OPERATOR_SESSION_ONLY_NOT_ALL_JOURNEYS",
    "a15_scope": "SELECTED_LOCAL_SUPPLIED_COST_RECORDS_ONLY",
    "b04_rollback_resistance": "NOT_PROVEN_IF_TRAIL_AND_ANCHORS_REPLACED",
    "cross_owner_identity": "NOT_ESTABLISHED",
    "cross_owner_snapshot": "NOT_ATOMIC",
    "provider_billing_verified": False,
    "human_or_brain_decision": None,
    "provider_effect_authority": "NONE",
    "financial_commitment": "NONE",
}
MISSING_ALWAYS = frozenset({
    "BRAIN_TURNS_NOT_INSTRUMENTED",
    "GLOBAL_EF02_AGENT_CALLS_NOT_INSTRUMENTED",
    "MODEL_QUOTA_NOT_INSTRUMENTED",
    "Q4_QUALITY_NOT_LINKED",
    "JOURNEY_LATENCY_NOT_INSTRUMENTED",
    "LOST_WORK_NOT_MEASURED",
    "PROVIDER_BILLING_NOT_VERIFIED",
    "CROSS_OWNER_JOURNEY_JOIN_UNVERIFIED",
})


class DashboardHold(ValueError):
    """Safe code-only errors. No private paths, user data or credentials."""


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise DashboardHold(code)


def _valid_sha(value: object) -> bool:
    return type(value) is str and bool(HEX.fullmatch(value))


@dataclass(frozen=True, slots=True)
class B3OperationalDashboard:
    raw: bytes

    def __post_init__(self):
        _need(type(self.raw) is bytes and 0 < len(self.raw) <= MAX_BYTES,
              "B34_PACKET_BOUNDS")
        try:
            value = json.loads(self.raw)
            _need(type(value) is dict and canonical_bytes(value) == self.raw,
                  "B34_PACKET_NOT_CANONICAL")
            _need(set(value) == {
                "schema_version", "status", "sources", "observed", "cost_summary",
                "unobserved", "missingness", "limits", "record_sha256"},
                "B34_PACKET_FIELDS")
            _need(value["schema_version"] == SCHEMA,
                  "B34_PACKET_SCHEMA")
            body = {key: val for key, val in value.items() if key != "record_sha256"}
            _need(value["record_sha256"] == sha256(canonical_bytes(body)).hexdigest(),
                  "B34_PACKET_IDENTITY")
            _need(value["limits"] == LIMITS, "B34_PACKET_AUTHORITY")
            _need(value["status"] in {"SCOPED_LOCAL_EVIDENCE_ONLY", "INSUFFICIENT_OBSERVATIONS"},
                  "B34_PACKET_STATUS")
            _need(value["missingness"] == sorted(set(value["missingness"]))
                  and set(value["missingness"]) >= MISSING_ALWAYS,
                  "B34_PACKET_MISSINGNESS")
            unobserved = value["unobserved"]
            _need(type(unobserved) is dict and set(unobserved) == {
                "global_ef02_calls", "brain_turns", "codex_quota_remaining",
                "antigravity_quota_remaining", "q4_quality_score",
                "journey_latency_ns", "lost_work_units", "verified_provider_cost"}
                  and all(v is None for v in unobserved.values()),
                  "B34_PACKET_FALSE_GLOBAL_COUNTER")
        except (TypeError, KeyError, ValueError, AttributeError, OverflowError) as exc:
            if isinstance(exc, DashboardHold):
                raise
            raise DashboardHold("B34_PACKET_INVALID") from None

    def to_dict(self) -> dict:
        return json.loads(self.raw)

    @property
    def record_sha256(self) -> str:
        return self.to_dict()["record_sha256"]


def operational_dashboard(*, operator_root: Path | None = None,
                          session_sha256: str | None = None,
                          expected_audit_head_sha256: str | None = None,
                          cost_ledger: CostLedger | None = None,
                          cost_entry_ids: tuple[str, ...] | None = None) -> B3OperationalDashboard:
    """Reopen selected native sources. No discovery, writes, callbacks or retries.

    A missing source is unknown. A supplied but malformed/replaced source HOLDS.
    Receiver independently pins the B0.4 head; hashes aren't signed authority.
    """
    has_operator = operator_root is not None
    _need(not has_operator and session_sha256 is None and expected_audit_head_sha256 is None
          or has_operator and isinstance(operator_root, Path) and _valid_sha(session_sha256)
          and _valid_sha(expected_audit_head_sha256), "B34_OPERATOR_SELECTION_INVALID")
    has_cost = cost_ledger is not None
    _need(not has_cost and cost_entry_ids is None or has_cost
          and type(cost_ledger) is CostLedger and type(cost_entry_ids) is tuple
          and 0 < len(cost_entry_ids) <= MAX_ENTRIES
          and all(type(x) is str and 0 < len(x) <= 96 for x in cost_entry_ids)
          and len(set(cost_entry_ids)) == len(cost_entry_ids),
          "B34_COST_SELECTION_INVALID")

    rows = None
    b04_summary = None
    b04_source = None
    cost_summary = None
    a15_source = None
    first_entries = None

    if has_operator:
        try:
            rows = metrics.read(operator_root, session_sha256)
            head = rows[-1]["checksum"] if rows else ZERO
            _need(head == expected_audit_head_sha256, "B34_AUDIT_HEAD_CHANGED")
            b04_summary = metrics.summarize(rows)
            b04_source = {
                "scope": "B04_LOCAL_HASH_CHAIN",
                "session_sha256": session_sha256,
                "event_count": len(rows),
                "head_sha256": head,
                "native_status": "LOCALLY_REPLAYED_UNATTESTED",
            }
        except DashboardHold:
            raise
        except Exception:
            raise DashboardHold("B34_B04_NATIVE_HOLD") from None

    if has_cost:
        try:
            # Only selected, native owner-verified immutable entries are opened.
            first_entries = [cost_ledger.get(key).to_record()
                             for key in cost_entry_ids]
            cost_summary = cost_ledger.summarize(cost_entry_ids)
            second_entries = [cost_ledger.get(key).to_record()
                              for key in cost_entry_ids]
            _need(first_entries == second_entries, "B34_A15_CHANGED_DURING_READ")
            a15_source = {
                "scope": "A15_SELECTED_LOCAL_ENTRIES",
                "selection_sha256": sha256(canonical_bytes(list(cost_entry_ids))).hexdigest(),
                "records_sha256": sha256(canonical_bytes(first_entries)).hexdigest(),
                "selected_count": len(cost_entry_ids),
                "native_status": "LOCALLY_REPLAYED_UNATTESTED",
            }
        except DashboardHold:
            raise
        except Exception:
            raise DashboardHold("B34_A15_NATIVE_HOLD") from None

    # B0.4 can append during an A15 read. Detect an obvious local race, but
    # never call the sources transactionally consistent across stores.
    if has_operator:
        try:
            again = metrics.read(operator_root, session_sha256)
            _need(canonical_bytes(rows) == canonical_bytes(again),
                  "B34_AUDIT_CHANGED_DURING_READ")
        except DashboardHold:
            raise
        except Exception:
            raise DashboardHold("B34_B04_NATIVE_HOLD") from None

    missing = set(MISSING_ALWAYS)
    if b04_summary is None:
        missing.add("B04_AUDIT_NOT_SELECTED")
    if cost_summary is None:
        missing.add("A15_COST_ENTRIES_NOT_SELECTED")
    if b04_summary is not None and b04_summary["incomplete_unknown"]:
        missing.add("B04_INCOMPLETE_ACTIVE_DURATION_UNKNOWN")
    if b04_summary is not None and not b04_summary["outcomes"]["PASS"] and not b04_summary["outcomes"]["HOLD"] and not b04_summary["outcomes"]["UNKNOWN"]:
        missing.add("B04_NO_COMPLETED_RESULTS")
    if cost_summary is not None and (cost_summary["counts"]["UNKNOWN"] or cost_summary["counts"]["PENDING"]):
        missing.add("A15_CHARGE_AMOUNT_UNKNOWN_OR_PENDING")

    observed = {
        "b04_attempts": None if b04_summary is None else b04_summary["observed_b04_attempts"],
        "b04_outcomes": None if b04_summary is None else b04_summary["outcomes"],
        "b04_incomplete_unknown": None if b04_summary is None else b04_summary["incomplete_unknown"],
        "b04_recovery_outcomes": None if b04_summary is None else b04_summary["recovery_outcomes"],
        "b04_active_completed_ns": None if b04_summary is None else b04_summary["active_invocation_ns"],
        "b04_observed_wall_span_ns": None if b04_summary is None else b04_summary["elapsed_wall_ns"],
        "b04_clock_drift_observed": None if b04_summary is None else b04_summary["clock_drift_observed"],
        "b04_agent_calls_within_audited_boundary": None if b04_summary is None else b04_summary["coding_agent_calls"]["observed"],
    }
    unobserved = {
        "global_ef02_calls": None, "brain_turns": None,
        "codex_quota_remaining": None, "antigravity_quota_remaining": None,
        "q4_quality_score": None, "journey_latency_ns": None,
        "lost_work_units": None, "verified_provider_cost": None,
    }
    status = ("SCOPED_LOCAL_EVIDENCE_ONLY" if
              b04_source is not None or a15_source is not None
              else "INSUFFICIENT_OBSERVATIONS")
    body = {
        "schema_version": SCHEMA,
        "status": status,
        "sources": {"b04": b04_source, "a15": a15_source},
        "observed": observed,
        "cost_summary": cost_summary,
        "unobserved": unobserved,
        "missingness": sorted(missing),
        "limits": dict(LIMITS),
    }
    packed = {**body, "record_sha256": sha256(canonical_bytes(body)).hexdigest()}
    return B3OperationalDashboard(canonical_bytes(packed))
