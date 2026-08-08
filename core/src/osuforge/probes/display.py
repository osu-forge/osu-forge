"""Monitor enumeration: resolution, refresh rate, position.

Uses ``EnumDisplayDevicesW`` + ``EnumDisplaySettingsExW``. **Not WMI.**
``Win32_VideoController.CurrentRefreshRate`` reports ``143`` for a 144 Hz panel
and only describes one adapter mode, so a rule comparing a config value against
it would report a mismatch that does not exist.

Refresh rate is the number several rules hang off, so getting it from the API
that actually knows is not a detail.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

from osuforge.probes.base import ProbeResult

__all__ = ["DISPLAYS_PROBE_ID", "Monitor", "probe_displays"]

DISPLAYS_PROBE_ID = "display.monitors"
_SOURCE = "EnumDisplayDevicesW + EnumDisplaySettingsExW(ENUM_CURRENT_SETTINGS)"

_ENUM_CURRENT_SETTINGS = -1
_DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
_DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004


# Struct names mirror the Win32 ones exactly so each can be checked against the
# MSDN page field by field. A CapWords rename would make that harder, and these
# layouts are the one place a silent mistake produces plausible wrong numbers.
class _DISPLAY_DEVICEW(ctypes.Structure):  # noqa: N801
    _fields_ = (
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    )


class _DEVMODEW(ctypes.Structure):
    """Field order matters and is not obvious.

    The position/orientation block is a union with the printer-oriented fields;
    laying it out as anything other than this exact sequence shifts
    ``dmDisplayFrequency`` and yields a plausible-looking wrong refresh rate,
    which is precisely the failure mode the probes are built to avoid.
    """

    _fields_ = (
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    )


@dataclass(frozen=True, slots=True)
class Monitor:
    device_name: str
    """``\\\\.\\DISPLAY1`` and friends."""

    description: str
    width: int
    height: int
    refresh_hz: int
    x: int
    y: int
    primary: bool

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    def __str__(self) -> str:
        tag = " (primary)" if self.primary else ""
        return f"{self.resolution}@{self.refresh_hz}Hz{tag}"

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


def _enumerate() -> list[Monitor]:
    user32 = ctypes.windll.user32
    monitors: list[Monitor] = []

    index = 0
    while True:
        device = _DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            break
        index += 1

        # Detached adapters report settings that describe nothing on screen.
        if not device.StateFlags & _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            continue

        mode = _DEVMODEW()
        mode.dmSize = ctypes.sizeof(_DEVMODEW)
        if not user32.EnumDisplaySettingsExW(
            device.DeviceName, _ENUM_CURRENT_SETTINGS, ctypes.byref(mode), 0
        ):
            continue

        monitors.append(
            Monitor(
                device_name=device.DeviceName,
                description=device.DeviceString,
                width=int(mode.dmPelsWidth),
                height=int(mode.dmPelsHeight),
                refresh_hz=int(mode.dmDisplayFrequency),
                x=int(mode.dmPositionX),
                y=int(mode.dmPositionY),
                primary=bool(device.StateFlags & _DISPLAY_DEVICE_PRIMARY_DEVICE),
            )
        )

    return monitors


def probe_displays() -> ProbeResult[list[Monitor]]:
    if sys.platform != "win32":
        return ProbeResult.unavailable(
            DISPLAYS_PROBE_ID, _SOURCE, "display enumeration is Windows-only"
        )

    try:
        monitors = _enumerate()
    except OSError as exc:
        return ProbeResult.error(DISPLAYS_PROBE_ID, _SOURCE, f"{type(exc).__name__}: {exc}")

    if not monitors:
        return ProbeResult.unavailable(
            DISPLAYS_PROBE_ID, _SOURCE, "no monitors attached to the desktop"
        )

    return ProbeResult.of(DISPLAYS_PROBE_ID, monitors, _SOURCE)


def primary(monitors: list[Monitor]) -> Monitor:
    """The primary monitor, falling back to the first attached one."""
    return next((m for m in monitors if m.primary), monitors[0])
