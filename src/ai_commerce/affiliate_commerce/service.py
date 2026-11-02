"""A14 E04: bind actual offline component evidence without commercial promotion.

Build reopens A06/A08 local bytes and A08's read-only publication journal.
Portable replay checks retained snapshots, not current disk or provider state.
Hashes establish content identity, not authenticity or legal entitlement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from ai_commerce.affiliate_attribution import AffiliateAttributionEvidence
from ai_commerce.affiliate_attribution.evidence import canonical_bytes, parse_json
from ai_commerce.affiliate_release import AffiliateReleaseEvidencePack
from ai_commerce.analytics.files import ledger_path
from ai_commerce.analytics.matching import _ReadOnlyJournal, _check_receipt
from ai_commerce.core.provenance import sha256_bytes
from ai_commerce.distribution.assisted import _check_fields, inspect_package
from ai_commerce.distribution.model import provider_profiles, validate_request, validate_snapshot
from ai_commerce.distribution.receipt import PublicationJournal
from ai_commerce.e2e import verify as verify_e01
from ai_commerce.e2e.service import _check_payload
from ai_commerce.product import ProductEvidencePack
from ai_commerce.product.offers import _freshness
from ai_commerce.product.pack import derive_offers_assessment
from ai_commerce.render.manifest import RenderManifest, manifest_ref

from .schemas import SLOTS, affiliate_commerce_request_schema, affiliate_commerce_run_schema

MAX_BYTES = 20 * 1024 * 1024
_REQUEST = Draft202012Validator(affiliate_commerce_request_schema(), format_checker=FormatChecker())
_RUN = Draft202012Validator(affiliate_commerce_run_schema(), format_checker=FormatChecker())
_EVENT = Draft202012Validator(parse_json(files("ai_commerce.distribution").joinpath(
    "schemas", "PublicationEvent.v1.schema.json").read_bytes()), format_checker=FormatChecker())


class CommerceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(ok, code):
    if not ok:
        raise CommerceError(code)


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def digest(value):
    return sha256_bytes(canonical_bytes(value))


def request_value(value):
    require(_REQUEST.is_valid(value), "INVALID_COMMERCE_REQUEST")
    scope = value["scope"]
    # The native A07 validator checks namespace, app, platform and surface shape.
    validate_request({"schema_version": "ChannelCapabilityRequest.v1",
        **{k: scope[k] for k in ("platform", "account_ref", "app_ref", "market", "surface")},
        "action": "EXPORT_PACKAGE"})
    account_ns = {"TIKTOK": "tiktok_open_id", "YOUTUBE": "youtube_channel_id",
                  "FACEBOOK": "facebook_page_id", "INSTAGRAM": "instagram_user_id"}
    app_ns = {"TIKTOK": "tiktok_client_key", "YOUTUBE": "google_cloud_project_id",
              "FACEBOOK": "meta_app_id", "INSTAGRAM": "meta_app_id"}
    require(scope["account_ref"]["namespace"] in {"internal_alias", account_ns[scope["platform"]]}
        and (scope["app_ref"] is None or scope["app_ref"]["namespace"] in
             {"internal_alias", app_ns[scope["platform"]]}) and
        scope["surface"] in provider_profiles()["platforms"][scope["platform"]]["surfaces"],
        "TARGET_NAMESPACE_OR_SURFACE_INVALID")
    start, end = map(instant, scope["measurement_window"])
    require(start < end <= instant(value["as_of"]), "INVALID_MEASUREMENT_WINDOW")
    return value


def blob(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 8 * 1024 * 1024, "EVIDENCE_SIZE_INVALID")
    return {"raw_utf8": raw.decode("utf-8"), "raw_sha256": sha256_bytes(raw)}


def target_matches(record, scope):
    require(all(record[k] == scope[k] for k in
        ("platform", "account_ref", "app_ref", "market", "surface")), "TARGET_IDENTITY_MISMATCH")


def stage(status, slots=(), *codes):
    return {"status": status, "evidence_slots": sorted(slots), "reason_codes": sorted(set(codes))}


def publication_snapshot(path, publication_id):
    """One read-only transaction, including A08's real BLOB/hash/state replay."""
    journal = _ReadOnlyJournal(ledger_path(path, create=False))
    db = journal._connect()
    try:
        receipt = PublicationJournal._read(db, publication_id)
        require(receipt is not None, "PUBLICATION_RECORD_MISSING")
        _check_receipt(receipt, publication_id)
        rows = db.execute("SELECT event_digest,event_json FROM publication_events "
                          "WHERE publication_id=? ORDER BY seq", (publication_id,)).fetchall()
        require(len(rows) <= 256, "PUBLICATION_EVENT_LIMIT")
        events = []
        for expected_hash, raw in rows:
            require(sha256_bytes(raw) == expected_hash, "PUBLICATION_EVENT_HASH_MISMATCH")
            events.append(blob(raw))
        return {"receipt": receipt, "events": events}
    finally:
        db.rollback()
        db.close()


def check_publication(snapshot, at):
    """Portable event snapshots retain timing and receipt pins, never receipt BLOBs.

    Build already verified original BLOBs using A08. Portable replay cannot
    authenticate those BLOBs or recheck current public visibility.
    """
    require(set(snapshot) == {"receipt", "events"} and type(snapshot["events"]) is list
        and len(snapshot["events"]) <= 256, "PUBLICATION_SNAPSHOT_INVALID")
    receipt = snapshot["receipt"]
    require(set(receipt) == {"publication_id", "idempotency_key", "approved_package_sha256",
        "platform", "surface", "account_ref", "account_identity", "state", "visibility",
        "commerce_link_status", "provider_post_id", "public_url", "receipt_refs", "receipt_sha256",
        "attempt_id", "created_at", "last_checked_at", "verification", "events"}, "PUBLICATION_FIELDS_INVALID")
    _check_receipt(receipt, receipt["publication_id"])
    previous_time = instant(receipt["created_at"])
    require(previous_time <= at, "PUBLICATION_FROM_FUTURE")
    state, visibility, post, url, receipt_sha, refs = "MANUAL_ACTION_REQUIRED", "UNKNOWN", None, None, None, []
    for envelope in snapshot["events"]:
        raw = envelope["raw_utf8"].encode("utf-8")
        require(blob(raw) == envelope, "PUBLICATION_EVENT_HASH_MISMATCH")
        event = parse_json(raw)
        require(_EVENT.is_valid(event) and event["publication_id"] == receipt["publication_id"],
                "PUBLICATION_EVENT_INVALID")
        recorded = instant(event["recorded_at"])
        require(previous_time <= recorded <= at, "PUBLICATION_EVENT_OUTSIDE_TIME_WINDOW")
        previous_time = recorded
        kind, sha = event["event_type"], event["evidence_sha256"]
        if sha is not None:
            refs.append(sha)
        if kind == "SUBMISSION_REPORTED":
            require(state in {"MANUAL_ACTION_REQUIRED", "UNKNOWN"}, "PUBLICATION_TRANSITION_INVALID")
            state = "SUBMITTED"
        elif kind == "PUBLICATION_RECEIPT":
            require(state in {"SUBMITTED", "UNKNOWN", "PRIVATE_ONLY"} and
                sha is not None and event["provider_post_id"] is not None, "PUBLICATION_RECEIPT_REQUIRED")
            state, receipt_sha, post = "PUBLISHED_CONFIRMED", sha, event["provider_post_id"]
        elif kind == "VISIBILITY_EVIDENCE":
            require(state == "PUBLISHED_CONFIRMED" and sha is not None and sha != receipt_sha
                and event["public_url"] is not None, "INDEPENDENT_VISIBILITY_REQUIRED")
            visibility, url = "PUBLIC", event["public_url"]
        elif kind == "REMOVAL_REPORTED":
            require(state in {"PUBLISHED_CONFIRMED", "PRIVATE_ONLY"} and sha is not None,
                    "PUBLICATION_TRANSITION_INVALID")
            state, visibility = "REMOVED", "UNKNOWN"
        elif kind in {"REJECTION_REPORTED", "OUTCOME_UNKNOWN"}:
            state, visibility = ("REJECTED" if kind == "REJECTION_REPORTED" else "UNKNOWN"), "UNKNOWN"
    require(receipt["state"] == state and receipt["visibility"] == visibility and
        receipt["provider_post_id"] == post and receipt["public_url"] == url and
        receipt["receipt_sha256"] == receipt_sha and receipt["receipt_refs"] == refs and
        receipt["events"] == len(snapshot["events"]), "PUBLICATION_REPLAY_MISMATCH")
    return receipt


def _derive(request, evidence):
    request_value(request)
    require(set(evidence) == set(SLOTS), "EVIDENCE_SLOTS_INVALID")
    docs = {}
    for slot, item in evidence.items():
        if item is None:
            continue
        require(set(item) == {"raw_utf8", "raw_sha256"}, "EVIDENCE_ENVELOPE_INVALID")
        raw = item["raw_utf8"].encode("utf-8")
        require(blob(raw) == item, "EVIDENCE_HASH_MISMATCH")
        docs[slot] = parse_json(raw, limit=MAX_BYTES)
    scope, at = request["scope"], instant(request["as_of"])
    require({"e01_manifest", "e01_delivery", "render_manifest"} <= docs.keys(),
            "CREATIVE_EVIDENCE_REQUIRED")
    manifest = _check_payload(docs["e01_manifest"])
    delivery = docs["e01_delivery"]
    render = RenderManifest(canonical_bytes(docs["render_manifest"])).to_dict()
    require(manifest["run_id"] == "e01-" + manifest["request_sha256"][:32], "CREATIVE_RUN_ID_MISMATCH")
    require(delivery == {"schema_version": "E01Delivery.v1", "video_ref": "video.mp4",
        "manifest_ref": "e01-manifest.json", "video_sha256": manifest["output_sha256"],
        "manifest_sha256": evidence["e01_manifest"]["raw_sha256"],
        "state": "DELIVERED", "publication_status": "NOT_ATTEMPTED"}, "DELIVERY_BINDING_MISMATCH")
    require(render["state"] == "SUCCEEDED" and render["job_id"] == manifest["render"]["job_id"]
        and render["record_sha256"] == manifest["render"]["manifest_sha256"]
        and manifest_ref(render["job_id"]) == manifest["render"]["manifest_ref"]
        and render["output"]["sha256"] == manifest["output_sha256"], "RENDER_BINDING_MISMATCH")
    require(scope["creative_ref"] == manifest["run_id"] and
        scope["creative_sha256"] == manifest["output_sha256"], "CREATIVE_IDENTITY_MISMATCH")
    stages = {"creative": stage("VERIFIED_LOCAL", ("e01_manifest", "e01_delivery", "render_manifest"),
                               "A06_LOCAL_DELIVERY_BOUND")}

    product = None
    stages["product"] = stage("UNKNOWN", (), "PRODUCT_EVIDENCE_MISSING")
    if "product_pack" in docs:
        product = ProductEvidencePack.from_bytes(canonical_bytes(docs["product_pack"])).to_dict()
        require(all(product["subject"][k] == scope[k] for k in
            ("product_id", "listing_id", "variant_id")), "PRODUCT_IDENTITY_MISMATCH")
        require(instant(product["generated_at"]) <= at and instant(product["offers"]["as_of"]) <= at,
                "PRODUCT_EVIDENCE_FROM_FUTURE")
        status = "VERIFIED_LOCAL" if product["rights_status"] == "GRANTED" else "UNKNOWN"
        # Refresh only native offer assessment at the explicit run time. A pack's
        # old assessment is evidence, not a claim that its offer remains current.
        current_offers = [{**obs, "freshness": _freshness(obs, at,
            product["offers"]["freshness_limit_seconds"])[0]} for obs in product["offers"]["observations"]]
        _, offer_status, active_offer = derive_offers_assessment(current_offers)
        if offer_status != "VALID_OBSERVATION":
            status = "UNKNOWN"
        if (product["rights_status"] == "RESTRICTED" or offer_status == "CONFLICT"
                or active_offer is not None and active_offer["stock_available"] is False
                or any(c["origin"] == "CONFLICT" for c in product["claims"])):
            status = "HOLD"
        for rights in product["rights"]:
            for permission in rights["permissions"]:
                term = permission["scope"]
                if permission["status"] == "GRANTED" and term is not None and not (
                    instant(term["valid_from"]) <= at < instant(term["valid_until"])):
                    status = "HOLD"
        stages["product"] = stage(status, ("product_pack",), "A12_FACTS_AND_RIGHTS_RETAINED")

    release = None
    decision = "UNKNOWN"
    stages["release"] = stage("UNKNOWN", (), "RELEASE_EVIDENCE_MISSING")
    if "release_pack" in docs:
        release = AffiliateReleaseEvidencePack.from_bytes(canonical_bytes(docs["release_pack"])).to_dict()
        rs, temporal, components = release["release_scope"], release["temporal"], release["components"]
        target_matches(rs, scope)
        require(all(rs[k] == scope[k] for k in ("channel", "product_id", "listing_id", "variant_id")),
                "RELEASE_IDENTITY_MISMATCH")
        require(rs["release_sha256"] == scope["creative_sha256"] and
            sha256_bytes(rs["link_url"].encode("utf-8")) == scope["link_url_sha256"],
            "RELEASE_LINK_OR_CREATIVE_MISMATCH")
        require(instant(temporal["as_of"]) <= at, "RELEASE_EVIDENCE_FROM_FUTURE")
        decision = release["state"]
        if (not instant(temporal["valid_from"]) <= at < instant(temporal["valid_until"])
                or at >= instant(temporal["use_until"])
                or temporal["revoked_at"] is not None and instant(temporal["revoked_at"]) <= at):
            decision = "HOLD"
        if product is not None:
            pc = components["product_evidence"]
            require(pc["pack_id"] == product["pack_id"] and
                pc["pack_sha256"] == evidence["product_pack"]["raw_sha256"], "RELEASE_PRODUCT_PIN_MISMATCH")
        elif decision != "HOLD":
            decision = "UNKNOWN"
        if stages["product"]["status"] == "HOLD":
            decision = "HOLD"
        elif stages["product"]["status"] == "UNKNOWN" and decision != "HOLD":
            decision = "UNKNOWN"
        stages["release"] = stage(decision, ("release_pack",), "A13_ADVISORY_DECISION_ONLY")

    stages["capability"] = stage("UNKNOWN", (), "CAPABILITY_EVIDENCE_MISSING")
    if "capability_snapshot" in docs:
        cap = validate_snapshot(docs["capability_snapshot"])
        target_matches(cap, scope)
        require(cap["action"] == "MANUAL_POST", "CAPABILITY_ACTION_MISMATCH")
        if release is not None:
            cc = release["components"]["capability_evidence"]
            require(cc["snapshot_id"] == cap["snapshot_id"] and
                cc["record_sha256"] == digest(cap), "RELEASE_CAPABILITY_PIN_MISMATCH")
        cap_status = "UNKNOWN"
        if cap["state"] in {"REVOKED", "DENIED"} or (
                cap.get("revoked_at") is not None and instant(cap["revoked_at"]) <= at):
            cap_status = "HOLD"
        elif (cap["state"] == "ALLOWED" and cap["source_mode"] == "OBSERVED"
                and instant(cap["observed_at"]) <= at and instant(cap["recorded_at"]) <= at
                and cap["expires_at"] is not None and at < instant(cap["expires_at"])):
            cap_status = "VERIFIED_LOCAL"
        stages["capability"] = stage(cap_status, ("capability_snapshot",), "A07_OFFLINE_EVIDENCE_ONLY")
    if decision == "READY_FOR_MANUAL_REVIEW" and stages["capability"]["status"] != "VERIFIED_LOCAL":
        decision = "HOLD" if stages["capability"]["status"] == "HOLD" else "UNKNOWN"
        stages["release"] = stage(decision, ("release_pack",), "CAPABILITY_NOT_CURRENTLY_ESTABLISHED")

    package = None
    stages["distribution"] = stage("UNKNOWN", (), "DISTRIBUTION_NOT_EXPORTED")
    if "distribution_package" in docs:
        package = docs["distribution_package"]
        _check_fields(package)
        target_matches(package, scope)
        require(package["source_run_id"] == manifest["run_id"] and
            package["source_manifest_sha256"] == evidence["e01_manifest"]["raw_sha256"] and
            package["video_sha256"] == scope["creative_sha256"], "DISTRIBUTION_CREATIVE_PIN_MISMATCH")
        if package["product_ref"] is not None:
            require(package["product_ref"] in
                {scope["product_id"], scope["listing_id"], product["pack_id"] if product else None} - {None},
                "DISTRIBUTION_PRODUCT_MISMATCH")
        if release is not None:
            require(package["disclosure"] == release["disclosure"]["statement"], "DISCLOSURE_MISMATCH")
            handoff = release["components"]["distribution_handoff"]
            if handoff["present"]:
                require(handoff["package_id"] == package["package_id"] and
                    handoff["package_sha256"] == package["package_sha256"], "RELEASE_HANDOFF_PIN_MISMATCH")
        status = ("HOLD" if package["capability"]["decision"] == "DENY" else
                  "PARTIAL" if package["product_ref"] is None else "EXPORTED_FOR_MANUAL_HANDOFF")
        stages["distribution"] = stage(status, ("distribution_package",), "EXPORT_IS_NOT_SUBMISSION")

    publication, visibility = "NOT_ATTEMPTED", "UNKNOWN"
    stages["publication"] = stage("UNKNOWN", (), "PUBLICATION_RECEIPT_MISSING")
    if "publication_record" in docs:
        receipt = check_publication(docs["publication_record"], at)
        require(package is not None and receipt["approved_package_sha256"] == package["package_sha256"],
                "PUBLICATION_PACKAGE_MISMATCH")
        require(receipt["platform"] == scope["platform"] and receipt["surface"] == scope["surface"]
            and receipt["account_identity"] == scope["account_ref"], "PUBLICATION_TARGET_MISMATCH")
        require(instant(receipt["created_at"]) <= at, "PUBLICATION_FROM_FUTURE")
        publication, visibility = receipt["state"], receipt["visibility"]
        require(publication in {"MANUAL_ACTION_REQUIRED", "SUBMITTED", "PUBLISHED_CONFIRMED",
            "REMOVED", "REJECTED", "UNKNOWN"} and visibility in {"PUBLIC", "UNKNOWN"},
            "PUBLICATION_STATE_INVALID")
        if publication in {"PUBLISHED_CONFIRMED", "REMOVED"}:
            require(receipt["receipt_sha256"] is not None and receipt["provider_post_id"] is not None,
                    "PUBLICATION_RECEIPT_REQUIRED")
        if visibility == "PUBLIC":
            require(publication == "PUBLISHED_CONFIRMED" and receipt["public_url"] is not None and
                bool(set(receipt["receipt_refs"]) - {receipt["receipt_sha256"]}), "INDEPENDENT_VISIBILITY_REQUIRED")
        stages["publication"] = stage(publication, ("publication_record",), "A08_USER_SUPPLIED_NOT_LIVE_VERIFIED")

    stages["attribution"] = stage("UNKNOWN", (), "ATTRIBUTION_EVIDENCE_MISSING")
    if "attribution" in docs:
        attr = AffiliateAttributionEvidence.from_bytes(canonical_bytes(docs["attribution"])).to_dict()
        a_scope = attr["scope"]
        for key in ("provider_ref", "platform", "account_ref", "market", "surface", "measurement_window", "timezone"):
            require(a_scope[key] == scope[key], "ATTRIBUTION_SCOPE_MISMATCH")
        require(a_scope["app_ref"] == (scope["app_ref"]["id"] if scope["app_ref"] else None),
                "ATTRIBUTION_APP_MISMATCH")
        events = attr["source"]["document"]["events"]
        for event in events:
            if event.get("link_ref") is not None:
                require(event["link_ref"] == scope["link_ref"], "ATTRIBUTION_LINK_MISMATCH")
            if event.get("creative_ref") is not None:
                require(event["creative_ref"] == scope["creative_ref"] and
                    event["creative_sha256"] == scope["creative_sha256"], "ATTRIBUTION_CREATIVE_MISMATCH")
        # L04 upstream refs are opaque pins. If supplied, open them against the exact
        # native record ID and raw bytes retained by this run; never ignore conflicts.
        refs = {"A06": (manifest["run_id"], "e01_manifest"),
                "A12": (product["pack_id"] if product else None, "product_pack"),
                "A13": (release["pack_id"] if release else None, "release_pack"),
                "A07": (docs.get("capability_snapshot", {}).get("snapshot_id"), "capability_snapshot"),
                "A08": (package["package_id"] if package else None, "distribution_package")}
        for ref in attr["source"]["document"]["upstream_refs"]:
            require(ref["component"] in refs, "UNSUPPORTED_ATTRIBUTION_UPSTREAM")
            identifier, slot = refs[ref["component"]]
            require(identifier == ref["evidence_ref"] and slot in docs and
                evidence[slot]["raw_sha256"] == ref["raw_sha256"], "ATTRIBUTION_UPSTREAM_PIN_MISMATCH")
        stages["attribution"] = stage("PARTIAL", ("attribution",), "L04_SUPPLIED_EVENTS_NOT_ACTUAL_ORDERS")

    stages["observations"] = stage("NOT_PROVIDED", (), "OPTIONAL_A09_NOT_USED")
    stages["commercial"] = stage("UNKNOWN", (), "NO_LIVE_ENTITLEMENT_ORDERS_OR_SETTLEMENT")
    payload = {"schema_version": "AffiliateCommerceRun.v1", "evaluator_version": "a14-e04/1.0",
        "request": request, "evidence": evidence, "stages": stages,
        "creative_output": {"video_ref": delivery["video_ref"], "sha256": scope["creative_sha256"],
            "source_run_id": manifest["run_id"], "composition_ref": manifest["composition"]["ref"],
            "render_manifest_ref": manifest["render"]["manifest_ref"], "custody_output_ref": render["output"]["ref"]},
        "status": "HOLD" if any(s["status"] == "HOLD" for s in stages.values()) else "PARTIAL",
        "release_decision": decision, "publication_status": publication, "visibility_status": visibility,
        "link_status": "UNKNOWN", "actual_orders_verified": False, "actual_affiliate_entitlement": "UNKNOWN",
        "actual_revenue": "UNKNOWN", "actual_payout": "UNKNOWN", "settlement_status": "UNKNOWN",
        "verification_scope": "OFFLINE_NATIVE_CONTRACTS_AND_PINNED_LOCAL_SNAPSHOTS",
        "network_calls_performed": False, "advisory_only": True}
    record_sha = digest(payload)
    result = {**payload, "record_sha256": record_sha, "run_id": "a14-commerce-" + record_sha}
    require(_RUN.is_valid(result), "RUN_SCHEMA_INVALID")
    return result


@dataclass(frozen=True, slots=True, init=False)
class AffiliateCommerceRun:
    """Immutable portable snapshot. Accessors return detached dictionaries."""
    _raw: bytes

    @classmethod
    def from_bytes(cls, raw, *, expected_id=None):
        try:
            value = parse_json(raw, limit=MAX_BYTES)
            require(_RUN.is_valid(value), "RUN_SCHEMA_INVALID")
            replay = _derive(value["request"], value["evidence"])
            require(canonical_bytes(value) == canonical_bytes(replay), "RUN_REPLAY_MISMATCH")
            require(expected_id is None or expected_id == value["run_id"], "RUN_PIN_MISMATCH")
            instance = object.__new__(cls)
            object.__setattr__(instance, "_raw", canonical_bytes(value))
            return instance
        except CommerceError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
            raise CommerceError("INVALID_UPSTREAM_EVIDENCE") from exc

    def to_bytes(self):
        return self._raw

    def to_dict(self):
        return parse_json(self._raw, limit=MAX_BYTES)

    @property
    def run_id(self):
        return self.to_dict()["run_id"]


def build_affiliate_commerce_run(*, request, creative_directory, product_pack=None,
        release_pack=None, capability_snapshot=None, distribution_directory=None,
        publication_db=None, publication_id=None, attribution=None):
    """Read-only integration. Missing optional evidence never stops A06 creative.

    No filesystem path is retained. All exact JSON input bytes and their hashes
    are retained; native immutable objects are required for A12/A13/L04.
    """
    try:
        request = parse_json(canonical_bytes(request), limit=MAX_BYTES)
        request_value(request)
        root = Path(creative_directory)
        verify_e01(root)  # Includes request, assets, composition, render, video and full decode.
        manifest_raw = (root / "e01-manifest.json").read_bytes()
        manifest = parse_json(manifest_raw)
        evidence = dict.fromkeys(SLOTS)
        evidence["e01_manifest"] = blob(manifest_raw)
        evidence["e01_delivery"] = blob((root / "delivery.json").read_bytes())
        evidence["render_manifest"] = blob((root / "custody" / manifest["render"]["manifest_ref"]).read_bytes())
        for slot, value, cls in (("product_pack", product_pack, ProductEvidencePack),
                ("release_pack", release_pack, AffiliateReleaseEvidencePack),
                ("attribution", attribution, AffiliateAttributionEvidence)):
            if value is not None:
                require(type(value) is cls, "NATIVE_EVIDENCE_OBJECT_REQUIRED")
                evidence[slot] = blob(value.to_bytes())
        if capability_snapshot is not None:
            evidence["capability_snapshot"] = blob(canonical_bytes(validate_snapshot(capability_snapshot)))
        if distribution_directory is not None:
            inspect_package(distribution_directory)  # Reopens every exported byte.
            evidence["distribution_package"] = blob((Path(distribution_directory) / "package.json").read_bytes())
        require((publication_db is None) == (publication_id is None), "PUBLICATION_REFERENCE_INCOMPLETE")
        if publication_db is not None:
            evidence["publication_record"] = blob(canonical_bytes(publication_snapshot(publication_db, publication_id)))
        return AffiliateCommerceRun.from_bytes(canonical_bytes(_derive(request, evidence)))
    except CommerceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
        raise CommerceError("INVALID_UPSTREAM_EVIDENCE") from exc


class CommerceRunHistory:
    """Bounded append-only snapshots. Caller serializes concurrent access.

    Revisions use new keys and retain earlier runs. Neither counts nor receipts
    are aggregated, and an existing link/creative identity cannot be rebound.
    """
    def __init__(self):
        self._runs = {}
        self._keys = {}
        self._bindings = {}

    def append(self, run):
        require(type(run) is AffiliateCommerceRun, "RUN_OBJECT_REQUIRED")
        checked = AffiliateCommerceRun.from_bytes(run.to_bytes())
        value = checked.to_dict()
        key = value["request"]["idempotency_key"]
        if key in self._keys:
            require(self._keys[key] == checked.run_id, "IDEMPOTENCY_KEY_COLLISION")
            return False
        require(len(self._runs) < 128, "HISTORY_RECORD_LIMIT")
        require(sum(len(raw) for raw in self._runs.values()) + len(checked.to_bytes()) <= 32 * 1024 * 1024,
                "HISTORY_SIZE_LIMIT")
        scope = value["request"]["scope"]
        namespace = {k: scope[k] for k in ("provider_ref", "platform", "account_ref", "app_ref", "market", "surface", "channel")}
        bindings = {digest({"creative_ref": scope["creative_ref"]}): scope["creative_sha256"],
            digest({**namespace, "link_ref": scope["link_ref"]}): digest({k: scope[k] for k in
                ("creative_ref", "creative_sha256", "product_id", "listing_id", "variant_id", "link_url_sha256")})}
        require(all(k not in self._bindings or self._bindings[k] == v for k, v in bindings.items()),
                "IDENTITY_BINDING_CONFLICT")
        self._runs[checked.run_id] = checked.to_bytes()
        self._keys[key] = checked.run_id
        self._bindings.update(bindings)
        return True

    def to_bytes(self):
        return canonical_bytes({"schema_version": "AffiliateCommerceHistory.v1",
            "runs": [parse_json(raw, limit=MAX_BYTES) for raw in self._runs.values()]})

    @classmethod
    def from_bytes(cls, raw):
        value = parse_json(raw, limit=32 * 1024 * 1024)
        require(set(value) == {"schema_version", "runs"} and
            value["schema_version"] == "AffiliateCommerceHistory.v1" and
            type(value["runs"]) is list and len(value["runs"]) <= 128, "INVALID_RUN_HISTORY")
        history = cls()
        for run in value["runs"]:
            require(history.append(AffiliateCommerceRun.from_bytes(canonical_bytes(run))), "DUPLICATE_HISTORY_RECORD")
        return history
