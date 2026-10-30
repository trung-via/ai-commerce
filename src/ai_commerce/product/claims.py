"""Immutable, source-backed product claims and explicit local reviewer proof.

Seller/API statements cannot grant verification. Independent status needs a
separate report's exact bytes and a deliberate, claim-scoped review. This
records that review; it does not authenticate the report, author or reviewer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ai_commerce.core.provenance import sha256_bytes

from .core import (canonical_bytes, content_id, digest, parse_json, parse_timestamp,
                   require, validate_contract)
from .evidence import (bounded_records, common_body, raw_payload, validate_payload,
                       verify_source)
from .evidence_schemas import CLAIM_INPUT, CLAIM_STATES
from .reader import ProductListingSnapshot
from .schemas import MAX_RAW_BYTES

_ORIGIN_STATUS = {"SELLER": "SELLER_ASSERTED", "OBSERVER": "SOURCE_OBSERVED",
                  "ILLUSTRATION": "ILLUSTRATIVE", "UNKNOWN": "UNKNOWN", "CONFLICT": "CONFLICT"}
INDEPENDENT_STATUS = CLAIM_STATES[2]


def _project(payload: dict, captured_at: str) -> dict:
    validate_payload(payload, CLAIM_INPUT, "INVALID_CLAIM_SOURCE")
    claim = {key: value for key, value in payload.items() if key != "schema_version"}
    claim.setdefault("conflict_refs", [])
    if claim["observed_at"] is not None:
        require(parse_timestamp(claim["observed_at"]) <= parse_timestamp(captured_at),
                "OBSERVED_AFTER_CAPTURE")
    if claim["origin"] == "UNKNOWN":
        require(claim["value"] is None and claim["role"] == "INCONCLUSIVE", "INVALID_UNKNOWN_CLAIM")
    elif claim["origin"] != "CONFLICT":
        require(claim["value"] is not None, "CLAIM_VALUE_REQUIRED")
    require((len(claim["conflict_refs"]) >= 2) if claim["origin"] == "CONFLICT"
            else not claim["conflict_refs"], "INVALID_CLAIM_CONFLICT_REFS")
    return claim


def claim_review_digest(value: dict) -> str:
    """Bind a review to this exact subject, raw evidence, statement and value."""
    require(type(value) is dict and all(key in value for key in ("subject", "source", "claim")),
            "INVALID_REVIEW_SUBJECT")
    return digest({key: value[key] for key in ("subject", "source", "claim")})


def _verify_proof(value: dict, proof_raw: bytes | None, snapshot) -> None:
    proof = value["reviewer_proof"]
    require(type(proof_raw) is bytes and 0 < len(proof_raw) <= MAX_RAW_BYTES
            and bool(proof_raw.strip()), "INDEPENDENT_EVIDENCE_REQUIRED")
    proof_hash = sha256_bytes(proof_raw)
    require(proof["evidence_sha256"] == proof_hash
            and proof["evidence_ref_sha256"] == "sha256:" + proof_hash
            and proof["evidence_size_bytes"] == len(proof_raw), "REVIEW_PROVENANCE_MISMATCH")
    require(proof["claim_sha256"] == claim_review_digest(value), "REVIEW_SCOPE_MISMATCH")
    require(not value["subject"]["synthetic"]
            and value["claim"]["origin"] in ("SELLER", "OBSERVER"), "CLAIM_NOT_VERIFIABLE")
    # A source export or its listing bytes cannot serve as its own independent report.
    require(proof_hash not in (value["source"]["raw_sha256"], value["subject"]["snapshot_raw_sha256"]),
            "INDEPENDENT_EVIDENCE_NOT_SEPARATE")
    row = snapshot.to_dict()["listings"][value["subject"]["ordinal"] - 1]
    seller_refs = {row["identity"]["seller_id"], row["identity"]["asserted_keys"]["seller"]}
    require(proof["reviewer_ref"] not in seller_refs and proof["producer_ref"] not in seller_refs,
            "SELLER_REVIEW_NOT_INDEPENDENT")
    require(parse_timestamp(proof["reviewed_at"]) >= parse_timestamp(value["source"]["captured_at"]),
            "REVIEW_BEFORE_EVIDENCE")


def _verify_claim(value: dict, *, raw: bytes | None, snapshot, proof_raw: bytes | None) -> None:
    payload = verify_source(value, raw, snapshot)
    expected = _project(payload, value["source"]["captured_at"])
    require(canonical_bytes(value["claim"]) == canonical_bytes(expected), "CLAIM_REPLAY_MISMATCH")
    if value["status"] == INDEPENDENT_STATUS:
        _verify_proof(value, proof_raw, snapshot)
    else:
        require(value["status"] == _ORIGIN_STATUS[expected["origin"]], "CLAIM_STATUS_UPGRADE")
        require(proof_raw is None, "UNEXPECTED_REVIEW_EVIDENCE")


@dataclass(frozen=True, slots=True, init=False)
class ProductClaimEvidence:
    _encoded: bytes = field(repr=False)
    _raw_evidence: bytes = field(repr=False)
    _snapshot: ProductListingSnapshot = field(repr=False)
    _proof_raw: bytes | None = field(repr=False)

    def __init__(self, value: dict, *, raw: bytes, snapshot: ProductListingSnapshot,
                 proof_raw: bytes | None = None):
        checked = validate_contract("ProductClaimEvidence", value, raw=raw, snapshot=snapshot,
                                    proof_raw=proof_raw)
        object.__setattr__(self, "_encoded", canonical_bytes(checked))
        object.__setattr__(self, "_raw_evidence", raw)
        object.__setattr__(self, "_snapshot", snapshot)
        object.__setattr__(self, "_proof_raw", proof_raw)

    @classmethod
    def from_bytes(cls, encoded: bytes, *, raw: bytes, snapshot: ProductListingSnapshot,
                   proof_raw: bytes | None = None) -> ProductClaimEvidence:
        return cls(parse_json(encoded), raw=raw, snapshot=snapshot, proof_raw=proof_raw)

    def to_dict(self) -> dict:
        return parse_json(self._encoded)

    def to_bytes(self) -> bytes:
        return self._encoded

    def raw_bytes(self) -> bytes:
        return self._raw_evidence

    def proof_bytes(self) -> bytes | None:
        return self._proof_raw

    @property
    def claim_id(self) -> str:
        return self.to_dict()["claim_id"]


def build_claim(snapshot: ProductListingSnapshot, *, ordinal: int, raw: bytes,
                source_ref: str, captured_at: str, source_kind: str = "HUMAN_SUPPLIED",
                pointer: str = "") -> ProductClaimEvidence:
    """Transcribe a ProductClaimSource.v1 object; no verification inference."""
    body = common_body("ProductClaimEvidence", snapshot, ordinal, raw, source_ref=source_ref,
                       captured_at=captured_at, source_kind=source_kind, pointer=pointer)
    claim = _project(raw_payload(raw, pointer), captured_at)
    body.update(claim=claim, status=_ORIGIN_STATUS[claim["origin"]], reviewer_proof=None)
    return ProductClaimEvidence({**body, "claim_id": content_id("ProductClaimEvidence", body)},
                                raw=raw, snapshot=snapshot)


def review_claim(claim: ProductClaimEvidence, *, proof_raw: bytes, reviewer_ref: str,
                 producer_ref: str, method: str, reviewed_at: str, evidence_ref: str,
                 evidence_kind: str) -> ProductClaimEvidence:
    """Explicit Human review of separate supporting evidence, never auto-called.

    All provenance stays user-supplied. Report authenticity and review quality
    require the actual reviewer; a local digest is not external attestation.
    The prior claim object and its original raw bytes remain unchanged.
    """
    require(type(claim) is ProductClaimEvidence, "INVALID_EVIDENCE_RECORD")
    require(type(proof_raw) is bytes and 0 < len(proof_raw) <= MAX_RAW_BYTES
            and bool(proof_raw.strip()), "INDEPENDENT_EVIDENCE_REQUIRED")
    body = claim.to_dict()
    body.pop("claim_id")
    proof_hash = sha256_bytes(proof_raw)
    body.update(status=INDEPENDENT_STATUS, reviewer_proof={
        "reviewer_ref": reviewer_ref, "producer_ref": producer_ref, "method": method,
        "reviewed_at": reviewed_at, "evidence_ref": evidence_ref, "evidence_kind": evidence_kind,
        "evidence_sha256": proof_hash, "evidence_ref_sha256": "sha256:" + proof_hash,
        "evidence_size_bytes": len(proof_raw), "claim_sha256": claim_review_digest(body),
        "conclusion": "SUPPORTS", "verification_scope": "EXPLICIT_REVIEW_OF_LOCAL_EVIDENCE_ONLY",
    })
    return ProductClaimEvidence({**body, "claim_id": content_id("ProductClaimEvidence", body)},
                                raw=claim.raw_bytes(), snapshot=claim._snapshot, proof_raw=proof_raw)


def assess_claims(claims: list[ProductClaimEvidence] | tuple[ProductClaimEvidence, ...]) -> dict:
    """Retain counterevidence and contradictions; select no authoritative truth."""
    groups = {}
    for item in bounded_records(claims, ProductClaimEvidence):
        value = item.to_dict()
        subject, claim = value["subject"], value["claim"]
        key = (subject["listing_id"], subject["variant_id"] or "", claim["category"], claim["claim_key"])
        groups.setdefault(key, []).append(value)
    result = []
    for key, values in sorted(groups.items()):
        factual = [value for value in values if value["status"] not in ("UNKNOWN", "ILLUSTRATIVE")]
        signatures = {canonical_bytes({"value": item["claim"]["value"], "unit": item["claim"]["unit"]})
                      for item in factual}
        roles = {item["claim"]["role"] for item in factual}
        conflict = (len(signatures) > 1 or {"SUPPORTING", "COUNTEREVIDENCE"} <= roles
                    or any(item["status"] == "CONFLICT" for item in factual))
        result.append({"listing_id": key[0], "variant_id": key[1] or None,
                       "category": key[2], "claim_key": key[3],
                       "status": "CONFLICT" if conflict else "NO_CONFLICT_DETECTED",
                       "claim_ids": sorted(item["claim_id"] for item in values),
                       "counterevidence_ids": sorted(item["claim_id"] for item in values
                                                     if item["claim"]["role"] == "COUNTEREVIDENCE"),
                       "evidence": values})
    return {"schema_version": "ProductClaimAssessment.v1", "inference": "ADVISORY_ONLY",
            "groups": result}
