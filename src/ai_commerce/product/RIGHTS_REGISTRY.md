# A12-03 local rights and evidence registry

P05/AC05–AC06 extend the inherited A12-01/02 records. Import the new APIs from
`ai_commerce.product.rights` and `ai_commerce.product.registry`. Existing
snapshot, identity, claim, offer and Creative APIs keep their behavior.

`build_rights(snapshot, ordinal=..., raw=..., source_ref=..., captured_at=...)`
transcribes `ProductAssetRightsSource.v1` JSON. The source has an `asset` object
and one to three `permissions`, with distinct purposes `COMMERCIAL_USE`,
`ADVERTISEMENT`, and `CREATIVE_DERIVATIVE`. Omitted purposes remain UNKNOWN.
The source may be wrapped in pre-exported JSON via an explicit JSON Pointer;
the complete original source bytes, including unmapped fields, remain evidence.

An asset specifies `asset_ref`, `content_sha256`, `media_type`, `origin`,
`origin_ref`, `copyright_holder_ref`, `claimant_ref`, and `license_label`.
Unknown origin/holder/claimant/labels are explicit. The manifest adds the raw
source hash and a `sha256:` content reference. The content hash is source
asserted: no URL, media path or license label is fetched or authenticated.

Each permission specifies `purpose`, `declared_status`, `license_ref`, and
nullable `scope`. Scope contains explicit `territories`, `media_types`,
`channels`, `valid_from` and `valid_until`. End time is exclusive. WORLDWIDE
must be explicit and cannot be mixed with country codes. A two-letter uppercase
territory is a caller's literal scope code, not geographic entitlement
verification. There are no wildcard channels or implicit/perpetual terms.

Imported UNKNOWN and RESTRICTED declarations retain those decisions. Even an
imported GRANTED declaration has UNKNOWN effective permission. A URL, CC label,
public-domain claim, seller statement or source hash does not grant reuse.

The narrow local exception is a deliberate `review_rights()` call for one
explicitly declared GRANTED purpose on a non-synthetic, identified-origin
record. It requires separate exact UTF-8 JSON `ProductAssetLicenseEvidence.v1`
bytes, reviewer alias, method, review time and evidence reference. The
instrument must identify the copyright holder as its issuer and authority,
exact asset hash, license reference, purpose, matching complete scope, issue
time and `ALLOWS_EXACT_SCOPED_USE` conclusion. Review is bound to the exact
source, product subject, asset and permission digest. A different reviewer
alias is required. Delegated authority and legal exceptions are unsupported.

This records an explicit review of supplied evidence; it does not authenticate
the holder, instrument, reviewer or legal permission. Source verification stays
`USER_SUPPLIED_NOT_LIVE_VERIFIED`. Manifest-wide rights and affiliate status
stay UNKNOWN and publication stays NOT_ATTEMPTED. A new review returns a new
content-addressed record and preserves the original raw declarations.

`assess_rights()` requires a concrete purpose, territory, medium, channel and
explicit `as_of`. A reviewed grant is usable only in the exact scope/term and
with matching local `asset_raw` bytes. Absent bytes remain UNKNOWN; a hash
mismatch or out-of-scope use is RESTRICTED. Actual media bytes are neither
retained nor fetched. A derivative review does not grant commercial/ad use;
assess each applicable purpose separately. Assess the current event in a
stream's history when later corrections or restrictions have been appended.
Historical grants remain evidence of the historical review, not current state.

## Local journal

```python
from ai_commerce.product.registry import ProductEvidenceRegistry

registry = ProductEvidenceRegistry("product.sqlite", root=existing_local_directory)
first = registry.append(
    rights, stream_key="red-image-rights", recorded_at="2026-10-30T07:00:00Z",
)["event"]
next_event = registry.append(
    reviewed_rights, stream_key="red-image-rights",
    recorded_at="2026-10-30T08:00:00Z",
    parent_event_id=first.event_id, operation="REVISION",
    actor_ref="operator-alias", reason_ref="license-review-1",
)["event"]
assert registry.get(first.event_id).evidence() == rights
registry.integrity_check()

copy = ProductEvidenceRegistry("replayed.sqlite", root=existing_local_directory)
copy.replay(registry.events())  # up to 64 events per atomic batch
assert all(item["duplicate"] for item in copy.replay(registry.events()))
```

Construction performs no IO. `initialize()` creates an empty journal; reads
never create it. Root/parents must already exist. The journal accepts validated
snapshots, claims, offers and rights; identities/variants remain inside their
source snapshots. It stores exact JSON/CSV raw bytes once by hash and canonical
versioned event payloads. Restoring records replays the raw source, snapshot,
and any reviewed claim/license evidence. It never imports or opens A11/A09.

Event identity covers record, complete provenance, stream, revision, explicit
timestamp, operation, parent and aliases. Initial events use OBSERVATION;
REVISION, CORRECTION and COUNTEREVIDENCE require a current parent, reason and
actor aliases. Subject checks prevent cross-merchant/variant/type corrections.
COUNTEREVIDENCE requires an explicit negative claim or restricted rights.
Corrections do not erase old claims or raw data. Two concurrent competing
revisions cannot fork a stream. Exact old retries remain idempotent even after
later revisions have been added.

SQLite uses bounded BEGIN IMMEDIATE transactions, FULL synchronization, foreign
keys, unique identities/revisions, ordered inserts, mutation-denying triggers
and a connection authorizer. The complete schema, every blob hash, normalized
record replay, row index, revision lineage and event hash chain are checked
before append/read/replay. Any failed batch rolls back its new events and raw
blobs together. The chain detects local damage, not complete privileged
rewriting or trailing truncation without an externally retained checkpoint.

Persistence rejects obvious personal/contact data, personal filesystem paths,
secret fields and common token formats, including unmapped raw fields and
Unicode/percent-encoded forms. It never redacts accepted raw data. Callers must
provide non-personal aliases and clean evidence; this is not a general semantic
PII classifier. Narrow exceptions are typed product identifiers (including
GTIN/SKU), timestamps, hashes and public credential-free HTTP(S) metadata URLs
without query/fragment. Binary media, PDF/report attachments and arbitrary
documents are excluded. A12-02's existing in-memory report API remains valid;
its journal evidence must be clean bounded JSON. No secret/PII fixtures are
persisted on rejection.

Bounds: 256 KiB rights source/license evidence, 4 MiB other raw evidence,
16 MiB event, 64 events/32 MiB replay batch, 2,048 events and 64 MiB database.
Existing JSON complexity/field bounds also apply. Local file guards reject
URLs, UNC/device/traversal paths, links/junctions and hardlinks for the database
and SQLite sidecars. This is not an OS sandbox against privileged path swaps.
Public failures use bounded `ProductError.code`; SQLite/paths/source contents
are not echoed. No network connector, paid model, external storage, Creative
mutation, affiliate eligibility, price freshness promotion or publication is
implemented by this action. ProductEvidencePack/CLI belong to A12-04.
