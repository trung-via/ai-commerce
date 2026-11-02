# HostOps v3 repair design

Decision/spec: [Issue #225](https://github.com/trung-via/ai-commerce/issues/225); repair [#231](https://github.com/trung-via/ai-commerce/issues/231). Base `e267e0f2438fdb2ed8d0ec13b9357d547c2100e6`. No live activation; v2 unchanged.

## Trust and data flow

ChatGPT prepares a typed request. An independently enrolled issuer signs it. A reviewed hosted workflow acknowledges the private owner Issue without executing host code. An outbound transport process verifies the run and workflow digest, then sends the bounded envelope over authenticated local IPC to a different non-admin broker identity. The broker verifies immutable installed source, both signature roles, the scoped Human lease, revocation and local OS admission. EF02/CI identities have neither broker IPC authority nor installed-code/state write access. Runner labels provide no identity isolation.

`contract.py`: closed schemas, strict JSON without duplicate keys/floats/unknown fields, semantic fingerprint and sanitized receipts. Canonical bytes are sorted compact ensure-ascii JSON plus final LF; this is a restricted versioned algorithm, not a claim of RFC 8785 compatibility.

`auth.py`: pinned OpenSSH executable and disjoint Ed25519 Human/issuer public keys. Namespaces are `hostops-v3-request`, `hostops-v3-lease`, `hostops-v3-exact-action`, `hostops-v3-release`. Each request binds host/release/source/parameters/ID/time; main branch drift does not invalidate identical approved installed bytes. Lease roots may cover every accessible mounted volume or explicit UNC scope under the actual token ACL.

`security.py`/`broker.py`: token/SID/elevation and ACL admission, separate broker/transport identities, explicit pipe DACL and peer token checks on Windows; Unix local peer credentials adapter. Frames are bounded, mutually authenticated with fresh challenges and directional HMAC, never unpickled. No arbitrary shell, public inbound service, elevation, Chrome/Wake route or EF02 dispatch.

`worker.py`/`journal.py`: SQLite WAL synchronous FULL commits request ID + semantic fingerprint + INTENT before effects. Broker execution serializes; duplicates return the durable receipt or UNKNOWN; changed semantics cannot reuse an ID. Crashes/lost reporting do not authorize replay. Reconciliation only observes and remains UNKNOWN because a matching postcondition does not identify its author. Watchdog termination also requires reconciliation when effects are uncertain.

`filesystem.py`: path/lease/control-plane exclusions, Unicode/traversal/ADS/device defenses, pinned ancestors, reparse/hardlink/special-file denials, bounds and read-back digests. Exclusive create and snapshot copy never overwrite a destination. Replace/patch/append/move/rename/quarantine code is prototype-only: final hash checks plus atomic replacement do not prove atomic compare-and-swap with external writers. Worker returns BLOCKED / CONDITIONAL_WRITE_ATOMICITY_UNPROVEN with no effect. Preview does not unlock the block. A native backend and concurrency evidence require a reviewed engineering change.

Reads and names remain private local selections. Normal receipts/journal/outbox expose only fixed codes, opaque refs, counts and hashes. Disclosure needs a separate exact Human signature binding selection hash, classification, byte range and recipient; GitHub disclosure additionally limits chunks to 32768 bytes and the exact Issue. No automatic secret upload follows a broad read lease. Lost disclosed bytes are not cached for re-export.

Limits: envelope 128 KiB, chunk/write payload 64 KiB, full-file ceiling 64 MiB narrowed by lease, lease at most 24 h, request at most 300 s, operation at most 30 s, 128 local selections, 100000 durable IDs, 262144-byte IPC frames. Fixed PowerShell is only the digest-pinned token inventory script, fixed argv and bounded sanitized output, with no request-supplied expressions.

## Evidence and blockers

Only ordinary source/contracts/config/tests/design/report and the stable 48-ID scenario manifest belong in publication. All per-run logs/XML/fixtures/fingerprints stay in `.local/hostops-v3-repair`. Use existing proven CRLF normalization before tests and preserve independent raw/Git-clean checks. The executor makes no Git metadata changes.

The source files were recovered against the exact old local manifest; independent artifact-byte cross-check is RECONCILE/RECOVERY_UNAVAILABLE in this environment. The 48 original descriptions are unavailable: category-derived tests cannot establish exact parity. Conditional atomicity, protected workflow/shared-map integration, native Windows dedicated-account acceptance, Linux verification, all live AC and registered EF01 verification remain separate requirements. See HOSTOPS_V3_ENGINEERING_REPORT.md for exact YAML, current receipts and install/recovery procedure. LIVE_READY=false; E2E_READY=false; MERGE_HOLD.
