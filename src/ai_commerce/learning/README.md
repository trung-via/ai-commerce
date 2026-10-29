# A11-01 foundation API

This package provides offline contracts and read-only A09 snapshots for L03/L06.
It implements no calculations, learning database, CLI, publication operation or
creative decision. A11-02 and A11-03 can depend independently on this API.

```python
from ai_commerce.learning import SnapshotPin, read_snapshot

# Values come from an explicitly selected A09 import, never implicit "latest".
pin = SnapshotPin(
    import_id=accepted_import_id,
    context_sha256=accepted_context_sha256,
    raw_sha256=accepted_source_sha256,
    observation_ids=tuple(accepted_observation_ids),
)
snapshot = read_snapshot(existing_a09_database, pin=pin)
snapshot.to_bytes()                         # deterministic canonical JSON
snapshot.to_dict()                          # detached, caller-owned dictionary
snapshot.snapshot_id                        # a11-snapshot-<SHA256>
snapshot.snapshot_ref()                     # immutable source/context identity
snapshot.observation_ref(observation_id)    # exact observation and metric digests
```

One snapshot contains **one complete import**, preserving A09 source order. There
is no observation filtering, multi-import transaction claim, implicit discovery,
or partial return. Reports can reference multiple independently verified
snapshots, but must evaluate compatibility themselves. Pins require every ID;
duplicates, reordering, missing or unexpected observations are errors.

`read_snapshot` calls the actual A09 `AnalyticsLedger.inspect()` exactly once.
A09 opens an existing ledger with `mode=ro`, `query_only` and one read transaction;
it verifies raw BLOB bytes, import identities and payload digests, replays the
versioned manual adapter, and checks every stored observation. A separate
`raw_bytes()` transaction is unnecessary. A11 calls A09's actual plan, mapping,
import and observation validators as well. No A09/A08 schema or journal is changed.

The context hash binds the full A09 plan, mapping, format, dialect and declared
source kind. The snapshot retains those values, import/correction/reprocess
lineage, normalized metric definitions and versions, exact platform/surface,
account namespace and ID, cohort (including explicit null), timezone spelling,
and both original measurement-window strings. Window instants are start
inclusive and end exclusive (`[start,end)`); A09 validates offset-aware timestamps
and strict start-before-end. A11 performs no timezone conversion, calendar
rounding, default window, account aliasing, or equivalence by metric name.
Timezone syntax/data availability follow A09, including UTC and fixed offsets.

All observations, including `SYNTHETIC_FIXTURE`, remain
`USER_SUPPLIED_NOT_LIVE_VERIFIED`. Hash checking and local A09 replay detect
modification relative to the supplied pins; they provide no external anchor,
provider attestation, account entitlement, publication or conversion evidence.
Rewriting data and all unanchored hashes cannot be detected cryptographically.
Verification covers the selected import and its A09 replay/lineage checks, not an
audit of unrelated database contents or SQLite metadata.

`ObservationSnapshot(value)` and `ObservationSnapshot.from_bytes(raw)` validate
saved content, identities and internal provenance. They do not reopen A09 or
repeat the original raw-byte replay. A consumer requiring fresh source evidence
must call `read_snapshot` with its independently retained pin. Snapshot objects
hold immutable canonical bytes; modifying a returned dictionary cannot change
their identities or references. No filesystem path, local capture time or A09
storage sequence number contributes to snapshot identity.

The seven versioned JSON Schema 2020-12 contracts are published in `contracts/`.
`schema(name)` returns the same schema from package-owned Python definitions, so
installed distributions need no repository lookup or packaging change. All
`$ref`s are local; existing A09 definitions are reused intact. A02's loader and
existing schemas are untouched. All fields are required except `as_of` in a
PerformanceReport and `report_ref` in LearningEvidence. Nullable required fields
retain explicit unknowns instead of being omitted.

`seal_contract(name, body)` adds the contract's content ID and validates it;
`validate_contract(name, value)` validates and returns a detached dictionary;
`parse_contract(name, bytes)` also rejects duplicate JSON keys and nonfinite
numbers. The ID is its type prefix plus SHA256 of the repository's canonical
UTF-8 JSON (sorted keys, compact separators, no NaN), excluding only that ID.
These functions do not produce reports or persist learning. For downstream
contracts they check shape and content identity; A11-02/03 must resolve referenced
snapshots/reports/hypotheses, check arithmetic and compatibility, and enforce
revision chains and evidence roles in their owning runtimes.

The contracts distinguish `AVAILABLE` (including numeric zero), `NOT_AVAILABLE`,
`INSUFFICIENT_DATA` and `NOT_COMPARABLE`. A non-available measurement requires null
and an explicit reason. Definitions retain unit and denominator meaning; result
quantities expose nullable numerator and denominator separately. Comparisons are
descriptive and retain `NO_CROSS_PROVIDER_EQUIVALENCE`. Confounders carry a code,
description, declared/observed/unknown status, and exact observation references.
LearningEvidence explicitly distinguishes supporting, counterevidence and
inconclusive roles. Records have advisory status/uncertainty and explicit
counterevidence references. A stable `learning_key` identifies a logical history;
content IDs identify immutable records, while revision IDs, sequence and previous
revision references identify corrections without modifying earlier payloads.
The foundation defines these contracts, without implementing that history.

Validation is bounded to A09's 8 MiB raw source/10,000 observations and A11's
32 MiB normalized document, JSON depth 32 and 2,000,000 nodes. Documents with
excessive normalization expansion can be rejected even when their raw import is
valid A09. Strings are bounded before serialization and schema evaluation.
File safety, malformed IDs/timezones, adapter/version mismatch and corrupt
SQLite failures are rejected with bounded `LearningError.code` values. The
original A09 reason remains available through the exception cause, without
echoing source payloads in A11's error message.
