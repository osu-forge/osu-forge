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

## Constants and rules read from upstream

Difficulty calculation has not been ported yet, but the hit simulator already
takes rules from the same source, and where it does the provenance belongs here
rather than only in a code comment. Read from `ppy/osu` at `master`, 2026-08-09:

| Taken | Upstream |
|---|---|
| `FOLLOW_AREA = 2.4f`, and that it applies only while already tracking | `osu.Game.Rulesets.Osu/Objects/Drawables/DrawableSliderBall.cs`, `SliderInputManager.cs` |
| Tracking requires the key that hit the head to stay down | `SliderInputManager.cs` |
| `TAIL_LENIENCY = -36`, `minDistanceFromEnd = velocity * 10`, reversed-span time progress | `osu.Game/Rulesets/Objects/SliderEventGenerator.cs` |
| `STACK_DISTANCE = 3`, the format-6 version gate, `(int)TimePreempt * StackLeniency` | `osu.Game.Rulesets.Osu/Beatmaps/OsuBeatmapProcessor.cs` |
| Slider scoring is proportional to parts collected; note lock covers the full hit window | `osu.Game.Rulesets.Osu/Mods/OsuModClassic.cs` |

### Where stable and lazer differ

`ppy/osu` is lazer. stable's source has never been published, so lazer is
evidence about stable only where it deliberately reproduces it — the `Legacy*`
constants and `OsuModClassic` exist for exactly that reason. Two places where it
does not, found by comparing against a real replay corpus:

- `HitWindows.ResultFor` compares **inclusively** in lazer. The replay headers
  say stable is strict: across 318 circle hits landing exactly on a window edge,
  `<` leaves the corpus 65 objects over on 300s and `<=` leaves it 358 over.
- lazer's windows are raw doubles; stable's are integers.

Where the two disagree, the corpus decides, because stable is what produced it.

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
