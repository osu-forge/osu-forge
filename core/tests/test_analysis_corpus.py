"""What the corpus says, and what it refuses to say.

The refusals matter more than the findings here. A tool that can only say
"change this" will say it when it should not, and "not enough data" and
"nothing wrong" are opposite conclusions that look identical as a blank page.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from osuforge.analysis.clustering import ClusteredMean
from osuforge.analysis.corpus import (
    MIN_REPLAYS,
    MIN_SESSIONS,
    Entry,
    beatmap_reading,
    by_beatmap,
    diagnose,
)

START = datetime(2026, 8, 1, tzinfo=UTC)


def entries(
    *,
    mean: float,
    sigma: float,
    sessions: int = 4,
    replays: int = 3,
    hits: int = 250,
    beatmap: str = "map-a",
    seed: int = 3,
) -> list[Entry]:
    rng = np.random.default_rng(seed)
    built: list[Entry] = []
    for session in range(sessions):
        for index in range(replays):
            built.append(
                Entry(
                    replay=f"s{session}r{index}",
                    beatmap_hash=beatmap,
                    beatmap=f"Artist - Title [{beatmap}]",
                    played_at=START + timedelta(days=session, minutes=index * 5),
                    session=session,
                    errors=rng.normal(mean, sigma, hits).tolist(),
                    miss_rate=0.01,
                    accuracy=0.97,
                    breaks=1,
                )
            )
    return built


class TestRefusal:
    def test_too_few_replays_says_which_is_missing(self) -> None:
        # "Play more" and "play on more days" are different instructions.
        result = diagnose(entries(mean=0.0, sigma=10.0, sessions=4, replays=1))
        assert not result.usable
        assert result.insufficient is not None
        assert "more replay" in result.insufficient

    def test_too_few_sessions_says_so_separately(self) -> None:
        result = diagnose(entries(mean=0.0, sigma=10.0, sessions=1, replays=20))
        assert not result.usable
        assert result.insufficient is not None
        assert "more session" in result.insufficient
        assert "habit from an evening" in result.insufficient

    def test_the_thresholds_require_both(self) -> None:
        assert MIN_SESSIONS == 3
        assert MIN_REPLAYS == 10

    def test_an_empty_corpus_refuses_rather_than_returning_zeros(self) -> None:
        result = diagnose([])
        assert not result.usable
        assert result.hits == 0

    def test_everything_excluded_is_said_rather_than_shown_as_nothing(self) -> None:
        thin = [
            Entry(
                replay=f"r{i}",
                beatmap_hash="m",
                beatmap="m",
                played_at=START + timedelta(days=i),
                session=i,
                errors=[1.0] * 5,  # below the estimator's minimum hits
                miss_rate=0.0,
                accuracy=0.9,
                breaks=0,
            )
            for i in range(12)
        ]
        result = diagnose(thin)
        assert not result.usable
        assert result.insufficient is not None
        assert "excluded" in result.insufficient
        assert result.dropped


class TestAxes:
    def test_a_real_bias_is_actionable_and_named(self) -> None:
        result = diagnose(entries(mean=9.0, sigma=12.0))
        assert result.usable
        bias = next(axis for axis in result.axes if axis.name == "timing bias")
        assert bias.actionable
        assert "late" in bias.verdict
        assert "one millisecond per millisecond" in bias.detail

    def test_a_centred_corpus_says_no_offset_helps(self) -> None:
        # The outcome a tool that can only recommend would get wrong.
        result = diagnose(entries(mean=0.0, sigma=25.0))
        bias = next(axis for axis in result.axes if axis.name == "timing bias")
        assert not bias.actionable
        assert "not distinguishable from correct" in bias.verdict
        assert "as likely to hurt as help" in bias.detail

    def test_spread_is_reported_whatever_the_bias_says(self) -> None:
        # It is usually the larger number and nothing in the settings touches it.
        for mean in (0.0, 12.0):
            result = diagnose(entries(mean=mean, sigma=20.0))
            spread = next(axis for axis in result.axes if axis.name == "timing spread")
            assert not spread.actionable
            assert "leaves its width alone" in spread.detail

    def test_a_small_bias_beside_a_large_spread_names_the_culprit(self) -> None:
        result = diagnose(entries(mean=3.0, sigma=25.0))
        spread = next(axis for axis in result.axes if axis.name == "timing spread")
        assert "the spread is what is costing the accuracy" in spread.detail

    def test_no_axis_claims_more_than_one_fix(self) -> None:
        # Exactly one of the timing axes may be actionable: the bias. If the
        # spread ever becomes actionable something has confused the two.
        result = diagnose(entries(mean=9.0, sigma=12.0))
        actionable = [axis.name for axis in result.axes if axis.actionable]
        assert actionable == ["timing bias"]


class TestArrival:
    """The split answers to the same inclusion policy the bias does."""

    HITS = 150
    SHIFT = 50.0
    """What a nudged map does to every hit error on it — and to `approach`."""

    def _corpus(self, *, local_offset: int) -> list[Entry]:
        """Twelve clean replays, plus one on a map carrying `local_offset`."""
        built = [
            Entry(
                replay=f"s{session}r{index}",
                beatmap_hash="map-a",
                beatmap="Artist - Title [map-a]",
                played_at=START + timedelta(days=session, minutes=index * 5),
                session=session,
                errors=[4.0] * self.HITS,
                miss_rate=0.01,
                accuracy=0.97,
                breaks=1,
                arrival=[(4.0, 1.0)] * self.HITS,
            )
            for session in range(4)
            for index in range(3)
        ]
        built.append(
            Entry(
                replay="nudged",
                beatmap_hash="map-b",
                beatmap="Artist - Title [map-b]",
                played_at=START + timedelta(days=3, hours=1),
                session=3,
                errors=[4.0 + self.SHIFT] * self.HITS,
                miss_rate=0.01,
                accuracy=0.97,
                breaks=1,
                local_offset=local_offset,
                arrival=[(4.0 + self.SHIFT, 1.0)] * self.HITS,
            )
        )
        return built

    def test_a_replay_the_policy_excludes_is_out_of_the_split_too(self) -> None:
        # The bias already refuses a map judged on a shifted clock. Pooling its
        # arrival pairs anyway would put the same shift into `approach` while
        # the number beside it refused it, and the two would describe different
        # corpora in one report.
        result = diagnose(self._corpus(local_offset=-20))
        assert "local offset" in result.dropped["nudged"]
        assert result.timing is not None
        split = result.timing.decomposition
        assert split is not None
        assert split.n == 12 * self.HITS
        assert split.approach == pytest.approx(3.0)
        assert split.reaction == pytest.approx(1.0)

    def test_without_the_offset_the_same_replay_is_in_it(self) -> None:
        # The control: the exclusion is what moved `approach`, not the pairs
        # being unreachable from here.
        result = diagnose(self._corpus(local_offset=0))
        assert "nudged" not in result.dropped
        assert result.timing is not None
        split = result.timing.decomposition
        assert split is not None
        assert split.n == 13 * self.HITS
        assert split.approach == pytest.approx((12 * 3.0 + 53.0) / 13)


class TestHonesty:
    def test_the_summary_carries_the_effective_sample(self) -> None:
        # A corpus number computed as though hits were independent is about
        # five times too confident, and that is invisible in the number.
        result = diagnose(entries(mean=5.0, sigma=15.0))
        assert "independent hits" in result.summary()
        assert "design effect" in result.summary()
        assert result.effective_hits < result.hits

    def test_every_timing_axis_shows_its_interval(self) -> None:
        result = diagnose(entries(mean=5.0, sigma=15.0))
        bias = next(axis for axis in result.axes if axis.name == "timing bias")
        assert any("95% CI" in line for line in bias.evidence)


class TestPerBeatmap:
    def test_maps_are_estimated_separately(self) -> None:
        # The question this exists for: is it this map, or every map.
        both = entries(mean=10.0, sigma=8.0, beatmap="map-a") + entries(
            mean=-6.0, sigma=8.0, beatmap="map-b", seed=9
        )
        found = by_beatmap(both)
        assert len(found) == 2
        values = sorted(result.mean for result in found.values())
        assert values[0] < 0 < values[1]

    def test_a_thin_map_is_left_out_rather_than_reported_widely(self) -> None:
        mixed = entries(mean=4.0, sigma=8.0, beatmap="map-a") + entries(
            mean=4.0, sigma=8.0, beatmap="map-thin", sessions=1, replays=1, seed=5
        )
        found = by_beatmap(mixed, minimum=3)
        assert not any("map-thin" in name for name in found)
        assert any("map-a" in name for name in found)

    def test_an_empty_corpus_gives_no_maps(self) -> None:
        assert by_beatmap([]) == {}


class TestReading:
    """The sentence that reads the pooled interval against the per-map ones.

    Built from hand-made intervals rather than from `diagnose`, because each
    case is defined by which intervals exclude zero — and arranging real data
    to land exactly there makes the test about the estimator instead.
    """

    @staticmethod
    def interval(low: float, high: float) -> ClusteredMean:
        return ClusteredMean(
            mean=(low + high) / 2,
            ci_low=low,
            ci_high=high,
            ci_source="cluster",
            se_cluster=1.0,
            se_naive=0.5,
            n_hits=400,
            n_replays=4,
            n_sessions=3,
            icc=0.05,
        )

    def test_a_pool_that_shifts_alone_reads_as_global(self) -> None:
        reading = beatmap_reading(
            self.interval(2.0, 6.0),
            {"a": self.interval(-1.0, 5.0), "b": self.interval(-2.0, 4.0)},
        )
        assert reading is not None
        assert "global offset" in reading

    def test_a_map_that_shifts_alone_reads_as_that_map(self) -> None:
        reading = beatmap_reading(
            self.interval(-2.0, 3.0),
            {"a": self.interval(4.0, 9.0), "b": self.interval(-3.0, 3.0)},
        )
        assert reading is not None
        assert "map to practise" in reading

    def test_both_shifting_says_part_habit_part_map(self) -> None:
        reading = beatmap_reading(
            self.interval(2.0, 6.0),
            {"a": self.interval(4.0, 9.0)},
        )
        assert reading is not None
        assert "part habit, part map" in reading

    def test_nothing_shifting_says_so(self) -> None:
        reading = beatmap_reading(
            self.interval(-2.0, 3.0),
            {"a": self.interval(-3.0, 3.0)},
        )
        assert reading is not None
        assert "shifts away from zero" in reading

    def test_no_reading_without_maps_or_pool(self) -> None:
        assert beatmap_reading(self.interval(2.0, 6.0), {}) is None
        assert beatmap_reading(None, {"a": self.interval(0.0, 1.0)}) is None
