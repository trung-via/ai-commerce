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
from . import auth
from .contract import Denied, sha, shape


# WP/EPWO source-only ledger and intent fixture are retired. Historical evidence
# remains in Git history, never imported by active HostOps journal.


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
