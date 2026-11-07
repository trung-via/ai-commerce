# Executor health: local read-only foundation v1

Capability: **SOURCE_ONLY**. This source foundation is not an installed or
deployed HostOps operation. Passing Windows source tests with stubbed metadata
does not establish native CLI, authentication, account, entitlement or quota.

## Scope and immutable evidence

R3 starts at main `0df102a61a9e1fdd2c05954d0832790656d3d81b`.
Before editing, the existing committed owner
`work_items/mt01-components/hostops-personal.json` matched the worktree bytes;
Git-byte SHA-256:
`b23d2b0e1db3cef44562ad61a04758b5f324c3eeaf6b43256b31c3d046d0577f`.
It owns `personal.py` and `test_hostops_personal.py`, with consumer
`hostops_v3/contract.py`. The registered `mt01-component-onboarding` profile
loads this persisted owner. Preflight selected `hostops-personal` and its
`hostops-v3` consumer group, with no missing mappings and a verified owner
binding. No new manifest or registry entry is needed.

The preceding R2/#570 attempt remains an immutable selection failure:
run `42335423354`, artifact `13559915901`, reported ZIP SHA-256
`672c96537ad4598059f2e94772126064c3ee84ee357ce468bc68f4cc93fbca42`.
It selected zero groups and executed no tests; executor exit zero was neither
quota evidence nor a candidate. #568's schema rejection also remains unchanged.
This implementation was rebuilt from the current source, not a transplanted
forensic patch. Any fresh verified candidate is distinct from these attempts.

## Local contract and limits

`collect_executor_health_local` returns an in-memory
`ExecutorHealthLocalReceipt.v1`, validated by
`validate_executor_health_local_receipt`. Exact keys, ordered provider rows,
closed states, UTC timestamps, strict types, freshness and cross-field
invariants are checked. Visibility is `LOCAL_PRIVATE`; context identity and
provider identities and accounts are always `UNKNOWN`.

On Windows only, fixed PATH discovery considers `codex.exe`, `codex.cmd`,
`agy.exe`, `agy.cmd`. Only regular native executables with safe ancestors may
run; a CMD launcher is reported unsupported and never launched through a shell.
No install-directory, profile or home search is performed. PATH discovery does
not authenticate the publisher of a binary. Exact permitted argv tails are:

- Codex: `--version` and, after a recognized version, `login status`.
- Antigravity: `--version` only; authentication remains unknown.

Each subprocess has a fixed five-second timeout, closed stdin, no shell, no
console window, and no model prompt. Only exact recognized version/login text
is projected. Malformed, mixed-stream, oversized, non-UTF-8 or failed responses
produce closed states. Raw stdout, stderr, paths and exception text are never
returned or persisted. Accepted output is limited to 8 KiB; memory capture is
time-bounded, not a sandbox against a hostile executable flooding output.

The collector validates the single public `model-defaults.v1.json` using the
existing `ExecutorSelection` rules and strict policy shape. It reports preferred
and fallback configuration without selecting, changing or saving either. It
does not read mutable `.local` preference state or use a cooldown as a reset.

An optional bounded EF01 report can be supplied as bytes with an independent
verifier callback. The caller must establish native report integrity, lineage,
selection, and provenance in its own trust boundary. No production verifier or
report-discovery route is installed here. A digest, JSON trust flag, agent
assertion or model-defaults state alone cannot authenticate a report. Missing
verification yields `UNTRUSTED`; malformed evidence or verifier failure cannot
trigger another provider call. Only the last invocation is considered.
Recognized native EF01 quota failures and determinate returns are historical
observations, bound to the report's provider, never to a claimed account. Age
uses the executor-finished timestamp; over 3,600 seconds is `STALE`, future or
naive timestamps are invalid. A successful return today proves no current quota.

For both providers, current `quota` remains `UNKNOWN`, and `remaining` and
`reset_at` are always `null`. Codex login status reports authentication mode,
not measured quota ([official OpenAI command reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli)).
Antigravity `/usage` opens an interactive TUI and refreshes backend quota
configuration ([official quota command](https://antigravity.google/docs/cli/commands/usage)).
Neither `/usage` nor Codex interactive `/status` is run in a headless collector.
No documented first-party machine-readable remaining/reset surface has been
proven for this capability. API billing and model inference are not substitutes.

## Public boundary and Phase 2

The original sixteen standing READ categories, Python public fields and
protected Issue sanitizer remain unchanged. No new operation is accepted by
`parse_event`, `perform` or the module CLI. The private receipt must never be
copied into a public Issue, log, artifact, `content_utf8` or standing diagnostic.
The repository is public: account information and real quota percentages must
not enter GitHub. No credentials, cookies, tokens, authentication mutation,
provider login, services, Chrome or PULSE operations are part of this work.

Phase 2 requires an independent protected-source-owner review of the sanitizer
and transport, with adversarial runtime parity tests; it also requires an
authenticated private account telemetry route and independently trusted EF01
receipt integration. Public disclosure must be separately minimized and
approved. Authentic Windows HostOps checks must establish the exact account,
session, PATH, binary provenance, supported output and read-only effects before
claiming native capability. Installation and workflow changes are not authorized.

Verification first runs the cheap targeted health tests, then the planner's two
registered groups with twelve actual Windows xdist workers per group and real
collection/execution evidence. Any owner or profile-binding conflict is a typed
HOLD, with no broader fallback or second workaround. After source/scope checks,
only the EF02 worker may publish a candidate. Brain review and hosted Windows
exact-head CI remain separate gates; a source candidate is not deployment.
