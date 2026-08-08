"""The rule set.

Rules are data. :data:`ALL_RULES` is the registry the engine walks; adding a
check means appending a record to one of the modules below and nothing else.

Everything here currently reads only the osu! config, so it runs with no probes,
no elevation, and no osu! process. Rules that need a measurement — the real
monitor refresh rate, the audio device period, the Windows pointer settings —
declare it via ``Rule.requires`` and are skipped with a visible finding when the
probe is unavailable.
"""

from __future__ import annotations

from osuforge.rules import display, frame, keybind, mouse, system
from osuforge.rules.engine import Rule, RuleContext, run_rules

__all__ = ["ALL_RULES", "CONFIG_ONLY_RULES", "Rule", "RuleContext", "run_rules"]

CONFIG_ONLY_RULES: tuple[Rule, ...] = (
    *keybind.RULES,
    *frame.RULES,
    *mouse.RULES,
)
"""Rules that need nothing but the config file. These run with no probes, no
elevation, and osu! not running."""

ALL_RULES: tuple[Rule, ...] = (
    *CONFIG_ONLY_RULES,
    *display.RULES,
    *system.RULES,
)


def _assert_unique_ids() -> None:
    seen: set[str] = set()
    for rule in ALL_RULES:
        if rule.id in seen:
            raise RuntimeError(f"duplicate rule id: {rule.id}")
        seen.add(rule.id)


_assert_unique_ids()
