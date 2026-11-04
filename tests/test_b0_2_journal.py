"""Offline synthetic fixtures ONLY; no provider or Human/Brain attestation.

Child processes below exercise local SQLite/Windows process termination. They
are test harnesses, never HostOps, a production worker, or provider adapters.
"""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import urllib.request

import pytest

from ai_commerce.production_runtime.contracts import draft_authorization, seal_contract, validate_contract
from ai_commerce.production_runtime.journal import (
    Checkpoint, Conflict, EffectTarget, Journal, JournalHold, Lease, MAX_TTL, OwnerRef,
)
from ai_commerce.production_runtime.recovery import recover
from ai_commerce.production_runtime.state_machine import Event, EventKind, EvidencePin, State

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "76212ef690347ed380753da8192a56caaabf2425"
JOURNAL_ID = "fixture:journal"
JOURNEY_ID = "fixture:journey"
TARGET = EffectTarget("PUBLISH", "provider:fixture", "account:fixture", "surface:fixture", "a" * 64)
QA = EvidencePin("MEDIA_CREATIVE", "fixture:qa", "b" * 64, "SYNTHETIC_FIXTURE")
OWNER_REFS = tuple(OwnerRef(ledger, f"fixture:{ledger}", char * 64)
                   for ledger, char in zip(("A08", "A09", "A15", "A16"), "cdef"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("B0.2 attempted external access")
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(urllib.request, "urlopen", deny)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", deny)


def directive(journey_id=JOURNEY_ID, *, product_ref=None):
    return seal_contract("CreativeDirective", {
        "schema_version": "CreativeDirective.v1", "journey_id": journey_id, "source_sha": SOURCE,
        "directive_version": 1, "creative": {"freeform": "SYNTHETIC_PRIVATE_TEXT_DO_NOT_PERSIST", "product_ref": product_ref},
        "scope": {"action_class": "PUBLISH", "provider_ref": TARGET.provider_ref,
                  "account_ref": TARGET.account_ref, "surface": TARGET.surface, "asset_sha256": TARGET.content_sha256},
        "assets": [], "action_classes": ["PLAN_OFFLINE", "PUBLISH"], "allowed_tools": ["tool:offline"],
        "provenance": {"actor": "FIXTURE", "actor_ref": "fixture:author", "evidence_type": "SYNTHETIC_FIXTURE",
                       "source_ref": "fixture:input", "source_sha": SOURCE}, "authorization": draft_authorization()})


def opened(path, **kwargs):
    return Journal.open(str(path), journal_id=JOURNAL_ID, source_sha=SOURCE, **kwargs)


def recovered(journal, record=None, **kwargs):
    return recover(journal.path, journal_id=JOURNAL_ID, source_sha=SOURCE,
                   journey_id=JOURNEY_ID, directive=record, **kwargs)


@pytest.fixture
def prepared(tmp_path):
    path = tmp_path / "pulse #1-é.sqlite"
    journal = Journal.create(str(path), journal_id=JOURNAL_ID, source_sha=SOURCE)
    lease = journal.acquire(owner="writer:first", at=10, ttl=100, event_id="lease:first")
    record = directive()
    journal.create_journey(record, directive_ref="directive:fixture", lease=lease,
                           at=11, event_id="event:input", owner_refs=OWNER_REFS)
    return journal, lease, record


def advance(journal, lease, record, journey_id=JOURNEY_ID, *, at=12):
    for sequence, kind, fields in (
        (1, EventKind.START, {"tool": "tool:offline"}),
        (2, EventKind.OUTPUT_READY, {"evidence": (QA,)}),
        (3, EventKind.QA_PASSED, {"evidence": (QA,)}),
    ):
        journal.append_event(Event(f"event:{journey_id.split(':')[1]}-{kind.value}", kind, journey_id, SOURCE, **fields),
                             directive=record, lease=lease, at=at, expected_sequence=sequence)


def reserve(journal, lease, record, **kwargs):
    values = {"directive": record, "lease": lease, "at": 15, "event_id": "event:intent", "expected_sequence": 4}
    values.update(kwargs)
    return journal.reserve_intent(JOURNEY_ID, TARGET, **values)


def observation(journal, lease, intent, **kwargs):
    values = {"lease": lease, "at": 16, "event_id": "event:observation", "expected_sequence": 5,
              "effect_key": intent.effect_key, "request_sha256": intent.request_sha256,
              "outcome": "CONFIRMED", "evidence_ref": "fixture:receipt", "evidence_sha256": "1" * 64}
    values.update(kwargs)
    return journal.record_observation(JOURNEY_ID, **values)


def test_create_reopen_replay_and_initial_null_product(prepared):
    journal, lease, record = prepared
    advance(journal, lease, record)
    first = recovered(journal, record)
    second = recovered(opened(journal.path), record)
    assert first == second
    assert first.state == "WAIT_BRAIN" and first.snapshot.state == State.WAIT_BRAIN
    assert [seq for seq, _, _ in first.event_sequence] == [1, 2, 3, 4]
    assert first.owner_refs == OWNER_REFS
    assert first.evidence_sha256 == second.evidence_sha256
    assert journal.inspect().records[1].data()["product_ref"] is None
    assert not first.provider_verified and not first.may_retry
    assert first.anti_rollback_proof == "NOT_PROVEN"


def test_directive_and_private_text_are_not_persisted(prepared):
    journal, _, record = prepared
    raw = Path(journal.path).read_bytes()
    assert b"SYNTHETIC_PRIVATE_TEXT_DO_NOT_PERSIST" not in raw
    assert b"freeform" not in raw and b"provenance" not in raw
    initial = journal.inspect().records[1].data()
    assert initial["directive_sha256"] == record.to_dict()["directive_sha256"]
    assert initial["directive_record_sha256"] == sha256(record.raw).hexdigest()
    assert recovered(journal).state == "HOLD"  # external pin unavailable, never trust state text


def test_immutable_records_and_no_second_business_ledger(prepared):
    journal, lease, record = prepared
    advance(journal, lease, record)
    reserve(journal, lease, record)
    with sqlite3.connect(journal.path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"metadata", "records", "journeys", "intents"}
        for table in tables:
            for action in (f"DELETE FROM {table}", f"UPDATE {table} SET rowid=rowid"):
                with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
                    conn.execute(action)
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    with pytest.raises(FrozenInstanceError):
        journal.inspect().records[0].sha256 = "0" * 64
    assert recovered(journal, record).owner_refs == OWNER_REFS


def test_event_id_dedup_across_journeys(prepared):
    journal, lease, record = prepared
    second = directive("fixture:second")
    with pytest.raises(Conflict, match="DUPLICATE_EVENT_ID"):
        journal.create_journey(second, directive_ref="directive:second", lease=lease, at=12, event_id="event:input")
    assert len(journal.inspect().records) == 2


def test_expected_sequence_cas_and_transaction_failure_roll_back(prepared, monkeypatch):
    journal, lease, record = prepared
    event = Event("event:start", EventKind.START, JOURNEY_ID, SOURCE, tool="tool:offline")
    before = journal.inspect()
    with pytest.raises(Conflict, match="EXPECTED_SEQUENCE_CONFLICT"):
        journal.append_event(event, directive=record, lease=lease, at=12, expected_sequence=0)
    original = Journal._insert
    def fail_after_insert(*args, **kwargs):
        original(*args, **kwargs)
        raise sqlite3.OperationalError("synthetic injected transaction failure")
    with monkeypatch.context() as patch:
        patch.setattr(Journal, "_insert", fail_after_insert)
        with pytest.raises(JournalHold, match="SQLITE_TRANSACTION_FAILED"):
            journal.append_event(event, directive=record, lease=lease, at=12, expected_sequence=1)
    assert journal.inspect() == before
    journal.append_event(event, directive=record, lease=lease, at=12, expected_sequence=1)


def test_duplicate_transition_event_and_state_override_denied(prepared):
    journal, lease, record = prepared
    event = Event("event:input", EventKind.START, JOURNEY_ID, SOURCE, tool="tool:offline")
    with pytest.raises(JournalHold):
        journal.append_event(event, directive=record, lease=lease, at=12, expected_sequence=1)
    with pytest.raises(TypeError):
        journal.append_event(event, directive=record, lease=lease, at=12, expected_sequence=1, state="COMPLETE")
    assert recovered(journal, record).state == "INPUT_VALIDATED"


@pytest.mark.parametrize("new_event,new_key", [(False, False), (True, False), (True, True)])
def test_semantic_action_dedup_cannot_rekey_or_change_event(prepared, new_event, new_key):
    journal, lease, record = prepared
    advance(journal, lease, record)
    intent = reserve(journal, lease, record)
    before = journal.inspect()
    with pytest.raises(JournalHold):
        reserve(journal, lease, record, expected_sequence=5,
                event_id="event:another" if new_event else "event:intent",
                effect_key="fixture:new-key" if new_key else intent.effect_key)
    assert journal.inspect() == before
    result = recovered(journal, record)
    assert result.state == "UNKNOWN_EFFECT" and result.effect_key == intent.effect_key
    assert result.request_sha256 == intent.request_sha256 and not result.may_retry


def test_same_account_action_content_across_journeys_dedup(prepared):
    journal, lease, record = prepared
    advance(journal, lease, record)
    reserve(journal, lease, record)
    second = directive("fixture:second")
    journal.create_journey(second, directive_ref="directive:second", lease=lease, at=16, event_id="event:second-input")
    advance(journal, lease, second, "fixture:second", at=16)
    before = journal.inspect()
    with pytest.raises(Conflict, match="DUPLICATE_SEMANTIC_ACTION"):
        journal.reserve_intent("fixture:second", TARGET, directive=second, lease=lease,
                               at=17, event_id="event:second-intent", expected_sequence=4)
    assert journal.inspect() == before


def test_semantic_identity_omits_caller_key_journey_directive():
    assert replace(TARGET).effect_key == TARGET.effect_key
    assert replace(TARGET, account_ref="account:other").effect_key != TARGET.effect_key
    assert replace(TARGET, content_sha256="0" * 64).effect_key != TARGET.effect_key


def test_intent_is_visible_to_independent_reader_before_modeled_call(prepared):
    journal, lease, record = prepared
    advance(journal, lease, record)
    intent = reserve(journal, lease, record)
    assert intent.boundary == "INTENT_COMMITTED_NO_EXECUTION_AUTHORITY"
    with sqlite3.connect(journal.path) as reader:
        assert reader.execute("SELECT effect_key FROM intents").fetchall() == [(intent.effect_key,)]
    modeled_calls = 0
    assert recovered(opened(journal.path), record).state == "UNKNOWN_EFFECT"
    modeled_calls += 1  # local counter only; journal has no dispatch hook
    assert modeled_calls == 1


@pytest.mark.parametrize("outcome", ["UNKNOWN", "NO_EFFECT", "CONFIRMED"])
@pytest.mark.parametrize("origin", ["SYNTHETIC_FIXTURE", "HUMAN_SUPPLIED", "PROVIDER_ATTESTED"])
def test_modeled_observation_never_becomes_provider_authority(prepared, outcome, origin):
    journal, lease, record = prepared
    advance(journal, lease, record)
    intent = reserve(journal, lease, record)
    observation(journal, lease, intent, outcome=outcome, evidence_type=origin)
    result = recovered(opened(journal.path), record)
    assert result.modeled_outcome == outcome and result.modeled_evidence_type == origin
    assert result.state == "UNKNOWN_EFFECT" and result.effect_status == "UNKNOWN"
    assert result.snapshot.state == State.WAIT_BRAIN
    assert not result.provider_verified and not result.may_retry
    assert result.effect_key == intent.effect_key
    with pytest.raises(TypeError):
        observation(journal, lease, intent, verified=True)


@pytest.mark.parametrize("kind", [EventKind.BRAIN_ACCEPTED, EventKind.RELEASE_REQUESTED,
                                 EventKind.PROVIDER_CONFIRMED, EventKind.RECONCILED_NO_EFFECT,
                                 EventKind.LEARNING_RECORDED, EventKind.OFFLINE_FINISHED])
def test_no_synthesized_brain_human_or_provider_authority(prepared, kind):
    journal, lease, record = prepared
    with pytest.raises(JournalHold, match="OFFLINE_EVENT_ONLY"):
        journal.append_event(Event("event:authority", kind, JOURNEY_ID, SOURCE),
                             directive=record, lease=lease, at=12, expected_sequence=1)


def test_unknown_effect_cannot_complete_even_after_takeover(prepared):
    journal, lease, record = prepared
    advance(journal, lease, record)
    intent = reserve(journal, lease, record)
    newer = journal.acquire(owner="writer:takeover", at=110, ttl=20, event_id="lease:takeover")
    assert newer.generation == lease.generation + 1
    result = recovered(journal, record)
    assert result.effect_key == intent.effect_key and result.state == "UNKNOWN_EFFECT"
    with pytest.raises(JournalHold, match="RECOVERY_HOLD_OR_UNKNOWN"):
        journal.append_event(Event("event:stop", EventKind.STOP, JOURNEY_ID, SOURCE),
                             directive=record, lease=newer, at=111, expected_sequence=5)
    with pytest.raises(JournalHold):
        reserve(journal, newer, record, at=111, event_id="event:retry", expected_sequence=5)


@pytest.mark.parametrize("operation", ["renew", "release", "append", "reserve", "observe"])
def test_stale_fencing_denies_all_writes(prepared, operation):
    journal, old, record = prepared
    advance(journal, old, record)
    intent = reserve(journal, old, record)
    journal.acquire(owner="writer:new", at=110, ttl=20, event_id="lease:new")
    before = journal.inspect()
    with pytest.raises(JournalHold, match="STALE_FENCING_GENERATION"):
        if operation == "renew":
            journal.renew(old, at=111, ttl=20, event_id="lease:stale")
        elif operation == "release":
            journal.release(old, at=111, event_id="lease:stale")
        elif operation == "append":
            journal.append_event(Event("event:stale", EventKind.STOP, JOURNEY_ID, SOURCE),
                                 directive=record, lease=old, at=111, expected_sequence=5)
        elif operation == "reserve":
            reserve(journal, old, record, at=111, expected_sequence=5)
        else:
            observation(journal, old, intent, at=111)
    assert journal.inspect() == before
    result = recovered(journal, record, expected_generation=old.generation)
    assert result.state == "HOLD" and result.effect_key == intent.effect_key and not result.may_retry


def test_lease_ttl_logical_time_unique_owner_renew_release(prepared):
    journal, lease, record = prepared
    for ttl in (0, MAX_TTL + 1, True):
        with pytest.raises(JournalHold):
            journal.renew(lease, at=12, ttl=ttl, event_id="lease:invalid")
    with pytest.raises(JournalHold):
        journal.renew(lease, at=9, ttl=10, event_id="lease:backwards")
    renewed = journal.renew(lease, at=20, ttl=30, event_id="lease:renewed")
    assert renewed.generation == lease.generation and renewed.expires_at == 50
    with pytest.raises(JournalHold):
        journal.release(lease, at=21, event_id="lease:old-token")
    journal.release(renewed, at=21, event_id="lease:released")
    with pytest.raises(JournalHold):
        journal.acquire(owner=lease.owner, at=22, ttl=20, event_id="lease:reuse")
    fresh = journal.acquire(owner="writer:fresh", at=22, ttl=20, event_id="lease:fresh")
    assert fresh.generation == 2 and recovered(journal, record).state == "INPUT_VALIDATED"


def _child(code, *args):
    env = os.environ.copy()  # Preserve the inherited Git/engineering guard.
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.Popen([sys.executable, "-u", "-c", code, *map(str, args)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, cwd=ROOT)


def _kill(process):
    if process.poll() is None:
        process.kill()
    return process.communicate(timeout=15)


LEASE_CHILD = """
import sys
from ai_commerce.production_runtime.journal import Journal, JournalHold
j = Journal.open(sys.argv[1], journal_id='fixture:journal', source_sha=sys.argv[2])
sys.stdin.read(1)
try:
    token = j.acquire(owner=sys.argv[3], at=200, ttl=100, event_id=sys.argv[3])
    print('HELD:' + str(token.generation), flush=True)
except JournalHold:
    print('FENCED', flush=True)
"""


def test_two_separate_processes_only_one_lease_writer(prepared):
    journal, _, _ = prepared
    children = [_child(LEASE_CHILD, journal.path, SOURCE, f"writer:process{n}") for n in (1, 2)]
    try:
        # Both processes are alive before either receives the start signal.
        for child in children:
            child.stdin.write("g")
            child.stdin.flush()
        results = [child.communicate(timeout=20) for child in children]
        assert sorted(out.strip() for out, _ in results) == ["FENCED", "HELD:2"]
        assert all(child.returncode == 0 for child in children), results
        history = opened(journal.path).inspect()
        assert history.lease.generation == 2
        assert sum(r.kind == "LEASE_ACQUIRED" and r.generation == 2 for r in history.records) == 1
    finally:
        for child in children:
            if child.poll() is None:
                _kill(child)


CRASH_CHILD = """
import sys,json
from ai_commerce.production_runtime.contracts import validate_contract
from ai_commerce.production_runtime.journal import Journal,EffectTarget
path,source,raw,cut = sys.argv[1:]
j=Journal.open(path,journal_id='fixture:journal',source_sha=source)
d=validate_contract('CreativeDirective',json.loads(raw))
lease=j.acquire(owner='writer:crash',at=200,ttl=100,event_id='lease:crash')
target=EffectTarget('PUBLISH','provider:fixture','account:fixture','surface:fixture','a'*64)
def boundary():
    print('BOUNDARY',flush=True)
    sys.stdin.read(1)
if cut == 'before_intent_commit':
    original=Journal._insert
    def interrupted(*args,**kw):
        result=original(*args,**kw)
        if kw.get('kind') == 'EFFECT_RESERVED':
            boundary()
        return result
    Journal._insert=interrupted
intent=j.reserve_intent('fixture:journey',target,directive=d,lease=lease,at=201,event_id='event:crash-intent',expected_sequence=4)
if cut == 'after_intent_before_modeled_call':
    boundary()
modeled_calls=1  # Synthetic local counter, no adapter/process/provider invocation.
if cut == 'after_modeled_call_before_receipt':
    boundary()
j.record_observation('fixture:journey',lease=lease,at=202,event_id='event:crash-receipt',expected_sequence=5,
    effect_key=intent.effect_key,request_sha256=intent.request_sha256,outcome='CONFIRMED',
    evidence_ref='fixture:crash-receipt',evidence_sha256='1'*64,evidence_type='SYNTHETIC_FIXTURE')
boundary()
"""


@pytest.mark.parametrize("cut,sequence,unknown,outcome", [
    ("before_intent_commit", 4, False, None),
    ("after_intent_before_modeled_call", 5, True, None),
    ("after_modeled_call_before_receipt", 5, True, None),
    ("after_receipt_commit", 6, True, "CONFIRMED"),
])
def test_actual_process_kill_at_crash_boundaries(prepared, cut, sequence, unknown, outcome):
    journal, lease, record = prepared
    advance(journal, lease, record)
    anchor = journal.inspect().checkpoint
    process = _child(CRASH_CHILD, journal.path, SOURCE, record.raw.decode(), cut)
    lines = []
    thread = threading.Thread(target=lambda: lines.append(process.stdout.readline()), daemon=True)
    thread.start()
    try:
        thread.join(timeout=20)
        assert not thread.is_alive() and lines == ["BOUNDARY\n"]
        _kill(process)  # Windows TerminateProcess via Popen.kill; no graceful cleanup.
        result = recovered(opened(journal.path, checkpoint=anchor), record)
        assert len(result.event_sequence) == sequence
        assert result.state == ("UNKNOWN_EFFECT" if unknown else "WAIT_BRAIN")
        assert result.modeled_outcome == outcome and not result.may_retry and not result.provider_verified
        if unknown:
            assert result.effect_key == TARGET.effect_key and result.request_sha256 is not None
            assert result == recovered(opened(journal.path, checkpoint=result.checkpoint), record)
    finally:
        if process.poll() is None:
            _kill(process)
        thread.join(timeout=5)


@pytest.mark.parametrize("damage", ["missing", "empty", "truncated", "random", "foreign", "version", "hash", "state-text", "owner-sha", "sequence", "schema"])
def test_missing_corrupt_foreign_journal_holds_without_reset(prepared, damage):
    journal, _, record = prepared
    path = Path(journal.path)
    if damage == "missing":
        path.unlink()
    elif damage == "empty":
        path.write_bytes(b"")
    elif damage == "random":
        path.write_bytes(b"synthetic invalid sqlite")
    elif damage == "truncated":
        path.write_bytes(path.read_bytes()[:700])
    else:
        with sqlite3.connect(path) as conn:
            if damage == "foreign":
                conn.execute("DROP TRIGGER immutable_metadata_update")
                conn.execute("UPDATE metadata SET journal_id='fixture:foreign'")
            elif damage == "version":
                conn.execute("PRAGMA user_version=999")
            elif damage == "schema":
                conn.execute("CREATE TABLE unauthorized_state (state TEXT)")
            else:
                trigger = conn.execute("SELECT sql FROM sqlite_master WHERE name='immutable_records_update'").fetchone()[0]
                conn.execute("DROP TRIGGER immutable_records_update")
                if damage == "hash":
                    conn.execute("UPDATE records SET payload_sha256=? WHERE position=2", ("0" * 64,))
                elif damage == "sequence":
                    conn.execute("UPDATE records SET sequence=7 WHERE position=2")
                else:
                    data = json.loads(conn.execute("SELECT payload FROM records WHERE position=2").fetchone()[0])
                    if damage == "state-text":
                        data.update(state="COMPLETE", verified=True)
                    else:
                        data["owner_refs"][0]["sha256"] = "0" * 64
                    conn.execute("UPDATE records SET payload=? WHERE position=2", (json.dumps(data),))
                conn.execute(trigger)
    before = path.read_bytes() if path.exists() else None
    result = recovered(journal, record)
    assert result.state == "HOLD" and not result.may_retry and not result.provider_verified
    with pytest.raises(JournalHold):
        opened(path)
    assert (path.read_bytes() if path.exists() else None) == before


def test_rollback_and_deleted_suffix_detected_with_retained_checkpoint(prepared, tmp_path):
    journal, lease, record = prepared
    backup = tmp_path / "before.sqlite"
    shutil.copyfile(journal.path, backup)
    advance(journal, lease, record)
    intent = reserve(journal, lease, record)
    anchor = journal.inspect().checkpoint
    shutil.copyfile(backup, journal.path)
    assert recovered(journal, record, checkpoint=anchor).state == "HOLD"
    with pytest.raises(JournalHold):
        opened(journal.path, checkpoint=anchor)
    # No outside retained anchor means a consistent old database cannot be
    # distinguished from its historical self. This is explicitly NOT_PROVEN.
    no_anchor = recovered(journal, record)
    assert no_anchor.anti_rollback_proof == "NOT_PROVEN"
    assert intent.effect_key == TARGET.effect_key


@pytest.mark.parametrize("wrong", ["source", "journal", "directive", "checkpoint"])
def test_source_journey_directive_and_checkpoint_bindings(prepared, wrong):
    journal, _, record = prepared
    if wrong == "source":
        result = recover(journal.path, journal_id=JOURNAL_ID, source_sha="0" * 40,
                         journey_id=JOURNEY_ID, directive=record)
    elif wrong == "journal":
        result = recover(journal.path, journal_id="fixture:foreign", source_sha=SOURCE,
                         journey_id=JOURNEY_ID, directive=record)
    elif wrong == "directive":
        result = recovered(journal, directive(product_ref="product:different"))
    else:
        result = recovered(journal, record, checkpoint=replace(journal.inspect().checkpoint, sha256="0" * 64))
    assert result.state == "HOLD" and not result.may_retry
    assert recover(journal.path, journal_id=JOURNAL_ID, source_sha=SOURCE,
                   journey_id="fixture:absent", directive=record).state == "HOLD"


def test_contradictory_owner_sha_rolls_back_without_relabel(prepared):
    journal, lease, record = prepared
    before = journal.inspect()
    with pytest.raises(JournalHold):
        journal.append_event(Event("event:start", EventKind.START, JOURNEY_ID, SOURCE, tool="tool:offline"),
                             directive=record, lease=lease, at=12, expected_sequence=1,
                             owner_refs=(replace(OWNER_REFS[0], sha256="0" * 64),))
    assert journal.inspect() == before
    assert recovered(journal, record).owner_refs == OWNER_REFS


def test_target_binding_receipt_identity_and_contradictions_roll_back(prepared):
    journal, lease, record = prepared
    advance(journal, lease, record)
    before = journal.inspect()
    with pytest.raises(JournalHold):
        journal.reserve_intent(JOURNEY_ID, replace(TARGET, account_ref="account:wrong"), directive=record,
            lease=lease, at=15, event_id="event:wrong-target", expected_sequence=4)
    assert journal.inspect() == before
    intent = reserve(journal, lease, record)
    before = journal.inspect()
    with pytest.raises(JournalHold):
        observation(journal, lease, intent, request_sha256="0" * 64)
    assert journal.inspect() == before
    observation(journal, lease, intent, outcome="NO_EFFECT")
    before = journal.inspect()
    with pytest.raises(JournalHold):
        observation(journal, lease, intent, expected_sequence=6, event_id="event:contradiction", outcome="CONFIRMED")
    assert journal.inspect() == before


def test_creation_is_add_only_missing_open_never_creates(tmp_path):
    path = tmp_path / "new.sqlite"
    with pytest.raises(JournalHold):
        opened(path)
    assert not path.exists()
    journal = Journal.create(str(path), journal_id=JOURNAL_ID, source_sha=SOURCE)
    before = path.read_bytes()
    with pytest.raises(JournalHold, match="DATABASE_ALREADY_EXISTS"):
        Journal.create(str(path), journal_id=JOURNAL_ID, source_sha=SOURCE)
    assert path.read_bytes() == before
    with pytest.raises(JournalHold, match="ABSOLUTE"):
        Journal.create("relative.sqlite", journal_id=JOURNAL_ID, source_sha=SOURCE)
    assert journal.inspect().checkpoint.position == 0


def test_unc_device_paths_denied_before_filesystem_access(monkeypatch):
    from ai_commerce.production_runtime import journal as module
    def deny(*args, **kwargs):
        pytest.fail("remote or device path reached filesystem resolution")
    with monkeypatch.context() as patch:
        patch.setattr(module.os.path, "realpath", deny)
        for path in (r"\\server\share\journal.sqlite", r"\\.\pipe\journal", "//server/share/journal.sqlite"):
            with pytest.raises(JournalHold):
                Journal.open(path, journal_id=JOURNAL_ID, source_sha=SOURCE)


def test_api_has_no_network_subprocess_provider_or_authority_ingress(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("journal attempted process or provider dispatch")
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", deny)
        patch.setattr(os, "system", deny)
        for name in ("startfile", "spawnv", "spawnve"):
            if hasattr(os, name):
                patch.setattr(os, name, deny)
        journal = Journal.create(str(tmp_path / "offline.sqlite"), journal_id=JOURNAL_ID, source_sha=SOURCE)
        lease = journal.acquire(owner="writer:offline", at=1, ttl=100, event_id="lease:offline")
        record = directive()
        journal.create_journey(record, directive_ref="directive:offline", lease=lease, at=2, event_id="event:input")
        advance(journal, lease, record)
        intent = reserve(journal, lease, record)
        observation(journal, lease, intent)
        assert recovered(opened(journal.path), record).state == "UNKNOWN_EFFECT"
    allowed_imports = {"__future__", "dataclasses", "hashlib", "json", "re"}
    loads = []
    for name in ("journal.py", "recovery.py"):
        tree = ast.parse((ROOT / "src/ai_commerce/production_runtime" / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in allowed_imports for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                assert node.module.split(".")[0] in allowed_imports
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__":
                assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
                loads.append(node.args[0].value)
    assert sorted(loads) == ["os", "sqlite3"]
