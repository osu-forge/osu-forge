"""The differential comparison against a second implementation."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import osu_forge_diffcalc as diffcalc
import pytest

from osuforge.replay.frames import NormalizedFrames
from osuforge.replay.oracle import (
    ORACLE_PYTHON_ENV,
    OracleUnavailableError,
    compare,
    run_oracle,
)
from osuforge.replay.parse import parse_path
from osuforge.replay.simulate import Grade, Hit, Simulation, simulate

CIRCLE = diffcalc.ObjectKind.Circle
SLIDER = diffcalc.ObjectKind.Slider
SPINNER = diffcalc.ObjectKind.Spinner


def simulation(*hits: Hit) -> Simulation:
    return Simulation(
        hits=list(hits),
        frames=NormalizedFrames(frames=[]),
        rate=1.0,
        windows=(32, 76, 120),
        fractional_windows=False,
    )


def hit(time: int, grade: Grade, kind: diffcalc.ObjectKind = CIRCLE) -> Hit:
    return Hit(index=time, time=time, kind=kind, grade=grade, grade_inferred=False)


def oracle_output(*objects: dict, replay: str = "a.osr") -> dict:
    return {
        "version": "5.4.3",
        "results": [{"replay": replay, "beatmap": "a.osu", "objects": list(objects)}],
    }


def judged(time: int, grade: int, kind: str = "Circle") -> dict:
    return {"object_time": time, "kind": kind, "grade": grade, "press_time": time, "x": 0, "y": 0}


class TestComparison:
    def test_full_agreement(self) -> None:
        sims = {"a.osr": simulation(hit(100, Grade.THREE_HUNDRED), hit(200, Grade.HUNDRED))}
        result = compare(sims, oracle_output(judged(100, 300), judged(200, 100)))
        assert result.agreement == 1.0
        assert result.circle_agreement == 1.0
        assert result.disagreements == []

    def test_a_differing_grade_is_recorded_with_both_verdicts(self) -> None:
        sims = {"a.osr": simulation(hit(100, Grade.HUNDRED))}
        result = compare(sims, oracle_output(judged(100, 300)))
        assert result.agreement == 0.0
        (disagreement,) = result.disagreements
        assert disagreement.ours is Grade.HUNDRED
        assert disagreement.theirs == 300
        assert disagreement.object_time == 100

    def test_objects_are_matched_by_time_not_by_position(self) -> None:
        # The oracle skips spinners, so the two lists are different lengths and
        # comparing them index by index would shift every object after one.
        sims = {
            "a.osr": simulation(
                hit(100, Grade.THREE_HUNDRED),
                hit(150, Grade.THREE_HUNDRED, SPINNER),
                hit(200, Grade.HUNDRED),
            )
        }
        result = compare(sims, oracle_output(judged(100, 300), judged(200, 100)))
        assert result.agreement == 1.0
        assert result.unjudged_by_oracle == 1

    def test_circle_agreement_is_reported_separately(self) -> None:
        sims = {
            "a.osr": simulation(
                hit(100, Grade.THREE_HUNDRED),
                hit(200, Grade.THREE_HUNDRED, SLIDER),
            )
        }
        result = compare(sims, oracle_output(judged(100, 300), judged(200, 100, "Slider")))
        assert result.agreement == 0.5
        # Both implementations guess about sliders, so their agreement there
        # measures how similar the guesses are, not whether either is right.
        assert result.circle_agreement == 1.0

    def test_an_object_only_the_oracle_judged_counts_against_us(self) -> None:
        sims = {"a.osr": simulation(hit(100, Grade.THREE_HUNDRED))}
        result = compare(sims, oracle_output(judged(100, 300), judged(999, 0)))
        assert result.compared == 2
        assert result.agreement == 0.5
        (disagreement,) = result.disagreements
        assert disagreement.ours is None, "we never produced this object at all"

    def test_a_replay_the_oracle_could_not_read_is_a_failure_not_a_pass(self) -> None:
        output = {
            "version": "5.4.3",
            "results": [{"replay": "a.osr", "beatmap": "a.osu", "error": "boom"}],
        }
        result = compare({"a.osr": simulation(hit(100, Grade.MISS))}, output)
        assert result.failures == {"a.osr": "boom"}
        assert result.compared == 0

    def test_a_simulation_that_was_not_supplied_is_a_failure(self) -> None:
        result = compare({}, oracle_output(judged(100, 300)))
        assert "a.osr" in result.failures
        assert result.compared == 0


class TestUnavailable:
    def test_a_missing_interpreter_raises_rather_than_returning_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # "The oracle found no disagreements" and "the oracle did not run" must
        # never be confused; one is a pass and the other is a blocker.
        monkeypatch.setenv(ORACLE_PYTHON_ENV, str(tmp_path / "nope.exe"))
        with pytest.raises(OracleUnavailableError, match="no interpreter"):
            run_oracle([(Path("a.osr"), Path("a.osu"))])

    def test_no_jobs_raises(self) -> None:
        with pytest.raises(OracleUnavailableError, match="no replay"):
            run_oracle([])


@pytest.mark.integration
class TestAgainstCircleguard:
    """The real differential run.

    Needs the isolated environment, which is deliberately not a dependency:

        py -3.14 -m venv tools/oracle-venv
        tools/oracle-venv/Scripts/pip install circleguard
    """

    @pytest.fixture(scope="class")
    def comparison(self):
        replays = os.environ.get("OSU_FORGE_REPLAYS")
        songs = os.environ.get("OSU_FORGE_SONGS")
        if not replays or not songs:
            pytest.skip("set OSU_FORGE_REPLAYS and OSU_FORGE_SONGS")

        index: dict[str, Path] = {}
        for path in Path(songs).rglob("*.osu"):
            try:
                index.setdefault(hashlib.md5(path.read_bytes()).hexdigest(), path)
            except OSError:
                continue

        jobs: list[tuple[Path, Path]] = []
        simulations: dict[str, Simulation] = {}
        for osr in sorted(Path(replays).glob("*.osr")):
            try:
                replay = parse_path(osr)
            except Exception:
                continue
            if not replay.usable_for_timing:
                continue
            beatmap_path = index.get(replay.beatmap_hash)
            if beatmap_path is None:
                continue
            jobs.append((osr, beatmap_path))
            simulations[str(osr)] = simulate(replay, diffcalc.Beatmap.from_file(beatmap_path))

        if not jobs:
            pytest.skip("no replay resolved to a local beatmap")
        try:
            output = run_oracle(jobs)
        except OracleUnavailableError as exc:
            pytest.skip(str(exc).splitlines()[0])
        return compare(simulations, output)

    def test_circles_agree_to_the_threshold(self, comparison) -> None:
        # Measured at 99.52% over 24,793 circles. This is the figure that
        # authorises an offset recommendation, so it is asserted rather than
        # printed.
        assert comparison.circle_agreement >= 0.99, comparison.summary()

    def test_every_replay_was_readable(self, comparison) -> None:
        assert comparison.failures == {}

    def test_sliders_are_where_the_two_implementations_part(self, comparison) -> None:
        # Not a quality bar — a statement of where the remaining disagreement
        # lives. Both implementations are guessing about slider ticks, and if
        # that ever stopped being true it would be worth noticing.
        kinds = [d.kind for d in comparison.disagreements]
        assert kinds.count("Slider") > kinds.count("Circle")
