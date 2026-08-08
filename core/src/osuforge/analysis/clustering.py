"""Estimating a mean from hits that are not independent.

A corpus of about 41,000 hits sounds like a lot. It is not, and treating it as
though it were is the single easiest way for this tool to produce a confident
wrong answer.

The hits are nested. Every hit sits inside a replay, and every replay sits
inside a playing session. Within one replay a player has one hand position, one
warm-up state, one audio device, one running feel for the map's timing; within
one session, one of most of those. Hits from the same replay are therefore
correlated, and correlated observations carry less information than independent
ones.

The arithmetic is unforgiving. Take 41,000 hits with a standard deviation of
around 15 ms. Assumed independent, the standard error of the mean is 15/√41,000
≈ 0.07 ms, which declares a 0.3 ms average "highly significant" — four standard
errors from zero. It is nothing of the kind. With an intraclass correlation of
only 0.05, the effective sample size collapses from 41,000 to roughly 1,000, the
interval widens by a factor of six, and 0.3 ms stops being distinguishable from
noise.

So the estimator here is clustered, and it reports the collapse rather than
hiding it. :attr:`ClusteredMean.effective_n` and
:attr:`ClusteredMean.design_effect` are not diagnostics for the curious; they
are the numbers that say how much of the corpus is really there.

# How it estimates

Two paths, deliberately, because each fails in a way the other does not:

1. **A cluster-robust standard error** at the session level. Sessions are the
   outermost grouping, so clustering there also absorbs the correlation within
   replays. With a handful of sessions the normal approximation is too narrow,
   so the interval uses a t distribution on the number of sessions rather than
   on the number of hits — which is the whole point.
2. **A hierarchical bootstrap**: resample sessions, then replays within the
   sessions drawn, then hits within the replays drawn. This makes no assumption
   about the shape of the distribution, and it is the one to believe when the
   errors are skewed or the sessions are wildly unequal in size.

Where the two disagree, :func:`estimate` reports **the wider interval**. Two
methods disagreeing means at least one of their assumptions does not hold here,
and the correct response to that is a wider interval, not a choice.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from scipy import stats

__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "MAX_MISS_RATE",
    "MIN_HITS_PER_REPLAY",
    "SESSION_GAP",
    "ClusteredMean",
    "Inclusion",
    "ReplaySample",
    "assign_sessions",
    "estimate",
    "select",
]

MIN_HITS_PER_REPLAY = 100
"""Below this, a replay's mean says more about which notes landed than about
timing."""

MAX_MISS_RATE = 0.10
"""Above this, a replay's hit errors are a truncated sample and the mean is not
an estimate of anything.

This is not an outlier rule. A hit error can only be observed when the object
was hit, and an object is only hit inside the 50 window — so a player who was
60 ms early on an attempt does not contribute a -60 ms error, they contribute a
miss, and the error vanishes from the sample entirely. The more a player misses,
the more of the distribution is cut off, and the surviving mean is pulled toward
whatever direction still lands.

The local corpus shows it plainly. Its two most extreme replay means, -57.3 and
-57.2 ms against a corpus median of +2.9, are both from the same map at 45% and
51% accuracy with 35 and 41 usable hits. Nothing about them is a measurement of
this player's offset."""

SESSION_GAP = timedelta(hours=2)
"""Silence longer than this starts a new session.

Two hours is a judgement rather than a measurement: it is long enough that a
break for a meal does not split a session and short enough that two evenings do
not merge into one. It matters because sessions are the clustering level, and
merging genuinely separate sittings would understate how correlated the data is.
"""

BOOTSTRAP_RESAMPLES = 10_000

_CONFIDENCE = 0.95


@dataclass(frozen=True, slots=True)
class ReplaySample:
    """One replay's usable hit errors, and where it sits in the hierarchy."""

    replay_id: str
    session: int
    errors: list[float]
    """Real-time milliseconds, early negative."""

    miss_rate: float = 0.0
    """Share of the replay's objects that scored nothing.

    Carried here because it decides whether the errors mean anything, not as a
    statistic about the play. See :data:`MAX_MISS_RATE`.
    """

    local_offset: int = 0
    """The beatmap's own offset in milliseconds, from `osu!.db`.

    A player can set a per-map offset in game with `+`/`-`, and it shifts when
    that map's objects are judged. A replay of a map carrying one produces hit
    errors on a different scale from a replay of a map without, and pooling the
    two averages quantities that are not the same quantity.

    Zero is the default because zero is what the reader reports when it cannot
    read the file, and assuming no offset is the direction that does not invent
    a correction. Non-zero excludes the replay — see :func:`select`.
    """

    @property
    def n(self) -> int:
        return len(self.errors)

    @property
    def mean(self) -> float:
        return float(np.mean(self.errors)) if self.errors else math.nan


@dataclass(frozen=True, slots=True)
class ClusteredMean:
    """A mean with an interval that accounts for the nesting."""

    mean: float
    ci_low: float
    ci_high: float
    ci_source: str
    """Which method produced the reported interval: `cluster` or `bootstrap`.

    The wider of the two, always. Recorded because knowing which one won says
    something — the bootstrap winning usually means the sessions are very
    unequal or the errors are skewed.
    """

    se_cluster: float
    se_naive: float
    """What the standard error would be if every hit were independent.

    Present only so the ratio to :attr:`se_cluster` can be shown. It is not a
    number to quote.
    """

    n_hits: int
    n_replays: int
    n_sessions: int

    icc: float
    """Intraclass correlation across replays, clamped to `[0, 1)`.

    Descriptive. The interval does not depend on it — it comes from the
    clustered estimators directly — but it is what makes the collapse legible:
    an ICC of 0.05 is enough to lose most of the corpus.
    """

    cluster_ci: tuple[float, float] = (math.nan, math.nan)
    bootstrap_ci: tuple[float, float] = (math.nan, math.nan)
    session_means: list[float] = field(default_factory=list)

    @property
    def design_effect(self) -> float:
        """How many times larger the variance is than the independent case.

        Measured from the two standard errors rather than derived from the ICC,
        so it reflects what the data did rather than what a model says it should
        have done.
        """
        if self.se_naive <= 0.0:
            return math.nan
        return (self.se_cluster / self.se_naive) ** 2

    @property
    def effective_n(self) -> float:
        """How many independent hits this corpus is worth.

        The most important number this module produces. If it comes back close
        to :attr:`n_hits`, the clustering did not take — which on real replay
        data means something is wrong with the grouping, not that the hits were
        independent after all.
        """
        deff = self.design_effect
        if not math.isfinite(deff) or deff <= 0.0:
            return math.nan
        return self.n_hits / deff

    @property
    def excludes_zero(self) -> bool:
        return self.ci_low > 0.0 or self.ci_high < 0.0

    def summary(self) -> str:
        return (
            f"{self.mean:+.2f} ms (95% CI {self.ci_low:+.2f} to {self.ci_high:+.2f}, "
            f"{self.ci_source}); {self.n_hits} hits in {self.n_replays} replays in "
            f"{self.n_sessions} sessions, worth {self.effective_n:.0f} independent hits "
            f"(design effect {self.design_effect:.1f}, ICC {self.icc:.3f})"
        )


def assign_sessions(
    timestamps: dict[str, datetime], *, gap: timedelta = SESSION_GAP
) -> dict[str, int]:
    """Group replays into sessions by when they were played.

    Returns replay id to session index, numbered from zero in time order.
    """
    ordered = sorted(timestamps.items(), key=lambda item: item[1])
    sessions: dict[str, int] = {}
    current = 0
    previous: datetime | None = None
    for replay_id, when in ordered:
        if previous is not None and when - previous > gap:
            current += 1
        sessions[replay_id] = current
        previous = when
    return sessions


@dataclass(frozen=True, slots=True)
class Inclusion:
    """Which replays feed the estimate, and why the rest do not."""

    kept: list[ReplaySample]
    dropped: dict[str, str]

    def summary(self) -> str:
        if not self.dropped:
            return f"all {len(self.kept)} replays included"
        reasons: dict[str, int] = {}
        for reason in self.dropped.values():
            key = reason.split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
        detail = ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
        return f"{len(self.kept)} replays included, {len(self.dropped)} excluded ({detail})"


def select(samples: list[ReplaySample]) -> Inclusion:
    """Apply the inclusion policy, and record what it excluded and why.

    Reported rather than applied silently. A filter that quietly removes the
    replays it does not like is indistinguishable from one that removes the
    replays that disagree with the answer, and the difference has to be
    visible in the output.
    """
    kept: list[ReplaySample] = []
    dropped: dict[str, str] = {}
    for sample in samples:
        if sample.local_offset:
            # Excluded rather than corrected. Correcting needs the sign
            # convention — whether osu! shifts the objects later or the audio
            # earlier — and every beatmap in the local corpus has an offset of
            # zero, so there is nothing here to establish it against. A
            # correction applied with the sign backwards doubles the error it
            # was meant to remove, and nothing downstream would notice.
            dropped[sample.replay_id] = (
                f"local offset: the beatmap carries {sample.local_offset:+d} ms, so its "
                "hit errors are on a different scale from the rest"
            )
        elif sample.n < MIN_HITS_PER_REPLAY:
            dropped[sample.replay_id] = f"too few hits: {sample.n} < {MIN_HITS_PER_REPLAY}"
        elif sample.miss_rate > MAX_MISS_RATE:
            dropped[sample.replay_id] = (
                f"miss rate: {sample.miss_rate:.0%} > {MAX_MISS_RATE:.0%}, so the "
                "observed errors are a truncated sample"
            )
        else:
            kept.append(sample)
    return Inclusion(kept=kept, dropped=dropped)


def _icc(samples: list[ReplaySample]) -> float:
    """One-way random effects intraclass correlation across replays."""
    groups = [np.asarray(s.errors, dtype=float) for s in samples if s.n > 0]
    k = len(groups)
    total = sum(g.size for g in groups)
    if k < 2 or total <= k:
        return 0.0

    grand = float(np.concatenate(groups).mean())
    between = sum(g.size * (float(g.mean()) - grand) ** 2 for g in groups) / (k - 1)
    within_ss = sum(float(((g - g.mean()) ** 2).sum()) for g in groups)
    within = within_ss / (total - k)
    if within <= 0.0:
        return 0.0

    # The size-adjusted average group size, which is what the ICC formula wants
    # when the groups are unequal — and replays are very unequal.
    sizes = np.array([g.size for g in groups], dtype=float)
    n0 = (total - (sizes**2).sum() / total) / (k - 1)
    if n0 <= 1.0:
        return 0.0

    icc = (between - within) / (between + (n0 - 1.0) * within)
    # A negative estimate is a real outcome of the estimator, not a real
    # negative correlation. Clamped to zero, which is the conservative reading:
    # it makes the design effect no smaller than one.
    return float(min(max(icc, 0.0), 0.999))


def _cluster_se(values: np.ndarray, clusters: np.ndarray, mean: float) -> float:
    """Cluster-robust standard error of a mean.

    The sandwich estimator for a mean reduces to summing each cluster's total
    deviation and taking the root mean square of those, with the usual finite
    cluster correction. What it does *not* do is divide by the number of
    observations, which is exactly why it does not shrink when a player adds
    another thousand hits from the same session.
    """
    unique = np.unique(clusters)
    g = unique.size
    n = values.size
    if g < 2 or n == 0:
        return math.nan
    totals = np.array([float((values[clusters == c] - mean).sum()) for c in unique])
    correction = g / (g - 1)
    return math.sqrt(correction * float((totals**2).sum())) / n


def _hierarchical_bootstrap(
    samples: list[ReplaySample],
    *,
    resamples: int,
    seed: int,
) -> tuple[float, float]:
    """Resample sessions, then replays, then hits."""
    by_session: dict[int, list[np.ndarray]] = defaultdict(list)
    for sample in samples:
        if sample.n:
            by_session[sample.session].append(np.asarray(sample.errors, dtype=float))
    sessions = list(by_session.values())
    if len(sessions) < 2:
        return (math.nan, math.nan)

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

    tail = (1.0 - _CONFIDENCE) / 2.0
    low, high = np.quantile(means, [tail, 1.0 - tail])
    return (float(low), float(high))


def estimate(
    samples: list[ReplaySample],
    *,
    seed: int = 0,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> ClusteredMean:
    """Estimate the mean hit error, accounting for the nesting.

    `seed` fixes the bootstrap so that two runs over the same corpus produce the
    same interval. An interval that moves when nothing else did would be a
    reason to distrust every number beside it.

    `resamples` is exposed for tests that do not need ten thousand of them. It
    is not a tuning knob: fewer resamples give a noisier interval, and a noisier
    interval here is a worse answer rather than a faster one.
    """
    usable = [s for s in samples if s.n > 0]
    values = np.concatenate([np.asarray(s.errors, dtype=float) for s in usable]) if usable else None
    if values is None or values.size == 0:
        return ClusteredMean(
            mean=math.nan,
            ci_low=math.nan,
            ci_high=math.nan,
            ci_source="none",
            se_cluster=math.nan,
            se_naive=math.nan,
            n_hits=0,
            n_replays=0,
            n_sessions=0,
            icc=0.0,
        )

    clusters = np.concatenate([np.full(s.n, s.session) for s in usable])
    mean = float(values.mean())
    n = values.size
    n_sessions = int(np.unique(clusters).size)

    se_naive = float(values.std(ddof=1) / math.sqrt(n)) if n > 1 else math.nan
    se_cluster = _cluster_se(values, clusters, mean)

    cluster_ci = (math.nan, math.nan)
    if math.isfinite(se_cluster) and n_sessions >= 2:
        # t on the number of sessions, not the number of hits. Ten sessions give
        # nine degrees of freedom and a multiplier of 2.26, against the 1.96 a
        # normal approximation would use — and that gap is the small part of the
        # correction. The large part is se_cluster itself.
        critical = float(stats.t.ppf(1.0 - (1.0 - _CONFIDENCE) / 2.0, n_sessions - 1))
        cluster_ci = (mean - critical * se_cluster, mean + critical * se_cluster)

    bootstrap_ci = _hierarchical_bootstrap(usable, resamples=resamples, seed=seed)

    candidates = [(cluster_ci, "cluster"), (bootstrap_ci, "bootstrap")]
    widths = [
        (high - low, interval, source)
        for interval, source in candidates
        if math.isfinite(interval[0]) and math.isfinite(interval[1])
        for low, high in [interval]
    ]
    if widths:
        _, interval, source = max(widths, key=lambda item: item[0])
    else:
        interval, source = (math.nan, math.nan), "none"

    session_means = [
        float(values[clusters == c].mean()) for c in sorted(np.unique(clusters).tolist())
    ]

    return ClusteredMean(
        mean=mean,
        ci_low=interval[0],
        ci_high=interval[1],
        ci_source=source,
        se_cluster=se_cluster,
        se_naive=se_naive,
        n_hits=int(n),
        n_replays=len(usable),
        n_sessions=n_sessions,
        icc=_icc(usable),
        cluster_ci=cluster_ci,
        bootstrap_ci=bootstrap_ci,
        session_means=session_means,
    )
