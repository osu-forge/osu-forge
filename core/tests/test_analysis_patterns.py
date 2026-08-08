"""Attributing a play's accuracy loss to groups of objects."""

from __future__ import annotations

import math

import osu_forge_diffcalc as diffcalc
import pytest

from osuforge.analysis.patterns import (
    CONCENTRATION,
    MINIMUM_GROUP,
    Attribution,
    Share,
    attribute,
    contexts_for,
)
from osuforge.replay.frames import NormalizedFrames
from osuforge.replay.simulate import Grade, Hit, Simulation

HEADER = (
    "osu file format v14\n"
    "[General]\nMode: 0\nStackLeniency: 0.7\n"
    "[Difficulty]\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\nSliderMultiplier:1\n"
    "[TimingPoints]\n0,500,4,2,0,100,1,0\n"
    "[HitObjects]\n"
)


def beatmap(objects: str) -> diffcalc.Beatmap:
    return diffcalc.Beatmap.from_bytes((HEADER + objects).encode())


def simulation(grades: list[Grade]) -> Simulation:
    return Simulation(
        hits=[
            Hit(
                index=i,
                time=1000 + 200 * i,
                kind=diffcalc.ObjectKind.Circle,
                grade=g,
                grade_inferred=False,
            )
            for i, g in enumerate(grades)
        ],
        frames=NormalizedFrames(frames=[]),
        rate=1.0,
        windows=(32, 76, 120),
        fractional_windows=False,
    )


class TestContexts:
    def test_velocity_is_distance_over_time(self) -> None:
        # 200 px apart, 200 ms apart: 1 px/ms.
        contexts = contexts_for(beatmap("100,100,1000,1,0\n300,100,1200,1,0\n"))
        assert contexts[1].velocity == pytest.approx(1.0, rel=1e-3)

    def test_spacing_is_in_radii_so_circle_size_cancels(self) -> None:
        objects = "100,100,1000,1,0\n300,100,1200,1,0\n"
        small = contexts_for(beatmap(objects))
        wide = contexts_for(
            diffcalc.Beatmap.from_bytes(
                (HEADER.replace("CircleSize:4", "CircleSize:2") + objects).encode()
            )
        )
        # The same geometry is fewer radii apart when the circles are bigger.
        assert wide[1].spacing < small[1].spacing

    def test_a_straight_line_is_a_wide_angle_and_a_reversal_is_a_narrow_one(self) -> None:
        # osu!'s convention: the angle at the middle object, so a straight line
        # through it is pi and doubling back is zero. An earlier version measured
        # the angle between the movement vectors instead — the same quantity
        # subtracted from pi — and then called small values "sharp reversals",
        # which put every reversal in the data at 160 to 180 degrees and filed
        # them under the opposite label.
        straight = contexts_for(beatmap("100,100,1000,1,0\n200,100,1200,1,0\n300,100,1400,1,0\n"))
        assert straight[2].angle == pytest.approx(math.pi, abs=1e-6), "no turn at all"

        # The reversal does not return to the starting point. An earlier fixture
        # did, and the two coincident objects 400 ms apart stacked — correct
        # behaviour, which moved the third object two degrees off the line.
        back = contexts_for(beatmap("100,100,1000,1,0\n300,100,1200,1,0\n150,100,1400,1,0\n"))
        assert back[2].angle == pytest.approx(0.0, abs=1e-6), "straight back"

    def test_a_right_angle_reads_as_one(self) -> None:
        turn = contexts_for(beatmap("100,100,1000,1,0\n300,100,1200,1,0\n300,300,1400,1,0\n"))
        assert turn[2].angle == pytest.approx(math.pi / 2, abs=1e-6)

    def test_the_first_objects_have_no_angle_or_rhythm(self) -> None:
        contexts = contexts_for(beatmap("100,100,1000,1,0\n200,100,1200,1,0\n"))
        assert contexts[0].angle is None
        assert contexts[1].angle is None
        assert contexts[1].rhythm_ratio is None

    def test_an_angle_is_not_computed_across_a_long_gap(self) -> None:
        # The "turn" between a movement and a rest is not a turn.
        contexts = contexts_for(beatmap("100,100,1000,1,0\n200,100,1200,1,0\n100,100,9000,1,0\n"))
        assert contexts[2].angle is None
        assert contexts[2].rhythm_ratio is not None, "the rhythm change is still real"

    def test_rhythm_ratio_reports_a_doubling(self) -> None:
        contexts = contexts_for(beatmap("100,100,1000,1,0\n200,100,1100,1,0\n300,100,1300,1,0\n"))
        assert contexts[2].rhythm_ratio == pytest.approx(2.0, rel=0.05)

    def test_reading_load_counts_what_is_on_screen(self) -> None:
        # AR 9 is a 600 ms preempt, so four objects 100 ms apart overlap and the
        # last one sees all of them.
        contexts = contexts_for(
            beatmap("".join(f"100,100,{1000 + 100 * i},1,0\n" for i in range(4)))
        )
        assert contexts[3].visible == 4
        # Spread them past the preempt and each is alone.
        far = contexts_for(beatmap("".join(f"100,100,{1000 + 1000 * i},1,0\n" for i in range(4))))
        assert far[3].visible == 1


class TestAttribution:
    def objects(self, count: int) -> str:
        return "".join(f"{100 + (i % 3) * 50},100,{1000 + 200 * i},1,0\n" for i in range(count))

    def test_the_parts_add_to_the_whole(self) -> None:
        # The property that makes this arithmetic rather than inference. Each
        # feature partitions the play, so its groups' shares sum to one.
        count = 120
        grades = [Grade.THREE_HUNDRED] * 100 + [Grade.HUNDRED] * 15 + [Grade.MISS] * 5
        result = attribute(simulation(grades), beatmap(self.objects(count)))

        by_feature: dict[str, float] = {}
        for share in result.shares:
            by_feature[share.feature] = by_feature.get(share.feature, 0.0) + share.share_of_loss
        assert by_feature, "no groups were formed"
        for feature, total in by_feature.items():
            assert total == pytest.approx(1.0, abs=1e-9), feature

    def test_object_shares_also_partition(self) -> None:
        result = attribute(
            simulation([Grade.THREE_HUNDRED] * 60 + [Grade.MISS] * 60),
            beatmap(self.objects(120)),
        )
        by_feature: dict[str, float] = {}
        for share in result.shares:
            by_feature[share.feature] = by_feature.get(share.feature, 0.0) + share.share_of_objects
        for feature, total in by_feature.items():
            assert total == pytest.approx(1.0, abs=1e-9), feature

    def test_a_perfect_play_loses_nothing(self) -> None:
        result = attribute(simulation([Grade.THREE_HUNDRED] * 60), beatmap(self.objects(60)))
        assert result.accuracy == pytest.approx(1.0)
        assert result.total_loss == 0.0
        assert result.worst() == []

    def test_a_miss_costs_more_than_a_hundred(self) -> None:
        miss = attribute(simulation([Grade.MISS]), beatmap(self.objects(1)))
        hundred = attribute(simulation([Grade.HUNDRED]), beatmap(self.objects(1)))
        assert miss.total_loss > hundred.total_loss

    def test_nothing_judged_is_not_a_crash(self) -> None:
        result = attribute(simulation([]), beatmap(self.objects(4)))
        assert result.judged == 0
        assert result.shares == []


class TestWhatGetsNamed:
    def share(self, *, objects: float, loss: float, n: int = 100) -> Share:
        return Share(
            feature="f",
            label="l",
            objects=n,
            share_of_objects=objects,
            share_of_loss=loss,
            accuracy=0.9,
            reportable=n >= MINIMUM_GROUP,
        )

    def attribution(self, *shares: Share) -> Attribution:
        return Attribution(accuracy=0.9, total_loss=10.0, judged=1000, shares=list(shares))

    def test_a_group_that_is_most_of_the_map_is_never_a_finding(self) -> None:
        # A first version reported "the other 90% of the map" as the top result.
        # "Most of the map is where you lost accuracy" is true of every play.
        bulk = self.share(objects=0.90, loss=0.96)
        assert bulk.concentration > 1.0
        assert self.attribution(bulk).worst() == []

    def test_a_group_barely_over_its_share_is_not_a_finding(self) -> None:
        assert self.attribution(self.share(objects=0.2, loss=0.21)).worst() == []

    def test_a_concentrated_minority_is(self) -> None:
        concentrated = self.share(objects=0.04, loss=0.115)
        assert concentrated.concentration > CONCENTRATION
        assert self.attribution(concentrated).worst() == [concentrated]

    def test_a_group_too_small_to_name_still_counts_toward_the_total(self) -> None:
        # Dropping it would break the partition, which is the one property this
        # module has going for it.
        small = self.share(objects=0.02, loss=0.30, n=MINIMUM_GROUP - 1)
        assert not small.reportable
        assert self.attribution(small).worst() == []
        assert small.share_of_loss == 0.30

    def test_findings_come_out_worst_first(self) -> None:
        small = self.share(objects=0.05, loss=0.10)
        large = self.share(objects=0.10, loss=0.30)
        assert self.attribution(small, large).worst() == [large, small]
