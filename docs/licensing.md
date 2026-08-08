# Licensing and provenance

> **Not legal advice.** This documents the project's working rules and the reasoning
> behind them. Anyone distributing a fork should get their own counsel.

osu-forge ships under **Apache-2.0**. Two things put that at risk, and this document
covers both: the licenses of our dependencies, and the provenance of the memory-layout
data the engine depends on.

---

## 1. Dependency licenses

| Verdict | Licenses |
|---|---|
| **Allowed** | MIT, Apache-2.0, BSD-2/3-Clause, ISC, MPL-2.0, Unlicense, Zlib, CC0 |
| **Rejected** | GPL-*, LGPL-*, AGPL-* — any version, any variant |
| **Rejected** | **No license file.** Absent a license, default copyright applies and all rights are reserved. "Unlicensed" is the *most* restrictive state, not the least — this trips people up regularly. |

Enforced by `cargo deny check licenses` and a `pip-licenses` allowlist in CI. The
allowlist fails closed: a dependency whose license cannot be determined is a build
failure, not a pass.

### What this policy cost us, and what we did instead

Two libraries that would have been natural fits are excluded:

- **`circleguard` / `circlecore`** (AGPL-3.0) — replay analysis. Excluded as a
  runtime dependency. It is still used as a **differential test oracle** in an
  isolated virtualenv under `tools/oracle-venv/`, driven by subprocess. It is never
  linked into a distributed artifact, so the AGPL does not propagate. It is excluded
  from the `dev` extra as well, so `pip install -e "core[dev]"` never pulls it in.
- **`slider`** (LGPL-3.0) — `.osu` parsing. Replaced by our own parser in
  `diffcalc/src/beatmap/`, which we needed anyway for difficulty calculation.

### Difficulty and pp calculation

`diffcalc` is an independent implementation of osu!'s difficulty and performance
algorithms, ported from the **authoritative** sources:

- [ppy/osu](https://github.com/ppy/osu) — MIT
- [ppy/osu-tools](https://github.com/ppy/osu-tools) — MIT

Both are MIT, so this is clean. MIT requires the copyright notice be preserved for
substantial derivation, which is why `NOTICE` carries ppy's notice in full.

We port from ppy's C# directly rather than from a third-party port. A port is one
more layer between us and ground truth, and ports lag — the well-known Rust port
still cited October-2025 upstream commits in an April-2026 release. If we are going
to carry the maintenance burden of an independent implementation, we should at least
be tracking the real thing.

`diffcalc/PORTED_FROM.md` records the exact upstream commit hashes this port
tracks. CI compares our output against the official
`POST /api/v2/beatmaps/{id}/attributes` endpoint; a drift means either we have a bug
or upstream reworked pp, and either way we want to know immediately.

---

## 2. Memory-layout data

The engine needs byte patterns and struct offsets describing osu!.exe. This section
is the policy for obtaining them.

### The position

A byte string like `F8 01 74 04 83 65` and an offset like `+0x94` are **observations
about a compiled binary**, not authored expression. Nobody wrote the fact that combo
lives at that offset — a compiler placed it there. Under
*Feist Publications v. Rural Telephone*, 499 U.S. 340 (1991), facts do not owe their
origin to an act of authorship and are therefore not copyrightable.

*Sega v. Accolade*, 977 F.2d 1510 (9th Cir. 1992) established the shape of the
procedure: **observe → document as fact → implement independently from the
documentation**. That is what we follow.

### Where the seam actually is

Being honest about the weak point: individual patterns are facts, but the
*compilation* — the specific set of signatures, their names, which offsets someone
selected as useful, their arrangement — could carry thin *Feist* compilation
copyright. And wildcard masking is a **choice**, not an observation: two projects
mask the same instruction differently.

This is untested in this domain. The clean-room procedure below exists to make the
question moot rather than to win it.

### Procedure, in priority order

**1. Derive it yourself (preferred).** Find patterns against your own installed osu!
with a disassembler. Beyond giving us the *Sega* fact pattern outright, this teaches
the skill we need anyway — osu! updates move field offsets a few times a year, and
somebody has to re-find them.

**2. MIT-licensed sources.** [UnnamedOrange/osu-memory](https://github.com/UnnamedOrange/osu-memory)
is MIT and carries the base/rulesets/hit/mod signatures. Using it sidesteps the
question entirely. Note it was archived in 2023, so its *offsets* are stale — the
signatures are the durable part.

**3. Clean room, if working from copyleft sources.** The set is small enough
(~16 signatures) that doing this properly costs about a day:

- One session reads the source and writes a **plain-English specification with no
  code in it** — e.g. *"the game state enum is reached by scanning for the
  instruction sequence that compares against 4, then taking the imm32 four bytes
  before the match."*
- The specification is dated and committed under `docs/specs/`.
- A separate session implements from the specification alone.
- The implementer's PR states they worked from the spec.

### Never

- Copying **source code**, in any language, including line-by-line translation
- Copying **comments, struct definitions, field naming schemes, or type hierarchies**
- Copying an **output schema**. A schema is expression, and there is no
  factual-necessity defense for reproducing one. We design ours deliberately and do
  not mirror any existing tool's JSON shape.
- Linking against or vendoring another tool's native module
- **Using another project's hosted offset service.** Even setting copyright aside,
  that is their infrastructure, their curated data, and their bandwidth.
- **Copying from an unlicensed project.** All rights reserved is worse than GPL, not
  better.

### Comment style

Write comments as assertions about the binary:

```rust
// osu!.exe stable 20260711.1: combo is int16 at scoreBase + 0x94.
// Confirmed by attaching and comparing against the on-screen counter.
```

Not as citations of another project:

```rust
// from tosu stable.ts:100          <-- this is an admission, not a citation
```

### A specific risk with AI assistants

A model trained on existing osu! tooling will reproduce that tooling's field names,
struct layout, and JSON schema **by default**, because that is the most probable
completion. This is the most realistic path for a violation to enter this repository.

If you use an assistant: design the schema and naming yourself first, then have it
implement against your design. Say so in the PR. Reviewers should look specifically
for naming that matches another project's field names too closely.

---

## 3. osu!'s own rules

Reading osu! process memory is not addressed by any osu! rule, ToS clause, or wiki
page. Primary sources, read from the [ppy/osu-wiki](https://github.com/ppy/osu-wiki)
repository (the site itself blocks automated fetches):

- [`wiki/Rules/en.md`](https://github.com/ppy/osu-wiki/blob/master/wiki/Rules/en.md)
  — prohibits *"third-party utilities of any kind to get any sort of advantage."*
  The test is **advantage**, not memory access.
- [`wiki/Community/Projects/en.md`](https://github.com/ppy/osu-wiki/blob/master/wiki/Community/Projects/en.md)
  — lists memory-reading tools by name while stating community projects are
  *"not endorsed by osu!, nor do they have any official support."*
- [`wiki/Legal/Terms/en.md`](https://github.com/ppy/osu-wiki/blob/master/wiki/Legal/Terms/en.md)
  — no anti-reverse-engineering, anti-memory-reading, or third-party-client clause.

**There is no whitelist of approved tools and no approval process.** Anyone claiming
a tool is "officially allowed" is guessing.

The read-only guarantees in `README.md` exist to keep the line between *reads and
displays* and *assists play* unambiguous. Do not blur it.
