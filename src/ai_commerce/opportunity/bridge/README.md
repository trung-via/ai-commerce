# A10 M05: optional A03 transfer and offline CLI

`opportunity_to_brief(proposal, edits=None, brief_id=None, synthetic=False)`
validates a real M04 `OpportunityProposal.v1` and returns an actual A03
`ContentBrief.v0.1`. It calls both `validate_record("ContentBrief", ...)` and
`intake_brief()`. The existing A03 schema and modules are unchanged.

```python
from ai_commerce.opportunity.bridge import opportunity_to_brief, freeform_brief
from ai_commerce.creative import CreativeStore, intake_brief, revise_brief

brief = opportunity_to_brief(proposal, edits={"objective": "Explore another point of view."})
assert intake_brief(brief) == brief
assert brief["product_ref"] is None  # unless explicitly supplied by the Human
assert brief["cta"] is None
digest = CreativeStore(".local/creative").save_brief(brief)

# No signal, audience note, proposal, clock, provider or model is needed.
independent = freeform_brief("  A cloud learns to whisper.\r\n  ")
revised = revise_brief(independent, {"objective": "Retell it from the rain's perspective."})
```

Required fields remain `brief_id` and `objective`. `prompt` preserves the original
manual proposal text, or the initial creative objective for an evidence-derived
proposal. Human edits can change objective, prompt, audience, nullable product/CTA
and optional media hint. No format, storyboard, concept quota or commerce input
is required. The original proposal/manual text stays in the transfer receipt.
The existing A03 freeform intake and edit/reframe commands remain independently
usable; A10 does not gate them.

## Provenance and validation scope

`ContentBrief.opportunity_bridge` is an extension allowed by A03's existing
`additionalProperties=true`. Its separate `OpportunityCreativeBridge.v1` contract
describes the transfer receipt, **not a replacement creative brief**. The receipt
contains the full detached M04 proposal, its content hash, all exact source refs,
raw byte hashes, signal availability/zero values, note statuses, uncertainty,
counterevidence, lineage and caller edits. Its content-addressed `bridge_id`
detects receipt changes. `validate_bridge()` validates that serialized receipt.

The brief retains every source ref and adds the proposal ID. Provenance stays
`USER_SUPPLIED_NOT_LIVE_VERIFIED`, proposal interpretation stays `INFERRED`, A03
factual status stays `UNVERIFIED`, and rights stay `UNKNOWN`. Hashes establish
local integrity; they do not authenticate a provider or validate an audience
claim. Bridge-only intake does not reopen missing ledgers or claim renewed replay.
Use M04 `propose_opportunities()` or CLI `propose` with the retained journals to
replay evidence before the transfer. Human edits cannot replace evidence refs,
rights, statuses or the receipt. Later ordinary A03 revisions remain editable;
the receipt documents the original transfer, not an attestation of later prose.

`freeform_brief()` preserves exact Unicode, whitespace and line endings. Optional
`raw_bytes` must decode to that text as UTF-8 with optional BOM; a separate raw
hash/size distinguishes original bytes from decoded-text identity. Keep the source
file for raw replay. The bridge does not silently redact or rewrite accepted text.
Inputs must be deidentified. Conservative scanning rejects common contacts,
handles, phone patterns and contact links; arbitrary names or indirect identifiers
cannot all be recognized. Record/text/file bounds reject oversized inputs.

Use `synthetic=True` / `--synthetic` only for caller-declared toy fixtures. The
original proposal's per-note labels are always preserved. A synthetic label is
not inferred from a provider's name or from metric values.

## CLI using the real M01–M05 APIs

Use the installed `ai-commerce opportunity ...` command, or from a source checkout
set `PYTHONPATH=src` and use `python -m ai_commerce opportunity ...`. PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m ai_commerce opportunity --help
python -m ai_commerce opportunity import --file signals.csv --snapshot snapshot.json --ledger .local/signals.sqlite --format csv --mapping mapping.json --dialect comma
python -m ai_commerce opportunity inspect --ledger .local/signals.sqlite --import-id IMPORT_ID
python -m ai_commerce opportunity trend --ledger .local/signals.sqlite --import-id IMPORT_ID --topic "caller topic" --contexts comparison.json --as-of 2026-10-29T12:00:00Z
python -m ai_commerce opportunity audience ref --signals .local/signals.sqlite --import-id IMPORT_ID --signal-id SIGNAL_ID
python -m ai_commerce opportunity audience append --file notes.json --ledger .local/audience.sqlite --signals .local/signals.sqlite
python -m ai_commerce opportunity propose --signals .local/signals.sqlite --series trend.json --audience-ledger .local/audience.sqlite --insight-id INSIGHT_ID --as-of 2026-10-29T12:00:00Z
python -m ai_commerce opportunity bridge --proposal proposals.json --proposal-id PROPOSAL_ID --edits creative-edits.json --store .local/creative
python -m ai_commerce opportunity bridge --text "A completely independent Human concept"
```

Commands emit JSON to stdout and bounded error codes to stderr (exit 2 on invalid
input). ASCII escaping preserves Unicode on legacy Windows consoles. `bridge`
emits the ContentBrief directly, suitable for A03 `--brief` after saving as UTF-8.
Optional `--store` saves and reloads that exact brief; it performs no generation.
Input reads and store paths use M01's local path policy, rejecting network shares,
traversal and links. JSON parsing rejects duplicate keys, NaN and malformed UTF-8.

CSV intake supports an explicit manual profile or custom mapping, correction and
reprocess lineage. It never claims a native provider export format. `trend`
requires caller-authored context per selected import; unbound contexts fail.
Omitting contexts leaves M02 uncertainty intact. `audience` also supports
`validate`, `inspect`, `history` and explicit `--revision-of` on append. `propose`
accepts repeated `--series`, optional repeated insight IDs, a Human `--idea` or
`--idea-file`, optional M04 creative edits and an M02 rule object. Saved hypothesis
fields are not trusted: M04 replays each actual series and evaluates its rule.
`bridge` requires an unambiguous proposal selection or independent `--text` /
`--text-file`. Nullable creative fields and absence of evidence are valid inputs.

## Reproducible synthetic Vietnamese demo

```powershell
python scripts/a10_demo.py --output .local/a10-demo-first
python scripts/a10_demo.py --replay-from .local/a10-demo-first --output .local/a10-demo-replay
```

The script works from a source checkout without package installation or network
access. Every output directory must be new. It creates explicitly synthetic
Vietnamese CSV BOM/CRLF bytes, canonical JSON, exact source snapshots and a custom
localized mapping. Separate CSV and JSON comparison contexts prevent metric
blending. The main toy count series includes available zero and a descriptive
growth hypothesis; a separate sparse series retains unavailable null and
`INSUFFICIENT_HISTORY`. Deidentified synthetic notes retain observed/inferred
statements and a counterexample, linked by the actual M03 evidence-ref API.

Every pipeline stage uses the real CLI and upstream callables. M04 proposals
become A03 briefs and round-trip through `intake_brief` and `CreativeStore`.
An independent exact-text brief demonstrates Human bypass. Raw M01 BLOB bytes
are read in SQLite read-only mode after M01 replay and compared with the files;
M01 has no public raw-bytes getter, so no such callable is invented here.

Fresh imports deliberately retain M01's real `recorded_at`. New independent
journals can therefore produce different downstream content hashes even for
identical raw bytes. Exact reproduction uses `--replay-from`: it checks hashes,
copies retained local journals into the new directory and reruns all real APIs.
Final content artifacts, SQLite bytes and `demo.json` are identical; the separate
`execution.json` truthfully distinguishes fresh/duplicate invocation results.
Clocks, upstream sources and evidence records are never patched to fake identity.

This component has no provider/network, model, account, publication, spending,
A11 dashboard or external-action implementation. A07/A08/A09 evidence remains
under its existing consumers and tests. Verification and repair evidence is in
`verification.json` and ignored `.local/a10-m05/`; Ubuntu and publication require
separate observed evidence.
