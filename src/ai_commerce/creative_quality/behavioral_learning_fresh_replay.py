"""BQ-06b independent source bootstrap. Product replay is NOT implemented.

This module was authored anew from trusted main after Human's independent
reauthoring instruction. No old R1/R2 candidate bytes are imported.
Only an independently accepted new EF02 candidate may implement replay.
"""
from __future__ import annotations

SCHEMA = "BQ06bFreshEvidenceReview.v1"
IMPLEMENTATION_STATUS = "NOT_IMPLEMENTED"


class BQ06bNotImplemented(RuntimeError):
    """No A09/A11 replay claim may be made by this registration stub."""


def review_fresh_evidence(*args, **kwargs):
    """Never fabricate a CURRENT result before authentic owner implementation."""
    raise BQ06bNotImplemented("BQ06B_INDEPENDENT_IMPLEMENTATION_REQUIRED")
