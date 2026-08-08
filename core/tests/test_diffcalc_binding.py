"""The Rust beatmap layer, seen from Python.

The point of these is the boundary, not the beatmap logic — that is tested in
Rust, against a real Songs folder, where it belongs. What can only be checked
here is that the two languages agree: the same mod bit means the same mod, a
parse failure arrives as an exception rather than as a crash, and the geometry
that comes across matches the formulas the analysis side reasons with.
"""

from __future__ import annotations

import math

import osu_forge_diffcalc as diffcalc
import pytest

from osuforge.replay.model import Mods

MINIMAL = (
    "osu file format v14\n"
    "[General]\nMode: 0\nStackLeniency: 0.7\n"
    "[Difficulty]\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\nSliderMultiplier:1.4\n"
    "[TimingPoints]\n0,500,4,2,0,100,1,0\n"
    "[HitObjects]\n"
    "100,100,1000,1,0\n"
    "100,100,1100,1,0\n"
    "200,200,2000,2,0,L|300:200,1,100\n"
    "300,300,3000,12,0,4000\n"
)


def beatmap(text: str = MINIMAL) -> diffcalc.Beatmap:
    return diffcalc.Beatmap.from_bytes(text.encode())


class TestModTable:
    """The one place two hand-maintained copies of a constant table can be pinned.

    Rust needs the bits to adjust difficulty, Python needs them to read a `.osr`
    header, and neither can import the other's. A drift would show up as a
    difficulty setting that is wrong only under Hard Rock, which is exactly the
    kind of thing that survives for months.
    """

    def test_every_exported_bit_matches_the_python_enum(self) -> None:
        for name, value in diffcalc.MODS.items():
            assert name in Mods.__members__, f"Rust exports {name}, Python has no such mod"
            assert Mods[name].value == value, (
                f"{name}: Rust says {value:#x}, Python says {Mods[name].value:#x}"
            )

    def test_the_table_is_not_empty_by_accident(self) -> None:
        # A binding that failed to populate MODS would pass the check above
        # vacuously.
        assert len(diffcalc.MODS) >= 15
        assert diffcalc.MODS["HARD_ROCK"] == 1 << 4


class TestParsing:
    def test_objects_arrive_in_order_with_their_kinds(self) -> None:
        objects = beatmap().objects
        assert [o.time for o in objects] == [1000, 1100, 2000, 3000]
        assert [o.kind for o in objects] == [
            diffcalc.ObjectKind.Circle,
            diffcalc.ObjectKind.Circle,
            diffcalc.ObjectKind.Slider,
            diffcalc.ObjectKind.Spinner,
        ]

    def test_a_circle_ends_when_it_starts_and_a_slider_does_not(self) -> None:
        objects = beatmap().objects
        assert objects[0].end_time == 1000.0
        # length 100 / (1.4 * 100 * 1.0) * 500 ms = 357.14 ms
        assert objects[2].end_time == pytest.approx(2357.142857, abs=1e-6)
        assert objects[3].end_time == 4000.0

    def test_a_file_for_another_ruleset_raises_rather_than_returning_nonsense(self) -> None:
        with pytest.raises(diffcalc.BeatmapError, match="game mode 3"):
            diffcalc.Beatmap.from_bytes(b"osu file format v14\n[General]\nMode: 3\n")

    def test_a_missing_file_names_itself(self) -> None:
        with pytest.raises(diffcalc.BeatmapError, match="nonexistent"):
            diffcalc.Beatmap.from_file("nonexistent.osu")


class TestGeometry:
    def test_radius_matches_the_circle_size_formula(self) -> None:
        # The community form of stable's radius is 54.4 - 4.48 * CS, which is the
        # radius *before* stable's playfield fudge factor. The binding computes
        # it the other way round — 64 * scale, with the scale in f32 — so this
        # checks the two arrive at the same number rather than restating one.
        for circle_size in (0.0, 3.0, 4.0, 5.0, 7.0, 10.0):
            text = MINIMAL.replace("CircleSize:4", f"CircleSize:{circle_size}")
            expected = (54.4 - 4.48 * circle_size) * 1.00041
            assert beatmap(text).radius == pytest.approx(expected, rel=1e-6)

    def test_the_stable_fudge_factor_is_applied(self) -> None:
        # 0.041% — far too small to change a judgement, and included for fidelity
        # rather than because it matters. Asserted separately so that dropping it
        # is a deliberate act rather than something absorbed by a tolerance.
        assert beatmap().radius > 64.0 * 0.57

    def test_hit_windows_come_from_overall_difficulty(self) -> None:
        b = beatmap()
        assert b.hit_window_300 == pytest.approx(80 - 6 * 8)
        assert b.hit_window_100 == pytest.approx(140 - 8 * 8)
        assert b.hit_window_50 == pytest.approx(200 - 10 * 8)

    def test_preempt_is_piecewise_around_approach_rate_five(self) -> None:
        def preempt(approach_rate: float) -> float:
            return beatmap(
                MINIMAL.replace("ApproachRate:9", f"ApproachRate:{approach_rate}")
            ).preempt

        assert preempt(5.0) == pytest.approx(1200.0)
        assert preempt(0.0) == pytest.approx(1800.0)
        assert preempt(10.0) == pytest.approx(450.0)
        # The two halves have different slopes. A single interpolation from 1800
        # to 450 would give 1462.5 here.
        assert preempt(2.5) == pytest.approx(1500.0)

    def test_stacking_moves_the_judged_position_but_not_the_file_position(self) -> None:
        first = beatmap().objects[0]
        assert first.stack_height == 1
        assert (first.raw_x, first.raw_y) == (100.0, 100.0)
        assert first.x < 100.0 and first.y < 100.0
        assert math.isclose(first.x, first.y), "the stack offset is equal in both axes"

    def test_an_unstacked_object_is_where_the_file_says(self) -> None:
        second = beatmap().objects[1]
        assert second.stack_height == 0
        assert (second.x, second.y) == (second.raw_x, second.raw_y)


class TestMods:
    def test_hard_rock_tightens_everything_and_caps_at_ten(self) -> None:
        hard_rock = beatmap().with_mods(diffcalc.MODS["HARD_ROCK"])
        assert hard_rock.circle_size == pytest.approx(5.2)
        assert hard_rock.approach_rate == 10.0
        assert hard_rock.overall_difficulty == 10.0
        assert hard_rock.hit_window_300 == pytest.approx(20.0)

    def test_double_time_leaves_the_beatmap_alone(self) -> None:
        # It changes the clock, not the map. Applying it here as well would count
        # the same speed increase twice.
        plain = beatmap()
        fast = plain.with_mods(diffcalc.MODS["DOUBLE_TIME"])
        assert fast.overall_difficulty == plain.overall_difficulty
        assert fast.approach_rate == plain.approach_rate
        assert [o.time for o in fast.objects] == [o.time for o in plain.objects]
        # The rate belongs to the replay side, which already knows it.
        assert Mods(diffcalc.MODS["DOUBLE_TIME"]).rate == 1.5

    def test_applying_mods_does_not_mutate_the_original(self) -> None:
        plain = beatmap()
        plain.with_mods(diffcalc.MODS["HARD_ROCK"])
        assert plain.circle_size == 4.0
