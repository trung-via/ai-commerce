"""Exact, offline A08 receipt matching; never changes either evidence ledger."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from jsonschema import Draft202012Validator

from ai_commerce.distribution.assisted import DistributionError
from ai_commerce.distribution.receipt import PublicationJournal

from .files import ledger_path
from .ledger import AnalyticsLedger
from .model import AnalyticsError, LedgerError, checked_url, digest, require, validate_target
from .schemas import ACCOUNT, VERIFICATION

_PUBLICATION_ID = re.compile(r"pub-[a-f0-9]{24}")
_SHA = re.compile(r"[a-f0-9]{64}")
MAX_PUBLICATIONS = 100


class _ReadOnlyJournal(PublicationJournal):
    """Keep A08's actual get()/event replay, replacing only its DDL connection."""

    def _connect(self):
        # SQLite mode=ro can still create WAL/SHM sidecars. Only quiescent,
        # rollback-mode A08 journals are supported, so reject those inputs
        # before SQLite opens them. Never ignore a WAL via immutable=1.
        if any(Path(str(self.path) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")):
            raise LedgerError("PUBLICATION_JOURNAL_NOT_QUIESCENT_ROLLBACK")
        with self.path.open("rb") as stream:
            header = stream.read(100)
        if header[:16] == b"SQLite format 3\x00" and header[18:20] != b"\x01\x01":
            raise LedgerError("PUBLICATION_JOURNAL_NOT_QUIESCENT_ROLLBACK")
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True,
                             timeout=5, isolation_level=None)
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            return db
        except Exception:
            db.close()
            raise


def _check_receipt(receipt: dict, publication_id: str) -> None:
    """Validate A08's typed identity without flattening or coercing identifiers."""
    try:
        account = receipt["account_identity"]
        require(Draft202012Validator(ACCOUNT).is_valid(account), "INVALID_RECEIPT_ACCOUNT")
        validate_target({**receipt, "account_ref": account})
        require(receipt["publication_id"] == publication_id
                and receipt["account_ref"] == account["namespace"] + ":" + account["id"]
                and receipt["verification"] == VERIFICATION, "INVALID_RECEIPT_IDENTITY")
        package_sha = receipt["approved_package_sha256"]
        require(isinstance(package_sha, str) and _SHA.fullmatch(package_sha) is not None,
                "INVALID_RECEIPT_PACKAGE")
        require(publication_id == "pub-" + digest({"idempotency_key": receipt["idempotency_key"],
                                                   "package_sha256": package_sha})[:24],
                "INVALID_RECEIPT_PACKAGE_IDENTITY")
        sha = receipt["receipt_sha256"]
        require(sha is None or (isinstance(sha, str) and _SHA.fullmatch(sha) is not None
                                and sha in receipt["receipt_refs"]), "INVALID_RECEIPT_HASH")
        post = receipt["provider_post_id"]
        require(post is None or (isinstance(post, str) and 0 < len(post) <= 256
                                 and bool(post.strip()) and sha is not None), "INVALID_RECEIPT_POST")
        checked_url(receipt["public_url"], row=1)
        digest(receipt)
    except (AnalyticsError, KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise LedgerError("PUBLICATION_RECORD_INVALID") from exc


def _receipts(path, publication_ids: list[str]) -> tuple[list[dict], list[str]]:
    try:
        journal_path = ledger_path(path, create=False)
    except AnalyticsError as exc:
        if exc.code == "LEDGER_NOT_FOUND":
            raise LedgerError("PUBLICATION_JOURNAL_NOT_FOUND") from exc
        raise
    journal = _ReadOnlyJournal(journal_path)
    receipts, missing = [], []
    for publication_id in publication_ids:
        try:
            # A08 reopens its event JSON and original receipt/visibility BLOBs,
            # verifies their hashes and replays the actual state transitions.
            receipt = journal.get(publication_id)
        except LedgerError:
            raise
        except DistributionError as exc:
            if str(exc) == "PUBLICATION_NOT_FOUND":
                missing.append(publication_id)
                continue
            raise LedgerError("PUBLICATION_EVIDENCE_INVALID") from exc
        except (sqlite3.Error, OSError, KeyError, TypeError, ValueError, UnicodeError,
                RecursionError) as exc:
            raise LedgerError("PUBLICATION_JOURNAL_INVALID") from exc
        _check_receipt(receipt, publication_id)
        receipts.append(receipt)
    return receipts, missing


def _match(observation: dict, receipts: list[dict], missing: list[str]) -> dict:
    scoped = [p for p in receipts if p["platform"] == observation["platform"]
              and p["surface"] == observation["surface"]
              and p["account_identity"] == observation["account_ref"]]
    post, url = observation["provider_post_id"], observation["public_url"]
    exact = [p for p in scoped if post is not None and p["provider_post_id"] == post]
    urls = [p for p in scoped if url is not None and p["public_url"] == url]
    bound = None
    status = "UNMATCHED"
    if post is None and url is None:
        reason = "NO_POST_IDENTIFIER"
    elif not scoped:
        reason = "TARGET_MISMATCH" if receipts else "NO_PUBLICATION_CANDIDATES"
    elif len(exact) > 1:
        status, reason = "PARTIAL", "AMBIGUOUS_PUBLICATION"
    elif exact:
        candidate = exact[0]
        if (url is not None and candidate["public_url"] is not None
                and url != candidate["public_url"]) or any(p != candidate for p in urls):
            status, reason = "PARTIAL", "IDENTIFIER_CONFLICT"
        elif candidate["receipt_sha256"] is None:
            status, reason = "PARTIAL", "RECEIPT_MISSING"
        elif missing:
            status, reason = "PARTIAL", "UNRESOLVED_PUBLICATION_CANDIDATE"
        else:
            status, reason, bound = "MATCHED", "EXACT_POST_ID_AND_TARGET", candidate
    elif urls:
        # An exact, checked URL can identify a candidate only. It cannot replace
        # the required post ID or rescue a contradictory provider post ID.
        status = "PARTIAL"
        reason = "IDENTIFIER_CONFLICT" if post is not None else "URL_ONLY_CANDIDATE"
    elif any(p["receipt_sha256"] is None for p in scoped):
        status, reason = "PARTIAL", "RECEIPT_MISSING"
    else:
        reason = "POST_ID_MISMATCH" if post is not None else "URL_NOT_MATCHED"
    reasons = [reason]
    if missing:
        reasons.append("PUBLICATION_NOT_FOUND")
    return {"observation": observation, "match_status": status, "reason_codes": reasons,
            "publication_id": bound["publication_id"] if bound else None,
            "publication": bound,
            "candidate_publication_ids": [p["publication_id"] for p in scoped]}


def match_import(ledger: AnalyticsLedger, import_id: str, *, publication_db,
                 publication_ids: list[str] | tuple[str, ...]) -> dict:
    """Reopen both sources, then return a deterministic, unpersisted match view.

    Candidate IDs explicitly delimit the search (no implicit journal scan).
    Duplicate IDs are one candidate; distinct receipts for the same post remain
    ambiguous. Unknown requested IDs prevent binding. No time/caption heuristics,
    URL rewriting, namespace aliases or provider attestation are inferred.
    """
    require(isinstance(publication_ids, (list, tuple)) and len(publication_ids) <= MAX_PUBLICATIONS,
            "INVALID_PUBLICATION_CANDIDATES")
    require(all(isinstance(p, str) and _PUBLICATION_ID.fullmatch(p) is not None
                for p in publication_ids), "INVALID_PUBLICATION_ID")
    ids = sorted(set(publication_ids))
    # This verifies raw bytes, context, lineage and every observation by replay;
    # callers cannot pass a fabricated observation or a cached receipt dict.
    imported = ledger.inspect(import_id)
    require(bool(imported["observations"]), "NO_OBSERVATIONS")
    receipts, missing = _receipts(publication_db, ids)
    matches = [_match(o, receipts, missing) for o in imported["observations"]]
    statuses = {m["match_status"] for m in matches}
    status = "MATCHED" if statuses == {"MATCHED"} else (
        "UNMATCHED" if statuses == {"UNMATCHED"} else "PARTIAL")
    result = {"matcher_version": "a09-e02.v1", "import_id": import_id,
              "raw_sha256": imported["import"]["raw_sha256"],
              "source_status": VERIFICATION, "status": status,
              "requested_publication_ids": ids, "missing_publication_ids": missing,
              "publications": [{"receipt": p, "snapshot_sha256": digest(p)} for p in receipts],
              "matches": matches}
    return {**result, "result_sha256": digest(result)}
