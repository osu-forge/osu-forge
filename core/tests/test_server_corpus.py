"""The corpus the server answers for, and what its answer admits to.

The decisions under test are the ones a page cannot check for itself: whether
disbelieved plays stayed out of the entries while still counting against the
corpus's health, whether sessions came from the replays' own timestamps, and
whether the answer says plainly that nothing here authorises a recommendation.
"""

from __future__ import annotations

import json
import math
import typing
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from osuforge.analysis.verification import UNRECORDED_CHANGE
from osuforge.collect.epoch import ConfigEpoch
from osuforge.replay.validate import Agreement
from osuforge.server.corpus import CorpusState

from .test_collect import config

START = datetime(2026, 8, 1, 19, 0, tzinfo=UTC)


def feed(
    state: CorpusState,
    *,
    sessions: int = 4,
    replays: int = 3,
    hits: int = 120,
    mean: float = 6.0,
    beatmap: str = "map-a",
    agreement: Agreement = Agreement.EXACT,
    seed: int = 3,
    first_session: int = 0,
    epoch: str | None = None,
    dwell: float | None = None,
) -> None:
    """Plays on separate days, well past the session gap."""
    rng = np.random.default_rng(seed)
    for offset in range(sessions):
        session = first_session + offset
        for index in range(replays):
            errors = rng.normal(mean, 12.0, hits).tolist()
            state.add(
                f"{beatmap}-s{session}r{index}.osr",
                beatmap_hash=beatmap,
                beatmap=f"Artist - Title [{beatmap}]",
                played_at=START + timedelta(days=session, minutes=5 * index),
                errors=errors,
                miss_rate=0.02,
                accuracy=0.97,
                breaks=1,
                agreement=agreement,
                agreement_reason="all judgements reproduce",
                fractional_windows=False,
                arrival=None if dwell is None else [(error, dwell) for error in errors],
                epoch=epoch,
            )


BEFORE = [2, 6, 10, 7, 5]
"""Per-object error under the old settings: +6 ms on average."""


def two_eras(
    state: CorpusState,
    *,
    after: list[int],
    before_epoch: str,
    after_epoch: str,
    before_evenings: int = 3,
) -> None:
    """Two plays an evening, three evenings each side of a recorded change.

    The recipe `forge diagnose`'s verification tests use, fed straight in
    rather than written out as replay files. Every evening carries its own
    millisecond of drift, -1, 0 and +1 within each side, so the side means stay
    exactly where they were written while the evenings still differ from one
    another — a corpus whose sessions share one mean has no between-session
    variance for the clustered estimators to find.

    Three evenings a side rather than two, because the interval on the
    difference is the wider of a bootstrap and a Welch-corrected cluster route,
    and two sessions a side buy the cluster route a t of 4.3 — wide enough to
    swallow the 5 ms move these tests are about. `before_evenings` shortens the
    older side, which is how a boundary the verdict has to refuse is built.
    """
    for evening in range(before_evenings + 3):
        old = evening < before_evenings
        pattern = BEFORE if old else after
        drift = (evening % 3) - 1
        for index in range(2):
            state.add(
                f"e{evening}r{index}.osr",
                beatmap_hash="map-a",
                beatmap="Artist - Title [map-a]",
                played_at=START + timedelta(days=evening, minutes=20 * index),
                errors=[float(pattern[hit % len(pattern)] + drift) for hit in range(120)],
                miss_rate=0.0,
                accuracy=1.0,
                breaks=0,
                agreement=Agreement.EXACT,
                agreement_reason="all judgements reproduce",
                fractional_windows=False,
                epoch=before_epoch if old else after_epoch,
            )


class TestSessions:
    def test_sessions_come_from_the_timestamps_not_the_caller(self) -> None:
        # Four days of plays, two hours apart within a day: four sessions.
        state = CorpusState()
        feed(state, sessions=4, replays=3)
        answer = state.recompute()
        assert answer["sessions"] == 4
        assert answer["replays"] == 12

    def test_one_sitting_is_one_session_however_many_plays(self) -> None:
        state = CorpusState()
        rng = np.random.default_rng(1)
        for index in range(12):
            state.add(
                f"r{index}.osr",
                beatmap_hash="m",
                beatmap="m",
                played_at=START + timedelta(minutes=6 * index),
                errors=rng.normal(0.0, 10.0, 120).tolist(),
                miss_rate=0.0,
                accuracy=0.98,
                breaks=0,
                agreement=Agreement.EXACT,
                agreement_reason="all judgements reproduce",
                fractional_windows=False,
            )
        answer = state.recompute()
        # Refused: twelve replays in one evening are one session, and the
        # refusal names the missing ingredient rather than showing a number.
        assert answer["insufficient"] is not None
        assert "session" in answer["insufficient"]


class TestBelief:
    def test_a_mismatched_play_is_excluded_and_named(self) -> None:
        state = CorpusState()
        feed(state, sessions=4, replays=3)
        state.add(
            "wrong.osr",
            beatmap_hash="map-a",
            beatmap="Artist - Title [map-a]",
            played_at=START + timedelta(days=1, hours=1),
            errors=[500.0] * 120,
            miss_rate=0.02,
            accuracy=0.5,
            breaks=9,
            agreement=Agreement.MISMATCH,
            agreement_reason="judged 400 objects, the game judged 380.",
            fractional_windows=False,
        )
        answer = state.recompute()
        # Out of the estimate...
        assert answer["replays"] == 12
        # ...named with the screen's own reason...
        assert "wrong.osr" in answer["excluded"]
        assert "did not reproduce" in answer["excluded"]["wrong.osr"]
        # ...and still counted against the corpus's health.
        assert answer["health"]["total"] == 13
        assert answer["health"]["usable"] == 12

    def test_no_recommendation_without_the_oracle_whatever_the_numbers_say(self) -> None:
        # A clear 6 ms bias, cleanly measured — and still not a recommendation,
        # because nothing in a serve run has judged these simulations
        # object by object.
        state = CorpusState()
        feed(state)
        answer = state.recompute()
        assert answer["health"]["may_recommend"] is False
        assert any("oracle" in blocker for blocker in answer["health"]["blockers"])


class TestAnswerShape:
    def test_the_answer_is_json_all_the_way_down(self) -> None:
        state = CorpusState()
        feed(state)
        # allow_nan off is the whole test: one NaN anywhere is a floor the
        # middleware would fall through at request time.
        json.dumps(state.recompute(), allow_nan=False)

    def test_so_is_the_refusal(self) -> None:
        state = CorpusState()
        feed(state, sessions=1, replays=2)
        answer = state.recompute()
        assert answer["insufficient"] is not None
        assert answer["bias"] is None
        json.dumps(answer, allow_nan=False)

    def test_an_empty_state_still_answers_honestly(self) -> None:
        answer = CorpusState().recompute()
        assert answer["insufficient"] is not None
        assert answer["health"]["total"] == 0
        json.dumps(answer, allow_nan=False)

    def test_per_map_reporting_carries_the_thin_maps_as_a_count(self) -> None:
        state = CorpusState()
        feed(state, beatmap="map-a")
        feed(state, beatmap="map-b", sessions=1, replays=1, seed=9)
        answer = state.recompute()
        reported = [entry["name"] for entry in answer["beatmaps"]["reported"]]
        assert any("map-a" in name for name in reported)
        assert not any("map-b" in name for name in reported)
        # Two maps were played; one was too thin to report alone. The count is
        # how the page can say so instead of silently showing a shorter table.
        assert answer["beatmaps"]["played"] == 2

    def test_the_reading_compares_pool_against_maps(self) -> None:
        state = CorpusState()
        feed(state, mean=6.0)
        answer = state.recompute()
        assert answer["beatmaps"]["reading"] is not None
        assert "pooled" in answer["beatmaps"]["reading"]


class TestEpochs:
    def test_a_recorded_change_splits_verdict_from_history(self) -> None:
        # Twelve replays under the old settings, twelve under the new. The
        # verdict is about the new era only; the old one is named for what it
        # is and becomes the before side of the progress view.
        state = CorpusState()
        feed(state, sessions=4, epoch="aaaa000000000000", mean=8.0)
        feed(state, sessions=4, first_session=4, epoch="bbbb111111111111", mean=1.0, seed=9)
        answer = state.recompute()

        assert answer["replays"] == 12
        assert sum("not the current" in reason for reason in answer["excluded"].values()) == 12

        assert answer["progress"] is not None
        boundary = answer["progress"]["boundary"]
        assert boundary is not None and boundary["kind"] == "settings"
        shift = answer["progress"]["shift"]
        assert shift is not None
        assert shift["before"]["replays"] == 12
        assert shift["after"]["replays"] == 12
        assert shift["difference"] is not None and shift["difference"] < 0
        assert "verdict" in shift

    def test_plays_the_journal_has_not_seen_join_the_current_era(self) -> None:
        # The ordinary live state: the newest plays are not collected yet.
        # They inherit the newest fingerprint rather than being dropped, or
        # every play made while the page is open would vanish from the verdict.
        state = CorpusState()
        feed(state, sessions=3, epoch="aaaa000000000000")
        feed(state, sessions=3, first_session=3, epoch=None, seed=9)
        answer = state.recompute()
        assert answer["replays"] == 18
        assert not answer["excluded"]

    def test_without_any_journal_the_behaviour_is_unchanged(self) -> None:
        state = CorpusState()
        feed(state, sessions=4)
        answer = state.recompute()
        assert answer["replays"] == 12
        assert answer["progress"] is not None
        boundary = answer["progress"]["boundary"]
        assert boundary is not None and boundary["kind"] == "midpoint"

    def test_the_progress_block_is_json_all_the_way_down(self) -> None:
        state = CorpusState()
        feed(state, sessions=4, epoch="aaaa000000000000", mean=8.0)
        feed(state, sessions=4, first_session=4, epoch="bbbb111111111111", mean=1.0, seed=9)
        json.dumps(state.recompute(), allow_nan=False)


class TestVerification:
    """The verdict `forge diagnose --all-epochs` prints, on the panel serving
    the same corpus.

    A progress split describes what happened either side of a change. Only the
    verdict can come out wrong, and a screen that shows the split without it is
    a screen where a recommendation cannot fail.
    """

    def recorded(self) -> tuple[ConfigEpoch, ConfigEpoch]:
        """Two eras on record: offset 0, then offset +5."""
        return ConfigEpoch.of(config(Offset="0")), ConfigEpoch.of(config(Offset="5"))

    def scored(self, after: list[int], *, snapshots: bool = True) -> dict:
        old, new = self.recorded()
        settings = {old.digest: old.settings, new.digest: new.settings} if snapshots else {}
        state = CorpusState(epoch_settings=settings)
        two_eras(state, after=after, before_epoch=old.digest, after_epoch=new.digest)
        answer = state.recompute()
        # allow_nan off is the whole point of the guard on every number: the
        # comparison leaves them all NaN when it refuses.
        json.dumps(answer, allow_nan=False)
        return answer["progress"]["verification"]

    def test_the_predicted_move_reaches_the_panel(self) -> None:
        # Offset raised by 5, and the mean fell by 5 — which is what raising it
        # is supposed to do, since the game then judges objects later.
        verified = self.scored([-3, 1, 5, 2, 0])
        assert verified["verdict"] == "confirmed"
        assert verified["predicted"] == pytest.approx(-5.0)
        assert verified["difference"] == pytest.approx(-5.0, abs=0.5)
        assert verified["ci_high"] < 0.0

    def test_the_wrong_way_is_contradicted_here_too(self) -> None:
        # The case this exists for, and the one a progress split alone stays
        # silent about: the same recorded change, the mean went the other way.
        verified = self.scored([7, 11, 15, 12, 10])
        assert verified["verdict"] == "contradicted"
        assert verified["difference"] == pytest.approx(5.0, abs=0.5)
        assert "put it back" in verified["reason"]

    def test_a_thin_side_refuses_with_a_sentence_and_no_numbers(self) -> None:
        old, new = self.recorded()
        state = CorpusState(epoch_settings={old.digest: old.settings, new.digest: new.settings})
        two_eras(
            state,
            after=[-3, 1, 5, 2, 0],
            before_epoch=old.digest,
            after_epoch=new.digest,
            before_evenings=1,
        )
        answer = state.recompute()
        json.dumps(answer, allow_nan=False)
        verified = answer["progress"]["verification"]
        assert verified["verdict"] == "insufficient"
        assert all(
            verified[field] is None
            for field in (
                "before_mean",
                "after_mean",
                "difference",
                "ci_low",
                "ci_high",
                "predicted",
            )
        )
        assert "comparing needs" in verified["reason"]

    def test_a_journal_from_before_snapshots_says_what_it_cannot_know(self) -> None:
        # The fingerprints differ, so something changed; without the values
        # behind them there is no way to say it was the offset. Reporting "no
        # offset change here" would be a claim the record does not support —
        # and the panel says it in the command's words, not a second set.
        verified = self.scored([-3, 1, 5, 2, 0], snapshots=False)
        assert verified["predicted"] is None
        assert verified["verdict"] != "confirmed"
        assert verified["reason"] == UNRECORDED_CHANGE

    def test_a_midpoint_split_predicts_nothing_and_is_not_scored(self) -> None:
        # The default state of a first-run corpus. The middle of the sessions
        # is a description of time; scoring it against a prediction nobody made
        # is how a statistics screen starts agreeing with itself.
        state = CorpusState()
        feed(state, sessions=4)
        found = state.recompute()["progress"]
        assert found["boundary"]["kind"] == "midpoint"
        assert found["verification"] is None


class TestLocalOffsets:
    """The same reading `forge diagnose` does, so the two cannot disagree."""

    def test_a_nudged_map_leaves_the_bias_and_is_named_for_it(self) -> None:
        state = CorpusState(offsets={"map-b": -12})
        feed(state, beatmap="map-a")
        feed(state, beatmap="map-b", seed=9)
        answer = state.recompute()

        assert answer["replays"] == 12
        excluded = [
            reason for name, reason in answer["excluded"].items() if name.startswith("map-b")
        ]
        assert len(excluded) == 12
        assert all("local offset" in reason for reason in excluded)
        assert all("-12 ms" in reason for reason in excluded)

    def test_the_same_corpus_without_the_reading_keeps_them(self) -> None:
        # The control, and the bug this fixes: a serve run that never opened
        # osu!.db assumed zero everywhere and pooled the nudged map in silently.
        state = CorpusState()
        feed(state, beatmap="map-a")
        feed(state, beatmap="map-b", seed=9)
        answer = state.recompute()
        assert answer["replays"] == 24
        assert not answer["excluded"]

    def test_read_and_all_zero_is_not_the_same_claim_as_unread(self) -> None:
        unknown = CorpusState()
        feed(unknown)
        known = CorpusState(offsets={})
        feed(known)
        assert unknown.recompute()["local_offsets_known"] is False
        assert known.recompute()["local_offsets_known"] is True


class TestArrival:
    def test_dwell_pairs_become_the_third_axis(self) -> None:
        state = CorpusState()
        feed(state, mean=6.0, dwell=2.0)
        answer = state.recompute()
        arrival = next(axis for axis in answer["axes"] if axis["name"] == "arrival")
        # Of a 6 ms mean error, 2 ms is the wait after the cursor arrived, so
        # the cursor was on target 4 ms after the object was due.
        assert "4 ms" in arrival["verdict"]
        assert arrival["actionable"] is False
        json.dumps(answer, allow_nan=False)

    def test_a_corpus_without_dwell_keeps_the_two_axes_it_had(self) -> None:
        state = CorpusState()
        feed(state, mean=6.0)
        names = [axis["name"] for axis in state.recompute()["axes"]]
        assert names == ["timing bias", "timing spread"]


class TestFacts:
    """The JSON-safe reduction — the shape the analysis cache persists."""

    FACTS: typing.ClassVar[dict[str, object]] = {
        "beatmap_hash": "map-a",
        "beatmap": "Artist - Title [map-a]",
        "played_at": "2026-08-01T19:00:00+00:00",
        "errors": [1.5, -2.25, 0.5],
        "miss_rate": 0.02,
        "accuracy": 0.97,
        "breaks": 1,
        "agreement": "exact",
        "agreement_reason": "all judgements reproduce",
        "fractional_windows": False,
        # A dwell that was never measured travels as `null`: NaN has no JSON
        # spelling, and this dictionary is persisted as strict JSON.
        "arrival": [[1.5, 0.5], [-2.25, None], [0.5, 0.25]],
    }

    def test_add_facts_is_add_with_the_types_restored(self) -> None:
        direct = CorpusState()
        direct.add(
            "a.osr",
            beatmap_hash="map-a",
            beatmap="Artist - Title [map-a]",
            played_at=datetime(2026, 8, 1, 19, 0, tzinfo=UTC),
            errors=[1.5, -2.25, 0.5],
            miss_rate=0.02,
            accuracy=0.97,
            breaks=1,
            agreement=Agreement.EXACT,
            agreement_reason="all judgements reproduce",
            fractional_windows=False,
            arrival=[(1.5, 0.5), (-2.25, math.nan), (0.5, 0.25)],
        )
        from_facts = CorpusState()
        from_facts.add_facts("a.osr", dict(self.FACTS))
        assert direct.recompute() == from_facts.recompute()

    def test_a_missing_arrival_is_a_row_from_before_the_split(self) -> None:
        # An older cache row cannot grow one: the cursor frames it would need
        # were discarded when the play was reduced. Raising sends the caller
        # back to the replay file rather than serving a corpus whose third axis
        # covers only half its history.
        state = CorpusState()
        stale = dict(self.FACTS)
        del stale["arrival"]
        try:
            state.add_facts("a.osr", stale)
        except KeyError:
            pass
        else:
            raise AssertionError("a row without arrival pairs must raise")
        assert len(state) == 0

    def test_a_malformed_arrival_raises_rather_than_being_guessed_at(self) -> None:
        state = CorpusState()
        try:
            state.add_facts("a.osr", {**self.FACTS, "arrival": [1.5, 0.5]})
        except TypeError:
            pass
        else:
            raise AssertionError("pairs that are not pairs must raise")
        assert len(state) == 0

    def test_a_malformed_dictionary_raises_rather_than_poisoning(self) -> None:
        # The caller treats this as a cache miss and re-analyses. Silently
        # coercing a wrong shape would put invented numbers in the corpus.
        state = CorpusState()
        broken = dict(self.FACTS)
        del broken["errors"]
        try:
            state.add_facts("a.osr", broken)
        except KeyError:
            pass
        else:
            raise AssertionError("a missing field must raise")
        assert len(state) == 0

    def test_an_unknown_agreement_raises(self) -> None:
        state = CorpusState()
        try:
            state.add_facts("a.osr", {**self.FACTS, "agreement": "probably"})
        except ValueError:
            pass
        else:
            raise AssertionError("an unknown agreement must raise")


class TestCaching:
    def test_an_unchanged_corpus_is_not_recomputed(self) -> None:
        state = CorpusState()
        feed(state)
        first = state.recompute()
        # Identity, not equality: the second call must return without running
        # the statistics, and handing back the same object is the proof.
        assert state.recompute() is first

    def test_a_new_play_invalidates_the_answer(self) -> None:
        state = CorpusState()
        feed(state)
        first = state.recompute()
        rng = np.random.default_rng(7)
        state.add(
            "late-arrival.osr",
            beatmap_hash="map-a",
            beatmap="Artist - Title [map-a]",
            played_at=START + timedelta(days=9),
            errors=rng.normal(6.0, 12.0, 120).tolist(),
            miss_rate=0.02,
            accuracy=0.97,
            breaks=0,
            agreement=Agreement.CLOSE,
            agreement_reason="grades differ by 1, within the 2 this map allows.",
            fractional_windows=True,
        )
        fresh = state.recompute()
        assert fresh is not first
        assert fresh["replays"] == 13
        assert fresh["sessions"] == 5
        assert state.payload() is fresh

    def test_payload_is_none_only_before_the_first_recompute(self) -> None:
        state = CorpusState()
        assert state.payload() is None
        state.recompute()
        assert state.payload() is not None
