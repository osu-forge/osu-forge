# Privacy

osu-forge reads gameplay data that identifies you and, for hardware diagnostics, can
observe keystrokes system-wide. This document states exactly what is collected, what
is discarded, and where the boundaries are enforced in code.

**Nothing is ever sent anywhere.** There is no telemetry, no crash reporting, and no
network code in `hwtest` at all — it is standard-library-only, so nothing in its
dependency tree *could* phone home. The only outbound network the project makes is
the optional osu! API client in `core`, used for beatmap lookup and difficulty
verification, and it is disabled entirely under `--offline`.

---

## Live input logger

The hardware tests (`forge hwtest`) use Windows Raw Input with `RIDEV_INPUTSINK`,
which means **the process receives input events even when another window is
focused.** That is what makes the measurements possible, and hiding it would be
dishonest, so it is stated in the first-run disclosure below.

### The design

**Capture-time allowlist is the default.** The `WM_INPUT` handler resolves the
virtual key code and, if it is not on the allowlist, **discards the event before it
reaches any buffer** — the discard is a few lines from the API call, not a filter
applied later over recorded data. Every character of a password typed in another
window is dropped in the same statement that reads it.

The default allowlist is exactly your configured osu! gameplay binds, read from
`osu!.<user>.cfg` — typically five keys.

| Mode | Behavior | Gate |
|---|---|---|
| `--keys=selected` | **Default.** The osu! bind allowlist only. | none |
| `--keys=anonymous` | Timestamp, device, and edge only. The key code is replaced by a per-session random permutation index — enough to count distinct keys for a rollover test, impossible to reconstruct text. | explicit flag |
| `--keys=all` | **Not implemented.** No built-in analysis needs it, and its only function would be to turn this into a keylogger. | — |

**Never a background service.** No Windows service, no scheduled task, no `Run` key,
no tray-resident mode. The logger runs inside an explicit `forge hwtest` invocation,
in the foreground, and dies with the command. There is no start that is not paired
with a stop in the same process lifetime.

**Unregistered is the resting state.** Between interactive test prompts the process
calls `RegisterRawInputDevices` with `RIDEV_REMOVE`, so it is not registered for raw
input at all while waiting for you. This is strictly stronger than filtering: there
is no capture to filter.

**Visible indicator.** A terminal banner is redrawn at least once per second while
recording, plus an audible tone at start and stop:

```
● RECORDING INPUT — S D LShift Space Esc — 00:14 — 412 events — Ctrl+C to stop
```

**Storage and retention.** Events go to
`%LOCALAPPDATA%\osu-forge\sessions\<ISO8601>-<kind>\` as compact binary records with
a JSON metadata sidecar. The sidecar records the exact allowlist that was in force,
which is the audit trail for what was captured.

- Raw events are **deleted at session end by default**, once derived statistics are
  computed. Only histograms and quantiles persist.
- `--keep-raw` requires a second confirmation.
- Anything older than 7 days is purged at startup. `forge hwtest purge` wipes
  unconditionally.
- Hard cap: 200 MB or 30 minutes, whichever comes first.

**Structural isolation.** `hwtest/` cannot import `core/` or `extensions/` — CI
enforces the import ban. The logger has no access to focused-window state, running
processes, or gameplay data. It measures hardware event timing and nothing else.

### First-run disclosure

Shown full-screen before the first capture ever runs. It requires typing `yes` —
not a keypress, because a keypress can be an accident and this is precisely the
moment not to accept one.

> **osu-forge is about to record keyboard input.**
>
> **What is recorded:** the timestamp, device, and press/release edge for these keys
> only — `S`, `D`, `Left Shift`, `Space`, `Escape`. These are your osu! gameplay
> binds, read from your osu! config.
>
> **What is discarded:** every other key. The filter runs inside the input handler,
> so other keys are dropped before being written anywhere — including passwords
> typed in other applications.
>
> **This records while other windows are focused.** Windows Raw Input does not scope
> to the active window. That is required for the measurement to work, and you should
> know it.
>
> **Where it goes:** `%LOCALAPPDATA%\osu-forge\sessions\`. Local only. Nothing is
> uploaded. Raw events are deleted when this session ends; only summary statistics
> are kept.
>
> **How to stop:** Ctrl+C at any time. A banner stays on screen while recording.
>
> Type `yes` to continue, anything else to cancel.

A hash of this text is stored with the consent record. If the disclosure changes or
the allowlist widens, the disclosure is shown again.

---

## Replays, config, and reports

**Replays and beatmaps** are read from your osu! install and never modified. They
stay local.

**Your osu! config contains a credential.** `osu!.<user>.cfg` has a `Password` field
holding a DPAPI-encrypted blob. It is replaced with a redaction sentinel **at the
parser boundary** — the plaintext is never stored on the parsed object, so it cannot
reach a report, a log, or a diff even by accident. The same policy covers any
key matching `pass|pwd|secret|token|credential|auth|session|cookie|api_key`.

This matters more than it might seem, because **users share reports.** A test renders
every output format from a fixture containing sentinel secrets and asserts they appear
nowhere in the HTML, the JSON, or the logs.

**Reports** are single self-contained HTML files with zero external requests (CI
asserts no remote `src=` or `http` reference). They do contain your username, your
beatmap list, and your play statistics — so think before posting one publicly, the
same way you would with a screenshot of your osu! folder.

**osu! API credentials**, if you configure them for difficulty verification, are read
from `OSU_CLIENT_ID` / `OSU_CLIENT_SECRET` environment variables or a gitignored
`.env`. They are wrapped in a type whose string representation is `***`, so they
cannot be logged accidentally.

---

## What osu-forge never does

- Write to osu! memory, synthesize input, or inject into the game process
- Modify `osu!.cfg`, `osu!.db`, `scores.db`, or your replays
- Run in the background, at login, or as a service
- Send any data off your machine
- Record keystrokes outside an explicit, consented, foreground `hwtest` command
