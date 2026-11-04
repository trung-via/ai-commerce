# PULSE B0.4 attended operator, source handoff

Human @AI Commerce supplied “đồng ý tiếp tục B0.4” on 2026-11-04. This eight-file source cut is pinned to `8d3ec319a10231b5603b2a435c83eb2f8b1c66d5`, after B0.3 PR #329. It provides an attended local operator around the existing worker. It does not install or launch a service, daemon, background product worker, provider, authenticated ChatGPT bridge, private MCP/CDP/Wake or HostOps effect. EF02 alone may publish a verified candidate. Hosted Windows exact-head CI with four workers and independent Brain acceptance are separate later gates.

## Copyable Windows synthetic walkthrough

Use only an independently reviewed source package and explicit synthetic or separately authorized Human-supplied local inputs. No package or host configuration installation is part of this cut. Existing Python, FFmpeg and ffprobe are material prerequisites. An absent material prerequisite is FAIL/UNPROVEN.

Prepare four existing separate directories outside the repository: assets, empty B0.3 private evidence, empty operator state, and empty export. None may contain another. The native sealed `CreativeDirective.v1` file also stays outside the repository. Its fixture has two PNGs, `product_ref=null`, offline EXPORT_DRAFT scope and the unchanged native tool allowlist. Tests construct these synthetic inputs; the commands below are attended examples, not a claim of live installation.

```powershell
$fixtureRoot = 'C:/Temp/pulse-b04-synthetic'
$sessionFile = "$fixtureRoot/operator/SESSION.json"
python -m ai_commerce.production_runtime.operator_cli setup --operator-dir "$fixtureRoot/operator" --private-dir "$fixtureRoot/private" --export-dir "$fixtureRoot/export" --directive "$fixtureRoot/directive.json" --source-sha 32f42b84567e1217a93854389b1c96435bbf454d --asset-root "$fixtureRoot/assets" --asset "asset:<first-png-sha256>=$fixtureRoot/assets/first.png" --asset "asset:<second-png-sha256>=$fixtureRoot/assets/second.png"
python -m ai_commerce.production_runtime.operator_cli run --session $sessionFile --human-attended
python -m ai_commerce.production_runtime.operator_cli status --session $sessionFile
python -m ai_commerce.production_runtime.operator_cli handoff --session $sessionFile
python -m ai_commerce.production_runtime.operator_cli learning-template --session $sessionFile
python -m ai_commerce.production_runtime.operator_cli metrics --session $sessionFile
```

Replace the two explicit digest placeholders with the selected PNG digests. Mappings are entered once at setup. Human assets additionally need `--rights "rights:<sha256>=C:/.../rights.json"` for each exact native `LocalStillRights.v1` claim. B0.3 checks claim scope, content digest, source pin and current validity on every session use. This remains a supplied local-use claim, not independent rights verification. The sealed directive must already exist; setup does not invent a Brain directive or approval.

B0.3 freezes `source_sha=32f42b84567e1217a93854389b1c96435bbf454d`. Its directive, intent, journal and receipt retain that historical native pin. B0.4 separately records `operator_baseline_sha=8d3ec319a10231b5603b2a435c83eb2f8b1c66d5` and compares the native complete runtime-tree fingerprint. Neither SHA authenticates an actor. B0.4 does not rewrite the reviewed B0.3 constant, reseal an input, or mislabel the old native pin as the new checkout's HEAD.

SESSION contains private paths and exact original input pins. Keep its entire operator directory private, outside both the repo and B0.3 private_dir (which must be empty on first run). Its local checksum detects damage; it is not authority. Every command reopens paths, denies symlinks/junctions/reparse/UNC/device/traversal aliases, checks source/directive/asset/rights pins and invokes native B0.3 `_inputs`. No session grants publish, spend or Brain identity. Same-SID replacement of all evidence and anchors is outside the proven boundary.

## HOLD reason to safe next Human action

| State or reason | Safe attended action |
|---|---|
| WAIT_BRAIN | Run handoff; manually share the three sanitized files below with Brain in the SAME ChatGPT chat. Wait for independent review. |
| Before START or earlier stopped run | Do not retry this journey. Preserve the original trail/journal and inspect A06 owner evidence. A new journey requires separately reviewed inputs and fresh private directories. |
| START with missing/partial/unknown output | Do not render again or rekey. Inspect the exact private A06 manifest/custody/video bytes. UNKNOWN stays blocked. |
| TECHNICAL_QA, complete native A06 package | Confirm the original process has stopped and inspect the retained lease/checkpoint generation. Only then use the verify-only command below. |
| Concurrent process or stale generation | HOLD. Confirm ownership and exact generation. Never delete a lease, lock file, journal or checkpoint to force takeover. |
| Bad SHA, stale directive/asset/rights, foreign/corrupt history | HOLD. Preserve original evidence and request source/owner inspection. No reset, repair-by-overwrite or guessed success. |
| Missing terminal attempt | INCOMPLETE/UNKNOWN permanently for that attempt. Recovery, if safe, is a separately observed invocation. |
| Corrupt/rolled-back/duplicate trail or partial/tampered handoff | HOLD. Preserve trail, anchors and package. No truncation, overwrite or fabricated PASS. |

```powershell
python -m ai_commerce.production_runtime.operator_cli reconcile --session $sessionFile --human-attended --stopped-generation 1
```

The generation is an example; inspect the exact retained B0.3 lease/checkpoint and confirm process termination before supplying it. B0.4 verifies artifacts before native `resume_once`; that native API also verifies again under its own lock. It never renders. If START exists, `run` performs safe inspection only and cannot call native run again. Even a crash before the native call leaves a durable attempt and blocks retry. Status never mutates the B0.2 journal. Fixed authored state/reason/next-action text suppresses private prompts, paths and raw SQLite/FFmpeg exceptions. Exit 0 denotes a determinate local result (SETUP/READY/WAIT_BRAIN), 20 HOLD, 21 UNKNOWN; 0 never denotes Brain approval.

## Sanitized manual handoff

The OFFLINE DRAFT package consists of `ProductJourneyReceipt.v1.json`, `attended-evidence.json`, and `OFFLINE_DRAFT_SUMMARY.json` in the selected export directory. Only these files are intended for manual sharing. Never share SESSION, the trail, directive, original assets, A06 custody, real media, account details or credentials. Full native replay, A06 ffprobe/full decode, exact sealed receipt bytes and the parsed companion packet must verify in genuine WAIT_BRAIN before export. The summary is a closed projection of generated refs, hashes, scope, provenance and QA checkpoint. It explicitly says SANITIZED, NOT_AUTHENTICATED, NOT_PUBLISHED and NO_PROVIDER_EFFECT. Synthetic data stays SYNTHETIC_FIXTURE; Human imports stay HUMAN_SUPPLIED.

Each file is flushed then atomically published without replacement, or accepted byte-for-byte identically. Preflight checks the entire bundle before writing. A partial publication is HOLD; the files are not a cross-file transaction. A retained private seal detects later removal of the whole exported package. The existing B0.3 `seal_wait_receipt`, `evidence_packet`, `export_packet` and `parse_packet` determine native bytes. No raw prompt or source path is projected, no chat_ref/private verifier receipt is invented, and no authenticated channel is claimed.

`OFFLINE_LEARNING_TEMPLATE.json` is separate and inert. It proposes observation fields with no observations filled, decision, automatic learning, Git branching or publication. A Human may manually propose notes for Brain review in the same chat. It cannot advance B0.1's state machine or create an approval.

## Observation metrics and limits

The bounded private operator trail is independent of the authoritative B0.2 journal and all commerce ledgers. ATTEMPT is fsynced before a native call. Each record has a unique event ID, attempt ID, monotonic sequence, previous hash and checksum, plus a flushed immutable head anchor. Missing terminal results are INCOMPLETE/UNKNOWN; duplicate attempts/terminals, corruption and tail rollback against retained anchors HOLD. A crash between trail append and anchor creation also HOLDs; there is no silent tail repair. Replacing both trail and anchors under the same SID is not defeated. Capacity is 2,048 records / 4 MiB; admission reserves space for both ATTEMPT and RESULT before a call.

A hash-chained native-admission bit and a separate flushed `run-admission.json` bind the one allowed native run to its original attempt while the private directory is empty. Merely inspecting a preexisting external B0.3 journey cannot create admission, even after repeated run/reconcile/handoff commands. A standalone forged admission file or recomputed session checksum cannot replace this retained trail evidence. These are local observation checks, not independent custody or identity authority. External actors racing native directory ownership remain outside the cooperating operator boundary; uncertain ownership must be inspected, not used as production authority.

`observed_b04_attempts` counts the instrumented run/status/reconcile/handoff/learning-template boundaries, including refusals after session validation. Setup establishes storage; metrics is a read-only observation of existing counts. Inputs rejected before instrumentation and concurrent calls rejected by the operator OS lock are outside that attempt count. Outcomes count PASS/HOLD/UNKNOWN of those calls, never Brain acceptance. Recovery outcomes count reconcile only. A missing terminal stays unknown even after a later successful recovery. Preexisting B0.3 or external journeys are UNKNOWN, never attributed as B0.4 successes.

Elapsed wall time spans first to last recorded event, clamped nonnegative; Human wait may be included and the clock can drift. Active time sums monotonic durations only for completed invocations; crashed active time is UNKNOWN, and process startup/session validation is excluded. Wall/monotonic disagreement or regression is reported. The runtime audit boundary allows only the existing local FFmpeg/ffprobe child executables and denies network/other-process audit events. Coding-agent calls target zero; zero OBSERVED applies only to completed instrumented B0.4 boundaries. Other host coding-agent invocations, external media costs, provider accounting and revenue remain UNKNOWN/NOT_INSTRUMENTED. No host-wide zero or new commerce ledger is asserted.

## Verification record

Preflight observed exact HEAD, clean tracked checkout and exactly eight absent targets. Authoritative inputs were Roadmap B PA2 B0.4 O07/E01, ADR B001, frozen B0.1 schemas/contracts/reducer, B0.2 journal/recovery, B0.3 worker/CLI/receipt/tests, A06 service/tests, MT01 V2.1 selection/observer and data-only owner onboarding. The new B0.4 AC is independently required; registered B0.3 alone cannot establish it. Source-ingress unknown/adoption status is retained in the WorkItem.

Actual Windows self-host scoped verification is recorded in [the WorkItem](../../work_items/B0-4-PULSE-ATTENDED-OPERATOR-001.json). Pending evidence must not be read as PASS. Every selected group must use twelve actual xdist workers and an independent all-worker collection digest. No worker reduction, hidden retry, filtered native group or full-suite substitution is authorized. There are at most two scoped Verify-Fix rounds. Native link creation denials remain SKIP/UNPROVEN, and absent FFmpeg is a material failure.

The immutable [B0.3 historical report](B0_3_ATTENDED_WORKER_HANDOFF_REPORT.md) recorded 37 passed, 16 failed and 2 skipped in its final 55-case twelve-worker group: 60-second CLI / 50-second cut-handshake timeouts and native link permission limits. B0.4 preserves this history; its longer bounded subprocess waits do not change B0.3's tests, lease or recorded outcomes. An inherited B0.3 defect requiring changes outside these eight files is a precise HOLD blocker. This document makes no live installation, final candidate, hosted CI or Brain acceptance claim.

The current initial native consumer check returned **HOLD**: A06 7 passed, B0.1 82 passed and B0.2 60 passed; B0.3 46 passed, 7 failed and 2 skipped. All seven failures were its unchanged 60-second CLI subprocess timeouts, including the native primary separate-process receipt/re-export test. MT01 returned 3 / VERIFY_INFRASTRUCTURE_ERROR and did not execute B0.4 in that controller run. All completed groups had twelve independently observed matching worker collections. This is a missing required proof, not a demonstrated diagnosis of the underlying performance cause. The baseline files and bounds remain unchanged; resolving their proof needs a separately authorized B0.3 investigation. No verified candidate may be published from these receipts.

Separately executed, manifest-admitted B0.4 initial tests had 63 passed / 0 failed / 2 skipped; the first scoped correction had 66 / 0 / 2. Final correction round2 had **67 passed / 0 failed / 2 skipped**, with 69 identically collected cases on twelve actual workers. Both allowed correction rounds are used. The initial unavailable temporary proof directory is retained as UNKNOWN with no pytest execution. Synthetic process termination/fsync observations do not prove power-loss or storage-controller durability. Native symlink/junction skips remain UNPROVEN even when lexical path and reparse guards pass.

| Group | Receipt stage | Passed | Failed | Skipped |
|---|---|---:|---:|---:|
| A06 | Initial native controller | 7 | 0 | 0 |
| B0.1 | Initial native controller | 82 | 0 | 0 |
| B0.2 | Initial native controller | 60 | 0 | 0 |
| B0.3 | Initial native controller, UNPROVEN | 46 | 7 | 2 |
| B0.4 | Final correction round2 | 67 | 0 | 2 |
| A02 | Final forward consumer | 30 | 0 | 0 |
| A03 | Final forward consumer | 193 | 0 | 0 |
| A04 | Final forward consumer | 178 | 0 | 3 |
| A05 | Final forward consumer | 217 | 0 | 0 |
| CLI | Final forward consumer | 8 | 0 | 0 |

Every executed group above used its exact registered/generated `python -m pytest <files> -q -n 12`, the existing MT01 controller collection observer, and twelve independent worker-side ordered collection hashes. MT01 selected A06/B0.1/B0.2/B0.3/B0.4 with ONBOARD_CONSUMER_OWNER_CLOSURE, fallback=false; forward imports additionally reach exactly the listed A02–A05/CLI owners. The independent baseline oracle accepted the selection. B0.4's AC was UNKNOWN before manifest admission; its later planner label REGISTERED is not Brain acceptance.

Combined dated observations are 902 collected/JUnit outcomes: 888 passed, 7 failed, 7 skipped (895 non-skipped). This combines different receipt stages and **is not an aggregate PASS**. B0.4's final positive test observed nine independent CLI processes, exactly one native render, four durable journey events, actual FFmpeg/ffprobe full decode and identical receipt/evidence/summary re-export. Actual kill cuts covered before the native call, before START, after START, after A06, after OUTPUT_READY and after QA. Recovery did not render again. A real separately created B0.3 WAIT_BRAIN journey remained UNKNOWN through repeated B0.4 commands; a forged admission file was rejected and its journal stayed byte-identical.

Final B0.4 JUnit SHA256: `be6b47bb52f4d85a7c8355d8917a344be87d2eed7d4a95741c24056431802f1f`. Its observed receipt-file SHA256 was `d87042f321419ad71bf9ca2203f186ed43ab8ef41c3056d407a6e6739d768491`; MP4 SHA256 was `a81e8dbd70b643ee34d2a810c1d07f42a26e78aa07e672eec9137bb4c3503c9c`. These identify synthetic observations, not independent authority. The WorkItem retains exact argv, per-group counts, collection/artifact hashes, material properties, skipped controls, all initial outcomes and the seven named B0.3 timeout failures.

Only this report and the WorkItem received final reporting metadata after tests. Four Python files, the test and owner manifest match final tested bytes. All 783 baseline files and Git HEAD remain unchanged, and the candidate scope is exactly eight additions. Final-tree aggregate proof, required B0.3 acceptance, native link controls, hosted exact-head CI and independent Brain remain incomplete. **HOLD / NOT READY; no candidate, Git write, PR, merge, dispatch or live activation was performed.** Generated ignored `.local/b04-proof` engineering receipts are local synthetic diagnostics, not a durable public artifact delivery; raw fixture paths are excluded from the WorkItem.
