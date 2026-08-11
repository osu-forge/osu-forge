"""One journal, one verdict, in whatever order the entries arrive.

`verify_boundary` has two callers and they do not agree about order. `forge
diagnose` builds its entries from `sorted(paths)`, which is alphabetical by
replay file name; the served panel builds them sorted by when the replay was
played. Inside `compare`, the bootstrap route resamples sessions and then
replays out of the lists it is handed and draws every index from one
generator, so two orderings of one corpus walk that generator differently and
land on different intervals.

Ground truth is not what these tests are about. They are about there being one
answer: the same folder and the same journal must not read `confirmed` on the
command line and `unchanged` on the panel.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import numpy as np

from osuforge.analysis.corpus import Entry
from osuforge.analysis.progress import Progress, progress
from osuforge.analysis.verification import verify_boundary
from osuforge.analysis.verify import Comparison

START = datetime(2026, 7, 1, 19, 0, tzinfo=UTC)
RESAMPLES = 400
BEFORE_EPOCH = "aaaa000000000000"
AFTER_EPOCH = "bbbb111111111111"
SETTINGS = {BEFORE_EPOCH: {"Offset": "0"}, AFTER_EPOCH: {"Offset": "3"}}

# Alphabetical order over these interleaves the two eras and the sessions
# inside them, which is what `sorted(paths)` does to a replay folder: the file
# name says which map was played, never when.
_MAPS = ("Anamanaguchi - Meow", "Mid - Some Map", "Zun - Bad Apple")


def block(
    *,
    mean: float,
    sessions: int,
    first_session: int = 0,
    replays: int = 3,
    hits: int = 100,
    sigma: float = 12.0,
    seed: int = 3,
) -> list[Entry]:
    """Sessions a day apart, and every session mean exactly `mean`.

    Centred on purpose. Sessions that differ only by sampling noise leave the
    cluster route with a near-zero between-session variance and a narrower
    interval than the bootstrap, and `compare` reports the wider of the two.
    Without this, the order-sensitive route would not be the one deciding and
    these tests would pass on a corpus they never exercised.
    """
    rng = np.random.default_rng(seed)
    built: list[Entry] = []
    for offset in range(sessions):
        session = first_session + offset
        for index in range(replays):
            errors = rng.normal(mean, sigma, hits)
            errors = errors - errors.mean() + mean
            name = _MAPS[(session * replays + index) % len(_MAPS)]
            built.append(
                Entry(
                    replay=f"{name} ({session}-{index}).osr",
                    beatmap_hash="map-a",
                    beatmap=f"{name} [Insane]",
                    played_at=START + timedelta(days=session, minutes=5 * index),
                    session=session,
                    errors=errors.tolist(),
                    miss_rate=0.02,
                    accuracy=0.97,
                    breaks=1,
                )
            )
    return built


def corpus() -> tuple[list[Entry], dict[str, str]]:
    """Offset raised by 3 ms, mean fell by 3.5 ms, three sessions a side."""
    before = block(mean=6.0, sessions=3, seed=3)
    after = block(mean=2.5, sessions=3, first_session=3, seed=4)
    epochs = {entry.replay: BEFORE_EPOCH for entry in before}
    epochs |= {entry.replay: AFTER_EPOCH for entry in after}
    return before + after, epochs


def scored(across: Progress, order: list[Entry]) -> Comparison:
    result = verify_boundary(order, across, SETTINGS, resamples=RESAMPLES)
    assert result is not None, "the boundary is a recorded settings change"
    comparison, unrecorded = result
    assert unrecorded is None, "the settings behind both fingerprints are recorded"
    return comparison


class TestOrderIndependence:
    def test_the_bootstrap_route_is_the_one_deciding(self) -> None:
        # Asserted first, so the tests below pin the order-sensitive route
        # rather than a corpus where the cluster route quietly answered.
        entries, epochs = corpus()
        across = progress(entries, epochs=epochs, resamples=RESAMPLES)
        assert across.shift is not None
        assert across.shift.ci_source == "bootstrap"

    def test_a_shuffled_corpus_scores_identically(self) -> None:
        entries, epochs = corpus()
        across = progress(entries, epochs=epochs, resamples=RESAMPLES)

        shuffler = random.Random(7)
        first = scored(across, list(entries))
        for _ in range(3):
            shuffled = list(entries)
            shuffler.shuffle(shuffled)
            assert shuffled != entries, "a shuffle that changed nothing tests nothing"
            assert scored(across, shuffled) == first

    def test_the_two_caller_orders_reach_the_same_verdict(self) -> None:
        # The concrete failure: `forge diagnose --json` and the panel `forge
        # serve` renders over the same folder, disagreeing about one journal.
        entries, epochs = corpus()
        across = progress(entries, epochs=epochs, resamples=RESAMPLES)

        by_file_name = sorted(entries, key=lambda entry: entry.replay)
        by_played_at = sorted(entries, key=lambda entry: entry.played_at)
        assert by_file_name != by_played_at, "the two orders have to differ to be a test"

        gathered = scored(across, by_file_name)
        served = scored(across, by_played_at)
        assert gathered == served
        assert (gathered.ci_low, gathered.ci_high) == (served.ci_low, served.ci_high)

    def test_two_replays_on_one_timestamp_do_not_decide_the_answer(self) -> None:
        # Sorting on the timestamp alone is stable, so a tie hands the decision
        # straight back to whichever order the caller happened to hold.
        entries, epochs = corpus()
        twin = Entry(
            replay="Zun - Bad Apple (twin).osr",
            beatmap_hash="map-a",
            beatmap="Zun - Bad Apple [Insane]",
            played_at=entries[0].played_at,
            session=entries[0].session,
            errors=list(reversed(entries[0].errors)),
            miss_rate=0.02,
            accuracy=0.97,
            breaks=1,
        )
        epochs[twin.replay] = BEFORE_EPOCH
        tied = [twin, *entries]
        across = progress(tied, epochs=epochs, resamples=RESAMPLES)

        swapped = [entries[0], twin, *entries[1:]]
        assert scored(across, tied) == scored(across, swapped)
