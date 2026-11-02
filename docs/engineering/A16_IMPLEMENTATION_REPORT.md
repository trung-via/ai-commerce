# A16 L05/E05 — EF02 engineering report

> **Integration reconciliation (2026-11-03):** This report's original **HOLD / UNKNOWN** heading documents the *Codex-internal lost temporary rerun artifact*, not the later EF01 pipeline result. The historical assertions below remain unchanged. Later independent EF01 Verify-Fix on the immutable final candidate bytes returned `BUILD_VERIFIED_FOR_SCOPE`, exit code 0, `mt01-a16-tests` **110 passed / 0 failed / 0 skipped / 0 errors**, `python -m pytest tests/test_a16_settlement.py tests/test_a16_commercial_review.py -q -n 12`, `MT01_V21_TARGETED`, `missing_mapping=[]`. See [#251](https://github.com/trung-via/ai-commerce/issues/251), [run #41495252097](https://github.com/trung-via/ai-commerce/actions/runs/41495252097), [original candidate](https://github.com/trung-via/ai-commerce/commit/1db09d402830cfc81b829e1fc5da58d777f0968b) and `ef02-results-41495252097-1` artifact. The EF01 evidence separately proves 110/110 after executor returned, but the EF01 structured `workers_observed` value is null: **12 workers requested, 12 observed in pytest/xdist stdout but not in a structured worker metric**. Prior executor-local consumer runs A14 120/120, A14-E04 33/33, A15 43/43 and A02 30/30 PASS remain distinct from hosted CI. The production feature is **INTEGRATION_PENDING_CI_AND_BRAIN**; the new integration branch must independently pass Ubuntu/Windows CI before product acceptance. Never infer a real external order, independently verified payout or profit from offline supplied data.

**HOLD: implementation is present; final registered verification is UNKNOWN.** This is executor worktree evidence,
not Brain acceptance, independent Ubuntu/Windows CI, or permission to publish.

Source: `55365d73cb744280c4f1f8fcfd9f04aa35997e01`. Handoff:
https://github.com/trung-via/ai-commerce/issues/247, dispatch #251, action
`A16-L05-E05-OFFLINE-01`, attempt `a16-l05-e05-default-20261103-v1`.
Only the eleven dispatch paths are authored. No Git writes or external commercial
actions are part of this implementation.

The implementation reopens native A14 AffiliateAttributionEvidence and
AffiliateCommerceRun and native A15 CostEntry/CostLedger. It does not change these
producers or their state machines. SettlementSource.v1 is an allowlisted business
projection, not a raw provider export: opaque business aliases, timestamps,
currency-scoped decimal strings, typed receipts and explicit provenance only.
Unrecognized fields (including customer/contact/credential fields) are rejected.
Receipt hashes identify supplied source bytes; they are not signatures and this
offline module cannot authenticate an independently attested provider receipt.

`admit_settlement(raw, attribution=..., correction_of=...,
expected_scope=..., expected_source_sha256=...)` returns immutable replayable
SettlementEvidence.v1. The full scope preserves provider, account, app, market,
surface, affiliate channel, product/listing/variant, creative/link hashes, order,
currency and attribution window. A14's exact order result/tier is retained and
checked; a click alone cannot establish an attributed order. Each money field
needs its own typed receipt. Missing evidence is UNKNOWN with a null amount;
an explicitly supplied zero remains distinguishable. Gross order claims,
credited commission, historical settled commission, refunds, reversals and fees
are separate fields. Commission basis is supplied merchant gross/net or creator
share; no percentage, exchange rate or profit is inferred.

`SettlementLedger(ArtifactRegistry(root)).append(evidence)` uses A04 atomic,
no-overwrite custody under a cross-process worker lock. Each numbered record
retains a sequence, previous hash and complete evidence. Reads replay the bounded
chain. Identical key/digest replay returns False, changed digest collides, real
order identity cannot be re-namespaced by window/link/currency, and reused
receipt IDs/content cannot be counted independently. Revisions require the
latest full parent, a new key, increasing recorded time, unchanged scope/origin,
and retained receipt history. A receipt correction explicitly supersedes one
current leaf of the same kind; prior bytes survive and only the new leaf counts.
Refund/revocation after settlement retains the historical payment and yields
HOLD in the review. A crashed writer's unreconciled lock fails closed.

`commercial_review(commerce_run=..., settlement_ledger=..., cost_ledger=...,
cost_selections=[{entry_id, job_id, attempt, intent_digest, provider}])` performs
read-only native replay. Cost selections are explicit **supplied creative cost
allocations**, retaining the actual A15 job identity; this module does not claim
independent proof that a selected media job produced the creative. Native A15
estimates, supplied charged costs and synthetic charged costs remain separate.
Duplicate source receipts and mismatched cost job/attempt/intent/provider fail
closed. Per-currency totals retain known subtotals and unknown counts without
turning missing A15 categories into zero. There is no cross-currency total.
Portable CommercialReview.v1 retains exact upstream snapshots and native cost
checksums and recomputes every projection, even if an attacker reseals its hash.
Actual settlement and realized profit remain UNKNOWN; provider verification
flags remain False. Status is PARTIAL or HOLD, never product PASS.

Bounds: 256 KiB source; 64 receipts per snapshot; 4 MiB evidence; 128 immutable
journal records and 40 MiB history; 128 selected A15 entries; 80 MiB review.
Input money allows 24 integral and 6 fractional decimal digits; aggregation uses
an explicit 64-digit Decimal context and up to 27 integral digits. No binary
floating point or FX conversion is used. Bounded errors do not echo raw inputs.
Storage custody and hash chains assume protected local storage; they do not
prove authenticity or completeness against deletion of an entire valid suffix
without an independently retained history pin. Portable reviews retain such a
pin to the exact snapshot they consumed.

Both previous scaffold tests have been replaced with functional positive and
negative coverage, including a native A06/A14 + A15 + A16 offline fixture and A02
mock consumer. Registered verification must still execute `mt01-a16-tests` with
12 workers, MT-01 v2.1 impact and mandatory A14 attribution/E04, A15 and A02 proof.
At most two bounded repairs are permitted on the same attempt. Infrastructure,
permission, timeout or unproven results require HOLD without fallback/re-dispatch.

The executor invoked the existing MT-01 v2.1 `select_plan` / `execute_plan`
with `registered_only=True`, the existing profile registry, actual eleven changed
paths and `A16.L05.E05.offline-settlement`. Impact selected group `a16` and the
exact `mt01-a16-tests` argv (two existing files, `-q -n 12`); no registry/control
files were changed. Mandatory upstream consumer proof used the four existing
registered profiles below. This is local executor verification, not an
independent EF01 worker receipt or GitHub-hosted CI.

Diagnostic environment: `PYTEST_ADDOPTS=-v` solely exposes actual xdist workers
and collection counts; the registered argv, test selection and worker count were
unchanged. The source snapshot remained unchanged throughout the first run and
mandatory consumer runs. No provider or network fallback was used.

| Registered profile | Selected test files | Collected | Observed call outcomes | Workers requested / observed |
| --- | ---: | ---: | --- | --- |
| mt01-a16-tests, initial | 2 | 110 | 108 passed, 1 failed, 2 error phase reports | 12 / 12 |
| mt01-a14-tests | 1 | 120 | 120 passed | 12 / 12 |
| mt01-a14-e04-tests | 1 | 33 | 33 passed | 12 / 12 |
| mt01-a15-tests | 1 | 43 | 43 passed | 12 / 12 |
| mt01-a02-tests | 2 | 30 | 30 passed | 12 / 12 |
| mt01-a16-tests, repair 1 | 2 | UNKNOWN | UNKNOWN; result artifact not retained | 12 / UNKNOWN |

The initial A16 run completed 109 pass/fail call outcomes; the error reports
must not be represented as successful calls. One negative assertion expected a
later rejection code than the implementation's earlier fail-closed check. An
oversized byte parameter also generated a pytest ID exceeding Windows' 32767
character environment-variable limit. Bounded repair 1 corrected the expected
code, supplied short test IDs and added assertions for changed-payload replay and
duplicate estimated cost. No test was skipped, filtered or reduced to obtain PASS.

The registered repair run returned to the local wrapper. Before printing or
persisting its result in an allowed durable artifact, that wrapper attempted to
load the initial summary using the new command's isolated temporary directory.
The file did not exist. A subsequent read-only check confirmed neither command's
temporary log directory survived into a later command. The rerun's outcome,
collection and observed workers cannot now be proven. Its result is **UNKNOWN**,
not PASS, not zero tests, and not a proven product failure. No verification
rerun, second repair, provider fallback, or re-dispatch followed this UNKNOWN.

The four mandatory consumer profiles observed 226 collected and 226 passed,
with 12 actual workers per profile. Those facts do not establish final A16 PASS.
Seven test files were selected across the A16 profile and mandatory consumers;
336 items were collected in the initial executions. Final exact-candidate
verification remains HOLD for the existing EF01/EF02 worker. The executor did
not commit, publish a branch/PR, change Git refs/configuration, invoke hosted CI,
claim Brain acceptance, or merge. Report/WorkItem metadata was finalized after
verification stopped; no exact-candidate acceptance receipt is asserted.

Observed initial log hashes (the tool transcript retained these observations;
the temporary files themselves are unavailable):

| Artifact | SHA-256 |
| --- | --- |
| A16 initial stdout | 3c7bcb2cd3110110f395407b7005c0be2265e841afa590934fb415504be994d3 |
| MT01 initial plan | dbc7bb8046ba5cb50985b2abf7c19276015e98fa0cc29efcac717238dee21421 |
| MT01 initial result | 094b61870f1f2a082df3bc07c51f4d352291f7fd1e315fe05c9507d38e954f30 |
| A14 attribution stdout | 881be051b72370e117b85b4877ef952775b8607f10e81c59cc98555eb8baf260 |
| A14 E04 stdout | a944f353db5286174003b9a088fe149f633c040bcee78d0781cf83b557da79a8 |
| A15 stdout | 96b996d5daaab626ff1e046d6e481366090b5a61391fe840add111ee42e997c1 |
| A02 stdout | c7136509ec8bc53f67ef0d471a501baa6296fd22285f6956d5301d67e8f7b9fa |

All five initial/consumer stderr streams were empty (SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`).
Final rerun log hashes are UNKNOWN. Profile-registry SHA-256:
`226b1549da8c2c8c689c636e3d9e16ec3b5f8babab82b12a7962a1df500ff414`.

Final authored source/test/contract byte hashes follow. These identify the
worktree bytes for EF01; they do not assert a successful final verification.
Report/WorkItem self-hashes are deliberately excluded from this embedded table.

| File | SHA-256 |
| --- | --- |

| `src/ai_commerce/settlement/__init__.py` | `7ef9a99a3508cbdf9d250b00365cf47d3a552a5d82ae2ef0f4a91611c4e2415e` |
| `src/ai_commerce/settlement/schemas.py` | `bd22ad49bc9876469600ed5a2ff47ef8e6fda18fc084a221202513d7b07184f6` |
| `src/ai_commerce/settlement/evidence.py` | `ff1175171d979268ef35af66ce4cdb94d22c500bc95c93e579708984f03ac84f` |
| `src/ai_commerce/settlement/ledger.py` | `d69193aee54e8a357a2bf8b50c99f9f07ba5b867e7bd14ecc6421ea450654fd1` |
| `src/ai_commerce/settlement/review.py` | `44851e8a755b07ff359a2047fe0175dfe261d89b49c75fbd8b5c6a76fa420c86` |
| `contracts/SettlementEvidence.v1.schema.json` | `80f875e02028f6becf1aa9b2589bcc5ad64d63167a623c3efe82f20d9714ce5e` |
| `contracts/CommercialReview.v1.schema.json` | `a76916f6cbb97b68f7e4bc78577469e96f7aad4c3977916961eb0b2d20166cbb` |
| `tests/test_a16_settlement.py` | `867dc85c34daaef2e1c2b7321c19699745efc1f8fe92d0f94d6361e542d3f23e` |
| `tests/test_a16_commercial_review.py` | `bbbd922e690cdad09315f10bd88684720199f815203522985c8231e9b943658b` |

Mandatory consumer test byte hashes (upstream files unchanged):

| File | SHA-256 |
| --- | --- |
| `tests/test_a14_attribution.py` | `f6fcbc818161560a0a4857519a4d67f24b37cf8f6ff784acbe6e204de8302020` |
| `tests/test_a14_commerce_e2e.py` | `9be4742ef2e29ccc16928512e97a6052ef712bcf9afbb95df0b2851d07d968d1` |
| `tests/test_a15_operations.py` | `be20a64fde9c10e98bb29c295eb34aad076487ebc36ccf85a5da24c16844caba` |
| `tests/test_a02_contracts.py` | `ceb4108828e69210b4fe27687763e972e965c7890f8a226f16a3684868e6b93e` |
| `tests/test_a02_mock_provider.py` | `b73a6ea1b34a45a81bda79fc7468ac4cb5a93615ec89b37e8aa1ffc370228748` |
