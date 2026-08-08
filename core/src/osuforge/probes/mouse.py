"""Windows pointer settings.

Note the name collision, which is the whole reason this lives in its own module
with its own field names: ``HKCU\\Control Panel\\Mouse\\MouseSpeed`` is the
**Enhance Pointer Precision** toggle. osu!'s ``MouseSpeed`` is a cursor
sensitivity multiplier. Unrelated quantities, identical name, and confusing them
would produce a confidently wrong finding — so this struct calls the registry
one ``enhance_pointer_precision`` and nothing here is reachable as a bare
``MouseSpeed``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from osuforge.probes.base import ProbeResult

__all__ = ["MOUSE_PROBE_ID", "SENSITIVITY_ONE_TO_ONE", "WindowsMouseSettings", "probe_mouse"]

MOUSE_PROBE_ID = "mouse.windows"
_KEY = r"HKCU\Control Panel\Mouse"
_SOURCE = _KEY

SENSITIVITY_ONE_TO_ONE = 10
"""The middle notch of the Windows pointer speed slider — the sixth of eleven
positions, hence "6/11". At this value one mouse count moves the cursor one
pixel; every other notch multiplies or divides, which throws away counts."""


@dataclass(frozen=True, slots=True)
class WindowsMouseSettings:
    sensitivity: int
    """1-20. 10 is the 1:1 notch."""

    enhance_pointer_precision: int
    """Registry ``MouseSpeed``: 0 off, 1 or 2 on. Not osu!'s MouseSpeed."""

    threshold1: int
    threshold2: int

    @property
    def acceleration_on(self) -> bool:
        return bool(self.enhance_pointer_precision) or bool(self.threshold1 or self.threshold2)

    @property
    def one_to_one(self) -> bool:
        return self.sensitivity == SENSITIVITY_ONE_TO_ONE


def _read_int(key: object, name: str, default: int) -> int:
    import winreg

    try:
        raw, _ = winreg.QueryValueEx(key, name)  # type: ignore[arg-type]
    except FileNotFoundError:
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def probe_mouse() -> ProbeResult[WindowsMouseSettings]:
    if sys.platform != "win32":
        return ProbeResult.unavailable(MOUSE_PROBE_ID, _SOURCE, "registry is Windows-only")

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse") as key:
            settings = WindowsMouseSettings(
                # Defaults are the Windows stock values, used when a value is
                # absent rather than treating absence as a problem.
                sensitivity=_read_int(key, "MouseSensitivity", SENSITIVITY_ONE_TO_ONE),
                enhance_pointer_precision=_read_int(key, "MouseSpeed", 0),
                threshold1=_read_int(key, "MouseThreshold1", 0),
                threshold2=_read_int(key, "MouseThreshold2", 0),
            )
    except OSError as exc:
        return ProbeResult.error(MOUSE_PROBE_ID, _SOURCE, f"{type(exc).__name__}: {exc}")

    return ProbeResult.of(MOUSE_PROBE_ID, settings, _SOURCE)
