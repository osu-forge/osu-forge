"""The rule engine.

Rules are **data**, not scattered conditionals. Each is a record carrying its
own metadata — severity, basis, confidence, source link, which probes it needs —
and a callable that turns a context into findings. Adding a rule is appending
one record, which is what keeps the rule set auditable as it grows past a
couple of dozen entries.

Two properties matter more than the dispatch itself:

**Nothing is silently skipped.** A rule whose probe is unavailable emits an info
finding saying so. Otherwise a check that could not run looks exactly like a
check that passed, and the report quietly overstates how much was verified.

**One bad rule cannot take down the run.** A rule that raises is caught, turned
into a finding, and the remaining rules still evaluate. A partial report beats
a stack trace.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from osuforge.config.parser import OsuConfig
from osuforge.models import (
    Basis,
    Category,
    Confidence,
    Evidence,
    Finding,
    Severity,
    Subsystem,
)
from osuforge.probes.base import ProbeResult

__all__ = ["Rule", "RuleContext", "run_rules"]


@dataclass(frozen=True, slots=True)
class RuleContext:
    """Everything a rule is allowed to look at.

    Rules read from here and nowhere else — no filesystem, no registry, no
    network. That keeps them pure enough to test with a literal context and
    stops two rules from independently probing the same thing.
    """

    config: OsuConfig
    probes: Mapping[str, ProbeResult[object]] = field(default_factory=dict)

    def probe(self, probe_id: str) -> ProbeResult[object] | None:
        return self.probes.get(probe_id)

    def missing_probes(self, required: Iterable[str]) -> list[str]:
        return [
            probe_id
            for probe_id in required
            if (result := self.probes.get(probe_id)) is None or not result.ok
        ]


RuleFn = Callable[[RuleContext], Finding | Sequence[Finding] | None]


@dataclass(frozen=True, slots=True)
class Rule:
    """One check.

    ``basis`` and ``confidence`` live on the rule rather than being decided
    inside it, so a reviewer can see at a glance which rules are asserting facts
    and which are expressing a preference.
    """

    id: str
    title: str
    category: Category
    subsystem: Subsystem
    severity: Severity
    basis: Basis
    confidence: Confidence
    evaluate: RuleFn
    requires: tuple[str, ...] = ()
    source_url: str | None = None


def _as_list(result: Finding | Sequence[Finding] | None) -> list[Finding]:
    if result is None:
        return []
    if isinstance(result, Finding):
        return [result]
    return list(result)


def _skipped(rule: Rule, missing: list[str], context: RuleContext) -> Finding:
    details = []
    for probe_id in missing:
        probe = context.probe(probe_id)
        reason = probe.reason if probe is not None else "probe did not run"
        details.append(
            Evidence(
                label=probe_id,
                value=reason or "unavailable",
                source=probe.source if probe is not None else "n/a",
            )
        )
    return Finding(
        id=f"{rule.id}.skipped",
        title=f"Not checked: {rule.title}",
        summary=(
            f"This check could not run because {', '.join(missing)} "
            f"{'was' if len(missing) == 1 else 'were'} unavailable."
        ),
        severity=Severity.INFO,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=rule.subsystem,
        category=rule.category,
        rationale=(
            "Reported rather than dropped: a check that could not run must not "
            "look the same as a check that passed."
        ),
        evidence=details,
        requires_elevation=any(
            (probe := context.probe(pid)) is not None and probe.requires_elevation
            for pid in missing
        ),
    )


def _crashed(rule: Rule, exc: Exception) -> Finding:
    return Finding(
        id=f"{rule.id}.error",
        title=f"Rule failed: {rule.title}",
        summary=f"{rule.id} raised {type(exc).__name__} and was skipped.",
        severity=Severity.WARN,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=rule.subsystem,
        category=rule.category,
        rationale=(
            "This is a bug in osu-forge, not a problem with your setup. The "
            "remaining checks still ran."
        ),
        evidence=[
            Evidence(
                label="traceback",
                value=traceback.format_exc(limit=6).strip(),
                source=f"rule {rule.id}",
            )
        ],
    )


def run_rules(rules: Iterable[Rule], context: RuleContext) -> list[Finding]:
    """Evaluate every rule and return findings, most severe first."""
    findings: list[Finding] = []

    for rule in rules:
        missing = context.missing_probes(rule.requires)
        if missing:
            findings.append(_skipped(rule, missing, context))
            continue

        try:
            produced = _as_list(rule.evaluate(context))
        # Deliberately broad. A rule raising anything at all must cost the user
        # that one finding, not the whole report. See the module docstring.
        except Exception as exc:
            findings.append(_crashed(rule, exc))
            continue

        findings.extend(produced)

    return Finding.sorted(findings)
