# A14 L04 offline attribution evidence

`build_attribution_evidence(raw_bytes, algorithm="UNSPECIFIED")` consumes only
`AffiliateAttributionSource.v1`, a deliberately narrow manual/export projection.
It does not parse native provider exports or fetch links. All references must be
nonpersonal business aliases; do not supply customer identifiers, personal data,
credentials, cookies, IP addresses or device information. Unknown fields are
rejected. There are no URLs, free text, money, receipts, contact or tracking fields.

The source explicitly supplies provider, platform, account namespace/ID, app,
market, surface, UTC window `[start, end)`, optional cohort, idempotency key,
source kind, upstream hash pins and events. `schema --source` prints the complete
input contract. All dates must be UTC `Z`; no timezone or window is inferred.
The projection must be prepared by the caller without personal data. An opaque
alias cannot prove that the caller obtained its source lawfully or truthfully.

| Tier | Required support | Scope |
| --- | --- | --- |
| A | Supplied order→click→link→creative IDs, unique bindings, ordered timestamps | Creative association in supplied bytes |
| B | Explicit provider-report reference with link and creative hash | Provider-reported creative association |
| C | Explicit cohort report matching the source cohort | Cohort only |
| D | Directional input or incomplete/noncausal join | Unknown attribution |

`FIRST_TOUCH`, `LAST_TOUCH`, `LINEAR`, `PROVIDER_REPORTED` and `UNSPECIFIED`
are declarations of the source model. This module executes no touch allocation
model. Changing the declaration never upgrades evidence strength. A deterministic
join is not proof of a real purchase, causality or payment. C/D cannot become
verified per-video profit; every tier explicitly leaves that claim false.

`reported_count` distinguishes `AVAILABLE` (including integer zero), `UNKNOWN`
and `NOT_AVAILABLE` (both require null). It is a reported order count, never
money or eligible earnings. A source order row permits only 0/1 or null; provider
and cohort reports permit aggregate counts. Directional input cannot supply an
available order count. `reported_order_status` independently preserves `UNKNOWN`,
`NOT_AVAILABLE`, `PENDING`, `REFUNDED`, `REVOKED` and `SETTLED`. Link revocation
is a different field. No state transition, netting, payout or settled contribution
is computed. A reported `SETTLED` remains unverified supplied data.

`AffiliateAttributionEvidence.from_bytes` validates, reopens the retained exact
UTF-8 source (including BOM/whitespace), checks SHA-256 and recomputes every
result, scoped event hash, evidence ID and guardrail. The raw source SHA differs
when serialization changes; the canonical source SHA can remain equal. Results
are sorted by event reference; identical duplicate event rows are deduplicated.
Hashes identify bytes, not issuer authority. Resealing a modified derived result
does not pass replay. Resealing a fully rewritten source is a new supplied claim;
an expected evidence ID or source SHA pins it to a previously reviewed artifact.

`AttributionLedger.append(evidence)` returns `Admission(evidence_id, duplicate)`.
Exact retries are no-ops. A reused key with different bytes/model/source kind in
the exact scope fails `IDEMPOTENCY_KEY_COLLISION`. Identifiers with inconsistent
bindings fail explicit `EVENT/CLICK/LINK/CREATIVE/ORDER/PROVIDER_REPORT_ID_COLLISION`
errors. Binding checks span overlapping windows in the same provider, platform,
account, app, market and surface; IDs from other namespaces are never joined.
Within a snapshot, multiple distinct mapping/click/order/report rows for the same identifier
are ambiguous and rejected even if they appear equivalent.

Corrections use `build_attribution_evidence(new_raw, algorithm=parent_algorithm,
correction_of=parent)`. They require the same full scope/source kind/model, a
new key and changed events. A correction is a complete replacement snapshot,
so callers must retain unchanged supporting rows. Append requires the actual
parent in history. A second distinct child fails `CONFLICTING_CORRECTION`;
missing parents fail closed. All validation finishes before history changes.
Prior bytes remain retrievable with `get(id)`. `active()` returns current
snapshots, never additive totals. `to_bytes` / `from_bytes` serialize/replay the
entire history; the caller owns local persistence and serialized access. Limits:
256 KiB source, 256 events, 32 upstream refs, 2 MiB evidence, 128 history records,
16 MiB history. Malformed/duplicate-key/nonfinite JSON fails with bounded codes.

Consumer boundaries read on the pinned source:

- A06 `E01Manifest.v1` and delivery hashes describe rendered bytes.
- A07 capability decisions remain recorded offline eligibility evidence.
- A08 handoff packages remain `NOT_ATTEMPTED`; publication journal truth stays
  in A08. A handoff or public post cannot establish an order.
- A09 observations retain explicit metric definition, account and window;
  `AVAILABLE=0` differs from unavailable data. Its receipt match is not attribution.
- A12 product/variant/offer/rights evidence cannot establish affiliate entitlement.
- A13 release and placement packs remain advisory manual-review artifacts.

`upstream_refs` holds opaque component/reference/raw-hash pins only. This L04
module does not open, validate or promote them. Their verification status is
explicitly `HASH_PINS_ONLY_NOT_OPENED`; their presence never upgrades a tier.
E04 orchestration and A16 settlement/reconciliation remain separate work.
Actual orders are never asserted as verified; entitlement, revenue, payout and
live link activation remain `UNKNOWN`, publication/visibility `NOT_ASSESSED`.

CLI (stdin/stdout only; no root CLI or existing consumer changes):

```text
python -m ai_commerce.affiliate_attribution schema --source
python -m ai_commerce.affiliate_attribution evaluate --algorithm LAST_TOUCH
python -m ai_commerce.affiliate_attribution verify --expected-id a14-attribution-<digest>
```

Evaluate/verify read bounded stdin; schema prints to stdout. Usage errors exit 2;
invalid evidence exits 40 with a reason code on stderr. The CLI has no correction
or persistence writes; use the Python history API for explicit correction lineage.
