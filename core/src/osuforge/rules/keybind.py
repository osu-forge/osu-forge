"""Keybinding rules."""

from __future__ import annotations

from osuforge.config.keybinds import find_conflicts
from osuforge.models import (
    Basis,
    Category,
    Confidence,
    Evidence,
    Finding,
    Severity,
    Subsystem,
)
from osuforge.rules.engine import Rule, RuleContext

__all__ = ["RULES"]

# osu!'s stock binding for keys that are commonly found in an accidental
# collision. Only keys whose default is documented are listed — suggesting a
# replacement we are not sure about would be worse than suggesting none.
_STOCK_DEFAULTS: dict[str, str] = {
    "keyQuickRetry": "OemTilde",
}

_KEY_DISPLAY: dict[str, str] = {
    "OemTilde": "` (backtick)",
    "OemPlus": "= (equals)",
    "OemMinus": "- (minus)",
}


def _display(value: str) -> str:
    return _KEY_DISPLAY.get(value, value)


def _duplicate_live_bindings(context: RuleContext) -> list[Finding]:
    findings: list[Finding] = []

    for conflict in find_conflicts(context.config):
        keys = list(conflict.keys)
        suggestion = next((k for k in keys if k in _STOCK_DEFAULTS), None)

        recommended: str | None = None
        rationale = (
            "These bindings are live at the same time, so pressing this key "
            "triggers more than one action. Rebind one of them."
        )
        if suggestion is not None:
            stock = _STOCK_DEFAULTS[suggestion]
            recommended = f"{suggestion} = {stock}"
            rationale = (
                f"These bindings are live at the same time, so pressing "
                f"{_display(conflict.value)} triggers more than one action. "
                f"osu!'s stock binding for {suggestion} is {_display(stock)}, "
                f"which suggests this collision was accidental.\n\n"
                "Note: exactly how osu! resolves two live bindings on one key is "
                "not documented — it may separate them by press-versus-hold. The "
                "collision itself is a fact; the resulting behaviour is not "
                "something we can verify from the config alone."
            )

        findings.append(
            Finding(
                id="cfg.keybind.duplicate_live",
                title=f"{' and '.join(keys)} share a binding",
                summary=conflict.describe(),
                severity=Severity.CRITICAL,
                confidence=Confidence.MEASURED,
                basis=Basis.HARD_FACT,
                subsystem=Subsystem.CONFIG,
                category=Category.KEYBIND,
                rationale=rationale,
                current_value=f"{keys[0]} = {conflict.value}" if recommended else conflict.value,
                recommended_value=recommended,
                evidence=[
                    Evidence(
                        label=binding.key,
                        value=binding.value,
                        source=f"osu!.cfg line {binding.lineno}",
                    )
                    for binding in conflict.bindings
                ]
                + [
                    Evidence(
                        label="overlapping contexts",
                        value=", ".join(sorted(conflict.scopes)),
                        source="osuforge.config.keybinds scope table",
                    )
                ],
                verification=(
                    "Rebind one of them in Options, then re-run `forge doctor`. "
                    "This finding should disappear."
                ),
            )
        )

    return findings


RULES: tuple[Rule, ...] = (
    Rule(
        id="cfg.keybind.duplicate_live",
        title="Two bindings live at once on the same key",
        category=Category.KEYBIND,
        subsystem=Subsystem.CONFIG,
        severity=Severity.CRITICAL,
        basis=Basis.HARD_FACT,
        confidence=Confidence.MEASURED,
        evaluate=_duplicate_live_bindings,
    ),
)
