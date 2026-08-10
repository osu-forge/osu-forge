"""Did it change — and does the module refuse when it cannot know.

Ground truth throughout: entries are drawn from normals whose means are
chosen, so the right answer is known before the estimator runs. The
resample counts are small; a noisier interval is acceptable in a test that
asserts containment, not width.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from osuforge.analysis.corpus import Entry
from osuforge.analysis.progress import (
    MIN_SIDE_SESSIONS,
    progress,
)

START = datetime(2026, 7, 1, 19, 0, tzinfo=UTC)
RESAMPLES = 400


def block(
    *,
    mean: float,
    sessions: int,
    first_session: int = 0,
    replays: int = 3,
    hits: int = 150,
    sigma: float = 12.0,
    beatmap: str = "map-a",
    seed: int = 3,
) -> list[Entry]:
    """Sessions a day apart, replays minutes apart — one settings era."""
    rng = np.random.default_rng(seed)
    built: list[Entry] = []
    for offset in range(sessions):
        session = first_session + offset
        for index in range(replays):
            built.append(
                Entry(
                    replay=f"s{session}r{index}.osr",
                    beatmap_hash=beatmap,
                    beatmap=f"Artist - Title [{beatmap}]",
                    played_at=START + timedelta(days=session, minutes=5 * index),
                    session=session,
                    errors=rng.normal(mean, sigma, hits).tolist(),
                    miss_rate=0.02,
                    accuracy=0.97,
                    breaks=1,
                )
            )
    return built


def epochs_for(entries: list[Entry], digest: str) -> dict[str, str]:
    return {entry.replay: digest for entry in entries}


class TestShift:
    def test_a_real_move_is_detected_and_contains_the_truth(self) -> None:
        # +8 ms before the change, +1 ms after: the true difference is -7.
        before = block(mean=8.0, sessions=3, seed=3)
        after = block(mean=1.0, sessions=3, first_session=3, seed=4)
        epochs = epochs_for(before, "aaaa000000000000") | epochs_for(after, "bbbb111111111111")

        result = progress(before + after, epochs=epochs, resamples=RESAMPLES)
        assert result.boundary_kind == "settings"
        assert result.shift is not None
        shift = result.shift
        assert shift.moved
        assert shift.ci_low <= -7.0 <= shift.ci_high
        assert "earlier" in shift.verdict()
        assert "nearer zero" in shift.verdict()

    def test_no_change_is_reported_as_no_change(self) -> None:
        # Same mean both sides: a tool that reads improvement into noise here
        # would read it into every settings change ever made.
        before = block(mean=3.0, sessions=3, seed=5)
        after = block(mean=3.0, sessions=3, first_session=3, seed=6)
        epochs = epochs_for(before, "aaaa000000000000") | epochs_for(after, "bbbb111111111111")

        result = progress(before + after, epochs=epochs, resamples=RESAMPLES)
        assert result.shift is not None
        assert not result.shift.moved
        assert "includes zero" in result.shift.verdict()

    def test_the_boundary_is_the_most_recent_change(self) -> None:
        first = block(mean=8.0, sessions=2, seed=7)
        second = block(mean=8.0, sessions=2, first_session=2, seed=8)
        third = block(mean=1.0, sessions=2, first_session=4, seed=9)
        epochs = (
            epochs_for(first, "aaaa000000000000")
            | epochs_for(second, "bbbb111111111111")
            | epochs_for(third, "cccc222222222222")
        )
        result = progress(first + second + third, epochs=epochs, resamples=RESAMPLES)
        assert result.boundary_at == min(entry.played_at for entry in third)
        assert "bbbb111111111111 → cccc222222222222" in result.boundary_label

    def test_unrecorded_replays_inherit_the_fingerprint_before_them(self) -> None:
        # The after side is not in the journal yet — the ordinary state of a
        # play made minutes ago. It inherits the newest fingerprint, so the
        # boundary stays where the record put it.
        before = block(mean=8.0, sessions=3, seed=3)
        changed = block(mean=1.0, sessions=1, first_session=3, seed=4)
        fresh = block(mean=1.0, sessions=2, first_session=4, seed=5)
        epochs = epochs_for(before, "aaaa000000000000") | epochs_for(changed, "bbbb111111111111")

        result = progress(before + changed + fresh, epochs=epochs, resamples=RESAMPLES)
        assert result.boundary_kind == "settings"
        assert result.boundary_at == min(entry.played_at for entry in changed)
        assert result.shift is not None
        assert result.shift.after.n_replays == len(changed) + len(fresh)


class TestFallbackAndRefusal:
    def test_without_epochs_the_split_is_the_midpoint_and_says_so(self) -> None:
        result = progress(block(mean=4.0, sessions=6, seed=11), resamples=RESAMPLES)
        assert result.boundary_kind == "midpoint"
        assert "description of time" in result.boundary_label
        assert result.shift is not None

    def test_too_few_sessions_refuses_with_the_number(self) -> None:
        result = progress(block(mean=4.0, sessions=3, seed=12), resamples=RESAMPLES)
        assert result.boundary_kind is None
        assert result.shift is None
        assert result.insufficient is not None
        assert str(2 * MIN_SIDE_SESSIONS) in result.insufficient

    def test_a_thin_side_names_itself_and_what_is_missing(self) -> None:
        # Settings changed one session ago: the honest answer is "play more
        # under the new settings", not a comparison against one sitting.
        before = block(mean=8.0, sessions=3, seed=13)
        after = block(mean=1.0, sessions=1, first_session=3, seed=14)
        epochs = epochs_for(before, "aaaa000000000000") | epochs_for(after, "bbbb111111111111")

        result = progress(before + after, epochs=epochs, resamples=RESAMPLES)
        assert result.boundary_kind == "settings"
        assert result.shift is None
        assert result.insufficient is not None
        assert "after side" in result.insufficient
        assert "session" in result.insufficient

    def test_an_empty_corpus_is_a_sentence_not_a_crash(self) -> None:
        result = progress([])
        assert result.points == []
        assert result.insufficient is not None


class TestSeries:
    def test_one_point_per_session_with_descriptive_numbers(self) -> None:
        entries = block(mean=6.0, sessions=4, replays=2, seed=15)
        result = progress(entries, resamples=RESAMPLES)
        assert len(result.points) == 4
        for point in result.points:
            assert point.replays == 2
            assert point.hits == 300
            assert abs(point.mean_error - 6.0) < 4.0

    def test_the_series_describes_what_the_estimates_use(self) -> None:
        # A replay the inclusion policy drops must not appear in the chart
        # either, or the picture and the intervals describe different corpora.
        entries = block(mean=6.0, sessions=4, seed=16)
        truncated = Entry(
            replay="truncated.osr",
            beatmap_hash="map-a",
            beatmap="Artist - Title [map-a]",
            played_at=START + timedelta(days=9),
            session=9,
            errors=[1.0] * 150,
            miss_rate=0.5,
            accuracy=0.5,
            breaks=9,
        )
        result = progress([*entries, truncated], resamples=RESAMPLES)
        assert all(point.session != 9 for point in result.points)
