"""`osu!.db` — the game's own beatmap index, read for one field.

The field is `LocalOffset`. osu! stores a per-beatmap offset that the player
sets in game with the `+`/`-` keys, and it shifts when that map's objects are
judged. An offset analysis that pools maps without knowing them is averaging
quantities that are not the same quantity, and it has no way to notice.

That is the whole reason this module exists. Everything else it parses is
parsed because the format is sequential and the offset is near the end of each
entry — not because anything wants it.

# The format is not documented and this is not trusted

The wiki page describing this file was deleted. What is implemented here was
reconstructed by walking a real file field by field, and it is checked rather
than believed: every entry carries a beatmap MD5, and those can be compared
against the hashes of the `.osu` files on disk. A layout that has drifted by one
byte produces garbage strings within a few fields, so agreement across hundreds
of entries is strong evidence — and disagreement is a reason to discard the
whole file rather than to trust the part that looked fine.

:data:`MINIMUM_AGREEMENT` is that gate. Below it, :func:`read` still returns
what it parsed but :attr:`Verified.trustworthy` is false and no caller may use
the offsets.

# One thing every description of this format gets wrong

The per-mod star ratings are described everywhere as Int-Double pairs. The build
this was written against writes Int-**Single**: the .NET type marker is `0x0c`,
not `0x0d`. Reading a fixed eight bytes puts every later field four bytes out
per pair, which is silent — the reader keeps finding plausible strings and runs
off the end of the file with the first entry still unfinished. The tag is
dispatched on rather than assumed, so either width is handled.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

from osuforge.models import (
    Basis,
    Category,
    Confidence,
    Evidence,
    Finding,
    Severity,
    Subsystem,
)

__all__ = [
    "MINIMUM_AGREEMENT",
    "BeatmapEntry",
    "OsuDb",
    "OsuDbError",
    "Verified",
    "default_path",
    "default_songs",
    "load",
    "read",
    "verify",
]

MINIMUM_AGREEMENT = 0.95
"""Share of entries whose MD5 must match a file on disk.

Below this the parse is treated as wrong in a way that cannot be localised. A
file that is 80% right is not 80% usable: the 20% that drifted took an unknown
set of fields with it, and there is no way to tell which entries are on the
right side of the drift.
"""

_STRING_ABSENT = 0x00
_STRING_PRESENT = 0x0B
_TAG_SINGLE = 0x0C
_TAG_DOUBLE = 0x0D

# Before this version the difficulty settings were bytes rather than floats and
# the per-mod star ratings were absent entirely.
_FLOAT_DIFFICULTY_FROM = 20140609


class OsuDbError(ValueError):
    """The file could not be parsed as `osu!.db`.

    Raised rather than returning a partial result: a half-read index is a set
    of entries whose fields may or may not be the fields they are labelled as,
    and there is no way for a caller to tell which.
    """


@dataclass(frozen=True, slots=True)
class BeatmapEntry:
    """One beatmap, as the game has it indexed."""

    md5: str
    osu_file: str
    folder: str
    mode: int

    local_offset: int
    """Milliseconds, set in game with `+`/`-`. **The reason this module exists.**

    Positive and negative both occur. It shifts when this map's objects are
    judged, so hit errors measured on a map with a non-zero one are not
    comparable with hit errors measured on a map without.
    """

    online_offset: int
    """The offset the beatmap's own metadata suggests. Recorded alongside
    because it is adjacent and free, not because anything uses it."""

    artist: str = ""
    title: str = ""
    difficulty: str = ""
    beatmap_id: int = 0
    set_id: int = 0


@dataclass(frozen=True, slots=True)
class OsuDb:
    """Everything read out of the file."""

    version: int
    player: str
    entries: list[BeatmapEntry] = field(default_factory=list)
    declared_count: int = 0

    @property
    def by_md5(self) -> dict[str, BeatmapEntry]:
        return {entry.md5: entry for entry in self.entries}

    @property
    def complete(self) -> bool:
        """Whether as many entries were read as the header promised."""
        return len(self.entries) == self.declared_count


@dataclass(frozen=True, slots=True)
class Verified:
    """Whether the parse can be believed, and what it says if so."""

    db: OsuDb
    matched: int
    checked: int

    missing: int = 0
    """Entries naming a file that is not there.

    The shape misalignment takes. A drifted cursor does not usually produce a
    wrong hash — it produces a folder and filename read out of the middle of
    some other field, which names nothing. Distinguished from a mismatch
    because a whole library of these means the Songs path is wrong, which is a
    different problem with a different fix.
    """

    mismatched: int = 0
    """Entries whose file is there but hashes differently — a stale index."""

    @property
    def agreement(self) -> float:
        return self.matched / self.checked if self.checked else 0.0

    @property
    def trustworthy(self) -> bool:
        return self.db.complete and self.agreement >= MINIMUM_AGREEMENT

    @property
    def offsets(self) -> dict[str, int]:
        """Non-zero local offsets by beatmap MD5, or empty if untrustworthy.

        Empty rather than raising, so a caller that has already checked
        :attr:`trustworthy` and one that has not both end up assuming zero —
        which is the safe direction.
        """
        if not self.trustworthy:
            return {}
        return {e.md5: e.local_offset for e in self.db.entries if e.local_offset}

    def summary(self) -> str:
        if not self.db.complete:
            return (
                f"osu!.db parse stopped after {len(self.db.entries)} of "
                f"{self.db.declared_count} entries; local offsets are unknown and "
                "every map is assumed to have none"
            )
        if not self.trustworthy:
            cause = (
                "Either the Songs folder is not the one this index describes, or the "
                "layout has drifted"
                if self.missing >= self.mismatched
                else "The index is stale, or the layout has drifted"
            )
            return (
                f"osu!.db parsed but only {self.agreement:.0%} of its beatmaps "
                f"({self.matched}/{self.checked}) hash to what they name on disk, below "
                f"the {MINIMUM_AGREEMENT:.0%} needed — {self.missing} absent, "
                f"{self.mismatched} different. {cause}, and there is no way to tell which "
                "entries are on the right side of it; local offsets are not used."
            )
        non_zero = len(self.offsets)
        if not non_zero:
            return (
                f"osu!.db: {self.matched}/{self.checked} hashes verified, and every "
                "beatmap has a local offset of zero — so pooling maps is comparing "
                "like with like"
            )
        return (
            f"osu!.db: {self.matched}/{self.checked} hashes verified. {non_zero} "
            "beatmap(s) carry a non-zero local offset, so hit errors on those maps "
            "are shifted relative to the rest and must not be pooled with them "
            "uncorrected."
        )


def _install_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "osu!" if base else Path.home() / "osu!"


def default_path() -> Path:
    return _install_root() / "osu!.db"


def default_songs() -> Path:
    return _install_root() / "Songs"


class _Reader:
    """A cursor over the file, raising on anything it cannot account for."""

    __slots__ = ("at", "data")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.at = 0

    def take(self, count: int) -> bytes:
        end = self.at + count
        if count < 0 or end > len(self.data):
            raise OsuDbError(
                f"wanted {count} bytes at offset {self.at}, file ends at {len(self.data)}"
            )
        chunk = self.data[self.at : end]
        self.at = end
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def i16(self) -> int:
        return int(struct.unpack("<h", self.take(2))[0])

    def i32(self) -> int:
        return int(struct.unpack("<i", self.take(4))[0])

    def i64(self) -> int:
        return int(struct.unpack("<q", self.take(8))[0])

    def f32(self) -> float:
        return float(struct.unpack("<f", self.take(4))[0])

    def f64(self) -> float:
        return float(struct.unpack("<d", self.take(8))[0])

    def uleb128(self) -> int:
        value = 0
        shift = 0
        while True:
            byte = self.u8()
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7
            if shift > 35:
                raise OsuDbError(f"string length at {self.at} does not terminate")

    def string(self) -> str:
        marker = self.u8()
        if marker == _STRING_ABSENT:
            return ""
        if marker != _STRING_PRESENT:
            raise OsuDbError(f"string marker {marker:#04x} at offset {self.at - 1}")
        return self.take(self.uleb128()).decode("utf-8", errors="replace")

    def star_ratings(self) -> None:
        """Skip one ruleset's per-mod star ratings.

        Dispatches on the .NET type marker rather than assuming a width. Every
        description of this format says Double; the build this was written
        against writes Single, and reading eight bytes for four is silent.
        """
        count = self.i32()
        if count < 0 or count > 4096:
            raise OsuDbError(f"implausible star rating count {count} at offset {self.at - 4}")
        for _ in range(count):
            self.u8()  # int marker
            self.i32()  # mod combination
            tag = self.u8()
            if tag == _TAG_SINGLE:
                self.f32()
            elif tag == _TAG_DOUBLE:
                self.f64()
            else:
                raise OsuDbError(f"star rating type {tag:#04x} at offset {self.at - 1}")


def _read_entry(r: _Reader, version: int) -> BeatmapEntry:
    artist = r.string()
    r.string()  # artist, unicode
    title = r.string()
    r.string()  # title, unicode
    r.string()  # creator
    difficulty = r.string()
    r.string()  # audio file
    md5 = r.string()
    osu_file = r.string()
    r.u8()  # ranked status
    r.i16()  # circles
    r.i16()  # sliders
    r.i16()  # spinners
    r.i64()  # last modified

    if version >= _FLOAT_DIFFICULTY_FROM:
        for _ in range(4):  # AR, CS, HP, OD
            r.f32()
    else:
        for _ in range(4):
            r.u8()

    r.f64()  # slider velocity

    if version >= _FLOAT_DIFFICULTY_FROM:
        for _ in range(4):  # std, taiko, catch, mania
            r.star_ratings()

    r.i32()  # drain time
    r.i32()  # total time
    r.i32()  # preview time

    points = r.i32()
    if points < 0 or points > 1 << 20:
        raise OsuDbError(f"implausible timing point count {points} at offset {r.at - 4}")
    for _ in range(points):
        r.f64()  # bpm
        r.f64()  # offset
        r.u8()  # uninherited

    beatmap_id = r.i32()
    set_id = r.i32()
    r.i32()  # thread id
    for _ in range(4):  # grades: std, taiko, catch, mania
        r.u8()
    local_offset = r.i16()
    r.f32()  # stack leniency
    mode = r.u8()
    r.string()  # source
    r.string()  # tags
    online_offset = r.i16()
    r.string()  # title font
    r.u8()  # unplayed
    r.i64()  # last played
    r.u8()  # is osz2
    folder = r.string()
    r.i64()  # last checked against the repository
    r.u8()  # ignore beatmap sound
    r.u8()  # ignore beatmap skin
    r.u8()  # disable storyboard
    r.u8()  # disable video
    r.u8()  # visual override
    if version < _FLOAT_DIFFICULTY_FROM:
        r.i16()  # unknown, present only on old files
    r.i32()  # last modification time
    r.u8()  # mania scroll speed

    return BeatmapEntry(
        md5=md5,
        osu_file=osu_file,
        folder=folder,
        mode=mode,
        local_offset=local_offset,
        online_offset=online_offset,
        artist=artist,
        title=title,
        difficulty=difficulty,
        beatmap_id=beatmap_id,
        set_id=set_id,
    )


def read(path: Path | None = None) -> OsuDb:
    """Parse the index. Raises :class:`OsuDbError` on anything unaccountable.

    Stops at the first entry it cannot read rather than skipping it: entries are
    variable length and sequential, so a failure means the cursor is at an
    unknown position and every later entry would be read from the wrong place.
    """
    target = path or default_path()
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise OsuDbError(f"{target}: {exc}") from exc

    r = _Reader(data)
    version = r.i32()
    r.i32()  # folder count
    r.u8()  # account unlocked
    r.i64()  # unlock date
    player = r.string()
    declared = r.i32()
    if declared < 0 or declared > 1 << 22:
        raise OsuDbError(f"implausible beatmap count {declared}")

    entries: list[BeatmapEntry] = []
    for index in range(declared):
        try:
            entries.append(_read_entry(r, version))
        except OsuDbError as exc:
            raise OsuDbError(
                f"{target}: entry {index} of {declared} at offset {r.at}: {exc}"
            ) from exc

    return OsuDb(version=version, player=player, entries=entries, declared_count=declared)


def verify(db: OsuDb, songs: Path) -> Verified:
    """Check the parse against the beatmaps on disk.

    The check that makes an undocumented format usable: a layout that has
    drifted stops naming real files long before it stops producing plausible
    strings.

    Each entry is checked against the exact path it names — `folder` and
    `osu_file` joined onto `songs` — rather than against the set of every hash
    under the tree. Three fields have to agree instead of one, and a library of
    a hundred thousand maps costs one read per entry rather than one read per
    file on disk.
    """
    matched = missing = mismatched = 0
    for entry in db.entries:
        path = songs / entry.folder / entry.osu_file
        try:
            digest = hashlib.md5(path.read_bytes()).hexdigest()
        except OSError:
            missing += 1
            continue
        if digest == entry.md5:
            matched += 1
        else:
            mismatched += 1

    return Verified(
        db=db,
        matched=matched,
        checked=len(db.entries),
        missing=missing,
        mismatched=mismatched,
    )


_ASSUMPTION = (
    "Every beatmap is assumed to carry a local offset of zero. If any of them does "
    "not, that map's hit errors are shifted by it and pooling them with the rest "
    "averages two different quantities."
)

_MECHANISM = (
    "osu! stores a per-beatmap offset that the player sets in game with the +/- "
    "keys, and it shifts when that map's objects are judged. It is held in osu!.db "
    "and nowhere else — not in the .osu file, not in the replay — so a corpus "
    "pooled across maps has no way to notice one from the replays alone."
)


def _source(path: Path, db: OsuDb | None) -> str:
    version = f" version {db.version}" if db else ""
    return f"{path}{version}: BeatmapEntry.LocalOffset, cross-checked by beatmap MD5"


def load(*, path: Path | None = None, songs: Path | None = None) -> tuple[Verified | None, Finding]:
    """Read the offsets, and say what was assumed either way.

    Always returns a finding. The interesting case is the one where the file
    cannot be read: the analysis proceeds regardless, on the assumption that no
    map carries an offset, and that assumption has to appear in the report
    rather than in a comment. `None` for the verification means exactly that —
    assume zero, and say so.
    """
    target = path or default_path()
    library = songs or default_songs()

    try:
        db = read(target)
    except OsuDbError as exc:
        return None, Finding(
            id="data.osudb.unreadable",
            title="Beatmap offsets could not be read",
            summary=(
                f"{target.name} could not be read, so every beatmap is assumed to carry "
                "a local offset of zero. If any of them does not, that map's hit errors "
                "are shifted by it and pooling them with the rest averages two different "
                "quantities."
            ),
            severity=Severity.WARN,
            confidence=Confidence.INSUFFICIENT,
            basis=Basis.HARD_FACT,
            subsystem=Subsystem.OFFSET,
            category=Category.DATA,
            rationale=_MECHANISM,
            evidence=[Evidence(label="read failed", value=str(exc), source=str(target))],
            caveats=[_ASSUMPTION],
            verification="Close osu! and run again — the file is rewritten on exit.",
        )

    result = verify(db, library)
    evidence = [
        Evidence(
            label="beatmaps verified",
            value=f"{result.matched}/{result.checked} ({result.agreement:.1%})",
            source=_source(target, db),
        )
    ]
    if result.missing or result.mismatched:
        evidence.append(
            Evidence(
                label="unverified",
                value=f"{result.missing} absent, {result.mismatched} different",
                source=str(library),
            )
        )

    if not result.trustworthy:
        return result, Finding(
            id="data.osudb.unverified",
            title="Beatmap offsets could not be trusted",
            summary=result.summary(),
            severity=Severity.WARN,
            confidence=Confidence.INSUFFICIENT,
            basis=Basis.HARD_FACT,
            subsystem=Subsystem.OFFSET,
            category=Category.DATA,
            rationale=(
                f"{_MECHANISM} The format is undocumented, so the parse is checked "
                f"rather than believed: below {MINIMUM_AGREEMENT:.0%} agreement it is "
                "discarded whole, because a layout that has drifted took an unknown "
                "set of entries with it and there is no way to tell which."
            ),
            evidence=evidence,
            caveats=[_ASSUMPTION],
            verification="Check that the Songs folder is the one this osu! install uses.",
        )

    offsets = result.offsets
    if not offsets:
        return result, Finding(
            id="data.osudb.verified",
            title="No beatmap carries its own offset",
            summary=result.summary(),
            severity=Severity.PASS,
            confidence=Confidence.MEASURED,
            basis=Basis.HARD_FACT,
            subsystem=Subsystem.OFFSET,
            category=Category.DATA,
            rationale=(
                f"{_MECHANISM} Checked rather than assumed, which is why the pooled "
                "estimate below is comparing like with like."
            ),
            evidence=evidence,
        )

    worst = max(offsets.values(), key=abs)
    return result, Finding(
        id="data.osudb.local_offset",
        title=f"{len(offsets)} beatmap(s) carry their own offset",
        summary=result.summary(),
        severity=Severity.WARN,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.OFFSET,
        category=Category.DATA,
        rationale=(
            f"{_MECHANISM} Replays of these maps are excluded from the pooled estimate "
            "rather than corrected: correcting needs the sign convention, and a "
            "correction applied backwards doubles the error it was meant to remove."
        ),
        evidence=[
            *evidence,
            Evidence(
                label="largest offset",
                value=f"{worst:+d} ms",
                source=_source(target, db),
                unit="ms",
            ),
        ],
        metadata={"offsets": dict(offsets)},
    )
