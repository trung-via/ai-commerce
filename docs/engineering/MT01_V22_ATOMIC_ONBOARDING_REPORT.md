# MT-01 V2.2 — Atomic Component Onboarding

## Human decision and scope
Human adopted Option C on 2026-11-03: eliminate the preregistration PR for each new Roadmap A component while preserving EF02 scope and immutable evidence. Engineering Issue [#260](https://github.com/trung-via/ai-commerce/issues/260) is the accepted implementation handoff.

- Replacement single review PR: [#265](https://github.com/trung-via/ai-commerce/pull/265), based on `main@b46e4a14889bc127085abd7b9769b0219b430c2a` with HostOps #262/#263 preserved.
- Obsolete conflicted Draft PR [#261](https://github.com/trung-via/ai-commerce/pull/261) was closed **without merge**.
- A17.1 EF02 Issue #258 was NOT retried, modified or re-dispatched.

## Implemented on review branch
1. One registered *ingress-only* `mt01-component-onboarding` profile in the protected registry. EF02 v1 Issue schema, owner/issuer authentication, claim and source integrity are unchanged.
2. Static, versioned `MT01ComponentOwner.v1` manifest under `work_items/mt01-components/<id>.json`; the manifest is DATA, never executable code or pytest argv. Constrained fields are sources, tests, AC, consumers, contracts. Validation enforces exact on-disk bytes, new owner uniqueness, actual changed paths, signed scope (finite directory prefixes permitted), contract/test existence and path/symlink safety.
3. Post-executor MT01 V2.1/V2.2 dynamically derives finite `python -m pytest <reviewed tests> -q -n 12` for new groups; legacy consumer owners are also selected even when untouched. Unmapped code, missing manifest/profile, unproven AC and aggregate fan-in HOLD, never implicitly expand to full.
4. EF02 independently replays new-owner mapping against immutable Git base, source diff, signed allowed paths, manifest hash, expected test argv, exact compiled evidence and actual candidate bytes. Fake ingress argv is not accepted as dynamic verification.
5. PR CI discovers only new declarative owner manifests, then computes ordinary impact tests. Independent stdlib baseline oracle enforces old + new owner test closure; protected merge gate re-fetches exact GitHub manifest blobs by PR HEAD and independently rechecks owners, legacy consumers and both-OS artifacts/JUnit. No candidate manifest is executed as Python.
6. PR-only workflow concurrency cancels superseded PR runs. Main push CI is not canceled. Hosted pytest remains 4 workers; self-hosted EF02 requests 12.

## Tests authored
- Good manifest admission, exact bytes, file and git-tree source, path-prefix authorization and scoped argv from a new owner;
- Malicious extras, shell argv, re-own baseline, duplicate IDs, symlink/path traversal, unknown consumers, missing manifest/source/tests, incorrect group/argv and allowed scope;
- Real temporary Git baseline replay of EF02 binding;
- Independent merge-gate GitHub blob SHA & changed-file source proofs;
- Baseline oracle omission of old consumer tests, manifest tampering, invalid test path;
- CI concurrency source invariant.

## Status, known limitations, no false claims
**ENGINEERING_IMPLEMENTED_ON_DRAFT / BRAIN_ACCEPTANCE_PENDING / NO_MERGE.**

The source now contains the main V2.2 implementation, but final exact-HEAD Ubuntu+Windows CI and ZIP/JUnit artifacts have NOT yet been independently accepted at the time of writing. Earlier #261 builds failed an overly strict success reason assertion (`SCOPED_CONSUMER_CLOSURE`), then a later exact commit passed; that evidence is NOT transferable to #265 current HEAD. A live EF02 executor-to-candidate-to-auto-merge demonstration has not occurred. Aggregate multi-component onboarding explicitly returns HOLD until exact-tree fan-in proof exists.

### Remaining acceptance before marking Ready
1. Read exact current #265 HEAD/base/main and latest Ubuntu+Windows CI run; verify actual selected/collected/executed counts, oracle, Node, artifact ZIP/JUnit and closure, not just workflow SUCCESS.
2. Repair any failed targeted tests within #265 and rerun scoped verification. Confirm the baseline consumer tests are included by deliberate A17-before-#256 offline canary (no paid executor).
3. Independent Brain source/security review of the 13-file diff and protected file changes; keep Draft / HOLD if any AC or CI is UNKNOWN.
4. Only after successful evidence and current-base reconciliation can authenticated Brain PASS trigger the existing guarded auto-merge. Do not manually merge Draft or bypass exact-tree checks.
5. Preserve V2.1 rollback; collect runtime measurements later. No unverified percentage speed saving is claimed.
