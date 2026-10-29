"""A11 offline snapshots, descriptive reports and advisory learning history."""
from .core import (
    LearningError, content_id, parse_contract, seal_contract, validate_contract,
)
from .reader import ObservationSnapshot, SnapshotPin, read_snapshot
from .schemas import EVIDENCE_STATES, READER_VERSION, schema
from .reporting import ComparisonResult, PerformanceReport, build_performance_report, compare_reports
from .registry import LearningEntry, LearningRegistry, hypothesis_context, validate_history
from .export import LearningAdvisory, advisory_schema, export_advisory, write_advisory

__all__ = [
    "EVIDENCE_STATES", "LearningError", "ObservationSnapshot", "READER_VERSION",
    "SnapshotPin", "content_id", "parse_contract", "read_snapshot", "schema",
    "seal_contract", "validate_contract",
    "ComparisonResult", "PerformanceReport", "build_performance_report", "compare_reports",
    "LearningEntry", "LearningRegistry", "hypothesis_context", "validate_history",
    "LearningAdvisory", "advisory_schema", "export_advisory", "write_advisory",
]
