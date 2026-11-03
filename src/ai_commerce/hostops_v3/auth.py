"""Domain-separated OpenSSH signatures; no keys or raw verifier output in receipts."""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time

from .contract import (OPS, MAX_FILE, SOURCE_REPOSITORY, CONTROL_REPOSITORY,
                       canonical, decode, deny, digest, ident, integer, sha, shape, text)


def verify_signature(body, signature, signers: Path, identity: str, namespace: str, executable: Path):
    try:
        signature = base64.b64decode(signature, validate=True)
        if not 20 <= len(signature) <= 12288:
            deny("SIGNATURE_INVALID")
        # Temporary files contain only PUBLIC signature data, never private content.
        with tempfile.TemporaryDirectory(prefix="hostops-v3-signature-") as folder:
            sig = Path(folder) / "approval.sig"
            sig.write_bytes(signature)
            p = subprocess.run([str(executable), "-Y", "verify", "-f", str(signers), "-I", identity,
                                "-n", namespace, "-s", str(sig)], input=canonical(body),
                               capture_output=True, timeout=5, check=False,
                               env=child_environment())
        if p.returncode:
            deny("SIGNATURE_INVALID")
    except (ValueError, OSError, subprocess.TimeoutExpired):
        deny("SIGNATURE_UNAVAILABLE")


# W1 is a source-only, signed, closed contract. It cannot execute a host write,
# retrieve files, persist a token, or start any background service. In
# particular, a same-Windows-SID public runner is NOT a credential boundary.
PERSONAL_W1_SCHEMA = "hostops.personal.signed_create_proposal.v1"
PERSONAL_W1_NAMESPACE = "hostops-w1-create@ai-commerce"
PERSONAL_W1_ROOT = "human-scratch-v1"
_PERSONAL_W1_ID = re.compile(r"w1-[0-9a-f]{32}\Z")
_PERSONAL_W1_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,55}\.(?:txt|md|json)\Z")
_PERSONAL_W1_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"} |
                                   {"COM" + str(i) for i in range(1, 10)} |
                                   {"LPT" + str(i) for i in range(1, 10)})


def personal_w1_signing_payload(proposal, *, issue_number, expected_source_sha,
                                now=None):
    """Validate a canonical, exact-effect proposal before crypto or OS effects.

    All fields, including the GitHub Issue number, target basename, content
    bytes and content SHA, are covered by the independent Human signature.
    The root is an allowlisted *identifier*, never an Issue-selected host path.
    """
    shape(proposal, "schema request_id issue_number source_repository control_repository source_sha operation parameters issued_at expires_at approval_signature_b64")
    if (proposal["schema"] != PERSONAL_W1_SCHEMA or
            proposal["source_repository"] != SOURCE_REPOSITORY or
            proposal["control_repository"] != CONTROL_REPOSITORY or
            proposal["operation"] != "create_new_utf8"):
        deny("W1_CONTRACT_DENIED")
    if (type(issue_number) is not int or not 1 <= issue_number <= 2147483647 or
            type(proposal["issue_number"]) is not int or
            proposal["issue_number"] != issue_number):
        deny("W1_ISSUE_MISMATCH")
    if (type(proposal["request_id"]) is not str or
            _PERSONAL_W1_ID.fullmatch(proposal["request_id"]) is None):
        deny("W1_REQUEST_ID_DENIED")
    sha(expected_source_sha, 40)
    if proposal["source_sha"] != expected_source_sha:
        deny("W1_SOURCE_MISMATCH")
    issued, expires = proposal["issued_at"], proposal["expires_at"]
    if now is None:
        now = int(time.time())
    if (type(issued) is not int or type(expires) is not int or
            type(now) is not int or not 1 <= expires - issued <= 600 or
            now < issued or now > expires or now - issued > 600):
        deny("W1_REQUEST_EXPIRED")
    p = proposal["parameters"]
    shape(p, "root_id filename content_utf8 content_sha256 content_bytes")
    if p["root_id"] != PERSONAL_W1_ROOT:
        deny("W1_ROOT_DENIED")
    name = p["filename"]
    if (type(name) is not str or _PERSONAL_W1_FILENAME.fullmatch(name) is None or
            name.rsplit(".", 1)[0].upper() in _PERSONAL_W1_RESERVED):
        # The ASCII-only flat basename rejects traversal, junction paths, ADS,
        # UNC, drive letters, Win32 device aliases, slashes and wildcards.
        deny("W1_TARGET_DENIED")
    content = p["content_utf8"]
    if type(content) is not str:
        deny("W1_CONTENT_DENIED")
    try:
        payload_bytes = content.encode("utf-8", "strict")
    except UnicodeError:
        deny("W1_CONTENT_DENIED")
    if (not 1 <= len(payload_bytes) <= 2048 or "\x00" in content or
            type(p["content_bytes"]) is not int or
            p["content_bytes"] != len(payload_bytes)):
        deny("W1_CONTENT_DENIED")
    sha(p["content_sha256"], 64)
    if digest(payload_bytes) != p["content_sha256"]:
        deny("W1_CONTENT_HASH_MISMATCH")
    sig = proposal["approval_signature_b64"]
    if type(sig) is not str or not 40 <= len(sig) <= 16384:
        deny("W1_HUMAN_SIGNATURE_REQUIRED")
    # The signature itself is excluded, all other exact request fields included.
    signed = {k: v for k, v in proposal.items() if k != "approval_signature_b64"}
    return decode(canonical(signed))


def personal_w1_verify_offline(proposal, *, issue_number, expected_source_sha,
                                allowed_signers, expected_signers_sha256,
                                ssh_keygen, expected_verifier_sha256, now=None):
    """Cryptographic source gate only. NEVER authorizes a real file write.

    Public signer policy and verifier binary MUST be pinned by an authority
    outside the untrusted local Windows user / public self-hosted runner.
    A correct signature alone cannot supply durable replay prevention, root
    integrity or credential custody, so the returned execution flag is false.
    """
    signed = personal_w1_signing_payload(
        proposal, issue_number=issue_number,
        expected_source_sha=expected_source_sha, now=now)
    sha(expected_signers_sha256, 64)
    sha(expected_verifier_sha256, 64)
    allowed, exe = Path(allowed_signers), Path(ssh_keygen)
    if (not allowed.is_absolute() or not exe.is_absolute() or
            allowed.is_symlink() or exe.is_symlink() or
            not allowed.is_file() or not exe.is_file()):
        deny("W1_SIGNER_POLICY_UNTRUSTED")
    try:
        # Reparse path components are rejected on Windows before reading.
        for part in (allowed, *allowed.parents, exe, *exe.parents):
            if getattr(part.lstat(), "st_file_attributes", 0) & 0x400:
                deny("W1_SIGNER_POLICY_UNTRUSTED")
        signer_bytes = allowed.read_bytes()
        binary_bytes = exe.read_bytes()
        if (len(signer_bytes) > 8192 or
                digest(signer_bytes) != expected_signers_sha256 or
                digest(binary_bytes) != expected_verifier_sha256):
            deny("W1_SIGNER_POLICY_UNTRUSTED")
        lines = [line.split() for line in signer_bytes.decode("ascii").splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
    except (OSError, UnicodeError):
        deny("W1_SIGNER_POLICY_UNTRUSTED")
    if (len(lines) != 1 or len(lines[0]) != 3 or
            lines[0][0] != "hostops-human" or
            lines[0][1] != "ssh-ed25519"):
        deny("W1_SIGNER_POLICY_UNTRUSTED")
    verify_signature(signed, proposal["approval_signature_b64"], allowed,
                     "hostops-human", PERSONAL_W1_NAMESPACE, exe)
    return {
        "schema": "hostops.personal.w1_source_gate.v1",
        "status": "SIGNED_PROPOSAL_VERIFIED_NO_EFFECT",
        "issue_number": issue_number,
        "request_id": signed["request_id"],
        "source_sha": signed["source_sha"],
        "proposal_sha256": digest(canonical(signed)),
        "signature_valid": True,
        "execution_allowed": False,
        "replay_ledger_proven": False,
        "credential_custody_proven": False,
    }


def child_environment():
    # Windows OpenSSH needs PROGRAMDATA even for offline -Y verification. Match
    # names case-insensitively; Windows exposes SYSTEMROOT on some installations.
    return {k: v for k, v in os.environ.items()
            if k.casefold() in {"systemroot", "windir", "temp", "tmp", "programdata"}}


class Authority:
    def __init__(self, config, release, *, clock=time.time):
        self.config, self.release, self.clock = config, release, clock
        self.policy_dir = Path(config["policy_dir"])
        self.exe = Path(config["ssh_keygen"])
        if not self.exe.is_absolute() or digest(self.exe.read_bytes()) != config["ssh_keygen_sha256"]:
            deny("VERIFIER_UNVERIFIED")
        self.human = self.policy_dir / "human-signers"
        self.issuers = self.policy_dir / "issuer-signers"
        # Same key in the two roles would make Human approval non-independent.
        def keys(p):
            lines = [l.split() for l in p.read_text(encoding="utf-8").splitlines()
                     if l.strip() and not l.lstrip().startswith("#")]
            if not lines or any(len(l) < 3 or l[1] != "ssh-ed25519" for l in lines):
                deny("SIGNER_POLICY_INVALID")
            return {l[2] for l in lines}
        if keys(self.human) & keys(self.issuers):
            deny("HUMAN_KEY_NOT_INDEPENDENT")

    def verify(self, body, sig, human, namespace, identity=None):
        verify_signature(body, sig, self.human if human else self.issuers,
                         "hostops-human" if human else identity, namespace, self.exe)

    def authenticate(self, e, request, *, reconcile=False):
        r, lease = request.value, e["lease"]
        self.verify(r, e["issuer_signature_b64"], False, "hostops-v3-request", r["issuer"])
        shape(lease, "schema lease_id issuer host_id release_id roots operations not_before expires_at max_bytes max_entries allow_unc")
        if lease["schema"] != "hostops.lease.v3":
            deny("BAD_LEASE")
        ident(lease["lease_id"])
        for k in ("issuer", "host_id", "release_id"):
            if lease[k] != r[k]:
                deny("LEASE_IDENTITY_MISMATCH")
        integer(lease["not_before"], 0, 2**53 - 1)
        integer(lease["expires_at"], lease["not_before"] + 1, lease["not_before"] + 86400)
        integer(lease["max_bytes"], 1, MAX_FILE)
        integer(lease["max_entries"], 1, 4096)
        if type(lease["allow_unc"]) is not bool:
            deny("BAD_LEASE")
        if type(lease["roots"]) is not list or not 1 <= len(lease["roots"]) <= 32:
            deny("BAD_LEASE")
        for root in lease["roots"]:
            text(root)
            if not Path(root).is_absolute():
                deny("BAD_LEASE_ROOT")
        if (type(lease["operations"]) is not list or not lease["operations"] or
                any(type(v) is not str or v not in OPS for v in lease["operations"]) or
                len(set(lease["operations"])) != len(lease["operations"])):
            deny("BAD_LEASE")
        self.verify(lease, e["lease_signature_b64"], True, "hostops-v3-lease")
        if (r["host_id"] != self.config["host_id"] or r["release_id"] != self.release["release_id"] or
                r["source_sha"] != self.release["source_sha"]):
            deny("SOURCE_UNVERIFIED")
        if r["operation"] not in lease["operations"]:
            deny("LEASE_OPERATION_DENIED")
        # Exact independent approval for irreversible quarantine and EVERY export.
        # Preview needs a lease but no exact action signature.
        if r["operation"] in ("quarantine", "disclose") and not r["preview"]:
            self.verify({"request": r, "lease_sha256": digest(canonical(lease))},
                        e["action_signature_b64"], True, "hostops-v3-exact-action")
        self.checkpoint(r, lease, reconcile=reconcile)
        return lease

    def checkpoint(self, r, lease, *, reconcile=False):
        now = int(self.clock())
        if not self.config["enabled"] or (self.policy_dir / "KILL").exists():
            deny("KILL_SWITCH")
        if not (r["created_at"] <= now and (now < r["expires_at"] or reconcile) and
                lease["not_before"] <= now < lease["expires_at"]):
            deny("EXPIRED_OR_FUTURE")
        v = decode((self.policy_dir / "revocations.json").read_bytes())
        shape(v, "schema epoch lease_ids request_ids")
        if v["schema"] != "hostops.revocations.v3":
            deny("REVOCATION_UNAVAILABLE")
        integer(v["epoch"], 0, 2**53 - 1)
        for k in ("lease_ids", "request_ids"):
            if type(v[k]) is not list or len(v[k]) > 4096:
                deny("REVOCATION_UNAVAILABLE")
            for item in v[k]:
                ident(item)
        if lease["lease_id"] in v["lease_ids"] or r["request_id"] in v["request_ids"]:
            deny("REVOKED")
        return v["epoch"]


def verify_release(config):
    """Installed immutable release, not code checked out from product main."""
    root = Path(config["release_dir"])
    release = decode((root / "release.json").read_bytes())
    shape(release, "schema release_id source_sha workflow_sha256 files")
    if release["schema"] != "hostops.release.v3":
        deny("BAD_RELEASE")
    sha(release["source_sha"], 40)
    sha(release["workflow_sha256"])
    unsigned = {k: v for k, v in release.items() if k != "release_id"}
    if digest(canonical(unsigned)) != release["release_id"]:
        deny("RELEASE_DIGEST_MISMATCH")
    if type(release["files"]) is not dict or not release["files"]:
        deny("BAD_RELEASE")
    for relative, expected in release["files"].items():
        if (type(relative) is not str or "\\" in relative or ":" in relative or
                relative.startswith("/") or any(p in ("", ".", "..") for p in relative.split("/"))):
            deny("BAD_RELEASE_PATH")
        sha(expected)
        target = root / relative
        if target.is_symlink() or not target.is_file() or digest(target.read_bytes()) != expected:
            deny("SOURCE_UNVERIFIED")
    authority = Authority(config, release)
    authority.verify(unsigned, (root / "release.sig.b64").read_text(encoding="ascii").strip(),
                     True, "hostops-v3-release")
    # Every loaded HostOps module must be in the signed manifest at its installed location.
    package = Path(__file__).resolve().parent
    for module in package.glob("*.py"):
        try:
            relative = module.relative_to(root.resolve()).as_posix()
        except ValueError:
            deny("EXECUTING_UNPINNED_CODE")
        if relative not in release["files"] or digest(module.read_bytes()) != release["files"][relative]:
            deny("EXECUTING_UNPINNED_CODE")
    return release, authority


# v4 release/request authentication is deliberately independent of the legacy
# v3 broker IPC dispatch. Only verify_release_v4 returns a verified v4 authority.
from .contract import SOURCE_REPOSITORY, CONTROL_REPOSITORY, MUTATIONS, dual_repo_envelope


def _check_v4_release(root, release):
    shape(release, "schema release_id source_repository control_repository source_sha workflow_sha256 files")
    if (release["schema"] != "hostops.release.v4" or
            release["source_repository"] != SOURCE_REPOSITORY or
            release["control_repository"] != CONTROL_REPOSITORY):
        deny("RELEASE_REPOSITORY_DENIED")
    sha(release["release_id"])
    sha(release["source_sha"], 40)
    sha(release["workflow_sha256"])
    unsigned = {key: value for key, value in release.items() if key != "release_id"}
    if digest(canonical(unsigned)) != release["release_id"]:
        deny("RELEASE_DIGEST_MISMATCH")
    if type(release["files"]) is not dict or not release["files"]:
        deny("BAD_RELEASE")
    for relative, expected in release["files"].items():
        if (type(relative) is not str or "\\" in relative or ":" in relative or
                relative.startswith("/") or
                any(part in ("", ".", "..") for part in relative.split("/"))):
            deny("BAD_RELEASE_PATH")
        sha(expected)
        target = root / relative
        if target.is_symlink() or not target.is_file() or digest(target.read_bytes()) != expected:
            deny("SOURCE_UNVERIFIED")
    return unsigned


class AuthorityV4(Authority):
    """Signed dual-repository admission, NOT wired into the v3 IPC broker."""

    def __init__(self, config, release, *, clock=time.time):
        super().__init__(config, release, clock=clock)
        unsigned = _check_v4_release(Path(config["release_dir"]), release)
        self.verify(unsigned, (Path(config["release_dir"]) / "release.sig.b64").read_text(
            encoding="ascii").strip(), True, "hostops-v4-release")

    def authenticate_v4(self, raw, *, reconcile=False):
        e, request = dual_repo_envelope(raw)
        r, lease = request.value, e["lease"]
        self.verify(r, e["issuer_signature_b64"], False, "hostops-v4-request", r["issuer"])
        # Closed lease schema, bounded local roots/operations, exact v4
        # independent Human signature. No v3 signature namespace accepted.
        ident(lease["lease_id"])
        integer(lease["not_before"], 0, 2**53 - 1)
        integer(lease["expires_at"], lease["not_before"] + 1, lease["not_before"] + 86400)
        integer(lease["max_bytes"], 1, MAX_FILE)
        integer(lease["max_entries"], 1, 4096)
        if type(lease["allow_unc"]) is not bool:
            deny("BAD_LEASE")
        if type(lease["roots"]) is not list or not 1 <= len(lease["roots"]) <= 32:
            deny("BAD_LEASE")
        for root in lease["roots"]:
            text(root)
            if not Path(root).is_absolute():
                deny("BAD_LEASE_ROOT")
        if (type(lease["operations"]) is not list or not lease["operations"] or
                any(type(op) is not str or op not in OPS for op in lease["operations"]) or
                len(set(lease["operations"])) != len(lease["operations"])):
            deny("BAD_LEASE")
        self.verify(lease, e["lease_signature_b64"], True, "hostops-v4-lease")
        if (r["source_repository"] != self.release["source_repository"] or
                r["control_repository"] != self.release["control_repository"] or
                r["source_sha"] != self.release["source_sha"] or
                r["release_id"] != self.release["release_id"] or
                r["host_id"] != self.config["host_id"]):
            deny("SOURCE_UNVERIFIED")
        if r["operation"] not in lease["operations"]:
            deny("LEASE_OPERATION_DENIED")
        # Independent exact Human signature for ALL mutating/script/export
        # operations, not just v3's quarantine/disclose subset.
        if not r["preview"] and r["operation"] in MUTATIONS | {"powershell", "disclose"}:
            self.verify({"request": r, "lease_sha256": digest(canonical(lease))},
                        e["action_signature_b64"], True, "hostops-v4-exact-action")
        self.checkpoint(r, lease, reconcile=reconcile)
        return request, lease


def verify_release_v4(config):
    """Human-signed pinned release; NEVER enabled by the current v3 CLI."""
    root = Path(config["release_dir"])
    release = decode((root / "release.json").read_bytes())
    authority = AuthorityV4(config, release)
    package = Path(__file__).resolve().parent
    for module in package.glob("*.py"):
        try:
            relative = module.relative_to(root.resolve()).as_posix()
        except ValueError:
            deny("EXECUTING_UNPINNED_CODE")
        if relative not in release["files"] or digest(module.read_bytes()) != release["files"][relative]:
            deny("EXECUTING_UNPINNED_CODE")
    return release, authority
