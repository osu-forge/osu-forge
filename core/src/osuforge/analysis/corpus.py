"""What repeatedly costs this player, across every play rather than within one.

A single play cannot answer "why do I fail". Its unstable rate is one draw from
a noisy distribution and one combo break is chance. The numbers say so directly:
on the local corpus, 23,723 hits are worth 893 independent ones after a design
effect of 26.6, so reading a conclusion off one play is close to reading it off
a sample of one.

So the unit here is the corpus. Everything a per-play view can honestly say is
"this happened"; only the corpus can say "this keeps happening", and that is the
sentence a player is actually asking for.

# Three axes, because they have three different fixes

Accuracy leaves by one of three doors, and confusing them is how a player ends
up changing a setting that could never have helped:

- **Timing bias** — the mean hit error. An offset moves it, one millisecond per
  millisecond. This is the only axis a number in a config file fixes.
- **Timing spread** — the width. An offset moves the whole distribution and
  leaves the width exactly as it was. A player whose problem is spread has to
  be told plainly that no offset value helps.
- **Aim and tracking** — where the cursor was, and whether it stayed. Neither is
  a setting; pointer gain is the only settings-shaped part and on this corpus it
  is already indistinguishable from correct.

# Every figure carries how much of it is real

A corpus number computed as though hits were independent is about five times
too confident, and that is invisible in the number itself. So each estimate
travels with its effective sample size and a session-clustered interval, and
:class:`Diagnosis` refuses to call anything a finding without them.

# This map, or every map

The most useful thing a corpus knows that a play does not: whether a weakness
belongs to one beatmap or follows the player everywhere. A pattern that only
costs on one map is a map to practise; one that costs everywhere is a habit.
Answering it needs the same measurement taken within and across maps, which is
why :func:`by_beatmap` exists rather than a single pooled figure.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from osuforge.analysis.clustering import ClusteredMean, ReplaySample, estimate, select
from osuforge.analysis.timing import Decomposition, TimingProfile, profile

__all__ = [
    "MIN_REPLAYS",
    "MIN_SESSIONS",
    "Axis",
    "Diagnosis",
    "Entry",
    "Verdict",
    "by_beatmap",
    "diagnose",
]

MIN_SESSIONS = 3
"""Sessions needed before the corpus says anything at all.

Two sessions can differ for reasons that have nothing to do with the player —
a different time of day, a different warm-up. Three is not many, and it is
where "this keeps happening" stops being a description of one evening.
"""

MIN_REPLAYS = 10
"""Replays needed alongside the sessions. Both, not either: ten replays in one
sitting is one sitting, and three sittings of one replay is three plays."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One replay, reduced to what a corpus-level question needs."""

    replay: str
    beatmap_hash: str
    beatmap: str
    """Artist - title [version], for showing rather than for grouping."""

    played_at: datetime
    session: int

    errors: list[float]
    """Circle hit errors, real milliseconds, early negative."""

    miss_rate: float
    accuracy: float
    breaks: int

    @property
    def sample(self) -> ReplaySample:
        return ReplaySample(
            replay_id=self.replay,
            session=self.session,
            errors=self.errors,
            miss_rate=self.miss_rate,
        )


class Verdict(str):
    """A sentence a player can act on, including 'nothing here'."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Axis:
    """One of the three doors accuracy leaves by."""

    name: str
    verdict: str
    """What to do, or that there is nothing to do. Both are outcomes."""

    actionable: bool
    """Whether a setting change follows from this.

    False is a real answer and the more common one. A tool that can only say
    "change this" will say it when it should not.
    """

    detail: str = ""
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """What the corpus supports, and how much of it is real."""

    replays: int
    sessions: int
    hits: int

    bias: ClusteredMean | None = None
    timing: TimingProfile | None = None
    axes: list[Axis] = field(default_factory=list)

    dropped: dict[str, str] = field(default_factory=dict)
    insufficient: str | None = None
    """Why the corpus cannot answer yet, if it cannot. Present rather than an
    empty result, because "not enough data" and "nothing wrong" are opposite
    conclusions that look identical as a blank page."""

    @property
    def usable(self) -> bool:
        return self.insufficient is None

    @property
    def effective_hits(self) -> float:
        return self.bias.effective_n if self.bias else math.nan

    def summary(self) -> str:
        if self.insufficient:
            return self.insufficient
        assert self.bias is not None
        return (
            f"{self.hits} hits across {self.replays} replays in {self.sessions} sessions, "
            f"worth {self.bias.effective_n:.0f} independent hits "
            f"(design effect {self.bias.design_effect:.1f})"
        )


def _timing_axes(timing: TimingProfile) -> list[Axis]:
    """Split timing into the part an offset fixes and the part it does not."""
    axes: list[Axis] = []
    bias = timing.bias

    if timing.offset_would_help:
        direction = "late" if bias.mean > 0 else "early"
        axes.append(
            Axis(
                name="timing bias",
                verdict=f"you hit {abs(bias.mean):.1f} ms {direction} on average",
                actionable=True,
                detail=(
                    "An offset moves this one millisecond per millisecond, and it is the "
                    "only part of your timing a number in a config file changes."
                ),
                evidence=[bias.summary()],
            )
        )
    else:
        axes.append(
            Axis(
                name="timing bias",
                verdict="your average timing is not distinguishable from correct",
                actionable=False,
                detail=(
                    "No offset value improves this. The interval on the mean includes "
                    "zero, so a change in either direction is as likely to hurt as help."
                ),
                evidence=[bias.summary()],
            )
        )

    # The spread is reported whatever the bias says, because it is usually the
    # larger number and it is the one nothing in the settings touches.
    axes.append(
        Axis(
            name="timing spread",
            verdict=f"unstable rate {timing.unstable_rate:.0f}",
            actionable=False,
            detail=(
                "An offset shifts the whole distribution and leaves its width alone, so "
                "this is not something a setting fixes. Against a mean of "
                f"{bias.mean:+.1f} ms, the spread is {timing.spread_ms:.1f} ms — "
                + (
                    "the spread is what is costing the accuracy."
                    if abs(bias.mean) < timing.spread_ms / 3
                    else "both are worth attention."
                )
            ),
        )
    )

    if timing.decomposition is not None:
        split = timing.decomposition
        axes.append(
            Axis(
                name="arrival",
                verdict=(
                    f"the cursor is on target {abs(split.approach):.0f} ms "
                    + ("before" if split.approach < 0 else "after")
                    + " the object is due"
                ),
                actionable=False,
                detail=(
                    f"Of the {split.total:+.1f} ms mean error, {split.reaction:+.1f} ms is "
                    "the wait after the cursor had already arrived — and no offset changes "
                    "that part. "
                    + (
                        "Aim arrival is not what makes these hits late."
                        if split.approach < 0
                        else "Getting there later than the object is due is part of it."
                    )
                ),
            )
        )

    return axes


def diagnose(
    entries: list[Entry],
    *,
    rate: float = 1.0,
    seed: int = 0,
    decomposition: Decomposition | None = None,
) -> Diagnosis:
    """What the corpus supports about this player.

    Refuses rather than guesses below :data:`MIN_SESSIONS` and
    :data:`MIN_REPLAYS`, and says which of the two is missing — "play more" and
    "play on more days" are different instructions.
    """
    sessions = {entry.session for entry in entries}
    if len(entries) < MIN_REPLAYS or len(sessions) < MIN_SESSIONS:
        missing = []
        if len(entries) < MIN_REPLAYS:
            missing.append(f"{MIN_REPLAYS - len(entries)} more replay(s)")
        if len(sessions) < MIN_SESSIONS:
            missing.append(f"{MIN_SESSIONS - len(sessions)} more session(s)")
        return Diagnosis(
            replays=len(entries),
            sessions=len(sessions),
            hits=sum(len(entry.errors) for entry in entries),
            insufficient=(
                f"Not enough yet to say what keeps happening: {' and '.join(missing)}. "
                "A single sitting cannot tell a habit from an evening."
            ),
        )

    inclusion = select([entry.sample for entry in entries])
    if not inclusion.kept:
        return Diagnosis(
            replays=len(entries),
            sessions=len(sessions),
            hits=0,
            dropped=inclusion.dropped,
            insufficient="Every replay was excluded; " + inclusion.summary(),
        )

    timing = profile(inclusion.kept, rate=rate, seed=seed, decomposition=decomposition)
    kept_sessions = {sample.session for sample in inclusion.kept}

    return Diagnosis(
        replays=len(inclusion.kept),
        sessions=len(kept_sessions),
        hits=timing.n_hits,
        bias=timing.bias,
        timing=timing,
        axes=_timing_axes(timing),
        dropped=inclusion.dropped,
    )


def by_beatmap(entries: list[Entry], *, minimum: int = 3) -> dict[str, ClusteredMean]:
    """The same estimate taken per beatmap.

    The question this exists for: is a weakness this map, or every map. A
    pattern that costs on one beatmap is a map to practise; one that costs
    everywhere is a habit, and only comparing the two answers it.

    Maps with fewer than `minimum` replays are left out rather than reported
    with an interval so wide it says nothing — but the count is the caller's to
    show, because a map excluded for thinness is still a map that was played.
    """
    grouped: dict[str, list[ReplaySample]] = defaultdict(list)
    names: dict[str, str] = {}
    for entry in entries:
        grouped[entry.beatmap_hash].append(entry.sample)
        names[entry.beatmap_hash] = entry.beatmap

    results: dict[str, ClusteredMean] = {}
    for beatmap_hash, samples in grouped.items():
        kept = select(samples).kept
        if len(kept) < minimum:
            continue
        results[names[beatmap_hash]] = estimate(kept, resamples=2000)
    return results
