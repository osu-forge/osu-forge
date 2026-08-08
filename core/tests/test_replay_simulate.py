"""The hit simulator and the screen that decides whether to believe it."""

from __future__ import annotations

import os
from pathlib import Path

import osu_forge_diffcalc as diffcalc
import pytest

from osuforge.replay.model import Judgements, Keys
from osuforge.replay.parse import parse, parse_path
from osuforge.replay.simulate import Grade, Simulation, simulate
from osuforge.replay.validate import Agreement, corpus_health, validate

from .replay_fixtures import build_replay

MARKERS: list[tuple[int, float, float, int]] = [
    (0, 256.0, -500.0, 0),
    (-1, 256.0, -500.0, 0),
]
K1 = int(Keys.K1 | Keys.M1)

# CS 4 gives a 36.5 px radius; OD 8 gives windows of 32 / 76 / 120 ms.
TWO_CIRCLES = (
    "osu file format v14\n"
    "[General]\nMode: 0\nStackLeniency: 0.7\n"
    "[Difficulty]\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\nSliderMultiplier:1\n"
    "[TimingPoints]\n0,500,4,2,0,100,1,0\n"
    "[HitObjects]\n100,100,1000,1,0\n300,100,2000,1,0\n"
)

ONE_SLIDER = (
    "osu file format v14\n"
    "[General]\nMode: 0\nStackLeniency: 0.7\n"
    "[Difficulty]\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\n"
    "SliderMultiplier:1\nSliderTickRate:1\n"
    "[TimingPoints]\n0,500,4,2,0,100,1,0\n"
    "[HitObjects]\n0,0,1000,2,0,L|300:0,1,300\n"
)


def beatmap(text: str) -> diffcalc.Beatmap:
    return diffcalc.Beatmap.from_bytes(text.encode())


def run(
    text: str,
    samples: list[tuple[int, float, float, int]],
    *,
    mods: int = 0,
) -> Simulation:
    """Simulate a replay built from absolute-time samples.

    The two marker frames leave the clock at -1 ms, so the first real delta is
    one more than its timestamp. Getting that wrong shifts every error by a
    millisecond, which is small enough to look like a result.
    """
    body = list(MARKERS)
    previous = -1
    for time, x, y, keys in samples:
        body.append((time - previous, x, y, keys))
        previous = time
    replay = parse(build_replay(frames=body, mods=mods))
    return simulate(replay, beatmap(text))


class TestTiming:
    def test_a_press_on_the_object_scores_by_its_error(self) -> None:
        for offset, grade in [(0, Grade.THREE_HUNDRED), (40, Grade.HUNDRED), (100, Grade.FIFTY)]:
            result = run(TWO_CIRCLES, [(1000 + offset, 100.0, 100.0, K1)])
            assert result.hits[0].grade is grade, offset
            assert result.hits[0].error == offset

    def test_early_is_negative(self) -> None:
        result = run(TWO_CIRCLES, [(960, 100.0, 100.0, K1)])
        assert result.hits[0].error == -40
        assert result.hits[0].grade is Grade.HUNDRED

    def test_the_windows_are_the_rounded_ones_actually_used(self) -> None:
        result = run(TWO_CIRCLES, [(1000, 100.0, 100.0, K1)])
        assert result.windows == (32, 76, 120)
        assert not result.fractional_windows

    def test_a_fractional_window_is_reported(self) -> None:
        # OD 8.3 gives a 30.2 ms 300 window, where the unverified rounding rule
        # could matter. The flag is what lets a corpus say whether it ever did.
        text = TWO_CIRCLES.replace("OverallDifficulty:8", "OverallDifficulty:8.3")
        assert run(text, [(1000, 100.0, 100.0, K1)]).fractional_windows

    def test_a_press_past_the_fifty_window_leaves_a_miss(self) -> None:
        result = run(TWO_CIRCLES, [(1120, 100.0, 100.0, K1)])
        assert result.hits[0].grade is Grade.MISS
        assert result.hits[0].error is None
        assert result.presses_out_of_window == 1

    def test_double_time_leaves_map_time_alone_and_scales_the_report(self) -> None:
        result = run(TWO_CIRCLES, [(1030, 100.0, 100.0, K1)], mods=diffcalc.MODS["DOUBLE_TIME"])
        assert result.rate == 1.5
        assert result.hits[0].error == 30, "map time is unchanged"
        # 30 map milliseconds is 20 real ones.
        assert result.timing_errors() == [pytest.approx(20.0)]


class TestPosition:
    def test_a_press_away_from_the_object_does_not_judge_it(self) -> None:
        # Perfect timing, cursor elsewhere. Without this the simulator turns
        # every mistimed attempt into a hit.
        result = run(TWO_CIRCLES, [(1000, 300.0, 300.0, K1)])
        assert result.hits[0].grade is Grade.MISS
        assert result.presses_off_target == 1

    def test_an_object_survives_a_press_that_missed_it(self) -> None:
        result = run(
            TWO_CIRCLES,
            [
                (990, 300.0, 300.0, K1),
                (995, 300.0, 300.0, 0),
                (1010, 100.0, 100.0, K1),
            ],
        )
        assert result.hits[0].grade is Grade.THREE_HUNDRED
        assert result.presses_off_target == 1

    def test_aim_error_is_the_distance_in_radii(self) -> None:
        result = run(TWO_CIRCLES, [(1000, 100.0 + 18.0, 100.0, K1)])
        hit = result.hits[0]
        assert hit.aim_error is not None
        assert hit.aim_error == pytest.approx(18.0 / 36.495, rel=1e-3)

    def test_a_press_at_the_very_edge_still_counts(self) -> None:
        result = run(TWO_CIRCLES, [(1000, 100.0 + 36.4, 100.0, K1)])
        assert result.hits[0].grade is Grade.THREE_HUNDRED


class TestOrdering:
    def test_a_missed_object_does_not_swallow_the_next_press(self) -> None:
        result = run(
            TWO_CIRCLES,
            [(2000, 300.0, 100.0, K1)],
        )
        assert result.hits[0].grade is Grade.MISS
        assert result.hits[1].grade is Grade.THREE_HUNDRED
        assert result.hits[1].error == 0

    def test_a_press_before_the_first_window_goes_nowhere(self) -> None:
        # Notelock: it is too early for the earliest open object, and therefore
        # too early for every object after it. It must not be handed forward.
        result = run(TWO_CIRCLES, [(500, 300.0, 100.0, K1)])
        assert result.presses_out_of_window == 1
        assert all(hit.grade is Grade.MISS for hit in result.hits)

    def test_one_press_judges_one_object(self) -> None:
        result = run(
            TWO_CIRCLES,
            [
                (1000, 100.0, 100.0, K1),
                (1005, 100.0, 100.0, 0),
                (1010, 300.0, 100.0, K1),
            ],
        )
        assert result.hits[0].grade is Grade.THREE_HUNDRED
        # The second press is far too early for the object at 2000.
        assert result.hits[1].grade is Grade.MISS
        assert result.presses_out_of_window == 1


class TestSliders:
    def parts_of(self) -> list[diffcalc.SliderPart]:
        return list(beatmap(ONE_SLIDER).objects[0].parts)

    def test_the_fixture_has_the_parts_this_class_assumes(self) -> None:
        kinds = [part.kind for part in self.parts_of()]
        assert kinds == [
            diffcalc.PartKind.Tick,
            diffcalc.PartKind.Tick,
            diffcalc.PartKind.Tail,
        ]

    def test_following_every_part_scores_three_hundred(self) -> None:
        samples = [(1000, 0.0, 0.0, K1)]
        samples += [(int(part.time), part.x, part.y, K1) for part in self.parts_of()]
        result = run(ONE_SLIDER, samples)
        assert result.hits[0].grade is Grade.THREE_HUNDRED
        assert not result.hits[0].grade_inferred

    def test_releasing_immediately_drops_the_slider(self) -> None:
        samples = [(1000, 0.0, 0.0, K1), (1005, 0.0, 0.0, 0)]
        samples += [(int(part.time), part.x, part.y, 0) for part in self.parts_of()]
        result = run(ONE_SLIDER, samples)
        # The head alone, out of four parts.
        assert result.hits[0].grade is Grade.FIFTY

    def test_holding_but_leaving_the_path_drops_it(self) -> None:
        samples = [(1000, 0.0, 0.0, K1)]
        samples += [(int(part.time), 400.0, 400.0, K1) for part in self.parts_of()]
        assert run(ONE_SLIDER, samples).hits[0].grade is Grade.FIFTY

    def test_half_the_parts_scores_one_hundred(self) -> None:
        parts = self.parts_of()
        samples = [(1000, 0.0, 0.0, K1), (int(parts[0].time), parts[0].x, parts[0].y, K1)]
        samples += [(int(part.time), 400.0, 400.0, K1) for part in parts[1:]]
        # Two of four: at least half, which the corpus rules out being "more
        # than half" — that would move over a thousand objects to 50.
        assert run(ONE_SLIDER, samples).hits[0].grade is Grade.HUNDRED

    def test_a_slider_can_score_without_its_head(self) -> None:
        samples = [(int(part.time), part.x, part.y, K1) for part in self.parts_of()]
        result = run(ONE_SLIDER, samples)
        assert result.hits[0].grade is Grade.HUNDRED
        assert result.hits[0].error is None, "no press judged the head"

    def test_a_slider_touched_nowhere_is_a_miss(self) -> None:
        assert run(ONE_SLIDER, [(1000, 400.0, 400.0, 0)]).hits[0].grade is Grade.MISS


class TestSpinners:
    SPINNER = (
        "osu file format v14\n"
        "[General]\nMode: 0\n[Difficulty]\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\n"
        "[HitObjects]\n256,192,1000,12,0,3000\n"
    )

    def test_a_spinner_is_assumed_cleared_and_says_so(self) -> None:
        result = run(self.SPINNER, [(1000, 256.0, 192.0, K1)])
        assert result.hits[0].grade is Grade.THREE_HUNDRED
        assert result.hits[0].grade_inferred, "nothing about spinning is modelled"

    def test_a_press_during_a_spinner_is_not_matched_to_it(self) -> None:
        assert run(self.SPINNER, [(2000, 256.0, 192.0, K1)]).presses_out_of_window == 1


class TestExclusions:
    def test_slider_heads_and_spinners_are_excluded_from_timing(self) -> None:
        samples = [(1000, 0.0, 0.0, K1)]
        result = run(ONE_SLIDER, samples)
        assert result.hits[0].excluded == "slider_head"
        assert result.hits[0].error == 0, "the error is still measured"
        assert not result.hits[0].usable_for_timing
        assert result.timing_errors() == []

    def test_the_first_object_after_a_long_gap_is_excluded(self) -> None:
        text = (
            "osu file format v14\n"
            "[General]\nMode: 0\n"
            "[Difficulty]\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\n"
            "[HitObjects]\n100,100,1000,1,0\n300,100,4000,1,0\n"
        )
        result = run(
            text,
            [
                (1000, 100.0, 100.0, K1),
                (1005, 100.0, 100.0, 0),
                (4000, 300.0, 100.0, K1),
            ],
        )
        assert result.hits[0].excluded is None
        assert result.hits[1].excluded == "after_break"
        assert result.exclusions() == {"after_break": 1}


class TestValidation:
    def judgements(self, count_300: int, count_100: int, count_50: int, count_miss: int):
        return Judgements(count_300, count_100, count_50, 0, 0, count_miss)

    def simulation(self, text: str, samples: list[tuple[int, float, float, int]]) -> Simulation:
        return run(text, samples)

    def test_a_reproduced_replay_is_exact(self) -> None:
        result = self.simulation(
            TWO_CIRCLES,
            [
                (1000, 100.0, 100.0, K1),
                (1005, 100.0, 100.0, 0),
                (2000, 300.0, 100.0, K1),
            ],
        )
        check = validate(result, self.judgements(2, 0, 0, 0))
        assert check.agreement is Agreement.EXACT
        assert check.differences == {}

    def test_a_different_object_count_is_the_wrong_beatmap(self) -> None:
        result = self.simulation(TWO_CIRCLES, [(1000, 100.0, 100.0, K1)])
        check = validate(result, self.judgements(500, 0, 0, 0))
        assert check.agreement is Agreement.MISMATCH
        assert "wrong beatmap" in check.reason

    def test_more_circle_misses_than_the_game_had_misses_at_all(self) -> None:
        # Both objects missed here, against a game that recorded one miss. A
        # circle's judgement is reproducible, so this cannot happen unless the
        # presses were paired differently.
        result = self.simulation(TWO_CIRCLES, [])
        check = validate(result, self.judgements(1, 0, 0, 1))
        assert check.agreement is Agreement.MISMATCH
        assert "impossible" in check.reason

    def test_a_small_grade_difference_is_within_the_slider_allowance(self) -> None:
        result = self.simulation(
            TWO_CIRCLES,
            [
                (1000, 100.0, 100.0, K1),
                (1005, 100.0, 100.0, 0),
                (2040, 300.0, 100.0, K1),
            ],
        )
        # Simulated 1x300 + 1x100; the game says 2x300. One object apart, and
        # the floor allowance is two even on a map with no sliders.
        check = validate(result, self.judgements(2, 0, 0, 0))
        assert check.agreement is Agreement.CLOSE
        assert check.differences == {"300": -1, "100": 1}

    def test_a_large_grade_difference_is_a_mismatch(self) -> None:
        # Ten circles, all hit dead on; a game that says every one was a 100.
        # A map with no sliders gets only the floor allowance of two, so ten
        # objects apart is past it. Two circles could never show this: the
        # largest disagreement possible on them is two.
        objects = "".join(f"100,100,{1000 + 500 * i},1,0\n" for i in range(10))
        text = TWO_CIRCLES.split("[HitObjects]")[0] + "[HitObjects]\n" + objects
        samples = []
        for i in range(10):
            samples.append((1000 + 500 * i, 100.0, 100.0, K1))
            samples.append((1005 + 500 * i, 100.0, 100.0, 0))
        result = self.simulation(text, samples)
        assert result.counts()[Grade.THREE_HUNDRED] == 10

        check = validate(result, self.judgements(0, 10, 0, 0))
        assert check.agreement is Agreement.MISMATCH
        assert check.allowance == 2, "no sliders, so only the floor"


class TestCorpusHealth:
    def health(self, exact: int, close: int, mismatched: int, **kwargs: object):
        results: list = []
        health = corpus_health(results, **kwargs)  # type: ignore[arg-type]
        return type(health)(
            total=exact + close + mismatched,
            exact=exact,
            close=close,
            mismatched=mismatched,
            fractional_window_replays=0,
            oracle_agreement=health.oracle_agreement,
        )

    def test_the_screen_alone_never_authorises_a_recommendation(self) -> None:
        health = self.health(50, 7, 0)
        assert health.screen_passed
        assert not health.may_recommend
        assert any("differential oracle has not been run" in b for b in health.blockers())

    def test_both_conditions_together_do(self) -> None:
        health = self.health(50, 7, 0, oracle_agreement=0.995)
        assert health.may_recommend
        assert health.blockers() == []

    def test_a_failing_screen_blocks_even_with_a_passing_oracle(self) -> None:
        health = self.health(10, 5, 42, oracle_agreement=1.0)
        assert not health.may_recommend
        assert any("below the 80% floor" in b for b in health.blockers())

    def test_an_oracle_that_disagrees_blocks(self) -> None:
        health = self.health(50, 7, 0, oracle_agreement=0.90)
        assert not health.may_recommend
        assert any("90.0% of objects" in b for b in health.blockers())

    def test_an_empty_corpus_recommends_nothing(self) -> None:
        health = corpus_health([])
        assert not health.may_recommend
        assert health.summary() == "no replays simulated"


@pytest.mark.integration
class TestAgainstRealReplays:
    """The screen over a real replay folder, opt-in via the environment.

    This is the measurement the module docstrings quote. It is here rather than
    in a notebook so that a change which quietly breaks the simulator shows up
    as a failing test on real data rather than as a slightly different number in
    a report nobody re-reads.
    """

    @pytest.fixture(scope="class")
    def corpus(self) -> list[tuple[Simulation, object]]:
        replays = os.environ.get("OSU_FORGE_REPLAYS")
        songs = os.environ.get("OSU_FORGE_SONGS")
        if not replays or not songs:
            pytest.skip("set OSU_FORGE_REPLAYS and OSU_FORGE_SONGS")

        import hashlib

        index: dict[str, Path] = {}
        for path in Path(songs).rglob("*.osu"):
            try:
                index.setdefault(hashlib.md5(path.read_bytes()).hexdigest(), path)
            except OSError:
                continue

        pairs = []
        for path in sorted(Path(replays).glob("*.osr")):
            try:
                replay = parse_path(path)
            except Exception:
                continue
            if not replay.usable_for_timing:
                continue
            beatmap_path = index.get(replay.beatmap_hash)
            if beatmap_path is None:
                continue
            result = simulate(replay, diffcalc.Beatmap.from_file(beatmap_path))
            pairs.append((result, validate(result, replay.judgements)))
        if not pairs:
            pytest.skip("no replay resolved to a local beatmap")
        return pairs

    def test_the_screen_passes_on_real_data(self, corpus) -> None:
        health = corpus_health(corpus)  # type: ignore[arg-type]
        assert health.screen_passed, health.summary()

    def test_no_replay_produces_more_circle_misses_than_the_game_had_misses(self, corpus) -> None:
        # The one-sided bound the screen rests on. It held across the whole
        # corpus even while slider scoring was still badly wrong, which is why
        # it is the part of the screen that is trusted.
        for result, check in corpus:
            assert result.circle_misses <= check.reported.count_miss, check.reason

    def test_presses_land_on_objects_far_more_often_than_not(self, corpus) -> None:
        presses = sum(r.presses for r, _ in corpus)
        unmatched = sum(r.unmatched_presses for r, _ in corpus)
        # Measured at 6.3% — 3.5% off target and 2.8% outside every window.
        # An order of magnitude more would mean the press list and the object
        # list had drifted apart.
        assert unmatched / presses < 0.15, f"{unmatched}/{presses}"

    def test_nothing_is_recommended_without_the_oracle(self, corpus) -> None:
        assert not corpus_health(corpus).may_recommend  # type: ignore[arg-type]
