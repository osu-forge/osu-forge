"""Display rules.

One thing governs this whole module: osu!'s ``Display`` config index does **not**
reliably correspond to ``\\\\.\\DISPLAYn``. osu! numbers monitors in its own
window-toolkit order, which is not guaranteed to match ``EnumDisplayDevices``.

So the config alone cannot say which screen the game opens on. When osu! is
running we find out exactly, by locating its window; when it is not, the rules
lay out both readings instead of choosing one.

That restraint is not pedantry. Somebody deliberately running 1080p on a 1440p
240 Hz panel — a normal choice, trading resolution for frame-rate headroom —
would be told "you are on your 144 Hz monitor" by a rule that guessed from the
config. Being confidently wrong about something the user can see on their own
desk costs them every other finding in the report.
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
from osuforge.probes.display import DISPLAYS_PROBE_ID, Monitor
from osuforge.probes.window import OSU_WINDOW_PROBE_ID, OsuWindowPlacement
from osuforge.rules.engine import Rule, RuleContext

__all__ = ["RULES"]


def _monitors(context: RuleContext) -> tuple[list[Monitor], str]:
    probe = context.probe(DISPLAYS_PROBE_ID)
    assert probe is not None  # guaranteed by Rule.requires
    value = probe.require()
    if not isinstance(value, list) or not all(isinstance(m, Monitor) for m in value):
        raise TypeError(f"{DISPLAYS_PROBE_ID} did not return a list of Monitor")
    return list(value), probe.source


def _placement(context: RuleContext) -> tuple[OsuWindowPlacement, str] | None:
    """Where osu!'s window actually is — only knowable while it runs."""
    probe = context.probe(OSU_WINDOW_PROBE_ID)
    if probe is None or not probe.ok:
        return None
    value = probe.value
    return (value, probe.source) if isinstance(value, OsuWindowPlacement) else None


def _monitor_evidence(monitors: list[Monitor], source: str) -> list[Evidence]:
    return [Evidence(label=m.device_name, value=str(m), source=source) for m in monitors]


# ------------------------------------------------------------ refresh rate


def _refresh_rate(context: RuleContext) -> Finding | None:
    configured = context.config.get_int("RefreshRate")
    if configured is None:
        return None

    monitors, source = _monitors(context)
    measured = {m.refresh_hz for m in monitors}

    # Two Hz of slack, because reported rates are rounded: a 143.98 Hz panel
    # reads as 144 through this API and 143 through WMI, which is one reason WMI
    # is not used here.
    if any(abs(configured - hz) <= 2 for hz in measured):
        return None

    rates = ", ".join(f"{hz} Hz" for hz in sorted(measured))
    common = [
        *_monitor_evidence(monitors, source),
        Evidence(label="RefreshRate", value=str(configured), source="osu!.cfg"),
    ]

    if context.config.get_bool("OverrideRefreshRate"):
        return Finding(
            id="sys.display.refreshrate_override_wrong",
            title=f"osu! is forcing {configured} Hz, which no monitor is running at",
            summary=(
                f"OverrideRefreshRate is on and RefreshRate is {configured}, but your "
                f"monitors run at {rates}."
            ),
            severity=Severity.WARN,
            confidence=Confidence.MEASURED,
            basis=Basis.HARD_FACT,
            subsystem=Subsystem.SYSTEM,
            category=Category.DISPLAY,
            rationale=(
                "With the override on, osu! acts on this value instead of asking "
                "the display, so a wrong number here changes real behaviour."
            ),
            current_value=str(configured),
            recommended_value=str(max(measured)),
            evidence=[
                *common,
                Evidence(label="OverrideRefreshRate", value="1", source="osu!.cfg"),
            ],
        )

    return Finding(
        id="sys.display.refreshrate_stale",
        title=f"RefreshRate says {configured}, which is out of date",
        summary=(
            f"The config records {configured} Hz while your monitors run at {rates}. "
            "OverrideRefreshRate is off, so this value is not being used."
        ),
        severity=Severity.INFO,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.SYSTEM,
        category=Category.DISPLAY,
        rationale=(
            "A stale cached value, not an active setting: osu! only acts on "
            "RefreshRate when OverrideRefreshRate is 1, and yours is 0. Worth "
            "recognising as a symptom — it usually means osu! detected the wrong "
            "rate at some point — but changing it would do nothing, so nothing "
            "is recommended."
        ),
        current_value=str(configured),
        evidence=[*common, Evidence(label="OverrideRefreshRate", value="0", source="osu!.cfg")],
        caveats=["Inert while OverrideRefreshRate is 0. Editing it changes nothing."],
    )


# ------------------------------------------------------ fullscreen resolution


def _scaling_note(lower: bool) -> str:
    tradeoff = (
        "Running below native trades sharpness for frame-rate headroom, which is "
        "a normal thing to do on purpose — especially on a high-refresh panel."
        if lower
        else "This is above the panel's native resolution, which is unusual."
    )
    return (
        f"{tradeoff}\n\n"
        "The cost is that the display does the scaling. Most panels handle it "
        "well; some add a frame of latency, and your GPU can do it instead if you "
        "prefer. Worth knowing, not worth changing on our say-so — which is why "
        "nothing is recommended."
    )


def _fullscreen_known_monitor(
    placement: OsuWindowPlacement, width: int, height: int, source: str
) -> Finding | None:
    monitor = placement.monitor
    if width == monitor.width and height == monitor.height:
        return None

    return Finding(
        id="sys.display.fullscreen_not_native",
        title=f"osu! runs {width}x{height} on a {monitor.width}x{monitor.height} panel",
        summary=(
            f"osu! is on {monitor.device_name} ({monitor} native), so the panel is "
            f"scaling {width}x{height} to fit."
        ),
        severity=Severity.INFO,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.SYSTEM,
        category=Category.DISPLAY,
        rationale=_scaling_note(width * height < monitor.width * monitor.height),
        current_value=f"{width}x{height} on {monitor.device_name} ({monitor})",
        evidence=[
            Evidence(
                label="osu! window monitor",
                value=f"{monitor.device_name} — {monitor}",
                source=source,
            ),
            Evidence(
                label="WidthFullscreen x HeightFullscreen",
                value=f"{width}x{height}",
                source="osu!.cfg",
            ),
        ],
    )


def _fullscreen_unknown_monitor(
    context: RuleContext, monitors: list[Monitor], width: int, height: int, source: str
) -> Finding | None:
    native_on = [m for m in monitors if m.width == width and m.height == height]
    cfg_evidence = Evidence(
        label="WidthFullscreen x HeightFullscreen",
        value=f"{width}x{height}",
        source="osu!.cfg",
    )

    if not native_on:
        # True whichever monitor osu! opens on, so it can be stated flatly.
        return Finding(
            id="sys.display.fullscreen_not_native",
            title=f"Fullscreen {width}x{height} is not native to any monitor",
            summary=(
                f"osu! runs fullscreen at {width}x{height}, which matches none of your "
                "monitors, so whichever it opens on is scaling."
            ),
            severity=Severity.INFO,
            confidence=Confidence.MEASURED,
            basis=Basis.HARD_FACT,
            subsystem=Subsystem.SYSTEM,
            category=Category.DISPLAY,
            rationale=_scaling_note(lower=True),
            current_value=f"{width}x{height}",
            evidence=[*_monitor_evidence(monitors, source), cfg_evidence],
        )

    if len(monitors) < 2:
        return None

    matched = max(native_on, key=lambda m: m.refresh_hz)
    fastest = max(monitors, key=lambda m: m.refresh_hz)
    if matched.refresh_hz >= fastest.refresh_hz:
        return None

    # Native on one monitor, and a faster one is also attached. Both readings are
    # live and the config cannot separate them, so both are stated rather than
    # one being asserted.
    others = ", ".join(f"{m.device_name} ({m})" for m in monitors if m not in native_on)
    return Finding(
        id="sys.display.fullscreen_monitor_ambiguous",
        title=f"Cannot tell which monitor osu! runs {width}x{height} on",
        summary=(
            f"{width}x{height} is native to {matched.device_name} ({matched}). "
            f"If osu! is on {others} instead, it is running a scaled mode there."
        ),
        severity=Severity.INFO,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.SYSTEM,
        category=Category.DISPLAY,
        rationale=(
            "Both readings are plausible and neither is a problem:\n\n"
            f"  - On {matched.device_name}: a native {width}x{height} at "
            f"{matched.refresh_hz} Hz.\n"
            f"  - On {fastest.device_name}: {fastest.refresh_hz} Hz with the panel "
            "scaling, which is a common way to trade resolution for headroom.\n\n"
            "osu!'s Display index uses its own monitor ordering and does not map "
            "reliably onto the Windows one, so the config cannot settle this. "
            "Nothing is recommended, because a recommendation would have to assume "
            "an answer we do not have."
        ),
        current_value=f"{width}x{height}",
        evidence=[
            *_monitor_evidence(monitors, source),
            cfg_evidence,
            Evidence(
                label="Display (osu! monitor index)",
                value=context.config.get_str("Display") or "unset",
                source="osu!.cfg",
            ),
        ],
        verification=(
            "Start osu! and re-run `forge doctor`. With the game running its window "
            "is located directly and this becomes one answer instead of two."
        ),
    )


def _fullscreen_resolution(context: RuleContext) -> Finding | None:
    if not context.config.get_bool("Fullscreen"):
        return None

    width = context.config.get_int("WidthFullscreen")
    height = context.config.get_int("HeightFullscreen")
    if width is None or height is None:
        return None

    monitors, source = _monitors(context)

    known = _placement(context)
    if known is not None:
        placement, window_source = known
        return _fullscreen_known_monitor(placement, width, height, window_source)

    return _fullscreen_unknown_monitor(context, monitors, width, height, source)


RULES: tuple[Rule, ...] = (
    Rule(
        id="sys.display.refreshrate",
        title="Configured refresh rate versus the measured one",
        category=Category.DISPLAY,
        subsystem=Subsystem.SYSTEM,
        severity=Severity.INFO,
        basis=Basis.HARD_FACT,
        confidence=Confidence.MEASURED,
        evaluate=_refresh_rate,
        requires=(DISPLAYS_PROBE_ID,),
    ),
    Rule(
        id="sys.display.fullscreen_resolution",
        title="Fullscreen resolution versus the monitor osu! uses",
        category=Category.DISPLAY,
        subsystem=Subsystem.SYSTEM,
        severity=Severity.INFO,
        basis=Basis.HARD_FACT,
        confidence=Confidence.MEASURED,
        evaluate=_fullscreen_resolution,
        # Deliberately does NOT require the window probe. That one only works
        # while osu! runs, and this rule has a real answer either way — requiring
        # it would turn a useful finding into a permanent "could not check".
        requires=(DISPLAYS_PROBE_ID,),
    ),
)
