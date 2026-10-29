"""A10 M02: offline, evidence-linked comparable trend hypotheses."""

from .schemas import schema, validate_contract
from .tracking import TrendRule, build_series, evaluate_trend, validate_series

__all__ = ["TrendRule", "build_series", "evaluate_trend", "validate_series", "schema", "validate_contract"]
