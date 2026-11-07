# WakeHub WH01B G4A native transport source

Parent: `2d783977ade56c7b3af9626f7f414ebf568ca751` (G3 #561).
Roadmaps: #492, #499. Source action: #564.
Batch: `wakehub-wh01b-g4a-native-transport-20261107-r1`.
Action: `WAKEHUB-WH01B-G4A-NATIVE-TRANSPORT-SOURCE-001`.
Attempt: `wakehub_wh01b_g4a_native_transport_2d78397_20261107_r1_codex`.

**HOLD: PACKAGING_OWNER_ESCALATION + HOLD_VERIFICATION_LIMIT.** This is source implementation, not an
installed Windows authority or a runtime-ready G4A release. The EF02 worker
alone owns candidate publication after independent EF01/MT01 verification.
There is no executor-created candidate, branch, PR, merge or Git ref write.

## Implementation and trust boundary

`g4_service_transport.py` implements the fixed SCM dispatcher, ServiceMain,
control handler and one-instance, overlapped local named-pipe adapter. It uses
the existing G2 name and G3 connected server-HANDLE protocol. It has no install,
enroll, service-name/path/port/command options, console fallback, shell or browser
dispatcher. Imports have no native effects. The packaged entrypoint calls
`scm_entrypoint` on its main thread with no arguments. ServiceMain reports
START_PENDING, initializes privately, and can report RUNNING only after the
internal production gate, signer, protected startup readback and listener have
succeeded. With the present hard gate it reports STOPPED/1066 without a pipe.

CreateNamedPipeW uses FILE_FLAG_FIRST_PIPE_INSTANCE, FILE_FLAG_OVERLAPPED,
PIPE_REJECT_REMOTE_CLIENTS, byte mode and exactly one instance. The server
retains that first HANDLE across sequential connections. Its explicit protected
DACL admits only SYSTEM/Administrators/service full control and the pinned
operator's GENERIC_READ plus FILE_WRITE_DATA, READ_CONTROL and SYNCHRONIZE.
FILE_GENERIC_WRITE is deliberately excluded because it includes the pipe
instance-creation right. Readback rejects extra/world/inherited ACEs, unknown
owners and an unprotected DACL. The descriptor and handles are not inheritable.
Client CreateFile uses the fixed local name, OPEN_EXISTING, overlapped mode and
anonymous SQOS, so a hostile server cannot impersonate the desktop client.
Missing/busy pipe errors are final for that one-shot call; no reconnect loop.

G3/G2 recheck kernel object name, pipe end, server/client PIDs and sessions,
live primary token SID, image and creation time, SCM account/configuration/SID,
protected release bytes and protected origin/source/generation. The new client
end has equivalent native endpoint reads. The service never impersonates or
opens a Session 0 input desktop. Startup alone may read START_PENDING; connected
authentication still requires RUNNING. SCM reads request QUERY_CONFIG and
QUERY_STATUS only, never service mutation rights. Demand-start and absence of
dependency/group loaders are checked. G4 further requires the fixed quoted
service image, own-process service, virtual account and service SID type 1.

Every frame has a four-byte big-endian length, a 262144-byte maximum, strict JSON
decoding and a single four-second monotonic I/O deadline for the whole exchange.
Hello admits exactly STATUS. G3 commits challenge UNKNOWN before the signed
challenge is sent. The broker signer output is cryptographically verified
against the independent enrolled public pin before transmission. Completion
contains a signed desktop status and the unchanged issuer-signed G2 request /
independent Human consent. Only inner STATUS is admitted. Signed or unsigned
START, REUSE and RECONCILE_ONLY cannot pass this G4 transport. G2's separate
signed original-effect reconciliation protocol remains unchanged and is tested
through its registered owner suites.

G3 reopens native custody, source and journals at completion. A lock covers the
entire exchange; a racing caller cannot steal/abandon another challenge. Once a
challenge is exposed, failed framing, exception, stale proof, crash or lost ACK
never releases its durable nonce. G2 request UNKNOWN precedes policy dispatch;
DONE precedes the final ACK. `BrokerRequests.readback` reads the exact original
private outcome by nonce AND digest, without claim/dispatch/retry. It returns
no outcome for UNKNOWN and rejects mismatch, absence and corrupt journals.
No wire readback command or new effect is introduced.

STOP/SHUTDOWN only sets the stop event and reports STOP_PENDING. Overlapped
waits poll at 25 ms and cancellation waits at most 250 ms. CancelIoEx is not
treated as completion: an uncompleted OVERLAPPED/buffer is quarantined until
own-process exit, the loop stops and cannot reuse it. There is no blocking
FlushFileBuffers. This establishes bounded transport I/O, not a measured native
shutdown guarantee: the inherited G3 verifier has bounded subprocess calls and
the not-yet-enrolled signer must honor the deadline. Independent packaging and
native shutdown observation remain owner gates.

`g4_desktop_transport.py` is an interactive STATUS-only one-shot client. It
verifies the server challenge signature and binds its origin to independently
protected source/generation/journal/process/target/logon readback. It uses the
existing G3 collector and rechecks origin after collection and before accepting
the receipt. Its packaged entrypoint reads only the fixed admin-protected
`StatusRequest.json`; it accepts no CLI path or public Issue payload. The
separately enrolled signer ABI requires its own durable first-wins nonce and
exact transcript digest before signing, fixed role/domain, protected origin,
live caller/token authentication and independent custody. There is no private
key reader, shared SSH agent or runnable signer implementation here. Production
signer loaders fail with PACKAGING_OWNER_ESCALATION. Private offline adapters
cannot change production readiness or substitute a trusted installation.

G3's standalone `read_logon_id` is now a read-only Windows primary-token
diagnostic like G2's `read_process_identity`; it proves only a logon LUID. G3's
collector and host retain their original CI/custody gates before using it for
authority or desktop access. All previous G2/G3 tests are preserved.

## Packaging feasibility and exact owner escalation

The current `pyproject.toml` builds a Python distribution with the generic
`ai-commerce` script. It does not build WakeHubBroker.exe, an SCM bootloader, an
isolated interpreter/dependency lock, Authenticode publisher verification, an
installer or independent signer custody. A Python module, wheel, PE-shaped
fixture, SHA string, service SID string or successful CI run cannot fill this
gap. No compiled executable was produced or claimed in this action.

`g4_release_provenance.py` supplies a fixed Windows AMD64/console/own-process
entrypoint and onedir/no-extraction build specification. It hashes the actual
G4/G3/G2 Python sources and conservative transitive repository import closure,
including package initializers and inherited HostOps/remote helpers. Toolchain
inputs require digest pins for Python executable/DLL, packager wheel,
bootloader, Windows SDK and dependency lock. Reproducibility specifies fixed
PYTHONHASHSEED and SOURCE_DATE_EPOCH; no packager is installed or invoked.

Its readback validates the exact spec/source digests, bounded generation,
complete declared bundle membership, no path traversal/case aliases/links,
both actual unsigned build trees byte-for-byte and the separately pinned signed
tree. Both fixed executables must pass AMD64 PE console structural rejection
checks. This is **ARTIFACT_BYTES_MATCH_SOURCE_ONLY**, not Authenticode,
reproducible-build independence, SCM installation or custody proof. The signed
tree still requires independent signature/publisher verification and protected
manifest/source-epoch observation. Every readiness field except SOURCE_CANDIDATE
remains false, including after synthetic artifact readback.

Required owner: **Windows release packaging owner + independent private HostOps
custody owner**. Required integration source paths are the three G4 modules,
particularly `g4_release_provenance.require_installed_release`,
`g4_service_transport.scm_entrypoint` and
`g4_desktop_transport._load_enrolled_signer`. A reviewed packaging/bootstrap and
dependency-lock owner scope must be authorized separately; `pyproject.toml`,
any `.spec`, installer, `.github` or CI additions are outside this action's
allowlist and were not changed. This is OWNER_ESCALATION for that scope, not
permission to add a packaging file or let a public Issue/CI install itself.

The production release gate intentionally always raises
**PACKAGING_OWNER_ESCALATION**. It must not be relaxed until a separate approved
implementation checks an admin-owned signed/pinned installed bundle, protected
monotonic source epoch, SCM readback and independent broker/observer/issuer/Human
custodies. A test boolean or injected attest function is not such a check.

Microsoft requires an own-process service to call its dispatcher promptly from
the main thread and initialize in ServiceMain:
[Service entry point](https://learn.microsoft.com/en-us/windows/win32/services/service-entry-point).
The pipe design follows
[CreateNamedPipeW](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createnamedpipew),
[ConnectNamedPipe](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-connectnamedpipe),
[GetNamedPipeInfo](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-getnamedpipeinfo)
and [CancelIoEx](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex).
Windows packaging is feasible in principle, but is not demonstrated here;
PyInstaller documents both Windows-host builds and the reproducibility settings:
[manual](https://pyinstaller.org/en/stable/),
[reproducible builds](https://pyinstaller.org/en/stable/advanced-topics.html#creating-a-reproducible-build).

## MT01 admission and verification boundary

Before implementation, the exact 16-path allowlist from #564 was reconciled with
the immutable parent. The new data-only manifest owns only three G4 modules and
`tests/test_wakehub_g4_native_transport.py`. All five fresh paths (including the
manifest) were absent in pinned main. Existing consumer ownership was verified:

| Consumer | Existing source owner |
| --- | --- |
| g3_service_host.py, g3_desktop_observer.py | wakehub-g3-native-enrollment |
| native_broker.py | wakehub-g2-private-broker |
| native_journal.py | wakehub-g2-native |

The initial admission selected four groups. With the final G3 logon diagnostic
change, the unchanged source-owned planner selects five owner groups under
`mt01-component-onboarding`: wakehub-g2-native, wakehub-g2-private-broker,
wakehub-g3-native-enrollment, wakehub-g4-native-transport and
wakehub-native-inventory. They select exactly
`test_wakehub_g2_native.py`, `test_wakehub_g2_broker.py`,
`test_wakehub_g3_native_enrollment.py`, `test_wakehub_g4_native_transport.py`
and `test_wakehub_native_inventory.py`. The fifth group is the registered
downstream G3 consumer, not a broad verification fallback.
There was no missing mapping, unsupported path, unknown consumer or full-suite
fallback. The G2 native_journal source is unchanged; its owner suite is selected
as the declared consumer closure. The private-operator source/tests are also
unchanged; no journal authority allowlist change or re-ownership is required.

Offline tests use the real framing methods on fragmented in-memory byte streams,
real scratch OpenSSH signatures, independently poisoned scratch signer ledgers,
G2 request/effect and G3 challenge SQLite journals, and synthetic SCM/token/ACL
readbacks. They cover the signed positive STATUS exchange with Human pause,
durable original-outcome readback, hostile operations/targets, replay/race,
mid-frame failure/lost ACK, source/generation/logon drift, revocation and journal
corruption, pipe security/anonymous SQOS, cancellation memory lifetime, SCM state
contracts and incompatible/unpinned artifact bytes. The actual Windows primary
token/image/creation/logon read is read-only and proves no installed service.

Local checks are executor diagnostics only. Independent EF01 selected/collected/
executed evidence, exact-tree hosted Windows CI and independent Brain PASS are
separate gates. None may be inferred from this document. Test scratch/JUnit/logs
belong outside the checkout, and no protected paths or registries were edited.

### Actual local verification result: HOLD

Two local Verify–Fix rounds were used; no third round or automatic next action.
The initial focused run collected/executed 45 G4 tests: 40 passed, 5 failed.
Three failures were assertions expecting a receipt when the client correctly
rejected source/generation/logon drift, one was a deadline refusal after journal
corruption, and one exposed the CI gate on the read-only logon diagnostic.
The source/test corrections are included in this attempt, not a new attempt.

The final source-owned plan collected and executed 479 tests from the five
selected files, with actual executing worker IDs gw0 through gw11 observed in
the xdist log. No tests were skipped or deselected; 478 passed and 1 failed.

| Selected owner/test file | Collected/executed | Passed | Failed |
| --- | ---: | ---: | ---: |
| wakehub-g2-private-broker / test_wakehub_g2_broker.py | 81 | 81 | 0 |
| wakehub-g2-native / test_wakehub_g2_native.py | 107 | 107 | 0 |
| wakehub-g3-native-enrollment / test_wakehub_g3_native_enrollment.py | 114 | 114 | 0 |
| wakehub-g4-native-transport / test_wakehub_g4_native_transport.py | 53 | 52 | 1 |
| wakehub-native-inventory / test_wakehub_native_inventory.py | 124 | 124 | 0 |

The failing test is
`test_real_signed_status_over_fragmented_frames_preserves_pause_and_privacy`:
the service reached the final receipt frame, whose WriteFile adapter refused
because the exchange's four-second monotonic deadline had elapsed. The positive
case passed in the initial serial diagnostic, but the final parallel positive
AC is **not proven**. This is a discriminating timing/completion failure under
12-worker cryptographic load, not evidence of an installed-service failure.
Do not turn it into PASS, silently extend a fresh challenge, skip the test or
reuse the earlier serial result for these final bytes. A future authorized
repair must reconcile bounded I/O/completion timing with the G3 five-second
freshness contract and measure the exact final tree.

All changed source/test bytes were unchanged during the final run. Its exit
code was 1, elapsed pytest time 68.84 seconds. Final log SHA256:
`5804d40c4a39dcccd16fd811a828e500c29d1c56102b90e9a24c689ace1c8951`.
Final JUnit SHA256:
`509a272512d3e99de2389481fd4e336e9656eec0a9bcc9c6e30fc79a85ca04b9`.
Scratch/logs used the executor's external sandbox TEMP, not protected worktree
scratch or a published artifact. These digests identify local diagnostic bytes;
they are not independent EF01 receipts, durable remote artifact claims or a
candidate acceptance. Candidate SHA/branch/digest: **NONE PUBLISHED BY EXECUTOR**.
Official EF01/MT01 evidence, hosted CI and Brain PASS: **NOT OBSERVED**.

### Source byte pins

The conservative repository source closure contains 48 files. SHA256 of its
canonical path-to-SHA256 map:
`de93163fb2641a0dd37a98addcc808241f9b960182495f9b5e0d8f453a3cedec`.
This pins source bytes only; it is not a binary release pin or protected epoch.

| Changed path | SHA256 |
| --- | --- |
| src/ai_commerce/wakehub/g4_service_transport.py | 48a802c5866a85e5ac46d437188c22df09630f455434602cacf1b11da05744ee |
| src/ai_commerce/wakehub/g4_desktop_transport.py | c8540b116d3f861162d3f4a72a6c0dec7438e56ba137b75b9aab209e75cc2b6e |
| src/ai_commerce/wakehub/g4_release_provenance.py | c2787f634cd66d13f4f09cf799643ce44d8b48f1e498f15e8ac115b57f64983e |
| src/ai_commerce/wakehub/g3_desktop_observer.py | 514037da9ff4c1efe90133d59c4c3aaf0aa6e47b545d9223ea882b50f9118831 |
| src/ai_commerce/wakehub/native_broker.py | 4096ce8cd3fd77de4b7b91e05d680a63c41f2aca07b82e8aab88df82585880e2 |
| tests/test_wakehub_g4_native_transport.py | 8dea3401748ea5d85d371a6075e7b4060f015e0734c853c96b37483ee11e21ae |
| work_items/mt01-components/wakehub-g4-native-transport.json | 2654dbacedca4e507d1dc738a567b3e8b6d19f0f46cdcd71242b4c4eb028cf24 |

The eighth changed path is this report; its final SHA256 is returned separately
to avoid a self-referential digest. No existing MT01 manifest, planner/oracle,
CI, packaging/installer file, G2 native_journal, private-operator test or Git
metadata was modified.

## Proposed G4B preflight, not authorized execution

The next native owner action must first authorize and implement a fixed
privileged HostOps operation named `wakehub_g4_enrollment_preflight_readonly`.
It is a proposed operation, not a claim that the current HostOps router supports
it. Its schema must accept only the approved release-manifest digest and exact
action/attempt/source generation; no executable path, shell, service name, PID,
URL, port or generic PowerShell argument. It should return redacted signed
readbacks of fixed bundle/publisher, SCM object/SID/config and process startup,
protected epoch/anchors, pipe policy, independent custodies, preprovisioned
journals and interactive desktop/session/logon state. Unknown/partial/drifted
state is reconciliation-only; CI and legacy standing_read_diagnostics are not
native witnesses.

Only after that private preflight is reviewed may separate exact per-effect
authorizations cover: fixed signed release placement and ACLs; fixed service
creation/configuration and readback; source-epoch/anchor and create-once journal
provisioning; independently separated signer enrollment; bounded service start;
and one attended interactive STATUS observation. Each needs an immutable intent,
UNKNOWN before native effect, independent original-outcome readback and no
blind retry. Chrome/9444 start/reuse, Send, logon tasks, browser residence and
PULSE remain outside those effects. No generic Human PowerShell request or
automatic next action is proposed as an authorization shortcut.

Public states for this action: `SOURCE_ONLY`, `NATIVE_SERVICE_NOT_INSTALLED`,
`SIGNER_NOT_ENROLLED`, `DESKTOP_OBSERVER_NOT_ENROLLED`, `9444_HUMAN_PAUSED`,
`LOGON_TASK_NOT_INSTALLED`, `WAKE_NOT_SENT`. These describe this action's effects
and evidence boundary, not a live inventory claim about another machine.
