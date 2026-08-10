# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial repository scaffolding: Apache-2.0 license, dependency-license policy
  with `cargo-deny` enforcement, CI workflows, issue templates.
- `docs/architecture.md` — three-tier extension model, event bus, capability model.
- `docs/licensing.md` — provenance policy for memory-layout data and the clean-room
  procedure for signature discovery.
- `docs/privacy.md` — live input logger disclosure.
- `osuforge.models` — the `Finding` contract shared by every subsystem.
- `osuforge.config` — read-only `osu!.<user>.cfg` parser with a byte-exact
  round trip, credential redaction applied at the parser boundary, config
  discovery, and locale-invariant typed accessors.
- `osuforge.config.keybinds` — ruleset-aware conflict detection. Two bindings
  conflict only when they are live in an overlapping context, so the four
  apparent collisions in a real config resolve to the one that is real.
- `osuforge.probes.base` — the `ProbeResult` contract. Probes return failure as
  a value and never raise into the rule engine.
- `osuforge.rules` — declarative rule engine plus the first five rules, all
  config-only so they run with no probes, no elevation, and osu! not running.
  A rule whose probe is unavailable emits a visible skip rather than vanishing,
  and a rule that raises costs its own finding rather than the whole report.
- `osuforge.probes` — read-only Windows measurements: monitors via
  `EnumDisplaySettingsExW`, pointer settings, accessibility shortcut state,
  timer resolution, power scheme, Game Bar, Fullscreen Optimizations, osu!
  process detection, and the monitor osu!'s window is actually on.
- Nine system rules built on those probes, and `forge doctor` / `forge scan`
  with `--json`, `--only-facts`, `--severity` and `--fail-on`.
- `osu_forge_diffcalc::beatmap` — `.osu` parser with timing points, slider
  durations, and the version-dependent `ApproachRate` fallback. Shared by
  difficulty calculation and the replay hit simulator so the two cannot
  disagree about what a beatmap says.
- `SliderPath` — slider geometry for all four curve types, with arc-length
  sampling and the declared-length trimming osu! applies. Checked against the
  length field of every slider in a real collection.
- `osuforge.replay` — `.osr` parser: header, judgement counts, mods, and the
  LZMA frame stream with delta accumulation and the trailing seed frame
  separated out.
- `osuforge.replay.frames` — marker stripping, key press/release extraction
  with device attribution, and a timing uncertainty on every event. Unknown
  uncertainty is reported as unknown rather than collapsed to zero.
- Stack leniency, so an object's position is where it is drawn and judged rather
  than where the file says. Files older than format v6 stacked by a different
  algorithm; that one is not implemented and those files are reported as
  unstacked instead of being stacked by the wrong rules.
- Difficulty derivations on the beatmap: approach rate to preempt time, circle
  size to object scale and radius, overall difficulty to judgement windows. The
  windows are returned unrounded — which rounding stable applies is a decision
  for whatever does the comparing.
- Mod handling for Hard Rock and Easy. Double Time and Half Time deliberately
  leave the beatmap alone: they change the clock, and applying them here as well
  would count the same speed increase twice.
- `osu_forge_diffcalc` — PyO3 bindings, so the replay hit simulator reads
  beatmaps through the same parser as difficulty calculation instead of through
  a second one written in Python.
- Slider parts — ticks, repeat points and the tail, with the position of the
  ball at each. A slider's grade comes from how many of them the player
  collected, so judgement counts cannot be reproduced without them.
- `osuforge.replay.simulate` — the hit simulator. Presses are matched to objects
  by timing *and* by where the cursor was, and sliders are scored part by part
  against the follow circle.
- `osuforge.replay.validate` — the screen that decides whether to believe a
  simulation, and the circuit breaker that withholds any recommendation from a
  corpus it does not.
- `engine/prototype` — read-only attachment to a running osu!, region-clipped
  reads, pattern scanning, and a `.NET` string reader whose type check is
  bootstrapped from a string the configuration file already names.
- `engine/CLEANROOM.md` — what has been observed about osu!'s memory, how, and
  when. Nothing in it comes from another project.
- `scripts/dev-setup.ps1` — the whole contributor setup in one command.
- `osuforge.collect` and `forge collect` — an append-only record of which
  replays exist and what the timing-relevant settings were, so that sessions and
  a settings history accumulate going forward. An offset estimate is limited by
  how many separate sessions it has, and sessions only accumulate forward.
- `osuforge.live` and `forge live` — a self-contained page rewritten as plays
  finish, so a browser left open on a second monitor shows what the last play
  did a second or two after it ends. No server, no port, nothing listening.
- `osuforge.analysis.patterns` — where a play's accuracy went, split across the
  kinds of object that cost it. A partition rather than a model: the parts add
  to the whole by construction, and groups are cut within one map at a time so
  that approach rate, circle size and settings control for themselves.
- `osuforge.analysis.clustering` — a mean and an interval that account for hits
  being nested in replays and replays in sessions, reporting the effective
  sample size rather than the raw one. On the local corpus that is 893
  independent hits out of 23,723.
- An inclusion policy with its exclusions counted: replays with too few hits, or
  with a miss rate high enough that the surviving errors are a truncated sample,
  do not contribute a mean.
- `osuforge.replay.oracle` — object-by-object comparison against circleguard,
  run as a separate process in an isolated environment. This is what authorises
  an offset recommendation; the header screen on its own never does.
- `osuforge.server.corpus` — the corpus diagnosis, served. `forge serve` feeds
  every analysed play into one `CorpusState`, prints the corpus summary at
  startup, answers `GET /api/corpus` from a cached result so no request waits
  on statistics, and pushes a fresh answer to open pages when a new play
  changes it. Plays whose simulation failed the header screen stay out of the
  estimate, are named with the screen's reason, and still count against the
  corpus's health — which reports `may_recommend: false` on every answer,
  because nothing in a serve run has been judged by the oracle.
- A corpus panel on the served page: the three axes with their actionable
  flags, the refusal sentence where the verdict would be when the corpus is
  too thin, the per-beatmap comparison with a one-sentence reading of pooled
  against per-map intervals (`osuforge.analysis.corpus.beatmap_reading`), and
  the excluded plays with their reasons.

### Changed
- Integration tests are deselected by default and require an environment
  variable pointing at real data. A plain `pytest` run can no longer read
  anyone's osu! install.
- `panic = "abort"` moved off the shared release profile onto a dedicated
  `engine-release` profile. PyO3 turns a Rust panic into a Python exception by
  catching the unwind, so under `abort` an unexpected panic would take the whole
  interpreter down with no traceback. The engine still gets the smaller binary.

### Fixed
- `osuforge` shipped without a `py.typed` marker, so every consumer saw it as
  untyped however strictly it checks itself.
- The Python license gate reported success when `pip-licenses` had failed and
  returned nothing. It now fails on a non-zero exit, unparseable output, an
  empty package list, or a missing canary package, and splits compound license
  expressions so `MIT AND GPL-3.0` can no longer pass on the strength of the MIT.

[Unreleased]: https://github.com/osu-forge/osu-forge/commits/main
