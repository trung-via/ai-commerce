# HostOps v2 — expanded READ and tiered WRITE

**Status: candidate until this PR merges. Existing HostOps v1 is unchanged.**

## Security model

- Private repository and verified GitHub Issue author (`trung-via`) are checked on hosted validation and independently on the Windows runner. A GitHub Issue is **not** independent Human approval because connected tools can create Issues.
- Read permission covers fixed OS metadata only, never arbitrary PowerShell, raw command lines, user paths, full environment variables, tokens, private files, browser profile data, account messages or filesystem contents.
- Routine write (`scratch_marker_write`) creates a bounded marker inside the job's own `.local/hostops-v2-scratch` directory. No overwrite.
- Sensitive write (`workspace_file_write`) requires a Human Ed25519 SSH signature over **the exact canonical request**; its destination must be inside independently configured workspace root. No overwrite, delete, network command, restart or Windows elevation.
- EF02 dispatch, Chrome launch, CDP navigation, ChatGPT Send, D5 Wake and UNKNOWN retry remain prohibited.

## Operations

| Operation | Permission | Parameters | Effect |
| --- | --- | --- | --- |
| `host_inventory_read` | read | `{}` | Session/PowerShell/process counts; 9222/9333 TCP inventory; interactive runner task state |
| `d5_deep_read` | read | `{}` | Above and in-memory counts of Chrome 9333 debug flags / user-data-dir flags, not their values |
| `d5_pid_map_read` | read | `{}` | Up to 32 sanitized Chrome PID rows, up to 16 TCP listener owner-PID rows for ports 9222/9333, ephemeral per-run profile groups and directory-exists flags; no raw paths/commands |
| `scratch_marker_write` | automatic bounded write | `{"marker":"PASS"}` | New marker in job-only scratch directory |
| `workspace_file_write` | signed Human approval | `{"relative_path":"notes/report.md","content":"text"}` | New text/JSON/Markdown file under approved root, max 2 KiB |

## GitHub request

Open an owner-authored private Issue with title `[HOSTOPS2] <purpose>` and only a JSON body similar to:

```json
{
  "schema": "ai-commerce-hostops-v2",
  "request_id": "hostops-v2-example-001",
  "repository": "trung-via/ai-commerce",
  "source_sha": "<exact-main-commit-sha>",
  "operation": "d5_deep_read",
  "parameters": {}
}
```

Substitute an actual 40-hex SHA from main. Do not include the angle brackets literally. The `issues: opened` event captures the immutable initial body, not later edits. Stages: hosted validate → Windows self-hosted execute → sanitized Issue report, with seven-day artifacts.

## Enrollment to enable signed Human writes

Sensitive writes are **fail-closed** until BOTH conditions are met: (1) the Human has registered an independent Ed25519 public SSH key in `config/hostops/human-allowed-signers`, and (2) repository Actions variable `HOSTOPS_APPROVED_WORKSPACE_ROOT` identifies a dedicated **existing directory** on the Windows host. Never use drive roots, Chrome profiles, keys, system paths or unrestricted shared dirs.

Human enrollment:

1. On a Human-controlled machine, choose/generate an independent signing key via `ssh-keygen -t ed25519 -f <private-key-path>`; retain the PRIVATE key outside GitHub, Actions and ChatGPT.
2. Through a separately reviewed PR, enroll its PUBLIC key in the one-line format `ai-commerce-human ssh-ed25519 AAAA... comment` in `config/hostops/human-allowed-signers`.
3. Set `HOSTOPS_APPROVED_WORKSPACE_ROOT` in private repo Settings → Secrets and variables → Actions → Variables to a narrow existing local workspace root.
4. Construct the full JSON request excluding `approval_signature_b64`, with the selected operation/path/content/latest SHA and a fresh request ID. Human independently inspects this content.
5. Canonicalize as `(json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=True)+'\n').encode('ascii')`, save bytes to `request.canonical.json`. Sign using `ssh-keygen -Y sign -f <private-key-path> -n hostops-v2 request.canonical.json`; base64-encode the produced `.sig` file and add it as `approval_signature_b64` to the final Issue JSON.
6. On Ubuntu AND Windows, the script uses `ssh-keygen -Y verify` against enrolled key, identity `ai-commerce-human`, namespace `hostops-v2`. Any changed SHA, operation, path, content or request ID invalidates signature. A replay cannot overwrite an existing file (`O_EXCL`).

Do not ask ChatGPT or the GitHub connector to possess the Human private signing key. No key enrollment or sensitive write is claimed by this PR. Human approval here is **per exact sensitive request**, not a blanket grant.

## Verification

Run `python -m unittest discover -s tests -p test_hostops_v2.py -v`. CI targets Ubuntu/Windows hosted runners with PowerShell parser and bounded hosted smoke. A CI PASS is not proof of Windows laptop deployment; one authorized new Issue is required after merge for live diagnostics. No scripts or configurations in the existing EF02 runner are modified.

## D5 PID map diagnostic (new, read-only)

The new `d5_pid_map_read` operation is available only after this PR is separately approved and merged. It inventories Chrome Win32_Process metadata and OS TCP listeners. Each Chrome process row contains a PID, Windows session ID, whether its command line was readable, flags for the two CDP ports, an ephemeral profile group such as `p01`, and a boolean/unknown that the claimed profile root directory exists. The OS TCP rows identify owner PID, port, loopback/other binding and whether the PID belongs to a Chrome process. **No profile path, process command line, cookies, keys or Chrome page is sent back.**

`profile_identity_verified=false` is intentional: equal `p01` means equal observed `--user-data-dir` claims *inside the same run*; it does **not** authenticate a particular D5 canonical profile or prove live browser attachment. `profile_root_exists=true` does **not** prove the directory is exclusively owned or unlocked. PIDs, flags and listeners can change between reads. When CIM or TCP inventory fails, the corresponding `process_known` / `tcp_known` is false, never silently reported as an empty inventory.

The updated Issue report prints only bounded PID/flags/profile-group mappings and TCP port owners; records are truncated to the verified limits, with explicit truncation flags. If no port is listening but a Chrome PID has `debug_9333=true`, classify it as a **debug-flag-without-listener observation**, not automatic proof of why Chrome CDP failed.

Use a new `[HOSTOPS2] ...` Issue with an immutable JSON body and `"operation":"d5_pid_map_read"`, `"parameters":{}`, and the exact new `main` source SHA after merge. This operation never launches Chrome, attaches to a page, alters profiles, triggers Wake/EF02 or changes approval keys. The Human signer gate and scoped write behavior remain unchanged.
