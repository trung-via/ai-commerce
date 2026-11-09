# B3.4 — O07/E06 Operational visibility (offline source-only)

**Source main:** `dd81e895911682a078ed7c2871a85b9742544919`. **Issue:** #752. No new storage, provider poller, quota checker, recording daemon, or private HostOps grants.

## Native sources, epistemic boundaries
- `production_runtime.metrics.read(root, session_sha)` verifies B0.4 operator audit JSONL/checksum chain and anchors, capped records, against a caller-retained **expected head hash**. `metrics.summarize` is the only authoritative summarizer of those narrowly audited operator calls. Its scoped 0 observed coding-agent calls when results exist means **inside B0.4 boundary only**, not the entire AI Commerce system.
- `operations.cost_ledger.CostLedger.get/summarize` reopens only selected A15 entries; selected IDs must be nonempty, bounded, unique. Unverified provider cost declarations, estimates and synthetic charges stay in separate native per-currency buckets, with no FX, allocations, actual invoice or ROI. For multiple sources the bridge rereads native data to catch obvious local races; it is NOT a globally consistent transaction or cryptographically signed identity join.
- `B3OperationalDashboard.v1` is deterministic canonical JSON with payload hash. It shows scoped attempt/outcome/recovery count, completed-only active CPU/wall timings, incomplete outcome warning, and selected A15 cost categories. Actual **Brain chat turns, global EF02 agent calls, model provider quota, Q4 quality, end-to-end journey latency, proven lost work and verified provider billing are always `null`** until real source-owned instrumentations exist. Missing is never interchangeable with zero.
- Hashes are local integrity checks, not private signer/provider attestation. If both audit and anchors are replaced, provenance cannot be proven. A15 and B0.4 have no verified common journey identity. Never infer business performance.

## Security and release
No new service, no writable journals, no native recovery or replay, no direct A15 accounting adjustment, no Chrome 9444, no API/website call, no PULSE, no provider entitlement. B3.4 is a review artifact, not self-improvement or LangGraph activation. Human and Brain must independently decide any future use.

Positive tests: valid B0.4 native replay and selected A15 immutable cost with no source writes; null for all uninstrumented globals; canonical stable digest. Negative tests: stale/tampered head/anchors, cost duplicates and unknowns, fabricated authority/quota, missing native selection. Exact-head hosted Windows MT01 owner, B0.4/A15 regression closure, Node test and Brain PASS before trusted auto-merge; no self-host live evidence claim.
