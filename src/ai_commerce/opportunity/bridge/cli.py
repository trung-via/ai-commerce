"""Local JSON CLI adapters over the integrated M01-M05 callable APIs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai_commerce.creative import CreativeStore

from ..audience import AudienceLedger, signal_evidence_ref
from ..audience.models import MAX_NOTES_BYTES, prepare_bytes as prepare_notes
from ..core.models import OpportunityError, parse_json
from ..proposals import propose_opportunities
from ..signals import SignalLedger, load_mapping, load_source_snapshot, read_local
from ..signals.files import local_path
from ..trends import TrendRule, build_series, evaluate_trend
from .briefs import CreativeBridgeError, freeform_brief, opportunity_to_brief
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
    return 0
