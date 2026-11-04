# HostOps Personal — direct route in AI Commerce

**Decision:** Human on 2026-11-04 selected single-account personal use and requested that setup occur entirely in `trung-via/ai-commerce`, using the existing Windows self-hosted runner. No second repository runner, external credential broker, Android signing, or new Windows account is needed for this limited mode.

## One existing pipeline

`ChatGPT → public AI Commerce Issue → existing Windows self-hosted runner (ai-commerce-ef02) → result comment on the same Issue → ChatGPT`.

The trusted `.github/workflows/issue-ingress.yml` is the only issue ingress. It contains the new Personal HostOps job directly, without a second workflow; old private-only HostOps workflows are not modified or reactivated.

**READ:** The Human requests and approves a read in ChatGPT. An authenticated GitHub connector creates an owner-authored Issue titled `[HOSTOPS READ] <purpose>` with an exact JSON body; its opening triggers one Windows operation. No second Human approval. READ operations now support `inventory`, `git_snapshot` (HEAD, branch, count of tracked changes only), `tracked_paths` (up to 50 public git-tracked paths), `read_text` (max 8 KiB, committed and unchanged public source/docs), and `file_digest` (SHA-256/size only for approved scratch/local-note files). Untracked names, changed file content, secrets and credential files are not sent to a public Issue.

**WRITE:** ChatGPT creates a public Issue titled `[HOSTOPS WRITE] <purpose>` containing the exact target and non-sensitive text. Merely opening it NEVER executes the write. The Human opens this Issue in the ordinary GitHub UI and types **exactly** `APPROVE WRITE #N` (N is the Issue number) as a new comment. The dedicated workflow admits this one owner-comment event, then creates **one new** `hostops-personal-*.txt|md|json` file, <=2048 UTF-8 bytes, in either the existing scratch root `C:\TOOL\AI-Commerce-HostOps-Approved` (`root_id=scratch`) or the existing ignored worktree notes directory `C:\TOOL\ai-commerce\.local` (`root_id=workspace_notes`). A missing directory produces HOLD; no directory auto-creation, overwrite, source edit or arbitrary command. No overwrite, delete or arbitrary PowerShell. Success returns SHA256/size; ambiguous effect returns HOLD and must be checked on disk before attempting a different request.

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

## Expanded V2 requests (same schema, same approval)

Git worktree snapshot — READ through ChatGPT, no second Human action:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"git_snapshot","parameters":{}}
```

List up to 50 tracked source paths:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"tracked_paths","parameters":{"prefix":"src/ai_commerce/hostops_v3/"}}
```

Read only public committed source with no uncommitted edits:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"read_text","parameters":{"root_id":"workspace","relative_path":"src/ai_commerce/hostops_v3/personal.py"}}
```

Independently check a prior created file's SHA-256 without publishing its text:

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"file_digest","parameters":{"root_id":"scratch","relative_path":"hostops-personal-smoke-20261104.txt"}}
```

Create a worktree-local, ignored handoff note (Human posts `APPROVE WRITE #N` once):

```json
{"schema":"ai-commerce.personal-hostops.v1","operation":"create_text","parameters":{"root_id":"workspace_notes","relative_path":"hostops-personal-handoff.md","content_utf8":"Approved handoff note"}}
```

These fixed operations are not general PowerShell, arbitrary shell, repo source edit or a PULSE production runtime. The existing EF02/PR pipeline remains the approved route for editing tracked code. READ requests still produce public Issue receipts, so only request public source and nonsensitive machine metadata.

## Operational acceptance

1. Merge the reviewed PR with exact-head Windows CI evidence and independent Brain source acceptance.
2. Create a **new** `[HOSTOPS READ]` Issue through ChatGPT. Verify the exact repo Issue receipt and actual `ai-commerce-ef02` self-host Windows job completion. This demonstrates only the allowed read.
3. For a separate new scratch write, create a `[HOSTOPS WRITE]` Issue. Await the Human's one-line comment, then verify the receipt and a separate READ `file_digest` Issue on the Windows runner to compare the recorded SHA. No automatic retry of ambiguous writes.

Old private W1.3 Issues #6/#7 and signed requests remain **CONSUMED / NO_RETRY**. Public Issue bodies/comments expose all request metadata and any returned text: never ask this mode to read secrets, cookies, credentials, personal files or other sensitive data. Running public-repository self-host workflows under the same Windows account does not isolate its private tokens, so keep unrelated private provider credentials outside this route.

## Status claims

`SOURCE_MERGED`, `RUNNER_JOB_OBSERVED`, `READ_RETURNED` and `WRITE_DISK_VERIFIED` are separate. Do not claim a live laptop operation based on CI, source code or an Issue created by ChatGPT alone.
