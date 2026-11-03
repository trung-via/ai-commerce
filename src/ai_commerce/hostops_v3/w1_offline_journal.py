"""W1 offline signed-intent journal and custody decision — NO host writer.

This is deliberately not imported by the HostOps CLI/transport/worker. SQLite
writes affect only an explicitly supplied *test/review ledger*, never a laptop
target path. No external trust boundary or real file effect is established.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from . import auth
from .contract import Denied, deny, sha, shape


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
