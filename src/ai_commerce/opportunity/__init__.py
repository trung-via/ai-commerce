"""A10 offline Opportunity & Audience Intelligence; optional A03 creative bridge."""

from .core.models import (
    OpportunityError,
    SignalLedgerError,
    SignalValidationError,
)
from .signals.importer import (
    PreparedSignalImport,
    load_mapping,
    load_source_snapshot,
    prepare_bytes,
)
from .signals.ledger import SignalLedger
from .signals.profiles import mapping_profile
from .bridge import CreativeBridgeError, freeform_brief, opportunity_to_brief, validate_bridge

__all__ = [
    "OpportunityError",
    "SignalLedgerError",
    "SignalValidationError",
    "PreparedSignalImport",
    "load_mapping",
    "load_source_snapshot",
    "prepare_bytes",
    "SignalLedger",
    "mapping_profile",
    "CreativeBridgeError",
    "freeform_brief",
    "opportunity_to_brief",
    "validate_bridge",
]
