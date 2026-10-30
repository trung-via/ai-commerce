# A12-01 — offline listing snapshots v1

This component implements AC01 and the identity primitives of AC02 only. It
reads Human-supplied local files, retains the original bytes, and verifies
normalized content by replay. It does not certify claims, acquire rights,
register corrections, export evidence packs, or change any Creative brief.

```python
from ai_commerce.product import ProductListingSnapshot, read_snapshot

snapshot = read_snapshot(
    "listings.json", root="/operator/snapshots",
    source_ref="human-export-001", source_url="https://example.invalid/listing",
    captured_at="2026-10-30T12:00:00+07:00", synthetic=True,
)
record = snapshot.to_dict()  # detached copy
raw = snapshot.raw_bytes()  # exact original bytes, including BOM/line endings
restored = ProductListingSnapshot.from_bytes(snapshot.to_bytes(), raw=raw)
assert restored.snapshot_id == snapshot.snapshot_id
```

`source_ref`, `source_url`, and per-row `listing_url` are literal metadata. They
are never fetched or interpreted as file paths. The actual input path must be a
regular local file under the explicit `root`. Traversal, UNC/device/ADS paths,
links, junctions, reparse points, oversized files and detected file changes are
rejected. `prepare_bytes()` performs the same import entirely in memory. No
reader function writes a file or uses the wall clock.

## Manual input

JSON uses UTF-8 (optional BOM), with exactly this envelope:

```json
{
  "schema_version": "ManualProductListings.v1",
  "rows": [{
    "marketplace": "synthetic-market",
    "seller_key": "seller-01",
    "listing_key": "listing-01",
    "product_key": null,
    "variant_key": "red",
    "sku": "0001",
    "gtin": "claimed-not-verified",
    "title": "Synthetic example",
    "observed_at": "2026-10-30T11:00:00+07:00",
    "price_amount": 0,
    "currency": "VND",
    "variant_attributes": {"color": "red"}
  }]
}
```

Only `marketplace`, `seller_key`, and `listing_key` are mandatory. Keys are
nonblank strings, preserving case, leading zeros and internal spaces. Unknown
fields in manual input are rejected. Other optional fields are `description`
and `listing_url`. Optional scalar fields may be omitted or explicitly null;
`provided_fields` retains that distinction. `variant_attributes`, when supplied,
is an object of at most 32 nonblank string attributes.

CSV uses comma separators, a header, UTF-8 (optional BOM), LF/CRLF, and RFC4180
quotes. Header names are the same row fields. An empty optional cell means
unknown (or an empty attributes object). Missing columns remain absent from
`provided_fields`. Numeric price cells must use JSON number syntax. An
attributes cell is a quoted JSON object. No spreadsheet formulas are executed.
Duplicate headers, blank records, ragged rows, bare/unclosed quotes and unknown
manual columns fail the whole import.

Price zero is an observed source assertion; absent/null price is `UNKNOWN`.
Numeric price requires a three-letter uppercase currency and must be finite,
nonnegative and at most 1e15. No currency conversion or live/fresh price is
inferred. Numeric underflow to zero is rejected. A missing observation time
remains null even when a price is supplied.
Capture time is an explicit caller-supplied timestamp, distinct from the row's
asserted observation time. RFC3339 timestamps require a known offset (`Z` is
supported), valid calendar dates and at most six fractional digits. Unknown
`-00:00`, leap seconds, UTC overflow, and observations after capture are rejected.
Offsets are preserved in serialization; comparisons use UTC instants.

## Pre-exported input and later adapters

For a local API export such as `{"data":{"items":[{"shop":"s", ...}]}}`,
pass `source_kind="PREEXPORTED_API"`, `json_rows_pointer="/data/items"`, and a
flat map from canonical fields to literal source field names:

```python
field_map = {"marketplace": "market", "seller_key": "shop", "listing_key": "id"}
```

An empty pointer selects a root array. RFC6901 escapes are supported. A map must
include all required fields and cannot map two fields to one source key. CSV
also accepts a field map. Unmapped provider data remains in the raw bytes;
ignored fields never become normalized facts. Pointer/map/options are versioned
with the snapshot and used in provenance replay. There is no connector, remote
discovery, permissive fallback, or provider-specific model dependency.

## Identities, integrity and trust

The three public contracts are `ProductListingSnapshot.v1`, `ProductIdentity.v1`
and `ProductVariant.v1`. `schema()`, `validate_contract()`, `seal_contract()` and
`parse_contract()` belong to this component. Runtime Python schemas mirror the
three top-level JSON schemas and work without a repository checkout or A02
schema registration. All JSON references are local.

Seller ID covers marketplace + seller key; listing ID covers seller ID + listing
key; product ID covers seller ID + asserted product key; variant ID covers
listing ID + asserted variant key. Unknown product/variant IDs remain null.
`canonical_product_id` always remains null. GTIN, SKU, title and URL never drive
deduplication. Product/variant record IDs cover all their own contract fields.
Duplicate listing/variant pairs and conflicting product keys within a listing
are rejected with a logical source row, preserving the caller's original input
for later correction. No automatic merge or correction ledger is implemented.

Raw SHA-256 covers the exact file bytes. `source_snapshot_id` covers raw
hash/size/format plus source metadata (including capture time). It is independent
of projection options. `snapshot_id` covers every normalized field, nested
identity, source metadata, and reader/options version. Content IDs exclude only
their own ID field. Canonical bytes use sorted JSON keys, compact separators,
unescaped UTF-8, no nonfinite numbers and preserved array order, matching A02's
serialization. Formatting/BOM changes therefore change raw/snapshot identity;
repeated import with identical bytes and explicit metadata is reproducible.

The immutable Python snapshot owns both canonical content and raw evidence.
Archive both `to_bytes()` and `raw_bytes()`. Validating or restoring a snapshot
requires exact raw bytes and rebuilds the full projection, not just its hashes.
Identity/variant validation recomputes source-scoped IDs as well as content IDs.
Hashes demonstrate local consistency, not external attestation or authorship.
Caller-supplied metadata cannot be independently authenticated by this reader.

All sources retain `USER_SUPPLIED_NOT_LIVE_VERIFIED`, including a caller's
`declared_verification="INDEPENDENTLY_VERIFIED"`. Synthetic marking is explicit
and included in the IDs, never inferred from a filename. Rights and affiliate
status remain `UNKNOWN`; publication remains `NOT_ATTEMPTED`. No datum grants
commercial media permission or affiliate entitlement.

Limits: 4 MiB raw, 16 MiB canonical document, 2,000 rows, 64 KiB per projected
source row, 65,536 characters per raw scalar, JSON depth 32, 200,000 JSON nodes,
256 keys per object, 64 CSV columns. Contract fields have narrower bounds.
`ProductError.code` and optional `.row` identify bounded failures without
echoing source bytes. CSV rows count logical records including the header;
JSON rows are one-based positions with the exact JSON Pointer also retained.
