# HostOps Personal — direct route in AI Commerce

**Decision:** Human on 2026-11-04 selected single-account personal use and requested that setup occur entirely in `trung-via/ai-commerce`, using the existing Windows self-hosted runner. No second repository runner, external credential broker, Android signing, or new Windows account is needed for this limited mode.

## One existing pipeline

`ChatGPT → public AI Commerce Issue → existing Windows self-hosted runner (ai-commerce-ef02) → result comment on the same Issue → ChatGPT`.

The trusted `.github/workflows/issue-ingress.yml` is the only issue ingress. It calls `hostops-personal.yml`; old private-only HostOps workflows are not modified or reactivated.

**READ:** The Human requests and approves a read in ChatGPT. An authenticated GitHub connector creates an owner-authored Issue titled `[HOSTOPS READ] <purpose>` with an exact JSON body; its opening triggers one Windows operation. No second Human approval. Only fixed metadata (architecture, logical CPU) and UTF-8 **public git-tracked Markdown** under `C:\TOOL\ai-commerce\docs` or `README.md` (max 8 KiB) may be returned in a public Issue.

**WRITE:** ChatGPT creates a public Issue titled `[HOSTOPS WRITE] <purpose>` containing the exact target and non-sensitive text. Merely opening it NEVER executes the write. The Human opens this Issue in the ordinary GitHub UI and types **exactly** `APPROVE WRITE #N` (N is the Issue number) as a new comment. The dedicated workflow admits this one owner-comment event, then creates **one new** `hostops-personal-*.txt|md|json` file, <=2048 UTF-8 bytes, under the fixed `C:\TOOL\AI-Commerce-HostOps-Approved` root only. No overwrite, delete or arbitrary PowerShell. Success returns SHA256/size; ambiguous effect returns HOLD and must be checked on disk before attempting a different request.

**The owner GitHub account can also be used by ChatGPT's connector:** this confirmation is a simple operational workflow, not cryptographically independent Human authentication. ChatGPT must **never create its own WRITE approval comment**. If strong separation becomes necessary later, a separate non-connector approval factor would be needed; do not misrepresent owner comments as such.

## Exact request examples

Read basic Windows inventory, after Human chat approval:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"inventory","parameters":{}}
```

Read a public tracked document on the laptop:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"read_text","parameters":{"root_id":"workspace","relative_path":"README.md"}}
```

Request a new scratch proof; the Human alone posts `APPROVE WRITE #N` on that Issue:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"create_text","parameters":{"root_id":"scratch","relative_path":"hostops-personal-proof.txt","content_utf8":"AI_COMMERCE_PERSONAL_HOSTOPS_PROOF_V1"}}
```

## Operational acceptance

1. Merge the reviewed PR with exact-head Windows CI evidence and independent Brain source acceptance.
2. Create a **new** `[HOSTOPS READ]` Issue through ChatGPT. Verify the exact repo Issue receipt and actual `ai-commerce-ef02` self-host Windows job completion. This demonstrates only the allowed read.
3. For a separate new scratch write, create a `[HOSTOPS WRITE]` Issue. Await the Human's one-line comment, then verify the receipt and a separate Windows disk SHA readback. No automatic retry of ambiguous writes.

Old private W1.3 Issues #6/#7 and signed requests remain **CONSUMED / NO_RETRY**. Public Issue bodies/comments expose all request metadata and any returned text: never ask this mode to read secrets, cookies, credentials, personal files or other sensitive data. Running public-repository self-host workflows under the same Windows account does not isolate its private tokens, so keep unrelated private provider credentials outside this route.

## Status claims

`SOURCE_MERGED`, `RUNNER_JOB_OBSERVED`, `READ_RETURNED` and `WRITE_DISK_VERIFIED` are separate. Do not claim a live laptop operation based on CI, source code or an Issue created by ChatGPT alone.
