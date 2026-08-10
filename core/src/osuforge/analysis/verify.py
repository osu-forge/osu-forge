"""Did the change actually help.

The stage that makes this a loop rather than another statistics screen. A tool
that recommends a change and then reports success whichever way the number
moved is worse than one that recommends nothing, because it launders a wrong
recommendation into evidence for itself.

`forge collect` already records a configuration fingerprint against every
replay, so the boundaries exist. This compares across them.

# The prediction is what makes it falsifiable

An offset recommendation is not "this number should be different". It is a
prediction: *move the offset by +5 ms and the mean hit error will move by
-5 ms*. That prediction can fail, and it is the only part of any of this that
can. A measurement that cannot come out wrong is not evidence of anything.

So the verdict is about **sign and size together**:

- the mean moved the predicted way, by roughly the predicted amount — CONFIRMED
- the right way but the wrong size — PARTIAL, which usually means something
  else moved too
- the wrong way — CONTRADICTED, and the recommendation should be undone

CONTRADICTED is the reason to build this. Without it, every recommendation is
self-confirming.

# Why both sides need sessions, not just replays

A boundary with ten replays from one sitting on each side compares one evening
with another evening. Whatever differs between them — warm-up, time of day,
which maps were in the mood — is confounded with the change. Requiring sessions
on both sides does not remove that, but it stops the comparison being made
entirely out of it.

# Two routes to the interval, and the wider one decides

Same posture as :func:`osuforge.analysis.clustering.estimate` and
:mod:`osuforge.analysis.progress`, and here it is not only for the usual
reason. The interval on `after - before` comes from a Welch-corrected cluster
route with sessions as the unit and from a hierarchical bootstrap over
sessions, replays and hits; the wider of the two is what gets reported and
what :attr:`Comparison.measurable` reads.

Progress describes the same difference across the same boundary, on the same
screen, and has always reported the wider of these two routes. A verdict built
on the narrower one would let this tool print "no detectable change" and
"confirmed" about one corpus three lines apart, which is the failure this
module exists to prevent rather than a rounding difference. A verdict that
survives the wider interval is worth acting on; one that only survives the
narrower is not worth printing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

from osuforge.analysis.clustering import ReplaySample, select, welch_difference_ci

__all__ = [
    "AGREEMENT_TOLERANCE",
    "BOOTSTRAP_RESAMPLES",
    "MIN_REPLAYS_PER_SIDE",
    "MIN_SESSIONS_PER_SIDE",
    "Boundary",
    "Comparison",
    "Verdict",
    "compare",
]

MIN_REPLAYS_PER_SIDE = 5
MIN_SESSIONS_PER_SIDE = 2
"""Both sides need more than one sitting.

Ten replays from one evening on each side compares two evenings, and everything
that differs between two evenings is confounded with the change being tested.
"""

AGREEMENT_TOLERANCE = 0.40
"""How far the observed move may sit from the predicted one and still count.

Forty percent is loose, deliberately. The prediction is that a 5 ms offset
change moves the mean by 5 ms, and a player partially re-adapting within the
same session is expected — the question this answers is whether the change did
what it was supposed to at all, not whether it did so to two decimal places.
"""

BOOTSTRAP_RESAMPLES = 4_000

_CONFIDENCE = 0.95


class Verdict(StrEnum):
    CONFIRMED = "confirmed"
    """Moved the predicted way, by roughly the predicted amount."""

    PARTIAL = "partial"
    """Right direction, wrong size. Usually something else moved too."""

    CONTRADICTED = "contradicted"
    """Wrong direction. The change should be undone."""

    UNCHANGED = "unchanged"
    """The interval on the difference includes zero: nothing measurable
    happened, which is not the same as the change having failed."""

    INSUFFICIENT = "insufficient"
    """Not enough on one side to compare. No verdict is issued."""


@dataclass(frozen=True, slots=True)
class Boundary:
    """One configuration change, with what was played on each side."""

    before_epoch: str
    after_epoch: str

    before: list[ReplaySample]
    after: list[ReplaySample]

    changed: dict[str, tuple[str, str]] = field(default_factory=dict)
    """Setting name to `(old, new)`. From the fingerprint diff."""

    @property
    def offset_change(self) -> float | None:
        """How far the audio offset moved, if it did.

        `None` when the boundary changed something else — which is worth
        distinguishing, because a boundary with no offset change makes no
        prediction about the mean and must not be scored as though it had.
        """
        for name in ("Offset", "AudioOffset", "UniversalOffset"):
            if name not in self.changed:
                continue
            old, new = self.changed[name]
            try:
                return float(new) - float(old)
            except ValueError:
                return None
        return None


@dataclass(frozen=True, slots=True)
class Comparison:
    """What moved across one boundary, and whether it moved as predicted."""

    boundary: Boundary
    verdict: Verdict

    before_mean: float = math.nan
    after_mean: float = math.nan
    difference: float = math.nan
    ci_low: float = math.nan
    ci_high: float = math.nan

    predicted: float | None = None
    reason: str = ""

    @property
    def measurable(self) -> bool:
        """Whether the interval on the difference excludes zero."""
        return math.isfinite(self.ci_low) and (self.ci_low > 0.0 or self.ci_high < 0.0)

    def summary(self) -> str:
        if self.verdict is Verdict.INSUFFICIENT:
            return self.reason
        moved = (
            f"mean hit error moved {self.difference:+.2f} ms "
            f"(95% CI {self.ci_low:+.2f} to {self.ci_high:+.2f}), "
            f"{self.before_mean:+.2f} to {self.after_mean:+.2f}"
        )
        if self.predicted is None:
            return f"{moved}. No offset change here, so nothing was predicted."
        return f"{moved}, against {self.predicted:+.2f} ms predicted. {self.reason}"


def _bootstrap_difference_ci(
    before: list[ReplaySample],
    after: list[ReplaySample],
    *,
    seed: int,
    resamples: int,
) -> tuple[float, float]:
    """The bootstrap route: resample sessions, then replays, then hits."""

    def group(samples: list[ReplaySample]) -> list[list[np.ndarray]]:
        buckets: dict[int, list[np.ndarray]] = {}
        for sample in samples:
            if sample.n:
                buckets.setdefault(sample.session, []).append(
                    np.asarray(sample.errors, dtype=float)
                )
        return list(buckets.values())

    left, right = group(before), group(after)
    if len(left) < 2 or len(right) < 2:
        return (math.nan, math.nan)

    rng = np.random.default_rng(seed)

    def draw(sessions: list[list[np.ndarray]]) -> float:
        pieces = []
        for index in rng.integers(0, len(sessions), len(sessions)):
            replays = sessions[index]
            for pick in rng.integers(0, len(replays), len(replays)):
                errors = replays[pick]
                pieces.append(rng.choice(errors, size=errors.size, replace=True))
        return float(np.concatenate(pieces).mean())

    differences = np.array([draw(right) - draw(left) for _ in range(resamples)])
    tail = (1.0 - _CONFIDENCE) / 2.0
    low, high = np.quantile(differences, [tail, 1.0 - tail])
    return (float(low), float(high))


def _difference_ci(
    before: list[ReplaySample],
    after: list[ReplaySample],
    *,
    seed: int,
    resamples: int,
) -> tuple[float, float]:
    """The wider of the two routes to an interval on `after - before`.

    On the difference rather than on each side separately: two intervals that
    overlap can still have a difference that excludes zero, and reading
    overlap as "no change" is a standard way to miss a real one.

    Wider of the two rather than either alone, for the reason in the module
    docstring. `(nan, nan)` only when neither route holds, which leaves the
    comparison unmeasurable and is the honest outcome of not being able to put
    an interval on the difference at all.
    """
    routes = (
        welch_difference_ci(before, after, confidence=_CONFIDENCE),
        _bootstrap_difference_ci(before, after, seed=seed, resamples=resamples),
    )
    usable = [
        interval for interval in routes if math.isfinite(interval[0]) and math.isfinite(interval[1])
    ]
    if not usable:
        return (math.nan, math.nan)
    return max(usable, key=lambda interval: interval[1] - interval[0])


def compare(
    boundary: Boundary,
    *,
    seed: int = 0,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> Comparison:
    """Score one boundary against what it predicted."""
    before = select(boundary.before).kept
    after = select(boundary.after).kept

    for side, samples in (("before", before), ("after", after)):
        sessions = {sample.session for sample in samples}
        if len(samples) < MIN_REPLAYS_PER_SIDE or len(sessions) < MIN_SESSIONS_PER_SIDE:
            return Comparison(
                boundary=boundary,
                verdict=Verdict.INSUFFICIENT,
                reason=(
                    f"Not enough {side} the change: {len(samples)} replay(s) in "
                    f"{len(sessions)} session(s), and comparing needs "
                    f"{MIN_REPLAYS_PER_SIDE} in {MIN_SESSIONS_PER_SIDE}. "
                    "One sitting against another confounds the change with the evening."
                ),
            )

    before_mean = float(np.mean([error for s in before for error in s.errors]))
    after_mean = float(np.mean([error for s in after for error in s.errors]))
    difference = after_mean - before_mean
    low, high = _difference_ci(before, after, seed=seed, resamples=resamples)

    predicted = boundary.offset_change
    # Raising the offset delays when the game judges objects, so a hit that was
    # +3 ms late becomes closer to on time: the mean moves opposite to the
    # offset change. The sign is the load-bearing part of this whole module and
    # is pinned by test.
    predicted_mean_change = None if predicted is None else -predicted

    measurable = math.isfinite(low) and (low > 0.0 or high < 0.0)

    if predicted_mean_change is None:
        return Comparison(
            boundary=boundary,
            verdict=Verdict.UNCHANGED if not measurable else Verdict.PARTIAL,
            before_mean=before_mean,
            after_mean=after_mean,
            difference=difference,
            ci_low=low,
            ci_high=high,
            reason=(
                "This boundary changed something other than the offset, so it made no "
                "prediction about the mean and is not scored against one."
            ),
        )

    if not measurable:
        return Comparison(
            boundary=boundary,
            verdict=Verdict.UNCHANGED,
            before_mean=before_mean,
            after_mean=after_mean,
            difference=difference,
            ci_low=low,
            ci_high=high,
            predicted=predicted_mean_change,
            reason=(
                "The interval on the difference includes zero, so nothing measurable "
                "happened. That is not the same as the change having failed — it may "
                "be too small to see against this much spread."
            ),
        )

    if difference * predicted_mean_change < 0:
        return Comparison(
            boundary=boundary,
            verdict=Verdict.CONTRADICTED,
            before_mean=before_mean,
            after_mean=after_mean,
            difference=difference,
            ci_low=low,
            ci_high=high,
            predicted=predicted_mean_change,
            reason=(
                "It moved the opposite way to the prediction. The change did not do what "
                "it was supposed to, and the honest response is to put it back rather "
                "than to keep adjusting from here."
            ),
        )

    gap = abs(difference - predicted_mean_change)
    within = gap <= abs(predicted_mean_change) * AGREEMENT_TOLERANCE
    return Comparison(
        boundary=boundary,
        verdict=Verdict.CONFIRMED if within else Verdict.PARTIAL,
        before_mean=before_mean,
        after_mean=after_mean,
        difference=difference,
        ci_low=low,
        ci_high=high,
        predicted=predicted_mean_change,
        reason=(
            "It moved the predicted way, by roughly the predicted amount."
            if within
            else (
                "It moved the right way but not by the predicted amount, which usually "
                "means something else moved at the same time."
            )
        ),
    )
