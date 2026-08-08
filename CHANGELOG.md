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

### Fixed
- The Python license gate reported success when `pip-licenses` had failed and
  returned nothing. It now fails on a non-zero exit, unparseable output, an
  empty package list, or a missing canary package, and splits compound license
  expressions so `MIT AND GPL-3.0` can no longer pass on the strength of the MIT.

[Unreleased]: https://github.com/osu-forge/osu-forge/commits/main
