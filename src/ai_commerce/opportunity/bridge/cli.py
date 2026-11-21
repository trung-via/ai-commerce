"""Local JSON CLI adapters over the integrated M01-M05 callable APIs.

Authorized scheduler/trigger transport (no task/workflow is installed here)::

    python -m ai_commerce opportunity automate --manifest JOB.json --mode offline
        --source-sha RUNNER_CHECKOUT_SHA --state-dir LOCAL_STATE --result EVENT.json
        --allow-effect IMPORT_SIGNALS --allow-effect CREATE_BRIEF

All arguments belong on one command line. Manifest-relative input paths stay
inside its trusted local directory. source-sha is an independent runner-supplied
checkout pin, compared to the manifest; A10 does not attest Git cleanliness.
Modes: plan writes only the requested result, never state/invocations; offline runs M01-M05 without a provider;
execute additionally permits an explicitly selected/capable AI provider. Effects
require BOTH manifest authorization and runner --allow-effect grants. No publish.

A10AutomationJob.v1 requires job_id, source_sha, topic, as_of, sources,
provider (explicit null for offline), ai_assessment, allowed_effects. Each source
requires file, format, sha256, snapshot; CSV also requires profile. Optional
dialect and comparison_context use existing M01/M02 contracts. Optional audience
is {file,sha256,origin:"HUMAN"}, containing real AudienceNotes.v1, never AI drafts.
No manual_idea, overrides or model-generated Human edits are accepted.
Limits: 256 KiB manifest, eight signal files, 32 MiB combined signal bytes,
existing M01/M03 per-file limits, 1 MiB AI task/output, 4 MiB result.

Results/events use A10AutomationResult.v1 with stable event_id, status, reason,
next_step and exact provenance. Resume with identical job bytes/semantic manifest,
job_id, source pin, execution mode and state directory. Default decision_mode is
autonomous: offline uses A10EligibleFallback.v1, execute requires an authenticated
structured model chooser. No selection file, Human observation or review is
required. Completion is READY_FOR_A03_DISPATCH. To opt in to local A03 brief
intake, add --handoff-a03 --handoff-grant GRANT.json. GRANT.json is a trusted
runner-supplied LocalAuthorization with journey_id, cycle_id, job_id,
source_sha, authorization_ref, route_id="a10-m05-to-a03" and
scope="SOURCE_ONLY_LOCAL". The A10 --result still holds only the producer event.
The CLI prints a separate dispatch outcome backed by the existing production
journal and CreativeStore in STATE_DIR/.production. For dispatch-only recovery,
use opportunity resume-a03 with the same --handoff-grant, --source-sha and
--state-dir. An unresolved reservation is reported as UNKNOWN (exit 4).
Optional decision_mode:"manual" accepts a separate A10OpportunitySelection.v1
using selection_template and a trusted runner --human-identity attestation
{actor:"HUMAN",subject,selection_sha256}. Identity must come from an actual user
session, never model output or the job. An external BRAIN selection is rejected.
For attended
ChatGPT, deliver the bounded handoff packet manually and return its strict output
via --attended-output. No UI/CDP Send exists. A reserved/uncertain invocation is
never replayed. Results and state receipts are immutable; use a fresh --result
filename for each changed event (an identical replay may reuse its filename).
The authorized trigger must retain the same local state directory between runs.
Exit codes: 0 PLAN/COMPLETE or successful intake, 2 FAIL or blocked input,
3 WAIT or held dispatch, 4 UNKNOWN local dispatch.

CLI capabilities are trusted operator files, not fields in an untrusted job:
--capability JSON follows run_ai_research's exact-argv contract. Real invocations
also require installed_probe:{executable_sha256,capability,authentication}; both
probe entries contain read-only argv and an independently trusted stdout_sha256
for this exact installed version/authenticated session. Unsupported CLI versions
must not be granted a capability. No capability means PROVIDER_UNAVAILABLE,
never fallback. Injected test runners are explicitly labelled MOCK, not real AI.
Time, token and Human counters remain UNKNOWN unless observed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from ai_commerce.creative import CreativeStore
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from ..audience import AudienceLedger, signal_evidence_ref
from ..audience.models import MAX_NOTES_BYTES, prepare_bytes as prepare_notes, prepare_human_audience
from ..core.models import (
    AI_MAX_BYTES, OpportunityError, ai_handoff_packet, ai_provider_profile,
    ai_research_receipt, digest, make_ai_research_task, parse_json, require,
    run_ai_research, validate_ai_research_receipt, make_opportunity_decision,
    validate_opportunity_decision,
)
from ..proposals import propose_opportunities
from ..signals import SignalLedger, load_mapping, load_source_snapshot, read_local
from ..signals.files import local_path
from ..signals.importer import prepare_pinned_source
from ..trends import TrendRule, build_series, evaluate_trend
from .briefs import CreativeBridgeError, freeform_brief, opportunity_to_brief, selected_opportunity_to_brief
from .schemas import MAX_RECORD_BYTES, MAX_TEXT_BYTES


def add_parser(commands) -> None:
    parser = commands.add_parser("opportunity", help="Offline user-supplied signals, hypotheses and A03 briefs")
    operations = parser.add_subparsers(dest="opportunity_operation", required=True)
    imp = operations.add_parser("import", help="Atomically import a local CSV/JSON and its explicit snapshot")
    imp.add_argument("--file", type=Path, required=True)
    imp.add_argument("--snapshot", type=Path, required=True)
    imp.add_argument("--ledger", type=Path, required=True)
    imp.add_argument("--format", choices=("csv", "json"), required=True)
    mapping = imp.add_mutually_exclusive_group()
    mapping.add_argument("--mapping", type=Path, help="Explicit custom mapping JSON; not a provider export claim")
    mapping.add_argument("--profile", help="M01 manual mapping profile")
    imp.add_argument("--dialect", choices=("comma", "semicolon", "tab"))
    lineage = imp.add_mutually_exclusive_group()
    lineage.add_argument("--correction-of")
    lineage.add_argument("--reprocess-of")
    inspect = operations.add_parser("inspect", help="Replay one import; local integrity does not verify provider facts")
    inspect.add_argument("--ledger", type=Path, required=True)
    inspect.add_argument("--import-id", required=True)
    trend = operations.add_parser("trend", help="Build comparable M02 series and descriptive hypotheses")
    trend.add_argument("--ledger", type=Path, required=True)
    trend.add_argument("--import-id", action="append", required=True)
    trend.add_argument("--topic", required=True)
    trend.add_argument("--signal-type", default="TOPIC")
    trend.add_argument("--contexts", type=Path, help="Object mapping import IDs to caller-authored comparison contexts")
    trend.add_argument("--as-of", required=True, help="Explicit timezone-aware clock")
    trend.add_argument("--rule", type=Path, help="Optional M02 TrendRule keyword object")
    audience = operations.add_parser("audience", help="Optional deidentified M03 notes and exact signal references")
    audience_ops = audience.add_subparsers(dest="audience_operation", required=True)
    for name in ("validate", "append", "inspect", "history", "ref"):
        command = audience_ops.add_parser(name)
        command.add_argument("--signals", type=Path, required=name == "ref")
        if name in ("validate", "append"):
            command.add_argument("--file", type=Path, required=True)
        if name in ("append", "inspect", "history"):
            command.add_argument("--ledger", type=Path, required=True)
        if name == "append":
            command.add_argument("--revision-of")
        if name in ("inspect", "history"):
            command.add_argument("--insight-id", required=True)
        if name == "ref":
            command.add_argument("--import-id", required=True)
            command.add_argument("--signal-id", required=True)
    propose = operations.add_parser("propose", help="Replay selected sources through real M04; suggestions only")
    propose.add_argument("--signals", type=Path)
    propose.add_argument("--series", type=Path, action="append", default=[])
    propose.add_argument("--audience-ledger", type=Path)
    propose.add_argument("--insight-id", action="append", default=[])
    propose.add_argument("--as-of", required=True)
    idea = propose.add_mutually_exclusive_group()
    idea.add_argument("--idea")
    idea.add_argument("--idea-file", type=Path)
    propose.add_argument("--edits", type=Path, help="M04 creative overrides only")
    propose.add_argument("--rule", type=Path)
    bridge = operations.add_parser("bridge", help="Produce an actual A03 ContentBrief; Human text bypasses A10 evidence")
    source = bridge.add_mutually_exclusive_group(required=True)
    source.add_argument("--proposal", type=Path, help="One proposal or the output of opportunity propose")
    source.add_argument("--text", help="Exact freeform Human text")
    source.add_argument("--text-file", type=Path, help="Exact local UTF-8 text, optional BOM")
    bridge.add_argument("--proposal-id", help="Required to select from multiple proposals")
    bridge.add_argument("--edits", type=Path, help="Human objective/prompt/audience/product_ref/cta/media_hint edits")
    bridge.add_argument("--brief-id")
    bridge.add_argument("--synthetic", action="store_true", help="Explicitly label caller-supplied synthetic fixtures")
    bridge.add_argument("--store", type=Path, help="Optional local A03 CreativeStore; no concept/model generation")
    automate = operations.add_parser("automate", help="Guarded local A10 job; no scheduling, UI, fallback or publish",
                                    description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    automate.add_argument("--manifest", type=Path, required=True, help="Bounded A10AutomationJob.v1 local JSON")
    automate.add_argument("--mode", choices=("plan", "offline", "execute"), required=True)
    automate.add_argument("--source-sha", required=True, help="Independent authorized runner checkout SHA pin")
    automate.add_argument("--state-dir", type=Path, required=True, help="Existing A10 journals and immutable job receipts")
    automate.add_argument("--result", type=Path, required=True, help="Bounded immutable A10AutomationResult.v1 event file")
    automate.add_argument("--allow-effect", action="append", default=[], choices=JOB_EFFECTS)
    automate.add_argument("--selection", type=Path, help="Optional authenticated Human override in explicit manual mode")
    automate.add_argument("--human-identity", type=Path, help="Trusted actual user identity bound to the exact manual selection")
    automate.add_argument("--capability", type=Path, help="Trusted operator CLI capability; never from model output")
    automate.add_argument("--attended-output", type=Path, help="Manually returned ChatGPT structured output; no automated Send")
    automate.add_argument("--handoff-a03", action="store_true", help="Opt in to SOURCE_ONLY_LOCAL A03 brief intake")
    automate.add_argument("--handoff-grant", type=Path, help="Independent trusted LocalAuthorization JSON")
    resume = operations.add_parser("resume-a03", help="Reconcile existing local A10 to A03 dispatch")
    resume.add_argument("--handoff-grant", type=Path, required=True, help="Independent trusted LocalAuthorization JSON")
    resume.add_argument("--source-sha", required=True, help="Independent authorized runner checkout SHA pin")
    resume.add_argument("--state-dir", type=Path, required=True, help="Same trusted local state as the initial handoff")


def _json(path: Path, *, limit: int = MAX_RECORD_BYTES) -> dict:
    return parse_json(read_local(path, limit=limit))


def _rule(path: Path | None) -> TrendRule | None:
    return TrendRule(**_json(path)) if path is not None else None


def _text_file(path: Path) -> tuple[str, bytes]:
    raw = read_local(path, limit=MAX_TEXT_BYTES)
    return raw.decode("utf-8-sig"), raw


def _selected_proposal(args) -> dict:
    document = _json(args.proposal)
    if document.get("schema_version") == "OpportunityProposal.v1":
        choices = [document]
    else:
        if set(document) != {"proposals"} or not isinstance(document["proposals"], list):
            raise CreativeBridgeError("BRIDGE_PROPOSAL_DOCUMENT_INVALID")
        choices = document["proposals"]
    if not all(isinstance(p, dict) for p in choices):
        raise CreativeBridgeError("BRIDGE_PROPOSAL_DOCUMENT_INVALID")
    if args.proposal_id is not None:
        choices = [p for p in choices if p.get("proposal_id") == args.proposal_id]
    if len(choices) != 1:
        raise CreativeBridgeError("BRIDGE_SELECT_EXACTLY_ONE_PROPOSAL")
    return choices[0]


def _audience(args) -> dict | list:
    signals = SignalLedger(args.signals) if args.signals is not None else None
    op = args.audience_operation
    if op == "ref":
        return signal_evidence_ref(signals, args.import_id, args.signal_id)
    if op == "validate":
        return prepare_notes(read_local(args.file, limit=MAX_NOTES_BYTES), signal_ledger=signals).record
    ledger = AudienceLedger(args.ledger)
    if op == "append":
        return ledger.append_file(args.file, signal_ledger=signals, revision_of=args.revision_of)
    if op == "inspect":
        return ledger.get(args.insight_id, signal_ledger=signals)
    return ledger.history(args.insight_id, signal_ledger=signals)


def execute(args):
    op = args.opportunity_operation
    if op == "automate":
        if getattr(args, "handoff_a03", False) or getattr(args, "handoff_grant", None) is not None:
            return _dispatch_a03(args)
        manifest_path = local_path(args.manifest)
        return _execute_automation(args, manifest_path, _json(manifest_path, limit=MAX_JOB_BYTES))
    if op == "resume-a03":
        return _dispatch_a03(args)
    if op == "import":
        return SignalLedger(args.ledger).import_file(
            args.file, source_snapshot=load_source_snapshot(str(args.snapshot)),
            profile=load_mapping(str(args.mapping)) if args.mapping else args.profile,
            file_format=args.format, dialect=args.dialect,
            correction_of=args.correction_of, reprocess_of=args.reprocess_of,
        )
    if op == "inspect":
        ledger = SignalLedger(args.ledger)
        return {**ledger.get(args.import_id), "inspection": ledger.inspect(args.import_id),
                "verification_scope": "LOCAL_REPLAY_ONLY_NOT_PROVIDER_VERIFICATION"}
    if op == "trend":
        series = build_series(SignalLedger(args.ledger), import_ids=args.import_id,
                              topic=args.topic, signal_type=args.signal_type,
                              comparison_contexts=_json(args.contexts) if args.contexts else None)
        return {"series": series, "hypothesis": evaluate_trend(series, as_of=args.as_of, rule=_rule(args.rule))}
    if op == "audience":
        return _audience(args)
    if op == "propose":
        series = []
        for path in args.series:
            document = _json(path)
            # A saved hypothesis is not trusted: M04 replays/re-evaluates series.
            series.append(document["series"] if "series" in document else document)
        idea = _text_file(args.idea_file)[0] if args.idea_file else args.idea
        return {"proposals": propose_opportunities(
            trend_series=series, audience_insight_ids=args.insight_id,
            signal_ledger=SignalLedger(args.signals) if args.signals else None,
            audience_ledger=AudienceLedger(args.audience_ledger) if args.audience_ledger else None,
            as_of=args.as_of, manual_idea=idea,
            creative_overrides=_json(args.edits) if args.edits else None, trend_rule=_rule(args.rule),
        )}
    if op == "bridge":
        kwargs = {"edits": _json(args.edits) if args.edits else None,
                  "brief_id": args.brief_id, "synthetic": args.synthetic}
        if args.proposal is not None:
            brief = opportunity_to_brief(_selected_proposal(args), **kwargs)
        else:
            if args.proposal_id is not None:
                raise CreativeBridgeError("BRIDGE_PROPOSAL_ID_WITHOUT_PROPOSAL")
            text, raw = _text_file(args.text_file) if args.text_file else (args.text, None)
            brief = freeform_brief(text, raw_bytes=raw, **kwargs)
        if args.store is not None:
            store = CreativeStore(local_path(args.store))
            saved = store.save_brief(brief)
            if store.load_brief(saved) != brief:
                raise CreativeBridgeError("BRIDGE_CREATIVE_STORE_REPLAY_MISMATCH")
        return brief
    raise CreativeBridgeError("OPPORTUNITY_OPERATION_UNKNOWN")


def _execute_automation(args, manifest_path: Path, manifest: dict, *, production=None) -> dict:
    return run_automation(
        manifest, base_dir=manifest_path.parent, source_sha=args.source_sha,
        state_dir=args.state_dir, mode=args.mode, permissions=args.allow_effect,
        selection=_json(args.selection) if args.selection else None,
        human_identity=_json(args.human_identity) if args.human_identity else None,
        capability=_json(args.capability, limit=MAX_JOB_BYTES) if args.capability else None,
        attended_output=read_local(args.attended_output, limit=AI_MAX_BYTES) if args.attended_output else None,
        production=production, result_path=args.result,
    )


def _dispatch_a03(args) -> dict:
    from ai_commerce.production_orchestration import (
        LocalAuthorization, OrchestrationError, ProductionPipelineOrchestrator,
    )

    response = {"schema_version": "A10A03DispatchResult.v1", "scope": "SOURCE_ONLY_LOCAL",
                "status": "BLOCKED", "reason": None, "producer_status": None,
                "accepted_consumer": None, "completed": None, "event": None}
    resume = args.opportunity_operation == "resume-a03"
    try:
        require(resume or args.handoff_a03, "HANDOFF_OPT_IN_REQUIRED")
        require(args.handoff_grant is not None, "LOCAL_AUTHORIZATION_REQUIRED")
        grant = LocalAuthorization.from_dict(_json(args.handoff_grant, limit=MAX_JOB_BYTES))
        require(grant.route_id == "a10-m05-to-a03", "LOCAL_ROUTE_MISMATCH")
        require(grant.source_sha == args.source_sha, "SOURCE_SHA_MISMATCH")
        require(_valid_job_id(grant.job_id), "JOB_ID_INVALID")
        production_dir = local_path(local_path(args.state_dir) / ".production")
        owner = ProductionPipelineOrchestrator(state_dir=production_dir, source_sha=args.source_sha,
                                               local_authorization=grant)
        if resume:
            attempts = owner.resume_local()
            if not attempts:
                return {**response, "status": "HOLD", "reason": "COMPLETED_NOT_FOUND"}
            downstream, = attempts
        else:
            manifest_path = local_path(args.manifest)
            manifest = _json(manifest_path, limit=MAX_JOB_BYTES)
            require(manifest.get("job_id") == grant.job_id, "JOB_ID_MISMATCH")
            require(manifest.get("source_sha") == grant.source_sha, "JOB_SOURCE_SHA_MISMATCH")
            require(not local_path(args.result).is_relative_to(production_dir), "HANDOFF_OUTPUT_OVERLAPS_STATE")
            producer = _execute_automation(args, manifest_path, manifest, production=owner)
            response["producer_status"] = producer["status"]
            downstream = owner.last_result
            if downstream is None:
                status = {"WAIT": "HOLD", "FAIL": "BLOCKED"}.get(producer["status"], "NOT_DISPATCHED")
                return {**response, "status": status, "reason": producer["reason"]}
    except OpportunityError as exc:
        return {**response, "reason": exc.code}
    except OrchestrationError as exc:
        return {**response, "reason": str(exc)}
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return {**response, "reason": "HANDOFF_INPUT_OR_STORAGE_INVALID"}

    return {**response, "status": downstream.status, "reason": downstream.reason,
            "accepted_consumer": downstream.accepted_consumer,
            "completed": downstream.completed.to_dict() if downstream.completed else None,
            "event": downstream.event.to_dict() if downstream.event else None}


def run(args) -> int:
    try:
        result = execute(args)
    except OpportunityError as exc:
        print(json.dumps({"error": exc.code, "row": exc.row}, sort_keys=True), file=sys.stderr)
        return 2
    except (ValueError, OSError, UnicodeError, TypeError, KeyError, RecursionError):
        print('{"error":"OPPORTUNITY_INPUT_INVALID"}', file=sys.stderr)
        return 2
    # JSON ASCII escaping keeps exact Unicode portable across Windows consoles.
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False))
    if isinstance(result, dict) and result.get("schema_version") == "A10A03DispatchResult.v1":
        status = result["status"]
        if status == "UNKNOWN":
            return 4
        if status in ("BLOCKED", "FAILED"):
            return 2
        if status in ("HOLD", "PENDING", "RUNNING", "RETRY_SAFE"):
            return 3
        return 0
    if args.opportunity_operation == "automate":
        return 2 if result["status"] == "FAIL" else 3 if result["status"] == "WAIT" else 0
    return 0


MAX_JOB_BYTES = 256 * 1024
MAX_JOB_RESULT_BYTES = 4 * 1024 * 1024
JOB_EFFECTS = ("IMPORT_SIGNALS", "APPEND_HUMAN_AUDIENCE", "AI_RESEARCH", "CREATE_BRIEF")


def _job_object(value, required: set[str], optional: set[str] = frozenset()) -> None:
    require(isinstance(value, dict) and required <= set(value) <= required | optional, "JOB_MANIFEST_INVALID")


def _valid_job_id(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", value) is not None


def validate_automation_manifest(manifest: dict) -> dict:
    """Strict, bounded transport envelope; embedded records use their A10 owners."""
    require(len(canonical_bytes(manifest)) <= MAX_JOB_BYTES, "JOB_MANIFEST_TOO_LARGE")
    _job_object(manifest, {"schema_version", "job_id", "source_sha", "topic", "as_of", "sources",
                           "provider", "ai_assessment", "allowed_effects"}, {"audience", "decision_mode"})
    require(manifest["schema_version"] == "A10AutomationJob.v1", "JOB_MANIFEST_VERSION")
    require(_valid_job_id(manifest["job_id"]), "JOB_ID_INVALID")
    # Reuse existing clock validation without writing any source or proposal.
    propose_opportunities(as_of=manifest["as_of"])
    require(isinstance(manifest["source_sha"], str)
            and re.fullmatch(r"[a-f0-9]{40}(?:[a-f0-9]{24})?", manifest["source_sha"]) is not None, "JOB_SOURCE_SHA_INVALID")
    require(isinstance(manifest["topic"], str) and 0 < len(manifest["topic"].strip()) <= 512, "JOB_TOPIC_INVALID")
    require(type(manifest["ai_assessment"]) is bool, "JOB_ASSESSMENT_BOOLEAN_REQUIRED")
    require(manifest.get("decision_mode", "autonomous") in ("autonomous", "manual"), "JOB_DECISION_MODE_INVALID")
    if manifest["provider"] is not None:
        ai_provider_profile(manifest["provider"])
    effects = manifest["allowed_effects"]
    require(isinstance(effects, list) and all(isinstance(e, str) and e in JOB_EFFECTS for e in effects)
            and len(set(effects)) == len(effects), "JOB_EFFECT_INVALID")
    require(isinstance(manifest["sources"], list) and len(manifest["sources"]) <= 8, "JOB_SOURCE_LIMIT")
    for source in manifest["sources"]:
        _job_object(source, {"file", "format", "sha256", "snapshot"}, {"profile", "dialect", "comparison_context"})
        require(source["format"] in ("csv", "json"), "JOB_SOURCE_FORMAT")
        require(source["format"] != "csv" or "profile" in source and source["profile"] is not None, "JOB_CSV_PROFILE_REQUIRED")
        require(source["format"] != "json" or not {"profile", "dialect"} & set(source), "JOB_JSON_MAPPING_NOT_ALLOWED")
    if manifest.get("audience") is not None:
        _job_object(manifest["audience"], {"file", "sha256", "origin"})
        require(manifest["audience"]["origin"] == "HUMAN", "AUDIENCE_HUMAN_ORIGIN_REQUIRED")
    return manifest


def _job_input(base: Path, name) -> Path:
    require(isinstance(name, str) and 0 < len(name) <= 512, "JOB_INPUT_PATH_INVALID")
    # Check before joining: an absolute/UNC path must not bypass the trusted root.
    relative = Path(name)
    require(not relative.is_absolute() and not relative.drive and not name.startswith(("/", "\\"))
            and ":" not in name, "JOB_INPUT_OUTSIDE_ROOT")
    path = local_path(base / relative)
    require(path.is_relative_to(base), "JOB_INPUT_OUTSIDE_ROOT")
    return path


def _write_job_record(path: Path, value: dict) -> bool:
    """Immutable content write, exclusive reservation and exact replay only."""
    raw = canonical_bytes(value) + b"\n"
    require(len(raw) <= MAX_JOB_RESULT_BYTES, "JOB_RESULT_TOO_LARGE")
    path = local_path(path)
    if path.exists():
        require(path.stat().st_nlink == 1 and read_local(path, limit=MAX_JOB_RESULT_BYTES) == raw, "JOB_STATE_CONFLICT")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    local_path(path)
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return True
    except FileExistsError:
        require(read_local(path, limit=MAX_JOB_RESULT_BYTES) == raw, "JOB_STATE_CONFLICT")
        return False


def run_automation(manifest: dict, *, base_dir: Path, source_sha: str, state_dir: Path,
                   mode: str, permissions=(), selection: dict | None = None,
                   capability: dict | None = None, runner=None,
                   attended_output: bytes | None = None, human_identity: dict | None = None,
                   production=None, result_path: Path | None = None) -> dict:
    """Run genuine M01-M05, then optionally hand its committed result to P1.

    ``production`` is a caller-owned ProductionPipelineOrchestrator with an
    independent source pin and local grant, never a job/model-supplied field.
    Its typed ``last_result`` reports downstream state separately from the
    immutable A10 result. A downstream failure cannot rewrite producer success.
    No completion event is offered on WAIT/FAIL/no-suitable/provider uncertainty.
    A requested immutable transport result must be written before delivery.
    """
    if production is not None:
        from ai_commerce.production_orchestration import ProductionPipelineOrchestrator
        if type(production) is not ProductionPipelineOrchestrator:
            raise CreativeBridgeError("JOB_PRODUCTION_OWNER_REQUIRED")
        production.last_result = None
    result = _run_automation(manifest, base_dir=base_dir, source_sha=source_sha, state_dir=state_dir,
        mode=mode, permissions=permissions, selection=selection, capability=capability, runner=runner,
        attended_output=attended_output, human_identity=human_identity)
    if result_path is not None:
        _write_job_record(result_path, result)
    if production is not None and (result["status"], result["reason"], result["next_step"]) == (
            "COMPLETE", "A03_BRIEF_CREATED", "READY_FOR_A03_DISPATCH"):
        job_dir = local_path(state_dir) / result["job_id"]
        production.complete_a10(result, result_ref=job_dir / (result["event_id"] + ".json"),
                                brief_ref=job_dir / "brief.json")
    return result


def _run_automation(manifest: dict, *, base_dir: Path, source_sha: str, state_dir: Path,
                   mode: str, permissions=(), selection: dict | None = None,
                   capability: dict | None = None, runner=None,
                   attended_output: bytes | None = None, human_identity: dict | None = None) -> dict:
    """Public scheduler seam; reads pinned local inputs and returns a bounded event.

    Only existing SignalLedger/AudienceLedger own data storage. The state directory
    contains those journals plus immutable A10 job, invocation and result receipts;
    there is no parallel evidence database or global executor. Optional actual
    user identity and capabilities are separate trusted runner inputs, never
    manifest/model assertions. Never automatically retry an
    invocation with a reservation, even when the final receipt was lost.
    """
    result = {"schema_version": "A10AutomationResult.v1", "job_id": None, "manifest_sha256": None,
              "source_sha": source_sha if isinstance(source_sha, str) and len(source_sha) <= 64 else None,
              "status": "FAIL", "reason": "JOB_INPUT_INVALID", "next_step": "FIX_INPUT",
              "source_hashes": [], "import_ids": [], "insight_ids": [], "series": [], "hypotheses": [],
              "proposals": [], "ai_task": None, "assessment": None, "handoff": None, "brief": None,
              "selection_template": None, "decision": None, "decision_sha256": None, "brief_sha256": None,
              "metrics": {"time_seconds": "UNKNOWN", "tokens": "UNKNOWN", "human_actions": "UNKNOWN"},
              "publication_status": "NOT_ATTEMPTED", "dispatch_status": "NOT_ATTEMPTED",
              "brief_ref": None, "brief_bytes_sha256": None}
    job_dir, bound = None, False

    def finish(status, reason, next_step):
        result.update(status=status, reason=reason, next_step=next_step, next_state=next_step)
        result["event_id"] = "a10event-" + digest({k: v for k, v in result.items() if k != "event_id"})
        require(len(canonical_bytes(result)) < MAX_JOB_RESULT_BYTES, "JOB_RESULT_TOO_LARGE")
        if bound:
            _write_job_record(job_dir / (result["event_id"] + ".json"), result)
        return result

    try:
        require(mode in ("plan", "offline", "execute"), "JOB_MODE_INVALID")
        validate_automation_manifest(manifest)
        # Copy only through strict JSON to isolate caller mutations during a run.
        manifest = parse_json(canonical_bytes(manifest))
        result.update(job_id=manifest["job_id"], manifest_sha256=digest(manifest))
        require(source_sha == manifest["source_sha"], "JOB_SOURCE_SHA_MISMATCH")
        manual = manifest.get("decision_mode", "autonomous") == "manual"
        require(manual or selection is None and human_identity is None, "JOB_MANUAL_MODE_REQUIRED")
        base = local_path(base_dir)
        job_dir = local_path(local_path(state_dir) / manifest["job_id"])
        if not manifest["sources"]:
            return finish("WAIT", "NO_SIGNAL_DATA", "SUPPLY_PINNED_SIGNALS")
        if mode == "execute" and (manifest["provider"] is None or not manifest["ai_assessment"]):
            return finish("WAIT", "PROVIDER_UNAVAILABLE", "CONFIGURE_AUTHENTICATED_PROVIDER")
        prepared, contexts, total_bytes = [], {}, 0
        for source in manifest["sources"]:
            path = _job_input(base, source["file"])
            require(not path.is_relative_to(job_dir), "JOB_INPUT_OVERLAPS_STATE")
            raw = read_local(path)
            total_bytes += len(raw)
            require(total_bytes <= 32 * 1024 * 1024, "JOB_INPUT_BYTES_LIMIT")
            kwargs = {"source_snapshot": source["snapshot"], "file_format": source["format"],
                      "profile": source.get("profile"), "dialect": source.get("dialect")}
            item = prepare_pinned_source(raw, expected_sha256=source["sha256"], **kwargs)
            require(item.record["import_id"] not in result["import_ids"], "JOB_DUPLICATE_SOURCE")
            result["source_hashes"].append(item.record["raw_sha256"])
            result["import_ids"].append(item.record["import_id"])
            prepared.append((item, kwargs))
            if "comparison_context" in source:
                contexts[item.record["import_id"]] = source["comparison_context"]

        # Preview through real M02/M03 on prepared M01 records. This adapter
        # only supplies get(); it has no persistence or alternative signal logic.
        class PreviewSignals:
            def get(self, import_id):
                for item, _ in prepared:
                    if item.record["import_id"] == import_id:
                        return {"import": item.record, "signals": list(item.signals)}
                raise OpportunityError("JOB_IMPORT_NOT_SELECTED")

        preview = PreviewSignals()
        raw_notes = None
        if manifest.get("audience") is not None:
            audience = manifest["audience"]
            path = _job_input(base, audience["file"])
            require(not path.is_relative_to(job_dir), "JOB_INPUT_OVERLAPS_STATE")
            raw_notes = read_local(path, limit=MAX_NOTES_BYTES)
            require(sha256_bytes(raw_notes) == audience["sha256"], "JOB_AUDIENCE_HASH_MISMATCH")
            prepare_human_audience(raw_notes, origin=audience["origin"], signal_ledger=preview)

        series = build_series(preview, import_ids=result["import_ids"], topic=manifest["topic"],
                              comparison_contexts=contexts or None)
        evaluate_trend(series, as_of=manifest["as_of"])
        required_effects = {"IMPORT_SIGNALS", "CREATE_BRIEF"}
        if raw_notes is not None:
            required_effects.add("APPEND_HUMAN_AUDIENCE")
        if manifest["ai_assessment"] and mode == "execute":
            required_effects.add("AI_RESEARCH")
        result["required_effects"] = sorted(required_effects)
        if mode == "plan":
            # Prepared M01 recorded_at is a real preview clock, not durable
            # evidence. Plan identity binds stable inputs, never that timestamp.
            result["planned_series_inputs_sha256"] = digest({"import_ids": result["import_ids"],
                "topic": manifest["topic"], "comparison_contexts": contexts})
            return finish("PLAN", "LOCAL_PLAN_VALIDATED", "RUN_WITH_EXPLICIT_EFFECT_GRANTS")
        if not required_effects <= set(manifest["allowed_effects"]) & set(permissions):
            return finish("WAIT", "PERMISSION_DENIED", "GRANT_REQUIRED_EFFECTS")
        if attended_output is not None and manifest["provider"] != "chatgpt-attended":
            return finish("FAIL", "ATTENDED_OUTPUT_WRONG_PROVIDER", "FIX_INPUT")
        binding = {"schema_version": "A10AutomationBinding.v1", "job_id": manifest["job_id"],
                   "manifest_sha256": digest(manifest), "manifest": manifest, "mode": mode}
        _write_job_record(job_dir / "job.json", binding)
        bound = True
        signals = SignalLedger(job_dir / "signals.sqlite")
        for item, kwargs in prepared:
            imported = signals.import_bytes(item.raw, **kwargs)
            require(imported["import"]["import_id"] == item.record["import_id"], "JOB_IMPORT_REPLAY_MISMATCH")
        audience_ledger = None
        if raw_notes is not None:
            audience_ledger = AudienceLedger(job_dir / "audience.sqlite")
            insight = audience_ledger.append_bytes(raw_notes, signal_ledger=signals)["insight"]
            result["insight_ids"] = [insight["insight_id"]]
        series = build_series(signals, import_ids=result["import_ids"], topic=manifest["topic"],
                              comparison_contexts=contexts or None)
        result["series"] = [series]
        result["hypotheses"] = [evaluate_trend(series, as_of=manifest["as_of"])]
        result["proposals"] = propose_opportunities(
            trend_series=[series], audience_insight_ids=result["insight_ids"], signal_ledger=signals,
            audience_ledger=audience_ledger, as_of=manifest["as_of"])
        if manifest["ai_assessment"] and mode == "execute" and result["proposals"]:
            sources = [{"ref": "sha256:" + sha, "sha256": sha} for sha in sorted(set(result["source_hashes"]))]
            sources += [{"ref": p["proposal_id"], "sha256": digest(p)} for p in result["proposals"]]
            if raw_notes is not None:
                sha = sha256_bytes(raw_notes)
                sources.append({"ref": "sha256:" + sha, "sha256": sha})
            task = make_ai_research_task(stage="OPPORTUNITY_ASSESSMENT", sources=sources,
                source_sha=source_sha, provider=manifest["provider"],
                context={"job_id": manifest["job_id"], "manifest_sha256": digest(manifest),
                         "topic": manifest["topic"], "as_of": manifest["as_of"], "proposals": result["proposals"],
                         "decision_schema": "A10OpportunityChoice.v1"})
            result["ai_task"] = task
            if manifest["provider"] == "chatgpt-attended":
                result["handoff"] = ai_handoff_packet(task)
                _write_job_record(job_dir / "handoff.json", result["handoff"])
            receipt_path, intent_path = job_dir / "assessment.json", job_dir / "invocation.json"
            if receipt_path.exists():
                assessment = validate_ai_research_receipt(_json(receipt_path, limit=AI_MAX_BYTES), task)
                if assessment["execution"] is not None:
                    require(intent_path.exists(), "JOB_ASSESSMENT_INVOCATION_REQUIRED")
                    require(_json(intent_path) == {"schema_version": "A10AIInvocation.v1", "task_id": task["task_id"],
                        "task_sha256": digest(task), "execution": assessment["execution"]}, "JOB_INVOCATION_BINDING_MISMATCH")
                    require(assessment["execution"]["kind"] != "MOCK_AUTHENTICATED_RUNNER" or runner is not None,
                            "JOB_MOCK_RUNNER_REQUIRED")
            elif intent_path.exists():
                intent = _json(intent_path)
                require(set(intent) == {"schema_version", "task_id", "task_sha256", "execution"}
                        and intent["schema_version"] == "A10AIInvocation.v1" and intent["task_id"] == task["task_id"]
                        and intent["task_sha256"] == digest(task), "JOB_INVOCATION_BINDING_MISMATCH")
                assessment = ai_research_receipt(task, status="UNKNOWN_EFFECT", reason="RESERVED_INVOCATION_UNRESOLVED", effect="UNKNOWN")
                _write_job_record(receipt_path, assessment)
            else:
                def reserve(execution):
                    created = _write_job_record(intent_path, {"schema_version": "A10AIInvocation.v1",
                        "task_id": task["task_id"], "task_sha256": digest(task), "execution": execution})
                    require(created, "JOB_INVOCATION_ALREADY_RESERVED")

                assessment = run_ai_research(task, permissions=permissions, capability=capability, runner=runner,
                                             attended_output=attended_output, before_invoke=reserve)
                if assessment["effect"] != "NOT_ATTEMPTED" or attended_output is not None or intent_path.exists():
                    _write_job_record(receipt_path, assessment)
            result["assessment"] = assessment
            if assessment["status"] != "OK":
                reason = "PROVIDER_UNAVAILABLE" if assessment["status"] == "UNSUPPORTED" else assessment["status"]
                return finish("FAIL" if assessment["status"] == "FAILED" else "WAIT", reason,
                              "RECONCILE_WITH_OPERATOR_NO_RETRY" if assessment["status"] == "UNKNOWN_EFFECT" else "REVIEW_PROVIDER_RECEIPT")
        context = {"proposals": result["proposals"], "job_id": manifest["job_id"],
                   "manifest_sha256": digest(manifest), "source_sha": source_sha}
        if not result["proposals"]:
            # No model invocation is needed to observe an empty deterministic M04.
            actor = "SYSTEM"
        elif manual:
            result["selection_template"] = {"schema_version": "A10OpportunitySelection.v1", "job_id": manifest["job_id"],
                "manifest_sha256": digest(manifest), "proposal_id": None, "proposal_sha256": None, "actor": "HUMAN",
                "decision": "SELECT", "use_ai_assessment": False, "assessment_receipt_id": None}
            if selection is None:
                return finish("WAIT", "SELECTION_REQUIRED", "SUPPLY_AUTHENTICATED_HUMAN_OVERRIDE")
            actor = "HUMAN"
            context.update(selection=selection, human_identity=human_identity,
                           assessment=result["assessment"], assessment_task=result["ai_task"])
        elif mode == "offline":
            actor = "SYSTEM"
        else:
            actor = "BRAIN"
            assessment = result["assessment"]
            if assessment is None or assessment["execution"] is None:
                return finish("WAIT", "PROVIDER_UNAVAILABLE", "CONFIGURE_AUTHENTICATED_CHOOSER")
            if assessment["decision"] is None:
                return finish("WAIT", "AI_DECISION_REQUIRED", "CONFIGURE_STRUCTURED_CHOOSER")
            context.update(assessment=assessment, assessment_task=result["ai_task"])
        decision = make_opportunity_decision(actor=actor, **context)
        decision_path = job_dir / "decision.json"
        if decision_path.exists():
            validate_opportunity_decision(_json(decision_path, limit=AI_MAX_BYTES), **context)
        _write_job_record(decision_path, decision)
        result.update(decision=decision, decision_sha256=digest(decision))
        if decision["outcome"] == "NO_SUITABLE_OPPORTUNITY":
            return finish("COMPLETE", "NO_SUITABLE_OPPORTUNITY", "NO_SUITABLE_OPPORTUNITY")
        brief = selected_opportunity_to_brief(decision=decision, **context)
        if actor == "HUMAN":
            _write_job_record(job_dir / "selection.json", selection)
        _write_job_record(job_dir / "brief.json", brief)
        brief_path = local_path(job_dir / "brief.json")
        brief_raw = read_local(brief_path, limit=MAX_RECORD_BYTES)
        require(brief_raw == canonical_bytes(brief) + b"\n", "JOB_BRIEF_BYTES_MISMATCH")
        result.update(brief=brief, brief_sha256=digest(brief), brief_ref=str(brief_path),
                      brief_bytes_sha256=sha256_bytes(brief_raw))
        return finish("COMPLETE", "A03_BRIEF_CREATED", "READY_FOR_A03_DISPATCH")
    except OpportunityError as exc:
        # A race after invocation reservation is unresolved, never permission to
        # launch again. Other bounded validation/storage errors fail closed.
        result.update(status="WAIT" if exc.code == "JOB_INVOCATION_ALREADY_RESERVED" else "FAIL",
                      reason=exc.code, next_step="RECONCILE_WITH_OPERATOR_NO_RETRY" if exc.code == "JOB_INVOCATION_ALREADY_RESERVED" else "FIX_INPUT")
        if exc.code == "JOB_INVOCATION_ALREADY_RESERVED" and result["ai_task"] is not None:
            result["assessment"] = ai_research_receipt(result["ai_task"], status="UNKNOWN_EFFECT",
                reason="RESERVED_INVOCATION_UNRESOLVED", effect="UNKNOWN")
            result["reason"] = "UNKNOWN_EFFECT"
    except (OSError, ValueError, UnicodeError, TypeError, KeyError, RecursionError):
        result.update(status="FAIL", reason="JOB_INPUT_OR_STORAGE_INVALID", next_step="FIX_INPUT")
    # Error results cannot contain a fabricated success or a half-built brief.
    result["brief"] = None
    result["brief_sha256"] = None
    result["brief_ref"] = None
    result["brief_bytes_sha256"] = None
    result["next_state"] = result["next_step"]
    result["event_id"] = "a10event-" + digest({k: v for k, v in result.items() if k != "event_id"})
    if len(canonical_bytes(result)) >= MAX_JOB_RESULT_BYTES:
        compact = {k: result[k] for k in ("schema_version", "job_id", "manifest_sha256", "source_sha", "publication_status")}
        compact.update(status="FAIL", reason="JOB_RESULT_TOO_LARGE", next_step="REDUCE_JOB", brief=None)
        compact["event_id"] = "a10event-" + digest(compact)
        return compact
    return result
