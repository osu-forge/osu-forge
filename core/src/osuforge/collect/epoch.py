"""The settings a hit error depends on, and a fingerprint of them.

An offset measurement is only about the configuration it was measured under.
Change the audio device, the frame limiter or the offset itself, and earlier
replays stop being evidence about the present. Pooling across such a change
averages two different answers into one that is neither.

The fingerprint is what makes that visible. Replays are grouped by it, a change
in it splits the corpus, and the split is what turns the sign convention from an
assumption into something falsifiable: if the offset moves by a known amount and
the measured mean does not follow, the convention is wrong and the tool should
say so rather than keep recommending.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from osuforge.config.parser import OsuConfig
from osuforge.config.redaction import is_redacted

__all__ = ["EPOCH_KEYS", "ConfigEpoch", "RedactedSettingError"]

EPOCH_KEYS: tuple[str, ...] = (
    # The setting a recommendation would change, and the one everything else
    # here exists to isolate.
    "Offset",
    # Audio path. A different device or a compatibility mode is a different
    # latency, and the player retunes to it without noticing.
    "AudioDevice",
    "AudioCompatibility",
    "AudioDeviceBuffer",
    # Frame pacing. How long a frame waits before it is shown is part of the
    # loop the player is timing against.
    "FrameSync",
    "CustomFrameLimit",
    "RefreshRate",
    "OverrideRefreshRate",
    # Whether the game is composited by the desktop, which adds a frame or more.
    "Fullscreen",
)
"""Settings that change what a hit error means.

Deliberately short. Every key here has to be defensible as something that moves
measured timing, because a key that does not simply splits the corpus for no
reason and throws away sessions that should have been pooled.
"""


class RedactedSettingError(ValueError):
    """A setting in the fingerprint came back redacted.

    Raised rather than hashing the placeholder. A redacted value is one the
    parser refused to keep in memory, and none of :data:`EPOCH_KEYS` should ever
    be one — if that changes, the fingerprint would silently become a constant
    and every configuration would look identical.
    """


@dataclass(frozen=True, slots=True)
class ConfigEpoch:
    """A configuration, identified by the settings that affect timing."""

    digest: str
    """First 16 hex characters of the SHA-256 of the canonical settings.

    Short enough to read in a log line and long enough that two configurations
    colliding is not something to plan around.
    """

    settings: dict[str, str] = field(default_factory=dict)
    """The values that went into the digest, for showing what changed.

    Absent keys are recorded as absent rather than dropped, so a setting
    appearing for the first time is a change rather than a silent no-op.
    """

    @classmethod
    def of(cls, config: OsuConfig) -> ConfigEpoch:
        settings: dict[str, str] = {}
        for key in EPOCH_KEYS:
            value = config.get(key)
            if is_redacted(value):
                raise RedactedSettingError(
                    f"{key} is redacted, so it cannot go into a configuration "
                    "fingerprint. Either it should not be in EPOCH_KEYS or the "
                    "redaction policy has grown to cover something it should not."
                )
            settings[key] = "" if value is None else str(value)

        canonical = "\n".join(f"{key}={settings[key]}" for key in EPOCH_KEYS)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return cls(digest=digest, settings=settings)

    def differences(self, other: ConfigEpoch) -> dict[str, tuple[str, str]]:
        """Which settings differ, as `key -> (theirs, ours)`."""
        return {
            key: (other.settings.get(key, ""), self.settings.get(key, ""))
            for key in EPOCH_KEYS
            if other.settings.get(key, "") != self.settings.get(key, "")
        }

    def describe_change(self, other: ConfigEpoch) -> str:
        changed = self.differences(other)
        if not changed:
            return "no timing-relevant setting changed"
        return "; ".join(
            f"{key}: {before or '(absent)'} -> {after or '(absent)'}"
            for key, (before, after) in changed.items()
        )
