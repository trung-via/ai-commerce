"""Domain-separated OpenSSH signatures; no keys or raw verifier output in receipts."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess
import tempfile
import time

from .contract import (OPS, MAX_FILE, canonical, decode, deny, digest, ident,
                       integer, sha, shape, text)


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
