# A12 — offline product evidence foundation v1

The A12-01 layer implements AC01 and the identity primitives of AC02. A12-02
adds the identity history, claim evidence and offer observations described
below (AC02–AC04). Both retain original bytes and check normalized content by
replay. A12-03 adds purpose-scoped rights evidence and the independent local
append-only registry described in [RIGHTS_REGISTRY.md](RIGHTS_REGISTRY.md).
Evidence packs remain for A12-04. No operation changes a Creative brief or
authenticates legal permission.

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
for later correction. IdentityHistory below retains corrections across valid
snapshots; no automatic merge or persistent correction ledger is implemented.

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

## A12-02 identity history and corrections

Import `IdentityHistory` from `ai_commerce.product.history`. Marketplace is
the literal source namespace, including case. Existing seller/listing/product/
variant ID algorithms and all three A12-01 schemas remain unchanged. An asserted
product key never identifies a product across sellers. GTIN and SKU remain
attributes; changing either changes the variant record ID, not the stable
variant ID. Unknown product/variant keys remain null.

```python
from ai_commerce.product.history import IdentityHistory

history = IdentityHistory().append(snapshot)
assert history.append(snapshot) is history
# corrected_snapshot is another validated A12-01 snapshot with its own raw bytes.
history = history.append(
    corrected_snapshot, correction_of=snapshot.snapshot_id,
    reason="Correct seller export", actor_ref="operator-1",
)
assert history.snapshots()[0].raw_bytes() == snapshot.raw_bytes()
report = history.to_dict()
```

History is an immutable in-memory value, capped at 64 snapshots, 2,000 combined
rows and 16 MiB of combined raw/normalized input, plus the existing JSON output
complexity/size limits. Rejection is atomic. Exact replay requires the same
revision metadata; different metadata is `REPLAY_METADATA_CONFLICT`. Corrections
require a known, uncorrected parent, the same source ref/kind/synthetic marking
and the same set of source-scoped listings. A correction cannot reassign a
merchant. `recorded_at` defaults to explicit capture time, never wall-clock time.
Both parent and corrected snapshots remain accessible with exact original raw.

`ProductIdentityHistory.v1` and `ProductIdentityRevision.v1` are versioned Python
read models for the later registry, not new persistence or A02 contracts.
Different asserted product keys within a listing, or conflicting SKU/GTIN/
attribute values for an exact variant, produce conflict records with every
source pin. Null/absent assertions do not contradict known values. Corrections
remain visible as historical conflicts; no canonical winner is selected.
The A12-03 registry can separately persist explicit snapshot revisions while
this existing history API remains in memory.

## A12-02 claim evidence

Import `build_claim`, `ProductClaimEvidence`, `review_claim`, and `assess_claims`
from `ai_commerce.product.claims`. A local raw JSON `ProductClaimSource.v1`
object has these fields:

```json
{
  "schema_version": "ProductClaimSource.v1",
  "claim_key": "material",
  "category": "COMPOSITION",
  "statement": "Seller asserts steel",
  "value": "steel",
  "unit": null,
  "origin": "SELLER",
  "role": "SUPPORTING",
  "observed_at": "2026-10-30T04:00:00Z"
}
```

```python
from ai_commerce.product.claims import build_claim

claim = build_claim(
    snapshot, ordinal=1, raw=claim_source_bytes,
    source_ref="seller-export-1", captured_at="2026-10-30T05:00:00Z",
)
assert claim.to_dict()["status"] == "SELLER_ASSERTED"
```

Categories are ATTRIBUTE, PERFORMANCE, SAFETY, COMPOSITION, ORIGIN,
COMPATIBILITY, WARRANTY and OTHER. Scalar value/unit and the original statement
are preserved. Source origin maps to SELLER_ASSERTED, SOURCE_OBSERVED,
ILLUSTRATIVE, UNKNOWN or CONFLICT. UNKNOWN requires a null value and INCONCLUSIVE
role; other ordinary claims require a value (including false or numeric zero).
SUPPORTING, COUNTEREVIDENCE and INCONCLUSIVE roles stay explicit. Optional
`conflict_refs` (2–32 distinct claim IDs) on a CONFLICT origin faithfully retain
a source-declared conflict; these references do not prove that evidence exists.
`assess_claims()` separately detects incompatible factual values/units and
supporting/counterevidence, retaining all complete records. Illustrations and
unknowns do not manufacture factual contradictions or corroboration.

The status INDEPENDENTLY_VERIFIED_ONLY_WITH_REAL_EVIDENCE is available only
through an explicit scoped review. `review_claim()` requires separate report
bytes (up to 4 MiB), reviewer/producer refs, review time, method, report ref and
one of INDEPENDENT_TEST_REPORT, INDEPENDENT_CERTIFICATE or
INDEPENDENT_MEASUREMENT. Hash/size and a digest of this exact subject, source
and claim bind the review. The report cannot be the seller statement/listing
bytes, and the seller cannot be the declared reviewer/producer. Synthetic,
illustrative and unknown claims cannot acquire that status. The review cannot
precede evidence capture. Restoring the record also requires the report bytes.

This records a Human's review of actual local evidence; it cannot authenticate
the report, reviewer identity or quality of their review. Source status always
remains USER_SUPPLIED_NOT_LIVE_VERIFIED. A scrape/API response, source's
"verified" label or a digest alone never creates independent verification.
The reviewed record has a new content ID; the original claim/raw stays intact.

## A12-02 time-bound offer observations

Import `build_offer`, `ProductOfferObservation`, `decimal_amount`, and
`assess_offers` from `ai_commerce.product.offers`. The local raw payload is:

```json
{
  "schema_version": "ProductOfferSource.v1",
  "observed_at": "2026-10-30T04:00:00Z",
  "expires_at": "2026-10-30T06:00:00Z",
  "price_amount": "0.00",
  "currency": "VND",
  "stock_available": false,
  "commission_rate": "0.10"
}
```

Only schema_version and observed_at are required. Optional expires_at must
follow observation time; observation cannot follow capture. An offer requires
an explicit variant key in the pinned A12-01 snapshot, even if SKU/GTIN exists.
Other optional fields are commission_amount and commission_currency. Stock is
boolean or null. Commission rate is a decimal fraction from 0 through 1.
Amounts are nonnegative decimal strings up to 1e15 and eight fractional digits;
numeric JSON prices, floats, exponent strings, negative zero, whitespace,
nonfinite values and rounding are rejected. `decimal_amount()` also accepts a
bounded Decimal and normalizes without relying on process Decimal precision.
Currency is exactly three uppercase ASCII letters and required for any known
monetary amount, including zero. No currency registry lookup/conversion occurs.

Normalized amount strings remove trailing fractional zeros, while raw retains
the exact lexical source. MISSING, NULL and VALUE presence are separate;
`"0"`, false stock and zero commission are known values. Commission always
remains NOT_ELIGIBILITY_VERIFIED. A12-01's original numeric price API is
unchanged; it is not silently reused as precise offer evidence.

```python
from ai_commerce.product.offers import build_offer, assess_offers

offer = build_offer(
    snapshot, ordinal=1, raw=offer_source_bytes,
    source_ref="offer-export-1", captured_at="2026-10-30T05:00:00Z",
)
report = assess_offers(
    [offer], as_of="2026-10-30T05:00:00Z", max_age_seconds=3600,
)
```

Assessment takes an explicit timezone-aware as_of and integer freshness limit
(0–366 days). Expiry is exclusive; age equal to the limit is still recent.
Future observations are NOT_YET_OBSERVED; expired/too-old offers are STALE and
retained. RECENT_OBSERVATION never means live/current/eligible. Overlapping
recent observations with different known prices/currencies, stock or commission
values are CONFLICT. No latest-wins selection, imputed zero or fiat conversion
is used. Exact replay does not create extra supporting evidence. Assessment
order is deterministic and bounded to 2,000 input records/16 MiB serialized.

## New contracts and local adapter seam

ProductClaimEvidence.v1 and ProductOfferObservation.v1 are self-contained
2020-12 contracts, with runtime definitions mirroring the two top-level JSON
schemas. Generic product schema/content-ID/parse/validate helpers support them
without changing A02 registration or top-level `product.__init__` exports.
New contract validation additionally requires `raw=` and `snapshot=`; reviewed
claims require `proof_raw=`. Rehashing a forged value cannot bypass raw replay.
Each immutable object retains normalized bytes, exact raw and the pinned
snapshot; `to_dict()` returns a detached copy. Archive the snapshot and its raw
alongside the new record/raw/report bytes for restoration.

Both builders accept a local RFC6901 pointer (at most 16 steps) into a larger
pre-exported JSON file and `source_kind="PREEXPORTED_API"`. Unmapped bytes stay
in raw evidence. Source refs/URLs are literal metadata. No file, URL, provider,
model or account is opened by these functions. A future authorized connector
can supply bytes at this boundary without changing evidence semantics.
