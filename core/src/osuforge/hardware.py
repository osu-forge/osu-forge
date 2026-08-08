"""What the player had to tell us, because no file records it.

Almost everything this tool knows it reads: `osu!.cfg` for settings, the
registry for pointer behaviour, `osu!.db` for beatmap offsets, the replay for
what the cursor did. Mouse DPI is different. A replay stores the cursor position
in osu! pixels and never the raw counts behind it, so the hardware's counts per
inch leave no trace in anything on disk. Neither does the polling rate.

That matters more than it sounds. DPI and osu!'s `MouseSpeed` multiply into a
single pointer gain, and the replay observes only the product. Nothing in this
package can therefore say "your DPI is wrong" — it can only estimate the
residual gain error as a percentage. Turning that percentage into a number of
DPI counts needs the denominator, and the denominator has to be asked for.

# Declared, not measured

Everything here is the player's word. It is stored with that status attached so
that a finding built on it can say so: the residual gain percentage is measured
and stands on its own, while the DPI translation beside it rests on a number
this tool cannot check. Conflating the two would let a typo in a settings field
turn a real measurement into a confident wrong recommendation.

# Separate axes

`dpi_x` and `dpi_y` are stored separately because mice and drivers allow them to
differ, and when they do, a scalar model of pointer gain is simply the wrong
model — vertical hand motion produces proportionally more cursor motion than
horizontal. Where they differ the analysis is expected to split by axis rather
than average across one, and :attr:`Mouse.anisotropy` exists so a report can say
by how much.

# Mice only, and the shape says so

A tablet is not a mouse with different numbers. A mouse is a **relative** device:
it reports displacement, that displacement is scaled by a gain, and asking "is
the gain right" is a meaningful question. A tablet is **absolute**: a point on
its active area maps to a point on the screen, there is no gain to be wrong, and
the analogous question is whether the active area's shape matches the
playfield's. Running the gain estimator over tablet data would produce a
plausible number that means nothing.

So the pointer is stored as a tagged object rather than as bare DPI fields, and
an unrecognised tag is an error rather than something quietly skipped. When
tablet support lands it becomes a second variant beside :class:`Mouse`, and
everything that consumes a pointer has to say what it does with it — which is
the point of writing it this way before there is a second variant.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

__all__ = [
    "ASYMMETRIC_CUTOFF_PRESETS",
    "LIFT_OFF_OF_CONCERN_MM",
    "PROFILE_ENV",
    "Mouse",
    "Pointer",
    "Profile",
    "ProfileError",
    "Tracking",
    "default_profile_path",
    "load",
    "save",
]

PROFILE_ENV = "OSU_FORGE_PROFILE"
"""Environment variable overriding where the profile is read and written."""

_SCHEMA = 1

# A mouse reporting outside this range is a typo, not a mouse. The low end is
# below any shipping sensor; the high end is above the marketing numbers on
# sensors that advertise more than they resolve.
_MIN_DPI = 100
_MAX_DPI = 32_000

# Below this the two axes are equal for every purpose here: a quarter of a
# percent of gain is far under the noise in any estimate this feeds.
_ANISOTROPY_EPSILON = 0.0025


class ProfileError(ValueError):
    """The profile could not be read, or holds a value that cannot be true."""


@dataclass(frozen=True, slots=True)
class Mouse:
    """A relative pointing device, as the player described it.

    Not verified and not verifiable from anything on disk. Treated as an input
    to a conversion, never as evidence in its own right.
    """

    kind: ClassVar[str] = "mouse"

    relative: ClassVar[bool] = True
    """Reports displacement rather than position, so pointer gain is a
    meaningful quantity. False for a tablet, where it is not."""

    dpi_x: int
    dpi_y: int

    def __post_init__(self) -> None:
        for axis, value in (("dpi_x", self.dpi_x), ("dpi_y", self.dpi_y)):
            if not _MIN_DPI <= value <= _MAX_DPI:
                raise ProfileError(
                    f"{axis}={value} is outside {_MIN_DPI}-{_MAX_DPI}; that is a typo rather "
                    "than a mouse, and a wrong denominator turns a real measurement into a "
                    "confident wrong recommendation"
                )

    @property
    def anisotropy(self) -> float:
        """How much more sensitive the vertical axis is, as a fraction.

        Positive means vertical moves further than horizontal for the same hand
        motion. Zero for the usual case of one DPI on both axes.
        """
        return (self.dpi_y - self.dpi_x) / self.dpi_x

    @property
    def axes_differ(self) -> bool:
        return abs(self.anisotropy) > _ANISOTROPY_EPSILON

    def step_fraction(self, step: int = 50) -> tuple[float, float]:
        """What one DPI step is worth on each axis, as a fraction of gain.

        The number that decides whether DPI is even the right knob. Fifty counts
        is 6.25% at 800 DPI and 1.6% at 3200, so the same step is a nudge on one
        mouse and an overcorrection on another.
        """
        return (step / self.dpi_x, step / self.dpi_y)

    def describe(self) -> str:
        if not self.axes_differ:
            return f"{self.dpi_x} DPI"
        return (
            f"{self.dpi_x} x {self.dpi_y} DPI — the vertical axis is "
            f"{self.anisotropy:+.2%} relative to the horizontal, so pointer gain is not "
            "one number and the two axes are estimated separately"
        )


ASYMMETRIC_CUTOFF_PRESETS: dict[int, tuple[float, float]] = {
    1: (1.0, 2.0),
    2: (1.0, 3.0),
    3: (2.0, 3.0),
}
"""Razer Smart Tracking's asymmetric cut-off presets, as `(landing, lift-off)` mm.

Reported by a user from the Synapse interface rather than read from a
specification, and recorded here so a settings page can offer the presets
instead of asking for millimetres. Every one has landing below lift-off, which
is hysteresis: the sensor cuts out higher than it resumes, so it does not
flicker on and off while the mouse is being glided at the threshold.

Ranked by how much unintended motion they admit, preset 1 is the tightest and
preset 3 the loosest. With asymmetric cut-off switched off the choice is Low /
Medium / High instead, and those are not published in millimetres — so they are
recorded as a label with the distances left unknown rather than as invented
numbers.
"""

LIFT_OFF_OF_CONCERN_MM = 2.0
"""Above this, lift-off distance is worth mentioning next to an aim result.

A judgement rather than a measurement, and a contestable one — it is named so
that it can be argued with rather than buried in a comparison. Two millimetres
is roughly where community advice stops treating lift-off as irrelevant. Going
lower is not free either: a player who glides with slight lift gets tracking
dropouts instead.
"""


@dataclass(frozen=True, slots=True)
class Tracking:
    """When the sensor stops and starts tracking as the mouse leaves the pad.

    The reason this belongs in a profile at all: motion recorded while the mouse
    is in the air, or on its way back down, is cursor movement the hand never
    aimed. It does not spread evenly over a play — it concentrates on
    repositioning, which follows long jumps and edges of the playfield, which
    are exactly the cases a direction- and distance-conditioned aim analysis
    reports on.

    Declared like everything else here. No file on disk records it; it lives in
    vendor software.
    """

    landing_mm: float | None = None
    """Height at which the sensor resumes tracking on the way down."""

    lift_off_mm: float | None = None
    """Height at which the sensor stops tracking on the way up."""

    label: str = ""
    """Where the numbers came from, or the vendor setting when they are unknown
    — `"Low"`, `"Medium"`, `"High"` are labels with no published millimetres."""

    def __post_init__(self) -> None:
        for name, value in (("landing_mm", self.landing_mm), ("lift_off_mm", self.lift_off_mm)):
            if value is not None and not 0.0 < value <= 10.0:
                raise ProfileError(f"{name}={value} is not a plausible sensor height in mm")

    @classmethod
    def asymmetric(cls, preset: int) -> Tracking:
        """One of the vendor's asymmetric cut-off presets."""
        try:
            landing, lift_off = ASYMMETRIC_CUTOFF_PRESETS[preset]
        except KeyError:
            options = ", ".join(str(k) for k in sorted(ASYMMETRIC_CUTOFF_PRESETS))
            raise ProfileError(
                f"asymmetric cut-off preset {preset} is not one of {options}"
            ) from None
        return cls(
            landing_mm=landing,
            lift_off_mm=lift_off,
            label=f"asymmetric cut-off {preset}",
        )

    @property
    def known(self) -> bool:
        return self.lift_off_mm is not None

    @property
    def of_concern(self) -> bool:
        """Whether the lift-off distance is high enough to mention.

        False when unknown — an unknown is handled as its own case, because
        "not declared" and "declared and fine" call for different sentences.
        """
        return self.lift_off_mm is not None and self.lift_off_mm > LIFT_OFF_OF_CONCERN_MM

    def describe(self) -> str:
        if not self.known:
            return f"tracking cut-off {self.label or 'not declared'}, distances unknown"
        parts = [f"lift-off {self.lift_off_mm:g}mm"]
        if self.landing_mm is not None:
            parts.append(f"landing {self.landing_mm:g}mm")
        detail = ", ".join(parts)
        return f"{self.label} ({detail})" if self.label else detail


Pointer = Mouse
"""The pointing device. A union of one, until tablet support lands.

Written as an alias rather than left as a bare `Mouse` so that the second
variant is a change to this line and to whatever fails to type-check against it,
instead of a search for every place that assumed DPI existed.
"""


@dataclass(frozen=True, slots=True)
class Profile:
    """Everything the player has told us about their hardware."""

    pointer: Pointer | None = None
    tracking: Tracking | None = None

    @property
    def mouse(self) -> Mouse | None:
        """The pointer, if it is a mouse. `None` for absent or for anything
        else — so a caller that needs DPI gets nothing rather than something
        that happens to have the right shape."""
        return self.pointer if isinstance(self.pointer, Mouse) else None


def default_profile_path() -> Path:
    """`%LOCALAPPDATA%\\osu-forge\\profile.json`, or the environment override.

    Alongside the journal, and outside the osu! install. Nothing this tool
    writes belongs in a directory the game owns.
    """
    override = os.environ.get(PROFILE_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "osu-forge" / "profile.json"


def load(path: Path | None = None) -> Profile:
    """Read the profile. An absent file is an empty profile, not an error.

    Absent is the normal state on a first run, and everything downstream is
    expected to work without it — less precisely, and saying so.
    """
    target = path or default_profile_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Profile()
    except OSError as exc:
        raise ProfileError(f"{target}: {exc}") from exc

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{target}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"{target}: expected an object, found {type(data).__name__}")

    version = data.get("schema", _SCHEMA)
    if not isinstance(version, int) or version > _SCHEMA:
        raise ProfileError(
            f"{target}: schema {version} was written by a newer osu-forge; "
            "this one would read the fields it recognises and silently ignore the rest"
        )

    tracking_data = data.get("tracking")
    tracking: Tracking | None = None
    if tracking_data is not None:
        if not isinstance(tracking_data, dict):
            raise ProfileError(f"{target}: 'tracking' should be an object")
        try:
            landing = tracking_data.get("landing_mm")
            lift_off = tracking_data.get("lift_off_mm")
            tracking = Tracking(
                landing_mm=None if landing is None else float(landing),
                lift_off_mm=None if lift_off is None else float(lift_off),
                label=str(tracking_data.get("label", "")),
            )
        except (TypeError, ValueError) as exc:
            raise ProfileError(f"{target}: tracking distances must be numbers: {exc}") from exc

    pointer_data = data.get("pointer")
    if pointer_data is None:
        return Profile(tracking=tracking)
    if not isinstance(pointer_data, dict):
        raise ProfileError(f"{target}: 'pointer' should be an object")

    kind = pointer_data.get("kind")
    if kind != Mouse.kind:
        # Refused rather than skipped. A tablet profile read by a build that
        # predates tablet support must not come back as "no pointer declared"
        # and get analysed as if the question had never been asked.
        raise ProfileError(
            f"{target}: pointer kind {kind!r} is not supported by this build; "
            f"only {Mouse.kind!r} is"
        )

    try:
        dpi_x = int(pointer_data["dpi_x"])
        dpi_y = int(pointer_data["dpi_y"])
    except KeyError as exc:
        raise ProfileError(f"{target}: mouse is missing {exc.args[0]}") from exc
    except (TypeError, ValueError) as exc:
        raise ProfileError(f"{target}: mouse DPI must be a whole number: {exc}") from exc

    return Profile(pointer=Mouse(dpi_x=dpi_x, dpi_y=dpi_y), tracking=tracking)


def save(profile: Profile, path: Path | None = None) -> Path:
    """Write the profile, creating its directory. Returns where it went.

    Rewritten in place rather than appended to, unlike the journal: this is
    current configuration, and its history is not evidence for anything.
    """
    target = path or default_profile_path()
    data: dict[str, Any] = {"schema": _SCHEMA}
    if profile.mouse is not None:
        data["pointer"] = {
            "kind": Mouse.kind,
            "dpi_x": profile.mouse.dpi_x,
            "dpi_y": profile.mouse.dpi_y,
        }
    if profile.tracking is not None:
        data["tracking"] = {
            "landing_mm": profile.tracking.landing_mm,
            "lift_off_mm": profile.tracking.lift_off_mm,
            "label": profile.tracking.label,
        }

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"{target}: {exc}") from exc
    return target
