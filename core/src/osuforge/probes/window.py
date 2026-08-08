"""Which monitor osu! is actually on.

osu!'s ``Display`` config index uses its own window-toolkit ordering, which is
not guaranteed to match ``EnumDisplayDevices``. So the config cannot tell you
which screen the game opens on — but a live window can, exactly.

That distinction matters. A rule that guesses from the config will happily
report "you are on the 144 Hz monitor" to somebody who deliberately runs a
non-native mode on their 240 Hz one, and being confidently wrong about
something the user can see makes the rest of the report worthless to them.

When osu! is not running this probe reports unavailable, and the rules that
depend on it say what they cannot determine rather than picking a side.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

from osuforge.probes.base import ProbeResult
from osuforge.probes.display import Monitor

__all__ = ["OSU_WINDOW_PROBE_ID", "OsuWindowPlacement", "probe_osu_window_monitor"]

OSU_WINDOW_PROBE_ID = "display.osu_window"
_SOURCE = "EnumWindows + GetWindowThreadProcessId + MonitorFromWindow + GetMonitorInfoW"

_MONITOR_DEFAULTTONEAREST = 0x00000002

_ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _RECT(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


class _MONITORINFO(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    )


@dataclass(frozen=True, slots=True)
class OsuWindowPlacement:
    """The monitor osu!'s window sits on, matched by position."""

    monitor: Monitor
    window_width: int
    window_height: int

    @property
    def native(self) -> bool:
        return self.window_width == self.monitor.width and self.window_height == self.monitor.height


def _find_window_for_pid(pid: int) -> int | None:
    user32 = ctypes.windll.user32
    found: list[int] = []

    # ctypes function types carry no annotations, so mypy sees the decorated
    # function as untyped. The body is annotated; only the wrapper is opaque.
    @_ENUM_WINDOWS_PROC  # type: ignore[untyped-decorator]
    def callback(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        # osu! creates helper windows; the game window is the one with a size.
        rect = _RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        if rect.right - rect.left < 320 or rect.bottom - rect.top < 240:
            return True
        found.append(hwnd)
        return False

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def probe_osu_window_monitor(
    pid: int | None, monitors: list[Monitor] | None
) -> ProbeResult[OsuWindowPlacement]:
    if sys.platform != "win32":
        return ProbeResult.unavailable(OSU_WINDOW_PROBE_ID, _SOURCE, "Windows-only")
    if pid is None:
        return ProbeResult.unavailable(
            OSU_WINDOW_PROBE_ID,
            _SOURCE,
            "osu! is not running, so its window cannot be located",
        )
    if not monitors:
        return ProbeResult.unavailable(
            OSU_WINDOW_PROBE_ID, _SOURCE, "monitor enumeration unavailable"
        )

    try:
        user32 = ctypes.windll.user32
        hwnd = _find_window_for_pid(pid)
        if hwnd is None:
            return ProbeResult.unavailable(
                OSU_WINDOW_PROBE_ID, _SOURCE, "no visible osu! window found"
            )

        rect = _RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

        handle = user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return ProbeResult.unavailable(OSU_WINDOW_PROBE_ID, _SOURCE, "GetMonitorInfoW failed")
    except OSError as exc:
        return ProbeResult.error(OSU_WINDOW_PROBE_ID, _SOURCE, f"{type(exc).__name__}: {exc}")

    # Match by top-left corner: the same coordinate space EnumDisplaySettingsExW
    # reports, so this needs no assumption about ordering.
    monitor = next(
        (m for m in monitors if m.x == info.rcMonitor.left and m.y == info.rcMonitor.top),
        None,
    )
    if monitor is None:
        return ProbeResult.unavailable(
            OSU_WINDOW_PROBE_ID,
            _SOURCE,
            f"window is on a monitor at ({info.rcMonitor.left}, {info.rcMonitor.top}) "
            "that did not appear in the enumeration",
        )

    return ProbeResult.of(
        OSU_WINDOW_PROBE_ID,
        OsuWindowPlacement(
            monitor=monitor,
            window_width=rect.right - rect.left,
            window_height=rect.bottom - rect.top,
        ),
        _SOURCE,
    )
