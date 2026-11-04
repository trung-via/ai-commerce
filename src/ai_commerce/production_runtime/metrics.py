"""Private B0.4 observations, independent of the authoritative B0.2 journal.

Flushed append-only records plus retained immutable head anchors detect accidental
tail rollback. Replacing both under the same SID is NOT defeated. No commerce
ledger, external cost estimate, host-wide agent counter or learning authority.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re

from . import attended_worker as w
from .contracts import canonical_bytes
from .receipt import put_new

os = __import__("os")
time = __import__("time")
uuid = __import__("uuid")
sys = __import__("sys")
shutil = __import__("shutil")
subprocess = __import__("subprocess")

MAX_RECORDS = 2048
MAX_TRAIL_BYTES = 4 * 1024 * 1024
COMMANDS = {"run", "status", "reconcile", "handoff", "learning-template"}
ZERO = "0" * 64


class MetricsHold(ValueError):
    pass


def need(ok):
    if not ok:
        raise MetricsHold("OPERATOR_TRAIL_HOLD")


def initialize(root, session_sha):
    put_new(root / "audit.jsonl", b"")
    put_new(root / "audit-genesis.json", canonical_bytes({
        "schema_version": "OperatorTrail.v1", "session_sha256": session_sha}))


def read(root, session_sha):
    try:
        return _read(root, session_sha)
    except MetricsHold:
        raise
    except Exception:
        raise MetricsHold("OPERATOR_TRAIL_HOLD") from None


def _read(root, session_sha):
    """Existing-only, canonical, closed and bounded; never repair a damaged tail."""
    need(w._json(root / "audit-genesis.json") == {
        "schema_version": "OperatorTrail.v1", "session_sha256": session_sha})
    raw = w._bytes(root / "audit.jsonl", MAX_TRAIL_BYTES)
    need(not raw or raw.endswith(b"\n"))
    lines = raw.splitlines()
    need(len(lines) <= MAX_RECORDS)
    rows, attempts, terminals, events = [], {}, set(), set()
    previous = ZERO
    fields = set("seq event_id attempt_id phase command outcome wall_ns active_ns wall_elapsed_ns clock_drift counters native_admission prev_sha256 checksum".split())
    for seq, line in enumerate(lines, 1):
        row = json.loads(line)
        need(type(row) is dict and set(row) == fields and canonical_bytes(row) == line)
        body = {k: v for k, v in row.items() if k != "checksum"}
        need(row["checksum"] == sha256(canonical_bytes(body)).hexdigest()
             and row["seq"] == seq and type(row["seq"]) is int
             and row["prev_sha256"] == previous and row["command"] in COMMANDS)
        for key in ("event_id", "attempt_id"):
            need(type(row[key]) is str and re.fullmatch(r"[0-9a-f]{32}", row[key]) is not None)
        need(row["event_id"] not in events)
        events.add(row["event_id"])
        need(type(row["wall_ns"]) is int and 0 <= row["wall_ns"] < 2**63)
        if row["phase"] == "ATTEMPT":
            need(row["attempt_id"] not in attempts and row["outcome"] is None
                 and all(row[k] is None for k in ("active_ns", "wall_elapsed_ns", "clock_drift", "counters"))
                 and type(row["native_admission"]) is bool
                 and (not row["native_admission"] or row["command"] == "run"))
            attempts[row["attempt_id"]] = row
        else:
            need(row["phase"] == "RESULT" and row["attempt_id"] in attempts
                 and row["attempt_id"] not in terminals and row["outcome"] in {"PASS", "HOLD", "UNKNOWN"}
                 and row["native_admission"] is None)
            first = attempts[row["attempt_id"]]
            need(row["command"] == first["command"] and type(row["clock_drift"]) is bool)
            need(all(type(row[k]) is int and 0 <= row[k] < 2**63 for k in ("active_ns", "wall_elapsed_ns")))
            need(row["wall_elapsed_ns"] == max(0, row["wall_ns"] - first["wall_ns"]))
            counts = row["counters"]
            need(type(counts) is dict and set(counts) == {"media_processes", "network_attempts", "other_process_attempts"}
                 and all(type(v) is int and 0 <= v <= 100000 for v in counts.values()))
            need(row["outcome"] != "PASS" or not (counts["network_attempts"] or counts["other_process_attempts"]))
            terminals.add(row["attempt_id"])
        rows.append(row)
        previous = row["checksum"]
    anchors = list(root.glob("audit-head-*.json"))
    need(len(anchors) <= MAX_RECORDS)
    for path in anchors:
        need(re.fullmatch(r"audit-head-[0-9]{6}\.json", path.name) is not None)
        seq = int(path.stem.rsplit("-", 1)[1])
        need(1 <= seq <= len(rows))
        need(w._json(path) == {"seq": seq, "checksum": rows[seq - 1]["checksum"]})
    # A missing anchor is also ambiguous, including a crash after append/fsync.
    need(len(anchors) == len(rows))
    return rows


def append(root, session_sha, *, command, attempt_id, phase, outcome=None,
           active_ns=None, wall_elapsed_ns=None, clock_drift=None, counters=None, wall_ns=None,
           native_admission=None):
    """Caller holds the native operator-directory OS lock for the whole call."""
    rows = read(root, session_sha)
    # Reserve a terminal slot BEFORE admitting any call, including at capacity.
    need(len(rows) + (2 if phase == "ATTEMPT" else 1) <= MAX_RECORDS and command in COMMANDS)
    row = {"seq": len(rows) + 1, "event_id": uuid.uuid4().hex,
        "attempt_id": attempt_id, "phase": phase, "command": command,
        "outcome": outcome, "wall_ns": time.time_ns() if wall_ns is None else wall_ns,
        "active_ns": active_ns, "wall_elapsed_ns": wall_elapsed_ns,
        "clock_drift": clock_drift, "counters": counters,
        "native_admission": bool(native_admission) if phase == "ATTEMPT" else None,
        "prev_sha256": rows[-1]["checksum"] if rows else ZERO}
    row["checksum"] = sha256(canonical_bytes(row)).hexdigest()
    # Validate the proposed event *before* making an irreversible append.
    need(phase in {"ATTEMPT", "RESULT"})
    matching = [r for r in rows if r["attempt_id"] == attempt_id]
    need((phase == "ATTEMPT" and not matching and outcome is None)
         or (phase == "RESULT" and len(matching) == 1 and matching[0]["phase"] == "ATTEMPT"
             and matching[0]["command"] == command and outcome in {"PASS", "HOLD", "UNKNOWN"}))
    raw = canonical_bytes(row) + b"\n"
    need(len(raw) <= 2048)
    need((root / "audit.jsonl").stat().st_size + len(raw)
         + (2048 if phase == "ATTEMPT" else 0) <= MAX_TRAIL_BYTES)
    w._local(str(root / "audit.jsonl"))
    with (root / "audit.jsonl").open("ab", buffering=0) as stream:
        need(stream.write(raw) == len(raw))
        os.fsync(stream.fileno())
    put_new(root / f"audit-head-{row['seq']:06d}.json", canonical_bytes({"seq": row["seq"], "checksum": row["checksum"]}))
    read(root, session_sha)
    return row


# A process-local audit boundary is active only during the actual B0.4 call.
# No global host/coding-agent observation is inferred from this boundary.
_active = None
_installed = False


def _audit(name, args):
    if _active is None:
        return
    counts, allowed = _active
    if name.startswith("socket.") and name != "socket.__new__":
        counts["network_attempts"] += 1
        raise MetricsHold("OFFLINE_BOUNDARY_HOLD")
    if name == "subprocess.Popen":
        exe, argv, _, _ = args
        if isinstance(argv, (list, tuple)):
            valid = bool(argv) and os.path.normcase(str(argv[0])) in allowed
        else:
            valid = type(argv) is str and any(
                argv.startswith(subprocess.list2cmdline([path]) + " ") for path in allowed.values())
        valid = valid and (exe is None or os.path.normcase(str(exe)) in allowed)
        if valid:
            counts["media_processes"] += 1
            return
        counts["other_process_attempts"] += 1
        raise MetricsHold("OFFLINE_BOUNDARY_HOLD")
    if name in {"os.system", "os.exec", "os.posix_spawn", "os.spawn", "pty.spawn"}:
        counts["other_process_attempts"] += 1
        raise MetricsHold("OFFLINE_BOUNDARY_HOLD")


class Boundary:
    def __enter__(self):
        global _installed, _active
        need(_active is None)
        if not _installed:
            sys.addaudithook(_audit)
            _installed = True
        self.counts = {"media_processes": 0, "network_attempts": 0, "other_process_attempts": 0}
        allowed = {os.path.normcase(p): p for n in ("ffmpeg", "ffprobe") if (p := shutil.which(n))}
        _active = (self.counts, allowed)
        return self

    def __exit__(self, *args):
        global _active
        _active = None


def summarize(rows, *, preexisting=False):
    attempts = {r["attempt_id"]: r for r in rows if r["phase"] == "ATTEMPT"}
    results = {r["attempt_id"]: r for r in rows if r["phase"] == "RESULT"}
    incomplete = len(attempts.keys() - results.keys())
    outcomes = {k: sum(r["outcome"] == k for r in results.values()) for k in ("PASS", "HOLD", "UNKNOWN")}
    recovery = {k: sum(r["command"] == "reconcile" and r["outcome"] == k for r in results.values())
                for k in ("PASS", "HOLD", "UNKNOWN")}
    recovery["INCOMPLETE_UNKNOWN"] = sum(a["command"] == "reconcile" and key not in results
                                         for key, a in attempts.items())
    return {"observed_b04_attempts": len(attempts), "outcomes": outcomes,
        "incomplete_unknown": incomplete, "recovery_outcomes": recovery,
        "elapsed_wall_ns": max(0, rows[-1]["wall_ns"] - rows[0]["wall_ns"]) if rows else 0,
        "active_invocation_ns": sum(r["active_ns"] for r in results.values()),
        "clock_drift_observed": any(r["clock_drift"] for r in results.values()),
        "time_limits": "Wall clock may drift; active is monotonic per completed invocation; crashed active time UNKNOWN; intervals may include Human wait.",
        "coding_agent_calls": {"target": 0, "observed": 0 if results else None,
            "boundary": "AUDITED_B04_CALLS_ONLY" if results else "NOT_INSTRUMENTED",
            "outside_boundary": "UNKNOWN_NOT_INSTRUMENTED"},
        "preexisting_or_external_journeys": "UNKNOWN" if preexisting else "NOT_OBSERVED_OUTSIDE_BOUNDARY",
        "external_media_costs": "UNKNOWN_NOT_INSTRUMENTED", "revenue": "UNKNOWN_NOT_INSTRUMENTED",
        "anti_rollback_proof": "NOT_PROVEN_AGAINST_REPLACEMENT_OF_TRAIL_AND_ANCHORS"}
