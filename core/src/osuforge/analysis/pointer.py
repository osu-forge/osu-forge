"""Whether cursor motion can be reasoned about at all on this setup.

Before any statement about aim — how far from the center, whether fast movements
drift more, whether pointer gain is off — there is a prior question: does the
cursor position in the replay stand in a fixed relationship to the player's hand?

Often it does not, and the ways it fails are silent. Windows can scale mouse
counts, and it can scale them *by speed*. When it does, "the cursor overshoots
on fast movements" is a description of the acceleration curve rather than of the
player, and every conditional result the analysis reports is a measurement of a
driver setting wearing the player's name.

So this module is a gate, not a computation. It reads three things that are
already available — osu!'s raw input flag, the Windows pointer registry, and the
declared pointer device — and says which of the downstream analyses may run.

# What raw input decides

With raw input on, osu! reads mouse counts from the device and Windows pointer
processing never touches them: the speed slider and Enhance Pointer Precision
stop mattering. With it off, the cursor osu! sees has already been through both.

That single flag therefore decides whether the registry findings are relevant to
aim analysis at all, which is why they are not consulted independently of it.

# What each failure costs

Not all of them cost the same, and collapsing them into one "cannot analyse"
would throw away work that is still valid:

- **Acceleration on, raw input off** — gain is velocity-dependent. This is fatal
  to both the gain estimate *and* the speed-conditioned results, because the
  confound is exactly the variable being conditioned on.
- **Speed slider off the 1:1 notch, raw input off** — gain is a constant
  multiple, which the estimate absorbs, so conditional results survive. What
  does not survive is the recommendation: counts are being scaled and discarded
  before osu! sees them, and changing DPI to compensate for a setting that is
  throwing away resolution is the wrong fix.
- **No declared pointer** — everything runs; only the translation from a
  percentage into DPI counts is unavailable.
- **Lift-off distance** — nothing is blocked, but a caveat is attached, because
  the confound is episodic rather than constant: see below.

# Why lift-off distance gets a caveat rather than a verdict

A sensor stops tracking at some height on the way up and resumes at some height
on the way down. Motion recorded inside either window is cursor movement the
hand never aimed — the mouse is being carried, not aimed.

That does not spread evenly over a play. It concentrates on repositioning, which
follows long jumps and happens near the edges of the playfield, which is exactly
where a distance- and direction-conditioned result reads. So a high lift-off
distance does not invalidate the measurement; it makes a particular *pattern* in
the measurement have a second possible cause, and the report has to say so where
that pattern appears rather than refusing the whole analysis.

It is also not measurable from a replay: the recorded cursor motion during a
lift is indistinguishable from aim. Detecting lift events at all is separate
work; until then the declared distance is the only thing to go on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from osuforge.hardware import LIFT_OFF_OF_CONCERN_MM, Mouse, Profile, Tracking
from osuforge.probes.mouse import SENSITIVITY_ONE_TO_ONE, WindowsMouseSettings

__all__ = ["Chain", "assess"]


@dataclass(frozen=True, slots=True)
class Chain:
    """What sits between the player's hand and the cursor in the replay."""

    raw_input: bool | None
    """osu!'s `RawInput`. `None` when the config could not be read, which is
    treated as unknown rather than as either answer."""

    windows: WindowsMouseSettings | None
    """The registry settings, or `None` off Windows or when the probe failed."""

    pointer: Mouse | None

    tracking: Tracking | None = None

    blockers: list[str] = field(default_factory=list)
    """Reasons no aim analysis may run, in the words a report would use."""

    cautions: list[str] = field(default_factory=list)
    """Reasons a recommendation must not be issued, though measurement stands."""

    caveats: list[str] = field(default_factory=list)
    """Alternative explanations that must travel with particular results.

    Distinct from a caution because they neither stop a measurement nor stop a
    recommendation — they attach to the reading of one. A high lift-off distance
    is the case this exists for: it gives long-jump and edge results a second
    possible cause without making any of them wrong.
    """

    @property
    def bypasses_windows(self) -> bool:
        """Whether osu! reads counts before Windows processes them."""
        return bool(self.raw_input)

    @property
    def may_measure(self) -> bool:
        """Whether conditional aim results mean anything on this setup."""
        return not self.blockers

    @property
    def may_recommend(self) -> bool:
        """Whether a pointer change may be recommended.

        Stricter than :attr:`may_measure` on purpose. Measuring something under
        a caveat is useful; recommending a change to the wrong knob is not.
        """
        return self.may_measure and not self.cautions

    @property
    def may_convert_to_counts(self) -> bool:
        """Whether a residual percentage can be expressed in DPI counts."""
        return self.pointer is not None

    def summary(self) -> str:
        if self.blockers:
            return "Aim analysis is not valid on this setup: " + "; ".join(self.blockers)
        if self.cautions:
            return "Aim can be measured but no pointer change is recommended: " + "; ".join(
                self.cautions
            )
        if not self.may_convert_to_counts:
            return (
                "The pointer chain is clean. No mouse is declared, so a gain residual "
                "stays a percentage rather than becoming a number of DPI counts."
            )
        assert self.pointer is not None
        detail = self.pointer.describe()
        if self.tracking is not None and self.tracking.known:
            detail += f", {self.tracking.describe()}"
        return f"The pointer chain is clean: {detail}."


def assess(
    *,
    raw_input: bool | None,
    windows: WindowsMouseSettings | None,
    profile: Profile,
) -> Chain:
    """Decide what the cursor data on this setup will support."""
    blockers: list[str] = []
    cautions: list[str] = []

    if raw_input is None:
        cautions.append(
            "osu!'s raw input setting could not be read, so whether Windows pointer "
            "processing is in the path is unknown"
        )
    elif not raw_input:
        # Only here do the registry settings reach the cursor osu! sees. With
        # raw input on they are real findings about the desktop and irrelevant
        # to this.
        if windows is None:
            cautions.append(
                "raw input is off, so Windows pointer processing is in the path, and "
                "the registry could not be read to say what it is doing"
            )
        else:
            if windows.acceleration_on:
                blockers.append(
                    "raw input is off and pointer acceleration is on, so how far the "
                    "cursor travels depends on how fast the hand moved — which is the "
                    "variable these results condition on, so any speed-dependent "
                    "finding would be a measurement of the acceleration curve"
                )
            if not windows.one_to_one:
                cautions.append(
                    f"raw input is off and the Windows pointer speed slider is at "
                    f"{windows.sensitivity} rather than {SENSITIVITY_ONE_TO_ONE}, so mouse "
                    "counts are being scaled and discarded before osu! sees them; "
                    "compensating for that with DPI would be correcting the wrong thing"
                )

    caveats: list[str] = []
    tracking = profile.tracking
    if tracking is None or not tracking.known:
        caveats.append(
            "the mouse's lift-off distance is not declared, so motion recorded while "
            "the mouse was being lifted to reposition cannot be told apart from aim"
        )
    elif tracking.of_concern:
        assert tracking.lift_off_mm is not None
        caveats.append(
            f"the sensor keeps tracking to {tracking.lift_off_mm:g}mm "
            f"(above {LIFT_OFF_OF_CONCERN_MM:g}mm), so some cursor motion during "
            "repositioning is recorded as though it were aim — which lands on long "
            "jumps and playfield edges rather than spreading evenly"
        )

    pointer = profile.mouse
    if profile.pointer is not None and pointer is None:
        # Unreachable while Mouse is the only variant. Present so that adding a
        # tablet is a change here rather than a scalar gain estimate quietly
        # running over absolute-positioning data and returning a number.
        blockers.append(
            "the declared pointer is not a relative device, so pointer gain is not a "
            "quantity it has"
        )

    return Chain(
        raw_input=raw_input,
        windows=windows,
        pointer=pointer,
        tracking=tracking,
        blockers=blockers,
        cautions=cautions,
        caveats=caveats,
    )
