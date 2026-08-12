"""Whether a change did what it predicted.

CONTRADICTED is why this exists. A tool that recommends a change and then
reports success whichever way the number moved is worse than one that
recommends nothing, because it launders a wrong recommendation into evidence
for itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from osuforge.analysis.clustering import (
    ReplaySample,
    hierarchical_draws,
    welch_difference_ci,
)
from osuforge.analysis.corpus import Entry
from osuforge.analysis.progress import progress
from osuforge.analysis.verification import verify_boundary
from osuforge.analysis.verify import (
    AGREEMENT_TOLERANCE,
    MIN_REPLAYS_PER_SIDE,
    MIN_SESSIONS_PER_SIDE,
    Boundary,
    Verdict,
    compare,
)

FAST = {"resamples": 600}

START = datetime(2026, 7, 1, 19, 0, tzinfo=UTC)
BEFORE_EPOCH = "aaaa000000000000"
AFTER_EPOCH = "bbbb111111111111"
RECORDED = {BEFORE_EPOCH: {"Offset": "0"}, AFTER_EPOCH: {"Offset": "7"}}
SHARED = {"seed": 0, "resamples": 800}


def bootstrap_route(
    before: list[ReplaySample],
    after: list[ReplaySample],
    *,
    seed: int,
    resamples: int,
) -> tuple[float, float]:
    """The bootstrap route on its own.

    `difference_interval` only reports it where it is the wider of the two, so
    a test that wants to say what this route alone concluded has to ask it
    directly — with the seeds the shared construction uses, or it is answering
    about a resampling nothing runs.
    """
    draws_before = hierarchical_draws(before, resamples=resamples, seed=seed)
    draws_after = hierarchical_draws(after, resamples=resamples, seed=seed + 1)
    assert draws_before is not None and draws_after is not None
    low, high = np.quantile(draws_after - draws_before, [0.025, 0.975])
    return (float(low), float(high))


def side(
    *, mean: float, sigma: float = 12.0, sessions: int = 3, replays: int = 3, seed: int = 1
) -> list[ReplaySample]:
    rng = np.random.default_rng(seed)
    return [
        ReplaySample(
            replay_id=f"s{session}r{index}",
            session=session,
            errors=rng.normal(mean, sigma, 300).tolist(),
        )
        for session in range(sessions)
        for index in range(replays)
    ]


def boundary(
    *, before_mean: float, after_mean: float, offset: tuple[str, str] | None = ("0", "5"), **kwargs
) -> Boundary:
    return Boundary(
        before_epoch="a",
        after_epoch="b",
        before=side(mean=before_mean, seed=1, **kwargs),
        after=side(mean=after_mean, seed=2, **kwargs),
        changed={} if offset is None else {"Offset": offset},
    )


def era(
    *,
    mean: float,
    sessions: int,
    first_session: int = 0,
    replays: int = 3,
    hits: int = 150,
    seed: int,
) -> list[Entry]:
    """One settings era as a caller hands it over: sessions a day apart,
    replays minutes apart inside them."""
    rng = np.random.default_rng(seed)
    return [
        Entry(
            replay=f"s{first_session + offset}r{index}.osr",
            beatmap_hash="map-a",
            beatmap="Artist - Title [map-a]",
            played_at=START + timedelta(days=first_session + offset, minutes=5 * index),
            session=first_session + offset,
            errors=rng.normal(mean, 12.0, hits).tolist(),
            miss_rate=0.02,
            accuracy=0.97,
            breaks=1,
        )
        for offset in range(sessions)
        for index in range(replays)
    ]


def crossed() -> tuple[list[Entry], dict[str, str]]:
    """+8 ms before a recorded offset change and +1 ms after, four sessions a side.

    Sized so the bootstrap is the wider route and therefore the reported one.
    The cluster route has been shared since it moved into `clustering`, so a
    corpus that route won would agree across both roads whatever the bootstrap
    did — and would have agreed before the bootstrap was shared as well, which
    is a test of nothing.
    """
    before = era(mean=8.0, sessions=4, seed=3)
    after = era(mean=1.0, sessions=4, first_session=4, seed=13)
    epochs = {entry.replay: BEFORE_EPOCH for entry in before}
    epochs |= {entry.replay: AFTER_EPOCH for entry in after}
    return before + after, epochs


def drifting_side(
    *, mean: float, drift: float, sessions: int = 2, replays: int = 3, seed: int = 1
) -> list[ReplaySample]:
    """Sessions that genuinely differ from one another.

    `side` above draws every session from one distribution, so its sessions
    differ only by sampling noise and the two routes to an interval come out
    nearly the same width. Real evenings do not sit that close together, and
    the gap between the routes is only visible when they do not: two sessions
    a side cost the cluster route a t of 4.3, which the bootstrap never pays.
    """
    rng = np.random.default_rng(seed)
    shifts = np.linspace(-drift, drift, sessions)
    return [
        ReplaySample(
            replay_id=f"s{session}r{index}",
            session=session,
            errors=rng.normal(mean + shifts[session], 12.0, 300).tolist(),
        )
        for session in range(sessions)
        for index in range(replays)
    ]


def drifting_boundary() -> Boundary:
    """A real -3.5 ms move, on two sessions a side that sit 2 ms apart.

    Sized on purpose to land between the two routes: far enough from zero for
    the bootstrap to call it a move, not far enough for the cluster route.
    """
    return Boundary(
        before_epoch="a",
        after_epoch="b",
        before=drifting_side(mean=6.0, drift=1.0, seed=1),
        after=drifting_side(mean=2.5, drift=1.0, seed=2),
        changed={"Offset": ("0", "3")},
    )


class TestTheSign:
    def test_raising_the_offset_predicts_the_mean_falls(self) -> None:
        # The load-bearing sign. Raising the offset delays when objects are
        # judged, so a hit that was late becomes closer to on time. Inverted,
        # every verdict in this module reverses.
        result = boundary(before_mean=0.0, after_mean=0.0, offset=("0", "5"))
        assert compare(result, **FAST).predicted == pytest.approx(-5.0)

    def test_lowering_it_predicts_the_other_way(self) -> None:
        result = boundary(before_mean=0.0, after_mean=0.0, offset=("5", "0"))
        assert compare(result, **FAST).predicted == pytest.approx(5.0)


class TestVerdicts:
    def test_moving_as_predicted_is_confirmed(self) -> None:
        # Offset raised by 5, mean fell by 5.
        result = compare(boundary(before_mean=8.0, after_mean=3.0, offset=("0", "5")), **FAST)
        assert result.verdict is Verdict.CONFIRMED
        assert result.measurable
        assert "roughly the predicted amount" in result.reason

    def test_moving_the_wrong_way_is_contradicted(self) -> None:
        # The case the whole module exists for: offset raised, mean went up.
        result = compare(boundary(before_mean=3.0, after_mean=11.0, offset=("0", "5")), **FAST)
        assert result.verdict is Verdict.CONTRADICTED
        assert "put it back" in result.reason

    def test_the_right_way_by_the_wrong_amount_is_partial(self) -> None:
        # Offset raised by 5, mean fell by 15. Something else moved too.
        result = compare(boundary(before_mean=18.0, after_mean=3.0, offset=("0", "5")), **FAST)
        assert result.verdict is Verdict.PARTIAL
        assert "something else moved" in result.reason

    def test_no_measurable_move_is_not_a_failure(self) -> None:
        # Distinguished from CONTRADICTED on purpose: too small to see against
        # this much spread is a different outcome from moved the wrong way.
        result = compare(
            boundary(before_mean=5.0, after_mean=5.0, offset=("0", "1"), sigma=30.0), **FAST
        )
        assert result.verdict is Verdict.UNCHANGED
        assert "not the same as the change having failed" in result.reason

    def test_the_tolerance_is_loose_and_named(self) -> None:
        # A player partially re-adapting is expected. The question is whether
        # the change did what it was supposed to at all.
        assert AGREEMENT_TOLERANCE == 0.40


class TestPredictionRequired:
    def test_a_boundary_that_did_not_touch_the_offset_predicts_nothing(self) -> None:
        # It must not be scored as though it had.
        result = compare(
            Boundary(
                before_epoch="a",
                after_epoch="b",
                before=side(mean=5.0, seed=1),
                after=side(mean=5.0, seed=2),
                changed={"Skin": ("one", "two")},
            ),
            **FAST,
        )
        assert result.predicted is None
        assert "nothing was predicted" in result.summary()
        assert "made no prediction" in result.reason

    def test_an_unparsable_offset_predicts_nothing_rather_than_zero(self) -> None:
        result = compare(boundary(before_mean=5.0, after_mean=5.0, offset=("", "wat")), **FAST)
        assert result.predicted is None


class TestRefusal:
    def test_one_session_a_side_is_refused(self) -> None:
        # Comparing one evening with another confounds the change with the
        # evening, and no amount of replays inside one sitting fixes that.
        result = compare(
            Boundary(
                before_epoch="a",
                after_epoch="b",
                before=side(mean=8.0, sessions=1, replays=9),
                after=side(mean=3.0, sessions=1, replays=9, seed=2),
                changed={"Offset": ("0", "5")},
            ),
            **FAST,
        )
        assert result.verdict is Verdict.INSUFFICIENT
        assert "session" in result.reason
        assert "confounds the change with the evening" in result.reason

    def test_too_few_replays_a_side_is_refused(self) -> None:
        result = compare(
            Boundary(
                before_epoch="a",
                after_epoch="b",
                before=side(mean=8.0, sessions=2, replays=1),
                after=side(mean=3.0, seed=2),
                changed={"Offset": ("0", "5")},
            ),
            **FAST,
        )
        assert result.verdict is Verdict.INSUFFICIENT
        assert "before the change" in result.reason

    def test_the_thresholds_are_named(self) -> None:
        assert MIN_REPLAYS_PER_SIDE == 5
        assert MIN_SESSIONS_PER_SIDE == 2


class TestTheWiderRoute:
    """Two routes to the interval, and the wider one decides the verdict.

    `progress` reports an interval on this same difference across this same
    boundary and has always reported the wider of the two routes. A verdict
    built on the narrower one lets the tool print "no detectable change" and
    "confirmed" about one corpus three lines apart, which is the failure this
    module exists to prevent rather than a difference of rounding.
    """

    def test_the_bootstrap_alone_would_call_this_a_move(self) -> None:
        # Asserted first, so the tests below pin which route won rather than a
        # corpus that was never measurable by either.
        drifted = drifting_boundary()
        _, high = bootstrap_route(drifted.before, drifted.after, seed=0, **FAST)
        assert high < 0.0

    def test_the_cluster_route_does_not(self) -> None:
        drifted = drifting_boundary()
        low, high = welch_difference_ci(drifted.before, drifted.after)
        assert low < 0.0 < high

    def test_so_the_verdict_is_unchanged(self) -> None:
        result = compare(drifting_boundary(), **FAST)
        assert result.verdict is Verdict.UNCHANGED
        assert not result.measurable
        assert "not the same as the change having failed" in result.reason

    def test_the_reported_interval_is_the_wider_one(self) -> None:
        drifted = drifting_boundary()
        result = compare(drifted, **FAST)
        assert (result.ci_low, result.ci_high) == welch_difference_ci(drifted.before, drifted.after)


class TestOneConstruction:
    """The interval the progress panel prints and the interval this module
    scores are one interval, to the last decimal.

    They describe the same difference across the same boundary of the same
    corpus, and a reader sees both at once. The cluster route was already
    shared; the bootstrap was not, and each road built it differently — two
    independent streams differenced elementwise here, both sides alternating
    down one stream there, over 10,000 resamples on one road and 4,000 on the
    other. Neither is wrong in isolation, which is why it survived: the
    disagreement is only visible when the two numbers are put beside each
    other, and that is what this does.
    """

    def scored(self) -> tuple[float, float, float, float]:
        entries, epochs = crossed()
        across = progress(entries, epochs=epochs, **SHARED)
        assert across.shift is not None, "four sessions a side is enough to compare"
        verified = verify_boundary(entries, across, RECORDED, **SHARED)
        assert verified is not None, "the boundary is a recorded settings change"
        comparison, unrecorded = verified
        assert unrecorded is None, "the settings behind both fingerprints are recorded"
        return (across.shift.ci_low, across.shift.ci_high, comparison.ci_low, comparison.ci_high)

    def test_the_bootstrap_route_is_the_one_reported(self) -> None:
        # Asserted first, so the test below pins the route that was written
        # twice rather than the one that was already shared.
        entries, epochs = crossed()
        across = progress(entries, epochs=epochs, **SHARED)
        assert across.shift is not None
        assert across.shift.ci_source == "bootstrap"

    def test_both_blocks_report_the_same_interval(self) -> None:
        # Exactly equal rather than close: two constructions that agree to a
        # decimal place are still two constructions, and the next corpus is
        # where they stop agreeing.
        progress_low, progress_high, verified_low, verified_high = self.scored()
        assert (verified_low, verified_high) == (progress_low, progress_high)


class TestReporting:
    def test_the_summary_carries_both_numbers(self) -> None:
        result = compare(boundary(before_mean=8.0, after_mean=3.0, offset=("0", "5")), **FAST)
        text = result.summary()
        assert "predicted" in text
        assert "95% CI" in text

    def test_it_is_stable_across_runs(self) -> None:
        first = compare(boundary(before_mean=8.0, after_mean=3.0), **FAST)
        second = compare(boundary(before_mean=8.0, after_mean=3.0), **FAST)
        assert first.ci_low == second.ci_low
        assert first.ci_high == second.ci_high
