"""Operator-private, transactional D5 turn ledger. No browser or Send.

The caller must pre-provision a secure out-of-repo directory. Every update uses
the existing D5 path/OS lock, owner/ACL check and durable atomic JSON replace.
It records intent, never authorizes or performs a ChatGPT message submission.
"""
from __future__ import annotations

from pathlib import Path

from ._private_state import PrivateJsonState
from .local_completion_handoff import _private_identity
from .plugin_turn_handoff import (
    TurnHandoffError,
    new_session,
    record_send_intent,
    reconcile_ui_observation,
    validate_session,
)


class TurnSessionStore:
    """One session per file; no implicit creation, recoveries or write retries."""

    def __init__(self, *, path: Path, repository_root: Path):
        self.state = PrivateJsonState(
            Path(path), Path(repository_root), label="PLUGIN_TURN", max_bytes=16384)
        if self.state.path.name.endswith((".lock", ".part")):
            raise TurnHandoffError("TURN_STORE_LOCATION_INVALID")
        self._owner()

    def _owner(self) -> str:
        # Reuse the already-reviewed native Windows SID+DACL or POSIX 0700 guard.
        try:
            principal = _private_identity(self.state.path.parent)
            for path in (self.state.path,
                         self.state.path.with_name(self.state.path.name + ".lock")):
                if (path.exists() or path.is_symlink()) and _private_identity(path) != principal:
                    raise TurnHandoffError("TURN_STORE_OWNER_MISMATCH")
            return principal
        except TurnHandoffError:
            raise
        except Exception:
            raise TurnHandoffError("TURN_STORE_PERMISSIONS_INVALID") from None

    def _operate(self, transform):
        principal = self._owner()
        with self.state.locked(
                new_file_owner_sid=principal if __import__("os").name == "nt" else None) as txn:
            if self._owner() != principal:
                raise TurnHandoffError("TURN_STORE_OWNER_MISMATCH")
            existing = txn.read({})
            if existing == {} and self.state.path.exists():
                raise TurnHandoffError("TURN_SESSION_INVALID")
            current = None if existing == {} else validate_session(existing)
            updated = transform(current)
            checked = validate_session(updated)
            txn.write(checked)
            if self._owner() != principal:
                raise TurnHandoffError("TURN_STORE_OWNER_MISMATCH")
            return checked

    def create(self, *, handoff_id: str, issue_number: int, source_sha: str) -> dict:
        def apply(existing):
            if existing is not None:
                raise TurnHandoffError("TURN_STORE_ALREADY_EXISTS")
            return new_session(
                handoff_id=handoff_id, issue_number=issue_number,
                source_sha=source_sha)
        return self._operate(apply)

    def read(self) -> dict:
        principal = self._owner()
        with self.state.locked(
                new_file_owner_sid=principal if __import__("os").name == "nt" else None) as txn:
            if self._owner() != principal:
                raise TurnHandoffError("TURN_STORE_OWNER_MISMATCH")
            raw = txn.read({})
            if raw == {}:
                raise TurnHandoffError("TURN_SESSION_NOT_FOUND")
            return validate_session(raw)

    def record_intent(self, *, operator_approved: bool = False) -> dict:
        """Persist UNKNOWN before any external caller might Send."""
        def apply(current):
            if current is None:
                raise TurnHandoffError("TURN_SESSION_NOT_FOUND")
            return record_send_intent(current, operator_approved=operator_approved)
        return self._operate(apply)

    def observe_reply(self, *, issue_comment_ref: str,
                      operator_verified: bool = False) -> dict:
        """Persist Human UI observation; never claim host plugin/server ACK."""
        def apply(current):
            if current is None:
                raise TurnHandoffError("TURN_SESSION_NOT_FOUND")
            return reconcile_ui_observation(
                current, issue_comment_ref=issue_comment_ref,
                operator_verified=operator_verified)
        return self._operate(apply)
