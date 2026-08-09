"""Bias, precision, and the part of lateness no offset touches."""

from __future__ import annotations

import math

import numpy as np
import pytest

from osuforge.analysis.clustering import ReplaySample
from osuforge.analysis.timing import (
    UNSTABLE_RATE_SCALE,
    decomposition_from,
    profile,
)

FAST = {"resamples": 400}


def samples(
    *, mean: float, sigma: float, sessions: int = 6, replays: int = 3, hits: int = 300
) -> list[ReplaySample]:
    rng = np.random.default_rng(7)
    return [
        ReplaySample(
            replay_id=f"s{session}r{index}",
            session=session,
            errors=rng.normal(mean, sigma, hits).tolist(),
        )
        for session in range(sessions)
        for index in range(replays)
    ]


class TestBiasAndPrecisionAreDifferentQuestions:
    def test_a_real_bias_is_reported_as_actionable(self) -> None:
        result = profile(samples(mean=8.0, sigma=12.0), **FAST)
        assert result.offset_would_help
        assert result.bias.mean == pytest.approx(8.0, abs=1.0)

    def test_a_centred_but_wide_distribution_is_not_an_offset_problem(self) -> None:
        # The case that matters. Handing this player an offset gives them
        # something that cannot work, and they conclude the tool is wrong
        # rather than that the advice was.
        result = profile(samples(mean=0.0, sigma=30.0), **FAST)
        assert not result.offset_would_help
        assert "no offset value improves this" in result.summary()
        assert "does not narrow a distribution" in result.summary()

    def test_a_wide_spread_does_not_make_a_zero_mean_actionable(self) -> None:
        # A large spread makes it more tempting to recommend something, not
        # less, so the gate is on the bias interval alone.
        narrow = profile(samples(mean=0.0, sigma=5.0), **FAST)
        wide = profile(samples(mean=0.0, sigma=40.0), **FAST)
        assert not narrow.offset_would_help
        assert not wide.offset_would_help
        assert wide.unstable_rate > narrow.unstable_rate

    def test_the_spread_is_independent_of_the_bias(self) -> None:
        # An offset moves the whole distribution and leaves its width alone,
        # which is the entire reason these are reported separately.
        centred = profile(samples(mean=0.0, sigma=15.0), **FAST)
        shifted = profile(samples(mean=25.0, sigma=15.0), **FAST)
        assert shifted.unstable_rate == pytest.approx(centred.unstable_rate, rel=0.01)


class TestUnstableRate:
    def test_it_is_ten_times_the_standard_deviation(self) -> None:
        result = profile(samples(mean=0.0, sigma=10.0), **FAST)
        assert result.spread_ms == pytest.approx(10.0, rel=0.02)
        assert result.unstable_rate == pytest.approx(result.spread_ms * UNSTABLE_RATE_SCALE)

    def test_it_is_reported_in_real_time_not_map_time(self) -> None:
        # Under Double Time a 10 ms map-time spread is 6.7 ms of real time, and
        # the score screen shows the latter. Quoting map time next to a game
        # displaying real time would read as a bug in the tool.
        built = samples(mean=0.0, sigma=15.0)
        assert profile(built, rate=1.5, **FAST).unstable_rate == pytest.approx(
            profile(built, rate=1.0, **FAST).unstable_rate / 1.5
        )


class TestDecomposition:
    def test_the_two_terms_sum_to_the_hit_error(self) -> None:
        # True by construction, and asserted because it is the identity the
        # whole split rests on.
        result = decomposition_from([(12.0, 5.0), (8.0, 3.0), (20.0, 11.0)])
        assert result is not None
        assert result.total == pytest.approx(np.mean([12.0, 8.0, 20.0]))

    def test_waiting_on_target_lands_in_the_reaction_term(self) -> None:
        # Cursor arrived 30 ms before the object was due and the press came
        # 10 ms after it. The lateness is entirely hand, not clock.
        result = decomposition_from([(10.0, 40.0)] * 50)
        assert result is not None
        assert result.reaction == pytest.approx(40.0)
        assert result.approach == pytest.approx(-30.0)
        assert "unchanged by any offset" in result.summary()

    def test_arriving_late_lands_in_the_approach_term(self) -> None:
        result = decomposition_from([(25.0, 2.0)] * 50)
        assert result is not None
        assert result.approach == pytest.approx(23.0)
        assert result.reaction == pytest.approx(2.0)

    def test_an_unmeasurable_arrival_is_excluded_rather_than_treated_as_zero(self) -> None:
        # A cursor already on target when the window opened has its arrival
        # outside the window; the split is undefined for it, not zero.
        result = decomposition_from([(10.0, 4.0), (10.0, math.nan), (10.0, 6.0)])
        assert result is not None
        assert result.n == 2
        assert result.reaction == pytest.approx(5.0)

    def test_nothing_measurable_gives_nothing(self) -> None:
        assert decomposition_from([(10.0, math.nan)]) is None
        assert decomposition_from([]) is None

    def test_the_profile_carries_it_into_the_summary(self) -> None:
        result = profile(
            samples(mean=10.0, sigma=12.0),
            decomposition=decomposition_from([(10.0, 40.0)] * 50),
            **FAST,
        )
        assert "unchanged by any offset" in result.summary()
