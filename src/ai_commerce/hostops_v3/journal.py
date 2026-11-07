"""Durable intent BEFORE effects, process-wide serialization, no content in journal."""
from __future__ import annotations

from contextlib import contextmanager
import sqlite3

from .contract import canonical, decode, deny


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), timeout=0.1, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                state TEXT NOT NULL, receipt BLOB);
            CREATE TABLE IF NOT EXISTS previews (fingerprint TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, epoch INTEGER NOT NULL);
        """)

    def close(self):
        self.db.close()

    def begin(self):
        try:
            self.db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            deny("HOST_BUSY")

    def epoch(self, epoch):
        old = self.db.execute("SELECT epoch FROM settings WHERE id='revocation'").fetchone()
        if old and epoch < old[0]:
            deny("REVOCATION_ROLLBACK")
        self.db.execute("INSERT OR REPLACE INTO settings VALUES ('revocation', ?)", (epoch,))

    @staticmethod
    def request_key(request):
        return ("v4:" if request.value["schema"] == "hostops.request.v4" else "") + request.value["request_id"]

    def reserve(self, request, epoch):
        self.begin()
        try:
            self.epoch(epoch)
            row = self.db.execute("SELECT fingerprint,state,receipt FROM requests WHERE id=?",
                                  (self.request_key(request),)).fetchone()
            if row:
                if row[0] != request.fingerprint:
                    deny("REQUEST_ID_CONFLICT")
                return row
            if self.db.execute("SELECT count(*) FROM requests").fetchone()[0] >= 100000:
                deny("JOURNAL_LIMIT")
            self.db.execute("INSERT INTO requests VALUES (?,?,'INTENT',NULL)",
                            (self.request_key(request), request.fingerprint))
            return None
        finally:
            # A semantic conflict must not erase the previous intent.
            self.db.commit()

    @contextmanager
    def execution(self):
        self.begin()
        try:
            yield
        finally:
            self.db.commit()

    def finish(self, request, receipt):
        self.db.execute("UPDATE requests SET state=?,receipt=? WHERE id=? AND fingerprint=?",
                        (receipt["status"], canonical(receipt), self.request_key(request), request.fingerprint))

    def preview(self, fingerprint):
        self.db.execute("INSERT OR IGNORE INTO previews VALUES (?)", (fingerprint,))

    def has_preview(self, fingerprint):
        return bool(self.db.execute("SELECT 1 FROM previews WHERE fingerprint=?", (fingerprint,)).fetchone())

    @staticmethod
    def previous(row):
        return decode(row[2]) if row[2] else None

# W1 signed-intent source prototype. Never called by HostOps worker/CLI.
from pathlib import Path
import time
from . import auth
from .contract import Denied, sha, shape


class OfflineW1IntentLedger:
    """Durable, fail-closed prototype for one-request-at-most-once admission.

    Recording a signed proposal immediately places it into an unresolved state.
    Neither a duplicate nor a crash can authorize a write; there is purposely
    no executor, retry, reconciliation, or transition-to-APPLIED method.
    """

    def __init__(self, path):
        path = Path(path)
        if not path.is_absolute() or path.suffix != ".sqlite3" or not path.parent.is_dir():
            deny("W1_LEDGER_PATH_DENIED")
        self.db = sqlite3.connect(str(path), timeout=0.25, isolation_level=None)
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS w1_intents (
                    request_id TEXT PRIMARY KEY,
                    proposal_sha256 TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    source_sha TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state='UNKNOWN_UNRECONCILED')
                )
            """)
        except sqlite3.Error:
            self.close()
            deny("W1_LEDGER_UNAVAILABLE")

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def record_verified_proposal(self, proposal, **verify_kwargs):
        """Crypto verification BEFORE durable reservation; never performs W1 effect.

        Caller-supplied verifier hashes are still subject to external trusted
        provisioning before future host use; a same-SID file is NOT such proof.
        """
        receipt = auth.personal_w1_verify_offline(proposal, **verify_kwargs)
        if (receipt["status"] != "SIGNED_PROPOSAL_VERIFIED_NO_EFFECT" or
                receipt["execution_allowed"] is not False):
            deny("W1_EFFECT_NOT_AUTHORIZED")
        request_id = receipt["request_id"]
        fingerprint = sha(receipt["proposal_sha256"])
        result_status = "INTENT_STAGED_NO_EFFECT"
        try:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute(
                "SELECT proposal_sha256,issue_number,source_sha FROM w1_intents WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if existing is not None:
                if existing != (fingerprint, receipt["issue_number"], receipt["source_sha"]):
                    deny("W1_REQUEST_ID_CONFLICT")
                result_status = "HOLD_REPLAY_UNKNOWN"
            else:
                if self.db.execute("SELECT COUNT(*) FROM w1_intents").fetchone()[0] >= 10000:
                    deny("W1_LEDGER_LIMIT")
                self.db.execute(
                    "INSERT INTO w1_intents VALUES (?,?,?,?,'UNKNOWN_UNRECONCILED')",
                    (request_id, fingerprint, receipt["issue_number"], receipt["source_sha"]),
                )
            self.db.execute("COMMIT")
        except (sqlite3.Error, Denied) as exc:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if isinstance(exc, Denied):
                raise
            deny("W1_LEDGER_UNAVAILABLE")
        return {
            "schema": "hostops.personal.w1_offline_intent.v1",
            "status": result_status,
            "request_id": request_id,
            "proposal_sha256": fingerprint,
            "source_sha": receipt["source_sha"],
            "execution_allowed": False,
            "disk_effect_verified": False,
            "credential_custody_proven": False,
        }


def classify_custody_claim(claim):
    """Conservative classification, NOT a trust attestation or token enrollment."""
    shape(claim, "single_windows_account public_runner_same_sid signing_key_on_host "
                 "long_lived_token_on_host external_signer_claimed external_token_broker_claimed")
    if any(type(value) is not bool for value in claim.values()):
        deny("W1_CUSTODY_BAD_CLAIM")
    if not claim["single_windows_account"]:
        deny("W1_ARCHITECTURE_MISMATCH")
    if claim["signing_key_on_host"] or claim["long_lived_token_on_host"]:
        status = "HOLD_SAME_SID_SECRET_EXPOSURE"
    elif not (claim["external_signer_claimed"] and claim["external_token_broker_claimed"]):
        status = "HOLD_EXTERNAL_BOUNDARY_MISSING"
    else:
        # Locally supplied booleans can never independently attest isolation.
        status = "HOLD_EXTERNAL_ATTESTATION_REQUIRED"
    return {
        "schema": "hostops.personal.w1_custody_assessment.v1",
        "status": status,
        "unattended_ready": False,
        "write_enabled": False,
        "human_enrollment_required": True,
    }


class OfflineWorkOwnerEffectLedger:
    """Durable, fail-closed ledger for WorkOwner effect-intent reservations.

    At-most-once reservation with exclusive ownership, fsync, and bounded crash behavior.
    Preserves unknown-before/after effect semantics, independently binds
    ledger/journal/source/round, and rejects drift, replay, duplicate or rollback.
    """

    def __init__(self, path):
        path = Path(path)
        if not path.is_absolute() or path.suffix not in (".sqlite3", ".sqlite") or not path.parent.is_dir():
            deny("EFFECT_LEDGER_PATH_DENIED")
        self.path = path
        self.db = sqlite3.connect(str(path), timeout=0.25, isolation_level=None)
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS workowner_effect_intents (
                    request_id TEXT PRIMARY KEY,
                    replay_id TEXT NOT NULL UNIQUE,
                    nonce TEXT NOT NULL UNIQUE,
                    repo TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    root_issue INTEGER NOT NULL,
                    current_issue INTEGER NOT NULL,
                    attempt_id TEXT NOT NULL,
                    source_sha TEXT NOT NULL,
                    journal_tip_sha TEXT NOT NULL,
                    journal_digest TEXT NOT NULL,
                    fence TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    provider_intent_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('INTENT_RESERVED_NO_EFFECT', 'HOLD_REPLAY_UNKNOWN')),
                    created_at INTEGER NOT NULL
                )
            """)
        except sqlite3.Error:
            self.close()
            deny("EFFECT_LEDGER_UNAVAILABLE")

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def record_effect_intent(self, request, *, now=None):
        shape(request, "request_id replay_id nonce repo action_id root_issue current_issue attempt_id source_sha work_owner provider_intent")
        shape(request["work_owner"], "root_commit_sha root_digest journal_tip_sha journal_digest work_key fingerprint fence generation")
        shape(request["provider_intent"], "commit_sha digest round")

        req_id = request["request_id"]
        replay_id = request["replay_id"]
        nonce = request["nonce"]
        for val in (req_id, replay_id, nonce, request["provider_intent"]["digest"],
                    request["work_owner"]["root_digest"], request["work_owner"]["journal_digest"],
                    request["work_owner"]["work_key"], request["work_owner"]["fingerprint"],
                    request["work_owner"]["fence"]):
            sha(val)
        for val in (request["source_sha"], request["work_owner"]["root_commit_sha"],
                    request["work_owner"]["journal_tip_sha"], request["provider_intent"]["commit_sha"]):
            sha(val, 40)
        for num in (request["root_issue"], request["current_issue"], request["work_owner"]["generation"]):
            if type(num) is not int or num < 1 or num > 2**53 - 1:
                deny("EFFECT_INTEGER_INVALID")
        round_num = request["provider_intent"]["round"]
        if type(round_num) is not int or round_num < 0 or round_num > 31:
            deny("EFFECT_INTEGER_INVALID")
        if not isinstance(request["repo"], str) or not request["repo"]:
            deny("EFFECT_REPO_INVALID")
        if not isinstance(request["action_id"], str) or not request["action_id"]:
            deny("EFFECT_ACTION_INVALID")
        if not isinstance(request["attempt_id"], str) or not request["attempt_id"]:
            deny("EFFECT_ATTEMPT_INVALID")

        timestamp = int(now if now is not None else time.time())
        result_status = "INTENT_RESERVED_NO_EFFECT"
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if self.db.execute("SELECT COUNT(*) FROM workowner_effect_intents").fetchone()[0] >= 10000:
                deny("EFFECT_LEDGER_LIMIT")

            existing = self.db.execute(
                "SELECT request_id, replay_id, nonce, repo, action_id, root_issue, current_issue, attempt_id, "
                "source_sha, journal_tip_sha, journal_digest, fence, generation, round, provider_intent_digest "
                "FROM workowner_effect_intents WHERE request_id=? OR replay_id=? OR nonce=?",
                (req_id, replay_id, nonce)
            ).fetchone()

            expected_tuple = (
                req_id, replay_id, nonce, request["repo"], request["action_id"],
                request["root_issue"], request["current_issue"], request["attempt_id"],
                request["source_sha"], request["work_owner"]["journal_tip_sha"],
                request["work_owner"]["journal_digest"], request["work_owner"]["fence"],
                request["work_owner"]["generation"], round_num, request["provider_intent"]["digest"]
            )

            if existing is not None:
                if existing != expected_tuple:
                    deny("EFFECT_REQUEST_ID_CONFLICT")
                result_status = "HOLD_REPLAY_UNKNOWN"
            else:
                prior = self.db.execute(
                    "SELECT fence, generation, round, source_sha, journal_digest FROM workowner_effect_intents "
                    "WHERE repo=? AND attempt_id=?",
                    (request["repo"], request["attempt_id"])
                ).fetchall()
                for p_fence, p_gen, p_round, p_source, p_j_digest in prior:
                    if p_source != request["source_sha"]:
                        deny("EFFECT_SOURCE_DRIFT")
                    if p_gen > request["work_owner"]["generation"]:
                        deny("EFFECT_GENERATION_ROLLBACK")
                    if p_gen == request["work_owner"]["generation"] and p_round > round_num:
                        deny("EFFECT_ROUND_ROLLBACK")
                    if p_gen == request["work_owner"]["generation"] and p_fence != request["work_owner"]["fence"]:
                        deny("EFFECT_FENCE_CONFLICT")

                self.db.execute(
                    "INSERT INTO workowner_effect_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (req_id, replay_id, nonce, request["repo"], request["action_id"],
                     request["root_issue"], request["current_issue"], request["attempt_id"],
                     request["source_sha"], request["work_owner"]["journal_tip_sha"],
                     request["work_owner"]["journal_digest"], request["work_owner"]["fence"],
                     request["work_owner"]["generation"], round_num,
                     request["provider_intent"]["digest"], "INTENT_RESERVED_NO_EFFECT", timestamp)
                )
            self.db.execute("COMMIT")
        except (sqlite3.Error, Denied) as exc:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if isinstance(exc, Denied):
                raise
            deny("EFFECT_LEDGER_UNAVAILABLE")

        return {
            "schema": "hostops.workowner_effect_intent.v1",
            "status": result_status,
            "request_id": req_id,
            "replay_id": replay_id,
            "nonce": nonce,
            "repo": request["repo"],
            "action_id": request["action_id"],
            "root_issue": request["root_issue"],
            "current_issue": request["current_issue"],
            "attempt_id": request["attempt_id"],
            "source_sha": request["source_sha"],
            "fence": request["work_owner"]["fence"],
            "generation": request["work_owner"]["generation"],
            "round": round_num,
            "execution_allowed": False,
            "disk_effect_verified": False,
            "credential_custody_proven": False,
            "native_side_effect_granted": False,
        }

    def unreconciled_intents_count(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM workowner_effect_intents WHERE state='INTENT_RESERVED_NO_EFFECT'"
        ).fetchone()[0]

