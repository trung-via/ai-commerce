# A11 offline advisory example

All checked-in inputs and outputs are synthetic. They do not describe an actual
account, creative result, platform verification, causal effect or earnings.

From a checkout with the existing Python dependencies installed, on PowerShell:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
python scripts/a11_demo.py --output examples/a11/local-demo
python -m ai_commerce learning snapshot --db examples/a11/local-demo/observations-a09.sqlite --pin examples/a11/local-demo/observations-pin.json
python -m ai_commerce learning report --snapshot examples/a11/local-demo/observations-snapshot.json
python -m ai_commerce learning compare --left examples/a11/local-demo/zero-report.json --right examples/a11/local-demo/observations-report.json
python -m ai_commerce learning inspect --db examples/a11/local-demo/learning.sqlite --learning-key synthetic-opener --revision 2
python -m ai_commerce learning export --db examples/a11/local-demo/learning.sqlite --output examples/a11/local-demo/second-export.json
python -m ai_commerce learning reload --file examples/a11/local-demo/second-export.json --expected-id a11-advisory-883700e4f19ed13cc63ab385b465ea2b876ac51a04f74710af7bd4dbfe5cb6ed
```

Use a new directory for each demo. On Ubuntu set `PYTHONPATH="$PWD/src"` and run
the same Python commands; no Ubuntu execution is asserted by these instructions.
The demo explicitly bootstraps fresh synthetic A09 databases using the existing
A09 API. Every subsequent A11 snapshot/read/export leaves those databases intact.
SQLite files are local ignored outputs; the portable JSON files under `synthetic/`
are the reviewable inputs, snapshots, reports, revisions, comparisons and archive.

To author a history explicitly rather than using the demo, `learning append`
requires a JSON object with exactly `hypothesis`, `evidence`, `record`, `revision`;
each value is the existing sealed A11 v1 contract (evidence is an array). Supply
each source with `--snapshot` and each referenced report with `--report`:

```powershell
python -m ai_commerce learning append --db examples/a11/local-demo/another-learning.sqlite --file examples/a11/synthetic/revision-1.json --snapshot examples/a11/synthetic/observations-snapshot.json --report examples/a11/synthetic/observations-report.json
```

Repeat with revision 2 then 3 to preserve the supporting observation, the zero
counterexample and the missing observation. Exact retries are idempotent. Roles,
prose, hypothesis and status are explicitly authored; A11 does not select or edit
a creative concept or turn a lesson into a creative gate.

The public Python surface is additive:

```python
from ai_commerce.learning import LearningRegistry, LearningAdvisory, export_advisory

advisory = export_advisory(LearningRegistry("examples/a11/local-demo/learning.sqlite"))
saved_id = advisory.export_id
reloaded = LearningAdvisory.from_bytes(advisory.to_bytes(), expected_id=saved_id)
```

`LearningAdvisory.v1` is a complete history, not a filtered result or database
import. Its `advisory_schema()` validates the strict envelope; reload additionally
validates every inner contract, evidence/source reference, report arithmetic,
revision lineage and event hash. `export` reads one transaction and retains all
counterevidence, uncertainty, confounders, exact metric definitions, cohort,
timezone, measurement windows, mapping/adapter versions and source hashes.
`inspect` can query a key or revision without changing the history. Unknown keys
return an empty history; a missing requested revision is an error.

ObservationSnapshot is a local source archive and can include source identifiers.
The Brain/Creative advisory export instead uses the registry's hashed account
alias and minimal source facts. It excludes source rows, post IDs, URLs and full
snapshots. Report proofs retain the original report reference and an explicitly
redacted body; the original report ID cannot be recomputed from that body after
account redaction. The local event/export hashes cover the archived derivative.
Free text must be non-personal. Existing contact checks are not a general PII
classifier.

`AVAILABLE(0)`, `NOT_AVAILABLE(null)`, `INSUFFICIENT_DATA` and `NOT_COMPARABLE`
remain distinct. A missing declared denominator is insufficient; denominator
observations with conflicting definitions are rejected. A supplied denominator
quantity remains a user declaration, without external exposure verification.
Report v1 does not identify its aggregation, so the registry checks its numerator
against the existing SUM/MEAN/COUNT calculations and checks the reported ratio.
It cannot infer which of equal-valued aggregations the author intended.

Saved snapshot/report identities validate archived bytes, not a fresh A09 replay.
Use `snapshot --db ... --pin ...` for an actual read-only A09/raw replay. Local
hashes do not prove provider truth, defend against a complete coherent rewrite,
or detect a resealed valid prefix without a separately retained export ID.
Keep `--expected-id` outside the archive when that check matters. Reload never
imports records or changes any database. The 32 MiB document limit and registry
event/source bounds apply; a large valid registry can exceed the export limit.

Success prints JSON and returns 0, including advisory negative/inconclusive
results. Input, file and integrity errors print a bounded JSON error to stderr
and return 60. Argument errors retain argparse exit 2. `export --output` creates
a new file exclusively and rejects existing destinations. No command accesses
platform APIs, posts, spends money, calls a model or changes A03/A08/A09 behavior.
