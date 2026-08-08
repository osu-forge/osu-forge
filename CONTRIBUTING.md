# Contributing to osu-forge

Thanks for your interest. Please read the two hard policies below before opening a
PR — they are enforced by CI and a PR that violates either cannot be merged
regardless of how good the code is.

---

## Policy 1 — Dependency licenses

osu-forge is **Apache-2.0**. To keep it that way:

| Verdict | Licenses |
|---|---|
| **Allowed** | MIT, Apache-2.0, BSD-2/3-Clause, ISC, MPL-2.0, Unlicense, Zlib |
| **Rejected** | GPL-*, LGPL-*, AGPL-* — any version, any variant |
| **Rejected** | No license file at all. Absent a license, default copyright applies and **all rights are reserved** — that is more restrictive than GPL, not less. |

`cargo-deny` (Rust) and `pip-licenses` (Python) run in CI and fail the build on a
copyleft or unlicensed dependency. If you need something that isn't allowed, open
an issue first — usually the answer is to implement the narrow slice we actually
need rather than take the dependency.

Development-only tools that are never linked into a distributed artifact are the one
exception, and they live in `tools/` with the exception documented in place.

## Policy 2 — Provenance of memory-layout data

The `engine` component depends on byte patterns and struct offsets describing
osu!.exe. These are treated as **facts about a compiled binary**, not as anyone's
authored expression, and they live in a data table separate from all logic.

**Never copy from other osu! memory-reading projects:**

- source code, in any language, including line-by-line translations
- comments, struct definitions, field naming schemes, or type hierarchies
- **output schemas** — a schema is expression, and there is no factual-necessity
  argument for reproducing one

Write comments as assertions about the binary:

```rust
// osu!.exe stable 20260711.1: combo is int16 at scoreBase + 0x94
```

not as citations of another project's source. See [`docs/licensing.md`](docs/licensing.md)
for the full procedure, including the clean-room workflow for signature discovery.

> **If you use an AI assistant**: models trained on existing osu! tooling will
> reproduce that tooling's field names and layout by default. This is the most
> realistic way for a violation to slip in. Design schemas and naming deliberately,
> and say so in your PR description.

---

## Development setup

Requires **Python 3.14+**, **Rust** (stable, `x86_64-pc-windows-msvc`), and
**Windows 10/11**.

```bash
py -3.14 -m venv .venv
.venv\Scripts\pip install -e "core[dev]"
rustup toolchain install stable
cargo build --workspace
```

### Checks that must pass

```bash
ruff check . && mypy core/src
pytest
cargo fmt --check && cargo clippy -- -D warnings && cargo test
cargo deny check licenses
```

---

## Things the codebase will not accept

These are architectural invariants with CI checks behind them. A PR that adds any
of them will fail:

1. **`WriteProcessMemory`, `CreateRemoteThread`, `VirtualAllocEx`, `SendInput`,
   `keybd_event`, `mouse_event`, `SetWindowsHookEx`** — anywhere in the repository.
2. **`ReadProcessMemory` outside `engine/`.**

   > The CI check is a plain grep. It skips `//` and `#` comment lines, but it
   > cannot tell a Python docstring from a call, so **do not spell these names in
   > docstrings or string literals outside `engine/`** — write "process-memory
   > APIs" instead. Loosening the filter enough to allow docstrings would also
   > let a `getattr(kernel32, "...")` lookup through, and the check is worth more
   > strict than convenient. (Markdown files are not scanned, so prose in the
   > docs may name them freely — as this line does.)
3. **`hwtest/` importing `core/` or `extensions/`** — the hardware logger must stay
   structurally incapable of correlating input to game state.
4. **An `apply` / `fix` / `write` command** that modifies `osu!.cfg`. The tool
   recommends; the user applies.
5. **Elevation requirements** in the core tool. Optional elevated probes must
   degrade gracefully when denied, never demand admin.

---

## Statistics and honesty

If your change produces a number a user might act on, it must carry its sample size
and a confidence interval, and it must be suppressed when the sample is too small.
"Probably around 8ms" with no `n` is worse than no output at all — it looks like
knowledge and isn't.

Likewise, when a detector cannot detect something, say so. A chatter detector that
finds nothing because it structurally *cannot* find anything must report
*"detector sensitivity ≈ 0 at this resolution"*, never *"no chatter found."*

---

## Pull requests

- One logical change per PR.
- New analysis code needs tests with **known ground truth** — synthetic fixtures
  whose correct answer is derivable analytically, not just smoke tests against real
  data.
- Never commit `.osr`, `.osu`, or `.db` files. Tests build their fixtures
  programmatically; see `tests/fixtures/`.
- Update `CHANGELOG.md` under `## [Unreleased]`.
