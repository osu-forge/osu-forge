"""Windows-level rules: pointer settings, accessibility hotkeys, power, timer."""

from __future__ import annotations

from osuforge.config.keybinds import read_bindings
from osuforge.models import (
    Basis,
    Category,
    Confidence,
    Evidence,
    Finding,
    Severity,
    Subsystem,
)
from osuforge.probes.a11y import A11Y_PROBE_ID, AccessibilityShortcuts
from osuforge.probes.mouse import MOUSE_PROBE_ID, SENSITIVITY_ONE_TO_ONE, WindowsMouseSettings
from osuforge.probes.process import OSU_PROCESS_PROBE_ID, OsuProcess
from osuforge.probes.system import (
    FSO_DISABLED_FLAG,
    FSO_PROBE_ID,
    GAMEBAR_PROBE_ID,
    POWER_PROBE_ID,
    TIMER_PROBE_ID,
    FullscreenOptimizations,
    GameBarSettings,
    PowerScheme,
    TimerResolution,
)
from osuforge.rules.engine import Rule, RuleContext

__all__ = ["RULES"]

_SHIFT_KEYS = frozenset({"LeftShift", "RightShift", "Shift", "ShiftKey"})


def _value[T](kind: type[T], context: RuleContext, probe_id: str) -> tuple[T, str]:
    """The probe's value and its source.

    The type is passed rather than subscripted because generic *functions* are
    not subscriptable at runtime — `_value[X](...)` type-checks fine and raises
    TypeError when it runs. The isinstance check earns its keep too: a probe
    returning the wrong shape fails here rather than deeper in a rule.
    """
    probe = context.probe(probe_id)
    assert probe is not None  # guaranteed by Rule.requires
    value = probe.require()
    if not isinstance(value, kind):
        raise TypeError(
            f"probe {probe_id} returned {type(value).__name__}, expected {kind.__name__}"
        )
    return value, probe.source


# ------------------------------------------------------------------ mouse


def _enhance_pointer_precision(context: RuleContext) -> Finding | None:
    settings, source = _value(WindowsMouseSettings, context, MOUSE_PROBE_ID)
    if not settings.acceleration_on:
        return None

    return Finding(
        id="sys.mouse.enhance_pointer_precision",
        title="Enhance Pointer Precision is on",
        summary="Windows is applying acceleration to your mouse movement.",
        severity=Severity.CRITICAL,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.SYSTEM,
        category=Category.INPUT,
        rationale=(
            "Enhance Pointer Precision makes cursor distance depend on how fast "
            "you move, so the same physical motion lands somewhere different "
            "depending on speed. That is directly at odds with building muscle "
            "memory for a fixed distance.\n\n"
            "Note this only reaches osu! when raw input is off — with raw input "
            "on, osu! bypasses Windows pointer processing entirely. It still "
            "affects every other application."
        ),
        current_value=(
            f"MouseSpeed={settings.enhance_pointer_precision}, "
            f"thresholds={settings.threshold1}/{settings.threshold2}"
        ),
        recommended_value="off (all three values 0)",
        evidence=[
            Evidence(
                label="Enhance Pointer Precision (registry MouseSpeed)",
                value=str(settings.enhance_pointer_precision),
                source=source,
            ),
            Evidence(label="MouseThreshold1", value=str(settings.threshold1), source=source),
            Evidence(label="MouseThreshold2", value=str(settings.threshold2), source=source),
        ],
        caveats=[
            "The registry MouseSpeed is the acceleration toggle. It is unrelated "
            "to osu!'s MouseSpeed, which is a sensitivity multiplier."
        ],
        verification="Settings > Mouse > Additional settings > Pointer Options.",
    )


def _sensitivity_notch(context: RuleContext) -> Finding | None:
    settings, source = _value(WindowsMouseSettings, context, MOUSE_PROBE_ID)
    if settings.one_to_one:
        return None

    return Finding(
        id="sys.mouse.sensitivity_not_one_to_one",
        title=f"Windows pointer speed is {settings.sensitivity}, not the 1:1 notch",
        summary="Windows is scaling your mouse counts before anything else sees them.",
        severity=Severity.WARN,
        confidence=Confidence.MEASURED,
        basis=Basis.COMMUNITY_CONSENSUS,
        subsystem=Subsystem.SYSTEM,
        category=Category.INPUT,
        rationale=(
            f"The slider's middle position — the sixth of eleven, value "
            f"{SENSITIVITY_ONE_TO_ONE} — moves the cursor one pixel per mouse "
            "count. Every other notch multiplies or divides, and dividing throws "
            "counts away outright, which shows up as the cursor skipping pixels.\n\n"
            "As with acceleration, this reaches osu! only when raw input is off, "
            "but it affects everything else you do."
        ),
        current_value=str(settings.sensitivity),
        recommended_value=str(SENSITIVITY_ONE_TO_ONE),
        evidence=[
            Evidence(label="MouseSensitivity", value=str(settings.sensitivity), source=source)
        ],
        verification="Settings > Mouse > Pointer speed — the middle position.",
    )


# ----------------------------------------------------------- accessibility


def _smoke_key_triggers_sticky_keys(context: RuleContext) -> Finding | None:
    shortcuts, source = _value(AccessibilityShortcuts, context, A11Y_PROBE_ID)
    if not shortcuts.sticky_keys.hotkey_armed:
        return None

    shift_bound = [b for b in read_bindings(context.config) if b.bound and b.value in _SHIFT_KEYS]
    if not shift_bound:
        return None

    names = ", ".join(b.key for b in shift_bound)
    verb = "uses" if len(shift_bound) == 1 else "use"
    return Finding(
        id="sys.a11y.shift_binding_triggers_sticky_keys",
        title="A gameplay key is bound to Shift while the Sticky Keys shortcut is armed",
        summary=(
            f"{names} {verb} Shift, and pressing Shift five times still opens the "
            "Sticky Keys prompt."
        ),
        severity=Severity.WARN,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.SYSTEM,
        category=Category.INPUT,
        rationale=(
            "Sticky Keys itself is off, which is why this is easy to miss — the "
            "*shortcut* that turns it on is separate, and it is armed by default. "
            "Five Shift presses is well within what a stream or a burst of smoke "
            "produces, and the prompt steals focus mid-play."
        ),
        current_value="Sticky Keys shortcut: enabled",
        recommended_value="Sticky Keys shortcut: disabled",
        evidence=[
            Evidence(
                label="Sticky Keys flags",
                value=f"0x{shortcuts.sticky_keys.flags:X} (hotkey armed, feature off)",
                source=source,
            ),
            *(Evidence(label=b.key, value=b.value, source="osu!.cfg") for b in shift_bound),
        ],
        verification=(
            "Settings > Accessibility > Keyboard > Sticky keys > untick the keyboard shortcut."
        ),
    )


def _other_accessibility_hotkeys(context: RuleContext) -> list[Finding]:
    shortcuts, source = _value(AccessibilityShortcuts, context, A11Y_PROBE_ID)
    findings: list[Finding] = []

    for shortcut, rule_id in (
        (shortcuts.filter_keys, "sys.a11y.filterkeys_hotkey"),
        (shortcuts.toggle_keys, "sys.a11y.togglekeys_hotkey"),
    ):
        if not shortcut.hotkey_armed:
            continue
        findings.append(
            Finding(
                id=rule_id,
                title=f"The {shortcut.name} shortcut is armed",
                summary=f"{shortcut.trigger.capitalize()} opens the {shortcut.name} prompt.",
                severity=Severity.INFO,
                confidence=Confidence.MEASURED,
                basis=Basis.HARD_FACT,
                subsystem=Subsystem.SYSTEM,
                category=Category.INPUT,
                rationale=(
                    f"{shortcut.name} is off; only its shortcut is enabled, which "
                    "is the Windows default. Less likely to fire during play than "
                    "the Sticky Keys one, so this is informational — but it costs "
                    "nothing to turn off and it cannot then interrupt you."
                ),
                current_value=f"{shortcut.name} shortcut: enabled",
                recommended_value=f"{shortcut.name} shortcut: disabled",
                evidence=[
                    Evidence(
                        label=f"{shortcut.name} flags",
                        value=f"0x{shortcut.flags:X}",
                        source=source,
                    )
                ],
                verification="Settings > Accessibility > Keyboard.",
            )
        )

    return findings


# ----------------------------------------------------------------- system


def _power_scheme(context: RuleContext) -> Finding | None:
    scheme, source = _value(PowerScheme, context, POWER_PROBE_ID)
    if not scheme.may_throttle:
        return None

    return Finding(
        id="sys.power.throttling_scheme",
        title=f"Power plan is {scheme.name}",
        summary="This plan parks cores and scales clocks down, which adds frame-time variance.",
        severity=Severity.WARN,
        confidence=Confidence.MEASURED,
        basis=Basis.COMMUNITY_CONSENSUS,
        subsystem=Subsystem.SYSTEM,
        category=Category.WINDOWS,
        rationale=(
            "Clock scaling shows up as inconsistent frame times rather than a "
            "lower average, which is the kind of thing that feels wrong without "
            "being visible in an FPS number. On a desktop the trade is usually "
            "worth it; on a laptop on battery it is a real cost."
        ),
        current_value=scheme.name,
        recommended_value="High performance",
        evidence=[Evidence(label="Active power scheme", value=scheme.name, source=source)],
    )


def _timer_resolution(context: RuleContext) -> Finding | None:
    timer, source = _value(TimerResolution, context, TIMER_PROBE_ID)
    if timer.current_ms <= 1.0:
        return None

    return Finding(
        id="sys.timer.resolution_coarse",
        title=f"System timer resolution is {timer.current_ms:.3f} ms",
        summary="Anything relying on short sleeps is quantised to that interval.",
        severity=Severity.INFO,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.SYSTEM,
        category=Category.WINDOWS,
        rationale=(
            f"Windows defaults to about 15.6 ms and drops to {timer.finest_ms:.3f} ms "
            "when an application asks. osu! normally requests a finer resolution "
            "while running, so a coarse value measured here does not necessarily "
            "mean osu! runs at it — this is a snapshot of the system right now."
        ),
        current_value=f"{timer.current_ms:.3f} ms",
        evidence=[
            Evidence(label="current", value=f"{timer.current_ms:.3f} ms", source=source),
            Evidence(label="finest available", value=f"{timer.finest_ms:.3f} ms", source=source),
        ],
        caveats=["Measured outside osu!. Applications raise this while they run."],
    )


def _game_mode(context: RuleContext) -> Finding | None:
    settings, source = _value(GameBarSettings, context, GAMEBAR_PROBE_ID)
    if not settings.game_mode:
        return None

    return Finding(
        id="sys.win.game_mode_on",
        title="Windows Game Mode is on",
        summary="Whether Game Mode helps osu! is genuinely disputed.",
        severity=Severity.INFO,
        confidence=Confidence.MEASURED,
        basis=Basis.PREFERENCE,
        subsystem=Subsystem.SYSTEM,
        category=Category.WINDOWS,
        rationale=(
            "Game Mode deprioritises background work while a game is focused. "
            "Reports of it helping and of it causing stutter are both common, and "
            "we have no measurement either way.\n\n"
            "Listed as a thing to A/B test yourself, not as a fix. It is here for "
            "completeness — turning it off because a tool mentioned it is not a "
            "better reason than leaving it on."
        ),
        current_value="on",
        evidence=[Evidence(label="AutoGameModeEnabled", value="1", source=source)],
    )


def _fullscreen_optimizations(context: RuleContext) -> Finding | None:
    fso, source = _value(FullscreenOptimizations, context, FSO_PROBE_ID)
    if fso.disabled:
        return None

    raw = fso.raw_flags
    return Finding(
        id="sys.win.fso_not_disabled",
        title="Fullscreen Optimizations is not disabled for osu!.exe",
        summary=(
            "Windows may run osu!'s fullscreen as a borderless window with a "
            "compositor in the path."
        ),
        severity=Severity.WARN,
        confidence=Confidence.MEASURED,
        basis=Basis.COMMUNITY_CONSENSUS,
        subsystem=Subsystem.SYSTEM,
        category=Category.WINDOWS,
        rationale=(
            "Fullscreen Optimizations replaces exclusive fullscreen with a "
            "flip-model borderless window. It is usually close to transparent, "
            "but it does put the desktop compositor in the path, and disabling it "
            "is a common step when chasing input latency.\n\n"
            "Community consensus rather than a measurement — we have not measured "
            "the difference on your machine."
        ),
        current_value=(f"compatibility flags: {raw}" if raw else "no compatibility flags set"),
        # Appended, not substituted. Replacing the value would drop whatever
        # shim osu! put there, and the checkbox in the Properties dialog adds
        # the flag rather than overwriting the entry.
        recommended_value=(
            f"compatibility flags: {raw} {FSO_DISABLED_FLAG}"
            if raw
            else f"compatibility flags: {FSO_DISABLED_FLAG}"
        ),
        evidence=[
            Evidence(
                label="AppCompatFlags\\Layers entry",
                value=raw or "(absent)",
                source=source,
            )
        ],
        caveats=(
            [
                f"An existing entry {raw!r} is reported verbatim. osu! installs "
                "carry an IgnoreFreeLibrary shim whose purpose we could not find "
                "documented, so it is shown rather than interpreted — it is "
                "certainly not the fullscreen-optimizations flag."
            ]
            if raw
            else []
        ),
        verification=(
            "Right-click osu!.exe > Properties > Compatibility > Disable fullscreen optimizations."
        ),
    )


def _osu_running(context: RuleContext) -> Finding | None:
    process, source = _value(OsuProcess, context, OSU_PROCESS_PROBE_ID)
    if not process.running:
        return None

    return Finding(
        id="sys.process.osu_running",
        title="osu! is running right now",
        summary="The config on disk may not reflect what osu! currently has in memory.",
        severity=Severity.INFO,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.SYSTEM,
        category=Category.DATA,
        rationale=(
            "osu! rewrites its config when it exits. Anything you change in-game "
            "after this scan is not in the findings above, and anything you "
            "change by editing the file now will be overwritten when osu! closes.\n\n"
            "Nothing is wrong — this is a note about how fresh these results are."
        ),
        current_value=f"pid {process.pid}",
        evidence=[Evidence(label="process", value=process.exe or "osu!.exe", source=source)],
        caveats=["osu-forge only checks whether the process exists. It never reads its memory."],
    )


def _rule(
    rule_id: str,
    title: str,
    category: Category,
    severity: Severity,
    basis: Basis,
    evaluate: object,
    requires: tuple[str, ...],
) -> Rule:
    return Rule(
        id=rule_id,
        title=title,
        category=category,
        subsystem=Subsystem.SYSTEM,
        severity=severity,
        basis=basis,
        confidence=Confidence.MEASURED,
        evaluate=evaluate,  # type: ignore[arg-type]
        requires=requires,
    )


RULES: tuple[Rule, ...] = (
    _rule(
        "sys.mouse.enhance_pointer_precision",
        "Windows pointer acceleration",
        Category.INPUT,
        Severity.CRITICAL,
        Basis.HARD_FACT,
        _enhance_pointer_precision,
        (MOUSE_PROBE_ID,),
    ),
    _rule(
        "sys.mouse.sensitivity_not_one_to_one",
        "Windows pointer speed notch",
        Category.INPUT,
        Severity.WARN,
        Basis.COMMUNITY_CONSENSUS,
        _sensitivity_notch,
        (MOUSE_PROBE_ID,),
    ),
    _rule(
        "sys.a11y.shift_binding_triggers_sticky_keys",
        "Shift binding versus the Sticky Keys shortcut",
        Category.INPUT,
        Severity.WARN,
        Basis.HARD_FACT,
        _smoke_key_triggers_sticky_keys,
        (A11Y_PROBE_ID,),
    ),
    _rule(
        "sys.a11y.other_hotkeys",
        "Other accessibility shortcuts",
        Category.INPUT,
        Severity.INFO,
        Basis.HARD_FACT,
        _other_accessibility_hotkeys,
        (A11Y_PROBE_ID,),
    ),
    _rule(
        "sys.power.throttling_scheme",
        "Power plan",
        Category.WINDOWS,
        Severity.WARN,
        Basis.COMMUNITY_CONSENSUS,
        _power_scheme,
        (POWER_PROBE_ID,),
    ),
    _rule(
        "sys.timer.resolution_coarse",
        "System timer resolution",
        Category.WINDOWS,
        Severity.INFO,
        Basis.HARD_FACT,
        _timer_resolution,
        (TIMER_PROBE_ID,),
    ),
    _rule(
        "sys.win.game_mode_on",
        "Windows Game Mode",
        Category.WINDOWS,
        Severity.INFO,
        Basis.PREFERENCE,
        _game_mode,
        (GAMEBAR_PROBE_ID,),
    ),
    _rule(
        "sys.win.fso_not_disabled",
        "Fullscreen Optimizations for osu!.exe",
        Category.WINDOWS,
        Severity.WARN,
        Basis.COMMUNITY_CONSENSUS,
        _fullscreen_optimizations,
        (FSO_PROBE_ID,),
    ),
    _rule(
        "sys.process.osu_running",
        "osu! process state",
        Category.DATA,
        Severity.INFO,
        Basis.HARD_FACT,
        _osu_running,
        (OSU_PROCESS_PROBE_ID,),
    ),
)
