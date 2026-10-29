# A10 M02 — comparable trend hypotheses

M02 consumes the actual M01 `SignalLedger.get(import_id)` replay API. It does not
use the unverified `query_signals()` / `list_imports()` shortcuts, write the ledger,
change M01, or integrate the CLI. All new code, schemas, tests and this report stay
inside `src/ai_commerce/opportunity/trends/` as required by the recovery scope.
The component does not require a product, CTA, media format or audience profile.

## Source and recovery boundary

- WorkItem: `work_items/A10-OPPORTUNITY-AUDIENCE-001.json`, M02 only.
- Inspected source: `1d839bbbe8f49c5e7ad0ef2a89f7f89578a0a1fe`.
- Integration parent: `3d97a33792223c97649df7b71041e6083a340192`.
- All 14 integrated M01 blobs were compared with
  `8e27597b7a73bfbd42d9ec45a39fd34576bd8a2d` and matched exactly.
- Human-selected recovery executor: Codex `gpt-6.1-sol`, effort `max`.
- M01 was preserved; the previously verified M01 action was not reimplemented.
  Composite-source regression tests are separate from reexecuting that action.
- Existing #87/#89 outcomes are historical external evidence, not inferred new
  executions. M03–M05 are outside this per-action candidate.

## API and explicit comparison metadata

`build_series(ledger, import_ids=..., topic=..., signal_type=...,
comparison_contexts=...)` replays an explicit bounded selection of imports.
`comparison_contexts` maps each import ID to a caller-supplied
`TrendComparisonContext.v1` record. A missing record produces `UNKNOWN`; it never
invents a metric or infers normalization from a provider name. One context
describes the selected metric within an import; callers should select imports
for one measurement definition. Other topics and signal types are listed as
excluded evidence, rather than blended into the series.

For example, for an existing import of human-counted local observations:

```python
from ai_commerce.opportunity.trends import build_series, evaluate_trend

comparison = {
    "schema_version": "TrendComparisonContext.v1",
    "metric": "manual_mentions",
    "metric_kind": "COUNT",  # COUNT / RATE / RELATIVE_INDEX / UNKNOWN
    "baseline_id": "human-counting-protocol.v1",
    "normalization_id": "raw-count.v1",
    "query_batch": None,
    "relative_scale": None,
    "window_kind": "POINT",  # POINT / INTERVAL / UNKNOWN
    "evidence_refs": ["local://human-comparison-note"],
    "source_status": "USER_SUPPLIED_NOT_LIVE_VERIFIED",
}
series = build_series(
    ledger, import_ids=[import_id], topic="coffee", signal_type="TOPIC",
    comparison_contexts={import_id: comparison},
)
hypothesis = evaluate_trend(series, as_of="2026-10-29T12:00:00Z")
```

Use `CREATIVE_FORMAT` for explicitly labeled format observations. Unknown signal
types remain `UNKNOWN`; they are not silently interpreted as topics or formats.
Rates need a stated denominator. Relative indices need an explicit scale, query
batch, normalization and baseline; a Google Trends relative index cannot become
an absolute search count by changing its label. These metadata are human
declarations, never proof of provider facts, access or statistical calibration.

An exact cohort includes category/topic, source identity/kind/type and M01 scope,
mapping/format/dialect, metric/kind/unit/denominator, query context, normalization,
query batch/scale, baseline, locale/geo/timezone, and window geometry. Window
geometry means exact offsets of both endpoints from `observed_at`, measured in
microseconds; shifting identical daily windows can compare, but different window
lengths, rolling/cumulative geometries or timezones cannot. Original endpoint
strings and all original signal/import fields remain in the series.

`TrendSeries.v1` preserves complete replayed import records, raw/context/scope
hashes, snapshots, rights, corrections/reprocess lineage, original signals, caller
metadata and its hash, cohort membership, unknown reasons and excluded IDs.
`validate_series()` checks the serialized content identity, M01 signal identity,
cohort reconstruction and references. The replay assertion means verified at
build time; a content hash is not a signature or renewed provider verification.

## Direction rule and uncertainty

`evaluate_trend(series, as_of=..., rule=TrendRule(...))` requires an explicit
timezone-aware clock and records it in UTC. There is no wall-clock default.
`median-baseline-delta.v1` is a descriptive, configurable rule:

1. Never combine cohorts. Require at least three numeric observations across
   distinct instants; repeated/corrected/reprocessed samples do not add history.
   Equivalent UTC timestamps count once. Conflicting values at one timestamp
   remain contradictory evidence. For intervals, overlapping windows do not add
   independent observations; statistical independence remains unknown.
2. Take the median of all prior independent values, or the latest explicitly
   configured `baseline_points` (minimum two). Compare the latest value with that
   median. The threshold is `max(absolute_delta, abs(median) * relative_delta)`;
   defaults are `0` and `0.20`. A delta must strictly exceed the threshold for
   `GROWING` or `DROPPING`; equality is `STABLE`.
3. Material adjacent changes in both directions produce `CONTRADICTORY`, retaining
   the IDs of counterevidence. No direction is emitted while any blocker exists.
4. Freshness uses the latest available measurement's window end, or `observed_at` for point
   observations, never its recent collection time. Default `max_age_seconds` is
   seven days. Future observations, collection times or window ends are unknown
   evidence at the supplied clock. Excluding overlapping windows from the
   independent history count does not hide their more recent measurement times.
5. Preserve zero as observed data, missing values as unavailable evidence, and a
   zero baseline's relative change as `null`. Nonfinite derived numbers yield
   `UNKNOWN`, rather than a fabricated number.

All blocking conditions remain visible, including concurrent conditions.
Primary status precedence is `INCOMPARABLE`, `CONTRADICTORY`, `UNKNOWN`, `STALE`,
then `INSUFFICIENT_HISTORY`. Each output records policy, baseline/latest/delta/
threshold, supporting and counterevidence signal IDs, unavailable/unused IDs,
counts, reasons and limitations. Counts on incompatible cohorts do not represent
a combined comparable history. Outputs assert `INFERRED`, not calibrated viral
probability, validated demand or causation. Rights stay user-declared/unknown.

## Verification and delivery

The initial working tree was clean. The actual integration source passed the
unchanged registered command `python -m pytest tests -q -n 12`: **1249 passed,
5 skipped**, exit 0, 361.65 seconds. This is new composite-tree evidence, not a
claim about an older sibling action's execution.

The component's first acceptance run passed **66 tests**. A subsequent review
added window-end freshness, incompatible unit protection, schema consistency and
additional negative cases; **76 tests passed**, exit 0. The first combined run
then passed **1325 tests, 5 skipped**, exit 0. A second bounded review refinement
ensured that overlap exclusion cannot hide the freshest measurement, adding one
regression case. Focused and combined logs explicitly show `created: 12/12 workers`.
There were no failing implementation test runs. Two bounded review refinements
were used; no executor reinvocation or side-effect replay was invoked.

The component-local test location is intentional to respect the directory-only
scope. The registered `project-tests` profile covers the existing `tests/` tree;
it does not discover these additional tests. Run the exact combined regression:

```text
python -m pytest tests src/ai_commerce/opportunity/trends/tests -n 12
```

Run the focused suite with:

```text
python -m pytest src/ai_commerce/opportunity/trends/tests -n 12
```

Observed final results, command/output hashes, source file hashes and scope checks
are recorded in `verification.json`. Raw local verification outputs are retained
as ignored `.log` files in this directory. The implementation tests block sockets,
use local synthetic fixtures, verify database bytes are unchanged by M02 and
exercise the actual M01 raw replay/tamper path. No network, OAuth, scraping, paid
model API, account actions or publishing is part of this component.

Local execution environment: Windows 11, CPython 3.14.7, pytest 8.4.2,
pytest-xdist 3.8.0. Ubuntu execution/CI is **UNKNOWN** in this action; portable code
and fixtures do not establish an unobserved CI result. Candidate publication and
cross-platform verification remain with the existing EF01/EF02 runner. This
executor leaves the detached source HEAD unchanged and does not write a branch,
push, merge, modify the registered profiles or dispatch sibling actions.
