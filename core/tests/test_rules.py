"""Rule engine behaviour, and the config-only rules.

The engine tests matter as much as the rules: a check that silently vanished
because its probe was missing looks exactly like a check that passed, and one
rule raising must not cost the user every other finding.
"""

from __future__ import annotations

import pytest

from osuforge.config import OsuConfig
from osuforge.models import Basis, Category, Confidence, Finding, Severity, Subsystem
from osuforge.probes.base import ProbeResult
from osuforge.rules import ALL_RULES, RuleContext, run_rules
from osuforge.rules.engine import Rule

from .fixtures import REALISTIC_CFG, build_cfg


def _context(**entries: str) -> RuleContext:
    return RuleContext(config=OsuConfig.parse(build_cfg(dict(entries), header=False)))


def _rule(rule_id: str, evaluate: object, requires: tuple[str, ...] = ()) -> Rule:
    return Rule(
        id=rule_id,
        title=f"test rule {rule_id}",
        category=Category.KEYBIND,
        subsystem=Subsystem.CONFIG,
        severity=Severity.INFO,
        basis=Basis.HARD_FACT,
        confidence=Confidence.MEASURED,
        evaluate=evaluate,  # type: ignore[arg-type]
        requires=requires,
    )


def _finding(rule_id: str, severity: Severity = Severity.INFO) -> Finding:
    return Finding(
        id=rule_id,
        title="t",
        summary="s",
        severity=severity,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.CONFIG,
        category=Category.KEYBIND,
    )


class TestEngine:
    def test_a_missing_probe_produces_a_visible_skip_not_silence(self) -> None:
        # The whole point: an unrunnable check must not read as a passing one.
        rules = [_rule("needs.probe", lambda _: _finding("x"), requires=("display.refresh",))]
        findings = run_rules(rules, _context(Offset="0"))
        assert [f.id for f in findings] == ["needs.probe.skipped"]
        assert "could not run" in findings[0].summary

    def test_a_present_but_failed_probe_also_counts_as_missing(self) -> None:
        context = RuleContext(
            config=OsuConfig.parse(REALISTIC_CFG),
            probes={
                "display.refresh": ProbeResult.unavailable(
                    "display.refresh", source="EnumDisplaySettingsExW", reason="no display"
                )
            },
        )
        rules = [_rule("needs.probe", lambda _: _finding("x"), requires=("display.refresh",))]
        findings = run_rules(rules, context)
        assert findings[0].id == "needs.probe.skipped"
        assert "no display" in str(findings[0].evidence[0].value)

    def test_elevation_requirement_is_carried_into_the_skip(self) -> None:
        context = RuleContext(
            config=OsuConfig.parse(REALISTIC_CFG),
            probes={
                "hid.interval": ProbeResult.unavailable(
                    "hid.interval",
                    source="IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX",
                    reason="access denied",
                    requires_elevation=True,
                )
            },
        )
        rules = [_rule("needs.hid", lambda _: _finding("x"), requires=("hid.interval",))]
        assert run_rules(rules, context)[0].requires_elevation

    def test_a_satisfied_probe_lets_the_rule_run(self) -> None:
        context = RuleContext(
            config=OsuConfig.parse(REALISTIC_CFG),
            probes={"display.refresh": ProbeResult.of("display.refresh", 240, "Enum...")},
        )
        rules = [_rule("needs.probe", lambda _: _finding("ran"), requires=("display.refresh",))]
        assert [f.id for f in run_rules(rules, context)] == ["ran"]

    def test_a_raising_rule_does_not_take_down_the_run(self) -> None:
        def explode(_: RuleContext) -> Finding:
            raise RuntimeError("boom")

        rules = [
            _rule("bad", explode),
            _rule("good", lambda _: _finding("survivor", Severity.CRITICAL)),
        ]
        findings = run_rules(rules, _context(Offset="0"))
        ids = [f.id for f in findings]
        assert "survivor" in ids
        assert "bad.error" in ids

    def test_a_crash_is_reported_as_our_bug_not_the_users(self) -> None:
        def explode(_: RuleContext) -> Finding:
            raise ValueError("boom")

        finding = run_rules([_rule("bad", explode)], _context(Offset="0"))[0]
        assert "bug in osu-forge" in finding.rationale
        assert "ValueError" in finding.summary

    def test_a_rule_returning_none_produces_nothing(self) -> None:
        assert run_rules([_rule("quiet", lambda _: None)], _context(Offset="0")) == []

    def test_findings_are_ordered_by_severity(self) -> None:
        rules = [
            _rule("a", lambda _: _finding("info", Severity.INFO)),
            _rule("b", lambda _: _finding("critical", Severity.CRITICAL)),
            _rule("c", lambda _: _finding("warn", Severity.WARN)),
        ]
        assert [f.id for f in run_rules(rules, _context(Offset="0"))] == [
            "critical",
            "warn",
            "info",
        ]

    def test_rule_ids_are_unique(self) -> None:
        ids = [rule.id for rule in ALL_RULES]
        assert len(ids) == len(set(ids))


class TestKnownFindings:
    """These reproduce problems measured in a real config.

    If a change to the rule set stops these firing, that is a regression even if
    every other test passes.
    """

    def test_the_space_keybind_conflict_is_reported(self) -> None:
        findings = run_rules(ALL_RULES, RuleContext(config=OsuConfig.parse(REALISTIC_CFG)))
        conflict = next(f for f in findings if f.id == "cfg.keybind.duplicate_live")
        assert conflict.severity is Severity.CRITICAL
        assert "keySkip" in conflict.summary
        assert "keyQuickRetry" in conflict.summary

    def test_the_leftshift_pair_is_not_reported(self) -> None:
        # keyOsuSmoke and keyFruitsDash both use LeftShift in the real config and
        # are not a conflict. This is the false positive the scope model exists
        # to prevent.
        findings = run_rules(ALL_RULES, RuleContext(config=OsuConfig.parse(REALISTIC_CFG)))
        conflicts = [f for f in findings if f.id == "cfg.keybind.duplicate_live"]
        assert len(conflicts) == 1
        assert "keyFruitsDash" not in conflicts[0].summary

    def test_custom_frame_limit_is_reported_as_inert(self) -> None:
        findings = run_rules(ALL_RULES, RuleContext(config=OsuConfig.parse(REALISTIC_CFG)))
        inert = next(f for f in findings if f.id == "cfg.frame.custom_limit_inert")
        assert "240" in inert.summary
        assert "Optimal" in inert.summary
        # Deliberately no recommendation: both resolutions are legitimate and
        # which is right depends on what the user intended.
        assert inert.recommended_value is None


class TestFrameRules:
    def test_custom_frame_limit_is_silent_when_framesync_is_custom(self) -> None:
        findings = run_rules(ALL_RULES, _context(FrameSync="Custom", CustomFrameLimit="240"))
        assert not [f for f in findings if f.id == "cfg.frame.custom_limit_inert"]

    def test_silent_when_no_custom_limit_is_set(self) -> None:
        findings = run_rules(ALL_RULES, _context(FrameSync="Optimal"))
        assert not [f for f in findings if f.id == "cfg.frame.custom_limit_inert"]

    def test_vsync_latency_is_flagged_as_a_tradeoff_not_a_fault(self) -> None:
        findings = run_rules(ALL_RULES, _context(FrameSync="VSync"))
        vsync = next(f for f in findings if f.id == "cfg.frame.vsync_latency")
        assert vsync.severity is Severity.INFO
        assert vsync.basis is Basis.COMMUNITY_CONSENSUS


class TestMouseRules:
    def test_raw_input_off_is_reported_with_a_concrete_fix(self) -> None:
        findings = run_rules(ALL_RULES, _context(RawInput="0"))
        raw = next(f for f in findings if f.id == "cfg.mouse.raw_input_off")
        assert raw.current_value == "0"
        assert raw.recommended_value == "1"

    def test_raw_input_on_is_silent(self) -> None:
        findings = run_rules(ALL_RULES, _context(RawInput="1"))
        assert not [f for f in findings if f.id == "cfg.mouse.raw_input_off"]

    def test_unity_mouse_speed_is_silent(self) -> None:
        findings = run_rules(ALL_RULES, _context(MouseSpeed="1"))
        assert not [f for f in findings if f.id == "cfg.mouse.speed_not_unity"]

    def test_non_unity_in_range_is_informational_only(self) -> None:
        findings = run_rules(ALL_RULES, _context(MouseSpeed="1.4"))
        speed = next(f for f in findings if f.id == "cfg.mouse.speed_not_unity")
        assert speed.severity is Severity.INFO
        assert speed.basis is Basis.COMMUNITY_CONSENSUS
        # In range and merely unconventional, so no value is prescribed.
        assert speed.recommended_value is None

    @pytest.mark.parametrize("value", ["0.2", "8.5"])
    def test_out_of_range_is_a_warning_because_osu_reverts_it(self, value: str) -> None:
        findings = run_rules(ALL_RULES, _context(MouseSpeed=value))
        speed = next(f for f in findings if f.id == "cfg.mouse.speed_not_unity")
        assert speed.severity is Severity.WARN
        assert speed.recommended_value == "1"
        assert "re-clamps" in speed.rationale

    def test_the_two_mousespeed_settings_are_never_confused(self) -> None:
        # The Windows registry MouseSpeed is the Enhance Pointer Precision
        # toggle; osu!'s is a sensitivity multiplier. Same name, unrelated.
        findings = run_rules(ALL_RULES, _context(MouseSpeed="2.0"))
        speed = next(f for f in findings if f.id == "cfg.mouse.speed_not_unity")
        assert any("Enhance Pointer Precision" in c for c in speed.caveats)


class TestNoRuleReadsOutsideTheContext:
    def test_rules_run_with_no_probes_at_all(self) -> None:
        # Every rule shipped so far is config-only, so `forge doctor` must work
        # with no probes, no elevation, and osu! not running.
        findings = run_rules(ALL_RULES, RuleContext(config=OsuConfig.parse(REALISTIC_CFG)))
        assert not [f for f in findings if f.id.endswith(".skipped")]
        assert not [f for f in findings if f.id.endswith(".error")]
