"""Whether the pointer moves too far, and whether it is worth changing.

A gain error is multiplicative: the cursor covers some fraction more or less
ground than the hand asked for. It therefore does not show up as a constant
offset in where the cursor lands — it shows up as an error that **grows with
the distance travelled**. A player who overshoots long jumps and lands well on
short ones has a gain problem. A player who lands 8 pixels out regardless of
jump length has a different problem, and no sensitivity change touches it.

That difference is the whole estimator. Regress the landing error on the
displacement that produced it: the slope is the fractional gain error, and the
intercept is a constant aim bias which is **not** gain and takes no part in the
recommendation.

# Per axis, because gain need not be one number

A mouse configured with different DPI on each axis has a different gain
horizontally and vertically, and pooling them measures the average of two
things. The signed x error is regressed on the signed x displacement and the y
on the y, so each axis gets its own slope. Signed on purpose: with 2% excess
gain a rightward jump lands past the target and a leftward one lands past it in
the other direction, and only the signed form makes those the same observation.

# The two forms have to agree, and on the local corpus they do not

The along-track slope is fitted as well, and it is not a second opinion — it is
a **consistency check with a known answer**. Under the model, along-track error
is just the per-axis errors projected onto the direction of travel, so the
along-track slope is determined by the two axis slopes and the distribution of
headings. Predicting it and comparing is free, and it is the only oracle this
estimator has: there is no corpus anywhere with a labelled sensitivity error.

On the local corpus the two disagree, and the disagreement is larger than any
effect being estimated:

    horizontal  +0.06%      vertical  -0.33%
    predicted along-track   -0.26%   (from those two and the headings)
    observed along-track    +0.57%

Aim bias was the obvious suspect — a constant landing bias projects onto the
direction of travel and could leak into the slope if long jumps have a
preferred direction. It was measured and it pulls the other way, contributing
-0.14%. So something in the along-track direction grows with jump distance and
is not a per-axis linear gain, and until it is identified neither number can be
trusted enough to change a setting over.

:func:`estimate` therefore reports the disagreement and refuses to be usable
while it stands. Shipping the number that happens to be significant, when a
second view of the same data contradicts it, is how this project has previously
produced two confidently wrong findings.

# What this cannot do, and says so

**DPI is not identifiable.** A replay records the cursor position in osu!
pixels and never the counts behind it. Hardware DPI, osu!'s `MouseSpeed`, and
the Windows pointer scaling multiply into one gain and the replay observes only
the product. The estimate is therefore of the *product*, and which knob to turn
is a separate question answered by which one can express the change.

**The player has already adapted.** Whatever mismatch existed when the
sensitivity was chosen has been trained around, so what is left is a residual.
It is small by construction, and its being small is the useful part of the
answer rather than a disappointment.

**The sample is truncated at the hit radius.** Only objects that were hit have
a landing, and a press outside the radius does not register — so the largest
overshoots became misses and left the sample entirely. This is the same
truncation that makes a high-miss-rate replay useless for timing, for the same
reason, and it biases the slope **toward zero**. A residual reported here is a
floor on the real one, not an estimate of it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from osuforge.analysis.cursor import Landing

__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "CONSISTENCY_TOLERANCE",
    "MEANINGFUL_GAIN_CHANGE",
    "MIN_LANDINGS_PER_REPLAY",
    "MIN_TRAVEL_SPREAD",
    "AxisGain",
    "Consistency",
    "GainEstimate",
    "GainSample",
    "estimate",
]

MIN_LANDINGS_PER_REPLAY = 100
"""Below this a replay's slope is fitted to noise. Mirrors the timing estimator's
threshold, for the same reason."""

MIN_TRAVEL_SPREAD = 40.0
"""Required spread of jump distances, in osu! pixels.

A slope is only identifiable if the predictor varies. A replay whose jumps are
all about the same length gives a line through one cloud, and its slope is
whatever the noise says. Checked rather than assumed because it is the failure
mode that produces a confident number from nothing.
"""

MEANINGFUL_GAIN_CHANGE = 0.01
"""Smallest gain change worth telling anyone about, as a fraction.

One percent. Below this the change is inside the noise of any sensitivity a
person can set by feel, and recommending it would be advice that cannot be
followed or verified.
"""

CONSISTENCY_TOLERANCE = 0.005
"""How far the observed along-track slope may sit from the predicted one.

Half of :data:`MEANINGFUL_GAIN_CHANGE`. The reasoning is that a disagreement
between two views of the same data has to be small relative to the smallest
effect anyone would act on — a check that tolerates more than the thing being
measured is not a check.
"""

BOOTSTRAP_RESAMPLES = 5_000

_CONFIDENCE = 0.95


@dataclass(frozen=True, slots=True)
class GainSample:
    """One replay's landings, and where it sits in the session hierarchy."""

    replay_id: str
    session: int
    landings: list[Landing]

    miss_rate: float = 0.0
    """Share of the replay's objects that scored nothing.

    Carried for the same reason the timing estimator carries it, and it bites
    harder here: a press outside the hit radius does not register, so the
    biggest landing errors are exactly the ones that became misses. A replay
    full of misses is a sample with its tails cut off.
    """

    @property
    def n(self) -> int:
        return len(self.landings)


@dataclass(frozen=True, slots=True)
class AxisGain:
    """One axis's fitted relationship between displacement and error."""

    name: str

    slope: float
    """Fractional gain error. `+0.02` means the cursor travels 2% too far."""

    ci_low: float
    ci_high: float

    intercept: float
    """Constant landing bias in osu! pixels. **Not gain**, and not part of any
    recommendation — an offset in where the hand aims does not scale with
    distance and is not fixed by a sensitivity."""

    n: int
    n_replays: int
    n_sessions: int

    @property
    def excludes_zero(self) -> bool:
        return self.ci_low > 0.0 or self.ci_high < 0.0

    @property
    def correction(self) -> float:
        """Fractional change in gain that would cancel this residual.

        Not simply the negated slope: cancelling a 2% excess needs a factor of
        1/1.02, which is -1.96%, and the difference grows with the residual.
        """
        return (1.0 / (1.0 + self.slope)) - 1.0 if self.slope > -1.0 else math.nan

    def summary(self) -> str:
        if not self.excludes_zero:
            return (
                f"{self.name}: {self.slope:+.2%} (95% CI {self.ci_low:+.2%} to "
                f"{self.ci_high:+.2%}) — consistent with no gain error"
            )
        direction = "too far" if self.slope > 0 else "not far enough"
        return (
            f"{self.name}: the cursor travels {abs(self.slope):.2%} {direction} "
            f"(95% CI {self.ci_low:+.2%} to {self.ci_high:+.2%})"
        )


@dataclass(frozen=True, slots=True)
class Consistency:
    """Whether the two views of the same data agree.

    The along-track slope is not independent evidence — it is determined by the
    two axis slopes and the distribution of headings. Predicting it and
    comparing is the only oracle available here, because no corpus exists with
    a labelled sensitivity error to check against.
    """

    predicted: float
    observed: float

    @property
    def gap(self) -> float:
        return self.observed - self.predicted

    @property
    def agrees(self) -> bool:
        return abs(self.gap) <= CONSISTENCY_TOLERANCE

    def summary(self) -> str:
        if self.agrees:
            return (
                f"along-track agrees with the per-axis fits "
                f"({self.observed:+.2%} against {self.predicted:+.2%} predicted)"
            )
        return (
            f"the two views of this data disagree: the per-axis fits predict an "
            f"along-track slope of {self.predicted:+.2%} and the observed one is "
            f"{self.observed:+.2%}, a gap of {self.gap:+.2%}. Something in the "
            "direction of travel grows with jump distance and is not a per-axis "
            "linear gain. Until it is identified, neither number is worth changing "
            "a setting over."
        )


@dataclass(frozen=True, slots=True)
class GainEstimate:
    """What the corpus supports about pointer gain."""

    horizontal: AxisGain | None
    vertical: AxisGain | None
    along_track: AxisGain | None
    """Not a third result. A consistency check with a known answer — see
    :class:`Consistency`."""

    consistency: Consistency | None = None

    dropped: dict[str, str] = field(default_factory=dict)
    """Replays excluded, by id and reason. Reported rather than applied
    silently."""

    @property
    def usable(self) -> bool:
        """Whether anything here may be acted on.

        Requires both axes **and** the consistency check. Shipping the number
        that happens to be significant while a second view of the same data
        contradicts it is how this project has previously produced two
        confidently wrong findings.
        """
        return (
            self.horizontal is not None
            and self.vertical is not None
            and self.consistency is not None
            and self.consistency.agrees
        )

    @property
    def axes_disagree(self) -> bool:
        """Whether one axis shows a gain error the other's interval excludes.

        When this is true a single sensitivity change cannot fix both, and
        saying so is more useful than reporting their average.
        """
        if self.horizontal is None or self.vertical is None:
            return False
        h, v = self.horizontal, self.vertical
        return h.slope < v.ci_low or h.slope > v.ci_high


def _fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Ordinary least squares slope and intercept."""
    if x.size < 2:
        return (math.nan, math.nan)
    variance = float(((x - x.mean()) ** 2).sum())
    if variance <= 0.0:
        return (math.nan, math.nan)
    slope = float(((x - x.mean()) * (y - y.mean())).sum() / variance)
    return (slope, float(y.mean() - slope * x.mean()))


def _bootstrap(
    grouped: dict[int, list[tuple[np.ndarray, np.ndarray]]],
    *,
    resamples: int,
    seed: int,
) -> tuple[float, float]:
    """Resample sessions, then replays within them, then landings, and refit.

    The same hierarchy the timing estimator resamples, for the same reason: two
    landings from one replay share a hand position, a warm-up state and a
    sensitivity, so treating them as independent would produce an interval
    several times too narrow.
    """
    sessions = list(grouped.values())
    if len(sessions) < 2:
        return (math.nan, math.nan)

    rng = np.random.default_rng(seed)
    slopes: list[float] = []
    for _ in range(resamples):
        xs: list[np.ndarray] = []
        ys: list[np.ndarray] = []
        for index in rng.integers(0, len(sessions), len(sessions)):
            replays = sessions[index]
            for pick in rng.integers(0, len(replays), len(replays)):
                x, y = replays[pick]
                draw = rng.integers(0, x.size, x.size)
                xs.append(x[draw])
                ys.append(y[draw])
        slope, _ = _fit(np.concatenate(xs), np.concatenate(ys))
        if math.isfinite(slope):
            slopes.append(slope)

    if len(slopes) < resamples // 2:
        # More than half the resamples failed to identify a slope, which means
        # the predictor barely varies. Reporting a quantile of what survived
        # would be reporting the shape of the failures.
        return (math.nan, math.nan)

    tail = (1.0 - _CONFIDENCE) / 2.0
    low, high = np.quantile(np.array(slopes), [tail, 1.0 - tail])
    return (float(low), float(high))


def _axis(
    name: str,
    samples: list[GainSample],
    pick: str,
    *,
    resamples: int,
    seed: int,
) -> AxisGain | None:
    """Fit one axis. `pick` is `x`, `y` or `along`."""
    grouped: dict[int, list[tuple[np.ndarray, np.ndarray]]] = defaultdict(list)
    all_x: list[np.ndarray] = []
    all_y: list[np.ndarray] = []

    for sample in samples:
        if pick == "x":
            travel = np.array(
                [landing.travel * math.cos(landing.heading) for landing in sample.landings]
            )
            error = np.array([landing.dx for landing in sample.landings])
        elif pick == "y":
            travel = np.array(
                [landing.travel * math.sin(landing.heading) for landing in sample.landings]
            )
            error = np.array([landing.dy for landing in sample.landings])
        else:
            travel = np.array([landing.travel for landing in sample.landings])
            error = np.array([landing.along for landing in sample.landings])

        grouped[sample.session].append((travel, error))
        all_x.append(travel)
        all_y.append(error)

    if not all_x:
        return None
    x = np.concatenate(all_x)
    y = np.concatenate(all_y)
    slope, intercept = _fit(x, y)
    if not math.isfinite(slope):
        return None

    low, high = _bootstrap(grouped, resamples=resamples, seed=seed)
    return AxisGain(
        name=name,
        slope=slope,
        ci_low=low,
        ci_high=high,
        intercept=intercept,
        n=int(x.size),
        n_replays=len(samples),
        n_sessions=len(grouped),
    )


def _predicted_along_slope(samples: list[GainSample], a: float, b: float) -> float:
    """What the along-track slope must be, given the two axis slopes.

    Along-track error is the per-axis error projected onto the direction of
    travel, so `along = a*travel*cos^2 h + b*travel*sin^2 h`. The headings are
    taken from the data rather than assumed uniform: a map whose long jumps run
    mostly horizontally weights the two axes differently, and assuming an even
    split would manufacture a disagreement out of the beatmaps.
    """
    travel: list[float] = []
    projected: list[float] = []
    for sample in samples:
        for landing in sample.landings:
            cos_h = math.cos(landing.heading)
            sin_h = math.sin(landing.heading)
            travel.append(landing.travel)
            projected.append(landing.travel * (a * cos_h**2 + b * sin_h**2))
    slope, _ = _fit(np.array(travel), np.array(projected))
    return slope


def estimate(
    samples: list[GainSample],
    *,
    max_miss_rate: float = 0.10,
    seed: int = 0,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> GainEstimate:
    """Fit pointer gain per axis, and say which replays were left out.

    `seed` fixes the bootstrap so two runs over one corpus give one interval.
    An interval that moves when nothing else did would be a reason to distrust
    every number beside it.
    """
    kept: list[GainSample] = []
    dropped: dict[str, str] = {}

    for sample in samples:
        if sample.n < MIN_LANDINGS_PER_REPLAY:
            dropped[sample.replay_id] = f"too few landings: {sample.n} < {MIN_LANDINGS_PER_REPLAY}"
            continue
        if sample.miss_rate > max_miss_rate:
            dropped[sample.replay_id] = (
                f"miss rate {sample.miss_rate:.0%}: a press outside the hit radius does "
                "not register, so the largest landing errors left this sample as misses"
            )
            continue
        travels = [landing.travel for landing in sample.landings]
        spread = max(travels) - min(travels)
        if spread < MIN_TRAVEL_SPREAD:
            dropped[sample.replay_id] = (
                f"jump distances span only {spread:.0f} px, so a slope against distance "
                "is not identifiable from them"
            )
            continue
        kept.append(sample)

    if not kept:
        return GainEstimate(horizontal=None, vertical=None, along_track=None, dropped=dropped)

    horizontal = _axis("horizontal", kept, "x", resamples=resamples, seed=seed)
    vertical = _axis("vertical", kept, "y", resamples=resamples, seed=seed + 1)
    along = _axis("along-track", kept, "along", resamples=resamples, seed=seed + 2)

    consistency: Consistency | None = None
    if horizontal is not None and vertical is not None and along is not None:
        consistency = Consistency(
            predicted=_predicted_along_slope(kept, horizontal.slope, vertical.slope),
            observed=along.slope,
        )

    return GainEstimate(
        horizontal=horizontal,
        vertical=vertical,
        along_track=along,
        consistency=consistency,
        dropped=dropped,
    )
