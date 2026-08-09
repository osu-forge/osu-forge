"""Landing errors, rotated into the frame of the movement that produced them.

The sign conventions are the point of this file. An inverted along-track axis
turns overshoot into undershoot and reverses every recommendation built on it,
and the arithmetic looks identical either way — this project has already shipped
one inverted angle convention and reported the opposite conclusion twice before
catching it.
"""

from __future__ import annotations

import math

import osu_forge_diffcalc as diffcalc
import pytest

from osuforge.analysis.cursor import LandingSet, Sector, decompose
from osuforge.replay.model import Keys
from osuforge.replay.parse import parse
from osuforge.replay.simulate import simulate

from .replay_fixtures import build_replay

# The two marker frames leave the clock at -1 ms, so the first real delta is one
# more than its timestamp.
MARKERS: list[tuple[int, float, float, int]] = [
    (0, 256.0, -500.0, 0),
    (-1, 256.0, -500.0, 0),
]
K1 = int(Keys.K1 | Keys.M1)

# CS 4 at OD 8; positions and times are set per test.
HEADER = (
    "osu file format v14\n"
    "[General]\nMode: 0\nStackLeniency: 0.7\n"
    "[Difficulty]\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\nSliderMultiplier:1.4\n"
    "[TimingPoints]\n0,500,4,2,0,100,1,0\n"
    "[HitObjects]\n"
)


def beatmap(objects: list[tuple[float, float, int]]) -> diffcalc.Beatmap:
    body = "".join(f"{x},{y},{t},1,0\n" for x, y, t in objects)
    return diffcalc.Beatmap.from_bytes((HEADER + body).encode())


def landings(
    objects: list[tuple[float, float, int]],
    samples: list[tuple[int, float, float, int]],
) -> LandingSet:
    body = list(MARKERS)
    previous = -1
    for time, x, y, keys in samples:
        body.append((time - previous, x, y, keys))
        previous = time
    played = beatmap(objects)
    return decompose(simulate(parse(build_replay(frames=body)), played), played)


class TestSignConventions:
    """Each of these would still pass with the axis inverted if it only checked
    a magnitude, so every one checks a sign against a hand-built case."""

    def test_landing_past_the_centre_is_positive_along_track(self) -> None:
        # Travelling right from x=100 to x=300, cursor lands at 310: ten pixels
        # beyond the target, in the direction it was going. Overshoot.
        result = landings(
            [(100, 192, 1000), (300, 192, 1500)],
            [(900, 100, 192, 0), (1000, 100, 192, K1), (1400, 250, 192, 0), (1500, 310, 192, K1)],
        )
        assert len(result.landings) == 1
        assert result.landings[0].along == pytest.approx(10.0)

    def test_landing_short_of_the_centre_is_negative(self) -> None:
        result = landings(
            [(100, 192, 1000), (300, 192, 1500)],
            [(900, 100, 192, 0), (1000, 100, 192, K1), (1400, 250, 192, 0), (1500, 288, 192, K1)],
        )
        assert result.landings[0].along == pytest.approx(-12.0)

    def test_the_sign_does_not_depend_on_which_way_the_jump_went(self) -> None:
        # Travelling left this time. Overshoot is still positive, which is the
        # whole reason the error is rotated rather than taken per axis.
        result = landings(
            [(300, 192, 1000), (100, 192, 1500)],
            [(900, 300, 192, 0), (1000, 300, 192, K1), (1400, 150, 192, 0), (1500, 90, 192, K1)],
        )
        assert result.landings[0].along == pytest.approx(10.0)
        assert result.landings[0].dx == pytest.approx(-10.0), "raw dx is the opposite sign"

    def test_a_vertical_jump_behaves_the_same(self) -> None:
        result = landings(
            [(256, 100, 1000), (256, 300, 1500)],
            [(900, 256, 100, 0), (1000, 256, 100, K1), (1400, 256, 250, 0), (1500, 256, 315, K1)],
        )
        assert result.landings[0].along == pytest.approx(15.0)
        assert result.landings[0].across == pytest.approx(0.0)

    def test_cross_track_is_clockwise_positive_on_screen(self) -> None:
        # Travelling right, so clockwise on screen is downward — and y grows
        # downward, so a cursor below the line has positive cross-track.
        result = landings(
            [(100, 192, 1000), (300, 192, 1500)],
            [(900, 100, 192, 0), (1000, 100, 192, K1), (1400, 250, 192, 0), (1500, 300, 210, K1)],
        )
        assert result.landings[0].across == pytest.approx(18.0)
        assert result.landings[0].along == pytest.approx(0.0)

    def test_the_two_components_reconstruct_the_raw_error(self) -> None:
        result = landings(
            [(120, 140, 1000), (300, 260, 1500)],
            [(900, 120, 140, 0), (1000, 120, 140, K1), (1400, 250, 220, 0), (1500, 311, 253, K1)],
        )
        landing = result.landings[0]
        assert math.hypot(landing.along, landing.across) == pytest.approx(landing.distance)


class TestSectors:
    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            ((400, 192), Sector.RIGHT),
            ((400, 300), Sector.DOWN_RIGHT),
            ((256, 330), Sector.DOWN),
            ((120, 300), Sector.DOWN_LEFT),
            ((100, 192), Sector.LEFT),
            ((120, 80), Sector.UP_LEFT),
            ((256, 60), Sector.UP),
            ((400, 80), Sector.UP_RIGHT),
        ],
    )
    def test_the_compass_points_match_what_is_seen_on_screen(
        self, target: tuple[float, float], expected: Sector
    ) -> None:
        # y grows downward, so "down" is toward the bottom of the playfield —
        # the movement a player would call top-to-bottom, not a positive step on
        # a mathematical axis.
        x, y = target
        result = landings(
            [(256, 192, 1000), (x, y, 1500)],
            [(900, 256, 192, 0), (1000, 256, 192, K1), (1400, x, y, 0), (1500, x, y, K1)],
        )
        assert result.landings[0].sector is expected


class TestExclusions:
    def test_the_first_object_has_no_direction_and_is_counted(self) -> None:
        result = landings(
            [(100, 192, 1000), (300, 192, 1500)],
            [(900, 100, 192, 0), (1000, 100, 192, K1), (1400, 250, 192, 0), (1500, 300, 192, K1)],
        )
        assert result.skipped["no previous object"] == 1
        assert "skipped" in result.summary()

    def test_objects_on_the_same_spot_are_excluded_rather_than_given_a_direction(self) -> None:
        # Normalising a one-pixel vector produces a direction that describes
        # rounding rather than where the hand went.
        result = landings(
            [(200, 192, 1000), (200, 192, 1500)],
            [(900, 200, 192, 0), (1000, 200, 192, K1), (1400, 200, 192, 0), (1500, 200, 192, K1)],
        )
        assert result.landings == []
        assert result.skipped["stacked or repeated on one spot"] == 1

    def test_a_miss_has_no_press_to_measure(self) -> None:
        result = landings(
            [(100, 192, 1000), (300, 192, 1500)],
            [(900, 100, 192, 0), (1000, 100, 192, K1), (1400, 250, 192, 0)],
        )
        assert result.landings == []
        assert result.skipped["no press"] == 1

    def test_an_unmeasurable_object_still_serves_as_the_next_origin(self) -> None:
        # The player travelled from it whether or not its own landing could be
        # measured, so dropping it as an origin would mis-state the next jump.
        result = landings(
            [(100, 192, 1000), (200, 192, 1500), (400, 192, 2000)],
            [
                (900, 100, 192, 0),
                (1000, 100, 192, K1),
                (1400, 200, 192, 0),
                (1500, 200, 192, K1),
                (1900, 300, 192, 0),
                (2000, 400, 192, K1),
            ],
        )
        assert [landing.travel for landing in result.landings] == [
            pytest.approx(100.0),
            pytest.approx(200.0),
        ]


class TestSliderOrigins:
    """The movement to the next object starts from the tail, not the head."""

    def sliders(self, objects: str, samples: list[tuple[int, float, float, int]]) -> LandingSet:
        text = (
            "osu file format v14\n"
            "[General]\nMode: 0\nStackLeniency: 0.7\n"
            "[Difficulty]\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\n"
            "SliderMultiplier:1\nSliderTickRate:1\n"
            "[TimingPoints]\n0,500,4,2,0,100,1,0\n"
            "[HitObjects]\n" + objects
        )
        body = list(MARKERS)
        previous = -1
        for time, x, y, keys in samples:
            body.append((time - previous, x, y, keys))
            previous = time
        played = diffcalc.Beatmap.from_bytes(text.encode())
        return decompose(simulate(parse(build_replay(frames=body)), played), played)

    def test_travel_starts_where_the_slider_ended(self) -> None:
        # A 200 px slider from x=100 to x=300, then a circle at x=400. The
        # player travels 100 px from the tail, not 300 px from the head, and
        # measuring it from the head would report a jump three times as long.
        result = self.sliders(
            "100,192,1000,2,0,L|300:192,1,200\n400,192,3000,1,0\n",
            [
                (1000, 100, 192, K1),
                (1500, 200, 192, K1),
                (2000, 300, 192, K1),
                (2100, 300, 192, 0),
                (3000, 400, 192, K1),
            ],
        )
        assert len(result.landings) == 1
        assert result.landings[0].travel == pytest.approx(100.0, abs=1.0)

    def test_an_even_slide_count_comes_back_to_the_head(self) -> None:
        # Two traversals end where they started, so the movement is the full
        # distance from the head after all. Getting this backwards misplaces
        # the origin of every movement after a repeat slider.
        result = self.sliders(
            "100,192,1000,2,0,L|300:192,2,200\n400,192,4000,1,0\n",
            [
                (1000, 100, 192, K1),
                (2000, 300, 192, K1),
                (3000, 100, 192, K1),
                (3100, 100, 192, 0),
                (4000, 400, 192, K1),
            ],
        )
        assert len(result.landings) == 1
        assert result.landings[0].travel == pytest.approx(300.0, abs=1.0)

    def test_the_clock_starts_at_the_tail_too(self) -> None:
        # A long slider does not hand the next object its whole duration as
        # travel time. Measuring from the start time overstates the time
        # available by the length of the slider.
        result = self.sliders(
            "100,192,1000,2,0,L|300:192,1,200\n400,192,3000,1,0\n",
            [
                (1000, 100, 192, K1),
                (1500, 200, 192, K1),
                (2000, 300, 192, K1),
                (2100, 300, 192, 0),
                (3000, 400, 192, K1),
            ],
        )
        landing = result.landings[0]
        assert landing.available == pytest.approx(1000.0, abs=60.0), "3000 - the tail, not - 1000"
        assert landing.available < 1500.0, "not the full 2000 ms from the slider's start"


class TestKinematics:
    def test_travel_and_time_come_from_the_objects_not_the_cursor(self) -> None:
        result = landings(
            [(100, 192, 1000), (300, 192, 1500)],
            [(900, 100, 192, 0), (1000, 100, 192, K1), (1400, 250, 192, 0), (1500, 305, 192, K1)],
        )
        landing = result.landings[0]
        assert landing.travel == pytest.approx(200.0), "centre to centre, not cursor to cursor"
        assert landing.available == pytest.approx(500.0)
        assert landing.duration_speed == pytest.approx(0.4)

    def test_peak_speed_is_the_fastest_step_not_the_average(self) -> None:
        result = landings(
            [(100, 192, 1000), (300, 192, 1500)],
            [
                (1000, 100, 192, K1),
                (1100, 110, 192, 0),
                (1200, 260, 192, 0),
                (1500, 300, 192, K1),
            ],
        )
        landing = result.landings[0]
        assert landing.peak_speed == pytest.approx(1.5), "150 px in 100 ms"
        assert landing.mean_speed < landing.peak_speed

    def test_dwell_measures_how_long_the_cursor_waited_on_target(self) -> None:
        # Arrived at 1300, clicked at 1500: the hand was on the object for
        # 200 ms before the press, so a late hit here is not an aim problem and
        # no offset change would fix the part that is.
        result = landings(
            [(100, 192, 1000), (300, 192, 1500)],
            [
                (1000, 100, 192, K1),
                (1200, 200, 192, 0),
                (1300, 300, 192, 0),
                (1400, 300, 192, 0),
                (1500, 300, 192, K1),
            ],
        )
        assert result.landings[0].dwell == pytest.approx(200.0)

    def test_a_cursor_already_on_target_has_no_arrival_to_measure(self) -> None:
        # Distinguished from "arrived instantly", which is a real and different
        # observation; collapsing them would report a dwell of zero for a hand
        # that never moved.
        result = landings(
            [(100, 192, 1000), (300, 192, 1500)],
            [(1000, 300, 192, K1), (1200, 300, 192, 0), (1500, 300, 192, K1)],
        )
        assert result.landings[0].dwell is None
