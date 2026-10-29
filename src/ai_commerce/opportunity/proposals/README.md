# M04 offline opportunity hypotheses

`propose_opportunities()` consumes the integrated M01, M02 and M03 APIs. It
returns deterministic `OpportunityProposal.v1` dictionaries for creative
exploration. Every result is explicitly `HYPOTHESIS` / `INFERRED`, suggestion
only, rights `UNKNOWN`, and publication `NOT_ATTEMPTED`.

No product, CTA, audience profile or media format is required. A Human idea can
be proposed without any signals or notes:

```python
from ai_commerce.opportunity.proposals import propose_opportunities

proposals = propose_opportunities(
    as_of="2026-10-29T12:00:00Z",
    manual_idea="  A cloud learns to whisper.\r\n  ",
)
proposal = proposals[0]
assert proposal["creative"]["product_ref"] is None
assert proposal["creative"]["cta"] is None
assert proposal["creative"]["media_hint"] is None
```

Manual text, including whitespace and Unicode, remains exact in `manual_input`;
its hash is SHA-256 of that exact string's UTF-8 encoding. This is a text input,
not a new claim about an original file's encoding. Existing M01/M03 raw byte
hashes retain their original meaning. `creative_overrides` accepts only
`objective`, `audience`, `product_ref`, `cta` and `media_hint`; optional fields may
be null. The original manual text/hash remains intact when its editable creative
objective is overridden. Evidence, rights, statuses and action authority cannot
be overridden.

## Actual dependency fan-in

```python
from ai_commerce.opportunity.signals import SignalLedger
from ai_commerce.opportunity.audience import AudienceLedger
from ai_commerce.opportunity.trends import build_series
from ai_commerce.opportunity.proposals import propose_opportunities

# These are existing, explicit local imports and caller-authored comparison
# contexts, using M02's actual TrendComparisonContext.v1 contract.
signals = SignalLedger("signals.sqlite")
series = build_series(
    signals, import_ids=selected_import_ids, topic=selected_topic,
    comparison_contexts=caller_comparison_contexts,
)
proposals = propose_opportunities(
    trend_series=[series],
    audience_insight_ids=selected_insight_ids,
    signal_ledger=signals,
    audience_ledger=AudienceLedger("audience.sqlite"),
    as_of="2026-10-29T12:00:00Z",
)
```

The builder validates each M02 series and then rebuilds it from
`SignalLedger.get()` and its explicit comparison contexts. Full equality is
required: rehashing a forged import or omitting inconvenient points does not
make it evidence. Snapshot declarations must match the actual raw hash. Trend
hypotheses are evaluated again with M02's real rule and the explicit clock.
There is no caller-supplied direction or confidence field to trust.

M03 inputs are immutable insight IDs. `AudienceLedger.get()` replays the original
notes, revisions and exact M01 references. Missing or corrupted evidence fails
the call; it cannot silently become note-only or verified. M01 and M03 are read
only. No journal, account, platform, budget or source module is written.

A trend and an audience statement join only through the same replayed signal
identity. Topic text, provider names or source labels are insufficient. A linked
note's remaining statements stay visible, with `MATCHED_SIGNAL` or
`UNLINKED_CONTEXT` per statement. Unlinked statements cannot supply a supporting
reason. Counterexamples and alternatives remain visible, including note-only
counterexamples. Unmatched insights produce separate audience content hypotheses
with `NO_TREND`; they are not attached to an unrelated trend.

The first eligible caller-authored job, friction or content-context statement can
inform the creative objective. Its wording remains exact in the evidence trace.
Unknown-status, stale, future and unrelated statements do not become the chosen
creative prompt. The proposal is still an interpretation, not an audience fact.
Creative-format signals suggest an optional direction without requiring a media
format. A supplied manual idea always has its own independent proposal and does
not acquire the other proposals' evidence by proximity.

## Evidence, uncertainty and determinism

Each proposal retains series/hypothesis IDs and full-record hashes, audience
record/raw hashes and revision lineage, per-statement provenance/status, and
selected signal receipts with import/raw/context/scope hashes and correction or
reprocess lineage. Receipts preserve available zero, unavailable null, units,
denominators, locale/geo, supplied rights and original timestamps. Free-text source
URLs and metadata are not copied into the proposal; their original records remain
anchored by hashes. `source_refs` is an exact set of locally bound IDs/hash refs.

Reasons explain support, counterevidence, context, limitations and alternatives.
All M02 blockers, reason codes and counterevidence survive. Freshness uses an
explicit timezone-aware `as_of`, the observation/window end and the rule's
`max_age_seconds`. A recent collection time never refreshes old evidence. Note
freshness uses only its optional original `noted_at`; absent time stays `UNKNOWN`.
Future evidence is explicit. Use `trend_rule=TrendRule(...)` to adjust M02's
documented descriptive rule; the complete effective rule stays in the trace.

There is no numeric opportunity score or demand/virality/revenue probability.
Results sort by content ID for reproducibility; order is not a merit ranking.
Duplicate selections are idempotent, and separate revisions remain separate.
Selection counts, total signal replay and canonical input/output sizes are bounded.
Empty evidence yields no invented proposal; empty evidence plus a manual idea
still yields that manual proposal.

`validate_proposal()` checks the schema, content identity and internal references.
It does not authenticate authorship or renew source replay. Retain the source
series and ledgers and rebuild with `propose_opportunities()` when evidence must
be rechecked. The runtime schema is self-contained and mirrored at
`contracts/OpportunityProposal.v1.schema.json`, without changes to shared schema
registries or package configuration. M05 can consume `creative`, `source_refs`
and `proposal_id` through its separately authorized A03 bridge.

Inputs must be deidentified. Strict schemas exclude person/demographic metadata;
contact-pattern scanning rejects common emails, handles, phones and contact links,
including normalized obfuscations, before returning proposals. Accepted text is
never silently redacted. Like M03, this conservative scanner cannot identify every
name or indirect identifier in prose; callers must supply deidentified material.

## Recovery scope and verification

Actual starting HEAD: `7e1f5f22274ea8ae1e238d0126f16ef4fc7f7e5d`, tree
`739cb053e15b166a52cdca9e19a5eacdaab6fd11`. Its parent is the supplied recovery
integration `1d839bbbe8f49c5e7ad0ef2a89f7f89578a0a1fe`; it adds the M02/M03
dependency candidates. The starting worktree was clean. All 297 source-file byte
hashes were captured before editing. M01, other dependencies, A03/A09, shared CLI,
EF01/EF02, workflows, governance and WorkItem retain their exact starting bytes.
Human-selected recovery executor: Codex `gpt-6.1-sol`, effort `max`.

The untouched integrated baseline ran the exact registered command
`python -m pytest tests -q -n 12`: **1317 passed, 5 skipped**, exit 0, 147.04s.
Its saved log shows xdist startup; this baseline record does not independently
enumerate all worker IDs. The five skips are Windows host link-permission limits.

Initial affected verification used:

```text
python -m pytest tests/test_a10_m04.py src/ai_commerce/opportunity/trends/tests/test_m02.py tests/test_a10_m01.py tests/test_a10_m03.py -q
```

It passed **283 tests**, exit 0, 18.52s. A subsequent read-only AC review identified
that an eligible linked audience note should also inform the default creative
objective; the refinement and discriminating tests were added before final
verification. No failing implementation test was observed or discarded.

Final verification evidence is recorded in this component's `verification.json`.
The affected command explicitly includes M02's component-local tests because the
registered root `tests` selection does not discover that directory. Full project
verification uses the registered argv plus `-v` solely to expose xdist worker
creation in the actual log. Raw local commands, outputs, exit codes and source/
scope manifests are retained under ignored `.local/a10-m04/`.

This is the M04 action only. It covers AC06 and the M01/M02/M03 provenance path
through proposals. M05's ContentBrief bridge/demo, Ubuntu CI, remote candidate
publication and main acceptance require their own actual evidence. EF01/EF02's
existing Verify-First, scope reconciliation and publisher checks apply after the
executor returns. This component never commits, pushes or merges.
