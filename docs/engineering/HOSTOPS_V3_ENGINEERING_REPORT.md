# HostOps v3 repair of EF02 #226

Base: `e267e0f2438fdb2ed8d0ec13b9357d547c2100e6`. Authorization: [#231](https://github.com/trung-via/ai-commerce/issues/231), [#225](https://github.com/trung-via/ai-commerce/issues/225), and [Brain decision](https://github.com/trung-via/ai-commerce/issues/225#issuecomment-6578284045). **MERGE_HOLD; LIVE_READY=false; E2E_READY=false.** This executor edits ordinary files only. EF01 verifies; EF02 alone may publish a candidate. Candidate commit SHA: **unassigned/null**.

## Recovery and provenance

Only the exact old worktree `ai-commerce-ef02-hostopsv3_e2e-3l4ogx53`, adjacent to this owned worktree in the runner's original temporary worktree area, was inspected. Its read-only observed HEAD is `846110081b0cae3b490e36e142cdea945754df6a`. All **38/38** files match its prior `candidate-files.sha256.json`; the canonical path/hash map digest is `c8f166a4852d0201c9621859d7ad29bc957d0c863950a932d74a9922b5d25540`. Ancestors and file components have no symlink/reparse escape; each file is regular with link count one. A bounded credential/private-key-pattern scan found zero matches. The reviewed sources contain test-generated keys only, with no real key material. This is source recovery from a local prior manifest, **not recovery of a verified/published candidate**.

Copied 25 individually hash-validated ordinary source/schema/config/test/report files without overwriting current-main files. The original scratch remains untouched. The old scenario file was hash-checked separately; only a stable, corrected scenario mapping is retained under `hostops_v3_evidence`. Prior logs, XML, snapshots, per-run results and fixtures were not copied into publication. Recovery audit and original map are private local evidence under `.local/hostops-v3-repair/`.

**Independent artifact cross-check: `RECONCILE/RECOVERY_UNAVAILABLE`.** The connected GitHub artifact endpoint returned a download reference for artifact `13134355826`, but the bytes could not be retrieved here (DNS failure / fetch unavailable). Thus the original artifact's `engineering_result.json` was not independently compared with the local map. The live API identifies [run 41424651469](https://github.com/trung-via/ai-commerce/actions/runs/41424651469), artifact `ef02-results-41424651469-1`, archive digest `888a14d4d529051a01c93d2d2b6043d3adc970fbe69805f73e1cd3bad37bbd47`. That archive digest is not a source-map or candidate SHA. This limitation is retained for review; no artifact-anchored recovery claim is made.

## Causal changes and scope

- Keep the complete ordinary v3 package, four contracts, disabled configuration, six scripts, tests, this report and design. No existing tracked source or HostOps v2 file is changed.
- New ownership now covers `config/hostops-v3`, all six actual v3 scripts and `docs/engineering/hostops_v3_evidence`. The sole file in that evidence directory is the small stable `scenarios.json`. Run-specific output is under ignored `.local`.
- Preserve GitGuard, protected-path checks and `PREVERIFY_GIT_FILTER_MISMATCH`. Before testing, use the repository's existing normalization helper only when Git proves a CRLF-only conversion, then prove raw and Git-clean blob hashes agree. No Git configuration, index, HEAD or ref writes.
- `scripts/hostops_v3_verify.py` refuses a restricted Codex/guard child and writes any parent-context diagnostic output to `.local`. It never clears inherited guard/environment. Actual registered verification remains the existing EF01 parent phase.
- Broker execution returns **BLOCKED / CONDITIONAL_WRITE_ATOMICITY_UNPROVEN** for replace, patch, append, move, rename and quarantine. No lease or configuration flag enables these effects. Preview and observational UNKNOWN reconciliation remain available. Create uses exclusive creation; copy uses a hash-checked snapshot and exclusive destination. Conditional adapter code is retained as a prototype and tested directly only on fixture files.
- A deterministic regression places an external edit after the prototype's final hash check and before replace. The prototype overwrites it despite its own successful postcondition. This demonstrates the missing CAS guarantee and justifies the block; it is not atomicity acceptance. Existing derived scenario tests remain collected and now distinguish broker denial from direct prototype behavior.

Allowed roots: `src/ai_commerce/hostops_v3`; the exact six scripts; `config/hostops-v3`; four `contracts/hostops-v3.*.schema.json`; three `tests/test_hostops_v3*.py`; the two named design/report files; stable scenario manifest. There is no `.github` change, registry/selector change, installation, ACL/account mutation, live file operation, desktop/Chrome/Wake action, external message or merge.

## Verification phase and failure roots

Local targeted results are recorded below after execution. Registered **`project-tests` = `python -m pytest tests -q -n 12`** must execute in the existing EF01 parent after this executor returns, on this exact current base plus the final file fingerprint. No broad run is launched inside this restricted Codex session. Do not substitute a helper or focused pass for registered evidence. At most two bounded Verify–Fix rounds are authorized; parent verification owns subsequent failure feedback.

The old **3513 passed / 48 failed / 23 skipped** is historical: 45 sandbox MSYS/Git fixture failures (`NtCreateDirectoryObject ... 0xC0000022`, git init 128), two separate D5 private-path / D2 concurrent-registry failures, and one MT01 `NEW_UNMAPPED_TEST_DEFINITION` selector failure. These are not 48 v3 functional defects. The 12 intervening main commits mean none can be carried forward as a current result. Do not skip/remove those tests or weaken their guards. If parent-context fixtures still fail, preserve exact IDs/causes and repair only authorized execution preconditions. Shared runner/CI/map changes require an explicitly scoped handoff.

Current new test files have no reviewed MT01 family registration. The unchanged real A12 selector regression can therefore still require protected/shared-map integration; a precise targeted check will record its current behavior. The executor cannot edit `src/ai_commerce/engineering/minimum_test.py:approved_groups()` or `.github` under this action. The source of the unconditional global check is `.github/scripts/ef02-pr-ci-tests.py:197`; it requires the reviewed mapping for the three new test files. Broad verification is required by `MT01_ACCEPTANCE_JSON:["MT01.ACCEPTANCE_UNDECLARED"]`.

## Original plan and acceptance

[#225](https://github.com/trung-via/ai-commerce/issues/225) and its comments expose category ranges, not the referenced original document `AI_Commerce_HostOps_E2E_Full_Laptop_ReadWrite_Approval_2026-11-02.md`. That document is absent from the repository evidence inspected. The 48 derived IDs/test functions are retained, but **exact original scenarios validated = 0; parity = BLOCKED_ORIGINAL_ROW_TEXT_UNAVAILABLE**. Do not relabel 48 fixture test passes as original-plan or live E2E validation. Rows 10–12, 18 and 20–23 explicitly retain BLOCKED_ATOMICITY acceptance.

| AC | Available engineering evidence | Remaining acceptance requirement |
|---|---|---|
| A01 | Scoped metadata/read and simulated bridge | Real ChatGPT read on eligible laptop volumes |
| A02 | Binary chunks, complete/chunk hashes, signed disclosure | Actual large-binary reconstruction through installed bridge |
| A03 | Exclusive fixture create outside old root; prototype conditional APIs | Signed installed-host write+SHA; conditional writes BLOCKED |
| A04 | Independent real test-key signatures, revoke/expiry/tamper denials | Production enrollment and no-effect evidence |
| A05 | Durable intent, replay, crash/report loss, UNKNOWN observation | Native conditional atomicity fix; real crash/power-loss evidence |
| A06 | Fixture Unicode/path/link/permission/offline/disk-full cases | Actual account ACL, UNC, reparse and multi-volume evidence; Linux run |
| A07 | Token/SID/elevation and conservative ACL admission | Installed dedicated-account attestation |
| A08 | Separate identities, SID/peer checks, authenticated local IPC | Actual CI/EF02 denial and protected installation/task proof |
| A09 | Signed immutable release and workflow digest independent of main drift | Reviewed production release and protected workflow integration |
| A10 | Kill/revoke checkpoints and bounded worker watchdog | Installed disable/reboot proof |
| A11 | Private local selections; public receipt allowlist; independent disclosure | Live confidentiality review and protected issuer provisioning |
| A12 | Simulated GitHub → signed worker → sanitized receipt | Real ChatGPT → GitHub → Windows → receipt → ChatGPT |

All live AC are UNVERIFIED/BLOCKED. Native Windows IPC/hardlink denial can invoke explicitly recorded OS fault tests; that does not prove two-account native admission. Linux is not run here. Conditional features require a reviewed engineering fix, not merely installer consent.

## Protected integration design — not installed or executed

Proposed exact `.github/workflows/hostops-v3.yml` content follows. This is a hosted queue acknowledgement only: it has no laptop job, checkout, broker token or Issue body in shell expressions. Hash its UTF-8 LF bytes including final newline into the independently signed release only after protected review in the same composite PR. Queue status is `QUEUED_UNVERIFIED`; broker signatures remain authoritative. No free-form `workspace_file_write` execution is used.

```yaml
name: AI Commerce HostOps v3 signed broker queue
run-name: "HostOpsV3 Issue #${{ github.event.issue.number }}"
on:
  issues:
    types: [opened]
permissions:
  contents: read
concurrency:
  group: hostops-v3-issue-${{ github.event.issue.number }}
  cancel-in-progress: false
jobs:
  queue:
    if: >-
      github.event.repository.private == true &&
      github.event.issue.user.login == github.repository_owner &&
      startsWith(github.event.issue.title, '[HOSTOPS3] ')
    runs-on: ubuntu-latest
    timeout-minutes: 3
    outputs:
      issue_number: ${{ steps.envelope.outputs.issue_number }}
      envelope_sha256: ${{ steps.envelope.outputs.envelope_sha256 }}
      status: QUEUED_UNVERIFIED
    steps:
      - name: Capture bounded signed-envelope digest only
        id: envelope
        shell: bash
        run: |
          python3 - <<'PY'
          import hashlib, json, os
          def reject(*args):
              raise ValueError('HOSTOPS_V3_INVALID_ENVELOPE')
          def unique(pairs):
              result = {}
              for key, value in pairs:
                  if key in result:
                      reject()
                  result[key] = value
              return result
          with open(os.environ['GITHUB_EVENT_PATH'], encoding='utf-8') as f:
              event = json.load(f)
          issue = event['issue']
          body = issue.get('body')
          if type(body) is not str or not 0 < len(body.encode('utf-8')) <= 131072:
              reject()
          try:
              value = json.loads(body, object_pairs_hook=unique,
                                 parse_float=reject, parse_constant=reject)
              fields = {'schema', 'request', 'issuer_signature_b64', 'lease',
                        'lease_signature_b64', 'action_signature_b64'}
              if type(value) is not dict or set(value) != fields or value['schema'] != 'hostops.envelope.v3':
                  reject()
              canonical = (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')
              number = issue['number']
              if type(number) is not int or number < 1:
                  reject()
          except Exception:
              raise SystemExit('HOSTOPS_V3_INVALID_ENVELOPE')
          with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='ascii') as f:
              f.write('issue_number=' + str(number) + '\n')
              f.write('envelope_sha256=' + hashlib.sha256(canonical).hexdigest() + '\n')
          print('HOSTOPS_V3_QUEUED_UNVERIFIED')
          PY
```

Workflow outputs: `issue_number`, canonical `envelope_sha256`, `status`. The outbound installed poller verifies private repository, actual owner, run event/status/conclusion/path/repository and workflow bytes at the run SHA before submitting to authenticated IPC. Only sanitized receipts are automatically posted. Explicit signed disclosures bind classification, content hash, chunk and exact Issue recipient; they are separate from ordinary reads.

Protected/shared test-map handoff: the following finite addition to `src/ai_commerce/engineering/minimum_test.py:approved_groups()` is review material only. That source is outside this action's allowed paths. It avoids changing the selector guard, skipping tests or adding names to `.github/scripts/ef02-pr-ci-tests.py` exceptions. It is not applied here.

```python
TestGroup(
    "hostops-v3",
    ("tests/test_hostops_v3.py", "tests/test_hostops_v3_integration.py",
     "tests/test_hostops_v3_repair.py"),
    ("HOSTOPS.v3.security-contract", "HOSTOPS.v3.issue225-matrix"),
    sources("hostops_v3", "__init__ contract auth filesystem journal worker security broker transport cli") + (
        "scripts/hostops_v3.py", "scripts/hostops_v3_release.py",
        "scripts/hostops_v3_schemas.py", "scripts/hostops_v3_verify.py",
        "scripts/HostOps-V3-Launch.py", "scripts/HostOps-V3-TokenInventory.ps1"),
    contracts=("contracts/hostops-v3.request.schema.json",
               "contracts/hostops-v3.lease.schema.json",
               "contracts/hostops-v3.envelope.schema.json",
               "contracts/hostops-v3.receipt.schema.json",
               "config/hostops-v3/config.example.json",
               "config/hostops-v3/revocations.example.json")),
```

Preserve broad project-tests selection for this new security/contract family and undeclared acceptance. No narrow registry entry is proposed. Windows and Linux parent-context project-tests, plus later separately authorized dedicated-account live acceptance, are required integration checks. The ordinary repair cannot be declared fully verified while the unchanged current MT01 regression fails.

## Installation, disable and recovery — prospective only

1. Review the composite PR, protected handoff, missing-plan reconciliation, independent artifact cross-check and conditional-write block. Obtain EF02's actual verified candidate SHA. Keep `enabled=false` and a protected `KILL` marker until all prerequisites for the intended operation set are met; conditional writes remain blocked by code.
2. Through the Human's normal installer, select distinct non-admin broker and transport accounts, neither a CI/EF02 identity. Existing account ACLs decide accessible mounted volumes/authorized UNC roots. Do not grant Administrator/SYSTEM, bypass UAC, change volume ACLs automatically or impersonate the user. If isolation cannot be demonstrated, stop.
3. Protect the absolute interpreter, versioned installed source, policy, ancestors and scheduled-process definitions from CI/EF02/transport modification. Broker state/journal/selections must be private to broker/trusted installer; only broker/transport may read the 32-byte IPC key. Provision no public network listener. Linux requires separately reviewed directory/group/socket permissions.
4. Replace disabled example paths/SIDs and verifier/PowerShell digests. Enroll disjoint public Ed25519 issuer and `hostops-human` keys; private Human keys stay outside GitHub, CI and runtime accounts. Use the monotonic revocation example. No live key is provided in this patch.
5. Build with `python scripts/hostops_v3_release.py --root REVIEWED_RELEASE --source-sha FINAL_CANDIDATE_SHA --workflow-sha256 EXACT_REVIEWED_DIGEST --output REVIEWED_RELEASE/release.json`. Independently review and sign `release.to-sign.json` using `ssh-keygen -Y sign -n hostops-v3-release -f HUMAN_PRIVATE_KEY release.to-sign.json`; install only the public signature in `release.sig.b64`. Preserve the reviewed installed bytes, including script newlines. The builder does not authenticate or publish a commit.
6. Use a fixed absolute launcher: `APPROVED_PYTHON -I -B REVIEWED_RELEASE/scripts/HostOps-V3-Launch.py broker --config PROTECTED_CONFIG`, and a separate scheduled `poll --config PROTECTED_CONFIG --token-file PRIVATE_TRANSPORT_TOKEN` under transport. The token needs private-repository Issues read/write, Actions read, Contents read; no code-write/dispatch. Broker imports installed source only. Provision an issuer signing service separately; ChatGPT holds no private key.
7. Sign narrow revocable leases (`hostops-v3-lease`) and canonical requests (`hostops-v3-request`); max lease 24 hours, request 300 seconds. Local read selections do not authorize export. Every disclosure needs an independent `hostops-v3-exact-action` signature and explicit recipient/size/class. Do not upload personal data in routine artifacts. Test real operations only under separate live authorization.

Disable by placing protected `KILL`, disabling installed processes, setting `enabled=false`, advancing revocation epoch and revoking leases/token/IPC access. Keep unresolved intents; never clear the journal or retry an UNKNOWN write because a GitHub response was lost. Reconciliation observes only and remains UNKNOWN.

Software rollback selects a previously reviewed signed release; it does not reverse filesystem changes. Prototype cross-volume move may leave both files; quarantine/partial creates have no universal inverse. Inspect partial/staging/quarantine files privately, restore from verified backup or obtain a new explicit repair. Uninstall only after stopping processes/revoking credentials, preserving a private journal archive and checking exact resolved install/state paths. Do not recursively delete leased volumes. V2 remains independently callable.

## Current verification receipt

**Local targeted round 1: PASS, 111 passed / 1 skipped, exit 0, 6.17 s pytest / 7.044 s wrapper.** UTC span `2026-11-02T13:50:06.010101+00:00`–`2026-11-02T13:50:13.054304+00:00`. This includes **74/74 v3 test rows** (48 derived scenarios, 16 integration/provenance rows, 10 repair rows) and 37 passing v1/v2 regression rows. The unchanged conditional v2 recovery test skipped because native symlinks are unavailable. No tests were deselected or removed.

Exact targeted argv:

```text
python -m pytest tests/test_hostops_v3.py tests/test_hostops_v3_integration.py tests/test_hostops_v3_repair.py tests/test_hostops_v2.py tests/test_hostops_request.py tests/test_hostops_ef02_202_recovery.py -q -n 12 --basetemp=.local/hostops-v3-repair/pytest-round1 --junitxml=.local/hostops-v3-repair/targeted-round1.xml
```

Log SHA256: `0712e331cea82a73431f6b49fc35a0b25463c8200035899127351e436db62870`. The XML confirms all 48 derived IDs and records `native_hardlinks=OS_DENIED; link-count fault injection used` and `native_ipc=OS_DENIED_WINERROR_5; mocked OS byte stream`. There is no native two-account IPC or original-plan parity claim. Twelve workers were requested; complete observed-worker telemetry is not claimed.

**Separate current MT01 diagnostic: FAILED, 1 failed, exit 1, 0.76 s pytest.** Exact test `tests/test_minimum_test.py::test_pr_ci_real_a12_component_can_select_narrow_test_files`, with `--basetemp=.local/hostops-v3-repair/pytest-mt01-current`, returned `FULL_PROJECT_REQUIRED / NEW_UNMAPPED_TEST_DEFINITION` where it expects `SELECTED_AFFECTED_INTEGRATION`. This is a reproducible shared-map integration blocker on current base plus these additions; it is not a v3 filesystem failure. Its log/result are `.local/hostops-v3-repair/mt01-current.*`. No retry can fix the missing reviewed map without the out-of-scope source change above.

Python AST and PowerShell parse checks passed; no inventory script was executed. Pre-test scope and raw/Git-clean identity passed for all 28 new ordinary files, with no normalization needed. All pre-existing tracked files are unchanged. Per-file final SHA256s, scope/byte checks, old-worktree recheck and machine-readable blocked result are `.local/hostops-v3-repair/final-candidate-files.sha256.json`, `final-integrity.json`, and `repair-result.json`. These are not candidate commit identities.

**Registered EF01 project-tests: PENDING_PARENT_EXECUTION, no result claimed.** No broad suite was launched inside Codex. The existing enclosing EF01 verifier must execute it after executor return, retaining Git/fixture protections and binding final file hashes to the actual result. Old MSYS fixture/D5/D2 outcomes are not reclassified without that run. One local verification batch was used; no repeated broad sandbox attempt or second blind retry occurred. Candidate SHA remains null, all live AC remain unverified, and MERGE_HOLD remains in force.
