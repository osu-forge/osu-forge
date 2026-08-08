"""Frame limiter rules.

Everything here reads only the config. The rules that need the *real* monitor
refresh rate live with the display probes, because comparing a config value to
a number nobody measured is how you end up reporting a symptom as a cause.
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

WIKI_PERFORMANCE = "https://osu.ppy.sh/wiki/en/Performance_troubleshooting"


def _custom_frame_limit_inert(context: RuleContext) -> Finding | None:
    """``CustomFrameLimit`` only applies when ``FrameSync = Custom``.

    Worth a finding on its own because the failure is invisible: the value sits
    in the config looking authoritative, the user believes they are capped where
    they set it, and nothing anywhere says otherwise.
    """
    frame_sync = context.config.get_str("FrameSync")
    limit = context.config.get_int("CustomFrameLimit")

    if frame_sync is None or limit is None:
        return None
    if frame_sync == "Custom":
        return None

    return Finding(
        id="cfg.frame.custom_limit_inert",
        title=f"CustomFrameLimit = {limit} is doing nothing",
        summary=(
            f"CustomFrameLimit is set to {limit}, but it only applies when "
            f"FrameSync = Custom. Yours is {frame_sync}, so the limit is ignored."
        ),
        severity=Severity.WARN,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.CONFIG,
        category=Category.FRAME,
        rationale=(
            f"osu! ignores CustomFrameLimit unless FrameSync is Custom. Under "
            f"{frame_sync} your actual cap is whatever that mode implies — "
            "Optimal targets 8x your refresh rate (capped at 960 FPS), Power "
            "Saving targets 2x, and VSync follows the display.\n\n"
            "There is no single right fix here, which is why no value is "
            "recommended: set FrameSync = Custom if you actually want the limit "
            "you configured, or leave FrameSync alone and stop treating "
            "CustomFrameLimit as if it were in effect."
        ),
        # No recommended_value on purpose. Both resolutions are legitimate and
        # which one is right depends on what the user intended, so proposing one
        # would be stating a preference as a fix.
        current_value=f"FrameSync = {frame_sync}, CustomFrameLimit = {limit}",
        evidence=[
            Evidence(label="FrameSync", value=frame_sync, source="osu!.cfg"),
            Evidence(label="CustomFrameLimit", value=str(limit), source="osu!.cfg"),
        ],
        source_url=WIKI_PERFORMANCE,
        verification=(
            "Enable the in-game FPS counter (Options > Graphics) and compare it "
            "to the number you expected."
        ),
    )


def _vsync_latency(context: RuleContext) -> Finding | None:
    frame_sync = context.config.get_str("FrameSync")
    if frame_sync != "VSync":
        return None

    return Finding(
        id="cfg.frame.vsync_latency",
        title="FrameSync is VSync",
        summary="VSync adds roughly one to two frames of input latency.",
        severity=Severity.INFO,
        confidence=Confidence.MEASURED,
        basis=Basis.COMMUNITY_CONSENSUS,
        subsystem=Subsystem.CONFIG,
        category=Category.FRAME,
        rationale=(
            "The osu! wiki describes VSync as adding one to two frames of "
            "latency. Whether that trade is worth it is genuinely a preference: "
            "it removes tearing, and some players prefer it. Listed so the cost "
            "is visible, not because it is wrong."
        ),
        current_value="VSync",
        evidence=[Evidence(label="FrameSync", value="VSync", source="osu!.cfg")],
        source_url=WIKI_PERFORMANCE,
    )


RULES: tuple[Rule, ...] = (
    Rule(
        id="cfg.frame.custom_limit_inert",
        title="CustomFrameLimit ignored under the current FrameSync",
        category=Category.FRAME,
        subsystem=Subsystem.CONFIG,
        severity=Severity.WARN,
        basis=Basis.HARD_FACT,
        confidence=Confidence.MEASURED,
        evaluate=_custom_frame_limit_inert,
        source_url=WIKI_PERFORMANCE,
    ),
    Rule(
        id="cfg.frame.vsync_latency",
        title="VSync input latency",
        category=Category.FRAME,
        subsystem=Subsystem.CONFIG,
        severity=Severity.INFO,
        basis=Basis.COMMUNITY_CONSENSUS,
        confidence=Confidence.MEASURED,
        evaluate=_vsync_latency,
        source_url=WIKI_PERFORMANCE,
    ),
)
