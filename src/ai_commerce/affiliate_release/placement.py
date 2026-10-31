"""A13-D06 Offline affiliate placement evidence review.

Evaluates:
- 7 dimensions: account x app x market x surface x product x variant x link
- Evidence provenance & tamper detection
- Required commercial affiliate disclosure
- Expiry and revocation
- Hostile URL detection (SSRF, malicious schemes, credentials, internal networks)
- Cross-account, cross-app, cross-market, cross-surface, cross-product mismatches

Parsed URL alone MUST NOT imply active eligible link.
Decisions HOLD / UNKNOWN / REVIEW only.
No OAuth, live provider calls, posting, real approvals, sales or earnings claims.
"""
from __future__ import annotations

import copy
import ipaddress
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit

from jsonschema import Draft202012Validator

from ai_commerce.core.provenance import sha256_bytes
from ai_commerce.product import (
    ProductError,
    ProductEvidencePack,
    canonical_bytes,
    parse_json,
    parse_timestamp,
)

from .placement_schemas import affiliate_placement_schema

_SCHEMA = affiliate_placement_schema()
_VALIDATOR = Draft202012Validator(_SCHEMA)
_DOCUMENT_LIMIT = 8 * 1024 * 1024

_REJECT = re.compile(
    r'(?i)(?:["\']?(?:access_token|refresh_token|client_secret|authorization|cookie|set-cookie|upload_token)["\']?\s*[:=]'
    r'|bearer\s+[a-z0-9._-]{8,}|[?&](?:access_token|refresh_token|client_secret|auth)=)'
)

_AFFILIATE_KEYWORDS = (
    "affiliate",
    "commission",
    "sponsored",
    "paid partnership",
    "paid promotion",
    "ad",
    "quảng cáo",
    "tiếp thị liên kết",
    "advertisement",
    "#ad",
    "#affiliate",
    "#sponsored",
)

_AFFILIATE_TAGS = (
    "#ad",
    "#affiliate",
    "#sponsored",
    "#paidpartner",
    "#paidpartnership",
    "#promotion",
    "#commercial",
    "#quangcao",
    "#tiepthilienket",
)


class AffiliatePlacementError(ValueError):
    """Stable bounded code; errors do not echo secrets or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise AffiliatePlacementError(code)


@dataclass(frozen=True)
class PlacementRequest:
    account_ref: dict[str, str] | str
    app_ref: dict[str, str] | str | None = None
    market: str = "VN"
    surface: str = "PRODUCT_ANCHOR"
    product_id: str = ""
    variant_id: str = ""
    link_url: str = ""


@dataclass(frozen=True)
class PlacementDisclosure:
    statement: str
    mode: str = "CLEAR_AND_CONSPICUOUS"
    tags: tuple[str, ...] = ()
    has_affiliate_notice: bool | None = None


@dataclass(frozen=True)
class PlacementTemporal:
    as_of: str
    valid_from: str
    valid_until: str
    revoked_at: str | None = None


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    kind: str
    record_sha256: str
    source_sha256: str | None = None
    tamper_checked: bool = True
    raw_bytes: bytes | None = field(default=None, repr=False)


def _normalize_account(acc: dict[str, Any] | str) -> dict[str, str]:
    if isinstance(acc, dict):
        return {
            "namespace": str(acc.get("namespace", "internal_alias")),
            "id": str(acc.get("id", "")),
        }
    if isinstance(acc, str):
        return {"namespace": "internal_alias", "id": acc}
    raise AffiliatePlacementError("INVALID_ACCOUNT_REF")


def _normalize_app(app: dict[str, Any] | str | None) -> dict[str, str] | None:
    if app is None:
        return None
    if isinstance(app, dict):
        return {
            "namespace": str(app.get("namespace", "internal_alias")),
            "id": str(app.get("id", "")),
        }
    if isinstance(app, str):
        return {"namespace": "internal_alias", "id": app}
    raise AffiliatePlacementError("INVALID_APP_REF")


def _evaluate_url(url: str) -> tuple[dict[str, Any], list[str], bool]:
    codes: list[str] = []
    is_hostile = False

    if not isinstance(url, str):
        return {
            "parsed_url": "",
            "scheme": "https",
            "host": "invalid",
            "is_hostile": True,
            "reason_codes": ["HOSTILE_URL", "MALFORMED_URL"],
        }, ["HOSTILE_URL", "MALFORMED_URL"], True

    if len(url) < 8 or len(url) > 4096:
        codes.append("INVALID_URL_LENGTH")
        is_hostile = True

    if any(ord(c) < 32 or ord(c) == 127 for c in url) or "\r" in url or "\n" in url or "\0" in url:
        codes.append("CONTROL_CHARACTERS_IN_URL")
        is_hostile = True

    try:
        parsed = urlsplit(url)
    except Exception:
        codes.append("MALFORMED_URL")
        codes.append("HOSTILE_URL")
        return {
            "parsed_url": url[:128],
            "scheme": "https",
            "host": "invalid",
            "is_hostile": True,
            "reason_codes": sorted(set(codes)),
        }, codes, True

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        codes.append("HOSTILE_URL_SCHEME")
        is_hostile = True
        if scheme in ("javascript", "data", "file", "ftp", "blob", "about", "vbscript", "mailto", "gopher", "ws", "wss"):
            codes.append("HOSTILE_URL")

    if parsed.username or parsed.password:
        codes.append("CREDENTIALS_IN_URL")
        is_hostile = True

    hostname = parsed.hostname
    if not hostname:
        codes.append("MISSING_HOST")
        is_hostile = True
        host_str = "missing"
    else:
        host_str = hostname.lower()
        if host_str in ("localhost", "0.0.0.0") or host_str.endswith((".localhost", ".local", ".internal")):
            codes.append("INTERNAL_NETWORK_TARGET")
            is_hostile = True

        clean_ip = host_str.strip("[]")
        try:
            ip_obj = ipaddress.ip_address(clean_ip)
            if (ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local
                    or ip_obj.is_unspecified or ip_obj.is_multicast or ip_obj.is_reserved):
                codes.append("INTERNAL_OR_RESERVED_TARGET")
                is_hostile = True
        except ValueError:
            pass  # Standard DNS hostname

    try:
        port = parsed.port
        if port is not None and port not in (80, 443):
            codes.append("SUSPICIOUS_PORT")
            is_hostile = True
    except ValueError:
        codes.append("MALFORMED_URL")
        is_hostile = True

    path_lower = parsed.path.lower()
    query_lower = parsed.query.lower()

    if "/../" in path_lower or path_lower.endswith("/..") or path_lower.startswith("../"):
        codes.append("INJECTION_PATTERN_DETECTED")
        is_hostile = True

    if any(p in query_lower or p in path_lower for p in ("<script", "%3cscript", "javascript:", "%00")):
        codes.append("INJECTION_PATTERN_DETECTED")
        is_hostile = True

    if is_hostile:
        if "HOSTILE_URL" not in codes:
            codes.append("HOSTILE_URL")
    else:
        codes.append("URL_SYNTAX_VALID")
        codes.append("URL_OFFLINE_PARSED_ONLY")

    return {
        "parsed_url": parsed.geturl(),
        "scheme": scheme or "https",
        "host": host_str[:256],
        "is_hostile": is_hostile,
        "reason_codes": sorted(set(codes)),
    }, codes, is_hostile


def _evaluate_disclosure(disclosure: PlacementDisclosure | dict[str, Any] | str | None) -> tuple[dict[str, Any], list[str], bool]:
    codes: list[str] = []
    if disclosure is None:
        return {
            "statement": "UNSPECIFIED",
            "mode": "CLEAR_AND_CONSPICUOUS",
            "has_affiliate_notice": False,
            "tags": [],
            "status": "NON_COMPLIANT",
        }, ["MISSING_REQUIRED_DISCLOSURE"], False

    if isinstance(disclosure, PlacementDisclosure):
        statement = disclosure.statement
        mode = disclosure.mode
        tags = list(disclosure.tags)
        declared_notice = disclosure.has_affiliate_notice
    elif isinstance(disclosure, dict):
        statement = str(disclosure.get("statement", ""))
        mode = str(disclosure.get("mode", "CLEAR_AND_CONSPICUOUS"))
        tags = [str(t) for t in disclosure.get("tags", [])]
        declared_notice = disclosure.get("has_affiliate_notice")
    elif isinstance(disclosure, str):
        statement = disclosure
        mode = "CLEAR_AND_CONSPICUOUS"
        tags = [w for w in statement.split() if w.startswith("#")]
        declared_notice = None
    else:
        statement = ""
        mode = "CLEAR_AND_CONSPICUOUS"
        tags = []
        declared_notice = False

    cleaned_statement = statement.strip()
    stmt_lower = cleaned_statement.lower()
    tag_lower = [t.lower() for t in tags]

    notice_found = any(k in stmt_lower for k in _AFFILIATE_KEYWORDS) or any(
        t in _AFFILIATE_TAGS for t in tag_lower
    )

    if declared_notice is not None:
        has_affiliate_notice = bool(declared_notice and notice_found)
    else:
        has_affiliate_notice = bool(notice_found and cleaned_statement)

    if has_affiliate_notice and cleaned_statement:
        status = "COMPLIANT"
        codes.append("REQUIRED_DISCLOSURE_PRESENT")
        is_compliant = True
    else:
        status = "NON_COMPLIANT"
        codes.append("MISSING_REQUIRED_DISCLOSURE")
        is_compliant = False

    valid_tags = [t for t in tags if re.match(r"^#[A-Za-z0-9_]+$", t)]

    return {
        "statement": cleaned_statement or "UNSPECIFIED",
        "mode": mode if mode in ("CLEAR_AND_CONSPICUOUS", "PROMINENT_TEXT", "PLATFORM_DISCLOSURE_LABEL") else "CLEAR_AND_CONSPICUOUS",
        "has_affiliate_notice": has_affiliate_notice,
        "tags": sorted(set(valid_tags)),
        "status": status,
    }, codes, is_compliant


def _evaluate_temporal(
    as_of: str,
    valid_from: str,
    valid_until: str,
    revoked_at: str | None = None,
) -> tuple[dict[str, Any], list[str], str]:
    codes: list[str] = []

    dt_as_of = parse_timestamp(as_of)
    dt_from = parse_timestamp(valid_from)
    dt_until = parse_timestamp(valid_until)
    dt_revoked = parse_timestamp(revoked_at) if revoked_at is not None else None

    if dt_from >= dt_until:
        status = "INVALID_WINDOW"
        codes.append("INVALID_TEMPORAL_WINDOW")
    elif dt_revoked is not None and dt_revoked <= dt_as_of:
        status = "REVOKED"
        codes.append("PLACEMENT_REVOKED")
    elif dt_as_of >= dt_until:
        status = "EXPIRED"
        codes.append("PLACEMENT_EXPIRED")
    elif dt_as_of < dt_from:
        status = "NOT_YET_EFFECTIVE"
        codes.append("PLACEMENT_NOT_YET_EFFECTIVE")
    else:
        status = "ACTIVE"
        codes.append("TEMPORAL_WINDOW_VALID")

    return {
        "as_of": as_of,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "revoked_at": revoked_at,
        "status": status,
    }, codes, status


def _build_placement_evidence(
    *,
    request: PlacementRequest | None = None,
    account_ref: dict[str, str] | str | None = None,
    app_ref: dict[str, str] | str | None = None,
    market: str | None = None,
    surface: str | None = None,
    product_id: str | None = None,
    variant_id: str | None = None,
    link_url: str | None = None,
    temporal: PlacementTemporal | dict[str, Any] | None = None,
    as_of: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    revoked_at: str | None = None,
    disclosure: PlacementDisclosure | dict[str, Any] | str | None = None,
    evidence_refs: Iterable[EvidenceRef | dict[str, Any]] | None = None,
    capability_snapshot: dict[str, Any] | None = None,
    evidence_pack: ProductEvidencePack | dict[str, Any] | None = None,
    offer_observation: dict[str, Any] | None = None,
    source_kind: str = "OFFLINE_EVIDENCE_REPLAY",
) -> dict[str, Any]:
    hold = False
    unknown = False
    all_reason_codes: set[str] = set()

    # 1. Resolve 7 dimensions
    if request is not None:
        acc_raw = request.account_ref
        app_raw = request.app_ref
        market = market or request.market
        surface = surface or request.surface
        product_id = product_id or request.product_id
        variant_id = variant_id or request.variant_id
        link_url = link_url or request.link_url
    else:
        acc_raw = account_ref
        app_raw = app_ref

    _require(acc_raw is not None, "ACCOUNT_REF_REQUIRED")
    _require(link_url is not None, "LINK_URL_REQUIRED")
    _require(bool(market), "MARKET_REQUIRED")
    _require(bool(surface), "SURFACE_REQUIRED")
    _require(bool(product_id), "PRODUCT_ID_REQUIRED")
    _require(bool(variant_id), "VARIANT_ID_REQUIRED")

    norm_account = _normalize_account(acc_raw)
    norm_app = _normalize_app(app_raw)
    norm_market = str(market).upper()
    norm_surface = str(surface)
    norm_product = str(product_id)
    norm_variant = str(variant_id)
    norm_link = str(link_url).strip()

    # Credential reject test
    payload_str = str(norm_account) + str(norm_app) + norm_link
    if _REJECT.search(payload_str):
        all_reason_codes.add("CREDENTIAL_MATERIAL_NOT_ALLOWED")
        hold = True

    # 2. URL evaluation
    url_eval, url_codes, is_hostile = _evaluate_url(norm_link)
    all_reason_codes.update(url_codes)
    if is_hostile:
        hold = True

    # URL query parameter cross-account mismatch check
    try:
        query_params = parse_qs(urlsplit(norm_link).query)
        for param in ("affiliate_id", "aff_id", "creator_id", "partner_id", "account_id"):
            if param in query_params:
                val = query_params[param][0]
                if val != norm_account["id"]:
                    all_reason_codes.add("CROSS_ACCOUNT_MISMATCH")
                    hold = True
    except Exception:
        pass

    # 3. Temporal evaluation
    if temporal is not None:
        if isinstance(temporal, PlacementTemporal):
            t_as_of = temporal.as_of
            t_from = temporal.valid_from
            t_until = temporal.valid_until
            t_revoked = temporal.revoked_at
        else:
            t_as_of = str(temporal.get("as_of", ""))
            t_from = str(temporal.get("valid_from", ""))
            t_until = str(temporal.get("valid_until", ""))
            t_revoked = temporal.get("revoked_at")
    else:
        _require(as_of is not None and valid_from is not None and valid_until is not None, "TEMPORAL_FIELDS_REQUIRED")
        t_as_of = str(as_of)
        t_from = str(valid_from)
        t_until = str(valid_until)
        t_revoked = revoked_at

    temporal_eval, temporal_codes, temporal_status = _evaluate_temporal(
        t_as_of, t_from, t_until, t_revoked
    )
    all_reason_codes.update(temporal_codes)
    if temporal_status != "ACTIVE":
        hold = True

    # 4. Disclosure evaluation
    disclosure_eval, disclosure_codes, is_compliant = _evaluate_disclosure(disclosure)
    all_reason_codes.update(disclosure_codes)
    if not is_compliant:
        hold = True

    # 5. Supporting Evidence & Provenance
    provenance_records: list[dict[str, Any]] = []
    tampered = False

    # Check evidence_refs
    if evidence_refs is not None:
        for ref in evidence_refs:
            if isinstance(ref, EvidenceRef):
                r_id = ref.evidence_id
                r_kind = ref.kind
                r_sha = ref.record_sha256
                r_src = ref.source_sha256
                r_tamper = ref.tamper_checked
                raw = ref.raw_bytes
            elif isinstance(ref, dict):
                r_id = str(ref.get("evidence_id", ""))
                r_kind = str(ref.get("kind", ""))
                r_sha = str(ref.get("record_sha256", ""))
                r_src = ref.get("source_sha256")
                r_tamper = bool(ref.get("tamper_checked", True))
                raw = ref.get("raw_bytes")
            else:
                continue

            if raw is not None:
                calc_sha = sha256_bytes(raw if isinstance(raw, bytes) else canonical_bytes(raw))
                if calc_sha != r_sha:
                    tampered = True
                    r_tamper = True
                    all_reason_codes.add("TAMPERED_HASH")
                    hold = True
                else:
                    r_tamper = True

            provenance_records.append({
                "evidence_id": r_id,
                "kind": r_kind,
                "record_sha256": r_sha,
                "source_sha256": r_src,
                "tamper_checked": r_tamper,
            })

    # Check capability snapshot
    if capability_snapshot is not None:
        snap_acc = capability_snapshot.get("account_ref", {})
        snap_acc_id = snap_acc.get("id") if isinstance(snap_acc, dict) else str(snap_acc)
        if snap_acc_id and snap_acc_id != norm_account["id"]:
            all_reason_codes.add("CROSS_ACCOUNT_MISMATCH")
            hold = True

        snap_app = capability_snapshot.get("app_ref")
        if snap_app and norm_app:
            snap_app_id = snap_app.get("id") if isinstance(snap_app, dict) else str(snap_app)
            if snap_app_id and snap_app_id != norm_app["id"]:
                all_reason_codes.add("APP_MISMATCH")
                hold = True

        snap_market = capability_snapshot.get("market")
        if snap_market and snap_market != norm_market:
            all_reason_codes.add("MARKET_MISMATCH")
            hold = True

        snap_surface = capability_snapshot.get("surface")
        if snap_surface and snap_surface != norm_surface:
            all_reason_codes.add("SURFACE_MISMATCH")
            hold = True

        snap_state = capability_snapshot.get("state")
        if snap_state in ("DENIED", "REVOKED", "EXPIRED"):
            all_reason_codes.add("CAPABILITY_DENIED_OR_REVOKED")
            hold = True
        elif snap_state == "UNKNOWN":
            all_reason_codes.add("CAPABILITY_STATE_UNKNOWN")
            unknown = True

        snap_bytes = canonical_bytes(capability_snapshot)
        snap_hash = sha256_bytes(snap_bytes)
        provenance_records.append({
            "evidence_id": capability_snapshot.get("snapshot_id", "ccs_capability_snapshot"),
            "kind": "A07_CAPABILITY_SNAPSHOT",
            "record_sha256": snap_hash,
            "source_sha256": None,
            "tamper_checked": True,
        })

    # Check evidence pack
    if evidence_pack is not None:
        if hasattr(evidence_pack, "to_dict"):
            ep_dict = evidence_pack.to_dict()
        else:
            ep_dict = evidence_pack

        ep_subject = ep_dict.get("subject", {})
        pack_prod = ep_subject.get("product_id") or ep_dict.get("product_id")
        pack_var = ep_subject.get("variant_id") or ep_dict.get("variant_id")

        if pack_prod and pack_prod != norm_product:
            all_reason_codes.add("PRODUCT_MISMATCH")
            hold = True
        if pack_var and pack_var != norm_variant:
            all_reason_codes.add("VARIANT_MISMATCH")
            hold = True

        if hasattr(evidence_pack, "to_bytes"):
            ep_bytes = evidence_pack.to_bytes()
        else:
            ep_bytes = canonical_bytes(evidence_pack)
        ep_hash = sha256_bytes(ep_bytes)
        provenance_records.append({
            "evidence_id": ep_dict.get("pack_id", "a12-pack-000"),
            "kind": "A12_PRODUCT_EVIDENCE_PACK",
            "record_sha256": ep_hash,
            "source_sha256": None,
            "tamper_checked": True,
        })

    # Check offer observation
    if offer_observation is not None:
        if hasattr(offer_observation, "to_dict"):
            oo_dict = offer_observation.to_dict()
        else:
            oo_dict = offer_observation

        oo_prod = oo_dict.get("product_id")
        oo_var = oo_dict.get("variant_id")
        if oo_prod and oo_prod != norm_product:
            all_reason_codes.add("PRODUCT_MISMATCH")
            hold = True
        if oo_var and oo_var != norm_variant:
            all_reason_codes.add("VARIANT_MISMATCH")
            hold = True

        if hasattr(offer_observation, "to_bytes"):
            oo_bytes = offer_observation.to_bytes()
        else:
            oo_bytes = canonical_bytes(offer_observation)
        oo_hash = sha256_bytes(oo_bytes)
        provenance_records.append({
            "evidence_id": oo_dict.get("offer_id", "a12-offer-000"),
            "kind": "A12_PRODUCT_OFFER_OBSERVATION",
            "record_sha256": oo_hash,
            "source_sha256": None,
            "tamper_checked": True,
        })

    if tampered:
        provenance_status = "TAMPERED"
    elif provenance_records:
        provenance_status = "PROVEN"
        all_reason_codes.add("EVIDENCE_PROVENANCE_VERIFIED")
    else:
        provenance_status = "UNPROVEN"
        all_reason_codes.add("EVIDENCE_UNPROVEN")
        unknown = True

    # 6. Overall Decision
    if hold:
        decision = "HOLD"
    elif unknown:
        decision = "UNKNOWN"
    else:
        decision = "REVIEW"
        all_reason_codes.add("OFFLINE_PLACEMENT_VERIFIED_FOR_REVIEW")

    # Sort reason codes deterministically
    sorted_codes = sorted(all_reason_codes)

    result = {
        "schema_version": "AffiliatePlacementEvidence.v1",
        "reviewer_version": "a13-d06/1.0",
        "scope": "COMMERCIAL_AFFILIATE_PLACEMENT_OFFLINE_EVIDENCE_ONLY",
        "verification_scope": "LOCAL_OFFLINE_EVIDENCE_ONLY_NOT_EXTERNAL_OR_PROVIDER_APPROVAL",
        "placement": {
            "account_ref": norm_account,
            "app_ref": norm_app,
            "market": norm_market,
            "surface": norm_surface,
            "product_id": norm_product,
            "variant_id": norm_variant,
            "link_url": norm_link,
        },
        "temporal": temporal_eval,
        "disclosure": disclosure_eval,
        "url_evaluation": url_eval,
        "evidence_provenance": {
            "source_kind": source_kind if source_kind in (
                "A12_OFFER_OBSERVATION",
                "A12_PRODUCT_PACK_BOUND",
                "A07_CAPABILITY_BOUND",
                "OFFLINE_EVIDENCE_REPLAY",
                "TEST_FIXTURE",
            ) else "OFFLINE_EVIDENCE_REPLAY",
            "evidence_refs": provenance_records,
            "provenance_status": provenance_status,
        },
        "decision": decision,
        "reason_codes": sorted_codes,
        "live_link_verified": False,
        "account_eligibility": "UNKNOWN",
        "link_activation": "UNKNOWN",
        "platform_approval": "UNKNOWN",
        "publication_status": "NOT_ATTEMPTED",
        "sales_claims_allowed": False,
        "earnings_claims_allowed": False,
        "sales_earnings_claims": "NOT_PERMITTED",
    }

    result["record_sha256"] = sha256_bytes(canonical_bytes(result))
    _require(_VALIDATOR.is_valid(result), "PLACEMENT_SCHEMA_INVALID")
    _require(len(canonical_bytes(result)) <= _DOCUMENT_LIMIT, "PLACEMENT_BYTE_LIMIT")
    return result


@dataclass(frozen=True, slots=True, init=False)
class AffiliatePlacementEvidence:
    """Immutable receipt. Restoration replays inputs and checks digest and schema."""

    _encoded: bytes = field(repr=False)

    def __init__(self, **inputs: Any):
        try:
            value = _build_placement_evidence(**inputs)
            object.__setattr__(self, "_encoded", canonical_bytes(value))
        except AffiliatePlacementError:
            raise
        except ProductError as exc:
            raise AffiliatePlacementError("A12_" + exc.code) from exc
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
            raise AffiliatePlacementError("INVALID_PLACEMENT_INPUT") from exc

    @classmethod
    def from_bytes(cls, raw: bytes, **inputs: Any) -> AffiliatePlacementEvidence:
        try:
            _require(type(raw) is bytes and 0 < len(raw) <= _DOCUMENT_LIMIT, "PLACEMENT_BYTE_LIMIT")
            value = parse_json(raw, limit=_DOCUMENT_LIMIT)
            _require(_VALIDATOR.is_valid(value), "PLACEMENT_SCHEMA_INVALID")
            body = {k: v for k, v in value.items() if k != "record_sha256"}
            _require(value["record_sha256"] == sha256_bytes(canonical_bytes(body)), "PLACEMENT_DIGEST_MISMATCH")
            if inputs:
                expected = cls(**inputs)
                _require(canonical_bytes(value) == expected.to_bytes(), "PLACEMENT_REPLAY_MISMATCH")
                return expected
            instance = cls.__new__(cls)
            object.__setattr__(instance, "_encoded", canonical_bytes(value))
            return instance
        except AffiliatePlacementError:
            raise
        except ProductError as exc:
            raise AffiliatePlacementError("PLACEMENT_JSON_INVALID") from exc

    def to_dict(self) -> dict[str, Any]:
        return parse_json(self._encoded, limit=_DOCUMENT_LIMIT)

    def to_bytes(self) -> bytes:
        return self._encoded

    @property
    def decision(self) -> str:
        return self.to_dict()["decision"]

    @property
    def status(self) -> str:
        return self.decision

    @property
    def reason_codes(self) -> list[str]:
        return self.to_dict()["reason_codes"]

    @property
    def placement(self) -> dict[str, Any]:
        return self.to_dict()["placement"]

    @property
    def temporal(self) -> dict[str, Any]:
        return self.to_dict()["temporal"]

    @property
    def disclosure(self) -> dict[str, Any]:
        return self.to_dict()["disclosure"]

    @property
    def url_evaluation(self) -> dict[str, Any]:
        return self.to_dict()["url_evaluation"]

    @property
    def evidence_provenance(self) -> dict[str, Any]:
        return self.to_dict()["evidence_provenance"]

    @property
    def record_sha256(self) -> str:
        return self.to_dict()["record_sha256"]


def evaluate_affiliate_placement(**inputs: Any) -> AffiliatePlacementEvidence:
    """Evaluate offline affiliate placement evidence."""
    return AffiliatePlacementEvidence(**inputs)


def review_affiliate_placement(**inputs: Any) -> AffiliatePlacementEvidence:
    """Review offline affiliate placement evidence."""
    return AffiliatePlacementEvidence(**inputs)
