"""Whether the estimator notices that hits are not independent."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from osuforge.analysis.clustering import (
    MAX_MISS_RATE,
    MIN_HITS_PER_REPLAY,
    ClusteredMean,
    ReplaySample,
    assign_sessions,
    estimate,
    select,
)

FAST = {"resamples": 400, "seed": 7}


def independent(
    *, sessions: int, replays: int, hits: int, sigma: float = 15.0, seed: int = 1
) -> list[ReplaySample]:
    """Hits with no structure at all: one distribution, drawn independently."""
    rng = np.random.default_rng(seed)
    samples = []
    for session in range(sessions):
        for replay in range(replays):
            samples.append(
                ReplaySample(
                    replay_id=f"s{session}r{replay}",
                    session=session,
                    errors=rng.normal(0.0, sigma, hits).tolist(),
                )
            )
    return samples


def clustered(
    *,
    sessions: int,
    replays: int,
    hits: int,
    session_sigma: float = 3.0,
    sigma: float = 15.0,
    seed: int = 1,
) -> list[ReplaySample]:
    """What real data looks like: each session has its own true offset.

    A different audio device, a different warm-up, a different night. Hits
    within a session share it, which is exactly the correlation that makes
    41,000 hits worth far fewer.
    """
    rng = np.random.default_rng(seed)
    samples = []
    for session in range(sessions):
        session_offset = rng.normal(0.0, session_sigma)
        for replay in range(replays):
            samples.append(
                ReplaySample(
                    replay_id=f"s{session}r{replay}",
                    session=session,
                    errors=rng.normal(session_offset, sigma, hits).tolist(),
                )
            )
    return samples


class TestSessions:
    def test_a_long_gap_starts_a_new_session(self) -> None:
        base = datetime(2026, 6, 3, 20, 0, tzinfo=UTC)
        stamps = {
            "a": base,
            "b": base + timedelta(minutes=30),
            "c": base + timedelta(hours=5),
            "d": base + timedelta(hours=5, minutes=20),
        }
        assert assign_sessions(stamps) == {"a": 0, "b": 0, "c": 1, "d": 1}

    def test_sessions_are_numbered_in_time_order_whatever_the_input_order(self) -> None:
        base = datetime(2026, 6, 3, 20, 0, tzinfo=UTC)
        stamps = {"later": base + timedelta(days=1), "earlier": base}
        assert assign_sessions(stamps) == {"earlier": 0, "later": 1}

    def test_a_single_replay_is_one_session(self) -> None:
        assert assign_sessions({"a": datetime(2026, 6, 3, tzinfo=UTC)}) == {"a": 0}

    def test_nothing_is_no_sessions(self) -> None:
        assert assign_sessions({}) == {}


class TestCollapse:
    """The measurement this module exists to make."""

    def test_correlated_hits_are_worth_far_fewer_independent_ones(self) -> None:
        samples = clustered(sessions=10, replays=5, hits=800)
        result = estimate(samples, **FAST)
        assert result.n_hits == 40_000
        assert result.n_sessions == 10
        # A session-level standard deviation of 3 ms against 15 ms within is an
        # ICC of about 0.04, and that is enough to lose most of the corpus.
        assert result.effective_n < result.n_hits / 10, result.summary()
        assert result.design_effect > 10, result.summary()

    def test_independent_hits_keep_their_sample_size(self) -> None:
        # The control. Without it, an estimator that always reported a collapse
        # would pass the test above.
        result = estimate(independent(sessions=10, replays=5, hits=800), **FAST)
        assert result.effective_n > result.n_hits / 3, result.summary()
        assert result.design_effect < 3, result.summary()
        assert result.icc < 0.01

    def test_the_naive_interval_would_have_called_noise_significant(self) -> None:
        # The concrete failure. With a real session-to-session spread, the
        # independent standard error is small enough to declare a mean that is
        # entirely consistent with zero to be four errors away from it.
        samples = clustered(sessions=10, replays=5, hits=800, session_sigma=3.0, seed=5)
        result = estimate(samples, **FAST)
        naive_low = result.mean - 1.96 * result.se_naive
        naive_high = result.mean + 1.96 * result.se_naive
        assert naive_low > 0.0 or naive_high < 0.0, "the fixture needs a nonzero-looking mean"
        assert not result.excludes_zero, result.summary()

    def test_more_hits_from_the_same_sessions_do_not_narrow_the_interval(self) -> None:
        # The property that separates a clustered estimator from a naive one.
        # Playing the same maps again on the same nights adds hits, not
        # information about a different night.
        few = estimate(clustered(sessions=6, replays=3, hits=200, seed=3), **FAST)
        many = estimate(clustered(sessions=6, replays=3, hits=2000, seed=3), **FAST)
        assert many.n_hits == 10 * few.n_hits
        few_width = few.ci_high - few.ci_low
        many_width = many.ci_high - many.ci_low
        assert many_width > few_width * 0.5, (
            f"ten times the hits narrowed the interval from {few_width:.2f} to "
            f"{many_width:.2f}; a naive estimator would have divided it by three"
        )


class TestInterval:
    def test_the_wider_of_the_two_methods_is_reported(self) -> None:
        result = estimate(clustered(sessions=8, replays=4, hits=400), **FAST)
        cluster_width = result.cluster_ci[1] - result.cluster_ci[0]
        bootstrap_width = result.bootstrap_ci[1] - result.bootstrap_ci[0]
        reported = result.ci_high - result.ci_low
        assert reported == pytest.approx(max(cluster_width, bootstrap_width))
        assert result.ci_source in {"cluster", "bootstrap"}

    def test_the_two_methods_broadly_agree_on_well_behaved_data(self) -> None:
        # They are allowed to differ — that is why the wider one is taken — but
        # a factor of two apart would mean one of them is wrong rather than
        # cautious.
        result = estimate(clustered(sessions=10, replays=5, hits=500), seed=11, resamples=2000)
        cluster_width = result.cluster_ci[1] - result.cluster_ci[0]
        bootstrap_width = result.bootstrap_ci[1] - result.bootstrap_ci[0]
        assert 0.5 < cluster_width / bootstrap_width < 2.0, result.summary()

    def test_the_same_corpus_and_seed_give_the_same_interval(self) -> None:
        samples = clustered(sessions=6, replays=3, hits=300)
        first = estimate(samples, **FAST)
        second = estimate(samples, **FAST)
        assert (first.ci_low, first.ci_high) == (second.ci_low, second.ci_high)

    def test_a_real_offset_is_still_detected(self) -> None:
        # Being conservative is not the same as being unable to find anything.
        # A 12 ms bias with only 3 ms of session-to-session spread should clear
        # the interval comfortably.
        rng = np.random.default_rng(4)
        samples = [
            ReplaySample(
                replay_id=f"s{session}r{replay}",
                session=session,
                errors=(12.0 + rng.normal(0.0, 3.0) + rng.normal(0.0, 15.0, 400)).tolist(),
            )
            for session in range(10)
            for replay in range(4)
        ]
        result = estimate(samples, **FAST)
        assert result.excludes_zero, result.summary()
        assert result.ci_low > 5.0, result.summary()


class TestDegenerate:
    def test_no_samples(self) -> None:
        result = estimate([], **FAST)
        assert result.n_hits == 0
        assert math.isnan(result.mean)
        assert result.ci_source == "none"

    def test_replays_with_no_usable_hits_are_dropped(self) -> None:
        samples = [
            ReplaySample("a", 0, []),
            *clustered(sessions=4, replays=2, hits=100),
        ]
        assert estimate(samples, **FAST).n_replays == 8

    def test_a_single_session_cannot_be_clustered(self) -> None:
        # One session gives nothing to compare against, so neither method can
        # produce an interval. Reporting `none` is right; reporting a narrow
        # interval from the hits alone would be the whole mistake.
        result = estimate(clustered(sessions=1, replays=5, hits=200), **FAST)
        assert result.n_sessions == 1
        assert result.ci_source == "none"
        assert math.isnan(result.ci_low)
        assert not result.excludes_zero

    def test_a_negative_icc_estimate_is_clamped_to_zero(self) -> None:
        # The estimator can return a negative value on data that is less
        # correlated than chance. Clamping is the conservative reading: it keeps
        # the design effect from dropping below one.
        result = estimate(independent(sessions=6, replays=4, hits=50, seed=99), **FAST)
        assert result.icc >= 0.0


class TestInclusion:
    def sample(self, replay_id: str, *, hits: int, miss_rate: float = 0.0) -> ReplaySample:
        return ReplaySample(replay_id, 0, [1.0] * hits, miss_rate=miss_rate)

    def test_a_short_replay_is_excluded_with_a_reason(self) -> None:
        result = select([self.sample("short", hits=MIN_HITS_PER_REPLAY - 1)])
        assert result.kept == []
        assert "too few hits" in result.dropped["short"]

    def test_a_replay_with_too_many_misses_is_excluded(self) -> None:
        # The two worst replay means in the local corpus, -57.3 and -57.2 ms
        # against a corpus median of +2.9, are both from plays at 45% and 51%
        # accuracy. The errors that survived are the ones that landed inside the
        # window; the rest became misses and left the sample.
        result = select([self.sample("sloppy", hits=500, miss_rate=MAX_MISS_RATE + 0.01)])
        assert result.kept == []
        assert "truncated sample" in result.dropped["sloppy"]

    def test_a_clean_replay_is_kept(self) -> None:
        result = select([self.sample("good", hits=500, miss_rate=0.01)])
        assert [s.replay_id for s in result.kept] == ["good"]
        assert result.dropped == {}
        assert result.summary() == "all 1 replays included"

    def test_exclusions_are_counted_in_the_summary_rather_than_hidden(self) -> None:
        # A filter that quietly drops what it dislikes is indistinguishable from
        # one that drops what disagrees with the answer.
        result = select(
            [
                self.sample("a", hits=500),
                self.sample("b", hits=10),
                self.sample("c", hits=500, miss_rate=0.5),
            ]
        )
        assert len(result.kept) == 1
        assert "2 excluded" in result.summary()
        assert "1 miss rate" in result.summary()
        assert "1 too few hits" in result.summary()


class TestSummary:
    def test_the_summary_names_the_collapse(self) -> None:
        result = estimate(clustered(sessions=10, replays=5, hits=400), **FAST)
        text = result.summary()
        assert "independent hits" in text
        assert "design effect" in text
        assert "ICC" in text

    def test_session_means_are_reported_in_order(self) -> None:
        result = estimate(clustered(sessions=5, replays=2, hits=100), **FAST)
        assert len(result.session_means) == 5

    def test_excludes_zero_is_false_when_the_interval_straddles_it(self) -> None:
        straddling = ClusteredMean(
            mean=0.4,
            ci_low=-1.0,
            ci_high=1.8,
            ci_source="cluster",
            se_cluster=0.7,
            se_naive=0.07,
            n_hits=41_000,
            n_replays=57,
            n_sessions=10,
            icc=0.05,
        )
        assert not straddling.excludes_zero
        assert straddling.design_effect == pytest.approx(100.0)
        assert straddling.effective_n == pytest.approx(410.0)
