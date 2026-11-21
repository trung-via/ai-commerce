"""A10 M01-M05 intelligence, explicit AI sidecars and guarded local automation.

run_automation is the scheduler callable; ``opportunity automate --help`` defines
the local manifest/event transport. No provider, scheduling or publishing is
implicit. AI audience drafts remain research receipts, never Human M03 notes.
"""

from .core.models import (
    OpportunityError,
    SignalLedgerError,
    SignalValidationError,
    A10AIResearchTask,
    A10AIResearchReceipt,
    A10OpportunityDecision,
    NATIVE_CODEX_TRANSPORT,
    ai_provider_profile,
    codex_research_argv,
    make_ai_research_task,
    validate_ai_research_task,
    validate_ai_research_receipt,
    parse_ai_research_output,
    ai_handoff_packet,
    run_ai_research,
    make_opportunity_decision,
    validate_opportunity_decision,
    validate_ai_opportunity_choice,
    opportunity_policy_scores,
)
from .signals.importer import (
    PreparedSignalImport,
    load_mapping,
    load_source_snapshot,
    prepare_bytes,
    prepare_pinned_source,
)
from .signals.ledger import SignalLedger
from .signals.profiles import mapping_profile
from .bridge import CreativeBridgeError, freeform_brief, opportunity_to_brief, validate_bridge
from .bridge.briefs import selected_opportunity_to_brief
from .bridge.cli import run_automation, validate_automation_manifest
from .audience.models import prepare_human_audience

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
    "A10AIResearchTask",
    "A10AIResearchReceipt",
    "A10OpportunityDecision",
    "NATIVE_CODEX_TRANSPORT",
    "ai_provider_profile",
    "codex_research_argv",
    "make_ai_research_task",
    "validate_ai_research_task",
    "validate_ai_research_receipt",
    "parse_ai_research_output",
    "ai_handoff_packet",
    "run_ai_research",
    "make_opportunity_decision",
    "validate_opportunity_decision",
    "validate_ai_opportunity_choice",
    "opportunity_policy_scores",
    "prepare_pinned_source",
    "prepare_human_audience",
    "selected_opportunity_to_brief",
    "run_automation",
    "validate_automation_manifest",
]
