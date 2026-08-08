"""Is osu! running?

Detection only. This module enumerates process names and nothing else — it does
not open a handle to osu!, does not read its memory, and could not: the only
API it touches is ``psutil.process_iter``. The repo-wide CI grep for
process-memory and input-synthesis APIs covers the rest.

(Those API names are deliberately not spelled out here. The CI check is a plain
grep with no way to tell a docstring from a call, and keeping it strict is worth
more than the convenience of naming them in prose — a filter permissive enough
to skip docstrings would also skip a `getattr(kernel32, "...")` lookup.)

The reason to care is narrow but real. osu! rewrites its config when it exits,
so a config read while the game is running may not be what the game will keep,
and any recommendation the user applies in-game will be overwritten if they
edit the file instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from osuforge.probes.base import ProbeResult

__all__ = ["OSU_PROCESS_PROBE_ID", "OsuProcess", "probe_osu_process"]

OSU_PROCESS_PROBE_ID = "process.osu"
_SOURCE = "psutil.process_iter(['name', 'exe'])"

_OSU_EXE_NAMES = frozenset({"osu!.exe", "osu!auth.exe", "osu!.Game.exe"})


@dataclass(frozen=True, slots=True)
class OsuProcess:
    running: bool
    pid: int | None = None
    exe: str | None = None


def probe_osu_process() -> ProbeResult[OsuProcess]:
    try:
        import psutil
    except ImportError:
        return ProbeResult.unavailable(OSU_PROCESS_PROBE_ID, _SOURCE, "psutil is not installed")

    try:
        for process in psutil.process_iter(["name", "exe"]):
            name = process.info.get("name") or ""
            if name in _OSU_EXE_NAMES:
                return ProbeResult.of(
                    OSU_PROCESS_PROBE_ID,
                    OsuProcess(running=True, pid=process.pid, exe=process.info.get("exe")),
                    _SOURCE,
                )
    except (psutil.Error, OSError) as exc:
        return ProbeResult.error(OSU_PROCESS_PROBE_ID, _SOURCE, f"{type(exc).__name__}: {exc}")

    return ProbeResult.of(OSU_PROCESS_PROBE_ID, OsuProcess(running=False), _SOURCE)
