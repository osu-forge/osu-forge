"""Finding the beatmap a replay was played on, and judging the two together.

A replay names its beatmap by MD5 and nothing else, so every consumer of a
replay folder needs the same two steps before it can say anything: locate the
map, then simulate the play against it. This module is those two steps and
nothing more.

It lives here rather than beside either consumer because there are now two —
the live page, which shows one play as it finishes, and the corpus, which reads
every play at once. A second copy of "parse, find the map, apply the mods,
simulate, screen" would be a second set of decisions about which objects count,
and the two would drift. The whole point of :class:`Judged` is that the page and
the corpus are looking at the same judgement.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import osu_forge_diffcalc as diffcalc

from osuforge.replay.model import Replay
from osuforge.replay.parse import ReplayParseError, parse_path
from osuforge.replay.simulate import Simulation, simulate
from osuforge.replay.validate import Agreement, Validation, validate

__all__ = ["BeatmapIndex", "Judged", "judge"]


class BeatmapIndex:
    """Beatmaps by MD5, so a replay can find the map it was played on.

    Built once and topped up. A full scan of a real collection takes under half a
    second, but doing it every poll would read half a gigabyte a minute for no
    reason, so only files that appeared since last time are hashed.
    """

    def __init__(self, songs: Path) -> None:
        self.songs = songs
        self._by_hash: dict[str, Path] = {}
        self._seen: set[Path] = set()
        self.refresh()

    def refresh(self) -> int:
        added = 0
        for path in self.songs.rglob("*.osu"):
            if path in self._seen:
                continue
            self._seen.add(path)
            try:
                digest = hashlib.md5(path.read_bytes()).hexdigest()
            except OSError:
                continue
            self._by_hash.setdefault(digest, path)
            added += 1
        return added

    def get(self, beatmap_hash: str) -> Path | None:
        found = self._by_hash.get(beatmap_hash)
        if found is None:
            # A map downloaded since the index was built is the ordinary reason
            # for a miss, and it is worth one rescan before giving up.
            self.refresh()
            found = self._by_hash.get(beatmap_hash)
        return found

    def __len__(self) -> int:
        return len(self._by_hash)


@dataclass(frozen=True, slots=True)
class Judged:
    """One replay, its beatmap, and the simulation of the two together."""

    path: Path
    replay: Replay

    beatmap: diffcalc.Beatmap
    """As the file has it, without the replay's mods."""

    beatmap_path: Path

    played: diffcalc.Beatmap
    """With the replay's mods applied — the map the player actually saw.

    Kept separately because both are needed and confusing them is silent: under
    Hard Rock every position is mirrored, so measuring aim against `beatmap`
    would measure it against objects that were never on the screen.
    """

    simulation: Simulation
    check: Validation

    @property
    def usable(self) -> bool:
        """Whether the simulation reproduced the game's own judgements.

        A play the simulator did not reproduce describes objects that may not be
        the objects the player hit, so its hit errors are not evidence about
        anything.
        """
        return self.check.agreement is not Agreement.MISMATCH


def judge(path: Path, index: BeatmapIndex) -> Judged | str:
    """Read one replay and simulate it, or return why it could not be.

    A reason rather than `None`, because "the beatmap is not installed" and "the
    file is corrupt" want different responses from whoever is reading the
    result.
    """
    try:
        replay = parse_path(path)
    except (ReplayParseError, OSError) as exc:
        return str(exc)
    if not replay.usable_for_timing:
        return "not an osu!standard play by a person"

    beatmap_path = index.get(replay.beatmap_hash)
    if beatmap_path is None:
        return "the beatmap for this replay is not in the songs folder"

    try:
        beatmap = diffcalc.Beatmap.from_file(beatmap_path)
    except diffcalc.BeatmapError as exc:
        return str(exc)

    simulation = simulate(replay, beatmap)
    return Judged(
        path=path,
        replay=replay,
        beatmap=beatmap,
        beatmap_path=beatmap_path,
        played=beatmap.with_mods(int(replay.mods)),
        simulation=simulation,
        check=validate(simulation, replay.judgements),
    )
