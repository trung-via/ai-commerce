# EF02-D5 — Optional dedicated Chrome CDP acquisition (operator-only)

Status: scoped implementation proposed for Human review; **not** a live Wake proof or runner installation. Reuses the technical pattern of AIOS-Renew's Environment.command() and ownership-check approach, not AIOS Python imports, browser profiles, credentials, routes or runtime authority. Existing AI Commerce D1–D5 completion, consent, and Doorbell code remains unchanged.

## Why this exists

D4 uses Playwright connect_over_cdp(endpoint) and deliberately never launches a browser. D5.4 offers a finite, operator-enrolled local handoff but never starts Chrome. On the operator's laptop, ad hoc new-profile Chrome attempts did not produce a verified TCP listener on port 9222; a separate automation profile also encountered Google sign-in restrictions. These observations do not prove a D4 code regression. This tool makes a dedicated Chrome CDP profile and executable/port matching explicit, verifies ownership instead of attaching to arbitrary listeners, and does not defeat Google authentication controls.

## Operator commands and behavior

Probe (read-only):

~~~text
python -m ai_commerce.remote.chrome_acquisition probe --config <PRIVATE-ABSOLUTE-JSON> --checkout <ABSOLUTE-AI-COMMERCE-CHECKOUT>
~~~

Launch (only with explicit opt-in and locally configured allow_launch=true):

~~~text
python -m ai_commerce.remote.chrome_acquisition launch --opt-in --config <PRIVATE-ABSOLUTE-JSON> --checkout <ABSOLUTE-AI-COMMERCE-CHECKOUT>
~~~

The probe validates operator-owned private paths, Google Authenticode signature, a non-elevated interactive Windows session and TCP process identity. If an owned listener exists, it requests `/json/version` over loopback with a strict 2-second timeout, rejects redirects and oversized, malformed, or non-CDP responses. No ChatGPT page or conversation is read. The launch command may start **one** dedicated Chrome process when the endpoint is absent, no Chrome process claims the profile, and no singleton markers are present. It then waits a finite period for exactly one loopback listener, validating process executable, user-data directory, profile name, port, address, and interactive Windows Session ID. A verified existing browser is reported without launching again. Uncertain ownership, multiple listeners, stale locks, or uncertain startup results cause a stop; no automatic retries, forced Chrome termination, cookie access or profile migration.

This is a standalone operator action. It is **not** automatically invoked by EF02 GitHub jobs, D5 listener or D4 Send. It does not open ChatGPT, select conversations, auto-login, click Send, make a watcher/daemon, or change runner configuration. The child Chrome and Windows probe subprocess inherit only a limited allowlist of ordinary Windows environment variables, excluding AIOS, GitHub and AI Commerce Wake secrets.

## External configuration (dedicated AI Commerce profile)

Operator prepares a dedicated private directory under LOCALAPPDATA/AI-Commerce, outside the repository and outside the default Google Chrome User Data and all AIOS-Renew browser directories. The profile directory must exist, e.g. Default. Config JSON must be inside the private LOCALAPPDATA/AI-Commerce root, but outside the Chrome profile and Git checkout. It must never be committed. Before launching, the operator must provision restricted Windows ACLs on the JSON, profile root and selected profile directory with exact owner user SID, SYSTEM/Administrators and OWNER RIGHTS only. The tool does not create or loosen these permissions.

~~~json
{
  "version": 1,
  "executable": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "user_data_dir": "C:\\Users\\OPERATOR\\AppData\\Local\\AI-Commerce\\Chrome-Doorbell",
  "profile_directory": "Default",
  "cdp_endpoint": "http://127.0.0.1:9222",
  "allow_launch": true,
  "exclusive_user_data": true
}
~~~

Replace the illustration with the operator's actual approved paths locally. Do not copy Chrome cookies, profiles, passwords, account tokens or AIOS configuration. Do not disable Google Safe Browsing or attempt to bypass a sign-in warning. Google sign-in might remain unavailable under debugging; a dedicated, already authenticated ChatGPT session is an independent requirement, not established by Chrome launch.

## Proof and rollback

- Probe output: NOT_RUNNING, READY_ATTACH with validated CDP metadata, or a fixed blocking reason. A successful probe may read the loopback `/json/version` endpoint but does not open browser tabs or read chat.
- Launch output: READY_ATTACH with launched true only when the new process owns the expected 127.0.0.1 port, its Google-signed executable matches and its CDP protocol responds. Elevated Administrator Chrome and unavailable interactive desktops are blocked. If launch reports uncertainty, inspect local processes before repeating.
- D4 still requires an already-open exact ChatGPT conversation with separate valid D2/D3 route, signed grants and idempotency authority. This CLI neither enables Wake send nor claims chat_delivery_proven or brain_continuation_proven.
- Tests cover synthetic config, mocked CDP HTTP responses, mocked Windows single-spawn behavior, invalid ownership, environment secret filtering and Windows argv parsing. Hosted CI PASS does not prove live Google login, Playwright attach or end-to-end Wake.
- Source rollback removes only this module/tests/document. No Windows service, runner, live browser or operator secrets are modified by the code review.


## Remaining live acceptance

A valid signed Chrome executable and `/json/version` response do not establish that Google will accept a login or that Playwright can attach to the original regular ChatGPT conversation. Windows operator login and D4 exact-chat conformance remain separate steps with Human authorization. Do not copy Chrome credentials from AIOS-Renew or bypass Google security. `LIVE_WAKE_NOT_PROVEN` remains the only supported status.
