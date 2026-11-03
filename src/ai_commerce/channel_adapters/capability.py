"""Native A07/A08/A15/D05/L07 read bindings and unconditional live denial."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3

from ai_commerce.analytics.files import local_path
from ai_commerce.distribution.evaluator import evaluate
from ai_commerce.distribution.model import identity
from ai_commerce.distribution.registry import CapabilityRegistry
from ai_commerce.distribution.receipt import PublicationJournal
from ai_commerce.distribution_intelligence.dashboard import PublicationSelection, build_dashboard
from ai_commerce.distribution_intelligence.reconciliation import ReconciliationLedger, inspect_sources, read_package
from ai_commerce.distribution_intelligence.schemas import scope_value
from ai_commerce.operations.registry import ArtifactRegistry
from ai_commerce.operations.retry_guard import RetryGuard

from .schemas import (AdapterError, PROFILES, aware, checked_request, detached, digest,
                      public_data, require, validate)


class _ReadOnlyRegistry(CapabilityRegistry):
    """Use the native decoder/evaluator without A07's creating read connection."""
    @contextmanager
    def _connect(self):
        db = None
        try:
            path = local_path(self.path)
            info = path.stat()
            require(path.is_file() and info.st_nlink == 1 and 0 < info.st_size <= 16 * 1024 * 1024,
                    "A07_SOURCE_UNSAFE")
            db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5, isolation_level=None)
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("BEGIN")
            require(db.execute("PRAGMA quick_check(1)").fetchone() == ("ok",), "A07_DATABASE_CORRUPT")
            rows = db.execute("SELECT seq,snapshot_id,stream_key,payload,payload_sha256 FROM snapshots ORDER BY seq LIMIT 257").fetchall()
            require(len(rows) <= 256 and [r[0] for r in rows] == list(range(1, len(rows) + 1)), "A07_HISTORY_INVALID")
            heads = {}
            for _, ident, key, raw, sha in rows:
                require(type(raw) is str and len(raw.encode()) <= 65536, "A07_RECORD_SIZE_LIMIT")
                record = self._decode((raw, sha))
                require(record["snapshot_id"] == ident and identity(record) == key, "A07_IDENTITY_MISMATCH")
                previous = heads.get(key)
                require(record.get("supersedes_snapshot_id") == previous, "A07_SUPERSESSION_INVALID")
                heads[key] = ident
            yield db
        except AdapterError:
            raise
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            raise AdapterError("NATIVE_A07_UNPROVEN") from exc
        finally:
            if db is not None:
                db.close()


def capability_for(request, registry: CapabilityRegistry, *, at: datetime):
    value = checked_request(request)
    require(type(registry) is CapabilityRegistry, "NATIVE_A07_REGISTRY_REQUIRED")
    target = value["target"]
    native = {"schema_version": "ChannelCapabilityRequest.v1",
              **{k: target[k] for k in ("platform", "surface", "account_ref", "app_ref", "market")},
              "action": PROFILES[target["platform"]]["a07_action"], "simulation": True}
    decision = evaluate(native, _ReadOnlyRegistry(registry.path), at=aware(at))
    require(decision["verification"] == "OFFLINE_RECORDED_NOT_LIVE_VERIFIED", "A07_VERIFICATION_CHANGED")
    return validate("Capability", {"schema_version": "AdapterCapability.v1", "adapter_version": value["adapter_version"],
        "platform": target["platform"], "target_sha256": digest(target), "a07_decision_sha256": digest(decision),
        "a07_decision": decision["decision"], "verification": decision["verification"],
        "live_eligible": False, "entitlement": "ENTITLEMENT_UNPROVEN"})


@dataclass(frozen=True, slots=True)
class NativeBinding:
    """Selectors refer to native custody, never an adapter-supplied receipt dict."""
    selection: PublicationSelection
    scope: dict

    def reopen(self, request):
        req = checked_request(request)
        sel = self.selection
        require(type(sel) is PublicationSelection and type(sel.journal) is PublicationJournal
                and type(sel.guard) is RetryGuard and type(sel.guard.registry) is ArtifactRegistry,
                "NATIVE_SOURCE_SELECTION_REQUIRED")
        require(type(sel.package_dir) in (str, Path) or isinstance(sel.package_dir, Path), "LOCAL_PACKAGE_PATH_REQUIRED")
        require((sel.reconciliation_id is None) == (sel.reconciliation_ledger is None), "D05_SELECTION_INCOMPLETE")
        try:
            scope = scope_value(detached(self.scope), publication=True)
            public_data(scope)
            target = req["target"]
            for field in ("platform", "surface", "account_ref", "app_ref", "market", "content_sha256"):
                require(scope[field] == target[field], "NATIVE_TARGET_MISMATCH")
            sources = inspect_sources(**sel.arguments(scope))
            require(sources["source_sha256"] == sel.source_sha256 == req["source_sha256"], "NATIVE_SOURCE_PIN_MISMATCH")
            package = read_package(sel.package_dir)
            require(all(package[k] == target[k] for k in ("package_id", "package_sha256"))
                    and package["video_sha256"] == target["content_sha256"], "NATIVE_PACKAGE_MISMATCH")
            reconciliation_sha = None
            if sel.reconciliation_ledger is not None:
                require(type(sel.reconciliation_ledger) is ReconciliationLedger, "NATIVE_D05_LEDGER_REQUIRED")
                history = sel.reconciliation_ledger.history()
                matching = [r for r in history if r.to_dict()["sources"]["package"] == sources["package"]]
                require(matching and matching[-1].record_id == sel.reconciliation_id, "STALE_D05_SELECTION")
                record = sel.reconciliation_ledger.get(sel.reconciliation_id).to_dict()
                require(record["sources"] == sources, "D05_SOURCE_MISMATCH")
                reconciliation_sha = record["record_sha256"]
            dashboard = build_dashboard(scope=scope, publication=sel)
            native = dashboard.to_dict()
            require(type(native["publication"]["decision"]) is str, "NATIVE_DECISION_UNPROVEN")
            require(inspect_sources(**sel.arguments(scope)) == sources, "NATIVE_SOURCE_CHANGED_DURING_READ")
            return {"sources": sources, "dashboard": dashboard, "scope": scope,
                    "reconciliation_sha256": reconciliation_sha, "decision": native["publication"]["decision"]}
        except AdapterError:
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            raise AdapterError("NATIVE_SOURCE_UNPROVEN") from exc


def submit_live(request):
    """No transport argument, provider client, credential or effect implementation."""
    checked_request(request)
    return validate("Denial", {"schema_version": "DirectPostDenial.v1", "request_sha256": request.sha256,
        "reason": "ENTITLEMENT_UNPROVEN", "provider_called": False, "live_authorized": False,
        "provider_attested": False, "public_visibility": "UNKNOWN", "real_post_created": False,
        "retry_dispatched": False})
