"""What went wrong, when, and what the map was asking for at that moment.

An average tells you a play was 91% and tells you nothing about the four places
it actually fell apart. This finds those places: every object that scored less
than a 300, where combo broke and how much of it was lost, and what the map
looked like right there.

# On "why"

The context reported beside a failure is a description, not a cause. "A 41-degree
turn at 2.3 px/ms with five objects on screen" is a fact about the beatmap at
that timestamp; whether that is why the object was missed is not something a
replay can settle, and a player who missed it because they sneezed will get the
same line. What it is good for is recognising the place — you can seek to the
timestamp and see for yourself, which is the point.

# Combo, and where it really breaks

Combo does not only break on a miss. A slider whose head is hit but which drops
a tick or its tail scores 100 and breaks combo just the same, and that is the
break players most often cannot account for afterwards because the results
screen shows a 100 and no explanation.

Combo counts every scoring point, so a slider with four of them is worth four.
That is why the simulator carries how many parts each slider collected: without
it, "you lost a 240 combo there" cannot be computed from the grade.
"""

from __future__ import annotations

from dataclasses import dataclass

import osu_forge_diffcalc as diffcalc

from osuforge.analysis.patterns import ObjectContext, contexts_for
from osuforge.replay.simulate import Grade, Simulation

__all__ = ["Failure", "FailureReport", "failures", "format_time"]


def format_time(milliseconds: float) -> str:
    """`m:ss.mmm`, in map time — what you would seek to.

    Map time rather than real time on purpose. Under Double Time the two differ,
    and the number that lets you find the place again is the one the song has.
    """
    total = max(0, int(milliseconds))
    minutes, remainder = divmod(total, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


CLIP_BEFORE_MS = 700
"""How far back a clip reaches. Far enough to include the movement into the
object, which is usually where the answer is."""

CLIP_AFTER_MS = 350
CLIP_MAX_SAMPLES = 90
"""Cursor samples kept per clip. About a second of a 60 Hz recording is sixty,
so this rarely thins anything and bounds the page size when it does."""


@dataclass(frozen=True, slots=True)
class Clip:
    """Enough of the replay around a failure to draw it.

    A description of what happened is not the same as seeing it. This carries
    the objects on screen, the cursor's path into them, and where the presses
    landed, so the moment can be drawn rather than narrated.
    """

    radius: float
    objects: list[tuple[float, float, int, bool]]
    """`(x, y, time, is_the_failure)` for each object in the window."""

    cursor: list[tuple[float, float, int]]
    presses: list[tuple[float, float, int]]

    @property
    def empty(self) -> bool:
        return len(self.cursor) < 2


@dataclass(frozen=True, slots=True)
class Failure:
    """One object that cost something, with the state around it."""

    time: int
    """Map time in milliseconds. Use :func:`format_time` to show it."""

    grade: Grade
    kind: diffcalc.ObjectKind

    broke_combo: bool
    combo_lost: int
    """The combo that ended here. Zero when this did not break combo."""

    error: int | None
    """Timing error in map milliseconds, negative early. ``None`` for a miss and
    for a slider that was never pressed."""

    aim_error: float | None
    """Distance from the centre at the press, in radii."""

    context: ObjectContext
    parts_collected: int | None
    parts_total: int | None
    clip: Clip | None = None

    @property
    def at(self) -> str:
        """The timestamp to seek to, `m:ss.mmm` in map time."""
        return format_time(self.time)

    @property
    def is_sliderbreak(self) -> bool:
        """A slider that was hit but not completed.

        The break players cannot account for: the results screen shows a 100 and
        says nothing about the combo that went with it.
        """
        return (
            self.kind is diffcalc.ObjectKind.Slider
            and self.broke_combo
            and self.grade is not Grade.MISS
        )

    def describe(self) -> str:
        """One line naming what happened and what the map was asking for."""
        if self.grade is Grade.MISS:
            what = "missed"
        elif self.is_sliderbreak:
            collected = self.parts_collected or 0
            total = self.parts_total or 0
            what = f"slider dropped ({collected} of {total} parts)"
        else:
            what = f"{int(self.grade)}"
            if self.error is not None:
                what += f", {self.error:+d} ms"

        pieces = [what]
        if self.context.velocity > 0:
            pieces.append(f"{self.context.velocity:.2f} px/ms")
        if self.context.angle is not None:
            pieces.append(f"{self.context.angle * 57.2958:.0f} deg turn")
        if self.context.visible > 2:
            pieces.append(f"{self.context.visible} on screen")
        if self.broke_combo and self.combo_lost:
            pieces.append(f"broke a {self.combo_lost} combo")
        return " · ".join(pieces)


@dataclass(frozen=True, slots=True)
class FailureReport:
    """Where a play went wrong, and whether that account can be believed."""

    failures: list[Failure]
    max_combo: int
    """The longest run the simulation produced."""

    reported_max_combo: int
    """What the replay header says the game reached.

    The check on everything else here. Combo is the one place a break has a
    consequence the game wrote down, so a simulated combo that matches the
    recorded one is evidence that the breaks are in the right places — and one
    that does not is a reason to distrust the whole list.
    """

    @property
    def combo_agrees(self) -> bool:
        """Whether the simulated longest run matches the game's own figure."""
        return self.max_combo == self.reported_max_combo

    @property
    def misses(self) -> list[Failure]:
        """Objects that scored nothing.

        The trustworthy half. A miss is the outcome of a press that did or did
        not happen at a recorded frame, and the differential oracle agrees with
        an independent implementation on 99.5% of circles.
        """
        return [failure for failure in self.failures if failure.grade is Grade.MISS]

    @property
    def sliderbreaks(self) -> list[Failure]:
        """Sliders scored but not completed.

        Measured on the local corpus and **currently not reliable**: the
        simulated longest run disagreed with the game's own figure on every one
        of five real plays, by as much as 294 against 516, which means drops are
        being reported that did not happen. Whether a tick was collected depends
        on where the cursor was between recorded frames, and a replay records
        about sixty a second while the game judges faster.

        Kept, and computed, so the disagreement stays visible and can be worked
        on. Not shown as fact — see :meth:`shown_breaks`.
        """
        return [failure for failure in self.failures if failure.is_sliderbreak]

    def shown_breaks(self) -> list[Failure]:
        """The breaks worth putting in front of someone.

        Misses always. Slider drops only when the combo arithmetic comes out
        matching the game's recorded figure, because that is the one check
        available on whether they are in the right places — and without it a
        confident timestamped list of things that did not happen is worse than
        no list.
        """
        if self.combo_agrees:
            return [failure for failure in self.failures if failure.broke_combo]
        return self.misses

    def caveat(self) -> str | None:
        """What is being withheld and why, or `None` if nothing is."""
        if self.combo_agrees:
            return None
        dropped = len(self.sliderbreaks)
        return (
            f"Only misses are listed. Working the combo through gives {self.max_combo} "
            f"where the game recorded {self.reported_max_combo}, so the "
            f"{dropped} slider drop(s) this found are not in the right places and are "
            "left out rather than shown. Whether a slider dropped a tick depends on "
            "where the cursor was between recorded frames, and a replay only records "
            "about sixty a second."
        )


def _build_clip(simulation: Simulation, beatmap: diffcalc.Beatmap, index: int, time: int) -> Clip:
    """Gather what is needed to draw the moment around one object."""
    start = time - CLIP_BEFORE_MS
    end = time + CLIP_AFTER_MS

    objects = [
        (obj.x, obj.y, obj.time, position == index)
        for position, obj in enumerate(beatmap.objects)
        if start <= obj.time <= end
    ]

    frames = [frame for frame in simulation.frames.frames if start <= frame.time <= end]
    step = max(1, len(frames) // CLIP_MAX_SAMPLES)
    cursor = [(frame.x, frame.y, frame.time) for frame in frames[::step]]

    presses = [
        (
            simulation.frames.frames[event.frame_index].x,
            simulation.frames.frames[event.frame_index].y,
            event.time,
        )
        for event in simulation.frames.press_events
        if start <= event.time <= end
    ]

    return Clip(radius=beatmap.radius, objects=objects, cursor=cursor, presses=presses)


def _combo_value(hit: object) -> int:
    """How much combo an object adds when nothing is dropped."""
    parts = getattr(hit, "parts_total", None)
    return int(parts) if parts else 1


def failures(
    simulation: Simulation, beatmap: diffcalc.Beatmap, *, reported_max_combo: int = 0
) -> FailureReport:
    """Every object that scored less than a 300, in map order.

    `beatmap` must already have the play's mods applied, so that the context
    describes what the player actually saw. `reported_max_combo` comes from the
    replay header and is what the result is checked against.
    """
    contexts = contexts_for(beatmap)
    found: list[Failure] = []
    combo = 0
    longest = 0

    for hit in simulation.hits:
        if hit.index >= len(contexts):
            continue

        is_slider = hit.kind is diffcalc.ObjectKind.Slider
        dropped_a_part = (
            is_slider
            and hit.parts_total is not None
            and (hit.parts_collected or 0) < hit.parts_total
        )
        broke = hit.grade is Grade.MISS or dropped_a_part

        if hit.grade is not Grade.THREE_HUNDRED or broke:
            found.append(
                Failure(
                    time=hit.time,
                    grade=hit.grade,
                    kind=hit.kind,
                    broke_combo=broke,
                    combo_lost=combo if broke else 0,
                    error=hit.error,
                    aim_error=hit.aim_error,
                    context=contexts[hit.index],
                    parts_collected=hit.parts_collected,
                    parts_total=hit.parts_total,
                    # Only for a miss. Everything else has an explanation in one
                    # line, and a clip per hundred would be most of the play.
                    clip=(
                        _build_clip(simulation, beatmap, hit.index, hit.time)
                        if hit.grade is Grade.MISS
                        else None
                    ),
                )
            )

        if broke:
            # Everything scored before this point is gone. A dropped slider ends
            # the run exactly as a miss does, which is the part the results
            # screen never shows.
            longest = max(longest, combo)
            combo = 0
        else:
            combo += _combo_value(hit)

    # The run after the last break counts too, and is usually the longest.
    longest = max(longest, combo)
    return FailureReport(failures=found, max_combo=longest, reported_max_combo=reported_max_combo)
