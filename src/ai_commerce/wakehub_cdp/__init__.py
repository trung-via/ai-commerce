"""Source-only CDP authentication boundary; no native provider or sender.

R3B2B must separately integrate the registered runtime/doorbell consumers after
independent Brain acceptance. Importing this package performs no native work.
"""

from .authenticated_turn import (
    AttestationPhase,
    BrowserObservation,
    ChatGPTUserTurnRef,
    DurableReservation,
    EvidenceSource,
    GateCode,
    GateResult,
    GateStatus,
    NativeAttestation,
    OriginPins,
    SendOutcome,
    SignedWakeGrant,
    TurnEvidence,
    TurnScope,
    WakeRequest,
    post_send,
    pre_send,
)

__all__ = [
    "AttestationPhase", "BrowserObservation", "ChatGPTUserTurnRef",
    "DurableReservation", "EvidenceSource", "GateCode", "GateResult",
    "GateStatus", "NativeAttestation", "OriginPins", "SendOutcome",
    "SignedWakeGrant", "TurnEvidence", "TurnScope", "WakeRequest",
    "post_send", "pre_send",
]
