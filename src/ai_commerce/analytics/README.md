# A09 L01/L02 offline analytics

The CLI validates user-authored inputs, preserves original evidence bytes and
stores observations in an append-only SQLite ledger. It performs no provider
requests. Every record remains `USER_SUPPLIED_NOT_LIVE_VERIFIED`, including an
input declared `USER_PROVIDED_PLATFORM_EXPORT`. Native provider export layouts,
real account access, metric meanings and published/public state are unverified.
Publication receipt matching belongs to the subsequent A09-E02 action.

## Contracts and context

The four published `contracts/*.v1.schema.json` files cover `MeasurementPlan`,
`MetricDefinition`, `PerformanceObservation` and `AnalyticsImport`. Runtime
schemas are packaged as Python data in `schemas.py`; tests check the published
copies. All schema references are local, including outside the repository.
The original `MeasurementPlan.v0.2.schema.json` remains an untouched DRAFT.

A v1 plan retains every named draft field. Its `account_ref` is the A08 object
`{"namespace":"internal_alias","id":"creator"}` or the platform's namespace;
its platform/surface use the actual A08 uppercase enums. This is an explicit new
version, not automatic conversion of flat draft accounts or lowercase platforms.
Required additions include `schema_version`, `surface`, `cohort` (nullable),
`source_status` and `metric_definitions`. Optional draft economics and evidence
metadata remain available. The importer accepts only `source_mode=MANUAL_IMPORT`.

`measurement_window` contains two aware RFC3339 instants, start inclusive and end
exclusive, strictly increasing. `timezone` is mandatory: `UTC`, an explicit
`UTC+07:00`-style offset, or an IANA name available to Python's `zoneinfo` on the
host. A missing timezone database returns `TIMEZONE_UNKNOWN_OR_DATA_UNAVAILABLE`;
there is no fallback or inferred seven-day window. Input strings and offsets
remain unchanged. `denominator` and `cohort` must be explicit even when null.

Each metric definition includes `metric_key`, the exact user-supplied
`provider_name`, `definition_version`, `platform`, `unit`, `definition`,
`denominator`, `cohort`, `comparison_policy=NO_CROSS_PROVIDER_EQUIVALENCE` and
`source_status=USER_SUPPLIED_NOT_LIVE_VERIFIED`, plus
`schema_version=MetricDefinition.v1`. All metric definitions must belong to the
plan platform/cohort. `primary_metric` and `metric_definition_version` select
one definition whose denominator agrees with the plan. Provider labels and
definitions are user declarations; names alone establish no metric equivalence.

This complete synthetic plan pairs with the canonical JSON example below. Save
it as `plan.json`; its one-day UTC window is an explicit fixture choice.

```json
{
  "schema_version": "MeasurementPlan.v1",
  "plan_id": "synthetic-plan", "campaign_ref": "synthetic-campaign",
  "primary_metric": "plays", "denominator": null,
  "metric_definition_version": "synthetic-1",
  "platform": "TIKTOK", "surface": "VIDEO",
  "account_ref": {"namespace": "internal_alias", "id": "synthetic-creator"},
  "measurement_window": ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"],
  "timezone": "UTC", "cohort": null, "source_mode": "MANUAL_IMPORT",
  "attribution_tier": "NOT_AVAILABLE",
  "missingness_rule": "NOT_AVAILABLE requires null; never impute zero",
  "source_status": "USER_SUPPLIED_NOT_LIVE_VERIFIED",
  "metric_definitions": [{
    "schema_version": "MetricDefinition.v1", "metric_key": "plays",
    "provider_name": "Synthetic declared plays", "definition_version": "synthetic-1",
    "platform": "TIKTOK", "unit": "count", "definition": "Synthetic fixture count only",
    "denominator": null, "cohort": null,
    "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE",
    "source_status": "USER_SUPPLIED_NOT_LIVE_VERIFIED"
  }]
}
```

## Manual formats

Select `tiktok.manual.v1`, `youtube.manual.v1`, `facebook.manual.v1` or
`instagram.manual.v1` explicitly. These profiles describe the application's
manual long-form format. They do not claim verified native export layouts or
supply guessed provider metric definitions.

CSV uses UTF-8 (optional BOM), CRLF or LF, standard double-quote escaping, and
an explicit `--dialect comma|semicolon|tab` (comma when omitted). The exact
required headers, in any order, are:

```text
provider_post_id,public_url,metric_key,provider_name,definition_version,status,value
```

There is one metric per row. The metric key/name/version must select an explicit
plan definition. Post ID and URL may be blank for account-level observations;
IDs are strings, preserving leading zeros. URLs, when supplied, must be HTTPS
without embedded credentials. No URL is fetched or treated as publication proof.
Unknown, duplicate or missing headers fail. Repeated post ID or exact URL for
the same metric definition/window fails as ambiguous, as do repeated account
aggregates. Unlike metric definitions remain separate.

`AVAILABLE` requires a finite JSON-syntax number, including zero. `NOT_AVAILABLE`
requires an empty CSV value. Boolean values, formulas in numeric cells, `N/A`,
NaN and infinity fail. Text cells are inert data. There is no CSV/spreadsheet
export, formula execution or expression evaluation.

Canonical JSON is an internal user-authored format, not a provider export:

```json
{"schema_version":"ManualAnalyticsRows.v1","rows":[
  {"provider_post_id":"synthetic-0001","public_url":null,
   "metric_key":"plays","provider_name":"Synthetic declared plays",
   "definition_version":"synthetic-1","status":"AVAILABLE","value":0}
]}
```

The example is synthetic and requires a corresponding explicit plan definition.
JSON rows use the same seven canonical keys; unavailable values must be JSON
null. Duplicate JSON keys, unknown fields and invalid/nonfinite numbers fail.
CSV source rows count logical records including the header (first data row 2),
including when quoted fields span physical lines. JSON rows are one-based array
positions. Provenance records the applicable row semantics, raw hash, mapping
ID/version, adapter version, source kind and verification status.

To adapt a user-owned CSV, `--mapping mapping.json` replaces `--profile`.
The mapping is an object with `profile_id`, `mapping_version`, `platform`,
`adapter_version=manual-analytics.v1`, `native_export_layout_verified=false`,
and `columns` mapping every canonical header above to exactly one explicit
source header. Repeated destination headers and unknown mapping fields fail.
The entire mapping is retained in each import's context. JSON still uses
canonical row keys; the profile supplies explicit platform/provenance metadata.

## CLI

```text
python -m ai_commerce analytics --help
python -m ai_commerce analytics validate --plan plan.json --profile tiktok.manual.v1 --file manual.csv --format csv
python -m ai_commerce analytics import --plan plan.json --profile tiktok.manual.v1 --file manual.csv --format csv --db analytics.sqlite
python -m ai_commerce analytics inspect --db analytics.sqlite --import-id a09-<64-hex-digest>
python -m ai_commerce analytics history --db analytics.sqlite --plan plan.json --limit 20
python -m ai_commerce analytics import --plan plan.json --profile tiktok.manual.v1 --file corrected.csv --format csv --db analytics.sqlite --correction-of a09-<parent-digest>
python -m ai_commerce analytics import --plan plan.json --mapping revised-mapping.json --db analytics.sqlite --reprocess-of a09-<parent-digest>
```

`validate` has no ledger side effect and returns status `VALIDATED`.
`import` returns status `IMPORTED` and a separate `duplicate` flag. Use
`--source-kind SYNTHETIC_FIXTURE` for synthetic input. The default source kind
is `USER_AUTHORED_MANUAL`; declarations never upgrade source verification.
Exit codes: 0 successful operation, 2 argument usage error, 40 invalid input/local
file access, 50 database/integrity failure. Data errors return bounded reason
codes and optional logical row numbers, without echoing source contents.

## Evidence, replay and limits

Raw bytes are SQLite BLOBs addressed by their exact SHA256 (`sha256:<digest>`).
BOMs, delimiters, quoting, whitespace and line endings remain unchanged.
Raw evidence, import records and observations commit in one `BEGIN IMMEDIATE`
transaction. Tables reject updates/deletes; indexes cover exact scope, source
context and lineage. Retries reuse an import only for identical bytes, the full
plan/mapping/parser/source-kind context and the same explicit lineage. Account,
surface, window, timezone, definitions or mapping changes produce distinct IDs.

A correction requires new raw bytes in the parent's exact context and scope.
Reprocessing reads hash-checked bytes from the ledger, requires changed context
in the same scope, and retains source format/kind. Neither operation changes
earlier records. Both retain an explicit `parent_import_id`; retries are
idempotent. `inspect` reopens raw bytes, verifies hashes and deterministic IDs,
replays normalization and compares every persisted observation. `history` does
the same for its bounded page in the plan's exact account/surface/window/timezone/
cohort scope; it includes imports using earlier mappings in that scope.

Limits: 8 MiB raw input, 256 KiB plan/mapping file, 10,000 observations, 4,096
characters per CSV field, 64 KiB logical row (including JSON), at most 100 metric
definitions, history pages of 1..100. Schema strings have additional field-specific
limits. Missing, malformed or oversized input fails before import mutation.
Symlinks, parent symlinks, Windows reparse points, lexical `..`, UNC/device paths,
alternate data streams and unsafe SQLite sidecars are rejected. Foreign databases
are rejected, and inspect/history open an existing ledger read-only.

SQLite triggers/hashes detect ordinary local modification; they are not an
external trust anchor against an administrator rewriting the database. Concurrent
hostile replacement of an entire directory is outside this local offline threat
model. This action adds no A08 receipt writes, E02 match result, dashboard,
cross-platform aggregation, attribution or live analytics assertion.

## Verification observed on 2026-10-29

Canonical source: `d3239e671c205a2940d411ef7488b2b48c0854c2`.
Local environment: Windows 11, Python 3.14.7.

- `python -m pytest tests/test_a09_analytics.py -q`: **121 passed, 2 skipped**,
  exit 0 (2.10 s).
- Registered `project-tests`, loaded unchanged from
  `.github/ef02/verify-profiles.json`: `python -m pytest tests -q`:
  **1025 passed, 5 skipped**, exit 0 (698.22 s pytest time). This includes the
  existing A06/A07/A08 regressions. SHA256 snapshots confirm all authored Python
  and JSON code stayed unchanged throughout this final run.
- Both A09 skips and three existing A04 skips reflect host denial of native
  symlinks/junctions/hardlinks. Reparse-point rejection is also covered by a
  deterministic attribute test; skipped native cases remain unverified here.
- The README's synthetic plan/rows ran through the actual module CLI's
  `validate`, `import`, `inspect` and `history` commands, all exit 0.
- Action allowlist, Python 3.11 syntax parsing, whitespace and original draft
  byte equality checked. Actual runtime tests above used Python 3.14.7.

Ignored local evidence: `.local/a09-l01-l02/project-tests-final.log`,
`project-tests-final.result.json`, `focused-final.result.json`, and the
`synthetic-demo/` command results. A preliminary run made while schemas were
being revised was superseded by the final unchanged-source run above.
No Ubuntu CI, real provider export, live account, publication or real analytics
result was verified by this action. Delivery is a candidate for review; A09-E02
matching and Human acceptance remain subsequent workflow steps.

This scoped executor could not create a local branch because the shared runner
Git metadata is outside its writable roots. The GitHub connector also rejected
tree creation with `MCP tool call requires approval, but approval policy is never`.
Consequently this session leaves a verified worktree/patch for EF02 candidate
branch creation, with no candidate commit, remote branch or merge claimed here.
