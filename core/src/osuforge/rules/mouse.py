"""Mouse and pointer rules that read only the osu! config.

The Windows-side half of this — pointer precision, the 6/11 sensitivity notch —
lives with the registry probes. Keeping them apart matters because
``MouseSpeed`` names two completely unrelated things:

- ``HKCU\\Control Panel\\Mouse\\MouseSpeed`` is the Enhance Pointer Precision
  toggle, values 0/1/2.
- osu!'s ``MouseSpeed`` is a cursor sensitivity multiplier, range 0.4 to 6.0.

They share a name and nothing else. Rules reach them as
``context.config.get_float("MouseSpeed")`` and ``probes["mouse.win_speed"]``
respectively, and no dictionary anywhere resolves the bare string
``"MouseSpeed"`` to either one.
"""

from __future__ import annotations

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

# osu! clamps MouseSpeed into this range whenever the Options menu is opened,
# so a hand-edited value outside it silently reverts.
_MOUSE_SPEED_MIN = 0.4
_MOUSE_SPEED_MAX = 6.0


def _raw_input_disabled(context: RuleContext) -> Finding | None:
    raw_input = context.config.get_bool("RawInput")
    if raw_input is None or raw_input:
        return None

    return Finding(
        id="cfg.mouse.raw_input_off",
        title="Raw input is off",
        summary="Cursor movement is going through Windows pointer processing.",
        severity=Severity.CRITICAL,
        confidence=Confidence.MEASURED,
        basis=Basis.COMMUNITY_CONSENSUS,
        subsystem=Subsystem.CONFIG,
        category=Category.INPUT,
        rationale=(
            "Without raw input, osu! receives cursor positions after Windows has "
            "applied its own pointer handling, so acceleration and the desktop "
            "sensitivity curve affect aim. With raw input on, osu! reads mouse "
            "counts directly and Windows pointer settings stop mattering."
        ),
        current_value="0",
        recommended_value="1",
        evidence=[Evidence(label="RawInput", value="0", source="osu!.cfg")],
        verification="Options > Input > Raw input. Re-run `forge doctor` after.",
    )


def _mouse_speed_not_unity(context: RuleContext) -> Finding | None:
    speed = context.config.get_float("MouseSpeed")
    if speed is None or speed == 1.0:
        return None

    out_of_range = not (_MOUSE_SPEED_MIN <= speed <= _MOUSE_SPEED_MAX)

    rationale = (
        "osu!'s MouseSpeed multiplies your mouse's raw counts on top of whatever "
        "your DPI already is. The usual advice is to leave it at 1.00 and change "
        "DPI instead, so there is one number controlling sensitivity rather than "
        "two multiplied together — which matters when you later try to reproduce "
        "or compare a sensitivity.\n\n"
        "This is a convention, not a correctness issue. A non-unity value is not "
        "broken."
    )
    if out_of_range:
        rationale += (
            f"\n\nSeparately, {speed} is outside osu!'s accepted range of "
            f"{_MOUSE_SPEED_MIN} to {_MOUSE_SPEED_MAX}. osu! re-clamps this value "
            "whenever the Options menu is opened, so a hand-edited value outside "
            "the range reverts without warning."
        )

    return Finding(
        id="cfg.mouse.speed_not_unity",
        title=f"osu! MouseSpeed is {speed}, not 1.00",
        summary=(
            "Sensitivity is being scaled in-game as well as by your mouse DPI."
            + (" The value is also outside the range osu! accepts." if out_of_range else "")
        ),
        severity=Severity.WARN if out_of_range else Severity.INFO,
        confidence=Confidence.MEASURED,
        basis=Basis.COMMUNITY_CONSENSUS,
        subsystem=Subsystem.CONFIG,
        category=Category.INPUT,
        rationale=rationale,
        current_value=str(speed),
        recommended_value="1" if out_of_range else None,
        evidence=[
            Evidence(
                label="osu! MouseSpeed (cursor sensitivity multiplier)",
                value=str(speed),
                source="osu!.cfg",
            )
        ],
        caveats=[
            "Not to be confused with the Windows registry value of the same "
            "name, which is the Enhance Pointer Precision toggle."
        ],
    )


RULES: tuple[Rule, ...] = (
    Rule(
        id="cfg.mouse.raw_input_off",
        title="Raw input disabled",
        category=Category.INPUT,
        subsystem=Subsystem.CONFIG,
        severity=Severity.CRITICAL,
        basis=Basis.COMMUNITY_CONSENSUS,
        confidence=Confidence.MEASURED,
        evaluate=_raw_input_disabled,
    ),
    Rule(
        id="cfg.mouse.speed_not_unity",
        title="osu! MouseSpeed is not 1.00",
        category=Category.INPUT,
        subsystem=Subsystem.CONFIG,
        severity=Severity.INFO,
        basis=Basis.COMMUNITY_CONSENSUS,
        confidence=Confidence.MEASURED,
        evaluate=_mouse_speed_not_unity,
    ),
)
