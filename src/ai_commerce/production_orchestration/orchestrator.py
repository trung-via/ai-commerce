"""P0 owner: validate detached snapshots and observe supplied offline receipts.

No consumer runner exists here. Dispatch always reports NOT_DISPATCHED until a
receipt is recorded. Wire receipts are evidence claims, not authentication of
their author. Independent trusted receipt pins are also required for admission.
All duplicate tracking is local to this object and disappears on restart.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from ai_commerce.creative.briefs import brief_digest, intake_brief
from ai_commerce.opportunity.bridge.briefs import validate_bridge

from .contracts import (
    OrchestrationError, ProductionStageCompleted, ProductionStageDispatched,
    ProductionStageReceipt, bytes_digest, digest, require,
)


@dataclass(frozen=True, kw_only=True)
class DispatchResult:
    status: Literal["NOT_DISPATCHED", "RECEIPT_RECORDED"]
    accepted_consumer: str | None = None
    event: ProductionStageDispatched | None = None


class ProductionPipelineOrchestrator:
    """Contract bookkeeping only; no outbox, scheduler or persistent state.

    ``authorized_specs`` is an independent, trusted caller input containing
    (authorization_ref, exact StageSpec SHA-256) pairs. A wire authorization
    label alone grants nothing, including permission to execute a consumer.
    ``verified_receipts`` contains (consumer_id, receipt SHA-256) pairs from an
    independently authenticated receipt source. P0 supplies no such verifier;
    an untrusted wire receipt can never grant its own acknowledgement.
    """

    def __init__(self, *, authorized_specs: Iterable[tuple[str, str]] = (),
                 verified_receipts: Iterable[tuple[str, str]] = ()) -> None:
        pins = tuple(authorized_specs)
        require(all(type(pin) is tuple and len(pin) == 2
                    and all(type(part) is str for part in pin) for pin in pins), "AUTHORIZATION_PINS")
        self._authorized_specs = frozenset(pins)
        receipts = tuple(verified_receipts)
        require(all(type(pin) is tuple and len(pin) == 2
                    and all(type(part) is str for part in pin) for pin in receipts), "RECEIPT_PINS")
        self._verified_receipts = frozenset(receipts)
        self._completed: dict[str, ProductionStageCompleted] = {}
        self._receipts: dict[str, ProductionStageDispatched] = {}
        self._receipt_refs: dict[str, str] = {}
        self._execution_refs: dict[str, str] = {}

    def record_completed(self, completed: ProductionStageCompleted, *, source_bytes: bytes,
                         input_value: dict, output: dict) -> DispatchResult:
        """Check bytes and real consumer contract compatibility; never dispatch."""
        require(type(completed) is ProductionStageCompleted, "COMPLETED_REQUIRED")
        # Revalidate detached wire data at the public boundary.
        completed = ProductionStageCompleted.from_dict(completed.to_dict())
        spec = completed.spec
        require((spec.authorization.authorization_ref, spec.sha256) in self._authorized_specs,
                "ROUTE_NOT_AUTHORIZED")
        require(bytes_digest(source_bytes) == spec.source_sha256, "SOURCE_HASH_MISMATCH")
        require(type(input_value) is dict and digest(input_value) == spec.input_sha256, "INPUT_HASH_MISMATCH")
        require(type(output) is dict and digest(output) == spec.output_sha256, "OUTPUT_HASH_MISMATCH")
        # A03 validation returns a detached ContentBrief. This compatibility
        # check is not an execution receipt for either registered consumer.
        try:
            brief = intake_brief(output)
            require(brief_digest(brief) == spec.output_sha256, "OUTPUT_HASH_MISMATCH")
            if spec.route_id == "a10-m05-content-brief":
                bridge = validate_bridge(brief.get("opportunity_bridge"))
                require(bridge["mode"] == "OPPORTUNITY" and bridge["proposal"] == input_value,
                        "M05_INPUT_BINDING")
            else:
                require(intake_brief(input_value) == brief, "A03_INPUT_BINDING")
        except OrchestrationError:
            raise
        except (ValueError, TypeError, KeyError):
            raise OrchestrationError("CONSUMER_CONTRACT_INVALID") from None
        key = spec.idempotency_key
        previous = self._completed.get(key)
        require(previous is None or previous == completed, "IDEMPOTENCY_CONFLICT")
        self._completed[key] = completed
        return self.dispatch(key)

    def dispatch(self, idempotency_key: str) -> DispatchResult:
        """Report receipt state. This method has no execution capability."""
        require(type(idempotency_key) is str and idempotency_key in self._completed, "COMPLETED_NOT_FOUND")
        event = self._receipts.get(idempotency_key)
        if event is None:
            return DispatchResult(status="NOT_DISPATCHED")
        return DispatchResult(status="RECEIPT_RECORDED", accepted_consumer=event.spec.consumer_id, event=event)

    def record_receipt(self, receipt: ProductionStageReceipt) -> DispatchResult:
        """Observe an actual caller-supplied receipt; never mint one from output."""
        require(type(receipt) is ProductionStageReceipt, "RECEIPT_REQUIRED")
        receipt = ProductionStageReceipt.from_dict(receipt.to_dict())
        spec = receipt.spec
        key = spec.idempotency_key
        completed = self._completed.get(key)
        require(completed is not None, "COMPLETED_NOT_FOUND")
        require(completed.spec == spec and receipt.completed_sha256 == completed.sha256,
                "RECEIPT_COMPLETION_MISMATCH")
        previous = self._receipts.get(key)
        require(previous is None or previous.receipt == receipt, "IDEMPOTENCY_CONFLICT")
        for refs, ref in ((self._receipt_refs, receipt.receipt_ref),
                          (self._execution_refs, receipt.execution_ref)):
            require(ref not in refs or refs[ref] == key, "RECEIPT_REPLAY")
        require((receipt.consumer_id, receipt.sha256) in self._verified_receipts, "RECEIPT_NOT_VERIFIED")
        event = ProductionStageDispatched(spec=spec, completed_sha256=completed.sha256,
                                          receipt=receipt, receipt_sha256=receipt.sha256)
        self._receipts[key] = event
        self._receipt_refs[receipt.receipt_ref] = key
        self._execution_refs[receipt.execution_ref] = key
        return self.dispatch(key)
