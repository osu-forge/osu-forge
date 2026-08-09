"""What the corpus says, and what it refuses to say.

The refusals matter more than the findings here. A tool that can only say
"change this" will say it when it should not, and "not enough data" and
"nothing wrong" are opposite conclusions that look identical as a blank page.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from osuforge.analysis.corpus import (
    MIN_REPLAYS,
    MIN_SESSIONS,
    Entry,
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
