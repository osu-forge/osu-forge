"""Where the cursor landed, in a frame where the answer means something.

The distance from an object's centre is already recorded, and on its own it
cannot answer a single useful question. Landing 12 pixels out says nothing about
whether the cursor sailed past the centre or stopped short of it, drifted to one
side, or simply shook — and those call for different fixes. A cursor that
consistently overshoots long jumps and lands well on short ones has too much
pointer gain. A cursor that scatters evenly in every direction has none of that
problem and cannot be helped by changing a sensitivity.

So each landing is rotated into the frame of the movement that produced it:

- **along-track** — the component in the direction of travel. Positive is past
  the centre, which is overshoot. This is the one that scales with jump distance
  when gain is wrong, and it is the input to that estimate.
- **cross-track** — the component perpendicular to it. This is scatter, and it
  does not scale with gain, which is exactly why keeping the two apart is what
  makes either of them readable.

The raw `dx`/`dy` are kept beside them because pointer gain is not necessarily
one number: a mouse configured with different DPI on each axis has a different
gain horizontally and vertically, and a single along-track slope would average
across a difference rather than measure it.

# The direction of travel is the intended one

Travel is measured from where the player was free to leave the previous object
to this one — where they had to go — rather than from the cursor's own velocity
at the moment of the press. Both are meaningful and they answer different
questions. The intended displacement is the one gain acts on: a gain error
scales the movement the hand made toward a target, so the error it produces
lies along that line. Cursor velocity at the press answers "were they still
moving when they clicked", which is a timing question wearing a spatial
disguise.

"Where they were free to leave" is not the previous object's own position when
that object is a slider. The ball carries the cursor along the path, so the
movement starts from the **tail**; taking the head points two in five movements
on a normal map from the wrong origin. The same applies to the clock — a
four-beat slider does not give the next object four beats of travel time, and
measuring from its start time overstates the time available by an order of
magnitude on a slow map.

# What is deliberately excluded

Spinners, and any object with no previous object to travel from. A landing needs
a direction, and neither of those has one. They are counted rather than dropped
quietly, because a filter that silently removes what it cannot handle is
indistinguishable from one that removes what disagrees with it.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from enum import StrEnum

import osu_forge_diffcalc as diffcalc

from osuforge.replay.simulate import Simulation

__all__ = ["Landing", "LandingSet", "Sector", "decompose"]


class Sector(StrEnum):
    """Which way the cursor had to travel, in eight compass points.

    Named for what is seen on screen, where **y increases downward**. `DOWN` is
    a jump toward the bottom of the playfield — the "top to bottom" movement a
    player would describe — not a positive step on a mathematical axis.
    """

    RIGHT = "right"
    DOWN_RIGHT = "down-right"
    DOWN = "down"
    DOWN_LEFT = "down-left"
    LEFT = "left"
    UP_LEFT = "up-left"
    UP = "up"
    UP_RIGHT = "up-right"


_SECTORS = (
    Sector.RIGHT,
    Sector.DOWN_RIGHT,
    Sector.DOWN,
    Sector.DOWN_LEFT,
    Sector.LEFT,
    Sector.UP_LEFT,
    Sector.UP,
    Sector.UP_RIGHT,
)

_MIN_TRAVEL = 1.0
"""Below this, in osu! pixels, the direction of travel is noise.

Stacked objects sit a few pixels apart and repeated objects on one spot are
zero apart; normalising a vector that short produces a direction that says more
about rounding than about where the hand went.
"""


@dataclass(frozen=True, slots=True)
class Landing:
    """One object's landing, in the frame of the movement that reached it."""

    index: int
    """Position in the beatmap's object list."""

    time: int

    dx: float
    dy: float
    """Cursor minus centre at the press, in osu! pixels, screen axes.

    Kept alongside the rotated components because a mouse with different DPI per
    axis has a different gain on each, and a single along-track number would
    average across that difference instead of measuring it.
    """

    along: float
    """Component in the direction of travel. **Positive is overshoot.**

    The sign is the load-bearing part of this module: inverted, every
    recommendation downstream reverses. Pinned by test.
    """

    across: float
    """Component perpendicular to travel, positive clockwise on screen.

    Scatter rather than bias. Does not scale with pointer gain, which is what
    makes the along-track component readable.
    """

    travel: float
    """Distance from where the player left the previous object, in osu! pixels.

    A slider's tail rather than its head — see the module docstring.
    """

    heading: float
    """Direction of travel in radians, `atan2(dy, dx)` with y downward."""

    sector: Sector

    available: float
    """Milliseconds the player had to cover `travel`.

    Measured from when they were free to leave the previous object — its end
    time, which for a slider is the tail rather than the head.
    """

    peak_speed: float
    """Fastest cursor speed over the approach, in osu! pixels per millisecond."""

    mean_speed: float

    dwell: float | None
    """How long the cursor was already inside the hit radius before the press.

    Milliseconds. Zero means the press landed on the same frame the cursor
    arrived; larger means the hand was waiting on target. `None` when the
    approach was not recorded — a cursor already inside the radius at the start
    of the window has no arrival to measure.

    This is the spatial half of the timing question: a late press from a cursor
    that arrived early is a different problem from a late press from a cursor
    that arrived late, and no offset fixes the first.
    """

    @property
    def distance(self) -> float:
        return math.hypot(self.dx, self.dy)

    @property
    def duration_speed(self) -> float:
        """Speed the movement demanded, in pixels per millisecond."""
        return self.travel / self.available if self.available > 0 else math.inf


@dataclass(frozen=True, slots=True)
class LandingSet:
    """Every usable landing in one replay, and what was left out."""

    landings: list[Landing]
    radius: float

    skipped: dict[str, int]
    """Why objects were left out, by reason and count. Reported rather than
    applied silently."""

    def summary(self) -> str:
        if not self.skipped:
            return f"{len(self.landings)} landings"
        detail = ", ".join(f"{count} {reason}" for reason, count in sorted(self.skipped.items()))
        return f"{len(self.landings)} landings, {sum(self.skipped.values())} skipped ({detail})"


def _departure(origin: diffcalc.HitObject) -> tuple[float, float]:
    """Where the cursor was when it was free to leave the previous object.

    For a circle that is the circle. For a slider it is the **tail**, not the
    head: the ball has carried the cursor along the path, and the movement to
    the next object starts from wherever it ended up. Taking the head instead
    points two in five movements on a normal map from the wrong origin, which
    corrupts the direction as well as the distance.

    Odd slide counts finish at the far end and even ones come back to the
    start, the same rule the path geometry uses for a slider's visual end.
    """
    if origin.kind is not diffcalc.ObjectKind.Slider or not origin.path:
        return (origin.x, origin.y)
    if origin.slides % 2 == 1:
        return (origin.path[-2], origin.path[-1])
    return (origin.path[0], origin.path[1])


def _sector(heading: float) -> Sector:
    # Eight 45-degree sectors centred on each compass point, so a movement
    # straight down falls in DOWN rather than on a boundary between two.
    return _SECTORS[math.floor((heading + math.pi / 8) / (math.pi / 4)) % 8]


def decompose(simulation: Simulation, played: diffcalc.Beatmap) -> LandingSet:
    """Rotate each landing into the frame of the movement that reached it.

    `played` must be the beatmap with the replay's mods already applied — the
    same object the simulation judged against. Passing the unmodified beatmap
    would measure aim against positions the player never saw, and under Hard
    Rock every one of them is mirrored.
    """
    objects = played.objects
    radius = played.radius
    frames = simulation.frames.frames
    times = [frame.time for frame in frames]

    landings: list[Landing] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    previous: diffcalc.HitObject | None = None
    for hit in simulation.hits:
        obj = objects[hit.index]

        # Updated before every early exit below, so that an object which cannot
        # itself be measured still serves as the origin of the next movement —
        # the player travelled from it regardless.
        origin, previous = previous, obj

        if obj.kind == diffcalc.ObjectKind.Spinner:
            skip("spinner")
            continue
        if origin is None:
            skip("no previous object")
            continue
        if origin.kind == diffcalc.ObjectKind.Spinner:
            # A spinner ends wherever the player stopped spinning, which is not
            # its centre, so the distance travelled from it is unknown.
            skip("travelled from a spinner")
            continue
        if hit.press_time is None:
            skip("no press")
            continue

        origin_x, origin_y = _departure(origin)
        dx_travel = obj.x - origin_x
        dy_travel = obj.y - origin_y
        travel = math.hypot(dx_travel, dy_travel)
        if travel < _MIN_TRAVEL:
            skip("stacked or repeated on one spot")
            continue

        index = bisect.bisect_left(times, hit.press_time)
        if index >= len(frames):
            skip("press beyond the last frame")
            continue
        frame = frames[index]

        dx = frame.x - obj.x
        dy = frame.y - obj.y

        ux = dx_travel / travel
        uy = dy_travel / travel
        along = dx * ux + dy * uy
        # Perpendicular is (-uy, ux). With y increasing downward that is a
        # clockwise quarter turn as seen on screen, so positive `across` is
        # clockwise from the direction of travel. Stated rather than left to be
        # rediscovered, because the convention is invisible in the arithmetic.
        across = dx * -uy + dy * ux

        # From when the player was free to leave, not from when the previous
        # object started. A four-beat slider does not give the next object four
        # beats of travel time — it gives whatever is left after the ball
        # reaches the tail, and using the start time can overstate the time
        # available by an order of magnitude on slow maps.
        departure = origin.end_time
        available = float(obj.time) - departure
        start = bisect.bisect_left(times, departure)
        peak, total, samples = 0.0, 0.0, 0
        arrival: int | None = None
        inside_at_start = True
        for i in range(max(start, 1), index + 1):
            here, before = frames[i], frames[i - 1]
            step = here.time - before.time
            if step > 0:
                speed = math.hypot(here.x - before.x, here.y - before.y) / step
                peak = max(peak, speed)
                total += speed
                samples += 1
            if arrival is None:
                within = math.hypot(here.x - obj.x, here.y - obj.y) <= radius
                if i == max(start, 1) and within:
                    # Already on target when the window opened, so there is no
                    # arrival inside it to measure. Distinguished from "arrived
                    # immediately", which is a real and different observation.
                    inside_at_start = True
                    arrival = here.time
                elif within:
                    inside_at_start = False
                    arrival = here.time

        landings.append(
            Landing(
                index=hit.index,
                time=hit.time,
                dx=dx,
                dy=dy,
                along=along,
                across=across,
                travel=travel,
                heading=math.atan2(dy_travel, dx_travel),
                sector=_sector(math.atan2(dy_travel, dx_travel)),
                available=available,
                peak_speed=peak,
                mean_speed=total / samples if samples else 0.0,
                dwell=(
                    None if arrival is None or inside_at_start else float(hit.press_time - arrival)
                ),
            )
        )

    return LandingSet(landings=landings, radius=radius, skipped=skipped)
