"""Probe contract tests.

Deliberately built on synthetic values. A test that asserts "refresh rate is
240" passes on one machine and fails on the next, and a suite that only works on
the author's hardware is not a suite. The handful of checks that do touch real
hardware are marked ``integration`` and deselected by default.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from osuforge.probes import ProbeResult, ProbeStatus
from osuforge.probes.a11y import probe_accessibility
from osuforge.probes.collect import collect_probes
from osuforge.probes.display import DISPLAYS_PROBE_ID, Monitor, primary, probe_displays
from osuforge.probes.mouse import SENSITIVITY_ONE_TO_ONE, WindowsMouseSettings, probe_mouse
from osuforge.probes.process import probe_osu_process
from osuforge.probes.system import (
    FSO_DISABLED_FLAG,
    FullscreenOptimizations,
    PowerScheme,
    TimerResolution,
    probe_game_bar,
    probe_power_scheme,
    probe_timer_resolution,
)


class TestProbeResult:
    def test_ok_carries_the_value(self) -> None:
        result = ProbeResult.of("x", 42, "SomeApi()")
        assert result.ok
        assert result.require() == 42

    def test_unavailable_is_a_value_not_an_exception(self) -> None:
        # The whole contract: a probe that cannot run returns, it does not raise.
        # A partial report beats a stack trace.
        result: ProbeResult[int] = ProbeResult.unavailable("x", "SomeApi()", "no such device")
        assert not result.ok
        assert result.status is ProbeStatus.UNAVAILABLE
        assert result.reason == "no such device"

    def test_require_on_a_failed_probe_raises_with_the_reason(self) -> None:
        result: ProbeResult[int] = ProbeResult.error("x", "SomeApi()", "boom")
        with pytest.raises(ValueError, match="boom"):
            result.require()

    def test_elevation_requirement_is_recorded(self) -> None:
        result: ProbeResult[int] = ProbeResult.unavailable(
            "hid", "IOCTL", "access denied", requires_elevation=True
        )
        assert result.requires_elevation

    def test_source_is_always_present(self) -> None:
        # Every number in a report has to be traceable to where it came from.
        for result in (
            ProbeResult.of("a", 1, "ApiOne()"),
            ProbeResult.unavailable("b", "ApiTwo()", "nope"),
            ProbeResult.error("c", "ApiThree()", "boom"),
        ):
            assert result.source


class TestMonitor:
    def _monitor(self, **kwargs: object) -> Monitor:
        defaults: dict[str, object] = {
            "device_name": r"\\.\DISPLAY1",
            "description": "GPU",
            "width": 1920,
            "height": 1080,
            "refresh_hz": 144,
            "x": 0,
            "y": 0,
            "primary": True,
        }
        defaults.update(kwargs)
        return Monitor(**defaults)  # type: ignore[arg-type]

    def test_str_names_the_mode(self) -> None:
        assert str(self._monitor()) == "1920x1080@144Hz (primary)"
        assert str(self._monitor(primary=False)) == "1920x1080@144Hz"

    def test_contains_uses_virtual_desktop_coordinates(self) -> None:
        # Secondary monitors sit at non-zero, sometimes negative, offsets.
        secondary = self._monitor(x=2560, y=-6, primary=False)
        assert secondary.contains(2560, -6)
        assert secondary.contains(4479, 1073)
        assert not secondary.contains(0, 0)

    def test_primary_falls_back_to_the_first_monitor(self) -> None:
        none_primary = [self._monitor(primary=False), self._monitor(primary=False)]
        assert primary(none_primary) is none_primary[0]

    def test_primary_is_preferred_when_present(self) -> None:
        monitors = [self._monitor(primary=False), self._monitor(device_name=r"\\.\DISPLAY2")]
        assert primary(monitors).device_name == r"\\.\DISPLAY2"


class TestDerivedProperties:
    @pytest.mark.parametrize(
        ("epp", "t1", "t2", "expected"),
        [(0, 0, 0, False), (1, 6, 10, True), (0, 6, 0, True), (2, 0, 0, True)],
    )
    def test_acceleration_detection(self, epp: int, t1: int, t2: int, expected: bool) -> None:
        settings = WindowsMouseSettings(
            sensitivity=SENSITIVITY_ONE_TO_ONE,
            enhance_pointer_precision=epp,
            threshold1=t1,
            threshold2=t2,
        )
        assert settings.acceleration_on is expected

    def test_one_to_one_is_the_sixth_of_eleven_notches(self) -> None:
        assert SENSITIVITY_ONE_TO_ONE == 10
        assert WindowsMouseSettings(10, 0, 0, 0).one_to_one
        assert not WindowsMouseSettings(11, 0, 0, 0).one_to_one

    @pytest.mark.parametrize(
        ("guid", "throttles"),
        [
            ("381b4222-f694-41f0-9685-ff5bb260df2e", True),  # Balanced
            ("a1841308-3541-4fab-bc81-f71556f20b4a", True),  # Power saver
            ("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", False),  # High performance
            ("e9a42b02-d5df-448d-aa00-03f14749eb61", False),  # Ultimate
            ("00000000-0000-0000-0000-000000000000", False),  # custom, assume not
        ],
    )
    def test_power_scheme_throttling(self, guid: str, throttles: bool) -> None:
        assert PowerScheme(guid=guid, name="x").may_throttle is throttles

    def test_timer_at_finest(self) -> None:
        assert TimerResolution(coarsest_ms=15.625, finest_ms=0.5, current_ms=0.5).at_finest
        assert not TimerResolution(coarsest_ms=15.625, finest_ms=0.5, current_ms=1.0).at_finest

    def test_fso_flag_detection(self) -> None:
        assert not FullscreenOptimizations("osu!.exe", None).has_entry
        assert not FullscreenOptimizations("osu!.exe", "$ IgnoreFreeLibrary<osu!au>").disabled
        assert FullscreenOptimizations("osu!.exe", f"~ {FSO_DISABLED_FLAG}").disabled
        # An existing shim plus the flag must still read as disabled.
        assert FullscreenOptimizations(
            "osu!.exe", f"$ IgnoreFreeLibrary<osu!au> {FSO_DISABLED_FLAG}"
        ).disabled


class TestCollect:
    def test_every_probe_id_is_present_even_when_unavailable(self) -> None:
        # A missing key and a failed probe are different things: the rule engine
        # reports the second and would silently skip the first.
        probes = collect_probes()
        for probe_id in (
            "display.monitors",
            "mouse.windows",
            "system.a11y",
            "system.timer_resolution",
            "system.power_scheme",
            "system.game_bar",
            "process.osu",
            "system.fullscreen_optimizations",
        ):
            assert probe_id in probes

    def test_fso_is_unavailable_with_a_reason_when_the_exe_is_unknown(self) -> None:
        # Guessing a path would be worse than reporting that we do not have one.
        result = collect_probes()["system.fullscreen_optimizations"]
        assert not result.ok
        assert "install directory" in (result.reason or "")

    def test_no_probe_raises(self) -> None:
        for probe_id, result in collect_probes(osu_exe=Path("C:/nonexistent/osu!.exe")).items():
            assert result.status in tuple(ProbeStatus), probe_id
            assert result.source, probe_id


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only probes")
class TestAgainstRealHardware:
    """Plausibility only. These assert ranges, never specific values."""

    def test_displays_report_a_sane_mode(self) -> None:
        result = probe_displays()
        assert result.ok
        for monitor in result.require():
            assert monitor.width >= 640
            assert monitor.height >= 480
            # Deliberately loose: this must hold on a 60 Hz laptop and a 540 Hz
            # panel alike. Pinning it to the author's 240 Hz monitor would be
            # exactly the mistake this class exists to avoid.
            assert 24 <= monitor.refresh_hz <= 1000

    def test_mouse_settings_are_in_range(self) -> None:
        result = probe_mouse()
        assert result.ok
        settings = result.require()
        assert 1 <= settings.sensitivity <= 20
        assert settings.enhance_pointer_precision in (0, 1, 2)

    def test_accessibility_flags_are_readable(self) -> None:
        result = probe_accessibility()
        assert result.ok
        assert isinstance(result.require().sticky_keys.hotkey_armed, bool)

    def test_timer_resolution_is_ordered(self) -> None:
        result = probe_timer_resolution()
        assert result.ok
        timer = result.require()
        assert timer.finest_ms <= timer.current_ms <= timer.coarsest_ms

    def test_power_scheme_has_a_guid(self) -> None:
        result = probe_power_scheme()
        assert result.ok
        assert len(result.require().guid) == 36

    def test_game_bar_is_readable(self) -> None:
        assert probe_game_bar().ok

    def test_osu_process_detection_does_not_error(self) -> None:
        # Whether osu! happens to be running is not the point; not erroring is.
        assert probe_osu_process().status is not ProbeStatus.ERROR


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows path")
def test_windows_only_probes_degrade_rather_than_crash() -> None:
    for result in (probe_displays(), probe_mouse(), probe_accessibility()):
        assert result.status is ProbeStatus.UNAVAILABLE
        assert "Windows" in (result.reason or "")


def test_display_probe_id_is_stable() -> None:
    # Rules reference probes by id string; renaming one silently detaches them.
    assert DISPLAYS_PROBE_ID == "display.monitors"
