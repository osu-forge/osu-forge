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

# Rotating rather than repeating, so alphabetical order and chronological order
# really do disagree about which replay inside a session comes first. The file
# name says which map was played, never when.
MAPS = ("Anamanaguchi - Meow", "Mid - Some Map", "Zun - Bad Apple")


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


def flat_block(
    *,
    error: float,
    sessions: int,
    first_session: int = 0,
    replays: int = 3,
    hits: int = 150,
) -> list[Entry]:
    """Every hit the same millisecond, so every session mean is bit-identical.

    Nobody plays this. It is the corpus that leaves the cluster route with no
    session-level variance at all on either side, which divides by zero rather
    than producing a wide interval unless the route refuses first.
    """
    return [
        Entry(
            replay=f"flat-s{first_session + offset}r{index}.osr",
            beatmap_hash="map-a",
            beatmap="Artist - Title [map-a]",
            played_at=START + timedelta(days=first_session + offset, minutes=5 * index),
            session=first_session + offset,
            errors=[error] * hits,
            miss_rate=0.02,
            accuracy=0.97,
            breaks=1,
        )
        for offset in range(sessions)
        for index in range(replays)
    ]


def tied_block(
    *,
    mean: float,
    sessions: int,
    first_session: int = 0,
    replays: int = 3,
    hits: int = 150,
    sigma: float = 12.0,
    seed: int = 3,
) -> list[Entry]:
    """A session whose replays all carry one timestamp, named after the map.

    `block` spaces them five minutes apart, which orders them the same way
    whatever order the caller held them in. Replays sharing a stamp are the
    case where a stable sort has nothing left to sort by and the caller's
    order stands. The name leads with the map so that sorting by it cuts
    across the sessions rather than agreeing with them, which is what the two
    callers really do to one folder. The map rotates with the session as well
    as the replay, so the two orders disagree inside a session and not only
    about which session comes first — a rotation that agreed inside the
    sessions would leave the stable sort putting both callers back on one list.
    """
    rng = np.random.default_rng(seed)
    return [
        Entry(
            replay=(
                f"{MAPS[(first_session + offset + index) % len(MAPS)]} - "
                f"s{first_session + offset}r{index}.osr"
            ),
            beatmap_hash="map-a",
            beatmap="Artist - Title [map-a]",
            played_at=START + timedelta(days=first_session + offset),
            session=first_session + offset,
            errors=rng.normal(mean, sigma, hits).tolist(),
            miss_rate=0.02,
            accuracy=0.97,
            breaks=1,
        )
        for offset in range(sessions)
        for index in range(replays)
    ]


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

    def test_identical_session_means_leave_the_other_route_to_answer(self) -> None:
        # Both sides carrying zero session-level variance puts a zero in the
        # Welch denominator. Reported as a route that does not hold, so the
        # bootstrap answers alone. A crash here would cost the whole comparison
        # to a corpus that is merely unusually tidy.
        before = flat_block(error=6.0, sessions=3)
        after = flat_block(error=1.0, sessions=3, first_session=3)
        epochs = epochs_for(before, "aaaa000000000000") | epochs_for(after, "bbbb111111111111")

        result = progress(before + after, epochs=epochs, resamples=RESAMPLES)
        assert result.shift is not None
        assert result.shift.ci_source == "bootstrap"
        assert result.shift.difference == -5.0
        assert result.shift.moved

    def test_an_empty_corpus_is_a_sentence_not_a_crash(self) -> None:
        result = progress([])
        assert result.points == []
        assert result.insufficient is not None


class TestOneCorpusOneAnswer:
    """One folder is answered one way, whoever hands it over.

    `forge diagnose` gathers alphabetically by file name and the served panel
    by when the replay was played. The bootstrap resamples sessions and then
    replays out of the lists it is given, drawing from one generator, so two
    orders of one corpus walk that generator differently and land on different
    intervals — which is a property of the caller, not of the play.
    """

    @staticmethod
    def orders() -> tuple[list[list[Entry]], dict[str, str]]:
        before = tied_block(mean=8.0, sessions=4, seed=11)
        after = tied_block(mean=1.0, sessions=4, first_session=4, seed=12)
        entries = before + after
        epochs = epochs_for(before, "aaaa000000000000") | epochs_for(after, "bbbb111111111111")
        return (
            [
                sorted(entries, key=lambda entry: entry.replay),
                sorted(entries, key=lambda entry: entry.played_at),
                list(reversed(entries)),
                entries[7:] + entries[:7],
            ],
            epochs,
        )

    def test_the_bootstrap_route_is_the_one_reported(self) -> None:
        # Asserted first: the cluster route reads session moments and does not
        # care what order they arrived in, so a corpus it wins would pass the
        # test below without the ordering having been fixed at all.
        orders, epochs = self.orders()
        result = progress(orders[0], epochs=epochs, resamples=RESAMPLES)
        assert result.shift is not None
        assert result.shift.ci_source == "bootstrap"

    def test_the_two_caller_orders_really_are_two_orders(self) -> None:
        # Without this the test below could be handed one list twice under two
        # names, and would pass on a corpus it never split two ways.
        orders, _ = self.orders()
        assert orders[0] != orders[1]

    def test_the_callers_order_does_not_decide_the_interval(self) -> None:
        orders, epochs = self.orders()
        intervals = set()
        for order in orders:
            result = progress(order, epochs=epochs, resamples=RESAMPLES)
            assert result.shift is not None
            intervals.add((result.shift.ci_low, result.shift.ci_high))
        assert len(intervals) == 1


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
