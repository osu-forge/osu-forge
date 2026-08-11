"""System rules, driven by synthetic probe values.

Every probe value here is constructed, never measured, so these assert what the
rules *decide* rather than what this machine happens to be configured as.
"""

from __future__ import annotations

import pytest

from osuforge.config import OsuConfig
from osuforge.models import Basis, Severity
from osuforge.probes.a11y import A11Y_PROBE_ID, AccessibilityShortcuts, Shortcut
from osuforge.probes.audio import (
    AUDIO_PROBE_ID,
    AudioEndpoint,
    SharedModeEnginePeriod,
)
from osuforge.probes.base import ProbeResult
from osuforge.probes.display import DISPLAYS_PROBE_ID, Monitor
from osuforge.probes.mouse import MOUSE_PROBE_ID, WindowsMouseSettings
from osuforge.probes.process import OSU_PROCESS_PROBE_ID, OsuProcess
from osuforge.probes.system import (
    FSO_DISABLED_FLAG,
    FSO_PROBE_ID,
    GAMEBAR_PROBE_ID,
    POWER_PROBE_ID,
    TIMER_PROBE_ID,
    FullscreenOptimizations,
    GameBarSettings,
    PowerScheme,
    TimerResolution,
)
from osuforge.probes.window import OSU_WINDOW_PROBE_ID, OsuWindowPlacement
from osuforge.rules import ALL_RULES, RuleContext, run_rules

from .fixtures import build_cfg

_HOTKEY_ARMED = 0x004
_FEATURE_ON = 0x001


def _monitor(width: int, height: int, hz: int, *, name: str, primary: bool = False) -> Monitor:
    return Monitor(
        device_name=name,
        description="GPU",
        width=width,
        height=height,
        refresh_hz=hz,
        x=0,
        y=0,
        primary=primary,
    )


def _shortcuts(sticky: int = 0, filt: int = 0, toggle: int = 0) -> AccessibilityShortcuts:
    return AccessibilityShortcuts(
        sticky_keys=Shortcut("Sticky Keys", sticky, "press Shift five times"),
        filter_keys=Shortcut("Filter Keys", filt, "hold right Shift for eight seconds"),
        toggle_keys=Shortcut("Toggle Keys", toggle, "hold Num Lock for five seconds"),
    )


def _endpoint(
    name: str = "Speakers (Test Audio)",
    *,
    default_frames: int = 480,
    minimum_frames: int = 480,
    engine: bool = True,
) -> AudioEndpoint:
    period = (
        SharedModeEnginePeriod(
            default_frames=default_frames,
            fundamental_frames=default_frames,
            minimum_frames=minimum_frames,
            maximum_frames=default_frames,
            current_frames=default_frames,
        )
        if engine
        else None
    )
    return AudioEndpoint(
        device_id="{0.0.0.00000000}.{11111111-2222-3333-4444-555555555555}",
        friendly_name=name,
        transport="USB",
        form_factor=1,
        state=1,
        default_period_ms=10.0,
        minimum_period_ms=3.0,
        mix_sample_rate_hz=48000,
        mix_channels=2,
        mix_bits_per_sample=32,
        mix_format_tag=65534,
        console_is_multimedia=True,
        engine_period=period,
    )


def _audio(endpoint: AudioEndpoint) -> dict[str, object]:
    return {AUDIO_PROBE_ID: ProbeResult.of(AUDIO_PROBE_ID, endpoint, "IAudioClient")}


def _run(entries: dict[str, str], probes: dict[str, object], rule_id: str) -> list[object]:
    context = RuleContext(
        config=OsuConfig.parse(build_cfg(entries, header=False)),
        probes=dict(probes),  # type: ignore[arg-type]
    )
    return [f for f in run_rules(ALL_RULES, context) if f.id == rule_id]


class TestRefreshRate:
    def test_matching_refresh_rate_is_silent(self) -> None:
        probes = {
            DISPLAYS_PROBE_ID: ProbeResult.of(
                DISPLAYS_PROBE_ID, [_monitor(2560, 1440, 240, name="D1", primary=True)], "src"
            )
        }
        assert not _run({"RefreshRate": "240"}, probes, "sys.display.refreshrate_stale")

    def test_rounding_within_two_hz_is_tolerated(self) -> None:
        # Reported rates are rounded; a 143.98 Hz panel reads as 144 here and 143
        # through WMI, which is why WMI is not used. Two Hz of slack keeps that
        # class of difference from becoming a finding.
        probes = {
            DISPLAYS_PROBE_ID: ProbeResult.of(
                DISPLAYS_PROBE_ID, [_monitor(1920, 1080, 144, name="D1")], "src"
            )
        }
        assert not _run({"RefreshRate": "143"}, probes, "sys.display.refreshrate_stale")

    def test_stale_value_is_informational_when_the_override_is_off(self) -> None:
        # The value is not used at all in this state, so flagging it as a
        # problem to fix would be wrong — it is a symptom, not a cause.
        probes = {
            DISPLAYS_PROBE_ID: ProbeResult.of(
                DISPLAYS_PROBE_ID, [_monitor(1920, 1080, 144, name="D1")], "src"
            )
        }
        findings = _run(
            {"RefreshRate": "60", "OverrideRefreshRate": "0"},
            probes,
            "sys.display.refreshrate_stale",
        )
        assert len(findings) == 1
        assert findings[0].severity is Severity.INFO  # type: ignore[attr-defined]
        assert findings[0].recommended_value is None  # type: ignore[attr-defined]

    def test_wrong_value_is_a_warning_when_the_override_is_on(self) -> None:
        probes = {
            DISPLAYS_PROBE_ID: ProbeResult.of(
                DISPLAYS_PROBE_ID,
                [_monitor(1920, 1080, 144, name="D1"), _monitor(2560, 1440, 240, name="D2")],
                "src",
            )
        }
        findings = _run(
            {"RefreshRate": "60", "OverrideRefreshRate": "1"},
            probes,
            "sys.display.refreshrate_override_wrong",
        )
        assert len(findings) == 1
        assert findings[0].severity is Severity.WARN  # type: ignore[attr-defined]
        assert findings[0].recommended_value == "240"  # type: ignore[attr-defined]


class TestFullscreenResolution:
    def _probes(self, *monitors: Monitor) -> dict[str, object]:
        return {DISPLAYS_PROBE_ID: ProbeResult.of(DISPLAYS_PROBE_ID, list(monitors), "src")}

    def test_windowed_mode_is_not_checked(self) -> None:
        probes = self._probes(_monitor(2560, 1440, 240, name="D1"))
        assert not _run(
            {"Fullscreen": "0", "WidthFullscreen": "800", "HeightFullscreen": "600"},
            probes,
            "sys.display.fullscreen_not_native",
        )

    def test_native_resolution_is_silent(self) -> None:
        probes = self._probes(_monitor(2560, 1440, 240, name="D1", primary=True))
        entries = {"Fullscreen": "1", "WidthFullscreen": "2560", "HeightFullscreen": "1440"}
        assert not _run(entries, probes, "sys.display.fullscreen_not_native")
        assert not _run(entries, probes, "sys.display.fullscreen_monitor_ambiguous")

    def test_non_native_resolution_is_informational_not_a_fault(self) -> None:
        # Running below native is a normal deliberate trade for frame-rate
        # headroom, so it is reported without being called a problem and without
        # a recommendation to undo it.
        probes = self._probes(_monitor(2560, 1440, 240, name="D1"))
        findings = _run(
            {"Fullscreen": "1", "WidthFullscreen": "1280", "HeightFullscreen": "720"},
            probes,
            "sys.display.fullscreen_not_native",
        )
        assert len(findings) == 1
        assert findings[0].severity is Severity.INFO  # type: ignore[attr-defined]
        assert findings[0].recommended_value is None  # type: ignore[attr-defined]

    def test_a_known_window_monitor_settles_the_question(self) -> None:
        # The case that motivated this rework: 1080p is native to the 144 Hz
        # panel, but osu! is actually on the 1440p 240 Hz one. Guessing from the
        # config would tell the user they are on the wrong monitor.
        fast = _monitor(2560, 1440, 240, name="D2", primary=True)
        probes = {
            DISPLAYS_PROBE_ID: ProbeResult.of(
                DISPLAYS_PROBE_ID, [_monitor(1920, 1080, 144, name="D1"), fast], "src"
            ),
            OSU_WINDOW_PROBE_ID: ProbeResult.of(
                OSU_WINDOW_PROBE_ID,
                OsuWindowPlacement(monitor=fast, window_width=1920, window_height=1080),
                "MonitorFromWindow",
            ),
        }
        entries = {"Fullscreen": "1", "WidthFullscreen": "1920", "HeightFullscreen": "1080"}

        # No ambiguity finding, because there is no ambiguity any more.
        assert not _run(entries, probes, "sys.display.fullscreen_monitor_ambiguous")

        findings = _run(entries, probes, "sys.display.fullscreen_not_native")
        assert len(findings) == 1
        assert "D2" in findings[0].summary  # type: ignore[attr-defined]
        assert findings[0].severity is Severity.INFO  # type: ignore[attr-defined]
        assert findings[0].recommended_value is None  # type: ignore[attr-defined]

    def test_a_known_window_monitor_at_native_is_silent(self) -> None:
        fast = _monitor(2560, 1440, 240, name="D2", primary=True)
        probes = {
            DISPLAYS_PROBE_ID: ProbeResult.of(
                DISPLAYS_PROBE_ID, [_monitor(1920, 1080, 144, name="D1"), fast], "src"
            ),
            OSU_WINDOW_PROBE_ID: ProbeResult.of(
                OSU_WINDOW_PROBE_ID,
                OsuWindowPlacement(monitor=fast, window_width=2560, window_height=1440),
                "MonitorFromWindow",
            ),
        }
        entries = {"Fullscreen": "1", "WidthFullscreen": "2560", "HeightFullscreen": "1440"}
        assert not _run(entries, probes, "sys.display.fullscreen_not_native")
        assert not _run(entries, probes, "sys.display.fullscreen_monitor_ambiguous")

    def test_ambiguous_monitor_states_both_readings(self) -> None:
        # A real dual-monitor case: 1080p is native to the 144 Hz panel while a
        # 240 Hz 1440p panel is also attached. The user may be on either — running
        # 1080p on the 240 Hz panel for headroom is just as normal — so the finding
        # must lay out both rather than assert one.
        probes = self._probes(
            _monitor(1920, 1080, 144, name="D1"),
            _monitor(2560, 1440, 240, name="D2", primary=True),
        )
        findings = _run(
            {"Fullscreen": "1", "WidthFullscreen": "1920", "HeightFullscreen": "1080"},
            probes,
            "sys.display.fullscreen_monitor_ambiguous",
        )
        assert len(findings) == 1
        assert findings[0].severity is Severity.INFO  # type: ignore[attr-defined]
        # No recommendation: it would have to assume an answer we do not have.
        assert findings[0].recommended_value is None  # type: ignore[attr-defined]
        rationale = findings[0].rationale  # type: ignore[attr-defined]
        assert "D1" in rationale and "D2" in rationale, "both readings must be stated"

    def test_a_single_monitor_is_never_ambiguous(self) -> None:
        probes = self._probes(_monitor(1920, 1080, 60, name="D1", primary=True))
        assert not _run(
            {"Fullscreen": "1", "WidthFullscreen": "1920", "HeightFullscreen": "1080"},
            probes,
            "sys.display.fullscreen_monitor_ambiguous",
        )


class TestMouseRules:
    def _probes(self, settings: WindowsMouseSettings) -> dict[str, object]:
        return {MOUSE_PROBE_ID: ProbeResult.of(MOUSE_PROBE_ID, settings, "HKCU\\...\\Mouse")}

    def test_clean_settings_are_silent(self) -> None:
        probes = self._probes(WindowsMouseSettings(10, 0, 0, 0))
        assert not _run({}, probes, "sys.mouse.enhance_pointer_precision")
        assert not _run({}, probes, "sys.mouse.sensitivity_not_one_to_one")

    def test_acceleration_is_critical(self) -> None:
        findings = _run(
            {},
            self._probes(WindowsMouseSettings(10, 1, 6, 10)),
            "sys.mouse.enhance_pointer_precision",
        )
        assert findings[0].severity is Severity.CRITICAL  # type: ignore[attr-defined]

    def test_the_finding_says_which_mousespeed_it_means(self) -> None:
        # Registry MouseSpeed is the acceleration toggle; osu!'s is a
        # sensitivity multiplier. Same name, unrelated setting.
        findings = _run(
            {},
            self._probes(WindowsMouseSettings(10, 1, 0, 0)),
            "sys.mouse.enhance_pointer_precision",
        )
        assert any("unrelated" in c for c in findings[0].caveats)  # type: ignore[attr-defined]

    @pytest.mark.parametrize("sensitivity", [1, 6, 11, 20])
    def test_off_notch_sensitivity_is_reported(self, sensitivity: int) -> None:
        findings = _run(
            {},
            self._probes(WindowsMouseSettings(sensitivity, 0, 0, 0)),
            "sys.mouse.sensitivity_not_one_to_one",
        )
        assert findings[0].recommended_value == "10"  # type: ignore[attr-defined]


class TestAccessibilityRules:
    def test_shift_binding_with_the_shortcut_armed_is_a_warning(self) -> None:
        findings = _run(
            {"keyOsuSmoke": "LeftShift"},
            {A11Y_PROBE_ID: ProbeResult.of(A11Y_PROBE_ID, _shortcuts(sticky=_HOTKEY_ARMED), "spi")},
            "sys.a11y.shift_binding_triggers_sticky_keys",
        )
        assert len(findings) == 1
        assert findings[0].severity is Severity.WARN  # type: ignore[attr-defined]

    def test_no_shift_binding_means_no_finding(self) -> None:
        assert not _run(
            {"keyOsuSmoke": "V"},
            {A11Y_PROBE_ID: ProbeResult.of(A11Y_PROBE_ID, _shortcuts(sticky=_HOTKEY_ARMED), "spi")},
            "sys.a11y.shift_binding_triggers_sticky_keys",
        )

    def test_disarmed_shortcut_means_no_finding(self) -> None:
        # Sticky Keys being *on* is not the trigger; the shortcut being armed is.
        assert not _run(
            {"keyOsuSmoke": "LeftShift"},
            {A11Y_PROBE_ID: ProbeResult.of(A11Y_PROBE_ID, _shortcuts(sticky=_FEATURE_ON), "spi")},
            "sys.a11y.shift_binding_triggers_sticky_keys",
        )

    def test_multiple_shift_bindings_read_grammatically(self) -> None:
        findings = _run(
            {"keyOsuSmoke": "LeftShift", "keyFruitsDash": "LeftShift"},
            {A11Y_PROBE_ID: ProbeResult.of(A11Y_PROBE_ID, _shortcuts(sticky=_HOTKEY_ARMED), "spi")},
            "sys.a11y.shift_binding_triggers_sticky_keys",
        )
        assert "use Shift" in findings[0].summary  # type: ignore[attr-defined]

    def test_filter_and_toggle_hotkeys_report_separately(self) -> None:
        probes = {
            A11Y_PROBE_ID: ProbeResult.of(
                A11Y_PROBE_ID, _shortcuts(filt=_HOTKEY_ARMED, toggle=_HOTKEY_ARMED), "spi"
            )
        }
        assert _run({}, probes, "sys.a11y.filterkeys_hotkey")
        assert _run({}, probes, "sys.a11y.togglekeys_hotkey")


class TestSystemRules:
    def test_throttling_power_plan_is_reported(self) -> None:
        probes = {
            POWER_PROBE_ID: ProbeResult.of(
                POWER_PROBE_ID,
                PowerScheme("381b4222-f694-41f0-9685-ff5bb260df2e", "Balanced"),
                "powrprof",
            )
        }
        findings = _run({}, probes, "sys.power.throttling_scheme")
        assert findings[0].recommended_value == "High performance"  # type: ignore[attr-defined]

    def test_high_performance_is_silent(self) -> None:
        probes = {
            POWER_PROBE_ID: ProbeResult.of(
                POWER_PROBE_ID,
                PowerScheme("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", "High performance"),
                "powrprof",
            )
        }
        assert not _run({}, probes, "sys.power.throttling_scheme")

    @pytest.mark.parametrize(("current", "reported"), [(0.5, False), (1.0, False), (15.625, True)])
    def test_timer_resolution_threshold(self, current: float, reported: bool) -> None:
        probes = {
            TIMER_PROBE_ID: ProbeResult.of(
                TIMER_PROBE_ID, TimerResolution(15.625, 0.5, current), "ntdll"
            )
        }
        assert bool(_run({}, probes, "sys.timer.resolution_coarse")) is reported

    def test_game_mode_is_labelled_a_preference(self) -> None:
        # Genuinely disputed, so it must not render with the authority of a
        # measurement.
        probes = {
            GAMEBAR_PROBE_ID: ProbeResult.of(
                GAMEBAR_PROBE_ID, GameBarSettings(game_mode=True, game_dvr=False), "reg"
            )
        }
        findings = _run({}, probes, "sys.win.game_mode_on")
        assert findings[0].basis is Basis.PREFERENCE  # type: ignore[attr-defined]
        assert findings[0].recommended_value is None  # type: ignore[attr-defined]

    def test_fso_recommendation_appends_rather_than_replaces(self) -> None:
        # Replacing the value would drop whatever shim osu! installed there.
        probes = {
            FSO_PROBE_ID: ProbeResult.of(
                FSO_PROBE_ID,
                FullscreenOptimizations("osu!.exe", "$ IgnoreFreeLibrary<osu!au>"),
                "reg",
            )
        }
        findings = _run({}, probes, "sys.win.fso_not_disabled")
        recommended = findings[0].recommended_value  # type: ignore[attr-defined]
        assert "IgnoreFreeLibrary" in recommended
        assert FSO_DISABLED_FLAG in recommended

    def test_fso_already_disabled_is_silent(self) -> None:
        probes = {
            FSO_PROBE_ID: ProbeResult.of(
                FSO_PROBE_ID,
                FullscreenOptimizations("osu!.exe", f"~ {FSO_DISABLED_FLAG}"),
                "reg",
            )
        }
        assert not _run({}, probes, "sys.win.fso_not_disabled")

    def test_running_osu_is_noted_as_a_freshness_caveat(self) -> None:
        probes = {
            OSU_PROCESS_PROBE_ID: ProbeResult.of(
                OSU_PROCESS_PROBE_ID, OsuProcess(running=True, pid=1234, exe="osu!.exe"), "psutil"
            )
        }
        findings = _run({}, probes, "sys.process.osu_running")
        assert findings[0].severity is Severity.INFO  # type: ignore[attr-defined]
        assert any("never reads its memory" in c for c in findings[0].caveats)  # type: ignore[attr-defined]

    def test_osu_not_running_is_silent(self) -> None:
        probes = {
            OSU_PROCESS_PROBE_ID: ProbeResult.of(
                OSU_PROCESS_PROBE_ID, OsuProcess(running=False), "psutil"
            )
        }
        assert not _run({}, probes, "sys.process.osu_running")


class TestAudioRules:
    def test_compatibility_mode_needs_no_probe(self) -> None:
        # It reads a config key, so it must answer under `--no-probes` too.
        findings = _run({"AudioCompatibility": "1"}, {}, "cfg.audio.compatibility_mode")
        assert len(findings) == 1
        assert not _run({"AudioCompatibility": "1"}, {}, "cfg.audio.compatibility_mode.skipped")

    @pytest.mark.parametrize("entries", [{}, {"AudioCompatibility": "0"}])
    def test_compatibility_mode_off_or_absent_is_silent(self, entries: dict[str, str]) -> None:
        assert not _run(entries, {}, "cfg.audio.compatibility_mode")

    def test_compatibility_mode_states_the_fact_and_attributes_the_rest(self) -> None:
        # The setting being on is a fact. What it does to the output path is
        # the community's account, and the finding has to say so rather than
        # dress it as a reading.
        finding = _run({"AudioCompatibility": "1"}, {}, "cfg.audio.compatibility_mode")[0]
        assert finding.basis is Basis.HARD_FACT  # type: ignore[attr-defined]
        assert finding.severity is Severity.INFO  # type: ignore[attr-defined]
        assert "Players understand it" in finding.rationale  # type: ignore[attr-defined]
        assert any("never reads osu!'s memory" in c for c in finding.caveats)  # type: ignore[attr-defined]
        # No recommendation: changing it invalidates every offset measured
        # before the change, which is not a fix to hand out unprompted.
        assert finding.recommended_value is None  # type: ignore[attr-defined]

    def test_compatibility_mode_claims_no_offset_history(self) -> None:
        # The rule has read one config key. It does not know when the setting
        # was turned on, and it has seen no replay, no journal and no offset,
        # so the summary has to read the same for a player who has had this on
        # for a year and one who turned it on ten minutes ago because of
        # crackling. Saying an offset "was measured on this audio path" is
        # backwards for the second player, whose offset was measured on the
        # other one.
        finding = _run({"AudioCompatibility": "1"}, {}, "cfg.audio.compatibility_mode")[0]
        summary = finding.summary  # type: ignore[attr-defined]
        assert "Any offset you tune with this on" in summary
        assert "splits your replay history" in summary
        assert "was measured" not in summary

    def test_an_empty_audio_device_is_the_system_default_not_a_mismatch(self) -> None:
        assert not _run({"AudioDevice": ""}, _audio(_endpoint()), "sys.audio.device_missing")
        assert not _run({}, _audio(_endpoint()), "sys.audio.device_missing")

    @pytest.mark.parametrize(
        "configured",
        ["Speakers (Test Audio)", "speakers test audio", "Test Audio", "SPEAKERS(TESTAUDIO)"],
    )
    def test_a_name_that_differs_only_in_wording_is_not_a_mismatch(self, configured: str) -> None:
        # osu! and Windows spell the same device differently, and a rule that
        # called that a missing device would be confidently wrong about
        # something the user can see in their own options menu.
        assert not _run(
            {"AudioDevice": configured}, _audio(_endpoint()), "sys.audio.device_missing"
        )

    def test_an_unrelated_name_is_shown_beside_the_endpoint_without_a_verdict(self) -> None:
        findings = _run(
            {"AudioDevice": "Headphones (Old Interface)"},
            _audio(_endpoint("Speakers (Test Audio)")),
            "sys.audio.device_missing",
        )
        assert len(findings) == 1
        finding = findings[0]
        assert finding.severity is Severity.INFO  # type: ignore[attr-defined]
        assert "Headphones (Old Interface)" in finding.summary  # type: ignore[attr-defined]
        assert "Speakers (Test Audio)" in finding.summary  # type: ignore[attr-defined]
        # Reading only the default endpoint cannot establish that a device is
        # gone, so the finding must not claim it is.
        assert "does not say the" in finding.rationale  # type: ignore[attr-defined]
        assert finding.recommended_value is None  # type: ignore[attr-defined]

    def test_one_offered_period_is_reported_as_the_drivers_offer(self) -> None:
        findings = _run({}, _audio(_endpoint()), "sys.audio.no_low_latency_shared")
        assert len(findings) == 1
        finding = findings[0]
        assert finding.basis is Basis.HARD_FACT  # type: ignore[attr-defined]
        assert finding.severity is Severity.INFO  # type: ignore[attr-defined]
        assert "10.000 ms" in finding.title  # type: ignore[attr-defined]
        assert finding.recommended_value is None  # type: ignore[attr-defined]
        assert any("not your audio latency" in c for c in finding.caveats)  # type: ignore[attr-defined]
        assert any("BASS" in c for c in finding.caveats)  # type: ignore[attr-defined]

    def test_a_driver_offering_a_lower_period_is_silent(self) -> None:
        probes = _audio(_endpoint(minimum_frames=128))
        assert not _run({}, probes, "sys.audio.no_low_latency_shared")

    def test_no_iaudioclient3_is_silent_rather_than_reported_as_no_offer(self) -> None:
        # Older Windows has no interface to ask. Not knowing and being told
        # "no" are different answers and only one of them was measured.
        probes = _audio(_endpoint(engine=False))
        assert not _run({}, probes, "sys.audio.no_low_latency_shared")

    def test_an_unreadable_endpoint_skips_visibly(self) -> None:
        probes: dict[str, object] = {
            AUDIO_PROBE_ID: ProbeResult.unavailable(
                AUDIO_PROBE_ID, "IAudioClient", "Windows reports no default playback device"
            )
        }
        for rule_id in ("sys.audio.device_missing", "sys.audio.no_low_latency_shared"):
            skipped = _run({"AudioDevice": "Headphones"}, probes, f"{rule_id}.skipped")
            assert len(skipped) == 1, rule_id
            assert "no default playback device" in str(skipped[0].evidence[0].value)  # type: ignore[attr-defined]


class TestProbeTypeGuard:
    def test_a_probe_returning_the_wrong_type_fails_loudly(self) -> None:
        # Generic functions are not subscriptable at runtime, so the helper takes
        # the type as an argument — and checks it, which turns a mismatched probe
        # into a caught rule error rather than an attribute error deeper in.
        probes = {MOUSE_PROBE_ID: ProbeResult.of(MOUSE_PROBE_ID, "not settings", "reg")}
        context = RuleContext(
            config=OsuConfig.parse(build_cfg({}, header=False)),
            probes=probes,  # type: ignore[arg-type]
        )
        findings = run_rules(ALL_RULES, context)
        errors = [f for f in findings if f.id.endswith(".error")]
        assert errors
        assert "TypeError" in errors[0].summary
