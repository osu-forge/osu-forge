# osu-forge

An extensible analysis and tuning platform for **osu!stable** on Windows.

osu! tells you your average hit error on the results screen. It does not tell you
whether that error is an *offset* problem or a *consistency* problem, and it never
tells you that two of your keybinds collide or that your frame limiter setting has
been silently inert for months. osu-forge accumulates evidence across your own
replays, config, and hardware, and turns it into recommendations you can act on —
with the sample size and confidence interval attached to every one of them.

> **Status: early development.** Nothing here is released yet. The offline
> analysis path is being built before the live-state engine; see
> [`docs/architecture.md`](docs/architecture.md).

---

## What it does

| Area | What you get |
|---|---|
| **Offset** | Mean hit error and unstable rate computed from your `.osr` replays against the beatmap, aggregated with cluster-robust statistics. Distinguishes *"your offset is wrong"* from *"your timing is inconsistent"* — the two have completely different fixes. |
| **Keyboard** | K1/K2 balance, alternation failure on streams, per-key hit-error bias, hold-overlap rate. Plus a live Raw Input hardware test for switch chatter, N-key rollover, and real polling rate. |
| **Aim** | Cursor trajectory, overshoot/undershoot, aim error at hit time, cm-per-playfield in physical units. |
| **Config doctor** | Rule-based lint over `osu!.<user>.cfg`, the Windows registry, real monitor refresh rate, and audio device latency. |
| **Live** *(planned)* | Real-time pp counter and offset display over a self-contained memory-reading engine. |
| **Extensions** | Three-tier extension model — web-page overlays, process-isolated analysis extensions, and first-party in-process modules. |

---

## Guarantees

These are architectural, not aspirational. Each is enforced by a CI check.

**osu-forge never writes to osu!.**

- The engine opens the game process with `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION`
  and nothing else. No `PROCESS_VM_WRITE`. No `PROCESS_ALL_ACCESS`.
- `SeDebugPrivilege` is never requested or enabled. **No elevation is required.**
- `WriteProcessMemory`, `CreateRemoteThread`, `VirtualAllocEx`, `SendInput`,
  `keybd_event`, `mouse_event`, and `SetWindowsHookEx` appear nowhere in this
  repository. CI greps for them and fails the build.
- No DLL injection. No overlay hooking into the game's render pipeline.
- osu!'s own files — `osu!.cfg`, `osu!.db`, `scores.db`, `Data\r\` — are opened
  read-only and never modified.

**osu-forge only recommends.**

There is no `apply`, `fix`, or `write` command anywhere in the CLI. Settings changes
are printed as a diff; you make them yourself in-game. This is deliberate: osu!
rewrites its config on exit, so a third-party process editing it would lose your
changes anyway.

**Statistics are reported honestly.**

Every aggregate carries its sample size and a 95% confidence interval. Cells below
a minimum sample size are visibly marked unreliable and are excluded from
recommendations entirely. When there isn't enough data to recommend a change, the
tool says exactly that instead of hedging.

---

## Is this allowed?

Short answer: nothing in osu!'s rules or terms prohibits reading game memory for
display. But you should read the actual sources rather than take our word for it.

- The [osu! Rules](https://osu.ppy.sh/wiki/en/Rules) prohibit *"third-party
  utilities of any kind to get any sort of advantage."* The operative test is
  **advantage**, not memory access. osu-forge does nothing you should be doing
  yourself — it reads what already happened and reports statistics about it.
- The osu! wiki's [Community/Projects](https://github.com/ppy/osu-wiki/blob/master/wiki/Community/Projects/en.md)
  page lists memory-reading tools (gosumemory, osu!StreamCompanion) by name, while
  stating plainly that community projects are *"not endorsed by osu!, nor do they
  have any official support."*
- The [osu! Terms of Service](https://github.com/ppy/osu-wiki/blob/master/wiki/Legal/Terms/en.md)
  contain no anti-reverse-engineering, anti-memory-reading, or third-party-client
  clause.
- **There is no whitelist of approved tools and no approval process.** Anyone who
  tells you a tool is "officially allowed" is guessing.

osu-forge is tolerated-but-unsanctioned territory, like every other tool in this
space. We keep the read-only guarantees above precisely so that the distinction
between "reads and displays" and "assists play" stays unambiguous.

---

## Antivirus false positives

**Expect them.** Any tool that reads another process's memory trips heuristic and
ML-based detection, and this affects every tool in this category — including the
most established ones, repeatedly and unresolved.

What we do about it:

- Release binaries are code-signed.
- Every release publishes SHA-256 checksums. Verify before running.
- We request the minimum possible process access rights (see Guarantees above),
  which is both the correct security posture and the best available mitigation.
- We do not pack, obfuscate, or self-update by overwriting our own binary.

If your AV flags a signed release, please open an
[AV false positive issue](.github/ISSUE_TEMPLATE/av_false_positive.yml) — it
includes the vendor submission steps.

---

## Repository layout

```
engine/      Rust — osu!stable memory engine, separate process, read-only
diffcalc/    Rust — difficulty and pp calculation (independent implementation)
core/        Python — platform: event bus, extension host, local server
extensions/  first-party analysis extensions
overlays/    first-party overlays (HTML/JS)
hwtest/      Windows Raw Input hardware diagnostics, separate process
web/         dashboard and HTML report templates
docs/        architecture, licensing policy, privacy disclosure
```

The engine and the hardware tester run as **separate processes** on purpose. They
are the components most likely to break on a game update, get flagged by AV, or
crash — isolating them means none of those takes down the rest of the tool.

---

## Privacy

The hardware diagnostics use Windows Raw Input, which can observe keystrokes while
other windows are focused. We take that seriously:

- **Default is a five-key allowlist**, filtered *in the input handler* — anything
  that isn't one of your configured osu! gameplay binds is discarded before it
  reaches any buffer.
- **Never runs as a background service.** No service, no scheduled task, no `Run`
  key, no tray resident. It runs inside an explicit command and dies with it.
- Between interactive test prompts the process is **unregistered from raw input
  entirely** — there is no capture to filter.
- Raw events are deleted at session end by default; only derived statistics persist.
- First run shows a full disclosure and requires you to type `yes`.

The full disclosure text is in [`docs/privacy.md`](docs/privacy.md).

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Note the dependency-license policy: this
project is Apache-2.0 and **GPL / LGPL / AGPL dependencies are rejected by CI**.

## License

[Apache-2.0](LICENSE). See [`NOTICE`](NOTICE) for third-party attributions and
[`docs/licensing.md`](docs/licensing.md) for the provenance rules governing
memory-layout data.

osu! is a trademark of ppy Pty Ltd. This project is not affiliated with,
endorsed by, or supported by ppy Pty Ltd.
