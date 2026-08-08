# Clean-room log

Facts about osu!stable's memory, each recorded with how it was established and
when. The procedure is the one from *Sega v. Accolade*: observe the running
binary, write down what is there as a statement about the binary, and implement
from the write-up. What makes it clean is that the observation is ours and the
write-up is the only input to the code.

`docs/licensing.md` sets out why this matters. Briefly: a byte pattern and a
struct offset are facts about a compiled binary rather than authored expression,
but the *selection* of which offsets are useful, the naming, and the arrangement
are choices — so those are made here, from our own observations, rather than
transcribed.

Nothing in this file came from tosu, gosumemory, rosu-memory or any offsets
service. Where a third-party project is consulted at all it will be named in the
entry, along with its licence.

---

## 2026-08-09 — `System.String` layout, and how to type-check one

**Target.** `osu!.exe`, file version 1.3.3.8, 4,541,728 bytes, running as pid
21048 on Windows 11. Attached with `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION`
from a non-elevated session.

**Method.** The configuration file gives the player's own username, so its
contents are known before looking. Searched every committed readable region for
that text encoded UTF-16LE, then read the eight bytes in front of each
occurrence.

**Observed.**

- 75 occurrences of the text.
- 62 of them had, in the preceding eight bytes, a value followed by an int32
  equal to the character count of the username. The other 13 are the text
  appearing inside something that is not a string object — a buffer, a rendered
  path, a log line.
- Among those 62, the first four bytes took **exactly one distinct value**,
  `0x712dabdc`. Not a majority: a single value across all 62.
- That value appears 42,427 times in the first 120 regions of the process, which
  is the order of magnitude expected if it fronts every string.

**Concluded.** From a string reference: method table at `+0x0`, character count
at `+0x4`, UTF-16LE characters at `+0x8`, not null-terminated. The length is in
characters and not bytes.

**Why the method table is discovered rather than recorded.** `0x712dabdc` is an
address inside the loaded runtime and will differ on another machine, another
run, and another .NET version. It is deliberately not a constant anywhere. It is
found at attach time by this same procedure and then used as a type check, which
is the more valuable half: a pointer chain that has gone wrong lands on
arbitrary bytes, and without the check those bytes read as a short piece of
plausible text rather than as a failure.

**What the type check is worth, measured.** 4,000 addresses picked at random
from committed readable regions were read as strings both ways. Without the
method table check, **147 of them — one in 27 — returned text**: a length that
happened to be small and plausible followed by bytes that decode. With the
check, none did. A pointer chain that has drifted is therefore not a loud
failure by default; it is a short piece of believable text, which is precisely
the failure mode that makes a memory reader worse than no memory reader.

**Not yet established.** Whether the same shape holds for `System.Text.StringBuilder`
or for the char array behind one. Arrays are assumed to share it — method table,
count, elements — on the strength of the two being described the same way, and
that assumption is not yet tested here.

---

## Measurements that shape the design

**2026-08-09, same process.** Read cost is per call rather than per byte: four
bytes took 9.2 µs and four kilobytes took 11.5 µs, over 2,000 calls each. So the
correct structure in any language is to read a whole struct in one call and slice
it locally, and a hundred fields spread over ten structs costs about 115 µs per
poll rather than 920 µs.

Walking the region map with `VirtualQueryEx` found 1,803 committed readable
regions totalling 857 MiB in 0.04 s, so re-walking it is cheap enough to do on
demand rather than caching aggressively.

Module enumeration returned 157 modules **only** with `LIST_MODULES_32BIT`.
osu!stable is a 32-bit process; the default filter is the wrong call here and
fails in a way that looks like an empty process rather than an error.
