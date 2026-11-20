"""Separate immutable V1.1 storage; existing CreativeStore never auto-upgrades."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ._validation import CreativeValidationError, VersionConflict, exact_fields, positive_int, read_json
from .briefs import brief_digest
from .decisions import ActorDecision
from .intent import CreativeIntent
from .models import EDITABLE_PACKAGE_FIELDS, ConceptRecord, require_digest, require_package_id
from .store import CreativeStore, _put
from .v2 import (
    A03BenchmarkCase, A03BenchmarkComparison, A03BenchmarkExperimentPlan, A03BenchmarkObservation,
    A03CreativeCycleJob, A03CreativeCycleResult, A03CreativeJob,
    A03CreativeProductionJob, A03CreativeProductionPackage, A03CreativeProductionResult,
    A03CreativeStageResult, BenchmarkAttemptTiming, BenchmarkTrialAttempt, ReasoningRequest, ReasoningResult,
    _benchmark_case_slot, _benchmark_comparison_slot, _benchmark_observation_slot,
    _build_benchmark_comparison_payload, _commit_creative_stage, _cycle_lineage, _cycle_stage_job, _output_pin,
    _benchmark_research_request, _reconcile_benchmark_sample,
    _stage_concepts, _validate_spec_evidence, inference_decision, inference_failure,
)

if TYPE_CHECKING:
    from .live import BenchmarkResearchReceipt, BenchmarkResearchRequest, InvocationAuthorization


class CreativeSidecarStore:
    """Uses a separate namespace, preserving all historical V1 bytes/paths.

    Event/model metadata is caller or adapter evidence, not a signed attestation.
    Exclusive creation has the same partial-write fail-closed limits as V1.
    """

    def __init__(self, store: CreativeStore | str | Path) -> None:
        self.creative = store if isinstance(store, CreativeStore) else CreativeStore(store)
        self.root = self.creative.root / "sidecars" / "a03-v11"

    def _path(self, kind: str, digest: str) -> Path:
        require_digest(digest)
        return self.root / kind / f"{digest}.json"

    def _save(self, kind: str, digest: str, data: dict[str, Any]) -> str:
        _put(self._path(kind, digest), canonical_bytes(data))
        return digest

    def _read(self, kind: str, digest: str) -> Any:
        raw = self._path(kind, digest).read_bytes()
        if sha256_bytes(raw) != digest:
            raise CreativeValidationError("stored sidecar digest mismatch")
        return read_json(raw.decode("utf-8"))

    @staticmethod
    def _check_bytes(path: Path, expected: dict[str, Any]) -> None:
        if path.read_bytes() != canonical_bytes(expected):
            raise CreativeValidationError("stored artifact bytes mismatch")

    def _validate_intent(self, intent: CreativeIntent) -> None:
        brief = self.creative.load_brief(intent.source_brief_digest)
        if brief["brief_id"] != intent.brief_id:
            raise CreativeValidationError("intent does not match source brief")
        if intent.parent_intent_digest is not None:
            parent = self.load_intent(intent.parent_intent_digest)
            if parent.brief_id != intent.brief_id:
                raise CreativeValidationError("intent revision cannot rebind brief identity")

    def save_intent(self, intent: CreativeIntent) -> str:
        if not isinstance(intent, CreativeIntent):
            raise CreativeValidationError("expected CreativeIntent")
        valid = CreativeIntent.from_dict(intent.to_dict())
        self._validate_intent(valid)
        return self._save("intents", valid.digest, valid.to_dict())

    def load_intent(self, digest: str) -> CreativeIntent:
        record = CreativeIntent.from_dict(self._read("intents", digest))
        if record.digest != digest:
            raise CreativeValidationError("stored intent digest mismatch")
        self._validate_intent(record)
        return record

    def save_decision(self, decision: ActorDecision) -> str:
        if not isinstance(decision, ActorDecision):
            raise CreativeValidationError("expected ActorDecision")
        valid = ActorDecision.from_dict(decision.to_dict())
        self.creative.load_selection(valid.selection_id)
        if valid.kind != "HUMAN_SELECTED":
            self.verify_inference_decision(valid)
        self._save("decisions", valid.decision_id[9:], valid.to_dict())
        return valid.decision_id

    def load_decision(self, decision_id: str) -> ActorDecision:
        if not isinstance(decision_id, str) or not decision_id.startswith("decision:"):
            raise CreativeValidationError("invalid decision_id")
        record = ActorDecision.from_dict(self._read("decisions", decision_id[9:]))
        if record.decision_id != decision_id:
            raise CreativeValidationError("stored actor decision digest mismatch")
        self.creative.load_selection(record.selection_id)
        # V1 records remain readable history, but are never usable inference
        # evidence through verify_inference_decision or the stage owner.
        if record.schema_version == "a03.actor-decision.v2" and record.kind != "HUMAN_SELECTED":
            self.verify_inference_decision(record)
        return record

    def verify_inference_decision(self, decision: ActorDecision) -> ReasoningResult:
        """Resolve actual saved inference, not an arbitrary evidence label.

        Mock receipts remain explicitly TEST_ONLY_MOCK in the binding. This is
        content verification within trusted storage, not AI authentication or
        proof that a provider executed outside the injected adapter boundary.
        """
        valid = ActorDecision.from_dict(decision.to_dict())
        if valid.schema_version != "a03.actor-decision.v2" or valid.inference is None:
            raise CreativeValidationError("AI decision requires saved inference binding")
        binding = valid.inference
        result = self.load_result(binding.receipt_digest)
        if inference_failure(result) is not None:
            raise CreativeValidationError("decision receipt is not bound successful inference")
        if result.request.cycle is not None and (result.request.operation != "SELECT" or valid.kind != "AI_RECOMMENDED"):
            raise CreativeValidationError("R2 AI decisions require an independent typed model selector")
        selection = self.creative.load_selection(valid.selection_id)
        self._check_bytes(self.creative._selection_path(valid.selection_id), selection.to_dict())
        concept = self.creative.load_concept(selection.concept_id, selection.revision)
        self._check_bytes(self.creative._concept_path(concept.concept_id, concept.revision), concept.to_dict())
        if result.request.operation == "SELECT":
            if (valid.kind != "AI_RECOMMENDED" or result.selection.action != "SELECT"
                    or result.selection.selection != selection):
                raise CreativeValidationError("R2 AI choice requires explicit model SELECT evidence")
            permitted = (result.selection.selection,)
        else:
            permitted = tuple(_output_pin(c) for c in _stage_concepts(result))
        if (binding.request_digest != result.request.digest
                or binding.source_brief_digest != brief_digest(result.request.brief)
                or binding.source_brief_digest != selection.source_brief_digest
                or binding.concept_digest != selection.concept_digest
                or binding.mode != result.observation.mode
                or selection not in permitted):
            raise CreativeValidationError("decision inference/source/selected concept mismatch")
        return result

    def decide_from_result(self, selection_id: str, receipt_digest: str, *,
                           kind: str = "AUTO_PINNED", actor_ref: str = "a03:run_creative_stage") -> ActorDecision:
        """Bind a system pin or AI proposal to actual saved output; Human is optional."""
        if kind not in ("AUTO_PINNED", "AI_RECOMMENDED"):
            raise CreativeValidationError("inference decision must be AUTO_PINNED or AI_RECOMMENDED")
        selection = self.creative.load_selection(selection_id)
        result = self.load_result(receipt_digest)
        decision = inference_decision(result, selection, kind=kind, actor_ref=actor_ref)
        self.save_decision(decision)
        return decision

    def _validate_result(self, result: ReasoningResult) -> None:
        self.validate_request(result.request)
        if result.request.benchmark is not None and result.observation.mode == "MODEL":
            raise CreativeValidationError("no verified live benchmark transport")

    def validate_request(self, request: ReasoningRequest) -> None:
        request = ReasoningRequest.from_dict(request.to_dict())
        # The canonical request carries its complete source snapshot; compare
        # against storage rather than accepting a matching brief_id alone.
        brief = self.creative.load_brief(brief_digest(request.brief))
        if brief != request.brief:
            raise CreativeValidationError("stored reasoning brief mismatch")
        self._check_bytes(self.creative.root / "briefs" / f"{brief_digest(brief)}.json", brief)
        if request.intent is not None:
            self._validate_intent(request.intent)
        if request.source is not None:
            saved = self.creative.load_concept(request.source.concept_id, request.source.revision)
            if saved != request.source:
                raise CreativeValidationError("proposal source revision mismatch")
            self._check_bytes(self.creative._concept_path(saved.concept_id, saved.revision), saved.to_dict())
        if request.benchmark is not None:
            context = request.benchmark
            plan = self.load_benchmark_experiment_plan(context.plan_digest)
            sample = next((sample for sample in plan.samples if sample.sample_id == context.sample_id), None)
            if (plan.purpose != "TEST_ONLY_MOCK" or not plan.role_configurations or sample is None
                    or request.operation not in sample.operations or context.attempt > plan.budget.max_attempts
                    or self.load_benchmark_case_by_digest(sample.case_digest).source_brief_digest != brief_digest(brief)):
                raise CreativeValidationError("benchmark role rebound trial sample, brief or plan")
            if sample.strategy == "S3":
                case = self.load_benchmark_case_by_digest(sample.case_digest)
                _, receipt = self._load_benchmark_research(_benchmark_research_request(plan, sample, case))
                if (receipt is None or receipt.error_code is not None
                        or receipt.digest != context.research_receipt_digest or receipt.sources != context.evidence):
                    raise CreativeValidationError("benchmark role needs retrieved sample source bytes")
            elif context.research_receipt_digest is not None:
                raise CreativeValidationError("benchmark strategy does not include research")
            if context.generator_receipt_digest is not None:
                generator = self.load_result(context.generator_receipt_digest)
                generated_context = generator.request.benchmark
                if (generated_context is None or generator.request.operation != "EXPLORE" or generator.state != "OK"
                        or inference_failure(generator) is not None
                        or (generated_context.plan_digest, generated_context.sample_id)
                        != (context.plan_digest, context.sample_id)
                        or generator.concepts()[0] != request.source):
                    raise CreativeValidationError("benchmark role rebound generator receipt or source")
            if context.critic is not None:
                critic = self.load_result(context.critic.receipt_digest)
                if (critic.request.benchmark is None or critic.request.operation != "CRITIQUE" or critic.state != "OK"
                        or inference_failure(critic) is not None
                        or (critic.request.benchmark.plan_digest, critic.request.benchmark.sample_id)
                        != (context.plan_digest, context.sample_id)
                        or critic.request.benchmark.generator_receipt_digest != context.generator_receipt_digest
                        or (critic.request.digest, critic.critique, _output_pin(critic.request.source))
                        != (context.critic.request_digest, context.critic.critique, context.critic.selection)):
                    raise CreativeValidationError("benchmark developer rebound independent critic evidence")
        if request.cycle is not None:
            context = request.cycle
            cycle = A03CreativeCycleJob.from_dict(self._read("cycle-requests", context.cycle_digest))
            self._check_bytes(self._path("cycle-jobs", cycle.seed.slot), cycle.to_dict())
            if (cycle.digest != context.cycle_digest or request.brief != cycle.seed.request.brief
                    or request.intent != cycle.seed.request.intent or context.evidence != cycle.evidence):
                raise CreativeValidationError("cycle request rebound its brief, intent or evidence")
            for candidate in context.candidates:
                saved = self.creative.load_concept(candidate.concept_id, candidate.revision)
                if saved != candidate:
                    raise CreativeValidationError("cycle candidate snapshot mismatch")
                self._check_bytes(self.creative._concept_path(saved.concept_id, saved.revision), saved.to_dict())
                pin = _output_pin(saved)
                if self.creative.load_selection(pin.selection_id) != pin:
                    raise CreativeValidationError("cycle candidate pin mismatch")
                self._check_bytes(self.creative._selection_path(pin.selection_id), pin.to_dict())
            for critic in context.critics:
                result = self.load_result(critic.receipt_digest)
                if (result.state != "OK" or inference_failure(result) is not None
                        or result.request.operation != "CRITIQUE" or result.request.cycle is None
                        or result.request.cycle.cycle_digest != context.cycle_digest
                        or result.request.digest != critic.request_digest or result.critique != critic.critique
                        or _output_pin(result.request.source) != critic.selection):
                    raise CreativeValidationError("selector critic evidence/source mismatch")
                stage = self.load_stage(_cycle_stage_job(cycle, result.request))
                if stage.reasoning != result or stage.status not in ("TEST_ONLY_MOCK", "CREATIVE_COMPLETE"):
                    raise CreativeValidationError("selector needs independent claimed critic attempts")
            if context.selector_receipt is not None:
                selector = self.load_result(context.selector_receipt)
                if (selector.state != "OK" or inference_failure(selector) is not None
                        or selector.request.operation != "SELECT" or selector.request.cycle is None
                        or selector.request.cycle.cycle_digest != context.cycle_digest
                        or selector.selection.action not in ("SELECT", "REVISE")
                        or selector.selection.selection != _output_pin(request.source)):
                    raise CreativeValidationError("development source does not match model selection")
                stage = self.load_stage(_cycle_stage_job(cycle, selector.request))
                if stage.reasoning != selector or stage.status not in ("TEST_ONLY_MOCK", "CREATIVE_COMPLETE"):
                    raise CreativeValidationError("development needs a claimed independent selector attempt")
        if request.production is not None:
            context = request.production
            prod_job = A03CreativeProductionJob.from_dict(
                self._read("production-requests", context.production_job_digest)
            )
            self._check_bytes(self._path("production-jobs", prod_job.slot), prod_job.to_dict())
            if prod_job.digest != context.production_job_digest:
                raise CreativeValidationError("production request job digest mismatch")
            cycle = self.load_cycle_by_digest(prod_job.cycle_job_digest, prod_job.cycle_result_digest)
            if (request.brief != cycle.job.seed.request.brief
                    or request.intent != cycle.job.seed.request.intent
                    or context.evidence != cycle.job.evidence
                    or context.lineage != _cycle_lineage(cycle)
                    or request.source is None
                    or _output_pin(request.source) != prod_job.selected_concept
                    or prod_job.selected_concept != cycle.output):
                raise CreativeValidationError("production request rebound brief, intent, evidence or R2 lineage")
            if context.parent_package is not None:
                saved_parent = self.load_production_package_by_digest(context.parent_package.digest)
                if saved_parent != context.parent_package or prod_job.parent_package_digest != saved_parent.digest:
                    raise CreativeValidationError("production request parent package mismatch")
            elif prod_job.parent_package_digest is not None:
                raise CreativeValidationError("production request missing pinned parent package")

    def save_result(self, result: ReasoningResult) -> str:
        if not isinstance(result, ReasoningResult):
            raise CreativeValidationError("expected ReasoningResult")
        valid = ReasoningResult.from_dict(result.to_dict())
        self._validate_result(valid)
        return self._save("reasoning", valid.digest, valid.to_dict())

    def load_result(self, digest: str) -> ReasoningResult:
        result = ReasoningResult.from_dict(self._read("reasoning", digest))
        if result.digest != digest:
            raise CreativeValidationError("stored reasoning result digest mismatch")
        self._validate_result(result)
        return result

    def claim_job(self, job: A03CreativeJob) -> bool:
        """Immutable attempt binding, not a scheduler, journal or retry lease.

        Exclusive creation reserves one invocation. Partial/uncertain writes
        fail closed under the same trusted-directory limits as CreativeStore.
        """
        valid = A03CreativeJob.from_dict(job.to_dict())
        self.validate_request(valid.request)
        return self._claim("jobs", valid.slot, valid.to_dict())

    def _claim(self, kind: str, slot: str, data: dict[str, Any]) -> bool:
        path = self._path(kind, slot)
        content = canonical_bytes(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as target:
                target.write(content)
        except FileExistsError:
            if path.read_bytes() != content:
                raise VersionConflict("VERSION_CONFLICT: creative attempt already bound") from None
            return False
        return True

    def _validate_stage(self, stage: A03CreativeStageResult) -> None:
        self._check_bytes(self._path("jobs", stage.job.slot), stage.job.to_dict())
        if self.load_result(stage.reasoning.digest) != stage.reasoning:
            raise CreativeValidationError("stage reasoning receipt mismatch")
        if stage.job.request.cycle is not None or stage.job.request.production is not None:
            self._check_bytes(self._path("cycle-observed-refs", stage.job.slot),
                              {"reasoning_digest": stage.reasoning.digest})
        for output in stage.outputs:
            saved = self.creative.load_selection(output.selection_id)
            if saved != output:
                raise CreativeValidationError("stage output pin mismatch")
            self._check_bytes(self.creative._selection_path(output.selection_id), output.to_dict())
            concept = self.creative.load_concept(output.concept_id, output.revision)
            self._check_bytes(self.creative._concept_path(output.concept_id, output.revision), concept.to_dict())
        if stage.decision is not None and self.load_decision(stage.decision.decision_id) != stage.decision:
            raise CreativeValidationError("stage decision receipt mismatch")

    def save_stage(self, stage: A03CreativeStageResult) -> str:
        valid = A03CreativeStageResult.from_dict(stage.to_dict())
        self._validate_stage(valid)
        # Content-addressed receipt plus a single immutable attempt reference.
        # A crash before the reference is committed never permits re-invocation.
        self._save("stages", valid.digest, valid.to_dict())
        self._save("stage-refs", valid.job.slot, {"stage_digest": valid.digest})
        return valid.digest

    def load_stage(self, job: A03CreativeJob) -> A03CreativeStageResult:
        valid = A03CreativeJob.from_dict(job.to_dict())
        self._check_bytes(self._path("jobs", valid.slot), valid.to_dict())
        path = self._path("stage-refs", valid.slot)
        if not path.exists():
            raise CreativeValidationError("UNRESOLVED_CREATIVE_JOB: reserved attempt has no terminal receipt")
        ref = exact_fields(read_json(path.read_text(encoding="utf-8")), {"stage_digest"})
        self._check_bytes(path, ref)
        stage = A03CreativeStageResult.from_dict(self._read("stages", ref["stage_digest"]))
        if stage.job != valid:
            raise CreativeValidationError("stage receipt job mismatch")
        self._validate_stage(stage)
        return stage

    def save_revision(self, result: ReasoningResult) -> ConceptRecord:
        """Deliberately commit proposed edits; conflicts never overwrite a child.

        Store the observed proposal first. A later revision conflict can leave
        that valid observation on disk, without asserting the edit was applied.
        DEVELOP maps to V1 REVISE; this result retains the original V2 operation.
        """
        if not isinstance(result, ReasoningResult):
            raise CreativeValidationError("expected ReasoningResult")
        valid = ReasoningResult.from_dict(result.to_dict())
        proposal = valid.proposed_revision()
        self.save_result(valid)
        return self.creative.save_concept(proposal)

    def claim_cycle(self, job: A03CreativeCycleJob, authorization: InvocationAuthorization) -> bool:
        from .live import InvocationAuthorization
        if not isinstance(authorization, InvocationAuthorization):
            raise CreativeValidationError("expected runner-side cycle authorization")
        valid = A03CreativeCycleJob.from_dict(job.to_dict())
        self.creative.save_brief(valid.seed.request.brief)
        self.validate_request(valid.seed.request)
        if valid.seed.request.intent is not None:
            self.save_intent(valid.seed.request.intent)
        path = self._path("cycle-jobs", valid.seed.slot)
        content = canonical_bytes(valid.to_dict())
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as target:
                target.write(content)
        except FileExistsError:
            if path.read_bytes() != content:
                raise VersionConflict("VERSION_CONFLICT: creative cycle attempt already bound") from None
            self._check_bytes(self._path("cycle-requests", valid.digest), valid.to_dict())
            return False
        self._save("cycle-requests", valid.digest, valid.to_dict())
        self._save("cycle-grants", valid.seed.slot, {"cycle_digest": valid.digest,
            "allow_network": authorization.allow_network, "allow_spend": authorization.allow_spend,
            "authorization_ref": authorization.authorization_ref})
        return True

    def cycle_authorization(self, job: A03CreativeCycleJob) -> InvocationAuthorization:
        """Read the original runner snapshot only for verification/reconstruction."""
        from .live import InvocationAuthorization
        path = self._path("cycle-grants", job.seed.slot)
        value = exact_fields(read_json(path.read_text(encoding="utf-8")),
                             {"cycle_digest", "allow_network", "allow_spend", "authorization_ref"})
        self._check_bytes(path, value)
        if value.pop("cycle_digest") != job.digest:
            raise CreativeValidationError("cycle grant rebound its original job")
        return InvocationAuthorization(**value)

    def save_observed_step(self, job: A03CreativeJob, result: ReasoningResult) -> None:
        self._check_bytes(self._path("jobs", job.slot), job.to_dict())
        if result.request != job.request or self.load_result(result.digest) != result:
            raise CreativeValidationError("observed step request mismatch")
        self._save("cycle-observed-refs", job.slot, {"reasoning_digest": result.digest})

    def observed_step(self, job: A03CreativeJob) -> ReasoningResult | None:
        path = self._path("cycle-observed-refs", job.slot)
        if not path.exists():
            return None
        self._check_bytes(self._path("jobs", job.slot), job.to_dict())
        ref = exact_fields(read_json(path.read_text(encoding="utf-8")), {"reasoning_digest"})
        self._check_bytes(path, ref)
        result = self.load_result(ref["reasoning_digest"])
        if result.request != job.request:
            raise CreativeValidationError("observed step rebound its request")
        return result

    def has_cycle_result(self, job: A03CreativeCycleJob) -> bool:
        return self._path("cycle-refs", job.seed.slot).exists()

    def _validate_cycle(self, result: A03CreativeCycleResult) -> None:
        self._check_bytes(self._path("cycle-jobs", result.job.seed.slot), result.job.to_dict())
        self._check_bytes(self._path("cycle-requests", result.job.digest), result.job.to_dict())
        if self.cycle_authorization(result.job) != result.authorization:
            raise CreativeValidationError("cycle receipt rebound its original runner grant")
        for step in result.steps:
            if self.load_stage(step.job) != step:
                raise CreativeValidationError("cycle stage receipt mismatch")
        if result.halted_step is not None and self.observed_step(result.halted_step) != result.observed:
            raise CreativeValidationError("cycle lost or rebound its halted observation")
        if result.decision is not None:
            self.verify_inference_decision(result.decision)
        if result.output is not None:
            saved = self.creative.load_selection(result.output.selection_id)
            if saved != result.output:
                raise CreativeValidationError("cycle production source pin mismatch")

    def save_cycle(self, result: A03CreativeCycleResult) -> str:
        valid = A03CreativeCycleResult.from_dict(result.to_dict())
        self._validate_cycle(valid)
        if valid.decision is not None:
            self.save_decision(valid.decision)
        self._save("cycle-results", valid.digest, valid.to_dict())
        self._save("cycle-refs", valid.job.seed.slot, {"cycle_digest": valid.digest})
        return valid.digest

    def load_cycle(self, job: A03CreativeCycleJob) -> A03CreativeCycleResult:
        valid = A03CreativeCycleJob.from_dict(job.to_dict())
        self._check_bytes(self._path("cycle-jobs", valid.seed.slot), valid.to_dict())
        path = self._path("cycle-refs", valid.seed.slot)
        if not path.exists():
            raise CreativeValidationError("UNRESOLVED_CREATIVE_CYCLE: no terminal receipt; never auto retry")
        ref = exact_fields(read_json(path.read_text(encoding="utf-8")), {"cycle_digest"})
        self._check_bytes(path, ref)
        result = A03CreativeCycleResult.from_dict(self._read("cycle-results", ref["cycle_digest"]))
        if result.job != valid:
            raise CreativeValidationError("cycle receipt job mismatch")
        self._validate_cycle(result)
        if result.decision is not None and self.load_decision(result.decision.decision_id) != result.decision:
            raise CreativeValidationError("cycle decision bytes mismatch")
        return result

    def load_cycle_by_digest(self, cycle_job_digest: str, cycle_result_digest: str) -> A03CreativeCycleResult:
        require_digest(cycle_job_digest)
        require_digest(cycle_result_digest)
        job = A03CreativeCycleJob.from_dict(self._read("cycle-requests", cycle_job_digest))
        if job.digest != cycle_job_digest:
            raise CreativeValidationError("cycle job digest mismatch")
        result = self.load_cycle(job)
        if result.digest != cycle_result_digest:
            raise CreativeValidationError("cycle result digest mismatch")
        return result

    def _package_path(self, package_id: str, revision: int) -> Path:
        require_package_id(package_id)
        positive_int(revision, "revision")
        return self.root / "production-packages" / package_id[8:] / f"r{revision}.json"

    def _validate_production_package(self, package: A03CreativeProductionPackage) -> None:
        brief = self.creative.load_brief(package.source_brief_digest)
        if brief["brief_id"] != package.brief_id:
            raise CreativeValidationError("production package brief mismatch")
        self._check_bytes(self.creative.root / "briefs" / f"{package.source_brief_digest}.json", brief)
        selection = self.creative.load_selection(package.selected_concept.selection_id)
        if selection != package.selected_concept:
            raise CreativeValidationError("production package selection pin mismatch")
        self._check_bytes(self.creative._selection_path(selection.selection_id), selection.to_dict())
        concept = self.creative.load_concept(selection.concept_id, selection.revision)
        self._check_bytes(self.creative._concept_path(concept.concept_id, concept.revision), concept.to_dict())
        cycle = self.load_cycle_by_digest(package.lineage.cycle_job_digest, package.lineage.cycle_result_digest)
        if _cycle_lineage(cycle) != package.lineage or cycle.output != package.selected_concept:
            raise CreativeValidationError("production package lineage disagrees with verified R2 cycle")
        _validate_spec_evidence(package.spec, brief, cycle.job.evidence, package.evidence_refs)
        if package.reasoning_receipt_digest is not None:
            reasoning = self.load_result(package.reasoning_receipt_digest)
            if (reasoning.state != "OK" or inference_failure(reasoning) is not None
                    or reasoning.request.production is None
                    or reasoning.request.production.lineage != package.lineage
                    or reasoning.production_spec != package.spec
                    or reasoning.observation != package.observation or reasoning.usage != package.usage
                    or reasoning.configuration != package.configuration or reasoning.execution != package.execution
                    or reasoning.provider_request_id != package.provider_request_id
                    or reasoning.evidence_refs != package.evidence_refs
                    or reasoning.hypotheses != package.hypotheses):
                raise CreativeValidationError("production package disagrees with saved inference receipt")
            if package.revision == 1:
                if reasoning.request.operation != "PRODUCE_PACKAGE":
                    raise CreativeValidationError("initial production package requires PRODUCE_PACKAGE receipt")
            else:
                if (reasoning.request.operation != "REVISE_PACKAGE"
                        or reasoning.request.production.parent_package is None
                        or reasoning.request.production.parent_package.digest != package.parent.digest):
                    raise CreativeValidationError("AI package revision disagrees with pinned parent receipt")
        if package.parent is not None:
            parent = self.load_production_package(package.parent.package_id, package.parent.revision)
            if parent.digest != package.parent.digest:
                raise CreativeValidationError("production package parent digest mismatch")
            retained = ("package_id", "brief_id", "source_brief_digest", "selected_concept", "lineage")
            if any(getattr(package, name) != getattr(parent, name) for name in retained):
                raise CreativeValidationError("production package revision cannot alter source or R2 lineage")
            expected_sections = tuple(sorted(
                name for name in EDITABLE_PACKAGE_FIELDS
                if getattr(parent.spec, name) != getattr(package.spec, name)
            ))
            if package.changed_sections != expected_sections:
                raise CreativeValidationError("production package changed_sections disagrees with parent spec")
            if package.reasoning_receipt_digest is None:
                preserved = (
                    "configuration", "observation", "usage", "execution",
                    "provider_request_id", "evidence_refs", "hypotheses",
                )
                if any(getattr(package, name) != getattr(parent, name) for name in preserved):
                    raise CreativeValidationError("direct package edit cannot alter generation provenance")

    def save_production_package(self, package: A03CreativeProductionPackage) -> A03CreativeProductionPackage:
        if not isinstance(package, A03CreativeProductionPackage):
            raise CreativeValidationError("expected A03CreativeProductionPackage")
        valid = A03CreativeProductionPackage.from_dict(package.to_dict())
        self._validate_production_package(valid)
        _put(self._package_path(valid.package_id, valid.revision), canonical_bytes(valid.to_dict()))
        self._save("production-package-digests", valid.digest, {
            "package_id": valid.package_id, "revision": valid.revision, "package_digest": valid.digest,
        })
        return valid

    def load_production_package(self, package_id: str, revision: int) -> A03CreativeProductionPackage:
        path = self._package_path(package_id, revision)
        raw = path.read_bytes()
        record = A03CreativeProductionPackage.from_dict(read_json(raw.decode("utf-8")))
        if raw != canonical_bytes(record.to_dict()):
            raise CreativeValidationError("stored production package bytes mismatch")
        if record.package_id != package_id or record.revision != revision:
            raise CreativeValidationError("stored production package identity/revision mismatch")
        self._check_bytes(self._path("production-package-digests", record.digest), {
            "package_id": record.package_id, "revision": record.revision, "package_digest": record.digest,
        })
        self._validate_production_package(record)
        return record

    def load_production_package_by_digest(self, digest: str) -> A03CreativeProductionPackage:
        require_digest(digest)
        path = self._path("production-package-digests", digest)
        ref = exact_fields(read_json(path.read_text(encoding="utf-8")), {"package_id", "revision", "package_digest"})
        self._check_bytes(path, ref)
        if ref["package_digest"] != digest:
            raise CreativeValidationError("stored production package digest index mismatch")
        record = self.load_production_package(ref["package_id"], ref["revision"])
        if record.digest != digest:
            raise CreativeValidationError("stored production package digest mismatch")
        return record

    def load_production_handoff(self, package_id: str, revision: int) -> dict[str, Any]:
        """Return a fresh detached A04/R4 consumer payload without altering closed A04 contracts."""
        package = self.load_production_package(package_id, revision)
        selected = self.creative.load_selected(package.selected_concept.selection_id)
        return {
            "brief": selected["brief"],
            "concept": selected["concept"],
            "selection": selected["selection"],
            "production_package": package.to_dict(),
            "package_digest": package.digest,
        }

    def claim_production_job(self, job: A03CreativeProductionJob, authorization: InvocationAuthorization) -> bool:
        from .live import InvocationAuthorization
        if not isinstance(authorization, InvocationAuthorization):
            raise CreativeValidationError("expected runner-side production authorization")
        valid = A03CreativeProductionJob.from_dict(job.to_dict())
        path = self._path("production-jobs", valid.slot)
        content = canonical_bytes(valid.to_dict())
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as target:
                target.write(content)
        except FileExistsError:
            if path.read_bytes() != content:
                raise VersionConflict("VERSION_CONFLICT: creative production job attempt already bound") from None
            self._check_bytes(self._path("production-requests", valid.digest), valid.to_dict())
            return False
        self._save("production-requests", valid.digest, valid.to_dict())
        self._save("production-grants", valid.slot, {
            "production_job_digest": valid.digest,
            "allow_network": authorization.allow_network,
            "allow_spend": authorization.allow_spend,
            "authorization_ref": authorization.authorization_ref,
        })
        return True

    def production_authorization(self, job: A03CreativeProductionJob) -> InvocationAuthorization:
        from .live import InvocationAuthorization
        path = self._path("production-grants", job.slot)
        value = exact_fields(read_json(path.read_text(encoding="utf-8")),
                             {"production_job_digest", "allow_network", "allow_spend", "authorization_ref"})
        self._check_bytes(path, value)
        if value.pop("production_job_digest") != job.digest:
            raise CreativeValidationError("production grant rebound its original job")
        return InvocationAuthorization(**value)

    def has_production_result(self, job: A03CreativeProductionJob) -> bool:
        return self._path("production-refs", job.slot).exists()

    def _validate_production_result(self, result: A03CreativeProductionResult) -> None:
        self._check_bytes(self._path("production-jobs", result.job.slot), result.job.to_dict())
        self._check_bytes(self._path("production-requests", result.job.digest), result.job.to_dict())
        if self.production_authorization(result.job) != result.authorization:
            raise CreativeValidationError("production receipt rebound its original runner grant")
        cycle = self.load_cycle_by_digest(result.job.cycle_job_digest, result.job.cycle_result_digest)
        if cycle.output != result.job.selected_concept:
            raise CreativeValidationError("production receipt disagrees with saved R2 cycle output")
        if result.stage is not None and self.load_stage(result.stage.job) != result.stage:
            raise CreativeValidationError("production stage receipt mismatch")
        if result.observed is not None and self.load_result(result.observed.digest) != result.observed:
            raise CreativeValidationError("production observed reasoning mismatch")
        if result.package is not None:
            saved = self.load_production_package(result.package.package_id, result.package.revision)
            if saved != result.package or self.load_production_package_by_digest(result.package.digest) != result.package:
                raise CreativeValidationError("production package bytes mismatch")

    def save_production_result(self, result: A03CreativeProductionResult) -> str:
        valid = A03CreativeProductionResult.from_dict(result.to_dict())
        self._validate_production_result(valid)
        self._save("production-results", valid.digest, valid.to_dict())
        self._save("production-refs", valid.job.slot, {"production_result_digest": valid.digest})
        return valid.digest

    def load_production_result(self, job: A03CreativeProductionJob) -> A03CreativeProductionResult:
        valid = A03CreativeProductionJob.from_dict(job.to_dict())
        self._check_bytes(self._path("production-jobs", valid.slot), valid.to_dict())
        path = self._path("production-refs", valid.slot)
        if not path.exists():
            raise CreativeValidationError("UNRESOLVED_PRODUCTION_JOB: no terminal receipt; never auto retry")
        ref = exact_fields(read_json(path.read_text(encoding="utf-8")), {"production_result_digest"})
        self._check_bytes(path, ref)
        result = A03CreativeProductionResult.from_dict(
            self._read("production-results", ref["production_result_digest"])
        )
        if result.job != valid:
            raise CreativeValidationError("production receipt job mismatch")
        self._validate_production_result(result)
        return result

    def production_result_path(self, result_digest: str) -> Path:
        """Exact canonical path for one immutable saved A03CreativeProductionResult.v1."""
        return self._path("production-results", result_digest)

    def production_package_path(self, package_id: str, revision: int) -> Path:
        """Exact canonical path for one immutable saved A03CreativeProductionPackage.v1."""
        return self._package_path(package_id, revision)

    def production_package_digest_path(self, package_digest: str) -> Path:
        """Exact canonical path for one production-package digest index entry."""
        return self._path("production-package-digests", package_digest)

    def load_production_result_by_digest(self, result_digest: str) -> A03CreativeProductionResult:
        """Resolve and strictly verify a saved A03CreativeProductionResult.v1 by exact digest."""
        require_digest(result_digest)
        path = self._path("production-results", result_digest)
        raw = path.read_bytes()
        if sha256_bytes(raw) != result_digest:
            raise CreativeValidationError("stored production result digest mismatch")
        record = A03CreativeProductionResult.from_dict(read_json(raw.decode("utf-8")))
        if raw != canonical_bytes(record.to_dict()) or record.digest != result_digest:
            raise CreativeValidationError("stored production result bytes mismatch")
        loaded = self.load_production_result(record.job)
        if loaded != record or loaded.digest != result_digest:
            raise CreativeValidationError("stored production result ref mismatch")
        return loaded

    def verify_production_package_lineage(
        self, package_id: str, revision: int, *, origin_result_digest: str,
    ) -> tuple[A03CreativeProductionResult, A03CreativeProductionPackage, dict[str, Any]]:
        """Verify saved package bytes, digest index, parent chain and origin production result.

        Editable HUMAN/SYSTEM revisions retain an immutable parent chain back to
        the verified origin A03CreativeProductionResult.v1. AI revisions must
        bind to their own saved A03CreativeProductionResult.v1.
        """
        origin = self.load_production_result_by_digest(origin_result_digest)
        if origin.package is None:
            raise CreativeValidationError("origin production result has no committed package")
        package = self.load_production_package(package_id, revision)
        if self.load_production_package_by_digest(package.digest) != package:
            raise CreativeValidationError("stored production package digest index mismatch")
        if package.package_id != origin.package.package_id:
            raise CreativeValidationError("production package identity disagrees with origin result")
        if package.revision < origin.package.revision:
            raise CreativeValidationError("stale production package revision predates origin result")
        if package.revision == origin.package.revision:
            if package != origin.package:
                raise CreativeValidationError("production package disagrees with origin result")
        else:
            current = package
            while current.revision > origin.package.revision:
                if (current.reasoning_receipt_digest is not None
                        or current.edit_actor.kind not in ("HUMAN", "SYSTEM")
                        or current.parent is None):
                    raise CreativeValidationError("AI package revision must bind its own production result")
                parent = self.load_production_package(current.parent.package_id, current.parent.revision)
                if (parent.digest != current.parent.digest
                        or self.load_production_package_by_digest(parent.digest) != parent):
                    raise CreativeValidationError("production package parent lineage mismatch")
                current = parent
            if current != origin.package:
                raise CreativeValidationError("production package parent chain disagrees with origin result")
        handoff = self.load_production_handoff(package.package_id, package.revision)
        if (handoff["package_digest"] != package.digest
                or handoff["production_package"] != package.to_dict()):
            raise CreativeValidationError("production handoff payload mismatch")
        return origin, package, handoff

    # ------------------------------------------------------------------
    # R5a benchmark sidecar namespaces (disjoint from V1/V2/R0-R4 paths).
    # ------------------------------------------------------------------

    def _read_job_slot(self, slot: str) -> A03CreativeJob:
        require_digest(slot)
        path = self._path("jobs", slot)
        raw = path.read_bytes()
        job = A03CreativeJob.from_dict(read_json(raw.decode("utf-8")))
        if raw != canonical_bytes(job.to_dict()) or job.slot != slot:
            raise CreativeValidationError("stored creative job bytes/slot mismatch")
        return job

    def _validate_benchmark_case(self, case: A03BenchmarkCase) -> None:
        brief = self.creative.load_brief(case.source_brief_digest)
        if brief["brief_id"] != case.brief_id:
            raise CreativeValidationError("benchmark case brief_id mismatch")
        self._check_bytes(self.creative.root / "briefs" / f"{case.source_brief_digest}.json", brief)
        if not set(case.expected_evidence_refs) <= set(brief.get("source_refs", [])):
            raise CreativeValidationError("benchmark case expected_evidence_refs not in source brief")
        if case.brief_class == "VN_NON_PRODUCT_FICTION" and brief.get("product_ref") is not None:
            raise CreativeValidationError("VN_NON_PRODUCT_FICTION brief cannot carry a product_ref")
        if case.brief_class == "SILENT_NO_CTA" and brief.get("cta") is not None:
            raise CreativeValidationError("SILENT_NO_CTA brief cannot carry a non-null cta")

    def save_benchmark_case(self, case: A03BenchmarkCase) -> str:
        if not isinstance(case, A03BenchmarkCase):
            raise CreativeValidationError("expected A03BenchmarkCase")
        valid = A03BenchmarkCase.from_dict(case.to_dict())
        self._validate_benchmark_case(valid)
        self._save("benchmark-case-slots", valid.slot, {"case_digest": valid.digest})
        return self._save("benchmark-cases", valid.digest, valid.to_dict())

    def load_benchmark_case_by_digest(self, digest: str) -> A03BenchmarkCase:
        require_digest(digest)
        record = A03BenchmarkCase.from_dict(self._read("benchmark-cases", digest))
        if record.digest != digest:
            raise CreativeValidationError("stored benchmark case digest mismatch")
        self._check_bytes(self._path("benchmark-case-slots", record.slot), {"case_digest": digest})
        self._validate_benchmark_case(record)
        return record

    def load_benchmark_case(self, cohort_id: str, case_id: str) -> A03BenchmarkCase:
        slot = _benchmark_case_slot(cohort_id, case_id)
        path = self._path("benchmark-case-slots", slot)
        ref = exact_fields(read_json(path.read_text(encoding="utf-8")), {"case_digest"})
        self._check_bytes(path, ref)
        record = self.load_benchmark_case_by_digest(ref["case_digest"])
        if record.cohort_id != cohort_id or record.case_id != case_id:
            raise CreativeValidationError("stored benchmark case slot identity mismatch")
        return record

    def _validate_benchmark_observation(self, obs: A03BenchmarkObservation) -> None:
        case = self.load_benchmark_case_by_digest(obs.case_digest)
        if (case.cohort_id != obs.cohort_id or case.case_id != obs.case_id
                or case.rubric_version != obs.rubric_version or case.source_sha != obs.source_sha
                or case.brief_id != obs.brief_id or case.source_brief_digest != obs.source_brief_digest
                or case.brief_class != obs.brief_class):
            raise CreativeValidationError("benchmark observation disagrees with pinned cohort case")
        if obs.judge_receipt is not None and case.factual_evidence_applicable:
            if obs.judge_receipt.scores.factual_evidence_accuracy is None:
                raise CreativeValidationError("factual_evidence_accuracy is required for this benchmark case")
        if obs.experiment_plan_digest is not None:
            plan = self.load_benchmark_experiment_plan(obs.experiment_plan_digest)
            sample = next((sample for sample in plan.samples if sample.sample_id == obs.sample_id), None)
            if (sample is None or not plan.role_configurations
                    or _reconcile_benchmark_sample(plan, sample, self, persist_observation=False).observation != obs):
                raise CreativeValidationError("benchmark observation differs from its trial receipts")
            return

        seen_cli_threads: set[str] = set()
        seen_cli_sessions: set[str] = set()
        seen_cli_stdouts: set[str] = set()
        for trace in obs.role_traces:
            if trace.role == "RESEARCH":
                if obs.cycle_job_digest is None or trace.receipt_digest is not None:
                    raise CreativeValidationError("legacy research requires verified R2 cycle evidence")
                continue
            job = self._read_job_slot(trace.job_slot)
            if (job.request.benchmark is not None or job.digest != trace.job_digest or job.brief_id != obs.brief_id
                    or job.source_brief_digest != obs.source_brief_digest):
                raise CreativeValidationError("benchmark trace job disagrees with stored A03 job/brief")
            stage = self.load_stage(job)
            if stage.digest != trace.stage_digest:
                raise CreativeValidationError("benchmark trace stage_digest disagrees with stored stage")
            res = self.load_result(trace.receipt_digest)
            if (stage.reasoning != res
                    or res.request.digest != trace.request_digest
                    or res.request.operation != trace.operation
                    or res.observation != trace.observed
                    or res.usage != trace.usage
                    or res.provider_request_id != trace.provider_request_id
                    or (res.configuration if res.configuration is not None else job.configuration) != trace.requested
                    or (res.execution.observed_effort if res.execution is not None else "UNKNOWN")
                    != trace.observed_effort):
                raise CreativeValidationError("benchmark trace disagrees with stored ReasoningResult receipt")
            if obs.status == "REAL_MODEL_OBSERVED" and not trace.is_retry:
                e = res.execution
                if (e is None or e.kind != "AUTHENTICATED_CLI_PROBE" or e.effect != "PROCESS_INVOKED"
                        or e.session_sha256 is None or e.stdout_sha256 is None or e.cli_thread_id is None):
                    raise CreativeValidationError("REAL_MODEL_OBSERVED trace requires verified CLI execution receipt")
            if res.execution is not None:
                for seen, val in (
                    (seen_cli_threads, res.execution.cli_thread_id),
                    (seen_cli_sessions, res.execution.session_sha256),
                    (seen_cli_stdouts, res.execution.stdout_sha256),
                ):
                    if val is not None:
                        if val in seen:
                            raise CreativeValidationError("replayed CLI execution evidence within benchmark observation")
                        seen.add(val)

        if obs.cycle_job_digest is not None:
            cycle = self.load_cycle_by_digest(obs.cycle_job_digest, obs.cycle_result_digest)
            if (cycle.job.seed.brief_id != obs.brief_id
                    or cycle.job.seed.source_brief_digest != obs.source_brief_digest):
                raise CreativeValidationError("benchmark observation cycle rebound source brief")
            if (obs.evidence_refs != tuple(e.source_ref for e in cycle.job.evidence)
                    or obs.evidence_digests != tuple(e.content_sha256 for e in cycle.job.evidence)):
                raise CreativeValidationError("benchmark observation evidence disagrees with verified cycle evidence")
            stage_set = set(obs.stage_result_digests)
            if any(step.digest not in stage_set for step in cycle.steps):
                raise CreativeValidationError("benchmark observation omitted linked cycle stage receipts")

        if obs.production_result_digest is not None:
            prod = self.load_production_result_by_digest(obs.production_result_digest)
            if (prod.job.digest != obs.production_job_digest
                    or prod.job.brief_id != obs.brief_id
                    or prod.job.source_brief_digest != obs.source_brief_digest):
                raise CreativeValidationError("benchmark observation production result mismatch")
            if obs.cycle_result_digest is not None and (
                prod.job.cycle_job_digest != obs.cycle_job_digest
                or prod.job.cycle_result_digest != obs.cycle_result_digest
            ):
                raise CreativeValidationError("benchmark observation production result disagrees with cycle")
            if obs.package_digest is not None:
                origin, pkg, _ = self.verify_production_package_lineage(
                    obs.package_id, obs.package_revision, origin_result_digest=obs.production_result_digest,
                )
                if (origin != prod or pkg.digest != obs.package_digest
                        or pkg.reasoning_receipt_digest != obs.package_receipt_digest):
                    raise CreativeValidationError("benchmark observation package disagrees with verified R3 lineage")
                if obs.consumer_evidence.source_handoff_status == "SOURCE_HANDOFF_VERIFIED":
                    a04 = obs.consumer_evidence.a04_receipt
                    if (origin.status != "CREATIVE_COMPLETE" or origin.job.purpose != "PRODUCTION"
                            or a04.journey_id != origin.job.journey_id
                            or a04.cycle_id != origin.job.cycle_id
                            or a04.job_id != origin.job.job_id
                            or a04.attempt != origin.job.attempt
                            or a04.job_actor_ref != origin.job.provenance.actor_ref
                            or a04.actor_ref != f"{pkg.edit_actor.kind}:{pkg.edit_actor.ref}"
                            or a04.parent_package_digest != (pkg.parent.digest if pkg.parent is not None else None)
                            or a04.selection_id != pkg.selected_concept.selection_id
                            or a04.concept_id != pkg.selected_concept.concept_id
                            or a04.concept_revision != pkg.selected_concept.revision
                            or a04.concept_digest != pkg.selected_concept.concept_digest
                            or a04.audio_mode != pkg.spec.audio.mode
                            or a04.scene_count != len(pkg.spec.scenes)
                            or a04.media_target_count != len(pkg.spec.media_targets)
                            or a04.rights_status != pkg.rights_status):
                        raise CreativeValidationError("A04 handoff receipt disagrees with persisted A03/R4 lineage")

    def save_benchmark_observation(self, obs: A03BenchmarkObservation) -> str:
        if not isinstance(obs, A03BenchmarkObservation):
            raise CreativeValidationError("expected A03BenchmarkObservation")
        valid = A03BenchmarkObservation.from_dict(obs.to_dict())
        self._validate_benchmark_observation(valid)
        return self._write_benchmark_observation(valid)

    def _write_benchmark_observation(self, obs: A03BenchmarkObservation) -> str:
        self._save("benchmark-observation-slots", obs.slot, {"observation_digest": obs.digest})
        return self._save("benchmark-observations", obs.digest, obs.to_dict())

    def load_benchmark_observation_by_digest(self, digest: str) -> A03BenchmarkObservation:
        require_digest(digest)
        record = A03BenchmarkObservation.from_dict(self._read("benchmark-observations", digest))
        if record.digest != digest:
            raise CreativeValidationError("stored benchmark observation digest mismatch")
        self._check_bytes(
            self._path("benchmark-observation-slots", record.slot), {"observation_digest": digest},
        )
        self._validate_benchmark_observation(record)
        return record

    def load_benchmark_observation(
        self, cohort_id: str, case_id: str, strategy: str, sample_id: str, attempt: int,
    ) -> A03BenchmarkObservation:
        slot = _benchmark_observation_slot(cohort_id, case_id, strategy, sample_id, attempt)
        path = self._path("benchmark-observation-slots", slot)
        ref = exact_fields(read_json(path.read_text(encoding="utf-8")), {"observation_digest"})
        self._check_bytes(path, ref)
        record = self.load_benchmark_observation_by_digest(ref["observation_digest"])
        if (record.cohort_id != cohort_id or record.case_id != case_id
                or record.strategy != strategy or record.sample_id != sample_id
                or record.attempt != attempt):
            raise CreativeValidationError("stored benchmark observation slot identity mismatch")
        return record

    def _validate_benchmark_comparison(self, comp: A03BenchmarkComparison) -> None:
        cases = tuple(self.load_benchmark_case_by_digest(d) for d in comp.case_digests)
        observations = tuple(self.load_benchmark_observation_by_digest(d) for d in comp.observation_digests)
        expected = _build_benchmark_comparison_payload(
            comparison_id=comp.comparison_id,
            cases=cases,
            observations=observations,
            strategies=comp.compared_strategies,
            provenance=comp.provenance,
            created_at=comp.created_at,
        )
        if expected != comp:
            raise CreativeValidationError("stored benchmark comparison disagrees with verified observations")

    def save_benchmark_comparison(self, comp: A03BenchmarkComparison) -> str:
        if not isinstance(comp, A03BenchmarkComparison):
            raise CreativeValidationError("expected A03BenchmarkComparison")
        valid = A03BenchmarkComparison.from_dict(comp.to_dict())
        self._validate_benchmark_comparison(valid)
        self._save("benchmark-comparison-slots", valid.slot, {"comparison_digest": valid.digest})
        return self._save("benchmark-comparisons", valid.digest, valid.to_dict())

    def load_benchmark_comparison_by_digest(self, digest: str) -> A03BenchmarkComparison:
        require_digest(digest)
        record = A03BenchmarkComparison.from_dict(self._read("benchmark-comparisons", digest))
        if record.digest != digest:
            raise CreativeValidationError("stored benchmark comparison digest mismatch")
        self._check_bytes(
            self._path("benchmark-comparison-slots", record.slot), {"comparison_digest": digest},
        )
        self._validate_benchmark_comparison(record)
        return record

    def load_benchmark_comparison(self, cohort_id: str, comparison_id: str) -> A03BenchmarkComparison:
        slot = _benchmark_comparison_slot(cohort_id, comparison_id)
        path = self._path("benchmark-comparison-slots", slot)
        ref = exact_fields(read_json(path.read_text(encoding="utf-8")), {"comparison_digest"})
        self._check_bytes(path, ref)
        record = self.load_benchmark_comparison_by_digest(ref["comparison_digest"])
        if record.cohort_id != cohort_id or record.comparison_id != comparison_id:
            raise CreativeValidationError("stored benchmark comparison slot identity mismatch")
        return record

    def _validate_benchmark_experiment_plan(self, plan: A03BenchmarkExperimentPlan) -> None:
        cases = tuple(self.load_benchmark_case_by_digest(digest) for digest in plan.case_digests)
        plan.validate_cases(cases)
        for case in cases:
            brief = self.creative.load_brief(case.source_brief_digest)
            if not plan.role_configurations and case.brief_class == "VN_PRODUCT" and not brief.get("product_ref"):
                raise CreativeValidationError("VN_PRODUCT experiment case requires a product reference")
            if case.brief_class == "SILENT_NO_CTA":
                audio = brief.get("audio")
                if not isinstance(audio, dict) or audio.get("mode") not in ("silence", "SILENCE"):
                    raise CreativeValidationError("SILENT_NO_CTA experiment case must pin explicit silence")

    def save_benchmark_experiment_plan(self, plan: A03BenchmarkExperimentPlan) -> str:
        valid = A03BenchmarkExperimentPlan.from_dict(plan.to_dict())
        self._validate_benchmark_experiment_plan(valid)
        self._save("benchmark-experiment-plan-slots", valid.slot, {"plan_digest": valid.digest})
        return self._save("benchmark-experiment-plans", valid.digest, valid.to_dict())

    def load_benchmark_experiment_plan(self, digest: str) -> A03BenchmarkExperimentPlan:
        plan = A03BenchmarkExperimentPlan.from_dict(self._read("benchmark-experiment-plans", digest))
        if plan.digest != digest:
            raise CreativeValidationError("stored benchmark experiment plan digest mismatch")
        self._check_bytes(self._path("benchmark-experiment-plan-slots", plan.slot), {"plan_digest": digest})
        self._validate_benchmark_experiment_plan(plan)
        return plan

    def _save_experiment_attempt_timing(
        self, job: A03CreativeJob, timing: BenchmarkAttemptTiming,
    ) -> None:
        observed = self.observed_step(job)
        if observed is None or timing.receipt_digest != observed.digest:
            raise CreativeValidationError("benchmark attempt timing requires a linked reasoning receipt")
        if job.request.benchmark is not None:
            digest = sha256_bytes(canonical_bytes(timing.to_dict()))
            self._save("benchmark-timing-records", digest, timing.to_dict())
            self._save("benchmark-attempt-timings", job.slot, {"timing_digest": digest})
        else:
            self._save("benchmark-attempt-timings", job.slot, timing.to_dict())

    def _load_experiment_attempt(self, job: A03CreativeJob) -> tuple[bool, BenchmarkTrialAttempt | None]:
        if not self._path("jobs", job.slot).exists():
            return False, None
        self._check_bytes(self._path("jobs", job.slot), job.to_dict())
        observed = self.observed_step(job)
        if observed is None:
            return True, None
        if self._path("stage-refs", job.slot).exists():
            stage = self.load_stage(job)
            if stage.reasoning != observed:
                raise CreativeValidationError("benchmark stage rebound linked attempt receipt")
        else:
            stage = _commit_creative_stage(job, self, observed)
        path = self._path("benchmark-attempt-timings", job.slot)
        timing = None
        if path.exists():
            raw = read_json(path.read_text(encoding="utf-8"))
            if job.request.benchmark is not None:
                self._check_bytes(path, exact_fields(raw, {"timing_digest"}))
                timing = BenchmarkAttemptTiming.from_dict(self._read("benchmark-timing-records", raw["timing_digest"]))
                if sha256_bytes(canonical_bytes(timing.to_dict())) != raw["timing_digest"]:
                    raise CreativeValidationError("stored benchmark timing is not canonical")
            else:
                timing = BenchmarkAttemptTiming.from_dict(raw)
                self._check_bytes(path, timing.to_dict())
            if timing.receipt_digest != observed.digest:
                raise CreativeValidationError("benchmark attempt timing rebound reasoning receipt")
        return True, BenchmarkTrialAttempt(stage, timing)

    def _claim_benchmark_research(self, request: BenchmarkResearchRequest, authorization_ref: str,
                                  custody_ref: str) -> bool:
        claim = {"request": request.to_dict(), "authorization_ref": authorization_ref, "custody_ref": custody_ref}
        return self._claim("benchmark-research-claims", request.digest, claim)

    def _save_benchmark_research(self, receipt: BenchmarkResearchReceipt) -> None:
        self._check_bytes(self._path("benchmark-research-claims", receipt.request.digest), {
            "request": receipt.request.to_dict(), "authorization_ref": receipt.authorization_ref,
            "custody_ref": receipt.custody_ref,
        })
        self._save("benchmark-research-receipts", receipt.digest, receipt.to_dict())
        self._save("benchmark-research-refs", receipt.request.digest, {"receipt_digest": receipt.digest})

    def _load_benchmark_research(self, request: BenchmarkResearchRequest) -> tuple[bool, BenchmarkResearchReceipt | None]:
        from .live import BenchmarkResearchReceipt

        claim_path = self._path("benchmark-research-claims", request.digest)
        if not claim_path.exists():
            return False, None
        claim = exact_fields(read_json(claim_path.read_text(encoding="utf-8")),
                             {"request", "authorization_ref", "custody_ref"})
        self._check_bytes(claim_path, claim)
        if claim["request"] != request.to_dict():
            raise CreativeValidationError("benchmark research claim rebound its request")
        path = self._path("benchmark-research-refs", request.digest)
        if not path.exists():
            return True, None
        ref = exact_fields(read_json(path.read_text(encoding="utf-8")), {"receipt_digest"})
        self._check_bytes(path, ref)
        receipt = BenchmarkResearchReceipt.from_dict(self._read("benchmark-research-receipts", ref["receipt_digest"]))
        if (receipt.request != request or receipt.digest != ref["receipt_digest"]
                or (receipt.authorization_ref, receipt.custody_ref) != (claim["authorization_ref"], claim["custody_ref"])):
            raise CreativeValidationError("benchmark research receipt rebound request or custody")
        return True, receipt
