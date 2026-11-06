# D5 HostOps #446 repair — source only

This repair is a new immutable action, `HOSTOPS-D5-446-CODEX-XHIGH-ROOT-REPAIR-01`, with Human-selected Codex `gpt-6.1-sol`, effort `xhigh`, and no Antigravity fallback. It edits ordinary allowed worktree files only. EF02 alone may publish a verified candidate; the executor must not change Git HEAD, refs, config, old attempts or their receipts.

Inspected checkout: detached HEAD `4f99064251d53c7e1c6d8449524230fa6fac979a`, with source attempt's exact parent/main `655668b41eebb721762bcc5b4bf6aadf6a153351` (PR #440). The original #446 candidate changed only this document, `d5_control.py`, and its owner test. Original attempt `hostops_d5_receiver_655668b_20261105_r1`, run [42070072771](https://github.com/trung-via/ai-commerce/actions/runs/42070072771), artifact ID `13437357665`, candidate branch `ef02/candidate/0f92fb4a537eb56deee220d46ed81f57` are historical **SOURCE ONLY** evidence. CANDIDATE_READY and workflow success are neither Brain PASS nor deployment. The artifact ID is supplied provenance; its contents were not reused as repair verification. No existing attempt was replayed.

Current repair state: **SOURCE_CHANGES_PREPARED / VERIFICATION_HOLD**. `SOURCE_READY` requires genuine scoped EF01 verification and an EF02-published exact SHA; no new SHA exists in this executor worktree. Live acceptance remains **PENDING_GENUINE_HOSTOPS_DEPLOYMENT**. Protected workflow integration remains **PROTECTED_INGRESS_NOT_MERGED**.

## Preserved behavior and authority

Option 1 remains dedicated Chrome acquisition with the existing private profile/config ownership guard, loopback `http://127.0.0.1:9333`, and two bounded Playwright attaches without pages/chat/cookie access. Historical Downloads script bytes remain unavailable, unexecuted and unaudited. The acquisition source is preserved; no reset chain, extra Chrome root, forced kill, lock/profile deletion, personal Chrome access, privilege escalation, #179 resend, Wake, chat submission or provider effect is authorized.

All three tasks remain mandatory and unchanged: `AI-Commerce-EF02-Interactive`, `AI-Commerce-EF02-02-Interactive`, `AI-Commerce-HostOps-01-Interactive`. The PowerShell adapter rechecks exact task names and pre/post XML hashes. An unknown D5 task, conflicting launcher, ambiguous process or ownership mismatch blocks effects.

The candidate's valid stop repair remains: an exact identity-verified `PROFILE_ALREADY_ACTIVE_NO_CDP` root may receive only bounded `CloseMainWindow()` and a ten-second exit wait. Identity is revalidated using owner SID/session, creation time, signed executable and dedicated argv. Every other mutation holds in that state. Absence must be independently observed after stopping; uncertain exit is UNKNOWN, never a relaunch permission.

Current protected Personal ingress was inspected at exact main. Its only active HostOps routes are `[HOSTOPS READ]` and separately approved `[HOSTOPS WRITE]`; Personal standing READ does not authorize D5. Both public repositories and the shared runner SID fail the independent-custody prerequisite. Public repository ownership, Issue/comment authorship, runner labels, same-SID ACLs, a caller dictionary, a boolean callback, and local HMAC possession are never D5 authentication.

## Runnable fixed entrypoints

Implementation and offline tests stay in the already registered `d5_control.py` / `test_hostops_d5_control.py` owner. The existing owner manifest is unchanged: the trusted planner rejects mutation of persisted owners. No protected registry or workflow is changed.

The following commands are implemented source interfaces. They are **not** commands executed by this source repair:

```powershell
# Independently installed protected operator runtime; attended startup only.
# Its reviewed python._pth must expose only the signed source/runtime closure.
& "$env:ProgramFiles\AI-Commerce\D5-Runtime\python.exe" -I -B -m ai_commerce.hostops_v3.d5_control --serve

# Public carrier: materialize the fixed signed request from the actual event.
python -B -m ai_commerce.hostops_v3.d5_control --prepare-event "$env:GITHUB_EVENT_PATH" --output "$env:RUNNER_TEMP\d5-request.json"
# Send only that public signed envelope over the fixed local pipe; output is sanitized.
python -B -m ai_commerce.hostops_v3.d5_control --request "$env:RUNNER_TEMP\d5-request.json" --output "$env:RUNNER_TEMP\d5-receipt.json"
```

`--prepare-event` accepts an owner-authored D5 Issue opened event or an owner-authored signed-envelope comment on that Issue. A comment supports signing after the Issue number is known. It binds the signed `issue_number`. The event is a carrier, never an authorization. Files are bounded, duplicate keys are rejected, and outputs are create-new: old input/receipt evidence is not overwritten. A missing pipe returns `BLOCKED_NOT_DEPLOYED`; broken/timeout delivery never retries an operation. No HTTP endpoint, shell-by-Issue, request-selected transport, command, script, path, PID, task or environment is accepted.

`--serve` refuses Actions execution, verifies enrollment/source/custody before opening the pipe, reloads them for each operation and logon, and serves exactly `AI-Commerce-D5-Operator-v1` using local-only Windows message pipes. It uses first-instance creation, a fixed exact-SID DACL, kernel peer process/session IDs, process and impersonated token SID/session/elevation, active WTS session, input desktop ownership, and peer thread desktop checks. Logon additionally requires the kernel-selected PowerShell peer to run exactly the source-pinned fixed launcher and -Logon argv, using a bounded read-only peer query; a different script or command is rejected. No supplied dictionary is trusted. A frame-bound single-use internal capability is issued only after these checks. The carrier also verifies the server process uses the fixed protected runtime. The native fixed `D5_LOGON_START_V1\n` path uses this same receiver. The reviewed `-Logon` launcher now requests impersonation, reads a bounded acknowledgment, and distinguishes delivery from the nested actual lifecycle receipt. Neither registering a task nor receiving a trigger proves Chrome startup.

The private adapter's existing local MAC is only internal message integrity. Before native operations it invokes the fixed protected runtime's real `--native-preflight` entrypoint. That gate independently reloads the signed grant and fresh custody/revocation lease, verifies the pinned executing source, exact policy fields and existing durable INTENT, and cannot create an intent. Missing proof blocks even a caller possessing the local integrity key. The current shared-SID runtime has no qualifying custody proof and must remain disabled.

## Public-key enrollment and closed request protocol

There is no signer or private signing key in this source, runner environment, public repository or task action. Provisioning/maintaining the real off-host authority and proving runtime isolation are independent deployment work, not implied by these interfaces.

The root trust anchor is fixed at the protected installation's `AI-Commerce/D5-Trust/anchor.json`. It has exactly `schema=ai-commerce.d5.root.v1`, `identity` (a `d5-` identifier) and `public_key` (one OpenSSH Ed25519 public key). The file/installation must have SYSTEM/Administrators/TrustedInstaller ownership, no untrusted writable allow ACEs, and no reparse ancestors. Operator-owned same-SID files cannot enroll a root. Windows OpenSSH `ssh-keygen.exe` is resolved only from the protected system installation. Verification uses fixed namespaces, canonical JSON and bounded subprocess timeouts; only public signature and allowed-signer data enter temporary files.

Every signed envelope has exactly `body` (an object) and `signature` (base64 of an OpenSSH armored signature over canonical sorted compact JSON). Enrollment and custody leases are delivered **privately**, never via a public Issue, because their signed bodies contain private deployment metadata. Only operation envelopes may use a public carrier.

The one-time root-signed grant uses namespace `ai-commerce-d5-enrollment-v1`. Its exact fields are `schema=ai-commerce.d5.enrollment.v1`, `enrollment_id` (32 lowercase hex), integer `issued`/`expires` (maximum 24 hours), `issuer`, `issuer_public_key`, `custody`, and `native`. `custody` contains exactly `signer=OFF_HOST_PRIVATE`, `operator=INDEPENDENT_EXECUTION_BOUNDARY`, and an independently reviewed `audit_sha256`. The root authority must verify the actual arrangement before signing; it must not equate same-SID ACLs with isolation. `native` contains the prior exact enrolled fields: source SHA, epoch, SID, expiry, release root/file hashes, Chrome config/local AppData, fixed state/key file, legacy task hashes, three runner tasks, enabled/revoked/private-control-plane/public-runner-exposure flags. Private paths and the local integrity key never become public request fields.

Every call also needs a root-signed lease at fixed private `custody-lease.json`, namespace `ai-commerce-d5-custody-v1`. Its exact fields are `schema=ai-commerce.d5.custody-lease.v1`, `enrollment_id`, `source_sha`, `epoch`, `grant_sha256`, monotonically increasing `serial`, integer `issued`/`expires` with at most 60 seconds validity, and boolean `revoked`, `signer_exposed`, `runtime_exposed` all false. Missing, stale, revoked or exposed custody fails closed. Revocation is bounded by the maximum 60-second lease, not claimed instantaneous. A durable FULL-synchronous authority journal pins one grant, durably latches observed revocation/exposure, and prevents serial rollback or replacement enrollment; rotation requires attended reconciliation and archived replay/UNKNOWN evidence, never automatic journal deletion.

The issuer signs operation envelopes in namespace `ai-commerce-d5-request-v1`. The body has exactly:

```json
{"schema":"ai-commerce.d5.request.v1","op":"STATUS","request_id":"<32 lowercase hex>","nonce":"<independent 32 lowercase hex>","issued":0,"expires":0,"source_sha":"<exact reviewed deployed 40-hex SHA>","epoch":1,"enrollment_id":"<32 lowercase hex>","issue_number":446,"dry_run":true}
```

This is a field/type illustration, not a valid request. Time must be current; TTL is at most 60 seconds. The issuer, namespace, source, epoch, grant, Issue number, nonce and complete body are covered by the signature. The receiver checks freshness twice, never extends the signed deadline, and reserves request ID and nonce atomically with INTENT in its FULL-synchronous SQLite journal. A replay, crashed INTENT, UNKNOWN, pending rotation, or recovery-rate limit blocks all further mutations. Authentication has no callback returning True and no caller-provided custody enable flag.

The fixed operation catalog is STATUS, MIGRATION_PREFLIGHT, RETIRE_NEW_AUTOSTART, STOP_OWNED_D5_CHROME, INSTALL_VERIFIED_OLD_BEHAVIOR_LAUNCHER, START_OR_REUSE_D5_CHROME, VERIFY_CDP_9333_AND_PLAYWRIGHT, INSTALL_SINGLE_LOGON_AUTOSTART, REMOVE_D5_AUTOSTART, RECOVER_BOUNDED, ROLLBACK. All are parameterless except the signed dry-run boolean. START can create one root only from verified absence/no claim/no lock; RECOVER is bounded/rate-limited; rollback disables the reviewed D5 baseline without restoring an unproven chain.

PowerShell saves a create-new native nonce and correlated native receipt, preserving pre/post runner hashes and private process identity locally. Python independently reads back that exact receipt and verifies request/source/operation/session/snapshot/result/task hashes before accepting stdout success. Missing or inconsistent readback becomes UNKNOWN. Public responses validate enum values, booleans, IDs/SHA/epoch and bounded nesting, not just field names; paths, SID, raw process data, arbitrary strings, keys/cookies/chat data and exceptions cannot pass through an allowlisted key.

## Exact protected workflow handoff — PROTECTED_INGRESS_NOT_MERGED

This is a complete job insertion under `jobs:` in the exact inspected `issue-ingress.yml`, not a mutation performed by EF02. The protected owner must independently review its exact merged source, runtime/custody proof and test closure before enabling the job. `HOSTOPS_D5_PROTECTED_ENABLE` is only a scheduling kill switch; it conveys no authentication or permission. The carrier contains no private credentials and must never load/sign enrollment. The known current shared-SID setup remains inadmissible for effects.

```yaml
  hostops-d5-carrier:
    if: >-
      vars.HOSTOPS_D5_PROTECTED_ENABLE == 'REVIEWED' &&
      github.repository == 'trung-via/ai-commerce' &&
      github.actor == github.repository_owner &&
      github.event_name == 'issue_comment' && github.event.action == 'created' &&
      github.event.issue.user.login == github.repository_owner &&
      github.event.comment.user.login == github.repository_owner &&
      startsWith(github.event.issue.title, '[HOSTOPS D5] ')
    runs-on: [self-hosted, Windows, ai-commerce-hostops]
    timeout-minutes: 3
    permissions:
      contents: read
      issues: write
    concurrency:
      group: d5-signed-carrier
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
          ref: ${{ github.sha }}
      - name: Materialize and relay fixed signed envelope
        shell: pwsh
        run: |
          $env:PYTHONPATH = Join-Path $env:GITHUB_WORKSPACE 'src'
          $request = Join-Path $env:RUNNER_TEMP "d5-$($env:GITHUB_RUN_ID)-$($env:GITHUB_RUN_ATTEMPT)-request.json"
          $receipt = Join-Path $env:RUNNER_TEMP "d5-$($env:GITHUB_RUN_ID)-$($env:GITHUB_RUN_ATTEMPT)-receipt.json"
          python -B -m ai_commerce.hostops_v3.d5_control --prepare-event "$env:GITHUB_EVENT_PATH" --output "$request"
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          python -B -m ai_commerce.hostops_v3.d5_control --request "$request" --output "$receipt"
          exit $LASTEXITCODE
      - name: Publish independently sanitized receipt to same Issue
        if: always()
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs'), path = require('path');
            const file = path.join(process.env.RUNNER_TEMP,
              `d5-${process.env.GITHUB_RUN_ID}-${process.env.GITHUB_RUN_ATTEMPT}-receipt.json`);
            const ops = ['STATUS','MIGRATION_PREFLIGHT','RETIRE_NEW_AUTOSTART','STOP_OWNED_D5_CHROME',
              'INSTALL_VERIFIED_OLD_BEHAVIOR_LAUNCHER','START_OR_REUSE_D5_CHROME',
              'VERIFY_CDP_9333_AND_PLAYWRIGHT','INSTALL_SINGLE_LOGON_AUTOSTART',
              'REMOVE_D5_AUTOSTART','RECOVER_BOUNDED','ROLLBACK'];
            const codes = ['BLOCKED_NOT_DEPLOYED','INPUT_INVALID','SIGNATURE_INVALID','REVOKED_OR_STALE',
              'REQUEST_INVALID_OR_STALE','REQUEST_REPLAY','ENROLLMENT_REUSE_DENIED','UNKNOWN_EFFECT_HOLD',
              'PIPE_UNAVAILABLE','INVALID_PUBLIC_RECEIPT','SOURCE_DRIFT','OWNERSHIP_UNPROVEN',
              'PROFILE_ALREADY_ACTIVE_NO_CDP','RUNNER_PRESERVATION_UNPROVEN','TASK_OWNERSHIP_UNPROVEN',
              'UNSUPPORTED_SESSION','NATIVE_FAILURE_HOLD','CONFLICTING_D5_TASK','LAUNCHER_UNVERIFIED',
              'PROFILE_LOCKED','BACKUP_UNVERIFIED','LAUNCH_NOT_AUTHORIZED','PLAYWRIGHT_UNAVAILABLE',
              'CDP_PROTOCOL_UNAVAILABLE','JOURNAL_UNAVAILABLE','JOURNAL_ROTATION_REQUIRED','RECOVERY_RATE_LIMIT',
              'DRY_RUN','NOT_RUNNING','ALREADY_STOPPED','MIGRATION_READY','RETIRED','STOPPED',
              'LAUNCHER_INSTALLED','READY_ATTACH','REGISTERED','AUTOSTART_REMOVED','ROLLED_BACK_DISABLED',
              'ROOT_PROCESS_LOST','CDP_LISTENER_LOST','BROWSER_RESTARTED','BROWSER_MUST_BE_STOPPED'];
            const flags = ['effect_observed','registered','started','listener_bound','playwright_attached',
              'stable','wake_sent','login_verified','manifest_verified'];
            const allowed = new Set(['status','code','operation','request_id','source_sha','epoch',...flags]);
            let safe = {status:'BLOCKED',code:'INVALID_PUBLIC_RECEIPT',effect_observed:false};
            try {
              if (fs.statSync(file).size > 8192) throw Error();
              const raw = fs.readFileSync(file,'utf8'), r = JSON.parse(raw);
              if (!r || Array.isArray(r) || raw !== JSON.stringify(r) ||
                  Object.keys(r).some(k => !allowed.has(k)) ||
                  !['BLOCKED','OBSERVED'].includes(r.status) || !codes.includes(r.code) ||
                  (r.operation !== undefined && ![...ops,'UNKNOWN'].includes(r.operation)) ||
                  flags.some(k => r[k] !== undefined && typeof r[k] !== 'boolean') ||
                  ['wake_sent','login_verified','manifest_verified'].some(k => r[k] !== undefined && r[k] !== false)) throw Error();
              const signed = JSON.parse(context.payload.comment.body).body;
              if (r.status === 'OBSERVED' &&
                  (!ops.includes(r.operation) || r.operation !== signed.op ||
                   !/^[0-9a-f]{32}$/.test(r.request_id) || r.request_id !== signed.request_id ||
                   !/^[0-9a-f]{40}$/.test(r.source_sha) || r.source_sha !== context.sha ||
                   r.source_sha !== signed.source_sha || !Number.isInteger(r.epoch) || r.epoch < 1 ||
                   r.epoch !== signed.epoch || signed.issue_number !== context.issue.number)) throw Error();
              // Validate optional IDs even on a BLOCKED receipt before copying them.
              if ((r.request_id !== undefined && !/^[0-9a-f]{32}$/.test(r.request_id)) ||
                  (r.source_sha !== undefined && !/^[0-9a-f]{40}$/.test(r.source_sha)) ||
                  (r.epoch !== undefined && (!Number.isInteger(r.epoch) || r.epoch < 1))) throw Error();
              safe = r;
            } catch (_) {}
            await github.rest.issues.createComment({...context.repo, issue_number:context.issue.number,
              body:'AI_COMMERCE_D5_RESULT_V1\n```json\n'+JSON.stringify(safe)+'\n```'});
```

The job creates the input before invoking an implemented CLI, reads only its unique bounded output, independently validates public values, and reports a safe fallback on malformed/missing receipt. A separate protected workflow merge and actual native deployment receipts are still required. Do not copy this job into the protected file in an EF02 source attempt.

## Scoped verification and remaining gates

AC IDs are exactly `HOSTOPS.D5.enrolled-fixed-lifecycle`, `HOSTOPS.D5.ownership-migration-preservation`, `HOSTOPS.D5.replay-crash-unknown-privacy`. Synthetic tests use ephemeral Ed25519 keys generated outside checkout and the actual Windows OpenSSH verifier, plus simulated kernel/pipe/process/task/Chrome adapters. They exercise authenticated fixed lifecycle, dry-runs, repeated logon and one root, graceful verified stop, missing/forged peer attestation, wrong SID/session/elevation/desktop, unsigned/forged/wrong-namespace/expired/source-drift/replayed requests, nonce replay, revoked/exposed/untrusted/missing custody, one-time grant/lease rollback, unknown tasks/process ownership, broken pipes, crash/UNKNOWN and public value sanitization. No tests operate real tasks, desktop, Chrome, pipe, network, chat or Wake. PowerShell checks parse AST only.

Minimum sufficient closure is the existing D5 owner plus `hostops-v3`, persisted `hostops-personal`, and registered `ef02-wake` consumers, and the four exact independent PowerShell witnesses already protected on main. The trusted EF01 planner must derive its immutable group bindings from exact base/candidate, not a candidate-edited registry. Use true Windows self-host xdist `-n 12` per selected registered group; retain worker-start and collection logs, actual worker IDs/counts, pass/fail/skip/error counts and genuine tracebacks in private runner artifacts. Requested workers are not observed workers. No previous attempt proof is reused. Read-only impact inspection reports AFFECTED with 26 selected test files, the D5/HostOps v3/Wake groups and the independent Personal witness, with no missing source mapping. This inspection is not a trusted execution binding or a worker/consumer PASS.

Initial executor invocation `000` could not create scratch under the existing OS RUNNER_TEMP. Its subsequent genuine EF01 scoped verification did execute 12 collected Windows xdist workers per selected group: `ef02-wake` had 709 pass / 6 skip, `hostops-d5-control` had 134 pass / 25 fail, and `hostops-v3` had 183 pass / 1 skip. The failed D5 assertions consistently received `SIGNATURE_INVALID` before the intended lifecycle/custody result; the transport CLI consequently returned 2. This is a failed verification round, not scoped PASS. These earlier counts do not verify the repaired bytes.

Repair invocation `001` read the full feedback, source snapshot, tracked patch and verification logs and matched all supplied SHA-256 evidence hashes. Its initial four-file worktree matched the prior snapshot exactly. The verifier's LF-only SSH armor header gate rejected CRLF before invoking OpenSSH; it now accepts exactly LF or CRLF headers and passes the original signature and canonical body unchanged to the protected OpenSSH verifier. Six real-Ed25519 regression cases cover both line endings with valid signatures, tampered signed bodies, and invalid header delimiters. Existing unsigned, issuer, namespace, custody, replay and UNKNOWN checks remain mandatory. This correction does not normalize signed JSON or bypass cryptography.

This executor invocation again received Access denied when creating a fresh directory under the existing RUNNER_TEMP, before any pytest invocation. No temp relocation, ACL/permission change, cache inside checkout or alternate test invocation was attempted. Local pytest invocations = 0; the new tests and repaired lifecycle remain UNVERIFIED until EF01 reruns the genuine registered closure within the maximum two Verify-Fix rounds. In-memory compile/import/closed-schema smoke, PowerShell AST parse and diff whitespace checks pass; these are not unit/consumer or acceptance PASS. The current protected Personal ingress was re-read from GitHub and its normalized blob matches the untouched checkout (`b194d885956cf47482268fedfbed5042404a986b`); both repositories still report PUBLIC. EF01 must use its authorized existing RUNNER_TEMP with separate external base/cache/JUnit/log directories. Do not silently fall back to serial/full-suite or a different temp root.

Repair invocation `002` preserved HEAD `4f99064251d53c7e1c6d8449524230fa6fac979a` and all four cumulative changed files. It read both earlier verification logs and patches, the full feedback and source snapshots, and matched their recorded SHA-256 hashes, including feedback `cae10026b1ad0dfec26ef4fd36d5f23e0ceb94eb92ae02e7e90a448efcd936a4`. Invocation `001` actually failed with 27 D5 failures / 138 passes, including both valid armor cases; its Wake and HostOps v3 consumer counts remained 709 pass / 6 skip and 183 pass / 1 skip, with 12 collected Windows workers per group. The armor correction alone did not resolve the failure.

The additional determinate defect is the verifier's child environment: D5 used the Chrome subprocess allowlist, which omits `PROGRAMDATA`. The existing HostOps v3 signature environment explicitly preserves that Windows OpenSSH initialization dependency. A read-only diagnostic using fixed nonexistent synthetic input filenames exited 255 with no diagnostic under the Chrome environment; with the existing signature environment it reached the expected `Couldn't read signature file: No such file or directory` validation failure instead. No signature was accepted, and no file, pipe, network, task or Chrome effect occurred in this probe. D5 now reuses `auth.child_environment`, retaining only SYSTEMROOT/WINDIR/TEMP/TMP/PROGRAMDATA, with case-insensitive name matching. The browser environment remains untouched. The added real-Ed25519 regression checks the actual verifier subprocess environment on authenticated requests, preserves PROGRAMDATA, excludes synthetic GitHub/Actions/agent/Python environment credentials and requires an authenticated STATUS result without native effects. It does not stub signature verification or its return code.

Invocation `002` also received Access denied creating fresh scratch under the existing RUNNER_TEMP. No alternate scratch root, ACL change or escalation is authorized. The new real-crypto regression and registered 12-worker groups therefore require EF01 execution; the prior worker counts never qualify the repaired bytes. Both repositories were rechecked as PUBLIC; current GitHub main remains `655668b41eebb721762bcc5b4bf6aadf6a153351`, and its protected Personal ingress still has blob `b194d885956cf47482268fedfbed5042404a986b` with no D5 job. No protected file, persisted registry, immutable earlier evidence or Git ref was modified by this repair.

Invocation `002` passed in-memory compilation of both changed Python files, import/minimal signature-environment checks with synthetic credential sentinels, the real read-only OpenSSH initialization/missing-input rejection probe, PowerShell AST parsing without script execution, and `git diff --check`. Pytest invocations = 0; workers actual = unobserved for these repaired bytes. These checks are not scoped unit/consumer PASS or SOURCE_READY.

To qualify SOURCE_READY, EF01 must observe scoped PASS and EF02 must publish exact verified SHA/proof. Brain review remains independent. To qualify deployment, a trusted owner must independently merge protected ingress, provision the protected immutable runtime/root anchor, genuinely prove off-host signer and independent runtime custody while preserving all three runner tasks, and perform one attended enrollment. Native receiver-ready, subsequent logon, migration backup/retirement, graceful stop, stable CDP 9333, both Playwright attaches, bounded recovery, rollback and independent native receipt readback each require distinct genuine correlated receipts. Missing proof means BLOCKED_NOT_DEPLOYED / PENDING_GENUINE_HOSTOPS_DEPLOYMENT, never fabricated full closure.
