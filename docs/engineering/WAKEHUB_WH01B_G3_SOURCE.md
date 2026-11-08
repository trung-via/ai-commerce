# WakeHub WH01B G3 R2 source

Attempt: `wakehub_wh01b_g3_owner_scope_r2_4a9b07b_20261107_codex`.
Source parent: `4a9b07bd41df8cfe4b011e575c4d0ea8a729357f`.

This is a new source action for #558. Failed #555 R1, its artifact, its local
test claim, and its immutable failure record are not inputs to these sources.
The EF02 worker owns publication; this executor must not modify Git refs or
create a candidate, PR, installation, or service identity receipt.

## Source boundary

`g3_enrollment.py` produces a deterministic, data-only attended plan for a
fixed demand-start, own-process, noninteractive virtual-account service. All
resources have explicit desired readback, including protected administrative
ownership, four separate signer roles, separate service/observer custody,
anchors, fixed pipe policy, source/release pins and journal identities.
There is no installer, registry writer, ACL writer, service creation, signer
enrollment, task creation, or generic command dispatcher. Plan inputs and
matching offline readback never constitute native installation evidence.

Preflight requires a complete typed readback for every resource. Only complete
known absence produces review steps. An exact complete match produces an
idempotent no-op. Missing/duplicate resources, unknown state, partial install,
configuration drift or a changed prior plan digest produce
`HOLD_RECONCILE_ONLY`. Postflight requires complete matching readback; even
complete absence after an attempted install is reconciliation-only. No API
resets, migrates or replaces an existing replay ledger. Readback supplied to
this pure planner is an offline claim, not a protected Windows observation.

`g3_service_host.py` contains a fixed staged SCM entrypoint and a connected
server-HANDLE host. SCM staging registers the fixed ServiceMain/control handler
and reports `SERVICE_STOPPED` with a service-specific custody hold. It cannot
report a working service: transport packaging and enrollment are deliberately
absent. There is no listener or transport loop in this source stage. A future
separately reviewed packaged transport must supply the live connected server
HANDLE to `ConnectedStatusHost`, carry the exact challenge signing transcript
to the separately enrolled broker signer, and return the independently signed
observer evidence. Calling the Python host is not proof of SCM installation.

The host's `begin` authenticates that HANDLE using G2's kernel pipe name, server
end, endpoint PIDs/sessions, primary-token/image/start identity, SCM account,
service SID, service configuration and protected pipe/SCM ACL checks. It reads
the fixed protected G3 registry anchor and independent public signer pins,
checks the desktop logon LUID, and validates all private paths. Desktop and
runner may share a user SID; neither may share the private service identity or
read its journals. The broker signing key must differ from the issuer, Human
and observer keys. The service must not hold the observer's signing key.

Each challenge has a random canonical UUID nonce, full source/release binding,
generation, journal identity, service/desktop process identities, exact target,
logon LUID, and a lifetime of at most five seconds. `begin` durably claims it
as UNKNOWN in the separate preprovisioned challenge journal before returning
any signing transcript. Only one challenge is pending per host. Completion
consumes the pending state before revalidating native custody. A crash, lost
ACK, stale response, invalid signature or changed HANDLE leaves the durable
nonce consumed. Reopening never recovers unsigned in-memory authority.

`g3_desktop_observer.py` checks its own pinned interactive primary-token
process before any input-desktop read, verifies the broker challenge signature
in the dedicated G3 domain, checks the source/generation and live logon, and
uses G2's connected/unlocked owner-desktop read. It rechecks process and logon
after the read, then produces exact bytes for the independent observer signer.
It neither owns a signing key nor invokes a signer. Unsigned output is never
evidence. The service consumes only the distinct observer signature domain and
its own exact pending challenge. An old signature, wrong role, wrong logon,
SID, session, process start, source, generation, nonce or time fails closed.
Session 0 only verifies signatures/tokens; it never calls desktop APIs or
impersonates the desktop user.

The G3 authority subclasses the reviewed G2 service authority. It replaces the
desktop assertion with the fresh challenge proof at every inherited security
boundary. G2 issuer-signed grants, independent Human consent, revocations,
release-byte verification, protected ACL checks, monotonic epoch checkpoints,
complete signed inventory, and original-outcome reconciliation still apply.
The existing G2 request and effect journals remain distinct from the challenge
journal. Their first-wins claims, durable UNKNOWN state and no-retry behavior
are unchanged. New challenge/request IDs cannot replay an old effect.

Native START and REUSE/attach stay unconditionally denied by the reused G2
runtime and executor. STATUS preserves Human pause. RECONCILE_ONLY can resolve
only independently signed evidence of the original operation; it cannot
dispatch the original native effect. No code here starts/reuses CDP 9444,
touches 9333, starts a logon task, performs browser/GUI actions, sends a Wake,
changes PULSE, or sends user chat.

## Ownership and verification

The sole new data-only owner manifest owns exactly these three G3 Python
sources and `tests/test_wakehub_g3_native_enrollment.py`. Its existing consumers
are proven by the pinned main manifests:

| Consumer | Main owner | Registered tests |
| --- | --- | --- |
| `native_broker.py` | `wakehub-g2-private-broker` | `test_wakehub_g2_broker.py` |
| `native_runtime.py` | `wakehub-g2-native` | `test_wakehub_g2_native.py` |
| `native_inventory.py` | `wakehub-native-inventory` | `test_wakehub_native_inventory.py` |

Before substantial generation, the unchanged `impact_v21.py` admission and
registered planner were invoked with the exact #558 allowed paths and the six
actual new paths. Admission selected those three existing owners plus
`wakehub-g3-native-enrollment`, with zero missing mappings, no unsupported
paths, no unknown consumers/tests, and no full-suite fallback. Existing merged
manifests, the oracle, protected scripts and planner were not edited.

An additional positive G3 reconciliation check exposed G2's exact verifier
type allowlist in `native_journal.py`. The final source adds only the concrete
`G3ServiceAuthority` to that allowlist with a lazy import. Arbitrary subclasses
still deny before their verifier method is invoked; the real signature is
rechecked and the existing atomic journal transition is unchanged. This
existing G2-owned source is the seventh changed path, already authorized by
#558. Final admission and owner/consumer verification include that actual
path; the new owner does not re-own it. The registered final closure also
selects `wakehub-private-operator` and `test_wakehub_private_operator.py`, since
that existing owner declares the affected journal consumer. Final verification
therefore requires five groups, with no full-suite fallback.

The G3 tests use synthetic Windows SCM, HANDLE, token, desktop and ACL seams,
real scratch-only OpenSSH signatures, and disk SQLite. They cover the complete
positive signed STATUS exchange with Human pause, exact interactive signing
transcript, role separation, hostile readback, forged/stale/mismatched observer
claims, source/config/token/logon changes after challenge, crash/lost ACK,
reopen/replay, concurrent first-wins completion, denied native effects, and the
SCM stage's stopped custody hold. No fixture installs an OS service or enrolls
a real signer. The existing G2 negative suites run via their registered owner
closure. Tests and JUnit/scratch artifacts stay outside the checkout.

Local focused checks and registered MT01 diagnostics do not constitute an
independent EF01 receipt. The independent worker must verify unchanged exact
source bytes, registered selected/collected/executed/passed coverage, worker
observation and admission before publishing anything. Hosted exact-tree
Windows CI and independent Brain review remain subsequent gates; CI alone
does not authorize merge.

## Native state

`SOURCE_ONLY`; `NATIVE_SERVICE_NOT_INSTALLED`; `SIGNER_NOT_ENROLLED`;
`DESKTOP_OBSERVER_NOT_ENROLLED`; `CDP_9444_NOT_STARTED`;
`LOGON_TASK_NOT_INSTALLED`; `WAKE_NOT_SENT`.

These labels describe what this action did and did not provision; they are
not a live inventory claim about any independently managed machine state.
The Human's paused 9444 lane remains paused. There is no native authority
receipt and no claim that offline PASS proves deployment or custody.

SCM API shape follows Microsoft's [service entrypoint documentation](https://learn.microsoft.com/en-us/windows/win32/services/service-entry-point).
The inherited HANDLE authenticity checks use the kernel pipe endpoint API
described in [GetNamedPipeServerProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getnamedpipeserverprocessid).
