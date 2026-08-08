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

### Fixed
- The Python license gate reported success when `pip-licenses` had failed and
  returned nothing. It now fails on a non-zero exit, unparseable output, an
  empty package list, or a missing canary package, and splits compound license
  expressions so `MIT AND GPL-3.0` can no longer pass on the strength of the MIT.

[Unreleased]: https://github.com/osu-forge/osu-forge/commits/main
