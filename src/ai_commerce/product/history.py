"""Bounded immutable identity history over authoritative A12-01 raw snapshots.

This in-memory seam is suitable for the later A12 registry. It writes no files,
never chooses a canonical merchant/product and never discards superseded raw.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .core import canonical_bytes, digest, parse_timestamp, require
from .evidence import _subject_from_document
from .reader import ProductListingSnapshot
from .schemas import MAX_DOCUMENT_BYTES, MAX_ROWS

MAX_HISTORY_SNAPSHOTS = 64


def _text(value, code):
    require(type(value) is str and 0 < len(value) <= 2048 and bool(value.strip()), code)
    canonical_bytes(value)


@dataclass(frozen=True, slots=True, init=False)
class IdentityHistory:
    _entries: tuple = field(repr=False)

    def __init__(self):
        object.__setattr__(self, "_entries", ())

    def append(self, snapshot: ProductListingSnapshot, *, correction_of: str | None = None,
               reason: str | None = None, actor_ref: str | None = None,
               recorded_at: str | None = None) -> IdentityHistory:
        require(type(snapshot) is ProductListingSnapshot, "LISTING_SNAPSHOT_REQUIRED")
        value = snapshot.to_dict()
        recorded_at = value["source"]["captured_at"] if recorded_at is None else recorded_at
        recorded = parse_timestamp(recorded_at)
        require(recorded >= parse_timestamp(value["source"]["captured_at"]), "REVISION_BEFORE_CAPTURE")
        if correction_of is None:
            require(reason is None and actor_ref is None, "CORRECTION_PARENT_REQUIRED")
        else:
            _text(reason, "CORRECTION_REASON_REQUIRED")
            _text(actor_ref, "CORRECTION_ACTOR_REQUIRED")
            _text(correction_of, "CORRECTION_PARENT_REQUIRED")
        revision = {"schema_version": "ProductIdentityRevision.v1", "snapshot_id": value["snapshot_id"],
                    "operation": "OBSERVATION" if correction_of is None else "CORRECTION",
                    "correction_of": correction_of, "reason": reason, "actor_ref": actor_ref,
                    "recorded_at": recorded_at}
        revision["revision_id"] = "a12-identity-revision-" + digest(revision)
        for prior, metadata in self._entries:
            if prior.snapshot_id == snapshot.snapshot_id:
                require(metadata == revision and prior.raw_bytes() == snapshot.raw_bytes(),
                        "REPLAY_METADATA_CONFLICT")
                return self
        if correction_of is not None:
            parent = next(((prior, metadata) for prior, metadata in self._entries
                           if prior.snapshot_id == correction_of), None)
            require(parent is not None, "CORRECTION_PARENT_NOT_FOUND")
            require(not any(metadata["correction_of"] == correction_of for _, metadata in self._entries),
                    "CORRECTION_PARENT_NOT_CURRENT")
            previous, parent_revision = parent
            previous_value = previous.to_dict()
            require(recorded >= parse_timestamp(parent_revision["recorded_at"]), "CORRECTION_BEFORE_PARENT")
            require(all(value["source"][key] == previous_value["source"][key]
                        for key in ("source_ref", "source_kind", "synthetic")), "CORRECTION_SOURCE_MISMATCH")
            listing_ids = lambda doc: {row["identity"]["listing_id"] for row in doc["listings"]}
            require(listing_ids(value) == listing_ids(previous_value), "CORRECTION_SUBJECT_MISMATCH")
        entries = (*self._entries, (snapshot, revision))
        require(len(entries) <= MAX_HISTORY_SNAPSHOTS
                and sum(len(item.to_dict()["listings"]) for item, _ in entries) <= MAX_ROWS,
                "IDENTITY_HISTORY_LIMIT")
        require(sum(len(item.raw_bytes()) + len(item.to_bytes()) for item, _ in entries) <= MAX_DOCUMENT_BYTES,
                "IDENTITY_HISTORY_SIZE_LIMIT")
        result = object.__new__(IdentityHistory)
        object.__setattr__(result, "_entries", entries)
        result.to_dict()  # Reject an over-complex projection atomically, before accepting the revision.
        return result

    def snapshots(self) -> tuple[ProductListingSnapshot, ...]:
        """Original immutable snapshots, including all exact prior raw evidence."""
        return tuple(snapshot for snapshot, _ in self._entries)

    def to_dict(self) -> dict:
        observations, revisions = [], []
        listing_groups, variant_groups = {}, {}
        for snapshot, metadata in self._entries:
            revisions.append(dict(metadata))
            value = snapshot.to_dict()
            for row in value["listings"]:
                pin = _subject_from_document(value, row["ordinal"])
                observation = {"subject": pin, "source": value["source"],
                               "identity": row["identity"], "variant": row["variant"]}
                observations.append(observation)
                listing_groups.setdefault(pin["listing_id"], []).append(observation)
                if pin["variant_id"] is not None:
                    variant_groups.setdefault(pin["variant_id"], []).append(observation)
        conflicts = []
        for kind, groups in (("LISTING", listing_groups), ("VARIANT", variant_groups)):
            for subject_id, items in sorted(groups.items()):
                fields = {}
                for item in items:
                    if kind == "LISTING":
                        pairs = [("asserted_product_key", item["identity"]["asserted_keys"]["product"])]
                    else:
                        variant = item["variant"]
                        pairs = [(key, variant[key]) for key in ("asserted_sku", "asserted_gtin")]
                        pairs.extend(("attributes." + key, value) for key, value in variant["attributes"].items())
                    for key, value in pairs:
                        if value is not None:
                            fields.setdefault(key, set()).add(value)
                conflicting = sorted(key for key, values in fields.items() if len(values) > 1)
                if conflicting:
                    refs = [item["subject"] for item in items]
                    conflict = {"kind": kind, "subject_id": subject_id, "status": "CONFLICT",
                                "conflicting_fields": conflicting, "source_refs": refs}
                    conflict["conflict_id"] = "a12-identity-conflict-" + digest(conflict)
                    conflicts.append(conflict)
        result = {"schema_version": "ProductIdentityHistory.v1", "source_status": "USER_SUPPLIED_NOT_LIVE_VERIFIED",
                  "canonical_resolution": "NOT_ATTEMPTED", "revisions": revisions,
                  "observations": observations, "conflicts": conflicts}
        canonical_bytes(result)
        return result
