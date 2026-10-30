# EF02-D5.4 pinned-source reconstruction and verification handoff

Date: 2026-10-30. Source and executor HEAD:
`07ce567ee6250d162eaf695fc74a7f1def9bb001`.
This is ordinary worktree source/test/docs for EF01 verification and later
Human review. No candidate ref/SHA is published or claimed by this executor.

## Lineage and corrected source precondition

Connected GitHub reads inspected the complete original
[Issue #129](https://github.com/trung-via/ai-commerce/issues/129) AC1–AC5,
[its result comment](https://github.com/trung-via/ai-commerce/issues/129#issuecomment-6517261721),
[Issue #132](https://github.com/trung-via/ai-commerce/issues/132) and
[its result comment](https://github.com/trung-via/ai-commerce/issues/132#issuecomment-6518362308).
The comments confirm run `41012798751` ended `MODEL_AT_CAPACITY` and run
`41021315919` ended `NO_CODE_CHANGE` respectively. #132's request records
#129's six draft paths/checksums and a non-final 106-test observation.

The Human's current repair instruction supplies the additional result details:
#129 had no final registered verification/publishable candidate; #132 returned
exit 0 with no changed files/candidate and did not run official tests. Its old
instruction required mutable main to equal `99f1832...` after unrelated A12
PR #133 had advanced main. That obsolete workflow precondition is not evidence
of a D5 code/test defect. This attempt does not require live main equality.

The worker-prepared isolated worktree was clean at the new exact pinned SHA.
No other runner's private/temp paths were read. Complete authenticated bytes for
all six prior draft files were not established; hashes/Issue text alone cannot
recover missing new files. This implementation is independently reconstructed
from the pinned source and original/current acceptance criteria. No old patch,
test PASS or candidate proof is reused. No Git metadata/config/ref write,
publisher invocation, main merge or provider/model switch was performed.

## Scope and implementation

The six expected paths were preflighted before creation/editing. One additional
existing source consumer was preflighted: `src/ai_commerce/remote/_private_state.py`
is within the explicitly allowed source directory. AC2 requires the worker
never to wait for a contended listener lock. Its small optional
`locked(wait_seconds=0)` extension preserves the existing default five-second
lock, OS mechanisms, atomic replace and location checks. This avoids a daemon
whose atomic signal could be lost at worker process shutdown.

Final changed paths:

```text
docs/engineering/EF02_D5_4_IMPLEMENTATION_REPORT.md
docs/engineering/EF02_D5_4_LOCAL_HANDOFF.md
docs/engineering/EF02_D5_LOCAL_RUN_TRIGGER.md
src/ai_commerce/remote/__main__.py
src/ai_commerce/remote/_private_state.py
src/ai_commerce/remote/local_completion_handoff.py
tests/test_ef02_d5_local_handoff.py
```

No other flat tests, protected paths, workflows, scripts, credentials, runner
settings, A12 source or Git state were edited. The existing mixed line endings
in `__main__.py` were preserved; the worker diff adds twelve lines.
Source/check_source, content/hash, scope, hook/filter and publication gates
remain in place.

| AC | Implementation and deterministic evidence |
| --- | --- |
| AC1 | Literal operator enrollment binds exact repo/Issue/run/attempt/workflow/batch/source/issuer/actor, retained full-request fingerprint and finite UTC consent. One private 16-KiB record, strict native owner/DACL or POSIX permissions, existing OS lock/atomic state and no link/reparse/hardlink acceptance. No route data or secret persisted. |
| AC2 | Optional Windows hook runs only after final event/report writes. It requires literal local env opt-in and pre-completion enrollment, attempts one zero-wait local atomic selector hint and drops any failure without changing EF02 feedback/result/exit. No network, daemon, upload wait or worker send. |
| AC3 | Exact D5.3 registrar authenticates current Issue and immutable request/results, then D3.1 independently fetches exact results again. Run-in-progress and completed-before-upload races only cause bounded read-only waits. Bogus/mismatched/ambiguous evidence cannot authenticate a Wake. Restart preserves saved exact identity. |
| AC4 | Observe is default. Optional delivery requires delivery-enabled prior enrollment, preexisting literal send env gate, Windows interactive desktop/session, matching SID, private non-aliasing delivery files and existing D5.1/D3.1/D3.2/D2/D4. Durable handoff UNKNOWN precedes one invocation, survives crash and prevents replay. LIVE_WAKE_NOT_PROVEN and both proof flags remain false. |
| AC5 | Synthetic tests exercise native ACLs, permission/identity failure, lock contention/zero-wait, malformed/integrity/size/link checks, selectors, immutable fingerprint, upload race, finite reads/deadline/consent, duplicate/restart/concurrent consumers, no-send, write failure, worker failure isolation, real signed-grant/idempotency/CDP composition with a fake browser. Full independent acceptance remains pending the EF01 worker result. |

The operational document includes exact CLI steps, operator provisioning,
same-host/SID limitations, both opt-ins, finite budgets, fallback, stop and
rollback. No live enrollment, local signal or Wake was activated by this
executor outside isolated synthetic tests.

## Actual verification before this report was frozen

An initial red check failed collection because the new module did not yet exist.
The first substantive tests exposed a test-fixture numeric-SID syntax error
(`icacls` requires `*SID`), then the native OWNER RIGHTS ACL alias created by
Python 3.14. The fixture was corrected and the verified owner alias supported;
other principal/group access still blocks. No OS permission bypass or guard
change was used. An intermediate test launch was interrupted after an asserted
source edit precondition failed; it is not a verification receipt.

After the source/test refinements, the final affected command actually ran:

```text
python -m pytest tests/test_ef02_d5_local_handoff.py tests/test_ef02_d5_run_registration.py tests/test_ef02_d5_local_wake_trigger.py tests/test_ef02_d5_local_wake_entry.py tests/test_ef02_github_read_retry.py tests/test_ef02_wake_d1.py tests/test_ef02_wake_d2.py tests/test_ef02_d3_github_artifact_authority.py tests/test_ef02_d3_signed_origin_consent.py tests/test_ef02_d3_two_grant_delivery.py tests/test_ef02_cdp_doorbell.py tests/test_ef02.py -q --tb=short
520 passed, 11 skipped in 38.36s
exit 0
```

Skips: one POSIX-only permission-bits test on Windows, three D5.4 native symlink
tests (OS permission unavailable), five existing D2 hardlink and two existing
D2 symlink tests. Synthetic reparse/ACL negatives and native Windows owner/DACL
checks ran. No live Chrome/network/ChatGPT or paid provider was used by these
tests. Earlier draft counts, including #129's 106, do not verify these bytes.

This report is frozen BEFORE execution of the exact registered command:
`python -m pytest tests -q -n 12`. Its actual exit, count, stdout/stderr digests,
source stability and full seven-path byte manifest are to be emitted in the
executor tool transcript/final response after this freeze, with all inherited
Git guard settings retained. This paragraph is not a full-suite PASS claim.
The independent EF01 post-provider verification must still run on these final
bytes; executor-local pytest cannot substitute for worker acceptance/publication.

Frozen non-report full-byte SHA256 values (this report's own hash is necessarily
reported externally to avoid self-reference):

| Path | SHA256 |
| --- | --- |
| docs/engineering/EF02_D5_4_LOCAL_HANDOFF.md | `1c0d1b3cc7966c1e92343c6dc67d94e7bb6700c82c5d638049a73deb5e728e33` |
| docs/engineering/EF02_D5_LOCAL_RUN_TRIGGER.md | `ec64f1295a48719f4ec845d439d5f784086fe33bf88c0e2867d7970b58704c99` |
| src/ai_commerce/remote/__main__.py | `67471ce1d67680c456f822a6bc7fdfd57b31cb3bbb7d08ef0f06a9366b922d61` |
| src/ai_commerce/remote/_private_state.py | `3ef5861cc45f08fbe7692c43785c8234cffbc2b75c63efd234586908c2240385` |
| src/ai_commerce/remote/local_completion_handoff.py | `fa758bd17da38596668d316db5b09b47a8337fb184fb9517917565eef6921cc3` |
| tests/test_ef02_d5_local_handoff.py | `93f6b4514d23ea42b130b8e24d72f88c80af6a367f85659ec2277ecf535df541` |

## Unresolved acceptance and publication

AC1–AC4 have source plus affected deterministic evidence. AC5's independent full
registered Windows acceptance is UNKNOWN until EF01 reports it. Native link
permission cases and Ubuntu execution need the appropriate later CI environment.
Real interactive Windows consent, operator service provisioning, actual local
hint delivery, authenticated ChatGPT receipt and Brain continuation remain
UNPROVEN. A hung filesystem is outside the zero-lock-wait guarantee.

Do not reset UNKNOWN or create a replacement registration to force another click.
Preserve private D2 records during rollback. Only EF02 may publish a candidate
after its registered verification, scope and byte-integrity gates pass.
Candidate ref/SHA: UNKNOWN / not published here. Human review/PR and any main
merge are later, separately authorized steps.
