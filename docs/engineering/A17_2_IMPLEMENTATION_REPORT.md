# A17.2 D05 / L07 offline implementation

Action `A17-2-D05-L07-OFFLINE-01`, attempt `a17-2-d05-l07-v22-20261103-v1`, pinned source `cb6564fa1f3a99945b35fd36baf1398beb874f33`. Authorized handoff [#270](https://github.com/trung-via/ai-commerce/issues/270), historical contract [#255](https://github.com/trung-via/ai-commerce/issues/255), MT01 V2.2 design [#260](https://github.com/trung-via/ai-commerce/issues/260) / [#265](https://github.com/trung-via/ai-commerce/pull/265).

Implementation is substantive offline code. Acceptance remains **PARTIAL / HOLD** pending EF01 candidate proof, hosted Ubuntu + Windows source CI, and independent Brain product acceptance. Observed local results and the frozen-tree receipt location are recorded below. This executor edits worktree files only; EF02 alone may publish. No branch/ref/config writes, commit, provider/post/payment/model/Wake calls, fallback provider or automatic retry are part of this component.

## Source contract review and compatibility

Read the actual DistributionPackage, PublicationJournal SQLite tables/events/evidence/collision semantics; RetryGuard intent/attempt/ReconciliationObservation and CostLedger; AnalyticsLedger and ObservationSnapshot; LearningAdvisory archives; AffiliateAttributionEvidence and history; SettlementLedger and CommercialReview; C07 variant/render and C10/E06 consumer schemas. Existing sources/contracts are untouched. New schemas are self-contained runtime definitions with identical JSON mirrors, finite arrays/text/bytes, exact keys, no remote schema resolution.

The one data-only owner manifest is `work_items/mt01-components/a17-2.json`. Its eight existing consumers belong to trusted `approved_groups()` owners: receipt → a08; retry_guard/cost_ledger → a15; analytics ledger → a09; learning export → a11; attribution evidence → a14; settlement ledger → a16; variants → a17-1. Admission and observed execution evidence will be recorded below; this mapping is not itself a PASS.

## D05 behavior

`inspect_sources` opens A08 with SQLite `mode=ro`, `query_only`, a consistent read transaction and bounded inventory. It replays events independently, checks event BLOB and evidence digests, native schema, transitions/time order, package bytes/account/platform/surface/product/market/app/video digest, publication identity and duplicate/re-keyed evidence across the journal. It does not call A08's write-capable `get()` connection path.

`publication_request_digest` declares an exact D05 association between an existing A08 publication/manual attempt, package and scope, and the native A15 intent. An arbitrary media-job retry digest cannot prove that association. A15's entire consecutive attempt history, supplied evidence bytes, reconciliation records, checksums and current decision are reopened. Generic A15 policy is retained but is never permission to post.

An explicit observation pin must point to canonical `DistributionScopedObservation.v1` bytes in the A15 registry, with full scope, original attempt digest, request, publication/post identity, timestamp, supplied/synthetic provenance, and a separately reopened evidence BLOB/hash. Reusing a wrapper under a different ID/time cannot create independent evidence. Synthetic observations cannot resolve UNKNOWN. User-supplied observations may narrow the **supplied** state; visibility and provider attestation stay UNKNOWN/false, and replay_safe always stays false. Competing positive and negative assertions remain CONFLICTING_EVIDENCE/RECONCILE_REQUIRED. A supplied no-effect observation requires separate authorization and grants no automatic retry.

`ReconciliationLedger.reconcile` writes only new immutable records through ArtifactRegistry, serialized with its existing transient worker/staging mechanism. One numbered record is the transaction; no mutable index, source overwrite or secondary partial commit exists. Identical key/bytes replays return the original; changed keys/bytes, duplicate source under a new key, stale parents and truncated evidence fail. Records retain old/new state, full scoped snapshots/fingerprints, original ambiguity, observations, source provenance, global hash chain and per-publication revision lineage. Archived replay verifies local integrity/decision derivation; checking current source custody requires a fresh `inspect_sources` pin. Local hashes do not detect a complete malicious rewrite plus replacement of every external pin.

## L07 behavior and native limitations

`build_dashboard` accepts typed selectors for pinned native sources and returns deterministic `DecisionDashboardSnapshot` bytes. It has no writer/output directory. `verify_dashboard` rebuilds from native sources; `from_bytes` validates an archived projection and hashes, not fresh provider truth. A caller may explicitly retain the returned bytes using existing ArtifactRegistry custody.

Creative creation, local MP4 receipt, reported submission/confirmation/visibility, audience metrics, click events, reported attributed orders, supplied settlement, estimated cost and supplied/synthetic charges occupy separate fields. Missing inputs yield null/UNKNOWN/NOT_AVAILABLE and empty observation lists; numeric zero appears only when a native source reported zero. Product may be null. No live provider, causal uplift, profit, order-from-click, settlement-from-pending or PUBLIC-from-submission inference exists. Costs use exact decimals and separate per-currency/per-provenance categories with null absent categories; no FX or cross-currency sum is offered. Commission observations stay in their native currency rows.

The requested scope is a filter and is never evidence for a missing native field. A09 lacks market/product/creative identity and a numeric denominator; its native context/definition hashes, cohort, window, source, attribution tier, post IDs and missingness remain explicit. A11 uses its exact hashed-account alias and archive pin, retains every selected learning revision, counterevidence, uncertainty and confounder fingerprints, and cannot establish market or provider creative attribution. A14 records supplied link/click/order tiers, not a provider attestation or product/channel proof. A15 costs bind job/attempt/intent/provider; their allocation to dashboard scope is explicitly supplied because account/channel/creative attribution is absent. A16 native commercial scope and current correction leaves are checked, but reported settlement is not provider verified. Native A17.1 scope may be absent for content-only work. Local MP4 bytes are reopened and hashed against native C07/A05 requests/receipts; archived decode evidence is retained without rerunning the native loader that writes decoder scratch files. Semantic realization remains UNKNOWN.

Only non-personal aliases/prose are supported. Paths use existing link/reparse/ADS/traversal guards and bounded reads; raw A08 evidence BLOBs, URLs, captions and credentials are not copied into outputs. Obvious private strings are rejected. This is not a general-purpose PII classifier or an OS sandbox against a concurrently hostile filesystem process.

## Verification

The unchanged trusted V2.2 `validate_component_onboarding_manifest` and `registered_action_plan` admitted the manifest from actual candidate bytes. Manifest SHA256: `a93b9c07c013ff8bdb0bc46a9a33210916d5686971f024cb538ad033ae3a056b`. Selection is AFFECTED / ONBOARD_CONSUMER_OWNER_CLOSURE, with no unknown AC and no fallback. All eight listed existing consumers were confirmed in the trusted base owner graph. The generated new-owner command is exactly:

```text
python -m pytest tests/test_a17_reconciliation.py tests/test_a17_dashboard.py -q -n 12
```

The following results were actually observed during development. Consumer results are from the first completed registered closure run; the new-owner result is the corrected component probe using that trusted generated command. They are not relabeled as one frozen-tree aggregate PASS.

| Registered group | Collected | Executed / passed | Skipped | Requested / observed workers |
| --- | ---: | ---: | ---: | ---: |
| a08 | 10 | 10 | 0 | 12 / 12 |
| a09 | 182 | 180 | 2 | 12 / 12 |
| a11 | 223 | 220 | 3 | 12 / 12 |
| a14 | 120 | 120 | 0 | 12 / 12 |
| a15 | 43 | 43 | 0 | 12 / 12 |
| a16 | 110 | 110 | 0 | 12 / 12 |
| a17-1 | 136 | 136 | 0 | 12 / 12 |
| a17-2 corrected component probe | 106 | 106 | 0 | 12 / 12 |
| a02 supplemental registered contract/provider regression | 30 | 30 | 0 | 12 / 12 |

The five upstream skips concern host-denied symlink/hardlink fixtures: two A09 symlinks, two A11 symlinks and one A11 hardlink. No native link-creation proof is claimed for this Windows environment.

Two bounded component repair rounds were used. First, the no-model-call test fixture incorrectly patched a nonexistent method, producing 98 setup errors before any component test body; it now patches the actual native `reason` method. The first body-level component probe then produced 69 PASS / 29 FAIL, all 29 due to a shared settlement fixture whose capture time preceded its completed measurement window. That fixture was corrected; exact numeric version handling, journal sequence gaps and immutable history checks were also strengthened. The next actual probe collected/executed/passed all 106 tests. Positive tests reopen native A08/A15, actual A09 imports, C07/A05 MP4 bytes, A11 history/E06 proposals, A14 identity chains and A16 durable settlement; negative tests cover evidence duplication/re-keying, conflicting observations, stale/wrong scope, corrupt custody, prohibited trust/effect claims, fake later attempts and absent inputs. Network/provider/model and upstream writer mocks fail if called.

There were also two local evidence-adapter interruptions before successful component execution: a Windows JUnit option lost its backslashes and created a misplaced file (MT01 correctly stopped for source drift), then the local save adapter passed a metadata dictionary to an API expecting a relative filename. Both are preserved as unsuccessful diagnostics. The misplaced file was moved into the ignored evidence directory; no source or guard was changed to bypass either check.

The frozen candidate, including this report and the work item, is then verified by unchanged `engineering.verification.execute_plan`. Its actual result is written to `.local/a17-2/final/receipt.json` and raw `mt01.results.json`, with per-group JUnit/stdout/stderr and the manifest/source before/after hashes. The final result must be read from those observed artifacts; this document does not predeclare a future PASS. A subsequent local audit independently checks JUnit cases against exact selected test files, collection/execution counts, 12 workers, artifact hashes, native onboarding-binding replay and source byte stability. `.local/a17-2/candidate-evidence.zip` retains final receipts, candidate files, A02 evidence and the audit; it is local evidence, not a GitHub-hosted acceptance receipt.

Only pytest JUnit reporting and verbosity are added via `PYTEST_ADDOPTS`; the registered argv, selected files and `-n 12` stay unchanged. No collection filters, plugin disabling, worker fallback, arbitrary full suite, Git-write fixture, registry/verify-profile change or candidate test omission is used. Every pre-existing worktree file matched its captured baseline at the report freeze, including all upstream contracts and protected controls. Final source stability is checked again by the trusted runner and audit.

No hosted Ubuntu/Windows run, independently accepted ZIP/JUnit oracle, candidate branch publication, Brain PASS or merge is claimed. Those exact-source external gates remain **HOLD / PENDING**. A17.3 D07/D08 remains outside this action.
