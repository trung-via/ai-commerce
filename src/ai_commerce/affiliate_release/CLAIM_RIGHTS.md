# C09 claims and rights evidence

`review_claim_rights(pack, request=..., ...)` reviews explicitly declared
commercial placements using the actual A12 `ProductEvidencePack` API. It does
not run during creative ideation, composition, generation or rendering. No
platform rules, required hashtag, account decision, link check, external action
or publication permission is introduced.

The immutable result is `ClaimRightsReleaseReview.v1`, with a detached schema
from `claim_rights_review_schema()` and an identical published contract at
`contracts/ClaimRightsReleaseReview.v1.schema.json`.

## Inputs and replay

```python
from ai_commerce.affiliate_release import (
    AssetUsage, ClaimPlacement, ClaimRightsReviewRequest,
    ClaimRightsReleaseReview, review_claim_rights,
)

request = ClaimRightsReviewRequest(
    product_id=pack.subject["product_id"],
    listing_id=pack.subject["listing_id"],
    variant_id=pack.subject["variant_id"],
    release_sha256=release_sha256,
    territory="VN", channel="social",
    as_of="2026-10-30T03:00:00Z",
    use_from="2026-10-30T04:00:00Z",
    use_until="2026-11-01T04:00:00Z",
)
inputs = dict(
    request=request, snapshot=snapshot,
    claims=(ClaimPlacement("caption", exact_statement, "FACTUAL", (claim.claim_id,)),),
    assets=(AssetUsage("image-alias", image_sha256, "IMAGE", ("COMMERCIAL_USE",)),),
    claim_evidence=(claim,), rights_evidence=(rights,),
    asset_bytes={"image-alias": image_bytes},
    rights_status_evidence=(holder_status_json_bytes,),
    # When reviewing rendered media, supply the existing A05 RenderManifest
    # and optionally the C08 MediaReleaseInspection pinned to the same output.
    render_manifest=render_manifest, media_inspection=media_inspection,
)
receipt = review_claim_rights(pack, **inputs)
restored = ClaimRightsReleaseReview.from_bytes(receipt.to_bytes(), pack, **inputs)
```

All refs are inert logical identifiers. This function accepts bytes already
obtained by the caller; it never opens a path, resolves a URL, reads a registry,
retrieves secrets or discovers tools. Asset aliases need not equal A12 source
locators: matching rights requires the exact content hash and media type.
No media or raw report/license bytes are retained in the receipt.

Restoration requires the original inputs and recomputes the complete receipt.
A checksum alone cannot restore or upgrade a result. A12 snapshot, claims,
reports, rights and license instruments are reconstructed through the original
public `from_bytes` APIs. Their exact product/variant subject and pack
projections must agree, including source and listing fields. Missing originals
remain UNKNOWN. A mismatched or altered original raises a bounded error code.
Request identity mismatches produce RESTRICTED with specific reason codes.

`as_of <= use_from < use_until` is required. Times include explicit UTC offsets;
no wall clock, randomness or provider lookup is used. Arrays are sorted for
deterministic replay. Requests and receipts are immutable; dictionary and
schema accessors return detached copies.

## Status meaning

| Status | Meaning within this local review |
| --- | --- |
| VERIFIED_EVIDENCE | The supplied local evidence and exact use replay consistently, or declared nonfactual art is outside factual claim review. |
| UNKNOWN | Necessary original, asset bytes, purpose, current status or usage provenance is absent or insufficient. |
| NEEDS_REVIEW | Seller/source assertion, synthetic evidence, conflict, paraphrase, ambiguous classification, source mismatch or future evidence needs review. |
| RESTRICTED | Exact identity/bytes mismatch, evidenced restriction/revocation, scope violation or use outside the term. |

Aggregation preserves the most restrictive result, in the order RESTRICTED,
NEEDS_REVIEW, UNKNOWN, VERIFIED_EVIDENCE. Reason codes are retained at claim,
asset, purpose and overall level. A grant for one asset or purpose does not
override another applicable restriction. Unused pack assets do not affect this
review. Retained, conflicting rights records are combined conservatively;
C09 does not invent an active revision or discard A12 history.

VERIFIED_EVIDENCE describes local replay, not independent authentication of a
report, issuer, reviewer, copyright holder or real-world claim. In every result,
`source_authenticity` is NOT_ATTESTED; `legal_rights_status`, `platform_approval`,
`account_eligibility` and `link_activation` remain UNKNOWN; publication remains
NOT_ATTEMPTED. A plausible fabricated report cannot become external truth merely
because its bytes and user-supplied reviewer fields are internally consistent.
Synthetic A12 subjects and synthetic status sources cannot establish real
verification. The caller remains responsible for authenticating original
evidence outside this offline component.

## Claims and nonfactual art

Each `ClaimPlacement` identifies a concrete occurrence and its exact statement,
FACTUAL or NONFACTUAL interpretation, and A12 claim IDs. Factual verification
requires a supporting, exact-statement A12 independent review and separate
report bytes. A seller assertion or source observation alone needs review.
Contradicting values, conflicts and counterevidence in the same pack claim family
remain visible even if only a supporting claim was selected. Missing references
remain UNKNOWN; paraphrases require review rather than inheriting verification.

NONFACTUAL declarations can omit evidence and do not become factual product
claims. A NONFACTUAL label linked to factual product evidence needs review.
This is a caller-declared inventory, not an NLP detector or exhaustive review of
all text, imagery or implied claims in a creative asset. The receipt states
`claim_coverage=CALLER_DECLARED_NOT_EXHAUSTIVE`.

## Actual use, rights and revocation

Each `AssetUsage` identifies actual bytes, type and requested commercial/ad
purposes. An exact direct asset can bind directly to `release_sha256`. A
rendered output uses the real A05 `RenderManifest`: its successful output must
match the release, and all source hashes must appear in the asset usages.
Source omission is RESTRICTED; extra unbound assets need review. Every render
source also requires CREATIVE_DERIVATIVE evidence in addition to its commercial
or advertisement use. Silent art adds no compulsory audio-rights requirement.
An optional C08 receipt is revalidated and pinned to the same release hash.
C09 leaves the separate C08 technical verdict for its consumer to assess.
A render manifest is local provenance evidence, not external attestation or a
repeat of A06 full custody verification.

A license label or declared GRANTED flag never grants permission. C09 calls
the existing A12 `assess_rights` only after replaying the separate, exact
holder-issued instrument and explicit local review. It checks each purpose,
literal territory, channel, medium and the entire requested use window against
the evidenced term. A scope's `valid_until` is exclusive: a use may end exactly
there, but may not start there or continue beyond it. Missing purposes and
unreviewed instruments remain UNKNOWN.

A12 has no current revocation field on its license instruments. C09 therefore
accepts separate bounded source bytes using the nested
`ReleaseRightsStatusEvidence.v1` shape in the C09 schema:

```json
{
  "schema_version": "ReleaseRightsStatusEvidence.v1",
  "rights_id": "a12-rights-<exact 64-character digest>",
  "asset_sha256": "<exact 64-character digest>",
  "license_ref": "license-alias",
  "purpose": "COMMERCIAL_USE",
  "state": "NOT_REVOKED",
  "issuer_ref": "holder-alias",
  "source_ref": "holder-observation-alias",
  "observed_at": "2026-10-30T02:00:00Z",
  "effective_at": "2026-10-30T02:00:00Z",
  "valid_until": "2027-01-01T00:00:00Z",
  "synthetic": false
}
```

The source must match the exact rights ID, content, purpose, license and holder
alias. A positive observation must be at or after the original review, known
at `as_of`, and bounded through the whole requested use. It is a local supplied
observation, never an authenticated guarantee about future revocation. Missing,
expired, UNKNOWN, synthetic, future or mismatched observations cannot support
verification. REVOKED/RESTRICTED evidence effective before the end of the use
window prevails over positive notices, even when grant evidence or that purpose
is missing. Future scheduled revocation within the window also restricts use.
There is no implicit reinstatement or live revocation check. A later UNKNOWN
notice cannot be hidden by a positive one.

## Bounds and integration

Limits are 128 claim placements, 64 asset uses, 32 claim refs per placement,
2,000 original claims, 100 original rights records and 32 MiB of original
claim/rights evidence. Actual assets are capped at 256 MiB each and 512 MiB in
total; these resource ceilings are not creative/platform limits. There are at
most 64 status sources, 256 KiB each and 4 MiB total. Receipts are capped at
8 MiB and parsed with A12's strict bounded JSON parser; duplicate JSON keys,
nonfinite values, invalid timestamps, excess complexity and invalid fields are
rejected. Validation uses self-contained schemas without remote references.

A07 account capability decisions and A08 assisted handoff retain their existing
APIs and behavior. C09 supplies claims/rights evidence for a later consumer; it
does not call either action, promote its outcome, or replace their checks.
