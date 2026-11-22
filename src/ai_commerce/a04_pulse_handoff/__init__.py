"""Public entrypoint for the offline A04 -> PULSE handoff contract owner.

Importing or calling this package validates detached offline contracts only;
it never executes PULSE receivers, never claims WAIT_BRAIN, and performs no
network, provider, publish, or WakeHub side effects.
"""
from __future__ import annotations

from .handoff import (
    DIRECTIVE_SCHEMA_VERSION,
    HANDOFF_PRODUCER_ID,
    HANDOFF_ROUTE_ID,
    HANDOFF_SCHEMA_VERSION,
    HANDOFF_STATUS,
    MAX_ASSET_BYTES,
    MAX_PACKET_BYTES,
    OFFLINE_ALLOWED_ACTION_CLASSES,
    PULSE_RECEIVER_EXECUTION,
    SUPPORTED_ASSET_EVIDENCE_TYPES,
    SUPPORTED_AUDIO_MODES,
    SUPPORTED_CTA_MODES,
    WAIT_BRAIN_STATUS,
    A04PulseAssetPin,
    A04PulseHandoffError,
    A04PulseHandoffPacket,
    A04PulseHandoffProvenance,
    A04PulseHandoffScope,
    A04PulseHandoffValidationResult,
    A04PulseOfflineAuthority,
    build_a04_pulse_handoff_packet,
    canonical_handoff_bytes,
    check_unique_logical_identities,
    derive_logical_identity,
    parse_a04_pulse_handoff_packet,
    validate_a04_pulse_handoff_packet,
)

__all__ = [
    "DIRECTIVE_SCHEMA_VERSION",
    "HANDOFF_PRODUCER_ID",
    "HANDOFF_ROUTE_ID",
    "HANDOFF_SCHEMA_VERSION",
    "HANDOFF_STATUS",
    "MAX_ASSET_BYTES",
    "MAX_PACKET_BYTES",
    "OFFLINE_ALLOWED_ACTION_CLASSES",
    "PULSE_RECEIVER_EXECUTION",
    "SUPPORTED_ASSET_EVIDENCE_TYPES",
    "SUPPORTED_AUDIO_MODES",
    "SUPPORTED_CTA_MODES",
    "WAIT_BRAIN_STATUS",
    "A04PulseAssetPin",
    "A04PulseHandoffError",
    "A04PulseHandoffPacket",
    "A04PulseHandoffProvenance",
    "A04PulseHandoffScope",
    "A04PulseHandoffValidationResult",
    "A04PulseOfflineAuthority",
    "build_a04_pulse_handoff_packet",
    "canonical_handoff_bytes",
    "check_unique_logical_identities",
    "derive_logical_identity",
    "parse_a04_pulse_handoff_packet",
    "validate_a04_pulse_handoff_packet",
]
