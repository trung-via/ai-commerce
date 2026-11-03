"""A17.D05.L07.offline-reconciliation-dashboard: native D05 AC and attacks."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import socket
import sqlite3
import urllib.request

import pytest
from jsonschema import Draft202012Validator

from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.distribution.assisted import export_package
from ai_commerce.distribution.receipt import PublicationJournal
from ai_commerce.distribution_intelligence import (DistributionReconciliation, IntelligenceError,
    ReconciliationLedger, inspect_sources, publication_request_digest, schema)
from ai_commerce.distribution_intelligence.schemas import bounded, digest, parse
from ai_commerce.operations import ArtifactRegistry
from ai_commerce.operations.retry_guard import AttemptObservation, ReconciliationObservation, RetryGuard

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline D05/L07 called network/provider/effect APIs")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    from ai_commerce.creative.live import LiveCreativeReasoner
    from ai_commerce.media.fake_generation import FakeAsyncMediaProvider
    from ai_commerce.operations.generation import GenerationJobRunner
    from ai_commerce.providers.base import MockContentProvider
    monkeypatch.setattr(LiveCreativeReasoner, "reason", forbidden)
    monkeypatch.setattr(FakeAsyncMediaProvider, "submit", forbidden)
    monkeypatch.setattr(MockContentProvider, "generate", forbidden)
    for name in ("submit", "observe", "fetch", "reconcile"):
        monkeypatch.setattr(GenerationJobRunner, name, forbidden)


def scope_value(**changes):
    value = {"platform": "TIKTOK", "surface": "VIDEO", "account_ref": {"namespace": "internal_alias", "id": "fixture-account"},
        "app_ref": None, "provider_ref": None, "market": "VN", "channel": None, "product_ref": None,
        "listing_ref": None, "variant_ref": None, "link_ref": None, "link_url_sha256": None,
        "creative_ref": None, "creative_revision": None, "creative_sha256": None, "content_sha256": None,
        "measurement_window": ["2026-11-01T00:00:00Z", "2026-11-04T00:00:00Z"], "timezone": "UTC", "cohort": None}
    value.update(changes)
    return value


def tree_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def make_case(tmp_path, monkeypatch, *, receipt=False, state="UNKNOWN_EFFECT", product_ref=None, scope_changes=None):
    source = tmp_path / "delivery"
    source.mkdir()
    # Deliberately synthetic MP4 unit fixture. Real local-render coverage is in
    # L07 integration and the mandatory registered A08/A17.1 regression groups.
    video = b"synthetic MP4 fixture bytes; no media generation"
    (source / "video.mp4").write_bytes(video)
    (source / "e01-manifest.json").write_bytes(canonical_bytes({"run_id": "fixture-e01",
        "publication_status": "NOT_ATTEMPTED", "output_sha256": sha256_bytes(video)}))
    monkeypatch.setattr("ai_commerce.distribution.assisted.verify_e01", lambda _: {"state": "DELIVERED"})
    monkeypatch.setattr("ai_commerce.distribution.receipt._now", lambda: "2026-11-03T01:00:00Z")
    package_dir = tmp_path / "package"
    package = export_package(source, package_dir, platform="TIKTOK", surface="VIDEO",
                             account="internal_alias:fixture-account", market="VN", product_ref=product_ref)
    scope = scope_value(content_sha256=package["video_sha256"], product_ref=product_ref, **(scope_changes or {}))
    journal = PublicationJournal(tmp_path / "publication.sqlite")
    pid = journal.create(package_dir, idempotency_key="fixture-publication")["publication_id"]
    journal.record(pid, "SUBMISSION_REPORTED")
    journal.record(pid, "OUTCOME_UNKNOWN")
    if receipt:
        receipt_file = tmp_path / "receipt.json"
        receipt_file.write_bytes(b"supplied receipt fixture")
        journal.record(pid, "PUBLICATION_RECEIPT", provider_post_id="fixture-post", evidence_file=receipt_file)
    registry = ArtifactRegistry(tmp_path / "a15")
    guard = RetryGuard(registry)
    request = publication_request_digest(package, pid, scope)
    guard.register_intent("fixture-intent", request)
    proof = b"uncertain attempt evidence"
    registry.put_immutable("proofs/attempt.json", proof)
    guard.record_attempt(AttemptObservation("fixture-intent", 1, state,
        "UNKNOWN" if state in {"UNKNOWN_EFFECT", "PENDING"} else "NONE", "proofs/attempt.json", sha256_bytes(proof)))
    args = {"package_dir": package_dir, "journal": journal, "guard": guard, "scope": scope,
            "publication_id": pid, "intent_id": "fixture-intent", "attempt": 1}
    store = ReconciliationLedger(ArtifactRegistry(tmp_path / "d05"))
    return args, store, package


def observation(args, ident="observation-1", *, effect="CONFIRMED", hour=2, origin="USER_SUPPLIED", proof=None, **changes):
    registry = args["guard"].registry
    ref = "observations/" + ident + ".json"
    proof_ref = "observation-proofs/" + ident + ".json"
    proof = ("independent supplied evidence " + ident).encode() if proof is None else proof
    registry.put_immutable(proof_ref, proof)
    doc = {"schema_version": "DistributionScopedObservation.v1", "observation_id": ident,
        "publication_id": args["publication_id"], "intent_id": args["intent_id"], "attempt": args["attempt"],
        "request_digest": args["guard"].read_intent(args["intent_id"])["request_digest"],
        "original_record_sha256": digest(args["guard"].read_attempt(args["intent_id"], args["attempt"])),
        "scope": deepcopy(args["scope"]), "observed_at": f"2026-11-03T{hour:02d}:00:00Z",
        "side_effects": effect, "provider_post_id": "fixture-post" if effect == "CONFIRMED" else None,
        "evidence_ref": proof_ref, "evidence_sha256": sha256_bytes(proof), "evidence_origin": origin}
    doc.update(changes)
    raw = bounded(doc)
    registry.put_immutable(ref, raw)
    return {"ref": ref, "sha256": sha256_bytes(raw)}, doc


def append(store, args, *, key="correction-1", parent=None, pin=None):
    source = inspect_sources(**args)
    return store.reconcile(key=key, expected_source_sha256=pin or source["source_sha256"], parent_id=parent, **args)


def test_native_unknown_receipt_replay_is_immutable_and_has_no_effects(tmp_path, monkeypatch):
    args, store, _ = make_case(tmp_path, monkeypatch)
    before = (args["journal"].path.read_bytes(), tree_bytes(args["guard"].registry.root))
    original = append(store, args)
    value = original.to_dict()
    assert value["old_state"] == {"publication": "UNKNOWN", "reported_visibility": "UNKNOWN", "attempt_outcome": "UNKNOWN_EFFECT"}
    assert value["new_state"] == {"status": "RECONCILE_REQUIRED", "decision": "RECONCILE_REQUIRED",
        "public_visibility": "UNKNOWN", "replay_safe": False, "provider_attested": False}
    retained = tree_bytes(store.registry.root)
    assert append(store, args).to_bytes() == original.to_bytes()
    assert tree_bytes(store.registry.root) == retained and len(store.history()) == 1
    assert (args["journal"].path.read_bytes(), tree_bytes(args["guard"].registry.root)) == before
    assert not value["provider_called"] and not value["retry_dispatched"]
    assert DistributionReconciliation.from_bytes(original.to_bytes(), expected_id=original.record_id) == original
    with pytest.raises(FrozenInstanceError):
        original._raw = b"changed"


@pytest.mark.parametrize("effect,status,decision", [("CONFIRMED", "OBSERVED_CONFIRMED_UNATTESTED", "NO_RETRY"),
    ("NONE", "OBSERVED_NO_EFFECT_UNATTESTED", "REQUIRES_SEPARATE_AUTHORIZATION")])
def test_separate_independent_scoped_observation_narrows_only_supplied_state(tmp_path, monkeypatch, effect, status, decision):
    args, store, _ = make_case(tmp_path, monkeypatch)
    original = append(store, args)
    pin, doc = observation(args, effect=effect)
    args["observation_pins"] = (pin,)
    correction = append(store, args, key="correction-2", parent=original.record_id).to_dict()
    assert correction["revision"] == 2 and correction["parent_id"] == original.record_id
    assert correction["new_state"]["status"] == status and correction["new_state"]["decision"] == decision
    assert correction["old_state"]["publication"] == "UNKNOWN"
    assert correction["sources"]["observations"][0]["document"] == doc
    assert correction["new_state"]["public_visibility"] == "UNKNOWN" and not correction["new_state"]["replay_safe"]
    assert args["journal"].get(args["publication_id"])["state"] == "UNKNOWN"
    assert args["guard"].decision(args["intent_id"], last_attempt=1)["decision"] == "RECONCILE_REQUIRED"


def test_competing_negative_observation_and_revision_lineage_are_retained(tmp_path, monkeypatch):
    args, store, _ = make_case(tmp_path, monkeypatch)
    first = append(store, args)
    one, _ = observation(args)
    args["observation_pins"] = (one,)
    second = append(store, args, key="second", parent=first.record_id)
    two, _ = observation(args, "negative-observation", effect="NONE", hour=3)
    args["observation_pins"] = (one, two)
    third = append(store, args, key="third", parent=second.record_id).to_dict()
    assert third["new_state"]["status"] == "CONFLICTING_EVIDENCE"
    assert third["new_state"]["decision"] == "RECONCILE_REQUIRED"
    assert [o["document"]["side_effects"] for o in third["sources"]["observations"]] == ["CONFIRMED", "NONE"]
    assert [r.to_dict()["revision"] for r in store.history()] == [1, 2, 3]
    assert store.get(first.record_id) == first
    three, _ = observation(args, "fourth-observation", effect="NONE", hour=4)
    args["observation_pins"] = (one, two, three)
    with pytest.raises(IntelligenceError, match="STALE_RECONCILIATION_PARENT"):
        append(store, args, key="stale-parent", parent=first.record_id)


def test_user_receipt_and_generic_a15_policy_cannot_establish_public_or_replay_safe(tmp_path, monkeypatch):
    args, store, _ = make_case(tmp_path, monkeypatch, receipt=True)
    visibility = tmp_path / "visibility.json"
    visibility.write_bytes(b"separate user screenshot")
    args["journal"].record(args["publication_id"], "VISIBILITY_EVIDENCE", evidence_file=visibility,
                           public_url="https://example.test/fixture-post")
    record = append(store, args).to_dict()
    assert record["old_state"]["publication"] == "PUBLISHED_CONFIRMED"
    assert record["old_state"]["reported_visibility"] == "PUBLIC"
    assert record["new_state"]["public_visibility"] == "UNKNOWN" and not record["new_state"]["replay_safe"]
    pin, _ = observation(args, effect="NONE")
    args["observation_pins"] = (pin,)
    correction = append(store, args, key="negative", parent=record["record_id"]).to_dict()
    assert correction["new_state"]["status"] == "CONFLICTING_EVIDENCE"


@pytest.mark.parametrize("effect", ["NONE", "CONFIRMED"])
def test_generic_a15_reconciliation_is_retained_but_not_scoped_permission(tmp_path, monkeypatch, effect):
    args, store, _ = make_case(tmp_path, monkeypatch)
    guard = args["guard"]
    raw = ("generic " + effect).encode()
    guard.registry.put_immutable("generic/evidence.json", raw)
    guard.record_reconciliation(ReconciliationObservation(args["intent_id"], 1,
        guard.read_intent(args["intent_id"])["request_digest"], digest(guard.read_attempt(args["intent_id"], 1)),
        effect, "generic/evidence.json", sha256_bytes(raw)))
    assert guard.decision(args["intent_id"], last_attempt=1)["decision"] != "RECONCILE_REQUIRED"
    value = append(store, args).to_dict()
    assert value["new_state"]["decision"] == "RECONCILE_REQUIRED"
    assert value["sources"]["retry"]["reconciliation"]["side_effects"] == effect
    assert value["sources"]["retry"]["history_reconciliations"]


def test_synthetic_observation_preserved_without_resolving_unknown(tmp_path, monkeypatch):
    args, store, _ = make_case(tmp_path, monkeypatch)
    pin, _ = observation(args, origin="SYNTHETIC_FIXTURE")
    args["observation_pins"] = (pin,)
    value = append(store, args).to_dict()
    assert value["new_state"]["status"] == "RECONCILE_REQUIRED"
    assert "SYNTHETIC_OBSERVATION_NOT_EFFECT_PROOF" in value["reason_codes"]


@pytest.mark.parametrize("change", ["account", "platform", "surface", "market", "window", "content", "product", "app"])
def test_scope_swaps_fail_before_records_are_written(tmp_path, monkeypatch, change):
    args, store, _ = make_case(tmp_path, monkeypatch)
    scope = deepcopy(args["scope"])
    values = {"account": ("account_ref", {"namespace": "internal_alias", "id": "swapped"}),
        "platform": ("platform", "YOUTUBE"), "surface": ("surface", "PROFILE_VIDEO"), "market": ("market", "US"),
        "window": ("measurement_window", ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"]),
        "content": ("content_sha256", "f" * 64), "product": ("product_ref", "wrong-product"),
        "app": ("app_ref", {"namespace": "internal_alias", "id": "wrong-app"})}
    key, value = values[change]
    scope[key] = value
    args["scope"] = scope
    with pytest.raises(ValueError):
        append(store, args)
    assert store.history() == ()


@pytest.mark.parametrize("mutation", ["provider", "account", "market", "window", "post", "intent", "attempt", "original", "request", "stale", "extra", "proof-hash"])
def test_forged_or_wrong_scope_observation_fails_closed(tmp_path, monkeypatch, mutation):
    args, store, _ = make_case(tmp_path, monkeypatch, receipt=True)
    changes = {}
    if mutation == "provider": changes["evidence_origin"] = "PROVIDER_ATTESTED"
    elif mutation in {"account", "market", "window"}:
        changed = deepcopy(args["scope"])
        if mutation == "account": changed["account_ref"]["id"] = "other"
        elif mutation == "market": changed["market"] = "US"
        else: changed["measurement_window"] = ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"]
        changes["scope"] = changed
    elif mutation == "post": changes["provider_post_id"] = "other-post"
    elif mutation == "intent": changes["intent_id"] = "other-intent"
    elif mutation == "attempt": changes["attempt"] = 1.0
    elif mutation == "original": changes["original_record_sha256"] = "f" * 64
    elif mutation == "request": changes["request_digest"] = "f" * 64
    elif mutation == "stale": changes["observed_at"] = "2026-11-03T00:00:00Z"
    elif mutation == "extra": changes["post_now"] = True
    else: changes["evidence_sha256"] = "f" * 64
    pin, _ = observation(args, **changes)
    args["observation_pins"] = (pin,)
    with pytest.raises(ValueError):
        append(store, args)
    assert not store.history()


@pytest.mark.parametrize("mutation", ["evidence", "event", "account", "transition", "timestamp", "rekey", "duplicate", "db-bytes"])
def test_actual_native_sqlite_tampering_rejected(tmp_path, monkeypatch, mutation):
    args, store, _ = make_case(tmp_path, monkeypatch, receipt=True)
    journal = args["journal"]
    if mutation == "db-bytes":
        journal.path.write_bytes(b"not a SQLite journal")
    elif mutation == "rekey":
        journal.create(args["package_dir"], idempotency_key="same-content-new-key")
    elif mutation == "duplicate":
        # Native A08 permits reusing a receipt BLOB as visibility evidence under
        # later timestamps/IDs; D05 independently rejects repeated evidence.
        raw = tmp_path / "repeat.json"
        raw.write_bytes(b"supplied receipt fixture")
        with sqlite3.connect(journal.path) as db:
            event = parse(db.execute("SELECT event_json FROM publication_events WHERE evidence_sha256 IS NOT NULL").fetchone()[0])
            event.update(event_type="VISIBILITY_EVIDENCE", provider_post_id=None, public_url="https://example.test/p",
                         recorded_at="2026-11-03T02:00:00Z")
            db.execute("INSERT INTO publication_events(publication_id,event_digest,event_json,evidence_blob,evidence_sha256) VALUES(?,?,?,?,?)",
                       (args["publication_id"], digest(event), bounded(event), raw.read_bytes(), sha256_bytes(raw.read_bytes())))
    else:
        with sqlite3.connect(journal.path) as db:
            if mutation == "evidence": db.execute("UPDATE publication_events SET evidence_blob=? WHERE evidence_sha256 IS NOT NULL", (b"corrupt",))
            elif mutation == "event": db.execute("UPDATE publication_events SET event_digest=? WHERE seq=1", ("f" * 64,))
            elif mutation == "account": db.execute("UPDATE publications SET account_ref=?", (json.dumps({"namespace": "internal_alias", "id": "swapped"}),))
            else:
                seq = 2
                event = parse(db.execute("SELECT event_json FROM publication_events WHERE seq=?", (seq,)).fetchone()[0])
                if mutation == "transition": event["event_type"] = "REMOVAL_REPORTED"
                else: event["recorded_at"] = "2026-11-02T00:00:00Z"
                db.execute("UPDATE publication_events SET event_json=?,event_digest=? WHERE seq=?", (bounded(event), digest(event), seq))
    with pytest.raises(ValueError):
        append(store, args)
    assert not store.history()


def test_stale_source_duplicate_key_rekey_and_truncation_rejected(tmp_path, monkeypatch):
    args, store, _ = make_case(tmp_path, monkeypatch)
    original = append(store, args)
    with pytest.raises(IntelligenceError, match="DUPLICATE_RECONCILIATION_REKEY"):
        append(store, args, key="renamed-key", parent=original.record_id)
    pinned = inspect_sources(**args)["source_sha256"]
    monkeypatch.setattr("ai_commerce.distribution.receipt._now", lambda: "2026-11-03T02:00:00Z")
    args["journal"].record(args["publication_id"], "OUTCOME_UNKNOWN")
    with pytest.raises(IntelligenceError, match="STALE_SOURCE_PIN"):
        append(store, args, key="second", pin=pinned, parent=original.record_id)
    with pytest.raises(IntelligenceError, match="KEY_COLLISION"):
        append(store, args)
    next_ = append(store, args, key="second", parent=original.record_id)
    pin, _ = observation(args, hour=3)
    args["observation_pins"] = (pin,)
    third = append(store, args, key="third", parent=next_.record_id)
    args["observation_pins"] = ()
    with pytest.raises(IntelligenceError, match="DUPLICATE_RECONCILIATION_REKEY|TRUNCATED"):
        append(store, args, key="fourth", parent=third.record_id)
    assert len(store.history()) == 3


@pytest.mark.parametrize("repeat", ["original-proof", "same-pin", "rekeyed-proof"])
def test_independence_requires_new_actual_evidence_bytes(tmp_path, monkeypatch, repeat):
    args, store, _ = make_case(tmp_path, monkeypatch)
    proof = b"uncertain attempt evidence" if repeat == "original-proof" else b"new independent evidence"
    first, _ = observation(args, proof=proof)
    pins = [first]
    if repeat == "same-pin": pins.append(first)
    elif repeat == "rekeyed-proof": pins.append(observation(args, "renamed-observation", hour=3, proof=proof)[0])
    args["observation_pins"] = pins
    with pytest.raises(IntelligenceError, match="NOT_INDEPENDENT"):
        append(store, args)
    assert not store.history()


@pytest.mark.parametrize("mutation", ["intent-hash", "attempt-hash", "proof", "future-attempt", "generic-digest"])
def test_native_a15_state_is_independently_reopened(tmp_path, monkeypatch, mutation):
    args, store, _ = make_case(tmp_path, monkeypatch)
    registry = args["guard"].registry
    if mutation == "proof": registry.safe_path("proofs/attempt.json").write_bytes(b"corrupt")
    elif mutation == "future-attempt": args["attempt"] = 2
    else:
        ref = "retry-intents/fixture-intent/intent.json" if mutation in {"intent-hash", "generic-digest"} else "retry-intents/fixture-intent/attempt-1.json"
        value = parse(registry.read(ref))
        if mutation == "generic-digest":
            value["request_digest"] = "f" * 64
            value["record_sha256"] = digest({k: v for k, v in value.items() if k != "record_sha256"})
        else: value["record_sha256"] = "f" * 64
        registry.safe_path(ref).write_bytes(bounded(value))
    with pytest.raises(ValueError):
        append(store, args)
    assert not store.history()


@pytest.mark.parametrize("reference", ["../outside.json", "C:/secret.json", "https://provider.test/receipt", "proofs/CON.json", "proofs/a:ads"])
def test_unsafe_observation_paths_fail_closed(tmp_path, monkeypatch, reference):
    args, store, _ = make_case(tmp_path, monkeypatch)
    args["observation_pins"] = ({"ref": reference, "sha256": "f" * 64},)
    with pytest.raises((ValueError, RuntimeError)):
        append(store, args)
    assert not store.history()


def test_contract_mirror_and_forged_reconciliation_replay(tmp_path, monkeypatch):
    assert json.loads((ROOT / "contracts/DistributionReconciliation.v1.schema.json").read_bytes()) == schema("DistributionReconciliation.v1")
    Draft202012Validator.check_schema(schema("DistributionReconciliation.v1"))
    args, store, _ = make_case(tmp_path, monkeypatch)
    record = append(store, args).to_dict()
    for field, change in (("provider_called", True), ("retry_dispatched", True), ("revision", 1.0), ("unapproved", "post")):
        forged = {**record, field: change}
        with pytest.raises(ValueError):
            DistributionReconciliation.from_bytes(bounded(forged))
    record["new_state"]["status"] = "OBSERVED_CONFIRMED_UNATTESTED"
    body = {k: v for k, v in record.items() if k not in {"record_id", "record_sha256"}}
    sha = digest(body)
    with pytest.raises(IntelligenceError, match="REPLAY_MISMATCH"):
        DistributionReconciliation.from_bytes(bounded({**body, "record_sha256": sha, "record_id": "a17-reconciliation-" + sha}))


def test_reads_never_use_native_a08_write_connection_or_a15_writers(tmp_path, monkeypatch):
    args, store, _ = make_case(tmp_path, monkeypatch)
    before = (args["journal"].path.read_bytes(), tree_bytes(args["guard"].registry.root))
    def forbidden(*a, **k):
        pytest.fail("D05 invoked an upstream writer")
    monkeypatch.setattr(PublicationJournal, "_connect", forbidden)
    for name in ("register_intent", "record_attempt", "record_reconciliation"):
        monkeypatch.setattr(RetryGuard, name, forbidden)
    assert append(store, args).to_dict()["new_state"]["decision"] == "RECONCILE_REQUIRED"
    assert (args["journal"].path.read_bytes(), tree_bytes(args["guard"].registry.root)) == before


def test_native_reconciliation_and_scoped_observation_are_both_reopened(tmp_path, monkeypatch):
    args, store, _ = make_case(tmp_path, monkeypatch)
    pin, doc = observation(args)
    args["guard"].record_reconciliation(ReconciliationObservation(args["intent_id"], 1, doc["request_digest"],
        doc["original_record_sha256"], "CONFIRMED", pin["ref"], pin["sha256"]))
    args["observation_pins"] = (pin,)
    value = append(store, args).to_dict()
    assert value["new_state"]["status"] == "OBSERVED_CONFIRMED_UNATTESTED"
    assert value["sources"]["retry"]["reconciliation"]["evidence_sha256"] == pin["sha256"]
    assert not value["new_state"]["replay_safe"]


def test_forged_second_attempt_cannot_bypass_original_unknown(tmp_path, monkeypatch):
    args, store, _ = make_case(tmp_path, monkeypatch)
    forged = AttemptObservation(args["intent_id"], 2, "PENDING", "UNKNOWN")
    args["guard"].registry.put_immutable("retry-intents/fixture-intent/attempt-2.json", bounded(forged.record()))
    args["attempt"] = 2
    with pytest.raises(IntelligenceError, match="PREVIOUS_ATTEMPT_EFFECT_UNRESOLVED"):
        append(store, args)
    assert not store.history()


@pytest.mark.parametrize("mutation", ["hash", "gap", "extra"])
def test_d05_durable_history_damage_is_not_healed(tmp_path, monkeypatch, mutation):
    args, store, _ = make_case(tmp_path, monkeypatch)
    original = append(store, args)
    path = store.registry.safe_path(store.ref(1))
    if mutation == "hash":
        row = original.to_dict()
        row["sources"]["scope"]["market"] = "US"
        path.write_bytes(bounded(row))
    elif mutation == "gap":
        path.rename(path.with_name("0002.json"))
    else:
        (path.parent / "unreviewed.json").write_bytes(b"{}")
    before = tree_bytes(store.registry.root)
    with pytest.raises(ValueError):
        store.history()
    assert tree_bytes(store.registry.root) == before


def test_missing_read_only_source_does_not_create_database_or_parents(tmp_path):
    journal = PublicationJournal(tmp_path / "missing" / "new.sqlite")
    from ai_commerce.distribution_intelligence.reconciliation import _read_journal
    before = tree_bytes(tmp_path)
    with pytest.raises(ValueError):
        _read_journal(journal, "pub-missing", {}, scope_value())
    assert tree_bytes(tmp_path) == before and not journal.path.parent.exists()
