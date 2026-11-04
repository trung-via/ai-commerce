"""One attended local session around B0.3, never a second execution engine."""
from __future__ import annotations

from hashlib import sha256

from . import attended_worker as w
from . import handoff, metrics
from .contracts import canonical_bytes
from .receipt import put_new

BASELINE_SHA = "8d3ec319a10231b5603b2a435c83eb2f8b1c66d5"
SESSION_NAME = "SESSION.json"
SESSION_FIELDS = set("schema_version operator_baseline_sha operator_dir private_dir export_dir inputs intent session_sha256".split())
INPUT_FIELDS = set("directive_path source_sha asset_root assets rights".split())

# Output is finite authored text; neither user text nor native exception details
# are interpolated into these reasons/actions.
MESSAGES = {
    "SETUP_COMPLETE": ("SETUP", "Local inputs were retained and validated.", "Run once with --session and --human-attended."),
    "READY": ("READY", "No B0.4 run has been observed.", "Run once with --session and --human-attended."),
    "WAIT_BRAIN": ("WAIT_BRAIN", "Exact native offline evidence is waiting for Brain.", "Use handoff; manually share ProductJourneyReceipt.v1.json, attended-evidence.json and OFFLINE_DRAFT_SUMMARY.json with Brain in the SAME ChatGPT chat."),
    "PRE_START_STOPPED": ("HOLD", "The original journey stopped before START.", "Do not retry. Preserve the journal and inspect exact A06 owner evidence with a Human."),
    "OUTPUT_INSPECTION_REQUIRED": ("HOLD", "START exists; completion is uncertain.", "Do not retry. Inspect exact A06 owner evidence; use attended reconcile only for complete matching artifacts and exact stopped generation."),
    "TECHNICAL_QA": ("HOLD", "Output awaits verify-only technical completion.", "Confirm the worker stopped; use reconcile with --human-attended and exact --stopped-generation."),
    "INCOMPLETE_ATTEMPT": ("UNKNOWN", "An observed attempt has no durable terminal result.", "Do not retry. Preserve the trail and journal; inspect exact A06 evidence and stopped lease generation."),
    "RUN_STOPPED_NO_RETRY": ("HOLD", "An earlier run attempt ended without confirmed native journal evidence.", "Do not retry. Preserve the trail and inspect the original native owner evidence with a Human."),
    "UNOBSERVED_JOURNEY": ("UNKNOWN", "Existing B0.3 work was outside the observed B0.4 boundary.", "Preserve original evidence; request Human inspection. No successful B0.4 run is inferred."),
    "HUMAN_ATTENDED_REQUIRED": ("HOLD", "A real attended invocation is required.", "A Human must attend run or reconcile and supply --human-attended."),
    "LEASE_OR_PROCESS_HOLD": ("HOLD", "Concurrent ownership or a stale lease generation blocks this call.", "Confirm the original worker stopped and inspect the exact lease generation. Never delete a lease or journal."),
    "INPUT_OR_EVIDENCE_HOLD": ("HOLD", "Inputs, source, rights, paths or native evidence did not verify.", "Preserve original files and journal; inspect the exact source, asset, rights, checkpoint and owner evidence."),
    "HANDOFF_HOLD": ("HOLD", "The receipt package is missing, partial, conflicting or tampered.", "Preserve all package and journal bytes; inspect the exact native receipt and A06 evidence. Do not replace files."),
    "TRAIL_HOLD": ("HOLD", "The private observation trail did not verify.", "Preserve the original trail and anchors for Human inspection; do not truncate or reset them."),
    "CLI_ARGUMENTS_INVALID": ("HOLD", "The command or arguments are not supported.", "Use --help; later commands accept a local --session path, with attended recovery flags only."),
    "OFFLINE_BOUNDARY_HOLD": ("HOLD", "A call crossed the bounded offline process boundary.", "Preserve evidence and request source review; no provider or coding-agent action is authorized."),
}


def response(code, **details):
    state, reason, action = MESSAGES[code]
    return {"state": state, "code": code, "reason": reason, "next_action": action, **details}


def error_code(exc):
    if type(exc) is metrics.MetricsHold:
        return "OFFLINE_BOUNDARY_HOLD" if str(exc) == "OFFLINE_BOUNDARY_HOLD" else "TRAIL_HOLD"
    if type(exc) is w.WorkerHold:
        if str(exc) in {"PROCESS_LOCK_BUSY", "STALE_FENCING_GENERATION", "STALE_LEASE_ATTENDED_RECONCILIATION_REQUIRED"}:
            return "LEASE_OR_PROCESS_HOLD"
        if str(exc) == "HUMAN_ATTENDED_REQUIRED":
            return "HUMAN_ATTENDED_REQUIRED"
        if str(exc).startswith("HANDOFF_") or str(exc) == "RECEIPT_BYTES_MISMATCH":
            return "HANDOFF_HOLD"
        if str(exc) in {"PENDING_OFFLINE_EFFECT_RECONCILIATION", "UNKNOWN_EFFECT"}:
            return "OUTPUT_INSPECTION_REQUIRED"
    return "INPUT_OR_EVIDENCE_HOLD"


def _directories(operator_dir, private_dir, export_dir, asset_root):
    roots = [w._private(p) for p in (operator_dir, private_dir, export_dir, asset_root)]
    w._need(all(a != b and a not in b.parents and b not in a.parents
                for i, a in enumerate(roots) for b in roots[i + 1:]), "SEPARATE_DIRECTORIES_REQUIRED")
    return roots


def _native_inputs(inputs):
    directive, paths, intent = w._inputs(**inputs, human_attended=True)
    w._need(w._bytes(w.Path(inputs["directive_path"])) == directive.raw, "NATIVE_SEALED_DIRECTIVE_REQUIRED")
    w._need(all(a["ref"] == "asset:" + a["sha256"] for a in directive.to_dict()["assets"]),
            "CONTENT_ADDRESSED_ASSET_REF_REQUIRED")
    return directive, paths, intent


def setup(*, operator_dir, private_dir, export_dir, directive_path, source_sha,
          asset_root, assets, rights=()):
    op, private, dest, _ = _directories(operator_dir, private_dir, export_dir, asset_root)
    w._need(all(not any(p.iterdir()) for p in (op, private, dest)), "NEW_EMPTY_DIRECTORIES_REQUIRED")
    inputs = {"directive_path": directive_path, "source_sha": source_sha,
              "asset_root": asset_root, "assets": tuple(assets), "rights": tuple(rights)}
    _, _, intent = _native_inputs(inputs)
    body = {"schema_version": "OperatorSession.v1", "operator_baseline_sha": BASELINE_SHA,
            "operator_dir": str(op), "private_dir": str(private), "export_dir": str(dest),
            "inputs": {**inputs, "assets": list(assets), "rights": list(rights)}, "intent": intent}
    body["session_sha256"] = sha256(canonical_bytes(body)).hexdigest()
    with w._Lock(op, create=True):
        w._need({p.name for p in op.iterdir()} == {".attended.lock"}, "NEW_EMPTY_DIRECTORIES_REQUIRED")
        put_new(op / SESSION_NAME, canonical_bytes(body))
        metrics.initialize(op, body["session_sha256"])
    return response("SETUP_COMPLETE")


def load(session_path):
    path = w._local(session_path)
    w._need(path.name == SESSION_NAME, "SESSION_PATH_REQUIRED")
    session = w._json(path)
    w._need(type(session) is dict and set(session) == SESSION_FIELDS
            and session["schema_version"] == "OperatorSession.v1"
            and session["operator_baseline_sha"] == BASELINE_SHA, "SESSION_INVALID")
    w._need(session["session_sha256"] == sha256(canonical_bytes(
        {k: v for k, v in session.items() if k != "session_sha256"})).hexdigest(), "SESSION_INVALID")
    inputs = session["inputs"]
    w._need(type(inputs) is dict and set(inputs) == INPUT_FIELDS
            and type(inputs["assets"]) is list and type(inputs["rights"]) is list, "SESSION_INVALID")
    op, root, dest, _ = _directories(session["operator_dir"], session["private_dir"], session["export_dir"], inputs["asset_root"])
    w._need(path == op / SESSION_NAME, "SESSION_PATH_MISMATCH")
    inputs = {**inputs, "assets": tuple(inputs["assets"]), "rights": tuple(inputs["rights"])}
    directive, _, intent = _native_inputs(inputs)
    w._need(session["intent"] == intent, "SESSION_INPUT_SOURCE_OR_PATH_DRIFT")
    return session, op, root, directive, intent, {**inputs, "human_attended": True}


def _admission(op, session, intent, rows):
    path = op / "run-admission.json"
    if not path.exists():
        return False
    value = w._json(path)
    w._need(type(value) is dict and set(value) == {"session_sha256", "intent_sha256", "attempt_id"}
            and value["session_sha256"] == session["session_sha256"]
            and value["intent_sha256"] == sha256(canonical_bytes(intent)).hexdigest()
            and any(r["phase"] == "ATTEMPT" and r["command"] == "run"
                    and r["native_admission"] is True
                    and r["attempt_id"] == value["attempt_id"] for r in rows), "RUN_ADMISSION_EVIDENCE_INVALID")
    return True


def _inspect(session, root, inputs, *, prior_run, native_admitted=False, incomplete_run=False):
    if not any(root.iterdir()):
        return response("INCOMPLETE_ATTEMPT" if incomplete_run else "RUN_STOPPED_NO_RETRY" if prior_run else "READY")
    if not native_admitted:
        return response("UNOBSERVED_JOURNEY")
    with w._Lock(root):
        checked = w.inspect_once(private_dir=str(root), **inputs)
        if (w.Path(session["operator_dir"]) / "handoff-seal.json").exists() or any(
                (w.Path(session["export_dir"]) / name).exists() for name in
                (handoff.RECEIPT, handoff.PACKET, handoff.SUMMARY)):
            directive, _, intent = w._inputs(**inputs)
            handoff.check_existing(session, root, directive, intent)
    return response({"INPUT_VALIDATED": "PRE_START_STOPPED", "RUNNING": "OUTPUT_INSPECTION_REQUIRED",
                     "TECHNICAL_QA": "TECHNICAL_QA", "WAIT_BRAIN": "WAIT_BRAIN"}[checked["state"]])


def invoke(command, *, session_path, human_attended=False, stopped_generation=None):
    """No automatic replay. Status/metrics never mutate the B0.2 journal."""
    try:
        session, op, root, directive, intent, inputs = load(session_path)
        with w._Lock(op):
            rows = metrics.read(op, session["session_sha256"])
            runs = [r for r in rows if r["command"] == "run" and r["phase"] == "ATTEMPT"]
            completed = {r["attempt_id"] for r in rows if r["phase"] == "RESULT"}
            incomplete_run = any(r["attempt_id"] not in completed for r in runs)
            admitted = _admission(op, session, intent, rows)
            preexisting = not admitted and any(root.iterdir())
            if command == "metrics":
                summary = metrics.summarize(rows, preexisting=preexisting)
                # These are observational counts even when native state HOLDs.
                # Reopen native evidence; never present corrupt history as READY.
                with metrics.Boundary():
                    try:
                        result = _inspect(session, root, inputs, prior_run=bool(runs), native_admitted=admitted,
                                          incomplete_run=incomplete_run)
                    except Exception as exc:
                        result = response(error_code(exc))
                if summary["incomplete_unknown"] and result["state"] != "HOLD":
                    result = response("INCOMPLETE_ATTEMPT")
                return {**result, "metrics": summary}
            w._need(command in metrics.COMMANDS, "COMMAND_INVALID")
            attempt_id = metrics.uuid.uuid4().hex
            start_wall, start_active = metrics.time.time_ns(), metrics.time.perf_counter_ns()
            admit = command == "run" and human_attended is True and not runs and not any(root.iterdir())
            first = metrics.append(op, session["session_sha256"], command=command, attempt_id=attempt_id,
                                   phase="ATTEMPT", wall_ns=start_wall, native_admission=admit)
            with metrics.Boundary() as boundary:
                try:
                    if command in {"run", "reconcile"}:
                        w._need(human_attended is True, "HUMAN_ATTENDED_REQUIRED")
                    if command == "run":
                        if runs or any(root.iterdir()):
                            result = _inspect(session, root, inputs, prior_run=bool(runs), native_admitted=admitted,
                                              incomplete_run=incomplete_run)
                        else:
                            # This flushed local observation binds admission to
                            # an already-durable attempt while the native dir is
                            # empty. Inspection of external work cannot mint it.
                            put_new(op / "run-admission.json", canonical_bytes({
                                "session_sha256": session["session_sha256"],
                                "intent_sha256": sha256(canonical_bytes(intent)).hexdigest(),
                                "attempt_id": attempt_id}))
                            native = w.run_once(private_dir=str(root), **inputs)
                            w._need(native["state"] == "WAIT_BRAIN", "WAIT_BRAIN_REQUIRED")
                            result = response("WAIT_BRAIN")
                    elif command == "status":
                        result = _inspect(session, root, inputs, prior_run=bool(runs), native_admitted=admitted,
                                          incomplete_run=incomplete_run)
                    elif not admitted:
                        result = response("UNOBSERVED_JOURNEY")
                    elif command == "reconcile":
                        # Exact artifact inspection precedes the native mutating resume.
                        with w._Lock(root):
                            _, history, current = w._read(root, directive, intent)
                            w._need(type(stopped_generation) is int and stopped_generation > 0
                                    and stopped_generation == history.checkpoint.generation, "STALE_FENCING_GENERATION")
                            w.inspect_once(private_dir=str(root), expected_generation=stopped_generation, **inputs)
                            if current.state == "INPUT_VALIDATED":
                                result = response("PRE_START_STOPPED")
                            else:
                                result = None
                        if result is None:
                            native = w.resume_once(private_dir=str(root), expected_generation=stopped_generation,
                                reconcile_stopped_generation=stopped_generation if history.lease else None, **inputs)
                            w._need(native["state"] == "WAIT_BRAIN", "WAIT_BRAIN_REQUIRED")
                            result = response("WAIT_BRAIN")
                    else:
                        with w._Lock(root):
                            details = (handoff.export if command == "handoff" else handoff.learning_template)(
                                session, root, directive, intent)
                        result = response("WAIT_BRAIN", **details)
                except Exception as exc:
                    result = response(error_code(exc))
            now = metrics.time.time_ns()
            active = max(0, metrics.time.perf_counter_ns() - start_active)
            elapsed = max(0, now - first["wall_ns"])
            outcome = "UNKNOWN" if result["state"] == "UNKNOWN" else "HOLD" if result["state"] == "HOLD" else "PASS"
            metrics.append(op, session["session_sha256"], command=command, attempt_id=attempt_id,
                phase="RESULT", outcome=outcome, wall_ns=now, active_ns=active,
                wall_elapsed_ns=elapsed, clock_drift=now < start_wall or abs(elapsed - active) > 1_000_000_000,
                counters=boundary.counts)
            return result
    except Exception as exc:
        return response(error_code(exc))
