"""Turning a raw frame stream into something you can measure.

The parser preserves what the file says. This module says what it means.

Every event produced here carries **how uncertain its timestamp is**.
osu!stable samples input once per rendered frame, so a key press is recorded at
the first frame at or after the finger actually moved. The gap back to the
previous sample is therefore an error bar on that timestamp, not an incidental
detail.

That matters more than it sounds. Measured across a real replay folder, the gap
at a press has a median of 9 ms and a 90th percentile of 16 ms. Anything derived
from these timestamps carries an error bar of that size, which is comparable to
the effects an offset analysis is trying to detect. A metric that quietly drops
it is a metric that will overstate what it knows.

The uncertainty is the gap between samples, deliberately *not* the frame's own
delta field. On the first frame the delta is the audio lead-in rather than a
sampling interval — one real replay records -22,674 there — so using it as an
error bar would place the press twenty-two seconds in the future. Eight of 58
real replays have a press on that first frame, so this is not hypothetical.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from osuforge.replay.model import Keys, Replay, ReplayFrame

__all__ = ["Button", "Edge", "KeyEvent", "NormalizedFrames", "normalize"]

# The marker frames osu! writes at the head of every replay sit here. No cursor
# can occupy it, which is what makes it a marker rather than a position.
_MARKER_X = 256.0
_MARKER_Y = -500.0


class Button(StrEnum):
    """Which physical control produced a press.

    Distinct from the judgement bit. ``K1`` and ``M1`` both set the same
    judgement bit, and telling them apart needs the raw key mask.
    """

    K1 = "k1"
    K2 = "k2"
    M1 = "m1"
    M2 = "m2"

    @property
    def is_mouse(self) -> bool:
        return self in (Button.M1, Button.M2)

    @property
    def side(self) -> Literal["left", "right"]:
        """Which of the two judgement bits this button drives."""
        return "left" if self in (Button.K1, Button.M1) else "right"


Edge = Literal["down", "up"]


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """A press or release, with the uncertainty on when it happened."""

    time: int
    uncertainty: int | None
    """How far before :attr:`time` the event might really have happened.

    This is the gap to the previous sample, so the true event lies somewhere in
    ``(time - uncertainty, time]``.

    ``None`` means unknown, and that is not the same as zero. It happens when
    the press was observed on the very first frame — there is no previous
    sample to bound it against. Deliberately not collapsed to 0, which would
    read as "measured precisely"; a caller has to decide what to do with an
    unbounded event rather than silently treating it as exact.

    Note this is *not* the frame's raw delta. On the first frame the delta is
    the audio lead-in — one real replay carries -22,674 there — and using it as
    an error bar would place the press twenty-two seconds in the future.
    """

    edge: Edge
    button: Button
    frame_index: int
    """Index into :attr:`NormalizedFrames.frames`."""

    @property
    def earliest_possible(self) -> int | None:
        """Lower bound on the true time, or ``None`` when unbounded."""
        return None if self.uncertainty is None else self.time - self.uncertainty


@dataclass(frozen=True, slots=True)
class NormalizedFrames:
    """A cleaned frame stream, plus what had to be cleaned.

    The counts are reported rather than swallowed. A replay whose stream needed
    unusual handling is one whose derived numbers deserve less confidence, and
    that is only visible if the handling is counted.
    """

    frames: list[ReplayFrame]
    events: list[KeyEvent] = field(default_factory=list)

    markers_removed: int = 0
    """Leading ``(256, -500)`` frames dropped. Two in a normal replay."""

    backwards_steps: int = 0
    """Frames after the lead-in whose time went backwards.

    Real and rare: measured at one step of -1 to -16 ms in four of 57 replays,
    about 2% into the stream, where playback leaves the lead-in section. Many of
    these, or large ones, would mean something else is wrong.
    """

    start_time: int = 0
    """Time of the first frame after the markers. Usually negative — it records
    the audio lead-in or a skip."""

    @property
    def press_events(self) -> list[KeyEvent]:
        return [e for e in self.events if e.edge == "down"]

    def button_counts(self) -> dict[Button, int]:
        counts = dict.fromkeys(Button, 0)
        for event in self.press_events:
            counts[event.button] += 1
        return counts

    def press_uncertainties(self) -> list[int]:
        """Known timing uncertainties, for reporting an error bar.

        Presses whose uncertainty is unknown are omitted rather than counted as
        zero, so a summary of this list is not quietly optimistic. Their number
        is :attr:`unbounded_presses`.
        """
        return [e.uncertainty for e in self.press_events if e.uncertainty is not None]

    @property
    def unbounded_presses(self) -> int:
        """Presses observed on the first frame, with no previous sample."""
        return sum(1 for e in self.press_events if e.uncertainty is None)


def _is_marker(frame: ReplayFrame) -> bool:
    return frame.x == _MARKER_X and frame.y == _MARKER_Y


def _button_for(bit: int, keys: Keys) -> Button:
    """Which control drove a judgement bit on this frame.

    ``K1`` always sets ``M1`` as well, so the keyboard bit being present is what
    distinguishes a key press from a mouse click. Checking only ``M1`` would
    call every keyboard press a click; checking only ``K1`` would lose every
    real click — a real corpus has 465 of them.
    """
    if bit == Keys.M1:
        return Button.K1 if keys & Keys.K1 else Button.M1
    return Button.K2 if keys & Keys.K2 else Button.M2


def normalize(replay: Replay) -> NormalizedFrames:
    """Strip markers, extract key events, and report what needed handling."""
    raw = replay.frames

    # Markers only appear at the head. Scanning the whole stream for them would
    # drop a legitimate frame that happens to land on the same coordinates,
    # which is unlikely but not impossible.
    marker_count = 0
    for frame in raw:
        if not _is_marker(frame):
            break
        marker_count += 1

    frames = raw[marker_count:]
    if not frames:
        return NormalizedFrames(frames=[], markers_removed=marker_count)

    # The first surviving frame carries the lead-in or skip as a large negative
    # delta. It is an absolute starting point, not something to add to anything,
    # and the parser has already accumulated it correctly — this only records it.
    start_time = frames[0].time

    backwards = sum(
        1 for previous, frame in itertools.pairwise(frames) if frame.time < previous.time
    )

    events: list[KeyEvent] = []
    previous_mask = 0

    for index, frame in enumerate(frames):
        mask = frame.keys.judgement_mask

        # The gap to the previous sample, which is what bounds when the event
        # really happened. Deliberately not the frame's own delta: on the first
        # frame that is the audio lead-in rather than a sampling interval, and
        # on a backwards step it is negative. Both would be nonsense as an
        # error bar, and eight of 58 real replays have a press on frame zero.
        if index == 0:
            uncertainty: int | None = None
        else:
            gap = frame.time - frames[index - 1].time
            uncertainty = gap if gap >= 0 else None

        # Masking to M1|M2 collapses the K1/M1 and K2/M2 pairings, so a single
        # physical press produces one edge rather than two. Edge-detecting on
        # all four bits would double-count every keyboard press.
        pressed = mask & ~previous_mask
        released = previous_mask & ~mask

        for bit in (Keys.M1, Keys.M2):
            if pressed & bit:
                events.append(
                    KeyEvent(
                        time=frame.time,
                        uncertainty=uncertainty,
                        edge="down",
                        button=_button_for(bit, frame.keys),
                        frame_index=index,
                    )
                )
            if released & bit:
                events.append(
                    KeyEvent(
                        time=frame.time,
                        uncertainty=uncertainty,
                        edge="up",
                        # A release is attributed to whatever was held, which is
                        # read from the previous frame — this frame no longer
                        # has the bit set.
                        button=_button_for(bit, frames[index - 1].keys if index else frame.keys),
                        frame_index=index,
                    )
                )

        previous_mask = mask

    return NormalizedFrames(
        frames=frames,
        events=events,
        markers_removed=marker_count,
        backwards_steps=backwards,
        start_time=start_time,
    )
