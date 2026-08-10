"""Turning a replay folder into the corpus the statistics take as input.

:mod:`osuforge.analysis.corpus` answers "what keeps happening" from a list of
:class:`~osuforge.analysis.corpus.Entry`. Nothing built that list from files, so
the answer was unreachable from the command line — which is where this module
comes in and all it does.

# What it refuses to put in

Three exclusions, each recorded rather than applied quietly:

- **A replay the simulator did not reproduce.** If the simulation disagrees
  with the game's own judgement counts, its hit errors describe objects that may
  not be the objects the player hit. That is not a small error in the numbers,
  it is numbers about something else.
- **A replay of a map that is not installed**, or one that cannot be read. Those
  are absences rather than results.
- **A map carrying a local offset**, which the inclusion policy in
  :mod:`osuforge.analysis.clustering` drops for being on a different scale. This
  module's part is only to *know* the offsets, by reading them from `osu!.db`;
  assuming zero when the file cannot be read is the direction that invents no
  correction.

Everything excluded is counted and named in :class:`Gathered`, because a filter
whose output is only the survivors is indistinguishable from one that removed
whatever disagreed with the answer.

# Sessions are derived, not recorded

Nothing in a replay says which sitting it belonged to, and sessions are the
level the intervals cluster on — so getting them wrong makes every interval
wrong. They are derived from the gap between plays, which is what
:func:`osuforge.analysis.clustering.assign_sessions` exists for, using the
replay header's own timestamp rather than the file's mtime: a backup or a
folder sync rewrites the second and not the first.

# Real milliseconds throughout

Hit errors leave the simulator in map time and are divided by the replay's rate
here, so that a Double Time play and a no-mod play contribute the same quantity.
The arrival split is converted the same way. Its two terms still sum to the mean
hit error — dividing both by one number cannot break an identity — and they are
now on the scale the score screen and the rest of the report use.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from osuforge.analysis.clustering import assign_sessions
from osuforge.analysis.corpus import Entry
from osuforge.analysis.cursor import decompose
from osuforge.analysis.timing import Decomposition, decomposition_from
from osuforge.replay.source import BeatmapIndex, Judged, judge

__all__ = ["Gathered", "gather"]


@dataclass(frozen=True, slots=True)
class Gathered:
    """The corpus, and everything that did not make it into one."""

    entries: list[Entry]

    skipped: dict[str, str] = field(default_factory=dict)
    """Replay file name to why it contributed nothing."""

    decomposition: Decomposition | None = None
    """The arrival split, pooled over every gathered replay.

    `None` when no object had a measurable arrival — a cursor already inside the
    hit radius when the approach window opened has no arrival to measure, and on
    a map of stacked objects that can be all of them.
    """

    offsets_known: bool = False
    """Whether local offsets were read, or assumed to be zero everywhere.

    Reported because the two look identical in the entries and are not the same
    claim. See :func:`osuforge.sources.osudb.load`.
    """

    @property
    def sessions(self) -> int:
        return len({entry.session for entry in self.entries})

    def summary(self) -> str:
        parts = [f"{len(self.entries)} replay(s) gathered into {self.sessions} session(s)"]
        if self.skipped:
            reasons: dict[str, int] = {}
            for reason in self.skipped.values():
                reasons[reason] = reasons.get(reason, 0) + 1
            worst = sorted(reasons.items(), key=lambda item: -item[1])
            detail = ", ".join(f"{count} {reason}" for reason, count in worst[:3])
            parts.append(f"{len(self.skipped)} skipped ({detail})")
        if not self.offsets_known:
            parts.append("local offsets unknown, assumed zero")
        return "; ".join(parts)


def _name(judged: Judged) -> str:
    return f"{judged.beatmap.artist} - {judged.beatmap.title} [{judged.beatmap.version}]"


def _arrival_pairs(judged: Judged) -> list[tuple[float, float]]:
    """`(hit error, dwell)` in real milliseconds, for objects with an arrival.

    Only hits usable for timing are paired, so the split describes the same
    hits the bias does. Objects whose arrival was not recorded contribute a NaN
    dwell, which :func:`decomposition_from` drops — reported as absent rather
    than as an arrival of zero, which would be a measurement nobody made.
    """
    rate = judged.simulation.rate
    errors = {
        hit.index: hit.error
        for hit in judged.simulation.hits
        if hit.usable_for_timing and hit.error is not None
    }
    pairs: list[tuple[float, float]] = []
    for landing in decompose(judged.simulation, judged.played).landings:
        error = errors.get(landing.index)
        if error is None:
            continue
        dwell = math.nan if landing.dwell is None else landing.dwell / rate
        pairs.append((error / rate, dwell))
    return pairs


def gather(
    paths: Iterable[Path],
    index: BeatmapIndex,
    *,
    offsets: dict[str, int] | None = None,
) -> Gathered:
    """Read every replay in `paths` and reduce it to what the corpus needs.

    `offsets` maps beatmap MD5 to the local offset the game has recorded for it,
    as :attr:`osuforge.sources.osudb.Verified.offsets` returns. Passing `None`
    means they could not be read, which is not the same as knowing they are all
    zero — the entries look the same either way, so :class:`Gathered` carries
    which it was.
    """
    judged: list[Judged] = []
    skipped: dict[str, str] = {}
    for path in sorted(paths):
        result = judge(path, index)
        if isinstance(result, str):
            skipped[path.name] = result
            continue
        if not result.usable:
            # The screen, not a preference. A simulation that disagrees with the
            # game about what was hit is not a noisy measurement of this play.
            skipped[path.name] = (
                f"the simulation does not reproduce this play: {result.check.reason}"
            )
            continue
        judged.append(result)

    timestamps: dict[str, datetime] = {item.path.name: item.replay.timestamp for item in judged}
    sessions = assign_sessions(timestamps)

    entries: list[Entry] = []
    pairs: list[tuple[float, float]] = []
    for item in judged:
        judgements = item.replay.judgements
        entries.append(
            Entry(
                replay=item.path.name,
                beatmap_hash=item.replay.beatmap_hash,
                beatmap=_name(item),
                played_at=item.replay.timestamp,
                session=sessions[item.path.name],
                errors=item.simulation.timing_errors(),
                miss_rate=(judgements.count_miss / judgements.total if judgements.total else 1.0),
                accuracy=judgements.accuracy,
                breaks=judgements.count_miss,
                local_offset=(offsets or {}).get(item.replay.beatmap_hash, 0),
            )
        )
        pairs.extend(_arrival_pairs(item))

    return Gathered(
        entries=entries,
        skipped=skipped,
        decomposition=decomposition_from(pairs),
        offsets_known=offsets is not None,
    )
