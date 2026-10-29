# A09 E02 offline audience observation matching

Date: 2026-10-29. Work item: `A09-AUDIENCE-OBSERVATION-001`, E02 only.
Authorization: Human explicitly requested "kiểm tra fix #80".
Source: `bb056ce7745ef1d7ae62061d145dd5926bf373e0` on
`repair/a09-issue80-e02-lf-normalized-20261029` (the executor checkout is detached).
The initial `git status --porcelain=v1` was empty, before any implementation.
This is the normalized L01/L02 candidate; L01/L02 were not reimplemented or
redispatched. The failed earlier E02 attempt was a worktree/input failure before
its provider invocation, not an E02 implementation result.

## Implementation

`analytics.match_import()` accepts an existing `AnalyticsLedger`, import ID,
existing A08 journal path, and up to 100 explicit publication IDs. It first calls
`AnalyticsLedger.inspect()`: original raw bytes, import/context/lineage hashes and
every normalized observation must pass the existing deterministic replay.
It then calls the actual `PublicationJournal.get(publication_id)`, overriding only
the connection with SQLite `mode=ro`, `query_only`, and a read transaction. It
does not call A08 create/record, initialize tables, create missing databases or
write to either journal. A08 replays its events and checks the original receipt
and visibility evidence BLOB hashes. E02 also checks typed account metadata,
platform/surface compatibility, receipt hash references and the publication ID's
package/idempotency digest.

Automatic binding requires exactly one candidate with an identical post ID,
platform, surface and account object (`namespace` and `id`, case sensitive).
Leading zeroes and namespace distinctions are retained. A checked HTTPS URL is
secondary: URL-only agreement stays PARTIAL, and a conflicting URL or post ID
blocks binding. Multiple exact receipt records remain ambiguous even when their
raw evidence hashes agree. Repeating the same publication ID is deduplicated.
Missing requested publication IDs prevent otherwise exact binding. A same-target
handoff without a receipt is PARTIAL; wrong targets and account aggregates without
post identifiers are UNMATCHED. No caption, date proximity or score binds a post.

Results contain original observations, exact A08 snapshots (including
`publication_id`, `approved_package_sha256`, `account_identity`, `account_ref`,
`provider_post_id`, `receipt_sha256`, state, visibility and verification), per-item
reasons, snapshot SHA256 and a deterministic result digest. A mixed import is
PARTIAL. MATCHED means identity agreement only; it does not assert available
metrics, PUBLIC visibility, provider verification or current publication. A08's
recorded state remains separate, including later removal. AVAILABLE zero remains
zero; NOT_AVAILABLE remains null. No clock-based output or persisted match state
is added, so unchanged evidence and candidate sets yield identical results.

The accepted manual CSV/BOM/delimiter and canonical `ManualAnalyticsRows.v1` JSON
formats and profiles remain those delivered by L01/L02. Provider-native exports
remain unverified. `USER_PROVIDED_PLATFORM_EXPORT` is a user declaration, still
`USER_SUPPLIED_NOT_LIVE_VERIFIED`. Draft v0.2 and published v1 contracts are unchanged.

## Commands

Use this checkout's `src`, especially on runners with another editable install:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m ai_commerce analytics match --help
python scripts/a09_demo.py --output .local/a09-e02/demo
python -m ai_commerce analytics match --db .local/a09-e02/demo/analytics.sqlite --import-id <a09-id> --publication-db .local/a09-e02/demo/publications.sqlite --publication-id <pub-id>
```

On Ubuntu use `export PYTHONPATH="$PWD/src"`, then the same Python commands.
The demo prints its synthetic IDs; repeat `--publication-id` for additional
candidates. It requires already installed ffmpeg/ffprobe, creates a new output
directory, renders real A06 media from a locally authored PNG, exports actual A08
packages, and records explicitly synthetic manual events. It exercises exact
zero, exact unknown, wrong account, missing receipt and duplicate-receipt
ambiguity. There is no real platform post or analytics claim.

The CLI returns 0 for a computed view, including PARTIAL/UNMATCHED; consumers must
read `status` and `match_status`. Input errors return 40, integrity/storage errors
50, and parser usage errors 2. Errors do not echo raw files, accounts or SQL.
Existing analytics `inspect` and `history` supply status without a second API.

## Verification

Local host: Windows 11, Python 3.14.7. The focused tests stub only A06 media
verification when building synthetic unit packages, as the existing A08 tests do;
actual A08 package validation, events, get() and hash replay execute. The demo
separately exercises real A06 rendering and receipt integration.

First focused run: `python -m pytest tests/test_a09_e02.py -q` returned 48 passed,
2 failed in 21.13 s. One tamper fixture was rejected by SQLite's size constraint
before reaching replay; it now corrupts bytes without changing their length.
One help assertion exposed that argparse's subcommand help omitted the parent
help text; the match parser now also supplies its description. These were fixed
within the first internal repair round. Final verification evidence follows.

During verification, a separate SQLite WAL experiment reproduced creation of
`-wal`/`-shm` files with `mode=ro`. The in-progress full run was stopped and
superseded. The second internal repair round adds a header/sidecar check before
the A08 SQLite connection, rejecting WAL-mode or active-sidecar journals without
changing them. Focused tests assert byte/file equality in these cases. No
additional repair round or L01/L02 execution was used.

Observed checks (exit 0 unless noted):

- Final focused command: `python -m pytest tests/test_a09_e02.py -q`:
  **59 passed in 24.38 s** after the WAL repair.
- Dedicated affected regressions:
  `python -m pytest tests/test_a06_e01.py tests/test_a07_capability.py tests/test_a08_distribution.py tests/test_cli.py -q`:
  **60 passed in 68.36 s**. These unchanged modules are also included in the final
  registered project profile after the analytics-only WAL repair.
- `python scripts/a09_demo.py --output .local/a09-e02/demo`: exit 0, real local
  A06 MP4 render/verification and A08 events. Expected statuses were observed:
  exact zero/unknown MATCHED; wrong account UNMATCHED; missing/duplicate receipts
  PARTIAL. The actual module CLI reopened the demo twice, exit 0 both times,
  with identical JSON and result digest.
- Python 3.11 syntax parsing for all authored Python files: PASS (actual runtime
  above remains Python 3.14.7). Candidate LF checks and `git diff --check`: PASS.

Registered `project-tests` is loaded unchanged from
`.github/ef02/verify-profiles.json`: `python -m pytest tests -q`.
Final result: **1084 passed, 5 skipped in 719.90 s**, exit **0** (720.93 s
including subprocess startup/collection). All 117 Python source/test/demo file
hashes were unchanged across this final run. Full-log SHA256:
`cfee659eae7b656f81e3b250730035a8be97a66482fff290ae36c253a0f569bc`.
The five skips are host restrictions: two A04 directory symlink/junction tests,
one A04 hardlink test (`WinError 5`), and two L01/L02 native symlink tests. These
native filesystem checks remain unverified on this restricted host; they are not
E02 failures. All 59 new E02 tests executed without skips. No Ubuntu run is claimed.

Local ignored evidence is under `.local/a09-e02/`: focused/affected/full logs and
their `.result.json` records, `verified-code-sha256.json`, CLI result records, and
the synthetic demo. Python source hashes are compared before/after the full run.
The final `candidate-manifest.json` records the source SHA, exact candidate file
hashes and computed Git tree SHA without creating a Git object or commit. That
tree SHA is a content identity, not a claimed candidate commit SHA.

## Limits and delivery

- Offline, local, user-supplied evidence only. No network/provider analytics,
  OAuth, credentials, scraping, external posting, A11 dashboard or AIOS code.
- Explicit candidate IDs delimit the search. The caller must include every
  plausible publication; an omitted candidate cannot be diagnosed. Unknown
  requested IDs block binding. No full journal discovery is claimed.
- Measurement windows and timezone are preserved; A08 `created_at` is a local
  handoff timestamp, not a verified provider publication time. Matching does not
  certify the analytics period against a provider or infer causality/attribution.
- Each A08 get() reads a consistent SQLite transaction; different candidates are
  separate reads. Repeatability assumes unchanged journals. Coordinated concurrent
  edits or hostile whole-directory replacement are outside this offline model.
- A08 journals must be quiescent, in its default rollback mode, with no SQLite
  journal/WAL/SHM sidecars. WAL and recovery inputs return bounded
  `PUBLICATION_JOURNAL_NOT_QUIESCENT_ROLLBACK` (CLI 50). E02 does not checkpoint,
  change journal modes, ignore pending WAL contents, or recover an active journal.
- Local hashes are not external attestations. A08's package bytes are referenced
  by hash, not reopened from an unavailable original package path; a privileged
  actor rewriting all coherent journal metadata/evidence is not defeated.
- This Windows execution supplies no new Ubuntu CI claim, real export sample,
  provider entitlement or Human acceptance. Tests use portable Python/SQLite,
  explicit UTC offsets and local temporary fixtures suitable for both OSes.
- All new/modified candidate text is UTF-8 LF, including analytics CLI files.
  No existing contract or A06/A07/A08 implementation is changed. Delivery is
  candidate-only; no main commit, publication or merge is authorized here.

The provider leaves Git HEAD at the exact source SHA. The shared runner's Git
metadata is outside this writable checkout. Under the existing EF02 worker
contract, candidate transport runs after provider/verification completion and
assigns the branch and commit SHA from the verified file hashes. This provider
does not create a local commit or claim a transport result before it exists.
