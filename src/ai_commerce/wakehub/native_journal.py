"""Private SQLite custody adapter. Importing this module opens no files.

The database must be provisioned separately in private Windows custody. Opening
an absent/empty/replaced database never bootstraps a new replay ledger. The
schema below is also the offline fixture/provisioning specification; there is
deliberately no public reset, migration, expiry, retry or provisioning command.
An administrator must preserve this ledger (including backups) forever. ACLs
cannot defend against a hostile administrator restoring an old disk image.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import ntpath
import os
from pathlib import Path
import re
import sqlite3
from uuid import UUID, uuid4

from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub.native_inventory import ProcessIdentity


OS_TRUSTED_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544",
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"})


class NativeDenied(ValueError):
    """Fixed private boundary error; never wrap OS exception text."""


def deny():
    raise NativeDenied("WAKEHUB_NATIVE_CUSTODY_HOLD") from None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def decode(raw, limit=262144):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                deny()
            result[k] = v
        return result
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= limit:
            deny()
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: deny())
        if type(value) is not dict:
            deny()
        return value
    except (ValueError, UnicodeError, RecursionError):
        deny()


def shape(value, fields):
    if type(value) is not dict or set(value) != set(fields.split()):
        deny()
    return value


def key_data(key):
    return {k: str(v) for k, v in asdict(key).items()}


def key_from(value):
    shape(value, "action_id effect_id one_shot_key")
    return op.EffectKey(**{k: UUID(v) for k, v in value.items()})


def grant_data(grant):
    return dict(operation=grant.operation.value, scope=grant.scope.value,
        source=asdict(grant.source), target=asdict(grant.target), key=key_data(grant.key),
        issued_at=grant.issued_at, expires_at=grant.expires_at,
        expected_process=asdict(grant.expected_process) if grant.expected_process else None,
        reconciles=key_data(grant.reconciles) if grant.reconciles else None)


def grant_from(value):
    shape(value, "operation scope source target key issued_at expires_at expected_process reconciles")
    return op.PrivateGrant(op.Operation(value["operation"]), op.GrantScope(value["scope"]),
        op.SourceBinding(**value["source"]), op.PrivateTarget(**value["target"]),
        key_from(value["key"]), value["issued_at"], value["expires_at"],
        ProcessIdentity(**value["expected_process"]) if value["expected_process"] else None,
        key_from(value["reconciles"]) if value["reconciles"] else None)


def lease_data(lease):
    return dict(grant=grant_data(lease.intent.grant), lease_id=str(lease.lease_id),
                expires_at=lease.expires_at)


def lease_from(value):
    shape(value, "grant lease_id expires_at")
    return op.PrivateLease(op.PrivateIntent(grant_from(value["grant"])),
                           UUID(value["lease_id"]), value["expires_at"])


def observation_data(observation):
    return dict(lease=lease_data(observation.lease), outcome=observation.outcome.value,
                observed_at=observation.observed_at,
                process=asdict(observation.process) if observation.process else None)


def observation_from(value):
    shape(value, "lease outcome observed_at process")
    return op.ObservedEffect(lease_from(value["lease"]), op.ObservedOutcome(value["outcome"]),
        value["observed_at"], ProcessIdentity(**value["process"]) if value["process"] else None)


def lane_for(target):
    # One owner/profile lane, independent of epoch/release, case and session.
    return hashlib.sha256(canonical([target.user_sid,
        ntpath.normcase(target.dedicated_profile)])).hexdigest()


class WindowsPathCustody:
    """Read back owner and every DACL ACE, rejecting reparse/hardlink paths.

    Public anchors/code are writable only by SYSTEM/Administrators. Private
    journal/evidence leaves may also be writable by their separately enrolled
    owner. Inherit-only ACEs do not apply to the object being checked. Unknown
    ACE forms fail closed. No ACL is changed by this adapter.
    """
    def __init__(self, owner_sid=None):
        self.owner_sid = owner_sid

    @staticmethod
    def check_descriptor(owner, aces, allowed, *, ancestor=False, private=False):
        if owner not in allowed or aces is None:
            deny()
        # Ancestors must not allow replacement/deletion/ACL takeover. Adding
        # an unrelated sibling is harmless when every existing child is pinned.
        writes = 0x10000000 | 0x40000000 | 0x00010000 | 0x00040000 | 0x00080000
        writes |= 0x40 if ancestor else 0x2 | 0x4 | 0x10 | 0x100 | 0x40
        for kind, flags, mask, sid in aces:
            if kind not in (0, 1):
                deny()
            if kind == 0 and not flags & 8 and (mask if private else mask & writes) and sid not in allowed:
                deny()

    @staticmethod
    def _descriptor(name, object_type=1):
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        ptr = ctypes.c_void_p
        adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, wintypes.DWORD,
            wintypes.DWORD, ctypes.POINTER(ptr), ptr, ctypes.POINTER(ptr), ptr, ctypes.POINTER(ptr)]
        adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
        adv.ConvertSidToStringSidW.argtypes = [ptr, ctypes.POINTER(wintypes.LPWSTR)]
        adv.ConvertSidToStringSidW.restype = wintypes.BOOL
        adv.GetAclInformation.argtypes = [ptr, ptr, wintypes.DWORD, wintypes.DWORD]
        adv.GetAclInformation.restype = wintypes.BOOL
        adv.GetAce.argtypes = [ptr, wintypes.DWORD, ctypes.POINTER(ptr)]
        adv.GetAce.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ptr]
        kernel.LocalFree.restype = ptr
        owner, dacl, sd = ptr(), ptr(), ptr()
        def sid_text(sid):
            out = wintypes.LPWSTR()
            if not adv.ConvertSidToStringSidW(sid, ctypes.byref(out)):
                deny()
            try:
                return out.value
            finally:
                kernel.LocalFree(ctypes.cast(out, ptr))
        if adv.GetNamedSecurityInfoW(name, object_type, 5, ctypes.byref(owner), None,
                                    ctypes.byref(dacl), None, ctypes.byref(sd)):
            deny()
        try:
            if not owner or not dacl:
                deny()
            info = (wintypes.DWORD * 3)()
            if not adv.GetAclInformation(dacl, info, ctypes.sizeof(info), 2):
                deny()
            aces = []
            for i in range(info[0]):
                ace = ptr()
                if not adv.GetAce(dacl, i, ctypes.byref(ace)):
                    deny()
                header = (ctypes.c_ubyte * 4).from_address(ace.value)
                if header[0] not in (0, 1):
                    deny()
                mask = wintypes.DWORD.from_address(ace.value + 4).value
                aces.append((header[0], header[1], mask, sid_text(ptr(ace.value + 8))))
            return sid_text(owner), aces
        finally:
            kernel.LocalFree(sd)

    def check(self, path, *, directory=False):
        try:
            if os.name != "nt":
                deny()
            path = Path(path)
            if (not path.is_absolute() or str(path) != ntpath.normpath(str(path))
                    or ":" in str(path)[2:] or str(path).startswith("\\\\")
                    or (not path.is_dir() if directory else not path.is_file())):
                deny()
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
            kernel.GetDriveTypeW.restype = wintypes.UINT
            if kernel.GetDriveTypeW(path.anchor) != 3:  # fixed local volume only
                deny()
            allowed = set(OS_TRUSTED_SIDS)
            if self.owner_sid:
                allowed.add(self.owner_sid)
            for index, part in enumerate((path, *path.parents)):
                stat = part.lstat()
                if (part.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400
                        or (part.is_file() and stat.st_nlink != 1)):
                    deny()
                self.check_descriptor(*self._descriptor(str(part)), allowed, ancestor=index > 0,
                                      private=self.owner_sid is not None and index == 0)
            return path
        except Exception:
            deny()


_SCHEMA = """
CREATE TABLE meta(id INTEGER PRIMARY KEY CHECK(id=1), journal_id TEXT NOT NULL,
 lane TEXT NOT NULL, source_epoch INTEGER NOT NULL, release_epoch INTEGER NOT NULL,
 revocation_epoch INTEGER NOT NULL, clock REAL NOT NULL, source_sha TEXT NOT NULL,
 release_sha TEXT NOT NULL, revocation_sha TEXT NOT NULL);
CREATE TABLE intents(seq INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT UNIQUE NOT NULL,
 effect TEXT UNIQUE NOT NULL, shot TEXT UNIQUE NOT NULL, lease BLOB NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('PENDING','UNKNOWN','OBSERVED')), observation BLOB, proof BLOB);
CREATE TABLE consumed(key TEXT PRIMARY KEY, seq INTEGER NOT NULL REFERENCES intents(seq));
"""


class SQLitePrivateJournal:
    """All reads/writes serialize with BEGIN IMMEDIATE; errors never mean EMPTY.

    Reopen preserves PENDING as poison even if its process/lease has disappeared.
    There is exactly one lane in one enrolled database for all releases. Only a
    separately verified signed original-outcome observation may clear it.
    """
    def __init__(self, path, *, owner_sid, journal_id, lane):
        self.path = Path(path)
        self.custody = WindowsPathCustody(owner_sid)
        self.journal_id, self.lane = journal_id, lane
        self._identity = None
        with self._transaction():
            pass

    def _guard(self):
        self.custody.check(self.path.parent, directory=True)
        self.custody.check(self.path)
        for suffix in ("-wal", "-shm", "-journal"):
            side = Path(str(self.path) + suffix)
            if side.exists():
                self.custody.check(side)
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self._identity is not None and identity != self._identity:
            deny()
        self._identity = identity

    @contextmanager
    def _transaction(self):
        db = None
        try:
            self._guard()
            # mode=rw prevents accidental creation after removal or bad paths.
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                 timeout=0.1, isolation_level=None)
            db.execute("PRAGMA foreign_keys=ON")
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                deny()
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA synchronous").fetchone()[0] != 2:
                deny()
            db.execute("BEGIN IMMEDIATE")
            self._guard()
            self._validate(db)
            yield db
            self._validate(db)
            self._guard()
            db.commit()
        except Exception:
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
            deny()
        finally:
            if db is not None:
                db.close()

    def _validate(self, db):
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            deny()
        if db.execute("PRAGMA foreign_key_check").fetchall():
            deny()
        # A replaced schema/trigger must never silently discard replay keys.
        expected = [s.strip() for s in _SCHEMA.split(";") if s.strip()]
        actual = db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall()
        if {n for n, _ in actual} != {"meta", "intents", "consumed", "sqlite_sequence"}:
            deny()
        normalize = lambda s: " ".join(s.split()).casefold()
        if ({normalize(s) for n, s in actual if n != "sqlite_sequence"}
                != {normalize(s) for s in expected}
                or db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()):
            deny()
        meta = db.execute("SELECT * FROM meta").fetchall()
        if (len(meta) != 1 or meta[0][:3] != (1, self.journal_id, self.lane)
                or any(type(v) is not int or v < 0 for v in meta[0][3:6])
                or not math.isfinite(meta[0][6]) or meta[0][6] < 0
                or any(type(v) is not str or not re.fullmatch(r"[0-9a-f]{40}", v) for v in meta[0][7:9])
                or type(meta[0][9]) is not str or not re.fullmatch(r"[0-9a-f]{64}", meta[0][9])):
            deny()
        pending = 0
        for seq, action, effect, shot, raw, state, observed, proof in db.execute("SELECT * FROM intents"):
            lease = lease_from(decode(raw))
            grant = lease.intent.grant
            keys = tuple(key_data(grant.key).values())
            if (keys != (action, effect, shot) or len(set(keys)) != 3
                    or lane_for(grant.target) != self.lane
                    or set(db.execute("SELECT key FROM consumed WHERE seq=?", (seq,))) != {(v,) for v in keys}):
                deny()
            observation = observation_from(decode(observed)) if observed is not None else None
            op.JournalRecord(op.JournalState(state), lease, observation)
            if (state == "OBSERVED") != (proof is not None):
                deny()
            if proof is not None:
                shape(decode(proof), "body signature")
            pending += state != "OBSERVED"
        if pending > 1:
            deny()

    def checkpoint(self, source_epoch, release_epoch, revocation_epoch, now, *,
                   source_sha, release_sha, revocation_sha):
        if (any(type(v) is not int or v < 1 for v in
                (source_epoch, release_epoch, revocation_epoch))
                or type(now) not in (int, float) or not math.isfinite(now)
                or any(type(v) is not str or not re.fullmatch(r"[0-9a-f]{40}", v)
                       for v in (source_sha, release_sha))
                or type(revocation_sha) is not str or not re.fullmatch(r"[0-9a-f]{64}", revocation_sha)):
            deny()
        with self._transaction() as db:
            old = db.execute("SELECT source_epoch,release_epoch,revocation_epoch,clock,source_sha,release_sha,revocation_sha FROM meta").fetchone()
            if any(new < before for new, before in zip(
                    (source_epoch, release_epoch, revocation_epoch, now), old)):
                deny()
            # A different signed release or revocation set cannot reuse an
            # already admitted epoch, even after a fresh process/reboot.
            if any(epoch == before and pin != old_pin for epoch, before, pin, old_pin in zip(
                    (source_epoch, release_epoch, revocation_epoch), old[:3],
                    (source_sha, release_sha, revocation_sha), old[4:])):
                deny()
            db.execute("UPDATE meta SET source_epoch=?,release_epoch=?,revocation_epoch=?,clock=?,source_sha=?,release_sha=?,revocation_sha=? WHERE id=1",
                       (source_epoch, release_epoch, revocation_epoch, now, source_sha, release_sha, revocation_sha))

    def _record(self, db, grant):
        if lane_for(grant.target) != self.lane:
            deny()
        row = db.execute("SELECT lease,state,observation FROM intents WHERE state!='OBSERVED' ORDER BY seq LIMIT 1").fetchone()
        if row is None:
            keys = key_data(grant.reconciles or grant.key).values()
            row = db.execute("SELECT lease,state,observation FROM intents WHERE seq IN "
                "(SELECT seq FROM consumed WHERE key IN (?,?,?)) ORDER BY seq LIMIT 1", tuple(keys)).fetchone()
        if row is None:
            return op.JournalRecord(op.JournalState.EMPTY)
        return op.JournalRecord(op.JournalState(row[1]), lease_from(decode(row[0])),
            observation_from(decode(row[2])) if row[2] is not None else None)

    def read(self, grant):
        with self._transaction() as db:
            return self._record(db, grant)

    def observed_records(self):
        # Authority reauthenticates durable proofs after reopen, before it
        # admits EMPTY for a fresh key. A bare persisted dataclass is not proof.
        with self._transaction() as db:
            return [(op.JournalRecord(op.JournalState.OBSERVED, lease_from(decode(lease)),
                         observation_from(decode(observed))), proof)
                    for lease, observed, proof in db.execute(
                        "SELECT lease,observation,proof FROM intents WHERE state='OBSERVED'")]

    def outcome_proof(self, record):
        with self._transaction() as db:
            row = db.execute("SELECT proof FROM intents WHERE action=? AND state='OBSERVED'",
                             (str(record.lease.intent.grant.key.action_id),)).fetchone()
            if row is None or row[0] is None:
                deny()
            return row[0]

    def reserve(self, intent, now):
        grant = intent.grant
        if not grant.issued_at <= now < grant.expires_at or grant.expires_at - grant.issued_at >= 30:
            deny()
        with self._transaction() as db:
            meta = db.execute("SELECT source_epoch,release_epoch,clock,source_sha,release_sha FROM meta").fetchone()
            if (grant.source.source_epoch != meta[0] or grant.source.release_epoch != meta[1]
                    or now < meta[2] or lane_for(grant.target) != self.lane
                    or (grant.source.source_sha, grant.source.release_sha) != meta[3:]):
                deny()
            keys = tuple(key_data(grant.key).values())
            if len(set(keys)) != 3:
                deny()
            if (db.execute("SELECT 1 FROM intents WHERE state!='OBSERVED'").fetchone()
                    or db.execute("SELECT 1 FROM consumed WHERE key IN (?,?,?)", keys).fetchone()):
                return None
            lease = op.PrivateLease(intent, uuid4(), grant.expires_at)
            cursor = db.execute("INSERT INTO intents(action,effect,shot,lease,state) VALUES(?,?,?,?,'PENDING')",
                                (*keys, canonical(lease_data(lease))))
            db.executemany("INSERT INTO consumed VALUES(?,?)", [(k, cursor.lastrowid) for k in keys])
            db.execute("UPDATE meta SET clock=? WHERE id=1", (now,))
            return lease

    def mark_unknown(self, intent):
        with self._transaction() as db:
            record = self._record(db, intent.grant)
            if record.lease is None or record.lease.intent != intent:
                deny()
            db.execute("UPDATE intents SET state='UNKNOWN' WHERE action=? AND state='PENDING'",
                       (str(intent.grant.key.action_id),))

    def complete(self, lease, observation):
        # A Python ObservedEffect/ACK is not independent proof. Only the
        # authority's reconciliation path can supply a signed durable outcome.
        return False

    def resolve(self, record, observation):
        # Public protocol entry cannot bless a caller-constructed observation.
        return False

    def resolve_verified(self, record, observation, signed_outcome, authority):
        from ai_commerce.wakehub.native_authority import WindowsGrantVerifier
        if type(authority) is not WindowsGrantVerifier:
            deny()
        if authority.verify_original_outcome(record, signed_outcome) != observation:
            deny()
        with self._transaction() as db:
            current = self._record(db, record.lease.intent.grant)
            if (current != record or observation.lease != record.lease
                    or current.state not in (op.JournalState.PENDING, op.JournalState.UNKNOWN,
                                             op.JournalState.OBSERVED)):
                deny()
            if current.observation is not None:
                return current.observation == observation
            db.execute("UPDATE intents SET state='OBSERVED',observation=?,proof=? WHERE action=?",
                (canonical(observation_data(observation)), signed_outcome,
                 str(record.lease.intent.grant.key.action_id)))
            return True
