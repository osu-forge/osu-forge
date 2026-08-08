# Security Policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub Security Advisories](https://github.com/osu-forge/osu-forge/security/advisories/new)
rather than a public issue.

Include what you did, what happened, and what you expected. If it involves the local
HTTP/WebSocket server, include the exact request — headers matter here.

## Scope

osu-forge runs entirely on the user's machine and exposes a local server. The
security-relevant surface is:

| Component | Concern |
|---|---|
| Local HTTP/WS server | Reachable by any local process and by any web page the user visits |
| Extension host | Runs third-party code |
| Config parser | Handles a file containing an encrypted credential blob |
| Hardware logger | Can observe keystrokes from other windows |
| Memory engine | Reads another process |

## Design guarantees

These are the properties an attacker would have to break. If you find a way to break
one, that is a vulnerability worth reporting.

**The engine never writes.** `OpenProcess` is called with
`PROCESS_VM_READ | PROCESS_QUERY_INFORMATION` only. `SeDebugPrivilege` is never
enabled. `WriteProcessMemory`, `CreateRemoteThread`, `VirtualAllocEx`, `SendInput`,
`keybd_event`, `mouse_event`, and `SetWindowsHookEx` are absent from the repository
and CI enforces this. No elevation is required or requested.

**The local server is defended in layers.** A local server is reachable from any web
page the user visits — IP-based filtering does nothing about that, because the
request originates from the user's own machine. So:

1. Bound explicitly to `127.0.0.1` — never `0.0.0.0` or `::`.
2. The `Host` header is validated on every request. A DNS-rebinding attack
   necessarily carries the attacker's hostname, so this catches it.
3. `Origin` is validated against an allowlist on WebSocket upgrade. Never `*`.
4. A per-session bearer token is required on every request. This holds even if 2
   and 3 are bypassed.
5. Anything that mutates the filesystem requires the token **and** in-app
   confirmation. Never a bare GET.

**Credentials are redacted at the parser boundary.** `osu!.<user>.cfg` contains a
DPAPI-encrypted password blob. It is replaced with a sentinel *in memory* at parse
time — the plaintext is never stored on the parsed object, so it cannot reach a
report, a log, or a shared diff even by accident. This matters because users share
reports. A test renders every output format from a fixture containing sentinel
secrets and asserts they appear nowhere.

**The hardware logger is filtered at capture time.** Keys outside the configured
allowlist are discarded inside the `WM_INPUT` handler, before reaching any buffer.
Between interactive test prompts the process is unregistered from raw input
entirely. It never runs as a background service.

**Extensions are mediated.** Process-isolated extensions receive neither filesystem
paths nor network credentials. They request `beatmap(md5)` and receive a parsed
object; the host owns the file handles and the API token.

## Not vulnerabilities

- **Antivirus false positives.** Expected for any memory-reading tool. Use the
  [AV false positive template](.github/ISSUE_TEMPLATE/av_false_positive.yml).
- **A stale signature after an osu! update.** Use the
  [engine broken template](.github/ISSUE_TEMPLATE/engine_broken.yml). The engine is
  designed to fail loudly rather than report wrong values; if it reported *wrong*
  values instead of failing, **that is a bug worth reporting** — silent corruption
  is the failure mode we design against.

## Supported versions

Pre-release. Only the latest commit on `main` is supported.
