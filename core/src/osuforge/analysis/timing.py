"""Late, early, or just inconsistent — three questions, one fixable.

"Was I late?" is three different measurements, and only one of them is a
sensitivity or an offset away from being fixed:

- **Bias** is the mean hit error. An offset moves it, one millisecond per
  millisecond, which is what makes it the only thing an offset recommendation
  can honestly promise.
- **Precision** is the spread. An offset moves the whole distribution and
  leaves its width exactly as it was. A player whose problem is spread must be
  told that no offset value helps — handing them a number instead is handing
  them something that cannot work, and they will conclude the tool is wrong
  rather than that the advice was.
- **Where the lateness came from** splits the bias itself. The cursor has to
  arrive before the key can usefully be pressed, so

      hit error = (arrival - object time) + (press - arrival)

  and the second term is **invariant to any global offset**. Shifting every
  object in the map by 5 ms shifts the first term by 5 ms and leaves the second
  untouched. It is the part of being late that is about the hand rather than
  about the clock, and it is the part that survives every offset change.

# Unstable rate, and why it is reported as it is

The community measures precision as the unstable rate: ten times the standard
deviation of hit errors, in milliseconds. The factor of ten is a convention
with no meaning beyond making the numbers conveniently sized, and it is kept
here because a player comparing against their own score screen needs the same
units rather than a better-scaled one.

It is reported in **real** time rather than map time, because that is what the
score screen shows. Under Double Time a 10 ms map-time spread is 6.7 ms of real
time, and quoting the map-time figure next to a game that displays the other
would look like a discrepancy in the tool.

# The intervals are clustered, always

Hits within one replay share a hand position, a warm-up state and a single
mental calibration. Both the bias and the spread are therefore reported through
the session-clustered machinery in :mod:`osuforge.analysis.clustering`, never
the naive standard error, which on this corpus is five times too narrow.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from osuforge.analysis.clustering import ClusteredMean, ReplaySample, estimate

__all__ = [
    "UNSTABLE_RATE_SCALE",
    "Decomposition",
    "TimingProfile",
    "decomposition_from",
    "profile",
]

UNSTABLE_RATE_SCALE = 10.0
"""What the score screen multiplies the standard deviation by.

A convention, not a unit conversion. Kept so a player can compare against the
number their own game shows them.
"""


@dataclass(frozen=True, slots=True)
class Decomposition:
    """Being late, split into the part an offset can move and the part it cannot.

    Built per object from the cursor's arrival, so it needs the landings rather
    than the hit errors alone.
    """

    approach: float
    """`arrival - object time`, in map milliseconds, averaged.

    Negative means the cursor was on target before the object was due. A global
    offset moves this term one for one.
    """

    reaction: float
    """`press - arrival`, in map milliseconds, averaged.

    How long the hand waited once it was already on target. **No offset changes
    this**, so it is the floor under any timing recommendation.
    """

    n: int

    @property
    def total(self) -> float:
        """The two terms, which sum to the mean hit error by construction."""
        return self.approach + self.reaction

    def summary(self) -> str:
        return (
            f"of {self.total:+.2f} ms mean error, {self.approach:+.2f} ms is when the "
            f"cursor arrived relative to the object and {self.reaction:+.2f} ms is the "
            "wait after arriving — the second is unchanged by any offset"
        )


@dataclass(frozen=True, slots=True)
class TimingProfile:
    """Bias and precision, kept apart."""

    bias: ClusteredMean
    """Mean hit error, with a session-clustered interval. What an offset moves."""

    spread_ms: float
    """Standard deviation of hit errors in **real** milliseconds."""

    n_hits: int

    decomposition: Decomposition | None = None

    @property
    def unstable_rate(self) -> float:
        return self.spread_ms * UNSTABLE_RATE_SCALE

    @property
    def offset_would_help(self) -> bool:
        """Whether moving the offset would change anything worth having.

        Requires the bias interval to exclude zero. A mean that cannot be
        distinguished from zero is not evidence for a shift in either
        direction, however large the spread beside it is — and a large spread
        makes it *more* tempting to recommend something, not less.
        """
        return self.bias.excludes_zero

    def summary(self) -> str:
        parts = [f"bias {self.bias.summary()}", f"unstable rate {self.unstable_rate:.1f}"]
        if not self.offset_would_help:
            parts.append(
                "the mean is not distinguishable from zero, so no offset value improves "
                "this — the spread is what is costing accuracy, and an offset does not "
                "narrow a distribution"
            )
        if self.decomposition is not None:
            parts.append(self.decomposition.summary())
        return "; ".join(parts)


def decomposition_from(pairs: list[tuple[float, float]]) -> Decomposition | None:
    """Split mean hit error into approach and reaction.

    `pairs` are `(hit_error, dwell)` in map milliseconds, where dwell is how
    long the cursor was already inside the hit radius before the press. Objects
    whose dwell could not be measured — the cursor was already on target when
    the approach window opened — are excluded, because for those the arrival
    happened outside the window and the split is undefined rather than zero.
    """
    usable = [(error, dwell) for error, dwell in pairs if not math.isnan(dwell)]
    if not usable:
        return None
    errors = np.array([error for error, _ in usable])
    dwells = np.array([dwell for _, dwell in usable])
    # press - object = (arrival - object) + (press - arrival), and dwell is the
    # second term directly, so the first falls out by subtraction.
    return Decomposition(
        approach=float((errors - dwells).mean()),
        reaction=float(dwells.mean()),
        n=len(usable),
    )


def profile(
    samples: list[ReplaySample],
    *,
    rate: float = 1.0,
    decomposition: Decomposition | None = None,
    seed: int = 0,
    resamples: int | None = None,
) -> TimingProfile:
    """Estimate bias and precision from the same replays, and keep them apart.

    `rate` converts map milliseconds to real ones for the spread, so the
    unstable rate matches what the game displays.
    """
    bias = (
        estimate(samples, seed=seed)
        if resamples is None
        else estimate(samples, seed=seed, resamples=resamples)
    )

    errors = [error for sample in samples for error in sample.errors]
    spread = float(np.std(np.array(errors), ddof=1) / rate) if len(errors) > 1 else math.nan

    return TimingProfile(
        bias=bias,
        spread_ms=spread,
        n_hits=len(errors),
        decomposition=decomposition,
    )
