# PULSE B0.3 attended offline worker — source handoff

Source baseline `32f42b84567e1217a93854389b1c96435bbf454d`. Human @AI Commerce 2026-11-04 “ok đồng ý, tiếp tục” authorizes this seven-file source-only cut after B0.1 PR #320 and B0.2 PR #323. Commerce Operations owns coordination; EF01/EF02 builds/tests software and does not schedule product journeys. No live PULSE launch, installation, service, provider generation, public posting, spend, HostOps, CDP/Wake, credentials or Brain authentication is authorized.

Preflight observed the exact HEAD, clean tracked tree and all seven targets absent. The three schemas and existing source/tests/registry/workflows remain frozen. Authoritative inputs: [Roadmap B PA2](../roadmap/ROADMAP_B_PA2_PRODUCTION_RUNTIME_V2_0.md), [ADR B001](../architecture/ADR_B001_BRAIN_LED_INDEPENDENT_RUNTIME.md), B0.1 contracts/reducer/schemas/tests and B0.2 journal/recovery/manifest/tests, A06 service run/inspect/verify and FFmpeg prerequisites, registered MT-01 selection/observer. The new B0.3 AC may be unknown to the ingress baseline registry; known A06.e01 alone does not prove B0.3. Data-only manifest admission and source tests do not authenticate Brain or establish a registered baseline PASS.

## Attended synthetic commands

These are examples for an independently reviewed source package, not a live setup instruction. Use original synthetic PNGs and a sealed synthetic CreativeDirective.v1 fixture in an explicit local temp folder outside the repository. The test fixture in `tests/test_b0_3_attended_worker.py` constructs those inputs; it contains no real customer files. `run` requires an existing empty private directory; all other commands open existing-only. Existing empty export directory is explicit. No shell file, background task or automatic startup is provided.

```text
python -m ai_commerce.production_runtime.attended_cli run --human-attended --source-sha 32f42b84567e1217a93854389b1c96435bbf454d --directive C:/Temp/b03-fixture/directive.json --asset-root C:/Temp/b03-fixture/assets --asset asset:<sha256>=C:/Temp/b03-fixture/assets/scene.png --private-dir C:/Temp/b03-fixture/private
```

Repeat the same explicit input arguments for `inspect`, `resume` or `export`; `export` also requires `--destination C:/Temp/b03-fixture/export`. Paths must be absolute local paths, with no links, traversal, device/UNC/ADS aliases. Asset refs are opaque `asset:<64 hex>` tokens; journey IDs are `journey:<32–64 hex>`, product refs are null or `product:<64 hex>`, source refs are hashed tokens. Tools must equal `["tool:a06-e01-offline-local-stills"]` and actions `["EXPORT_DRAFT"]`. Scope provider/account/surface/final asset pins are null. This source cut chooses no strategy and accepts no BrainDecision. Exit 0 means durable WAIT_BRAIN; 20 means HOLD; 21 is reserved for preserved UNKNOWN_EFFECT. Machine stderr never includes decoder errors, creative text or supplied paths.

Parameters are finite and fixed: up to four explicitly selected PNGs, each at most 4 MiB and 1024×1024; 64×96 canvas, 12 fps, three frames per still, no audio. A06 imports and renders supplied stills; generation_performed=false. Synthetic assets stay SYNTHETIC_FIXTURE even though the actual A06 storage origin is LOCAL_IMPORT. Human-supplied imports require exact hashed local rights evidence, supplied separately via `--rights rights:<64hex>=C:/.../rights.json`. Its closed local statement has schema_version LocalStillRights.v1, asset_sha256, source_sha, scope OFFLINE_LOCAL_RENDER, status GRANTED, integer valid_from/expires_at (Unix seconds). Matching bytes/scope/current validity is checked. This is a Human-supplied local-use claim, not a new grant, provider entitlement or external rights verification. Frozen CreativeDirective has no TTL; none is invented for it.

## STEP boundaries and recovery

The B0.2 JOURNEY_CREATED row durably binds the directive and replays INPUT_ACCEPTED using the unchanged reducer; there is no supported separate INPUT_ACCEPTED append API. START contains the exact tool; its stable event ID hashes a sealed sanitized intent binding the deterministic destination `private/e01-<directive-record-hash-prefix>`, ordered asset hashes and parameters. No unsupported B0.2 field or external PUBLISH intent is used. START coordinates a scoped local filesystem effect, not a non-idempotent external provider effect.

| Last durable cut | Attended next action |
|---|---|
| Before START | Stop safely; resume never renders. Review this stopped journey; do not reset/replace its journal. A separately reviewed new journey needs a new private directory. |
| START without definitive complete inspected A06 package | HOLD / PENDING_OFFLINE_EFFECT_RECONCILIATION. No automatic rerender, cleanup, guessed success or rekey. |
| A06 complete, before OUTPUT_READY | Reopen exact native manifests, request/config/asset pins, output bytes, ffprobe and full decode; only then append missing OUTPUT_READY/QA. |
| OUTPUT_READY, before QA_PASSED | Repeat technical verification; append QA only. |
| WAIT_BRAIN, before export | Remain WAIT_BRAIN and export the same deterministic receipt. No extra events. |

OS byte locking excludes concurrent cooperating Windows processes and is automatically released on actual process termination. B0.2's append-only lease/fence remains separate. An abandoned lease never confers automatic takeover: `resume --reconcile-stopped-generation N` additionally requires the actual lock and exact retained generation, complete matching output and all pins. It explicitly closes an unexpired stopped lease using B0.2's existing release API, or acquires after expiry, before verification-only journal completion. A stale expected generation, concurrent holder or partial evidence means HOLD without mutation. WAIT_BRAIN recovery needs no writer and leaves even an abandoned lease unchanged. A stale running writer cannot append after a new generation. No bounded invocation has a renewal loop; exceeding the 300-second lease stops progress.

Immutable local checkpoints are written after each commit. Reopen compares every retained anchor to native B0.2 replay, detecting missing/changed history; an ahead journal after a crash is allowed only if the anchored prefix verifies. This does not defeat malicious same-SID replacement of journal and anchors. Windows TerminateProcess tests do not prove power-loss/storage-controller durability. Do not delete/replace journals to clear HOLD. B0.2 reserved PUBLISH/UNKNOWN paths remain blocked at offline ingress; no authority context or retry/rekey API is connected.

## Receipt and private evidence boundary

`ProductJourneyReceipt.v1.json` is sealed and parsed with unchanged B0.1 functions: WAIT_BRAIN, exact directive/journey/source/version/scope, owner evidence pins, brain_decision=null, effect NONE, false authorization/network/external-effects. The separate canonical `attended-evidence.json` binds its file/digest hashes, exact directive-file SHA, fingerprint of all shipped Python source, real E01 artifact/manifest/tree SHA, A06 full-decode verification and the precise QA commit checkpoint and journey sequence. It contains only generated refs/hashes/enums: no raw prompt, credentials, asset source paths or customer text. A06 internal custody includes the prompt and stays private outside the repository; never upload that directory as a public artifact.

The frozen receipt schema cannot hold journal checkpoints or LOCAL_IMPORT as an evidence enum; the companion records those limitations explicitly. LOCAL_IMPORT maps to HUMAN_SUPPLIED for local Human inputs, SYNTHETIC_FIXTURE for fixtures. Neither schema validity, a SHA, actor label nor attended transfer authenticates CHATGPT_BRAIN. Native owner truth stays in A06/A04/A05 and existing business owners. Atomic create-new publication uses flushed local temporary bytes and Windows no-replace rename (the POSIX helper uses a no-replace hard link); identical bytes may be re-exported. Divergent existing files are never replaced. Two files are not a cross-file transaction: an interrupted export is incomplete until the same bytes are safely re-exported.

As with B0.2, finite literal stdlib loads preserve the unchanged older AST import test. B0.3 separately audits every literal dependency, denies dynamic adapter/authority dispatch, and instruments actual separate CLI processes to reject socket activity and any child executable except the local FFmpeg/ffprobe tools. There is one fixed service.run call and no arbitrary tool/command argv.

## Actual verification receipts

**HOLD / NOT READY. The two allowed Verify-Fix rounds are exhausted.** Final B0.3 pytest exited 1; the unchanged MT-01 controller returned 3, classifying the material group UNPROVEN / VERIFY_INFRASTRUCTURE_ERROR. No candidate should be published as verified from these receipts.

Observed Windows self-host environment: win32, Python 3.14.7, pytest 8.4.2, pytest-xdist 3.8.0, SQLite 3.50.4, FFmpeg and ffprobe 9.0.1. RUNNER_ENVIRONMENT=self-hosted and GITHUB_ACTIONS=true were observed environment values, not independent runner/custody attestation. No test dependency installation or host configuration was performed.

MT-01 selected A06/B0.1/B0.2/B0.3 from the actual seven changed paths and validated new data-only manifest. Its reason was ONBOARD_CONSUMER_OWNER_CLOSURE and fallback=false. Actual forward imports additionally reach registered A02, A03, A04, A05 and CLI/core owners; their exact registered tests ran in round0. Their native source/test bytes stayed unchanged through the repairs. This is a scoped source/dependency record, not a final candidate PASS. The raw planner reports ac_adoption_state=REGISTERED only after candidate manifest admission; that flag is not baseline AC acceptance, Windows CI PASS or independent Brain adoption of the new B0.3 AC.

Every group below had **12 actual workers** from the existing controller collection hook plus a separate all-worker observer. Each of the 12 workers collected the same count and ordered node-ID digest; JUnit execution totals matched. Commands were exact registered `python -m pytest <files> -q -n 12`, with generated bounded argv for admitted B0.1/B0.2/B0.3 owners. No filter, worker reduction or global suite was used.

| Group | Receipt | Selected / collected / executed | Passed | Failed | Skipped |
|---|---|---:|---:|---:|---:|
| a06 | round2 | 7 / 7 / 7 | 7 | 0 | 0 |
| b0-1 | round2 | 82 / 82 / 82 | 82 | 0 | 0 |
| b0-2 | round2 | 60 / 60 / 60 | 60 | 0 | 0 |
| b0-3 | round2 | 55 / 55 / 55 | 37 | 16 | 2 |
| a02 | round0 | 30 / 30 / 30 | 30 | 0 | 0 |
| a03 | round0 | 193 / 193 / 193 | 193 | 0 | 0 |
| a04 | round0 | 181 / 181 / 181 | 178 | 0 | 3 |
| a05 | round0 | 217 / 217 / 217 | 217 | 0 | 0 |
| cli | round0 | 8 / 8 / 8 | 8 | 0 | 0 |

**Combined dated observations: 833 selected/collected/JUnit outcomes, 812 passed, 16 failed, 5 skipped. This is not an aggregate PASS.** The table’s executed column includes skipped JUnit records; 828 records were not skipped. The 16 final failures exceeded the 60-second child CLI timeout or 50-second process-cut handshake bound while the group used twelve workers. The underlying performance cause remains unproven. No lower-worker retry or third repair round was attempted.

All earlier outcomes remain retained: initial controller UNKNOWN from a missing artifact-reference callback key; initial B0.3 28 passed / 21 failed / 1 skipped. Repair round1 controller UNKNOWN from an incorrectly typed artifact reference; B0.3 33 passed / 20 failed / 2 skipped. The initial source publication used hard links, which this sandbox denied. Round1 used native Windows no-replace rename; the remaining failures exposed a test audit-hook error: CPython Windows emits a command-line string after converting the argv list. Round2 validates the exact prechecked conversion, shell=False and the pinned local executable. Old B0.1/B0.2 tests were preserved and passed in each executed core attempt.

The first temporary preflight snapshot was unavailable after its sandbox command ended; this was recorded UNKNOWN with no pytest execution. A 760-file baseline snapshot was reconstructed excluding exactly the seven additions and checked against clean tracked Git state. No checkout/path/source drift was observed. Raw local harness logs and synthetic temp directories are not a durable artifact delivery; their hashes and sanitized outcomes are retained in the WorkItem.

An actual separate synthetic diagnostic child reached WAIT_BRAIN and exported ProductJourneyReceipt.v1 through the new worker: one A06 render, 11 FFmpeg processes, 7 ffprobe processes, zero denied network/process attempts, zero provider generation/publish. Observed receipt-file SHA256: `c5639f99e08ca59b3db526373b83657d5d5de25eb52d1d20365bd5892436a8fd`. This diagnostic is not the required registered subprocess-group PASS; its MP4 digest was not retained. The primary five-process repeated-export test timed out before its material digest properties were recorded, so repeated receipt equality and full material acceptance remain UNPROVEN.

Final JUnit properties do prove actual process termination at before_start (0 renders), after_start (0 renders), and after_a06 (1 render). The after_a06 case reopened complete native A06 evidence, recovered to four-event durable WAIT_BRAIN, exported and inspected without rerender. The after_output and after_wait cases timed out at their bounded handshakes and are not claimed as proven. The three killed-after-A06 missing/partial/corrupt-package tests passed and retained HOLD with no journal mutation or duplicate render.

The host denied native symlink/junction creation in two new tests and denied symlink/junction/hardlink creation in three existing A04 tests. Those five skips are UNPROVEN native controls, never PASS. The separate lexical escape/device/UNC and structural symlink/reparse denial tests passed; the latter use injected filesystem observations and do not substitute for native creation proof. No permission, host setting or installed environment was changed to bypass these denials.

Final B0.3 JUnit SHA256: `b3f4dfb057d7038ff66079a54f8eaed0853ae5dff692c070d64b34902dec8c58`; stdout: `6640ca88debd8e0c3cdc85e1e85f51348d6b860d3d4cf99064f850aba334b219`; all-worker collections: `a5a4dcdf618572d987f51c94310ce63ba0b4a5982ba6164d17272f641b679983`. MT-01 results SHA256: `39e3bc91f6eabbcf3e9b16ed11b60919d296b78a7244433793dbf22489a18f94`. [WorkItem](../../work_items/B0-3-PULSE-ATTENDED-WORKER-001.json) retains all three B0.3 attempts, per-group counts/argv source mappings, observer hashes, source/test/manifest/schema pins and pending material witnesses.

Only this report and WorkItem receipt metadata were updated after the final tests. The executable, test and manifest pins remain the tested bytes. Exact final-tree verification, material AC reconciliation, hosted Windows CI and independent Brain review remain pending; this source cut is on HOLD.

Independent EF02 must verify the final tree, publish one candidate and retain its own receipts. Later Windows hosted CI uses four workers and the independent exact-HEAD oracle; genuine independent Brain reconciles the new AC and exact source. B0.4 operator experience and B0-S private verifier/custody are future separately authorized work. No real rights, customer outcome or secure private identity is claimed from this source cut.

Temporary engineering logs remain in ignored `.local/b03-proof`: automatic approval review rejected their cleanup with “blocked by policy.” No alternate deletion was attempted. They contain synthetic test diagnostics and are outside the seven candidate source paths; they are not a durable public artifact delivery.
