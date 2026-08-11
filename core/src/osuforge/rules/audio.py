"""Audio rules.

The audio path is where the useful answer and the honest one are furthest
apart. A player whose timing feels wrong wants to be told what their latency is,
and no read-only tool can tell them: the buffering that decides it happens
inside osu!, in BASS, and reading another process's memory is something this
project does not do.

So these three rules report what the config says and what the endpoint says
about itself, and stop there. None of them recommends a change. Two describe a
measurement, and the one that repeats the community's account of what
compatibility mode does labels it as an account rather than an observation.
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
from osuforge.probes.audio import AUDIO_PROBE_ID, AudioEndpoint
from osuforge.rules.engine import Rule, RuleContext

__all__ = ["CONFIG_RULES", "PROBE_RULES"]

_CFG = "osu!.cfg"


def _endpoint(context: RuleContext) -> tuple[AudioEndpoint, str]:
    probe = context.probe(AUDIO_PROBE_ID)
    assert probe is not None  # guaranteed by Rule.requires
    value = probe.require()
    if not isinstance(value, AudioEndpoint):
        raise TypeError(f"{AUDIO_PROBE_ID} returned {type(value).__name__}, expected AudioEndpoint")
    return value, probe.source


def _endpoint_evidence(endpoint: AudioEndpoint, source: str) -> list[Evidence]:
    return [
        Evidence(
            label="Default playback endpoint",
            value=endpoint.friendly_name or endpoint.device_id,
            source=source,
        ),
        Evidence(
            label="Transport",
            value=endpoint.transport or "not reported",
            source=source,
        ),
        Evidence(label="Shared mix format", value=endpoint.mix_format, source=source),
    ]


# ------------------------------------------------------------------- config


def _compatibility_mode(context: RuleContext) -> Finding | None:
    if not context.config.get_bool("AudioCompatibility"):
        return None

    return Finding(
        id="cfg.audio.compatibility_mode",
        title="Audio compatibility mode is on",
        summary=(
            "Any offset you tune with this on belongs to this audio path, and "
            "turning it off splits your replay history there."
        ),
        severity=Severity.INFO,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.CONFIG,
        category=Category.AUDIO,
        rationale=(
            "AudioCompatibility is one of the settings osu-forge fingerprints your "
            "replays by, so turning it off would split your history in two: every "
            "hit error recorded before the change describes a different audio path "
            "from the ones after it, and the offset you have tuned to would have to "
            "be tuned again.\n\n"
            "What the setting does is not something osu-forge measured. Players "
            "understand it as selecting osu!'s legacy output path in place of the "
            "modern one, at a cost in latency and a gain in robustness against "
            "crackling and device-specific breakage. That is the community's "
            "account of it, offered as context rather than as a reading — nothing "
            "outside the game can see which path osu! took."
        ),
        # No recommendation. Turning this off is a change to the thing every
        # offset measurement is relative to, and whether it is worth the retune
        # depends on why it was turned on.
        current_value="1",
        evidence=[Evidence(label="AudioCompatibility", value="1", source=_CFG)],
        caveats=[
            "osu-forge never reads osu!'s memory, so which output path the game "
            "actually opened is not observable from here."
        ],
        verification="Options > Audio > Compatibility mode.",
    )


# -------------------------------------------------------------------- probe


def _comparable(name: str) -> str:
    """Letters and digits only, casefolded.

    Punctuation and spacing differ between how osu! stores a device name and how
    Windows synthesizes one, and neither difference means a different device.
    """
    return "".join(character for character in name if character.isalnum()).casefold()


def _device_mismatch(context: RuleContext) -> Finding | None:
    configured = (context.config.get_str("AudioDevice") or "").strip()
    if not configured:
        return None

    endpoint, source = _endpoint(context)
    if not endpoint.friendly_name:
        return None

    wanted = _comparable(configured)
    actual = _comparable(endpoint.friendly_name)
    if wanted in actual or actual in wanted:
        return None

    return Finding(
        id="sys.audio.device_missing",
        title="The audio device in your config is not the default endpoint",
        summary=(
            f"osu!.cfg names {configured!r}. Windows is currently defaulting to "
            f"{endpoint.friendly_name!r}."
        ),
        severity=Severity.INFO,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.SYSTEM,
        category=Category.AUDIO,
        rationale=(
            "Both names are shown rather than judged. osu-forge reads the default "
            "endpoint, not the full device list, so this does not say the "
            "configured device is gone — deliberately playing on a device that is "
            "not the system default is an ordinary thing to do. The comparison is "
            "text against text, and the endpoint name is synthesized by Windows, "
            "is localized, and is not the name osu! enumerates devices under.\n\n"
            "It is worth showing because AudioDevice is part of the fingerprint "
            "your replays are split by. If the name here is a surprise, the "
            "replays either side of the change were played through different "
            "hardware and their hit errors are not comparable."
        ),
        # No recommendation: whether the config or the default endpoint is the
        # one that should change is a question about what the player intended.
        current_value=f"AudioDevice = {configured}",
        evidence=[
            Evidence(label="AudioDevice", value=configured, source=_CFG),
            *_endpoint_evidence(endpoint, source),
        ],
        caveats=[
            "Compared as text. Two names for one device read as a mismatch here, "
            "which is why this is stated and not acted on."
        ],
        verification="Options > Audio > Output device.",
    )


def _no_low_latency_shared(context: RuleContext) -> Finding | None:
    endpoint, source = _endpoint(context)
    period = endpoint.engine_period
    if period is None or period.offers_lower_than_default:
        return None

    # Quoted from the default period rather than the current one: the default
    # is the number the finding is about, and the two can differ on a driver
    # that offers a range above it.
    default_ms = endpoint.frames_to_ms(period.default_frames)
    floor = f"{default_ms:.3f} ms" if default_ms is not None else f"{period.default_frames} frames"

    return Finding(
        id="sys.audio.no_low_latency_shared",
        title=f"Your audio driver offers no shared-mode period below {floor}",
        summary=(
            "Its shortest shared engine period is its default one, so there is "
            "nothing faster for an application to ask for."
        ),
        severity=Severity.INFO,
        confidence=Confidence.MEASURED,
        basis=Basis.HARD_FACT,
        subsystem=Subsystem.SYSTEM,
        category=Category.AUDIO,
        rationale=(
            f"Windows mixes for {endpoint.friendly_name or 'this endpoint'} at "
            f"{endpoint.mix_sample_rate_hz} Hz and hands the driver a buffer every "
            f"{endpoint.default_period_ms:.3f} ms. Asked what shared-mode periods it "
            f"supports, the driver reports a minimum of {period.minimum_frames} frames "
            f"against a default of {period.default_frames}, so the low-latency shared "
            "mode Windows 10 added is not on offer here.\n\n"
            f"The {endpoint.minimum_period_ms:.3f} ms minimum period reported "
            "alongside it is the exclusive-mode floor, which is a different thing: "
            "reaching it means taking the device away from every other application, "
            "osu! included.\n\n"
            "There is nothing to change. This is what the endpoint offers, recorded "
            "so a number you may see quoted elsewhere can be checked against your "
            "own hardware."
        ),
        current_value=f"{period.default_frames} frames at {endpoint.mix_sample_rate_hz} Hz",
        evidence=[
            *_endpoint_evidence(endpoint, source),
            Evidence(
                label="Device period (default / minimum)",
                value=f"{endpoint.default_period_ms:.3f} ms / {endpoint.minimum_period_ms:.3f} ms",
                source=source,
            ),
            Evidence(
                label="Shared engine period (default / min / max)",
                value=(
                    f"{period.default_frames} / {period.minimum_frames} / "
                    f"{period.maximum_frames} frames"
                ),
                source=source,
            ),
        ],
        caveats=[
            "One term of the audio path, not your audio latency. osu!'s own "
            "buffering happens in BASS inside the game, and the driver, the DAC "
            "and any effects chain each add their own — none of which is readable "
            "from outside the process.",
            "Read without opening a stream, so this is what the device offers "
            "rather than what osu! asked it for.",
        ],
    )


CONFIG_RULES: tuple[Rule, ...] = (
    Rule(
        id="cfg.audio.compatibility_mode",
        title="Audio compatibility mode",
        category=Category.AUDIO,
        subsystem=Subsystem.CONFIG,
        severity=Severity.INFO,
        basis=Basis.HARD_FACT,
        confidence=Confidence.MEASURED,
        evaluate=_compatibility_mode,
    ),
)

PROBE_RULES: tuple[Rule, ...] = (
    Rule(
        id="sys.audio.device_missing",
        title="Configured audio device against the default endpoint",
        category=Category.AUDIO,
        subsystem=Subsystem.SYSTEM,
        severity=Severity.INFO,
        basis=Basis.HARD_FACT,
        confidence=Confidence.MEASURED,
        evaluate=_device_mismatch,
        requires=(AUDIO_PROBE_ID,),
    ),
    Rule(
        id="sys.audio.no_low_latency_shared",
        title="Shared-mode engine period",
        category=Category.AUDIO,
        subsystem=Subsystem.SYSTEM,
        severity=Severity.INFO,
        basis=Basis.HARD_FACT,
        confidence=Confidence.MEASURED,
        evaluate=_no_low_latency_shared,
        requires=(AUDIO_PROBE_ID,),
    ),
)
