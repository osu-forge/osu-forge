"""Whether it changed, and by how much — the question every change is played for.

A player who edits an offset, swaps a mouse, or spends a month on streams is
running an experiment, and everything else in this package only describes the
state they are in now. This module reads the experiment: the corpus split at
the moment something changed, each side estimated on its own, and the
difference reported with an interval of its own.

# A comparison, never a pool

The epoch machinery refuses to pool hit errors across a settings change,
because that averages two different answers into one that is neither. This
module is the other half of that refusal. The two sides are never mixed; what
crosses the boundary is the *difference between two estimates*, which is
exactly the quantity a before-and-after question is about.

# Where the boundary comes from

In order of preference:

- **A recorded settings change.** The collect journal fingerprints the
  timing-relevant settings per replay. The boundary is the most recent change
  of fingerprint, and the sentence can honestly say "since the settings
  changed". Replays the journal has not seen yet inherit the most recent
  fingerprint before them — the same attribution the journal itself makes,
  stated rather than hidden.
- **The middle of the sessions**, when no change is on record. "Earliest
  sessions against latest" answers "am I drifting" and is labelled as exactly
  that — it is a description of time, not of any intervention.

A boundary that exists but cannot be compared — one side too thin — is
reported with the side and the missing ingredient named, because "play more
under the new settings" is an instruction and an empty panel is not.

# The difference carries its own interval

Same posture as :func:`osuforge.analysis.clustering.estimate`, for the same
reason. Two routes to the interval on `after - before`:

1. The cluster route: the two sides are disjoint sets of sessions, so their
   errors are independent and the variances add. Degrees of freedom by
   Welch-Satterthwaite on the session counts, which with two or three sessions
   a side is the difference between a t of 2.8 and a normal's 1.96 — not a
   refinement.
2. A hierarchical bootstrap on each side — sessions, then replays, then hits —
   differenced draw by draw.

Where they disagree, the wider interval is reported. Two methods disagreeing
means an assumption does not hold here, and the correct response is a wider
interval, not a choice.

Spread is reported per side and left as a description. A difference of
standard deviations needs its own estimator to carry an honest interval, and
until it has one, showing "23.9 → 22.1 ms" with no interval is the truthful
version of that sentence.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy import stats

from osuforge.analysis.clustering import (
    BOOTSTRAP_RESAMPLES,
    ClusteredMean,
    ReplaySample,
    estimate,
    select,
)
from osuforge.analysis.corpus import Entry

__all__ = [
    "MIN_SIDE_REPLAYS",
    "MIN_SIDE_SESSIONS",
    "Progress",
    "SessionPoint",
    "Shift",
    "fill_epochs",
    "progress",
]

MIN_SIDE_SESSIONS = 2
"""Sessions each side needs before its estimate means anything.

Two is the floor at which a session-clustered standard error exists at all.
It buys a wide interval, and the width is the honest part.
"""

MIN_SIDE_REPLAYS = 5
"""Replays each side needs alongside its sessions."""

_CONFIDENCE = 0.95


@dataclass(frozen=True, slots=True)
class SessionPoint:
    """One sitting, described rather than estimated.

    A single session is one cluster, and one cluster has no clustered
    interval — so a point carries its mean and spread as descriptions and
    makes no claim about what they would be next time. The claims live in
    :class:`Shift`, where there are enough sessions to make them.
    """

    session: int
    started_at: datetime
    replays: int
    hits: int
    mean_error: float
    """Real milliseconds, early negative."""

    spread_ms: float
    """Standard deviation of this session's hit errors. NaN below two hits."""


@dataclass(frozen=True, slots=True)
class Shift:
    """Before and after, and the difference with its own interval."""

    before: ClusteredMean
    after: ClusteredMean

    spread_before: float
    spread_after: float
    """Per-side standard deviations, real milliseconds. Descriptions —
    see the module docstring for why no interval is attached."""

    difference: float
    """`after.mean - before.mean`. Positive means the bias moved later."""

    ci_low: float
    ci_high: float
    ci_source: str
    """Which route produced the reported interval: `cluster` or `bootstrap`.
    The wider of the two, always."""

    @property
    def moved(self) -> bool:
        return self.ci_low > 0.0 or self.ci_high < 0.0

    @property
    def toward_zero(self) -> bool:
        return abs(self.after.mean) < abs(self.before.mean)

    def verdict(self) -> str:
        """One sentence a player can read off, in the same voice as the axes."""
        if not self.moved:
            return (
                "no detectable change in bias — the interval on the difference "
                f"includes zero ({self.ci_low:+.1f} to {self.ci_high:+.1f} ms)"
            )
        direction = "later" if self.difference > 0 else "earlier"
        placement = "nearer zero" if self.toward_zero else "further from zero"
        return (
            f"the bias moved {abs(self.difference):.1f} ms {direction} "
            f"(95% CI {self.ci_low:+.1f} to {self.ci_high:+.1f}), "
            f"and now sits {placement}"
        )


@dataclass(frozen=True, slots=True)
class Progress:
    """The corpus over time, and what changed across the boundary."""

    points: list[SessionPoint]

    boundary_kind: str | None = None
    """`settings` when the journal recorded a change, `midpoint` when the
    split is only the middle of the sessions. `None` when nothing could be
    compared."""

    boundary_at: datetime | None = None
    boundary_label: str = ""

    before_epoch: str | None = None
    after_epoch: str | None = None
    """The fingerprints either side of a `settings` boundary, so a caller can
    look up what changed between them and score the change against what it
    predicted. `None` on a midpoint split, which is a description of time and
    predicts nothing."""

    shift: Shift | None = None
    insufficient: str | None = None
    """Why there is no shift, when there is none. A sentence rather than an
    absent block, because "cannot compare yet" and "nothing changed" are
    different answers and only one of them ends the experiment."""


def _kept(entries: list[Entry]) -> list[Entry]:
    """The entries the estimators would use, so the chart describes the same
    corpus the intervals do."""
    kept_ids = {sample.replay_id for sample in select([e.sample for e in entries]).kept}
    return [entry for entry in entries if entry.replay in kept_ids]


def _series(entries: list[Entry]) -> list[SessionPoint]:
    by_session: dict[int, list[Entry]] = defaultdict(list)
    for entry in entries:
        by_session[entry.session].append(entry)

    points: list[SessionPoint] = []
    for session in sorted(by_session):
        group = by_session[session]
        errors = np.array([error for entry in group for error in entry.errors], dtype=float)
        points.append(
            SessionPoint(
                session=session,
                started_at=min(entry.played_at for entry in group),
                replays=len(group),
                hits=int(errors.size),
                mean_error=float(errors.mean()) if errors.size else math.nan,
                spread_ms=float(errors.std(ddof=1)) if errors.size > 1 else math.nan,
            )
        )
    return points


def fill_epochs(ordered: list[Entry], epochs: dict[str, str]) -> dict[str, str | None]:
    """Each replay's fingerprint, carried forward over the ones not recorded.

    The journal attributes a replay to the settings in force when it was first
    seen; a replay it has not seen yet is attributed to the most recent
    fingerprint before it, which is the same claim made explicit. Replays
    before the first recorded one stay unknown rather than inheriting
    backwards — nothing was in force "before" the record starts.

    Public because whoever decides which epoch is "current" — the serve
    corpus does — must make that decision from the same filled record the
    boundary detection uses, or the two can disagree about where the present
    starts.
    """
    filled: dict[str, str | None] = {}
    current: str | None = None
    for entry in ordered:
        recorded = epochs.get(entry.replay)
        if recorded is not None:
            current = recorded
        filled[entry.replay] = current
    return filled


def _settings_boundary(
    ordered: list[Entry], filled: dict[str, str | None]
) -> tuple[datetime, str, str, str] | None:
    """The most recent change of fingerprint, if there was one.

    Carries the two fingerprints out with it. They are what a caller needs to
    look up which settings differ, and re-deriving them from the boundary
    timestamp elsewhere would be a second place for "which epoch is which" to
    be decided.
    """
    previous: str | None = None
    boundary: tuple[datetime, str, str, str] | None = None
    for entry in ordered:
        epoch = filled[entry.replay]
        if epoch is None:
            continue
        if previous is not None and epoch != previous:
            boundary = (
                entry.played_at,
                f"the settings changed ({previous} → {epoch})",
                previous,
                epoch,
            )
        previous = epoch
    return boundary


def _midpoint_boundary(entries: list[Entry]) -> tuple[datetime, str] | None:
    sessions = sorted({entry.session for entry in entries})
    if len(sessions) < 2 * MIN_SIDE_SESSIONS:
        return None
    split = sessions[len(sessions) // 2]
    at = min(entry.played_at for entry in entries if entry.session == split)
    return (
        at,
        "the earliest sessions against the latest — a description of time, not of any change",
    )


def _bootstrap_means(
    samples: list[ReplaySample], *, resamples: int, seed: int
) -> np.ndarray | None:
    """One side's bootstrap distribution of the mean. Sessions, then replays,
    then hits — the same resampling the pooled estimate uses, kept here so the
    draws can be differenced against the other side's."""
    by_session: dict[int, list[np.ndarray]] = defaultdict(list)
    for sample in samples:
        if sample.n:
            by_session[sample.session].append(np.asarray(sample.errors, dtype=float))
    sessions = list(by_session.values())
    if len(sessions) < 2:
        return None

    rng = np.random.default_rng(seed)
    means = np.empty(resamples, dtype=float)
    for draw in range(resamples):
        chosen = rng.integers(0, len(sessions), len(sessions))
        pieces = []
        for index in chosen:
            replays = sessions[index]
            picks = rng.integers(0, len(replays), len(replays))
            for pick in picks:
                errors = replays[pick]
                pieces.append(rng.choice(errors, size=errors.size, replace=True))
        means[draw] = float(np.concatenate(pieces).mean())
    return means


def _spread(entries: list[Entry]) -> float:
    errors = [error for entry in entries for error in entry.errors]
    return float(np.std(np.array(errors), ddof=1)) if len(errors) > 1 else math.nan


def _side_refusal(name: str, kept: list[ReplaySample]) -> str | None:
    sessions = len({sample.session for sample in kept})
    missing: list[str] = []
    if len(kept) < MIN_SIDE_REPLAYS:
        missing.append(f"{MIN_SIDE_REPLAYS - len(kept)} more replay(s)")
    if sessions < MIN_SIDE_SESSIONS:
        missing.append(f"{MIN_SIDE_SESSIONS - sessions} more session(s)")
    if not missing:
        return None
    return f"the {name} side needs {' and '.join(missing)} before the comparison means anything"


def _compare(
    before: list[Entry],
    after: list[Entry],
    *,
    seed: int,
    resamples: int,
) -> Shift | str:
    """Estimate each side on its own and difference the estimates.

    Returns the refusal sentence instead of a `Shift` when a side is too thin.
    """
    kept_before = select([entry.sample for entry in before]).kept
    kept_after = select([entry.sample for entry in after]).kept
    for name, kept in (("before", kept_before), ("after", kept_after)):
        refusal = _side_refusal(name, kept)
        if refusal is not None:
            return refusal

    # Different seeds on purpose: the sides are independent, and resampling
    # both with the same stream would correlate draws that the difference
    # then wrongly cancels.
    estimated_before = estimate(kept_before, seed=seed, resamples=resamples)
    estimated_after = estimate(kept_after, seed=seed + 1, resamples=resamples)
    difference = estimated_after.mean - estimated_before.mean

    cluster_ci = (math.nan, math.nan)
    variance_before = estimated_before.se_cluster**2
    variance_after = estimated_after.se_cluster**2
    if math.isfinite(variance_before) and math.isfinite(variance_after):
        se = math.sqrt(variance_before + variance_after)
        df_before = estimated_before.n_sessions - 1
        df_after = estimated_after.n_sessions - 1
        welch = (variance_before + variance_after) ** 2 / (
            variance_before**2 / df_before + variance_after**2 / df_after
        )
        critical = float(stats.t.ppf(1.0 - (1.0 - _CONFIDENCE) / 2.0, welch))
        cluster_ci = (difference - critical * se, difference + critical * se)

    bootstrap_ci = (math.nan, math.nan)
    draws_before = _bootstrap_means(kept_before, resamples=resamples, seed=seed)
    draws_after = _bootstrap_means(kept_after, resamples=resamples, seed=seed + 1)
    if draws_before is not None and draws_after is not None:
        deltas = draws_after - draws_before
        tail = (1.0 - _CONFIDENCE) / 2.0
        low, high = np.quantile(deltas, [tail, 1.0 - tail])
        bootstrap_ci = (float(low), float(high))

    widths = [
        (interval[1] - interval[0], interval, source)
        for interval, source in ((cluster_ci, "cluster"), (bootstrap_ci, "bootstrap"))
        if math.isfinite(interval[0]) and math.isfinite(interval[1])
    ]
    if not widths:
        return "neither route to an interval on the difference holds up on this split"
    _, interval, source = max(widths, key=lambda item: item[0])

    return Shift(
        before=estimated_before,
        after=estimated_after,
        spread_before=_spread(before),
        spread_after=_spread(after),
        difference=difference,
        ci_low=interval[0],
        ci_high=interval[1],
        ci_source=source,
    )


def progress(
    entries: list[Entry],
    *,
    epochs: dict[str, str] | None = None,
    seed: int = 0,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> Progress:
    """The corpus over time: the per-session series, and the split that
    answers "did it change".

    `epochs` maps replay file name to the settings fingerprint the collect
    journal recorded for it. Without one — or without a change in one — the
    split falls back to the middle of the sessions and says so.
    """
    if not entries:
        return Progress(points=[], insufficient="nothing to chart yet")

    ordered = sorted(entries, key=lambda entry: entry.played_at)
    kept = _kept(ordered)
    points = _series(kept)

    filled = fill_epochs(ordered, epochs or {})
    change = _settings_boundary(ordered, filled)
    before_epoch: str | None = None
    after_epoch: str | None = None
    boundary: tuple[datetime, str] | None
    kind = "settings"
    if change is not None:
        at, label, before_epoch, after_epoch = change
        boundary = (at, label)
    else:
        boundary = _midpoint_boundary(kept)
        kind = "midpoint"
    if boundary is None:
        return Progress(
            points=points,
            insufficient=(
                f"comparing halves needs at least {2 * MIN_SIDE_SESSIONS} sessions; "
                "a trend read off fewer is a description of two evenings"
            ),
        )

    at, label = boundary
    before = [entry for entry in ordered if entry.played_at < at]
    after = [entry for entry in ordered if entry.played_at >= at]
    outcome = _compare(before, after, seed=seed, resamples=resamples)

    if isinstance(outcome, str):
        return Progress(
            points=points,
            boundary_kind=kind,
            boundary_at=at,
            boundary_label=label,
            before_epoch=before_epoch,
            after_epoch=after_epoch,
            insufficient=outcome,
        )
    return Progress(
        points=points,
        boundary_kind=kind,
        boundary_at=at,
        boundary_label=label,
        before_epoch=before_epoch,
        after_epoch=after_epoch,
        shift=outcome,
    )
