"""Reading osu!'s own beatmap index, and refusing to when the parse drifts."""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

import pytest

from osuforge.models import Basis, Category, Confidence, Severity, Subsystem
from osuforge.sources.osudb import (
    MINIMUM_AGREEMENT,
    BeatmapEntry,
    OsuDb,
    OsuDbError,
    Verified,
    default_path,
    default_songs,
    load,
    read,
    verify,
)

VERSION = 20260711


def string(value: str) -> bytes:
    if not value:
        return b"\x00"
    raw = value.encode("utf-8")
    length = bytearray()
    size = len(raw)
    while True:
        byte = size & 0x7F
        size >>= 7
        length.append(byte | 0x80 if size else byte)
        if not size:
            break
    return b"\x0b" + bytes(length) + raw


def star_ratings(pairs: list[tuple[int, float]], *, single: bool = True) -> bytes:
    out = struct.pack("<i", len(pairs))
    for mods, rating in pairs:
        out += b"\x08" + struct.pack("<i", mods)
        out += (
            (b"\x0c" + struct.pack("<f", rating))
            if single
            else (b"\x0d" + struct.pack("<d", rating))
        )
    return out


def entry(
    *,
    md5: str = "0" * 32,
    local_offset: int = 0,
    online_offset: int = 0,
    mode: int = 0,
    single_ratings: bool = True,
    timing_points: int = 1,
    folder: str = "1234 Artist - Title",
) -> bytes:
    out = b"".join(
        string(v) for v in ("Artist", "Artist", "Title", "Title", "Creator", "Insane", "audio.mp3")
    )
    out += string(md5) + string("map.osu")
    out += b"\x04"  # ranked
    out += struct.pack("<hhh", 100, 50, 1)
    out += struct.pack("<q", 0)  # last modified
    out += struct.pack("<ffff", 9.0, 4.0, 5.0, 8.0)
    out += struct.pack("<d", 1.4)
    for _ in range(4):
        out += star_ratings([(0, 5.5), (64, 7.2)], single=single_ratings)
    out += struct.pack("<iii", 100, 120_000, 30_000)
    out += struct.pack("<i", timing_points)
    for _ in range(timing_points):
        out += struct.pack("<dd", 500.0, 0.0) + b"\x01"
    out += struct.pack("<iii", 1234, 5678, 0)
    out += b"\x09\x09\x09\x09"  # grades
    out += struct.pack("<h", local_offset)
    out += struct.pack("<f", 0.7)
    out += bytes([mode])
    out += string("") + string("tags")
    out += struct.pack("<h", online_offset)
    out += string("")
    out += b"\x00" + struct.pack("<q", 0) + b"\x00"
    out += string(folder)
    out += struct.pack("<q", 0)
    out += b"\x00\x00\x00\x00\x00"
    out += struct.pack("<i", 0)
    out += b"\x00"
    return out


def header(count: int, *, version: int = VERSION) -> bytes:
    out = struct.pack("<i", version) + struct.pack("<i", 1) + b"\x01" + struct.pack("<q", 0)
    return out + string("tester") + struct.pack("<i", count)


def database(entries: list[bytes], *, version: int = VERSION) -> bytes:
    return header(len(entries), version=version) + b"".join(entries) + struct.pack("<i", 0)


class TestParsing:
    def test_a_single_entry_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "osu!.db"
        path.write_bytes(database([entry(md5="a" * 32, local_offset=-12)]))
        db = read(path)
        assert db.version == VERSION
        assert db.player == "tester"
        assert db.complete
        assert [e.md5 for e in db.entries] == ["a" * 32]
        assert db.entries[0].local_offset == -12
        assert db.entries[0].title == "Title"

    def test_several_entries_stay_aligned(self, tmp_path: Path) -> None:
        # Entries are variable length and sequential, so alignment surviving to
        # the last one is the whole test.
        path = tmp_path / "osu!.db"
        path.write_bytes(
            database(
                [entry(md5=f"{i:032x}", local_offset=i, timing_points=i + 1) for i in range(6)]
            )
        )
        db = read(path)
        assert [e.local_offset for e in db.entries] == [0, 1, 2, 3, 4, 5]
        assert db.complete

    def test_star_ratings_written_as_doubles_also_parse(self, tmp_path: Path) -> None:
        # Every description of this format says Double; the build this was
        # written against writes Single. Dispatching on the tag handles both,
        # and reading a fixed width for either is silent — the reader keeps
        # finding plausible strings and runs off the end of the file.
        path = tmp_path / "osu!.db"
        path.write_bytes(database([entry(md5="b" * 32, single_ratings=False)]))
        assert read(path).entries[0].md5 == "b" * 32

    def test_offsets_of_both_signs(self, tmp_path: Path) -> None:
        path = tmp_path / "osu!.db"
        path.write_bytes(database([entry(md5="c" * 32, local_offset=-25, online_offset=8)]))
        found = read(path).entries[0]
        assert (found.local_offset, found.online_offset) == (-25, 8)


class TestRefusal:
    def test_a_truncated_file_raises_rather_than_returning_what_it_got(
        self, tmp_path: Path
    ) -> None:
        # A half-read index is a set of entries whose fields may not be the
        # fields they are labelled as, and a caller cannot tell which.
        path = tmp_path / "osu!.db"
        first, second = entry(md5="d" * 32), entry(md5="e" * 32)
        whole = database([first, second])
        # Cut partway through the second entry: the first reads cleanly, so the
        # failure is about where the file ends rather than about the layout.
        path.write_bytes(whole[: len(header(2)) + len(first) + len(second) // 2])
        with pytest.raises(OsuDbError, match="entry 1 of 2"):
            read(path)

    def test_a_bad_string_marker_names_its_offset(self, tmp_path: Path) -> None:
        path = tmp_path / "osu!.db"
        data = bytearray(database([entry(md5="f" * 32)]))
        data[len(header(1))] = 0x42  # first entry's artist marker
        path.write_bytes(bytes(data))
        with pytest.raises(OsuDbError, match="string marker 0x42"):
            read(path)

    def test_an_unknown_star_rating_type_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "osu!.db"
        data = database([entry(md5="0" * 32)])
        broken = data.replace(
            b"\x08" + struct.pack("<i", 0) + b"\x0c", b"\x08" + struct.pack("<i", 0) + b"\x99", 1
        )
        path.write_bytes(broken)
        with pytest.raises(OsuDbError, match="star rating type 0x99"):
            read(path)

    def test_a_missing_file_is_an_osudb_error_not_an_oserror(self, tmp_path: Path) -> None:
        with pytest.raises(OsuDbError):
            read(tmp_path / "absent.db")


class TestVerification:
    """Each entry is checked against the exact path it names, so the fixture
    has to lay the Songs tree out the way an entry describes it."""

    def library(
        self, tmp_path: Path, offsets: list[int], *, declared: int | None = None
    ) -> tuple[Path, OsuDb]:
        songs = tmp_path / "Songs"
        entries = []
        for index, offset in enumerate(offsets):
            folder = f"{index} Artist - Title"
            (songs / folder).mkdir(parents=True)
            path = songs / folder / "map.osu"
            path.write_text(f"beatmap {index}", encoding="utf-8")
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            entries.append(BeatmapEntry(digest, "map.osu", folder, 0, offset, 0))
        db = OsuDb(
            version=VERSION,
            player="t",
            entries=entries,
            declared_count=len(offsets) if declared is None else declared,
        )
        return songs, db

    def test_full_agreement_is_trustworthy(self, tmp_path: Path) -> None:
        songs, db = self.library(tmp_path, [0] * 4)
        result = verify(db, songs)
        assert (result.matched, result.missing, result.mismatched) == (4, 0, 0)
        assert result.agreement == 1.0
        assert result.trustworthy

    def test_below_the_gate_is_refused_wholesale(self, tmp_path: Path) -> None:
        # A file that is 80% right is not 80% usable. The 20% that drifted took
        # an unknown set of fields with it and there is no way to tell which
        # entries are on the right side of the drift.
        songs, db = self.library(tmp_path, [-20] * 10)
        drifted = [
            *db.entries[:8],
            *(BeatmapEntry(e.md5, "gone.osu", "?", 0, -20, 0) for e in db.entries[8:]),
        ]
        result = verify(
            OsuDb(version=VERSION, player="t", entries=drifted, declared_count=10), songs
        )
        assert result.agreement == pytest.approx(0.8)
        assert not result.trustworthy
        assert result.offsets == {}, "no offset may be used from an untrustworthy parse"
        assert "below the 95% needed" in result.summary()

    def test_a_file_that_is_there_but_differs_is_told_apart_from_one_that_is_not(
        self, tmp_path: Path
    ) -> None:
        # Misalignment reads a folder name out of the middle of another field
        # and names nothing; a stale index names a real file that has changed.
        # Different problems, different fixes.
        songs, db = self.library(tmp_path, [0, 0])
        entries = [
            BeatmapEntry("f" * 32, db.entries[0].osu_file, db.entries[0].folder, 0, 0, 0),
            BeatmapEntry(db.entries[1].md5, "vanished.osu", db.entries[1].folder, 0, 0, 0),
        ]
        result = verify(
            OsuDb(version=VERSION, player="t", entries=entries, declared_count=2), songs
        )
        assert (result.matched, result.mismatched, result.missing) == (0, 1, 1)

    def test_an_incomplete_parse_is_never_trustworthy(self, tmp_path: Path) -> None:
        songs, db = self.library(tmp_path, [0], declared=99)
        result = verify(db, songs)
        assert result.agreement == 1.0, "what was read matches"
        assert not result.trustworthy, "but most of the file was not read"
        assert "assumed to have none" in result.summary()

    def test_non_zero_offsets_are_reported(self, tmp_path: Path) -> None:
        songs, db = self.library(tmp_path, [-20, 0, 15])
        result = verify(db, songs)
        assert result.offsets == {db.entries[0].md5: -20, db.entries[2].md5: 15}
        assert "2 beatmap(s) carry a non-zero local offset" in result.summary()

    def test_all_zero_says_pooling_is_safe(self, tmp_path: Path) -> None:
        songs, db = self.library(tmp_path, [0, 0])
        result = verify(db, songs)
        assert result.offsets == {}
        assert "comparing like with like" in result.summary()

    def test_the_gate_is_where_it_says_it_is(self) -> None:
        assert MINIMUM_AGREEMENT == 0.95


class TestLoad:
    """Whatever happens, the report says what was assumed about offsets."""

    def install(self, tmp_path: Path, offsets: list[int]) -> tuple[Path, Path]:
        songs = tmp_path / "Songs"
        entries = []
        for index, offset in enumerate(offsets):
            folder = f"{index} Artist - Title"
            (songs / folder).mkdir(parents=True)
            path = songs / folder / "map.osu"
            path.write_text(f"beatmap {index}", encoding="utf-8")
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            entries.append(entry(md5=digest, local_offset=offset, folder=folder))
        db = tmp_path / "osu!.db"
        db.write_bytes(database(entries))
        return db, songs

    def test_an_unreadable_file_still_says_what_was_assumed(self, tmp_path: Path) -> None:
        # The important case. The analysis proceeds either way, so the
        # assumption has to appear in the report rather than in a comment.
        result, found = load(path=tmp_path / "absent.db", songs=tmp_path)
        assert result is None
        assert found.id == "data.osudb.unreadable"
        assert found.severity is Severity.WARN
        assert found.confidence is Confidence.INSUFFICIENT
        assert found.recommended_value is None
        assert any("assumed to carry a local offset of zero" in c for c in found.caveats)

    def test_a_clean_install_passes_and_says_so(self, tmp_path: Path) -> None:
        db, songs = self.install(tmp_path, [0, 0, 0])
        result, found = load(path=db, songs=songs)
        assert result is not None and result.trustworthy
        assert found.id == "data.osudb.verified"
        assert found.severity is Severity.PASS
        assert found.confidence is Confidence.MEASURED
        assert found.caveats == [], "nothing was assumed; it was checked"

    def test_a_map_with_an_offset_is_named_and_carried(self, tmp_path: Path) -> None:
        db, songs = self.install(tmp_path, [0, -20, 7])
        result, found = load(path=db, songs=songs)
        assert result is not None
        assert found.id == "data.osudb.local_offset"
        assert found.severity is Severity.WARN
        assert len(found.metadata["offsets"]) == 2
        assert set(found.metadata["offsets"].values()) == {-20, 7}
        assert any(e.value == "-20 ms" for e in found.evidence), "the largest, by magnitude"
        assert "doubles the error" in found.rationale, "why excluded rather than corrected"

    def test_a_wrong_songs_folder_fails_the_gate_and_says_which_way(self, tmp_path: Path) -> None:
        db, _ = self.install(tmp_path, [0, 0])
        result, found = load(path=db, songs=tmp_path / "elsewhere")
        assert result is not None and not result.trustworthy
        assert result.offsets == {}
        assert found.id == "data.osudb.unverified"
        assert found.confidence is Confidence.INSUFFICIENT
        assert "Songs folder is not the one this index describes" in found.summary

    def test_every_outcome_names_the_mechanism(self, tmp_path: Path) -> None:
        db, songs = self.install(tmp_path, [0, -5])
        outcomes = [
            load(path=tmp_path / "absent.db", songs=songs)[1],
            load(path=db, songs=tmp_path / "elsewhere")[1],
            load(path=db, songs=songs)[1],
        ]
        for found in outcomes:
            assert "+/- keys" in found.rationale
            assert found.subsystem is Subsystem.OFFSET
            assert found.category is Category.DATA
            assert found.basis is Basis.HARD_FACT
        assert len({f.id for f in outcomes}) == 3, "distinct ids, so one can be suppressed"


class TestPaths:
    def test_both_defaults_sit_in_the_same_install(self, monkeypatch) -> None:
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")
        assert default_path().name == "osu!.db"
        assert default_songs().name == "Songs"
        assert default_path().parent == default_songs().parent


@pytest.mark.integration
class TestAgainstTheRealFile:
    """The measurement the module docstring rests on.

    Recorded here rather than in a notebook so that an osu! update changing the
    format shows up as a failing test on real data rather than as a silently
    different answer in a report.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def verified() -> Verified:
        songs = os.environ.get("OSU_FORGE_SONGS")
        if not songs:
            pytest.skip("set OSU_FORGE_SONGS")
        path = default_path()
        if not path.is_file():
            pytest.skip(f"no osu!.db at {path}")
        return verify(read(path), Path(songs))

    def test_every_entry_parses(self, verified: Verified) -> None:
        assert verified.db.complete
        assert verified.db.declared_count > 0

    def test_the_hashes_correspond_to_files_on_disk(self, verified: Verified) -> None:
        # Measured at 614/614. This is what makes an undocumented format usable:
        # a layout that has drifted stops producing hashes that match real files
        # long before it stops producing hashes.
        assert verified.trustworthy, verified.summary()
        assert verified.agreement >= MINIMUM_AGREEMENT
