"""Read complete, explicitly pinned A09 imports via its public atomic inspect API.

One snapshot is one import, never a union of independent database transactions.
All normalized observations and their source are replay-verified by A09 before
the immutable A11 value is returned. A11 never opens a write connection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ai_commerce.analytics.ledger import AnalyticsLedger
from ai_commerce.analytics.model import (
    AnalyticsError, digest as a09_digest, metric_identity, scope,
    validate_contract as validate_a09, validate_mapping, validate_plan,
)
from ai_commerce.analytics.schemas import VERIFICATION
from ai_commerce.core.provenance import canonical_bytes

from .core import LearningError, content_id, digest, parse_contract, require, validate_contract
from .schemas import MAX_OBSERVATIONS, READER_VERSION


@dataclass(frozen=True, slots=True)
class SnapshotPin:
    """Expected A09 context and full ordered observation set, supplied by caller."""

    import_id: str
    context_sha256: str
    raw_sha256: str
    observation_ids: tuple[str, ...]

    def __post_init__(self):
        for value, pattern in ((self.import_id, r"a09-[a-f0-9]{64}"),
                               (self.context_sha256, r"[a-f0-9]{64}"),
                               (self.raw_sha256, r"[a-f0-9]{64}")):
            require(type(value) is str and re.fullmatch(pattern, value) is not None, "INVALID_SNAPSHOT_PIN")
        require(type(self.observation_ids) is tuple
                and 1 <= len(self.observation_ids) <= MAX_OBSERVATIONS, "INVALID_OBSERVATION_IDS")
        require(all(type(value) is str and re.fullmatch(r"obs-[a-f0-9]{64}", value) is not None
                    for value in self.observation_ids), "INVALID_OBSERVATION_IDS")
        require(len(set(self.observation_ids)) == len(self.observation_ids), "DUPLICATE_OBSERVATION_ID")

    def to_dict(self) -> dict:
        return {"import_id": self.import_id, "context_sha256": self.context_sha256,
                "raw_sha256": self.raw_sha256, "observation_ids": list(self.observation_ids)}


def _validate_snapshot(value: dict) -> None:
    """Internal consistency of an archived snapshot; raw replay occurs at read."""
    record, observations, pin = value["import"], value["observations"], value["pin"]
    validate_a09("AnalyticsImport", record)
    plan = validate_plan(record["plan"])
    mapping = validate_mapping(record["mapping"])
    context = {key: record[key] for key in ("plan", "mapping", "format", "dialect", "source_kind")}
    require(record["status"] == "IMPORTED" and plan["source_mode"] == "MANUAL_IMPORT",
            "A09_IMPORT_STATUS_OR_MODE_MISMATCH")
    require(record["context_sha256"] == a09_digest(context)
            and record["scope_sha256"] == a09_digest(scope(plan))
            and record["raw_ref"] == "sha256:" + record["raw_sha256"]
            and record["import_id"] == "a09-" + a09_digest({
                "raw_sha256": record["raw_sha256"], "context_sha256": record["context_sha256"],
                "lineage": record["lineage"]}), "A09_IMPORT_IDENTITY_MISMATCH")
    require(all(record[key] == pin[key] for key in ("import_id", "context_sha256", "raw_sha256")),
            "SNAPSHOT_CONTEXT_MISMATCH")
    require(len(observations) == record["observation_count"], "SNAPSHOT_OBSERVATION_COUNT_MISMATCH")
    ids = [item["observation_id"] for item in observations]
    require(len(set(ids)) == len(ids), "DUPLICATE_OBSERVATION_ID")
    require(ids == pin["observation_ids"], "SNAPSHOT_OBSERVATION_IDS_MISMATCH")
    metrics = {metric_identity(metric): metric for metric in plan["metric_definitions"]}
    for ordinal, observation in enumerate(observations, 1):
        validate_a09("PerformanceObservation", observation)
        require(observation["observation_id"] == "obs-" + a09_digest({
            key: item for key, item in observation.items() if key != "observation_id"
        }), "A09_OBSERVATION_IDENTITY_MISMATCH")
        require(observation["import_id"] == record["import_id"]
                and scope(observation) == scope(plan), "A09_OBSERVATION_CONTEXT_MISMATCH")
        metric = observation["metric"]
        require(metrics.get(metric_identity(metric)) == metric, "A09_METRIC_DEFINITION_MISMATCH")
        source = {
            "raw_sha256": record["raw_sha256"],
            "source_row": ordinal + (record["format"] == "csv"),
            "source_row_semantics": "CSV_LOGICAL_RECORD_INCLUDING_HEADER" if record["format"] == "csv"
            else "JSON_ROWS_ONE_BASED",
            "profile_id": mapping["profile_id"], "mapping_version": mapping["mapping_version"],
            "adapter_version": mapping["adapter_version"], "source_kind": record["source_kind"],
            "source_status": VERIFICATION,
        }
        require(observation["source"] == source, "A09_OBSERVATION_PROVENANCE_MISMATCH")


@dataclass(frozen=True, slots=True, init=False)
class ObservationSnapshot:
    """Immutable canonical bytes; dictionary access returns detached copies."""

    _raw: bytes

    def __init__(self, value: dict):
        checked = validate_contract("ObservationSnapshot", value)
        object.__setattr__(self, "_raw", canonical_bytes(checked))

    @classmethod
    def from_bytes(cls, raw: bytes) -> ObservationSnapshot:
        """Validate saved content, without claiming a fresh A09/raw replay."""
        checked = parse_contract("ObservationSnapshot", raw)
        result = object.__new__(cls)
        object.__setattr__(result, "_raw", canonical_bytes(checked))
        return result

    def to_dict(self) -> dict:
        # Bytes are owned and were fully validated at construction.
        from ai_commerce.analytics.model import parse_json
        return parse_json(self._raw)

    def to_bytes(self) -> bytes:
        return self._raw

    @property
    def snapshot_id(self) -> str:
        return self.to_dict()["snapshot_id"]

    @property
    def import_id(self) -> str:
        return self.to_dict()["import"]["import_id"]

    def snapshot_ref(self) -> dict:
        value = self.to_dict()
        return {"snapshot_id": value["snapshot_id"],
                **{key: value["import"][key] for key in ("import_id", "context_sha256", "raw_sha256")}}

    def observation_ref(self, observation_id: str) -> dict:
        require(type(observation_id) is str and re.fullmatch(r"obs-[a-f0-9]{64}", observation_id) is not None,
                "INVALID_OBSERVATION_ID")
        for observation in self.to_dict()["observations"]:
            if observation["observation_id"] == observation_id:
                return {**self.snapshot_ref(), "observation_id": observation_id,
                        "observation_sha256": digest(observation),
                        "metric_definition_sha256": digest(observation["metric"])}
        raise LearningError("OBSERVATION_NOT_IN_SNAPSHOT")


def read_snapshot(path, *, pin: SnapshotPin) -> ObservationSnapshot:
    """Read one full import or raise; no partial snapshot, discovery or writes.

    A09 inspect() uses mode=ro, query_only and a single read transaction covering
    import, raw BLOB, replay, lineage and all observations. There is deliberately
    no separate raw_bytes() read that could mix database transaction snapshots.
    """
    require(type(pin) is SnapshotPin, "SNAPSHOT_PIN_REQUIRED")
    try:
        source = AnalyticsLedger(path).inspect(pin.import_id)
        record = source["import"]
        require(all(record[key] == getattr(pin, key)
                    for key in ("import_id", "context_sha256", "raw_sha256")), "SNAPSHOT_CONTEXT_MISMATCH")
        require([item["observation_id"] for item in source["observations"]] == list(pin.observation_ids),
                "SNAPSHOT_OBSERVATION_IDS_MISMATCH")
        value = {
            "schema_version": "ObservationSnapshot.v1", "reader_version": READER_VERSION,
            "source_status": VERIFICATION, "verification_scope": "LOCAL_HASH_AND_A09_REPLAY_ONLY",
            "pin": pin.to_dict(), "import": record, "observations": source["observations"],
        }
        return ObservationSnapshot({**value, "snapshot_id": content_id("ObservationSnapshot", value)})
    except LearningError:
        raise
    except AnalyticsError as exc:
        raise LearningError("A09_READ_OR_VALIDATION_FAILED") from exc
    except (OSError, ValueError, TypeError, UnicodeError, KeyError, OverflowError, RecursionError) as exc:
        raise LearningError("A09_READ_FAILED") from exc
