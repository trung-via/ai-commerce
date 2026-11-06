"""Windows G2 verification, separate from D5/HostOps enrollment and keys.

Installation is a separate private administrative ceremony, NOT performed here:
HKLM\\SOFTWARE\\AI-Commerce\\WakeHubG2 contains one REG_SZ `Anchor` with the
closed fields checked by _load_anchor. This key, the public signer policies,
verifier binary and installed release must be protected against the operator
and EVERY public runner. Issuer, Human and observer Ed25519 keys must differ.
No key generation, unsigned enable switch or Issue/comment enrollment exists.

The independently signed enrollment pins release bytes, task parent, source /
release epochs, separate operator/runner SIDs and one permanent journal ID/path.
Private observer records and revocations are independently signed, short lived,
and read again on every boundary. These are source adapters, not an installed
observer, signer, trusted host or claim of successful local enrollment.
"""
from __future__ import annotations

from dataclasses import asdict
import base64
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import re
import sys
import time

from ai_commerce.hostops_v3.auth import verify_signature
from ai_commerce.hostops_v3.contract import canonical as ssh_canonical
from ai_commerce.remote.chrome_acquisition import _interactive_session
from ai_commerce.remote.local_wake_entry import _windows_process_sid
from ai_commerce.wakehub import native_inventory as inv
from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub.native_journal import (
    WindowsPathCustody, OS_TRUSTED_SIDS, canonical, decode, deny, shape, grant_data, grant_from,
    key_data, lease_data, observation_from, lane_for,
)


TASK_PARENT = "c51b3bc4e717d11b4eadc89eba2791876d645419"
ANCHOR_KEY = r"SOFTWARE\AI-Commerce\WakeHubG2"
NAMESPACES = {role: "wakehub-g2-9444-" + role + "@ai-commerce" for role in
              ("enrollment", "grant", "consent", "revocations", "inventory", "outcome")}
IDENTITIES = {"issuer": "wakehub-g2-issuer", "human": "wakehub-g2-human",
              "observer": "wakehub-g2-observer"}


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def windows_only():
    # Deny all CI conventions, including arbitrary nonempty CI/Actions values.
    if os.name != "nt" or any(os.environ.get(k) for k in
            ("CI", "GITHUB_ACTIONS", "GITHUB_RUN_ID", "TF_BUILD", "BUILD_BUILDID")):
        deny()


def _fresh(value, now):
    issued, expires = value["issued_at"], value["expires_at"]
    if (any(type(v) not in (int, float) for v in (issued, expires, now))
            or not 0 < expires - issued < 30 or not 0 < issued <= now < expires < 2**53):
        deny()


def signing_payload(body):
    # The reviewed HostOps serializer only permits integer numbers. Bind our
    # exact canonical bytes (which include subsecond Windows creation times)
    # in a closed ASCII transcript. No lossy float conversion is permitted.
    return {"schema": "WakeHubG2SignatureTranscript.v1",
            "payload_b64": base64.b64encode(canonical(body)).decode("ascii")}


def signing_bytes(body):
    """Exact offline signing specification, including OpenSSH input newline."""
    return ssh_canonical(signing_payload(body))


def _signed(raw, role, purpose, anchor):
    value = decode(raw)
    shape(value, "body signature")
    if type(value["body"]) is not dict or type(value["signature"]) is not str:
        deny()
    # Reuse only the reviewed low-level OpenSSH crypto helper. No D5 policy,
    # identity, MAC, namespace, secret, 9333 endpoint or task bridge is used.
    signers = anchor[role + "_signers"]
    exe = anchor["verifier"]
    verify_signature(signing_payload(value["body"]), value["signature"], Path(signers["path"]),
                     IDENTITIES[role], NAMESPACES[purpose], Path(exe["path"]))
    return value["body"]


def _load_anchor():
    windows_only()
    import winreg
    # KEY_WOW64_64KEY avoids a per-process registry redirection trust alias.
    admin = OS_TRUSTED_SIDS
    for suffix in (r"SOFTWARE", r"SOFTWARE\AI-Commerce", ANCHOR_KEY):
        WindowsPathCustody.check_descriptor(*WindowsPathCustody._descriptor(
            "MACHINE\\" + suffix, 4), admin)
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ANCHOR_KEY, 0,
                       winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        raw, kind = winreg.QueryValueEx(key, "Anchor")
    if kind != winreg.REG_SZ or type(raw) is not str:
        deny()
    return _validate_anchor(decode(raw.encode("utf-8"), 32768))


def _validate_anchor(anchor):
    shape(anchor, "schema issuer_signers human_signers observer_signers verifier enrollment operator_sid runner_sids journal_path journal_id lane")
    if (anchor["schema"] != "WakeHubG2Anchor.v1"
            or type(anchor["operator_sid"]) is not str
            or not re.fullmatch(r"S-1-5-21-(?:[0-9]+-){3}[0-9]+", anchor["operator_sid"])
            or type(anchor["runner_sids"]) is not list or not anchor["runner_sids"]
            or any(type(s) is not str or not re.fullmatch(r"S-1-(?:[0-9]+-)+[0-9]+", s)
                   for s in anchor["runner_sids"])
            or anchor["operator_sid"] in anchor["runner_sids"]):
        deny()
    custody = WindowsPathCustody()
    keys = []
    for role in ("issuer", "human", "observer"):
        pinned = anchor[role + "_signers"]
        shape(pinned, "path sha256")
        raw = custody.check(pinned["path"]).read_bytes()
        if len(raw) > 8192 or _digest(raw) != pinned["sha256"]:
            deny()
        lines = [l.split() for l in raw.decode("ascii").splitlines() if l.strip()]
        # Exactly one key, no wildcard principal, options, cert or extra role.
        if (len(lines) != 1 or len(lines[0]) != 3
                or lines[0][:2] != [IDENTITIES[role], "ssh-ed25519"]):
            deny()
        keys.append(lines[0][2])
    if len(set(keys)) != 3:
        deny()
    shape(anchor["verifier"], "path sha256")
    exe = custody.check(anchor["verifier"]["path"])
    if exe.name.casefold() != "ssh-keygen.exe" or _digest(exe.read_bytes()) != anchor["verifier"]["sha256"]:
        deny()
    custody.check(anchor["enrollment"])
    return anchor


def _desktop_identity(target):
    windows_only()
    if _windows_process_sid() != target.user_sid or _interactive_session() != target.session_id:
        deny()
    # A usable input desktop in a disconnected RDP session is insufficient.
    wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
    buffer = ctypes.c_void_p()
    length = wintypes.DWORD()
    wts.WTSQuerySessionInformationW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
        ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    wts.WTSQuerySessionInformationW.restype = wintypes.BOOL
    wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    if not wts.WTSQuerySessionInformationW(None, target.session_id, 8,
                                          ctypes.byref(buffer), ctypes.byref(length)):
        deny()
    try:
        if length.value != ctypes.sizeof(wintypes.DWORD) or not buffer or ctypes.cast(
                buffer, ctypes.POINTER(wintypes.DWORD)).contents.value != 0:
            deny()
    finally:
        wts.WTSFreeMemory(buffer)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.GetUserNameW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    adv.GetUserNameW.restype = wintypes.BOOL
    # GetUserNameEx gives the same DOMAIN\user syntax used by WindowsIdentity.
    secur = ctypes.WinDLL("secur32", use_last_error=True)
    secur.GetUserNameExW.argtypes = [ctypes.c_int, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    secur.GetUserNameExW.restype = wintypes.BOOL
    size = wintypes.DWORD(512)
    name = ctypes.create_unicode_buffer(size.value)
    if not secur.GetUserNameExW(2, name, ctypes.byref(size)) or name.value != target.user_name:
        deny()
    user.GetProcessWindowStation.restype = wintypes.HANDLE
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    user.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user.GetThreadDesktop.restype = wintypes.HANDLE
    user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user.OpenInputDesktop.restype = wintypes.HANDLE
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    user.GetUserObjectInformationW.restype = wintypes.BOOL
    def object_name(handle):
        out = ctypes.create_unicode_buffer(256)
        required = wintypes.DWORD()
        if not handle or not user.GetUserObjectInformationW(handle, 2, out,
                ctypes.sizeof(out), ctypes.byref(required)):
            deny()
        return out.value
    desktop = user.OpenInputDesktop(0, False, 1)
    if not desktop:
        deny()
    try:
        if (object_name(user.GetProcessWindowStation()) != "WinSta0"
                or object_name(user.GetThreadDesktop(kernel.GetCurrentThreadId())) != "Default"
                or object_name(desktop) != "Default" or target.desktop != r"WinSta0\Default"):
            deny()
    finally:
        user.CloseDesktop(desktop)


def _verify_release(value):
    root = WindowsPathCustody().check(value["release_root"], directory=True)
    files = value["files"]
    if type(files) is not dict or not 5 <= len(files) <= 512:
        deny()
    for relative, expected in files.items():
        if (type(relative) is not str or not re.fullmatch(r"[A-Za-z0-9_./-]+", relative)
                or any(p in ("", ".", "..") for p in relative.split("/"))
                or type(expected) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected)):
            deny()
        path = WindowsPathCustody().check(root / relative)
        if _digest(path.read_bytes()) != expected:
            deny()
    # Bind actual executing modules, including reused auth/helper consumers.
    # The old SourceBinding parent remains a protocol compatibility field;
    # task_parent independently pins THIS task's exact authorized main.
    for name, module in tuple(sys.modules.items()):
        if name == "ai_commerce" or name.startswith("ai_commerce."):
            path = getattr(module, "__file__", None)
            if path:
                path = WindowsPathCustody().check(path)
                relative = path.relative_to(root).as_posix()
                if files.get(relative) != _digest(path.read_bytes()):
                    deny()


class WindowsGrantVerifier:
    """Concrete PrivateGrantVerifier; no injected production trust callbacks.

    Construction and every checkpoint reload admin-protected anchor, signed
    enrollment/release and fresh revocations. Offline tests replace OS/crypto
    boundaries explicitly; their success is never Windows native-effect proof.
    """
    def __init__(self, *, clock=time.time):
        self.clock = clock
        self.journal = None
        self.backend = inv.WindowsReadBackend(self)
        self._proofs = {}
        self._active = None
        self._anchor_digest = None
        self._journal_binding = None
        self._load(self.clock())

    def _load(self, now):
        anchor = _load_anchor()
        digest = _digest(canonical(anchor))
        if self._anchor_digest is not None and digest != self._anchor_digest:
            deny()  # Changing trust anchors requires a new explicit enrollment.
        self._anchor_digest = digest
        value = _signed(WindowsPathCustody().check(anchor["enrollment"]).read_bytes(),
                        "issuer", "enrollment", anchor)
        shape(value, "schema task_parent source target release_root files journal_path journal_id lane observer_root observer_sid issued_at expires_at enabled")
        if (value["schema"] != "WakeHubG2Enrollment.v1" or value["task_parent"] != TASK_PARENT
                or value["enabled"] is not True or not 0 < value["issued_at"] <= now < value["expires_at"]):
            deny()
        source, target = op.SourceBinding(**value["source"]), op.PrivateTarget(**value["target"])
        if (target.user_sid != anchor["operator_sid"] or target.user_sid in anchor["runner_sids"]
                or value["observer_sid"] in (target.user_sid, *anchor["runner_sids"])
                or not re.fullmatch(r"S-1-(?:[0-9]+-)+[0-9]+", value["observer_sid"])
                or value["lane"] != lane_for(target)
                or not re.fullmatch(r"[0-9a-f]{32}", value["journal_id"])):
            deny()
        _desktop_identity(target)
        _verify_release(value)
        state = WindowsPathCustody(target.user_sid)
        state.check(value["journal_path"])
        observer = WindowsPathCustody(value["observer_sid"])
        observer_root = observer.check(value["observer_root"], directory=True)
        # Presence is a veto, regardless of freshness or any signed enable flag.
        if any((root / "KILL").exists() for root in
               (Path(anchor["enrollment"]).parent, observer_root, Path(value["journal_path"]).parent)):
            deny()
        rev = _signed(observer.check(observer_root / "revocations.json").read_bytes(),
                      "issuer", "revocations", anchor)
        shape(rev, "schema journal_id epoch source_epoch release_epoch issued_at expires_at revoked")
        _fresh(rev, now)
        if (rev["schema"] != "WakeHubG2Revocations.v1" or rev["journal_id"] != value["journal_id"]
                or any(type(rev[k]) is not int or rev[k] < 1 for k in
                       ("epoch", "source_epoch", "release_epoch"))
                or rev["source_epoch"] != source.source_epoch or rev["release_epoch"] != source.release_epoch
                or type(rev["revoked"]) is not list or len(rev["revoked"]) > 4096
                or any(type(k) is not str or not re.fullmatch(r"[0-9a-f-]{36}", k) for k in rev["revoked"])):
            deny()
        binding = (value["journal_path"], value["journal_id"], value["lane"])
        # A release signer cannot move the global lane to a fresh ledger.
        # These three values are permanent ADMIN anchor custody, independent
        # of signed release/enrollment changes and process restarts.
        if binding != (anchor["journal_path"], anchor["journal_id"], anchor["lane"]):
            deny()
        if self._journal_binding is not None and binding != self._journal_binding:
            deny()
        self._journal_binding = binding
        self.anchor, self.enrolled, self.source, self.target, self.revocations = anchor, value, source, target, rev
        if self.journal is not None:
            if (str(self.journal.path), self.journal.journal_id, self.journal.lane) != binding:
                deny()
            self.journal.checkpoint(source.source_epoch, source.release_epoch, rev["epoch"], now,
                source_sha=source.source_sha, release_sha=source.release_sha,
                revocation_sha=_digest(canonical(rev["revoked"])))
            for record, raw in self.journal.observed_records():
                if self._original_outcome(record, raw, now) != record.observation:
                    deny()
        return value

    def _check(self, grant, now):
        self._load(now)
        if (self.journal is None or grant.source != self.source or grant.target != self.target
                or not grant.issued_at <= now < grant.expires_at
                or grant.expires_at - grant.issued_at >= 30
                or any(v in self.revocations["revoked"] for v in key_data(grant.key).values())):
            deny()
        return True

    def verify_grant(self, proof, operation, policy, now):
        try:
            if type(proof) is not op.PrivateGrantProof or type(operation) is not op.Operation:
                deny()
            self._load(now)
            body = _signed(proof.signature, "issuer", "grant", self.anchor)
            shape(body, "schema task_parent journal_id grant")
            if (body["schema"] != "WakeHubG2Grant.v1" or body["task_parent"] != TASK_PARENT
                    or body["journal_id"] != self.enrolled["journal_id"]):
                deny()
            grant = grant_from(body["grant"])
            if grant.operation is not operation or grant.source != policy.source:
                deny()
            self._check(grant, now)
            consent = _signed(proof.consent, "human", "consent", self.anchor)
            shape(consent, "schema task_parent journal_id grant_sha256 issued_at expires_at pause receiver")
            _fresh(consent, now)
            if (consent["schema"] != "WakeHubG2Consent.v1" or consent["task_parent"] != TASK_PARENT
                    or consent["journal_id"] != self.enrolled["journal_id"]
                    or consent["grant_sha256"] != _digest(canonical(body))
                    or consent["issued_at"] < grant.issued_at or consent["expires_at"] > grant.expires_at
                    or consent["pause"] not in ("PAUSED", "RELEASED_INDEPENDENTLY")
                    or consent["receiver"] not in ("ONLINE", "OFFLINE", "MUTED", "UNPROVEN")
                    or len(set(key_data(grant.key).values())) != 3):
                deny()
            if operation in (op.Operation.REUSE, op.Operation.START_IF_INDEPENDENTLY_VERIFIED_ABSENT):
                if consent["pause"] != "RELEASED_INDEPENDENTLY" or consent["receiver"] != "ONLINE":
                    deny()
            self._proofs[_digest(canonical(grant_data(grant)))] = (proof, consent)
            self._active = grant
            return grant
        except Exception:
            return None

    def _authenticated(self, grant, now):
        key = _digest(canonical(grant_data(grant)))
        saved = self._proofs.get(key)
        if saved is None or self.verify_grant(saved[0], grant.operation,
                                             op.LifecyclePolicy(grant.source), now) != grant:
            deny()
        return saved[1]

    def authorize_read(self):
        grant = self._active
        if grant is None:
            return None
        now = self.clock()
        consent = self._authenticated(grant, now)
        record = self.journal.read(grant)
        return inv.PrivateEnrollment(inv.SOURCE_EPOCH, grant.target.user_name,
            grant.target.user_sid, grant.target.session_id, grant.target.chrome_executable,
            grant.target.dedicated_profile, consent["issued_at"], consent["expires_at"], True,
            consent["receiver"] == "MUTED", True, consent["pause"] != "RELEASED_INDEPENDENTLY",
            record.state in (op.JournalState.PENDING, op.JournalState.UNKNOWN))

    def _observer(self, filename, purpose):
        root = Path(self.enrolled["observer_root"])
        custody = WindowsPathCustody(self.enrolled["observer_sid"])
        return _signed(custody.check(root / filename).read_bytes(), "observer", purpose, self.anchor)

    def verify_read(self, snapshot, enrollment):
        now = self.clock()
        grant = self._active
        consent = self._authenticated(grant, now)
        body = self._observer("inventory.json", "inventory")
        shape(body, "schema journal_id grant_sha256 snapshot enrollment desktop pause receiver verified_at")
        expected = _digest(canonical(grant_data(grant)))
        # Complete independent observer content must match the local READ,
        # not a caller's assertion or an echoed unsigned digest. A <=2s
        # observation gap permits the two independent reads to finish.
        observed = body["snapshot"]
        local = asdict(snapshot)
        if (body["schema"] != "WakeHubG2Inventory.v1" or body["journal_id"] != self.enrolled["journal_id"]
                or body["grant_sha256"] != expected or type(observed) is not dict
                or not grant.issued_at <= observed.get("observed_at", 0) <= snapshot.observed_at <= now
                or now - observed["observed_at"] >= 2
                or {k: v for k, v in observed.items() if k != "observed_at"} !=
                   decode(canonical({k: v for k, v in local.items() if k != "observed_at"}))
                or body["enrollment"] != asdict(enrollment)
                or body["desktop"] != grant.target.desktop
                or body["pause"] != consent["pause"] or body["receiver"] != consent["receiver"]
                or not observed["observed_at"] <= body["verified_at"] <= now):
            deny()
        _desktop_identity(grant.target)
        return inv.PrivateAttestation(inv._private_digest(snapshot), inv._private_digest(enrollment), now)

    def read_verified(self, grant, now):
        self._authenticated(grant, now)
        self._active = grant
        enrollment = self.authorize_read()
        snapshot = self.backend.read_fixed_inventory()
        attestation = self.verify_read(snapshot, enrollment)
        public = inv.normalize_private_inventory(snapshot, enrollment, attestation, now=self.clock())
        if public.inventory_status not in ("VERIFIED_READ", "HOLD_RECONCILE"):
            deny()
        consent = self._authenticated(grant, self.clock())
        return op.PrivateEvidence(grant.source, snapshot, enrollment, attestation, public.supervisor,
            op.DesktopState.LIVE_UNLOCKED_OWNER, op.ReceiverState(consent["receiver"]), op.PauseState(consent["pause"]))

    def verify_journal(self, grant, record, now):
        self._authenticated(grant, now)
        return self.journal.read(grant) == record

    def verify_lease(self, lease, now):
        self._authenticated(lease.intent.grant, now)
        record = self.journal.read(lease.intent.grant)
        return (record.state is op.JournalState.PENDING and record.lease == lease and now < lease.expires_at)

    def verify_original_outcome(self, record, raw):
        now = self.clock()
        self._load(now)
        return self._original_outcome(record, raw, now)

    def _original_outcome(self, record, raw, now):
        body = _signed(raw, "observer", "outcome", self.anchor)
        shape(body, "schema journal_id lease original_grant_sha256 observation basis")
        if (record.lease is None or body["schema"] != "WakeHubG2OriginalOutcome.v1"
                or body["journal_id"] != self.enrolled["journal_id"]
                or body["lease"] != lease_data(record.lease)
                or body["original_grant_sha256"] != _digest(canonical(grant_data(record.lease.intent.grant)))
                or body["basis"] != "INDEPENDENT_DURABLE_ORIGINAL_OPERATION"):
            deny()
        observation = observation_from(body["observation"])
        if observation.lease != record.lease or observation.observed_at > now:
            deny()
        return observation

    def reconcile(self, grant, record, now):
        try:
            self._authenticated(grant, now)
            if (grant.operation is not op.Operation.RECONCILE_ONLY or record.lease is None
                    or record.lease.intent.grant.key != grant.reconciles
                    or lane_for(record.lease.intent.grant.target) != lane_for(grant.target)):
                deny()
            raw = (self.journal.outcome_proof(record) if record.state is op.JournalState.OBSERVED else
                   WindowsPathCustody(self.enrolled["observer_sid"]).check(
                       Path(self.enrolled["observer_root"]) / "outcome.json").read_bytes())
            observation = self.verify_original_outcome(record, raw)
            self._authenticated(grant, self.clock())
            if not self.journal.resolve_verified(record, observation, raw, self):
                deny()
            return observation
        except Exception:
            return None
