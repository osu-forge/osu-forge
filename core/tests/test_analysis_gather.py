"""Building a corpus out of a replay folder.

The maps and replays here are constructed, so the ground truth is known: a
replay written with a deliberate +6 ms bias has to come back as one, and a
folder of ten of them across three evenings has to come back as a corpus the
statistics will answer for. A test that read a real folder and asserted it did
not crash would establish neither.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from osuforge.analysis.corpus import diagnose
from osuforge.analysis.gather import gather
from osuforge.replay.model import Keys
from osuforge.replay.source import BeatmapIndex

from .replay_fixtures import build_replay

K1 = int(Keys.K1 | Keys.M1)
MARKERS: list[tuple[int, float, float, int]] = [(0, 256.0, -500.0, 0), (-1, 256.0, -500.0, 0)]

OBJECTS = 120
"""Comfortably over the hundred hits below which a replay is excluded."""

_HEADER = (
    "osu file format v14\n"
    "[General]\nMode: 0\nStackLeniency: 0.7\n"
    "[Metadata]\nArtist:{artist}\nTitle:{title}\nVersion:V\n"
    "[Difficulty]\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\nSliderMultiplier:1\n"
    "[TimingPoints]\n0,500,4,2,0,100,1,0\n"
    "[HitObjects]\n"
)


def _position(index: int) -> tuple[float, float]:
    """Well apart, so nothing stacks and every press is unambiguously on one object."""
    return 60.0 + (index % 5) * 80.0, 60.0 + (index // 5 % 4) * 80.0


def beatmap_text(*, title: str = "T", artist: str = "A") -> str:
    lines = [_HEADER.format(artist=artist, title=title)]
    for index in range(OBJECTS):
        x, y = _position(index)
        lines.append(f"{x:.0f},{y:.0f},{1000 + index * 400},1,0\n")
    return "".join(lines)


def write_map(songs: Path, name: str, *, title: str = "T") -> str:
    path = songs / f"{name}.osu"
    path.write_text(beatmap_text(title=title), encoding="utf-8")
    return hashlib.md5(path.read_bytes()).hexdigest()


def write_replay(
    folder: Path,
    name: str,
    *,
    beatmap_hash: str,
    when: datetime,
    errors: list[int],
) -> Path:
    """A replay that hits every object by the given per-object error."""
    samples: list[tuple[int, float, float, int]] = []
    for index in range(OBJECTS):
        time = 1000 + index * 400
        x, y = _position(index)
        error = errors[index % len(errors)]
        samples.extend(
            [(time - 200, x, y, 0), (time + error, x, y, K1), (time + error + 40, x, y, 0)]
        )

    body = list(MARKERS)
    previous = -1
    for time, x, y, keys in samples:
        body.append((time - previous, x, y, keys))
        previous = time

    path = folder / name
    path.write_bytes(
        build_replay(
            beatmap_hash=beatmap_hash,
            # Every error below is inside the 32 ms 300 window, so the game and
            # the simulation have to agree on all of them or the screen rejects
            # the replay — which is itself the thing being relied on here.
            count_300=OBJECTS,
            count_100=0,
            count_50=0,
            count_geki=0,
            count_katu=0,
            count_miss=0,
            max_combo=OBJECTS,
            timestamp=when,
            frames=body,
        )
    )
    return path


@pytest.fixture
def songs(tmp_path: Path) -> Path:
    folder = tmp_path / "Songs"
    folder.mkdir()
    return folder


@pytest.fixture
def replays(tmp_path: Path) -> Path:
    folder = tmp_path / "r"
    folder.mkdir()
    return folder


class TestGather:
    def test_a_replay_becomes_an_entry_carrying_its_own_bias(
        self, songs: Path, replays: Path
    ) -> None:
        digest = write_map(songs, "map", title="Some Song")
        write_replay(
            replays,
            "a.osr",
            beatmap_hash=digest,
            when=datetime(2026, 8, 1, 20, 0, tzinfo=UTC),
            errors=[6],
        )

        collected = gather(replays.glob("*.osr"), BeatmapIndex(songs))
        assert len(collected.entries) == 1
        entry = collected.entries[0]
        assert entry.beatmap == "A - Some Song [V]"
        assert len(entry.errors) == OBJECTS
        assert sum(entry.errors) / len(entry.errors) == pytest.approx(6.0)

    def test_sessions_come_from_the_gap_between_plays(self, songs: Path, replays: Path) -> None:
        digest = write_map(songs, "map")
        evening = datetime(2026, 8, 1, 20, 0, tzinfo=UTC)
        for name, when in [
            ("a.osr", evening),
            ("b.osr", evening + timedelta(minutes=20)),
            ("c.osr", evening + timedelta(days=1)),
        ]:
            write_replay(replays, name, beatmap_hash=digest, when=when, errors=[0])

        collected = gather(replays.glob("*.osr"), BeatmapIndex(songs))
        # Two plays in one sitting and one the next day. Merging the evenings
        # would understate how correlated the hits are, and every interval
        # downstream is computed at this level.
        assert collected.sessions == 2

    def test_a_replay_whose_map_is_missing_is_named_rather_than_dropped(
        self, songs: Path, replays: Path
    ) -> None:
        write_replay(
            replays,
            "orphan.osr",
            beatmap_hash="0" * 32,
            when=datetime(2026, 8, 1, 20, 0, tzinfo=UTC),
            errors=[0],
        )
        collected = gather(replays.glob("*.osr"), BeatmapIndex(songs))
        assert collected.entries == []
        assert "not in the songs folder" in collected.skipped["orphan.osr"]
        assert "1 skipped" in collected.summary()

    def test_an_unreadable_replay_is_a_reason_not_a_crash(self, songs: Path, replays: Path) -> None:
        (replays / "broken.osr").write_bytes(b"")
        collected = gather(replays.glob("*.osr"), BeatmapIndex(songs))
        assert collected.entries == []
        assert "broken.osr" in collected.skipped

    def test_unknown_local_offsets_are_not_reported_as_zero(
        self, songs: Path, replays: Path
    ) -> None:
        digest = write_map(songs, "map")
        write_replay(
            replays,
            "a.osr",
            beatmap_hash=digest,
            when=datetime(2026, 8, 1, 20, 0, tzinfo=UTC),
            errors=[0],
        )

        unknown = gather(replays.glob("*.osr"), BeatmapIndex(songs))
        assert not unknown.offsets_known
        assert "local offsets unknown" in unknown.summary()

        known = gather(replays.glob("*.osr"), BeatmapIndex(songs), offsets={})
        assert known.offsets_known
        assert "local offsets unknown" not in known.summary()

    def test_a_local_offset_reaches_the_inclusion_policy(self, songs: Path, replays: Path) -> None:
        digest = write_map(songs, "map")
        write_replay(
            replays,
            "a.osr",
            beatmap_hash=digest,
            when=datetime(2026, 8, 1, 20, 0, tzinfo=UTC),
            errors=[0],
        )
        collected = gather(replays.glob("*.osr"), BeatmapIndex(songs), offsets={digest: -12})
        # Carried, not silently applied: hit errors on a map the player nudged
        # in game are on a different scale, and it is `select` that drops them.
        assert collected.entries[0].local_offset == -12
        assert collected.entries[0].sample.local_offset == -12

    def test_the_arrival_split_sums_to_the_mean_error(self, songs: Path, replays: Path) -> None:
        digest = write_map(songs, "map")
        write_replay(
            replays,
            "a.osr",
            beatmap_hash=digest,
            when=datetime(2026, 8, 1, 20, 0, tzinfo=UTC),
            errors=[8],
        )
        collected = gather(replays.glob("*.osr"), BeatmapIndex(songs))
        split = collected.decomposition
        assert split is not None
        # The identity is the whole point of the split: approach plus reaction
        # is the mean hit error, so a conversion that broke it would make the
        # two halves describe different quantities.
        assert split.total == pytest.approx(split.approach + split.reaction)
        assert split.total == pytest.approx(8.0, abs=1.0)


class TestEndToEnd:
    def _corpus(self, songs: Path, replays: Path, *, bias: int) -> None:
        digest = write_map(songs, "map")
        start = datetime(2026, 8, 1, 20, 0, tzinfo=UTC)
        for index in range(12):
            write_replay(
                replays,
                f"{index:02d}.osr",
                beatmap_hash=digest,
                # Four evenings, three plays each — enough sessions for the
                # corpus to be willing to answer at all.
                when=start + timedelta(days=index // 3, minutes=20 * (index % 3)),
                errors=[bias - 4, bias, bias + 4, bias + 1, bias - 1],
            )

    def test_a_folder_of_plays_becomes_an_answer(self, songs: Path, replays: Path) -> None:
        self._corpus(songs, replays, bias=6)
        collected = gather(replays.glob("*.osr"), BeatmapIndex(songs))
        result = diagnose(collected.entries, decomposition=collected.decomposition)

        assert result.usable, result.insufficient
        assert result.sessions == 4
        assert result.bias is not None
        assert result.bias.mean == pytest.approx(6.0, abs=1.0)
        bias_axis = next(axis for axis in result.axes if axis.name == "timing bias")
        assert bias_axis.actionable
        assert "late" in bias_axis.verdict

    def test_too_few_plays_is_a_sentence_rather_than_a_blank(
        self, songs: Path, replays: Path
    ) -> None:
        digest = write_map(songs, "map")
        start = datetime(2026, 8, 1, 20, 0, tzinfo=UTC)
        for index in range(3):
            write_replay(
                replays,
                f"{index}.osr",
                beatmap_hash=digest,
                when=start + timedelta(days=index),
                errors=[6],
            )
        collected = gather(replays.glob("*.osr"), BeatmapIndex(songs))
        result = diagnose(collected.entries)

        assert not result.usable
        assert result.insufficient is not None
        # "Not enough data" and "nothing wrong" are opposite conclusions that
        # look identical as an empty report, and only one is a reason to change
        # a setting.
        assert "more replay(s)" in result.insufficient
