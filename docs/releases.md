# Releasing

Nothing here is shipped yet. This is what shipping will look like and, more
importantly, what has to be true before each piece is allowed out.

## Who this is for

Right now the project can only be used by someone willing to create a
virtualenv, install a Rust toolchain and run commands. That is fine for
development and useless for the people it is meant to help. A release has to be
a download and a double-click.

## Form: an installer, not a bundle

A single-file Python bundle is the obvious shortcut and it is the wrong one.
Self-extracting single-file executables are among the most common causes of
antivirus false positives, and this program reads another process's memory —
which is the other most common cause. Combining them is the worst available
option, and the false positives would land on users rather than on us.

So a release lays files down on disk and runs them normally:

| Piece | How it ships |
|---|---|
| Python runtime | An embeddable CPython inside the application folder. No system Python, no PATH changes. |
| `osuforge` and the compiled beatmap extension | Pre-built wheels, installed into that runtime at install time. No Rust toolchain for the user. |
| The memory engine | A separate native executable, signed. Separate because it is the piece that breaks on an osu! update, trips antivirus, and crashes — and isolating it means none of those takes the rest down. |
| Entry point | A signed launcher that starts the local server and opens a browser. No terminal. |

Code signing is the only mitigation that reliably works, and it clears
SmartScreen at the same time. SignPath provides certificates to open-source
projects, which is the intended route.

## Order: gated on evidence, not on dates

There are no dates here on purpose. Each stage ships when the thing it depends
on has been demonstrated, and shipping earlier would mean handing someone a
confident number that has not earned it.

**First — the configuration linter.** `forge doctor` produces findings that are
hard facts about the machine: key binds that genuinely conflict, a frame limiter
that is not in effect, accessibility shortcuts that will interrupt play. No
statistics, no memory reading, nothing that needs a corpus. It is useful today
and it is safe to be wrong about in the ordinary way.

Its real value as a first release is that it exercises the whole
signing-installer-report pipeline while none of the dangerous parts exist yet.

**Second — the live page, between plays.** A page that sits open on a second
monitor and shows the play you just finished, a second or two after you finish
it, along with what the session so far looks like.

This does not need the memory engine, and separating it from the part that does
is the point. Offset, key balance and settings diagnosis are all post-hoc
analyses anyway — watching a number move mid-play does not help you tune an
offset, and seeing what the last play actually did does. All it needs is a
watcher on the replay folder and the local server that is already designed.

What it cannot show is a play that left no replay: a quick retry after a
mistake, or a fail. Those are exactly the attempts worth seeing, and they are
what the engine is for.

**Third — offset analysis.** Blocked on `CorpusHealth.may_recommend`, which
requires both the header screen and the differential oracle to pass. Both do
now. What is not ready is the answer: the interval on the local corpus runs from
+1.00 to +6.02 ms, and telling someone to change a setting on that is not much
better than guessing. It narrows with more *sessions*, not more hits, so this
waits on data that only accumulates forward — which is what `forge collect` is
for.

**Fourth — the engine, and the live page during a play.** Blocked on the
self-validation battery: sixteen signatures each resolving exactly once, a
beatmap hash that corresponds to a file on disk, field-to-field consistency
every poll. A memory reader that is quietly wrong is worse than none, and "it
looked right when I tried it" is not the standard.

This is what adds the things the file watcher cannot see — the current combo and
accuracy, a live error bar, performance points as they accrue, and the retries
and fails that never become a replay.

**Fifth — the hardware diagnostic.** Blocked on the disclosure flow, not on the
code. It captures keystrokes while other windows have focus, and a user has to
understand that before it runs rather than after.

## What a release must contain

- SHA-256 checksums for every artifact, published in the release notes.
- A note on antivirus false positives with the vendor submission links, because
  they will happen: the best-known tool in this space still gets them.
- The version of osu!stable the engine was validated against, and what happens
  when it does not match — a degraded flag, not a silent wrong answer.
