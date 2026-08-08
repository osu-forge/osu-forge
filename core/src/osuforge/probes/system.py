"""Windows-level settings that affect input and frame timing.

All read-only, none requiring elevation. Where Windows exposes no readable API
at all — GPU low-latency mode is the notable one — there is no probe rather than
a guess, because a fabricated value in a report is worse than an absent one.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from osuforge.probes.base import ProbeResult

__all__ = [
    "FSO_DISABLED_FLAG",
    "FSO_PROBE_ID",
    "GAMEBAR_PROBE_ID",
    "POWER_PROBE_ID",
    "TIMER_PROBE_ID",
    "FullscreenOptimizations",
    "GameBarSettings",
    "PowerScheme",
    "TimerResolution",
    "probe_fullscreen_optimizations",
    "probe_game_bar",
    "probe_power_scheme",
    "probe_timer_resolution",
]

TIMER_PROBE_ID = "system.timer_resolution"
POWER_PROBE_ID = "system.power_scheme"
GAMEBAR_PROBE_ID = "system.game_bar"
FSO_PROBE_ID = "system.fullscreen_optimizations"


# ---------------------------------------------------------------- timer

_TIMER_SOURCE = "NtQueryTimerResolution (ntdll)"


@dataclass(frozen=True, slots=True)
class TimerResolution:
    """Values in milliseconds.

    The Win32 naming is backwards from intuition: the API's "minimum" is the
    coarsest resolution and its "maximum" is the finest, so they are renamed
    here to say what they mean.
    """

    coarsest_ms: float
    finest_ms: float
    current_ms: float

    @property
    def at_finest(self) -> bool:
        return abs(self.current_ms - self.finest_ms) < 1e-6


def probe_timer_resolution() -> ProbeResult[TimerResolution]:
    if sys.platform != "win32":
        return ProbeResult.unavailable(TIMER_PROBE_ID, _TIMER_SOURCE, "Windows-only")

    try:
        coarsest = wintypes.ULONG()
        finest = wintypes.ULONG()
        current = wintypes.ULONG()
        status = ctypes.windll.ntdll.NtQueryTimerResolution(
            ctypes.byref(coarsest), ctypes.byref(finest), ctypes.byref(current)
        )
    except OSError as exc:
        return ProbeResult.error(TIMER_PROBE_ID, _TIMER_SOURCE, f"{type(exc).__name__}: {exc}")

    if status != 0:
        return ProbeResult.unavailable(
            TIMER_PROBE_ID, _TIMER_SOURCE, f"NTSTATUS 0x{status & 0xFFFFFFFF:08X}"
        )

    # The API reports 100-nanosecond units.
    return ProbeResult.of(
        TIMER_PROBE_ID,
        TimerResolution(
            coarsest_ms=coarsest.value / 10_000,
            finest_ms=finest.value / 10_000,
            current_ms=current.value / 10_000,
        ),
        _TIMER_SOURCE,
    )


# ---------------------------------------------------------------- power

_POWER_SOURCE = "PowerGetActiveScheme + PowerReadFriendlyName (powrprof)"

_KNOWN_SCHEMES = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "Balanced",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "High performance",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "Power saver",
    "e9a42b02-d5df-448d-aa00-03f14749eb61": "Ultimate performance",
}

_THROTTLING_SCHEMES = frozenset(
    {"381b4222-f694-41f0-9685-ff5bb260df2e", "a1841308-3541-4fab-bc81-f71556f20b4a"}
)


class _GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    )

    def __str__(self) -> str:
        tail = "".join(f"{b:02x}" for b in self.Data4)
        return f"{self.Data1:08x}-{self.Data2:04x}-{self.Data3:04x}-{tail[:4]}-{tail[4:]}"


@dataclass(frozen=True, slots=True)
class PowerScheme:
    guid: str
    name: str

    @property
    def may_throttle(self) -> bool:
        """Whether this scheme is one that parks cores or scales clocks down."""
        return self.guid in _THROTTLING_SCHEMES


def probe_power_scheme() -> ProbeResult[PowerScheme]:
    if sys.platform != "win32":
        return ProbeResult.unavailable(POWER_PROBE_ID, _POWER_SOURCE, "Windows-only")

    try:
        powrprof = ctypes.windll.powrprof
        pointer = ctypes.POINTER(_GUID)()
        if powrprof.PowerGetActiveScheme(None, ctypes.byref(pointer)) != 0:
            return ProbeResult.unavailable(
                POWER_PROBE_ID, _POWER_SOURCE, "PowerGetActiveScheme failed"
            )
        guid = str(pointer.contents)

        name = _KNOWN_SCHEMES.get(guid, "")
        if not name:
            size = wintypes.DWORD(0)
            powrprof.PowerReadFriendlyName(None, pointer, None, None, None, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if (
                powrprof.PowerReadFriendlyName(
                    None, pointer, None, None, buffer, ctypes.byref(size)
                )
                == 0
            ):
                name = buffer.raw.decode("utf-16-le", errors="replace").rstrip("\x00")
        ctypes.windll.kernel32.LocalFree(pointer)
    except OSError as exc:
        return ProbeResult.error(POWER_PROBE_ID, _POWER_SOURCE, f"{type(exc).__name__}: {exc}")

    return ProbeResult.of(
        POWER_PROBE_ID, PowerScheme(guid=guid, name=name or "unknown"), _POWER_SOURCE
    )


# ---------------------------------------------------------------- game bar

_GAMEBAR_SOURCE = r"HKCU\Software\Microsoft\GameBar + HKCU\System\GameConfigStore"


@dataclass(frozen=True, slots=True)
class GameBarSettings:
    game_mode: bool | None
    game_dvr: bool | None


def _read_dword(root: int, subkey: str, name: str) -> int | None:
    import winreg

    try:
        with winreg.OpenKey(root, subkey) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def probe_game_bar() -> ProbeResult[GameBarSettings]:
    if sys.platform != "win32":
        return ProbeResult.unavailable(GAMEBAR_PROBE_ID, _GAMEBAR_SOURCE, "Windows-only")

    import winreg

    mode = _read_dword(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar", "AutoGameModeEnabled"
    )
    dvr = _read_dword(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_Enabled")

    return ProbeResult.of(
        GAMEBAR_PROBE_ID,
        GameBarSettings(
            game_mode=None if mode is None else bool(mode),
            game_dvr=None if dvr is None else bool(dvr),
        ),
        _GAMEBAR_SOURCE,
    )


# ------------------------------------------------- fullscreen optimizations

_LAYERS_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
_FSO_SOURCE = rf"HKCU\{_LAYERS_KEY}"
FSO_DISABLED_FLAG = "DISABLEDXMAXIMIZEDWINDOWEDMODE"
"""The compatibility layer that turns Fullscreen Optimizations off."""


@dataclass(frozen=True, slots=True)
class FullscreenOptimizations:
    exe_path: str
    raw_flags: str | None
    """The verbatim layer string, e.g. ``~ DISABLEDXMAXIMIZEDWINDOWEDMODE``.

    Reported as-is. osu! installs carry an ``IgnoreFreeLibrary<osu!au>`` shim
    whose purpose is not documented anywhere we could find; it is definitely not
    the FSO flag, and guessing at what it does would be worse than showing it.
    """

    @property
    def has_entry(self) -> bool:
        return self.raw_flags is not None

    @property
    def disabled(self) -> bool:
        return bool(self.raw_flags) and FSO_DISABLED_FLAG in (self.raw_flags or "")


def probe_fullscreen_optimizations(exe_path: Path) -> ProbeResult[FullscreenOptimizations]:
    if sys.platform != "win32":
        return ProbeResult.unavailable(FSO_PROBE_ID, _FSO_SOURCE, "Windows-only")

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _LAYERS_KEY) as key:
            try:
                raw, _ = winreg.QueryValueEx(key, str(exe_path))
                flags: str | None = str(raw)
            except FileNotFoundError:
                flags = None
    except FileNotFoundError:
        # No Layers key at all simply means no per-app compatibility overrides.
        flags = None
    except OSError as exc:
        return ProbeResult.error(FSO_PROBE_ID, _FSO_SOURCE, f"{type(exc).__name__}: {exc}")

    return ProbeResult.of(
        FSO_PROBE_ID,
        FullscreenOptimizations(exe_path=str(exe_path), raw_flags=flags),
        _FSO_SOURCE,
    )
