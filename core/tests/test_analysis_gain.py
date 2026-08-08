"""Pointer gain, recovered from landings with a known error injected.

A gain estimator that cannot recover a gain it was handed is not testable
against anything else — there is no corpus with a labelled sensitivity error.
So the synthetic case is the oracle, and every other test here is about what
the estimator must refuse rather than what it must find.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from osuforge.analysis.cursor import Landing, Sector
from osuforge.analysis.gain import (
    CONSISTENCY_TOLERANCE,
    MEANINGFUL_GAIN_CHANGE,
    MIN_LANDINGS_PER_REPLAY,
    GainSample,
    estimate,
)

FAST = {"resamples": 400}


def landing(
    *,
    travel: float,
    heading: float,
    gain_x: float,
    gain_y: float,
    bias: tuple[float, float] = (0.0, 0.0),
    noise: tuple[float, float] = (0.0, 0.0),
) -> Landing:
    """A landing produced by a hand that aimed correctly through a wrong gain.

    The cursor covers `gain` times the displacement asked for, so the error is
    `(gain - 1)` times it — which is why the error grows with distance and a
    constant bias does not.
    """
    tx, ty = travel * math.cos(heading), travel * math.sin(heading)
    dx = (gain_x - 1.0) * tx + bias[0] + noise[0]
    dy = (gain_y - 1.0) * ty + bias[1] + noise[1]
    ux, uy = math.cos(heading), math.sin(heading)
    return Landing(
        index=0,
        time=0,
        dx=dx,
        dy=dy,
        along=dx * ux + dy * uy,
        across=dx * -uy + dy * ux,
        travel=travel,
        heading=heading,
        sector=Sector.RIGHT,
        available=300.0,
        peak_speed=1.0,
        mean_speed=0.5,
        dwell=None,
    )


def replay(
    replay_id: str,
    session: int,
    *,
    gain_x: float,
    gain_y: float,
    count: int = 200,
    bias: tuple[float, float] = (0.0, 0.0),
    sigma: float = 0.0,
    seed: int = 0,
    spread: tuple[float, float] = (40.0, 300.0),
) -> GainSample:
    rng = np.random.default_rng(seed)
    travels = rng.uniform(spread[0], spread[1], count)
    headings = rng.uniform(-math.pi, math.pi, count)
    noise = rng.normal(0.0, sigma, (count, 2)) if sigma else np.zeros((count, 2))
    return GainSample(
        replay_id=replay_id,
        session=session,
        landings=[
            landing(
                travel=float(t),
                heading=float(h),
                gain_x=gain_x,
                gain_y=gain_y,
                bias=bias,
                noise=(float(n[0]), float(n[1])),
            )
            for t, h, n in zip(travels, headings, noise, strict=True)
        ],
    )


def corpus(**kwargs: object) -> list[GainSample]:
    """Four sessions of three replays, so the bootstrap has a hierarchy."""
    return [
        replay(f"s{session}r{index}", session, seed=session * 10 + index, **kwargs)  # type: ignore[arg-type]
        for session in range(4)
        for index in range(3)
    ]


class TestRecovery:
    def test_an_injected_gain_error_comes_back(self) -> None:
        result = estimate(corpus(gain_x=1.02, gain_y=1.02), **FAST)
        assert result.usable
        assert result.horizontal is not None and result.vertical is not None
        assert result.horizontal.slope == pytest.approx(0.02, abs=0.002)
        assert result.vertical.slope == pytest.approx(0.02, abs=0.002)
        assert result.along_track is not None
        assert result.along_track.slope == pytest.approx(0.02, abs=0.002)

    def test_the_sign_says_which_way(self) -> None:
        too_much = estimate(corpus(gain_x=1.05, gain_y=1.05), **FAST)
        too_little = estimate(corpus(gain_x=0.95, gain_y=0.95), **FAST)
        assert too_much.horizontal is not None and too_little.horizontal is not None
        assert too_much.horizontal.slope > 0
        assert too_little.horizontal.slope < 0
        assert "too far" in too_much.horizontal.summary()
        assert "not far enough" in too_little.horizontal.summary()

    def test_each_axis_is_fitted_on_its_own(self) -> None:
        # The reason per-axis exists. Pooling these two would report 1% and
        # describe neither axis.
        result = estimate(corpus(gain_x=1.04, gain_y=0.98), **FAST)
        assert result.horizontal is not None and result.vertical is not None
        assert result.horizontal.slope == pytest.approx(0.04, abs=0.003)
        assert result.vertical.slope == pytest.approx(-0.02, abs=0.003)
        assert result.axes_disagree, "a single sensitivity change cannot fix both"

    def test_matching_axes_do_not_read_as_disagreeing(self) -> None:
        result = estimate(corpus(gain_x=1.02, gain_y=1.02, sigma=2.0), **FAST)
        assert not result.axes_disagree

    def test_a_constant_bias_stays_out_of_the_slope(self) -> None:
        # Aiming 8 pixels off centre every time is not a gain error, and no
        # sensitivity change fixes it. It must land in the intercept.
        result = estimate(corpus(gain_x=1.0, gain_y=1.0, bias=(8.0, -5.0)), **FAST)
        assert result.horizontal is not None and result.vertical is not None
        assert result.horizontal.slope == pytest.approx(0.0, abs=0.002)
        assert result.horizontal.intercept == pytest.approx(8.0, abs=0.5)
        assert result.vertical.intercept == pytest.approx(-5.0, abs=0.5)

    def test_no_gain_error_gives_an_interval_that_includes_zero(self) -> None:
        result = estimate(corpus(gain_x=1.0, gain_y=1.0, sigma=6.0), **FAST)
        assert result.horizontal is not None
        assert not result.horizontal.excludes_zero
        assert "consistent with no gain error" in result.horizontal.summary()

    def test_a_real_error_survives_realistic_noise(self) -> None:
        result = estimate(corpus(gain_x=1.03, gain_y=1.03, sigma=6.0), **FAST)
        assert result.horizontal is not None
        assert result.horizontal.excludes_zero
        assert result.horizontal.ci_low <= 0.03 <= result.horizontal.ci_high


class TestConsistency:
    """The only oracle this estimator has.

    No corpus exists with a labelled sensitivity error, so the check is
    internal: the along-track slope is determined by the two axis slopes and
    the headings, and predicting it costs nothing.
    """

    def test_a_clean_fit_agrees_with_itself(self) -> None:
        result = estimate(corpus(gain_x=1.02, gain_y=1.02), **FAST)
        assert result.consistency is not None
        assert result.consistency.agrees
        assert result.usable
        assert "agrees with the per-axis fits" in result.consistency.summary()

    def test_different_axes_still_agree_because_the_headings_are_used(self) -> None:
        # The prediction weights each axis by how much of the travel ran along
        # it. Assuming an even split would manufacture a disagreement out of
        # the beatmaps rather than finding one in the data.
        result = estimate(corpus(gain_x=1.04, gain_y=0.98), **FAST)
        assert result.consistency is not None
        assert result.consistency.agrees, result.consistency.summary()

    def test_an_along_track_effect_the_axes_cannot_explain_blocks_everything(self) -> None:
        # This is the shape of what the local corpus shows: something growing
        # with jump distance in the direction of travel that is not a per-axis
        # linear gain. The estimate must refuse rather than report whichever
        # number happens to be significant.
        samples = []
        for session in range(4):
            for index in range(3):
                base = replay(f"s{session}r{index}", session, gain_x=1.0, gain_y=1.0, seed=index)
                pushed = [
                    Landing(
                        index=0,
                        time=0,
                        dx=landing.dx + 0.02 * landing.travel * math.cos(landing.heading),
                        dy=landing.dy + 0.02 * landing.travel * math.sin(landing.heading),
                        along=landing.along + 0.02 * landing.travel,
                        across=landing.across,
                        travel=landing.travel,
                        heading=landing.heading,
                        sector=landing.sector,
                        available=landing.available,
                        peak_speed=landing.peak_speed,
                        mean_speed=landing.mean_speed,
                        dwell=landing.dwell,
                    )
                    for landing in base.landings
                ]
                # The axis components are then thrown away, leaving an
                # along-track effect the per-axis fits cannot see.
                samples.append(
                    GainSample(
                        base.replay_id,
                        session,
                        [
                            Landing(
                                index=0,
                                time=0,
                                dx=base.landings[i].dx,
                                dy=base.landings[i].dy,
                                along=pushed[i].along,
                                across=pushed[i].across,
                                travel=pushed[i].travel,
                                heading=pushed[i].heading,
                                sector=pushed[i].sector,
                                available=pushed[i].available,
                                peak_speed=pushed[i].peak_speed,
                                mean_speed=pushed[i].mean_speed,
                                dwell=pushed[i].dwell,
                            )
                            for i in range(len(pushed))
                        ],
                    )
                )

        result = estimate(samples, **FAST)
        assert result.consistency is not None
        assert not result.consistency.agrees
        assert not result.usable, "a contradicted estimate is not a usable one"
        assert "two views of this data disagree" in result.consistency.summary()

    def test_the_tolerance_is_smaller_than_what_it_guards(self) -> None:
        # A check that tolerates more than the effect being measured is not a
        # check.
        assert CONSISTENCY_TOLERANCE < MEANINGFUL_GAIN_CHANGE


class TestCorrection:
    def test_cancelling_an_excess_is_not_the_negated_slope(self) -> None:
        # Cancelling 5% excess needs a factor of 1/1.05, which is -4.76%, not
        # -5%. The difference grows with the residual and is the sort of thing
        # that quietly makes a recommendation land next to the target.
        result = estimate(corpus(gain_x=1.05, gain_y=1.05), **FAST)
        assert result.horizontal is not None
        assert result.horizontal.correction == pytest.approx(1 / (1 + result.horizontal.slope) - 1)
        assert result.horizontal.correction == pytest.approx(-0.0476, abs=0.002)
        assert result.horizontal.correction != pytest.approx(-result.horizontal.slope, abs=1e-4)

    def test_the_meaningful_threshold_is_named(self) -> None:
        assert MEANINGFUL_GAIN_CHANGE == 0.01


class TestRefusal:
    def test_a_short_replay_is_excluded_with_a_reason(self) -> None:
        result = estimate(
            [replay("short", 0, gain_x=1.02, gain_y=1.02, count=MIN_LANDINGS_PER_REPLAY - 1)],
            **FAST,
        )
        assert not result.usable
        assert "too few landings" in result.dropped["short"]

    def test_a_high_miss_rate_is_excluded_because_the_sample_is_truncated(self) -> None:
        # The bias that matters most here: a press outside the hit radius does
        # not register, so the biggest landing errors became misses and left.
        sample = replay("sloppy", 0, gain_x=1.02, gain_y=1.02)
        result = estimate([GainSample("sloppy", 0, sample.landings, miss_rate=0.5)], **FAST)
        assert "left this sample as misses" in result.dropped["sloppy"]

    def test_jumps_all_the_same_length_cannot_identify_a_slope(self) -> None:
        # A line through one cloud. The slope is whatever the noise says, and
        # this is the failure mode that produces a confident number from
        # nothing.
        result = estimate(
            [replay("flat", 0, gain_x=1.02, gain_y=1.02, spread=(200.0, 210.0))], **FAST
        )
        assert not result.usable
        assert "not identifiable" in result.dropped["flat"]

    def test_an_empty_corpus_returns_nothing_rather_than_zero(self) -> None:
        result = estimate([], **FAST)
        assert not result.usable
        assert result.horizontal is None
        assert result.along_track is None

    def test_a_single_session_has_no_interval(self) -> None:
        # One session gives the bootstrap nothing to resample across, so the
        # interval is undefined rather than narrow.
        result = estimate(
            [replay(f"r{i}", 0, gain_x=1.02, gain_y=1.02, seed=i) for i in range(3)], **FAST
        )
        assert result.horizontal is not None
        assert math.isnan(result.horizontal.ci_low)
        assert not result.horizontal.excludes_zero, "an undefined interval excludes nothing"


class TestReporting:
    def test_the_counts_describe_the_nesting(self) -> None:
        result = estimate(corpus(gain_x=1.02, gain_y=1.02), **FAST)
        assert result.horizontal is not None
        assert result.horizontal.n_sessions == 4
        assert result.horizontal.n_replays == 12
        assert result.horizontal.n == 12 * 200

    def test_the_estimate_is_stable_across_runs(self) -> None:
        # An interval that moves when nothing else did would be a reason to
        # distrust every number beside it.
        first = estimate(corpus(gain_x=1.02, gain_y=1.02), **FAST)
        second = estimate(corpus(gain_x=1.02, gain_y=1.02), **FAST)
        assert first.horizontal is not None and second.horizontal is not None
        assert first.horizontal.ci_low == second.horizontal.ci_low
        assert first.horizontal.ci_high == second.horizontal.ci_high
