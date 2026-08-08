# Upstream provenance

`osu-forge-diffcalc` is an independent implementation of osu!'s difficulty and
performance-point algorithms, ported from the authoritative MIT-licensed sources.

| Upstream | Commit | Date | Purpose |
|---|---|---|---|
| [ppy/osu](https://github.com/ppy/osu) | _TBD — fill in at first port_ | | difficulty + performance calculators, skills, evaluators |
| [ppy/osu-tools](https://github.com/ppy/osu-tools) | _TBD_ | | reference CLI behaviour, simulate semantics |

Relevant upstream paths:

```
osu.Game.Rulesets.Osu/Difficulty/OsuDifficultyCalculator.cs
osu.Game.Rulesets.Osu/Difficulty/OsuPerformanceCalculator.cs
osu.Game.Rulesets.Osu/Difficulty/Skills/{Aim,Speed,Flashlight,Reading}.cs
osu.Game.Rulesets.Osu/Difficulty/Evaluators/
osu.Game.Rulesets.Osu/Difficulty/Preprocessing/OsuDifficultyHitObject.cs
```

## Keeping this current

pp gets reworked. When it does, an implementation that is not re-ported goes quietly
stale — it keeps producing confident numbers that no longer match the game. Two
mechanisms guard against that:

1. **CI accuracy gate.** Every run compares our difficulty attributes against
   `POST /api/v2/beatmaps/{id}/attributes` to 1e-6 relative error over a fixed map
   set spanning a range of CS/AR/slider densities and mod combinations. A rework
   upstream shows up here as a failure.
2. **Scheduled drift check.** The same comparison runs weekly and opens an issue on
   divergence, so a rework is noticed even when nobody is pushing.

When re-porting: update the commit hashes above, note the change in `CHANGELOG.md`,
and expect the CI gate to be red until the port is complete. Do not loosen the
tolerance to make it pass.

## What we deliberately do not do

We do not port from [rosu-pp](https://github.com/MaxOhn/rosu-pp) or any other
third-party port, even though they are MIT-licensed and would be legally fine. A
port is one more layer between us and ground truth, and it lags — the reference Rust
port's README still cited October-2025 upstream commits in an April-2026 release. If
we are carrying the maintenance cost of an independent implementation, it should
track the real thing.

## Attribution

MIT requires the copyright notice be preserved for substantial derivation. ppy's
notice is reproduced in full in the repository-root `NOTICE` file.
