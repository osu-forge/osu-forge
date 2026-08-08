"""The gate in front of every statement about aim."""

from __future__ import annotations

import pytest

from osuforge.analysis.pointer import assess
from osuforge.hardware import Mouse, Profile, Tracking
from osuforge.probes.mouse import WindowsMouseSettings


def windows(
    *, sensitivity: int = 10, epp: int = 0, t1: int = 0, t2: int = 0
) -> WindowsMouseSettings:
    return WindowsMouseSettings(
        sensitivity=sensitivity, enhance_pointer_precision=epp, threshold1=t1, threshold2=t2
    )


MOUSE = Profile(pointer=Mouse(1350, 1400))


class TestRawInputDecides:
    def test_raw_input_makes_the_windows_settings_irrelevant(self) -> None:
        # osu! reads counts from the device; the Windows pipeline never touches
        # them. Acceleration on the desktop is a real finding about the desktop
        # and says nothing about what the replay recorded.
        chain = assess(
            raw_input=True, windows=windows(sensitivity=18, epp=1, t1=6, t2=10), profile=MOUSE
        )
        assert chain.bypasses_windows
        assert chain.may_measure
        assert chain.may_recommend
        assert chain.blockers == []

    def test_acceleration_without_raw_input_blocks_everything(self) -> None:
        # Fatal rather than a caveat: the confound is exactly the variable the
        # speed-conditioned results condition on, so "drifts more on fast
        # movements" would be a description of the acceleration curve.
        chain = assess(raw_input=False, windows=windows(epp=1), profile=MOUSE)
        assert not chain.may_measure
        assert not chain.may_recommend
        assert "acceleration" in chain.summary()
        assert "not valid" in chain.summary()

    def test_the_thresholds_count_as_acceleration_too(self) -> None:
        chain = assess(raw_input=False, windows=windows(epp=0, t1=6, t2=10), profile=MOUSE)
        assert not chain.may_measure

    def test_a_speed_slider_off_the_notch_permits_measurement_but_not_advice(self) -> None:
        # A constant multiple is absorbed by the gain estimate, so the numbers
        # stand. What does not stand is the recommendation: counts are being
        # discarded before osu! sees them, and DPI is the wrong knob for that.
        chain = assess(raw_input=False, windows=windows(sensitivity=14), profile=MOUSE)
        assert chain.may_measure
        assert not chain.may_recommend
        assert "wrong thing" in chain.summary()

    def test_the_notch_with_no_acceleration_is_clean(self) -> None:
        chain = assess(raw_input=False, windows=windows(), profile=MOUSE)
        assert chain.may_recommend

    def test_both_problems_are_reported_not_just_the_first(self) -> None:
        chain = assess(raw_input=False, windows=windows(sensitivity=2, epp=2), profile=MOUSE)
        assert chain.blockers and chain.cautions


class TestUnknowns:
    def test_an_unreadable_raw_input_setting_is_neither_answer(self) -> None:
        chain = assess(raw_input=None, windows=windows(), profile=MOUSE)
        assert chain.may_measure, "measurement is not thrown away over an unknown"
        assert not chain.may_recommend
        assert "could not be read" in chain.summary()

    def test_raw_input_off_with_no_registry_reading_is_a_caution(self) -> None:
        chain = assess(raw_input=False, windows=None, profile=MOUSE)
        assert chain.may_measure
        assert not chain.may_recommend

    def test_no_declared_mouse_costs_only_the_conversion(self) -> None:
        chain = assess(raw_input=True, windows=windows(), profile=Profile())
        assert chain.may_measure
        assert chain.may_recommend
        assert not chain.may_convert_to_counts
        assert "stays a percentage" in chain.summary()

    def test_a_declared_mouse_is_named_in_the_summary(self) -> None:
        chain = assess(raw_input=True, windows=windows(), profile=MOUSE)
        assert chain.may_convert_to_counts
        assert "1350 x 1400 DPI" in chain.summary()


class TestLiftOff:
    """Episodic, so it caveats a reading rather than stopping one."""

    def test_a_high_lift_off_stops_nothing_but_travels_with_the_result(self) -> None:
        chain = assess(
            raw_input=True,
            windows=windows(),
            profile=Profile(pointer=Mouse(800, 800), tracking=Tracking.asymmetric(3)),
        )
        assert chain.may_measure and chain.may_recommend, "not a defect, an alternative cause"
        assert any("repositioning" in c for c in chain.caveats)
        assert any("long jumps and playfield edges" in c for c in chain.caveats)

    def test_a_tight_cutoff_leaves_no_caveat(self) -> None:
        chain = assess(
            raw_input=True,
            windows=windows(),
            profile=Profile(pointer=Mouse(800, 800), tracking=Tracking.asymmetric(1)),
        )
        assert chain.caveats == []
        assert "lift-off 2mm" in chain.summary()

    def test_an_undeclared_cutoff_is_its_own_caveat(self) -> None:
        # Not the same as declared-and-fine: the report has to say which.
        chain = assess(raw_input=True, windows=windows(), profile=MOUSE)
        assert any("not declared" in c for c in chain.caveats)

    def test_a_label_without_millimetres_is_still_unknown(self) -> None:
        chain = assess(
            raw_input=True,
            windows=windows(),
            profile=Profile(pointer=Mouse(800, 800), tracking=Tracking(label="Medium")),
        )
        assert any("not declared" in c for c in chain.caveats)


class TestOrdering:
    @pytest.mark.parametrize(
        ("raw", "win"),
        [
            (True, windows(epp=1)),
            (False, windows()),
            (None, windows()),
        ],
    )
    def test_may_recommend_never_exceeds_may_measure(self, raw: bool | None, win: object) -> None:
        chain = assess(raw_input=raw, windows=win, profile=MOUSE)  # type: ignore[arg-type]
        assert not (chain.may_recommend and not chain.may_measure)
