"""Replaying a `.osr` against its beatmap to recover per-hit timing errors.

osu! shows an average error on the results screen and then forgets it. To say
anything about a player's offset you need the individual errors, and the only
way to get them from a stable replay is to judge the recorded key presses
against the beatmap the way the game did.

This is the highest-risk component in the project, because a simulator that is
quietly wrong produces confident, plausible, wrong numbers — and everything
downstream believes them. It ships with :mod:`osuforge.replay.validate`, which
checks each simulation against the judgement counts in the replay's own header
and refuses to let a replay whose counts do not reproduce contribute to any
recommendation. Read that module before trusting anything here.

# What is simulated and what is assumed

Circles are simulated: a press is matched to an object and the grade follows
from the timing error. That is the part offset analysis rests on.

Sliders are simulated too, part by part. A slider scores on its head, each tick
along its path, each repeat point and its tail, and each of those is collected
when the cursor is inside the follow circle with a key held at that moment. The
grade then follows from the fraction collected. This is not a refinement: an
earlier version scored a slider 300 whenever its head was hit, and across the
whole local corpus that produced between 19 and 107 too many 300s per replay,
which alone was enough to fail every replay's validation.

Spinners are **assumed** to score 300, and are the only results still marked
``grade_inferred``. Nothing about spinning is modelled.

# The open questions

Several details could not be settled from documentation, and are stated rather
than hidden:

1. **Rounding of the hit windows.** stable compares against an integer window;
   whether it truncates or rounds is unverified. :func:`simulate` rounds.
   :mod:`osuforge.replay.validate` reports how many beatmaps in a corpus even
   have a fractional window, which is what decides whether the question matters.

   The related question of whether the comparison is strict *is* settled. 318
   circle hits in the local corpus land exactly on a window edge, which is
   enough to tell the two apart: with ``<`` the corpus ends up 65 objects over
   on 300s, and with ``<=`` it ends up 358 over. It is strict. circleguard
   disagrees here and is where the question came from — 22 of the 31 circles the
   two implementations grade differently are boundary cases of exactly this
   shape, and the replay headers side against it.
2. **Notelock.** The model here is that a press goes to the earliest object
   still inside its own window, and that a press arriving before that object's
   window opens is discarded rather than passed to a later object. That is the
   behaviour notelock produces, but stable's implementation releases the lock on
   an object's end time, which is not obviously the same rule in every case.
3. **Tracking is sampled at the parts, not continuously.** osu! evaluates
   tracking every frame; this evaluates it only where a part is judged. A cursor
   that leaves the follow circle and returns between two ticks keeps the wide
   radius here and would have lost it in the game.

   This is the largest known gap, and it is measurable: the simulation collects
   every part on about 283 more sliders than the game does, out of 15,359. The
   fix needs the ball's position at arbitrary times, which means exposing the
   slider path through the bindings rather than only its scoring points.
4. **When a part is judged.** The game checks a tick on the update its clock
   passes the tick's time, so the cursor position used here is the one on the
   first frame at or after it — not an interpolation, which would be smoother
   than the game and therefore wrong in the player's favour.
5. **A hit slider head keeps blocking.** ppy's own note, in the description of
   the classic mod: on stable, a slider head that has already been hit blocks
   input from reaching objects underneath it until the slider is fully judged.
   Not implemented — here the head stops blocking as soon as it is hit.

# What was read rather than reconstructed

The slider rules above were first reconstructed from documentation and then
checked against ppy/osu, which is MIT-licensed and is the authority for this.
Doing it in that order was a mistake worth recording, because the reconstruction
had a constant fitted to the corpus that turned out to be wrong: the follow
circle was set to 2.0 because at 2.4 the simulation produced 400 too many 300s,
and the real answer was that `FOLLOW_AREA` is 2.4 and tracking is a state
machine. Acquiring it needs the cursor within the object's own radius; only
keeping it allows the wider one, and only while the key that hit the head stays
down. The fit was good and the model was wrong, which is the argument against
fitted constants in general.

Two divergences between stable and lazer showed up in the process. lazer's
`HitWindows.ResultFor` compares inclusively; the replay headers say stable is
strict. And lazer's windows are raw doubles where stable's are integers. Where
the two disagree, the corpus decides, because it is stable that produced it.

Both are decided empirically by how well the simulation reproduces the header
counts, which is the only evidence available without the game's source.

# Why simulate at all, when the game already knows

osu! computes hit errors itself — that is what the error bar on screen is drawn
from — and the memory engine will eventually be able to read them. Where that is
available it is the better source for offset analysis: no press has to be paired
with an object, so the whole class of error this module can make disappears. And
live polling samples the cursor far faster than the ~60 Hz a replay records at.

Simulation does not become redundant.

- A replay folder is history. Live collection starts when the tool is first run;
  the replays already on disk are the only record of everything before that.
- Nothing here needs the engine, so the offline analysis keeps working through
  an osu! update that breaks a signature, on a machine where the engine trips an
  antivirus, and for anyone who would rather not have a process read another
  process's memory at all.
- The game does not compute the rest of it. Which key was used, where the cursor
  was relative to the object when it was pressed, what the pattern around it
  looked like — those come from the replay whatever else is running.
- Running both at once is a per-object oracle. The game's own hit errors against
  this module's, on the same play, is a stronger check than any of the ones
  described above, and it costs nothing once the engine exists.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from enum import IntEnum

import osu_forge_diffcalc as diffcalc

from osuforge.replay.frames import Button, NormalizedFrames, normalize
from osuforge.replay.model import Keys, Replay

__all__ = [
    "BREAK_MS",
    "Grade",
    "Hit",
    "Simulation",
    "simulate",
]

BREAK_MS = 2000
"""A gap this long stops being rhythm and starts being a rest.

The first object after one is excluded from timing analysis: the player is
re-entering from nothing rather than continuing a pattern, and their error there
measures something different.
"""

AFTER_SPINNER_MS = 1000
"""How long after a spinner ends to keep excluding objects.

The cursor is wherever the spin left it and the hand is still recovering, so
these errors are not comparable with the rest.
"""

FOLLOW_CIRCLE_SCALE = 2.4
"""The follow circle's radius, as a multiple of the object radius.

`DrawableSliderBall.FOLLOW_AREA` in ppy/osu, read rather than inferred.

It applies only while already tracking. Acquiring tracking needs the cursor
inside the object's own radius; `SliderInputManager` calls
`IsMouseInFollowArea(Tracking)` and expands the radius only when that is
already true.

This was 2.0 for a while, fitted to the corpus because at 2.4 the simulation
produced 400 too many 300s. The fitted value was wrong and the sweep was
measuring a compensating error: the model tested every part independently at the
wide radius, which is too generous, and shrinking the radius hid it. Reading the
source found the state machine instead. Worth remembering as an argument against
calibrated constants generally — the fit was good and the model was wrong.
"""


class Grade(IntEnum):
    """What an object scored. The values are osu!'s own."""

    MISS = 0
    FIFTY = 50
    HUNDRED = 100
    THREE_HUNDRED = 300


@dataclass(frozen=True, slots=True)
class Hit:
    """One object's outcome."""

    index: int
    """Position in the beatmap's object list."""

    time: int
    """The object's time, in milliseconds of map time."""

    kind: diffcalc.ObjectKind
    grade: Grade

    grade_inferred: bool
    """True when the grade was assumed rather than derived from a press.

    Set for every slider and spinner. See the module docstring for what is
    assumed about each.
    """

    error: int | None = None
    """Press time minus object time, in milliseconds of **map** time.

    Negative is early. ``None`` for a miss, where there is no press to measure.
    Divide by the replay's rate for real time — under Double Time the same
    number of map milliseconds is fewer real ones.
    """

    press_time: int | None = None
    button: Button | None = None

    uncertainty: int | None = None
    """How far before the press it might really have happened.

    Carried through from the replay frame that recorded it. ``None`` means
    unbounded, not zero — see :class:`osuforge.replay.frames.KeyEvent`.
    """

    aim_error: float | None = None
    """Distance from the object's centre when it was pressed, in radii.

    0 is dead centre and 1 is the edge; a hit cannot exceed 1, because a press
    outside the radius does not register. ``None`` for a miss.

    The cursor position is the one recorded on the press's own frame, which is
    not an approximation: stable reads the mouse and the key state in the same
    update, so that frame is the game's own view of where the cursor was.
    """

    parts_collected: int | None = None
    """For a slider, how many of its scoring points were collected, head included.

    ``None`` for anything that is not a slider. Carried because combo counts
    every one of them, so "how much combo did that cost" cannot be answered from
    the grade alone.
    """

    parts_total: int | None = None

    excluded: str | None = None
    """Why this hit is not usable for timing analysis, or ``None`` if it is.

    A reason rather than a flag, so a corpus can be audited for *what* it
    dropped instead of only how much.
    """

    @property
    def usable_for_timing(self) -> bool:
        return self.error is not None and self.excluded is None


@dataclass(frozen=True, slots=True)
class Simulation:
    """The result of judging one replay against one beatmap."""

    hits: list[Hit]
    frames: NormalizedFrames

    rate: float
    """Map time multiplied by this gives real time."""

    windows: tuple[int, int, int]
    """The rounded 300/100/50 half-widths actually used, in map milliseconds."""

    fractional_windows: bool
    """Whether rounding the windows changed them.

    When false, the unresolved question of how stable rounds cannot have
    affected this replay.
    """

    presses: int = 0

    presses_off_target: int = 0
    """Presses whose timing suited the next object but whose cursor was not on it.

    These are the player missing, and they have to be counted as such: a press
    that judged an object it was nowhere near would turn every mistimed attempt
    into a hit.
    """

    presses_out_of_window: int = 0
    """Presses that fell outside every object's timing window.

    Some are normal — tapping through a break, or the second half of a
    double-tap. A large fraction means the object list and the press list have
    drifted out of alignment and the errors describe the wrong objects.
    """

    @property
    def unmatched_presses(self) -> int:
        return self.presses_off_target + self.presses_out_of_window

    def counts(self) -> dict[Grade, int]:
        counts = dict.fromkeys(Grade, 0)
        for hit in self.hits:
            counts[hit.grade] += 1
        return counts

    @property
    def circle_misses(self) -> int:
        """Circles that no press judged.

        Separated from the total because a circle's judgement is reproducible
        from a replay and a slider's is not, which makes this the one miss count
        that can be checked against the game's.
        """
        return sum(
            1
            for hit in self.hits
            if hit.grade is Grade.MISS and hit.kind is diffcalc.ObjectKind.Circle
        )

    @property
    def sliders(self) -> int:
        return sum(1 for hit in self.hits if hit.kind is diffcalc.ObjectKind.Slider)

    def timing_errors(self) -> list[float]:
        """Usable hit errors in **real** milliseconds, early negative."""
        return [
            hit.error / self.rate
            for hit in self.hits
            if hit.usable_for_timing and hit.error is not None
        ]

    def exclusions(self) -> dict[str, int]:
        reasons: dict[str, int] = {}
        for hit in self.hits:
            if hit.excluded is not None:
                reasons[hit.excluded] = reasons.get(hit.excluded, 0) + 1
        return reasons


@dataclass(slots=True)
class _Pending:
    """An object waiting to be judged, with what the matcher needs to know."""

    index: int
    time: int
    x: float
    y: float
    kind: diffcalc.ObjectKind
    excluded: str | None


@dataclass(slots=True)
class _Head:
    """What a press did to an object's head, before the slider parts are read."""

    hit: bool = False
    error: int | None = None
    press_time: int | None = None
    button: Button | None = None
    uncertainty: int | None = None
    aim_error: float | None = None


class _FrameSampler:
    """Reads the replay at an arbitrary time the way the game would.

    The game judges a slider tick on the update its clock passes the tick's
    time, so what matters is the first frame at or after it. Interpolating
    between frames instead would put the cursor where it never was recorded and
    would be smoother than the game — an error in the player's favour.
    """

    __slots__ = ("_frames", "_times")

    def __init__(self, frames: NormalizedFrames) -> None:
        self._frames = frames.frames
        # A handful of real replays step backwards by a millisecond or two where
        # playback leaves the lead-in. `bisect` needs a sorted sequence, so the
        # search key is a running maximum. The frames themselves are untouched:
        # this changes which frame a lookup lands on by at most the size of the
        # step, and only near it.
        times: list[int] = []
        highest = -1 << 62
        for frame in self._frames:
            highest = max(highest, frame.time)
            times.append(highest)
        self._times = times

    def at(self, time: float, required: int) -> tuple[float, float, bool] | None:
        """Cursor position and whether a qualifying key was held, at `time`.

        `required` is the judgement bit that has to be set — the one belonging to
        the key that hit the slider's head. osu! only tracks while *that* key is
        held, so a player alternating through a stream drops the slider even
        though a key is down the whole time. Pass the full mask to accept any.
        """
        index = bisect.bisect_left(self._times, time)
        if index >= len(self._frames):
            return None
        frame = self._frames[index]
        return (frame.x, frame.y, bool(frame.keys.judgement_mask & required))


def _grade_for(magnitude: int, windows: tuple[int, int, int]) -> Grade:
    """Grade a timing error already known to be inside the 50 window."""
    window_300, window_100, _ = windows
    if magnitude < window_300:
        return Grade.THREE_HUNDRED
    if magnitude < window_100:
        return Grade.HUNDRED
    return Grade.FIFTY


def _slider_grade(collected: int, total: int) -> Grade:
    """A slider's grade from how many of its parts were collected."""
    if total <= 0 or collected <= 0:
        return Grade.MISS
    if collected == total:
        return Grade.THREE_HUNDRED
    if collected * 2 >= total:
        return Grade.HUNDRED
    return Grade.FIFTY


def _exclusion_reasons(objects: list[diffcalc.HitObject]) -> list[str | None]:
    """Why each object cannot contribute a timing measurement, if it cannot.

    Judgement covers every object regardless — the counts have to reproduce the
    header. This only decides what feeds the statistics.
    """
    reasons: list[str | None] = []
    spinner_ended: float | None = None
    previous_end: float | None = None

    for index, obj in enumerate(objects):
        reason: str | None = None
        if obj.kind is diffcalc.ObjectKind.Spinner:
            reason = "spinner"
        elif obj.kind is diffcalc.ObjectKind.Slider:
            # The head's error is real and recorded, but slider leniency means
            # the player is not aiming at the same target a circle presents.
            reason = "slider_head"
        elif index > 0 and objects[index - 1].time == obj.time:
            # Two objects at the same instant. One press cannot be attributed to
            # one of them, so neither error means anything.
            reason = "simultaneous_objects"
        elif previous_end is not None and obj.time - previous_end > BREAK_MS:
            reason = "after_break"
        elif spinner_ended is not None and obj.time - spinner_ended <= AFTER_SPINNER_MS:
            reason = "after_spinner"

        reasons.append(reason)

        if obj.kind is diffcalc.ObjectKind.Spinner:
            spinner_ended = obj.end_time
        previous_end = obj.end_time

    # `simultaneous_objects` has to be symmetric: the second of a pair is caught
    # above, the first is not, because at that point the pair is not yet visible.
    for index in range(1, len(objects)):
        if reasons[index] == "simultaneous_objects" and reasons[index - 1] is None:
            reasons[index - 1] = "simultaneous_objects"

    return reasons


def simulate(replay: Replay, beatmap: diffcalc.Beatmap) -> Simulation:
    """Judge a replay against its beatmap.

    `beatmap` is the unmodified map: the replay's mods are applied here, so a
    caller cannot forget to. Hard Rock and Easy change the hit windows and the
    object positions, and a simulation run without them would produce errors
    that look like a badly mistimed player.
    """
    played = beatmap.with_mods(int(replay.mods))
    frames = normalize(replay)
    presses = frames.press_events

    raw_windows = (
        played.hit_window_300,
        played.hit_window_100,
        played.hit_window_50,
    )
    windows = (round(raw_windows[0]), round(raw_windows[1]), round(raw_windows[2]))
    fractional = any(raw != rounded for raw, rounded in zip(raw_windows, windows, strict=True))
    window_50 = windows[2]

    objects = played.objects
    reasons = _exclusion_reasons(objects)
    radius = played.radius
    radius_squared = radius * radius
    follow_squared = (radius * FOLLOW_CIRCLE_SCALE) ** 2

    # Spinners take no press, so they are not in the queue a press is matched
    # against. Leaving them in would let a press near the end of a long spin be
    # attributed to the spin instead of to the object after it.
    queue = [
        _Pending(index, obj.time, obj.x, obj.y, obj.kind, reasons[index])
        for index, obj in enumerate(objects)
        if obj.kind is not diffcalc.ObjectKind.Spinner
    ]

    heads: list[_Head] = [_Head() for _ in objects]
    cursor = 0
    off_target = 0
    out_of_window = 0

    for press in presses:
        frame = frames.frames[press.frame_index]
        outcome = "out_of_window"
        while cursor < len(queue):
            pending = queue[cursor]
            error = press.time - pending.time
            if error >= window_50:
                # This object can no longer be hit, and it stops blocking the
                # next one. Re-test the same press against that.
                cursor += 1
                continue
            if error <= -window_50:
                # Too early for the earliest object still open, so too early for
                # every object after it. The press goes nowhere.
                break

            # Timing alone does not judge anything. osu! requires the cursor to
            # be on the object, and a press that is not does nothing at all: the
            # object stays alive and a later press can still hit it. Without this
            # check every mistimed attempt becomes a hit, and the simulation
            # produced almost no misses on any of the 57 local replays.
            distance_squared = (frame.x - pending.x) ** 2 + (frame.y - pending.y) ** 2
            if distance_squared > radius_squared:
                outcome = "off_target"
                break

            heads[pending.index] = _Head(
                hit=True,
                error=error,
                press_time=press.time,
                button=press.button,
                uncertainty=press.uncertainty,
                aim_error=math.sqrt(distance_squared) / radius,
            )
            cursor += 1
            outcome = "hit"
            break
        if outcome == "off_target":
            off_target += 1
        elif outcome == "out_of_window":
            out_of_window += 1

    sampler = _FrameSampler(frames)
    hits = [
        _judge(
            index,
            obj,
            heads[index],
            reasons[index],
            windows,
            sampler,
            radius_squared,
            follow_squared,
        )
        for index, obj in enumerate(objects)
    ]

    return Simulation(
        hits=hits,
        frames=frames,
        rate=replay.rate,
        windows=windows,
        fractional_windows=fractional,
        presses=len(presses),
        presses_off_target=off_target,
        presses_out_of_window=out_of_window,
    )


def _judge(
    index: int,
    obj: diffcalc.HitObject,
    head: _Head,
    excluded: str | None,
    windows: tuple[int, int, int],
    sampler: _FrameSampler,
    radius_squared: float,
    follow_squared: float,
) -> Hit:
    """Turn one object's head outcome into its final grade."""
    if obj.kind is diffcalc.ObjectKind.Spinner:
        # Not modelled. Players at any level clear spinners essentially always,
        # so assuming otherwise would be the larger error — but it is an
        # assumption, and `grade_inferred` says so.
        return Hit(
            index=index,
            time=obj.time,
            kind=obj.kind,
            grade=Grade.THREE_HUNDRED,
            grade_inferred=True,
            excluded=excluded,
        )

    collected: int | None
    total: int | None
    if obj.kind is diffcalc.ObjectKind.Slider:
        # The head is one scoring part among several, and a slider whose head
        # was missed still scores off ticks collected while sliding through it.
        collected = 1 if head.hit else 0
        # Tracking is a state machine, not a per-part test. The follow circle
        # only applies once you are already following: acquiring tracking needs
        # the cursor inside the object's own radius, and keeping it allows the
        # wider one. Testing every part independently at the wide radius is too
        # generous, and ending the slider at the first part missed is too harsh —
        # measured on a real corpus, those give 400 too many 300s and 293 too
        # many misses respectively.
        # Which key has to stay down. When the head was hit, it is the key that
        # hit it; when it was not, any key can acquire tracking.
        required = int(Keys.M1 | Keys.M2)
        if head.button is not None:
            required = int(Keys.M1 if head.button.side == "left" else Keys.M2)

        tracking = head.hit
        for part in obj.parts:
            sample = sampler.at(part.time, required)
            if sample is None:
                tracking = False
                continue
            x, y, held = sample
            limit = follow_squared if tracking else radius_squared
            tracking = held and (x - part.x) ** 2 + (y - part.y) ** 2 <= limit
            if tracking:
                collected += 1
        total = 1 + len(obj.parts)
        grade = _slider_grade(collected, total)
    elif head.hit and head.error is not None:
        collected = total = None
        grade = _grade_for(abs(head.error), windows)
    else:
        collected = total = None
        grade = Grade.MISS

    return Hit(
        index=index,
        time=obj.time,
        kind=obj.kind,
        grade=grade,
        grade_inferred=False,
        parts_collected=collected,
        parts_total=total,
        error=head.error,
        press_time=head.press_time,
        button=head.button,
        uncertainty=head.uncertainty,
        aim_error=head.aim_error,
        excluded=excluded,
    )
