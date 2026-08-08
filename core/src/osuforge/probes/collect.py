"""Run every probe once, into the map the rule engine reads.

Collecting centrally is what stops two rules independently querying the same
thing and disagreeing about the answer. It also means a probe that fails does so
once, with one recorded reason, instead of failing differently for each caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from osuforge.probes.a11y import A11Y_PROBE_ID, probe_accessibility
from osuforge.probes.base import ProbeResult
from osuforge.probes.display import DISPLAYS_PROBE_ID, probe_displays
from osuforge.probes.mouse import MOUSE_PROBE_ID, probe_mouse
from osuforge.probes.process import OSU_PROCESS_PROBE_ID, probe_osu_process
from osuforge.probes.system import (
    FSO_PROBE_ID,
    GAMEBAR_PROBE_ID,
    POWER_PROBE_ID,
    TIMER_PROBE_ID,
    probe_fullscreen_optimizations,
    probe_game_bar,
    probe_power_scheme,
    probe_timer_resolution,
)
from osuforge.probes.window import OSU_WINDOW_PROBE_ID, probe_osu_window_monitor

__all__ = ["collect_probes"]


def collect_probes(*, osu_exe: Path | None = None) -> Mapping[str, ProbeResult[object]]:
    """Every probe, keyed by id.

    ``osu_exe`` is needed only for the fullscreen-optimizations lookup, whose
    registry value name is the full executable path. Without it that one probe
    reports unavailable rather than guessing at a path.
    """
    displays = probe_displays()
    osu_process = probe_osu_process()

    probes: dict[str, ProbeResult[object]] = {
        DISPLAYS_PROBE_ID: displays,  # type: ignore[dict-item]
        MOUSE_PROBE_ID: probe_mouse(),  # type: ignore[dict-item]
        A11Y_PROBE_ID: probe_accessibility(),  # type: ignore[dict-item]
        TIMER_PROBE_ID: probe_timer_resolution(),  # type: ignore[dict-item]
        POWER_PROBE_ID: probe_power_scheme(),  # type: ignore[dict-item]
        GAMEBAR_PROBE_ID: probe_game_bar(),  # type: ignore[dict-item]
        OSU_PROCESS_PROBE_ID: osu_process,  # type: ignore[dict-item]
    }

    # Only determinable while osu! is running, and the only way to know which
    # monitor it uses — its config Display index does not reliably map to the
    # Windows one.
    probes[OSU_WINDOW_PROBE_ID] = probe_osu_window_monitor(  # type: ignore[assignment]
        osu_process.value.pid if osu_process.ok and osu_process.value else None,
        displays.value if displays.ok else None,
    )

    if osu_exe is not None:
        probes[FSO_PROBE_ID] = probe_fullscreen_optimizations(osu_exe)  # type: ignore[assignment]
    else:
        probes[FSO_PROBE_ID] = ProbeResult.unavailable(
            FSO_PROBE_ID,
            r"HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers",
            "osu! install directory not known, so the executable path is unknown",
        )

    return probes
