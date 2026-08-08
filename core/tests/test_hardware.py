"""The one thing the player has to tell us, and what it is allowed to affect."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from osuforge.hardware import (
    ASYMMETRIC_CUTOFF_PRESETS,
    Mouse,
    Profile,
    ProfileError,
    Tracking,
    default_profile_path,
    load,
    save,
)


class TestMouse:
    def test_a_single_dpi_has_no_anisotropy(self) -> None:
        mouse = Mouse(dpi_x=800, dpi_y=800)
        assert mouse.anisotropy == 0.0
        assert not mouse.axes_differ
        assert mouse.describe() == "800 DPI"

    def test_different_axes_are_reported_not_averaged(self) -> None:
        # Averaging to 1375 would hide the thing that matters: vertical hand
        # motion produces more cursor motion than horizontal, so a scalar gain
        # is the wrong model rather than an imprecise one.
        mouse = Mouse(dpi_x=1350, dpi_y=1400)
        assert mouse.anisotropy == pytest.approx(0.037, abs=0.001)
        assert mouse.axes_differ
        assert "+3.70%" in mouse.describe()
        assert "separately" in mouse.describe()

    def test_a_negligible_difference_is_not_anisotropy(self) -> None:
        assert not Mouse(dpi_x=1600, dpi_y=1601).axes_differ

    def test_the_step_is_a_fraction_of_the_dpi_not_a_constant(self) -> None:
        # The whole answer to "what if DPI only moves in 50s": the same step is
        # a nudge on one mouse and an overcorrection on another.
        assert Mouse(800, 800).step_fraction(50)[0] == pytest.approx(0.0625)
        assert Mouse(3200, 3200).step_fraction(50)[0] == pytest.approx(0.015625)
        x, y = Mouse(1350, 1400).step_fraction(50)
        assert (x, y) == (pytest.approx(50 / 1350), pytest.approx(50 / 1400))
        assert x > y, "the coarser axis is the one with fewer counts per inch"

    @pytest.mark.parametrize("dpi", [0, -800, 50, 99, 32_001, 1_000_000])
    def test_an_impossible_dpi_is_refused(self, dpi: int) -> None:
        # A wrong denominator turns a real measurement into a confident wrong
        # recommendation, and nothing downstream can tell.
        with pytest.raises(ProfileError, match="typo rather than a mouse"):
            Mouse(dpi_x=dpi, dpi_y=800)
        with pytest.raises(ProfileError):
            Mouse(dpi_x=800, dpi_y=dpi)


class TestTracking:
    def test_the_presets_are_all_hysteresis(self) -> None:
        # Landing below lift-off in every one: the sensor cuts out higher than
        # it resumes, so it does not flicker while the mouse is glided at the
        # threshold. A preset with landing above lift-off would be a typo.
        for preset, (landing, lift_off) in ASYMMETRIC_CUTOFF_PRESETS.items():
            assert landing < lift_off, f"preset {preset}"

    def test_a_preset_carries_its_own_provenance(self) -> None:
        tracking = Tracking.asymmetric(1)
        assert (tracking.landing_mm, tracking.lift_off_mm) == (1.0, 2.0)
        assert "asymmetric cut-off 1" in tracking.describe()
        assert "lift-off 1mm" not in tracking.describe(), "lift-off is the 2mm one"

    def test_an_unknown_preset_lists_the_real_ones(self) -> None:
        with pytest.raises(ProfileError, match="not one of 1, 2, 3"):
            Tracking.asymmetric(4)

    def test_only_the_tightest_preset_is_below_the_line(self) -> None:
        # Preset 1 alone cuts out at 2mm; 2 and 3 both go to 3mm and differ only
        # in where they resume, which is not the distance that matters here.
        assert not Tracking.asymmetric(1).of_concern, "2mm is the line, not past it"
        assert Tracking.asymmetric(2).of_concern
        assert Tracking.asymmetric(3).of_concern

    def test_a_label_without_millimetres_is_not_treated_as_known(self) -> None:
        # Low/Medium/High are not published in mm. Recording a guess would put
        # an invented number into a caveat that reads as measured.
        tracking = Tracking(label="Medium")
        assert not tracking.known
        assert not tracking.of_concern, "unknown is its own case, not a bad one"
        assert "distances unknown" in tracking.describe()

    @pytest.mark.parametrize("mm", [0.0, -1.0, 11.0])
    def test_an_impossible_height_is_refused(self, mm: float) -> None:
        with pytest.raises(ProfileError, match="plausible sensor height"):
            Tracking(lift_off_mm=mm)


class TestRoundTrip:
    def test_saving_and_loading_returns_the_same_profile(self, tmp_path: Path) -> None:
        path = tmp_path / "profile.json"
        original = Profile(pointer=Mouse(1350, 1400), tracking=Tracking.asymmetric(2))
        save(original, path)
        assert load(path) == original

    def test_tracking_survives_without_a_mouse(self, tmp_path: Path) -> None:
        # The two are declared independently, so one must not drag the other
        # along or drop it.
        path = tmp_path / "profile.json"
        save(Profile(tracking=Tracking(label="High")), path)
        loaded = load(path)
        assert loaded.pointer is None
        assert loaded.tracking == Tracking(label="High")

    def test_an_absent_file_is_an_empty_profile_not_an_error(self, tmp_path: Path) -> None:
        # The normal state on a first run. Everything downstream works without
        # it, less precisely, and says so.
        profile = load(tmp_path / "nothing.json")
        assert profile.pointer is None
        assert profile.mouse is None

    def test_the_written_form_is_tagged(self, tmp_path: Path) -> None:
        path = tmp_path / "profile.json"
        save(Profile(pointer=Mouse(800, 800)), path)
        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["pointer"]["kind"] == "mouse"
        assert written["schema"] == 1

    def test_an_empty_profile_writes_no_pointer(self, tmp_path: Path) -> None:
        path = tmp_path / "profile.json"
        save(Profile(), path)
        assert "pointer" not in json.loads(path.read_text(encoding="utf-8"))

    def test_saving_creates_the_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "deeper" / "profile.json"
        assert save(Profile(pointer=Mouse(800, 800)), path) == path
        assert path.is_file()


class TestRefusal:
    def write(self, tmp_path: Path, data: object) -> Path:
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_an_unsupported_pointer_kind_is_refused_rather_than_skipped(
        self, tmp_path: Path
    ) -> None:
        # A tablet profile read by a build without tablet support must not come
        # back as "no pointer declared" and get analysed as though the question
        # had never been asked.
        path = self.write(tmp_path, {"pointer": {"kind": "tablet", "width_mm": 100}})
        with pytest.raises(ProfileError, match="'tablet' is not supported"):
            load(path)

    def test_a_newer_schema_is_refused(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, {"schema": 99, "pointer": {"kind": "mouse"}})
        with pytest.raises(ProfileError, match="newer osu-forge"):
            load(path)

    def test_malformed_json_names_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "profile.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ProfileError, match=r"profile\.json"):
            load(path)

    def test_a_missing_axis_is_refused(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, {"pointer": {"kind": "mouse", "dpi_x": 800}})
        with pytest.raises(ProfileError, match="dpi_y"):
            load(path)

    def test_a_non_numeric_dpi_is_refused(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, {"pointer": {"kind": "mouse", "dpi_x": "eight", "dpi_y": 800}})
        with pytest.raises(ProfileError, match="whole number"):
            load(path)

    def test_a_dpi_out_of_range_in_a_file_is_refused_the_same_way(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, {"pointer": {"kind": "mouse", "dpi_x": 5, "dpi_y": 5}})
        with pytest.raises(ProfileError, match="typo rather than a mouse"):
            load(path)


class TestPaths:
    def test_the_env_override_wins(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("OSU_FORGE_PROFILE", str(tmp_path / "elsewhere.json"))
        assert default_profile_path() == tmp_path / "elsewhere.json"

    def test_the_default_sits_beside_the_journal_not_inside_osu(self, monkeypatch) -> None:
        monkeypatch.delenv("OSU_FORGE_PROFILE", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")
        path = default_profile_path()
        assert path.parent.name == "osu-forge"
        assert "osu!" not in str(path), "nothing this tool writes belongs to the game"
