"""D05: read native A08/A15 custody; append only new offline corrections.

No call in this module submits, retries, authenticates a provider, or changes an
A08/A15 record. A separately pinned observation is still a supplied assertion.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from itertools import islice
from pathlib import Path
import sqlite3

from jsonschema import Draft202012Validator

from ai_commerce.analytics.files import ledger_path, local_path
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.distribution.assisted import inspect_package
from ai_commerce.distribution.receipt import PublicationJournal
from ai_commerce.operations.registry import ArtifactRegistry
from ai_commerce.operations.retry_guard import AttemptObservation, ReconciliationObservation, RetryGuard

from .schemas import (IntelligenceError, MAX_EVENTS, MAX_RECORDS, RECONCILIATION,
    bounded, detached, digest, instant, parse, registry_bytes, require, safe_file,
    scope_value, validate, within)

_MAX_JOURNAL = 64 * 1024 * 1024
_MAX_EVIDENCE = 5 * 1024 * 1024


def read_package(package_dir):
    """Bound every native file before A08 reopens and checks its full digest."""
    try:
        root = local_path(package_dir)
        names = {"package.json", "video.mp4", "title.txt", "caption.txt", "hashtags.txt", "publish-instructions.md"}
        require(root.is_dir() and set(p.name for p in islice(root.iterdir(), 7)) == names,
                "PACKAGE_INVENTORY_INVALID")
        for name in names:
            path = local_path(root / name)
            info = path.stat()
            require(path.is_file() and info.st_nlink == 1
                    and 0 <= info.st_size <= (256 * 1024 * 1024 if name == "video.mp4" else 256 * 1024),
                    "PACKAGE_FILE_UNSAFE_OR_TOO_LARGE")
        result = inspect_package(root)
        # Captions, instructions and URLs are not copied into this projection.
        return result
    except IntelligenceError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise IntelligenceError("PACKAGE_SOURCE_INVALID") from exc


def _package_scope(package, scope):
    for field in ("platform", "surface", "account_ref", "market", "app_ref", "product_ref"):
        require(package[field] == scope[field], "PACKAGE_SCOPE_MISMATCH")
    require(package["video_sha256"] == scope["content_sha256"], "PACKAGE_CONTENT_MISMATCH")


def publication_request_digest(package, publication_id, scope):
    """Explicit D05 binding for a native A15 intent, never a posting request.

    A generic media retry digest cannot prove a publication association. Existing
    unrelated A15 intents are deliberately not reinterpreted as distribution.
    """
    scope = scope_value(scope, publication=True)
    _package_scope(package, scope)
    return digest({"schema_version": "DistributionRequestBinding.v1", "publication_id": publication_id,
        "manual_attempt_id": "manual-" + publication_id, "package_sha256": package["package_sha256"],
        "scope": scope})


def _event_replay(events, created):
    state, visibility, post, receipt, last = "MANUAL_ACTION_REQUIRED", "UNKNOWN", None, None, instant(created)
    for event in events:
        when, kind = instant(event["recorded_at"]), event["event_type"]
        require(when >= last, "STALE_JOURNAL_EVENT")
        last = when
        evidence, event_post, url = event["evidence_sha256"], event["provider_post_id"], event["public_url_sha256"]
        if kind in {"SUBMISSION_REPORTED", "OUTCOME_UNKNOWN", "REJECTION_REPORTED"}:
            permitted = {"MANUAL_ACTION_REQUIRED", "UNKNOWN"} if kind == "SUBMISSION_REPORTED" else {
                "MANUAL_ACTION_REQUIRED", "SUBMITTED", "UNKNOWN"}
            require(state in permitted and event_post is None and url is None, "JOURNAL_TRANSITION_INVALID")
            state = {"SUBMISSION_REPORTED": "SUBMITTED", "OUTCOME_UNKNOWN": "UNKNOWN",
                     "REJECTION_REPORTED": "REJECTED"}[kind]
            visibility = "UNKNOWN"
        elif kind == "PUBLICATION_RECEIPT":
            require(state in {"SUBMITTED", "UNKNOWN"} and evidence is not None and event_post is not None,
                    "JOURNAL_RECEIPT_INVALID")
            state, visibility, post, receipt = "PUBLISHED_CONFIRMED", "UNKNOWN", event_post, evidence
        elif kind == "VISIBILITY_EVIDENCE":
            require(state == "PUBLISHED_CONFIRMED" and evidence is not None and evidence != receipt and url is not None
                    and event_post is None, "JOURNAL_VISIBILITY_INVALID")
            visibility = "PUBLIC"
        else:
            require(kind == "REMOVAL_REPORTED" and state == "PUBLISHED_CONFIRMED"
                    and evidence is not None and event_post is None and url is None, "JOURNAL_REMOVAL_INVALID")
            state, visibility = "REMOVED", "UNKNOWN"
    return state, visibility, post


def _read_journal(journal, publication_id, package, scope):
    require(type(journal) is PublicationJournal, "NATIVE_PUBLICATION_JOURNAL_REQUIRED")
    db = None
    try:
        path = ledger_path(journal.path, create=False)
        require(path.stat().st_size <= _MAX_JOURNAL, "JOURNAL_SIZE_LIMIT")
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5, isolation_level=None)
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("BEGIN")
        # A08 has no sealed root. Pin the complete bounded inventory as well as
        # the selected stream; this detects observed changes, not a total rewrite
        # by an attacker who also replaces all independently retained pins.
        require(db.execute("PRAGMA quick_check(1)").fetchone() == ("ok",), "JOURNAL_DATABASE_CORRUPT")
        tables = {r[0]: r[1] for r in db.execute(
            "SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        require(tables == {"publications": "table", "publication_events": "table"}, "JOURNAL_SCHEMA_INVALID")
        rows = db.execute("SELECT publication_id,idempotency_key,package_sha256,platform,surface,account_ref,created_at"
                          " FROM publications ORDER BY publication_id LIMIT 257").fetchall()
        require(len(rows) <= 256, "JOURNAL_PUBLICATION_LIMIT")
        stored = db.execute(
            "SELECT seq,publication_id,event_digest,CASE WHEN length(event_json)<=65536 THEN event_json END,"
            "CASE WHEN length(evidence_blob)<=? THEN evidence_blob END,evidence_sha256,length(evidence_blob)"
            " FROM publication_events ORDER BY seq LIMIT 2049", (_MAX_EVIDENCE,)).fetchall()
        require(len(stored) <= 2048, "JOURNAL_EVENT_LIMIT")
        require([row[0] for row in stored] == list(range(1, len(stored) + 1)), "JOURNAL_SEQUENCE_GAP")
        schema = parse(files("ai_commerce.distribution").joinpath("schemas", "PublicationEvent.v1.schema.json").read_bytes())
        validator = Draft202012Validator(schema)
        metadata, grouped, identities = {}, {}, set()
        for pid, key, sha, platform, surface, account_raw, created in rows:
            require(type(key) is str and 0 < len(key) <= 200, "JOURNAL_KEY_INVALID")
            account = parse(account_raw.encode("utf-8"), limit=4096)
            require(pid == "pub-" + digest({"idempotency_key": key, "package_sha256": sha})[:24], "JOURNAL_IDENTITY_INVALID")
            identity = digest({"package_sha256": sha, "platform": platform, "surface": surface, "account_ref": account})
            require(identity not in identities, "REKEYED_PUBLICATION")
            identities.add(identity)
            metadata[pid] = {"publication_id": pid, "idempotency_key_sha256": sha256_bytes(key.encode()),
                "package_sha256": sha, "platform": platform, "surface": surface, "account_ref": account, "created_at": created}
            grouped[pid] = []
        seen_events, seen_evidence, seen_posts, evidence_size = set(), set(), set(), 0
        for seq, pid, event_sha, raw, blob, evidence_sha, blob_size in stored:
            require(pid in metadata and type(raw) is bytes and sha256_bytes(raw) == event_sha, "JOURNAL_EVENT_HASH_INVALID")
            require(event_sha not in seen_events, "DUPLICATE_JOURNAL_EVENT")
            seen_events.add(event_sha)
            event = parse(raw, limit=65536)
            require(validator.is_valid(event) and canonical_bytes(event) == raw and event["publication_id"] == pid,
                    "JOURNAL_EVENT_INVALID")
            require((blob is None and blob_size is None and evidence_sha is None and event["evidence_sha256"] is None)
                    or (type(blob) is bytes and 0 < len(blob) <= _MAX_EVIDENCE
                        and sha256_bytes(blob) == evidence_sha == event["evidence_sha256"]), "JOURNAL_EVIDENCE_HASH_INVALID")
            evidence_size += blob_size or 0
            require(evidence_size <= _MAX_JOURNAL, "JOURNAL_EVIDENCE_SIZE_LIMIT")
            if evidence_sha is not None:
                require(evidence_sha not in seen_evidence, "DUPLICATE_RECEIPT_EVIDENCE")
                seen_evidence.add(evidence_sha)
            if event["event_type"] == "PUBLICATION_RECEIPT":
                address = (metadata[pid]["platform"], digest(metadata[pid]["account_ref"]), event["provider_post_id"])
                require(address not in seen_posts, "DUPLICATE_PROVIDER_POST")
                seen_posts.add(address)
            if event["public_url"] is not None:
                from urllib.parse import urlsplit
                url = urlsplit(event["public_url"])
                require(url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
                        and not url.query and not url.fragment, "JOURNAL_URL_UNSAFE")
            normalized = {"sequence": seq, "event_sha256": event_sha, "event_type": event["event_type"],
                "provider_post_id": event["provider_post_id"], "evidence_sha256": evidence_sha,
                "public_url_sha256": sha256_bytes(event["public_url"].encode()) if event["public_url"] else None,
                "recorded_at": event["recorded_at"], "provenance": "USER_SUPPLIED"}
            grouped[pid].append(normalized)
        streams = {}
        for pid, meta in metadata.items():
            events = grouped[pid]
            require(len(events) <= MAX_EVENTS, "PUBLICATION_EVENT_LIMIT")
            state, visibility, post = _event_replay(events, meta["created_at"])
            streams[pid] = {**meta, "events": events, "state": state,
                            "reported_visibility": visibility, "provider_post_id": post}
        require(publication_id in streams, "PUBLICATION_NOT_FOUND")
        selected = streams[publication_id]
        require(selected["package_sha256"] == package["package_sha256"], "JOURNAL_PACKAGE_MISMATCH")
        require(all(selected[key] == scope[key] for key in ("platform", "surface", "account_ref")), "JOURNAL_SCOPE_MISMATCH")
        within(selected["created_at"], scope["measurement_window"])
        for event in selected["events"]:
            within(event["recorded_at"], scope["measurement_window"])
        return {**selected, "journal_sha256": digest(selected), "inventory_sha256": digest(streams)}
    except IntelligenceError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
        raise IntelligenceError("JOURNAL_READ_UNPROVEN") from exc
    finally:
        if db is not None:
            db.close()


def _native_record(registry, ref, expected):
    value = parse(registry_bytes(registry, ref, limit=65536), limit=65536)
    body = {k: v for k, v in value.items() if k != "record_sha256"}
    require(value.get("record_sha256") == digest(body) and body == expected, "RETRY_SOURCE_RECORD_MISMATCH")


def _proof(registry, ref, sha):
    if ref is None:
        require(sha is None, "INCOMPLETE_EVIDENCE_PIN")
        return
    raw = registry_bytes(registry, ref, limit=_MAX_EVIDENCE)
    require(sha256_bytes(raw) == sha, "RETRY_EVIDENCE_HASH_INVALID")


def _read_retry(guard, intent_id, attempt, request_digest):
    require(type(guard) is RetryGuard and type(guard.registry) is ArtifactRegistry, "NATIVE_RETRY_GUARD_REQUIRED")
    require(type(attempt) is int and 1 <= attempt <= MAX_RECORDS, "ATTEMPT_INVALID")
    try:
        directory = guard.registry.safe_path(guard._base(intent_id))
        require(directory.is_dir() and len(list(islice(directory.iterdir(), MAX_RECORDS + 2))) <= MAX_RECORDS + 1,
                "RETRY_HISTORY_SIZE_LIMIT")
        numbers = guard._attempt_ids(intent_id)
        require(numbers and numbers[-1] == attempt, "STALE_ATTEMPT_VIEW")
        intent = guard.read_intent(intent_id)
        require(intent["request_digest"] == request_digest, "RETRY_PUBLICATION_BINDING_MISMATCH")
        _native_record(guard.registry, guard._base(intent_id) + "/intent.json", intent)
        attempts, history_reconciliations = [], []
        for number in numbers:
            item = guard.read_attempt(intent_id, number)
            _native_record(guard.registry, guard._base(intent_id) + f"/attempt-{number}.json", item)
            _proof(guard.registry, item["evidence_ref"], item["evidence_sha256"])
            attempts.append(item)
            prior_reconciliation = guard.read_reconciliation(intent_id, number)
            if prior_reconciliation is not None:
                _native_record(guard.registry, guard.reconciliation_ref(intent_id, number), prior_reconciliation)
                _proof(guard.registry, prior_reconciliation["evidence_ref"], prior_reconciliation["evidence_sha256"])
                history_reconciliations.append(prior_reconciliation)
            if number < attempt:
                require(item["outcome"] in {"FAILED_NO_EFFECT", "RECONCILED_NO_EFFECT"}
                        or prior_reconciliation is not None and prior_reconciliation["side_effects"] == "NONE",
                        "PREVIOUS_ATTEMPT_EFFECT_UNRESOLVED")
        reconciliation = guard.read_reconciliation(intent_id, attempt)
        if reconciliation is not None:
            _native_record(guard.registry, guard.reconciliation_ref(intent_id, attempt), reconciliation)
            _proof(guard.registry, reconciliation["evidence_ref"], reconciliation["evidence_sha256"])
        decision = guard.decision(intent_id, last_attempt=attempt)
        require(not decision["provider_called"] and not decision["retry_dispatched"], "RETRY_SIDE_EFFECTS_FORBIDDEN")
        body = {"intent": intent, "attempts": attempts, "reconciliation": reconciliation,
                "history_reconciliations": history_reconciliations, "native_decision": decision["decision"]}
        return {**body, "sha256": digest(body)}
    except IntelligenceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        raise IntelligenceError("RETRY_SOURCE_UNPROVEN") from exc


def _check_sources(value):
    value = validate("Sources", value)
    scope = scope_value(value["scope"], publication=True)
    require(value["source_sha256"] == digest({k: v for k, v in value.items() if k != "source_sha256"}), "SOURCE_DIGEST_MISMATCH")
    publication, retry = value["publication"], value["retry"]
    require(publication["journal_sha256"] == digest({k: v for k, v in publication.items()
        if k not in {"journal_sha256", "inventory_sha256"}}), "JOURNAL_SNAPSHOT_DIGEST_MISMATCH")
    require(retry["sha256"] == digest({k: v for k, v in retry.items() if k != "sha256"}), "RETRY_SNAPSHOT_DIGEST_MISMATCH")
    require(value["package"]["package_sha256"] == publication["package_sha256"]
            and value["package"]["video_sha256"] == scope["content_sha256"]
            and all(publication[k] == scope[k] for k in ("platform", "surface", "account_ref")), "SOURCE_BINDING_MISMATCH")
    require(retry["intent"]["request_digest"] == digest({"schema_version": "DistributionRequestBinding.v1",
            "publication_id": publication["publication_id"], "manual_attempt_id": "manual-" + publication["publication_id"],
            "package_sha256": value["package"]["package_sha256"], "scope": scope}),
            "RETRY_PUBLICATION_BINDING_MISMATCH")
    observed = _event_replay(publication["events"], publication["created_at"])
    require(observed == (publication["state"], publication["reported_visibility"], publication["provider_post_id"]),
            "PUBLICATION_SNAPSHOT_REPLAY_MISMATCH")
    within(publication["created_at"], scope["measurement_window"])
    receipt_hashes = []
    for event in publication["events"]:
        within(event["recorded_at"], scope["measurement_window"])
        if event["evidence_sha256"]:
            receipt_hashes.append(event["evidence_sha256"])
    require(len(set(receipt_hashes)) == len(receipt_hashes), "DUPLICATE_RECEIPT_EVIDENCE")
    require([a["attempt"] for a in retry["attempts"]] == list(range(1, len(retry["attempts"]) + 1)), "ATTEMPT_HISTORY_INVALID")
    for attempt in retry["attempts"]:
        AttemptObservation(**{k: v for k, v in attempt.items() if k != "version"})
        require(attempt["intent_id"] == retry["intent"]["intent_id"], "ATTEMPT_IDENTITY_MISMATCH")
    last = retry["attempts"][-1]
    history_reconciliations = {}
    for native in retry["history_reconciliations"]:
        ReconciliationObservation(**{k: v for k, v in native.items() if k != "version"})
        number = native["attempt"]
        require(number not in history_reconciliations and 1 <= number <= len(retry["attempts"]), "RECONCILIATION_HISTORY_INVALID")
        original = retry["attempts"][number - 1]
        require(native["intent_id"] == original["intent_id"] and native["original_record_sha256"] == digest(original)
                and native["request_digest"] == retry["intent"]["request_digest"]
                and original["outcome"] in {"PENDING", "UNKNOWN_EFFECT"}, "RECONCILIATION_HISTORY_BINDING_INVALID")
        history_reconciliations[number] = native
    require(history_reconciliations.get(last["attempt"]) == retry["reconciliation"], "LATEST_RECONCILIATION_MISMATCH")
    for original in retry["attempts"][:-1]:
        prior = history_reconciliations.get(original["attempt"])
        require(original["side_effects"] == "NONE" or prior is not None and prior["side_effects"] == "NONE",
                "PREVIOUS_ATTEMPT_EFFECT_UNRESOLVED")
    if retry["reconciliation"] is not None:
        native = retry["reconciliation"]
        ReconciliationObservation(**{k: v for k, v in native.items() if k != "version"})
        require(native["intent_id"] == last["intent_id"] and native["attempt"] == last["attempt"]
                and native["original_record_sha256"] == digest(last)
                and native["request_digest"] == retry["intent"]["request_digest"]
                and last["outcome"] in {"PENDING", "UNKNOWN_EFFECT"}, "NATIVE_RECONCILIATION_BINDING_INVALID")
    hashes = {e["evidence_sha256"] for e in publication["events"] if e["evidence_sha256"]}
    hashes.update(a["evidence_sha256"] for a in retry["attempts"] if a["evidence_sha256"])
    require(len({e["event_sha256"] for e in publication["events"]}) == len(publication["events"]), "DUPLICATE_JOURNAL_EVENT")
    ids, refs, observed_posts = set(), set(), set()
    last_time = instant(publication["events"][-1]["recorded_at"] if publication["events"] else publication["created_at"])
    for observation in value["observations"]:
        pin, doc = observation["pin"], observation["document"]
        require(doc["scope"] == scope and doc["publication_id"] == publication["publication_id"]
                and doc["intent_id"] == last["intent_id"] and doc["attempt"] == last["attempt"]
                and doc["request_digest"] == retry["intent"]["request_digest"]
                and doc["original_record_sha256"] == digest(last), "OBSERVATION_SCOPE_BINDING_MISMATCH")
        require(doc["observation_id"] not in ids and pin["ref"] not in refs and pin["sha256"] not in hashes
                and doc["evidence_sha256"] not in hashes and doc["evidence_ref"] != pin["ref"],
                "OBSERVATION_NOT_INDEPENDENT")
        require(pin["sha256"] == sha256_bytes(bounded(doc)), "OBSERVATION_DOCUMENT_HASH_MISMATCH")
        ids.add(doc["observation_id"])
        refs.add(pin["ref"])
        hashes.add(pin["sha256"])
        hashes.add(doc["evidence_sha256"])
        within(doc["observed_at"], scope["measurement_window"])
        require(instant(doc["observed_at"]) > last_time, "STALE_INDEPENDENT_OBSERVATION")
        last_time = instant(doc["observed_at"])
        require((doc["side_effects"] == "CONFIRMED") == (doc["provider_post_id"] is not None), "OBSERVATION_POST_ID_REQUIRED")
        require(publication["provider_post_id"] is None or doc["provider_post_id"] in {None, publication["provider_post_id"]},
                "OBSERVATION_POST_ID_MISMATCH")
        if doc["provider_post_id"] is not None:
            observed_posts.add(doc["provider_post_id"])
            require(len(observed_posts) == 1, "OBSERVATION_POST_ID_COLLISION")
    return value


def inspect_sources(*, package_dir, journal, guard, scope, publication_id, intent_id, attempt, observation_pins=()):
    """Read actual immutable source bytes; retain a pin before authorizing a record.

    Observation pins are explicit opt-in and must point to canonical, separately
    scoped DistributionScopedObservation.v1 bytes in the native A15 registry.
    A generic A15 reconciliation, even if affirmative, is not enough.
    """
    scope = scope_value(scope, publication=True)
    require(type(observation_pins) in {tuple, list} and len(observation_pins) <= 32, "OBSERVATION_PIN_LIMIT")
    package = read_package(package_dir)
    _package_scope(package, scope)
    publication = _read_journal(journal, publication_id, package, scope)
    request = publication_request_digest(package, publication_id, scope)
    retry = _read_retry(guard, intent_id, attempt, request)
    observations = []
    for pin in observation_pins:
        pin = validate("ObservationPin", pin)
        raw = registry_bytes(guard.registry, pin["ref"], limit=65536)
        require(sha256_bytes(raw) == pin["sha256"], "OBSERVATION_PIN_MISMATCH")
        document = validate("Observation", parse(raw, limit=65536))
        require(raw == bounded(document), "OBSERVATION_MUST_BE_CANONICAL")
        _proof(guard.registry, document["evidence_ref"], document["evidence_sha256"])
        observations.append({"pin": pin, "document": document})
    body = {"schema_version": "DistributionSourceSnapshot.v1", "scope": scope,
        "package": {k: package[k] for k in ("package_id", "package_sha256", "source_manifest_sha256", "video_sha256")},
        "publication": publication, "retry": retry, "observations": observations,
        "verification_scope": "LOCAL_SOURCE_REPLAY_NOT_PROVIDER_ATTESTED"}
    return _check_sources({**body, "source_sha256": digest(body)})


def derive_states(sources):
    sources = _check_sources(sources)
    publication, retry = sources["publication"], sources["retry"]
    old = {"publication": publication["state"], "reported_visibility": publication["reported_visibility"],
           "attempt_outcome": retry["attempts"][-1]["outcome"]}
    positive, negative = False, False
    reasons = {"OFFLINE_UNATTESTED", "REPLAY_NOT_AUTHORIZED", "ORIGINAL_AMBIGUITY_RETAINED"}
    if publication["state"] in {"PUBLISHED_CONFIRMED", "REMOVED"}:
        positive = True
        reasons.add("USER_SUPPLIED_PUBLICATION_RECEIPT")
    if publication["state"] == "REJECTED":
        negative = True
        reasons.add("USER_SUPPLIED_REJECTION")
    positive |= retry["attempts"][-1]["side_effects"] == "CONFIRMED"
    negative |= retry["attempts"][-1]["side_effects"] == "NONE"
    if retry["reconciliation"]:
        positive |= retry["reconciliation"]["side_effects"] == "CONFIRMED"
        negative |= retry["reconciliation"]["side_effects"] == "NONE"
        reasons.add("GENERIC_A15_RECONCILIATION_IS_NOT_POST_PERMISSION")
    for observation in sources["observations"]:
        positive |= observation["document"]["side_effects"] == "CONFIRMED"
        negative |= observation["document"]["side_effects"] == "NONE"
    if positive and negative:
        status, decision = "CONFLICTING_EVIDENCE", "RECONCILE_REQUIRED"
        reasons.add("COMPETING_POSITIVE_AND_NEGATIVE_EVIDENCE")
    elif any(o["document"]["evidence_origin"] == "USER_SUPPLIED" for o in sources["observations"]):
        if positive:
            status, decision = "OBSERVED_CONFIRMED_UNATTESTED", "NO_RETRY"
        else:
            status, decision = "OBSERVED_NO_EFFECT_UNATTESTED", "REQUIRES_SEPARATE_AUTHORIZATION"
        reasons.add("SEPARATELY_SCOPED_SUPPLIED_OBSERVATION")
    else:
        status, decision = "RECONCILE_REQUIRED", "RECONCILE_REQUIRED"
        reasons.add("INDEPENDENT_SCOPED_OBSERVATION_REQUIRED")
        if sources["observations"]:
            reasons.add("SYNTHETIC_OBSERVATION_NOT_EFFECT_PROOF")
    return old, {"status": status, "decision": decision, "public_visibility": "UNKNOWN",
                 "replay_safe": False, "provider_attested": False}, sorted(reasons)


@dataclass(frozen=True, slots=True, init=False)
class DistributionReconciliation:
    _raw: bytes

    @classmethod
    def from_bytes(cls, raw, *, expected_id=None):
        value = validate(RECONCILIATION, parse(raw))
        body = {k: v for k, v in value.items() if k not in {"record_sha256", "record_id"}}
        sha = digest(body)
        require(value["record_sha256"] == sha and value["record_id"] == "a17-reconciliation-" + sha,
                "RECONCILIATION_DIGEST_MISMATCH")
        require(expected_id is None or value["record_id"] == expected_id, "RECONCILIATION_PIN_MISMATCH")
        old, new, reasons = derive_states(value["sources"])
        require((old, new, reasons) == (value["old_state"], value["new_state"], value["reason_codes"]),
                "RECONCILIATION_REPLAY_MISMATCH")
        result = object.__new__(cls)
        object.__setattr__(result, "_raw", bounded(value))
        return result

    def to_bytes(self):
        return self._raw

    def to_dict(self):
        return parse(self._raw)

    @property
    def record_id(self):
        return self.to_dict()["record_id"]


def _identity(sources):
    # Re-keying the A15 intent or A08 publication cannot create a second effect.
    return digest({"package": sources["package"]["package_sha256"],
                   "account": sources["scope"]["account_ref"], "platform": sources["scope"]["platform"]})


def _admit(records, value):
    identity = _identity(value["sources"])
    same = [r for r in records if _identity(r["sources"]) == identity]
    for old in records:
        if old["key"] == value["key"]:
            require(old == value, "RECONCILIATION_KEY_COLLISION")
            return False
        require(old["sources"]["source_sha256"] != value["sources"]["source_sha256"], "DUPLICATE_RECONCILIATION_REKEY")
        before_pub, after_pub = old["sources"]["publication"], value["sources"]["publication"]
        if before_pub["publication_id"] != after_pub["publication_id"]:
            old_hashes = {e["evidence_sha256"] for e in before_pub["events"] if e["evidence_sha256"]}
            new_hashes = {e["evidence_sha256"] for e in after_pub["events"] if e["evidence_sha256"]}
            require(not old_hashes & new_hashes, "REKEYED_RECEIPT_ACROSS_RECORDS")
            require(not (before_pub["platform"] == after_pub["platform"] and
                before_pub["account_ref"] == after_pub["account_ref"] and before_pub["provider_post_id"] is not None and
                before_pub["provider_post_id"] == after_pub["provider_post_id"]), "REKEYED_POST_ACROSS_RECORDS")
    require(value["sequence"] == len(records) + 1 and value["previous_sha256"] ==
            (records[-1]["record_sha256"] if records else None), "RECONCILIATION_SEQUENCE_INVALID")
    if not same:
        require(value["revision"] == 1 and value["parent_id"] is None, "RECONCILIATION_PARENT_INVALID")
    else:
        parent = same[-1]
        before, after = parent["sources"], value["sources"]
        require(value["parent_id"] == parent["record_id"] and value["revision"] == parent["revision"] + 1,
                "STALE_RECONCILIATION_PARENT")
        require(before["scope"] == after["scope"] and before["package"] == after["package"]
                and before["publication"]["publication_id"] == after["publication"]["publication_id"]
                and before["retry"]["intent"] == after["retry"]["intent"]
                and before["retry"]["attempts"] == after["retry"]["attempts"], "REVISION_SOURCE_BINDING_MISMATCH")
        require(all(before["publication"][k] == after["publication"][k] for k in ("idempotency_key_sha256",
            "package_sha256", "platform", "surface", "account_ref", "created_at")), "REVISION_PUBLICATION_METADATA_CHANGED")
        require(after["publication"]["events"][:len(before["publication"]["events"])] == before["publication"]["events"]
                and after["observations"][:len(before["observations"])] == before["observations"]
                and (before["retry"]["reconciliation"] is None or
                     before["retry"]["reconciliation"] == after["retry"]["reconciliation"]), "REVISION_EVIDENCE_TRUNCATED")
        require(before != after, "EMPTY_RECONCILIATION_REVISION")
    return True


class ReconciliationLedger:
    """One bounded immutable record per append, no mutable secondary index.

    Registry worker serialization covers all local writers. A crashed lock is a
    visible failure, never an implicit retry. Reads do not acquire/write a lock.
    """
    def __init__(self, registry):
        require(type(registry) is ArtifactRegistry, "ARTIFACT_REGISTRY_REQUIRED")
        self.registry = registry

    @staticmethod
    def ref(sequence):
        require(type(sequence) is int and 1 <= sequence <= MAX_RECORDS, "RECONCILIATION_LIMIT")
        return f"distribution-intelligence/reconciliations/{sequence:04d}.json"

    def history(self):
        root = self.registry.safe_path("distribution-intelligence/reconciliations")
        if not root.exists():
            return ()
        names = sorted(p.name for p in islice(root.iterdir(), MAX_RECORDS + 1))
        require(len(names) <= MAX_RECORDS and names == [f"{n:04d}.json" for n in range(1, len(names) + 1)],
                "RECONCILIATION_INVENTORY_INVALID")
        records, size = [], 0
        for number in range(1, len(names) + 1):
            raw = registry_bytes(self.registry, self.ref(number))
            size += len(raw)
            require(size <= 32 * 1024 * 1024, "RECONCILIATION_HISTORY_SIZE_LIMIT")
            value = DistributionReconciliation.from_bytes(raw).to_dict()
            require(value["sequence"] == number and _admit(records, value), "RECONCILIATION_HISTORY_INVALID")
            records.append(value)
        return tuple(DistributionReconciliation.from_bytes(bounded(row)) for row in records)

    def get(self, record_id):
        for record in self.history():
            if record.record_id == record_id:
                return record
        raise IntelligenceError("RECONCILIATION_NOT_FOUND")

    def reconcile(self, *, key, expected_source_sha256, parent_id=None, **source_selection):
        sources = inspect_sources(**source_selection)
        require(sources["source_sha256"] == expected_source_sha256, "STALE_SOURCE_PIN")
        old_state, new_state, reasons = derive_states(sources)
        with self.registry.worker():
            records = [row.to_dict() for row in self.history()]
            for old in records:
                if old["key"] == key:
                    require(old["sources"] == sources and old["parent_id"] == parent_id, "RECONCILIATION_KEY_COLLISION")
                    return DistributionReconciliation.from_bytes(bounded(old))
            parents = [r for r in records if _identity(r["sources"]) == _identity(sources)]
            body = {"schema_version": RECONCILIATION, "sequence": len(records) + 1,
                "revision": parents[-1]["revision"] + 1 if parents else 1,
                "key": key, "parent_id": parent_id, "previous_sha256": records[-1]["record_sha256"] if records else None,
                "sources": sources, "old_state": old_state, "new_state": new_state, "reason_codes": reasons,
                "provider_called": False, "retry_dispatched": False}
            sha = digest(body)
            record = DistributionReconciliation.from_bytes(bounded({**body, "record_sha256": sha,
                "record_id": "a17-reconciliation-" + sha}))
            _admit(records, record.to_dict())
            # No stale cross-store view is committed if native sources moved
            # during validation. This is not a distributed transaction/attestation.
            require(inspect_sources(**source_selection) == sources, "SOURCE_CHANGED_DURING_RECONCILIATION")
            require(len(records) < MAX_RECORDS and sum(len(bounded(r)) for r in records) + len(record.to_bytes())
                    <= 32 * 1024 * 1024, "RECONCILIATION_HISTORY_SIZE_LIMIT")
            self.registry.put_immutable(self.ref(len(records) + 1), record.to_bytes())
            return record
