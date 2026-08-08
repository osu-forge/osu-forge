"""Tests for the Finding contract.

These lock down the invariants the rest of the project relies on. The two that
matter most are that an INSUFFICIENT-confidence finding cannot carry a
recommendation, and that a confidence interval cannot exist without saying how it
was computed — both are ways a plausible-looking wrong number reaches a user.
"""

from __future__ import annotations

import pytest

from osuforge.models import (
    Basis,
    Category,
    Confidence,
    Evidence,
    Finding,
    SampleSize,
    Severity,
    Subsystem,
)


def make_finding(**overrides: object) -> Finding:
    kwargs: dict[str, object] = {
        "id": "test.example",
        "title": "Example",
        "summary": "An example finding.",
        "severity": Severity.INFO,
        "confidence": Confidence.MEASURED,
        "basis": Basis.HARD_FACT,
        "subsystem": Subsystem.CONFIG,
        "category": Category.KEYBIND,
    }
    kwargs.update(overrides)
    return Finding(**kwargs)  # type: ignore[arg-type]


class TestFindingInvariants:
    def test_insufficient_confidence_forbids_a_recommendation(self) -> None:
        # When there is not enough data, the honest output names what is missing.
        # It does not hedge a number.
        with pytest.raises(ValueError, match="INSUFFICIENT"):
            make_finding(
                confidence=Confidence.INSUFFICIENT,
                current_value="0",
                recommended_value="+8",
            )

    def test_insufficient_confidence_may_still_report_a_current_value(self) -> None:
        f = make_finding(confidence=Confidence.INSUFFICIENT, current_value="0")
        assert f.recommended_value is None
        assert not f.has_change

    def test_recommendation_requires_a_current_value_to_diff_against(self) -> None:
        with pytest.raises(ValueError, match="current value"):
            make_finding(recommended_value="+8")

    def test_findings_are_always_advisory(self) -> None:
        # osu-forge has no code path that writes osu!.cfg. This guards against a
        # future change quietly introducing one.
        with pytest.raises(ValueError, match="advisory"):
            make_finding(advisory=False)

    def test_empty_id_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="id is required"):
            make_finding(id="")

    def test_has_change_is_false_when_recommendation_matches_current(self) -> None:
        assert not make_finding(current_value="1", recommended_value="1").has_change
        assert make_finding(current_value="0", recommended_value="1").has_change


class TestEvidenceValidation:
    def test_ci_requires_a_method(self) -> None:
        # Which estimator produced an interval changes how much it should be
        # trusted, so an interval without one is not reportable.
        with pytest.raises(ValueError, match="ci_method"):
            Evidence(label="mean error", value="-8.1 ms", source="offset.mu", ci95=(-12.4, -3.8))

    def test_inverted_ci_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="inverted"):
            Evidence(
                label="mean error",
                value="-8.1 ms",
                source="offset.mu",
                ci95=(-3.8, -12.4),
                ci_method="cluster-t",
            )

    def test_non_finite_ci_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-finite"):
            Evidence(
                label="mean error",
                value="-8.1 ms",
                source="offset.mu",
                ci95=(float("-inf"), 0.0),
                ci_method="cluster-t",
            )

    def test_p_value_range_is_enforced(self) -> None:
        with pytest.raises(ValueError, match="p_value"):
            Evidence(label="x", value="1", source="s", p_value=1.5)

    @pytest.mark.parametrize(
        ("ci", "expected"),
        [
            ((-12.4, -3.8), True),
            ((3.8, 12.4), True),
            ((-3.4, 11.8), False),
            ((0.0, 5.0), False),
        ],
    )
    def test_ci_excludes_zero(self, ci: tuple[float, float], expected: bool) -> None:
        ev = Evidence(label="x", value="1", source="s", ci95=ci, ci_method="cluster-t")
        assert ev.ci_excludes_zero is expected

    def test_ci_excludes_zero_is_false_without_an_interval(self) -> None:
        assert Evidence(label="x", value="1", source="s").ci_excludes_zero is False


class TestOrdering:
    def test_sorted_puts_severe_and_confident_first(self) -> None:
        findings = [
            make_finding(id="c", severity=Severity.INFO),
            make_finding(id="a", severity=Severity.CRITICAL),
            make_finding(id="b", severity=Severity.WARN),
            make_finding(id="d", severity=Severity.PASS),
        ]
        assert [f.id for f in Finding.sorted(findings)] == ["a", "b", "c", "d"]

    def test_confidence_breaks_severity_ties(self) -> None:
        findings = [
            make_finding(id="low", severity=Severity.WARN, confidence=Confidence.LOW),
            make_finding(id="high", severity=Severity.WARN, confidence=Confidence.HIGH),
        ]
        assert [f.id for f in Finding.sorted(findings)] == ["high", "low"]


class TestSampleSize:
    def test_str_reports_every_level_of_nesting(self) -> None:
        # Hits nest in replays which nest in sessions. Reporting only the hit
        # count is how a 0.3 ms mean gets declared "highly significant".
        s = SampleSize(hits=40989, replays=57, sessions=10, effective=980.0, design_effect=41.8)
        rendered = str(s)
        assert "n=40,989 hits" in rendered
        assert "57 replays" in rendered
        assert "10 sessions" in rendered
        assert "n_eff≈980" in rendered

    def test_str_handles_an_empty_sample(self) -> None:
        assert str(SampleSize()) == "no sample"
