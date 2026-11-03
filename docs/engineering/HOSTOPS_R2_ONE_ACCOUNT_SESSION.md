# HostOps R2 — One Windows account / supervised private read session

**Status:** Source candidate; **not installed or executed on the Human laptop** until native evidence. Design decision: [#306](https://github.com/trung-via/ai-commerce/issues/306), continuing verified [R1 #302](https://github.com/trung-via/ai-commerce/issues/302).

## Operating model

One Windows sign-in account (no Broker/Transport service identities), a terminal that the Human opens, and a **short-lived session with a private GitHub Issues-only credential typed into a concealed prompt**. The command `python -B -m ai_commerce.hostops_v3.cli personal-supervised-session --config <temporary-disabled-personal-config> --session-seconds 600` scans the private control repo every 15 seconds; maximum lifetime 30 minutes and maximum five successful reads per session. The Human must first type `START R2 READ ONLY`, then type the exact `ALLOW READ <issue> <request_id>` for **each** request. Closing the terminal or Ctrl+C stops the session. The personal config remains `enabled=false` and never stores the credential.

Only the already-established `hostops.personal.remote_read.v1` **fixed** `local_os_inventory` request is accepted, with empty parameters, 10-minute issue TTL, owner-authored private control repo, exact Git source SHA, and fixed sanitized result. Before each request and after consent the source/identity/KILL/source SHA are checked; Issue authentication is refreshed after consent. A single ambiguous receipt POST produces `HOSTOPS_R2_UNKNOWN` and immediately stops the session; it never silently retries. Expired/already-completed Issues are skipped. Unbounded queue/unsupported private routes fail closed.

This is a longer-lived **interactive connection**, **not an unattended auto-start service**, and does **not** grant arbitrary file/system read or any write to the user's machine. No credential is persisted or placed into a public-repository runner, Windows task scheduler, environment, command line, config, logs or GitHub Issue.

## No claim of credential isolation

Two programs under the same Windows user SID share access to much of the same process and filesystem security context. NTFS ACLs, DPAPI, Credential Manager or HMAC **do not** stop malicious public-repository Actions jobs sharing that identity from reading the session credential or impersonating its network capability. The Human must avoid running untrusted public self-hosted jobs while a supervised session holds a private credential. This milestone deliberately keeps `PERSONAL_MODE_NOT_ENROLLED` for normal unattended Broker/Transport paths and **does not** install a daemon.

## Future write contract (W1 — NOT YET IMPLEMENTED)

A private GitHub Issue authored by `trung-via` is **not** independent Human approval for a host write, because ChatGPT tooling can create Issues. Therefore the R2 source accepts **zero** write operations. An eventual single-user write feature requires an independent Human signature over the *exact canonical* tuple: purpose, source/workflow SHA, single target under a pre-approved directory, content hash+size, create-new/no-overwrite semantics, request ID, expiration, authority version and private Issue binding. The signing key must not be accessible to the public self-host runner or to the unsigned ChatGPT request origin. Do not treat a confirmation string under a shared Windows SID as a cryptographic signature.

Write verifier requirements: no path traversal, UNC/reparse points, symlink, elevated process, unexpected extension, protected root, overwrite, shell command, disclosure or broad glob; fixed maximum payload, separate local no-overwrite atomic create + fsync receipt, durable at-most-once intent ledger, and distinct `UNKNOWN` reconciliation before re-execution. On current one-account/public-runner architecture there is **no proven protected signer boundary**; default W1 decision is `WRITE_DISABLED`, `UNATTENDED_READY=false`.

## Verification scope

Hosted Windows CI + MT01 owner/consumer tests provide only source evidence. Native Human laptop must separately prove active non-admin same-SID terminal, exact source SHA, successful private Issue read/receipt and controlled session shutdown. Source CI must never claim an always-on unattended connection or signed local write.
