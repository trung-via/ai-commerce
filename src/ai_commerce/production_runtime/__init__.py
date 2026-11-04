"""B0.1 source-only PA2 foundation; importing this package starts no worker."""

from .contracts import (
    ContractError, ValidatedContract, canonical_bytes, draft_authorization,
    parse_contract, schema, seal_contract, validate_contract,
)
from .state_machine import (
    AuthorityKind, AuthorityReceipt, Event, EventKind, EvidencePin, Journey,
    State, TransitionError, VerificationContext, transition,
)

__all__ = [
    "ContractError", "ValidatedContract", "canonical_bytes", "draft_authorization",
    "parse_contract", "schema", "seal_contract", "validate_contract",
    "AuthorityKind", "AuthorityReceipt", "Event", "EventKind", "EvidencePin",
    "Journey", "State", "TransitionError", "VerificationContext", "transition",
]
