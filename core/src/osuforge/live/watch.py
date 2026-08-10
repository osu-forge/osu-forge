"""Turning replays into something worth looking at, as they appear.

A play produces a replay when it finishes, so watching the folder is enough to
know a play happened and to have everything needed to analyse it. The loop is a
poll rather than a filesystem notification: a new file appears at most once every
couple of minutes, the folder holds a few dozen entries, and a poll cannot miss
an event or fire twice on one.

Nothing here decides anything. It reads, simulates, screens, and hands the
result to be rendered — including whether the screen passed, so a play the
simulator did not reproduce is shown as such rather than quietly left out.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import osu_forge_diffcalc as diffcalc

from osuforge.analysis.failures import Failure, FailureReport, failures
from osuforge.analysis.patterns import Attribution, Share, attribute
from osuforge.replay.frames import Button
from osuforge.replay.simulate import Grade
from osuforge.replay.source import BeatmapIndex, Judged, judge
from osuforge.replay.validate import Agreement

__all__ = [
    "POLL_SECONDS",
    # Re-exported: locating the beatmap and judging the replay against it is
    # shared with the corpus, and lives in `osuforge.replay.source` so that
    # neither reader owns it.
    "BeatmapIndex",
    "Play",
    "Session",
    "analyse",
    "default_page_path",
]

POLL_SECONDS = 2.0
"""How often the replay folder is checked.

A poll rather than a filesystem notification. A replay appears at most once
every couple of minutes, the folder holds a few dozen entries, and a poll cannot
miss an event or fire twice on one.
"""


def default_page_path() -> Path:
    """Where the page is written.

    Beside the tool's own data, never inside the osu! install — the game owns
    that directory and rewrites parts of it.
    """
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "osu-forge" / "live.html"


@dataclass(frozen=True, slots=True)
class Play:
    """One finished play, analysed."""

    replay_name: str
    played_at: datetime
    artist: str
    title: str
    version: str

    background: Path | None
    """Full path to the map's background image, if it has one and it exists.

    A path rather than the image: embedding a few megabytes of JPEG per play
    would make the page enormous, and the browser is already looking at a local
    file so it can read a neighbouring one.
    """

    bpm: float
    object_count: int
    length_ms: float
    circle_size: float
    approach_rate: float
    overall_difficulty: float

    accuracy: float
    """From the replay header — the game's own figure, not the simulation's."""

    counts: dict[Grade, int]
    agreement: Agreement
    agreement_reason: str

    errors: list[float]
    """Usable circle timing errors, real milliseconds, early negative."""

    aim_errors: list[float]
    """Distance from centre at the press, in radii."""

    presses: dict[Button, int]
    attribution: Attribution
    report: FailureReport

    @property
    def usable(self) -> bool:
        return self.agreement is not Agreement.MISMATCH

    @property
    def breaks(self) -> list[Failure]:
        """The failures worth showing, in map order.

        Misses always; slider drops only when the combo arithmetic checks out
        against the game's own figure.
        """
        return self.report.shown_breaks()

    @property
    def sliderbreaks(self) -> int:
        return len(self.report.sliderbreaks) if self.report.combo_agrees else 0

    @property
    def max_combo(self) -> int:
        return self.report.max_combo

    @property
    def reported_max_combo(self) -> int:
        return self.report.reported_max_combo

    @property
    def combo_caveat(self) -> str | None:
        return self.report.caveat()

    @property
    def mean_error(self) -> float:
        return sum(self.errors) / len(self.errors) if self.errors else math.nan

    @property
    def unstable_rate(self) -> float:
        """Ten times the standard deviation of the hit errors, as osu! reports it."""
        if len(self.errors) < 2:
            return math.nan
        mean = self.mean_error
        variance = sum((error - mean) ** 2 for error in self.errors) / (len(self.errors) - 1)
        return 10.0 * math.sqrt(variance)

    @property
    def key_balance(self) -> float:
        """Share of keyboard presses on the first key. 0.5 is even."""
        k1 = self.presses.get(Button.K1, 0)
        k2 = self.presses.get(Button.K2, 0)
        return k1 / (k1 + k2) if k1 + k2 else math.nan

    def findings(self, limit: int = 3) -> list[Share]:
        return self.attribution.worst(limit)


@dataclass(slots=True)
class Session:
    """Everything analysed since the page was opened, newest first."""

    plays: list[Play] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)

    def add(self, play: Play) -> None:
        self.plays.insert(0, play)

    @property
    def usable(self) -> list[Play]:
        return [play for play in self.plays if play.usable]

    @property
    def errors(self) -> list[float]:
        return [error for play in self.usable for error in play.errors]

    @property
    def mean_error(self) -> float:
        errors = self.errors
        return sum(errors) / len(errors) if errors else math.nan

    @property
    def unstable_rate(self) -> float:
        errors = self.errors
        if len(errors) < 2:
            return math.nan
        mean = sum(errors) / len(errors)
        variance = sum((error - mean) ** 2 for error in errors) / (len(errors) - 1)
        return 10.0 * math.sqrt(variance)

    @property
    def key_balance(self) -> float:
        k1 = sum(play.presses.get(Button.K1, 0) for play in self.usable)
        k2 = sum(play.presses.get(Button.K2, 0) for play in self.usable)
        return k1 / (k1 + k2) if k1 + k2 else math.nan


def _play_from(judged: Judged) -> Play:
    path, replay = judged.path, judged.replay
    beatmap, played = judged.beatmap, judged.played
    simulation, check = judged.simulation, judged.check

    presses: dict[Button, int] = {}
    for event in simulation.frames.press_events:
        presses[event.button] = presses.get(event.button, 0) + 1

    background: Path | None = None
    if beatmap.background:
        candidate = judged.beatmap_path.parent / beatmap.background
        # Checked rather than assumed. A map whose background was deleted, or
        # whose Events section names a file that is not there, would otherwise
        # render as a broken image on every row.
        background = candidate if candidate.is_file() else None

    return Play(
        replay_name=path.name,
        played_at=replay.timestamp,
        artist=beatmap.artist,
        title=beatmap.title,
        version=beatmap.version,
        background=background,
        bpm=beatmap.bpm,
        object_count=beatmap.object_count,
        length_ms=beatmap.length,
        circle_size=played.circle_size,
        approach_rate=played.approach_rate,
        overall_difficulty=played.overall_difficulty,
        accuracy=replay.judgements.accuracy,
        counts=simulation.counts(),
        agreement=check.agreement,
        agreement_reason=check.reason,
        errors=simulation.timing_errors(),
        aim_errors=[
            hit.aim_error
            for hit in simulation.hits
            if hit.aim_error is not None and hit.kind is diffcalc.ObjectKind.Circle
        ],
        presses=presses,
        attribution=attribute(simulation, played),
        report=failures(simulation, played, reported_max_combo=replay.max_combo),
    )


def analyse(path: Path, index: BeatmapIndex) -> Play | str:
    """Analyse one replay, or return why it could not be.

    A reason rather than `None`, because "the beatmap is not installed" and "the
    file is corrupt" want different responses from whoever is reading the page.
    """
    judged = judge(path, index)
    return judged if isinstance(judged, str) else _play_from(judged)
