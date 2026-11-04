# HostOps W1.4 Phase 2 — fixed private GitHub REST reader (read-only source)

Parent [HostOps W1 #309](https://github.com/trung-via/ai-commerce/issues/309). Predecessor: [PR #324](https://github.com/trung-via/ai-commerce/pull/324), `main@4a94916c83f865e88c3cd2381b75fa5f4c312a77`, W1.4 Phase 1 signed canonical Issue ingress without dispatch.

## Trust / capability ceiling

**Purpose:** provide a bounded, HTTP GET-only source adapter for a *future isolated private token broker*. Authenticated HTTPS through the local CA can check GitHub REST metadata, but passing a token to this function is NOT proof of protected credential custody, an independently controlled worker or a remote effect. This phase does not wire an entrypoint or ever call this adapter from the public Windows self-host runner SID.

`src/ai_commerce/hostops_v3/w1_github_reader.py` has:

- `read_private_issue_snapshot(token, issue_number=...)`: requires a caller-supplied transient credential (never environment/config/file). Uses `urllib` HTTPS with CA validation, inherited proxies disabled, no redirects and strict timeout/response size. Only two fixed `GET` endpoints, no dynamic arbitrary URL:
  1. `https://api.github.com/repos/trung-via/ai-commerce-hostops-control`
  2. `https://api.github.com/repos/trung-via/ai-commerce-hostops-control/issues/<exact_positive_issue_number>`
- Accepts JSON media type and HTTP 200 only, no 301/302 relocation, 304 cached acceptance, pagination or automatic retry. Rejects malformed JSON/duplicate keys/oversize responses and unexpected final URL. Network errors produce fixed `W14_GITHUB_API_UNAVAILABLE`, never reveal token or response body.
- Confirms repo `full_name`, `private=true`, exact GitHub API/HTML URLs, repo/owner identities. **Integrates** the separately merged `w14_origin.normalize_private_issue_rest` gate from `main@d0f1f82e90ce7cb5673c94883b5967a677d601e7`, which pins numeric private repository ID **1443294096**, owner/author account ID **301941572**, Issue node ID and `updated_at` format in addition to the existing endpoint/body checks. Confirms exact Issue number/ID, OPEN state, original owner login/id and raw body marker `HOSTOPS_W1_SIGNED_ISSUE_V1`; rejects PR-as-Issue. This is validation of API data, **not** a broker signature or independent credential attestation.
- `inspect_github_issue_readonly` runs the existing **real** independent OpenSSH W1 signature verification against the snapshot; verifies exact source SHA, Issue number, effect-bound contents and <=600-second TTL. Returns sanitized hash/ref claims only.
- Both source functions deliberately return `github_origin_independently_attested=false`, `external_token_custody_proven=false`, `host_effect_authorized=false` and `remote_write_enabled=false`. No SQLite ledger reservation, W1 CLI, Windows native create, issue comment, service, EF02, Wake or PULSE action.

This is **not** a user-facing command. No `GITHUB_TOKEN`, PAT, signing key or credential must be placed in public code, CI variables, same-SID Credential Manager, DPAPI, terminal history or issue body.

## Verified GitHub API specification

- `GET /repos/{owner}/{repo}`: GitHub token scope **Metadata (read)** for private repo, current REST API response contains `private`, `full_name`, `owner`, `id`. <https://docs.github.com/en/rest/repos/repos#get-a-repository>
- `GET /repos/{owner}/{repo}/issues/{issue_number}`: GitHub App installation or fine-grained token with **Issues (read)**; note PR also appears as an Issue; a transfer produces HTTP 301 which must **not** be followed as a valid W1 effect. <https://docs.github.com/en/rest/issues/issues#get-an-issue>
- API header `X-GitHub-Api-Version: 2022-11-28` is a conservative pinned REST version.

## Negative AC and scope limitations

- Bad token syntax, invalid Issue number: deny *before any network*.
- Private-to-public metadata downgrade, wrong ID/owner/repository/URL, Issue transfer/redirect, closed Issue, PR-as-Issue: deny.
- Bad status, unsupported media type, malformed/duplicate JSON, body without W1 marker, modified packet, wrong signed source, expired signature: deny.
- In CI, use a fake opener and **test-only OpenSSH Ed25519 key**; no real GitHub token, live private data, private Android key, external service or Windows filesystem effect.
- Recorded fields are GitHub **API response validation**, not a cryptographically independent immutable GitHub Issue provenance receipt. Issued token provenance, TLS process isolation from same-SID public runner, Issue content immutability, independent root/key attestations, anti-rollback and fenced intent, and signed remote readback remain **UNPROVEN**.
- The historical W1.3 requests from private Issues #6 and #7 are **consumed / NO_RETRY**. Do not reuse or submit either to this reader to simulate new authorization.

## Acceptance and next engineering cut

Exact-HEAD Windows-only CI must select new MT01 data-onboarded `hostops-w14-api` and existing HostOps consumer closure; Python, Node, Oracle and artifact checks required; Brain independent PR acceptance + trusted auto-merge may accept **SOURCE ONLY**.

Acceptance ceiling: `W14_GITHUB_REST_READONLY_SOURCE_MERGED_PASS`, **not** live authenticated token retrieval, token custody, HostOps remote write or independent host receipt.

The next cut must implement and *demonstrate* an independently isolated short-lived GitHub App installation credential broker, immutable private Issue body digest confirmation / revoked-or-stale checks, externally anchored fenced at-most-once reservations and separately authorized Windows worker readback. A public Actions runner under the same Windows SID may not impersonate that broker or hold live private tokens.
