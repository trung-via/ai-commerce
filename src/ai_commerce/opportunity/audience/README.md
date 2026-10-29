# M03 offline audience insights

This component stores caller-authored qualitative notes. It does not generate
audience claims, demographics, scores, provider facts, accounts, or identifiers.
It is optional: freeform creative intake has no dependency on this journal.

## Input and command line

`AudienceNotes.v1` is UTF-8 JSON, optionally with a BOM. This example is explicitly
synthetic, contains no real participants, and is not a provider export:

```json
{
  "schema_version": "AudienceNotes.v1",
  "data_kind": "SYNTHETIC_FIXTURE",
  "source_note": "Synthetic workshop notes; no real participants.",
  "audience_segment": null,
  "statements": [
    {
      "kind": "JOB_TO_BE_DONE",
      "status": "OBSERVED_USER_SUPPLIED",
      "text": "The supplied note describes wanting a quiet break."
    },
    {
      "kind": "CONTENT_CONTEXT",
      "status": "INFERRED",
      "text": "A silent visual story might fit this situation.",
      "uncertainty": "A hypothesis from this synthetic note only."
    },
    {
      "kind": "COUNTEREXAMPLE",
      "status": "OBSERVED_USER_SUPPLIED",
      "text": "Another supplied note prefers lively sound."
    }
  ]
}
```

Use `USER_SUPPLIED_QUALITATIVE` for actual deidentified caller notes. Allowed kinds
also include `FRICTION` and `ALTERNATIVE`; statuses are
`OBSERVED_USER_SUPPLIED`, `INFERRED`, and `UNKNOWN`. Missing uncertainty becomes
`UNKNOWN`. Segment and timezone-aware `noted_at` are optional; absent values remain
null. Empty statements produce no audience claims. No age or gender is inferred.

```text
python -m ai_commerce.opportunity.audience validate --file notes.json
python -m ai_commerce.opportunity.audience append --file notes.json --ledger audience.sqlite
python -m ai_commerce.opportunity.audience inspect --ledger audience.sqlite --insight-id aud-<sha256>
python -m ai_commerce.opportunity.audience history --ledger audience.sqlite --insight-id aud-<sha256>
python -m ai_commerce.opportunity.audience append --file revised.json --ledger audience.sqlite --revision-of aud-<sha256>
```

Output is deterministic JSON. Errors return exit 2 and a bounded code without
echoing note text. `validate` does not persist files. No shared CLI changes are
required. The schema is available through `schema()` and the published
`contracts/AudienceInsight.v1.schema.json`; installed Python packages use the
self-contained runtime definition, without remote schema resolution.

## Exact M01 evidence and the M04 interface

```python
from ai_commerce.opportunity.signals import SignalLedger
from ai_commerce.opportunity.audience import AudienceLedger, prepare_bytes, signal_evidence_ref

signals = SignalLedger("signals.sqlite")
# Select these exact IDs from an existing, locally imported M01 result.
ref = signal_evidence_ref(signals, import_id, signal_id)
# Put ref in a caller statement's evidence_refs array before encoding notes.
preview = prepare_bytes(raw_note_bytes, signal_ledger=signals).record
result = AudienceLedger("audience.sqlite").append_bytes(raw_note_bytes, signal_ledger=signals)
insight = result["insight"]
```

Each reference contains `import_id`, `signal_id`, `raw_sha256`, and `evidence_ref`.
The helper uses M01's existing `sha256:<raw hash>` reference. An exact member of
the referenced signal's existing `evidence_refs` is also accepted; arbitrary URLs
or strings are never fetched or treated as proof. Supply `--signals signals.sqlite`
to CLI commands when references are present.

M01 `get()` replays the actual raw evidence and checks the normalized signal
identity. M03 then binds all four reference fields and rejects a declared snapshot
hash that differs from M01's actual raw hash. Receipts preserve context/scope
hashes, correction/reprocess lineage, original signal time, declared rights,
availability, and numeric value. Available zero remains zero; unavailable remains
null. Evidence never changes a statement's observation/inference status.

`LOCAL_INTEGRITY_CHECKED` describes a local M01 replay. It is not verification of
the qualitative claim, provider authenticity, audience representativeness, or
rights. Statements without M01 proof are `NOTE_ONLY`, with an exact reference to
their caller-supplied raw notes. Insight rights always remain `UNKNOWN`.
Reads and history replay stored notes and recheck M01; records with signal
references require the source ledger again. Missing or corrupted evidence raises
an error and cannot silently become verified. M01 is only opened read-only.

M04 may consume `result["insight"]` or `AudienceLedger.get(...)`: the output is the
AudienceInsight v1 dictionary, with `statements`, `signal_evidence`, and revision
`lineage`. M03 has no import of or dependency on M02/trends.

## Original bytes, revisions, and privacy

The independent SQLite journal keeps exact accepted JSON bytes, including BOM,
whitespace and Unicode. Its SHA-256 anchors `source_ref`; the entire deterministic
record anchors `insight_id`. Repeated identical input is idempotent. Explicit
revisions append a new record with the prior insight ID and revision number;
branches preserve their shared parent. Update/delete triggers prevent accidental
mutation, while replay and hashes detect raw/record/index changes. No wall clock
or random identifier enters the content identity. Revision depth is bounded to
128, source files to 256 KiB, and statements/references to 100/32 respectively.

Deidentify notes before intake. The strict schema accepts no person, email, phone,
age, gender, or arbitrary metadata fields. A normalized-copy scanner rejects
common email, phone, handle and contact-link patterns, including fullwidth digits
and zero-width obfuscation; accepted source text is never redacted or rewritten.
Rejected bytes are not persisted. The scanner is conservative (long digit strings
and web links can be rejected) and cannot recognize every name or indirect
identifier in prose. Callers remain responsible for supplying deidentified
qualitative descriptions. No separate PII retention or collection mode exists.

## Recovery and verification provenance

Source: `1d839bbbe8f49c5e7ad0ef2a89f7f89578a0a1fe`, integrating the exact M01
blobs from `8e27597b7a73bfbd42d9ec45a39fd34576bd8a2d` onto engineering main
`3d97a33792223c97649df7b71041e6083a340192`. This M03 recovery changes only its
audience directory, own schema and own test. The existing M01 implementation,
shared modules, EF01/EF02, governance and workflow files retain their bytes.

The untouched integration source was verified on Windows with the registered
`python -m pytest tests -q -n 12`: 1249 passed, 5 link-permission skips, exit 0.
Ignored local evidence is retained in `.local/a10-m03/`, including the initial
M03 test failure: 67 passed, 2 setup errors caused by an oversized pytest parameter
name exceeding Windows' environment-variable limit. Repair 1 adds short explicit
parameter IDs. No product-code failure was observed in that run.
The repaired targeted command `python -m pytest tests/test_a10_m03.py -q`
passed all 68 tests, exit 0 (1.27 seconds of pytest runtime).

M03 tests are in `tests/test_a10_m03.py`, with explicitly synthetic fixtures and
socket guards for the API/CLI path. Local Windows results do not establish Ubuntu
CI, remote candidate publication, full M02/M04/M05 fan-in, or acceptance on main.
Candidate publication remains behind EF01/EF02's actual verification and scope
checks; this module never commits, pushes or merges.
