"""Accessibility shortcut state.

The interesting bit is not whether StickyKeys is *on* — almost nobody has it on.
It is whether the **hotkey** is still armed, because that is the default and
because the trigger is five presses of Shift. A player whose smoke key is Shift
is one panicked stream away from a modal dialog mid-play.

Same story for FilterKeys (hold right Shift for eight seconds) and ToggleKeys
(hold Num Lock for five seconds).
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

from osuforge.probes.base import ProbeResult

__all__ = ["A11Y_PROBE_ID", "AccessibilityShortcuts", "probe_accessibility"]

A11Y_PROBE_ID = "system.a11y"
_SOURCE = "SystemParametersInfoW(SPI_GETSTICKYKEYS / SPI_GETFILTERKEYS / SPI_GETTOGGLEKEYS)"

_SPI_GETFILTERKEYS = 0x0032
_SPI_GETSTICKYKEYS = 0x003A
_SPI_GETTOGGLEKEYS = 0x0034

# Bit 0 is "the feature is on". Bit 2 is "the keyboard shortcut that turns it on
# is enabled" — that is the one that matters here.
_FLAG_ON = 0x00000001
_FLAG_HOTKEY_ACTIVE = 0x00000004


# `ctypes.wintypes` does not exist off Windows and these layouts need it while
# the class body runs, so leaving them at module scope makes the whole command
# line unimportable there. Nothing below is reached before `probe_accessibility`
# has already refused on a non-Windows platform.
if sys.platform == "win32":
    from ctypes import wintypes

    class _FLAGSTRUCT(ctypes.Structure):
        """STICKYKEYS and TOGGLEKEYS share this shape."""

        _fields_ = (("cbSize", wintypes.UINT), ("dwFlags", wintypes.DWORD))

    class _FILTERKEYS(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.UINT),
            ("dwFlags", wintypes.DWORD),
            ("iWaitMSec", wintypes.INT),
            ("iDelayMSec", wintypes.INT),
            ("iRepeatMSec", wintypes.INT),
            ("iBounceMSec", wintypes.INT),
        )


@dataclass(frozen=True, slots=True)
class Shortcut:
    name: str
    flags: int
    trigger: str

    @property
    def enabled(self) -> bool:
        return bool(self.flags & _FLAG_ON)

    @property
    def hotkey_armed(self) -> bool:
        return bool(self.flags & _FLAG_HOTKEY_ACTIVE)


@dataclass(frozen=True, slots=True)
class AccessibilityShortcuts:
    sticky_keys: Shortcut
    filter_keys: Shortcut
    toggle_keys: Shortcut

    @property
    def armed(self) -> tuple[Shortcut, ...]:
        return tuple(
            s for s in (self.sticky_keys, self.filter_keys, self.toggle_keys) if s.hotkey_armed
        )


def _query(action: int, struct: ctypes.Structure) -> int | None:
    struct.cbSize = ctypes.sizeof(struct)
    ok = ctypes.windll.user32.SystemParametersInfoW(
        action, ctypes.sizeof(struct), ctypes.byref(struct), 0
    )
    if not ok:
        return None
    return int(struct.dwFlags)


def probe_accessibility() -> ProbeResult[AccessibilityShortcuts]:
    if sys.platform != "win32":
        return ProbeResult.unavailable(
            A11Y_PROBE_ID, _SOURCE, "SystemParametersInfoW is Windows-only"
        )

    try:
        sticky = _query(_SPI_GETSTICKYKEYS, _FLAGSTRUCT())
        filt = _query(_SPI_GETFILTERKEYS, _FILTERKEYS())
        toggle = _query(_SPI_GETTOGGLEKEYS, _FLAGSTRUCT())
    except OSError as exc:
        return ProbeResult.error(A11Y_PROBE_ID, _SOURCE, f"{type(exc).__name__}: {exc}")

    if sticky is None or filt is None or toggle is None:
        return ProbeResult.unavailable(
            A11Y_PROBE_ID, _SOURCE, "SystemParametersInfoW reported failure"
        )

    return ProbeResult.of(
        A11Y_PROBE_ID,
        AccessibilityShortcuts(
            sticky_keys=Shortcut("Sticky Keys", sticky, "press Shift five times"),
            filter_keys=Shortcut("Filter Keys", filt, "hold right Shift for eight seconds"),
            toggle_keys=Shortcut("Toggle Keys", toggle, "hold Num Lock for five seconds"),
        ),
        _SOURCE,
    )
