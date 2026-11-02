"""Authenticated local execution, at-most-once intent and explicit reconciliation."""
from __future__ import annotations

import base64
import errno
import os
from pathlib import Path
import sqlite3
import subprocess
import time

from .contract import (BLOCKED_CONDITIONAL_WRITES, MAX_CHUNK, MUTATIONS, Denied, canonical, decode, deny, digest,
                       envelope, public_receipt, receipt, receipt_v4, public_receipt_v4)
from .filesystem import Filesystem, PinnedPath
from .journal import Journal
from .auth import child_environment


class Worker:
    _receipt = staticmethod(receipt)
    _public_receipt = staticmethod(public_receipt)

    def _authenticate(self, raw, *, reconcile=False):
        e, request = envelope(raw)
        lease = self.authority.authenticate(e, request, reconcile=reconcile)
        return request, lease

    def __init__(self, config, authority, journal, *, clock=time.monotonic, fault=lambda _: None):
        self.config, self.authority, self.journal = config, authority, journal
        self.clock, self.fault = clock, fault

    def execute(self, raw, *, reconcile=False):
        request, fs, reserved = None, None, False
        try:
            request, lease = self._authenticate(raw, reconcile=reconcile)
            r, p, op = request.value, request.value["parameters"], request.value["operation"]
            deadline = self.clock() + r["timeout_seconds"]
            def checkpoint():
                epoch = self.authority.checkpoint(r, lease, reconcile=reconcile)
                if self.clock() >= deadline:
                    deny("TIMEOUT")
                return epoch
            epoch = checkpoint()
            previous = self.journal.reserve(request, epoch)
            reserved = True
            state = Path(self.config["state_dir"])
            fs = Filesystem(lease, state, [self.config[k] for k in ("state_dir", "transport_state_dir", "policy_dir", "release_dir")],
                            checkpoint, fault=self.fault)
            if previous:
                saved = Journal.previous(previous)
                if saved and saved["status"] not in ("UNKNOWN", "PARTIAL"):
                    return {"receipt": self._public_receipt(saved)}
                # NEVER repeat an effect. Reconciliation only observes hash/postcondition.
                if reconcile:
                    with self.journal.execution():
                        result = self.reconcile(request, fs)
                        self.journal.finish(request, result)
                        return {"receipt": self._public_receipt(result)}
                return {"receipt": self._receipt(request, "UNKNOWN", "RECONCILIATION_REQUIRED")}
            if reconcile:
                # Reconciliation never turns an unexecuted request into execution.
                return {"receipt": self._receipt(request, "UNKNOWN", "NO_PRIOR_EFFECT_PROOF")}
            self.fault("after_intent")
            with self.journal.execution():
                # Another request may have changed revocations while waiting for the lock.
                self.journal.epoch(checkpoint())
                result, disclosed = self.perform(request, lease, fs)
                self.fault("after_effect")
                out = self._receipt(request, result.pop("_status", "APPLIED" if op in MUTATIONS else "OBSERVED"), result=result)
                self.journal.finish(request, out)
            # Simulates a committed operation whose IPC/GitHub report was lost.
            self.fault("after_receipt")
            response = {"receipt": self._public_receipt(out)}
            if disclosed is not None:
                checkpoint()
                response["disclosure_b64"] = base64.b64encode(disclosed).decode("ascii")
            return response
        except Denied as exc:
            status = "PARTIAL" if fs and fs.started else "BLOCKED"
            if str(exc) == "HOST_BUSY":
                status = "BUSY"
            out = self._receipt(request, status, str(exc))
        except (OSError, sqlite3.Error, subprocess.TimeoutExpired):
            out = self._receipt(request, "UNKNOWN" if fs and fs.started else "UNAVAILABLE", "IO_UNAVAILABLE")
        # BaseException is deliberately NOT caught: a killed process leaves durable INTENT.
        if reserved:
            try:
                with self.journal.execution():
                    self.journal.finish(request, out)
            except (Denied, sqlite3.Error):
                pass  # Previously persisted INTENT remains UNKNOWN, never retryable.
        return {"receipt": self._public_receipt(out)}

    def select(self, request, lease, value):
        content = value if type(value) is bytes else canonical(value)
        if len(content) > MAX_CHUNK:
            deny("SELECTION_LIMIT")
        folder = Path(self.config["state_dir"]) / "selections"
        folder.mkdir(mode=0o700, exist_ok=True)
        if len(list(folder.glob("*.json"))) >= 128:
            deny("LOCAL_SELECTION_QUOTA")
        selected = digest(request.fingerprint.encode() + os.urandom(32))
        meta = {"schema": "hostops.selection.v3", "lease_id": lease["lease_id"],
                "issuer": lease["issuer"], "expires_at": min(lease["expires_at"], request.value["expires_at"]),
                "sha256": digest(content), "bytes": len(content)}
        for suffix, data in ((".bin", content), (".json", canonical(meta))):
            with (folder / (selected + suffix)).open("xb") as f:
                if os.name != "nt":
                    os.chmod(f.name, 0o600)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        return selected

    def perform(self, request, lease, fs):
        r, p, op = request.value, request.value["parameters"], request.value["operation"]
        selection = lambda value: self.select(request, lease, value)
        if r["preview"]:
            # No requested filesystem mutations. Preview receipt itself is durable local state.
            if "path" in p:
                fs.path(p["path"])
                if "expected_sha256" in p:
                    fs.assert_hash(p["path"], p["expected_sha256"])
            if "destination" in p:
                fs.path(p["destination"])
            action = {"lease_sha256": digest(canonical(lease)), "operation": op,
                      "parameters": {k: v for k, v in p.items() if k != "preview_sha256"}}
            preview = digest(canonical(action))
            self.journal.preview(preview)
            return {"_status": "PREVIEW", "preview_sha256": preview}, None
        if op in MUTATIONS:
            if op == "quarantine":
                action = {"lease_sha256": digest(canonical(lease)), "operation": op,
                          "parameters": {k: v for k, v in p.items() if k != "preview_sha256"}}
                if p["preview_sha256"] != digest(canonical(action)) or not self.journal.has_preview(p["preview_sha256"]):
                    deny("EXACT_PREVIEW_REQUIRED")
            if op in BLOCKED_CONDITIONAL_WRITES:
                # No policy/configuration flag enables the prototype adapter.
                # Preview/reconciliation remain available, but live conditional
                # effects need a reviewed native atomicity implementation.
                deny("CONDITIONAL_WRITE_ATOMICITY_UNPROVEN")
            return fs.mutate(op, p), None
        if op in ("volumes", "stat", "list", "find"):
            selected = []
            def capture(v):
                selected.append(selection(v))
            result = fs.metadata(op, p, capture)
            if selected:
                result["selection_id"] = selected[0]
            return result, None
        if op == "hash":
            value, count = fs.hash(p["path"])
            return {"sha256": value, "bytes": count}, None
        if op == "read":
            return fs.read(p, selection), None
        if op == "powershell":
            return self.powershell(p, fs), None
        if op == "disclose":
            folder = Path(self.config["state_dir"]) / "selections"
            meta = decode((folder / (p["selection_id"] + ".json")).read_bytes())
            if (meta["lease_id"] != lease["lease_id"] or meta["issuer"] != lease["issuer"] or
                    int(self.authority.clock()) >= meta["expires_at"]):
                deny("SELECTION_EXPIRED_OR_UNSCOPED")
            data = (folder / (p["selection_id"] + ".bin")).read_bytes()
            if len(data) > MAX_CHUNK or digest(data) != p["content_sha256"] or digest(data) != meta["sha256"]:
                deny("SELECTION_HASH_MISMATCH")
            if p["offset"] + p["length"] > len(data):
                deny("CHUNK_RANGE")
            chunk = data[p["offset"]:p["offset"] + p["length"]]
            fs.checkpoint()
            return {"_status": "DISCLOSED", "sha256": digest(data), "chunk_sha256": digest(chunk),
                    "offset": p["offset"], "bytes": len(chunk)}, chunk
        deny("OPERATION_DENIED")

    def powershell(self, p, fs):
        config = self.config
        script = Path(config["release_dir"]) / "scripts" / "HostOps-V3-TokenInventory.ps1"
        relative = "scripts/HostOps-V3-TokenInventory.ps1"
        if (digest(script.read_bytes()) != p["script_sha256"] or
                self.authority.release["files"].get(relative) != p["script_sha256"]):
            deny("SCRIPT_DIGEST_MISMATCH")
        executable = Path(config["pwsh"])
        if not executable.is_absolute() or digest(executable.read_bytes()) != config["pwsh_sha256"]:
            deny("POWERSHELL_UNVERIFIED")
        fs.checkpoint()
        # Fixed argv only. No request paths/expressions, -Command or execution-policy bypass.
        result = subprocess.run([str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                                capture_output=True, timeout=5, check=False,
                                env=child_environment())
        if result.returncode or len(result.stdout) > 2048:
            deny("SCRIPT_FAILED")
        out = decode(result.stdout, 2048)
        from .contract import integer, shape
        shape(out, "pwsh_major non_elevated")
        integer(out["pwsh_major"], 7, 99)
        if out["non_elevated"] is not True:
            deny("ELEVATION_DENIED")
        fs.checkpoint()
        return out

    def reconcile(self, request, fs):
        """Observation can establish a postcondition, never prove who produced it."""
        op, p = request.value["operation"], request.value["parameters"]
        observed = {}
        if op in ("create", "replace"):
            value, count = fs.hash(p["path"])
            observed = {"sha256": value, "bytes": count}
        elif op in ("copy", "move", "rename"):
            value, count = fs.hash(p["destination"])
            observed = {"sha256": value, "bytes": count}
        return self._receipt(request, "UNKNOWN", "RECONCILED_OBSERVATION_ONLY", {**observed, "reconciled": True})



class WorkerV4(Worker):
    """Read-only signed V4 runtime, not reachable from the current v3 CLI."""
    _receipt = staticmethod(receipt_v4)
    _public_receipt = staticmethod(public_receipt_v4)
    READ_ONLY = frozenset({"volumes", "stat", "list", "find", "hash", "read"})

    def _authenticate(self, raw, *, reconcile=False):
        request, lease = self.authority.authenticate_v4(raw, reconcile=reconcile)
        if request.value["operation"] not in self.READ_ONLY:
            deny("V4_OPERATION_NOT_ENABLED")
        return request, lease
